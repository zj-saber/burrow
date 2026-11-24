"""Main window: tabs, toolbar, sidebar, inspector, menus and file operations."""
from __future__ import annotations

import ctypes
import os
import shutil
import sys

from PySide6.QtCore import QByteArray, QDir, QEvent, QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (QApplication, QComboBox, QCompleter, QDockWidget, QFileDialog,
                               QFileSystemModel, QLabel, QLineEdit, QMainWindow, QMenu,
                               QMessageBox, QProgressDialog, QStyle, QTabWidget, QToolBar,
                               QToolButton)
from PySide6.QtCore import QItemSelection, QItemSelectionModel

from . import dialogs, fileops
from .config import APP_NAME, Config
from .folderview import FolderView, display_path, normalize_path, title_for
from .inspector import Inspector
from .model import DRIVES, human_size, icon_cache
from .sidebar import Sidebar

HOME = os.path.expanduser("~")


class AddressBar(QLineEdit):
    escaped = Signal()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key.Key_Escape:
            self.escaped.emit()
            return
        super().keyPressEvent(ev)

    def focusInEvent(self, ev):
        super().focusInEvent(ev)
        QTimer.singleShot(0, self.selectAll)


class JobRunner(QObject):
    """Lives in the GUI thread; owns the worker thread and progress dialog of one copy job."""
    finished = Signal(object)

    def __init__(self, window, plan, move):
        super().__init__(window)
        self.window = window
        self.move = move
        self.verb = "Moving" if move else "Copying"
        self.job = fileops.CopyJob(plan, move)
        self.thread = QThread(self)
        self.job.moveToThread(self.thread)
        self.dialog = QProgressDialog(f"{self.verb} {len(plan)} item(s)…", "Cancel", 0, 1000, window)
        self.dialog.setWindowTitle(f"{self.verb} — {APP_NAME}")
        self.dialog.setWindowModality(Qt.WindowModality.NonModal)
        self.dialog.setMinimumDuration(400)
        self.dialog.setAutoClose(False)
        self.dialog.setAutoReset(False)
        self.dialog.setValue(0)
        self.dialog.canceled.connect(lambda: self.job.cancel())   # direct call; just sets a flag
        self.job.progress.connect(self.on_progress)
        self.job.done.connect(self.on_done)
        self.thread.started.connect(self.job.run)

    def start(self):
        self.thread.start()

    def cancel_and_wait(self):
        self.job.cancel()
        self.thread.quit()
        self.thread.wait(5000)

    @Slot(object, object, str)
    def on_progress(self, done, total, name):
        self.dialog.setValue(int(done * 1000 / total) if total else 0)
        if name:
            self.dialog.setLabelText(f"{self.verb} {name}\n{human_size(done)} of {human_size(total)}")

    @Slot(list, bool)
    def on_done(self, errors, cancelled):
        self.thread.quit()
        self.thread.wait()
        self.dialog.close()
        self.dialog.deleteLater()
        if errors:
            more = f"\n…and {len(errors) - 20} more" if len(errors) > 20 else ""
            QMessageBox.warning(self.window, APP_NAME,
                                "Some items weren't transferred:\n\n" + "\n".join(errors[:20]) + more)
        self.finished.emit(self)


class MainWindow(QMainWindow):
    def __init__(self, config: Config, paths: list[str]):
        super().__init__()
        self.config = config
        self.jobs: list[JobRunner] = []
        self.closed_tabs: list[str] = []
        self.tree_actions: list[QAction] = []
        self.setWindowTitle(APP_NAME)
        self.resize(1320, 800)
        self.setWindowIcon(icon_cache().folder)

        self._make_actions()
        self._make_ui()
        self._make_menus()

        self.inspector_timer = QTimer(self)
        self.inspector_timer.setSingleShot(True)
        self.inspector_timer.setInterval(120)
        self.inspector_timer.timeout.connect(self._update_inspector)

        config.settingsChanged.connect(self._settings_changed)
        config.viewChanged.connect(lambda _src: self._sync_preset_combo())
        config.presetsChanged.connect(self._sync_preset_combo)
        config.bookmarksChanged.connect(self._sync_location)

        if config.get("window_geometry"):
            self.restoreGeometry(QByteArray.fromHex(config.get("window_geometry").encode()))
        if config.get("window_state"):
            self.restoreState(QByteArray.fromHex(config.get("window_state").encode()))

        start = paths or [p for p in config.get("session_tabs") if p == DRIVES or os.path.isdir(p)] or [HOME]
        for p in start:
            self.new_tab(p, activate=False, after_current=False)
        cur = config.get("session_current") if not paths else 0
        self.tabs.setCurrentIndex(min(max(cur, 0), self.tabs.count() - 1))
        self.act_hidden.setChecked(config.get("show_hidden"))
        self._sync_preset_combo()
        self._sync_location()
        self.view().tree.setFocus()

    # ----- construction ----------------------------------------------------------
    def _action(self, text, slot, shortcut=None, *, tree=False, checkable=False, icon=None, tip=None):
        a = QAction(text, self)
        if icon is not None:
            a.setIcon(icon)
        if shortcut:
            keys = shortcut if isinstance(shortcut, list) else [shortcut]
            a.setShortcuts([QKeySequence(k) for k in keys])
        if tip:
            a.setToolTip(tip)
        a.setCheckable(checkable)
        if checkable:
            a.triggered.connect(slot)
        else:
            a.triggered.connect(lambda _=False: slot())
        if tree:
            # Only while the file list itself has focus, so text fields and the rename
            # editor keep their own Ctrl+C, Del, Backspace...
            a.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
            self.tree_actions.append(a)
        else:
            self.addAction(a)
        return a

    def _make_actions(self):
        S, SP = self.style().standardIcon, QStyle.StandardPixmap
        A = self._action
        self.act_back = A("Back", self.go_back, "Alt+Left", icon=S(SP.SP_ArrowBack), tip="Back (Alt+Left)")
        self.act_forward = A("Forward", self.go_forward, "Alt+Right", icon=S(SP.SP_ArrowForward), tip="Forward (Alt+Right)")
        self.act_up = A("Up", self.go_up, "Alt+Up", icon=S(SP.SP_ArrowUp), tip="Up one level (Alt+Up)")
        self.act_home = A("Home", lambda: self.view().navigate(HOME), "Alt+Home")
        self.act_this_pc = A("This PC", lambda: self.view().navigate(DRIVES))
        self.act_refresh = A("Refresh", lambda: self.view().refresh(), "F5", icon=S(SP.SP_BrowserReload), tip="Refresh (F5)")
        self.act_address = A("Edit address", self.focus_address, ["Ctrl+L", "Alt+D", "F4"])
        self.act_filter = A("Filter…", lambda: self.view().focus_filter(), "Ctrl+F")
        self.act_new_tab = A("New tab", lambda: self.new_tab(), "Ctrl+T")
        self.act_close_tab = A("Close tab", lambda: self.close_tab(self.tabs.currentIndex()), "Ctrl+W")
        self.act_reopen_tab = A("Reopen closed tab", self.reopen_tab, "Ctrl+Shift+T")
        self.act_next_tab = A("Next tab", lambda: self.cycle_tab(1), "Ctrl+Tab")
        self.act_prev_tab = A("Previous tab", lambda: self.cycle_tab(-1), "Ctrl+Shift+Tab")
        self.act_new_folder = A("New folder", self.new_folder, "Ctrl+Shift+N")
        self.act_new_file = A("New text file", self.new_text_file)
        self.act_hidden = A("Show hidden files", lambda on: self.config.set("show_hidden", on), "Ctrl+H", checkable=True)
        self.act_folders_first = A("Folders first", lambda on: self.view().set_folders_first(on), checkable=True)
        self.act_bookmark = A("Bookmark this folder", self.toggle_bookmark, "Ctrl+D")
        self.act_settings = A("Settings…", self.open_settings, "Ctrl+,", tip="Settings (Ctrl+,)")
        self.act_terminal = A("Open terminal here", self.open_terminal)
        self.act_explorer = A("Show in Explorer", self.show_in_explorer)
        self.act_quit = A("Exit", self.close, "Ctrl+Q")

        T = lambda *a, **k: self._action(*a, tree=True, **k)  # noqa: E731
        self.act_open = T("Open", lambda: self.view().open_entries(self.view().selected_entries()))
        self.act_open_tab = T("Open in new tab", self.open_selected_in_tabs, "Ctrl+Return")
        self.act_open_with = T("Open with…", self.open_with)
        self.act_cut = T("Cut", lambda: self.clip_selected(True), "Ctrl+X")
        self.act_copy = T("Copy", lambda: self.clip_selected(False), "Ctrl+C")
        self.act_paste = T("Paste", lambda: self.paste(None), "Ctrl+V")
        self.act_paste_tc = T("Paste with TeraCopy", lambda: self.paste("teracopy"), "Ctrl+Shift+V")
        self.act_paste_builtin = T("Paste with built-in copier", lambda: self.paste("builtin"))
        self.act_copy_to = T("Copy to folder…", lambda: self.transfer_to_dialog(False))
        self.act_move_to = T("Move to folder…", lambda: self.transfer_to_dialog(True))
        self.act_copy_path = T("Copy path", self.copy_path, "Ctrl+Shift+C")
        self.act_rename = T("Rename", lambda: self.view().edit_current(), "F2")
        self.act_delete = T("Delete", lambda: self.delete(False), "Del")
        self.act_delete_perm = T("Delete permanently", lambda: self.delete(True), "Shift+Del")
        self.act_select_all = T("Select all", lambda: self.view().tree.selectAll(), "Ctrl+A")
        self.act_invert = T("Invert selection", self.invert_selection, "Ctrl+I")
        self.act_properties = T("Properties", self.properties, "Alt+Return")
        self.act_back_key = T("Back", self.backspace, "Backspace")

    def _make_ui(self):
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setElideMode(Qt.TextElideMode.ElideRight)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(lambda _i: self._sync_location())
        self.tabs.tabBar().installEventFilter(self)
        plus = QToolButton()
        plus.setText("+")
        plus.setAutoRaise(True)
        plus.setToolTip("New tab (Ctrl+T)")
        plus.clicked.connect(lambda: self.new_tab())
        self.tabs.setCornerWidget(plus, Qt.Corner.TopRightCorner)
        self.setCentralWidget(self.tabs)

        tb = QToolBar("Navigation")
        tb.setObjectName("navigation")
        tb.setMovable(False)
        tb.addAction(self.act_back)
        tb.addAction(self.act_forward)
        tb.addAction(self.act_up)
        tb.addAction(self.act_refresh)
        self.address = AddressBar()
        self.address.setPlaceholderText("Type a path, or %USERPROFILE%, or This PC")
        self.address.returnPressed.connect(self._address_entered)
        self.address.escaped.connect(self._address_escaped)
        completer = QCompleter(self)
        fsm = QFileSystemModel(completer)
        fsm.setFilter(QDir.Filter.AllDirs | QDir.Filter.NoDotAndDotDot | QDir.Filter.Drives)
        fsm.setRootPath("")
        completer.setModel(fsm)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.address.setCompleter(completer)
        tb.addWidget(self.address)
        tb.addSeparator()
        tb.addWidget(QLabel(" View: "))
        self.preset_combo = QComboBox()
        self.preset_combo.setPlaceholderText("Custom")
        self.preset_combo.setMinimumContentsLength(14)
        self.preset_combo.setToolTip("View preset. It applies to every folder and tab.")
        self.preset_combo.textActivated.connect(self.config.apply_preset)
        tb.addWidget(self.preset_combo)
        tb.addSeparator()
        self.addToolBar(tb)

        self.sidebar = Sidebar(self.config)
        self.sidebar.openRequested.connect(self._sidebar_open)
        self.sidebar.dropRequested.connect(self.transfer)
        self.side_dock = QDockWidget("Places")
        self.side_dock.setObjectName("sidebar")
        self.side_dock.setWidget(self.sidebar)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.side_dock)

        self.inspector = Inspector()
        self.insp_dock = QDockWidget("Inspector")
        self.insp_dock.setObjectName("inspector")
        self.insp_dock.setWidget(self.inspector)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.insp_dock)
        self.insp_dock.visibilityChanged.connect(lambda vis: vis and self.inspector_timer.start())
        self.resizeDocks([self.side_dock, self.insp_dock], [220, 330], Qt.Orientation.Horizontal)

        self.act_inspector = self.insp_dock.toggleViewAction()
        self.act_inspector.setText("Inspector")
        self.act_inspector.setShortcut(QKeySequence("Alt+P"))
        self.act_inspector.setToolTip("Show or hide the inspector (Alt+P)")
        self.act_inspector.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogInfoView))
        self.addAction(self.act_inspector)
        self.act_sidebar = self.side_dock.toggleViewAction()
        self.act_sidebar.setText("Places sidebar")
        tb.addAction(self.act_inspector)
        tb.addAction(self.act_settings)

        self.status_left = QLabel()
        self.status_right = QLabel()
        self.statusBar().addWidget(self.status_left, 1)
        self.statusBar().addPermanentWidget(self.status_right)

    def _make_menus(self):
        mb = self.menuBar()
        f = mb.addMenu("&File")
        for a in (self.act_new_tab, self.act_reopen_tab, None, self.act_new_folder, self.act_new_file,
                  None, self.act_close_tab, self.act_quit):
            f.addSeparator() if a is None else f.addAction(a)
        e = mb.addMenu("&Edit")
        for a in (self.act_cut, self.act_copy, self.act_paste, self.act_paste_tc, self.act_paste_builtin,
                  None, self.act_copy_to, self.act_move_to, self.act_copy_path, None,
                  self.act_rename, self.act_delete, self.act_delete_perm, None,
                  self.act_select_all, self.act_invert):
            e.addSeparator() if a is None else e.addAction(a)
        v = mb.addMenu("&View")
        v.aboutToShow.connect(lambda: self.act_folders_first.setChecked(self.view().model.folders_first))
        for a in (self.act_inspector, self.act_sidebar, None, self.act_hidden, self.act_folders_first):
            v.addSeparator() if a is None else v.addAction(a)
        self.columns_menu = v.addMenu("Columns")
        self.columns_menu.aboutToShow.connect(self._fill_columns_menu)
        self.presets_menu = v.addMenu("View presets (all folders)")
        self.presets_menu.aboutToShow.connect(self._fill_presets_menu)
        v.addSeparator()
        v.addAction(self.act_filter)
        v.addAction(self.act_refresh)
        g = mb.addMenu("&Go")
        for a in (self.act_back, self.act_forward, self.act_up, self.act_home, self.act_this_pc, None,
                  self.act_address, None, self.act_next_tab, self.act_prev_tab):
            g.addSeparator() if a is None else g.addAction(a)
        self.bookmarks_menu = mb.addMenu("&Bookmarks")
        self.bookmarks_menu.aboutToShow.connect(self._fill_bookmarks_menu)
        t = mb.addMenu("&Tools")
        for a in (self.act_terminal, self.act_explorer, self.act_properties, None, self.act_settings):
            t.addSeparator() if a is None else t.addAction(a)

    def _fill_columns_menu(self):
        from .config import COLUMN_TITLES, COLUMNS
        m = self.columns_menu
        m.clear()
        v = self.view()
        for key in COLUMNS:
            a = m.addAction(COLUMN_TITLES[key])
            a.setCheckable(True)
            a.setChecked(v.is_column_visible(key))
            a.setEnabled(key != "name")
            a.toggled.connect(lambda on, k=key: self.view().set_column_visible(k, on))

    def _fill_presets_menu(self):
        self.presets_menu.clear()
        dialogs.add_preset_actions(self.presets_menu, self.config, self)

    def _fill_bookmarks_menu(self):
        m = self.bookmarks_menu
        m.clear()
        m.addAction(self.act_bookmark)
        m.addSeparator()
        bms = self.config.bookmarks()
        for b in bms:
            a = m.addAction(icon_cache().folder, b["name"])
            a.setToolTip(b["path"])
            a.triggered.connect(lambda _=False, p=b["path"]: self.view().navigate(p))
        if not bms:
            m.addAction("No bookmarks yet").setEnabled(False)

    # ----- tabs -----------------------------------------------------------------------
    def view(self) -> FolderView:
        return self.tabs.currentWidget()

    def views(self) -> list[FolderView]:
        return [self.tabs.widget(i) for i in range(self.tabs.count())]

    def new_tab(self, path=None, activate=True, after_current=True):
        if path is None:
            path = self.config.get("new_tab_path") or HOME
        v = FolderView(self.config, self)
        for a in self.tree_actions:
            v.tree.addAction(a)
        v.pathChanged.connect(lambda _p, v=v: self._on_view_path(v))
        v.selectionChanged.connect(lambda v=v: self._on_view_selection(v))
        v.openInNewTab.connect(lambda p: self.new_tab(p, activate=False))
        v.statusMessage.connect(lambda msg: self.statusBar().showMessage(msg, 6000))
        v.contextMenuRequested.connect(lambda pos, v=v: self._context_menu(v, pos))
        v.tree.dropRequested.connect(self.transfer)
        if not v.navigate(path, record=False):
            v.navigate(HOME, record=False)
        idx = self.tabs.currentIndex() + 1 if after_current and self.tabs.count() else self.tabs.count()
        self.tabs.insertTab(idx, v, title_for(v.path))
        self.tabs.setTabToolTip(idx, display_path(v.path))
        if activate:
            self.tabs.setCurrentIndex(idx)
            v.tree.setFocus()
        return v

    def close_tab(self, index):
        if self.tabs.count() <= 1 or index < 0:
            return
        v = self.tabs.widget(index)
        self.closed_tabs.append(v.path)
        self.tabs.removeTab(index)
        v.deleteLater()

    def reopen_tab(self):
        while self.closed_tabs:
            p = self.closed_tabs.pop()
            if p == DRIVES or os.path.isdir(p):
                self.new_tab(p)
                return

    def cycle_tab(self, delta):
        n = self.tabs.count()
        if n > 1:
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + delta) % n)

    def eventFilter(self, obj, ev):
        if (obj is self.tabs.tabBar() and ev.type() == QEvent.Type.MouseButtonRelease
                and ev.button() == Qt.MouseButton.MiddleButton):
            i = obj.tabAt(ev.position().toPoint())
            if i >= 0:
                self.close_tab(i)
                return True
        return super().eventFilter(obj, ev)

    # ----- keeping the chrome in sync -------------------------------------------------
    def _on_view_path(self, v):
        i = self.tabs.indexOf(v)
        if i >= 0:
            self.tabs.setTabText(i, title_for(v.path))
            self.tabs.setTabToolTip(i, display_path(v.path))
        if v is self.view():
            self._sync_location()

    def _on_view_selection(self, v):
        if v is self.view():
            self._update_status()
            self.inspector_timer.start()

    def _sync_location(self):
        v = self.view()
        if v is None or v.path is None:
            return
        self.address.setText(display_path(v.path))
        self.act_back.setEnabled(bool(v.back_stack))
        self.act_forward.setEnabled(bool(v.fwd_stack))
        self.act_up.setEnabled(v.path != DRIVES)
        marked = v.path != DRIVES and self.config.is_bookmarked(v.path)
        self.act_bookmark.setText("Remove bookmark" if marked else "Bookmark this folder")
        self.act_bookmark.setEnabled(v.path != DRIVES)
        self.sidebar.highlight(v.path)
        self.setWindowTitle(f"{title_for(v.path)} — {APP_NAME}")
        free = ""
        if v.path != DRIVES:
            try:
                du = shutil.disk_usage(v.path)
                free = f"{human_size(du.free)} free of {human_size(du.total)}"
            except OSError:
                pass
        self.status_right.setText(free)
        self._update_status()
        self.inspector_timer.start()

    def _update_status(self):
        v = self.view()
        if v is None:
            return
        shown, total = v.proxy.rowCount(), len(v.model.entries)
        parts = [f"{shown:,} items" if shown == total else f"{shown:,} of {total:,} items (filtered)"]
        sel = v.selected_entries()
        if sel:
            files = [e for e in sel if not e.is_dir]
            s = f"{len(sel):,} selected"
            if files:
                s += f" · {human_size(sum(e.size or 0 for e in files))}"
            parts.append(s)
        self.status_left.setText("     ".join(parts))

    def _update_inspector(self):
        if not self.insp_dock.isVisible():
            return
        v = self.view()
        if v is not None:
            self.inspector.show_entries(v.selected_entries(), v.path)

    def _sync_preset_combo(self):
        c = self.preset_combo
        c.blockSignals(True)
        c.clear()
        names = list(self.config.presets())
        c.addItems(names)
        active = self.config.get("active_preset")
        c.setCurrentIndex(names.index(active) if active in names and self.config.view_matches_active() else -1)
        c.blockSignals(False)

    def _settings_changed(self):
        self.act_hidden.setChecked(self.config.get("show_hidden"))
        for v in self.views():
            v.apply_settings()

    # ----- navigation -------------------------------------------------------------------
    def go_back(self):
        self.view().go_back()

    def go_forward(self):
        self.view().go_forward()

    def go_up(self):
        self.view().go_up()

    def backspace(self):
        v = self.view()
        if v.filter_text():
            v.chop_filter()
        else:
            v.go_back()

    def focus_address(self):
        self.address.setFocus()
        self.address.selectAll()

    def _address_entered(self):
        text = self.address.text().strip()
        v = self.view()
        if text.lower() in ("this pc", "computer", "my computer", ""):
            v.navigate(DRIVES)
        else:
            p = normalize_path(text)
            if os.path.isdir(p):
                v.navigate(p)
            elif os.path.isfile(p):
                v.navigate(os.path.dirname(p), select=p)
            else:
                QMessageBox.warning(self, APP_NAME, f"Can't find “{text}”.\nCheck the spelling and try again.")
                return
        v.tree.setFocus()

    def _address_escaped(self):
        self.address.setText(display_path(self.view().path))
        self.view().tree.setFocus()

    def _sidebar_open(self, path, in_new_tab):
        if in_new_tab:
            self.new_tab(path, activate=False)
        else:
            self.view().navigate(path)
            self.view().tree.setFocus()

    # ----- context menu ------------------------------------------------------------------
    def _context_menu(self, v: FolderView, pos):
        idx = v.tree.indexAt(pos)
        if not idx.isValid():
            v.tree.clearSelection()
        entries = v.selected_entries()
        m = QMenu(self)
        if entries:
            m.addAction(self.act_open)
            if any(e.is_dir for e in entries):
                m.addAction(self.act_open_tab)
            if len(entries) == 1 and not entries[0].is_dir:
                m.addAction(self.act_open_with)
            if not any(e.is_drive for e in entries):
                m.addSeparator()
                for a in (self.act_cut, self.act_copy, self.act_copy_path, None,
                          self.act_copy_to, self.act_move_to, None,
                          self.act_rename, self.act_delete, self.act_delete_perm):
                    m.addSeparator() if a is None else m.addAction(a)
            if len(entries) == 1 and entries[0].is_dir:
                p = entries[0].path
                m.addSeparator()
                if self.config.is_bookmarked(p):
                    m.addAction("Remove bookmark").triggered.connect(lambda: self.config.remove_bookmark(p))
                else:
                    m.addAction("Add bookmark").triggered.connect(lambda: self.config.add_bookmark(p))
            m.addSeparator()
            m.addAction(self.act_explorer)
            m.addAction(self.act_properties)
        else:
            if v.path != DRIVES:
                for a in (self.act_paste, self.act_paste_tc, self.act_paste_builtin, None,
                          self.act_new_folder, self.act_new_file, None):
                    m.addSeparator() if a is None else m.addAction(a)
            m.addAction(self.act_refresh)
            m.addAction(self.act_hidden)
            presets = m.addMenu("View presets (all folders)")
            dialogs.add_preset_actions(presets, self.config, self)
            if v.path != DRIVES:
                m.addSeparator()
                m.addAction(self.act_bookmark)
                m.addAction(self.act_terminal)
                m.addAction(self.act_explorer)
                m.addAction(self.act_properties)
        m.exec(v.tree.viewport().mapToGlobal(pos))

    # ----- file operations ---------------------------------------------------------------
    def _selected(self):
        return [e for e in self.view().selected_entries() if not e.is_drive]

    def open_selected_in_tabs(self):
        for e in self.view().selected_entries():
            if e.is_dir:
                self.new_tab(e.path, activate=False)

    def open_with(self):
        es = [e for e in self._selected() if not e.is_dir]
        if es:
            fileops.open_with_dialog(es[0].path)

    def clip_selected(self, cut):
        es = self._selected()
        if not es:
            return
        fileops.set_clipboard_paths([e.path for e in es], cut)
        self.statusBar().showMessage(f"{'Cut' if cut else 'Copied'} {len(es)} item(s)", 4000)

    def copy_path(self):
        es = self.view().selected_entries()
        paths = [e.path for e in es] or ([self.view().path] if self.view().path != DRIVES else [])
        if paths:
            QGuiApplication.clipboard().setText("\n".join(paths))
            self.statusBar().showMessage("Path copied", 3000)

    def paste(self, engine):
        v = self.view()
        if v.path == DRIVES:
            return
        paths, cut = fileops.clipboard_paths()
        if not paths:
            self.statusBar().showMessage("There are no files on the clipboard", 4000)
            return
        if self.transfer(paths, v.path, cut, engine) and cut:
            QGuiApplication.clipboard().clear()

    def transfer_to_dialog(self, move):
        es = self._selected()
        if not es:
            return
        dest = QFileDialog.getExistingDirectory(self, "Move to folder" if move else "Copy to folder",
                                                self.view().path if self.view().path != DRIVES else HOME)
        if dest:
            self.transfer([e.path for e in es], os.path.normpath(dest), move)

    def transfer(self, sources, target, move, engine=None) -> bool:
        """Copy or move sources into target, through TeraCopy or the built-in copier."""
        if not target or target == DRIVES:
            return False
        target = os.path.normpath(target)
        if not os.path.isdir(target):
            QMessageBox.warning(self, APP_NAME, f"The destination folder doesn't exist:\n{target}")
            return False
        srcs, problems = [], []
        for s in sources:
            s = os.path.normpath(s)
            if not os.path.lexists(s):
                problems.append(f"Not found: {s}")
            elif not os.path.basename(s):
                problems.append(f"Can't copy a whole drive: {s}")
            elif os.path.isdir(s) and fileops.is_inside(target, s):
                problems.append(f"Can't put a folder inside itself: {s}")
            elif move and fileops.same_path(os.path.dirname(s), target):
                continue                                  # already there
            else:
                srcs.append(s)
        if problems:
            QMessageBox.warning(self, APP_NAME, "\n".join(problems[:15]))
        if not srcs:
            return False

        same_dir_copy = any(fileops.same_path(os.path.dirname(s), target) for s in srcs)
        use_tc = engine == "teracopy" or (engine is None and self.config.get("use_teracopy"))
        if use_tc and not same_dir_copy:
            exe = fileops.find_teracopy(self.config.get("teracopy_path"))
            if exe:
                try:
                    fileops.teracopy_transfer(exe, srcs, target, move, self.config.get("teracopy_close"))
                except OSError as ex:
                    QMessageBox.warning(self, APP_NAME, f"Couldn't start TeraCopy: {ex}")
                    return False
                self.statusBar().showMessage(
                    f"Sent {len(srcs)} item(s) to TeraCopy ({'move' if move else 'copy'} to {target})", 6000)
                return True
            if engine == "teracopy":
                QMessageBox.warning(self, APP_NAME, "TeraCopy wasn't found. Set its location in Settings (Ctrl+,).")
                return False

        plan = self._build_plan(srcs, target, move)
        if not plan:
            return False
        runner = JobRunner(self, plan, move)
        runner.finished.connect(self._job_finished)
        self.jobs.append(runner)
        runner.start()
        return True

    def _build_plan(self, srcs, target, move):
        plan, conflicts, problems = [], [], []
        for s in srcs:
            name = os.path.basename(s)
            is_dir = os.path.isdir(s)
            dst = os.path.join(target, name)
            if fileops.same_path(dst, s):                 # copy into its own folder → "- Copy"
                plan.append([s, fileops.free_name(target, name, is_dir, copy_style=True), "new"])
            elif os.path.lexists(dst):
                if fileops.is_inside(s, dst):
                    problems.append(f"Can't replace “{name}” with an item that is inside it.")
                    continue
                conflicts.append(len(plan))
                plan.append([s, dst, "conflict"])
            else:
                plan.append([s, dst, "new"])
        if problems:
            QMessageBox.warning(self, APP_NAME, "\n".join(problems))
        if conflicts:
            names = [os.path.basename(plan[i][0]) for i in conflicts]
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Question)
            box.setWindowTitle("Replace or skip")
            box.setText(f"{len(conflicts)} item(s) with the same name already exist in “{title_for(target)}”.")
            box.setInformativeText("\n".join(names[:8]) + ("\n…" if len(names) > 8 else ""))
            b_replace = box.addButton("Replace", QMessageBox.ButtonRole.AcceptRole)
            b_skip = box.addButton("Skip these", QMessageBox.ButtonRole.AcceptRole)
            b_both = box.addButton("Keep both", QMessageBox.ButtonRole.AcceptRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.setDefaultButton(b_both)
            box.exec()
            clicked = box.clickedButton()
            if clicked is b_replace:
                for i in conflicts:
                    plan[i][2] = "overwrite"
            elif clicked is b_skip:
                plan = [p for p in plan if p[2] != "conflict"]
            elif clicked is b_both:
                for i in conflicts:
                    s = plan[i][0]
                    plan[i][1] = fileops.free_name(target, os.path.basename(s), os.path.isdir(s), force_new=True)
                    plan[i][2] = "new"
            else:
                return None
        return plan

    def _job_finished(self, runner):
        if runner in self.jobs:
            self.jobs.remove(runner)
        runner.deleteLater()
        self.view().refresh()

    def delete(self, permanent):
        es = self._selected()
        if not es:
            return
        desc = f"“{es[0].name}”" if len(es) == 1 else f"these {len(es)} items"
        if permanent:
            if QMessageBox.warning(self, "Delete permanently", f"Permanently delete {desc}?\n\nThis can't be undone.",
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                   QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
        elif self.config.get("confirm_delete"):
            if QMessageBox.question(self, "Delete", f"Move {desc} to the Recycle Bin?",
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.Yes) != QMessageBox.StandardButton.Yes:
                return
        v = self.view()
        row = v.tree.currentIndex().row()
        ok, aborted = fileops.delete_paths([e.path for e in es], int(self.winId()), permanent)
        v.refresh()
        n = v.proxy.rowCount()
        if n and row >= 0:
            v.tree.setCurrentIndex(v.proxy.index(min(row, n - 1), 0))
        if not ok and not aborted:
            self.statusBar().showMessage("Some items couldn't be deleted", 6000)

    def _create(self, name, is_dir):
        v = self.view()
        if v.path == DRIVES:
            return
        p = fileops.free_name(v.path, name, is_dir)
        try:
            if is_dir:
                os.mkdir(p)
            else:
                open(p, "x").close()
        except OSError as ex:
            QMessageBox.warning(self, APP_NAME, f"Couldn't create “{os.path.basename(p)}”: {ex.strerror or ex}")
            return
        v.clear_filter()
        v.refresh()
        v.select_paths([p], p)
        v.tree.setFocus()
        v.edit_current()

    def new_folder(self):
        self._create("New folder", True)

    def new_text_file(self):
        self._create("New Text Document.txt", False)

    def invert_selection(self):
        v = self.view()
        m = v.proxy
        if not m.rowCount():
            return
        sel = QItemSelection(m.index(0, 0), m.index(m.rowCount() - 1, m.columnCount() - 1))
        v.tree.selectionModel().select(sel, QItemSelectionModel.SelectionFlag.Toggle
                                       | QItemSelectionModel.SelectionFlag.Rows)

    def properties(self):
        v = self.view()
        es = v.selected_entries()
        path = es[0].path if es else (v.path if v.path != DRIVES else None)
        if path:
            fileops.show_properties(path, int(self.winId()))

    def toggle_bookmark(self):
        p = self.view().path
        if p == DRIVES:
            return
        if self.config.is_bookmarked(p):
            self.config.remove_bookmark(p)
            self.statusBar().showMessage("Bookmark removed", 3000)
        else:
            self.config.add_bookmark(p)
            self.statusBar().showMessage("Bookmarked", 3000)

    def open_terminal(self):
        p = self.view().path
        fileops.open_terminal(HOME if p == DRIVES else p)

    def show_in_explorer(self):
        v = self.view()
        es = v.selected_entries()
        if es:
            fileops.show_in_explorer(es[0].path, select=True)
        else:
            fileops.show_in_explorer("shell:MyComputerFolder" if v.path == DRIVES else v.path, select=False)

    def open_settings(self):
        dialogs.SettingsDialog(self.config, self).exec()

    # ----- shutdown --------------------------------------------------------------------------
    def closeEvent(self, ev):
        if self.jobs:
            if QMessageBox.question(self, APP_NAME, "A copy is still running. Cancel it and quit?") \
                    != QMessageBox.StandardButton.Yes:
                ev.ignore()
                return
            for j in list(self.jobs):
                j.cancel_and_wait()
        self.config.data["session_tabs"] = [v.path for v in self.views()]
        self.config.data["session_current"] = self.tabs.currentIndex()
        self.config.data["window_geometry"] = bytes(self.saveGeometry().toHex().data()).decode()
        self.config.data["window_state"] = bytes(self.saveState().toHex().data()).decode()
        self.config.save()
        super().closeEvent(ev)


def main():
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Burrow.FileManager")
        except (AttributeError, OSError):
            pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    config = Config()
    fileops.cleanup_temp()
    paths = [normalize_path(a) for a in sys.argv[1:] if os.path.isdir(a)]
    win = MainWindow(config, paths)
    win.show()
    return app.exec()
