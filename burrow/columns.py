"""Columns view (Miller columns, like macOS Finder / One Commander): each folder you pick opens
its contents in a new column to the right. Built only when a tab switches to it, and backed by
QFileSystemModel, which reads folders on its own thread, so the list view pays nothing for it."""
from __future__ import annotations

import os

import time

from PySide6.QtCore import QDir, QEasingCurve, QModelIndex, QPropertyAnimation, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (QAbstractItemView, QColumnView, QFileSystemModel, QFrame, QListView,
                               QStyledItemDelegate)


def compact_size(n: int) -> str:
    """Sizes in columns view: MB by default, GB from 1 GB, TB from 1 TB."""
    if n >= 1 << 40:
        return f"{n / (1 << 40):.1f} TB"
    if n >= 1 << 30:
        return f"{n / (1 << 30):.1f} GB"
    mb = n / (1 << 20)
    if mb >= 10:
        return f"{mb:.0f} MB"
    return f"{max(mb, 0.1):.1f} MB" if n else "0 MB"


class _RowDelegate(QStyledItemDelegate):
    """Columns rows: a minimum height and the clipboard tint."""
    def __init__(self, height: int, parent=None):
        super().__init__(parent)
        self.height = height

    def sizeHint(self, option, index):
        s = super().sizeHint(option, index)
        return QSize(s.width(), max(s.height(), self.height))

    def paint(self, painter, option, index):
        """The row (no folder arrows: the icon says it), an optional small size at its end, and
        the clipboard tint. With sizes off this is one attribute check more than a plain row."""
        view = self.parent()
        from PySide6.QtWidgets import QStyle
        if option.state & QStyle.StateFlag.State_MouseOver and not option.state & QStyle.StateFlag.State_Selected:
            from . import theme
            theme.paint_hover(painter, option.rect, getattr(view, "hover_color", QColor(0, 0, 0, 0)))
            option.state &= ~QStyle.StateFlag.State_MouseOver          # the style's own hover would double it
        size = view.size_for(index) if getattr(view, "show_sizes", False) else None
        if size is None:
            super().paint(painter, option, index)
        else:
            from PySide6.QtWidgets import QStyleOptionViewItem
            text = compact_size(size)
            font = option.font
            font.setPointSizeF(max(6.5, font.pointSizeF() * 0.82))
            from PySide6.QtGui import QFontMetrics
            w = QFontMetrics(font).horizontalAdvance(text) + 10
            full = QStyleOptionViewItem(option)                      # selection/hover across the whole row
            self.initStyleOption(full, index)
            from PySide6.QtWidgets import QStyle
            style = full.widget.style() if full.widget else view.style()
            style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, full, painter, full.widget)
            opt = QStyleOptionViewItem(option)
            opt.rect = option.rect.adjusted(0, 0, -w, 0)           # the name ends before the size
            super().paint(painter, opt, index)
            painter.save()
            painter.setFont(font)
            c = QColor(option.palette.color(QPalette.ColorRole.Text))
            c.setAlpha(150)
            painter.setPen(c)
            painter.drawText(option.rect.adjusted(0, 0, -8, 0), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, text)
            painter.restore()
        from . import marks
        if marks.MARKS:
            k = marks.MARKS.get(os.path.normpath(index.model().filePath(index)).lower())
            if k:
                painter.fillRect(option.rect, marks.color(k))


class _Column(QListView):
    """One column: smooth pixel scrolling that keeps 3 rows visible ahead of the cursor, and
    arrow keys / typing handed to the owner (in and out of folders, search the newest column)."""
    AHEAD = 3

    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self.owner = owner
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._anim = QPropertyAnimation(self.verticalScrollBar(), b"value", self)
        self._anim.setDuration(120)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _row_h(self) -> int:
        return max(16, self.owner._delegate.height)

    def _glide(self, target: int):
        sb = self.verticalScrollBar()
        target = max(0, min(sb.maximum(), target))
        base = self._anim.endValue() if self._anim.state() == QPropertyAnimation.State.Running else sb.value()
        if target == base:
            return
        self._anim.stop()
        self._anim.setStartValue(sb.value())
        self._anim.setEndValue(target)
        self._anim.start()

    def scrollTo(self, index, hint=QAbstractItemView.ScrollHint.EnsureVisible):
        """Glide so the current row keeps AHEAD rows visible above and below it."""
        if hint != QAbstractItemView.ScrollHint.EnsureVisible or not index.isValid():
            return super().scrollTo(index, hint)
        sb = self.verticalScrollBar()
        r = self.visualRect(index)
        h = r.height() or self._row_h()
        vh = self.viewport().height()
        y = r.top() + sb.value()                       # the row's position in the whole list
        base = self._anim.endValue() if self._anim.state() == QPropertyAnimation.State.Running else sb.value()
        margin = min(self.AHEAD * h, max(0, (vh - h) // 2))
        if y - base < margin:
            self._glide(y - margin)
        elif y + h - base > vh - margin:
            self._glide(y + h - vh + margin)

    def wheelEvent(self, ev):
        steps = ev.angleDelta().y() / 120
        if ev.pixelDelta().isNull() and steps and not ev.modifiers():
            base = self._anim.endValue() if self._anim.state() == QPropertyAnimation.State.Running \
                else self.verticalScrollBar().value()
            self._glide(int(base - steps * 3 * self._row_h()))      # 3 rows a notch, gliding
            ev.accept()
            return
        super().wheelEvent(ev)

    def keyPressEvent(self, ev):
        """← / → / typing go to the owner; everything else is a normal list."""
        k, mods = ev.key(), ev.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        if not mods and k == Qt.Key.Key_Right:
            self.owner.go_in()
            return
        if not mods and k == Qt.Key.Key_Left:
            self.owner.go_out()
            return
        text = ev.text()
        typing = self.owner.searching()          # a space only counts in the middle of a name
        if text and text.isprintable() and (typing or not text.isspace())                 and not (mods & ~Qt.KeyboardModifier.ShiftModifier):
            self.owner.type_search(text)
            return
        super().keyPressEvent(ev)


class ColumnsView(QColumnView):
    """Miller columns over QFileSystemModel, with Burrow's keys, scrolling and file actions."""
    folderChosen = Signal(str)        # a folder is now the deepest column: it's the tab's folder
    fileChosen = Signal(str)          # a file is selected: its folder is the tab's folder
    openRequested = Signal(list)      # Enter / double-click on files
    contextMenu = Signal(object)      # pos in viewport coordinates
    show_sizes = False                # Settings: small sizes after the names (class defaults:
    folder_sizes_on = True            # columns are created during setModel, before __init__ ends)
    extra_actions: tuple = ()         # the window's file actions (set per view); createColumn runs
                                      # during setModel, before __init__ finishes

    def __init__(self, show_hidden: bool, row_height: int = 24, parent=None):
        super().__init__(parent)
        self.fs = QFileSystemModel(self)
        self.fs.setReadOnly(True)                 # drag out only; moves go through Burrow
        self.fs.setRootPath("")                   # "This PC": drives in the first column
        # columns copy these from the view (initializeColumn): pixel scrolling for smooth glides
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
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
        # arrowing through folders only opens the tab's folder once you pause: each stop used
        # to reload the address bar, inspector and status bar (18-40 ms a step)
        self._nav_timer = QTimer(self)
        self._nav_timer.setSingleShot(True)
        self._nav_timer.setInterval(140)
        self._nav_timer.timeout.connect(self._emit_current)
        self._pending_in = None                 # a folder → was pressed on while still loading
        self.fs.directoryLoaded.connect(self._dir_loaded)
        self._sizes: dict[str, int] = {}        # folder path (normcase) -> size, from Everything
        self._search = ("", 0.0, None)          # typed text, when, the column searched

    def createColumn(self, index):
        """Each column is a _Column wired to the window's menu, actions and icon size."""
        col = _Column(self, self.viewport())    # our own list: smooth scrolling and arrow keys
        self.initializeColumn(col)
        col.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        col.verticalScrollBar().setSingleStep(max(16, self._delegate.height) if hasattr(self, "_delegate") else 24)
        col.setRootIndex(index)
        if self.fs.canFetchMore(index):
            self.fs.fetchMore(index)
        # each column is its own list: route its menu here
        col.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        col.customContextMenuRequested.connect(lambda pos: self.contextMenu.emit(pos))
        col.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        col.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        col.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        col.setFrameShape(QFrame.Shape.NoFrame)
        col.setViewportMargins(0, 0, 1, 0)       # 1px of window color between columns
        col.setIconSize(self.iconSize())
        col.addActions(self.extra_actions)
        col.setMouseTracking(True)
        col.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._ask_folder_sizes(index)
        return col

    def set_hover_color(self, color):
        self.hover_color = color
        for col in self.findChildren(_Column):
            col.viewport().update()

    # ----- sizes next to names -----------------------------------------------------------
    def set_show_sizes(self, on: bool, folders: bool = True):
        self.show_sizes, self.folder_sizes_on = on, folders
        if on:
            for col in self.findChildren(_Column):
                self._ask_folder_sizes(col.rootIndex())
        self.viewport().update()
        for col in self.findChildren(_Column):
            col.viewport().update()

    def _ask_folder_sizes(self, parent: QModelIndex):
        """Folder sizes for one column, from Everything (one quick query, answered in ms)."""
        if not (self.show_sizes and self.folder_sizes_on):
            return
        from . import everything, places
        folder = self.path_of(parent)
        if not folder or places.is_remote(folder):
            return
        c = everything.client()
        if c is None:
            return

        def got(sizes):
            if sizes:
                self._sizes.update(sizes)
                for col in self.findChildren(_Column):
                    if col.rootIndex() == parent:
                        col.viewport().update()
        c.folder_sizes(folder, got)

    def size_for(self, index: QModelIndex):
        """The size to show for a row: files from the file system, folders from Everything."""
        if not index.isValid():
            return None
        if self.fs.isDir(index):
            if not index.parent().isValid():                       # drives: no size here
                return None
            return self._sizes.get(os.path.normcase(os.path.normpath(self.fs.filePath(index))))
        return self.fs.size(index)

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
        self._nav_timer.start()

    def _emit_current(self):
        cur = self.currentIndex()
        if not cur.isValid():
            return
        p = self.path_of(cur)
        if self.fs.isDir(cur):
            self.folderChosen.emit(p)
        else:
            self.fileChosen.emit(p)

    # ----- keyboard -----------------------------------------------------------------------
    def column_for(self, parent: QModelIndex):
        """The column listing parent's contents, if it's open."""
        for c in self.findChildren(_Column):
            if c.rootIndex() == parent and c.isVisible():
                return c
        return None

    def _go_to(self, idx: QModelIndex):
        self.setCurrentIndex(idx)
        self.selectionModel().select(idx, self.selectionModel().SelectionFlag.ClearAndSelect)
        col = self.column_for(idx.parent())
        if col is not None:
            col.setFocus()
            col.scrollTo(idx)

    def go_in(self):
        """→: into the selected folder (its first item). A folder still being read is entered
        as soon as it's loaded."""
        cur = self.currentIndex()
        if not cur.isValid() or not self.fs.isDir(cur):
            return
        if self.fs.rowCount(cur):
            self._pending_in = None
            self._go_to(self.fs.index(0, 0, cur))
        else:
            self._pending_in = self.path_of(cur)
            if self.fs.canFetchMore(cur):
                self.fs.fetchMore(cur)

    def _dir_loaded(self, path: str):
        if self._pending_in and os.path.normcase(os.path.normpath(path)) == os.path.normcase(self._pending_in):
            cur = self.currentIndex()
            if cur.isValid() and os.path.normcase(self.path_of(cur)) == os.path.normcase(self._pending_in):
                QTimer.singleShot(0, self.go_in)
            else:
                self._pending_in = None

    def go_out(self):
        """←: back to the parent folder, one level at a time up to the drives."""
        self._pending_in = None
        cur = self.currentIndex()
        if cur.isValid() and cur.parent().isValid():
            self._go_to(cur.parent())

    def searching(self) -> bool:
        typed, when, _col = self._search
        return bool(typed) and time.monotonic() - when <= 1.0

    def type_search(self, text: str):
        """Typing jumps to a matching name in the newest column: inside the selected folder,
        or the current column when a file is selected. Letters typed within a second add up."""
        typed, when, col = self._search
        now = time.monotonic()
        cur = self.currentIndex()
        if now - when > 1.0 or col is None or not col.isVisible():
            typed = ""
            parent = cur if (cur.isValid() and self.fs.isDir(cur) and self.fs.rowCount(cur)) else \
                (cur.parent() if cur.isValid() else QModelIndex())
            col = self.column_for(parent)
        if col is None:
            return
        typed += text.lower()
        self._search = (typed, now, col)
        root = col.rootIndex()
        n = self.fs.rowCount(root)
        names = [(i, self.fs.fileName(self.fs.index(i, 0, root)).lower()) for i in range(n)]
        hit = next((i for i, nm in names if nm.startswith(typed)), None)
        if hit is None:
            hit = next((i for i, nm in names if typed in nm), None)
        if hit is not None:
            self._go_to(self.fs.index(hit, 0, root))

    def _activated(self, idx: QModelIndex):
        if idx.isValid() and not self.fs.isDir(idx):
            self.openRequested.emit(self.selected_paths())
