"""Folder colors that Windows Explorer shows too.

Works like FolderPainter: a colored folder .ico is referenced from the folder's desktop.ini
(IconResource), desktop.ini is marked Hidden+System and the folder Read-only (Windows only
reads desktop.ini on Read-only/System folders), then Explorer is told to refresh the item.
Other lines in desktop.ini are kept, and a folder's original icon (Downloads, Music...) is
saved so "Default" puts it back.
"""
from __future__ import annotations

import ctypes
import os
import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath

WIN = sys.platform == "win32"
ICON_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Burrow" / "FolderIcons"
SECTION = ".ShellClassInfo"
SAVED_KEY = "BurrowOriginalIconResource"     # the folder's icon before Burrow colored it

COLORS = [  # name, front color
    ("Red", "#e5484d"), ("Orange", "#f76b15"), ("Yellow", "#f5c518"), ("Green", "#30a46c"),
    ("Teal", "#12a594"), ("Blue", "#3e8ef7"), ("Purple", "#8e4ec6"), ("Pink", "#d6409f"),
    ("Brown", "#a1724e"), ("Gray", "#8b8d98"),
]
SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]


# ----- icons ----------------------------------------------------------------------------
def _render(size: int, color: str) -> bytes:
    c = QColor(color)
    back = c.darker(135)
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    s = size / 256.0
    # back panel with tab
    tab = QPainterPath()
    tab.addRoundedRect(QRectF(16 * s, 40 * s, 96 * s, 52 * s), 14 * s, 14 * s)
    tab.addRoundedRect(QRectF(16 * s, 62 * s, 224 * s, 160 * s), 16 * s, 16 * s)
    p.fillPath(tab.simplified(), back)
    # front panel
    g = QLinearGradient(QPointF(0, 92 * s), QPointF(0, 222 * s))
    g.setColorAt(0, c.lighter(118))
    g.setColorAt(1, c)
    front = QPainterPath()
    front.addRoundedRect(QRectF(16 * s, 92 * s, 224 * s, 130 * s), 16 * s, 16 * s)
    p.fillPath(front, g)
    if size >= 32:   # a soft top highlight on the front panel
        p.setBrush(QColor(255, 255, 255, 46))
        p.drawRoundedRect(QRectF(16 * s, 92 * s, 224 * s, 10 * s), 5 * s, 5 * s)
    p.end()
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba.data())


def icon_path(name: str) -> str:
    """Path of the .ico for a color, created on first use."""
    color = dict(COLORS)[name]
    path = ICON_DIR / f"{name.lower()}.ico"
    if not path.exists():
        ICON_DIR.mkdir(parents=True, exist_ok=True)
        images = [(sz, _render(sz, color)) for sz in SIZES]
        header = struct.pack("<HHH", 0, 1, len(images))
        offset = 6 + 16 * len(images)
        entries, blobs = b"", b""
        for sz, data in images:
            dim = 0 if sz >= 256 else sz
            entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
            offset += len(data)
            blobs += data
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(header + entries + blobs)
        os.replace(tmp, path)
    return str(path)


def current_color(folder: str) -> str | None:
    """The Burrow color set on a folder, if any (from its desktop.ini)."""
    ini = os.path.join(folder, "desktop.ini")
    if not os.path.isfile(ini):
        return None
    text, _ = _read(ini)
    val = _get(text, SECTION, "IconResource") or ""
    base = os.path.normcase(str(ICON_DIR))
    if os.path.normcase(val).startswith(base):
        return os.path.splitext(os.path.basename(val.split(",")[0]))[0].capitalize()
    return None


# ----- desktop.ini editing ---------------------------------------------------------------
def _read(path: str) -> tuple[str, str]:
    data = Path(path).read_bytes()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16"), "utf-16"
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace"), "utf-16"
    try:
        return data.decode("utf-8"), "utf-16"
    except UnicodeDecodeError:
        return data.decode("mbcs", errors="replace"), "utf-16"


def _sections(text: str):
    """Yield (section_name_lower, line_index) for every [section] header."""
    for i, line in enumerate(text.splitlines()):
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            yield s[1:-1].strip().lower(), i


def _get(text: str, section: str, key: str) -> str | None:
    lines = text.splitlines()
    cur = None
    for line in lines:
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            cur = s[1:-1].strip().lower()
        elif cur == section.lower() and "=" in s:
            k, v = s.split("=", 1)
            if k.strip().lower() == key.lower():
                return v.strip()
    return None


def _set(text: str, section: str, key: str, value: str | None) -> str:
    """Set (or with None, remove) key in section, keeping everything else as it was."""
    lines = text.splitlines()
    start = end = None
    cur = None
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            if cur == section.lower() and end is None:
                end = i
            cur = s[1:-1].strip().lower()
            if cur == section.lower() and start is None:
                start = i
    if start is not None and end is None:
        end = len(lines)
    if start is None:
        if value is None:
            return text
        lines = lines + ([""] if lines and lines[-1].strip() else []) + [f"[{section}]", f"{key}={value}"]
        return "\r\n".join(lines) + "\r\n"
    found = False
    out = lines[:start + 1]
    for line in lines[start + 1:end]:
        s = line.strip()
        if "=" in s and s.split("=", 1)[0].strip().lower() == key.lower():
            found = True
            if value is not None:
                out.append(f"{key}={value}")
            continue
        out.append(line)
    if not found and value is not None:
        out.append(f"{key}={value}")
    out += lines[end:]
    return "\r\n".join(out) + "\r\n"


def _has_content(text: str) -> bool:
    for line in text.splitlines():
        s = line.strip()
        if s and not (s.startswith("[") and s.endswith("]")) and not s.startswith(";"):
            return True
    return False


# ----- Windows attributes and shell notification ----------------------------------------
FILE_ATTRIBUTE_READONLY, FILE_ATTRIBUTE_HIDDEN, FILE_ATTRIBUTE_SYSTEM = 0x1, 0x2, 0x4
FILE_ATTRIBUTE_NORMAL = 0x80


def _attrs(path: str) -> int:
    if not WIN:
        return 0
    a = ctypes.windll.kernel32.GetFileAttributesW(path)
    return 0 if a == 0xFFFFFFFF else a


def _set_attrs(path: str, attrs: int):
    if WIN:
        ctypes.windll.kernel32.SetFileAttributesW(path, attrs or FILE_ATTRIBUTE_NORMAL)


def _notify(folder: str):
    if not WIN:
        return
    shell32 = ctypes.windll.shell32
    shell32.SHChangeNotify.argtypes = [ctypes.c_long, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
    buf = ctypes.create_unicode_buffer(folder)
    shell32.SHChangeNotify(0x00002000, 0x0005, ctypes.addressof(buf), None)   # UPDATEITEM, PATHW
    parent = ctypes.create_unicode_buffer(os.path.dirname(folder.rstrip("\\")) or folder)
    shell32.SHChangeNotify(0x00001000, 0x0005, ctypes.addressof(parent), None)  # UPDATEDIR


# ----- public ---------------------------------------------------------------------------
def set_color(folder: str, name: str | None) -> str | None:
    """Color a folder (name from COLORS) or, with None, restore its default icon.
    Returns an error message, or None on success."""
    folder = os.path.normpath(folder)
    ini = os.path.join(folder, "desktop.ini")
    try:
        if os.path.isfile(ini):
            text, _enc = _read(ini)
        else:
            text = ""
        ours = os.path.normcase(str(ICON_DIR))
        current = _get(text, SECTION, "IconResource")
        if name is not None:
            # remember the folder's own icon once, so Default can bring it back
            if current and not os.path.normcase(current).startswith(ours) and _get(text, SECTION, SAVED_KEY) is None:
                text = _set(text, SECTION, SAVED_KEY, current)
            text = _set(text, SECTION, "IconResource", f"{icon_path(name)},0")
            # older tools (FolderPainter) use IconFile/IconIndex; drop them so ours wins
            for k in ("IconFile", "IconIndex"):
                text = _set(text, SECTION, k, None)
        else:
            saved = _get(text, SECTION, SAVED_KEY)
            if current and os.path.normcase(current).startswith(ours):
                text = _set(text, SECTION, "IconResource", saved)   # None removes it
            else:
                # a color from another tool: remove it too
                text = _set(text, SECTION, "IconResource", saved)
                for k in ("IconFile", "IconIndex"):
                    text = _set(text, SECTION, k, None)
            text = _set(text, SECTION, SAVED_KEY, None)

        if os.path.exists(ini):
            _set_attrs(ini, FILE_ATTRIBUTE_NORMAL)          # hidden+system files can't be overwritten
        if _has_content(text):
            with open(ini, "w", encoding="utf-16", newline="") as f:
                f.write(text)
            _set_attrs(ini, FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM)
            a = _attrs(folder)
            if not a & (FILE_ATTRIBUTE_READONLY | FILE_ATTRIBUTE_SYSTEM):
                _set_attrs(folder, a | FILE_ATTRIBUTE_READONLY)
        elif os.path.exists(ini):
            os.remove(ini)
        _notify(folder)
        return None
    except OSError as ex:
        return f"{os.path.basename(folder)}: {ex.strerror or ex}"
