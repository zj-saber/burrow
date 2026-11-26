"""The preview column at the end of columns view (like macOS Finder).

When a file is selected in columns view, the next column shows it: a picture (Windows' own
thumbnail: photos, PDFs, Office files, videos...), its name, kind and size, then the details
(dimensions or length, created, modified, where), and Open / Quick Look. Videos and music
play right there. Online-only cloud files are never read (that would download them).
"""
from __future__ import annotations

import os
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from .media import AUDIO_EXTS, VIDEO_EXTS, MediaPreview
from .model import CLOUD_ONLY, DirModel, human_size, icon_cache

WIDTH = 280


def _when(ts) -> str:
    try:
        return datetime.fromtimestamp(ts).strftime("%d %b %Y, %H:%M") if ts else ""
    except (OSError, ValueError, OverflowError):
        return ""


class ColumnPreview(QWidget):
    """Shown by QColumnView as its last column whenever a file is current."""
    openRequested = Signal(str)
    quickLookRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(WIDTH)
        self.setMaximumWidth(WIDTH + 80)
        self.path = None
        self._entry = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        self.pic = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.pic.setMinimumHeight(140)
        self.pic.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.media = MediaPreview(self)
        self.media.metadataReady.connect(self._media_info)
        self.name = QLabel()
        self.name.setWordWrap(True)
        f = self.name.font()
        f.setBold(True)
        f.setPointSizeF(f.pointSizeF() * 1.08)
        self.name.setFont(f)
        self.name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.kind = QLabel()
        self.kind.setProperty("role", "note")
        self.details_box = QWidget()
        self.details = self._new_grid(self.details_box)
        line = QWidget()
        line.setFixedHeight(1)
        line.setObjectName("previewLine")
        self.open_btn = QPushButton("Open")
        self.open_btn.clicked.connect(lambda: self.path and self.openRequested.emit(self.path))
        self.ql_btn = QPushButton("Quick Look")
        self.ql_btn.setToolTip("Space")
        self.ql_btn.clicked.connect(self.quickLookRequested.emit)
        btns = QHBoxLayout()
        btns.addWidget(self.open_btn)
        btns.addWidget(self.ql_btn)
        btns.addStretch(1)
        lay.addWidget(self.pic)
        lay.addWidget(self.media)
        lay.addWidget(self.name)
        lay.addWidget(self.kind)
        lay.addWidget(line)
        lay.addWidget(self.details_box)
        self._lay = lay
        lay.addStretch(1)
        lay.addLayout(btns)
        from . import thumbs
        thumbs.loader().ready.connect(self._thumb_arrived)

    # ----- showing a file ----------------------------------------------------------------------
    def show_path(self, path: str):
        path = os.path.normpath(path)
        if path == self.path:
            return
        self.path = path
        self.media.unload()
        box = QWidget()                          # fresh details: nothing from the last file
        self.details = self._new_grid(box)
        self._lay.replaceWidget(self.details_box, box)
        self.details_box.hide()                  # gone at once, freed a moment later
        self.details_box.deleteLater()
        self.details_box = box
        found = DirModel.fetch_paths([path])
        if not found:
            return
        e = self._entry = found[0]
        icons = icon_cache()
        self.name.setText(e.name)
        self.kind.setText(" · ".join(x for x in (icons.type_name(e), human_size(e.size) if e.size is not None else "") if x))
        self._row("Created", _when(e.ctime))
        self._row("Modified", _when(e.mtime))
        self._row("Where", os.path.basename(os.path.dirname(path)) or os.path.dirname(path))
        from .model import CLOUD_STATES, cloud_state
        st = cloud_state(e.path, e.attrs, e.is_dir)
        if st:
            self._row("Status", CLOUD_STATES[st].split(" (")[0])
        cloud_only = bool(e.attrs & CLOUD_ONLY)
        if not cloud_only and (e.ext in VIDEO_EXTS or e.ext in AUDIO_EXTS):
            self.pic.hide()
            self.media.load(path, e.ext in VIDEO_EXTS)       # its first frame; ▶ plays it here
            self.media.setMinimumHeight(170 if e.ext in VIDEO_EXTS else 0)
            return
        self.media.hide()
        self.pic.show()
        self._icon()
        if cloud_only:
            return                               # never read a cloud-only file (it would download)
        self._dimensions(e)
        self._thumb_arrived()

    @staticmethod
    def _new_grid(box):
        g = QGridLayout(box)
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(3)
        return g

    def _row(self, key: str, value: str):
        if not value:
            return
        r = self.details.rowCount()
        k = QLabel(key)
        k.setProperty("role", "key")
        v = QLabel(value)
        v.setWordWrap(True)
        self.details.addWidget(k, r, 0, Qt.AlignmentFlag.AlignTop)
        self.details.addWidget(v, r, 1)

    def _icon(self):
        if self._entry is not None:
            self.pic.setPixmap(icon_cache().icon(self._entry).pixmap(96, 96))

    def _thumb_arrived(self):
        """Windows' thumbnail (made in the background, cached); the icon until then."""
        e = self._entry
        if e is None or self.pic.isHidden() or e.attrs & CLOUD_ONLY:
            return
        from . import thumbs
        pm = thumbs.loader().get(e.path, e.mtime)
        if pm is None:
            return
        w = max(120, self.width() - 32)
        self.pic.setPixmap(pm.scaled(w, 220, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation))

    def _dimensions(self, e):
        from PySide6.QtGui import QImageReader
        from .thumbs import _qt_image_exts
        if e.ext in _qt_image_exts():
            s = QImageReader(e.path).size()          # just the header
            if s.isValid():
                self._row("Dimensions", f"{s.width()} × {s.height()}")

    def _media_info(self, info: dict):
        if self.media._path is None or os.path.normpath(self.media._path) != self.path:
            return                               # a late answer about the previous file
        for k in ("Duration", "Video", "Artist", "Album"):
            if info.get(k):
                self._row(k, info[k])

    def apply_theme(self, c: dict):
        """Its own look: dim labels, a hairline, the panel color behind it."""
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("colPreview")
        self.setStyleSheet(
            f"#colPreview {{ background: {c['panel']}; }}"
            f"#colPreview QLabel[role='key'], #colPreview QLabel[role='note'] {{ color: {c['dim']}; }}"
            f"#colPreview QWidget#previewLine {{ background: {c['border']}; }}")
        self.media.apply_theme()

    def clear(self):
        self.path = None
        self._entry = None
        self.media.unload()

    def hideEvent(self, ev):
        self.media.pause()
        super().hideEvent(ev)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        QTimer.singleShot(0, self._thumb_arrived)
