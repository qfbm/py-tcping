import os
import secrets
from datetime import datetime, timedelta

from flask import Flask, jsonify, redirect, render_template, request, session, url_for

from backend_monitor import MonitorManager
from database import get_connection, init_db
from time_utils import app_now, minute_floor


app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
LOGIN_PASSWORD = os.environ.get("PING_MONITOR_PASSWORD", "114514")
monitor_manager = MonitorManager()


def row_to_dict(row):
    return {key: row[key] for key in row.keys()}


def normalize_node_order(conn):
    rows = conn.execute(
        """
        SELECT id
        FROM nodes
        WHERE is_active = 1
        ORDER BY sort_order ASC, id ASC
        """
    ).fetchall()
    for index, row in enumerate(rows, start=1):
        conn.execute(
            "UPDATE nodes SET sort_order = ? WHERE id = ?",
            (index, row["id"]),
        )


def current_minute_end():
    return minute_floor(app_now()) + timedelta(minutes=1)


@app.before_request
def require_login():
    public_endpoints = {"login"}
    if request.endpoint in public_endpoints or request.endpoint == "static":
        return None

    if session.get("authenticated"):
        return None

    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "请先登录"}), 401

    return redirect(url_for("login", next=request.full_path))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        password = request.form.get("password", "")
        if password == LOGIN_PASSWORD:
            session["authenticated"] = True
            next_url = request.args.get("next") or url_for("index")
            if not next_url.startswith("/"):
                next_url = url_for("index")
            return redirect(next_url)
        error = "密码错误"

    return render_template("login.html", error=error)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def index():
    return render_template("index.html")


@app.get("/api/nodes")
def api_nodes():
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, name, host, port, interval, sort_order, is_active
            FROM nodes
            WHERE is_active = 1
            ORDER BY sort_order ASC, id ASC
            """
        ).fetchall()
    return jsonify({"nodes": [row_to_dict(row) for row in rows]})


@app.post("/api/nodes/add")
def api_nodes_add():
    data = request.get_json(silent=True) or request.form
    name = (data.get("name") or "").strip()
    host = (data.get("host") or "").strip()
    interval = data.get("interval", 1)

    try:
        port = int(data.get("port", 0))
        interval = int(interval or 1)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "端口和间隔必须是数字"}), 400

    if not name or not host:
        return jsonify({"ok": False, "error": "节点名称和 IP/域名不能为空"}), 400
    if port < 1 or port > 65535:
        return jsonify({"ok": False, "error": "端口范围必须是 1-65535"}), 400
    if interval < 1:
        return jsonify({"ok": False, "error": "探测间隔至少为 1 秒"}), 400

    with get_connection() as conn:
        max_order = conn.execute(
            """
            SELECT COALESCE(MAX(sort_order), 0)
            FROM nodes
            WHERE is_active = 1
            """
        ).fetchone()[0]
        cursor = conn.execute(
            """
            INSERT INTO nodes (name, host, port, interval, sort_order, is_active)
            VALUES (?, ?, ?, ?, ?, 1)
            """,
            (name, host, port, interval, max_order + 1),
        )
        conn.commit()

    return jsonify({"ok": True, "node_id": cursor.lastrowid})


@app.post("/api/nodes/delete")
def api_nodes_delete():
    data = request.get_json(silent=True) or request.form
    try:
        node_id = int(data.get("node_id", 0))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "node_id 无效"}), 400

    if node_id <= 0:
        return jsonify({"ok": False, "error": "node_id 无效"}), 400

    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE nodes SET is_active = 0 WHERE id = ?",
            (node_id,),
        )
        normalize_node_order(conn)
        conn.commit()

    if cursor.rowcount == 0:
        return jsonify({"ok": False, "error": "节点不存在"}), 404

    return jsonify({"ok": True})


@app.post("/api/nodes/move")
def api_nodes_move():
    data = request.get_json(silent=True) or request.form
    try:
        node_id = int(data.get("node_id", 0))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "node_id 无效"}), 400

    direction = (data.get("direction") or "").strip()
    if node_id <= 0:
        return jsonify({"ok": False, "error": "node_id 无效"}), 400
    if direction not in {"up", "down"}:
        return jsonify({"ok": False, "error": "direction 必须是 up 或 down"}), 400

    with get_connection() as conn:
        normalize_node_order(conn)
        rows = conn.execute(
            """
            SELECT id, sort_order
            FROM nodes
            WHERE is_active = 1
            ORDER BY sort_order ASC, id ASC
            """
        ).fetchall()
        ids = [row["id"] for row in rows]
        if node_id not in ids:
            return jsonify({"ok": False, "error": "节点不存在"}), 404

        index = ids.index(node_id)
        target_index = index - 1 if direction == "up" else index + 1
        if target_index < 0 or target_index >= len(ids):
            return jsonify({"ok": True})

        ids[index], ids[target_index] = ids[target_index], ids[index]
        for order, ordered_id in enumerate(ids, start=1):
            conn.execute(
                "UPDATE nodes SET sort_order = ? WHERE id = ?",
                (order, ordered_id),
            )
        conn.commit()

    return jsonify({"ok": True})


@app.get("/api/chart_data")
def api_chart_data():
    try:
        node_id = int(request.args.get("node_id", 0))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "node_id 无效"}), 400

    if node_id <= 0:
        return jsonify({"ok": False, "error": "node_id 无效"}), 400

    date_text = (request.args.get("date") or "").strip()
    start_date_text = (request.args.get("start_date") or "").strip()
    end_date_text = (request.args.get("end_date") or "").strip()
    start_time = None
    end_time = None
    end_dt = None
    use_date_labels = False

    if start_date_text or end_date_text:
        try:
            start_date = datetime.strptime(start_date_text, "%Y-%m-%d")
            end_date = datetime.strptime(end_date_text, "%Y-%m-%d")
        except ValueError:
            return jsonify({"ok": False, "error": "日期格式必须是 YYYY-MM-DD"}), 400

        if start_date > end_date:
            return jsonify({"ok": False, "error": "开始日期不能晚于结束日期"}), 400

        end_dt = end_date + timedelta(days=1)
        start_time = start_date.strftime("%Y-%m-%d 00:00:00")
        end_time = end_dt.strftime("%Y-%m-%d 00:00:00")
        use_date_labels = start_date.date() != end_date.date()
    elif date_text:
        try:
            selected_date = datetime.strptime(date_text, "%Y-%m-%d")
        except ValueError:
            return jsonify({"ok": False, "error": "日期格式必须是 YYYY-MM-DD"}), 400

        end_dt = selected_date + timedelta(days=1)
        start_time = selected_date.strftime("%Y-%m-%d 00:00:00")
        end_time = end_dt.strftime("%Y-%m-%d 00:00:00")
    else:
        end_dt = current_minute_end()
        start_time = (end_dt - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
        end_time = end_dt.strftime("%Y-%m-%d %H:%M:%S")

    now_end = current_minute_end()
    if end_dt > now_end:
        end_dt = now_end
        end_time = end_dt.strftime("%Y-%m-%d %H:%M:%S")

    with get_connection() as conn:
        if start_time and end_time:
            rows = conn.execute(
                """
                SELECT timestamp, avg_delay, loss_rate
                FROM ping_logs
                WHERE node_id = ? AND timestamp >= ? AND timestamp < ?
                ORDER BY timestamp ASC
                LIMIT 10000
                """,
                (node_id, start_time, end_time),
            ).fetchall()
        else:
            rows = []

    timestamps = [
        row["timestamp"][5:16] if use_date_labels else row["timestamp"][11:16]
        for row in rows
    ]
    avg_delay = [row["avg_delay"] for row in rows]
    loss_rate = [row["loss_rate"] for row in rows]

    return jsonify(
        {
            "ok": True,
            "timestamps": timestamps,
            "avgDelay": avg_delay,
            "lossPk": loss_rate,
        }
    )


if __name__ == "__main__":
    init_db()
    monitor_manager.start()
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True, use_reloader=False)
