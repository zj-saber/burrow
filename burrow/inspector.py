"""Side panel that previews the selection: image, text, or details."""
from __future__ import annotations

import os
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase, QImageReader, QPixmap
from PySide6.QtWidgets import (QFormLayout, QLabel, QPlainTextEdit, QSizePolicy, QVBoxLayout,
                               QWidget)

from .model import CLOUD_ONLY, DRIVES, Entry, attr_string, human_size, icon_cache

TEXT_EXTS = set("""txt md markdown log ini cfg conf toml yaml yml json jsonc xml html htm css scss less
js mjs cjs ts tsx jsx py pyw pyi c h cpp hpp cc cs java kt kts go rs rb php pl lua sh bash zsh ps1
psm1 psd1 bat cmd sql csv tsv env gitignore gitattributes editorconfig properties gradle swift m mm
r dart vue svelte tex rst srt vtt nfo reg inf diff patch""".split())
BINARY_EXTS = set("""exe dll sys msi zip 7z rar gz bz2 xz tar iso img bin dat db sqlite mp3 mp4 mkv
avi mov wav flac ogg m4a pdf doc docx xls xlsx ppt pptx psd ttf otf woff woff2 class jar pyc""".split())
TEXT_LIMIT = 256 * 1024


def _image_exts() -> set[str]:
    return {bytes(f.data()).decode().lower() for f in QImageReader.supportedImageFormats()}


def _fmt_time(ts) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(ts).strftime("%A, %d %B %Y, %H:%M:%S")
    except (OSError, ValueError, OverflowError):
        return ""


class Inspector(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("inspector")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumWidth(220)
        self._img_exts = _image_exts()
        self._pix: QPixmap | None = None

        self.preview = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.preview.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.title = QLabel()
        f = self.title.font()
        f.setBold(True)
        f.setPointSizeF(f.pointSizeF() * 1.15)
        self.title.setFont(f)
        self.title.setWordWrap(True)
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.details = QWidget()
        self.form = QFormLayout(self.details)
        self.form.setContentsMargins(0, 4, 0, 4)
        self.form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setProperty("role", "note")
        self.text = QPlainTextEdit(readOnly=True)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.text.hide()

        self.form.setHorizontalSpacing(14)
        self.form.setVerticalSpacing(6)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(16, 16, 16, 12)
        self.lay.setSpacing(8)
        self.lay.addWidget(self.preview)
        self.lay.addWidget(self.title)
        self.lay.addWidget(self.details)
        self.lay.addWidget(self.note)
        self.lay.addWidget(self.text, 0)
        self.lay.addStretch(1)

    # ----- helpers -------------------------------------------------------------
    def _reset(self):
        while self.form.rowCount():
            self.form.removeRow(0)
        self._pix = None
        self.preview.clear()
        self.preview.setFixedHeight(0)
        self.note.clear()
        self.note.hide()
        self.text.clear()
        self._show_text(False)

    def _row(self, label: str, value: str):
        if not value:
            return
        k = QLabel(label)
        k.setProperty("role", "key")
        v = QLabel(value)
        v.setWordWrap(True)
        v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.form.addRow(k, v)

    def _show_text(self, on: bool):
        self.text.setVisible(on)
        self.lay.setStretch(4, 1 if on else 0)
        self.lay.setStretch(5, 0 if on else 1)

    def _set_note(self, text: str):
        self.note.setText(text)
        self.note.setVisible(bool(text))

    def _set_icon(self, icon):
        self._pix = None
        self.preview.setPixmap(icon.pixmap(64, 64))
        self.preview.setFixedHeight(72)

    def _fit_pixmap(self):
        if self._pix is None:
            return
        max_w = max(64, self.width() - 24)
        max_h = max(96, int(self.height() * 0.45))
        pix = self._pix
        if pix.width() > max_w or pix.height() > max_h:
            pix = pix.scaled(max_w, max_h, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
        self.preview.setPixmap(pix)
        self.preview.setFixedHeight(pix.height() + 8)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._fit_pixmap()

    # ----- public ---------------------------------------------------------------
    def show_entries(self, entries: list[Entry], folder: str | None):
        self._reset()
        icons = icon_cache()
        if not entries:
            self._show_folder(folder)
        elif len(entries) > 1:
            self._set_icon(icons.file)
            dirs = sum(1 for e in entries if e.is_dir)
            files = [e for e in entries if not e.is_dir]
            self.title.setText(f"{len(entries):,} items selected")
            self._row("Folders", f"{dirs:,}" if dirs else "")
            self._row("Files", f"{len(files):,}" if files else "")
            if files:
                total = sum(e.size or 0 for e in files)
                self._row("Size", human_size(total) + (" (files only)" if dirs else ""))
        else:
            self._show_entry(entries[0])

    def _show_folder(self, folder: str | None):
        icons = icon_cache()
        if folder in (None, DRIVES):
            self._set_icon(icons.computer)
            self.title.setText("This PC")
            self._set_note("Your drives. Select one to see details.")
            return
        self._set_icon(icons.folder)
        self.title.setText(os.path.basename(folder) or folder)
        self._row("Location", folder)
        self._folder_counts(folder)
        try:
            st = os.stat(folder)
            self._row("Created", _fmt_time(getattr(st, "st_birthtime", None) or st.st_ctime))
            self._row("Modified", _fmt_time(st.st_mtime))
        except OSError:
            pass

    def _folder_counts(self, path: str):
        dirs = files = 0
        capped = False
        try:
            with os.scandir(path) as it:
                for i, de in enumerate(it):
                    if i >= 20000:
                        capped = True
                        break
                    try:
                        if de.is_dir():
                            dirs += 1
                        else:
                            files += 1
                    except OSError:
                        files += 1
        except OSError as ex:
            self._row("Contents", f"Can't read ({ex.strerror or ex})")
            return
        more = "+" if capped else ""
        self._row("Contents", f"{files:,}{more} files, {dirs:,} folders")

    def _show_entry(self, e: Entry):
        icons = icon_cache()
        self._set_icon(icons.icon(e))
        self.title.setText(e.name)
        if e.is_drive:
            self._row("Drive", e.path)
            self._row("Space", e.note)
            return
        self._row("Type", icons.type_name(e))
        if not e.is_dir:
            self._row("Size", f"{human_size(e.size)} ({e.size:,} bytes)" if e.size and e.size >= 1024
                      else human_size(e.size))
        self._row("Location", os.path.dirname(e.path))
        if e.is_dir:
            self._folder_counts(e.path)
        self._row("Created", _fmt_time(e.ctime))
        self._row("Modified", _fmt_time(e.mtime))
        self._row("Accessed", _fmt_time(e.atime))
        self._row("Attributes", attr_string(e.attrs))
        if e.is_dir:
            return
        if e.attrs & CLOUD_ONLY:
            self._set_note("This file is only in the cloud. Open it to download it; "
                           "the preview won't download it for you.")
            return
        if e.ext in self._img_exts:
            self._preview_image(e)
        elif e.ext in TEXT_EXTS or (e.ext not in BINARY_EXTS and (e.size or 0) <= 4 * 1024 * 1024):
            self._preview_text(e)

    def _preview_image(self, e: Entry):
        reader = QImageReader(e.path)
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid():
            self._row("Dimensions", f"{size.width()} × {size.height()} px")
            if size.width() > 1600 or size.height() > 1600:
                reader.setScaledSize(size.scaled(1600, 1600, Qt.AspectRatioMode.KeepAspectRatio))
        img = reader.read()
        if img.isNull():
            self._set_note(f"Can't preview this image: {reader.errorString()}")
            return
        self._pix = QPixmap.fromImage(img)
        self._fit_pixmap()

    def _preview_text(self, e: Entry):
        try:
            with open(e.path, "rb") as f:
                data = f.read(TEXT_LIMIT)
        except OSError as ex:
            self._set_note(f"Can't read this file: {ex.strerror or ex}")
            return
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            text = data.decode("utf-16", errors="replace")
        else:
            if b"\x00" in data[:8192]:
                return                     # binary; details only
            try:
                text = data.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = data.decode("cp1252", errors="replace")
        if (e.size or 0) > TEXT_LIMIT:
            self._set_note(f"Showing the first {human_size(TEXT_LIMIT)}.")
        self.text.setPlainText(text)
        self._show_text(True)
