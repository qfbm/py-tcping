import socket
import threading
import time
import os
from dataclasses import dataclass

from database import cleanup_old_logs, get_connection, init_db
from time_utils import app_now


TCP_TIMEOUT_SECONDS = float(os.environ.get("TCP_TIMEOUT_SECONDS", "1"))
NODE_REFRESH_SECONDS = 2
LOG_CLEANUP_SECONDS = 3600


class PingLogBatchWriter:
    def __init__(self):
        self.lock = threading.Lock()
        self.pending = {}

    def enqueue(self, node_id, timestamp, avg_delay, loss_rate):
        with self.lock:
            self.pending[(node_id, timestamp)] = (
                node_id,
                timestamp,
                round(avg_delay, 2),
                round(loss_rate, 2),
            )

    def flush(self):
        with self.lock:
            if not self.pending:
                return
            rows = list(self.pending.values())
            self.pending.clear()

        with get_connection() as conn:
            conn.executemany(
                """
                INSERT INTO ping_logs (node_id, timestamp, avg_delay, loss_rate)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(node_id, timestamp) DO UPDATE SET
                    avg_delay = excluded.avg_delay,
                    loss_rate = excluded.loss_rate
                """,
                rows,
            )
            conn.commit()


ping_log_writer = PingLogBatchWriter()


@dataclass(frozen=True)
class NodeConfig:
    id: int
    name: str
    host: str
    port: int
    interval: int


class MinuteAggregator:
    def __init__(self, node_id):
        self.node_id = node_id
        self.minute = self._current_minute()
        self.success_count = 0
        self.failure_count = 0
        self.total_delay = 0.0

    @staticmethod
    def _current_minute():
        return app_now().replace(second=0, microsecond=0)

    def add_result(self, ok, delay_ms=0.0):
        now_minute = self._current_minute()
        if now_minute != self.minute:
            self.flush()
            self.minute = now_minute
            self.success_count = 0
            self.failure_count = 0
            self.total_delay = 0.0

        if ok:
            self.success_count += 1
            self.total_delay += delay_ms
        else:
            self.failure_count += 1

    def flush(self):
        total = self.success_count + self.failure_count
        if total == 0:
            return

        avg_delay = self.total_delay / self.success_count if self.success_count else 0
        loss_rate = (self.failure_count / total) * 100
        timestamp = self.minute.strftime("%Y-%m-%d %H:%M:00")
        ping_log_writer.enqueue(self.node_id, timestamp, avg_delay, loss_rate)


def tcping(host, port, timeout=TCP_TIMEOUT_SECONDS):
    start = time.perf_counter()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            elapsed_ms = (time.perf_counter() - start) * 1000
            return True, elapsed_ms
    except (OSError, socket.timeout, ValueError):
        return False, 0.0


def fetch_active_nodes():
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, name, host, port, interval
            FROM nodes
            WHERE is_active = 1
            ORDER BY sort_order ASC, id ASC
            """
        ).fetchall()
    return [
        NodeConfig(
            id=row["id"],
            name=row["name"],
            host=row["host"],
            port=row["port"],
            interval=max(1, int(row["interval"] or 1)),
        )
        for row in rows
    ]


class NodeWorker(threading.Thread):
    def __init__(self, node_config, stop_event):
        super().__init__(daemon=True)
        self.node_config = node_config
        self.stop_event = stop_event
        self.aggregator = MinuteAggregator(node_config.id)

    def run(self):
        print(f"[monitor] started node {self.node_config.id}: {self.node_config.name}")
        while not self.stop_event.is_set():
            ok, delay_ms = tcping(self.node_config.host, self.node_config.port)
            self.aggregator.add_result(ok, delay_ms)
            if self.stop_event.wait(self.node_config.interval):
                break

        self.aggregator.flush()
        print(f"[monitor] stopped node {self.node_config.id}: {self.node_config.name}")


class MonitorManager:
    def __init__(self):
        self.stop_event = threading.Event()
        self.supervisor_thread = threading.Thread(target=self._supervise, daemon=True)
        self.workers = {}
        self.worker_stop_events = {}
        self.lock = threading.Lock()
        self.last_cleanup_at = 0

    def start(self):
        init_db()
        cleanup_old_logs()
        if not self.supervisor_thread.is_alive():
            self.supervisor_thread.start()

    def stop(self):
        self.stop_event.set()
        with self.lock:
            for event in self.worker_stop_events.values():
                event.set()
            workers = list(self.workers.values())

        for worker in workers:
            worker.join(timeout=5)

        ping_log_writer.flush()

        if self.supervisor_thread.is_alive():
            self.supervisor_thread.join(timeout=5)

    def _supervise(self):
        print("[monitor] supervisor started")
        while not self.stop_event.is_set():
            try:
                self._cleanup_old_logs_if_needed()
                self._sync_workers()
                ping_log_writer.flush()
            except Exception as exc:
                print(f"[monitor] supervisor error: {exc}")
            self.stop_event.wait(NODE_REFRESH_SECONDS)
        print("[monitor] supervisor stopped")

    def _cleanup_old_logs_if_needed(self):
        now = time.time()
        if now - self.last_cleanup_at < LOG_CLEANUP_SECONDS:
            return

        cleanup_old_logs()
        self.last_cleanup_at = now

    def _sync_workers(self):
        active_nodes = {node.id: node for node in fetch_active_nodes()}

        with self.lock:
            known_ids = set(self.workers.keys())
            active_ids = set(active_nodes.keys())

            for node_id in known_ids - active_ids:
                self.worker_stop_events[node_id].set()

            for node_id in known_ids:
                worker = self.workers[node_id]
                if not worker.is_alive():
                    self.workers.pop(node_id, None)
                    self.worker_stop_events.pop(node_id, None)

            for node_id, node in active_nodes.items():
                existing = self.workers.get(node_id)
                if existing and existing.is_alive():
                    if existing.node_config != node:
                        self.worker_stop_events[node_id].set()
                    else:
                        continue

                event = threading.Event()
                worker = NodeWorker(node, event)
                self.worker_stop_events[node_id] = event
                self.workers[node_id] = worker
                worker.start()


if __name__ == "__main__":
    manager = MonitorManager()
    manager.start()
    print("[monitor] running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        manager.stop()
