"""Zoomable preview for photos and PDFs (inspector and a pop-out viewer).

- Images: the preview loads a screen-sized copy; zooming past it loads the full-resolution
  image on a worker thread, so browsing with the arrow keys stays fast.
- PDFs: Qt's PDF module (imported on first use) renders the current page at exactly the
  zoom level shown, so text stays sharp. Pages: buttons, PgUp/PgDn, or the wheel at the page edge.

Mouse/touchpad: the wheel, two-finger scrolling and pinch zoom (photos and PDFs) around the
pointer; Shift+wheel scrolls; drag pans; double-click toggles fit / 100%.
Keys: + / - zoom, 0 fit, 1 actual size, PgUp/PgDn pages.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QImageReader, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (QDialog, QFrame, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView,
                               QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget)

from . import places, theme

PREVIEW_MAX = 1600          # side length of the quick preview image
FULL_MAX = 12000            # cap for the full-resolution image (memory)
MIN_ZOOM, MAX_ZOOM = 0.05, 16.0


class _View(QGraphicsView):
    zoomRequested = Signal(float, object)     # factor, anchor (scene pos) or None
    pageRequested = Signal(int)               # +1 / -1
    fitToggled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRenderHints(QPainter.RenderHint.SmoothPixmapTransform | QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.wheel_zooms = True

    def wheelEvent(self, ev):
        """Mouse wheel and two-finger touchpad scrolling zoom (photos and PDFs), centred on the
        pointer. Touchpads send many small steps, so the zoom follows them smoothly.
        Shift+wheel scrolls instead."""
        if ev.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            super().wheelEvent(ev)
            return
        dy = ev.angleDelta().y() or ev.angleDelta().x()
        if not dy and not ev.pixelDelta().isNull():
            dy = ev.pixelDelta().y() * 2
        if dy:
            self.zoomRequested.emit(1.25 ** (dy / 120), self.mapToScene(ev.position().toPoint()))
        ev.accept()

    def viewportEvent(self, ev):
        # pinch on a precision touchpad / touchscreen
        if ev.type() == ev.Type.NativeGesture and ev.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
            self.zoomRequested.emit(1.0 + ev.value(), self.mapToScene(ev.position().toPoint()))
            return True
        return super().viewportEvent(ev)

    def mouseDoubleClickEvent(self, ev):
        self.fitToggled.emit()


class ZoomView(QWidget):
    """Shows an image file or a PDF, fit to the space, with zoom and page controls."""
    popOutRequested = Signal()

    def __init__(self, parent=None, show_popout=True):
        super().__init__(parent)
        self.scene = QGraphicsScene(self)
        self.item = QGraphicsPixmapItem()
        self.item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.scene.addItem(self.item)
        self.view = _View(self)
        self.view.setScene(self.scene)
        self.view.zoomRequested.connect(self._zoom_by)
        self.view.pageRequested.connect(lambda d: self.go_page(self.page + d, from_edge=d))
        self.view.fitToggled.connect(self.toggle_fit)

        def btn(text, tip, slot, glyph=None):
            b = QToolButton()
            b.setAutoRaise(True)
            b.setToolTip(tip)
            b.setText(text)
            b.clicked.connect(slot)
            return b
        self.b_out = btn("−", "Zoom out (-)", lambda: self._zoom_by(1 / 1.25, None))
        self.zoom_lbl = QLabel("100%")
        self.zoom_lbl.setMinimumWidth(42)
        self.zoom_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_lbl.setProperty("role", "key")
        self.b_in = btn("+", "Zoom in (+)", lambda: self._zoom_by(1.25, None))
        self.b_fit = btn("Fit", "Fit (0). Scroll or pinch to zoom, drag to pan, double-click toggles fit / 100%", self.fit)
        self.b_prev = btn("‹", "Previous page (PgUp)", lambda: self.go_page(self.page - 1))
        self.page_lbl = QLabel("")
        self.page_lbl.setProperty("role", "key")
        self.b_next = btn("›", "Next page (PgDn)", lambda: self.go_page(self.page + 1))
        self.b_pop = btn("⤢", "Open in a large viewer (Enter on the preview)", self.popOutRequested.emit)
        self.b_pop.setVisible(show_popout)

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 2, 0, 0)
        bar.setSpacing(2)
        for w in (self.b_out, self.zoom_lbl, self.b_in, self.b_fit):
            bar.addWidget(w)
        bar.addStretch(1)
        for w in (self.b_prev, self.page_lbl, self.b_next, self.b_pop):
            bar.addWidget(w)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        lay.addWidget(self.view, 1)
        lay.addLayout(bar)

        for keys, fn in (("+", lambda: self._zoom_by(1.25, None)), ("=", lambda: self._zoom_by(1.25, None)),
                         ("-", lambda: self._zoom_by(1 / 1.25, None)), ("0", self.fit),
                         ("1", lambda: self.set_zoom(1.0)), ("PgDown", lambda: self.go_page(self.page + 1)),
                         ("PgUp", lambda: self.go_page(self.page - 1))):
            sc = QShortcut(QKeySequence(keys), self.view)
            sc.setContext(Qt.ShortcutContext.WidgetShortcut)
            sc.activated.connect(fn)

        self.path = None
        self.kind = None          # "image" | "pdf"
        self.zoom = 1.0           # 1.0 = one source pixel per screen pixel (images) / 100% (PDF)
        self.fitting = True
        self.page = 0
        self._pdf = None
        self._src_size = QSize()  # full image size, or PDF page size in points
        self._pix_scale = 1.0     # how many source pixels one pixmap pixel represents
        self._full_loaded = False
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(90)
        self._render_timer.timeout.connect(self._render_pdf)
        self.view.setBackgroundBrush(Qt.GlobalColor.transparent)
        self.hide()

    # ----- loading ------------------------------------------------------------------------
    def clear(self):
        self.path = None
        self.kind = None
        self.item.setPixmap(QPixmap())
        if self._pdf is not None:
            self._pdf.close()
        self.hide()

    def load_image(self, path: str) -> dict:
        """Quick preview copy now; full resolution later, only if zoomed in. Returns info."""
        self.clear()
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        size = reader.size()
        info = {"size": size}
        if size.isValid() and (size.width() > PREVIEW_MAX or size.height() > PREVIEW_MAX):
            reader.setScaledSize(size.scaled(PREVIEW_MAX, PREVIEW_MAX, Qt.AspectRatioMode.KeepAspectRatio))
        img = reader.read()
        if img.isNull():
            info["error"] = reader.errorString()
            return info
        self.path, self.kind = path, "image"
        if not size.isValid():
            size = img.size()
        self._src_size = size
        self._pix_scale = size.width() / max(1, img.width())
        self._full_loaded = self._pix_scale <= 1.0001
        self._set_pixmap(QPixmap.fromImage(img))
        self._sync_controls()
        self.show()
        QTimer.singleShot(0, self.fit)
        return info

    def load_qimage(self, img, path: str):
        """Show an already-decoded image (e.g. a RAW file's embedded preview), full size."""
        self.clear()
        if img is None or img.isNull():
            return
        self.path, self.kind = path, "image"
        self._src_size = img.size()
        self._pix_scale = 1.0
        self._full_loaded = True
        self._set_pixmap(QPixmap.fromImage(img))
        self._sync_controls()
        self.show()
        QTimer.singleShot(0, self.fit)

    def load_pdf(self, path: str) -> dict:
        self.clear()
        from PySide6.QtPdf import QPdfDocument
        if self._pdf is None:
            self._pdf = QPdfDocument(self)
        err = self._pdf.load(path)
        if err != QPdfDocument.Error.None_:
            name = getattr(err, "name", str(err))
            return {"error": "This PDF is password-protected." if "Password" in name else f"Can't open this PDF ({name})."}
        self.path, self.kind, self.page = path, "pdf", 0
        self._src_size = self._pdf.pagePointSize(0).toSize()
        self._sync_controls()
        self.show()
        QTimer.singleShot(0, self.fit)
        return {"pages": self._pdf.pageCount(),
                "title": self._pdf.metaData(QPdfDocument.MetaDataField.Title) or "",
                "author": self._pdf.metaData(QPdfDocument.MetaDataField.Author) or ""}

    def load_same_as(self, other: "ZoomView"):
        """Open what another ZoomView shows (for the pop-out viewer)."""
        if other.kind == "pdf":
            self.load_pdf(other.path)
            self.go_page(other.page)
        elif other.kind == "image":
            if other._full_loaded and other._pix_scale == 1.0 and not other.item.pixmap().isNull():
                self.load_qimage(other.item.pixmap().toImage(), other.path)   # e.g. a RAW preview
            else:
                self.load_image(other.path)

    def aspect(self) -> float:
        """height / width of the image or current page."""
        return self._src_size.height() / max(1, self._src_size.width()) if self._src_size.isValid() else 0.75

    def showEvent(self, ev):
        super().showEvent(ev)
        if self.fitting and self.kind:
            QTimer.singleShot(0, self.fit)

    def _set_pixmap(self, pm: QPixmap):
        self.item.setPixmap(pm)
        self.scene.setSceneRect(QRectF(pm.rect()))

    # ----- zoom ----------------------------------------------------------------------------
    def _fit_zoom(self) -> float:
        vp = self.view.viewport().size()
        sw, sh = max(1, self._src_size.width()), max(1, self._src_size.height())
        if self.kind == "pdf":
            sw, sh = sw * self._screen_ppp(), sh * self._screen_ppp()
        return max(MIN_ZOOM, min((vp.width() - 8) / sw, (vp.height() - 8) / sh))

    def _screen_ppp(self) -> float:
        """Screen pixels per PDF point at 100% (96 dpi / 72 points per inch)."""
        return 96 / 72

    def fit(self):
        if not self.kind:
            return
        self.fitting = True
        z = self._fit_zoom()
        if self.kind == "image":
            z = min(z, 1.0)          # never blow small images up just to fit
        self._apply_zoom(z)

    def toggle_fit(self):
        if self.fitting:
            self.set_zoom(1.0)
        else:
            self.fit()

    def set_zoom(self, z: float, anchor=None):
        self.fitting = False
        self._apply_zoom(z, anchor)

    def _zoom_by(self, factor: float, anchor):
        self.set_zoom(self.zoom * factor, anchor)

    def _apply_zoom(self, z: float, anchor=None):
        z = max(MIN_ZOOM, min(MAX_ZOOM, z))
        self.zoom = z
        before = self.view.mapFromScene(anchor) if anchor is not None else None
        if self.kind == "image":
            # the pixmap may be a downscaled copy: scale the view so 1.0 means one real pixel
            s = z * self._pix_scale
            self.view.resetTransform()
            self.view.scale(s, s)
            if z > 1.0 / self._pix_scale + 1e-6 and not self._full_loaded:
                self._load_full()
        else:
            self._render_timer.start()     # re-render the page at the new size (crisp text)
            # meanwhile, scale what we have so the zoom feels immediate
            pm = self.item.pixmap()
            if not pm.isNull():
                target_w = self._src_size.width() * self._screen_ppp() * z
                s = target_w / max(1, pm.width())
                self.view.resetTransform()
                self.view.scale(s, s)
        if before is not None:            # keep the point under the pointer where it was
            after = self.view.mapFromScene(anchor)
            d = after - before
            self.view.horizontalScrollBar().setValue(self.view.horizontalScrollBar().value() + d.x())
            self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().value() + d.y())
        self._sync_controls()

    def _load_full(self):
        self._full_loaded = True
        path = self.path

        def read():
            r = QImageReader(path)
            r.setAutoTransform(True)
            sz = r.size()
            if sz.isValid() and (sz.width() > FULL_MAX or sz.height() > FULL_MAX):
                r.setScaledSize(sz.scaled(FULL_MAX, FULL_MAX, Qt.AspectRatioMode.KeepAspectRatio))
            return r.read()

        def done(img):
            if self.path != path or not hasattr(img, "isNull") or img.isNull():
                return
            center = self.view.mapToScene(self.view.viewport().rect().center())
            old_scale = self._pix_scale
            self._pix_scale = self._src_size.width() / max(1, img.width())
            self._set_pixmap(QPixmap.fromImage(img))
            self._apply_zoom(self.zoom)
            self.view.centerOn(center * (old_scale / self._pix_scale))
        places.run_async(read, done)

    def _render_pdf(self):
        if self.kind != "pdf" or self._pdf is None:
            return
        dpr = self.devicePixelRatioF()
        w = max(1, int(self._src_size.width() * self._screen_ppp() * self.zoom * dpr))
        h = max(1, int(self._src_size.height() * self._screen_ppp() * self.zoom * dpr))
        # cap the bitmap; past this the view just scales it
        cap = 6000
        if max(w, h) > cap:
            f = cap / max(w, h)
            w, h = int(w * f), int(h * f)
        img = self._pdf.render(self.page, QSize(w, h))
        if img.isNull():
            return
        page = QImage(img.size(), QImage.Format.Format_RGB32)   # PDF pages render transparent:
        page.fill(Qt.GlobalColor.white)                          # put them on paper
        qp = QPainter(page)
        qp.drawImage(0, 0, img)
        qp.end()
        img = page
        center = self.view.mapToScene(self.view.viewport().rect().center())
        old_w = max(1, self.item.pixmap().width())
        self._set_pixmap(QPixmap.fromImage(img))
        target_w = self._src_size.width() * self._screen_ppp() * self.zoom
        s = target_w / img.width()
        self.view.resetTransform()
        self.view.scale(s, s)
        self.view.centerOn(center * (img.width() / old_w))

    # ----- pages ---------------------------------------------------------------------------
    def go_page(self, n: int, from_edge: int = 0):
        if self.kind != "pdf" or self._pdf is None:
            return
        n = max(0, min(self._pdf.pageCount() - 1, n))
        if n == self.page:
            return
        self.page = n
        self._src_size = self._pdf.pagePointSize(n).toSize()
        if self.fitting:
            self.fit()
        else:
            self._apply_zoom(self.zoom)
        self._render_timer.stop()
        self._render_pdf()
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.minimum() if from_edge >= 0 else bar.maximum())
        self._sync_controls()

    def _sync_controls(self):
        self.zoom_lbl.setText(f"{self.zoom * 100:.0f}%")
        pdf = self.kind == "pdf" and self._pdf is not None
        count = self._pdf.pageCount() if pdf else 0
        for w in (self.b_prev, self.page_lbl, self.b_next):
            w.setVisible(pdf and count > 1)
        if pdf:
            self.page_lbl.setText(f"{self.page + 1} / {count}")
            self.b_prev.setEnabled(self.page > 0)
            self.b_next.setEnabled(self.page < count - 1)
        self.view.wheel_zooms = self.kind == "image"

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self.fitting and self.kind:
            QTimer.singleShot(0, self.fit)

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.popOutRequested.emit()
            return
        super().keyPressEvent(ev)


class ViewerWindow(QDialog):
    """A large, resizable viewer for the same image or PDF. Esc closes."""

    def __init__(self, source: ZoomView, title: str, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle(title)
        self.resize(1200, 860)
        self.zv = ZoomView(self, show_popout=False)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(self.zv)
        self.zv.load_same_as(source)
        self.zv.view.setFocus()
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
