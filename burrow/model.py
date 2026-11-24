"""Directory listing model: Explorer-style columns, natural sorting, filtering."""
from __future__ import annotations

import ctypes
import os
import re
import sys
from datetime import datetime

from PySide6.QtCore import (QAbstractTableModel, QFileInfo, QMimeData, QModelIndex,
                            QSortFilterProxyModel, QStorageInfo, Qt, QUrl, Signal)
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFileIconProvider

from . import places
from .config import COLUMN_TITLES, COLUMNS
from .places import DRIVES, NETWORK      # pseudo-paths: "This PC" and "Network"

ENTRY_ROLE = Qt.ItemDataRole.UserRole + 1

ATTR_READONLY = 0x1
ATTR_HIDDEN = 0x2
ATTR_SYSTEM = 0x4
ATTR_ARCHIVE = 0x20
ATTR_REPARSE = 0x400
ATTR_COMPRESSED = 0x800
ATTR_OFFLINE = 0x1000
ATTR_ENCRYPTED = 0x4000
ATTR_RECALL_ON_OPEN = 0x40000
ATTR_RECALL_ON_DATA_ACCESS = 0x400000
CLOUD_ONLY = ATTR_OFFLINE | ATTR_RECALL_ON_OPEN | ATTR_RECALL_ON_DATA_ACCESS

INVALID_NAME_CHARS = set('\\/:*?"<>|')

_num_re = re.compile(r"(\d+)")


def natural_key(s: str) -> tuple:
    return tuple((0, int(t), "") if t.isdigit() else (1, 0, t)
                 for t in _num_re.split(s.lower()) if t)


def human_size(n) -> str:
    if n is None:
        return ""
    if n < 1024:
        return f"{n} B"
    size = float(n)
    for unit in ("KB", "MB", "GB", "TB", "PB"):
        size /= 1024
        if size < 1024 or unit == "PB":
            return f"{size:.1f} {unit}" if size < 100 else f"{size:.0f} {unit}"
    return ""


def attr_string(attrs: int) -> str:
    flags = ((ATTR_READONLY, "R"), (ATTR_HIDDEN, "H"), (ATTR_SYSTEM, "S"), (ATTR_ARCHIVE, "A"),
             (ATTR_COMPRESSED, "C"), (ATTR_ENCRYPTED, "E"), (ATTR_REPARSE, "L"))
    s = "".join(ch for bit, ch in flags if attrs & bit)
    if attrs & CLOUD_ONLY:
        s += "O"
    return s


class Entry:
    __slots__ = ("name", "path", "is_dir", "size", "mtime", "ctime", "atime", "attrs",
                 "hidden", "is_drive", "note", "nkey", "ext", "kind")

    def __init__(self, name, path, is_dir, size=None, mtime=None, ctime=None, atime=None,
                 attrs=0, hidden=False, is_drive=False, note="", kind=""):
        # is_drive marks a location (drive, cloud root, server, share): it can be opened but
        # not renamed, copied or deleted. kind: "", local, removable, optical, network,
        # cloud, server, share.
        self.name, self.path, self.is_dir = name, path, is_dir
        self.size, self.mtime, self.ctime, self.atime = size, mtime, ctime, atime
        self.attrs, self.hidden, self.is_drive, self.note = attrs, hidden, is_drive, note
        self.kind = kind
        self._set_name_keys()

    def _set_name_keys(self):
        self.ext = "" if self.is_dir else os.path.splitext(self.name)[1][1:].lower()
        self.nkey = natural_key(self.name)

    def renamed(self, name: str, path: str):
        self.name, self.path = name, path
        self._set_name_keys()


# ----- Windows shell helpers ------------------------------------------------
if sys.platform == "win32":
    from ctypes import wintypes

    class _SHFILEINFOW(ctypes.Structure):
        _fields_ = [("hIcon", wintypes.HANDLE), ("iIcon", ctypes.c_int),
                    ("dwAttributes", wintypes.DWORD),
                    ("szDisplayName", wintypes.WCHAR * 260), ("szTypeName", wintypes.WCHAR * 80)]

    _SHGetFileInfoW = ctypes.windll.shell32.SHGetFileInfoW
    _SHGetFileInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(_SHFILEINFOW),
                                wintypes.UINT, wintypes.UINT]
    _SHGetFileInfoW.restype = ctypes.c_size_t

    def shell_type_name(ext: str) -> str:
        """Explorer's type name for an extension, without touching the disk."""
        info = _SHFILEINFOW()
        # SHGFI_TYPENAME | SHGFI_USEFILEATTRIBUTES, FILE_ATTRIBUTE_NORMAL
        if _SHGetFileInfoW("file." + ext, 0x80, ctypes.byref(info), ctypes.sizeof(info), 0x410):
            return info.szTypeName
        return ""
else:
    def shell_type_name(ext: str) -> str:
        return ""


class IconCache:
    PER_FILE = {"exe", "lnk", "ico", "url", "msi", "cpl", "scr", "appref-ms", "cur", "ani"}

    def __init__(self):
        self.provider = QFileIconProvider()
        self.folder = self.provider.icon(QFileIconProvider.IconType.Folder)
        self.file = self.provider.icon(QFileIconProvider.IconType.File)
        self.drive = self.provider.icon(QFileIconProvider.IconType.Drive)
        self.computer = self.provider.icon(QFileIconProvider.IconType.Computer)
        self.network = self.provider.icon(QFileIconProvider.IconType.Network)
        self._icons: dict = {}
        self._types: dict = {}

    def icon(self, e: Entry):
        if e.kind in ("network", "server", "share"):
            return self.network            # never ask the shell about a network path: it may block
        if e.is_drive and e.kind == "cloud":
            key = ("cloud", e.path)        # the provider's own folder icon (OneDrive, iCloud...)
        elif e.is_drive:
            key = ("drive", e.path)
        elif e.is_dir:
            return self.folder
        elif not e.ext:
            return self.file
        elif e.ext in self.PER_FILE:
            key = ("path", e.path)
        else:
            key = ("ext", e.ext)
        ic = self._icons.get(key)
        if ic is None:
            ic = self.provider.icon(QFileInfo(e.path))
            if ic.isNull():
                ic = self.drive if e.is_drive else self.file
            self._icons[key] = ic
        return ic

    def type_name(self, e: Entry) -> str:
        if e.is_drive:
            return e.note or "Drive"
        if e.is_dir:
            return "File folder"
        if not e.ext:
            return "File"
        t = self._types.get(e.ext)
        if t is None:
            t = shell_type_name(e.ext) or f"{e.ext.upper()} File"
            self._types[e.ext] = t
        return t


_ICONS: IconCache | None = None


def icon_cache() -> IconCache:
    global _ICONS
    if _ICONS is None:
        _ICONS = IconCache()
    return _ICONS


# ----- listing ----------------------------------------------------------------
def scan_dir(path: str, show_hidden: bool) -> list[Entry]:
    out = []
    with os.scandir(path) as it:
        for de in it:
            name = de.name
            try:
                st = de.stat(follow_symlinks=False)
            except OSError:
                continue
            attrs = getattr(st, "st_file_attributes", 0)
            hidden = bool(attrs & (ATTR_HIDDEN | ATTR_SYSTEM)) or name.startswith(".")
            if hidden and not show_hidden:
                continue
            try:
                is_dir = de.is_dir()
            except OSError:
                is_dir = False
            birth = getattr(st, "st_birthtime", None) or st.st_ctime
            out.append(Entry(name, de.path, is_dir, None if is_dir else st.st_size,
                             st.st_mtime, birth, st.st_atime, attrs, hidden))
    return out


def drive_entry(info: dict) -> Entry:
    name = places.drive_display_name(info)
    kind = info["kind"]
    if kind == "network":
        note = ("Network drive · " if info["connected"] else "Disconnected · ") + info["remote"]
    elif info["total"] is not None:
        note = f"{human_size(info['free'])} free of {human_size(info['total'])}"
    else:
        note = "Not ready"
    return Entry(name, info["root"], True, info["total"], is_drive=True, note=note, kind=kind)


def cloud_entries() -> list[Entry]:
    return [Entry(name, path, True, is_drive=True, note="Cloud storage", kind="cloud")
            for name, path in places.cloud_folders()]


def list_drives() -> list[Entry]:
    """This PC: drives (network drives included), then cloud folders. No network I/O."""
    return [drive_entry(i) for i in places.drive_infos()] + cloud_entries()


def list_network() -> list[Entry]:
    out = [drive_entry(i) for i in places.drive_infos() if i["kind"] == "network"]
    for s in places.RECENT_SERVERS:
        out.append(Entry(s[2:], s, True, is_drive=True, note="Computer", kind="server"))
    return out


def list_shares(server: str) -> list[Entry]:
    server = server.rstrip("\\")
    return [Entry(name, f"{server}\\{name}", True, is_drive=True, note="Shared folder", kind="share")
            for name in places.list_shares(server)]


class DirModel(QAbstractTableModel):
    renameFailed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.entries: list[Entry] = []
        self.path: str | None = None
        self.date_format = "%Y-%m-%d %H:%M"
        self.sort_column = 0
        self.sort_desc = False
        self.folders_first = True
        self.icons = icon_cache()
        self._dates: dict = {}
        self._dim = QColor(128, 128, 128, 170)
        self.meta_color = QColor(128, 128, 128)   # set by the theme: size/type/date columns

    # ----- loading & sorting ------------------------------------------------
    @staticmethod
    def fetch(path: str, show_hidden: bool) -> tuple[list[Entry], str | None]:
        """Read a location. Pure Python + OS calls, safe to run on a worker thread."""
        try:
            if path == DRIVES:
                return list_drives(), None
            if path == NETWORK:
                return list_network(), None
            if places.is_server(path):
                return list_shares(path), None
            return scan_dir(path, show_hidden), None
        except OSError as ex:
            return [], (ex.strerror or str(ex))

    def load(self, path: str, show_hidden: bool) -> str | None:
        entries, error = self.fetch(path, show_hidden)
        self.set_entries(path, entries)
        return error

    def set_entries(self, path: str, entries: list[Entry]):
        self.beginResetModel()
        self.path = path
        self.entries = entries
        self._dates.clear()
        self._sort_entries()
        self.endResetModel()

    def set_folder_sizes(self, sizes: dict):
        """Fill in folder sizes ({normcased path: bytes}) without reloading the listing."""
        changed = False
        for e in self.entries:
            if e.is_dir and not e.is_drive:
                s = sizes.get(os.path.normcase(e.path))
                if s is not None and s != e.size:
                    e.size = s
                    changed = True
        if not changed:
            return
        col = COLUMNS.index("size")
        self.dataChanged.emit(self.index(0, col), self.index(len(self.entries) - 1, col))
        if COLUMNS[self.sort_column] == "size":
            order = Qt.SortOrder.DescendingOrder if self.sort_desc else Qt.SortOrder.AscendingOrder
            self.sort(self.sort_column, order)

    def _key(self, col: str):
        if col == "size":
            return lambda e: (e.size if e.size is not None else -1, e.nkey)
        if col == "type":
            return lambda e: (self.icons.type_name(e).lower(), e.nkey)
        if col in ("modified", "created", "accessed"):
            attr = {"modified": "mtime", "created": "ctime", "accessed": "atime"}[col]
            return lambda e: (getattr(e, attr) or 0, e.nkey)
        if col == "ext":
            return lambda e: (e.ext, e.nkey)
        if col == "attrs":
            return lambda e: (e.attrs, e.nkey)
        return lambda e: e.nkey

    def _sort_entries(self):
        col = COLUMNS[self.sort_column] if 0 <= self.sort_column < len(COLUMNS) else "name"
        self.entries.sort(key=self._key(col), reverse=self.sort_desc)
        if self.folders_first:
            self.entries.sort(key=lambda e: not e.is_dir)   # stable: keeps the order above
        if self.path in (DRIVES, NETWORK):
            rank = {"network": 1, "server": 2, "cloud": 3}
            self.entries.sort(key=lambda e: rank.get(e.kind, 0))   # drives, network, cloud

    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        self.sort_column = column if column >= 0 else 0
        self.sort_desc = order == Qt.SortOrder.DescendingOrder
        if not self.entries:
            return
        self.layoutAboutToBeChanged.emit()
        persist = self.persistentIndexList()
        tracked = [(self.entries[i.row()], i.column()) for i in persist]
        self._sort_entries()
        pos = {id(e): r for r, e in enumerate(self.entries)}
        self.changePersistentIndexList(persist, [self.index(pos[id(e)], c) for e, c in tracked])
        self.layoutChanged.emit()

    # ----- Qt model API -----------------------------------------------------
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.entries)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMN_TITLES[COLUMNS[section]]
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.TextAlignmentRole:
            return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        return None

    def _fmt(self, ts):
        if not ts:
            return ""
        s = self._dates.get(ts)
        if s is None:
            try:
                s = datetime.fromtimestamp(ts).strftime(self.date_format)
            except (OSError, ValueError, OverflowError):
                s = ""
            self._dates[ts] = s
        return s

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        e = self.entries[index.row()]
        col = COLUMNS[index.column()]
        if role == Qt.ItemDataRole.DisplayRole:
            if col == "name":
                return e.name
            if col == "size":
                return human_size(e.size)        # folders too, once Everything has sized them
            if col == "type":
                return self.icons.type_name(e)
            if col == "modified":
                return self._fmt(e.mtime)
            if col == "created":
                return self._fmt(e.ctime)
            if col == "accessed":
                return self._fmt(e.atime)
            if col == "ext":
                return e.ext
            if col == "attrs":
                return attr_string(e.attrs)
        elif role == Qt.ItemDataRole.EditRole and col == "name":
            return e.name
        elif role == Qt.ItemDataRole.DecorationRole and col == "name":
            return self.icons.icon(e)
        elif role == Qt.ItemDataRole.TextAlignmentRole and col == "size":
            return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        elif role == Qt.ItemDataRole.ToolTipRole and col == "name":
            return e.path
        elif role == Qt.ItemDataRole.ForegroundRole:
            if e.hidden:
                return self._dim
            if col != "name":
                return self.meta_color
        elif role == ENTRY_ROLE:
            return e
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.ItemIsDropEnabled
        e = self.entries[index.row()]
        f = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled
        if index.column() == 0 and not e.is_drive:
            f |= Qt.ItemFlag.ItemIsEditable
        if e.is_dir:
            f |= Qt.ItemFlag.ItemIsDropEnabled
        return f

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role != Qt.ItemDataRole.EditRole or not index.isValid() or index.column() != 0:
            return False
        e = self.entries[index.row()]
        new = str(value).strip().rstrip(".")
        if not new or new == e.name:
            return False
        if any(c in INVALID_NAME_CHARS for c in new):
            self.renameFailed.emit('A name can\'t contain any of these characters: \\ / : * ? " < > |')
            return False
        new_path = os.path.join(os.path.dirname(e.path), new)
        if os.path.lexists(new_path) and os.path.normcase(new_path) != os.path.normcase(e.path):
            self.renameFailed.emit(f'"{new}" already exists in this folder.')
            return False
        try:
            os.rename(e.path, new_path)
        except OSError as ex:
            self.renameFailed.emit(f'Couldn\'t rename "{e.name}": {ex.strerror or ex}')
            return False
        e.renamed(new, new_path)
        self.dataChanged.emit(self.index(index.row(), 0), self.index(index.row(), len(COLUMNS) - 1))
        return True

    def mimeTypes(self):
        return ["text/uri-list"]

    def mimeData(self, indexes):
        rows = sorted({i.row() for i in indexes if i.isValid()})
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(self.entries[r].path) for r in rows])
        return md

    def supportedDragActions(self):
        return Qt.DropAction.CopyAction | Qt.DropAction.MoveAction | Qt.DropAction.LinkAction

    def supportedDropActions(self):
        return Qt.DropAction.CopyAction | Qt.DropAction.MoveAction


class FilterProxy(QSortFilterProxyModel):
    """Filters by name. Sorting is delegated to the source model (much faster in Python)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._needle = ""

    def set_filter(self, text: str):
        self._needle = text.lower()
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        if not self._needle:
            return True
        return self._needle in self.sourceModel().entries[row].name.lower()

    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        self.sourceModel().sort(column, order)

    def entry(self, index) -> Entry | None:
        if not index.isValid():
            return None
        src = self.mapToSource(index)
        return self.sourceModel().entries[src.row()] if src.isValid() else None
