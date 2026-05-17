from __future__ import annotations

import argparse
import contextlib
import ctypes
import io
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

try:
    import msvcrt
except ImportError:  # pragma: no cover - Windows target.
    msvcrt = None  # type: ignore[assignment]


from provider_sync_settings import DEFAULT_INTERVAL_SECONDS, DEFAULT_PROVIDERS, MIN_INTERVAL_SECONDS, load_settings, update_settings


IS_WINDOWS = os.name == 'nt'


def default_codex_home() -> Path:
    raw = os.environ.get('CODEX_HOME')
    if raw:
        return Path(raw).expanduser()
    return Path.home() / '.codex'


def default_log_path() -> Path:
    return default_codex_home() / 'log' / 'provider-sync-daemon.log'


def default_backup_dir() -> Path:
    return Path.home() / 'Desktop' / 'codex-provider-session-sync-backup'


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run Codex provider session sync periodically in the background.')
    parser.add_argument('--codex-home', type=Path, default=default_codex_home())
    parser.add_argument('--backup-dir', type=Path, default=default_backup_dir())
    parser.add_argument('--log-file', type=Path, default=default_log_path())
    parser.add_argument('--interval-seconds', type=int, default=None)
    parser.add_argument('--provider', action='append', default=None, help='Provider to include. Repeatable.')
    parser.add_argument('--once', action='store_true', help='Run one sync cycle and exit.')
    parser.add_argument('--no-tray', action='store_true', help='Run without a Windows notification area icon.')
    return parser.parse_args()


def timestamp() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def append_log(log_file: Path, message: str) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open('a', encoding='utf-8') as stream:
        stream.write(f'[{timestamp()}] {message.rstrip()}\n')


if IS_WINDOWS:
    class GUID(ctypes.Structure):
        _fields_ = [
            ('Data1', ctypes.c_ulong),
            ('Data2', ctypes.c_ushort),
            ('Data3', ctypes.c_ushort),
            ('Data4', ctypes.c_ubyte * 8),
        ]


    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [
            ('cbSize', ctypes.c_ulong),
            ('hWnd', ctypes.c_void_p),
            ('uID', ctypes.c_uint),
            ('uFlags', ctypes.c_uint),
            ('uCallbackMessage', ctypes.c_uint),
            ('hIcon', ctypes.c_void_p),
            ('szTip', ctypes.c_wchar * 128),
            ('dwState', ctypes.c_ulong),
            ('dwStateMask', ctypes.c_ulong),
            ('szInfo', ctypes.c_wchar * 256),
            ('uTimeoutOrVersion', ctypes.c_uint),
            ('szInfoTitle', ctypes.c_wchar * 64),
            ('dwInfoFlags', ctypes.c_ulong),
            ('guidItem', GUID),
            ('hBalloonIcon', ctypes.c_void_p),
        ]


    class WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ('style', ctypes.c_uint),
            ('lpfnWndProc', ctypes.c_void_p),
            ('cbClsExtra', ctypes.c_int),
            ('cbWndExtra', ctypes.c_int),
            ('hInstance', ctypes.c_void_p),
            ('hIcon', ctypes.c_void_p),
            ('hCursor', ctypes.c_void_p),
            ('hbrBackground', ctypes.c_void_p),
            ('lpszMenuName', ctypes.c_wchar_p),
            ('lpszClassName', ctypes.c_wchar_p),
        ]


    class MSG(ctypes.Structure):
        _fields_ = [
            ('hwnd', ctypes.c_void_p),
            ('message', ctypes.c_uint),
            ('wParam', ctypes.c_void_p),
            ('lParam', ctypes.c_void_p),
            ('time', ctypes.c_ulong),
            ('pt_x', ctypes.c_long),
            ('pt_y', ctypes.c_long),
        ]


    class POINT(ctypes.Structure):
        _fields_ = [
            ('x', ctypes.c_long),
            ('y', ctypes.c_long),
        ]


def pythonw_path() -> str:
    return shutil_which('pythonw.exe') or shutil_which('python.exe') or sys.executable


def shutil_which(name: str) -> str | None:
    for directory in os.environ.get('PATH', '').split(os.pathsep):
        candidate = Path(directory) / name
        if candidate.exists():
            return str(candidate)
    return None


def control_invocation(codex_home: Path, mode: str | None = None) -> list[str]:
    root = app_root()
    control_exe = root / 'ProviderSyncControl.exe'
    if not control_exe.exists():
        control_exe = root / 'dist' / 'ProviderSyncControl.exe'
    args = ['--codex-home', str(codex_home)]
    if mode:
        args.append(mode)
    if control_exe.exists():
        return [str(control_exe), *args]
    return [pythonw_path(), str(root / 'src' / 'provider_sync_control.py'), *args]


def open_control_panel(codex_home: Path, mode: str | None = None) -> None:
    subprocess.Popen(control_invocation(codex_home, mode), cwd=app_root(), creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def another_daemon_is_running() -> bool:
    if not IS_WINDOWS:
        return False
    command = 'Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,CommandLine | ConvertTo-Json -Compress'
    result = subprocess.run(
        ['powershell', '-NoProfile', '-Command', command],
        text=True,
        capture_output=True,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        check=False,
    )
    import json

    try:
        processes = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False
    if isinstance(processes, dict):
        processes = [processes]
    current_pid = os.getpid()
    parent_pid: int | None = None
    for process in processes if isinstance(processes, list) else []:
        if isinstance(process, dict) and process.get('ProcessId') == current_pid:
            candidate = process.get('ParentProcessId')
            parent_pid = candidate if isinstance(candidate, int) else None
    for process in processes if isinstance(processes, list) else []:
        name = str(process.get('Name', '')) if isinstance(process, dict) else ''
        command_line = str(process.get('CommandLine', '')) if isinstance(process, dict) else ''
        process_id = process.get('ProcessId') if isinstance(process, dict) else None
        if not isinstance(process_id, int) or process_id in (current_pid, parent_pid):
            continue
        if name == 'ProviderSyncDaemon.exe' or 'provider_sync_daemon.py' in command_line or 'ProviderSyncDaemon.exe' in command_line:
            return True
    return False


class TrayIcon:
    MENU_OPEN = 1001
    MENU_TOGGLE = 1002
    MENU_INTERVAL = 1003
    MENU_EXIT = 1004

    def __init__(self, tooltip: str, stop_event: threading.Event, codex_home: Path) -> None:
        self.tooltip = tooltip
        self.stop_event = stop_event
        self.codex_home = codex_home
        self.thread: threading.Thread | None = None
        self.ready = threading.Event()
        self.hwnd: int | None = None
        self.wndproc_ref: object | None = None
        self.icon_data: object | None = None

    def start(self) -> None:
        if not IS_WINDOWS:
            return
        self.thread = threading.Thread(target=self._run, name='provider-sync-tray', daemon=True)
        self.thread.start()
        self.ready.wait(timeout=5)

    def stop(self) -> None:
        if not IS_WINDOWS or self.hwnd is None:
            return
        ctypes.windll.user32.PostMessageW(self.hwnd, 0x0010, 0, 0)
        if self.thread is not None:
            self.thread.join(timeout=3)

    def _run(self) -> None:
        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32
        kernel32 = ctypes.windll.kernel32

        WM_DESTROY = 0x0002
        WM_CLOSE = 0x0010
        WM_RBUTTONUP = 0x0205
        WM_LBUTTONDBLCLK = 0x0203
        WM_CONTEXTMENU = 0x007B
        NIM_ADD = 0x00000000
        NIM_DELETE = 0x00000002
        NIF_MESSAGE = 0x00000001
        NIF_ICON = 0x00000002
        NIF_TIP = 0x00000004
        WM_APP = 0x8000
        IDI_APPLICATION = 32512
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x00000010
        LR_DEFAULTSIZE = 0x00000040
        MF_STRING = 0x00000000
        TPM_RIGHTBUTTON = 0x00000002
        TPM_RETURNCMD = 0x00000100

        def show_menu(hwnd: int) -> None:
            point = POINT()
            user32.GetCursorPos(ctypes.byref(point))
            menu = user32.CreatePopupMenu()
            settings = load_settings(self.codex_home)
            toggle_label = '关闭同步' if settings.enabled else '开启同步'
            user32.AppendMenuW(menu, MF_STRING, self.MENU_OPEN, '打开面板')
            user32.AppendMenuW(menu, MF_STRING, self.MENU_TOGGLE, toggle_label)
            user32.AppendMenuW(menu, MF_STRING, self.MENU_INTERVAL, '设置同步间隔')
            user32.AppendMenuW(menu, MF_STRING, self.MENU_EXIT, '退出')
            user32.SetForegroundWindow(hwnd)
            command = user32.TrackPopupMenu(
                menu,
                TPM_RIGHTBUTTON | TPM_RETURNCMD,
                point.x,
                point.y,
                0,
                hwnd,
                None,
            )
            user32.DestroyMenu(menu)
            if command == self.MENU_OPEN:
                open_control_panel(self.codex_home)
            elif command == self.MENU_TOGGLE:
                update_settings(self.codex_home, enabled=not settings.enabled)
            elif command == self.MENU_INTERVAL:
                open_control_panel(self.codex_home, '--interval-dialog')
            elif command == self.MENU_EXIT:
                self.stop_event.set()
                user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)

        def wndproc(hwnd: int, msg: int, wparam: int, lparam: int) -> int:
            if msg == WM_APP + 1:
                if lparam == WM_LBUTTONDBLCLK:
                    open_control_panel(self.codex_home)
                    return 0
                if lparam in (WM_RBUTTONUP, WM_CONTEXTMENU):
                    show_menu(hwnd)
                    return 0
                return 0
            if msg in (WM_CLOSE, WM_DESTROY):
                if self.icon_data is not None:
                    shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self.icon_data))
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        wndproc_type = ctypes.WINFUNCTYPE(ctypes.c_longlong, ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p)
        self.wndproc_ref = wndproc_type(wndproc)
        class_name = 'CodexProviderSyncTray'
        instance = kernel32.GetModuleHandleW(None)
        window_class = WNDCLASSW()
        window_class.lpfnWndProc = ctypes.cast(self.wndproc_ref, ctypes.c_void_p).value
        window_class.hInstance = instance
        window_class.lpszClassName = class_name
        user32.RegisterClassW(ctypes.byref(window_class))
        hwnd = user32.CreateWindowExW(0, class_name, class_name, 0, 0, 0, 0, 0, None, None, instance, None)
        self.hwnd = hwnd

        icon_data = NOTIFYICONDATAW()
        icon_data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        icon_data.hWnd = hwnd
        icon_data.uID = 1
        icon_data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        icon_data.uCallbackMessage = WM_APP + 1
        custom_icon = icon_file()
        if custom_icon is not None:
            icon_data.hIcon = user32.LoadImageW(None, str(custom_icon), IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE)
        if not icon_data.hIcon:
            icon_data.hIcon = user32.LoadIconW(None, IDI_APPLICATION)
        icon_data.szTip = self.tooltip[:127]
        self.icon_data = icon_data
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(icon_data))
        self.ready.set()

        msg = MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))


@contextlib.contextmanager
def single_instance_lock(codex_home: Path):
    lock_path = codex_home / 'tmp' / 'provider-sync-daemon.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if IS_WINDOWS:
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        generic_read = 0x80000000
        generic_write = 0x40000000
        open_always = 4
        file_attribute_normal = 0x80
        invalid_handle = ctypes.c_void_p(-1).value
        kernel32.CreateFileW.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        kernel32.CreateFileW.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.CreateFileW(
            str(lock_path),
            generic_read | generic_write,
            0,
            None,
            open_always,
            file_attribute_normal,
            None,
        )
        if handle == invalid_handle:
            raise SystemExit('Another provider sync daemon is already running.')
        try:
            yield
        finally:
            kernel32.CloseHandle(handle)
        return

    with lock_path.open('a+b') as lock_file:
        lock_file.seek(0)
        if msvcrt is not None:
            try:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise SystemExit('Another provider sync daemon is already running.')
        try:
            yield
        finally:
            if msvcrt is not None:
                lock_file.seek(0)
                with contextlib.suppress(OSError):
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)


def sync_args(args: argparse.Namespace) -> list[str]:
    settings = load_settings(args.codex_home)
    providers = args.provider or list(settings.providers or DEFAULT_PROVIDERS)
    command = [
        '--mode',
        'mirror-all',
        '--codex-home',
        str(args.codex_home.expanduser().resolve()),
        '--backup-dir',
        str(args.backup_dir.expanduser().resolve()),
        '--apply',
    ]
    for provider in providers:
        command.extend(['--provider', provider])
    return command


def run_sync(args: argparse.Namespace) -> None:
    import provider_sync_v2

    stream = io.StringIO()
    with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
        exit_code = provider_sync_v2.main(sync_args(args))
    append_log(args.log_file, f'sync exit_code={exit_code}\n{stream.getvalue().strip()}')


def main() -> int:
    args = parse_args()
    if args.interval_seconds is not None and args.interval_seconds < MIN_INTERVAL_SECONDS:
        raise SystemExit(f'--interval-seconds must be at least {MIN_INTERVAL_SECONDS}.')

    args.codex_home = args.codex_home.expanduser().resolve()
    args.backup_dir = args.backup_dir.expanduser().resolve()
    args.log_file = args.log_file.expanduser().resolve()

    stop_event = threading.Event()
    setting_changes: dict[str, object] = {}
    if args.provider:
        setting_changes['providers'] = args.provider
    if args.interval_seconds is not None:
        setting_changes['interval_seconds'] = args.interval_seconds
    if setting_changes:
        update_settings(args.codex_home, **setting_changes)

    tray = TrayIcon('Codex Provider Sync running', stop_event, args.codex_home) if IS_WINDOWS and not args.no_tray and not args.once else None

    with single_instance_lock(args.codex_home):
        if tray is not None:
            tray.start()
        append_log(args.log_file, 'daemon started')
        try:
            while not stop_event.is_set():
                settings = load_settings(args.codex_home)
                if settings.enabled:
                    try:
                        run_sync(args)
                    except Exception as exc:
                        append_log(args.log_file, f'sync failed: {type(exc).__name__}: {exc}')
                else:
                    append_log(args.log_file, 'sync paused')
                if args.once:
                    append_log(args.log_file, 'daemon stopped after one cycle')
                    return 0
                stop_event.wait(load_settings(args.codex_home).interval_seconds or DEFAULT_INTERVAL_SECONDS)
        finally:
            if tray is not None:
                tray.stop()


if __name__ == '__main__':
    raise SystemExit(main())
