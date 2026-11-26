"""EPUB books: details, cover and a small chapter reader.

An EPUB is a zip: META-INF/container.xml points to the package (.opf), which lists the
metadata, the files (manifest) and the reading order (spine). Chapters are XHTML, rendered
with QTextBrowser; images and styles are read straight from the zip, nothing is extracted.
"""
from __future__ import annotations

import posixpath
import re
import zipfile
from xml.etree import ElementTree as ET

from PySide6.QtCore import QByteArray, Qt, QUrl, Signal
from PySide6.QtGui import QImage, QTextDocument
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QTextBrowser, QToolButton, QVBoxLayout,
                               QWidget)

NS = {"c": "urn:oasis:names:tc:opendocument:xmlns:container", "opf": "http://www.idpf.org/2007/opf",
      "dc": "http://purl.org/dc/elements/1.1/"}


MAX_PART = 16 * 2**20          # a chapter, picture or stylesheet bigger than this isn't read


def _read(z: zipfile.ZipFile, name: str) -> bytes:
    """One file from the book, refusing oversized ones (a "zip bomb" can't eat the memory)."""
    if z.getinfo(name).file_size > MAX_PART:
        raise KeyError(name)
    return z.read(name)


def read_book(path: str) -> dict:
    """Metadata, cover image bytes and spine (chapter paths). Safe on a worker thread."""
    with zipfile.ZipFile(path) as z:
        container = ET.fromstring(_read(z, "META-INF/container.xml"))
        rootfile = container.find(".//c:rootfile", NS).get("full-path")
        base = posixpath.dirname(rootfile)
        opf = ET.fromstring(_read(z, rootfile))

        def dc(tag):
            vals = [(el.text or "").strip() for el in opf.findall(f".//dc:{tag}", NS)]
            return ", ".join(v for v in vals if v)
        desc = re.sub(r"<[^>]+>", " ", dc("description"))
        info = {"title": dc("title"), "author": dc("creator"), "publisher": dc("publisher"),
                "date": dc("date")[:10], "language": dc("language"),
                "description": re.sub(r"\s+", " ", desc).strip()}
        manifest = {}
        cover_id = None
        for it in opf.findall(".//opf:manifest/opf:item", NS):
            manifest[it.get("id")] = (posixpath.normpath(posixpath.join(base, it.get("href", ""))),
                                      it.get("media-type", ""), it.get("properties", ""))
            if "cover-image" in (it.get("properties") or ""):
                cover_id = it.get("id")
        if cover_id is None:
            meta = opf.find(".//opf:metadata/opf:meta[@name='cover']", NS)
            if meta is not None:
                cover_id = meta.get("content")
        cover = None
        if cover_id in manifest and manifest[cover_id][1].startswith("image/"):
            cover = _read(z, manifest[cover_id][0])
        else:   # some books just name the file "cover"
            for href, mtype, _ in manifest.values():
                if mtype.startswith("image/") and "cover" in href.lower():
                    cover = _read(z, href)
                    break
        spine = [manifest[r.get("idref")][0] for r in opf.findall(".//opf:spine/opf:itemref", NS)
                 if r.get("idref") in manifest]
        info["cover"] = cover
        info["spine"] = spine
        info["chapters"] = len(spine)
        return info


class EpubBrowser(QTextBrowser):
    """Renders one chapter; images/styles referenced by the chapter come from the zip."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.path = None
        self.current = ""

    def show_chapter(self, book_path: str, chapter: str):
        self.path, self.current = book_path, chapter
        with zipfile.ZipFile(book_path) as z:
            html = _read(z, chapter).decode("utf-8", errors="replace")
        # QTextBrowser doesn't understand XML prologs/doctypes; drop them
        html = re.sub(r"<\?xml[^>]*\?>|<!DOCTYPE[^>]*>", "", html, flags=re.I)
        self.document().clear()
        self.setHtml(html)
        self.verticalScrollBar().setValue(0)

    def loadResource(self, rtype, url: QUrl):
        """Pictures and stylesheets come from inside the book only."""
        if self.path:
            target = posixpath.normpath(posixpath.join(posixpath.dirname(self.current), url.path()))
            try:
                with zipfile.ZipFile(self.path) as z:
                    data = _read(z, target)
                if rtype == QTextDocument.ResourceType.ImageResource:
                    img = QImage.fromData(data)
                    if not img.isNull() and img.width() > 900:      # keep big illustrations readable
                        img = img.scaledToWidth(900, Qt.TransformationMode.SmoothTransformation)
                    return img
                return QByteArray(data)
            except (KeyError, OSError, zipfile.BadZipFile):
                pass
        return None          # never fall back to files on disk: a book only shows what's inside it

    def wheelEvent(self, ev):
        if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:      # Ctrl+wheel: text size
            (self.zoomIn if ev.angleDelta().y() > 0 else self.zoomOut)(1)
            return
        super().wheelEvent(ev)


class EpubReader(QWidget):
    """Chapter reader with ‹ › navigation; used in the inspector and in the pop-out window."""
    popOutRequested = Signal()

    def __init__(self, parent=None, show_popout=True):
        super().__init__(parent)
        self.view = EpubBrowser(self)
        self.prev_btn = QToolButton(text="‹", autoRaise=True, toolTip="Previous chapter")
        self.next_btn = QToolButton(text="›", autoRaise=True, toolTip="Next chapter")
        self.pos = QLabel()
        self.pos.setProperty("role", "key")
        self.pop = QToolButton(text="⤢", autoRaise=True, toolTip="Open in a large reader")
        self.pop.setVisible(show_popout)
        self.prev_btn.clicked.connect(lambda: self.go(self.index - 1))
        self.next_btn.clicked.connect(lambda: self.go(self.index + 1))
        self.pop.clicked.connect(self.popOutRequested.emit)
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 2, 0, 0)
        bar.addWidget(self.prev_btn)
        bar.addWidget(self.pos)
        bar.addWidget(self.next_btn)
        bar.addStretch(1)
        bar.addWidget(QLabel("Ctrl+wheel: text size"), 0)
        bar.itemAt(bar.count() - 1).widget().setProperty("role", "key")
        bar.addWidget(self.pop)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        lay.addWidget(self.view, 1)
        lay.addLayout(bar)
        self.book, self.spine, self.index = None, [], 0

    def open(self, book_path: str, spine: list[str], index: int = 0):
        self.book, self.spine = book_path, spine
        # skip a cover-only first page when there's more to read
        self.go(index if index or len(spine) < 2 else self._first_text_chapter())

    def _first_text_chapter(self) -> int:
        try:
            with zipfile.ZipFile(self.book) as z:
                for i, ch in enumerate(self.spine[:4]):
                    text = re.sub(r"<[^>]+>", "", _read(z, ch).decode("utf-8", "replace"))
                    if len(text.strip()) > 200:
                        return i
        except (OSError, KeyError, zipfile.BadZipFile):
            pass
        return 0

    def go(self, i: int):
        if not self.spine:
            return
        self.index = max(0, min(len(self.spine) - 1, i))
        try:
            self.view.show_chapter(self.book, self.spine[self.index])
        except (KeyError, OSError, zipfile.BadZipFile):
            self.view.setPlainText("This chapter can't be read.")
        self.pos.setText(f"Chapter {self.index + 1} / {len(self.spine)}")
        self.prev_btn.setEnabled(self.index > 0)
        self.next_btn.setEnabled(self.index < len(self.spine) - 1)

    def clear(self):
        self.book, self.spine = None, []
        self.view.clear()
        self.hide()


class EpubWindow(QDialog):
    def __init__(self, source: EpubReader, title: str, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle(title)
        self.resize(900, 1000)
        self.reader = EpubReader(self, show_popout=False)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 8)
        lay.addWidget(self.reader)
        self.reader.view.zoomIn(2)
        self.reader.open(source.book, source.spine, source.index)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
