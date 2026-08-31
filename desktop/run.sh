#!/bin/bash
# 桌面版一键启动:构建前端(有变更时) + 打开原生窗口
set -e
cd "$(dirname "$0")/.."

# dist 不存在,或 src 比 dist 新 -> 重新构建
if [ ! -d frontend/dist ] || [ -n "$(find frontend/src frontend/index.html -newer frontend/dist -print -quit 2>/dev/null)" ]; then
  echo "构建前端…"
  (cd frontend && npm run build)
fi

exec backend/.venv/bin/python desktop/app.py
