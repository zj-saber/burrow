"""Main window: tabs, toolbar, sidebar, inspector, menus and file operations."""
from __future__ import annotations

import ctypes
import os
import shutil
import sys

from PySide6.QtCore import QByteArray, QDir, QEvent, QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (QApplication, QComboBox, QCompleter, QDockWidget, QFileDialog,
                               QFileSystemModel, QHBoxLayout, QLabel, QMainWindow, QMenu,
                               QMessageBox, QProgressDialog, QSizePolicy, QStyle, QToolButton,
                               QAbstractItemView,
                               QVBoxLayout, QWidget)
from PySide6.QtCore import QItemSelection, QItemSelectionModel

from . import dialogs, fileops, foldercolor, integration, tags, theme
from .chrome import AddressArea, ProgressPill, UsagePill
from .toolbar import CommandBar
from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QSplitter
from .config import APP_NAME, Config
from .folderview import FolderView, display_path, normalize_path, title_for
from .inspector import Inspector
from . import places
from .model import DRIVES, NETWORK, human_size, icon_cache, list_drives
from .sidebar import Sidebar

HOME = os.path.expanduser("~")
MAX_PANES = 6                       # snapped panes per window


def is_location(p: str) -> bool:
    """This PC, Network or a bare \\\\server: browsable, but not a folder you can put files in."""
    return places.is_special(p) or places.is_server(p)


def restorable(p: str) -> bool:
    """Whether a saved tab can be reopened. Never waits on the network: remote tabs are
    reopened and checked by their background load."""
    return places.is_special(p) or places.is_remote(p) or os.path.isdir(p)


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
    def report(self, done, total, _name):
        """The status-bar pill (see MainWindow.task_update)."""
        self.window.task_update(getattr(self, "task", -1), done, total)

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




TAB_MIME_APP = "application/x-burrow-tab"
WINDOWS: list = []          # every open MainWindow; WINDOWS[0] is the primary (tray, Win+E, single instance)
LAST_ACTIVE = []            # most recently activated window


class Pane(QWidget):
    """One side of a split: its own tab strip and folder views."""

    def __init__(self, win):
        super().__init__()
        from .chrome import TabHost
        self.win = win
        self.tabs = TabHost(self)
        self.tabs.pane = self
        self.strip = QWidget()
        self.strip.setObjectName("tabstrip")
        self.strip.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.strip.installEventFilter(win)
        ts = QHBoxLayout(self.strip)
        ts.setContentsMargins(8, 6, 8, 0)
        ts.setSpacing(2)
        bar = self.tabs.bar
        bar.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        bar.installEventFilter(win)
        bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        bar.customContextMenuRequested.connect(lambda pos: win._tab_menu(self, pos))
        bar.tabBarClicked.connect(lambda i: win._tab_clicked(self, i))
        ts.addWidget(bar, 0, Qt.AlignmentFlag.AlignBottom)
        self.plus_btn = QToolButton()
        self.plus_btn.setObjectName("plus")
        self.plus_btn.setToolTip("New tab (Ctrl+T)")
        self.plus_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.plus_btn.clicked.connect(lambda: (win.set_active_pane(self), win.new_tab()))
        ts.addWidget(self.plus_btn, 0, Qt.AlignmentFlag.AlignBottom)
        ts.addStretch(1)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(0)
        self.lay.addWidget(self.strip)
        self.lay.addWidget(self.tabs.stack, 1)
        self.tabs.tabCloseRequested.connect(lambda i: win.close_tab(i, self))
        self.tabs.currentChanged.connect(lambda _i: win._pane_changed(self))

    def set_active(self, on: bool):
        if self.strip.property("active") != on:
            self.strip.setProperty("active", on)
            self.strip.setStyleSheet(self.strip.styleSheet())     # re-evaluate [active="true"]


class DropArea(QWidget):
    """Holds the panes; a tab dragged out of a tab bar can be dropped on an edge to split
    (left/right/top/bottom) or on the other pane to move it there."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setAcceptDrops(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.overlay = QWidget(self)
        self.overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.overlay.hide()
        self._zone = None

    def style_overlay(self, accent: str):
        c = QColor(accent)
        self.overlay.setStyleSheet(f"background: rgba({c.red()},{c.green()},{c.blue()},60);"
                                   f" border: 2px solid {accent}; border-radius: 8px;")

    def _zone_at(self, pos):
        """Which drop zone (a pane's edge to split, or its middle to move) is under the dragged tab."""
        from .chrome import SnapTabBar
        src = SnapTabBar.dragging
        if src is None:
            return None
        for p in self.win.panes:
            tl = p.mapTo(self, QPoint(0, 0))
            r = QRect(tl, p.size())
            if not r.contains(pos):
                continue
            fx = (pos.x() - r.left()) / max(1, r.width())
            fy = (pos.y() - r.top()) / max(1, r.height())
            w2, h2 = r.width() // 2, r.height() // 2
            room = len(self.win.panes) < MAX_PANES
            if room and fx < 0.25:
                return ("left", p, QRect(r.left(), r.top(), w2, r.height()))
            if room and fx > 0.75:
                return ("right", p, QRect(r.left() + w2, r.top(), r.width() - w2, r.height()))
            if room and fy < 0.25:
                return ("top", p, QRect(r.left(), r.top(), r.width(), h2))
            if room and fy > 0.75:
                return ("bottom", p, QRect(r.left(), r.top() + h2, r.width(), r.height() - h2))
            if len(self.win.panes) > 1 and p.tabs is not src[0]:
                return ("move", p, r)
            return None
        return None

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasFormat(TAB_MIME_APP):
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dragMoveEvent(self, ev):
        """Show the highlight for the zone under the dragged tab."""
        if not ev.mimeData().hasFormat(TAB_MIME_APP):
            ev.ignore()
            return
        self._zone = self._zone_at(ev.position().toPoint())
        if self._zone:
            self.overlay.setGeometry(self._zone[2].adjusted(4, 4, -4, -4))
            self.overlay.show()
            self.overlay.raise_()
            ev.acceptProposedAction()
        else:
            self.overlay.hide()
            ev.accept()

    def dragLeaveEvent(self, ev):
        self.overlay.hide()

    def dropEvent(self, ev):
        from .chrome import SnapTabBar
        self.overlay.hide()
        zone, src = self._zone, SnapTabBar.dragging
        self._zone = None
        if not zone or not src:
            ev.ignore()
            return
        ev.acceptProposedAction()
        host, index = src
        QTimer.singleShot(0, lambda: self.win.snap_tab(host.pane, index, zone[0], zone[1]))


def _drives_changed():
    """A drive, card, phone or network drive came or went: update sidebars and This PC pages."""
    for w in WINDOWS:
        w.sidebar._check_drives()
        for v in w.views():
            if v.path == DRIVES and v.isVisible():
                v.refresh()


_trim_timer = None


def schedule_trim(delay_ms: int = 3000):
    """Burrow went to the tray: once it has settled, give memory back to Windows."""
    global _trim_timer
    if _trim_timer is None:
        _trim_timer = QTimer()
        _trim_timer.setSingleShot(True)
        _trim_timer.timeout.connect(trim_memory)
    _trim_timer.start(delay_ms)


def trim_memory():
    """Burrow is hidden in the tray: drop previews, caches and decoders, then return the memory to Windows."""
    if any(w.isVisible() for w in WINDOWS):
        return                                   # came back meanwhile
    import gc
    from PySide6.QtGui import QPixmapCache
    for w in WINDOWS:
        insp = w.inspector
        insp._reset()                            # drop the previewed photo/PDF/book/archive
        insp.media._release()                    # video player, decoder buffers, video surface
        w._trimmed = True
    icon_cache().trim()
    QPixmapCache.clear()
    gc.collect()
    if sys.platform == "win32":                  # hand the now-unused pages back to Windows
        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        k32.SetProcessWorkingSetSize.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t]
        k32.SetProcessWorkingSetSize(k32.GetCurrentProcess(), ctypes.c_size_t(-1).value, ctypes.c_size_t(-1).value)


def _repaint_lists():
    """Clipboard tints changed: repaint the visible lists (cheap: only what's on screen)."""
    for w in WINDOWS:
        for p in w.panes:
            v = p.tabs.currentWidget()
            if v is not None:
                for iv in v.item_views():
                    iv.viewport().update()
                if v.columns is not None:
                    for c in v.columns.findChildren(QAbstractItemView):
                        c.viewport().update()


def _startup_chores(win):
    """A while after start: tidy up after an update and look for a new release."""
    if getattr(sys, "frozen", False):
        from . import updater
        updater.cleanup()
        updater.sync_registered_version()
    if win.config.get("check_updates"):          # Auto update: every start looks for a new version
        import time
        from . import updater
        _last_attempt[0] = time.time()
        updater.auto_check(win)


UPDATE_EVERY = 12 * 3600          # automatic checks at most this often
_last_attempt = [0.0]


def maybe_check_updates(win):
    """At start and whenever a window comes up (Burrow may live in the tray for weeks): look for
    a new release if the last check is old. Attempts are spaced too, so being offline doesn't
    mean a request on every activation."""
    import time
    cfg = win.config
    now = time.time()
    if not cfg.get("check_updates") or now - _last_attempt[0] < 3600 \
            or now - float(cfg.get("update_checked") or 0) < UPDATE_EVERY:
        return
    _last_attempt[0] = now
    from . import updater
    updater.auto_check(win)


def open_new_window(config, paths=None):
    """Ctrl+N / Win+E: another Burrow window (same settings, its own tabs)."""
    w = MainWindow(config, paths or [config.get("new_tab_path") or HOME], primary=False)
    if WINDOWS:
        g = WINDOWS[-1].geometry() if WINDOWS[-1].isVisible() else WINDOWS[0].geometry()
        w.setGeometry(g.translated(32, 32))
    w.show()
    w.bring_to_front()
    return w


def route_external(args):
    """A launch handed over by Windows or a second Burrow: open it in the most recent window."""
    target = next((w for w in reversed(LAST_ACTIVE) if w in WINDOWS and w.isVisible()), None) or WINDOWS[0]
    target.open_external(args)


class MainWindow(QMainWindow):
    """One Burrow window: panes of tabs, sidebar, inspector, status bar, menus and file commands."""
    def __init__(self, config: Config, paths: list[str], primary: bool = True):
        super().__init__()
        self.config = config
        places.RECENT_SERVERS[:] = [s for s in config.get("recent_servers") if places.is_server(s)][:8]
        self.primary = primary
        self.panes: list = []
        self.active_pane = None
        self.jobs: list[JobRunner] = []
        self.closed_tabs: list[str] = []
        self.tree_actions: list[QAction] = []
        self.setWindowTitle(APP_NAME)
        self.resize(1320, 800)
        if QApplication.windowIcon().isNull():
            self.setWindowIcon(icon_cache().folder)

        self._backdrop_key = (config.get("backdrop"), config.get("backdrop_tint"))
        if self._translucent():                  # must be set before the window first shows
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._make_actions()
        self._make_ui()
        self._make_menus()
        self.apply_theme()
        from . import marks
        marks.install(_repaint_lists)
        self.apply_ui_switches()
        self._listing_key = (config.get("show_hidden"), config.get("date_format"), config.get("everything_sizes"))
        try:
            QGuiApplication.styleHints().colorSchemeChanged.connect(self._system_scheme_changed)
        except AttributeError:
            pass

        self.inspector_timer = QTimer(self)
        self.inspector_timer.setSingleShot(True)
        self.inspector_timer.setInterval(120)
        self.inspector_timer.timeout.connect(self._update_inspector)
        self._feature_key = tuple(config.get(k) for k in self.FEATURE_KEYS)
        self.apply_feature_switches()
        self._bg_key = (config.get("win_e"), config.get("run_in_background"))
        self.apply_background_switches()

        config.settingsChanged.connect(self._settings_changed)
        config.viewChanged.connect(lambda _src: self._sync_preset_combo())
        config.presetsChanged.connect(self._sync_preset_combo)
        config.bookmarksChanged.connect(self._sync_location)
        tags.store().changed.connect(self._tags_changed)
        config.bookmarkAdded.connect(lambda p: self._mirror_bookmark(p, True))
        config.bookmarkRemoved.connect(lambda p: self._mirror_bookmark(p, False))

        if config.get("window_geometry"):
            self.restoreGeometry(QByteArray.fromHex(config.get("window_geometry").encode()))
        if config.get("window_state"):
            self.restoreState(QByteArray.fromHex(config.get("window_state").encode()))

        layout = self._saved_layout() if (primary and not paths) else None
        if not (layout and self._restore_layout(layout)):
            start = paths or [p for p in config.get("session_tabs") if restorable(p)] or [HOME]
            for p in start:
                self.new_tab(p, activate=False, after_current=False)
            cur = config.get("session_current") if not paths else 0
            self.tabs.setCurrentIndex(min(max(cur, 0), self.tabs.count() - 1))
        WINDOWS.append(self)
        self.act_hidden.setChecked(config.get("show_hidden"))
        self._sync_preset_combo()
        self._sync_location()
        self.view().focus_list()

    # ----- construction ----------------------------------------------------------
    def _action(self, text, slot, shortcut=None, *, tree=False, checkable=False, icon=None, tip=None):
        """Make a window action (menu item / shortcut); tree=True also puts it on every file list."""
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
        """Create every command with its shortcut (see SHORTCUTS for the Explorer / classic keys)."""
        S, SP = self.style().standardIcon, QStyle.StandardPixmap
        A = self._action
        self.act_back = A("Back", self.go_back, "Alt+Left", icon=S(SP.SP_ArrowBack), tip="Back (Alt+Left)")
        self.act_forward = A("Forward", self.go_forward, "Alt+Right", icon=S(SP.SP_ArrowForward), tip="Forward (Alt+Right)")
        self.act_up = A("Up", self.go_up, "Alt+Up", icon=S(SP.SP_ArrowUp), tip="Up one level (Alt+Up)")
        self.act_home = A("Home", lambda: self.view().navigate(HOME), "Alt+Home")
        self.act_this_pc = A("This PC", lambda: self.view().navigate(DRIVES))
        self.act_network = A("Network", lambda: self.view().navigate(NETWORK))
        self.act_map_drive = A("Map network drive…", self.map_drive)
        self.act_unmap_drive = A("Disconnect network drive…", self.disconnect_drive)
        self.act_default_fm = A("Open folders in Burrow", self.toggle_default_fm, checkable=True)
        self.act_win_e = A("Open Burrow with Win+E", self.toggle_win_e, checkable=True)
        self.act_default_fm.setToolTip("When on, folders opened anywhere in Windows and Win+E open in Burrow. Off by default.")
        self.act_refresh = A("Refresh", self.refresh_view, "F5", icon=S(SP.SP_BrowserReload), tip="Refresh (F5)")
        self.act_address = A("Edit address", self.focus_address, ["Ctrl+L", "Alt+D", "F4"])
        self.act_filter = A("Filter…", lambda: self.view().focus_filter(), "Ctrl+F")
        self.act_new_tab = A("New tab", lambda: self.new_tab(), "Ctrl+T")
        self.act_new_window = A("New window", lambda: open_new_window(self.config), "Ctrl+N")
        self.act_split_h = A("Split side by side", lambda: self.split(Qt.Orientation.Horizontal), "Ctrl+\\")
        self.act_split_v = A("Split stacked", lambda: self.split(Qt.Orientation.Vertical), "Ctrl+Shift+\\")
        self.act_unsplit = A("Single pane", self.unsplit)
        self.act_move_pane = A("Move tab to next pane", self.move_tab_to_other_pane, "Ctrl+Alt+M")
        self.act_other_pane = A("Next pane", self.focus_other_pane, "F6")
        self.act_rotate_split = A("Rotate split", self.rotate_split, "Ctrl+Alt+\\")
        self.act_close_tab = A("Close tab", lambda: self.close_tab(self.tabs.currentIndex()), "Ctrl+W")
        self.act_reopen_tab = A("Reopen closed tab", self.reopen_tab, "Ctrl+Shift+T")
        self.act_next_tab = A("Next tab", lambda: self.cycle_tab(1), "Ctrl+Tab")
        self.act_prev_tab = A("Previous tab", lambda: self.cycle_tab(-1), "Ctrl+Shift+Tab")
        self.act_new_folder = A("New folder", self.new_folder, "Ctrl+Shift+N")
        self.act_new_file = A("New text file", self.new_text_file)
        self.act_hidden = A("Show hidden files", lambda on: self.config.set("show_hidden", on), "Ctrl+H", checkable=True)
        self.act_columns_view = A("Columns view", lambda on: self.set_view_mode("columns" if on else "list"),
                                  "Ctrl+Shift+L", checkable=True)
        self.act_folders_first = A("Folders first", lambda on: (self.view().set_folders_first(on), self._sync_sort_mode()), checkable=True)
        self.act_bookmark = A("Bookmark this folder", self.toggle_bookmark, "Ctrl+D")
        self.act_settings = A("Settings…", self.open_settings, "Ctrl+,", tip="Settings (Ctrl+,)")
        self.act_terminal = A("Open terminal here", self.open_terminal)
        self.act_explorer = A("Show in Explorer", self.show_in_explorer)
        self.act_quit = A("Exit", lambda: self.quit_for_real(), "Ctrl+Q")
        self.act_updates = A("Check for updates…", self.check_updates)
        self.act_compare = A("Compare && sync folders…", self.open_compare)
        self.act_dupes = A("Find duplicates…", self.open_duplicates)
        self.act_bulk_rename = A("Rename selected…", self.open_bulk_rename, "Ctrl+Shift+F2")
        self.act_folderize = A("Folders for loose files…", self.folderize, "Ctrl+Alt+F")

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
        self.act_rename = T("Rename", self.rename_selected, "F2")
        self.act_extract = T("Extract", self.extract_selected, "Ctrl+Shift+X")
        self.act_delete = T("Delete", lambda: self.delete(False), "Del")
        self.act_delete_perm = T("Delete permanently", lambda: self.delete(True), "Shift+Del")
        self.act_select_all = T("Select all", lambda: self.view().select_all(), "Ctrl+A")
        self.act_invert = T("Invert selection", self.invert_selection, "Ctrl+I")
        self.act_properties = T("Properties", self.properties, "Alt+Return")
        self.act_back_key = T("Back", self.backspace, "Backspace")
        self.act_open_window = T("Open in new window", self.open_selected_in_window)
        self.act_fullscreen = A("Full screen", self.toggle_fullscreen, "F11")
        self.act_details_pane = A("Show details in the inspector", lambda: self.inspector.set_details_visible(
            not self.inspector.details.isVisible(), user=True), "Alt+Shift+P")
        self.act_focus_sidebar = A("Go to the sidebar", self.focus_sidebar, "Ctrl+Shift+E")
        self.act_cycle_focus = A("Next area", lambda: self.cycle_focus(1))
        self.act_cycle_focus_back = A("Previous area", lambda: self.cycle_focus(-1))
        self.act_list_view = A("List view", lambda: self.set_view_mode("list"), "Ctrl+Shift+6")
        self.act_thumbs_view = A("Thumbnails", lambda on: self.set_view_mode("thumbs" if on else "list"),
                                 "Ctrl+Shift+7", checkable=True)
        self.act_river_view = A("River (photos)", lambda on: self.set_view_mode("river" if on else "list"),
                                "Ctrl+Shift+8", checkable=True)
        for n, px in ((1, 64), (2, 48), (3, 32), (4, 16)):     # Explorer: extra large ... small icons
            A(f"Icons {px} px", lambda px=px: self.icon_slider.setValue(px), f"Ctrl+Shift+{n}")
        self.apply_shortcuts()

    # Burrow 0.11 keys that differ from Windows Explorer
    SHORTCUTS = {
        "classic": {"act_refresh": ["F5"], "act_filter": ["Ctrl+F"], "act_bookmark": ["Ctrl+D"],
                    "act_delete": ["Del"], "act_other_pane": ["F6"], "act_cycle_focus": [],
                    "act_cycle_focus_back": [], "act_open_tab": ["Ctrl+Return"], "act_open_window": [],
                    "act_close_tab": ["Ctrl+W"]},
        "explorer": {"act_refresh": ["F5", "Ctrl+R"], "act_filter": ["Ctrl+F", "Ctrl+E", "F3"],
                     "act_bookmark": ["Ctrl+B"], "act_delete": ["Del", "Ctrl+D"], "act_other_pane": ["Ctrl+F6"],
                     "act_cycle_focus": ["F6"], "act_cycle_focus_back": ["Shift+F6"],
                     "act_open_tab": ["Ctrl+Shift+Return"], "act_open_window": ["Ctrl+Return"],
                     "act_close_tab": ["Ctrl+W", "Ctrl+F4"]},
    }

    def apply_shortcuts(self):
        scheme = self.SHORTCUTS.get(self.config.get("shortcut_scheme"), self.SHORTCUTS["explorer"])
        for name, keys in scheme.items():
            getattr(self, name).setShortcuts([QKeySequence(k) for k in keys])
        self.act_refresh.setToolTip("Refresh (F5)")

    def _tool_button(self, action, parent):
        b = QToolButton(parent)
        b.setDefaultAction(action)
        b.setAutoRaise(True)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return b

    def _make_ui(self):
        # Panes (each with its own tab strip) live in a splitter inside the drop area. With one
        # pane the navigation bar sits between its tabs and files; when split it moves on top.
        """Build the window: pane area, navigation bar, sidebar, inspector and status bar."""
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(5)
        self.drop_area = DropArea(self)
        self.drop_area.layout().addWidget(self.splitter)

        self.navbar = QWidget()
        self.navbar.setObjectName("navbar")
        self.navbar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        rows = QVBoxLayout(self.navbar)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(0)
        addr_row = QWidget(self.navbar)
        rows.addWidget(addr_row)
        nb = QHBoxLayout(addr_row)
        nb.setContentsMargins(8, 6, 8, 6)
        nb.setSpacing(2)
        for a in (self.act_back, self.act_forward, self.act_up, self.act_refresh):
            nb.addWidget(self._tool_button(a, self.navbar))
        nb.addSpacing(6)
        self.address_area = AddressArea()
        self.address = self.address_area.edit
        self.address.returnPressed.connect(self._address_entered)
        self.address.escaped.connect(self._address_escaped)
        self.address_area.crumbs.navigate.connect(self._crumb_navigate)
        completer = QCompleter(self)
        fsm = QFileSystemModel(completer)
        fsm.setFilter(QDir.Filter.AllDirs | QDir.Filter.NoDotAndDotDot | QDir.Filter.Drives)
        fsm.setRootPath("")
        completer.setModel(fsm)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.address.setCompleter(completer)
        nb.addWidget(self.address_area, 1)
        self.bar_toggle = QToolButton(self.navbar)       # hides / shows the command bar
        self.bar_toggle.setAutoRaise(True)
        self.bar_toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.bar_toggle.clicked.connect(lambda: self.set_command_bar(not self.command_bar.isVisible()))
        nb.addWidget(self.bar_toggle)
        nb.addSpacing(8)
        view_label = QLabel("View")
        view_label.setObjectName("viewlabel")
        nb.addWidget(view_label)
        self.preset_combo = QComboBox()
        self.preset_combo.setPlaceholderText("Custom")
        self.preset_combo.setMinimumContentsLength(12)
        self.preset_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.preset_combo.setToolTip("View preset. It applies to every folder and tab.")
        self.preset_combo.textActivated.connect(self.config.apply_preset)
        nb.addWidget(self.preset_combo)
        nb.addSpacing(6)

        central = QWidget()
        self.central_layout = QVBoxLayout(central)
        self.central_layout.setContentsMargins(0, 0, 0, 0)
        self.central_layout.setSpacing(0)
        self.central_layout.addWidget(self.drop_area, 1)
        self.setCentralWidget(central)
        self._add_pane()
        self._place_navbar()
        QApplication.instance().focusChanged.connect(self._focus_changed)

        self.sidebar = Sidebar(self.config)
        self.sidebar.openRequested.connect(self._sidebar_open)
        self.sidebar.browseRequested.connect(lambda p: self.view().navigate(p))
        self.sidebar.focusListRequested.connect(lambda: self.view().focus_list())
        self.sidebar.dropRequested.connect(self.transfer)
        self.sidebar.mapDriveRequested.connect(self.map_drive)
        self.sidebar.disconnectRequested.connect(self.disconnect_drive)
        self.sidebar.ejectRequested.connect(self.eject_drive)
        self.side_dock = QDockWidget("Places")
        self.side_dock.setObjectName("sidebar")
        self.side_dock.setWidget(self.sidebar)
        self.side_dock.setTitleBarWidget(QWidget())
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.side_dock)

        self.inspector = Inspector()
        self.inspector.set_details_visible(bool(self.config.get("inspector_details")))
        self.inspector.detailsToggled.connect(lambda on: self.config.set("inspector_details", on))
        self.inspector.openRequested.connect(self._open_from_inspector)
        self.inspector.split_ratio = float(self.config.get("inspector_split") or 0.6)
        self.inspector.splitChanged.connect(lambda r: self.config.set("inspector_split", round(r, 3)))
        self.insp_dock = QDockWidget("Inspector")
        self.insp_dock.setObjectName("inspector")
        self.insp_dock.setWidget(self.inspector)
        self.insp_dock.setTitleBarWidget(QWidget())
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.insp_dock)
        self.insp_dock.visibilityChanged.connect(self._inspector_visibility)
        self._docks_sized = bool(self.config.get("window_state"))

        self.act_inspector = self.insp_dock.toggleViewAction()
        self.act_inspector.setText("Inspector")
        self.act_inspector.setShortcut(QKeySequence("Alt+P"))
        self.act_inspector.setToolTip("Inspector (Alt+P)")
        self.addAction(self.act_inspector)
        self.act_sidebar = self.side_dock.toggleViewAction()
        self.act_sidebar.setText("Places sidebar")
        self.act_sidebar.setShortcut(QKeySequence("Alt+B"))
        self.act_sidebar.setToolTip("Sidebar (Alt+B)")
        self.addAction(self.act_sidebar)
        self.act_menubar = self._action("Menu bar", self._toggle_menubar, checkable=True)
        self.act_command_bar = self._action("Toolbar", self.set_command_bar, checkable=True)
        self.act_command_bar.setChecked(bool(self.config.get("command_bar")))
        self.command_bar = CommandBar(self)
        self.command_bar.setVisible(self.act_command_bar.isChecked())
        self.navbar.layout().insertWidget(0, self.command_bar)
        nb.addWidget(self._tool_button(self.act_sidebar, self.navbar))
        nb.addWidget(self._tool_button(self.act_inspector, self.navbar))
        self.menu_btn = QToolButton(self.navbar)
        self.menu_btn.setAutoRaise(True)
        self.menu_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.menu_btn.setToolTip("Menu")
        self.menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        nb.addWidget(self.menu_btn)

        self.status_left = QLabel()
        self.status_right = QLabel()
        self.statusBar().setSizeGripEnabled(False)
        # Sorting toggle (FilePilot-style): folders on top, or everything mixed by the column
        self.sort_mode_btn = QToolButton()
        self.sort_mode_btn.setObjectName("statusToggle")
        self.sort_mode_btn.setCheckable(True)
        self.sort_mode_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.sort_mode_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sort_mode_btn.toggled.connect(self._sort_mode_toggled)
        self.config.viewChanged.connect(self._view_preset_changed)
        self.view_mode_btn = QToolButton()
        self.view_mode_btn.setObjectName("statusToggle")
        self.view_mode_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.view_mode_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.view_mode_btn.clicked.connect(self._view_mode_menu)
        # icon size slider (16-64 px), live for every tab in every window
        from PySide6.QtWidgets import QSlider
        self.icon_small = QLabel("□")
        self.icon_small.setStyleSheet("font-size: 8pt; padding: 0 2px 0 8px;")
        self.icon_slider = QSlider(Qt.Orientation.Horizontal)
        self.icon_slider.setObjectName("iconSlider")
        self.icon_slider.setRange(16, 64)
        self.icon_slider.setSingleStep(4)
        self.icon_slider.setPageStep(8)
        self.icon_slider.setFixedWidth(110)
        self.icon_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.icon_slider.setValue(int(self.config.get("icon_size") or 16))
        self.icon_slider.setToolTip("Icon size")
        self.icon_slider.valueChanged.connect(self.set_icon_size)
        self.icon_big = QLabel("□")
        self.icon_big.setStyleSheet("font-size: 13pt; padding: 0 8px 0 2px;")
        self.update_btn = QToolButton()               # shown when a new release is out
        self.update_btn.setObjectName("statusToggle")
        self.update_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.update_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_btn.hide()
        self.update_btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.update_btn.customContextMenuRequested.connect(self._update_btn_menu)
        self.auto_update_chk = QToolButton()          # an icon: lit when Burrow checks on start
        self.auto_update_chk.setObjectName("statusIcon")
        self.auto_update_chk.setCheckable(True)
        self.auto_update_chk.setAutoRaise(True)
        self.auto_update_chk.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.auto_update_chk.setCursor(Qt.CursorShape.PointingHandCursor)
        self.auto_update_chk.setChecked(bool(self.config.get("check_updates")))
        self.auto_update_chk.toggled.connect(self._auto_update_toggled)
        self.auto_update_chk.toggled.connect(lambda _on: self._sync_auto_update_icon())
        self._sync_auto_update_icon()
        self.progress_pill = ProgressPill()            # copying / extracting, next to the counts
        self._tasks: dict = {}
        self._task_ids = iter(range(1, 1 << 30))
        self.usage_pill = UsagePill()
        self.usage_pill.hide()

        # One row: counts on the left, the switches centred, slider / storage / update on the
        # right. It's a single permanent widget, so status messages are shown in the left
        # label (see _status_message) instead of replacing the row.
        from PySide6.QtWidgets import QGridLayout
        row = QWidget()
        g = QGridLayout(row)
        g.setContentsMargins(4, 0, 4, 0)
        g.setHorizontalSpacing(6)
        self.status_left.setMaximumWidth(520)
        left = QHBoxLayout()
        left.setSpacing(8)
        left.addWidget(self.status_left)
        left.addWidget(self.progress_pill)
        left.addStretch(1)
        g.addLayout(left, 0, 0)
        mid = QHBoxLayout()
        mid.setSpacing(6)
        for wdg in (self.sort_mode_btn, self.view_mode_btn):
            mid.addWidget(wdg)
        g.addLayout(mid, 0, 1)
        right = QHBoxLayout()
        right.setSpacing(4)
        right.addStretch(1)
        for wdg in (self.icon_small, self.icon_slider, self.icon_big, self.usage_pill, self.status_right,
                    self.auto_update_chk, self.update_btn):
            right.addWidget(wdg)
        g.addLayout(right, 0, 2)
        g.setColumnStretch(0, 1)
        g.setColumnStretch(2, 1)
        self.statusBar().addPermanentWidget(row, 1)
        self._status_text = ""
        self.statusBar().messageChanged.connect(self._status_message)

    def _make_menus(self):
        """The menu bar, and the same menus behind the ☰ button."""
        mb = self.menuBar()
        f = mb.addMenu("&File")
        for a in (self.act_new_window, self.act_new_tab, self.act_reopen_tab, None, self.act_new_folder, self.act_new_file,
                  None, self.act_close_tab, self.act_quit):
            f.addSeparator() if a is None else f.addAction(a)
        e = mb.addMenu("&Edit")
        for a in (self.act_cut, self.act_copy, self.act_paste, self.act_paste_tc, self.act_paste_builtin,
                  None, self.act_copy_to, self.act_move_to, self.act_copy_path, None,
                  self.act_rename, self.act_delete, self.act_delete_perm, None,
                  self.act_select_all, self.act_invert):
            e.addSeparator() if a is None else e.addAction(a)
        v = mb.addMenu("&View")
        v.aboutToShow.connect(lambda: (self.act_folders_first.setChecked(self.view().model.folders_first),
                                       self._sync_view_actions()))
        for a in (self.act_inspector, self.act_sidebar, None, self.act_columns_view, self.act_thumbs_view,
                  self.act_river_view, None, self.act_hidden,
                  self.act_folders_first):
            v.addSeparator() if a is None else v.addAction(a)
        self.columns_menu = v.addMenu("Columns")
        self.columns_menu.aboutToShow.connect(self._fill_columns_menu)
        self.presets_menu = v.addMenu("View presets (all folders)")
        self.presets_menu.aboutToShow.connect(self._fill_presets_menu)
        v.addSeparator()
        for a in (self.act_split_h, self.act_split_v, self.act_rotate_split, self.act_unsplit, self.act_move_pane,
                  self.act_other_pane):
            v.addAction(a)
        v.addSeparator()
        v.addAction(self.act_filter)
        v.addAction(self.act_refresh)
        g = mb.addMenu("&Go")
        for a in (self.act_back, self.act_forward, self.act_up, self.act_home, self.act_this_pc,
                  self.act_network, None,
                  self.act_address, None, self.act_next_tab, self.act_prev_tab):
            g.addSeparator() if a is None else g.addAction(a)
        self.bookmarks_menu = mb.addMenu("&Bookmarks")
        self.bookmarks_menu.aboutToShow.connect(self._fill_bookmarks_menu)
        t = mb.addMenu("&Tools")
        t.aboutToShow.connect(lambda: (self.act_default_fm.setChecked(integration.is_default()),
                                       self.act_win_e.setChecked(bool(self.config.get("win_e")))))
        for a in (self.act_compare, self.act_dupes, self.act_bulk_rename, self.act_folderize, None,
                  self.act_terminal, self.act_explorer, self.act_properties, None,
                  self.act_map_drive, self.act_unmap_drive, None, self.act_default_fm, self.act_win_e, None,
                  self.act_updates, self.act_settings):
            t.addSeparator() if a is None else t.addAction(a)
        v.addAction(self.act_command_bar)
        v.addAction(self.act_menubar)

        # The same menus behind one button, so the menu bar can stay hidden.
        self.main_menu = QMenu(self)
        for menu in (f, e, v, g, self.bookmarks_menu, t):
            self.main_menu.addMenu(menu)
        self.main_menu.addSeparator()
        self.main_menu.addAction(self.act_settings)
        self.menu_btn.setMenu(self.main_menu)
        show_mb = bool(self.config.get("show_menubar"))
        self.act_menubar.setChecked(show_mb)
        mb.setVisible(show_mb)

    def _inspector_visibility(self, visible):
        if visible:
            self.inspector_timer.start()
        elif self.isMinimized() or self.windowState() & Qt.WindowState.WindowMinimized:
            self.inspector.media.pause()        # minimized: pause, keep the place for later
        else:
            self.inspector.media.unload()       # inspector closed: stop and free the player

    def _seek_pressed(self, direction: int, big: bool) -> bool:
        if not self.config.get("media_arrow_seek") or not self.insp_dock.isVisible() \
                or not self.inspector.media.is_active():
            return False
        return self.inspector.media.seek_by(direction * (30000 if big else 5000))

    def _space_pressed(self) -> bool:
        return self.insp_dock.isVisible() and self.inspector.toggle_media()

    def _add_tag_menu(self, m: QMenu, paths: list[str]):
        sub = m.addMenu("Tags")
        store = tags.store()
        for tid, name, color in tags.TAGS:
            a = sub.addAction(tags.color_icon(color), name)
            a.setCheckable(True)
            a.setChecked(all(tid in store.get(p) for p in paths))
            a.triggered.connect(lambda on, tid=tid: store.set_tag(paths, tid, on))
        sub.addSeparator()
        clear = sub.addAction("Remove all tags")
        clear.setEnabled(any(store.get(p) for p in paths))
        clear.triggered.connect(lambda: store.clear(paths))

    def _add_color_menu(self, m: QMenu, folders: list[str]):
        sub = m.addMenu("Folder color")
        current = foldercolor.current_color(folders[0]) if len(folders) == 1 else None
        for name, color in foldercolor.COLORS:
            a = sub.addAction(tags.color_icon(color), name)
            a.setCheckable(True)
            a.setChecked(name == current)
            a.triggered.connect(lambda _=False, n=name: self.set_folder_color(folders, n))
        sub.addSeparator()
        sub.addAction("Default").triggered.connect(lambda: self.set_folder_color(folders, None))

    def set_folder_color(self, folders: list[str], name):
        errors = [err for f in folders if (err := foldercolor.set_color(f, name))]
        icon_cache().forget_folder_icons()
        for v in self.views():
            v.tree.viewport().update()
        self.sidebar.rebuild()
        self.inspector_timer.start()
        if errors:
            QMessageBox.warning(self, APP_NAME, "Couldn't change the color of:\n" + "\n".join(errors[:10]))
        else:
            what = name or "the default"
            self.statusBar().showMessage(f"Set {len(folders)} folder(s) to {what}. Explorer shows it too.", 5000)

    def _tags_changed(self):
        for v in self.views():
            if places.is_tag(v.path):
                v.refresh()
            else:
                v.tree.viewport().update()

    def _toggle_menubar(self, on):
        self.menuBar().setVisible(on)
        self.config.set("show_menubar", on)

    def apply_theme(self):
        """Recolor the chrome for the current theme (the file lists are themed by palette only)."""
        c = theme.apply(QApplication.instance(), self.config.get("theme"), self.config.get("palette"))
        self._theme_key = (self.config.get("theme"), c["name"], self.config.get("palette"))
        if self._translucent():
            from . import backdrop
            c = backdrop.tinted(c, self.config.get("backdrop_tint"))
            self.setPalette(theme._palette(c))       # this window only: menus/dialogs stay solid
        else:
            self.setPalette(QApplication.palette())
        if self.isVisible():
            self._native_backdrop(c)
        tf = QApplication.font()                 # inspector title follows the theme font
        tf.setBold(True)
        tf.setPointSizeF(tf.pointSizeF() * 1.15)
        self.inspector.title.setFont(tf)
        self._theme = c
        for p in self.panes:
            self._theme_pane(p)
        self.splitter.setStyleSheet(f"QSplitter::handle {{ background: {c['border']}; }}")
        self.drop_area.style_overlay(c['accent'])
        self.navbar.setStyleSheet(theme.navbar_qss(c))
        self.command_bar.apply_theme()
        self._sync_bar_toggle()
        self.statusBar().setStyleSheet(theme.statusbar_qss(c))
        self._sync_auto_update_icon()
        self.inspector.setStyleSheet(theme.inspector_qss(c))
        self.inspector.media.apply_theme()
        self.sidebar.apply_theme(c)
        for v in self.views():
            v.apply_theme(c)
        for act, name in ((self.act_back, "back"), (self.act_forward, "forward"), (self.act_up, "up"),
                          (self.act_refresh, "refresh"), (self.act_inspector, "inspector"),
                          (self.act_sidebar, "sidebar"), (self.act_settings, "settings")):
            act.setIcon(theme.icon(name))
        self.menu_btn.setIcon(theme.icon("menu"))
        if theme.icon("add").isNull():          # no icon font: fall back to text
            self.menu_btn.setText("☰")

    def _fill_columns_menu(self):
        """View ▸ Columns: column switches, Fit to content and the layout save commands."""
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
        m.addSeparator()
        fit = m.addAction("Fit columns to content")
        fit.setCheckable(True)
        fit.setChecked(bool(self.config.get("auto_fit_columns")))
        fit.toggled.connect(lambda on: self.config.set("auto_fit_columns", on))
        m.addSeparator()
        v.add_layout_actions(m)

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
    @property
    def tabs(self):
        """The active pane's tabs (most commands act on the pane you're working in)."""
        return self.active_pane.tabs

    def view(self) -> FolderView:
        return self.tabs.currentWidget()

    def views(self) -> list[FolderView]:
        return [p.tabs.widget(i) for p in self.panes for i in range(p.tabs.count())]

    # ----- panes ------------------------------------------------------------------------
    def _theme_pane(self, p):
        c = getattr(self, "_theme", None)
        if c is None:
            return
        p.strip.setStyleSheet(theme.tabstrip_qss(c))
        p.tabs.set_close_icon(theme.icon("close", 12))
        p.plus_btn.setIcon(theme.icon("add"))
        if theme.icon("add").isNull():
            p.plus_btn.setText("+")

    def _make_pane(self):
        p = Pane(self)
        self._theme_pane(p)
        return p

    def _new_splitter(self, orientation):
        s = QSplitter(orientation)
        s.setChildrenCollapsible(False)
        s.setHandleWidth(5)
        return s

    def _add_pane(self, index=None):
        p = self._make_pane()
        self.splitter.insertWidget(len(self.panes) if index is None else index, p)
        self._reorder_panes()
        if self.active_pane is None:
            self.active_pane = p
        return p

    def _reorder_panes(self):
        """self.panes in reading order (left to right, top to bottom) across nested splits."""
        out = []

        def walk(w):
            if isinstance(w, Pane):
                out.append(w)
            elif isinstance(w, QSplitter):
                for i in range(w.count()):
                    walk(w.widget(i))
        walk(self.splitter)
        self.panes[:] = out

    def _insert_pane(self, target, zone):
        """A new, empty pane beside target. zone: left / right / top / bottom. Panes split in
        the same direction share one splitter; a different direction nests a new one, so any
        grid (two columns, the right one stacked, ...) can be built by snapping tabs."""
        orient = Qt.Orientation.Horizontal if zone in ("left", "right") else Qt.Orientation.Vertical
        after = 1 if zone in ("right", "bottom") else 0
        p = self._make_pane()
        parent = target.parentWidget()
        idx = parent.indexOf(target)
        if parent.count() == 1 or parent.orientation() == orient:
            sizes = parent.sizes()
            parent.setOrientation(orient)
            parent.insertWidget(idx + after, p)
            if len(sizes) == parent.count() - 1 and sizes[idx] > 0 and parent.count() > 2:
                half = sizes[idx] // 2
                sizes[idx] -= half
                sizes.insert(idx + after, half)
                parent.setSizes(sizes)
            else:
                QTimer.singleShot(0, lambda s=parent: s.setSizes([10000] * s.count()))
        else:
            sizes = parent.sizes()
            sub = self._new_splitter(orient)
            parent.replaceWidget(idx, sub)
            sub.addWidget(target)
            sub.insertWidget(after, p)
            target.show()
            parent.setSizes(sizes)
            sub.setSizes([10000, 10000])
        self._reorder_panes()
        self._place_navbar()
        return p

    def _collapse(self, s):
        """After a pane closed: fold splitters left with a single child into their parent."""
        while s is not None and s is not self.splitter and isinstance(s, QSplitter) and s.count() <= 1:
            gp = s.parentWidget()
            if s.count() == 1:
                child = s.widget(0)
                sizes = gp.sizes()
                gp.replaceWidget(gp.indexOf(s), child)
                child.show()
                gp.setSizes(sizes)
            s.setParent(None)
            s.deleteLater()
            s = gp
        root = self.splitter
        if root.count() == 1 and isinstance(root.widget(0), QSplitter):
            sub = root.widget(0)
            sizes = sub.sizes()
            root.setOrientation(sub.orientation())
            for c in [sub.widget(i) for i in range(sub.count())]:
                root.addWidget(c)
                c.show()
            sub.setParent(None)
            sub.deleteLater()
            root.setSizes(sizes)

    def _place_navbar(self):
        if len(self.panes) == 1:
            self.panes[0].lay.insertWidget(1, self.navbar)       # tabs, navbar, files
        else:
            self.central_layout.insertWidget(0, self.navbar)     # navbar above the split
        for p in self.panes:
            p.set_active(len(self.panes) > 1 and p is self.active_pane)

    def set_active_pane(self, p):
        if p is self.active_pane or p not in self.panes:
            return
        self.active_pane = p
        for q in self.panes:
            q.set_active(len(self.panes) > 1 and q is p)
        self._sync_location()

    def _focus_changed(self, _old, new):
        if new is None or not self.panes:
            return
        for p in self.panes:
            if p.isAncestorOf(new):
                self.set_active_pane(p)
                return

    def _tab_clicked(self, p, i):
        """A click on any tab, even the one already showing, makes its pane the active one and
        puts the keyboard in its file list (the tab bar itself never takes focus)."""
        if i < 0:
            return
        self.set_active_pane(p)
        QTimer.singleShot(0, lambda: self.view().focus_list() if self.view() is not None else None)

    def _pane_changed(self, p):
        if p is self.active_pane:
            self._sync_location()
        elif p.tabs.count():
            self.set_active_pane(p)

    def pane_of(self, v):
        return next((p for p in self.panes if p.tabs.indexOf(v) >= 0), None)

    def _next_pane(self, step=1):
        i = self.panes.index(self.active_pane) if self.active_pane in self.panes else 0
        return self.panes[(i + step) % len(self.panes)]

    def _room_for_pane(self) -> bool:
        if len(self.panes) < MAX_PANES:
            return True
        self.statusBar().showMessage(f"Up to {MAX_PANES} panes per window", 4000)
        return False

    def split(self, orientation, move_current=False):
        """Split the active pane: side by side (Horizontal) or stacked (Vertical)."""
        if not self._room_for_pane():
            return
        src = self.active_pane
        new = self._insert_pane(src, "right" if orientation == Qt.Orientation.Horizontal else "bottom")
        if move_current and src.tabs.count() > 1:
            self._move_tab(src, src.tabs.currentIndex(), new)
        else:
            path = self.view().path
            self.active_pane = new
            self.new_tab(path)
        self._place_navbar()
        self.set_active_pane(new)
        self.config.set("split", "h" if orientation == Qt.Orientation.Horizontal else "v")

    def rotate_split(self):
        """Side by side <-> stacked, for every split in the window."""
        def flip(s):
            s.setOrientation(Qt.Orientation.Vertical if s.orientation() == Qt.Orientation.Horizontal
                             else Qt.Orientation.Horizontal)
            for i in range(s.count()):
                if isinstance(s.widget(i), QSplitter):
                    flip(s.widget(i))
        if len(self.panes) > 1:
            flip(self.splitter)
            self._reorder_panes()

    def unsplit(self):
        while len(self.panes) > 1:
            src = self.panes[-1]
            while src.tabs.count():
                self._move_tab(src, 0, self.panes[0], remove_empty=False)
            self._remove_pane(src)
        self.config.set("split", "")

    def _remove_pane(self, p):
        """Close a pane and fold the splitters around it."""
        if self.active_pane is p:
            i = self.panes.index(p)
            self.active_pane = self.panes[i - 1] if i > 0 else self.panes[1]
        self.panes.remove(p)
        self._place_navbar()                 # move the navbar out before the pane goes away
        parent = p.parentWidget()
        p.setParent(None)
        p.deleteLater()
        self._collapse(parent)
        self._reorder_panes()
        for q in self.panes:
            q.set_active(len(self.panes) > 1 and q is self.active_pane)
        if len(self.panes) == 1:
            self.config.set("split", "")
        self._sync_location()

    def _move_tab(self, src, index, dst, remove_empty=True):
        """Move one tab to another pane (closing the source pane if it's left empty)."""
        v = src.tabs.widget(index)
        if v is None:
            return
        src.tabs.removeTab(index)
        i = dst.tabs.insertTab(dst.tabs.count(), v, title_for(v.path))
        dst.tabs.setTabToolTip(i, display_path(v.path))
        dst.tabs.setCurrentIndex(i)
        if remove_empty and src.tabs.count() == 0 and len(self.panes) > 1:
            if self.active_pane is src:
                self.active_pane = dst
            self._remove_pane(src)
        self.set_active_pane(dst)
        v.focus_list()

    def move_tab_to_other_pane(self):
        if len(self.panes) < 2:
            self.split(Qt.Orientation.Horizontal, move_current=True)
            return
        src = self.active_pane
        self._move_tab(src, src.tabs.currentIndex(), self._next_pane())

    def focus_other_pane(self):
        if len(self.panes) > 1:
            other = self._next_pane()
            self.set_active_pane(other)
            if other.tabs.currentWidget():
                other.tabs.currentWidget().focus_list()

    def snap_tab(self, src, index, zone, target):
        """A tab dropped on a pane's edge (split that pane) or its middle (move it there)."""
        if src not in self.panes or target not in self.panes:
            return
        if zone == "move":
            self._move_tab(src, index, target)
            return
        if not self._room_for_pane():
            return
        new = self._insert_pane(target, zone)
        if src.tabs.count() > 1 or src is not target:
            self._move_tab(src, index, new)
        else:                                 # its only tab, on its own pane: show the folder twice
            v = src.tabs.widget(index)
            self.active_pane = new
            self.new_tab(v.path if v else None)
        self._place_navbar()
        self.set_active_pane(new)
        self.config.set("split", "h" if zone in ("left", "right") else "v")

    # ----- saving / restoring the split layout --------------------------------------
    def _layout_of(self, w):
        if isinstance(w, Pane):
            return {"tabs": [w.tabs.widget(i).path for i in range(w.tabs.count())], "cur": w.tabs.currentIndex()}
        return {"o": "h" if w.orientation() == Qt.Orientation.Horizontal else "v", "sizes": w.sizes(),
                "c": [self._layout_of(w.widget(i)) for i in range(w.count())]}

    def _restore_layout(self, node) -> bool:
        """Rebuild saved panes. The first pane already exists and is reused."""
        reuse = [self.panes[0]]

        def build(node, s):
            """Recreate one level of saved splits and the tabs in each pane."""
            s.setOrientation(Qt.Orientation.Horizontal if node.get("o") == "h" else Qt.Orientation.Vertical)
            for child in node.get("c", [])[:MAX_PANES]:
                if "c" in child:
                    sub = self._new_splitter(Qt.Orientation.Horizontal)
                    s.addWidget(sub)
                    build(child, sub)
                    continue
                p = reuse.pop() if reuse else self._make_pane()
                s.addWidget(p)
                if p not in self.panes:
                    self.panes.append(p)
                self.active_pane = p
                for path in [x for x in child.get("tabs", []) if restorable(x)] or [HOME]:
                    self.new_tab(path, activate=False, after_current=False)
                p.tabs.setCurrentIndex(min(max(int(child.get("cur") or 0), 0), p.tabs.count() - 1))
            sizes = node.get("sizes")
            if sizes and len(sizes) == s.count():
                QTimer.singleShot(0, lambda s=s, sizes=sizes: s.setSizes(sizes))
            else:
                QTimer.singleShot(0, lambda s=s: s.setSizes([10000] * s.count()))
        try:
            build(node, self.splitter)
        except (TypeError, AttributeError, ValueError, KeyError):
            return False
        self._reorder_panes()
        self.active_pane = self.panes[0]
        self._place_navbar()
        return True

    def _tab_menu(self, pane, pos):
        """Right-click on a tab: close, split, move, new window."""
        i = pane.tabs.bar.tabAt(pos)
        if i < 0:
            return
        self.set_active_pane(pane)
        pane.tabs.setCurrentIndex(i)
        m = QMenu(self)
        m.addAction("Close tab").triggered.connect(lambda: self.close_tab(i, pane))
        others = m.addAction("Close other tabs")
        others.setEnabled(pane.tabs.count() > 1)
        others.triggered.connect(lambda: [self.close_tab(j, pane) for j in reversed(range(pane.tabs.count())) if j != i])
        m.addSeparator()
        m.addAction("Open in split right").triggered.connect(lambda: self.split(Qt.Orientation.Horizontal, True))
        m.addAction("Open in split below").triggered.connect(lambda: self.split(Qt.Orientation.Vertical, True))
        if len(self.panes) > 1:
            m.addAction("Move to next pane").triggered.connect(self.move_tab_to_other_pane)
            m.addAction("Rotate split (side by side / stacked)").triggered.connect(self.rotate_split)
            m.addAction("Single pane").triggered.connect(self.unsplit)
        m.addSeparator()
        m.addAction("Open in new window").triggered.connect(lambda: open_new_window(self.config, [pane.tabs.widget(i).path]))
        m.exec(pane.tabs.bar.mapToGlobal(pos))

    def new_tab(self, path=None, activate=True, after_current=True):
        """Open a folder in a new tab of the active pane and wire the view to the window."""
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
        v.space_handler = self._space_pressed
        v.seek_handler = self._seek_pressed
        v.sidebar_handler = self._list_left_pressed
        v.icon_wheel_handler = self._icon_wheel
        if not v.navigate(path, record=False):
            v.navigate(HOME, record=False)
        idx = self.tabs.currentIndex() + 1 if after_current and self.tabs.count() else self.tabs.count()
        self.tabs.insertTab(idx, v, title_for(v.path))
        self.tabs.setTabToolTip(idx, display_path(v.path))
        if activate:
            self.tabs.setCurrentIndex(idx)
            v.focus_list()
        return v

    def close_tab(self, index, pane=None):
        """Close a tab (remembered for Ctrl+Shift+T); the last tab of a split pane closes the pane."""
        pane = pane or self.active_pane
        if index < 0 or pane not in self.panes:
            return
        if pane.tabs.count() <= 1:
            if len(self.panes) > 1:          # last tab of a split pane: close the pane
                v = pane.tabs.widget(index)
                self.closed_tabs.append(v.path)
                pane.tabs.removeTab(index)
                v.deleteLater()
                self._remove_pane(pane)
            else:                            # the window's only tab: close the window, like Explorer
                self.close()
            return
        v = pane.tabs.widget(index)
        self.closed_tabs.append(v.path)
        pane.tabs.removeTab(index)
        v.deleteLater()

    def reopen_tab(self):
        while self.closed_tabs:
            p = self.closed_tabs.pop()
            if restorable(p):
                self.new_tab(p)
                return

    def cycle_tab(self, delta):
        n = self.tabs.count()
        if n > 1:
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + delta) % n)

    def eventFilter(self, obj, ev):
        """Tab strip: double-click on empty space opens a tab, middle-click on a tab closes it."""
        for p in self.panes:
            if obj is p.strip and ev.type() == QEvent.Type.MouseButtonDblClick:
                self.set_active_pane(p)
                self.new_tab()
                return True
            if (obj is p.tabs.bar and ev.type() == QEvent.Type.MouseButtonRelease
                    and ev.button() == Qt.MouseButton.MiddleButton):
                i = obj.tabAt(ev.position().toPoint())
                if i >= 0:
                    self.close_tab(i, p)
                    return True
        return super().eventFilter(obj, ev)

    # ----- keeping the chrome in sync -------------------------------------------------
    def _on_view_path(self, v):
        p = self.pane_of(v)
        if p is not None:
            i = p.tabs.indexOf(v)
            p.tabs.setTabText(i, title_for(v.path))
            p.tabs.setTabToolTip(i, display_path(v.path))
        if v is self.view():
            self._sync_location()

    def _on_view_selection(self, v):
        if v is self.view():
            self._update_status()
            self.command_bar.sync()
            self.inspector_timer.start()

    def _sync_location(self):
        """The active tab changed folder: update address bar, buttons, title, sidebar and status."""
        v = self.view()
        if v is None or v.path is None:
            return
        self.address_area.set_path(v.path, display_path(v.path))
        self.act_back.setEnabled(bool(v.back_stack))
        self.act_forward.setEnabled(bool(v.fwd_stack))
        self.act_up.setEnabled(v.parent_path() is not None)
        marked = not places.is_special(v.path) and self.config.is_bookmarked(v.path)
        self.act_bookmark.setText("Remove bookmark" if marked else "Bookmark this folder")
        self.act_bookmark.setEnabled(not places.is_special(v.path))
        self.sidebar.highlight(v.path)
        self.command_bar.sync()
        self.setWindowTitle(f"{title_for(v.path)} — {APP_NAME}")
        self.status_right.setText("")
        self.usage_pill.set_usage(None, None)
        path = v.path
        if not is_location(path):
            def space():
                du = shutil.disk_usage(path)
                return du.total, du.free

            def show(res, path=path):
                if self.view() is not None and self.view().path == path and isinstance(res, tuple):
                    total, free = res
                    self.status_right.setText(f"{human_size(free)} free of {human_size(total)}")
                    self.usage_pill.set_usage(total, free)
                    self.usage_pill.setToolTip(f"{(1 - free / total) * 100:.0f}% used" if total else "")
            if places.is_remote(path):
                places.run_async(space, show)      # a network drive may answer slowly
            else:
                try:
                    show(space())
                except OSError:
                    pass
        self._update_status()
        self.inspector_timer.start()

    def _update_status(self):
        """Item and selection counts at the bottom left."""
        v = self.view()
        if v is None:
            return
        shown, total = v.proxy.rowCount(), len(v.model.entries)
        parts = [f"{shown:,} items" if shown == total else f"{shown:,} of {total:,} items (filtered)"]
        sel = v.selected_entries()
        if sel:
            sized = [e for e in sel if e.size is not None and not e.is_drive]   # folders sized by Everything count too
            s = f"{len(sel):,} selected"
            if sized:
                s += f" · {human_size(sum(e.size for e in sized))}"
            parts.append(s)
        self._status_text = "     ".join(parts)
        if not self.statusBar().currentMessage():
            self.status_left.setText(self._status_text)
        self._sync_sort_mode(v)

    def _status_message(self, text: str):
        """showMessage() text goes in the left label for a while, then the counts come back."""
        self.status_left.setText(text or self._status_text)

    def _sync_sort_mode(self, v=None):
        """Keep the status bar's Sort switch in step with the active view."""
        v = v or self.view()
        if v is None:
            return
        mixed = not v.model.folders_first
        self.sort_mode_btn.blockSignals(True)
        self.sort_mode_btn.setChecked(mixed)
        self.sort_mode_btn.blockSignals(False)
        self.sort_mode_btn.setText("Sort: mixed" if mixed else "Sort: folders first")
        self.sort_mode_btn.setVisible(bool(self.config.get("status_sort_toggle")) and not v.columns_active())
        self.view_mode_btn.setText("View: " + dict(self.VIEW_MODES)[v.mode].split(" ")[0].lower())
        self.view_mode_btn.setToolTip("List, columns, thumbnails or river (photos) for this tab")
        self.sort_mode_btn.setToolTip("Files, folders and archives sorted together by the column — click for folders first"
                                      if mixed else "Folders on top, then files — click to sort everything by the column only")

    def set_icon_size(self, px: int):
        self.config.set("icon_size", int(px))
        for win in WINDOWS:
            for view in win.views():
                view.set_icon_size(px)
            if win is not self and win.icon_slider.value() != px:
                win.icon_slider.blockSignals(True)
                win.icon_slider.setValue(px)
                win.icon_slider.blockSignals(False)

    VIEW_MODES = (("list", "List"), ("columns", "Columns"), ("thumbs", "Thumbnails"), ("river", "River (photos)"))

    def _sync_view_actions(self):
        mode = self.view().mode
        for a, m in ((self.act_columns_view, "columns"), (self.act_thumbs_view, "thumbs"), (self.act_river_view, "river")):
            a.setChecked(mode == m)

    def _view_mode_menu(self):
        """The status bar's View button: pick one of the four views."""
        from PySide6.QtGui import QActionGroup
        m = QMenu(self)
        group = QActionGroup(m)
        for mode, title in self.VIEW_MODES:
            a = m.addAction(title)
            a.setCheckable(True)
            a.setChecked(self.view().mode == mode)
            group.addAction(a)
            a.triggered.connect(lambda _=False, md=mode: self.set_view_mode(md))
        m.exec(self.view_mode_btn.mapToGlobal(self.view_mode_btn.rect().topLeft()) - QPoint(0, m.sizeHint().height()))

    def set_view_mode(self, mode: str):
        """List, columns, thumbnails or river for this tab; new tabs open the same way."""
        v = self.view()
        if v is not None:
            v.set_mode(mode)
        self.config.set("view_mode", mode)
        self._sync_sort_mode(v)

    # ----- window material (Mica / Acrylic) ----------------------------------------------
    def _translucent(self) -> bool:
        from . import backdrop
        return self.config.get("backdrop") in ("mica", "mica_alt", "acrylic") and backdrop.supported()

    def _native_backdrop(self, c=None):
        from . import backdrop
        c = c or getattr(self, "_theme", None) or theme.colors()
        hwnd = int(self.winId())
        backdrop.set_dark_title(hwnd, c.get("name") == "dark")      # title bar follows the theme
        backdrop.apply(hwnd, self.config.get("backdrop") if self._translucent() else "none")

    def apply_backdrop(self):
        """Switch the material. Turning see-through on/off needs a new native window, so the
        window is re-created in place (same size, tabs and state)."""
        self._backdrop_key = (self.config.get("backdrop"), self.config.get("backdrop_tint"))
        want = self._translucent()
        if want != self.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground):
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, want)
            if self.isVisible():
                geo, state = self.saveGeometry(), self.windowState()
                self.hide()
                self.destroy(True, False)
                self.create()
                self.restoreGeometry(geo)
                self.setWindowState(state)
                self.show()
        self.apply_theme()

    # ----- keyboard ------------------------------------------------------------------------
    def _list_left_pressed(self) -> bool:
        if not self.config.get("arrow_sidebar_nav") or not self.side_dock.isVisible():
            return False
        self.focus_sidebar()
        return True

    def focus_sidebar(self):
        if not self.side_dock.isVisible():
            self.side_dock.show()
        self.sidebar.highlight(self.view().path)
        self.sidebar.focus_from_list()

    def cycle_focus(self, step: int):
        """F6 / Shift+F6 like Explorer: address bar, sidebar, then each pane's files."""
        areas = [("address", None)]
        if self.side_dock.isVisible():
            areas.append(("sidebar", None))
        areas += [("pane", p) for p in self.panes]
        fw = QApplication.focusWidget()
        cur = 0                                   # the address bar sits inside a single pane: test it first
        if fw is not None and not self.address_area.isAncestorOf(fw):
            for i, (kind, p) in enumerate(areas):
                if (kind == "sidebar" and fw is self.sidebar) or (kind == "pane" and p.isAncestorOf(fw)):
                    cur = i
        kind, p = areas[(cur + step) % len(areas)]
        if kind == "address":
            self.focus_address()
        elif kind == "sidebar":
            self.focus_sidebar()
        else:
            self.set_active_pane(p)
            if p.tabs.currentWidget():
                p.tabs.currentWidget().focus_list()

    def toggle_fullscreen(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def open_selected_in_window(self):
        dirs = [e.path for e in self.view().selected_entries() if e.is_dir]
        if dirs:
            open_new_window(self.config, dirs)

    def _icon_wheel(self, step: int):
        if not self.config.get("ctrl_wheel_icon_size"):
            return
        self.icon_slider.setValue(self.icon_slider.value() + 4 * step)

    def apply_ui_switches(self):
        """Settings switches for the status bar, sidebar dragging, preview handle and keys."""
        c = self.config
        self.view_mode_btn.setVisible(bool(c.get("status_view_toggle")))
        self.auto_update_chk.setVisible(bool(c.get("status_auto_update")))
        if self.auto_update_chk.isChecked() != bool(c.get("check_updates")):
            self.auto_update_chk.blockSignals(True)
            self.auto_update_chk.setChecked(bool(c.get("check_updates")))
            self.auto_update_chk.blockSignals(False)
        self.update_btn.setVisible(bool(c.get("status_update_button")) and getattr(self, "_update_info", None) is not None)
        for wdg in (self.icon_small, self.icon_slider, self.icon_big):
            wdg.setVisible(bool(c.get("status_icon_slider")))
        self.sidebar.drag_reorder = bool(c.get("sidebar_drag_reorder"))
        self.sidebar.show_eject = bool(c.get("sidebar_eject"))
        from . import marks
        marks.set_enabled(bool(c.get("clipboard_marks")))
        self.usage_pill.on = bool(c.get("status_storage_bar"))
        if not self.usage_pill.on:
            self.usage_pill.hide()
        for v in self.views():
            v.set_drive_bars(bool(c.get("drive_usage_bars")))
        self.inspector.set_resizable(bool(c.get("inspector_resizable")))
        if c.get("shortcut_scheme") != getattr(self, "_scheme", None):
            self._scheme = c.get("shortcut_scheme")
            self.apply_shortcuts()
            self.sidebar.rebuild_soon()            # the bookmarks hint names the key
        self._sync_sort_mode()

    def _view_preset_changed(self, _src=None):
        QTimer.singleShot(0, self, self._sync_sort_mode)    # after the views have re-applied it

    def _sort_mode_toggled(self, mixed: bool):
        v = self.view()
        if v is not None:
            v.set_folders_first(not mixed)     # saved to the view preset, so every folder follows
        self._sync_sort_mode(v)

    def _update_inspector(self):
        if not self.insp_dock.isVisible():
            return
        v = self.view()
        if v is not None:
            self.inspector.use_everything = bool(self.config.get("everything_sizes"))
            self.inspector.folder_contents = not v.columns_active()   # columns show it already
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
        """Settings were saved: apply whatever changed, cheapest checks first."""
        self.act_hidden.setChecked(self.config.get("show_hidden"))
        self.apply_ui_switches()
        bd = (self.config.get("backdrop"), self.config.get("backdrop_tint"))
        if bd != getattr(self, "_backdrop_key", None):
            self.apply_backdrop()
        if (self.config.get("theme"), theme.resolve(self.config.get("theme")), self.config.get("palette")) != self._theme_key:
            self.apply_theme()
        bg = (self.config.get("win_e"), self.config.get("run_in_background"))
        if bg != getattr(self, "_bg_key", None):
            self._bg_key = bg
            self.apply_background_switches()
        feats = tuple(self.config.get(k) for k in self.FEATURE_KEYS)
        if feats != getattr(self, "_feature_key", None):
            self._feature_key = feats
            self.apply_feature_switches()
        key = (self.config.get("show_hidden"), self.config.get("date_format"), self.config.get("everything_sizes"))
        if key != getattr(self, "_listing_key", None):     # only re-list when it matters
            self._listing_key = key
            for v in self.views():
                v.apply_settings()

    FEATURE_KEYS = ("show_folder_icons", "show_tags", "preview_images", "preview_media", "preview_text",
                    "text_syntax_colors", "preview_archives", "preview_epub", "preview_folders", "sidebar_places", "sidebar_cloud",
                    "sidebar_tags", "sidebar_network")

    def _system_scheme_changed(self, *_):
        if self.config.get("theme") == "system":
            self.apply_theme()

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
        self.address_area.start_edit()

    def _crumb_navigate(self, path, in_new_tab):
        if in_new_tab:
            self.new_tab(path, activate=False)
        else:
            self.view().navigate(path)
            self.view().focus_list()

    def _address_entered(self):
        """Enter in the address bar: open a folder, select a file, or go to a server."""
        text = self.address.text().strip()
        v = self.view()
        if text.lower() in ("this pc", "computer", "my computer", ""):
            v.navigate(DRIVES)
        elif text.lower() == "network":
            v.navigate(NETWORK)
        elif text.startswith("\\\\") or places.is_remote(normalize_path(text)):
            v.navigate(text)                    # checked by the background load, never blocks
        else:
            p = normalize_path(text)
            if os.path.isdir(p):
                v.navigate(p)
            elif os.path.isfile(p):
                v.navigate(os.path.dirname(p), select=p)
            else:
                QMessageBox.warning(self, APP_NAME, f"Can't find “{text}”.\nCheck the spelling and try again.")
                return
        v.focus_list()

    def _address_escaped(self):
        self.address.setText(display_path(self.view().path))
        self.address_area.stop_edit()
        self.view().focus_list()

    def _sidebar_open(self, path, in_new_tab):
        if in_new_tab:
            self.new_tab(path, activate=False)
        else:
            self.view().navigate(path)
            self.view().focus_list()

    # ----- context menu ------------------------------------------------------------------
    def _context_menu(self, v: FolderView, pos):
        """Right-click in a file list (kept deliberately short)."""
        where, on_item = v.menu_pos(pos)
        if not on_item and not v.columns_active():
            v.tree.clearSelection()
        entries = v.selected_entries()
        m = QMenu(self)
        if entries:
            m.addAction(self.act_open)
            if any(e.is_dir for e in entries):
                m.addAction(self.act_open_tab)
            if len(entries) == 1 and not entries[0].is_dir:
                m.addAction(self.act_open_with)
            if len(entries) == 1 and entries[0].is_drive and entries[0].ejectable:
                root = entries[0].path
                m.addAction("Eject").triggered.connect(lambda: self.eject_drive(root))
            if not any(e.is_drive for e in entries):
                m.addSeparator()
                # kept short on purpose: Copy/Move to folder and Delete permanently live in
                # the Edit menu and on Shift+Del
                for a in (self.act_cut, self.act_copy, self.act_copy_path, None,
                          self.act_rename, self.act_delete):
                    m.addSeparator() if a is None else m.addAction(a)
                if self.config.get("context_archive") and not any(places.is_remote(e.path) and
                                                                  places.is_server(e.path) for e in entries):
                    self._add_archive_menu(m, [e.path for e in entries])
            plain = [e for e in entries if not e.is_drive]
            if plain:
                m.addSeparator()
                if tags.ENABLED:
                    self._add_tag_menu(m, [e.path for e in plain])
                folders = [e.path for e in plain if e.is_dir and not places.is_remote(e.path)]
                if folders:
                    self._add_color_menu(m, folders)
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
            if not is_location(v.path):
                for a in (self.act_paste, self.act_paste_tc, self.act_paste_builtin, None,
                          self.act_new_folder, self.act_new_file, None):
                    m.addSeparator() if a is None else m.addAction(a)
            m.addAction(self.act_refresh)
            m.addAction(self.act_hidden)
            presets = m.addMenu("View presets (all folders)")
            dialogs.add_preset_actions(presets, self.config, self)
            if not is_location(v.path):
                m.addSeparator()
                m.addAction(self.act_bookmark)
                m.addAction(self.act_terminal)
                m.addAction(self.act_explorer)
                m.addAction(self.act_properties)
        m.exec(where)

    # ----- file operations ---------------------------------------------------------------
    # ----- archives: Extract here / Extract to folder / Add to archive ---------------------
    def _add_archive_menu(self, m: QMenu, paths: list[str]):
        from . import extract
        if extract.tool() is None:
            return
        m.addSeparator()
        if all(extract.is_archive(p) for p in paths):
            m.addAction("Extract here").triggered.connect(lambda: self.run_extract(paths, False))
            label = (f"Extract to \"{extract.folder_name(paths[0])}\\\"" if len(paths) == 1
                     else "Extract each to its own folder")
            m.addAction(label).triggered.connect(lambda: self.run_extract(paths, True))
        m.addAction("Add to archive…").triggered.connect(lambda: self.run_compress(paths))

    def _archive_job(self, fn):
        """tar.exe runs without a window: do it in the background, then refresh."""
        self.statusBar().showMessage("Working on the archive…")
        tid = self.task_begin("Working on the archive")

        def done(res):
            self.task_end(tid)
            self.statusBar().clearMessage()
            errors = [str(res)] if isinstance(res, Exception) else res
            if errors:
                QMessageBox.warning(self, APP_NAME, "The archive couldn't be processed:\n\n" + "\n".join(errors[:8]))
            self.refresh_view()
        places.run_async(fn, done)

    def can_extract(self, entries) -> bool:
        """Whether every selected item is an archive and an extractor is installed."""
        from . import extract
        return bool(entries) and all(not e.is_dir and extract.is_archive(e.path) for e in entries) \
            and extract.tool() is not None

    def extract_selected(self):
        """Ctrl+Shift+X / the Extract button: each selected archive into its own folder."""
        es = self._selected()
        if self.can_extract(es):
            self.run_extract([e.path for e in es], True)
        elif es:
            self.statusBar().showMessage("Select an archive (zip, 7z, rar…) to extract", 4000)

    def set_command_bar(self, on: bool):
        """Show or hide the button row above the address bar (the chevron, or View ▸ Toolbar)."""
        self.config.set("command_bar", bool(on))
        for w in WINDOWS:
            w.command_bar.setVisible(bool(on))
            w.act_command_bar.setChecked(bool(on))
            w._sync_bar_toggle()

    def _sync_bar_toggle(self):
        on = self.command_bar.isVisibleTo(self.navbar)
        self.bar_toggle.setIcon(theme.icon("chevron_up" if on else "chevron_down"))
        self.bar_toggle.setToolTip("Hide the toolbar" if on else "Show the toolbar")

    def run_extract(self, paths, to_folder):
        from . import extract
        err = extract.extract(paths, to_folder, self._archive_job,
                              watch=lambda p: self._watch_process(p, f"Extracting {len(paths)} archive(s)"))
        if err:
            QMessageBox.warning(self, APP_NAME, err)

    def run_compress(self, paths):
        from . import extract
        err = extract.compress(paths, self._archive_job,
                               watch=lambda p: self._watch_process(p, "Adding to archive"))
        if err:
            QMessageBox.warning(self, APP_NAME, err)

    def rename_selected(self):
        if len(self._selected()) > 1 and self.config.get("bulk_rename_f2"):
            self.open_bulk_rename()
        else:
            self.view().edit_current()

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
        paths = [e.path for e in es] or ([self.view().path] if not places.is_special(self.view().path) else [])
        if paths:
            QGuiApplication.clipboard().setText("\n".join(paths))
            self.statusBar().showMessage("Path copied", 3000)

    def paste(self, engine):
        v = self.view()
        if is_location(v.path):
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
                                                self.view().path if not is_location(self.view().path) else HOME)
        if dest:
            self.transfer([e.path for e in es], os.path.normpath(dest), move)

    def transfer(self, sources, target, move, engine=None) -> bool:
        """Copy or move sources into target, through TeraCopy or the built-in copier."""
        if not target or is_location(target):
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
                    for s in srcs:                    # TeraCopy reports nothing back: copy tags along
                        tags.store().copied(s, os.path.join(target, os.path.basename(s)))
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
        from . import marks
        marks.add_pasting([d for _s, d, _m in plan])      # green while they're being written
        runner.finished.connect(self._job_finished)
        runner.task = self.task_begin(f"{'Moving' if move else 'Copying'} {len(plan)} item(s)")
        runner.job.progress.connect(runner.report)
        self.jobs.append(runner)
        runner.start()
        return True

    def _build_plan(self, srcs, target, move):
        """Turn a copy/move request into [source, target, mode] steps, asking about name clashes."""
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

    # ----- the progress pill: one for everything Burrow is copying, moving or unpacking ------------
    def task_begin(self, label: str) -> int:
        tid = next(self._task_ids)
        self._tasks[tid] = [label, 0, None]
        self._sync_progress()
        return tid

    def task_update(self, tid: int, done, total):
        if tid in self._tasks:
            self._tasks[tid][1:] = [done, total]
            self._sync_progress()

    def task_end(self, tid: int):
        self._tasks.pop(tid, None)
        self._sync_progress()

    def _sync_progress(self):
        """Filled to the share done (all jobs together), or gliding when an archiver in its
        own window gives no figures."""
        if not self._tasks:
            self.progress_pill.stop()
            return
        known = [t for t in self._tasks.values() if t[2]]
        value = None
        if len(known) == len(self._tasks):
            value = sum(t[1] for t in known) / max(1, sum(t[2] for t in known))
        tip = "\n".join(t[0] + (f" — {int(t[1] * 100 / t[2])}%" if t[2] else "") for t in self._tasks.values())
        self.progress_pill.set_progress(value, tip)

    def _watch_process(self, proc, label: str):
        """NanaZip / 7-Zip / WinRAR work in their own window: show the pill until they exit,
        then refresh."""
        tid = self.task_begin(label)
        timer = QTimer(self)
        timer.setInterval(400)

        def check():
            if proc.poll() is not None:
                timer.stop()
                timer.deleteLater()
                self.task_end(tid)
                self.refresh_view()
        timer.timeout.connect(check)
        timer.start()

    def _open_from_inspector(self, path: str):
        """Double-click in the preview's folder contents: folders open here, files in their app."""
        if os.path.isdir(path):
            self.view().navigate(path)
        else:
            try:
                fileops.open_path(path)
            except OSError as ex:
                self.statusBar().showMessage(f"Couldn't open it: {ex.strerror or ex}", 5000)

    def _job_finished(self, runner):
        """A copy/move finished: tags follow the items, the list refreshes."""
        from . import marks
        self.task_end(getattr(runner, "task", -1))
        marks.remove_pasting([d for _s, d, _m in runner.job.plan])
        for src, dst, _mode in runner.job.plan:      # tags follow items Burrow moved or copied
            if os.path.lexists(dst):
                if runner.move and not os.path.lexists(src):
                    tags.store().renamed(src, dst)
                elif not runner.move:
                    tags.store().copied(src, dst)
        if runner in self.jobs:
            self.jobs.remove(runner)
        runner.deleteLater()
        self.view().refresh()

    def delete(self, permanent):
        """Delete the selection: Recycle Bin, or permanently (Shift+Del), asking first if set to."""
        es = self._selected()
        if not es:
            return
        desc = f"“{es[0].name}”" if len(es) == 1 else f"these {len(es)} items"
        if permanent:
            if self.config.get("confirm_delete_permanent") and \
                    QMessageBox.warning(self, "Delete permanently", f"Permanently delete {desc}?\n\nThis can't be undone.",
                                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                        QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
        elif self.config.get("confirm_delete"):
            from PySide6.QtWidgets import QCheckBox
            box = QMessageBox(QMessageBox.Icon.Question, "Delete", f"Move {desc} to the Recycle Bin?",
                              QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, self)
            box.setDefaultButton(QMessageBox.StandardButton.Yes)
            dont = QCheckBox("Don't ask again (turn it back on in Settings ▸ General)")
            box.setCheckBox(dont)
            if box.exec() != QMessageBox.StandardButton.Yes:
                return
            if dont.isChecked():
                self.config.set("confirm_delete", False)
        v = self.view()
        row = v.tree.currentIndex().row()
        ok, aborted = fileops.delete_paths([e.path for e in es], int(self.winId()), permanent)
        tags.store().deleted([e.path for e in es if not os.path.lexists(e.path)])
        v.refresh()
        n = v.proxy.rowCount()
        if n and row >= 0:
            v.tree.setCurrentIndex(v.proxy.index(min(row, n - 1), 0))
        if not ok and not aborted:
            self.statusBar().showMessage("Some items couldn't be deleted", 6000)

    def _create(self, name, is_dir):
        """New folder / text file with a free name, then start renaming it."""
        v = self.view()
        if is_location(v.path):
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
        v.focus_list()
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
        path = es[0].path if es else (v.path if not places.is_special(v.path) else None)
        if path:
            fileops.show_properties(path, int(self.winId()))

    def toggle_bookmark(self):
        p = self.view().path
        if places.is_special(p):
            return
        if self.config.is_bookmarked(p):
            self.config.remove_bookmark(p)
            self.statusBar().showMessage("Bookmark removed", 3000)
        else:
            self.config.add_bookmark(p)
            self.statusBar().showMessage("Bookmarked", 3000)

    def open_terminal(self):
        p = self.view().path
        fileops.open_terminal(HOME if is_location(p) else p)

    def show_in_explorer(self):
        v = self.view()
        es = v.selected_entries()
        if es:
            fileops.show_in_explorer(es[0].path, select=True)
        else:
            fileops.show_in_explorer({DRIVES: "shell:MyComputerFolder", NETWORK: "shell:NetworkPlacesFolder"}.get(v.path, v.path), select=False)

    def refresh_view(self):
        icon_cache().forget_folder_icons()      # pick up folders recolored in Explorer
        self.view().refresh()
        self.view().tree.viewport().update()

    def open_external(self, args: list):
        """Paths from another launch (a folder opened in Windows, Win+E, a second Burrow)."""
        if not args and self.isVisible() and not self.isMinimized():
            self.new_tab()
        for a in args:
            if places.is_unc(a) or os.path.isdir(a):
                self.new_tab(a)
            elif os.path.isfile(a):
                v = self.new_tab(os.path.dirname(a))
                v.select_paths([a], a)
            else:
                self.new_tab(DRIVES)
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def toggle_win_e(self, on: bool):
        self.config.set("win_e", on)
        self.statusBar().showMessage("Win+E now opens Burrow" if on else "Win+E opens Explorer again", 5000)

    # ----- Win+E and background mode -------------------------------------------------------
    def apply_background_switches(self):
        """Win+E hook and tray/background mode follow their Settings switches."""
        if not self.primary:
            return                       # the primary window hosts these
        from .hotkey import WinEHook, set_start_with_windows, starts_with_windows
        if not hasattr(self, "win_e_hook"):
            self.win_e_hook = WinEHook(self)
            self.win_e_hook.pressed.connect(self._win_e_pressed)
        if self.config.get("win_e"):
            if not self.win_e_hook.install():
                self.statusBar().showMessage("Couldn't listen for Win+E", 6000)
        else:
            self.win_e_hook.uninstall()
        bg = bool(self.config.get("run_in_background"))
        if bg != starts_with_windows():
            set_start_with_windows(bg, integration.launch_command())
        if bg and getattr(self, "tray", None) is None:
            from PySide6.QtWidgets import QSystemTrayIcon
            self.tray = QSystemTrayIcon(self.windowIcon(), self)
            self.tray.setToolTip("Burrow")
            menu = QMenu(self)
            menu.addAction("Open Burrow").triggered.connect(lambda: self.bring_to_front())
            menu.addAction("New tab").triggered.connect(lambda: (self.new_tab(), self.bring_to_front()))
            menu.addSeparator()
            menu.addAction("Quit Burrow").triggered.connect(self.quit_for_real)
            self.tray.setContextMenu(menu)
            self.tray.activated.connect(lambda reason: reason == QSystemTrayIcon.ActivationReason.Trigger
                                        and self.bring_to_front())
            self.tray.show()
        elif not bg and getattr(self, "tray", None) is not None:
            self.tray.hide()
            self.tray.deleteLater()
            self.tray = None

    def _win_e_pressed(self):
        visible = [w for w in WINDOWS if w.isVisible() and not w.isMinimized()]
        if self.config.get("win_e_opens") == "tab":
            target = next((w for w in reversed(LAST_ACTIVE) if w in visible), None) or self
            if target.isVisible() and not target.isMinimized():
                target.new_tab()
            target.bring_to_front()
        elif not visible:
            self.bring_to_front()            # nothing on screen: show the (hidden) main window
        else:
            open_new_window(self.config)

    def bring_to_front(self):
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()
        if sys.platform == "win32":
            ctypes.windll.user32.SetForegroundWindow(int(self.winId()))

    def quit_for_real(self):
        self._really_quit = True
        for w in list(WINDOWS):
            if w is not self:
                w.close()
        self.close()

    def apply_feature_switches(self):
        """Push the Settings feature switches into the parts that use them."""
        from . import tags as _tags
        c = self.config
        icon_cache().show_custom = bool(c.get("show_folder_icons"))
        icon_cache().forget_folder_icons()
        _tags.ENABLED = bool(c.get("show_tags"))
        self.inspector.prefs = {k: bool(c.get(k)) for k in (
            "preview_images", "preview_media", "preview_text", "text_syntax_colors", "preview_archives",
            "preview_epub", "preview_folders", "show_hidden")}
        self.sidebar.rebuild_soon()
        for v in self.views():
            v.tree.viewport().update()
        self.inspector_timer.start()

    def _mirror_bookmark(self, path: str, added: bool):
        """Bookmarks are pinned/unpinned in Explorer's Quick Access too (Settings switch)."""
        if not self.config.get("quick_access_sync") or places.is_special(path):
            return
        fn = integration.pin_to_quick_access if added else integration.unpin_from_quick_access
        places.run_async(lambda: fn(path), lambda _res: None)

    def toggle_default_fm(self, on: bool):
        """Turn 'Burrow opens folders and Win+E' on or off (per user; off unless you turn it on)."""
        if on and QMessageBox.question(
                self, "Default file manager",
                "Open folders and drives in Burrow everywhere in Windows (desktop, Explorer, Run, "
                "shortcuts)?\n\nYou can turn this off again from the same menu "
                "or in Settings.") != QMessageBox.StandardButton.Yes:
            self.act_default_fm.setChecked(False)
            return
        err = integration.set_default(on)
        if err:
            QMessageBox.warning(self, "Default file manager", f"Couldn't change the setting: {err}")
        state = integration.is_default()
        self.act_default_fm.setChecked(state)
        self.statusBar().showMessage(
            "Folders now open in Burrow" if state else "Folders open in Explorer again", 5000)

    def map_drive(self):
        places.map_drive_dialog(int(self.winId()))
        self.sidebar.rebuild()
        if self.view().path in (DRIVES, NETWORK):
            self.view().refresh()

    def disconnect_drive(self, letter=None):
        if letter:
            if QMessageBox.question(self, APP_NAME, f"Disconnect network drive {letter}?") \
                    != QMessageBox.StandardButton.Yes:
                return
            if not places.disconnect(letter):
                QMessageBox.warning(self, APP_NAME, f"Couldn't disconnect {letter}. Close any files open on it and try again.")
        else:
            places.disconnect_dialog(int(self.winId()))
        self.sidebar.rebuild()
        if self.view().path in (DRIVES, NETWORK):
            self.view().refresh()

    # ----- updates and tools (each loads its module on first use) ----------------------
    def check_updates(self):
        from . import updater
        updater.UpdateDialog(self).exec()

    def show_update_available(self, info: dict):
        for w in WINDOWS:
            w.update_btn.setText(f"Update to {info['version']}")
            w.update_btn.setToolTip("A new version of Burrow is out. Click to see what's new.")
            if getattr(w, "_update_info", None) is None:
                w.update_btn.clicked.connect(lambda _=False, w=w: w._open_update(w._update_info))
            w._update_info = info
            w.update_btn.setVisible(bool(self.config.get("status_update_button")))

    def _open_update(self, info):
        from . import updater
        updater.UpdateDialog(self, info, start=True).exec()     # straight to the download
        if self.config.get("update_skip") == info["version"]:
            for w in WINDOWS:
                w.update_btn.hide()

    def _update_btn_menu(self, pos):
        m = QMenu(self)
        m.addAction("Hide this button").triggered.connect(self._hide_update_btn)
        m.exec(self.update_btn.mapToGlobal(pos))

    def _hide_update_btn(self):
        self.config.set("status_update_button", False)       # back on in Settings ▸ Sidebar & list
        self.statusBar().showMessage("Update button hidden. Settings ▸ Sidebar & list brings it back.", 6000)

    def _sync_auto_update_icon(self):
        on = self.auto_update_chk.isChecked()
        ic = theme.icon("auto_update", 16)
        if not on:                                     # off: the same icon, faded
            from PySide6.QtGui import QIcon
            ic = QIcon(ic.pixmap(16, 16, QIcon.Mode.Disabled))
        self.auto_update_chk.setIcon(ic)
        self.auto_update_chk.setToolTip("Auto update is on: Burrow checks GitHub for a new version every time it "
                                        "starts. Click to turn off." if on else
                                        "Auto update is off. Click to have Burrow check GitHub for a new version "
                                        "every time it starts.")

    def _auto_update_toggled(self, on: bool):
        self.config.set("check_updates", on)
        if on:
            from . import updater
            updater.auto_check(self)                         # look right away

    def _tool_paths(self):
        """Folders for the tools: the open folders of each pane (left/right for Compare)."""
        out = []
        for p in self.panes:
            v = p.tabs.currentWidget()
            if v is not None and v.path and not places.is_special(v.path) and v.path not in out:
                out.append(v.path)
        cur = self.view().path if self.view() else None
        if cur in out:
            out.remove(cur)
            out.insert(0, cur)
        return out

    def open_compare(self):
        from .tools.compare import CompareWindow
        paths = self._tool_paths()
        CompareWindow.open(self.config, paths[0] if paths else "", paths[1] if len(paths) > 1 else "", self)

    def open_duplicates(self):
        from .tools.duplicates import DuplicatesWindow
        sel = [e.path for e in self._selected() if e.is_dir]
        DuplicatesWindow.open(self.config, sel or self._tool_paths()[:1], self)

    def folderize(self):
        """Each loose file into a folder named after it; files sharing a name (a movie and its
        subtitles) share the folder. Works on the selected files, or every file here."""
        v = self.view()
        if v is None or not v.path or is_location(v.path):
            self.statusBar().showMessage("Open a folder first", 4000)
            return
        sel = [e.path for e in self._selected() if not e.is_dir]
        paths = sel or [e.path for e in v.model.entries if not e.is_dir and not e.is_drive]
        from .tools import folderize
        groups = folderize.plan(paths)
        if not groups:
            self.statusBar().showMessage("No loose files here", 4000)
            return
        if not folderize.FolderizeDialog(v.path, groups, self).exec():
            return
        errors = folderize.apply(v.path, groups)
        self.refresh_view()
        if errors:
            QMessageBox.warning(self, APP_NAME, f"{len(errors)} file(s) couldn't be moved:\n\n" + "\n".join(errors[:12]))
        else:
            self.statusBar().showMessage(f"Made {len(groups)} folder(s)", 5000)

    def open_bulk_rename(self):
        sel = self._selected()
        if len(sel) < 1:
            self.statusBar().showMessage("Select the files to rename first", 4000)
            return
        from .tools.bulkrename import BulkRenameDialog
        if BulkRenameDialog(self.config, [e.path for e in sel], self).exec():
            self.refresh_view()

    def eject_drive(self, root: str, kind: str = "drive"):
        """Eject button / menu: move every tab off the drive first (an open folder or a
        previewed file keeps it busy), then safely remove it on a worker thread."""
        letter = root[:2].upper()
        if kind == "netdrive":
            self.disconnect_drive(letter)
            return
        name = next((d.name for d in list_drives()
                     if d.path[:2].upper() == letter), letter)
        for w in WINDOWS:
            for v in w.views():
                if v.path and v.path[:2].upper() == letter:
                    v.navigate(DRIVES)
            shown = getattr(w.inspector, "_shown", None)
            if shown is not None:
                w.inspector._reset()
                w.inspector.media._release()
        self.statusBar().showMessage(f"Ejecting {name}…")
        from . import eject

        def done(res):
            if isinstance(res, Exception):
                res = str(res)
            if res:
                self.statusBar().clearMessage()
                QMessageBox.warning(self, APP_NAME, f"Couldn't eject {name}.\n\n{res}")
            else:
                self.statusBar().showMessage(f"{name} can be safely removed.", 6000)
            _drives_changed()
        places.run_async(lambda: eject.eject(letter), done)

    def open_settings(self):
        dialogs.SettingsDialog(self.config, self).exec()

    def showEvent(self, ev):
        super().showEvent(ev)
        self._native_backdrop()
        if getattr(self, "_trimmed", False):    # back from the tray: refill the dropped preview
            self._trimmed = False
            self.inspector_timer.start()
        if not self._docks_sized:              # first run: sensible panel widths (needs a shown window)
            self._docks_sized = True
            self.resizeDocks([self.side_dock, self.insp_dock], [230, 320], Qt.Orientation.Horizontal)

    # ----- shutdown --------------------------------------------------------------------------
    def changeEvent(self, ev):
        super().changeEvent(ev)
        if ev.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self.sidebar._check_drives()       # cheap; catches anything missed while away
            QTimer.singleShot(3000, self, lambda: maybe_check_updates(self))
            if self in LAST_ACTIVE:
                LAST_ACTIVE.remove(self)
            LAST_ACTIVE.append(self)
        if ev.type() == QEvent.Type.WindowStateChange and self.isMinimized():
            self.inspector.media.pause()

    def closeEvent(self, ev):
        """Closing: hide to the tray when Burrow runs in the background, else save and quit."""
        others = [w for w in WINDOWS if w is not self and w.isVisible()]
        if not self.primary:
            # an extra window: just close it; the last one standing ends the app (unless in tray)
            if self in WINDOWS:
                WINDOWS.remove(self)
            self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            super().closeEvent(ev)
            if not others and not self.config.get("run_in_background") and WINDOWS:
                WINDOWS[0].quit_for_real()
            return
        if others and not getattr(self, "_really_quit", False):
            self._save_session()             # the primary window hosts Win+E / tray: hide it
            self.hide()
            schedule_trim()
            ev.ignore()
            return
        if self.config.get("run_in_background") and not getattr(self, "_really_quit", False):
            self.hide()                    # keep running in the tray (Win+E, quick reopen)
            schedule_trim()
            ev.ignore()
            self._save_session()
            return
        if self.jobs:
            if QMessageBox.question(self, APP_NAME, "A copy is still running. Cancel it and quit?") \
                    != QMessageBox.StandardButton.Yes:
                ev.ignore()
                return
            for j in list(self.jobs):
                j.cancel_and_wait()
        self._save_session()
        if getattr(self, "win_e_hook", None) is not None:
            self.win_e_hook.uninstall()
        super().closeEvent(ev)
        QApplication.instance().quit()

    def _saved_layout(self):
        c = self.config
        lay = c.get("pane_layout")
        if isinstance(lay, dict) and lay.get("c"):
            return lay
        second = c.get("session_tabs2")                    # Burrow 0.12: at most two panes
        if second and c.get("split") in ("h", "v"):
            return {"o": c.get("split"), "c": [{"tabs": c.get("session_tabs"), "cur": c.get("session_current")},
                                              {"tabs": second, "cur": 0}]}
        return None

    def _save_session(self):
        p0 = self.panes[0]
        self.config.data["session_tabs"] = [p0.tabs.widget(i).path for i in range(p0.tabs.count())]
        self.config.data["session_current"] = p0.tabs.currentIndex()
        self.config.data["session_tabs2"] = []
        self.config.data["pane_layout"] = self._layout_of(self.splitter) if len(self.panes) > 1 else None
        self.config.data["window_geometry"] = bytes(self.saveGeometry().toHex().data()).decode()
        self.config.data["window_state"] = bytes(self.saveState().toHex().data()).decode()
        self.config.save()


def _crash_log():
    """Keep the reason if Burrow ever crashes or hits an error: %APPDATA%\\Burrow\\crash.log
    (the Python stack at a hard crash, and the traceback of any uncaught error, which Burrow
    survives). Kept under 256 KB."""
    import faulthandler
    import traceback
    from .config import CONFIG_DIR
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        path = os.path.join(CONFIG_DIR, "crash.log")
        if os.path.exists(path) and os.path.getsize(path) > 256 * 1024:
            os.replace(path, path + ".old")
        f = open(path, "a", encoding="utf-8", buffering=1)
    except OSError:
        return
    faulthandler.enable(f)
    globals()["_CRASH_FILE"] = f                    # keep it open for faulthandler

    def hook(kind, value, tb):
        import datetime
        f.write(f"\n--- {datetime.datetime.now():%Y-%m-%d %H:%M:%S} Burrow {__import__('burrow').__version__}\n")
        f.write("".join(traceback.format_exception(kind, value, tb)))
        sys.__excepthook__(kind, value, tb)
    sys.excepthook = hook


def main():
    """Start Burrow: hand off to a running copy if there is one, else open the window, tray and watchers."""
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Burrow.FileManager")
        except (AttributeError, OSError):
            pass
    _crash_log()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    icon_path = os.path.join(base, "assets", "burrow.ico")
    if os.path.isfile(icon_path):
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(icon_path))
    args = [a for a in sys.argv[1:] if a and not a.startswith("-")]
    # Already running? Hand over the paths (opened as tabs there) and quit.
    config = Config()
    if config.get("single_instance") and integration.send_to_running([os.path.abspath(a) for a in args]):
        return 0
    theme.apply(app, config.get("theme"), config.get("palette"))
    fileops.cleanup_temp()
    paths = [normalize_path(a) for a in args if os.path.isdir(a)]
    win = MainWindow(config, paths)
    if config.get("single_instance"):
        server = integration.InstanceServer(win)
        server.received.connect(route_external)
    for a in args:                        # a file on the command line: open its folder, select it
        if os.path.isfile(a):
            win.open_external([os.path.abspath(a)])
    if "--background" not in sys.argv or not config.get("run_in_background"):
        win.show()
    else:
        schedule_trim()                    # started hidden in the tray
    app.setQuitOnLastWindowClosed(not config.get("run_in_background"))
    from .devwatch import DeviceWatcher
    app._devwatch = DeviceWatcher(app)
    app._devwatch.changed.connect(_drives_changed)
    QTimer.singleShot(5000, lambda: _startup_chores(win))
    return app.exec()
