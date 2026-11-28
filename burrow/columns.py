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


_CLOUD_CACHE: dict = {}


def _cloud_state_of(path: str, is_dir: bool):
    """Sync status for columns view (QFileSystemModel has no attributes): a stat, cached 15 s."""
    import time as _t
    hit = _CLOUD_CACHE.get(path)
    now = _t.monotonic()
    if hit is not None and now - hit[1] < 15:
        return hit[0]
    from .model import cloud_state
    try:
        attrs = getattr(os.stat(path, follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        attrs = 0
    st = cloud_state(path, attrs, is_dir)
    if len(_CLOUD_CACHE) > 20000:
        _CLOUD_CACHE.clear()
    _CLOUD_CACHE[path] = (st, now)
    return st


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
        from .model import in_cloud
        path = os.path.normpath(index.model().filePath(index))
        cloud = _cloud_state_of(path, index.model().isDir(index)) if in_cloud(path) else None
        # Right end of the row, from the edge in: 6 px · size · 10 px · cloud (18 px); the name
        # ends 10 px before whatever comes first.
        r = option.rect
        font = option.font
        font.setPointSizeF(max(6.5, font.pointSizeF() * 0.82))
        text = compact_size(size) if size is not None else ""
        from PySide6.QtGui import QFontMetrics
        size_w = QFontMetrics(font).horizontalAdvance(text) + 16 if text else 6
        cloud_w = 18 if cloud else 0
        tail = size_w + cloud_w + (4 if cloud else 0)
        if tail <= 6:
            super().paint(painter, option, index)
        else:
            from PySide6.QtWidgets import QStyle, QStyleOptionViewItem
            full = QStyleOptionViewItem(option)                      # selection/hover across the whole row
            self.initStyleOption(full, index)
            style = full.widget.style() if full.widget else view.style()
            style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, full, painter, full.widget)
            opt = QStyleOptionViewItem(option)
            opt.rect = r.adjusted(0, 0, -tail, 0)                 # the name ends before the tail
            super().paint(painter, opt, index)
            if text:
                painter.save()
                painter.setFont(font)
                c = QColor(option.palette.color(QPalette.ColorRole.Text))
                c.setAlpha(150)
                painter.setPen(c)
                painter.drawText(r.adjusted(0, 0, -6, 0), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, text)
                painter.restore()
            if cloud:
                from . import theme
                theme.paint_cloud(painter, r.adjusted(r.width() - size_w - cloud_w, 0, -size_w, 0), cloud,
                                  option.palette.color(QPalette.ColorRole.PlaceholderText))
        from . import marks, tags
        if marks.MARKS:
            k = marks.MARKS.get(os.path.normpath(index.model().filePath(index)).lower())
            if k:
                painter.fillRect(option.rect, marks.color(k))
        store = tags.store()
        if tags.ENABLED and store.items:                     # tag dots, as in the list
            ids = store.get(os.path.normpath(index.model().filePath(index)))
            if ids:
                from PySide6.QtCore import QPointF
                from PySide6.QtGui import QPainter
                painter.save()
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setPen(option.palette.color(QPalette.ColorRole.Base))
                x, cy = option.rect.left() + 4, option.rect.top() + 6
                for tid in ids:                               # small dots in the icon's corner
                    painter.setBrush(QColor(tags.TAG_COLORS[tid]))
                    painter.drawEllipse(QPointF(x, cy), 3.5, 3.5)
                    x += 7
                painter.restore()

    # ----- inline rename (F2), checked and applied by Burrow like in the list -----------------
    def setEditorData(self, editor, index):
        """Start with the name selected but not the extension, like Explorer."""
        editor.setText(index.model().fileName(index))
        text = editor.text()
        n = text.rfind(".") if (not index.model().isDir(index) and "." in text[1:]) else len(text)
        def select():
            try:
                editor.setSelection(0, n)
            except RuntimeError:                  # the editor closed already
                pass
        QTimer.singleShot(0, select)

    def setModelData(self, editor, model, index):
        from . import tags
        from .model import name_problem
        view = self.parent()
        old = view.path_of(index)
        name = editor.text().strip().rstrip(".")
        if not name or name == os.path.basename(old):
            return
        problem = name_problem(name)
        if problem:
            view.renameFailed.emit(problem)
            return
        new = os.path.join(os.path.dirname(old), name)
        if os.path.exists(new) and os.path.normcase(new) != os.path.normcase(old):
            view.renameFailed.emit(f"“{name}” already exists here.")
            return
        try:
            from . import fileops
            fileops.release_files([old])
            os.rename(old, new)
        except OSError as ex:
            view.renameFailed.emit(f"Couldn't rename: {ex.strerror or ex}")
            return
        tags.store().renamed(old, new)
        QTimer.singleShot(200, lambda: view.select_path(new))     # once the model has the new name


STEP_KEYS = (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown,
             Qt.Key.Key_Home, Qt.Key.Key_End)


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
        if not index.isValid() or index.parent() != self.rootIndex():
            return                                 # another column's item (one shared selection)
        if hint != QAbstractItemView.ScrollHint.EnsureVisible:
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
        if ev.modifiers() & Qt.KeyboardModifier.ControlModifier and ev.angleDelta().y():
            if self.owner.icon_wheel_handler is not None:     # Ctrl+wheel: icon size
                self.owner.icon_wheel_handler(1 if ev.angleDelta().y() > 0 else -1)
            return
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
        if not mods and k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.owner._activated(self.owner.currentIndex())     # folder: in; file: open
            return
        if not mods and k in STEP_KEYS:
            self.owner.step(k)                   # ↑ ↓ PgUp PgDn Home End: always in the column
            return                               # that holds the current item, never Qt's own
        text = ev.text()
        typing = self.owner.searching()          # a space only counts in the middle of a name
        if (k == Qt.Key.Key_Space and not mods and not typing and self.owner.space_handler is not None
                and self.owner.space_handler()):
            return                               # Space played/paused the media preview
        if text and text.isprintable() and (typing or not text.isspace())                 and not (mods & ~Qt.KeyboardModifier.ShiftModifier):
            if self.owner.search_handler is None or not self.owner.search_handler(text):
                self.owner.type_search(text)       # no Everything: jump to a name in this column
            return
        super().keyPressEvent(ev)

    def mousePressEvent(self, ev):
        """Back / forward mouse buttons navigate; the middle button opens a folder in a new tab."""
        b = ev.button()
        if b == Qt.MouseButton.BackButton:
            self.owner.backRequested.emit()
            return
        if b == Qt.MouseButton.ForwardButton:
            self.owner.forwardRequested.emit()
            return
        if b == Qt.MouseButton.MiddleButton:
            idx = self.indexAt(ev.position().toPoint())
            if idx.isValid() and self.owner.fs.isDir(idx):
                self.owner.newTabRequested.emit(self.owner.path_of(idx))
            return
        super().mousePressEvent(ev)
        idx = self.indexAt(ev.position().toPoint())
        if idx.isValid() and b == Qt.MouseButton.LeftButton and not ev.modifiers():
            self.owner._go_to(idx)               # the cursor and the keys go where you clicked
        elif self.isVisible():
            self.setFocus()

    def mouseDoubleClickEvent(self, ev):
        """Double-click: a folder opens (its first item gets the cursor), a file opens in its app."""
        if ev.button() != Qt.MouseButton.LeftButton:
            return super().mouseDoubleClickEvent(ev)
        idx = self.indexAt(ev.position().toPoint())
        if idx.isValid():
            self.owner._activated(idx)
        ev.accept()

    def hideEvent(self, ev):
        """Qt closes columns by hiding them: if the keyboard was here, hand it to the column
        you're in instead of letting Windows pass it on (to the sidebar)."""
        had = self.hasFocus()
        super().hideEvent(ev)
        if had:
            QTimer.singleShot(0, self.owner.focus_current)

    # ----- drops: onto a folder, or into this column's folder (copy / move like the list) -------
    @staticmethod
    def _paths(md):
        if not md.hasUrls():
            return []
        return [os.path.normpath(u.toLocalFile()) for u in md.urls() if u.isLocalFile()]

    def _evaluate(self, ev):
        from . import fileops
        paths = self._paths(ev.mimeData())
        idx = self.indexAt(ev.position().toPoint())
        if idx.isValid() and self.owner.fs.isDir(idx):
            target = self.owner.path_of(idx)
        else:
            target = self.owner.path_of(self.rootIndex())       # "" in the drives column
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
        ev.setDropAction(Qt.DropAction.CopyAction)     # we move it ourselves (see the list)
        ev.accept()
        paths, target, move = r
        QTimer.singleShot(0, lambda: self.owner.dropRequested.emit(paths, target, move))


class ColumnsView(QColumnView):
    """Miller columns over QFileSystemModel, with Burrow's keys, scrolling and file actions."""
    folderChosen = Signal(str)        # a folder is now the deepest column: it's the tab's folder
    fileChosen = Signal(str)          # a file is selected: its folder is the tab's folder
    openRequested = Signal(list)      # Enter / double-click on files
    contextMenu = Signal(object)      # pos in viewport coordinates
    dropRequested = Signal(list, str, bool)   # sources, target folder, move?
    columnSelectionChanged = Signal()         # a column's own selection changed (Ctrl/Shift-click)
    renameFailed = Signal(str)
    backRequested = Signal()
    forwardRequested = Signal()
    newTabRequested = Signal(str)
    search_handler = None             # set by the tab: typing searches with Everything
    space_handler = None              # set by the tab: Space plays / pauses media previews
    icon_wheel_handler = None         # set by the tab: Ctrl+wheel changes the icon size
    show_sizes = False                # Settings: small sizes after the names (class defaults:
    folder_sizes_on = True            # columns are created during setModel, before __init__ ends)
    extra_actions: tuple = ()         # the window's file actions (set per view); createColumn runs
                                      # during setModel, before __init__ finishes

    def __init__(self, show_hidden: bool, row_height: int = 24, parent=None):
        super().__init__(parent)
        self.fs = QFileSystemModel(self)
        self.fs.setReadOnly(False)                # editable for inline rename, which the delegate
                                                  # does itself; drops and moves go through Burrow
        self.fs.setRootPath("")                   # "This PC": drives in the first column
        # columns copy these from the view (initializeColumn): pixel scrolling for smooth glides
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.set_show_hidden(show_hidden)
        self.setModel(self.fs)
        self._delegate = _RowDelegate(row_height, self)
        self.setItemDelegate(self._delegate)
        # the last column previews the selected file (like Finder); Qt shows it for files only
        from .colpreview import ColumnPreview
        self.preview = ColumnPreview()
        self.setPreviewWidget(self.preview)
        holder = self.preview.parentWidget()     # Qt's preview column is a scroll area: no bars
        while holder is not None and not isinstance(holder, QAbstractItemView):
            holder = holder.parentWidget()
        if holder is not None:
            holder.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            holder.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            holder.setFrameShape(QFrame.Shape.NoFrame)
        self.updatePreviewWidget.connect(lambda idx: self.preview.show_path(self.path_of(idx)))
        for col in self.findChildren(QListView):      # the drives column was made in setModel,
            col.setItemDelegate(self._delegate)       # with Qt's delegate (which draws arrows)
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
        # a selected folder shows what's in it in the next column (a peek: just its list), once
        # the cursor rests a moment, so arrowing quickly down a list never reads every folder
        self._peek_timer = QTimer(self)
        self._peek_timer.setSingleShot(True)
        self._peek_timer.setInterval(80)
        self._peek_timer.timeout.connect(self._peek)
        self._pending_in = None                 # a folder → was pressed on while still loading
        self.fs.directoryLoaded.connect(self._dir_loaded)
        self._sizes: dict[str, int] = {}        # folder path (normcase) -> size, from Everything
        self._search = ("", 0.0, None)          # typed text, when, the column searched

    def createColumn(self, index):
        """Each column is a _Column wired to the window's menu, actions and icon size."""
        col = _Column(self, self.viewport())    # our own list: smooth scrolling and arrow keys
        self.initializeColumn(col)
        # Each column keeps its own selection, as Qt designs it (sharing the view's let Python
        # delete it with the first column that closed). The view's current item is the cursor;
        # _mark() shows it in its column. Ctrl/Shift-clicks in a column report here.
        col.selectionModel().selectionChanged.connect(lambda *_: self.columnSelectionChanged.emit())
        col.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        col.setUniformItemSizes(True)            # every row is the same: measure one, not 5,000
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
        col.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)   # F2 only
        col.setAcceptDrops(True)
        col.viewport().setAcceptDrops(True)
        col.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        cur = self.currentIndex()
        if not (cur.isValid() and index == cur):   # a peek column: sizes wait until you go in
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

    FS_SORT = {"name": 0, "size": 1, "type": 2, "modified": 3}     # QFileSystemModel's columns

    def set_sort(self, key: str, desc: bool):
        """Sort every column like the tab (the toolbar's Sort). Windows' model keeps folders
        first when sorting by name or size; other keys fall back to name."""
        order = Qt.SortOrder.DescendingOrder if desc else Qt.SortOrder.AscendingOrder
        col = self.FS_SORT.get(key, 0)
        if (col, order) != getattr(self, "_sorted", (0, Qt.SortOrder.AscendingOrder)):
            self._sorted = (col, order)
            self.fs.sort(col, order)

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
            self._mark(idx)
            self.scrollTo(idx)
        finally:
            self._quiet = False
        QTimer.singleShot(0, lambda: self._mark(self.currentIndex()))   # its column may be new

    def select_path(self, path: str):
        """Select one item (after a rename), keeping the columns where they are."""
        idx = self.fs.index(path)
        if idx.isValid():
            self._go_to(idx)

    def edit_current(self) -> bool:
        """F2: rename the current item in place (not drives)."""
        cur = self.currentIndex()
        if not cur.isValid() or not cur.parent().isValid():
            return False
        col = self.column_for(cur.parent())
        if col is None:
            return False
        col.edit(cur)
        return True

    def path_of(self, idx: QModelIndex) -> str:
        return os.path.normpath(self.fs.filePath(idx)) if idx.isValid() else ""

    def selected_paths(self) -> list[str]:
        cur = self.currentIndex()
        if not cur.isValid():
            return []
        parent = cur.parent()
        col = self.column_for(parent)
        sm = col.selectionModel() if col is not None else None
        rows = [i for i in sm.selectedIndexes() if i.column() == 0 and i.parent() == parent] if sm else []
        rows.sort(key=lambda i: i.row())
        paths = [self.path_of(i) for i in rows]
        return paths if self.path_of(cur) in paths else [self.path_of(cur)]

    def select_all_in_column(self):
        cur = self.currentIndex()
        if not cur.isValid():
            return
        parent = cur.parent()
        col = self.column_for(parent)
        if col is None or col.selectionModel() is None:
            return
        sm = col.selectionModel()
        n = self.fs.rowCount(parent)
        if n:
            from PySide6.QtCore import QItemSelection
            sm.select(QItemSelection(self.fs.index(0, 0, parent), self.fs.index(n - 1, 0, parent)),
                      sm.SelectionFlag.ClearAndSelect)

    # ----- the user drives us -------------------------------------------------------
    def _current_changed(self, cur: QModelIndex, prev):
        if not cur.isValid():
            return
        self._hide_stale(cur)
        self._peek_timer.start()
        if cur.parent() != (prev.parent() if prev.isValid() else QModelIndex()) and cur.parent().isValid():
            self._ask_folder_sizes(cur.parent())  # went into a folder (or out): now its sizes
        if self._quiet:
            return
        self._nav_timer.start()

    def _peek(self):
        """Show the selected folder's contents in the next column, keeping the keyboard and the
        columns where they are. Only its own list is read (no sizes, nothing deeper)."""
        cur = self.currentIndex()
        if not cur.isValid() or not self.fs.isDir(cur) or self.column_for(cur) is not None:
            return
        here = self.column_for(cur.parent())
        had_focus = here is not None and here.hasFocus()
        hb = self.horizontalScrollBar()
        x = hb.value()
        col = self._any_column(cur)
        if col is None:
            QColumnView.currentChanged(self, cur, QModelIndex())     # Qt makes the column (hidden)
            col = self._any_column(cur)
        if col is None:
            return
        if self.fs.canFetchMore(cur):
            self.fs.fetchMore(cur)
        col.show()
        here = self._mark(cur)
        if here is not None and had_focus:
            here.setFocus()
        hb.setValue(x)
        self._align(cur)

    def _any_column(self, parent: QModelIndex):
        """A column listing parent, shown or not (Qt keeps made columns hidden until used)."""
        for c in self.findChildren(_Column):
            if c.rootIndex() == parent:
                return c
        return None

    def _hide_stale(self, cur: QModelIndex):
        """The cursor moved: a peek at the folder it left goes at once. Only cur's own path
        (the columns leading to it, and cur's contents) stays on show."""
        keep = []
        i = cur
        while i.isValid():
            keep.append(i)
            i = i.parent()
        shown = {}
        for c in self.findChildren(_Column):         # oldest first
            r = c.rootIndex()
            if not r.isValid() or c.isHidden():
                continue
            if r not in keep:
                c.hide()
                continue
            key = self.path_of(r)
            if key in shown:                     # a peek column, then Qt's own when you went in:
                shown[key].hide()                # one is enough (Qt's, the newer)
            shown[key] = c

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
    THIRD = 2                         # the column you're in sits at most this many columns in

    def scrollTo(self, index, hint=QAbstractItemView.ScrollHint.EnsureVisible):
        """Instead of Qt's (which keeps the newest column at the right edge): keep the column
        you're in at the third place from the left, older columns sliding off to the left, so
        the folder you're in and what's in it have the room on the right."""
        if not index.isValid():
            return
        col = self.column_for(index.parent())
        if col is None:
            QTimer.singleShot(0, lambda: self._align(index))    # its column is still being made
            return
        col.scrollTo(index)
        self._align(index)

    def _align(self, index):
        col = self.column_for(index.parent()) if index.isValid() else None
        if col is None:
            return
        hb = self.horizontalScrollBar()
        w = col.width() or 230
        target = max(0, col.x() + hb.value() - self.THIRD * w)
        self._want_x = target
        if target > hb.maximum():                # past the last column: room to slide left
            hb.setMaximum(target)
        if not hasattr(self, "_hanim"):
            hb.rangeChanged.connect(self._keep_range)
            self._hanim = QPropertyAnimation(hb, b"value", self)
            self._hanim.setDuration(140)
            self._hanim.setEasingCurve(QEasingCurve.Type.OutCubic)
        if self._hanim.state() == QPropertyAnimation.State.Running:
            if self._hanim.endValue() == target:
                return
            self._hanim.stop()
        if hb.value() != target:
            self._hanim.setStartValue(hb.value())
            self._hanim.setEndValue(target)
            self._hanim.start()

    def _keep_range(self, _lo, hi):
        """Qt recomputes the scroll range from the columns' total width; keep the extra room
        that holds the current column in third place."""
        want = getattr(self, "_want_x", 0)
        if want > hi:
            self.horizontalScrollBar().setMaximum(want)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        QTimer.singleShot(0, lambda: self._align(self.currentIndex()))

    def column_for(self, parent: QModelIndex):
        """The column listing parent's contents, if it's open."""
        for c in self.findChildren(_Column):
            if c.rootIndex() == parent and not c.isHidden():   # (isVisible is False while the
                return c                                       # view itself is still hidden)
        return None

    def current_column(self):
        """The column holding the current item, else the first one (the drives)."""
        cur = self.currentIndex()
        col = self.column_for(cur.parent()) if cur.isValid() else None
        if col is None:
            col = self.column_for(self.rootIndex())
        return col

    # The keyboard always belongs to a column: Qt's own columns view only knows ← / →, so if
    # it ever gets the focus (focus_list, a click between columns, back at the drives), it hands
    # it straight to the column you're in, and passes on any key that reached it meanwhile.
    def focusInEvent(self, ev):
        super().focusInEvent(ev)
        col = self.current_column()
        if col is not None:
            col.setFocus()

    def focus_current(self):
        """Put the keyboard back in the column you're in: called right after the focused column
        was closed (Windows would otherwise pass the keyboard on, e.g. to the sidebar)."""
        if not self.isVisible():
            return
        col = self.current_column()
        if col is not None and not col.hasFocus():
            col.setFocus()

    def keyPressEvent(self, ev):
        col = self.current_column()
        if col is not None:
            col.setFocus()
            col.keyPressEvent(ev)                # Burrow's handler (never Qt's for the arrows)
            return
        super().keyPressEvent(ev)

    def step(self, key):
        """↑ ↓ PgUp PgDn Home End in the column holding the current item."""
        cur = self.currentIndex()
        parent = cur.parent() if cur.isValid() else self.rootIndex()
        n = self.fs.rowCount(parent)
        if not n:
            return
        row = cur.row() if cur.isValid() else -1
        col = self.column_for(parent)
        rh = max(16, self._delegate.height)
        page = max(1, (col.viewport().height() // rh) - 1) if col is not None else 10
        target = {Qt.Key.Key_Up: row - 1, Qt.Key.Key_Down: row + 1, Qt.Key.Key_PageUp: row - page,
                  Qt.Key.Key_PageDown: row + page, Qt.Key.Key_Home: 0, Qt.Key.Key_End: n - 1}[key]
        if row < 0:
            target = 0
        target = max(0, min(n - 1, target))
        if target != row:
            self._go_to(self.fs.index(target, 0, parent))

    def _mark(self, idx: QModelIndex):
        """Show the cursor in its column: that column's own current item and selection."""
        if not idx.isValid():
            return
        col = self.column_for(idx.parent())
        if col is None:
            return
        sm = col.selectionModel()
        if sm is not None and (sm.currentIndex() != idx or not sm.isSelected(idx)):
            sm.setCurrentIndex(idx, sm.SelectionFlag.ClearAndSelect)
        return col

    def _go_to(self, idx: QModelIndex, focus: bool = True):
        """Move the cursor (the view's current item) to idx: the columns follow."""
        if not idx.isValid():
            return
        self.setCurrentIndex(idx)
        col = self._mark(idx)
        if col is None:                          # its column is being made: mark it next turn
            QTimer.singleShot(0, lambda: self._go_to(idx, focus) if self.column_for(idx.parent()) else None)
            return
        if focus:
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
        if not idx.isValid():
            return
        if self.fs.isDir(idx):
            if self.currentIndex() != idx:
                self._go_to(idx)
            self.go_in()
        else:
            self.openRequested.emit(self.selected_paths())
