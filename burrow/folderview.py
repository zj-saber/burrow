"""One tab: a folder listing with history, type-to-filter, drag and drop, and the shared layout."""
from __future__ import annotations

import os
import re

from PySide6.QtCore import QFileSystemWatcher, QItemSelection, QItemSelectionModel, QPointF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPalette
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QHeaderView, QLabel, QLineEdit, QListView, QMenu,
                               QStyle, QStyledItemDelegate, QTreeView, QVBoxLayout, QWidget)

from . import dialogs, everything, fileops, marks, places, tags, theme
from .config import COLUMN_TITLES, COLUMNS, DEFAULT_WIDTHS, Config
from .model import DRIVES, ENTRY_ROLE, NETWORK, DirModel, Entry, FilterProxy


_HOVER = QStyle.StateFlag.State_MouseOver
_SELECTED = QStyle.StateFlag.State_Selected
_FOCUS = QStyle.StateFlag.State_HasFocus


def normalize_path(p: str) -> str:
    if places.is_special(p):
        return p
    p = os.path.expandvars(os.path.expanduser(p.strip().strip('"'))).replace("/", "\\")
    if re.fullmatch(r"[A-Za-z]:", p):
        p += "\\"
    m = re.fullmatch(r"\\\\([^\\]+)\\?", p)
    if m:
        return "\\\\" + m.group(1)                 # a server: \\host
    return os.path.normpath(os.path.abspath(p))


def display_path(p: str) -> str:
    if places.is_tag(p):
        return f"Tag: {tags.TAG_NAMES.get(p[len(places.TAG_PREFIX):], '?')}"
    return {DRIVES: "This PC", NETWORK: "Network"}.get(p, p)


def title_for(p: str) -> str:
    if places.is_special(p):
        return display_path(p)
    if places.is_server(p):
        return p[2:]
    return os.path.basename(p.rstrip("\\")) or p


def _exists(p: str) -> bool:
    """Existence check that never waits on the network: remote paths are checked by the
    background load instead."""
    return places.is_special(p) or places.is_remote(p) or os.path.isdir(p)


class RowDelegate(QStyledItemDelegate):
    """Taller rows, a subtle hover fill, and no dotted focus rectangle. Kept tiny on purpose:
    it runs for every visible cell on every repaint."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.row_height = 26
        self.hover = QColor(0, 0, 0, 0)
        self.size_col = None       # This PC: the Size column also shows how full each drive is

    def sizeHint(self, option, index):
        s = super().sizeHint(option, index)
        if s.height() < self.row_height:
            s.setHeight(self.row_height)
        return s

    def paint(self, painter, option, index):
        state = option.state
        if state & _FOCUS:
            option.state = state & ~_FOCUS
        if self.size_col is not None and index.column() == self.size_col:
            e = index.data(ENTRY_ROLE)
            if e is not None and e.size and e.free is not None:
                # This PC: a pill instead of the total (Type already says "X free of Y")
                from PySide6.QtCore import QRectF
                from PySide6.QtWidgets import QApplication, QStyleOptionViewItem
                opt = QStyleOptionViewItem(option)
                self.initStyleOption(opt, index)
                opt.text = ""
                QApplication.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
                r = option.rect
                w = min(r.width() - 16, 120)
                if w >= 16:
                    theme.paint_usage(painter, QRectF(r.right() - 8 - w, r.center().y() - 1.5, w, 4),
                                      1 - e.free / e.size)
                return
        super().paint(painter, option, index)
        if marks.MARKS:                        # copied / cut / being pasted: a faint tint
            e = index.data(ENTRY_ROLE)
            k = marks.MARKS.get(e.path.lower()) if e is not None else None
            if k:
                painter.fillRect(option.rect, marks.color(k))


class NameDelegate(RowDelegate):
    """Inline rename that pre-selects the name without its extension, like Explorer."""

    def setEditorData(self, editor, index):
        """Start renaming with the name selected but not the extension, like Explorer."""
        super().setEditorData(editor, index)
        if isinstance(editor, QLineEdit):
            e = index.data(ENTRY_ROLE)
            text = editor.text()
            n = len(text)
            if e is not None and not e.is_dir and "." in text[1:]:
                n = text.rfind(".")

            def select():
                try:
                    editor.setSelection(0, n)
                except RuntimeError:
                    pass
            QTimer.singleShot(0, select)

    def paint(self, painter, option, index):
        """Name cell, plus colored tag dots at its right end. With no tags anywhere this is a
        single empty-dict check; with tags, one dict lookup per visible name."""
        store = tags.store()
        if not tags.ENABLED or not store.items:
            return super().paint(painter, option, index)
        e = index.data(ENTRY_ROLE)
        ids = store.get(e.path) if e is not None else []
        if not ids:
            return super().paint(painter, option, index)
        r = option.rect
        dots_w = 10 * len(ids) + 8
        # fill the dots area like the rest of the row (selection / hover)
        state = option.state
        if state & _SELECTED:
            group = QPalette.ColorGroup.Active if state & QStyle.StateFlag.State_Active else QPalette.ColorGroup.Inactive
            painter.fillRect(r.adjusted(r.width() - dots_w, 0, 0, 0), option.palette.color(group, QPalette.ColorRole.Highlight))
        option.rect = r.adjusted(0, 0, -dots_w, 0)
        super().paint(painter, option, index)
        option.rect = r
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        ring = option.palette.color(QPalette.ColorRole.Base)
        cy = r.center().y() + 1
        x = r.right() - dots_w + 8
        for tid in ids:
            painter.setPen(ring)
            painter.setBrush(QColor(tags.TAG_COLORS[tid]))
            painter.drawEllipse(QPointF(x + 4, cy), 4.5, 4.5)
            x += 10
        painter.restore()


class FilterEdit(QLineEdit):
    leave = Signal()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key.Key_Escape:
            self.clear()
            self.leave.emit()
            return
        if ev.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.leave.emit()
            return
        super().keyPressEvent(ev)


class FileTree(QTreeView):
    """The file list: type-to-filter, keyboard handling and drag and drop."""
    dropRequested = Signal(list, str, bool)   # sources, target folder, move?

    def __init__(self, owner: "FolderView"):
        super().__init__(owner)
        self.owner = owner
        self.hover_color = QColor(0, 0, 0, 0)       # set by the theme and the Settings slider
        self._hover_row = -1

    def drawRow(self, painter, option, index):
        """The hovered row gets one rounded band across all its columns, then the row on top."""
        if index.row() == self._hover_row and not self.selectionModel().isSelected(index):
            theme.paint_hover(painter, option.rect, self.hover_color)
        super().drawRow(painter, option, index)

    def _set_hover(self, row: int):
        if row == self._hover_row:
            return
        old, self._hover_row = self._hover_row, row
        vp = self.viewport()
        for r in (old, row):
            if r >= 0:
                rect = self.visualRect(self.model().index(r, 0))
                vp.update(0, rect.top(), vp.width(), rect.height())

    def mouseMoveEvent(self, ev):
        idx = self.indexAt(ev.position().toPoint())
        self._set_hover(idx.row() if idx.isValid() else -1)
        super().mouseMoveEvent(ev)

    def leaveEvent(self, ev):
        self._set_hover(-1)
        super().leaveEvent(ev)

    def wheelEvent(self, ev):
        if (ev.modifiers() & Qt.KeyboardModifier.ControlModifier and self.owner.icon_wheel_handler is not None
                and ev.angleDelta().y()):
            self.owner.icon_wheel_handler(1 if ev.angleDelta().y() > 0 else -1)
            return
        super().wheelEvent(ev)

    def is_editing(self) -> bool:
        return self.state() == QAbstractItemView.State.EditingState

    def keyPressEvent(self, ev):
        """Typing filters, Backspace goes up, ← can go to the sidebar, Space/arrows control media."""
        mods = ev.modifiers() & ~(Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.KeypadModifier)
        text = ev.text()
        if (ev.key() == Qt.Key.Key_Space and not self.owner.filter_text() and mods == Qt.KeyboardModifier.NoModifier
                and self.owner.space_handler is not None and self.owner.space_handler()):
            return                                  # Space played/paused the media preview
        if (ev.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right) and self.owner.seek_handler is not None
                and not mods & ~Qt.KeyboardModifier.ShiftModifier
                and self.owner.seek_handler(1 if ev.key() == Qt.Key.Key_Right else -1,
                                            bool(ev.modifiers() & Qt.KeyboardModifier.ShiftModifier))):
            return                                  # arrows seek the previewed video/audio
        if (ev.key() == Qt.Key.Key_Left and mods == Qt.KeyboardModifier.NoModifier
                and not ev.modifiers() & Qt.KeyboardModifier.ShiftModifier
                and self.owner.sidebar_handler is not None and self.owner.sidebar_handler()):
            return                                  # ← goes to the sidebar
        if ev.key() == Qt.Key.Key_Escape and self.owner.filter_text():
            self.owner.clear_filter()
            return
        if (text and text.isprintable() and mods == Qt.KeyboardModifier.NoModifier
                and not (text == " " and not self.owner.filter_text())):
            self.owner.type_into_filter(text)
            return
        super().keyPressEvent(ev)

    def mousePressEvent(self, ev):
        """Back / forward mouse buttons navigate."""
        b = ev.button()
        if b == Qt.MouseButton.BackButton:
            self.owner.go_back()
            return
        if b == Qt.MouseButton.ForwardButton:
            self.owner.go_forward()
            return
        if b == Qt.MouseButton.MiddleButton:
            e = self.owner.proxy.entry(self.indexAt(ev.position().toPoint()))
            if e is not None and e.is_dir:
                self.owner.openInNewTab.emit(e.path)
            return
        super().mousePressEvent(ev)

    # ----- drag and drop ---------------------------------------------------------
    @staticmethod
    def _paths(md):
        if not md.hasUrls():
            return []
        return [os.path.normpath(u.toLocalFile()) for u in md.urls() if u.isLocalFile()]

    def _target(self, pos):
        e = self.owner.proxy.entry(self.indexAt(pos))
        if e is not None and e.is_dir:
            return e.path
        p = self.owner.path
        return None if (places.is_special(p) or places.is_server(p)) else p

    def _evaluate(self, ev):
        paths = self._paths(ev.mimeData())
        target = self._target(ev.position().toPoint())
        if not paths or not target or any(fileops.same_path(p, target) for p in paths):
            return None
        return paths, target, fileops.decide_move(ev.modifiers(), paths, target)

    def dragEnterEvent(self, ev):
        if self._paths(ev.mimeData()):
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dragMoveEvent(self, ev):
        r = self._evaluate(ev)
        if r is None:
            ev.ignore()
            return
        ev.setDropAction(Qt.DropAction.MoveAction if r[2] else Qt.DropAction.CopyAction)
        ev.accept()

    def dropEvent(self, ev):
        r = self._evaluate(ev)
        if r is None:
            ev.ignore()
            return
        # Report a copy to the drag source even for moves: we do the move ourselves, and a
        # "move" result could make the source (e.g. Explorer) delete the originals too.
        ev.setDropAction(Qt.DropAction.CopyAction)
        ev.accept()
        paths, target, move = r
        QTimer.singleShot(0, lambda: self.dropRequested.emit(paths, target, move))


class FolderView(QWidget):
    """One tab: a folder listing with history, filter, layout, columns view and network handling."""
    pathChanged = Signal(str)
    loaded = Signal()
    selectionChanged = Signal()
    openInNewTab = Signal(str)
    statusMessage = Signal(str)
    contextMenuRequested = Signal(object)

    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self.path: str | None = None
        self.back_stack: list[str] = []
        self.fwd_stack: list[str] = []
        self._applying = False
        self.applied_version = -1
        self._load_token = 0
        self.space_handler = None          # set by the window: Space plays media previews
        self.seek_handler = None           # set by the window: Left/Right seek media previews
        self.sidebar_handler = None        # set by the window: ← moves focus to the sidebar
        self.icon_wheel_handler = None     # set by the window: Ctrl+wheel changes icon size
        self.loading = False
        self.mode = config.get("view_mode") if config.get("view_mode") in ("list", "columns", "thumbs", "river") else "list"
        self.columns = None                # built the first time this tab shows columns
        self.thumbs = None                 # Thumbnails view, built on first use
        self.river = None                  # River view (photos in flowing rows), built on first use
        self.results = None                # columns view: the search results column
        self._from_columns = False
        self._searched = False             # the list shows Everything results, not the folder
        self._search_token = 0
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(150)   # one query per pause in typing
        self._search_timer.timeout.connect(self._run_search)

        self.model = DirModel(self)
        self.model.date_format = config.get("date_format")
        self.model.renameFailed.connect(self._rename_failed)
        self.proxy = FilterProxy(self)
        self.proxy.setSourceModel(self.model)

        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #c0392b; padding: 6px;")
        self.error_label.hide()

        t = self.tree = FileTree(self)
        t.setModel(self.proxy)
        t.setRootIsDecorated(False)
        t.setItemsExpandable(False)
        t.setUniformRowHeights(True)
        t.setAllColumnsShowFocus(True)
        t.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setEditTriggers(QAbstractItemView.EditTrigger.EditKeyPressed | QAbstractItemView.EditTrigger.SelectedClicked)
        self.row_delegate = RowDelegate(t)
        self.name_delegate = NameDelegate(t)
        t.setItemDelegate(self.row_delegate)
        t.setItemDelegateForColumn(0, self.name_delegate)
        t.setFrameShape(QFrame.Shape.NoFrame)
        t.setMouseTracking(True)
        t.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        t.setDragEnabled(True)
        t.setAcceptDrops(True)
        t.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        t.setDefaultDropAction(Qt.DropAction.CopyAction)
        t.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        t.setSortingEnabled(True)
        t.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        t.customContextMenuRequested.connect(lambda pos: self.contextMenuRequested.emit(pos))
        t.activated.connect(self._activated)
        t.selectionModel().selectionChanged.connect(lambda *_: self.selectionChanged.emit())

        h = t.header()
        h.setSectionsMovable(True)
        h.setFirstSectionMovable(True)
        h.setStretchLastSection(False)
        h.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        h.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        h.customContextMenuRequested.connect(self._header_menu)
        h.sortIndicatorChanged.connect(lambda *_: self._layout_edited())
        h.sectionResized.connect(lambda *_: self._layout_edited())
        h.sectionMoved.connect(lambda *_: self._layout_edited())

        self.filter_edit = FilterEdit()
        self.filter_edit.setPlaceholderText("Search (just start typing) — Esc clears")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._filter_changed)
        self.filter_edit.leave.connect(self.focus_list)
        self.filter_edit.hide()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.error_label)
        lay.addWidget(self.tree, 1)
        lay.addWidget(self.filter_edit)
        self._lay = lay

        self.watcher = QFileSystemWatcher(self)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(400)
        self._refresh_timer.timeout.connect(self._auto_refresh)
        self.watcher.directoryChanged.connect(self._dir_changed)

        config.viewChanged.connect(self._on_view_changed)
        config.folderLayoutChanged.connect(self._on_folder_layout_changed)
        config.settingsChanged.connect(self._settings_changed)
        self.own_layout = False          # this folder has a layout of its own
        self._auto_fit = bool(config.get("auto_fit_columns"))
        self.apply_layout()
        self.apply_theme(theme.colors())

    def apply_theme(self, c: dict):
        """Style sheets go on the header and filter box only, never on the list itself."""
        self.tree.header().setStyleSheet(theme.header_qss(c))
        self.filter_edit.setStyleSheet(theme.field_qss(c))
        self.model.meta_color = QColor(c["dim"])
        self.tree.hover_color = theme.hover_color(c, int(self.config.get("hover_strength")))
        if self.columns is not None:
            self.columns.set_hover_color(self.tree.hover_color)
        for v in self.item_views()[1:]:
            v.hover_color = self.tree.hover_color
            v.viewport().update()
        self.tree.viewport().update()
        self.set_icon_size(self.config.get("icon_size") or 16)

    def row_height(self) -> int:
        return max(24, self.fontMetrics().height() + 10, self.icon_px + 8)

    def set_icon_size(self, px: int):
        """Icon size for the list and the columns (the slider in the status bar)."""
        self.icon_px = px = max(16, min(64, int(px)))
        self.tree.setIconSize(QSize(px, px))
        for d in (self.row_delegate, self.name_delegate):
            d.row_height = self.row_height()
        self.tree.doItemsLayout()               # uniform row heights: re-measure the first row
        if self.columns is not None:
            self.columns.set_icon_size(px, self.row_height())
        for v in (self.thumbs, self.river):
            if v is not None:
                v.set_size(px)
        if self.results is not None:
            self.results.setIconSize(QSize(px, px))
            self.results.row_h = self.row_height()

    # ----- navigation -------------------------------------------------------------
    def navigate(self, path: str, record: bool = True, select: str | None = None) -> bool:
        """Open a folder in this tab (network drives reconnect and load in the background)."""
        path = normalize_path(path)
        reconnect = places.needs_reconnect(path)   # a remembered network drive, not connected now
        if not _exists(path):
            self.statusMessage.emit(f"Folder not found: {path}")
            return False
        self._flush_layout()                   # a layout change still to save belongs to this folder
        if record and self.path is not None and path != self.path:
            self.back_stack.append(self.path)
            self.fwd_stack.clear()
        self.path = path
        self._searched = False
        self._search_timer.stop()
        self.filter_edit.blockSignals(True)
        self.filter_edit.clear()
        self.filter_edit.hide()
        self.filter_edit.blockSignals(False)
        self.proxy.set_filter("")
        if self.watcher.directories():
            self.watcher.removePaths(self.watcher.directories())
        self._retry_delay = 0
        if reconnect:
            self._reconnect_then_load(select)
        else:
            self._load([select] if select else [], select, clear=True)
        self._sync_own_layout()
        self._sync_drive_bars()
        self._sync_mode_widgets(select)
        self.pathChanged.emit(path)
        return True

    # ----- network drives: never freeze, recover by themselves ----------------------------
    def _reconnect_then_load(self, select):
        """Reconnect a remembered drive on a worker thread (Windows may ask for a password),
        then load. Done inline this froze the window for up to a minute on a slow server."""
        path, token = self.path, self._load_token + 1
        self._load_token = token
        self.model.set_entries(path, [])
        self.loading = True
        self._set_message(f"Reconnecting {path[:2]}…", error=False)
        hwnd = int(self.window().winId())

        def done(ok):
            if token != self._load_token or path != self.path:
                return
            if ok is True:
                self._load([select] if select else [], select, clear=True)
            else:
                self._loaded(token, ([], "the server isn't answering"), [], None, None)
        places.run_async(lambda: places.reconnect(path[:2], hwnd), done)

    def _network_state(self, err):
        """After a network folder loaded (or failed): retry failures with growing pauses, and
        keep a working connection from idling out."""
        if not hasattr(self, "_retry_timer"):
            self._retry_timer = QTimer(self)
            self._retry_timer.setSingleShot(True)
            self._retry_timer.timeout.connect(self._retry_network)
            self._alive_timer = QTimer(self)
            self._alive_timer.setInterval(4 * 60 * 1000)   # Windows drops idle connections after 10 min
            self._alive_timer.timeout.connect(self._keep_alive)
        remote = places.is_remote(self.path) or places.is_server(self.path)
        if err and remote:
            self._alive_timer.stop()
            self._retry_delay = min(max(self._retry_delay * 2, 2), 30)
            self._set_message(f"Can't reach {display_path(self.path)}: {err}. "
                              f"Trying again in {self._retry_delay} s…  (F5 tries now)")
            self._retry_timer.start(self._retry_delay * 1000)
            return
        self._retry_timer.stop()
        self._retry_delay = 0
        if remote and self.config.get("network_keepalive"):
            self._alive_timer.start()
        else:
            self._alive_timer.stop()

    def _retry_network(self):
        if not self.isVisible():
            self._retry_on_show = True        # a hidden tab tries again when it's shown
            return
        self.refresh()

    def _keep_alive(self):
        """While a network folder is on screen, touch it every few minutes on a worker thread:
        the connection stays up, so coming back doesn't wait for Windows to reconnect. A failed
        touch reloads the folder, which starts the retries."""
        if not self.isVisible() or not places.is_remote(self.path or ""):
            return
        path = self.path
        places.run_async(lambda: os.stat(path), lambda res: self.refresh()
                         if isinstance(res, Exception) and path == self.path else None)

    def set_drive_bars(self, on: bool):
        self.drive_bars = on
        self._sync_drive_bars()

    def _sync_drive_bars(self):
        col = COLUMNS.index("size") if (self.path == DRIVES and getattr(self, "drive_bars", True)) else None
        if col != self.row_delegate.size_col:
            self.row_delegate.size_col = col
            self.tree.viewport().update()

    # ----- columns view (optional, per tab) ------------------------------------------
    def columns_active(self) -> bool:
        """Columns on screen and in charge (not while its search results are showing)."""
        return self.mode == "columns" and self.columns is not None and self.columns.isVisible() \
            and not self.results_on()

    def results_on(self) -> bool:
        return self.results is not None and self.results.isVisible()

    def item_view(self):
        """The view showing this tab's list now: the list, Thumbnails, River or the columns'
        results column. (Columns themselves have their own model: see columns_active.)"""
        if self.results_on():
            return self.results
        for v in (self.thumbs, self.river):
            if v is not None and v.isVisible():
                return v
        return self.tree

    def item_views(self) -> list:
        return [v for v in (self.tree, self.thumbs, self.river, self.results) if v is not None]

    def _make_thumb_view(self, river: bool):
        from .thumbs import ThumbView
        v = ThumbView(self, river, self)
        v.hover_color = self.tree.hover_color
        v.set_size(self.icon_px)
        self._lay.insertWidget(2, v, 1)
        return v

    def set_mode(self, mode: str):
        if mode == self.mode:
            return
        self.mode = mode
        self._sync_mode_widgets()
        self.focus_list()
        self.selectionChanged.emit()

    def _sync_mode_widgets(self, select: str | None = None):
        """Show the tab's view: list, columns, thumbnails or river. Virtual places (This PC,
        Network, tags) always show as a list."""
        mode = self.mode if (self.path is not None and not places.is_special(self.path)) else "list"
        want = mode == "columns"
        if mode == "thumbs" and self.thumbs is None:
            self.thumbs = self._make_thumb_view(False)
        if mode == "river" and self.river is None:
            self.river = self._make_thumb_view(True)
        if mode in ("thumbs", "river"):
            from .thumbs import loader
            loader().drop_queue()                    # pictures for the folder on screen first
        if want and self.columns is None:
            from .columns import ColumnsView
            c = self.columns = ColumnsView(bool(self.config.get("show_hidden")), self.row_height(), self)
            c.set_icon_size(self.icon_px, self.row_height())
            c.folderChosen.connect(self._column_folder)
            c.fileChosen.connect(self._column_file)
            c.openRequested.connect(lambda paths: self.open_entries(DirModel.fetch_paths(paths)))
            c.contextMenu.connect(lambda pos: self.contextMenuRequested.emit(pos))
            c.selectionModel().selectionChanged.connect(lambda *_: self.selectionChanged.emit())
            c.extra_actions = list(self.tree.actions())   # Ctrl+C, Del, F2... work in columns too
            c.set_show_sizes(bool(self.config.get("columns_sizes")), bool(self.config.get("everything_sizes")))
            c.set_hover_color(self.tree.hover_color)
            c.search_handler = self._column_typed
            # the rest of what the list does: Space plays media, Ctrl+wheel icon size, mouse
            # back/forward/middle, drops, rename errors. (← stays in the columns, never the sidebar.)
            c.space_handler = lambda: self.space_handler is not None and self.space_handler()
            c.icon_wheel_handler = lambda d: self.icon_wheel_handler is not None and self.icon_wheel_handler(d)
            c.backRequested.connect(self.go_back)
            c.forwardRequested.connect(self.go_forward)
            c.newTabRequested.connect(self.openInNewTab.emit)
            c.dropRequested.connect(self.tree.dropRequested.emit)
            c.renameFailed.connect(self._rename_failed)
            c.horizontalScrollBar().valueChanged.connect(
                lambda *_: QTimer.singleShot(0, self._sync_results_column))   # after the columns moved
            self._lay.insertWidget(2, c, 1)
        if self.columns is not None:
            self.columns.setVisible(want)
        for v, m in ((self.thumbs, "thumbs"), (self.river, "river")):
            if v is not None:
                v.setVisible(mode == m)
        self.tree.setVisible(mode == "list")
        if want and not self._from_columns:
            self.columns.show_path(self.path, select)
        self._sync_results_column()

    def _sync_results_column(self):
        """Columns view while searching: the results take the searched folder's column and
        everything to its right; the columns to the left stay."""
        on = self.columns is not None and self.columns.isVisible() and bool(self.filter_text())
        if on and self.results is None:
            from .thumbs import ResultsColumn
            # a child of the columns view itself, not of its viewport: scrolling the columns
            # would carry it along with them
            self.results = ResultsColumn(self, self.columns)
            self.results.hover_color = self.tree.hover_color
            self.results.setIconSize(QSize(self.icon_px, self.icon_px))
            self.results.row_h = self.row_height()
        if self.results is None:
            return
        if not on:
            self.results.hide()
            return
        c = self.columns
        vp = c.viewport()
        # the searched folder's own column (Qt may have it hidden for a moment), else the
        # slot right after the column you're in
        root = c.fs.index(self.path)
        col = next((k for k in c.findChildren(QListView) if k.parent() is vp and k.rootIndex() == root), None)
        if col is not None:
            x = col.x()
        else:
            cur = c.column_for(c.currentIndex().parent())
            x = cur.x() + cur.width() if cur is not None else 0
        x = max(0, min(x, vp.width() - 320))
        self.results.setGeometry(vp.x() + x, vp.y(), vp.width() - x, vp.height())
        self.results.show()
        self.results.raise_()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self.results_on():
            self._sync_results_column()

    def _column_folder(self, path: str):
        self._from_columns = True
        try:
            if path != self.path:
                self.navigate(path)
        finally:
            self._from_columns = False
        self.selectionChanged.emit()

    def _column_file(self, path: str):
        parent = os.path.dirname(path)
        self._from_columns = True
        try:
            if parent and os.path.normcase(parent) != os.path.normcase(self.path or ""):
                self.navigate(parent)
        finally:
            self._from_columns = False
        self.selectionChanged.emit()

    def focus_list(self):
        (self.columns if self.columns_active() else self.item_view()).setFocus()

    def select_all(self):
        if self.columns_active():
            self.columns.select_all_in_column()
        else:
            self.tree.selectAll()               # one selection shared by every view

    def menu_pos(self, pos):
        """Global position for a context menu requested at `pos`, and whether it's on an item."""
        if self.columns_active():
            from PySide6.QtGui import QCursor
            return QCursor.pos(), bool(self.columns.selected_paths())
        v = self.item_view()
        return v.viewport().mapToGlobal(pos), v.indexAt(pos).isValid()

    def _watch(self):
        """Watch the folder for changes. Called after a successful load: adding a watch on an
        unreachable network path blocks for the whole network timeout."""
        p = self.path
        if places.is_special(p) or places.is_server(p):
            return
        if p in self.watcher.directories():
            if not places.is_remote(p):
                return
            self.watcher.removePath(p)      # a network watch dies with the connection: renew it
        if self.watcher.directories():
            self.watcher.removePaths(self.watcher.directories())
        self.watcher.addPath(p)

    def go_back(self):
        while self.back_stack:
            p = self.back_stack.pop()
            if _exists(p):
                self.fwd_stack.append(self.path)
                prev = self.path
                self.navigate(p, record=False, select=prev)
                return

    def go_forward(self):
        while self.fwd_stack:
            p = self.fwd_stack.pop()
            if _exists(p):
                self.back_stack.append(self.path)
                self.navigate(p, record=False)
                return

    def parent_path(self) -> str | None:
        p = self.path
        if p is None or places.is_special(p):
            return None
        if places.is_server(p):
            return NETWORK
        if places.is_unc(p):
            drive, rest = os.path.splitdrive(p)          # \\server\share, \rest
            if not rest.strip("\\"):
                return "\\\\" + places.server_of(p)       # share -> server
        parent = os.path.dirname(p)
        return DRIVES if parent == p else parent

    def go_up(self):
        parent = self.parent_path()
        if parent:
            self.navigate(parent, select=self.path)

    def refresh(self):
        """Reload the folder, keeping selection and scroll position."""
        if self.path is None:
            return
        if not _exists(self.path):
            p = self.path
            while p and not os.path.isdir(p):
                parent = os.path.dirname(p)
                p = None if parent == p else parent
            self.navigate(p or DRIVES, record=False)
            return
        if self._searched and self.filter_text():
            self._search_timer.start()         # searching: refresh the results, not the folder
            return
        if self.columns is not None:
            self.columns.set_show_hidden(bool(self.config.get("show_hidden")))
        sel = [e.path for e in self.selected_entries()]
        cur = self.current_entry()
        scroll = self.tree.verticalScrollBar().value()
        self._load(sel, cur.path if cur else None, scroll)

    def _dir_changed(self, _path):
        if not self._refresh_timer.isActive():
            # network folders: at most one quiet reload every 2 s, however chatty the share is
            remote = places.is_remote(self.path or "")
            self._refresh_timer.start(2000 if remote else 400)

    def _auto_refresh(self):
        if self.tree.is_editing():          # don't kill an inline rename
            self._refresh_timer.start()
            return
        self.refresh()

    def _load(self, select: list[str], current: str | None = None, scroll: int | None = None,
              clear: bool = False):
        """Local folders load inline (fast). Network locations load on a worker thread so a
        slow or offline server never freezes the window."""
        self._load_token += 1
        token, path, hidden = self._load_token, self.path, self.config.get("show_hidden")
        if places.is_tag(path):
            wanted = tags.store().paths_with(path[len(places.TAG_PREFIX):])
            self.loading = True
            places.run_async(lambda: (DirModel.fetch_paths(wanted), None),
                             lambda res: self._loaded(token, res, select, current, scroll))
            return
        remote = places.is_remote(path) or places.is_server(path)
        if not remote:
            self._loaded(token, DirModel.fetch(path, hidden), select, current, scroll)
            return
        if clear:
            self.model.set_entries(path, [])
        self.loading = True
        if clear or not self.model.entries:
            # first visit only, and only if it's slow: a refresh keeps the list on screen
            # instead of flashing "Loading…" (a busy share reports changes every second)
            QTimer.singleShot(300, lambda tk=token: self._show_loading(tk))
        places.run_async(lambda: DirModel.fetch(path, hidden),
                         lambda res: self._loaded(token, res, select, current, scroll))

    def _show_loading(self, token):
        if token == self._load_token and self.loading:
            self._set_message("Loading…", error=False)

    def _set_message(self, text: str, error: bool = True):
        self.error_label.setStyleSheet(
            f"color: {'#d9534f' if error else theme.colors()['dim']}; padding: 8px 12px;")
        self.error_label.setText(text)
        self.error_label.setVisible(bool(text))

    def _loaded(self, token, res, select, current, scroll):
        """A listing arrived: show it, or the error (network errors retry by themselves)."""
        if token != self._load_token:          # the user has moved on
            return
        self.loading = False
        entries, err = res if isinstance(res, tuple) else ([], str(res))
        self.model.set_entries(self.path, entries)
        self._set_message(f"Can't open {display_path(self.path)}: {err}" if err else "")
        self._network_state(err)
        if not err:
            self._watch()
            if places.remember_server(self.path):
                self.config.set("recent_servers", list(places.RECENT_SERVERS))
            self._request_folder_sizes(token)
        self.loaded.emit()
        self.fit_columns()
        self.select_paths(select, current)
        if scroll is not None:
            self.tree.verticalScrollBar().setValue(scroll)
        elif not select:
            self.tree.scrollToTop()
        self.selectionChanged.emit()

    def _request_folder_sizes(self, token):
        """Ask Everything (voidtools) for the sizes of this folder's subfolders. The answer
        comes back a few ms later through the event loop; nothing waits for it."""
        if not self.config.get("everything_sizes") or places.is_special(self.path) \
                or places.is_server(self.path) or not any(e.is_dir for e in self.model.entries):
            return
        c = everything.client()
        if c is None:
            return

        def got(sizes, token=token):
            if token == self._load_token and sizes:
                self.model.set_folder_sizes(sizes)
                self.selectionChanged.emit()          # refresh status bar and inspector
        c.folder_sizes(self.path, got)

    def apply_settings(self):
        self.model.date_format = self.config.get("date_format")
        self.refresh()

    # ----- selection --------------------------------------------------------------
    def selected_entries(self) -> list[Entry]:
        if self.columns_active():
            return DirModel.fetch_paths(self.columns.selected_paths())
        rows = sorted(self.tree.selectionModel().selectedRows(0), key=lambda i: i.row())
        return [e for e in (self.proxy.entry(i) for i in rows) if e is not None]

    def current_entry(self) -> Entry | None:
        if self.columns_active():
            es = self.selected_entries()
            return es[-1] if es else None
        return self.proxy.entry(self.tree.currentIndex())

    def select_paths(self, paths: list[str], current: str | None = None):
        """Select these items and make one of them current."""
        if self.columns_active() and (current or paths):
            self.columns.show_path(self.path, current or paths[0])
        sm = self.tree.selectionModel()
        want = {os.path.normcase(p) for p in paths if p}
        cur_key = os.path.normcase(current) if current else None
        sel = QItemSelection()
        cur_idx = None
        for r, e in enumerate(self.model.entries):
            key = os.path.normcase(e.path)
            if key in want or key == cur_key:
                pidx = self.proxy.mapFromSource(self.model.index(r, 0))
                if not pidx.isValid():
                    continue
                if key in want:
                    sel.select(pidx, pidx)
                if key == cur_key or (cur_idx is None and key in want):
                    cur_idx = pidx
        flags = QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows
        sm.select(sel, flags)
        if cur_idx is not None:
            sm.setCurrentIndex(cur_idx, QItemSelectionModel.SelectionFlag.NoUpdate)
            self.tree.scrollTo(cur_idx)
            if self.item_view() is not self.tree:
                self.item_view().scrollTo(cur_idx)
        elif self.proxy.rowCount():
            sm.setCurrentIndex(self.proxy.index(0, 0), QItemSelectionModel.SelectionFlag.NoUpdate)

    def edit_current(self):
        """Rename the current item in place (list and columns)."""
        if self.columns_active():
            self.columns.edit_current()        # in place, like the list
            return
        idx = self.tree.currentIndex()
        if idx.isValid():
            self.item_view().edit(idx.siblingAtColumn(0))

    def _rename_failed(self, msg):
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.warning(self, "Rename", msg)

    # ----- opening ------------------------------------------------------------------
    def _activated(self, index):
        entries = self.selected_entries()
        clicked = self.proxy.entry(index)
        if clicked is not None and clicked not in entries:
            entries = [clicked]
        self.open_entries(entries)

    def open_entries(self, entries: list[Entry]):
        """Open files with their apps (Windows' own security checks apply) and folders here."""
        dirs = [e for e in entries if e.is_dir]
        files = [e for e in entries if not e.is_dir]
        if len(files) > 12:
            from PySide6.QtWidgets import QMessageBox
            if QMessageBox.question(self, "Open files", f"Open {len(files)} files?") != QMessageBox.StandardButton.Yes:
                files = []
        for f in files:
            try:
                fileops.open_path(f.path)
            except OSError as ex:
                self.statusMessage.emit(f"Couldn't open {f.name}: {ex.strerror or ex}")
        if dirs:
            for d in dirs[1:]:
                self.openInNewTab.emit(d.path)
            self.navigate(dirs[0].path)

    # ----- type-to-filter ------------------------------------------------------------
    def filter_text(self) -> str:
        return self.filter_edit.text()

    def type_into_filter(self, text: str):
        self.filter_edit.show()
        self.filter_edit.setText(self.filter_edit.text() + text)

    def search_on(self) -> bool:
        """Typing searches subfolders too: on in Settings, a real folder, Everything running."""
        p = self.path or ""
        return bool(self.config.get("everything_search")) and not places.is_special(p) \
            and not places.is_server(p) and everything.is_running()

    def _column_typed(self, text: str) -> bool:
        """Columns view: typed letters start a search of the deepest open folder."""
        if not self.search_on():
            return False
        self.type_into_filter(text)
        if self.results is not None:
            self.results.setFocus()
        return True

    def _run_search(self):
        """Ask Everything for matches anywhere under this folder; they replace the list."""
        text = self.filter_text().strip()
        if not text or not self.search_on():
            return
        c = everything.client()
        if c is None:
            return
        self._search_token += 1
        token, folder = self._search_token, self.path

        def got(results, token=token):
            if token != self._search_token or self.path != folder or not self.filter_text():
                return
            entries = []
            for path, size, is_dir, mtime in results:
                e = Entry(os.path.basename(path), path, is_dir, size, mtime)
                rel = os.path.relpath(os.path.dirname(path), folder)
                e.where = "" if rel == "." else rel
                entries.append(e)
            self._searched = True
            self.model.set_entries(folder, entries)
            self.proxy.set_filter("")          # Everything matched them (words, wildcards...)
            if self.proxy.rowCount():
                self.tree.setCurrentIndex(self.proxy.index(0, 0))
            self.selectionChanged.emit()
        c.search_under(folder, text, got)

    def chop_filter(self):
        self.filter_edit.setText(self.filter_edit.text()[:-1])

    def clear_filter(self):
        self.filter_edit.clear()

    def focus_filter(self):
        self.filter_edit.show()
        self.filter_edit.setFocus()
        self.filter_edit.selectAll()

    def _filter_changed(self, text):
        if self.columns is not None and self.mode == "columns":
            had_focus = self.results is not None and self.results.hasFocus()
            self._sync_results_column()        # results column on while searching, off after
            if not text and had_focus:
                self.focus_list()
        if text and self.search_on():
            self._search_timer.start()         # this folder's own matches now, subfolders in 150 ms
        elif not text and self._searched:
            self._searched = False             # search over: back to the folder itself
            self._search_timer.stop()
            self._search_token += 1
            self._load([], None)
        self.proxy.set_filter(text)
        self.filter_edit.setVisible(bool(text) or self.filter_edit.hasFocus())
        if self.proxy.rowCount():
            self.tree.setCurrentIndex(self.proxy.index(0, 0))
        self.selectionChanged.emit()

    # ----- shared column layout ------------------------------------------------------
    def showEvent(self, ev):
        super().showEvent(ev)
        if self.applied_version != self.config.view_version:
            self.apply_layout()
        if getattr(self, "_retry_on_show", False):
            self._retry_on_show = False
            QTimer.singleShot(0, self.refresh)

    def _on_view_changed(self, source):
        if source is self or self.own_layout:
            return
        if self.isVisible():
            self.apply_layout()

    def current_layout(self) -> dict:
        """This folder's own layout if it has one, else the shared one."""
        return (self.own_layout and self.config.folder_layout(self.path)) or self.config.view

    def _sync_own_layout(self):
        own = self.config.folder_layout(self.path) is not None if self.path else False
        if own or self.own_layout:               # entering or leaving a folder with its own layout
            self.own_layout = own
            self.apply_layout()

    def _on_folder_layout_changed(self, key, source):
        if source is not self and self.path and self.config.folder_key(self.path) == key:
            self.own_layout = self.config.folder_layout(self.path) is not None
            self.apply_layout()

    def _flush_layout(self):
        timer = getattr(self, "_edit_timer", None)
        if timer is not None and timer.isActive():
            timer.stop()
            self._save_layout_now()

    def save_layout_for_all(self):
        """Header menu: this layout (visible columns only) becomes the one every folder uses."""
        self._flush_layout()
        self.config.set_view(self.read_layout(prune=True), source=self)
        self.applied_version = self.config.view_version
        self.statusMessage.emit("Column layout saved for all folders")

    def save_layout_for_folder(self):
        self._flush_layout()
        self.own_layout = True
        self.config.set_folder_layout(self.path, self.read_layout(prune=True), source=self)
        self.statusMessage.emit(f"Column layout saved for {title_for(self.path)}")

    def forget_folder_layout(self):
        self._flush_layout()
        self.own_layout = False
        self.config.set_folder_layout(self.path, None, source=self)
        self.apply_layout()
        self.statusMessage.emit(f"{title_for(self.path)} uses the shared layout again")

    # ----- auto-fit: columns as wide as their widest item ---------------------------------
    def _settings_changed(self):
        hover = theme.hover_color(theme.colors(), int(self.config.get("hover_strength")))
        if hover != self.tree.hover_color:              # the Settings slider, live
            self.tree.hover_color = hover
            self.tree.viewport().update()
            if self.columns is not None:
                self.columns.set_hover_color(hover)
            for v in self.item_views()[1:]:
                v.hover_color = hover
                v.viewport().update()
        if self.columns is not None:
            sizes = (bool(self.config.get("columns_sizes")), bool(self.config.get("everything_sizes")))
            if sizes != (self.columns.show_sizes, self.columns.folder_sizes_on):
                self.columns.set_show_sizes(*sizes)
        on = bool(self.config.get("auto_fit_columns"))
        if on != self._auto_fit:
            self._auto_fit = on
            self.apply_layout()                  # off: your saved widths come back

    def fit_columns(self):
        """Size each column to its widest item (not saved: your widths stay in the layout)."""
        if not self._auto_fit:
            return
        h = self.tree.header()
        self._applying = True                    # fitted widths are never saved
        try:
            for key in self.current_layout()["columns"]:
                i = COLUMNS.index(key)
                self.tree.resizeColumnToContents(i)
                if key == "name":
                    h.resizeSection(i, min(max(h.sectionSize(i), 160), 640))
                else:
                    h.resizeSection(i, max(h.sectionSize(i), 48))
        finally:
            self._applying = False

    def apply_layout(self):
        """Show this folder's layout (or the shared one): columns, order, widths, sort."""
        lay = self.current_layout()
        h = self.tree.header()
        self._applying = True
        try:
            for i, key in enumerate(COLUMNS):
                h.setSectionHidden(i, key not in lay["columns"])
            for target, key in enumerate(lay["columns"]):
                cur = h.visualIndex(COLUMNS.index(key))
                if cur != target:
                    h.moveSection(cur, target)
            for key in lay["columns"]:
                h.resizeSection(COLUMNS.index(key), lay["widths"].get(key, DEFAULT_WIDTHS[key]))
            self.model.folders_first = lay["folders_first"]
            order = Qt.SortOrder.DescendingOrder if lay["sort_desc"] else Qt.SortOrder.AscendingOrder
            self.tree.sortByColumn(COLUMNS.index(lay["sort_column"]), order)
        finally:
            self._applying = False
        self.applied_version = self.config.view_version
        self.fit_columns()

    def read_layout(self, prune: bool = False) -> dict:
        """The layout as it's on screen now; prune keeps only shown columns' widths."""
        h = self.tree.header()
        cols = []
        for v in range(h.count()):
            lg = h.logicalIndex(v)
            if not h.isSectionHidden(lg):
                cols.append(COLUMNS[lg])
        widths = dict(self.current_layout().get("widths", {}))
        for c in cols:
            if not self._auto_fit or c not in widths:    # auto-fit widths aren't yours: keep yours
                widths[c] = h.sectionSize(COLUMNS.index(c))
        if prune:
            widths = {c: w for c, w in widths.items() if c in cols}
        sec = h.sortIndicatorSection()
        return {
            "columns": cols,
            "widths": widths,
            "sort_column": COLUMNS[sec] if 0 <= sec < len(COLUMNS) else "name",
            "sort_desc": h.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder,
            "folders_first": self.model.folders_first,
        }

    def _layout_edited(self):
        """Header changed (sort, resize, move, show/hide): save once things have settled. Reading
        the header inside a sort catches it mid-change (a hidden column looked visible), and a
        drag-resize sends dozens of these."""
        if self._applying:
            return
        if not hasattr(self, "_edit_timer"):
            self._edit_timer = QTimer(self)
            self._edit_timer.setSingleShot(True)
            self._edit_timer.setInterval(0)
            self._edit_timer.timeout.connect(self._save_layout_now)
        self._edit_timer.start()

    def _save_layout_now(self):
        if self._applying:
            return
        if self.own_layout:                     # edits in a folder with its own layout stay there
            self.config.set_folder_layout(self.path, self.read_layout(), source=self)
            return
        self.config.set_view(self.read_layout(), source=self)
        self.applied_version = self.config.view_version

    def is_column_visible(self, key: str) -> bool:
        return not self.tree.header().isSectionHidden(COLUMNS.index(key))

    def set_column_visible(self, key: str, on: bool):
        if key == "name":
            return
        h = self.tree.header()
        i = COLUMNS.index(key)
        h.setSectionHidden(i, not on)
        if on and h.sectionSize(i) < 30:
            h.resizeSection(i, self.current_layout()["widths"].get(key, DEFAULT_WIDTHS[key]))
        self._layout_edited()
        if on:
            self.fit_columns()

    def set_folders_first(self, on: bool):
        self.model.folders_first = on
        order = Qt.SortOrder.DescendingOrder if self.model.sort_desc else Qt.SortOrder.AscendingOrder
        self.model.sort(self.model.sort_column, order)
        self._layout_edited()

    def _header_menu(self, pos):
        """Right-click on a column header: columns, fit, layouts and presets."""
        m = QMenu(self)
        for key in COLUMNS:
            a = m.addAction(COLUMN_TITLES[key])
            a.setCheckable(True)
            a.setChecked(self.is_column_visible(key))
            a.setEnabled(key != "name")
            a.toggled.connect(lambda on, k=key: self.set_column_visible(k, on))
        m.addSeparator()
        ff = m.addAction("Folders first")
        ff.setCheckable(True)
        ff.setChecked(self.model.folders_first)
        ff.toggled.connect(self.set_folders_first)
        fit = m.addAction("Fit columns to content")
        fit.setCheckable(True)
        fit.setChecked(self._auto_fit)
        fit.setToolTip("Each column as wide as its widest item. Turn off to get your widths back.")
        fit.toggled.connect(lambda on: self.config.set("auto_fit_columns", on))
        m.addSeparator()
        self.add_layout_actions(m)
        presets = m.addMenu("View presets (all folders)")
        dialogs.add_preset_actions(presets, self.config, self)
        m.exec(self.tree.header().mapToGlobal(pos))

    def add_layout_actions(self, m):
        m.addAction("Save layout for all folders").triggered.connect(self.save_layout_for_all)
        special = not self.path or places.is_special(self.path)
        here = m.addAction("Save layout for this folder")
        here.setEnabled(not special)
        here.triggered.connect(self.save_layout_for_folder)
        if self.own_layout:
            m.addAction("Use the shared layout here").triggered.connect(self.forget_folder_layout)
