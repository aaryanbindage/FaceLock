"""Win32 helpers via ctypes: global hotkey, keyboard blocker, monitors, locking."""

import ctypes
import threading
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WM_QUIT = 0x0012
WM_HOTKEY = 0x0312
WH_KEYBOARD_LL = 13

HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                     ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, MONITORENUMPROC, wintypes.LPARAM]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def set_dpi_aware():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


_instance_mutex = None


def acquire_single_instance(name="FaceLock_SingleInstance", wait_seconds=0):
    """True if we are the only FaceLock. wait_seconds lets a restarting instance wait for the old one."""
    global _instance_mutex
    import time
    deadline = time.monotonic() + wait_seconds
    while True:
        _instance_mutex = kernel32.CreateMutexW(None, False, name)
        if ctypes.get_last_error() != 183:  # ERROR_ALREADY_EXISTS
            return True
        kernel32.CloseHandle(_instance_mutex)
        _instance_mutex = None
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.25)


def lock_workstation():
    user32.LockWorkStation()


def monitors():
    """Return [(left, top, width, height, is_primary), ...] in physical pixels."""
    rects = []

    def cb(_hmon, _hdc, rect, _data):
        r = rect.contents
        rects.append((r.left, r.top, r.right - r.left, r.bottom - r.top,
                      r.left <= 0 < r.right and r.top <= 0 < r.bottom))
        return True

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(cb), 0)
    if rects and not any(r[4] for r in rects):
        rects[0] = rects[0][:4] + (True,)
    return rects or [(0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1), True)]


class MessageLoopThread(threading.Thread):
    """A thread with a Win32 message queue. Subclasses override setup/teardown/on_message."""

    def __init__(self):
        super().__init__(daemon=True)
        self._tid = None
        self._ready = threading.Event()
        self.ok = False

    def run(self):
        self._tid = kernel32.GetCurrentThreadId()
        try:
            self.ok = self.setup()
        finally:
            self._ready.set()
        if not self.ok:
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            self.on_message(msg)
        self.teardown()

    def start_and_wait(self):
        self.start()
        self._ready.wait(5)
        return self.ok

    def stop(self):
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)

    def setup(self):
        return True

    def teardown(self):
        pass

    def on_message(self, msg):
        pass


_MODS = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8}
_NAMED_KEYS = {"space": 0x20, "enter": 0x0D, "esc": 0x1B, "escape": 0x1B, "tab": 0x09,
               "pause": 0x13, "home": 0x24, "end": 0x23, "insert": 0x2D, "delete": 0x2E}


def parse_hotkey(text):
    """'ctrl+alt+l' -> (modifiers, vk). Raises ValueError if invalid."""
    mods, vk = 0, None
    for part in [p.strip().lower() for p in text.split("+") if p.strip()]:
        if part in _MODS:
            mods |= _MODS[part]
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        elif part.startswith("f") and part[1:].isdigit() and 1 <= int(part[1:]) <= 24:
            vk = 0x6F + int(part[1:])
        elif part in _NAMED_KEYS:
            vk = _NAMED_KEYS[part]
        else:
            raise ValueError(f"Unknown key: {part}")
    if vk is None or mods == 0:
        raise ValueError("Hotkey needs at least one modifier and one key")
    return mods, vk


class HotkeyThread(MessageLoopThread):
    def __init__(self, hotkey, callback):
        super().__init__()
        self.mods, self.vk = parse_hotkey(hotkey)
        self.callback = callback

    def setup(self):
        return bool(user32.RegisterHotKey(None, 1, self.mods | 0x4000, self.vk))  # MOD_NOREPEAT

    def teardown(self):
        user32.UnregisterHotKey(None, 1)

    def on_message(self, msg):
        if msg.message == WM_HOTKEY:
            self.callback()


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class KeyboardBlocker(MessageLoopThread):
    """Swallows Win key, Alt+Tab, Alt+F4, Alt+Esc, Ctrl+Esc, Ctrl+Shift+Esc while the lock screen is up.
    Ctrl+Alt+Del cannot be blocked by any user-mode program (by design)."""

    def __init__(self):
        super().__init__()
        self._hook = None
        self._proc = HOOKPROC(self._handler)

    def setup(self):
        self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, kernel32.GetModuleHandleW(None), 0)
        return bool(self._hook)

    def teardown(self):
        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None

    def _handler(self, code, wparam, lparam):
        if code == 0:
            kb = ctypes.cast(lparam, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
            vk = kb.vkCode
            alt = kb.flags & 0x20
            ctrl = user32.GetAsyncKeyState(0x11) & 0x8000
            if vk in (0x5B, 0x5C, 0x5D):                 # LWin, RWin, Apps
                return 1
            if alt and vk in (0x09, 0x1B, 0x73, 0x20):   # Alt+Tab/Esc/F4/Space
                return 1
            if ctrl and vk == 0x1B:                      # Ctrl+Esc, Ctrl+Shift+Esc
                return 1
        return user32.CallNextHookEx(None, code, wparam, lparam)


WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindow.restype = wintypes.HWND


def visible_window_titles():
    """{pid: title} for visible, titled, top-level windows (i.e. apps the user can see)."""
    titles = {}

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, 4):  # GW_OWNER
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                titles.setdefault(pid.value, buf.value)
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return titles


WM_WTSSESSION_CHANGE = 0x02B1
WTS_SESSION_LOCK = 0x7
WTS_SESSION_UNLOCK = 0x8


def register_session_notifications(hwnd):
    try:
        ctypes.windll.wtsapi32.WTSRegisterSessionNotification(wintypes.HWND(hwnd), 0)
        return True
    except (AttributeError, OSError):
        return False


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def relaunch_as_admin(argv):
    """Start argv elevated (UAC prompt). Returns True if the user accepted."""
    import subprocess
    exe, params = argv[0], subprocess.list2cmdline(argv[1:])
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
    return rc > 32


_LOCK_EVENT = "FaceLock_LockRequest"
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.OpenEventW.restype = wintypes.HANDLE
kernel32.SetEvent.argtypes = [wintypes.HANDLE]
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]


def listen_for_lock_requests(callback):
    """Call callback() whenever another process runs `FaceLock.exe --lock` (auto-reset named event)."""
    handle = kernel32.CreateEventW(None, False, False, _LOCK_EVENT)

    def wait():
        while kernel32.WaitForSingleObject(handle, 0xFFFFFFFF) == 0:
            callback()
    threading.Thread(target=wait, daemon=True).start()


def send_lock_request():
    handle = kernel32.OpenEventW(0x0002, False, _LOCK_EVENT)  # EVENT_MODIFY_STATE
    if not handle:
        return False
    kernel32.SetEvent(handle)
    kernel32.CloseHandle(handle)
    return True
