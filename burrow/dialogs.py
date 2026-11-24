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
    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Settings")
        self.setMinimumWidth(560)

        # TeraCopy
        tc_box = QGroupBox("TeraCopy")
        tc = QFormLayout(tc_box)
        self.use_tc = QCheckBox("Use TeraCopy for copy and move (paste, drag and drop, Copy to / Move to)")
        self.use_tc.setChecked(config.get("use_teracopy"))
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
        self.tc_close = QCheckBox("Close TeraCopy when the transfer finishes")
        self.tc_close.setChecked(config.get("teracopy_close"))
        tc.addRow(self.use_tc)
        tc.addRow("TeraCopy.exe:", path_row)
        tc.addRow("", self.tc_status)
        tc.addRow(self.tc_close)
        note = QLabel("Copying items into the folder they're already in (making “- Copy” duplicates) "
                      "always uses the built-in copier. Edit ▸ Paste with built-in copier skips TeraCopy.")
        note.setWordWrap(True)
        tc.addRow(note)

        # Everything (voidtools)
        ev_box = QGroupBox("Everything (voidtools)")
        ev = QFormLayout(ev_box)
        self.ev_sizes = QCheckBox("Show folder sizes from Everything")
        self.ev_sizes.setChecked(config.get("everything_sizes"))
        from . import everything
        running = everything.is_running()
        ev_status = QLabel(
            "✓ Everything is running. Folder sizes need “Index folder size” turned on in Everything "
            "(Tools ▸ Options ▸ Indexes)." if running else
            "⚠ Everything isn't running. Start it (voidtools.com) and folder sizes appear automatically.")
        ev_status.setWordWrap(True)
        ev.addRow(self.ev_sizes)
        ev.addRow(ev_status)

        # General
        gen_box = QGroupBox("General")
        gen = QFormLayout(gen_box)
        self.show_hidden = QCheckBox("Show hidden and system files")
        self.show_hidden.setChecked(config.get("show_hidden"))
        self.confirm = QCheckBox("Ask before moving items to the Recycle Bin")
        self.confirm.setChecked(config.get("confirm_delete"))
        self.new_tab = QLineEdit(config.get("new_tab_path"))
        self.new_tab.setPlaceholderText("Home folder")
        nt_browse = QPushButton("Browse…")
        nt_browse.clicked.connect(self._browse_new_tab)
        nt_row = QHBoxLayout()
        nt_row.addWidget(self.new_tab, 1)
        nt_row.addWidget(nt_browse)
        self.date_fmt = QComboBox()
        now = datetime.now()
        for f in DATE_FORMATS:
            self.date_fmt.addItem(now.strftime(f), f)
        idx = self.date_fmt.findData(config.get("date_format"))
        self.date_fmt.setCurrentIndex(max(idx, 0))
        self.theme = QComboBox()
        for label, key in (("Match Windows", "system"), ("Light", "light"), ("Dark", "dark")):
            self.theme.addItem(label, key)
        self.theme.setCurrentIndex(max(self.theme.findData(config.get("theme")), 0))
        gen.addRow("Theme:", self.theme)
        gen.addRow(self.show_hidden)
        gen.addRow(self.confirm)
        gen.addRow("New tabs open:", nt_row)
        gen.addRow("Date format:", self.date_fmt)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(tc_box)
        lay.addWidget(ev_box)
        lay.addWidget(gen_box)
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
        self.config.update({
            "use_teracopy": self.use_tc.isChecked(),
            "teracopy_path": self.tc_path.text().strip(),
            "teracopy_close": self.tc_close.isChecked(),
            "show_hidden": self.show_hidden.isChecked(),
            "confirm_delete": self.confirm.isChecked(),
            "new_tab_path": self.new_tab.text().strip(),
            "date_format": self.date_fmt.currentData(),
            "theme": self.theme.currentData(),
            "everything_sizes": self.ev_sizes.isChecked(),
        })
        self.accept()
