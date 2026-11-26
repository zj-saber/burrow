"""Faint row tints for items on the clipboard and items being pasted:
cyan = copied, yellow = cut, green = being pasted (Burrow's own copier).

Follows the system clipboard, so files copied or cut in Explorer show too. Painting costs
nothing while no item is marked (one empty-dict check per cell); otherwise one dict lookup.
"""
from __future__ import annotations

import os

from PySide6.QtGui import QColor, QGuiApplication

RGB = {"copy": (0, 200, 230), "cut": (245, 190, 0), "paste": (40, 205, 95)}
ALPHA = 42

MARKS: dict[str, str] = {}          # lower-case path -> "copy" / "cut" / "paste"
_clip: dict[str, str] = {}
_paste: dict[str, int] = {}
_colors = {k: QColor(r, g, b, ALPHA) for k, (r, g, b) in RGB.items()}
_enabled = [True]
_listeners: list = []
_installed = [False]


def _key(path: str) -> str:
    return os.path.normpath(path).lower()


def color(kind: str) -> QColor:
    return _colors[kind]


def _rebuild():
    MARKS.clear()
    if _enabled[0]:
        MARKS.update(_clip)
        MARKS.update({k: "paste" for k in _paste})        # pasting wins
    for fn in list(_listeners):
        fn()


def sync_clipboard():
    from .fileops import clipboard_paths
    try:
        paths, cut = clipboard_paths()
    except RuntimeError:
        return
    new = {_key(p): "cut" if cut else "copy" for p in paths}
    if new != _clip:
        _clip.clear()
        _clip.update(new)
        _rebuild()


def add_pasting(paths):
    for p in paths:
        k = _key(p)
        _paste[k] = _paste.get(k, 0) + 1
    _rebuild()


def remove_pasting(paths):
    for p in paths:
        k = _key(p)
        if _paste.get(k, 0) <= 1:
            _paste.pop(k, None)
        else:
            _paste[k] -= 1
    _rebuild()


def set_enabled(on: bool):
    if _enabled[0] != on:
        _enabled[0] = on
        _rebuild()


def install(repaint):
    """Once per app: follow the clipboard and repaint through repaint()."""
    _listeners.append(repaint)
    if not _installed[0]:
        _installed[0] = True
        QGuiApplication.clipboard().dataChanged.connect(sync_clipboard)
        sync_clipboard()
