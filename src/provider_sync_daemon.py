from __future__ import annotations

import argparse
import contextlib
import ctypes
import hashlib
import io
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

try:
    import msvcrt
except ImportError:  # pragma: no cover - Windows target.
    msvcrt = None  # type: ignore[assignment]


DEFAULT_INTERVAL_SECONDS = 300
DEFAULT_TASK_PROVIDERS = ('openai', 'openrouter', 'custom')
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
    parser.add_argument('--interval-seconds', type=int, default=DEFAULT_INTERVAL_SECONDS)
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


class TrayIcon:
    MENU_EXIT = 1001

    def __init__(self, tooltip: str, stop_event: threading.Event) -> None:
        self.tooltip = tooltip
        self.stop_event = stop_event
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
            if command == self.MENU_EXIT:
                self.stop_event.set()
                user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)

        def wndproc(hwnd: int, msg: int, wparam: int, lparam: int) -> int:
            if msg == WM_APP + 1 and lparam in (WM_RBUTTONUP, WM_CONTEXTMENU):
                show_menu(hwnd)
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
    if IS_WINDOWS:
        kernel32 = ctypes.windll.kernel32
        ERROR_ALREADY_EXISTS = 183
        digest = hashlib.sha256(str(codex_home).lower().encode('utf-8')).hexdigest()[:16]
        mutex_name = f'Local\\CodexProviderSync-{digest}'
        handle = kernel32.CreateMutexW(None, True, mutex_name)
        if not handle:
            raise SystemExit('Could not create provider sync mutex.')
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            raise SystemExit('Another provider sync daemon is already running.')
        try:
            yield
        finally:
            kernel32.ReleaseMutex(handle)
            kernel32.CloseHandle(handle)
        return

    lock_path = codex_home / 'tmp' / 'provider-sync-daemon.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
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
    providers = args.provider or list(DEFAULT_TASK_PROVIDERS)
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
    if args.interval_seconds < 30:
        raise SystemExit('--interval-seconds must be at least 30.')

    args.codex_home = args.codex_home.expanduser().resolve()
    args.backup_dir = args.backup_dir.expanduser().resolve()
    args.log_file = args.log_file.expanduser().resolve()

    stop_event = threading.Event()
    tray = TrayIcon('Codex Provider Sync running', stop_event) if IS_WINDOWS and not args.no_tray and not args.once else None

    with single_instance_lock(args.codex_home):
        if tray is not None:
            tray.start()
        append_log(args.log_file, 'daemon started')
        try:
            while not stop_event.is_set():
                try:
                    run_sync(args)
                except Exception as exc:
                    append_log(args.log_file, f'sync failed: {type(exc).__name__}: {exc}')
                if args.once:
                    append_log(args.log_file, 'daemon stopped after one cycle')
                    return 0
                stop_event.wait(args.interval_seconds)
        finally:
            if tray is not None:
                tray.stop()


if __name__ == '__main__':
    raise SystemExit(main())
