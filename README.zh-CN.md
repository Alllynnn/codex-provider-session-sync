# Codex Provider Session Sync

English README: [README.md](README.md)

Codex Provider Session Sync 是一个 Tauri 桌面应用，用来把 Codex Desktop 多个 `model_provider` 下的会话自动统一聚合。它的目标是让你在 `openai`、`openrouter`、`custom` 等 provider 之间切换时，仍然能看到同一批会话历史。

系统运行流程 HTML 文档：[docs/system-flow.html](docs/system-flow.html)

## 技术栈

- 桌面壳：Tauri v2
- 前端：React + TypeScript + Vite
- 样式：Tailwind CSS
- 图标：lucide-react
- 后端：Rust

当前版本已移除旧 Python / PyInstaller 实现，同步逻辑迁移到 `src-tauri` 的 Rust 代码中。

## 功能

- 扫描 `.codex/sessions` 和 `.codex/archived_sessions`。
- 按 `session_meta.id` 和 `model_provider` 聚合会话 lineage。
- 为其它 provider 生成确定性的镜像会话 ID。
- 创建或刷新镜像 JSONL。
- 更新 `.codex/session_index.jsonl`。
- 后台循环自动同步。
- 托盘图标：双击打开主窗口，右键菜单包含打开面板、开启/关闭同步、设置同步间隔、退出。
- 主窗口：类似 CC Switch 的白底卡片式 UI，但展示同步状态、Provider、同步间隔、开机自启和日志操作。
- Windows 开机自启：主窗口右上角设置按钮可写入或移除 HKCU Run 启动项。
- 备份与恢复：每次同步前自动创建快照，主窗口可手动创建备份并恢复到指定快照。
- 备份清理：自动同步只有发现会话或索引需要写入时才创建备份；旧快照按保留策略自动清理。

## 安装方式

### 方式一：下载 Release 安装包

1. 打开 GitHub Releases：<https://github.com/Alllynnn/codex-provider-session-sync/releases>
2. 下载最新版本的 Windows 安装包或可执行文件。
3. 启动 `Codex Provider Session Sync`。
4. 在设置页确认 provider 列表和同步间隔。
5. 需要开机自启时，打开右上角设置开关。

如果还没有发布 Release，可以先使用下面的源码构建方式。

### 方式二：从源码构建

环境要求：

- Node.js
- pnpm
- Rust toolchain，包括 `cargo` 和 `rustc`

构建命令：

```powershell
git clone https://github.com/Alllynnn/codex-provider-session-sync.git
cd codex-provider-session-sync
pnpm install
pnpm build
pnpm exec tauri build --no-bundle
```

构建完成后，可执行文件通常位于：

```text
src-tauri\target\release\codex-provider-session-sync.exe
```

如果需要生成安装包，可以运行：

```powershell
pnpm tauri:build
```

注意：完整打包依赖 Tauri 当前平台的打包工具链，Windows 上可能需要额外安装 WebView2 / WiX 等组件。

## 使用说明

1. 先关闭正在写入会话的 Codex Desktop，避免首次聚合时和 Codex 同时写文件。
2. 启动本工具，进入设置页确认 `Codex Home` 是否指向 `C:\Users\<用户名>\.codex`。
3. 在 Provider 页确认源 provider 和目标 provider，例如 `openai`、`openrouter`、`custom`。
4. 打开同步开关，或通过托盘右键菜单启用同步。
5. 工具会按间隔扫描会话文件，把同一组对话镜像到其它 provider 下。
6. 双击任务栏托盘图标可以打开主窗口；右键菜单可关闭同步、设置同步间隔或退出程序。
7. 如果同步结果异常，进入设置页选择备份快照恢复。恢复前建议关闭 Codex Desktop。

常用路径：

- 配置：`C:\Users\<用户名>\.codex\provider-session-sync.json`
- 日志：`C:\Users\<用户名>\.codex\log\provider-sync-daemon.log`
- 备份：`C:\Users\<用户名>\Desktop\codex-provider-session-sync-backup`

## 项目结构

```text
.
├── assets/
│   └── provider-sync.ico
├── src-ui/
│   ├── main.tsx
│   └── styles.css
├── src-tauri/
│   ├── src/
│   │   ├── lib.rs
│   │   ├── main.rs
│   │   ├── settings.rs
│   │   └── sync.rs
│   ├── icons/
│   │   └── icon.ico
│   ├── Cargo.toml
│   ├── build.rs
│   └── tauri.conf.json
├── package.json
├── vite.config.ts
├── tailwind.config.js
└── tsconfig.json
```

## 开发

安装前端依赖：

```powershell
pnpm install
```

启动前端开发服务器：

```powershell
pnpm dev
```

启动 Tauri 开发模式：

```powershell
pnpm tauri:dev
```

构建前端：

```powershell
pnpm build
```

构建桌面应用：

```powershell
pnpm tauri:build
```

## 环境要求

- Node.js
- pnpm
- Rust toolchain，包括 `cargo` 和 `rustc`

本机如果没有 Rust，需要先安装 Rust 后才能运行 `pnpm tauri:dev` 或 `pnpm tauri:build`。

## 配置文件

设置保存到：

```text
C:\Users\<用户名>\.codex\provider-session-sync.json
```

包含：

- 是否启用同步
- 同步间隔
- provider 列表

默认日志：

```text
C:\Users\<用户名>\.codex\log\provider-sync-daemon.log
```

默认备份目录：

```text
C:\Users\<用户名>\Desktop\codex-provider-session-sync-backup
```

备份快照存放在：

```text
C:\Users\<用户名>\Desktop\codex-provider-session-sync-backup\snapshots\<时间戳>
```

快照包含：

- `sessions`
- `archived_sessions`
- `session_index.jsonl`
- `provider-session-sync.json`

默认保留策略：

- 自动同步前备份 `before-sync`：保留最近 24 个
- 手动备份 `manual`：保留最近 10 个
- 恢复前回退备份 `before-restore`：保留最近 10 个

恢复快照会覆盖当前 `.codex` 下对应文件和目录。执行恢复前建议关闭 Codex Desktop，避免应用同时写入会话文件。

## 安全说明

- 镜像文件需要改写 `session_meta.id`、`model_provider` 和 `forked_from_id`，不能用软链接代替。
- 工具只刷新自己能识别的镜像文件；遇到冲突会跳过。
- 恢复备份会覆盖当前会话数据，执行前确认目标快照时间点正确。
- 如果 Codex Desktop 后续更改会话 JSONL 或 `session_index.jsonl` 格式，需要重新验证同步逻辑。

## 维护计划

- 保持对 Codex Desktop 当前会话 JSONL 和 `session_index.jsonl` 格式的兼容。
- 继续优化备份策略，后续可加入按总容量或时间范围清理。
- 增加更多同步报告字段，让“会话组”“同步副本”“刷新镜像”的含义更直观。
- 补充同步核心逻辑的自动化测试，覆盖镜像刷新、冲突跳过、备份清理和恢复前备份。
- 发布 GitHub Release，提供可直接下载的 Windows 安装包或便携版 exe。

## License

[MIT](LICENSE)
