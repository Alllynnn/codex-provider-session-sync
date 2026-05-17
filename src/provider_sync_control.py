from __future__ import annotations

import os
import contextlib
import shutil
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

try:
    import winreg
except ImportError:  # pragma: no cover - Windows target.
    winreg = None  # type: ignore[assignment]


TASK_NAME = 'CodexProviderSessionSync'
PROVIDERS = ('openai', 'openrouter', 'custom')
INTERVAL_SECONDS = 300


def default_codex_home() -> Path:
    raw = os.environ.get('CODEX_HOME')
    if raw:
        return Path(raw).expanduser()
    return Path.home() / '.codex'


def app_root() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def icon_file() -> Path | None:
    for path in (
        app_root() / 'provider-sync.ico',
        app_root() / 'assets' / 'provider-sync.ico',
        Path(__file__).resolve().parent.parent / 'assets' / 'provider-sync.ico',
    ):
        if path.exists():
            return path
    return None


def pythonw_path() -> str:
    return shutil.which('pythonw.exe') or shutil.which('python.exe') or sys.executable


def daemon_invocation() -> tuple[list[str], str]:
    root = app_root()
    daemon_exe = root / 'ProviderSyncDaemon.exe'
    if not daemon_exe.exists():
        daemon_exe = root / 'dist' / 'ProviderSyncDaemon.exe'
    daemon_script = root / 'src' / 'provider_sync_daemon.py'
    codex_home = default_codex_home()
    args = [
        '--codex-home',
        str(codex_home),
        '--backup-dir',
        str(Path.home() / 'Desktop' / 'codex-session-sync-backup-v2'),
        '--log-file',
        str(codex_home / 'log' / 'provider-sync-daemon.log'),
        '--interval-seconds',
        str(INTERVAL_SECONDS),
    ]
    for provider in PROVIDERS:
        args.extend(['--provider', provider])
    if daemon_exe.exists():
        command = [str(daemon_exe), *args]
    else:
        command = [pythonw_path(), str(daemon_script), *args]
    return command, subprocess.list2cmdline(command)


def run_key_path() -> str:
    return r'Software\Microsoft\Windows\CurrentVersion\Run'


def is_autostart_enabled() -> bool:
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key_path(), 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, TASK_NAME)
        return True
    except OSError:
        return False


def set_autostart(enabled: bool) -> None:
    if winreg is None:
        raise RuntimeError('Windows registry is unavailable.')
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, run_key_path()) as key:
        if enabled:
            _, command_line = daemon_invocation()
            winreg.SetValueEx(key, TASK_NAME, 0, winreg.REG_SZ, command_line)
        else:
            try:
                winreg.DeleteValue(key, TASK_NAME)
            except FileNotFoundError:
                pass


def creation_flags() -> int:
    return getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def query_daemon_process_ids() -> list[int]:
    command = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { "
        "$_.CommandLine -like '*provider_sync_daemon.py*' -or "
        "$_.Name -eq 'ProviderSyncDaemon.exe' -or "
        "$_.CommandLine -like '*ProviderSyncDaemon.exe*' "
        "} | ForEach-Object { $_.ProcessId }"
    )
    result = subprocess.run(
        ['powershell', '-NoProfile', '-Command', command],
        text=True,
        capture_output=True,
        creationflags=creation_flags(),
        check=False,
    )
    ids: list[int] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.isdigit():
            ids.append(int(line))
    return ids


def is_daemon_running() -> bool:
    return bool(query_daemon_process_ids())


def start_daemon() -> None:
    command, _ = daemon_invocation()
    subprocess.Popen(command, cwd=app_root(), creationflags=creation_flags())


def stop_daemon() -> None:
    ids = query_daemon_process_ids()
    if not ids:
        return
    id_list = ','.join(str(process_id) for process_id in ids)
    subprocess.run(
        ['powershell', '-NoProfile', '-Command', f'Stop-Process -Id {id_list} -Force'],
        text=True,
        capture_output=True,
        creationflags=creation_flags(),
        check=False,
    )


class SyncControlApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title('Codex Provider Sync')
        self.root.geometry('360x190')
        self.root.resizable(False, False)
        icon = icon_file()
        if icon is not None:
            with contextlib.suppress(tk.TclError):
                self.root.iconbitmap(str(icon))
        self.enabled = tk.BooleanVar(value=is_autostart_enabled())

        self.title = tk.Label(self.root, text='Codex Provider Sync', font=('Segoe UI', 16, 'bold'))
        self.title.pack(pady=(22, 6))

        self.switch = tk.Checkbutton(
            self.root,
            text='自动同步',
            variable=self.enabled,
            indicatoron=False,
            width=18,
            height=2,
            font=('Microsoft YaHei UI', 12, 'bold'),
            command=self.toggle,
        )
        self.switch.pack(pady=10)

        self.status = tk.Label(self.root, text='', font=('Microsoft YaHei UI', 10), fg='#4b5563')
        self.status.pack(pady=(4, 0))
        self.path_label = tk.Label(
            self.root,
            text=str(default_codex_home() / 'log' / 'provider-sync-daemon.log'),
            font=('Segoe UI', 8),
            fg='#6b7280',
        )
        self.path_label.pack(pady=(6, 0))

        self.refresh()
        self.root.after(3000, self.periodic_refresh)

    def set_busy(self, busy: bool) -> None:
        self.switch.configure(state=tk.DISABLED if busy else tk.NORMAL)

    def refresh(self) -> None:
        enabled = is_autostart_enabled()
        running = is_daemon_running()
        self.enabled.set(enabled)
        if enabled and running:
            self.status.configure(text='已开启，后台同步正在运行', fg='#047857')
            self.switch.configure(bg='#16a34a', activebackground='#16a34a', fg='white', selectcolor='#16a34a')
        elif enabled:
            self.status.configure(text='已开启，后台进程未运行', fg='#b45309')
            self.switch.configure(bg='#f59e0b', activebackground='#f59e0b', fg='white', selectcolor='#f59e0b')
        else:
            self.status.configure(text='已关闭', fg='#6b7280')
            self.switch.configure(bg='#e5e7eb', activebackground='#e5e7eb', fg='#111827', selectcolor='#e5e7eb')

    def periodic_refresh(self) -> None:
        self.refresh()
        self.root.after(3000, self.periodic_refresh)

    def toggle(self) -> None:
        self.set_busy(True)
        try:
            if self.enabled.get():
                set_autostart(True)
                if not is_daemon_running():
                    start_daemon()
            else:
                set_autostart(False)
                stop_daemon()
        except Exception as exc:
            messagebox.showerror('Codex Provider Sync', str(exc))
        finally:
            self.set_busy(False)
            self.refresh()

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    SyncControlApp().run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
