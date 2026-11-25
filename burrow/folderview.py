"""One tab: a folder listing with history, type-to-filter, drag and drop, and the shared layout."""
from __future__ import annotations

import os
import re

from PySide6.QtCore import QFileSystemWatcher, QItemSelection, QItemSelectionModel, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPalette
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QHeaderView, QLabel, QLineEdit, QMenu,
                               QStyle, QStyledItemDelegate, QTreeView, QVBoxLayout, QWidget)

from . import dialogs, everything, fileops, places, tags, theme
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

    def sizeHint(self, option, index):
        s = super().sizeHint(option, index)
        if s.height() < self.row_height:
            s.setHeight(self.row_height)
        return s

    def paint(self, painter, option, index):
        state = option.state
        if state & _HOVER and not state & _SELECTED:
            painter.fillRect(option.rect, self.hover)
        if state & _FOCUS:
            option.state = state & ~_FOCUS
        super().paint(painter, option, index)


class NameDelegate(RowDelegate):
    """Inline rename that pre-selects the name without its extension, like Explorer."""

    def setEditorData(self, editor, index):
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
        if not store.items:
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
        elif state & _HOVER:
            painter.fillRect(r.adjusted(r.width() - dots_w, 0, 0, 0), self.hover)
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
    dropRequested = Signal(list, str, bool)   # sources, target folder, move?

    def __init__(self, owner: "FolderView"):
        super().__init__(owner)
        self.owner = owner

    def is_editing(self) -> bool:
        return self.state() == QAbstractItemView.State.EditingState

    def keyPressEvent(self, ev):
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
        if ev.key() == Qt.Key.Key_Escape and self.owner.filter_text():
            self.owner.clear_filter()
            return
        if (text and text.isprintable() and mods == Qt.KeyboardModifier.NoModifier
                and not (text == " " and not self.owner.filter_text())):
            self.owner.type_into_filter(text)
            return
        super().keyPressEvent(ev)

    def mousePressEvent(self, ev):
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
        self.loading = False

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
        self.filter_edit.setPlaceholderText("Filter by name (just start typing) — Esc clears")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._filter_changed)
        self.filter_edit.leave.connect(self.tree.setFocus)
        self.filter_edit.hide()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.error_label)
        lay.addWidget(self.tree, 1)
        lay.addWidget(self.filter_edit)

        self.watcher = QFileSystemWatcher(self)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(400)
        self._refresh_timer.timeout.connect(self._auto_refresh)
        self.watcher.directoryChanged.connect(self._dir_changed)

        config.viewChanged.connect(self._on_view_changed)
        self.apply_layout()
        self.apply_theme(theme.colors())

    def apply_theme(self, c: dict):
        """Style sheets go on the header and filter box only, never on the list itself."""
        self.tree.header().setStyleSheet(theme.header_qss(c))
        self.filter_edit.setStyleSheet(theme.field_qss(c))
        self.model.meta_color = QColor(c["dim"])
        hover = QColor(c["hover"])
        row_h = max(24, self.fontMetrics().height() + 10)
        for d in (self.row_delegate, self.name_delegate):
            d.hover = hover
            d.row_height = row_h
        self.tree.viewport().update()

    # ----- navigation -------------------------------------------------------------
    def navigate(self, path: str, record: bool = True, select: str | None = None) -> bool:
        path = normalize_path(path)
        if places.needs_reconnect(path):
            # A remembered network drive that isn't connected: reconnect like Explorer does
            # (Windows asks for credentials if it needs them).
            self.statusMessage.emit(f"Reconnecting {path[:2]}…")
            if not places.reconnect(path[:2], int(self.window().winId())):
                self.statusMessage.emit(f"Couldn't reconnect {path[:2]}")
                return False
        if not _exists(path):
            self.statusMessage.emit(f"Folder not found: {path}")
            return False
        if record and self.path is not None and path != self.path:
            self.back_stack.append(self.path)
            self.fwd_stack.clear()
        self.path = path
        self.filter_edit.blockSignals(True)
        self.filter_edit.clear()
        self.filter_edit.hide()
        self.filter_edit.blockSignals(False)
        self.proxy.set_filter("")
        if self.watcher.directories():
            self.watcher.removePaths(self.watcher.directories())
        self._load([select] if select else [], select, clear=True)
        self.pathChanged.emit(path)
        return True

    def _watch(self):
        """Watch the folder for changes. Called after a successful load: adding a watch on an
        unreachable network path blocks for the whole network timeout."""
        p = self.path
        if places.is_special(p) or places.is_server(p) or p in self.watcher.directories():
            return
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
        if self.path is None:
            return
        if not _exists(self.path):
            p = self.path
            while p and not os.path.isdir(p):
                parent = os.path.dirname(p)
                p = None if parent == p else parent
            self.navigate(p or DRIVES, record=False)
            return
        sel = [e.path for e in self.selected_entries()]
        cur = self.current_entry()
        scroll = self.tree.verticalScrollBar().value()
        self._load(sel, cur.path if cur else None, scroll)

    def _dir_changed(self, _path):
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

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
        self._set_message("Loading…", error=False)
        places.run_async(lambda: DirModel.fetch(path, hidden),
                         lambda res: self._loaded(token, res, select, current, scroll))

    def _set_message(self, text: str, error: bool = True):
        self.error_label.setStyleSheet(
            f"color: {'#d9534f' if error else theme.colors()['dim']}; padding: 8px 12px;")
        self.error_label.setText(text)
        self.error_label.setVisible(bool(text))

    def _loaded(self, token, res, select, current, scroll):
        if token != self._load_token:          # the user has moved on
            return
        self.loading = False
        entries, err = res if isinstance(res, tuple) else ([], str(res))
        self.model.set_entries(self.path, entries)
        self._set_message(f"Can't open {display_path(self.path)}: {err}" if err else "")
        if not err:
            self._watch()
            if places.remember_server(self.path):
                self.config.set("recent_servers", list(places.RECENT_SERVERS))
            self._request_folder_sizes(token)
        self.loaded.emit()
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
        rows = sorted(self.tree.selectionModel().selectedRows(0), key=lambda i: i.row())
        return [e for e in (self.proxy.entry(i) for i in rows) if e is not None]

    def current_entry(self) -> Entry | None:
        return self.proxy.entry(self.tree.currentIndex())

    def select_paths(self, paths: list[str], current: str | None = None):
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
        elif self.proxy.rowCount():
            sm.setCurrentIndex(self.proxy.index(0, 0), QItemSelectionModel.SelectionFlag.NoUpdate)

    def edit_current(self):
        idx = self.tree.currentIndex()
        if idx.isValid():
            self.tree.edit(idx.siblingAtColumn(0))

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

    def chop_filter(self):
        self.filter_edit.setText(self.filter_edit.text()[:-1])

    def clear_filter(self):
        self.filter_edit.clear()

    def focus_filter(self):
        self.filter_edit.show()
        self.filter_edit.setFocus()
        self.filter_edit.selectAll()

    def _filter_changed(self, text):
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

    def _on_view_changed(self, source):
        if source is self:
            return
        if self.isVisible():
            self.apply_layout()

    def apply_layout(self):
        lay = self.config.view
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

    def read_layout(self) -> dict:
        h = self.tree.header()
        cols = []
        for v in range(h.count()):
            lg = h.logicalIndex(v)
            if not h.isSectionHidden(lg):
                cols.append(COLUMNS[lg])
        widths = dict(self.config.view.get("widths", {}))
        for c in cols:
            widths[c] = h.sectionSize(COLUMNS.index(c))
        sec = h.sortIndicatorSection()
        return {
            "columns": cols,
            "widths": widths,
            "sort_column": COLUMNS[sec] if 0 <= sec < len(COLUMNS) else "name",
            "sort_desc": h.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder,
            "folders_first": self.model.folders_first,
        }

    def _layout_edited(self):
        if self._applying:
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
            h.resizeSection(i, self.config.view["widths"].get(key, DEFAULT_WIDTHS[key]))
        self._layout_edited()

    def set_folders_first(self, on: bool):
        self.model.folders_first = on
        order = Qt.SortOrder.DescendingOrder if self.model.sort_desc else Qt.SortOrder.AscendingOrder
        self.model.sort(self.model.sort_column, order)
        self._layout_edited()

    def _header_menu(self, pos):
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
        m.addSeparator()
        presets = m.addMenu("View presets (all folders)")
        dialogs.add_preset_actions(presets, self.config, self)
        m.exec(self.tree.header().mapToGlobal(pos))
