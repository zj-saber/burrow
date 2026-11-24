"""Flat light/dark theme.

Performance rule: the big file list is themed only through QPalette and a thin delegate.
Qt style sheets are used only on small chrome widgets (tab strip, nav bar, sidebar, header,
status bar). A style sheet on the application or on the main window would route every row of
every file list through QStyleSheetStyle, which slows painting noticeably.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QIcon, QPainter, QPalette, QPixmap

THEMES = {
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

_current: dict = dict(THEMES["light"], name="light")


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


def apply(app, mode: str) -> dict:
    global _current
    name = resolve(mode)
    _current = dict(THEMES[name], name=name)
    if app.style().name().lower() != "fusion":
        app.setStyle("Fusion")
    app.setPalette(_palette(_current))
    _icon_cache.clear()
    return _current


# ----- icons drawn from the Windows icon font (crisp, no image files) -----------
GLYPHS = {
    "back": "", "forward": "", "up": "", "refresh": "",
    "settings": "", "inspector": "", "menu": "", "add": "",
    "sidebar": "", "close": "", "chevron": "",
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
    key = (name, size, _current["name"])
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
    return f"""
#tabstrip {{ background: {c['window']}; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{
    background: transparent; color: {c['dim']}; border: none;
    padding: 6px 10px 7px 12px; margin: 0 1px; min-width: 60px; max-width: 220px;
    border-top-left-radius: 8px; border-top-right-radius: 8px;
}}
QTabBar::tab:selected {{ background: {c['base']}; color: {c['text']}; }}
QTabBar::tab:hover:!selected {{ background: {c['chip']}; color: {c['text']}; }}
QTabBar QToolButton {{ background: {c['window']}; border: none; }}
QTabBar QToolButton#tabclose {{ background: transparent; border-radius: 4px; padding: 1px; }}
QTabBar QToolButton#tabclose:hover {{ background: {c['border']}; }}
#tabstrip QToolButton#plus {{ border: none; border-radius: 6px; padding: 4px; margin: 0 0 3px 2px; }}
#tabstrip QToolButton#plus:hover {{ background: {c['chip']}; }}
"""


def navbar_qss(c):
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
"""


def sidebar_qss(c):
    return f"""
QTreeWidget {{ background: {c['panel']}; border: none; outline: 0; padding: 6px 6px; }}
QTreeWidget::item {{ height: 26px; padding-left: 4px; border-radius: 6px; }}
QTreeWidget::item:hover {{ background: {c['chip']}; }}
QTreeWidget::item:selected {{ background: {c['select']}; color: {c['seltext']}; }}
QTreeWidget::item:disabled {{ color: {c['dim']}; }}
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
    return f"""
QStatusBar {{ background: {c['window']}; border-top: 1px solid {c['border']}; }}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ color: {c['dim']}; padding: 0 8px; }}
"""


def inspector_qss(c):
    return f"""
#inspector {{ background: {c['panel']}; }}
#inspector QLabel {{ background: transparent; }}
#inspector QLabel[role="key"] {{ color: {c['dim']}; }}
#inspector QLabel[role="note"] {{ color: {c['dim']}; }}
#inspector QPlainTextEdit {{ background: {c['base']}; border: 1px solid {c['border']}; border-radius: 8px;
                              padding: 4px; color: {c['text']}; }}
"""
