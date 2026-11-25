"""Settings, bookmarks and view presets, saved as JSON in %APPDATA%\\Burrow."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

APP_NAME = "Burrow"
CONFIG_DIR = Path(os.environ.get("APPDATA") or Path.home()) / APP_NAME
CONFIG_FILE = CONFIG_DIR / "settings.json"

# Logical column order of the folder model. Visible columns and their order
# come from the active view layout.
COLUMNS = ["name", "size", "type", "modified", "created", "accessed", "ext", "attrs"]
COLUMN_TITLES = {
    "name": "Name",
    "size": "Size",
    "type": "Type",
    "modified": "Date modified",
    "created": "Date created",
    "accessed": "Date accessed",
    "ext": "Extension",
    "attrs": "Attributes",
}
DEFAULT_WIDTHS = {
    "name": 340, "size": 90, "type": 150, "modified": 140,
    "created": 140, "accessed": 140, "ext": 80, "attrs": 80,
}

# A layout (and a preset) is: visible columns in order, widths, sort, folders-first.
BUILTIN_PRESETS = {
    "Details + Dates": {
        "columns": ["name", "size", "type", "created", "modified"],
        "sort_column": "name", "sort_desc": False, "folders_first": True,
    },
    "Recent first": {
        "columns": ["name", "modified", "created", "size", "type"],
        "sort_column": "modified", "sort_desc": True, "folders_first": False,
    },
    "Simple": {
        "columns": ["name", "size", "modified"],
        "sort_column": "name", "sort_desc": False, "folders_first": True,
    },
}

DATE_FORMATS = [
    "%Y-%m-%d %H:%M",
    "%d/%m/%Y %H:%M",
    "%m/%d/%Y %I:%M %p",
    "%d %b %Y %H:%M",
    "%Y-%m-%d %H:%M:%S",
]


def normalize_layout(layout: dict | None) -> dict:
    base = copy.deepcopy(BUILTIN_PRESETS["Details + Dates"])
    base["widths"] = dict(DEFAULT_WIDTHS)
    if not isinstance(layout, dict):
        return base
    cols: list[str] = []
    for c in layout.get("columns", []):
        if c in COLUMNS and c not in cols:
            cols.append(c)
    if "name" not in cols:
        cols.insert(0, "name")
    base["columns"] = cols
    for k, v in (layout.get("widths") or {}).items():
        if k in COLUMNS and isinstance(v, int) and v >= 24:
            base["widths"][k] = v
    if layout.get("sort_column") in COLUMNS:
        base["sort_column"] = layout["sort_column"]
    base["sort_desc"] = bool(layout.get("sort_desc", base["sort_desc"]))
    base["folders_first"] = bool(layout.get("folders_first", base["folders_first"]))
    return base


DEFAULTS = {
    "show_hidden": False,
    "confirm_delete": True,
    "use_teracopy": True,
    "teracopy_path": "",
    "teracopy_close": False,
    "new_tab_path": "",
    "date_format": DATE_FORMATS[0],
    "theme": "system",
    "palette": "burrow",
    "everything_sizes": True,
    # feature switches (Settings)
    "show_folder_icons": True,      # Explorer folder colors/icons in the list and sidebar
    "show_tags": True,              # tag dots, Tags menu and sidebar section
    "preview_images": True,         # photos, RAW, PDFs (zoomable)
    "preview_media": True,          # video and audio player
    "preview_text": True,
    "text_syntax_colors": True,
    "preview_archives": True,
    "media_arrow_seek": True,
    "sidebar_places": True,
    "sidebar_cloud": True,
    "sidebar_tags": True,
    "sidebar_network": True,
    "single_instance": True,        # later launches open as tabs
    "quick_access_sync": True,      # bookmarks pinned/unpinned in Explorer Quick Access
    "show_menubar": False,
    "bookmarks": [],
    "recent_servers": [],
    "sidebar_collapsed": [],
    "presets": {},
    "active_preset": "Details + Dates",
    "view": normalize_layout(BUILTIN_PRESETS["Details + Dates"]),
    "session_tabs": [],
    "session_current": 0,
    "window_geometry": "",
    "window_state": "",
}


def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(p))


class Config(QObject):
    viewChanged = Signal(object)      # the FolderView that made the change, or None
    settingsChanged = Signal()
    bookmarksChanged = Signal()
    bookmarkAdded = Signal(str)
    bookmarkRemoved = Signal(str)
    presetsChanged = Signal()

    def __init__(self):
        super().__init__()
        self.data = copy.deepcopy(DEFAULTS)
        self.view_version = 0
        self._load()
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(800)
        self._save_timer.timeout.connect(self.save)

    # ----- persistence -------------------------------------------------
    def _load(self):
        try:
            loaded = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(loaded, dict):
            return
        for key, default in DEFAULTS.items():
            if key in loaded and isinstance(loaded[key], type(default)):
                self.data[key] = loaded[key]
        self.data["view"] = normalize_layout(self.data.get("view"))

    def save(self):
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            tmp = CONFIG_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
            os.replace(tmp, CONFIG_FILE)
        except OSError:
            pass

    def schedule_save(self):
        self._save_timer.start()

    # ----- plain settings ----------------------------------------------
    def get(self, key):
        return self.data.get(key, DEFAULTS.get(key))

    def set(self, key, value):
        self.update({key: value})

    def update(self, values: dict):
        self.data.update(values)
        self.schedule_save()
        self.settingsChanged.emit()

    # ----- the shared view layout (applies to every folder) -----------
    @property
    def view(self) -> dict:
        return self.data["view"]

    def set_view(self, layout: dict, source=None):
        self.data["view"] = normalize_layout(layout)
        self.view_version += 1
        self.schedule_save()
        self.viewChanged.emit(source)

    def presets(self) -> dict:
        merged = {k: v for k, v in BUILTIN_PRESETS.items()}
        merged.update(self.data["presets"])
        return merged

    def is_builtin(self, name: str) -> bool:
        return name in BUILTIN_PRESETS

    def is_user_preset(self, name: str) -> bool:
        return name in self.data["presets"]

    def apply_preset(self, name: str):
        preset = self.presets().get(name)
        if preset is None:
            return
        layout = copy.deepcopy(preset)
        widths = dict(self.view.get("widths", {}))
        widths.update(preset.get("widths", {}))
        layout["widths"] = widths
        self.data["active_preset"] = name
        self.set_view(layout)

    def save_preset(self, name: str, layout: dict):
        self.data["presets"][name] = copy.deepcopy(normalize_layout(layout))
        self.data["active_preset"] = name
        self.schedule_save()
        self.presetsChanged.emit()

    def delete_preset(self, name: str):
        if self.data["presets"].pop(name, None) is not None:
            self.schedule_save()
            self.presetsChanged.emit()

    def rename_preset(self, old: str, new: str):
        if old in self.data["presets"] and new and new not in self.presets():
            self.data["presets"][new] = self.data["presets"].pop(old)
            if self.data["active_preset"] == old:
                self.data["active_preset"] = new
            self.schedule_save()
            self.presetsChanged.emit()

    def view_matches_active(self) -> bool:
        preset = self.presets().get(self.data["active_preset"])
        if preset is None:
            return False
        p = normalize_layout(preset)
        v = self.view
        return all(p[k] == v[k] for k in ("columns", "sort_column", "sort_desc", "folders_first"))

    # ----- bookmarks ----------------------------------------------------
    def bookmarks(self) -> list[dict]:
        return self.data["bookmarks"]

    def is_bookmarked(self, path: str) -> bool:
        n = _norm(path)
        return any(_norm(b["path"]) == n for b in self.bookmarks())

    def add_bookmark(self, path: str, name: str | None = None):
        if self.is_bookmarked(path):
            return
        self.bookmarks().append({"name": name or os.path.basename(path.rstrip("\\/")) or path, "path": path})
        self._bookmarks_edited()
        self.bookmarkAdded.emit(path)

    def remove_bookmark(self, path: str):
        n = _norm(path)
        self.data["bookmarks"] = [b for b in self.bookmarks() if _norm(b["path"]) != n]
        self._bookmarks_edited()
        self.bookmarkRemoved.emit(path)

    def rename_bookmark(self, index: int, name: str):
        if 0 <= index < len(self.bookmarks()) and name:
            self.bookmarks()[index]["name"] = name
            self._bookmarks_edited()

    def move_bookmark(self, index: int, delta: int):
        bms = self.bookmarks()
        j = index + delta
        if 0 <= index < len(bms) and 0 <= j < len(bms):
            bms[index], bms[j] = bms[j], bms[index]
            self._bookmarks_edited()

    def _bookmarks_edited(self):
        self.schedule_save()
        self.bookmarksChanged.emit()
