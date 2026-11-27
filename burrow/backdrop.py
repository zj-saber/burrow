"""Window material (Windows 11): Mica, Mica Alt or Acrylic behind Burrow's chrome, plus a title
bar that follows the theme's light/dark. The material is drawn by the desktop compositor (DWM),
not by Burrow, so it costs almost nothing; the window just paints its panels with some alpha."""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_SYSTEMBACKDROP_TYPE = 38
KINDS = {"none": 1, "mica": 2, "acrylic": 3, "mica_alt": 4}
NAMES = {"none": "None (solid)", "mica": "Mica (subtle, tinted by the wallpaper)",
         "mica_alt": "Mica Alt (stronger tint)", "acrylic": "Acrylic (frosted glass)"}
TINTS = {"clear": 0x40, "light": 0x70, "medium": 0xA8, "strong": 0xD8}     # alpha of the theme colors on top


class _MARGINS(ctypes.Structure):
    _fields_ = [("l", ctypes.c_int), ("r", ctypes.c_int), ("t", ctypes.c_int), ("b", ctypes.c_int)]


def supported() -> bool:
    """System backdrops need Windows 11 22H2 (build 22621) or newer."""
    if sys.platform != "win32":
        return False
    try:
        return sys.getwindowsversion().build >= 22621
    except AttributeError:
        return False


def _dwm():
    return ctypes.windll.dwmapi


def set_dark_title(hwnd: int, dark: bool):
    if sys.platform != "win32":
        return
    v = ctypes.c_int(1 if dark else 0)
    try:
        _dwm().DwmSetWindowAttribute(wintypes.HWND(hwnd), DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(v), 4)
    except OSError:
        pass


def apply(hwnd: int, kind: str) -> bool:
    """Ask DWM for the material. Returns False when this Windows can't do it."""
    if not supported():
        return False
    v = ctypes.c_int(KINDS.get(kind, 1))
    m = _MARGINS(-1, -1, -1, -1) if kind != "none" else _MARGINS(0, 0, 0, 0)
    try:
        _dwm().DwmExtendFrameIntoClientArea(wintypes.HWND(hwnd), ctypes.byref(m))
        return _dwm().DwmSetWindowAttribute(wintypes.HWND(hwnd), DWMWA_SYSTEMBACKDROP_TYPE,
                                            ctypes.byref(v), 4) == 0
    except OSError:
        return False


def tinted(c: dict, tint: str) -> dict:
    """Theme colors with alpha on the big surfaces, so the material shows through them."""
    a = TINTS.get(tint, TINTS["medium"])
    out = dict(c)
    for key, extra in (("window", 0), ("panel", 0), ("chip", 0), ("base", 0x20)):
        col = c[key].lstrip("#")[-6:]
        out[key] = f"#{min(255, a + extra):02x}{col}"
    out["backdrop"] = True
    return out
