# -*- mode: python ; coding: utf-8 -*-
"""pyinstaller 配置:经纪人智能体工作台 桌面版。

跨平台:macOS 产 .app(BUNDLE);Windows/Linux 产 onedir 目录包。
由 desktop/build.sh(mac/linux) 或 desktop/build.ps1(windows) 调用;直接运行:
  cd 项目根 && python -m PyInstaller desktop/workbench.spec --noconfirm
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(SPECPATH))) if os.path.basename(SPECPATH) else None
# SPECPATH 是 spec 所在目录(desktop/),项目根是它的上级
ROOT = os.path.dirname(SPECPATH)
BACKEND = os.path.join(ROOT, "backend")

datas = [
    (os.path.join(ROOT, "frontend", "dist"), "frontend_dist"),
    (os.path.join(BACKEND, "soul.md"), "."),
    (os.path.join(BACKEND, "app", "data"), os.path.join("app", "data")),
]

# 团队内部分发:build.sh --with-env 会生成过滤后的 bundled.env(仅千帆/语音 key)
_bundled_env = os.path.join(SPECPATH, "bundled.env")
if os.path.isfile(_bundled_env):
    datas.append((_bundled_env, "."))

a = Analysis(
    [os.path.join(SPECPATH, "launcher.py")],
    pathex=[BACKEND],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "app.main",
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
    ],
    excludes=["tests", "pytest", "pyinstaller"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="workbench",
    console=False,
    icon=os.path.join(SPECPATH, "icon.ico") if sys.platform == "win32" else None,
    disable_windowed_traceback=False,
    target_arch=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="workbench",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="经纪人智能体工作台.app",
        icon=os.path.join(SPECPATH, "icon.icns"),
        bundle_identifier="com.ensureok.workbench",
        info_plist={
            "CFBundleName": "经纪人智能体工作台",
            "CFBundleDisplayName": "经纪人智能体工作台",
            "CFBundleShortVersionString": "0.1.0",
            "NSHighResolutionCapable": True,
            # WKWebView 访问本机后端
            "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
        },
    )
# Windows/Linux: COLLECT 目录包即产物(desktop/dist/workbench/),
# 由构建脚本压缩为 zip/tar.gz 分发
