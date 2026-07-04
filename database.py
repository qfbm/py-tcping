import sqlite3
from datetime import timedelta
from pathlib import Path

from time_utils import app_now


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "ping_monitor.db"
RETENTION_DAYS = 30


def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db():
    with get_connection() as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                host TEXT NOT NULL,
                port INTEGER NOT NULL,
                interval INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            )
            """
        )
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(nodes)").fetchall()
        }
        if "sort_order" not in columns:
            conn.execute(
                "ALTER TABLE nodes ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0"
            )
        conn.execute(
            """
            UPDATE nodes
            SET sort_order = id
            WHERE sort_order = 0
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ping_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                node_id INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                avg_delay REAL NOT NULL DEFAULT 0,
                loss_rate REAL NOT NULL DEFAULT 0,
                FOREIGN KEY (node_id) REFERENCES nodes(id) ON DELETE CASCADE,
                UNIQUE(node_id, timestamp)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ping_logs_node_time
            ON ping_logs(node_id, timestamp)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_nodes_active
            ON nodes(is_active, sort_order)
            """
        )
        cleanup_old_logs(conn)
        conn.commit()


def cleanup_old_logs(conn=None, retention_days=RETENTION_DAYS):
    cutoff = (app_now() - timedelta(days=retention_days)).strftime("%Y-%m-%d %H:%M:%S")

    if conn is not None:
        conn.execute("DELETE FROM ping_logs WHERE timestamp < ?", (cutoff,))
        return

    with get_connection() as cleanup_conn:
        cleanup_conn.execute("DELETE FROM ping_logs WHERE timestamp < ?", (cutoff,))
        cleanup_conn.commit()


if __name__ == "__main__":
    init_db()
    print(f"SQLite database initialized at: {DB_PATH}")
