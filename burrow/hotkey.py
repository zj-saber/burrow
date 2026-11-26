"""Win+E opens Burrow, via a low-level keyboard hook (WH_KEYBOARD_LL).

Explorer owns the Win+E hotkey and newer Windows builds ignore the old registry redirect, so
Burrow listens for the key combination itself while it runs. The hook is as light as it can
be: every key goes straight through except E while a Windows key is held. That press is
swallowed (so Explorer doesn't also open) and a harmless unassigned key is sent so letting go
of the Windows key doesn't open the Start menu.

The hook lives on the GUI thread (it needs a message loop, which Qt provides) and only posts
a request; opening the window happens afterwards, outside the hook.
"""
from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import QObject, QTimer, Signal

WIN = sys.platform == "win32"
WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
VK_LWIN, VK_RWIN, VK_E = 0x5B, 0x5C, 0x45
VK_NONAME = 0xFC              # unassigned: pressing it while Win is held cancels the Start menu
LLKHF_INJECTED = 0x10

if WIN:
    from ctypes import wintypes

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
    _user32 = ctypes.windll.user32
    _user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
    _user32.SetWindowsHookExW.restype = wintypes.HHOOK
    _user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
    _user32.CallNextHookEx.restype = ctypes.c_ssize_t
    _user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]


class WinEHook(QObject):
    """Catches Win+E (opt-in) with a low-level keyboard hook that only ever looks at Win and E."""
    pressed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hook = None
        self._proc = None
        self._win_down = set()

    @property
    def active(self) -> bool:
        return bool(self._hook)

    def install(self) -> bool:
        if not WIN or self._hook:
            return bool(self._hook)
        self._proc = HOOKPROC(self._callback)          # keep a reference: Windows calls it
        self._hook = _user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, None, 0)
        return bool(self._hook)

    def uninstall(self):
        if self._hook:
            _user32.UnhookWindowsHookEx(self._hook)
        self._hook = None
        self._win_down.clear()

    def handle(self, msg: int, vk: int, flags: int) -> bool:
        """The decision, separate from Windows plumbing so it can be tested. True = swallow."""
        down = msg in (WM_KEYDOWN, WM_SYSKEYDOWN)
        if vk in (VK_LWIN, VK_RWIN):
            (self._win_down.add if down else self._win_down.discard)(vk)
            return False
        if vk == VK_E and self._win_down and not flags & LLKHF_INJECTED:
            if down:
                QTimer.singleShot(0, self.pressed.emit)    # act outside the hook
                QTimer.singleShot(0, self._cancel_start_menu)
            return True                                    # swallow E down and up
        return False

    def _callback(self, code, wparam, lparam):
        try:
            if code == 0:
                k = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if self.handle(int(wparam), k.vkCode, k.flags):
                    return 1
        except Exception:                                  # noqa: BLE001 - never break typing
            pass
        return _user32.CallNextHookEx(self._hook, code, wparam, lparam)

    @staticmethod
    def _cancel_start_menu():
        # a press/release of an unassigned key while Win is still held means "Win was used in a
        # combination", so Windows won't open the Start menu on release
        _user32.keybd_event(VK_NONAME, 0, 0, 0)
        _user32.keybd_event(VK_NONAME, 0, 2, 0)


# ----- start with Windows (background mode) ---------------------------------------------------
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def set_start_with_windows(enable: bool, command: str) -> str | None:
    """Add or remove Burrow from the HKCU Run key (start minimized to the tray)."""
    if not WIN:
        return "Only available on Windows."
    import winreg
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if enable:
                winreg.SetValueEx(k, "Burrow", 0, winreg.REG_SZ, f"{command} --background")
            else:
                try:
                    winreg.DeleteValue(k, "Burrow")
                except FileNotFoundError:
                    pass
        return None
    except OSError as ex:
        return str(ex)


def starts_with_windows() -> bool:
    if not WIN:
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return "burrow" in winreg.QueryValueEx(k, "Burrow")[0].lower()
    except OSError:
        return False
