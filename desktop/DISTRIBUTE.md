# 桌面版打包与分发指南

面向:把「经纪人智能体工作台」分发给团队里的其他经纪人。
接收方**不需要**安装 Python/Node/任何依赖。

## 平台矩阵

| 平台 | 产物 | 打包方式 | 状态 |
|---|---|---|---|
| macOS Apple Silicon (arm64) | .app / DMG | 本机 `build.sh` 或 CI | ✅ |
| macOS Intel (x64) | .app / DMG | CI(macos-13) | ✅ |
| Windows 10/11 (x64) | 目录包 zip | Windows 机 `build.ps1` 或 CI | ✅ |
| Windows (x86, 32 位) | 目录包 zip | CI best-effort | ⚠️ numpy 降 1.26(见 constraints-win32.txt) |
| Linux (x64, Ubuntu 22.04+) | tar.gz | Linux 机 `build.sh` 或 CI | ✅ 目标机需 `apt install gir1.2-webkit2-4.1` |
| macOS 32 位 | — | — | ❌ Apple 自 2019(Catalina) 系统层面移除 32 位支持 |
| Linux 32 位 | — | — | ❌ 主流发行版已放弃 i386 桌面 |

**pyinstaller 不支持交叉打包**:什么系统上打就出什么系统的包。多平台产物用 GitHub Actions
一次全出(`.github/workflows/desktop-build.yml`):

```bash
# 手动触发一轮全平台构建(产物在 Actions 的 artifacts 里)
gh workflow run desktop-build.yml && gh run watch

# 或打 tag,自动出带全部产物的 GitHub Release
git tag desktop-v0.1.0 && git push origin desktop-v0.1.0
```

注意:CI 出的是**干净包**(不含 key);带团队 key 的内部包在自己机器上用 `WITH_ENV=1` 打。

## Windows/Linux 接收方说明

- **Windows**:解压 zip,运行 `workbench\workbench.exe`(需要 Edge WebView2 运行时,
  Win10/11 已内置;SmartScreen 拦截时点"仍要运行")。数据在 `%APPDATA%\WorkbenchAdvisor`。
- **Linux**:解压 tar.gz,先 `sudo apt install gir1.2-webkit2-4.1`,运行 `workbench/workbench`。
  数据在 `~/.local/share/workbench-advisor`。

## 打包(在你的开发机上)

```bash
# 团队内部包:把本机 backend/.env 的千帆/语音 key 打进应用,经纪人开箱即可用 AI
WITH_ENV=1 ./desktop/build.sh

# 或干净包:不含任何 key,接收方首启后需在数据目录 .env 里自行填 key
./desktop/build.sh
```

产物在 `desktop/dist/`:

- `经纪人智能体工作台.app` — 应用本体(约 67MB)
- `经纪人智能体工作台.dmg` — 分发用镜像(约 35MB),发这个

> ⚠️ WITH_ENV 包内的 key 可被有心人从应用包里提取。仅限**信任的团队内部**分发;
> 对外分发前应改为中转服务(key 收在服务端)。Stripe/JWT 等敏感项永远不会被打入。

## 接收方安装(发给经纪人的说明)

1. 双击 `经纪人智能体工作台.dmg`,把应用拖到「应用程序」文件夹;
2. **首次打开:在应用上点右键 → 打开 → 再点「打开」**
   (应用未做 Apple 公证,直接双击会被系统拦下;右键打开一次后以后正常双击);
3. 等几秒,窗口出现即可用——首次启动会自动初始化演示数据。

## 数据与配置(接收方机器上)

全部在 `~/Library/Application Support/WorkbenchAdvisor/`:

| 文件 | 说明 |
|---|---|
| `app.db` | 本机数据库(客户/保单/对话/积分) |
| `.env` | 千帆等配置;改完重开应用生效 |
| `soul.md` | 助理人格,改完**保存即生效**(热加载,无需重启) |
| `uploads/` `client_files/` | 知识库与客户资料文件 |

删掉整个目录 = 恢复出厂(下次启动重新初始化)。

## 已知边界

- 当前构建为 Apple Silicon(arm64);Intel Mac 需在 Intel 机器上跑一次 build.sh。
- 端口:默认 8000,被占用时自动挑空闲端口,互不冲突。
- 每台机器数据独立(本地 SQLite)。多人共享客户库/中心计费需要部署服务端(后续)。
- 应用未签名公证:需要 Apple Developer 账号($99/年)后可加 `codesign + notarytool`,
  届时接收方可直接双击打开。
