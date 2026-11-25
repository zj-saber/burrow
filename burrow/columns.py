"""Columns view (Miller columns, like macOS Finder / One Commander): each folder you pick opens
its contents in a new column to the right. Built only when a tab switches to it, and backed by
QFileSystemModel, which reads folders on its own thread, so the list view pays nothing for it."""
from __future__ import annotations

import os

from PySide6.QtCore import QDir, QModelIndex, QSize, Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QColumnView, QFileSystemModel, QFrame, QStyledItemDelegate


class _RowDelegate(QStyledItemDelegate):
    def __init__(self, height: int, parent=None):
        super().__init__(parent)
        self.height = height

    def sizeHint(self, option, index):
        s = super().sizeHint(option, index)
        return QSize(s.width(), max(s.height(), self.height))


class ColumnsView(QColumnView):
    folderChosen = Signal(str)        # a folder is now the deepest column: it's the tab's folder
    fileChosen = Signal(str)          # a file is selected: its folder is the tab's folder
    openRequested = Signal(list)      # Enter / double-click on files
    contextMenu = Signal(object)      # pos in viewport coordinates

    def __init__(self, show_hidden: bool, row_height: int = 24, parent=None):
        super().__init__(parent)
        self.fs = QFileSystemModel(self)
        self.fs.setReadOnly(True)                 # drag out only; moves go through Burrow
        self.fs.setRootPath("")                   # "This PC": drives in the first column
        self.set_show_hidden(show_hidden)
        self.setModel(self.fs)
        self.setItemDelegate(_RowDelegate(row_height, self))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setResizeGripsVisible(True)
        self.setColumnWidths([230] * 64)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda pos: self.contextMenu.emit(pos))
        self.activated.connect(self._activated)
        self.selectionModel().currentChanged.connect(self._current_changed)
        self._quiet = False

    def createColumn(self, index):
        col = super().createColumn(index)       # each column is its own list: route its menu here
        col.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        col.customContextMenuRequested.connect(lambda pos: self.contextMenu.emit(pos))
        col.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        return col

    def set_show_hidden(self, on: bool):
        f = QDir.Filter.AllEntries | QDir.Filter.NoDotAndDotDot | QDir.Filter.AllDirs | QDir.Filter.Drives
        if on:
            f |= QDir.Filter.Hidden | QDir.Filter.System
        self.fs.setFilter(f)

    # ----- the tab drives us --------------------------------------------------------
    def show_path(self, path: str, select: str | None = None):
        """Open the columns down to `path` (and select `select` inside it, if given)."""
        idx = self.fs.index(select or path)
        if not idx.isValid():
            return
        self._quiet = True
        try:
            self.setCurrentIndex(idx)
            self.selectionModel().select(idx, self.selectionModel().SelectionFlag.ClearAndSelect)
            self.scrollTo(idx)
        finally:
            self._quiet = False

    def path_of(self, idx: QModelIndex) -> str:
        return os.path.normpath(self.fs.filePath(idx)) if idx.isValid() else ""

    def selected_paths(self) -> list[str]:
        cur = self.currentIndex()
        if not cur.isValid():
            return []
        parent = cur.parent()
        rows = [i for i in self.selectionModel().selectedIndexes() if i.column() == 0 and i.parent() == parent]
        rows.sort(key=lambda i: i.row())
        return [self.path_of(i) for i in rows] or [self.path_of(cur)]

    def select_all_in_column(self):
        cur = self.currentIndex()
        if not cur.isValid():
            return
        parent = cur.parent()
        sm = self.selectionModel()
        n = self.fs.rowCount(parent)
        if n:
            from PySide6.QtCore import QItemSelection
            sm.select(QItemSelection(self.fs.index(0, 0, parent), self.fs.index(n - 1, 0, parent)),
                      sm.SelectionFlag.ClearAndSelect)

    # ----- the user drives us -------------------------------------------------------
    def _current_changed(self, cur: QModelIndex, _prev):
        if self._quiet or not cur.isValid():
            return
        p = self.path_of(cur)
        if self.fs.isDir(cur):
            self.folderChosen.emit(p)
        else:
            self.fileChosen.emit(p)

    def _activated(self, idx: QModelIndex):
        if idx.isValid() and not self.fs.isDir(idx):
            self.openRequested.emit(self.selected_paths())
