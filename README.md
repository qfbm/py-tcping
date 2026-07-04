# TCPING Monitor

一个轻量级 TCPING 监控系统，使用 Python、Flask、SQLite 和 ECharts。


## Linux/Debian 建议

```bash
export PING_MONITOR_TZ=Asia/Shanghai
export TCP_TIMEOUT_SECONDS=1
python app.py
```

`PING_MONITOR_TZ` 用来固定应用聚合和查询使用的时区，避免服务器系统时区是 UTC 时图表日期错位。

```powershell
python backend_monitor.py
```

## 运行

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

打开 `http://127.0.0.1:5000`。

`app.py` 会自动初始化 `ping_monitor.db` 并启动后台探测线程。也可以单独运行后台探测：

```powershell
python backend_monitor.py
```

默认密码

```powershell
114514
```

## 文件

- `database.py`：SQLite 初始化与连接封装。
- `backend_monitor.py`：多线程 TCPING 探测与每分钟聚合写库。
- `app.py`：Flask 页面与 API。
- `templates/index.html`：暗色单页前端与 ECharts 双 Y 轴图表。
