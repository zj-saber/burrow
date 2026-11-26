"""Folder sizes from Everything (voidtools), over its IPC.

No SDK DLL needed: this speaks the same WM_COPYDATA protocol as Everything's SDK (QUERY2).
A query is sent to Everything's window; Everything answers a few milliseconds later with a
WM_COPYDATA message to our own hidden window, delivered through the normal event loop, so
nothing waits. One query per folder returns the sizes of all its subfolders. Folder sizes require "Index folder size" in Everything's
options (Tools > Options > Indexes).
"""
from __future__ import annotations

import ctypes
import itertools
import os
import struct
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

WIN = sys.platform == "win32"

WINDOW_CLASSES = ["EVERYTHING_TASKBAR_NOTIFICATION",            # Everything 1.4 / 1.5
                  "EVERYTHING_TASKBAR_NOTIFICATION_(1.5a)"]     # Everything 1.5 alpha instance
COPYDATA_QUERY2W = 18
WM_COPYDATA = 0x004A
REQUEST_FULL_PATH = 0x00000004
REQUEST_SIZE = 0x00000010
REQUEST_DATE_MODIFIED = 0x00000040
ITEM_FOLDER = 0x1
SORT_NAME_ASC = 1
TIMEOUT_MS = 1500

if WIN:
    from ctypes import wintypes
    _u32 = ctypes.windll.user32

    class COPYDATASTRUCT(ctypes.Structure):
        _fields_ = [("dwData", ctypes.c_size_t), ("cbData", wintypes.DWORD), ("lpData", ctypes.c_void_p)]

    _u32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    _u32.FindWindowW.restype = wintypes.HWND
    _u32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                         wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
    _u32.SendMessageTimeoutW.restype = ctypes.c_size_t


def _everything_hwnd():
    if not WIN:
        return None
    for cls in WINDOW_CLASSES:
        h = _u32.FindWindowW(cls, None)
        if h:
            return h
    return None


def is_running() -> bool:
    return bool(_everything_hwnd())


def _escape(path: str) -> str:
    return path.replace('"', "")


def parse_list2(data: bytes) -> list[tuple[str, int | None, bool, float | None]]:
    """Parse EVERYTHING_IPC_LIST2 (FULL_PATH, SIZE, DATE_MODIFIED as requested)
    -> [(path, size, is_folder, modified as a Unix time)]."""
    if len(data) < 20:
        return []
    _tot, num, _off, flags, _sort = struct.unpack_from("<5I", data, 0)
    out = []
    for i in range(num):
        item_flags, data_offset = struct.unpack_from("<2I", data, 20 + i * 8)
        p = data_offset
        path, size, mtime = "", None, None
        if flags & REQUEST_FULL_PATH:
            (n,) = struct.unpack_from("<I", data, p)
            p += 4
            path = data[p:p + n * 2].decode("utf-16-le", errors="replace")
            p += (n + 1) * 2
        if flags & REQUEST_SIZE:
            (sz,) = struct.unpack_from("<q", data, p)
            p += 8
            size = sz if sz >= 0 else None          # -1: size not indexed
        if flags & REQUEST_DATE_MODIFIED:
            (ft,) = struct.unpack_from("<q", data, p)
            p += 8
            if ft > 116444736000000000:              # FILETIME (100 ns since 1601) -> Unix time
                mtime = (ft - 116444736000000000) / 1e7
        out.append((path, size, bool(item_flags & ITEM_FOLDER), mtime))
    return out


class EverythingClient(QWidget):
    """A hidden native window that receives Everything's replies."""

    def __init__(self):
        super().__init__(None, Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        self._hwnd = int(self.winId())
        self._ids = itertools.count(0x4255)       # reply ids, to match answers to questions
        self._pending: dict[int, object] = {}
        self.last_error = ""
        # Everything answers only the newest question from a window: ask one at a time
        from collections import deque
        from PySide6.QtCore import QTimer
        self._queue = deque()
        self._busy = None                        # id of the question being answered
        self._stuck = QTimer(self)
        self._stuck.setSingleShot(True)
        self._stuck.setInterval(TIMEOUT_MS + 500)
        self._stuck.timeout.connect(self._give_up)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(4000)
        self._save_timer.timeout.connect(_save_sizes)

    def nativeEvent(self, event_type, message):
        """Everything answers by WM_COPYDATA: hand each reply to the query that asked."""
        if WIN and event_type == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_COPYDATA and msg.lParam:
                cds = COPYDATASTRUCT.from_address(msg.lParam)
                rid = int(cds.dwData)
                cb = self._pending.pop(rid, None)
                if rid == self._busy:            # answered: the next question can go
                    self._busy = None
                    self._stuck.stop()
                    from PySide6.QtCore import QTimer
                    QTimer.singleShot(0, self._send_next)
                if cb is not None:
                    data = ctypes.string_at(cds.lpData, cds.cbData)   # only valid during this call
                    try:
                        cb(parse_list2(data))
                    except Exception:            # noqa: BLE001 - never let a reply break the UI
                        pass
                    return True, 1
        return False, 0

    def query(self, search: str, callback, request=REQUEST_FULL_PATH | REQUEST_SIZE, limit: int = 0xFFFFFFFF) -> bool:
        """Ask Everything; callback(results) runs on the GUI thread when the answer arrives
        (a few ms later). Questions wait their turn: Everything only answers a window's newest
        one. Returns False if Everything isn't available."""
        if not _everything_hwnd():
            self.last_error = "Everything isn't running"
            return False
        self._queue.append((search, callback, request, limit))
        if self._busy is None:
            self._send_next()
        return True

    def _send_next(self):
        while self._busy is None and self._queue:
            self._send(*self._queue.popleft())

    def _give_up(self):
        """No answer in time: drop that question and go on."""
        self._pending.pop(self._busy, None)
        self._busy = None
        self._send_next()

    def _send(self, search, callback, request, limit) -> bool:
        target = _everything_hwnd()
        if not target:
            self.last_error = "Everything isn't running"
            return False
        rid = next(self._ids)
        text = (search + "\0").encode("utf-16-le")
        header = struct.pack("<7I", self._hwnd & 0xFFFFFFFF, rid, 0, 0, limit, request, SORT_NAME_ASC)
        payload = ctypes.create_string_buffer(header + text, len(header) + len(text))
        cds = COPYDATASTRUCT(COPYDATA_QUERY2W, len(header) + len(text), ctypes.addressof(payload))
        self._pending[rid] = callback
        self._busy = rid
        self._stuck.start()
        result = ctypes.c_size_t()
        # SMTO_ABORTIFHUNG (2): a frozen Everything can't freeze us. Everything's reply is a
        # sent message, which Windows delivers to our window while we wait here.
        ok = _u32.SendMessageTimeoutW(target, WM_COPYDATA, self._hwnd, ctypes.addressof(cds),
                                      0x0002, TIMEOUT_MS, ctypes.byref(result))
        if not ok or not result.value:
            self._pending.pop(rid, None)
            if self._busy == rid:
                self._busy = None
                self._stuck.stop()
            self.last_error = "Everything didn't answer"
            return False
        self.last_error = ""
        return True

    def folder_sizes(self, folder: str, callback) -> bool:
        """Sizes of the subfolders of folder: callback({normcased path: size})."""
        parent = folder.rstrip("\\") if len(folder) > 3 else folder
        search = f'parent:"{_escape(parent)}" folder:'

        key = os.path.normcase(parent)
        known = _sizes_cache().get(key)
        if known:
            callback(dict(known))                # the last recorded sizes, at once

        def done(results):
            sizes = {os.path.normcase(p): s for p, s, is_folder, _m in results if is_folder and s is not None}
            if sizes:
                _remember(key, sizes)
                self._save_timer.start()
            callback(sizes)
        return self.query(search, done) or bool(known)

    def search_under(self, folder: str, text: str, callback, limit: int = 2000) -> bool:
        """Everything under folder (any depth) whose name matches text, with Everything's own
        syntax (several words, *, ext:...): callback([(path, size, is_folder, modified)])."""
        root = folder.rstrip("\\") + "\\"
        return self.query(f'"{_escape(root)}" {text}', callback,
                          REQUEST_FULL_PATH | REQUEST_SIZE | REQUEST_DATE_MODIFIED, limit)

    def size_of(self, path: str, callback) -> bool:
        """Size of one folder: callback(size or None). Asks for the folder's siblings (an exact,
        small query) and picks it out."""
        path = os.path.normpath(path)
        parent = os.path.dirname(path)
        if parent == path:                      # a drive root: not a folder in Everything's terms
            return False
        norm = os.path.normcase(path)

        told = []

        def done(sizes):
            if norm in sizes and not told:       # once: the remembered size or the fresh one
                told.append(1)
                callback(sizes[norm])
        return self.folder_sizes(parent, done)


# ----- folder sizes remembered between runs (%APPDATA%\\Burrow\\sizes.json) -----------------
# Shown at once when a folder opens, and when Everything isn't running; Everything's fresh
# answer replaces them a few ms later.
_SIZES: dict | None = None
MAX_FOLDERS = 1500


def _sizes_path():
    from .config import CONFIG_DIR
    return os.path.join(CONFIG_DIR, "sizes.json")


def _sizes_cache() -> dict:
    global _SIZES
    if _SIZES is None:
        import json
        try:
            with open(_sizes_path(), encoding="utf-8") as f:
                data = json.load(f)
            _SIZES = {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}
        except (OSError, ValueError):
            _SIZES = {}
    return _SIZES


def _remember(parent_key: str, sizes: dict):
    cache = _sizes_cache()
    cache.pop(parent_key, None)
    cache[parent_key] = {k: int(v) for k, v in list(sizes.items())[:3000]}
    while len(cache) > MAX_FOLDERS:              # oldest first
        cache.pop(next(iter(cache)))




def _save_sizes():
    import json
    try:
        os.makedirs(os.path.dirname(_sizes_path()), exist_ok=True)
        tmp = _sizes_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_sizes_cache(), f, separators=(",", ":"))
        os.replace(tmp, _sizes_path())
    except OSError:
        pass


_client: EverythingClient | None = None


def client() -> EverythingClient | None:
    global _client
    if not WIN:
        return None
    if _client is None:
        _client = EverythingClient()
    return _client
