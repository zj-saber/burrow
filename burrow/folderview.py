"""One tab: a folder listing with history, type-to-filter, drag and drop, and the shared layout."""
from __future__ import annotations

import os
import re

from PySide6.QtCore import QFileSystemWatcher, QItemSelection, QItemSelectionModel, Qt, QTimer, Signal
from PySide6.QtWidgets import (QAbstractItemView, QHeaderView, QLabel, QLineEdit, QMenu,
                               QStyledItemDelegate, QTreeView, QVBoxLayout, QWidget)

from . import dialogs, fileops
from .config import COLUMN_TITLES, COLUMNS, DEFAULT_WIDTHS, Config
from .model import DRIVES, ENTRY_ROLE, DirModel, Entry, FilterProxy


def normalize_path(p: str) -> str:
    if p == DRIVES:
        return DRIVES
    p = os.path.expandvars(os.path.expanduser(p.strip().strip('"')))
    if re.fullmatch(r"[A-Za-z]:", p):
        p += "\\"
    return os.path.normpath(os.path.abspath(p))


def display_path(p: str) -> str:
    return "This PC" if p == DRIVES else p


def title_for(p: str) -> str:
    if p == DRIVES:
        return "This PC"
    return os.path.basename(p) or p


class NameDelegate(QStyledItemDelegate):
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
        return None if self.owner.path == DRIVES else self.owner.path

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
        t.setItemDelegateForColumn(0, NameDelegate(t))
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

    # ----- navigation -------------------------------------------------------------
    def navigate(self, path: str, record: bool = True, select: str | None = None) -> bool:
        path = normalize_path(path)
        if path != DRIVES and not os.path.isdir(path):
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
        self._load([select] if select else [], select)
        if self.watcher.directories():
            self.watcher.removePaths(self.watcher.directories())
        if path != DRIVES:
            self.watcher.addPath(path)
        self.pathChanged.emit(path)
        return True

    def go_back(self):
        while self.back_stack:
            p = self.back_stack.pop()
            if p == DRIVES or os.path.isdir(p):
                self.fwd_stack.append(self.path)
                prev = self.path
                self.navigate(p, record=False, select=prev)
                return

    def go_forward(self):
        while self.fwd_stack:
            p = self.fwd_stack.pop()
            if p == DRIVES or os.path.isdir(p):
                self.back_stack.append(self.path)
                self.navigate(p, record=False)
                return

    def go_up(self):
        if self.path == DRIVES:
            return
        parent = os.path.dirname(self.path)
        child = self.path
        self.navigate(DRIVES if parent == self.path else parent, select=child)

    def refresh(self):
        if self.path is None:
            return
        if self.path != DRIVES and not os.path.isdir(self.path):
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

    def _load(self, select: list[str], current: str | None = None, scroll: int | None = None):
        err = self.model.load(self.path, self.config.get("show_hidden"))
        self.error_label.setVisible(bool(err))
        if err:
            self.error_label.setText(f"Can't open {self.path}: {err}")
        self.select_paths(select, current)
        if scroll is not None:
            self.tree.verticalScrollBar().setValue(scroll)
        elif not select:
            self.tree.scrollToTop()
        self.selectionChanged.emit()

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
