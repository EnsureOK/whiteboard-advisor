# -*- mode: python ; coding: utf-8 -*-
"""pyinstaller 配置:经纪人智能体工作台 桌面版(.app)。

由 desktop/build.sh 调用;直接运行:
  cd 项目根 && backend/.venv/bin/pyinstaller desktop/workbench.spec --noconfirm
"""

import os

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
    disable_windowed_traceback=False,
    target_arch=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="workbench",
)

app = BUNDLE(
    coll,
    name="经纪人智能体工作台.app",
    icon=None,
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
