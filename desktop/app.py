"""经纪人智能体工作台 · 桌面版(pywebview 壳)。

行为:
- 若本机 8000 端口已有后端(开发时常驻的 uvicorn),直接复用;
- 否则在本进程内启动 uvicorn(关窗即退,不留孤儿进程);
- 打开原生窗口加载 /app/?view=workbench(前端构建产物由 FastAPI 托管)。

用法: ./desktop/run.sh  (或 backend/.venv/bin/python desktop/app.py)
"""

from __future__ import annotations

import os
import sys
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
DIST = os.path.join(ROOT, "frontend", "dist")

HOST = "127.0.0.1"
PORT = int(os.environ.get("WB_PORT", "8000"))
BASE = f"http://{HOST}:{PORT}"
APP_URL = f"{BASE}/app/?view=workbench"


def backend_alive() -> bool:
    try:
        with urllib.request.urlopen(f"{BASE}/health", timeout=1.5) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def start_backend_in_thread() -> None:
    """本进程内起 uvicorn(daemon 线程:窗口关闭即随主进程退出)。"""
    os.chdir(BACKEND)
    sys.path.insert(0, BACKEND)

    import uvicorn

    config = uvicorn.Config("app.main:app", host=HOST, port=PORT, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()


def wait_backend(timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if backend_alive():
            return True
        time.sleep(0.4)
    return False


def main() -> None:
    if not os.path.isdir(DIST):
        print("未找到 frontend/dist 构建产物。先执行: cd frontend && npm run build")
        sys.exit(1)

    reused = backend_alive()
    if not reused:
        print("本机 8000 无后端,正在本进程内启动…")
        start_backend_in_thread()
        if not wait_backend():
            print("后端启动失败,查看依赖与 backend/.env 配置。")
            sys.exit(1)
    else:
        print("检测到本机 8000 已有后端,直接复用。")

    import webview

    webview.create_window(
        "经纪人智能体工作台",
        APP_URL,
        width=1440,
        height=920,
        min_size=(960, 640),
        text_select=True,
    )
    webview.start()


if __name__ == "__main__":
    main()
