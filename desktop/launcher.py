"""经纪人智能体工作台 · 桌面版(pywebview 壳)。

两种形态:
- 源码运行(开发): ./desktop/run.sh — 复用本机 8000 后端或本进程内启动
- 打包运行(pyinstaller .app): 双击即用 — 数据在
  ~/Library/Application Support/WorkbenchAdvisor,首启自动建库+灌演示数据

用法(源码): ./desktop/run.sh  (或 backend/.venv/bin/python desktop/app.py)
"""

from __future__ import annotations

import os
import shutil
import socket
import sys
import threading
import time

IS_FROZEN = bool(getattr(sys, "frozen", False))

# Windows windowed 打包(console=False)下 stdout/stderr 为 None,重定向到 devnull
if sys.stdout is None or sys.stderr is None:
    _devnull_fd = os.open(os.devnull, os.O_WRONLY)
    if sys.stdout is None:
        os.dup2(_devnull_fd, 1)
    if sys.stderr is None:
        os.dup2(_devnull_fd, 2)
    os.close(_devnull_fd)

if not IS_FROZEN:
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    BACKEND = os.path.join(ROOT, "backend")
    DIST = os.path.join(ROOT, "frontend", "dist")
    sys.path.insert(0, BACKEND)
    os.chdir(BACKEND)

HOST = "127.0.0.1"


def _pick_port() -> int:
    """默认 8000;被占且不是我们的后端时挑空闲端口(打包版避免冲突)。"""
    want = int(os.environ.get("WB_PORT", "8000"))
    if _backend_alive(want):
        return want
    with socket.socket() as s:
        try:
            s.bind((HOST, want))
            return want
        except OSError:
            if not IS_FROZEN:
                return want  # 开发模式保持 8000,由 backend_alive 复用
            s2 = socket.socket()
            s2.bind((HOST, 0))
            port = s2.getsockname()[1]
            s2.close()
            return port


def _backend_alive(port: int) -> bool:
    """健康检查只允许打向本机 HOST,端口为合法整数(http.client 不拼 URL 字符串)。"""
    if not isinstance(port, int) or not (0 < port < 65536):
        return False
    try:
        import http.client

        conn = http.client.HTTPConnection(HOST, port, timeout=1.5)
        conn.request("GET", "/health")
        resp = conn.getresponse()
        ok = resp.status == 200
        conn.close()
        return ok
    except Exception:  # noqa: BLE001
        return False


def _data_file_path(name: str) -> str:
    """数据目录内文件路径:规范化后校验仍在 DATA_DIR 内(防路径穿越)。"""
    path = os.path.abspath(os.path.join(DATA_DIR, name))
    if not path.startswith(os.path.abspath(DATA_DIR) + os.sep):
        raise RuntimeError("invalid data path")
    return path


def _first_run_setup() -> None:
    """打包版首启:准备数据目录(.env 模板/soul.md 副本/建库/演示数据)。"""
    from pathlib import Path

    from app.paths import DATA_DIR, resource_path

    env_path = _data_file_path(".env")
    if not os.path.isfile(env_path):
        bundled = resource_path("bundled.env")
        if os.path.isfile(bundled):
            shutil.copyfile(bundled, env_path)  # 打包时注入的团队配置
        else:
            Path(env_path).write_text(
                "# 工作台配置(填入后重启应用生效)\n"
                "QIANFAN_API_KEY=\n"
                "QIANFAN_BASE_URL=https://qianfan.baidubce.com/v2\n"
                "QIANFAN_MODEL_FAST=glm-5.3-flash\n"
                "QIANFAN_MODEL_DEEP=glm-5.3-flash\n",
                encoding="utf-8",
            )

    soul_path = _data_file_path("soul.md")
    if not os.path.isfile(soul_path):
        tpl = resource_path("soul.md")
        if os.path.isfile(tpl):
            shutil.copyfile(tpl, soul_path)

    from app.db import init_db
    from app.services.demo_seed import ensure_starter_content

    init_db()
    if ensure_starter_content():
        print("首次启动:已就绪(内置示例客户与知识库)。")


def start_backend_in_thread(port: int) -> None:
    """本进程内起 uvicorn(daemon 线程:窗口关闭即随主进程退出)。"""
    import uvicorn

    from app.main import app as fastapi_app  # 直接导入对象,打包器可静态收集

    config = uvicorn.Config(fastapi_app, host=HOST, port=port, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()


def wait_backend(port: int, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _backend_alive(port):
            return True
        time.sleep(0.4)
    return False


def main() -> None:
    if not IS_FROZEN and not os.path.isdir(DIST):
        print("未找到 frontend/dist 构建产物。先执行: cd frontend && npm run build")
        sys.exit(1)

    if IS_FROZEN:
        _first_run_setup()

    port = _pick_port()
    if _backend_alive(port):
        print(f"检测到本机 {port} 已有后端,直接复用。")
    else:
        print(f"正在本进程内启动后端(:{port})…")
        start_backend_in_thread(port)
        if not wait_backend(port):
            print("后端启动失败。源码模式检查依赖与 backend/.env;打包模式查看数据目录 .env。")
            sys.exit(1)

    import webview

    webview.create_window(
        "经纪人智能体工作台",
        f"http://{HOST}:{port}/app/?view=workbench",
        width=1440,
        height=920,
        min_size=(960, 640),
        text_select=True,
    )
    webview.start()


if __name__ == "__main__":
    main()
