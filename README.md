# TCPING Monitor

一个轻量级 TCPING 监控系统，使用 Python、Flask、SQLite 和 ECharts。

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

## 文件

- `database.py`：SQLite 初始化与连接封装。
- `backend_monitor.py`：多线程 TCPING 探测与每分钟聚合写库。
- `app.py`：Flask 页面与 API。
- `templates/index.html`：暗色单页前端与 ECharts 双 Y 轴图表。
