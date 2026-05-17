# Codex Session Sync

中文说明: [README.zh-CN.md](README.zh-CN.md)

Local utility for synchronizing Codex conversation visibility across model providers. It supports one-shot CLI sync, a local Web UI, a background daemon, Windows autostart, and a small control GUI.

By default, write-capable tools run in preview mode. Real writes require explicit confirmation in the Web UI or `--apply` on the command line.

## UI Preview

![Codex Session Sync Web UI](assets/ui-screenshot.png)

## Project Structure

- `src/m.py`: original sync CLI with preview, apply, backup, and legacy SQLite handling.
- `src/m_webui.py`: local Web UI entrypoint.
- `src/provider_sync_v2.py`: newer provider session-file sync engine.
- `src/provider_sync_daemon.py`: background sync daemon with tray icon support.
- `src/provider_sync_control.py`: Windows GUI control panel.
- `assets/`: screenshots and application icons.
- `packaging/pyinstaller/`: PyInstaller specs.
- `scripts/windows/`: Windows autostart install and uninstall scripts.
- `tools/`: recovery and maintenance scripts.
- `requirements.txt`: packaging dependency list.
- `.gitignore`: ignores local builds, logs, virtual environments, and backups.

`build/`, `dist/`, `.build-venv/`, logs, and `__pycache__/` are local generated artifacts and are not intended to be committed.

## Background Sync

Install dependencies:

```powershell
python -m pip install -r .\requirements.txt
```

Install autostart and start the daemon:

```powershell
.\scripts\windows\install_provider_sync_autostart.ps1
```

Uninstall autostart:

```powershell
.\scripts\windows\uninstall_provider_sync_autostart.ps1
```

The default providers are `openai`, `openrouter`, and `custom`. Edit the `--provider` arguments in `scripts/windows/install_provider_sync_autostart.ps1` if your local provider names differ.

## Control GUI

Run from source:

```powershell
python .\src\provider_sync_control.py
```

Run a packaged build:

```powershell
.\dist\ProviderSyncControl.exe
```

## Web UI

Run:

```powershell
python .\src\m_webui.py
```

The Web UI opens a local browser page on `127.0.0.1`.

## CLI

Preview mutual provider synchronization:

```powershell
python .\src\m.py --sync-all-providers-mutually
```

Apply mutual provider synchronization:

```powershell
python .\src\m.py --sync-all-providers-mutually --backup-dir .\backup --apply
```

Other supported modes:

```powershell
python .\src\m.py --sync-openai-to-all-providers --source-provider openai
python .\src\m.py --target-provider openai --backup-dir .\backup --apply
```

## Rebuild EXEs

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

If Windows reports that an executable is in use, stop the daemon or control panel before rebuilding.

## Safety Notes

- Preview first.
- Back up before `--apply`.
- Existing conflicting files are skipped and reported, not overwritten.
- Do not commit `dist/`, `build/`, logs, or local backup directories.
