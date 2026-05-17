# Codex 会话同步工具

English README: [README.md](README.md)

这是一个本地工具，用来同步 Codex 在不同模型供应商 provider 下的历史会话可见性。它支持一次性命令行同步、本地 Web UI、后台常驻同步、Windows 开机自启动和托盘控制程序。

工具默认只做预览，不会写入数据。只有在 Web UI 中勾选确认并点击“执行写入”，或在命令行中显式追加 `--apply`，才会修改 Codex 会话文件。

## UI 截图

![Codex 会话同步工具 Web UI](assets/ui-screenshot.png)

## 项目结构

- `src/m.py`：原始同步 CLI，支持预览、写入、备份和旧 SQLite 索引处理。
- `src/m_webui.py`：本地 Web UI 入口。
- `src/provider_sync_v2.py`：新版 provider 会话文件同步核心逻辑。
- `src/provider_sync_daemon.py`：后台同步 daemon，包含托盘图标。
- `src/provider_sync_control.py`：Windows GUI 控制面板，提供开关控制。
- `assets/`：截图和应用图标。
- `packaging/pyinstaller/`：PyInstaller 打包配置。
- `scripts/windows/`：Windows 自启动安装和卸载脚本。
- `tools/`：恢复和维护脚本。
- `requirements.txt`：打包依赖。
- `.gitignore`：忽略本地构建产物、日志、虚拟环境和备份目录。

`build/`、`dist/`、`.build-venv/`、日志文件和 `__pycache__/` 属于本机构建或运行产物，默认不进入 Git。

## 推荐用法：后台同步

安装或更新依赖：

```powershell
python -m pip install -r .\requirements.txt
```

安装开机自启动并立即启动后台同步：

```powershell
.\scripts\windows\install_provider_sync_autostart.ps1
```

卸载自启动：

```powershell
.\scripts\windows\uninstall_provider_sync_autostart.ps1
```

默认同步 provider 为 `openai`、`openrouter` 和 `custom`。如需调整，修改 `scripts/windows/install_provider_sync_autostart.ps1` 中的 `--provider` 参数。

## GUI 控制面板

源码方式运行：

```powershell
python .\src\provider_sync_control.py
```

如果已经打包，可运行：

```powershell
.\dist\ProviderSyncControl.exe
```

控制面板用于查看后台同步状态，并执行启动 / 停止操作。

## Web UI

在项目根目录运行：

```powershell
python .\src\m_webui.py
```

程序会启动本地服务，并自动打开浏览器页面。服务只监听本机 `127.0.0.1`。

建议流程：

1. 确认 `Codex Home` 路径是否正确，默认通常是 `C:\Users\<用户名>\.codex`。
2. 选择“全供应商互同步”。
3. 先点击“预览”，查看需要创建的镜像文件和冲突数。
4. 如果要正式写入，填写备份目录。
5. 勾选“我已备份或确认可以写入”。
6. 点击“执行写入”。

## 命令行同步

全供应商互同步预览：

```powershell
python .\src\m.py --sync-all-providers-mutually
```

全供应商互同步写入：

```powershell
python .\src\m.py --sync-all-providers-mutually --backup-dir .\backup --apply
```

从指定源 provider 同步到其它 provider：

```powershell
python .\src\m.py --sync-openai-to-all-providers --source-provider openai
```

单目标迁移：

```powershell
python .\src\m.py --target-provider openai --backup-dir .\backup --apply
```

## 重新打包 EXE

在项目根目录运行：

```powershell
python -m PyInstaller --clean --noconfirm .\packaging\pyinstaller\ProviderSyncDaemon.spec
python -m PyInstaller --clean --noconfirm .\packaging\pyinstaller\ProviderSyncControl.spec
```

生成结果：

```text
dist/ProviderSyncDaemon.exe
dist/ProviderSyncControl.exe
```

如果提示 exe 被占用，先停止正在运行的后台同步程序或控制面板，再重新打包。

## 安全注意事项

- 第一次使用一定先预览。
- 正式写入前建议填写备份目录。
- `--apply` 会修改 Codex 会话文件。
- 工具不会覆盖冲突文件，只会跳过并报告。
- 不建议把 `dist/`、`build/`、日志和备份目录提交到 Git。
