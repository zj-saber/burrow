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
    "preview_epub": True,
    "media_arrow_seek": True,
    "sidebar_places": True,
    "sidebar_cloud": True,
    "sidebar_tags": True,
    "sidebar_network": True,
    "single_instance": True,        # later launches open as tabs
    "quick_access_sync": True,      # bookmarks pinned/unpinned in Explorer Quick Access
    "inspector_details": True,      # file details shown under the preview
    "inspector_split": 0.6,         # preview height share in the inspector
    "inspector_resizable": True,    # drag handle under photo / PDF / video previews
    "sidebar_order": [],            # section keys, top to bottom (dragged by their headings)
    "sidebar_drag_reorder": True,   # drag sidebar headings to reorder sections
    "icon_size": 16,                # list / columns icon size (slider at the bottom)
    "ctrl_wheel_icon_size": True,   # Ctrl+wheel over the list changes the icon size (like Explorer)
    "view_mode": "list",            # new tabs open as "list" or "columns"
    "status_sort_toggle": True,     # status bar: Sort folders-first / mixed
    "status_view_toggle": True,     # status bar: View list / columns
    "status_icon_slider": True,     # status bar: icon size slider
    "status_folder_button": True,   # status bar: Auto folder (folders for loose files)
    "status_storage_bar": True,     # status bar: how full the drive is, as a skinny pill
    "drive_usage_bars": True,       # This PC: the same pill beside each drive
    "sidebar_eject": True,          # eject buttons next to USB drives, cards, DVDs
    "check_updates": True,          # look for a new release on GitHub (every 12 h at most)
    "update_checked": 0,            # when that last happened (epoch seconds)
    "update_skip": "",              # a version the user chose to skip
    "pane_layout": None,            # split panes: nested layout with tabs per pane
    "sync_pairs": [],               # Compare & sync: recent folder pairs
    "sync_excludes": "Thumbs.db; desktop.ini; ~$*; *.tmp",
    "rename_recent": {},            # Bulk rename: last used fields
    "bulk_rename_f2": True,         # F2 with several items selected opens Bulk rename
    "clipboard_marks": True,        # tint copied (cyan), cut (yellow) and pasting (green) items
    "context_archive": True,        # right-click: Extract here / Extract to folder / Add to archive
    "confirm_delete_permanent": True,
    "arrow_sidebar_nav": True,      # ← from the list goes to the sidebar, → comes back
    "shortcut_scheme": "explorer",  # "explorer" (Windows Explorer keys) or "classic" (Burrow 0.11)
    "backdrop": "none",             # window material: none, mica, mica_alt, acrylic
    "backdrop_tint": "medium",      # how much theme color covers it: light, medium, strong
    "win_e_opens": "window",        # Win+E: "window" or "tab"
    "split": "",                    # "", "h" (side by side) or "v" (stacked)
    "session_tabs2": [],
    "win_e": False,                 # Win+E opens Burrow (keyboard hook while Burrow runs)
    "run_in_background": False,     # tray icon, start with Windows, closing hides
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
            loaded = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))   # tolerate a BOM (e.g. edited in PowerShell)
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
