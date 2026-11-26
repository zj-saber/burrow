"""Settings dialog and view-preset dialogs."""
from __future__ import annotations

import os
from datetime import datetime

from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox,
                               QPushButton, QVBoxLayout)

from . import fileops
from .config import COLUMN_TITLES, DATE_FORMATS, Config


# ----- presets --------------------------------------------------------------
def add_preset_actions(menu: QMenu, config: Config, parent):
    """Fill a menu with the view presets (radio items) plus save/manage entries."""
    group = QActionGroup(menu)
    group.setExclusive(True)
    active = config.get("active_preset") if config.view_matches_active() else None
    for name in config.presets():
        a = menu.addAction(name)
        a.setCheckable(True)
        a.setChecked(name == active)
        group.addAction(a)
        a.triggered.connect(lambda _=False, n=name: config.apply_preset(n))
    menu.addSeparator()
    menu.addAction("Save current view as preset…").triggered.connect(
        lambda: save_preset_dialog(parent, config))
    menu.addAction("Manage presets…").triggered.connect(
        lambda: ManagePresetsDialog(config, parent).exec())


def save_preset_dialog(parent, config: Config):
    cols = ", ".join(COLUMN_TITLES[c] for c in config.view["columns"])
    suggested = config.get("active_preset") if config.is_user_preset(config.get("active_preset")) else ""
    name, ok = QInputDialog.getText(
        parent, "Save view preset",
        f"The preset stores the columns ({cols}), their widths, the sort order\n"
        "and folders-first. It is applied to every folder.\n\nPreset name:",
        text=suggested)
    name = name.strip()
    if not ok or not name:
        return
    if name in config.presets():
        if QMessageBox.question(parent, "Replace preset", f'Replace the preset "{name}"?') \
                != QMessageBox.StandardButton.Yes:
            return
    config.save_preset(name, config.view)


class ManagePresetsDialog(QDialog):
    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("View presets")
        self.resize(420, 320)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._sync)
        self.list.itemDoubleClicked.connect(lambda _: self._apply())
        self.b_apply = QPushButton("Apply")
        self.b_rename = QPushButton("Rename…")
        self.b_delete = QPushButton("Delete")
        self.b_apply.clicked.connect(self._apply)
        self.b_rename.clicked.connect(self._rename)
        self.b_delete.clicked.connect(self._delete)
        side = QVBoxLayout()
        for b in (self.b_apply, self.b_rename, self.b_delete):
            side.addWidget(b)
        side.addStretch(1)
        row = QHBoxLayout()
        row.addWidget(self.list, 1)
        row.addLayout(side)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(row)
        lay.addWidget(QLabel("Built-in presets can't be removed. Deleting a customized built-in restores it."))
        lay.addWidget(buttons)
        self._fill()

    def _fill(self):
        self.list.clear()
        for name, p in self.config.presets().items():
            tag = ""
            if self.config.is_builtin(name):
                tag = "  (built-in, customized)" if self.config.is_user_preset(name) else "  (built-in)"
            cols = ", ".join(COLUMN_TITLES[c] for c in p["columns"])
            item = QListWidgetItem(f"{name}{tag}\n    {cols}")
            item.setData(256, name)
            self.list.addItem(item)
        self.list.setCurrentRow(0)

    def _name(self):
        item = self.list.currentItem()
        return item.data(256) if item else None

    def _sync(self, *_):
        n = self._name()
        user = bool(n) and self.config.is_user_preset(n)
        self.b_delete.setEnabled(user)
        self.b_rename.setEnabled(user and not self.config.is_builtin(n))
        self.b_apply.setEnabled(bool(n))

    def _apply(self):
        if self._name():
            self.config.apply_preset(self._name())

    def _rename(self):
        old = self._name()
        new, ok = QInputDialog.getText(self, "Rename preset", "New name:", text=old)
        new = new.strip()
        if ok and new and new != old:
            if new in self.config.presets():
                QMessageBox.warning(self, "Rename preset", f'A preset named "{new}" already exists.')
                return
            self.config.rename_preset(old, new)
            self._fill()

    def _delete(self):
        n = self._name()
        if n and QMessageBox.question(self, "Delete preset", f'Delete the preset "{n}"?') \
                == QMessageBox.StandardButton.Yes:
            self.config.delete_preset(n)
            self._fill()


# ----- settings -----------------------------------------------------------------
class SettingsDialog(QDialog):
    """Settings in four tabs. Every feature has its own on/off switch."""

    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QScrollArea, QTabWidget, QWidget
        from . import everything, integration
        from . import theme as _theme
        self.config = config
        self.setWindowTitle("Settings")
        self.setMinimumSize(620, 560)
        self._checks: dict[str, QCheckBox] = {}

        def check(key, text, tip=""):
            cb = QCheckBox(text)
            cb.setChecked(bool(config.get(key)))
            if tip:
                cb.setToolTip(tip)
            self._checks[key] = cb
            return cb

        def note(text):
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lbl.setProperty("role", "key")
            return lbl

        def page(*groups):
            w = QWidget()
            lay = QVBoxLayout(w)
            for g in groups:
                lay.addWidget(g)
            lay.addStretch(1)
            sa = QScrollArea()
            sa.setWidgetResizable(True)
            sa.setFrameShape(QScrollArea.Shape.NoFrame)
            sa.setWidget(w)
            return sa

        # ----- General -------------------------------------------------------------
        look = QGroupBox("Look")
        f = QFormLayout(look)
        self.theme = QComboBox()
        for label, key in (("Match Windows", "system"), ("Light", "light"), ("Dark", "dark")):
            self.theme.addItem(label, key)
        self.theme.setCurrentIndex(max(self.theme.findData(config.get("theme")), 0))
        self.palette = QComboBox()
        for key, label in _theme.PALETTE_NAMES.items():
            self.palette.addItem(label, key)
        self.palette.setCurrentIndex(max(self.palette.findData(config.get("palette")), 0))
        self.date_fmt = QComboBox()
        now = datetime.now()
        for fmt in DATE_FORMATS:
            self.date_fmt.addItem(now.strftime(fmt), fmt)
        self.date_fmt.setCurrentIndex(max(self.date_fmt.findData(config.get("date_format")), 0))
        f.addRow("Theme:", self.theme)
        f.addRow("Colors:", self.palette)
        from . import backdrop as _bd
        self.backdrop = QComboBox()
        for label, data in [(v, k) for k, v in _bd.NAMES.items()]:
            self.backdrop.addItem(label, data)
        self.backdrop.setCurrentIndex(max(self.backdrop.findData(config.get("backdrop")), 0))
        self.backdrop_tint = QComboBox()
        for label, data in [("Light (more see-through)", "light"), ("Medium", "medium"), ("Strong (more solid)", "strong")]:
            self.backdrop_tint.addItem(label, data)
        self.backdrop_tint.setCurrentIndex(max(self.backdrop_tint.findData(config.get("backdrop_tint")), 0))
        if not _bd.supported():
            self.backdrop.setEnabled(False)
            self.backdrop_tint.setEnabled(False)
            self.backdrop.setToolTip("Needs Windows 11 22H2 or newer")
        f.addRow("Window material:", self.backdrop)
        f.addRow("Material tint:", self.backdrop_tint)
        f.addRow(note("Mica and Acrylic are drawn by Windows itself, so they cost almost nothing. "
                      "They show while the window is active."))
        f.addRow("Date format:", self.date_fmt)

        behave = QGroupBox("Behavior")
        f = QFormLayout(behave)
        self.new_tab = QLineEdit(config.get("new_tab_path"))
        self.new_tab.setPlaceholderText("Home folder")
        nt_browse = QPushButton("Browse…")
        nt_browse.clicked.connect(self._browse_new_tab)
        nt_row = QHBoxLayout()
        nt_row.addWidget(self.new_tab, 1)
        nt_row.addWidget(nt_browse)
        f.addRow("New tabs open:", nt_row)
        f.addRow(check("confirm_delete", "Ask before moving items to the Recycle Bin"))
        f.addRow(check("confirm_delete_permanent", "Ask before deleting permanently (Shift+Del)"))
        self.shortcut_scheme = QComboBox()
        for label, data in [("Windows Explorer", "explorer"), ("Burrow classic (0.11)", "classic")]:
            self.shortcut_scheme.addItem(label, data)
        self.shortcut_scheme.setCurrentIndex(max(self.shortcut_scheme.findData(config.get("shortcut_scheme")), 0))
        f.addRow("Keyboard shortcuts:", self.shortcut_scheme)
        f.addRow(check("arrow_sidebar_nav", "← from the file list goes to the sidebar; ↑/↓ there browse, → comes back"))
        f.addRow(check("single_instance", "Open new windows as tabs in the running Burrow",
                       "When off, every launch opens its own window (takes effect next start)."))

        # ----- Preview -------------------------------------------------------------
        prev = QGroupBox("Inspector previews")
        f = QFormLayout(prev)
        f.addRow(check("preview_images", "Photos, RAW photos and PDFs (zoomable)"))
        f.addRow(check("preview_media", "Video and audio player"))
        f.addRow(check("media_arrow_seek", "← / → seek the previewed video or song (Shift: 30 s)"))
        f.addRow(check("preview_text", "Text files (YAML, JSON, code, logs…)"))
        f.addRow(check("text_syntax_colors", "Syntax colors in text previews"))
        f.addRow(check("preview_archives", "Archive contents (zip, rar, 7z…)"))
        f.addRow(check("preview_epub", "EPUB books (cover, details and a chapter reader)"))
        f.addRow(check("inspector_resizable", "Drag handle to make photo / PDF / video previews taller"))
        f.addRow(note("Turned-off previews still show the file's details."))

        # ----- Sidebar & list ------------------------------------------------------
        lst = QGroupBox("File list")
        f = QFormLayout(lst)
        f.addRow(check("show_hidden", "Show hidden and system files"))
        f.addRow(check("show_folder_icons", "Show Explorer folder colors and custom folder icons"))
        f.addRow(check("show_tags", "Color tags (dots, Tags menu and sidebar section)"))
        self.view_mode = QComboBox()
        for label, data in [("List", "list"), ("Columns", "columns")]:
            self.view_mode.addItem(label, data)
        self.view_mode.setCurrentIndex(max(self.view_mode.findData(config.get("view_mode")), 0))
        f.addRow("New tabs open as:", self.view_mode)
        f.addRow(check("ctrl_wheel_icon_size", "Ctrl+mouse wheel changes the icon size"))
        f.addRow(check("drive_usage_bars", "This PC shows how full each drive is"))
        f.addRow(check("network_keepalive", "Keep network drives connected while one is open",
                       "Touches the open network folder every 4 minutes so Windows doesn't drop the "
                       "connection; a dropped drive is retried automatically either way."))
        f.addRow(check("auto_fit_columns", "Fit columns to their widest item (your widths come back when off)"))
        f.addRow(check("clipboard_marks", "Tint copied (cyan), cut (yellow) and pasting (green) items"))
        f.addRow(check("bulk_rename_f2", "F2 with several items selected opens Bulk rename"))
        f.addRow(check("context_archive", "Extract and Add to archive in the right-click menu",
                       "Uses NanaZip or 7-Zip, else WinRAR, else Windows' built-in tar."))
        bar = QGroupBox("Status bar")
        fb = QFormLayout(bar)
        fb.addRow(check("status_sort_toggle", "Sort switch (folders first / mixed)"))
        fb.addRow(check("status_view_toggle", "View switch (list / columns)"))
        fb.addRow(check("status_icon_slider", "Icon size slider"))
        fb.addRow(check("status_storage_bar", "Storage pill (how full the drive is)"))
        fb.addRow(check("status_folder_button", "Auto folder button (folders for loose files)"))
        fb.addRow(check("status_auto_update", "Auto update check mark"))
        fb.addRow(check("status_update_button", "Update button when a new version is out"))
        side = QGroupBox("Sidebar sections")
        f = QFormLayout(side)
        f.addRow(check("sidebar_places", "Places (Home, Desktop, Documents…)"))
        f.addRow(check("sidebar_cloud", "Cloud (OneDrive, iCloud…)"))
        f.addRow(check("sidebar_tags", "Tags"))
        f.addRow(check("sidebar_network", "Network"))
        f.addRow(check("sidebar_drag_reorder", "Drag section headings to reorder them"))
        f.addRow(check("sidebar_eject", "Eject buttons next to USB drives, cards, DVDs and network drives"))
        f.addRow(note("Bookmarks and This PC are always shown. Any section can also be collapsed by clicking its heading."))

        # ----- Integrations --------------------------------------------------------
        win = QGroupBox("Windows")
        f = QFormLayout(win)
        self.default_fm = QCheckBox("Open folders and drives in Burrow")
        self.default_fm.setChecked(integration.is_default())
        self._default_was = self.default_fm.isChecked()
        f.addRow(self.default_fm)
        f.addRow(note("Desktop, Explorer, the Run box, shortcuts and apps that open a folder go to Burrow, as tabs."))
        f.addRow(check("win_e", "Open Burrow with Win+E",
                       "Burrow catches Win+E while it runs, so Explorer doesn't open."))
        self.win_e_opens = QComboBox()
        self.win_e_opens.addItem("a new window", "window")
        self.win_e_opens.addItem("a new tab", "tab")
        self.win_e_opens.setCurrentIndex(max(self.win_e_opens.findData(config.get("win_e_opens")), 0))
        f.addRow("Win+E opens:", self.win_e_opens)
        f.addRow(check("run_in_background", "Keep Burrow running in the background and start it with Windows",
                       "A tray icon keeps Burrow ready, so Win+E always works. Closing the window hides it; "
                       "quit from the tray icon or File ▸ Exit."))
        f.addRow(check("quick_access_sync", "Mirror bookmarks to Explorer Quick Access",
                       "Adding or removing a bookmark pins or unpins the folder in Explorer."))
        f.addRow(note("Folder colors set in Burrow are always written so Explorer shows them too. "
                      "The Open/Save dialogs inside other apps can't be replaced."))

        tc_box = QGroupBox("TeraCopy")
        tc = QFormLayout(tc_box)
        self.use_tc = check("use_teracopy", "Use TeraCopy for copy and move (paste, drag and drop, Copy to / Move to)")
        self.tc_path = QLineEdit(config.get("teracopy_path"))
        self.tc_path.setPlaceholderText("Detect automatically")
        self.tc_path.textChanged.connect(self._update_tc_status)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_tc)
        detect = QPushButton("Detect")
        detect.clicked.connect(self._detect_tc)
        path_row = QHBoxLayout()
        path_row.addWidget(self.tc_path, 1)
        path_row.addWidget(browse)
        path_row.addWidget(detect)
        self.tc_status = QLabel()
        self.tc_status.setWordWrap(True)
        tc.addRow(self.use_tc)
        tc.addRow("TeraCopy.exe:", path_row)
        tc.addRow("", self.tc_status)
        tc.addRow(check("teracopy_close", "Close TeraCopy when the transfer finishes"))
        tc.addRow(note("Duplicating items in their own folder always uses the built-in copier."))

        ev_box = QGroupBox("Everything (voidtools)")
        ev = QFormLayout(ev_box)
        ev.addRow(check("everything_sizes", "Show folder sizes from Everything"))
        ev.addRow(note("✓ Everything is running. Folder sizes need “Index folder size” in Everything's options."
                       if everything.is_running() else
                       "⚠ Everything isn't running. Start it and folder sizes appear automatically."))

        upd = QGroupBox("Updates")
        f = QFormLayout(upd)
        f.addRow(check("check_updates", "Auto update: check for a new version every time Burrow starts",
                       "Asks GitHub for the latest Burrow release. Nothing else is sent."))
        from . import __version__
        now = QPushButton("Check now")
        now.clicked.connect(lambda: (self.parent() and self.parent().check_updates()))
        row = QHBoxLayout()
        row.addWidget(QLabel(f"You have Burrow {__version__}."))
        row.addStretch(1)
        row.addWidget(now)
        f.addRow(row)
        f.addRow(note("When there's a new version, an Update button shows in the status bar. "
                      "Updating downloads it, restarts Burrow and keeps your settings."))

        tabs = QTabWidget()
        tabs.addTab(page(look, behave, upd), "General")
        tabs.addTab(page(prev), "Preview")
        tabs.addTab(page(lst, bar, side), "Sidebar && list")
        tabs.addTab(page(win, tc_box, ev_box), "Integrations")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(tabs, 1)
        lay.addWidget(buttons)
        self._update_tc_status()

    def _update_tc_status(self):
        typed = self.tc_path.text().strip()
        if typed and not os.path.isfile(typed):
            self.tc_status.setText("⚠ That file doesn't exist.")
            return
        found = fileops.find_teracopy(typed)
        self.tc_status.setText(f"✓ Using {found}" if found else
                               "⚠ TeraCopy wasn't found. Install it or browse to TeraCopy.exe; "
                               "until then the built-in copier is used.")

    def _browse_tc(self):
        p, _ = QFileDialog.getOpenFileName(self, "Locate TeraCopy.exe", self.tc_path.text() or "C:\\Program Files",
                                           "TeraCopy (TeraCopy.exe);;Programs (*.exe)")
        if p:
            self.tc_path.setText(os.path.normpath(p))

    def _detect_tc(self):
        found = fileops.find_teracopy("")
        self.tc_path.setText(found)
        if not found:
            self._update_tc_status()

    def _browse_new_tab(self):
        p = QFileDialog.getExistingDirectory(self, "Folder for new tabs", self.new_tab.text() or os.path.expanduser("~"))
        if p:
            self.new_tab.setText(os.path.normpath(p))

    def _save(self):
        from . import integration
        for box, was, fn, label in ((self.default_fm, self._default_was, integration.set_default, "Open folders in Burrow"),):
            if box.isChecked() != was:
                err = fn(box.isChecked())
                if err:
                    QMessageBox.warning(self, label, f"Couldn't change the setting: {err}")
        values = {key: cb.isChecked() for key, cb in self._checks.items()}
        values.update({
            "teracopy_path": self.tc_path.text().strip(),
            "new_tab_path": self.new_tab.text().strip(),
            "date_format": self.date_fmt.currentData(),
            "theme": self.theme.currentData(),
            "palette": self.palette.currentData(),
            "win_e_opens": self.win_e_opens.currentData(),
            "backdrop": self.backdrop.currentData(),
            "backdrop_tint": self.backdrop_tint.currentData(),
            "shortcut_scheme": self.shortcut_scheme.currentData(),
            "view_mode": self.view_mode.currentData(),
        })
        self.config.update(values)
        self.accept()
