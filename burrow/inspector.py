"""Side panel that previews the selection: image, text, or details."""
from __future__ import annotations

import os
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase, QImageReader, QPixmap
from PySide6.QtWidgets import (QFormLayout, QHeaderView, QLabel, QPlainTextEdit, QSizePolicy,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from . import archive, everything, places
from .zoomview import ZoomView
from .media import AUDIO_EXTS, VIDEO_EXTS, MediaPreview
from .model import CLOUD_ONLY, DRIVES, NETWORK, Entry, attr_string, human_size, icon_cache

TEXT_EXTS = set("""txt text md markdown mdx rst adoc org log ini cfg conf config toml yaml yml json jsonc json5
ndjson geojson xml xaml svg plist html htm xhtml css scss sass less csv tsv tab nfo diz srt vtt ass ssa sub lrc cue
m3u m3u8 pls tex bib gitconfig gitmodules dockerfile makefile cmake mk gradle sbt tf tfvars hcl nix
ps1xml lock sum mod workspace sln csproj vcxproj props targets manifest desktop service url
js mjs cjs ts tsx jsx py pyw pyi c h cpp hpp cc cs java kt kts go rs rb php pl lua sh bash zsh ps1
psm1 psd1 bat cmd sql csv tsv env gitignore gitattributes editorconfig properties gradle swift m mm
r dart vue svelte tex rst srt vtt nfo reg inf diff patch""".split())
BINARY_EXTS = VIDEO_EXTS | AUDIO_EXTS | set("""exe dll sys msi zip 7z rar gz bz2 xz tar iso img bin dat db sqlite mp3 mp4 mkv
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
        self.media = MediaPreview(self)
        self.media.metadataReady.connect(self._media_info)
        self.media.failed.connect(self._set_note)
        self.media.coverShown.connect(lambda: (self.preview.clear(), self.preview.setFixedHeight(0)))
        self.lay.addWidget(self.preview)
        self.lay.addWidget(self.media)
        self.zoom = ZoomView(self)
        self.zoom.popOutRequested.connect(self._pop_out)
        self.lay.addWidget(self.zoom, 0)
        self.lay.addWidget(self.title)
        self.lay.addWidget(self.details)
        self.lay.addWidget(self.note)
        self.lay.addWidget(self.text, 0)
        self.archive_tree = QTreeWidget()
        self.archive_tree.setHeaderLabels(["Name", "Size"])
        self.archive_tree.setUniformRowHeights(True)
        self.archive_tree.setIndentation(14)
        self.archive_tree.header().setStretchLastSection(False)
        self.archive_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.archive_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.archive_tree.hide()
        self.lay.addWidget(self.archive_tree, 0)
        self.lay.addStretch(1)
        self._stretch_index = self.lay.count() - 1

    # ----- helpers -------------------------------------------------------------
    use_everything = True
    _hl = None
    _shown = None

    def _reset(self):
        self._shown = None
        self.media.unload()
        self._archive_req = getattr(self, "_archive_req", 0) + 1
        self.archive_tree.clear()
        self.zoom.clear()
        self._show_zoom(False)
        self._show_archive(False)
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
        # long paths have no spaces to wrap at: allow breaks after each backslash
        v = QLabel(value.replace("\\", "\\​") if "\\" in value else value)
        v.setWordWrap(True)
        v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.form.addRow(k, v)

    def _show_archive(self, on: bool):
        self.archive_tree.setVisible(on)
        self.lay.setStretchFactor(self.archive_tree, 1 if on else 0)
        if on:
            self.lay.setStretch(self._stretch_index, 0)

    def _show_text(self, on: bool):
        self.text.setVisible(on)
        self.lay.setStretchFactor(self.text, 1 if on else 0)
        self.lay.setStretch(self._stretch_index, 0 if on else 1)

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
        # Same single file still selected (window restored, folder refreshed...): keep the
        # playing video / zoomed photo exactly as it is instead of reloading it.
        if len(entries) == 1 and ((self.media.is_active() and self.media._path == entries[0].path)
                                  or (self.zoom.kind and self.zoom.path == entries[0].path)):
            return
        self._reset()
        icons = icon_cache()
        if not entries:
            self._show_folder(folder)
        elif len(entries) > 1:
            self._set_icon(icons.file)
            dirs = [e for e in entries if e.is_dir]
            files = [e for e in entries if not e.is_dir]
            self.title.setText(f"{len(entries):,} items selected")
            self._row("Folders", f"{len(dirs):,}" if dirs else "")
            self._row("Files", f"{len(files):,}" if files else "")
            sized = [e for e in entries if e.size is not None and not e.is_drive]
            if sized:
                partial = len(sized) < len(entries)
                self._row("Size", human_size(sum(e.size for e in sized)) + (" (files only)" if partial else ""))
        else:
            self._show_entry(entries[0])

    def _show_folder(self, folder: str | None):
        icons = icon_cache()
        if folder in (None, DRIVES):
            self._set_icon(icons.computer)
            self.title.setText("This PC")
            self._set_note("Your drives and cloud folders. Select one to see details.")
            return
        if places.is_tag(folder):
            from . import tags
            tid = folder[len(places.TAG_PREFIX):]
            self._set_icon(icons.folder)
            self.title.setText(f"Tag: {tags.TAG_NAMES.get(tid, tid)}")
            self._set_note("Everything you've tagged with this color, wherever it is. Right-click any "
                           "file or folder ▸ Tags to add or remove tags.")
            return
        if folder == NETWORK or places.is_server(folder):
            self._set_icon(icons.network)
            self.title.setText("Network" if folder == NETWORK else folder[2:])
            self._set_note("Mapped network drives and computers you've opened. Type \\\\computer "
                           "in the address bar to browse another one." if folder == NETWORK
                           else "Shared folders on this computer.")
            return
        self._set_icon(icons.folder_icon(folder))
        self.title.setText(os.path.basename(folder.rstrip("\\")) or folder)
        self._row("Location", folder)
        if places.is_remote(folder):
            self._row("Contents", "On a network location")
            return                              # don't scan a share on the GUI thread
        self._folder_counts(folder)
        self._everything_size(folder)
        try:
            st = os.stat(folder)
            self._row("Created", _fmt_time(getattr(st, "st_birthtime", None) or st.st_ctime))
            self._row("Modified", _fmt_time(st.st_mtime))
        except OSError:
            pass

    def _everything_size(self, folder: str):
        """Size of the folder being shown, from Everything; added when the answer arrives."""
        if not self.use_everything:
            return
        c = everything.client()
        if c is None:
            return
        self._shown = folder

        def got(size, folder=folder):
            if size is not None and self._shown == folder:
                self._row("Size", f"{human_size(size)} ({size:,} bytes)")
        c.size_of(folder, got)

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
            label = {"cloud": "Cloud folder", "network": "Network drive", "server": "Computer",
                     "share": "Shared folder"}.get(e.kind, "Drive")
            self._row("Kind", label)
            self._row("Path", e.path)
            if e.kind in ("", "local", "removable", "optical"):
                self._row("Space", e.note)
            elif e.kind == "network":
                self._row("Status", e.note.split(" · ")[0])
            return
        remote = places.is_remote(e.path)
        self._row("Type", icons.type_name(e))
        if e.size is not None:                  # files, and folders sized by Everything
            self._row("Size", f"{human_size(e.size)} ({e.size:,} bytes)" if e.size >= 1024
                      else human_size(e.size))
        self._row("Location", os.path.dirname(e.path))
        if e.is_dir and not remote:
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
        if remote and (e.size or 0) > 512 * 1024:
            self._set_note("Large file on a network location: no automatic preview.")
            return
        if e.ext == "pdf":
            self._preview_pdf(e)
        elif e.ext in self._img_exts:
            self._preview_image(e)
        elif e.ext in archive.ARCHIVE_EXTS or e.path.lower().endswith(archive.TAR_SUFFIXES):
            self._preview_archive(e)
        elif e.ext in VIDEO_EXTS or e.ext in AUDIO_EXTS:
            self._preview_media(e)
        elif e.ext in TEXT_EXTS or e.ext not in BINARY_EXTS:
            # any size: only the first 256 KB is read, and binary content is detected
            self._preview_text(e)

    def _preview_archive(self, e: Entry):
        """List what's inside without extracting anything. Read on a worker thread."""
        self._shown = e.path
        self._archive_req = getattr(self, "_archive_req", 0) + 1
        req = self._archive_req
        self._set_note("Reading archive…")
        path = e.path
        places.run_async(lambda: archive.list_archive(path), lambda res: self._archive_ready(path, res, req))

    def _archive_ready(self, path: str, res: dict, req: int = 0):
        if self._shown != path or req != getattr(self, "_archive_req", 0):
            return                                  # the selection moved on
        self._set_note("")
        if res["error"] and not res["entries"]:
            self._row("Archive", res["kind"])
            self._set_note(res["error"])
            return
        self._row("Archive", res["kind"] + ("  (encrypted)" if res["encrypted"] else ""))
        self._row("Contents", f"{res['files']:,} files, {res['dirs']:,} folders")
        self._row("Unpacked", human_size(res["size"]))
        if res["packed"] and res["size"]:
            self._row("Compression", f"{100 - res['packed'] * 100 / res['size']:.0f}% smaller")
        icons = icon_cache()
        folder_icon, file_icon = icons.folder, icons.file
        nodes: dict[str, QTreeWidgetItem] = {}

        def node(parts) -> QTreeWidgetItem | None:
            if not parts:
                return None
            key = "/".join(parts)
            it = nodes.get(key)
            if it is None:
                parent = node(parts[:-1])
                it = QTreeWidgetItem([parts[-1], ""])
                it.setIcon(0, folder_icon)
                it.setData(0, 256, (0, parts[-1].lower()))
                (parent.addChild(it) if parent else self.archive_tree.addTopLevelItem(it))
                nodes[key] = it
            return it
        self.archive_tree.setUpdatesEnabled(False)
        for name, size, is_dir, _mt in res["entries"]:
            parts = [x for x in name.split("/") if x]
            if is_dir:
                node(parts)
                continue
            parent = node(parts[:-1])
            it = QTreeWidgetItem([parts[-1], human_size(size)])
            it.setIcon(0, file_icon)
            it.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            (parent.addChild(it) if parent else self.archive_tree.addTopLevelItem(it))
        self.archive_tree.sortItems(0, Qt.SortOrder.AscendingOrder)
        if self.archive_tree.topLevelItemCount() == 1:
            self.archive_tree.topLevelItem(0).setExpanded(True)   # single root folder: open it
        self.archive_tree.setUpdatesEnabled(True)
        if res["truncated"]:
            self._set_note(f"Showing the first {len(res['entries']):,} items.")
        self._show_archive(True)

    def _preview_media(self, e: Entry):
        """Video or audio player in place of the image preview. Nothing plays until asked."""
        video = e.ext in VIDEO_EXTS
        if not video:
            self._set_icon(icon_cache().icon(e))
        else:
            self.preview.clear()
            self.preview.setFixedHeight(0)
        self._shown = e.path
        self.media.load(e.path, video)

    def _media_info(self, info: dict):
        if self._shown != self.media._path:
            return
        for k in ("Title", "Artist", "Album", "Track", "Genre", "Duration", "Video", "Codecs", "Bitrate"):
            if info.get(k):
                self._row(k, info[k])

    def toggle_media(self) -> bool:
        if self.media.is_active():
            self.media.toggle()
            return True
        return False

    def _preview_image(self, e: Entry):
        info = self.zoom.load_image(e.path)
        size = info.get("size")
        if size is not None and size.isValid():
            self._row("Dimensions", f"{size.width()} × {size.height()} px")
        if info.get("error"):
            self._set_note(f"Can't preview this image: {info['error']}")
            return
        self.preview.clear()
        self.preview.setFixedHeight(0)
        self._show_zoom(True)

    def _preview_pdf(self, e: Entry):
        try:
            info = self.zoom.load_pdf(e.path)
        except ImportError:
            self._set_note("PDF preview isn't available in this build.")
            return
        if info.get("error"):
            self._set_note(info["error"])
            return
        self._row("Pages", f"{info['pages']:,}")
        self._row("Title", info.get("title", ""))
        self._row("Author", info.get("author", ""))
        self.preview.clear()
        self.preview.setFixedHeight(0)
        self._show_zoom(True)

    def _show_zoom(self, on: bool):
        if on:   # size the box to the picture/page shape, at most 60% of the panel
            w = max(160, self.width() - 32)
            h = int(w * self.zoom.aspect()) + 34
            self.zoom.setMaximumHeight(max(140, min(h, int(self.height() * 0.6))))
        self.zoom.setVisible(on)
        self.lay.setStretchFactor(self.zoom, 3 if on else 0)
        if on:
            self.lay.setStretch(self._stretch_index, 0)

    def _pop_out(self):
        if self.zoom.kind:
            import os
            from .zoomview import ViewerWindow
            ViewerWindow(self.zoom, os.path.basename(self.zoom.path), self.window()).show()

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
        if self._hl is not None:
            self._hl.setDocument(None)
        from .highlight import PreviewHighlighter
        from . import theme
        self._hl = PreviewHighlighter(self.text.document(), e.ext, theme.colors())
        self._show_text(True)
