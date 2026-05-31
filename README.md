# Codex Provider Session Sync

中文说明: [README.zh-CN.md](README.zh-CN.md)

Codex Provider Session Sync is a Tauri desktop app that automatically aggregates Codex Desktop sessions across multiple `model_provider` values. It keeps the same conversation history visible when switching between providers such as `openai`, `openrouter`, and `custom`.

## Tech Stack

- Desktop shell: Tauri v2
- Frontend: React + TypeScript + Vite
- Styling: Tailwind CSS
- Icons: lucide-react
- Backend: Rust

The old Python / PyInstaller implementation has been removed. The sync engine now lives in `src-tauri`.

## Features

- Scans `.codex/sessions` and `.codex/archived_sessions`.
- Groups sessions by `session_meta.id`, `forked_from_id`, and `model_provider`.
- Creates deterministic mirror session IDs for other providers.
- Creates or refreshes provider-specific JSONL mirrors.
- Updates `.codex/session_index.jsonl`.
- Runs an automatic background sync loop.
- Tray icon: double-click opens the main window; right-click shows open, enable/disable sync, set interval, and exit actions.
- Main window: CC Switch-style white card UI adapted for sync status, providers, interval, autostart, and log actions.
- Windows autostart: the top-right settings button writes or removes the HKCU Run entry.
- Backup and restore: every sync creates a pre-sync snapshot; the main window can create manual backups and restore a selected snapshot.
- Backup pruning: automatic sync creates a snapshot only when changes are needed, then prunes older snapshots by category.

## Installation

### Option 1: Download a Release

1. Open GitHub Releases: <https://github.com/Alllynnn/codex-provider-session-sync/releases>
2. Download the latest Windows installer or executable.
3. Start `Codex Provider Session Sync`.
4. Check providers and sync interval in Settings.
5. Enable autostart if you want the sync loop to start with Windows.

If no release has been published yet, build from source.

### Option 2: Build From Source

Requirements:

- Node.js
- pnpm
- Rust toolchain with `cargo` and `rustc`

Build:

```powershell
git clone https://github.com/Alllynnn/codex-provider-session-sync.git
cd codex-provider-session-sync
pnpm install
pnpm build
pnpm exec tauri build --no-bundle
```

The executable is usually written to:

```text
src-tauri\target\release\codex-provider-session-sync.exe
```

To build full installers, run:

```powershell
pnpm tauri:build
```

Full bundling depends on the current Tauri platform toolchain. On Windows, extra WebView2 / WiX components may be required.

## Usage

1. Close Codex Desktop before first sync if it is actively writing session files.
2. Start this app and confirm `Codex Home` points to `C:\Users\<user>\.codex`.
3. Open the Provider tab and confirm source and target providers, such as `openai`, `openrouter`, and `custom`.
4. Enable sync from the main window or the tray menu.
5. The app periodically scans session files and mirrors each conversation group across providers.
6. Double-click the tray icon to open the main window. Right-click it to enable/disable sync, change interval, or exit.
7. If sync output looks wrong, restore a known-good backup snapshot from Settings. Close Codex Desktop before restoring.

Useful paths:

- Settings: `C:\Users\<user>\.codex\provider-session-sync.json`
- Log: `C:\Users\<user>\.codex\log\provider-sync-daemon.log`
- Backup root: `C:\Users\<user>\Desktop\codex-provider-session-sync-backup`

## Project Structure

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

## Development

Install frontend dependencies:

```powershell
pnpm install
```

Run the frontend dev server:

```powershell
pnpm dev
```

Run Tauri dev mode:

```powershell
pnpm tauri:dev
```

Build the frontend:

```powershell
pnpm build
```

Build the desktop app:

```powershell
pnpm tauri:build
```

## Requirements

- Node.js
- pnpm
- Rust toolchain with `cargo` and `rustc`

If Rust is not installed, `pnpm tauri:dev` and `pnpm tauri:build` cannot run.

## Settings

Settings are saved to:

```text
C:\Users\<user>\.codex\provider-session-sync.json
```

The file stores enabled state, sync interval, and provider names.

Default log file:

```text
C:\Users\<user>\.codex\log\provider-sync-daemon.log
```

Default backup directory:

```text
C:\Users\<user>\Desktop\codex-provider-session-sync-backup
```

Backup snapshots are stored under:

```text
C:\Users\<user>\Desktop\codex-provider-session-sync-backup\snapshots\<timestamp>
```

Each snapshot includes:

- `sessions`
- `archived_sessions`
- `session_index.jsonl`
- `provider-session-sync.json`

Restoring a snapshot overwrites the matching files and directories in `.codex`. Close Codex Desktop before restoring to avoid concurrent writes.

## Safety Notes

- Mirror files rewrite `session_meta.id`, `model_provider`, and `forked_from_id`, so symlinks cannot replace mirror files.
- The tool only refreshes mirrors it can identify and skips conflicts.
- Restoring a backup overwrites current session data. Check the snapshot timestamp before restoring.
- If Codex Desktop changes its JSONL or `session_index.jsonl` format, re-check the sync logic before applying writes.

## Maintenance Plan

- Keep compatibility with the current Codex Desktop session JSONL and `session_index.jsonl` formats.
- Improve backup retention with optional size and age limits.
- Make dashboard metrics clearer, especially session groups, mirror copies, and refreshed mirrors.
- Add automated tests for mirror refresh, conflict skipping, backup pruning, and pre-restore snapshots.
- Publish GitHub Releases with a Windows installer or portable exe.

## License

[MIT](LICENSE)
