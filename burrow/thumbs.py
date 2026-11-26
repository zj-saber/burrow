"""Thumbnails and River views, and the search-results column of columns view.

All three are extra views of the tab's own list (the same filtered model and the same
selection), so copy, delete, rename, the inspector, search and the status bar work in them
exactly as in the list. Only the drawing differs:

- Thumbnails: a grid of pictures with the names under them.
- River: photos at one height, each as wide as its picture, flowing in rows (no cropping).
- ResultsColumn: search results in columns view, drawn as one wide column in place of the
  folder's own column, so the columns stay on screen while you search.

Pictures are Windows' own thumbnails (the ones Explorer shows, mostly from its cache), made
on three background threads, newest request first (what's on screen now), and kept in a
small memory cache. Folders and files without a thumbnail show their icon.
"""
from __future__ import annotations

import os
import threading
from collections import OrderedDict

from PySide6.QtCore import (QModelIndex, QObject, QPoint, QRect, QRectF, QRunnable, QSize, Qt, QThreadPool,
                            QTimer, Signal, Slot)
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPalette, QPixmap
from PySide6.QtWidgets import QAbstractItemView, QListView, QStyle, QStyledItemDelegate

from . import theme
from .model import ENTRY_ROLE

THUMB_PX = 320                      # made once at this size, scaled down when drawn
CACHE_BYTES = 96 << 20              # ~96 MB of pictures at most, oldest dropped first
WORKERS = 3


def _qt_image_exts() -> set:
    from PySide6.QtGui import QImageReader
    global _EXTS
    if _EXTS is None:
        _EXTS = {bytes(f.data()).decode().lower() for f in QImageReader.supportedImageFormats()}
    return _EXTS


_EXTS = None


# ----- the loader ---------------------------------------------------------------------------
class _Relay(QObject):
    done = Signal(object, object)   # key, QImage or None (emitted from a worker thread)


class _Job(QRunnable):
    """A worker: takes the newest request until there are none left."""

    def __init__(self, loader):
        super().__init__()
        self.loader = loader

    def run(self):
        from .raw import shell_thumbnail
        while True:
            key = self.loader._next()
            if key is None:
                return
            try:
                img = shell_thumbnail(key[0], THUMB_PX)
                if not img.isNull() and max(img.width(), img.height()) > THUMB_PX:
                    img = img.scaled(THUMB_PX, THUMB_PX, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
            except Exception:                    # noqa: BLE001 - a bad file never stops the others
                img = None
            self.loader._relay.done.emit(key, img)


class ThumbLoader(QObject):
    """Thumbnails by (path, modified time): get() answers from the cache or queues the file."""
    ready = Signal()                         # some thumbnails arrived (batched, ~60 ms)

    def __init__(self):
        super().__init__()
        self._cache: OrderedDict = OrderedDict()
        self._bytes = 0
        self._failed: set = set()            # no thumbnail (Windows has none): show the icon
        self._lock = threading.Lock()
        self._stack: list = []               # newest last: what's on screen now goes first
        self._queued: set = set()
        self._workers = 0
        self._relay = _Relay()
        self._relay.done.connect(self._finished)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(WORKERS)
        self._notify = QTimer(self)
        self._notify.setSingleShot(True)
        self._notify.setInterval(60)
        self._notify.timeout.connect(self.ready.emit)

    def peek(self, path: str, mtime) -> QPixmap | None:
        """The cached picture, without asking for one."""
        return self._cache.get((path, mtime))

    def get(self, path: str, mtime) -> QPixmap | None:
        key = (path, mtime)
        pm = self._cache.get(key)
        if pm is not None:
            self._cache.move_to_end(key)
            return pm
        if key in self._failed:
            return None
        start = False
        with self._lock:
            if key not in self._queued:
                self._queued.add(key)
                self._stack.append(key)
                if len(self._stack) > 400:           # scrolled far past these: forget them
                    self._queued.discard(self._stack.pop(0))
            if self._workers < WORKERS:
                self._workers += 1
                start = True
        if start:
            self._pool.start(_Job(self))
        return None

    def _next(self):
        with self._lock:
            if self._stack:
                return self._stack.pop()
            self._workers -= 1
            return None

    @Slot(object, object)
    def _finished(self, key, img):
        with self._lock:
            self._queued.discard(key)
        if img is None or img.isNull():
            self._failed.add(key)
        else:
            pm = QPixmap.fromImage(img)
            self._cache[key] = pm
            self._bytes += pm.width() * pm.height() * 4
            while self._bytes > CACHE_BYTES and self._cache:
                _k, old = self._cache.popitem(last=False)
                self._bytes -= old.width() * old.height() * 4
        self._notify.start()

    def drop_queue(self):
        """A new folder: stop making pictures for the old one."""
        with self._lock:
            self._stack.clear()
            self._queued.clear()

    def clear(self):
        self.drop_queue()
        self._cache.clear()
        self._bytes = 0
        self._failed.clear()


# River needs each picture's shape before its thumbnail exists: read just the image header
# (a few KB) for every picture in the folder, on a worker thread. path -> width / height.
ASPECTS: dict = {}


def read_aspects(paths: list[str]) -> dict:
    from PySide6.QtGui import QImageReader
    out = {}
    for p in paths:
        r = QImageReader(p)
        r.setAutoTransform(True)                 # phone photos stored sideways
        s = r.size()
        if s.isValid() and s.height() > 0:
            w, h = s.width(), s.height()
            try:
                if int(r.transformation().value) & 0x4:   # rotated 90°: swap
                    w, h = h, w
            except (AttributeError, TypeError, ValueError):
                pass
            out[p] = w / h
    return out


_loader: ThumbLoader | None = None


def loader() -> ThumbLoader:
    global _loader
    if _loader is None:
        _loader = ThumbLoader()
    return _loader


# ----- what the list does, for these views too ------------------------------------------------
class _Extras:
    """Keys, mouse buttons, Ctrl+wheel and drops, the same as in the list (FileTree)."""
    owner = None
    left_closes = False                      # results column: ← ends the search

    def keyPressEvent(self, ev):
        o = self.owner
        mods = ev.modifiers() & ~(Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.KeypadModifier)
        text = ev.text()
        k = ev.key()
        if k == Qt.Key.Key_Space and not o.filter_text() and not mods \
                and o.space_handler is not None and o.space_handler():
            return                                   # Space played/paused the media preview
        if k == Qt.Key.Key_Escape and o.filter_text():
            o.clear_filter()
            return
        if self.left_closes and k == Qt.Key.Key_Left and not mods:
            o.clear_filter()
            return
        if self.left_closes and k == Qt.Key.Key_Right and not mods:
            e = o.proxy.entry(self.currentIndex())
            if e is not None and e.is_dir:
                o.open_entries([e])
            return
        if text and text.isprintable() and not mods and not (text == " " and not o.filter_text()):
            o.type_into_filter(text)
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

    def wheelEvent(self, ev):
        if ev.modifiers() & Qt.KeyboardModifier.ControlModifier and ev.angleDelta().y():
            if self.owner.icon_wheel_handler is not None:
                self.owner.icon_wheel_handler(1 if ev.angleDelta().y() > 0 else -1)
            return
        super().wheelEvent(ev)

    @staticmethod
    def _paths(md):
        if not md.hasUrls():
            return []
        return [os.path.normpath(u.toLocalFile()) for u in md.urls() if u.isLocalFile()]

    def _evaluate(self, ev):
        from . import fileops, places
        paths = self._paths(ev.mimeData())
        e = self.owner.proxy.entry(self.indexAt(ev.position().toPoint()))
        if e is not None and e.is_dir:
            target = e.path
        else:
            p = self.owner.path
            target = None if (places.is_special(p) or places.is_server(p)) else p
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
        ev.setDropAction(Qt.DropAction.CopyAction)   # we move it ourselves (see the list)
        ev.accept()
        paths, target, move = r
        QTimer.singleShot(0, lambda: self.owner.tree.dropRequested.emit(paths, target, move))


def _setup(view, owner):
    """Shared set-up: the tab's model and selection, its menu, actions and drag and drop."""
    view.owner = owner
    view.setModel(owner.proxy)
    view.setSelectionModel(owner.tree.selectionModel())     # one selection for every view
    view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)   # like the list
    view.setFrameShape(QListView.Shape.NoFrame)
    view.setMouseTracking(True)
    view.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
    view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    view.verticalScrollBar().setSingleStep(24)
    view.setDragEnabled(True)
    view.setAcceptDrops(True)
    view.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
    view.setDefaultDropAction(Qt.DropAction.CopyAction)
    view.setEditTriggers(QAbstractItemView.EditTrigger.EditKeyPressed)
    view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    view.customContextMenuRequested.connect(lambda pos: owner.contextMenuRequested.emit(pos))
    view.activated.connect(owner._activated)
    view.addActions(owner.tree.actions())                    # Ctrl+C, Del, F2... (window actions)
    view.hover_color = QColor(0, 0, 0, 0)


# ----- Thumbnails and River -----------------------------------------------------------------
class ThumbDelegate(QStyledItemDelegate):
    def __init__(self, view):
        super().__init__(view)
        self.view = view

    def sizeHint(self, option, index):
        return self.view.item_size(index)

    def paint(self, p, option, index):
        v, r = self.view, QRect(option.rect)
        e = index.data(ENTRY_ROLE)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        pal = option.palette
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        hl = pal.color(QPalette.ColorGroup.Active if option.state & QStyle.StateFlag.State_Active
                       else QPalette.ColorGroup.Inactive, QPalette.ColorRole.Highlight)
        if not v.river:
            if selected:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(hl)
                p.drawRoundedRect(QRectF(r).adjusted(2, 2, -2, -2), 8, 8)
            elif hovered:
                theme.paint_hover(p, r.adjusted(-1, 0, 1, 0), v.hover_color)
        pic = v.picture_rect(r)
        pm = v.thumb_for(e)
        if pm is not None:
            if v.river:                               # the whole tile, rounded
                target = QRectF(pic)
            else:                                     # fitted and centred in the square
                s = pm.size().scaled(pic.size(), Qt.AspectRatioMode.KeepAspectRatio)
                target = QRectF(pic.x() + (pic.width() - s.width()) / 2, pic.y() + (pic.height() - s.height()) / 2,
                                s.width(), s.height())
            path = QPainterPath()
            path.addRoundedRect(target, 6, 6)
            p.setClipPath(path)
            p.drawPixmap(target.toRect(), pm)
            p.setClipping(False)
        else:
            ic = index.data(Qt.ItemDataRole.DecorationRole)
            side = int(min(pic.width(), pic.height()) * (0.62 if e is not None and e.is_dir else 0.5))
            if v.river:
                p.fillRect(pic, QColor(128, 128, 128, 30))
            if ic is not None:
                ic.paint(p, QRect(pic.center().x() - side // 2, pic.center().y() - side // 2, side, side))
        if v.river:
            if selected:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(hl.red(), hl.green(), hl.blue(), 90))
                p.drawRoundedRect(QRectF(pic), 6, 6)
                p.setPen(hl)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(QRectF(pic).adjusted(1, 1, -1, -1), 6, 6)
            elif hovered:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 255, 255, 34))
                p.drawRoundedRect(QRectF(pic), 6, 6)
        else:
            text = index.data(Qt.ItemDataRole.DisplayRole) or ""
            tr = QRect(r.left() + 6, pic.bottom() + 4, r.width() - 12, option.fontMetrics.height() + 2)
            p.setPen(pal.color(QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text))
            p.drawText(tr, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                       option.fontMetrics.elidedText(text, Qt.TextElideMode.ElideMiddle, tr.width()))
        from . import marks
        if marks.MARKS and e is not None:
            k = marks.MARKS.get(os.path.normpath(e.path).lower())
            if k:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(marks.color(k))
                p.drawRoundedRect(QRectF(r).adjusted(2, 2, -2, -2), 8, 8)
        p.restore()


class ThumbView(_Extras, QListView):
    """Thumbnails (river=False): a grid of pictures with names. River (river=True): pictures at
    one height in flowing rows, each as wide as its picture; the name is in the tooltip."""

    def __init__(self, owner, river: bool, parent=None):
        super().__init__(parent)
        self.river = river
        _setup(self, owner)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setUniformItemSizes(not river)
        self.setSpacing(4 if river else 2)
        self.setWordWrap(False)
        self.size_px = 160
        self.setItemDelegate(ThumbDelegate(self))
        self._relayout = QTimer(self)                 # river: pictures arrived, widths changed
        self._relayout.setSingleShot(True)
        self._relayout.setInterval(120)
        self._relayout.timeout.connect(self._redo_layout)
        loader().ready.connect(self._thumbs_arrived)
        self._shapes_token = 0
        if river:
            owner.proxy.modelReset.connect(self._read_shapes)
            owner.proxy.layoutChanged.connect(self._read_shapes)

    def showEvent(self, ev):
        super().showEvent(ev)
        if self.river:
            self._read_shapes()                  # the folder may have loaded before River was on

    # sizes ------------------------------------------------------------------------------------
    def set_size(self, icon_px: int):
        """The status-bar slider (16-64 px icons) scales the pictures too."""
        self.size_px = (120 + (icon_px - 16) * 4) if self.river else (96 + (icon_px - 16) * 8 // 3)
        self.doItemsLayout()

    def item_size(self, index) -> QSize:
        s = self.size_px
        if self.river:
            return QSize(max(s // 2, round(s * self.aspect(index.data(ENTRY_ROLE)))), s)
        return QSize(s + 16, s + self.fontMetrics().height() + 16)

    def picture_rect(self, r: QRect) -> QRect:
        if self.river:
            return QRect(r)
        s = self.size_px
        return QRect(r.left() + (r.width() - s) // 2, r.top() + 6, s, s)

    def aspect(self, e) -> float:
        """Width / height of the picture (known once its thumbnail is made)."""
        if e is None or e.is_dir or e.is_drive:
            return 1.0
        a = ASPECTS.get(e.path)
        if a is None:
            pm = loader().peek(e.path, e.mtime)
            if pm is None or not pm.height():
                return 1.5
            a = pm.width() / pm.height()
        return max(0.4, min(3.0, a))

    def thumb_for(self, e):
        if e is None or e.is_dir or e.is_drive:
            return None
        return loader().get(e.path, e.mtime)          # queued if missing; drawn when ready

    # repaint / relayout as pictures arrive ------------------------------------------------------
    def _read_shapes(self):
        """River: read every picture's shape (headers only, off the GUI thread), then lay out once."""
        from . import places
        model = self.owner.model
        todo = [e.path for e in model.entries if not e.is_dir and e.path not in ASPECTS
                and os.path.splitext(e.name)[1][1:].lower() in _qt_image_exts()][:5000]
        if not todo or places.is_remote(todo[0]):
            return
        self._shapes_token += 1
        token = self._shapes_token

        def done(res, token=token):
            if isinstance(res, dict):
                ASPECTS.update(res)
            if token == self._shapes_token:
                self._redo_layout()
        places.run_async(lambda: read_aspects(todo), done)

    def _thumbs_arrived(self):
        if not self.isVisible():
            return
        if self.river and self._needs_shapes():
            self._relayout.start()               # only pictures Qt can't read (HEIC, RAW...)
        self.viewport().update()

    def _needs_shapes(self) -> bool:
        """A picture on show still has a guessed shape that its new thumbnail can correct."""
        found = False
        for e in self.owner.model.entries:
            if not e.is_dir and e.path not in ASPECTS:
                pm = loader().peek(e.path, e.mtime)
                if pm is not None:
                    ASPECTS[e.path] = pm.width() / max(1, pm.height())
                    found = True
        return found

    def _top_item(self):
        """The first item showing at the top (searched across the row, past the gaps)."""
        vw = self.viewport().width()
        for y in (4, 12, 24, 48):
            for x in range(4, max(5, vw), 24):
                idx = self.indexAt(QPoint(x, y))
                if idx.isValid():
                    return idx
        return QModelIndex()

    def _redo_layout(self):
        """Re-flow the rows, keeping the same pictures where they were on screen."""
        top = self._top_item()
        off = self.visualRect(top).top() if top.isValid() else 0
        value = self.verticalScrollBar().value()
        self.doItemsLayout()

        def restore():
            vb = self.verticalScrollBar()
            if top.isValid():
                vb.setValue(vb.value() + self.visualRect(top).top() - off)
            else:
                vb.setValue(value)
        restore()
        QTimer.singleShot(0, restore)            # again once Qt has finished the layout


# ----- columns view: search results as one wide column ---------------------------------------
class _ResultDelegate(QStyledItemDelegate):
    def __init__(self, view):
        super().__init__(view)
        self.view = view

    def sizeHint(self, option, index):
        s = super().sizeHint(option, index)
        return QSize(s.width(), max(s.height(), self.view.row_h))

    def paint(self, p, option, index):
        if option.state & QStyle.StateFlag.State_MouseOver and not option.state & QStyle.StateFlag.State_Selected:
            theme.paint_hover(p, option.rect, self.view.hover_color)
            option.state &= ~QStyle.StateFlag.State_MouseOver
        super().paint(p, option, index)


class ResultsColumn(_Extras, QListView):
    """Search results inside columns view, over the searched folder's column and to its right.
    ← or Esc ends the search; Enter / → opens a result."""
    left_closes = True

    def __init__(self, owner, parent):
        super().__init__(parent)
        _setup(self, owner)
        self.row_h = 24
        self.setItemDelegate(_ResultDelegate(self))
        self.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.setUniformItemSizes(True)
        self.setAutoFillBackground(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
