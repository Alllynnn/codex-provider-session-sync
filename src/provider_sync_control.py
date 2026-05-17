from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog

from provider_sync_settings import MIN_INTERVAL_SECONDS, SyncSettings, default_codex_home, load_settings, save_settings, update_settings

try:
    import winreg
except ImportError:  # pragma: no cover - Windows target.
    winreg = None  # type: ignore[assignment]


TASK_NAME = 'CodexProviderSessionSync'


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


def log_file(codex_home: Path) -> Path:
    return codex_home / 'log' / 'provider-sync-daemon.log'


def backup_dir() -> Path:
    return Path.home() / 'Desktop' / 'codex-provider-session-sync-backup'


def daemon_invocation(codex_home: Path) -> tuple[list[str], str]:
    root = app_root()
    daemon_exe = root / 'ProviderSyncDaemon.exe'
    if not daemon_exe.exists():
        daemon_exe = root / 'dist' / 'ProviderSyncDaemon.exe'
    daemon_script = root / 'src' / 'provider_sync_daemon.py'
    settings = load_settings(codex_home)
    args = [
        '--codex-home',
        str(codex_home),
        '--backup-dir',
        str(backup_dir()),
        '--log-file',
        str(log_file(codex_home)),
        '--interval-seconds',
        str(settings.interval_seconds),
    ]
    for provider in settings.providers:
        args.extend(['--provider', provider])
    if daemon_exe.exists():
        command = [str(daemon_exe), *args]
    else:
        command = [pythonw_path(), str(daemon_script), *args]
    return command, subprocess.list2cmdline(command)


def once_invocation(codex_home: Path) -> list[str]:
    command, _ = daemon_invocation(codex_home)
    return [*command, '--once', '--no-tray']


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


def set_autostart(enabled: bool, codex_home: Path) -> None:
    if winreg is None:
        raise RuntimeError('Windows registry is unavailable.')
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, run_key_path()) as key:
        if enabled:
            _, command_line = daemon_invocation(codex_home)
            winreg.SetValueEx(key, TASK_NAME, 0, winreg.REG_SZ, command_line)
        else:
            try:
                winreg.DeleteValue(key, TASK_NAME)
            except FileNotFoundError:
                pass


def creation_flags() -> int:
    return getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def query_daemon_process_ids() -> list[int]:
    command = 'Get-CimInstance Win32_Process | Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress'
    result = subprocess.run(
        ['powershell', '-NoProfile', '-Command', command],
        text=True,
        capture_output=True,
        creationflags=creation_flags(),
        check=False,
    )
    import json

    try:
        processes = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    if isinstance(processes, dict):
        processes = [processes]
    ids: list[int] = []
    for process in processes if isinstance(processes, list) else []:
        if not isinstance(process, dict):
            continue
        process_id = process.get('ProcessId')
        name = str(process.get('Name', ''))
        command_line = str(process.get('CommandLine', ''))
        if not isinstance(process_id, int):
            continue
        if name == 'ProviderSyncDaemon.exe' or 'provider_sync_daemon.py' in command_line or 'ProviderSyncDaemon.exe' in command_line:
            ids.append(process_id)
    return ids


def is_daemon_running() -> bool:
    return bool(query_daemon_process_ids())


def start_daemon(codex_home: Path) -> None:
    command, _ = daemon_invocation(codex_home)
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
    def __init__(self, codex_home: Path) -> None:
        self.codex_home = codex_home
        self.root = tk.Tk()
        self.root.title('Codex Provider Session Sync')
        self.root.geometry('900x620')
        self.root.minsize(760, 520)
        self.root.configure(bg='#f8fafc')
        icon = icon_file()
        if icon is not None:
            with contextlib.suppress(tk.TclError):
                self.root.iconbitmap(str(icon))

        self.sync_enabled = tk.BooleanVar(value=load_settings(self.codex_home).enabled)
        self.autostart_enabled = tk.BooleanVar(value=is_autostart_enabled())
        self.status_text = tk.StringVar(value='')
        self.interval_text = tk.StringVar(value='')
        self.providers_text = tk.StringVar(value='')

        self.build_ui()
        self.refresh()
        self.root.after(3000, self.periodic_refresh)

    def build_ui(self) -> None:
        header = tk.Frame(self.root, bg='#f8fafc')
        header.pack(fill=tk.X, padx=24, pady=(22, 14))

        title = tk.Label(header, text='Codex Sync', bg='#f8fafc', fg='#0f172a', font=('Segoe UI', 20, 'bold'))
        title.pack(side=tk.LEFT)

        self.sync_switch = tk.Checkbutton(
            header,
            text='自动同步',
            variable=self.sync_enabled,
            command=self.toggle_sync,
            indicatoron=False,
            width=12,
            height=2,
            borderwidth=0,
            font=('Microsoft YaHei UI', 10, 'bold'),
        )
        self.sync_switch.pack(side=tk.LEFT, padx=(28, 0))

        settings_button = tk.Button(
            header,
            text='设置间隔',
            command=self.ask_interval,
            bg='#eef2ff',
            fg='#1d4ed8',
            relief=tk.FLAT,
            padx=16,
            pady=9,
            font=('Microsoft YaHei UI', 10),
        )
        settings_button.pack(side=tk.RIGHT, padx=(8, 0))

        self.autostart_button = tk.Button(
            header,
            text='开机自启',
            command=self.toggle_autostart,
            bg='#f1f5f9',
            fg='#334155',
            relief=tk.FLAT,
            padx=16,
            pady=9,
            font=('Microsoft YaHei UI', 10),
        )
        self.autostart_button.pack(side=tk.RIGHT, padx=(8, 0))

        nav = tk.Frame(self.root, bg='#f8fafc')
        nav.pack(fill=tk.X, padx=24)
        for label, active in (('同步', True), ('Provider', False), ('日志', False), ('设置', False)):
            tk.Label(
                nav,
                text=label,
                bg='#ffffff' if active else '#f1f5f9',
                fg='#0f172a' if active else '#64748b',
                padx=20,
                pady=10,
                font=('Microsoft YaHei UI', 10, 'bold' if active else 'normal'),
            ).pack(side=tk.LEFT, padx=(0, 8))

        body = tk.Frame(self.root, bg='#f8fafc')
        body.pack(fill=tk.BOTH, expand=True, padx=28, pady=20)

        self.status_card = self.card(body)
        self.status_card.pack(fill=tk.X, pady=(0, 14))
        tk.Label(self.status_card, text='会话自动聚合', bg='#ffffff', fg='#0f172a', font=('Microsoft YaHei UI', 13, 'bold')).pack(
            anchor='w', padx=22, pady=(18, 4)
        )
        tk.Label(self.status_card, textvariable=self.status_text, bg='#ffffff', fg='#475569', font=('Microsoft YaHei UI', 10)).pack(
            anchor='w', padx=22, pady=(0, 16)
        )

        metrics = tk.Frame(body, bg='#f8fafc')
        metrics.pack(fill=tk.X, pady=(0, 14))
        self.metric_card(metrics, '同步间隔', self.interval_text).pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))
        self.metric_card(metrics, 'Provider 范围', self.providers_text).pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0))

        provider_card = self.card(body)
        provider_card.pack(fill=tk.BOTH, expand=True)
        tk.Label(provider_card, text='Provider 聚合列表', bg='#ffffff', fg='#0f172a', font=('Microsoft YaHei UI', 12, 'bold')).pack(
            anchor='w', padx=22, pady=(18, 8)
        )
        self.provider_list = tk.Frame(provider_card, bg='#ffffff')
        self.provider_list.pack(fill=tk.X, padx=18)

        actions = tk.Frame(self.root, bg='#f8fafc')
        actions.pack(fill=tk.X, padx=28, pady=(0, 24))
        self.action_button(actions, '立即同步一次', self.run_once).pack(side=tk.LEFT, padx=(0, 10))
        self.action_button(actions, '打开日志', self.open_log).pack(side=tk.LEFT, padx=(0, 10))
        self.action_button(actions, '退出后台', self.exit_daemon).pack(side=tk.RIGHT)

    def card(self, parent: tk.Misc) -> tk.Frame:
        frame = tk.Frame(parent, bg='#ffffff', highlightthickness=1, highlightbackground='#e2e8f0')
        return frame

    def metric_card(self, parent: tk.Misc, title: str, value: tk.StringVar) -> tk.Frame:
        frame = self.card(parent)
        tk.Label(frame, text=title, bg='#ffffff', fg='#64748b', font=('Microsoft YaHei UI', 9)).pack(anchor='w', padx=18, pady=(16, 2))
        tk.Label(frame, textvariable=value, bg='#ffffff', fg='#0f172a', font=('Segoe UI', 18, 'bold')).pack(anchor='w', padx=18, pady=(0, 16))
        return frame

    def action_button(self, parent: tk.Misc, text: str, command) -> tk.Button:
        return tk.Button(parent, text=text, command=command, bg='#ffffff', fg='#0f172a', relief=tk.FLAT, padx=18, pady=10)

    def provider_row(self, parent: tk.Misc, name: str, active: bool) -> None:
        row = tk.Frame(parent, bg='#eff6ff' if active else '#ffffff', highlightthickness=1, highlightbackground='#93c5fd' if active else '#e2e8f0')
        row.pack(fill=tk.X, pady=6)
        tk.Label(row, text='⋮⋮', bg=row['bg'], fg='#cbd5e1', font=('Segoe UI', 13)).pack(side=tk.LEFT, padx=(16, 12), pady=14)
        tk.Label(row, text=name[:1].upper(), bg='#ffffff', fg='#0f172a', width=3, height=1, font=('Segoe UI', 10)).pack(side=tk.LEFT, padx=(0, 12))
        tk.Label(row, text=name, bg=row['bg'], fg='#0f172a', font=('Segoe UI', 11, 'bold')).pack(side=tk.LEFT, pady=14)
        tk.Label(row, text='已纳入聚合' if active else '待同步', bg=row['bg'], fg='#2563eb', font=('Microsoft YaHei UI', 9)).pack(side=tk.RIGHT, padx=18)

    def set_busy(self, busy: bool) -> None:
        state = tk.DISABLED if busy else tk.NORMAL
        self.sync_switch.configure(state=state)
        self.autostart_button.configure(state=state)

    def refresh(self) -> None:
        settings = load_settings(self.codex_home)
        running = is_daemon_running()
        self.sync_enabled.set(settings.enabled)
        self.autostart_enabled.set(is_autostart_enabled())
        state = '运行中' if running else '未运行'
        enabled = '已开启' if settings.enabled else '已关闭'
        self.status_text.set(f'{enabled}，后台进程{state}。Codex Home: {self.codex_home}')
        self.interval_text.set(f'{settings.interval_seconds} 秒')
        self.providers_text.set(', '.join(settings.providers))

        if settings.enabled:
            self.sync_switch.configure(bg='#2563eb', activebackground='#2563eb', fg='white', selectcolor='#2563eb')
        else:
            self.sync_switch.configure(bg='#e2e8f0', activebackground='#e2e8f0', fg='#0f172a', selectcolor='#e2e8f0')

        self.autostart_button.configure(text='关闭自启' if self.autostart_enabled.get() else '开机自启')
        for child in self.provider_list.winfo_children():
            child.destroy()
        for index, provider in enumerate(settings.providers):
            self.provider_row(self.provider_list, provider, active=index == 0)

    def periodic_refresh(self) -> None:
        self.refresh()
        self.root.after(3000, self.periodic_refresh)

    def toggle_sync(self) -> None:
        self.set_busy(True)
        try:
            settings = update_settings(self.codex_home, enabled=self.sync_enabled.get())
            if settings.enabled and not is_daemon_running():
                start_daemon(self.codex_home)
        except Exception as exc:
            messagebox.showerror('Codex Provider Sync', str(exc))
        finally:
            self.set_busy(False)
            self.refresh()

    def toggle_autostart(self) -> None:
        self.set_busy(True)
        try:
            enabled = not is_autostart_enabled()
            set_autostart(enabled, self.codex_home)
            if enabled and not is_daemon_running():
                start_daemon(self.codex_home)
        except Exception as exc:
            messagebox.showerror('Codex Provider Sync', str(exc))
        finally:
            self.set_busy(False)
            self.refresh()

    def ask_interval(self) -> None:
        settings = load_settings(self.codex_home)
        value = simpledialog.askinteger(
            '设置同步间隔',
            f'输入同步间隔秒数，最小 {MIN_INTERVAL_SECONDS} 秒：',
            initialvalue=settings.interval_seconds,
            minvalue=MIN_INTERVAL_SECONDS,
            parent=self.root,
        )
        if value is None:
            return
        save_settings(SyncSettings(settings.enabled, value, settings.providers), self.codex_home)
        self.refresh()

    def run_once(self) -> None:
        subprocess.Popen(once_invocation(self.codex_home), cwd=app_root(), creationflags=creation_flags())
        messagebox.showinfo('Codex Provider Sync', '已启动一次同步，结果会写入日志。')

    def open_log(self) -> None:
        path = log_file(self.codex_home)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        os.startfile(path)  # type: ignore[attr-defined]

    def exit_daemon(self) -> None:
        stop_daemon()
        self.refresh()

    def run(self) -> None:
        self.root.mainloop()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Codex provider session sync control panel.')
    parser.add_argument('--codex-home', type=Path, default=default_codex_home())
    parser.add_argument('--interval-dialog', action='store_true')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    codex_home = args.codex_home.expanduser().resolve()
    app = SyncControlApp(codex_home)
    if args.interval_dialog:
        app.root.after(200, app.ask_interval)
    app.run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
