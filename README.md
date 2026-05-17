# Codex Provider Session Sync

中文说明: [README.zh-CN.md](README.zh-CN.md)

Codex Provider Session Sync is a local background utility for Codex Desktop. It automatically aggregates conversation history across multiple `model_provider` values so the same sessions remain visible when switching between providers such as `openai`, `openrouter`, and `custom`.

## What It Does

Codex Desktop stores the provider name inside session JSONL metadata. After switching providers, sessions that belong only to another provider may disappear from the current provider view. This project keeps those views aligned by:

- scanning `.codex/sessions` and `.codex/archived_sessions`;
- reading each source session's `session_meta.id` and `model_provider`;
- creating deterministic mirror session IDs for the other providers;
- writing provider-specific mirror JSONL files;
- updating `.codex/session_index.jsonl`;
- periodically refreshing stale mirrors when source sessions receive new messages.

Generated mirrors include `forked_from_id`, so the sync engine does not re-use its own mirrors as new source sessions.

## Features

- Mutual aggregation across multiple providers.
- Background daemon with configurable sync interval.
- Windows tray icon: double-click opens the control panel; right-click shows open, enable/disable sync, set interval, and exit actions.
- Card-style GUI control panel showing sync state, providers, interval, log path, and common actions.
- Windows autostart install and uninstall scripts.
- CLI preview mode by default; writes require `--apply`.
- Local artifacts such as `dist/`, `build/`, logs, and backups are ignored by Git.

## Project Structure

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
│   ├── provider_sync_settings.py
│   └── provider_sync_v2.py
├── .gitignore
├── README.md
├── README.zh-CN.md
└── requirements.txt
```

## Quick Start

Install dependencies:

```powershell
python -m pip install -r .\requirements.txt
```

Preview a sync:

```powershell
python .\src\provider_sync_v2.py --mode mirror-all --provider openai --provider openrouter --provider custom
```

Apply one sync cycle:

```powershell
python .\src\provider_sync_v2.py --mode mirror-all --provider openai --provider openrouter --provider custom --apply
```

Start the background daemon:

```powershell
python .\src\provider_sync_daemon.py --provider openai --provider openrouter --provider custom
```

When the daemon is running:

- Double-click the tray icon to open the control panel.
- Right-click the tray icon to open the panel, enable/disable sync, set the sync interval, or exit.
- Disable sync pauses automatic aggregation; exit stops the background process.

## Windows Autostart

Install autostart and start the daemon:

```powershell
.\scripts\windows\install_provider_sync_autostart.ps1
```

Uninstall autostart and stop the daemon:

```powershell
.\scripts\windows\uninstall_provider_sync_autostart.ps1
```

Default backup directory:

```text
C:\Users\<user>\Desktop\codex-provider-session-sync-backup
```

Default log file:

```text
C:\Users\<user>\.codex\log\provider-sync-daemon.log
```

## Control GUI

Run from source:

```powershell
python .\src\provider_sync_control.py
```

Run a packaged build:

```powershell
.\dist\ProviderSyncControl.exe
```

The control panel shows daemon status and provides a single switch for background sync.

Settings are stored in:

```text
C:\Users\<user>\.codex\provider-session-sync.json
```

The file stores enabled state, sync interval, and provider names. The daemon reads it before every cycle, so interval changes do not require a daemon restart.

## Packaging

From the project root:

```powershell
python -m PyInstaller --clean --noconfirm .\packaging\pyinstaller\ProviderSyncDaemon.spec
python -m PyInstaller --clean --noconfirm .\packaging\pyinstaller\ProviderSyncControl.spec
```

Outputs:

```text
dist/ProviderSyncDaemon.exe
dist/ProviderSyncControl.exe
```

## Safety Notes

- Preview first by omitting `--apply`.
- The tool creates or refreshes its own mirror files and skips unrecognized conflicts.
- Mirror files rewrite `session_meta.id`, `model_provider`, and `forked_from_id`, so symlinks cannot replace mirror files.
- If Codex Desktop changes its session file format, re-check the `session_meta` and `session_index.jsonl` handling before applying writes.
