"""Color tags (macOS style) for any file or folder, kept in Burrow's own index.

Stored in %APPDATA%\\Burrow\\tags.json as {normalized path: [tag ids]}. Lookups are a single
dict access, so drawing tag dots costs nothing measurable. Renames, moves, copies and deletes
done in Burrow keep the index up to date; changes made outside Burrow (e.g. a move in
Explorer) leave the old entry behind, which the tag views simply skip.
"""
from __future__ import annotations

import json
import os

from PySide6.QtCore import QObject, QTimer, Signal

from .config import CONFIG_DIR

TAGS = [  # id, name, color
    ("red", "Red", "#e5484d"), ("orange", "Orange", "#f76b15"), ("yellow", "Yellow", "#f5c518"),
    ("green", "Green", "#30a46c"), ("blue", "Blue", "#3e8ef7"), ("purple", "Purple", "#8e4ec6"),
    ("gray", "Gray", "#8b8d98"),
]
TAG_NAMES = {t[0]: t[1] for t in TAGS}
TAG_COLORS = {t[0]: t[2] for t in TAGS}
ORDER = {t[0]: i for i, t in enumerate(TAGS)}
TAG_PREFIX = "::tag:"
TAGS_FILE = CONFIG_DIR / "tags.json"


def key(path: str) -> str:
    return os.path.normpath(path).lower()


def tag_location(tag: str) -> str:
    return TAG_PREFIX + tag


def is_tag_location(path: str) -> bool:
    return bool(path) and path.startswith(TAG_PREFIX)


ENABLED = True       # Settings: show tags (dots, menu, sidebar)


class TagStore(QObject):
    """Color tags per path, saved next to the settings and kept in step with renames and moves."""
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.items: dict[str, list[str]] = {}     # key -> tag ids (ordered)
        self.paths: dict[str, str] = {}           # key -> path as written
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(600)
        self._timer.timeout.connect(self.save)
        self._load()

    # ----- persistence --------------------------------------------------------------------
    def _load(self):
        try:
            data = json.loads(TAGS_FILE.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return
        for path, tags in (data.get("items") or {}).items():
            tags = [t for t in tags if t in TAG_NAMES]
            if tags:
                k = key(path)
                self.items[k] = sorted(set(tags), key=ORDER.get)
                self.paths[k] = path

    def save(self):
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            data = {"version": 1, "items": {self.paths[k]: v for k, v in self.items.items()}}
            tmp = TAGS_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, TAGS_FILE)
        except OSError:
            pass

    def _edited(self):
        self._timer.start()
        self.changed.emit()

    # ----- queries ------------------------------------------------------------------------
    def get(self, path: str) -> list[str]:
        return self.items.get(key(path), []) if self.items else []

    def paths_with(self, tag: str) -> list[str]:
        return [self.paths[k] for k, v in self.items.items() if tag in v]

    def count(self, tag: str) -> int:
        return sum(1 for v in self.items.values() if tag in v)

    # ----- edits ---------------------------------------------------------------------------
    def set_tag(self, paths: list[str], tag: str, on: bool):
        for p in paths:
            k = key(p)
            cur = set(self.items.get(k, []))
            cur.add(tag) if on else cur.discard(tag)
            if cur:
                self.items[k] = sorted(cur, key=ORDER.get)
                self.paths[k] = os.path.normpath(p)
            else:
                self.items.pop(k, None)
                self.paths.pop(k, None)
        self._edited()

    def clear(self, paths: list[str]):
        for p in paths:
            k = key(p)
            self.items.pop(k, None)
            self.paths.pop(k, None)
        self._edited()

    def _remap(self, old: str, new: str, keep_old: bool):
        """Move (or copy) tags of old and everything below it to new."""
        ok, nk = key(old), key(new)
        new = os.path.normpath(new)
        changed = False
        for k in list(self.items):
            if k == ok or k.startswith(ok + os.sep):
                rest = self.paths[k][len(os.path.normpath(old)):]
                dest_k = nk + k[len(ok):]
                self.items[dest_k] = list(self.items[k])
                self.paths[dest_k] = new + rest
                if not keep_old:
                    del self.items[k]
                    del self.paths[k]
                changed = True
        if changed:
            self._edited()

    def renamed(self, old: str, new: str):
        self._remap(old, new, keep_old=False)

    def copied(self, src: str, dst: str):
        self._remap(src, dst, keep_old=True)

    def deleted(self, paths: list[str]):
        gone = [key(p) for p in paths]
        before = len(self.items)
        for k in list(self.items):
            if any(k == g or k.startswith(g + os.sep) for g in gone):
                del self.items[k]
                del self.paths[k]
        if len(self.items) != before:
            self._edited()


_store: TagStore | None = None


def store() -> TagStore:
    global _store
    if _store is None:
        _store = TagStore()
    return _store


_icons: dict = {}


def color_icon(color: str, size: int = 16):
    """A round color swatch, for menus and the sidebar."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
    ic = _icons.get((color, size))
    if ic is None:
        ic = QIcon()
        for scale in (1, 2):
            pm = QPixmap(size * scale, size * scale)
            pm.setDevicePixelRatio(scale)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(color))
            m = size * 0.2
            p.drawEllipse(QRectF(m, m, size - 2 * m, size - 2 * m))
            p.end()
            ic.addPixmap(pm)
        _icons[(color, size)] = ic
    return ic
