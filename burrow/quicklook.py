"""Quick Look: Space shows a big preview of the selected file over the window, like macOS.

Photos (RAW and HEIC too) and PDFs open zoomable; video and music play; text shows as text;
Word, Excel, PowerPoint and anything else Windows can draw a thumbnail for show that page
(Windows' own thumbnail, which Office and other apps provide). Space or Esc closes it, the
arrow keys move to the next file in the list behind it, Enter opens the file.

Online-only cloud files are never opened here (that would download them).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFontDatabase, QKeyEvent
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                               QStackedWidget, QVBoxLayout)

from . import places, raw
from .media import AUDIO_EXTS, VIDEO_EXTS, MediaPreview
from .inspector import BINARY_EXTS, TEXT_EXTS
from .model import CLOUD_ONLY, Entry, human_size, icon_cache
from .thumbs import _qt_image_exts

TEXT_LIMIT = 512 * 1024


class QuickLook(QDialog):
    """One window, reused: show(entry) fills it for the file now selected."""

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setModal(False)
        self.entry: Entry | None = None
        self._req = 0
        self.title = QLabel()
        f = self.title.font()
        f.setBold(True)
        self.title.setFont(f)
        self.info = QLabel()
        self.info.setProperty("role", "note")
        self.open_btn = QPushButton("Open")
        self.open_btn.setToolTip("Open it in its app (Enter)")
        self.open_btn.clicked.connect(self._open)
        head = QHBoxLayout()
        head.addWidget(self.title)
        head.addWidget(self.info)
        head.addStretch(1)
        head.addWidget(self.open_btn)

        from .zoomview import ZoomView
        self.zoom = ZoomView(self, show_popout=False)
        self.media = MediaPreview(self)
        self.text = QPlainTextEdit(readOnly=True)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.plain = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)    # icon + a line of text
        self.plain.setWordWrap(True)
        from .officeview import PreviewHost
        self.host = PreviewHost()                 # Windows' own previewer draws in here
        self.office = None                        # Burrow's Word / Excel / PowerPoint reader
        self.stack = QStackedWidget()
        for w in (self.zoom, self.media, self.text, self.plain, self.host):
            self.stack.addWidget(w)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 14)
        lay.addLayout(head)
        lay.addWidget(self.stack, 1)
        for w in (self.zoom, self.media, self.text, self.open_btn):
            w.installEventFilter(self)            # keys reach us whatever has the focus inside

    # ----- showing a file ----------------------------------------------------------------------
    def show_entry(self, e: Entry):
        self._req += 1
        self.entry = e
        self.media.unload()
        self.zoom.clear()
        self.host.unload()
        self._drop_office()
        self.setWindowTitle(e.name)
        self.title.setText(e.name)
        self.info.setText("" if e.is_dir or e.size is None else "  " + human_size(e.size))
        self.open_btn.setVisible(not e.is_dir)
        ext = e.ext
        img_exts = _qt_image_exts()
        if e.is_dir:
            self._plain(e, "Folder")
        elif e.attrs & CLOUD_ONLY:
            self._plain(e, "Online-only: open it to download it. Quick Look won't download it for you.")
        elif ext == "pdf":
            info = self.zoom.load_pdf(e.path)
            self._zoom_or_note(e, info)
        elif ext in img_exts:
            info = self.zoom.load_image(e.path)
            self._zoom_or_note(e, info)
        elif ext in raw.RAW_EXTS or ext in raw.SHELL_IMAGE_EXTS:
            self._plain(e, "Loading…")
            self._async(e, lambda p=e.path: raw.read_raw(p).get("image"))
        elif ext in VIDEO_EXTS or ext in AUDIO_EXTS:
            self.media.load(e.path, ext in VIDEO_EXTS)
            self.stack.setCurrentWidget(self.media)
            self._autoplay(self._req)
        elif ext in TEXT_EXTS or (ext not in BINARY_EXTS and (e.size or 0) < 4 * 1024 * 1024
                                   and self._looks_text(e.path)):
            self._show_text(e)
        else:                                     # Word, Excel, PowerPoint, Outlook, …
            self._plain(e, "Loading…")
            QTimer.singleShot(0, lambda req=self._req: self._office(e, req))

    def _office(self, e, req):
        """Windows' previewer for the type if there is one (Office installed), else Burrow's
        own reader for .docx / .xlsx / .pptx, else the page picture Windows can make."""
        if req != self._req:
            return
        from . import officeview
        self.stack.setCurrentWidget(self.host)    # the previewer needs its window on screen
        if officeview.handler_for(e.ext) and self.host.load(e.path):
            self.info.setText(self.info.text().split("   ")[0])
            return
        if e.ext in officeview.OOXML:
            def done(res, req=req):
                if req != self._req:
                    return
                if not isinstance(res, dict) or res.get("error"):
                    self._plain(e, (res.get("error") if isinstance(res, dict) else str(res)) or "Can't read it.")
                    return
                self._drop_office()
                self.office = officeview.build_view(res)
                self.stack.addWidget(self.office)
                self.stack.setCurrentWidget(self.office)
                self.info.setText(self.info.text().split("   ")[0])
            places.run_async(lambda p=e.path: officeview.read_office(p), done)
            return
        self._async(e, lambda p=e.path: raw.shell_thumbnail(p, 1600))

    def _drop_office(self):
        if self.office is not None:
            self.stack.removeWidget(self.office)
            self.office.deleteLater()
            self.office = None

    def _zoom_or_note(self, e, info):
        if info.get("error"):
            self._plain(e, info["error"])
        else:
            self.stack.setCurrentWidget(self.zoom)

    def _async(self, e, read):
        req = self._req

        def done(img):
            if req != self._req:
                return                            # moved on to another file
            if img is not None and not isinstance(img, Exception) and not img.isNull():
                self.zoom.load_qimage(img, e.path)
                self.stack.setCurrentWidget(self.zoom)
            else:
                self._plain(e, "No preview for this file. Enter opens it in its app.")
        places.run_async(read, done)

    def _autoplay(self, req, tries=0):
        """Play as soon as the file is open (like macOS); give up after 3 s."""
        if req != self._req:                      # another file, or closed (closing bumps _req)
            return
        p = self.media._player
        if p is not None and self.media._at_poster:   # opened and parked on its first frame
            self.media.toggle()
        elif tries < 30:
            QTimer.singleShot(100, lambda: self._autoplay(req, tries + 1))

    def _plain(self, e, text):
        pm = icon_cache().icon(e).pixmap(96, 96)
        self.plain.setPixmap(pm)
        self.plain.setToolTip(text)
        self.info.setText((self.info.text() + "   " if self.info.text() else "  ") + text)
        self.stack.setCurrentWidget(self.plain)

    @staticmethod
    def _looks_text(path) -> bool:
        try:
            with open(path, "rb") as f:
                head = f.read(8192)
        except OSError:
            return False
        return bool(head) and b"\x00" not in head

    def _show_text(self, e):
        try:
            with open(e.path, "rb") as f:
                data = f.read(TEXT_LIMIT)
        except OSError as ex:
            self._plain(e, f"Can't read it: {ex.strerror or ex}")
            return
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp1252", errors="replace")
        self.text.setPlainText(text)
        self.stack.setCurrentWidget(self.text)

    # ----- keys: Space / Esc close, arrows move through the list behind, Enter opens --------------
    def eventFilter(self, obj, ev):
        if ev.type() == ev.Type.KeyPress and self._key(ev):
            return True
        return super().eventFilter(obj, ev)

    def keyPressEvent(self, ev):
        if not self._key(ev):
            super().keyPressEvent(ev)

    def _key(self, ev) -> bool:
        k = ev.key()
        if ev.modifiers() & ~Qt.KeyboardModifier.KeypadModifier:
            return False
        if k in (Qt.Key.Key_Space, Qt.Key.Key_Escape):
            self.close()
            return True
        if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._open()
            return True
        if k in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right):
            self._step(k)
            return True
        return False

    def _step(self, key):
        """Move in the window's list (or columns) and show the new file."""
        v = self.win.view()
        if v is None:
            return
        target = v.columns.current_column() if v.columns_active() else v.item_view()
        if target is None:
            return
        QApplication.sendEvent(target, QKeyEvent(QKeyEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))
        QTimer.singleShot(20, self._follow)

    def _follow(self):
        v = self.win.view()
        e = v.current_entry() if v is not None else None
        if e is not None and (self.entry is None or e.path != self.entry.path):
            self.show_entry(e)

    def _open(self):
        if self.entry is not None and not self.entry.is_dir:
            self.win._open_from_inspector(self.entry.path)
            self.close()

    def closeEvent(self, ev):
        self._req += 1
        self.media.unload()
        self.zoom.clear()
        self.host.unload()
        self._drop_office()
        super().closeEvent(ev)

    def place(self):
        """About two thirds of the window, centred on it."""
        g = self.win.frameGeometry()
        w, h = int(g.width() * 0.66), int(g.height() * 0.78)
        self.resize(max(520, w), max(400, h))
        self.move(g.center().x() - self.width() // 2, g.center().y() - self.height() // 2)

