"""Flat light/dark theme.

Performance rule: the big file list is themed only through QPalette and a thin delegate.
Qt style sheets are used only on small chrome widgets (tab strip, nav bar, sidebar, header,
status bar). A style sheet on the application or on the main window would route every row of
every file list through QStyleSheetStyle, which slows painting noticeably.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QIcon, QPainter, QPalette, QPixmap

_BURROW = {
    "dark": {
        "window": "#1b1c1f", "base": "#151618", "panel": "#1b1c1f", "text": "#e3e4e6",
        "dim": "#8a8d93", "border": "#2a2c31", "accent": "#4d8dff", "select": "#24406f",
        "select_inactive": "#2a3242", "hover": "#202226", "chip": "#25272b", "seltext": "#ffffff",
    },
    "light": {
        "window": "#f3f4f6", "base": "#ffffff", "panel": "#f3f4f6", "text": "#1e2227",
        "dim": "#6b7079", "border": "#e1e3e7", "accent": "#2f6fed", "select": "#d6e4ff",
        "select_inactive": "#e6ebf3", "hover": "#f2f5f9", "chip": "#e8eaee", "seltext": "#0f1a2e",
    },
}

# Inspired by "Primary" for Obsidian (Cecilia May): warm paper tones in light mode, espresso
# browns in dark mode, a muted tan accent, Inter for the interface. Colors are taken from
# Primary's own gray scale (--color-l-gray-* / --color-d-gray-*) and used the way Primary uses
# them: content on background-primary, sidebar and chrome on background-secondary.
_PRIMARY = {
    "light": {
        "window": "#eee7dd",   # l-gray-40  background-secondary
        "base": "#f8f5f1",     # l-gray-20  background-primary
        "panel": "#eee7dd",
        "text": "#593e22",     # l-gray-130 text-normal
        "dim": "#836b49",      # l-gray-100 text-muted
        "border": "#e4d7c3",   # l-gray-60  background-modifier-border
        "accent": "#ac9379",   # accent-1 (h31 s23% l50% x1.15)
        "select": "#e6dccb", "select_inactive": "#efe8de",
        "hover": "#f2ece3",    # l-gray-30
        "chip": "#ebe3d6",     # l-gray-50
        "seltext": "#432e14",  # l-gray-140
        "font": "Inter",
    },
    "dark": {
        "window": "#26211c",   # d-gray-120 background-secondary
        "base": "#2e261f",     # d-gray-100 background-primary
        "panel": "#26211c",
        "text": "#d7c0a3",     # d-gray-20  text-normal
        "dim": "#ab916d",      # d-gray-40  text-muted
        "border": "#3b3026",   # d-gray-80  background-modifier-border
        "accent": "#917959",   # d-gray-50, the visible tan of the dark accent
        "select": "#51402f",   # d-gray-70
        "select_inactive": "#3b3026",
        "hover": "#342c23",    # d-gray-90
        "chip": "#3b3026",     # d-gray-80
        "seltext": "#ebdac6",  # d-gray-10
        "font": "Inter",
    },
}

_GREYSCALE = {   # no hue at all: only lightness separates things
    "light": {
        "window": "#f2f2f2", "base": "#ffffff", "panel": "#f2f2f2", "text": "#1f1f1f",
        "dim": "#6e6e6e", "border": "#e0e0e0", "accent": "#5c5c5c", "select": "#dcdcdc",
        "select_inactive": "#e8e8e8", "hover": "#f4f4f4", "chip": "#e6e6e6", "seltext": "#000000",
    },
    "dark": {
        "window": "#1c1c1c", "base": "#161616", "panel": "#1c1c1c", "text": "#e2e2e2",
        "dim": "#8c8c8c", "border": "#2c2c2c", "accent": "#a8a8a8", "select": "#3a3a3a",
        "select_inactive": "#2e2e2e", "hover": "#222222", "chip": "#262626", "seltext": "#ffffff",
    },
}

_SEPIA = {       # old paper and ink
    "light": {
        "window": "#ede0c8", "base": "#f6ecd9", "panel": "#ede0c8", "text": "#4a3720",
        "dim": "#85704f", "border": "#dfcfae", "accent": "#a0703c", "select": "#e3cfa6",
        "select_inactive": "#eadcc0", "hover": "#f1e5cd", "chip": "#e6d6b8", "seltext": "#2f2111",
    },
    "dark": {
        "window": "#2a2218", "base": "#30271c", "panel": "#2a2218", "text": "#e3d3b5",
        "dim": "#a99572", "border": "#3e3324", "accent": "#c49a5e", "select": "#55442c",
        "select_inactive": "#403424", "hover": "#372d20", "chip": "#3e3324", "seltext": "#f5e8cf",
    },
}

_OLED = {        # true black: pixels off on OLED screens, so it's dark whatever the Theme says
    "dark": {
        "window": "#000000", "base": "#000000", "panel": "#000000", "text": "#d8d8d8",
        "dim": "#7a7a7a", "border": "#1a1a1a", "accent": "#5b8cff", "select": "#16264a",
        "select_inactive": "#141a26", "hover": "#0c0c0c", "chip": "#111111", "seltext": "#ffffff",
    },
    "only": "dark",
}

_CALM = {        # easy on the eyes: low contrast, little blue light, soft sage accent
    "light": {
        "window": "#e9ece4", "base": "#f3f4ee", "panel": "#e9ece4", "text": "#3c4a40",
        "dim": "#748278", "border": "#d9ded2", "accent": "#6f9a7c", "select": "#d3e0d1",
        "select_inactive": "#e0e6dc", "hover": "#edf0e8", "chip": "#e1e5da", "seltext": "#24322a",
    },
    "dark": {
        "window": "#1f2421", "base": "#232925", "panel": "#1f2421", "text": "#c5cfc5",
        "dim": "#879387", "border": "#2f3732", "accent": "#7fa88a", "select": "#34473a",
        "select_inactive": "#2c352f", "hover": "#28302b", "chip": "#2b322d", "seltext": "#e4ece3",
    },
}

PALETTES = {"burrow": _BURROW, "primary": _PRIMARY, "greyscale": _GREYSCALE, "sepia": _SEPIA,
            "oled": _OLED, "calm": _CALM}
PALETTE_NAMES = {"burrow": "Burrow (cool blue)", "primary": "Primary (warm, inspired by the Obsidian theme)",
                 "greyscale": "Greyscale", "sepia": "Sepia", "oled": "OLED black (always dark)",
                 "calm": "Calm (easy on the eyes)"}
_current: dict = dict(_BURROW["light"], name="light", palette="burrow")
_default_font = None


def colors() -> dict:
    return _current


def resolve(mode: str) -> str:
    if mode in ("light", "dark"):
        return mode
    try:
        scheme = QGuiApplication.styleHints().colorScheme()
        return "dark" if scheme == Qt.ColorScheme.Dark else "light"
    except AttributeError:
        return "light"


def _palette(c: dict) -> QPalette:
    """The QPalette for a color set (this is all the file lists are styled with)."""
    p = QPalette()
    col = lambda k: QColor(c[k])  # noqa: E731
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive, QPalette.ColorGroup.Disabled):
        p.setColor(group, QPalette.ColorRole.Window, col("window"))
        p.setColor(group, QPalette.ColorRole.WindowText, col("text"))
        p.setColor(group, QPalette.ColorRole.Base, col("base"))
        p.setColor(group, QPalette.ColorRole.AlternateBase, col("hover"))
        p.setColor(group, QPalette.ColorRole.Text, col("text"))
        p.setColor(group, QPalette.ColorRole.Button, col("chip"))
        p.setColor(group, QPalette.ColorRole.ButtonText, col("text"))
        p.setColor(group, QPalette.ColorRole.Highlight, col("select"))
        p.setColor(group, QPalette.ColorRole.HighlightedText, col("seltext"))
        p.setColor(group, QPalette.ColorRole.ToolTipBase, col("chip"))
        p.setColor(group, QPalette.ColorRole.ToolTipText, col("text"))
        p.setColor(group, QPalette.ColorRole.PlaceholderText, col("dim"))
        p.setColor(group, QPalette.ColorRole.Link, col("accent"))
        p.setColor(group, QPalette.ColorRole.Light, col("base"))
        p.setColor(group, QPalette.ColorRole.Midlight, col("border"))
        p.setColor(group, QPalette.ColorRole.Mid, col("border"))
        p.setColor(group, QPalette.ColorRole.Dark, col("border"))
        p.setColor(group, QPalette.ColorRole.Shadow, col("border"))
    p.setColor(QPalette.ColorGroup.Inactive, QPalette.ColorRole.Highlight, col("select_inactive"))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, col("dim"))
    return p


def apply(app, mode: str, palette: str = "burrow") -> dict:
    """Apply light/dark and a color set to the whole app."""
    global _current, _default_font
    pal = PALETTES.get(palette, _BURROW)
    name = pal.get("only") or resolve(mode)
    _current = dict(pal[name], name=name, palette=palette if palette in PALETTES else "burrow")
    if app.style().name().lower() != "fusion":
        app.setStyle("Fusion")
    app.setPalette(_palette(_current))
    # Interface font: the palette's own (Inter for Primary) when installed, else the system font.
    if _default_font is None:
        _default_font = QFont(app.font())
    family = _current.get("font")
    if family and family in QFontDatabase.families():
        f = QFont(_default_font)
        f.setFamily(family)
        app.setFont(f)
    else:
        app.setFont(_default_font)
    _icon_cache.clear()
    return _current


# ----- icons drawn from the Windows icon font (crisp, no image files) -----------
GLYPHS = {
    "back": "", "forward": "", "up": "", "refresh": "",
    "settings": "", "inspector": "", "menu": "", "add": "",
    "sidebar": "", "close": "", "chevron": "", "chevron_down": "",
    "play": "", "pause": "", "volume": "", "mute": "",
    "chevron_up": "",
    # command bar
    "new": "", "new_folder": "", "new_file": "", "cut": "", "copy": "",
    "paste": "", "rename": "", "extract": "", "delete": "", "sort": "",
    "view": "", "filter": "", "compare": "", "duplicates": "",
    "bulk_rename": "", "auto_folder": "", "auto_update": "", "mode_list": "", "mode_columns": "", "mode_thumbs": "", "mode_river": "", "save_view": "",
}
_icon_font_family: str | None = None
_icon_cache: dict = {}


def _icon_family() -> str:
    global _icon_font_family
    if _icon_font_family is None:
        fams = set(QFontDatabase.families())
        _icon_font_family = next((f for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if f in fams), "")
    return _icon_font_family


def icon(name: str, size: int = 16) -> QIcon:
    """A themed icon drawn from the Segoe icon font, cached per size and theme."""
    key = (name, size, _current["name"], _current.get("palette"))
    ic = _icon_cache.get(key)
    if ic is not None:
        return ic
    fam = _icon_family()
    ic = QIcon()
    if fam:
        for mode, color in ((QIcon.Mode.Normal, _current["text"]), (QIcon.Mode.Disabled, _current["border"]),
                            (QIcon.Mode.Active, _current["text"])):
            for scale in (1, 2):
                pm = QPixmap(size * scale, size * scale)
                pm.setDevicePixelRatio(scale)
                pm.fill(Qt.GlobalColor.transparent)
                p = QPainter(pm)
                p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
                f = QFont(fam)
                f.setPixelSize(int(size * 0.8))
                p.setFont(f)
                p.setPen(QColor(color))
                p.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, GLYPHS[name])
                p.end()
                ic.addPixmap(pm, mode)
    _icon_cache[key] = ic
    return ic


# ----- style sheets for small chrome widgets only ---------------------------------
def tabstrip_qss(c):
    """Style for the tab strip."""
    return f"""
#tabstrip {{ background: {c['window']}; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{
    background: transparent; color: {c['dim']}; border: none;
    padding: 6px 10px 7px 12px; margin: 0 1px; min-width: 60px; max-width: 220px;
    border-top-left-radius: 8px; border-top-right-radius: 8px;
}}
QTabBar::tab:selected {{ background: {c['base']}; color: {c['text']}; }}
#tabstrip[active="true"] QTabBar::tab:selected {{ border-top: 2px solid {c['accent']}; }}
QTabBar::tab:hover:!selected {{ background: {c['chip']}; color: {c['text']}; }}
QTabBar QToolButton {{ background: {c['window']}; border: none; }}
QTabBar QToolButton#tabclose {{ background: transparent; border-radius: 4px; padding: 1px; }}
QTabBar QToolButton#tabclose:hover {{ background: {c['border']}; }}
#tabstrip QToolButton#plus {{ border: none; border-radius: 6px; padding: 4px; margin: 0 0 3px 2px; }}
#tabstrip QToolButton#plus:hover {{ background: {c['chip']}; }}
"""


def navbar_qss(c):
    """Style for the navigation bar and address field."""
    return f"""
#navbar {{ background: {c['base']}; border-bottom: 1px solid {c['border']}; }}
#navbar QToolButton {{ border: none; border-radius: 6px; padding: 5px; background: transparent; color: {c['text']}; }}
#navbar QToolButton:hover {{ background: {c['chip']}; }}
#navbar QToolButton:pressed, #navbar QToolButton:checked {{ background: {c['border']}; }}
#navbar QToolButton::menu-indicator {{ image: none; width: 0; }}
#crumbs {{ background: {c['window']}; border: 1px solid {c['window']}; border-radius: 8px; }}
#crumbs:hover {{ border: 1px solid {c['border']}; }}
#crumbs QToolButton {{ padding: 2px 6px; border-radius: 5px; color: {c['text']}; }}
#crumbs QToolButton:hover {{ background: {c['chip']}; }}
#crumbs QLabel {{ color: {c['dim']}; background: transparent; }}
#navbar QLineEdit {{
    background: {c['base']}; border: 1px solid {c['accent']}; border-radius: 8px;
    padding: 3px 9px; selection-background-color: {c['accent']}; selection-color: white;
}}
#navbar QComboBox {{
    background: {c['window']}; border: 1px solid {c['window']}; border-radius: 8px;
    padding: 3px 8px; color: {c['text']};
}}
#navbar QComboBox:hover {{ border: 1px solid {c['border']}; }}
#navbar QComboBox::drop-down {{ border: none; width: 16px; }}
#navbar QLabel#viewlabel {{ color: {c['dim']}; }}
#cmdbar QToolButton {{ padding: 5px 7px; }}
#cmdbar QToolButton:disabled {{ color: {c['dim']}; }}
#cmdsep {{ background: {c['border']}; }}
"""


def sidebar_qss(c):
    # Only the widget itself is styled. Item hover/selection are painted by the sidebar's
    # delegate: Qt's style-sheet item highlight also fills the indentation strip as a
    # separate block beside the row.
    return f"""
QTreeWidget {{ background: {c['panel']}; border: none; outline: 0; padding: 6px 6px; }}
"""


def header_qss(c):
    return f"""
QHeaderView {{ background: {c['base']}; border: none; }}
QHeaderView::section {{
    background: {c['base']}; color: {c['dim']}; border: none;
    border-bottom: 1px solid {c['border']}; padding: 5px 8px;
}}
QHeaderView::section:hover {{ color: {c['text']}; background: {c['hover']}; }}
"""


def field_qss(c):
    return f"""
QLineEdit {{ background: {c['window']}; border: 1px solid {c['border']}; border-radius: 8px;
             padding: 4px 9px; margin: 6px; color: {c['text']}; }}
QLineEdit:focus {{ border: 1px solid {c['accent']}; }}
"""


def statusbar_qss(c):
    """Style for the status bar and its switches."""
    return f"""
QStatusBar {{ background: {c['window']}; border-top: 1px solid {c['border']}; }}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ color: {c['dim']}; padding: 0 8px; }}
QStatusBar QToolButton#statusToggle {{ color: {c['dim']}; background: transparent; border: 1px solid {c['border']};
    border-radius: 9px; padding: 1px 10px; margin: 2px 6px; }}
QStatusBar QToolButton#statusToggle:hover {{ color: {c['text']}; border-color: {c['accent']}; }}
QStatusBar QToolButton#statusToggle:checked {{ color: {c['accent']}; }}
QStatusBar QSlider#iconSlider {{ margin: 0 2px; }}
QStatusBar QCheckBox#statusCheck {{ color: {c['dim']}; padding: 0 6px; spacing: 5px; }}
QStatusBar QToolButton#statusIcon {{ background: transparent; border: none; border-radius: 6px; padding: 3px; }}
QStatusBar QToolButton#statusIcon:hover {{ background: {c['chip']}; }}
QStatusBar QToolButton#statusIcon:checked {{ background: {c['chip']}; }}
QStatusBar QCheckBox#statusCheck:hover {{ color: {c['text']}; }}
QSlider#iconSlider::groove:horizontal {{ height: 3px; background: {c['border']}; border-radius: 1px; }}
QSlider#iconSlider::sub-page:horizontal {{ background: {c['accent']}; border-radius: 1px; }}
QSlider#iconSlider::handle:horizontal {{ width: 11px; height: 11px; margin: -4px 0; border-radius: 5px;
    background: {c['text']}; }}
"""


def inspector_qss(c):
    """Style for the inspector panel."""
    return f"""
#inspector {{ background: {c['panel']}; }}
#inspector QLabel {{ background: transparent; }}
#inspector QSplitter::handle:vertical {{ background: transparent; border-top: 1px solid {c['border']};
                                         margin: 4px 40px; }}
#inspector QSplitter::handle:vertical:hover {{ border-top: 2px solid {c['accent']}; }}
#inspector QLabel[role="key"] {{ color: {c['dim']}; }}
#inspector QLabel[role="note"] {{ color: {c['dim']}; }}
#inspector QToolButton#detailsToggle {{ color: {c['dim']}; border: none; padding: 2px 0; text-align: left; }}
#inspector QToolButton#detailsToggle:hover {{ color: {c['text']}; }}
#inspector QPlainTextEdit {{ background: {c['base']}; border: 1px solid {c['border']}; border-radius: 8px;
                              padding: 4px; color: {c['text']}; }}
"""


# ----- hover highlight (FilePilot-style rounded band) ---------------------------------
def hover_color(c: dict, strength: int) -> QColor:
    """The hover band: the text color at low opacity, so it lightens dark themes and darkens
    light ones. strength 0 (off) to 100 (strong)."""
    col = QColor(c["text"])
    col.setAlpha(round(max(0, min(100, strength)) * 0.7))     # 0..70 of 255
    return col


def paint_hover(p: QPainter, rect, color: QColor):
    """A soft rounded band, inset a little from the row's edges."""
    if color.alpha() == 0:
        return
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawRoundedRect(QRectF(rect).adjusted(3, 1, -3, -1), 6, 6)
    p.restore()


# ----- cloud sync status glyphs (like Explorer's Status column) ------------------------------
CLOUD_GLYPHS = {"online": ("\ue753", None), "local": ("\ue930", "#2fa84f"), "pinned": ("\uec61", "#2fa84f")}


def paint_cloud(p: QPainter, rect, state: str, dim_color):
    """A small cloud (online-only) or green check (on this device; solid: always kept)."""
    glyph, color = CLOUD_GLYPHS.get(state, (None, None))
    fam = _icon_family()
    if not glyph or not fam:
        return
    p.save()
    f = QFont(fam)
    f.setPixelSize(max(10, min(14, rect.height() - 8)))
    p.setFont(f)
    p.setPen(QColor(color) if color else dim_color)
    p.drawText(rect, Qt.AlignmentFlag.AlignCenter, glyph)
    p.restore()


# ----- skinny storage pill (status bar, This PC) ------------------------------------
FULL_AT = 0.9                      # turns red past this, like Explorer


def paint_usage(p: QPainter, r: QRectF, used: float, full_warning: bool = True):
    """A thin rounded track with the used share filled in the accent (red when nearly full)."""
    c = _current
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    rad = r.height() / 2
    track = QColor(c["dim"])
    track.setAlpha(60)
    p.setBrush(track)
    p.drawRoundedRect(r, rad, rad)
    used = min(max(used, 0.0), 1.0)
    if used > 0:
        p.setBrush(QColor("#e5484d" if full_warning and used >= FULL_AT else c["accent"]))
        p.drawRoundedRect(QRectF(r.left(), r.top(), max(r.height(), r.width() * used), r.height()), rad, rad)
    p.restore()
