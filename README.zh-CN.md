# Codex Provider Session Sync

English README: [README.md](README.md)

Codex Provider Session Sync 是一个面向 Codex Desktop 的本地后台工具，用来把多个 `model_provider` 下的会话自动统一聚合。它会定期扫描 `.codex` 会话文件，把每个真实会话镜像到其它 provider，让你在 `openai`、`openrouter`、`custom` 等 provider 之间切换时，仍然能看到同一批会话历史。

## 它解决什么问题

Codex Desktop 的会话 JSONL 里会记录 `model_provider`。切换 provider 后，如果某些历史会话只属于原 provider，新 provider 视角下可能不可见。这个工具的做法是：

- 读取本机 `.codex/sessions` 和 `.codex/archived_sessions`。
- 识别每条真实会话的 `session_meta.id` 和 `model_provider`。
- 为其它 provider 创建确定性的镜像会话 ID。
- 写入镜像 JSONL，并更新 `.codex/session_index.jsonl`。
- 后台定期刷新镜像内容，所以源会话继续追加消息后，镜像也会跟着更新。

镜像会话会带 `forked_from_id`，工具不会把自己生成的镜像再次作为源会话扩散。

## 当前特性

- 多 provider 互相聚合：默认覆盖 `openai`、`openrouter`、`custom`。
- 后台 daemon：按固定间隔自动同步。
- 托盘图标：后台运行时可在任务栏托盘看到状态，并可右键退出。
- GUI 控制面板：一个开关控制后台同步启停。
- 开机自启动：Windows 登录后自动运行。
- 预览模式：核心同步 CLI 默认不写入，只有 `--apply` 才会修改文件。
- 构建产物隔离：`dist/`、`build/`、日志、备份目录默认不进入 Git。

## 项目结构

```text
.
├── assets/
│   └── provider-sync.ico
├── packaging/
│   └── pyinstaller/
│       ├── ProviderSyncControl.spec
│       └── ProviderSyncDaemon.spec
├── scripts/
│   └── windows/
│       ├── install_provider_sync_autostart.ps1
│       └── uninstall_provider_sync_autostart.ps1
├── src/
│   ├── provider_sync_control.py
│   ├── provider_sync_daemon.py
│   └── provider_sync_v2.py
├── .gitignore
├── README.md
├── README.zh-CN.md
└── requirements.txt
```

## 快速开始

安装依赖：

```powershell
python -m pip install -r .\requirements.txt
```

先做一次预览：

```powershell
python .\src\provider_sync_v2.py --mode mirror-all --provider openai --provider openrouter --provider custom
```

执行一次真实同步：

```powershell
python .\src\provider_sync_v2.py --mode mirror-all --provider openai --provider openrouter --provider custom --apply
```

启动后台同步：

```powershell
python .\src\provider_sync_daemon.py --provider openai --provider openrouter --provider custom
```

## Windows 开机自启动

安装并立即启动：

```powershell
.\scripts\windows\install_provider_sync_autostart.ps1
```

卸载并停止后台同步：

```powershell
.\scripts\windows\uninstall_provider_sync_autostart.ps1
```

默认备份目录：

```text
C:\Users\<用户名>\Desktop\codex-provider-session-sync-backup
```

默认日志：

```text
C:\Users\<用户名>\.codex\log\provider-sync-daemon.log
```

## GUI 控制面板

源码运行：

```powershell
python .\src\provider_sync_control.py
```

打包后运行：

```powershell
.\dist\ProviderSyncControl.exe
```

控制面板会显示后台同步状态，并提供一个开关控制自动同步。

## 打包

在项目根目录执行：

```powershell
python -m PyInstaller --clean --noconfirm .\packaging\pyinstaller\ProviderSyncDaemon.spec
python -m PyInstaller --clean --noconfirm .\packaging\pyinstaller\ProviderSyncControl.spec
```

输出：

```text
dist/ProviderSyncDaemon.exe
dist/ProviderSyncControl.exe
```

## 安全说明

- 第一次使用建议先运行预览命令，不加 `--apply`。
- 工具只创建或刷新自己的镜像文件，不覆盖无法识别的冲突文件。
- 镜像文件会改写 `session_meta.id`、`model_provider` 和 `forked_from_id`，因此不能用软链接代替。
- 如果 Codex Desktop 后续更改会话文件格式，需要重新验证 `session_meta` 和 `session_index.jsonl` 的处理逻辑。
