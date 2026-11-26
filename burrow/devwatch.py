"""Drive arrival/removal without polling. Windows broadcasts WM_DEVICECHANGE to top-level windows
when a USB drive, card, phone or network drive appears or goes away. A tiny hidden window of
our own receives it, so only those few messages ever reach Python (hooking Qt's native event
filter instead would run Python for every mouse move)."""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import QObject, QTimer, Signal

WM_DEVICECHANGE = 0x0219
DBT_DEVICEARRIVAL = 0x8000
DBT_DEVICEREMOVECOMPLETE = 0x8004
DBT_DEVNODES_CHANGED = 0x0007

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class _WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


class DeviceWatcher(QObject):
    """Tells Burrow when a drive appears or goes away, without polling."""
    changed = Signal()            # debounced: drives may still be mounting when the first message comes

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hwnd = None
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(600)
        self._debounce.timeout.connect(self._fire)
        self._late = QTimer(self)                 # one more look a little later (slow volumes)
        self._late.setSingleShot(True)
        self._late.setInterval(3000)
        self._late.timeout.connect(self.changed)
        if sys.platform == "win32":
            self._create()

    def _fire(self):
        self.changed.emit()
        self._late.start()

    def _create(self):
        """Register the hidden window that receives WM_DEVICECHANGE."""
        u32 = ctypes.windll.user32
        u32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u32.DefWindowProcW.restype = LRESULT
        u32.CreateWindowExW.restype = wintypes.HWND
        u32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                        wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]

        def proc(hwnd, msg, wp, lp):
            if msg == WM_DEVICECHANGE and wp in (DBT_DEVICEARRIVAL, DBT_DEVICEREMOVECOMPLETE, DBT_DEVNODES_CHANGED):
                self._debounce.start()
            return u32.DefWindowProcW(hwnd, msg, wp, lp)

        self._proc = WNDPROC(proc)                # keep a reference: Windows calls it later
        hinst = ctypes.windll.kernel32.GetModuleHandleW(None)
        wc = _WNDCLASSW(lpfnWndProc=self._proc, hInstance=hinst, lpszClassName="BurrowDeviceWatch")
        u32.RegisterClassW(ctypes.byref(wc))      # fails harmlessly if a second window registers it again
        # a hidden top-level window (message-only windows don't get broadcasts)
        self.hwnd = u32.CreateWindowExW(0, "BurrowDeviceWatch", "Burrow device watch", 0, 0, 0, 0, 0,
                                        None, None, hinst, None)
