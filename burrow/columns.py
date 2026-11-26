"""Columns view (Miller columns, like macOS Finder / One Commander): each folder you pick opens
its contents in a new column to the right. Built only when a tab switches to it, and backed by
QFileSystemModel, which reads folders on its own thread, so the list view pays nothing for it."""
from __future__ import annotations

import os

from PySide6.QtCore import QDir, QModelIndex, QPointF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPalette, QPen
from PySide6.QtWidgets import (QAbstractItemView, QColumnView, QFileSystemModel, QFrame, QListView,
                               QStyledItemDelegate, QStyleOptionViewItem)


class _RowDelegate(QStyledItemDelegate):
    def __init__(self, height: int, parent=None):
        super().__init__(parent)
        self.height = height

    def sizeHint(self, option, index):
        s = super().sizeHint(option, index)
        return QSize(s.width(), max(s.height(), self.height))

    def paint(self, painter, option, index):
        """Row, then a tiny faint chevron on folders (instead of Qt's large arrow)."""
        opt = option
        is_dir = index.model().isDir(index)
        if is_dir:
            opt = QStyleOptionViewItem(option)
            opt.rect = option.rect.adjusted(0, 0, -12, 0)      # keep the name clear of it
        super().paint(painter, opt, index)
        from . import marks
        if marks.MARKS:
            k = marks.MARKS.get(os.path.normpath(index.model().filePath(index)).lower())
            if k:
                painter.fillRect(option.rect, marks.color(k))
        if is_dir:
            r = option.rect
            c = QColor(option.palette.color(QPalette.ColorRole.Text))
            c.setAlpha(90)
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(c, 1.2))
            x, y = r.right() - 8, r.center().y() + 0.5
            painter.drawPolyline([QPointF(x - 2, y - 3), QPointF(x + 1, y), QPointF(x - 2, y + 3)])
            painter.restore()


class ColumnsView(QColumnView):
    folderChosen = Signal(str)        # a folder is now the deepest column: it's the tab's folder
    fileChosen = Signal(str)          # a file is selected: its folder is the tab's folder
    openRequested = Signal(list)      # Enter / double-click on files
    contextMenu = Signal(object)      # pos in viewport coordinates
    extra_actions: tuple = ()         # the window's file actions (set per view); createColumn runs
                                      # during setModel, before __init__ finishes

    def __init__(self, show_hidden: bool, row_height: int = 24, parent=None):
        super().__init__(parent)
        self.fs = QFileSystemModel(self)
        self.fs.setReadOnly(True)                 # drag out only; moves go through Burrow
        self.fs.setRootPath("")                   # "This PC": drives in the first column
        self.set_show_hidden(show_hidden)
        self.setModel(self.fs)
        self._delegate = _RowDelegate(row_height, self)
        self.setItemDelegate(self._delegate)
        self.setFrameShape(QFrame.Shape.NoFrame)
        # compact: no scroll bars or resize grips (the wheel, Shift+wheel and keys still scroll)
        self.setResizeGripsVisible(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
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
        col.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        col.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        col.setFrameShape(QFrame.Shape.NoFrame)
        col.setViewportMargins(0, 0, 1, 0)       # 1px of window color between columns
        col.setIconSize(self.iconSize())
        col.addActions(self.extra_actions)
        return col

    def set_icon_size(self, px: int, row_height: int):
        self._delegate.height = row_height
        self.setIconSize(QSize(px, px))
        for col in self.findChildren(QListView):
            col.setIconSize(QSize(px, px))
            col.doItemsLayout()

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
