"""数据与资源路径的单一出口。

三种运行形态:
- 源码运行(开发):可写数据在 backend/data,只读资源也在 backend/data
- 打包运行(pyinstaller frozen):可写数据在 ~/Library/Application Support/WorkbenchAdvisor
  (macOS;其他平台用 ~/.workbench-advisor),只读资源在打包目录(sys._MEIPASS)
- WB_DATA_DIR 环境变量可显式指定可写数据目录(两种形态都生效)
"""

from __future__ import annotations

import os
import sys

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

IS_FROZEN = bool(getattr(sys, "frozen", False))


def _default_data_dir() -> str:
    if not IS_FROZEN:
        return os.path.join(_BACKEND_ROOT, "data")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/WorkbenchAdvisor")
    return os.path.expanduser("~/.workbench-advisor")


#: 可写数据根目录(SQLite/上传文件/会话 JSON/配置)
DATA_DIR = os.environ.get("WB_DATA_DIR") or _default_data_dir()
os.makedirs(DATA_DIR, exist_ok=True)


def data_path(*parts: str) -> str:
    return os.path.join(DATA_DIR, *parts)


def _resource_root() -> str:
    if IS_FROZEN:
        # pyinstaller 解包目录;资源在 build 时以 datas 打入
        return getattr(sys, "_MEIPASS", _BACKEND_ROOT)
    return _BACKEND_ROOT


#: 只读资源根(种子 JSON/soul 模板/前端构建产物)
RESOURCE_ROOT = _resource_root()


def resource_path(*parts: str) -> str:
    """只读资源:源码模式直接指 backend 下路径;打包模式指 _MEIPASS 下同名路径。"""
    return os.path.join(RESOURCE_ROOT, *parts)
