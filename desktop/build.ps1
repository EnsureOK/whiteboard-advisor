# 打包桌面版(Windows: 目录包 + zip),用于分发给其他经纪人。
#
# 用法(PowerShell,项目根目录):
#   .\desktop\build.ps1                 # 干净包(不含 key)
#   $env:WITH_ENV=1; .\desktop\build.ps1  # 团队内部包:注入千帆/语音 key
#   $env:PY="py -3.11"                  # 指定 Python(默认 python)
#
# 32 位包:用 32 位 Python 运行本脚本,依赖安装时加约束:
#   python -m pip install -r backend\requirements.txt -r desktop\requirements-desktop.txt -c desktop\constraints-win32.txt
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$py = if ($env:PY) { $env:PY } else { "python" }

Write-Host "==> 构建前端"
Push-Location frontend
npm run build
if ($LASTEXITCODE -ne 0) { throw "前端构建失败" }
Pop-Location

Remove-Item -Force desktop\bundled.env -ErrorAction SilentlyContinue
if ($env:WITH_ENV -eq "1") {
  Write-Host "==> 注入团队配置(仅千帆/语音,剔除 Stripe/JWT 等敏感项)"
  Select-String -Path backend\.env -Pattern '^(QIANFAN_|BAIDU_SPEECH_)' |
    ForEach-Object { $_.Line } | Set-Content -Encoding utf8 desktop\bundled.env
}

Write-Host "==> pyinstaller 打包 (Windows)"
Remove-Item -Recurse -Force desktop\build, desktop\dist -ErrorAction SilentlyContinue
& $py -m PyInstaller desktop\workbench.spec --noconfirm --distpath desktop\dist --workpath desktop\build
if ($LASTEXITCODE -ne 0) { throw "pyinstaller 失败" }
Remove-Item -Force desktop\bundled.env -ErrorAction SilentlyContinue

$dir = "desktop\dist\workbench"
if (-not (Test-Path $dir)) { throw "打包失败:未生成目录包" }

$arch = if ([Environment]::Is64BitProcess) { "x64" } else { "x86" }
$zip = "desktop\dist\workbench-windows-$arch.zip"
Write-Host "==> 打 zip"
Compress-Archive -Path $dir -DestinationPath $zip -Force

Write-Host ""
Write-Host "完成:"
Get-Item $dir, $zip | ForEach-Object { "{0}  {1:N1} MB" -f $_.FullName, ((Get-ChildItem $_ -Recurse -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum / 1MB) }
Write-Host "接收方解压后运行 workbench\workbench.exe;分发说明见 desktop/DISTRIBUTE.md"
