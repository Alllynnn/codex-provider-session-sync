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
