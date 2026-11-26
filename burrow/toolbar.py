"""The command bar above the address bar (Explorer-style).

New ▾ | Cut  Copy  Paste  Rename  Extract  Delete | Sort ▾  View ▾ | Compare
Duplicates  Bulk rename  Auto folder.  Every button runs one of the window's existing
actions, so it does exactly what the menu item or shortcut does; the bar only adds the
buttons and keeps them enabled when they apply. The chevron at the end of the address bar
hides and shows it (config "command_bar").
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QActionGroup, QGuiApplication, QKeySequence
from PySide6.QtWidgets import QFrame, QHBoxLayout, QMenu, QToolButton, QWidget

from . import fileops, theme
from .config import COLUMNS
from .places import is_server, is_special

SORT_KEYS = (("name", "Name"), ("modified", "Date modified"), ("type", "Type"), ("size", "Size"))


class CommandBar(QWidget):
    """One row of buttons; `win` is the MainWindow whose actions they run."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setObjectName("cmdbar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 5, 8, 0)
        lay.setSpacing(2)
        self._lay = lay
        self._buttons = []                       # (button, icon name) for re-theming

        self.new_btn = self._button("new", "New", menu=self._new_menu())
        self._sep()
        self.cut_btn = self._button("cut", action=win.act_cut)
        self.copy_btn = self._button("copy", action=win.act_copy)
        self.paste_btn = self._button("paste", action=win.act_paste)
        self.rename_btn = self._button("rename", action=win.act_rename)
        self.extract_btn = self._button("extract", action=win.act_extract)
        self.delete_btn = self._button("delete", action=win.act_delete)
        self._sep()
        self.sort_menu = QMenu(self)
        self.sort_menu.aboutToShow.connect(self._fill_sort_menu)
        self.sort_btn = self._button("sort", "Sort", menu=self.sort_menu)
        self.view_menu = QMenu(self)
        self.view_menu.aboutToShow.connect(self._fill_view_menu)
        self.view_btn = self._button("view", "View", menu=self.view_menu)
        self._sep()
        self.compare_btn = self._button("compare", action=win.act_compare)
        self.dupes_btn = self._button("duplicates", action=win.act_dupes)
        self.bulk_btn = self._button("bulk_rename", action=win.act_bulk_rename)
        self.folder_btn = self._button("auto_folder", action=win.act_folderize)
        lay.addStretch(1)
        self._clip_files = False
        QGuiApplication.clipboard().dataChanged.connect(self._clipboard_changed)
        self._clipboard_changed()

    def _clipboard_changed(self):
        self._clip_files = bool(fileops.clipboard_paths()[0])     # read once per change, not per click
        self.sync()

    # ----- building ------------------------------------------------------------------------
    def _button(self, icon, text=None, *, action=None, menu=None):
        """A flat button: icon only (tooltip says what and the shortcut) or icon + label."""
        b = QToolButton(self)
        b.setAutoRaise(True)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setIcon(theme.icon(icon, 18))
        if text:
            b.setText(text + (" ▾" if menu else ""))
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        if action is not None:
            b.clicked.connect(action.trigger)
            name = action.text().replace("&&", "&").rstrip("…")
            keys = action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
            b.setToolTip(f"{name} ({keys})" if keys else name)
        if menu is not None:
            b.setMenu(menu)
            b.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._lay.addWidget(b)
        self._buttons.append((b, icon))
        return b

    def _sep(self):
        line = QFrame(self)
        line.setObjectName("cmdsep")
        line.setFixedSize(1, 20)
        self._lay.addSpacing(5)
        self._lay.addWidget(line, 0, Qt.AlignmentFlag.AlignVCenter)
        self._lay.addSpacing(5)

    def _new_menu(self):
        m = QMenu(self)
        m.addAction(self.win.act_new_folder)
        m.addAction(self.win.act_new_file)
        self.win.act_new_folder.setIcon(theme.icon("new_folder"))
        self.win.act_new_file.setIcon(theme.icon("new_file"))
        return m

    def _fill_sort_menu(self):
        """Sort by a column, the direction, and folders first, for the active tab."""
        m, v = self.sort_menu, self.win.view()
        m.clear()
        if v is None:
            return
        model = v.model
        cur = COLUMNS[model.sort_column] if 0 <= model.sort_column < len(COLUMNS) else "name"
        group = QActionGroup(m)
        for key, title in SORT_KEYS:
            a = m.addAction(title)
            a.setCheckable(True)
            a.setChecked(key == cur)
            group.addAction(a)
            a.triggered.connect(lambda _=False, k=key: self._sort(k, model.sort_desc))
        m.addSeparator()
        for desc, title in ((False, "Ascending"), (True, "Descending")):
            a = m.addAction(title)
            a.setCheckable(True)
            a.setChecked(model.sort_desc == desc)
            a.triggered.connect(lambda _=False, d=desc: self._sort(cur, d))
        m.addSeparator()
        ff = m.addAction("Folders first")
        ff.setCheckable(True)
        ff.setChecked(model.folders_first)
        ff.triggered.connect(lambda on: (v.set_folders_first(on), self.win._sync_sort_mode()))

    def _sort(self, key, desc):
        v = self.win.view()
        order = Qt.SortOrder.DescendingOrder if desc else Qt.SortOrder.AscendingOrder
        v.tree.sortByColumn(COLUMNS.index(key), order)          # saved like a header click

    def _fill_view_menu(self):
        m, w = self.view_menu, self.win
        m.clear()
        v = w.view()
        group = QActionGroup(m)
        cur = v.mode if v is not None else "list"
        for mode, title in w.VIEW_MODES:
            a = m.addAction(title)
            a.setCheckable(True)
            a.setChecked(mode == cur)
            group.addAction(a)
            a.triggered.connect(lambda _=False, md=mode: w.set_view_mode(md))
        m.addSeparator()
        for a in (w.act_hidden, w.act_sidebar, w.act_inspector):
            m.addAction(a)

    # ----- state ---------------------------------------------------------------------------
    def sync(self):
        """Enable each button only when it would do something here."""
        w = self.win
        v = w.view() if w.panes else None
        if v is None or v.path is None or not self.isVisible():
            return
        real = not (is_special(v.path) or is_server(v.path))     # not This PC / Network
        sel = w._selected() if real else []
        for b in (self.cut_btn, self.copy_btn, self.rename_btn, self.delete_btn):
            b.setEnabled(bool(sel))
        self.bulk_btn.setEnabled(bool(sel))
        self.new_btn.setEnabled(real)
        self.folder_btn.setEnabled(real)
        self.paste_btn.setEnabled(real and self._clip_files)
        self.extract_btn.setEnabled(w.can_extract(sel))
        self.sort_btn.setEnabled(not v.columns_active())

    def showEvent(self, ev):
        super().showEvent(ev)
        self.sync()

    def apply_theme(self):
        for b, name in self._buttons:
            b.setIcon(theme.icon(name, 18))
        self.win.act_new_folder.setIcon(theme.icon("new_folder"))
        self.win.act_new_file.setIcon(theme.icon("new_file"))
