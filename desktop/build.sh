#!/bin/bash
# 打包桌面版 .app + DMG,用于分发给其他经纪人。
#
# 用法:
#   ./desktop/build.sh              # 干净包:不含任何 key,首启生成 .env 模板让使用者填
#   WITH_ENV=1 ./desktop/build.sh   # 团队内部包:把本机 backend/.env 的千帆/语音 key 打入
#                                   # (注意:key 在包内可被提取,仅限内部分发)
set -e
cd "$(dirname "$0")/.."
PY=backend/.venv/bin/python

echo "==> 构建前端"
(cd frontend && npm run build)

rm -f desktop/bundled.env
if [ "${WITH_ENV:-0}" = "1" ]; then
  echo "==> 注入团队配置(仅千帆/语音,剔除 Stripe/JWT 等敏感项)"
  grep -E '^(QIANFAN_|BAIDU_SPEECH_)' backend/.env > desktop/bundled.env
fi

echo "==> pyinstaller 打包"
rm -rf desktop/build desktop/dist
$PY -m PyInstaller desktop/workbench.spec --noconfirm \
  --distpath desktop/dist --workpath desktop/build 2>&1 | tail -4
rm -f desktop/bundled.env

APP="desktop/dist/经纪人智能体工作台.app"
[ -d "$APP" ] || { echo "打包失败:未生成 .app"; exit 1; }

echo "==> ad-hoc 签名(免开发者证书;接收方首次需右键-打开)"
codesign --force --deep -s - "$APP" 2>/dev/null || true

echo "==> 打 DMG"
DMG="desktop/dist/经纪人智能体工作台.dmg"
rm -f "$DMG"
hdiutil create -volname "经纪人智能体工作台" -srcfolder "$APP" -ov -format UDZO "$DMG" >/dev/null

echo ""
echo "完成:"
du -sh "$APP" "$DMG"
echo ""
echo "分发说明见 desktop/DISTRIBUTE.md"
