"""Side panel that previews the selection: image, text, or details."""
from __future__ import annotations

import os
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontDatabase, QImageReader, QPixmap
from PySide6.QtWidgets import (QFormLayout, QHeaderView, QLabel, QPlainTextEdit, QSizePolicy,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from . import archive, everything, places
from .media import AUDIO_EXTS, VIDEO_EXTS, MediaPreview
from . import raw
from .model import CLOUD_ONLY, DRIVES, NETWORK, Entry, attr_string, human_size, icon_cache

TEXT_EXTS = set("""txt text md markdown mdx rst adoc org log ini cfg conf config toml yaml yml json jsonc json5
ndjson geojson xml xaml svg plist html htm xhtml css scss sass less csv tsv tab nfo diz srt vtt ass ssa sub lrc cue
m3u m3u8 pls tex bib gitconfig gitmodules dockerfile makefile cmake mk gradle sbt tf tfvars hcl nix
ps1xml lock sum mod workspace sln csproj vcxproj props targets manifest desktop service url
js mjs cjs ts tsx jsx py pyw pyi c h cpp hpp cc cs java kt kts go rs rb php pl lua sh bash zsh ps1
psm1 psd1 bat cmd sql csv tsv env gitignore gitattributes editorconfig properties gradle swift m mm
r dart vue svelte tex rst srt vtt nfo reg inf diff patch""".split())
BINARY_EXTS = VIDEO_EXTS | AUDIO_EXTS | raw.RAW_EXTS | raw.BRAW_EXTS | set("""exe dll sys msi zip 7z rar gz bz2 xz tar iso img bin dat db sqlite mp3 mp4 mkv
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
    """The right panel: preview (photo, PDF, video, text, archive, book) and file details."""
    detailsToggled = Signal(bool)
    openRequested = Signal(str)      # double-click in a folder's contents: open it
    folder_contents = True           # the window turns this off in columns view (it has its own)
    splitChanged = Signal(float)     # preview height share, for Settings persistence

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
        # Two parts with a drag handle between them: the stage (photo / PDF / video, as tall as
        # you like) and the body (name, details, text, archive, book).
        from PySide6.QtWidgets import QSplitter
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 12)
        self.split = QSplitter(Qt.Orientation.Vertical)
        self.split.setChildrenCollapsible(False)
        self.split.setHandleWidth(9)
        outer.addWidget(self.split)
        self.stage = QWidget()
        self.stage_lay = QVBoxLayout(self.stage)
        self.stage_lay.setContentsMargins(0, 0, 0, 4)
        self.stage_lay.setSpacing(4)
        self.body = QWidget()
        self.split.addWidget(self.stage)
        self.split.addWidget(self.body)
        self.split.setStretchFactor(0, 3)
        self.split.setStretchFactor(1, 2)
        self.split.splitterMoved.connect(self._split_moved)
        self.split_ratio = 0.6
        self.stage.hide()
        self.lay = QVBoxLayout(self.body)
        self.lay.setContentsMargins(0, 4, 0, 0)
        self.lay.setSpacing(8)
        self.media = MediaPreview(self)
        self.media.metadataReady.connect(self._media_info)
        self.media.failed.connect(self._set_note)
        self.media.coverShown.connect(lambda: (self.preview.clear(), self.preview.setFixedHeight(0)))
        self.lay.addWidget(self.preview)
        self.stage_lay.addWidget(self.media, 1)
        self._zoom = None          # photo/PDF viewer: built the first time one is previewed
        self.lay.addWidget(self.title)
        # "Details" header: folds the file information away to give the preview more room
        from PySide6.QtWidgets import QToolButton
        self.details_btn = QToolButton()
        self.details_btn.setObjectName("detailsToggle")
        self.details_btn.setAutoRaise(True)
        self.details_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.details_btn.setToolTip("Show or hide the file details")
        self.details_btn.clicked.connect(lambda: self.set_details_visible(not self.details.isVisible(), user=True))
        self.lay.addWidget(self.details_btn)
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
        # a selected folder's contents: shown in the stage, where photos and videos go
        self.folder_list = QTreeWidget()
        self.folder_list.setHeaderLabels(["Name", "Size"])
        self.folder_list.setRootIsDecorated(False)
        self.folder_list.setUniformRowHeights(True)
        self.folder_list.header().setStretchLastSection(False)
        self.folder_list.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.folder_list.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.folder_list.itemActivated.connect(self._item_activated)   # double-click / Enter: open
        self.folder_list.hide()
        self.stage_lay.addWidget(self.folder_list, 1)
        self._epub = None          # EPUB reader: built the first time a book is previewed
        self.lay.addStretch(1)

    @property
    def _stretch_index(self) -> int:
        return self.lay.count() - 1

    @property
    def zoom(self):
        if self._zoom is None:
            from .zoomview import ZoomView
            self._zoom = ZoomView(self)
            self._zoom.popOutRequested.connect(self._pop_out)
            self._zoom.hide()
            self.stage_lay.addWidget(self._zoom, 1)
        return self._zoom

    @property
    def epub(self):
        if self._epub is None:
            from .epub import EpubReader
            self._epub = EpubReader(self)
            self._epub.popOutRequested.connect(self._pop_out_epub)
            self._epub.hide()
            self.lay.insertWidget(self.lay.indexOf(self.archive_tree) + 1, self._epub, 0)
        return self._epub

    # ----- helpers -------------------------------------------------------------
    use_everything = True
    prefs: dict = {}       # Settings switches, set by the window
    _hl = None
    _shown = None

    def set_details_visible(self, on: bool, user: bool = False):
        self.details.setVisible(on)
        self.details_btn.setText(("▾  Details" if on else "▸  Details"))
        if not self.stage.isHidden():
            self._apply_split()
        if user:
            self.detailsToggled.emit(on)

    def _reset(self):
        """Clear the preview and free what it held."""
        self._shown = None
        self.media.unload()
        self.stage.hide()
        self._raw_req = getattr(self, "_raw_req", 0) + 1
        self._archive_req = getattr(self, "_archive_req", 0) + 1
        self.archive_tree.clear()
        self.folder_list.clear()
        self.folder_list.hide()
        if self._epub is not None:
            self._epub.clear()
            self.lay.setStretchFactor(self._epub, 0)
        self._epub_req = getattr(self, "_epub_req", 0) + 1
        if self._zoom is not None:
            self._zoom.clear()
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
        """Show the selection: one item in detail, several as a summary."""
        if len(entries) == 1 and ((self.media.is_active() and self.media._path == entries[0].path)
                                  or (self._zoom is not None and self._zoom.kind and self._zoom.path == entries[0].path)):
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
        """A folder's details: location, contents count and dates."""
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
        """Count files and folders inside, stopping early on huge folders."""
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
        """One file: pick the preview for its type and fill in the details."""
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
            if self.folder_contents and self.prefs.get("preview_folders", True):
                self._preview_folder(e)
            return
        if e.attrs & CLOUD_ONLY:
            self._set_note("This file is only in the cloud. Open it to download it; "
                           "the preview won't download it for you.")
            return
        if remote and (e.size or 0) > 512 * 1024:
            self._set_note("Large file on a network location: no automatic preview.")
            return
        pref = lambda k: self.prefs.get(k, True)   # noqa: E731
        if e.ext == "epub":
            if pref("preview_epub"):
                self._preview_epub(e)
        elif not pref("preview_images") and (e.ext in raw.RAW_EXTS or e.ext in raw.SHELL_IMAGE_EXTS
                                         or e.ext == "pdf" or e.ext in self._img_exts):
            pass
        elif not pref("preview_media") and (e.ext in VIDEO_EXTS or e.ext in AUDIO_EXTS or e.ext in raw.BRAW_EXTS):
            pass
        elif not pref("preview_archives") and (e.ext in archive.ARCHIVE_EXTS or e.path.lower().endswith(archive.TAR_SUFFIXES)):
            pass
        elif e.ext in raw.RAW_EXTS or (e.ext in raw.SHELL_IMAGE_EXTS and e.ext not in self._img_exts):
            self._preview_raw(e)          # RAW, or a photo format only Windows can decode (HEIC...)
        elif e.ext in raw.BRAW_EXTS:
            self._preview_braw(e)
        elif e.ext == "pdf":
            self._preview_pdf(e)
        elif e.ext in self._img_exts:
            self._preview_image(e)
        elif e.ext in archive.ARCHIVE_EXTS or e.path.lower().endswith(archive.TAR_SUFFIXES):
            self._preview_archive(e)
        elif e.ext in VIDEO_EXTS or e.ext in AUDIO_EXTS:
            self._preview_media_staged(e)
        elif pref("preview_text") and (e.ext in TEXT_EXTS or e.ext not in BINARY_EXTS):
            # any size: only the first 256 KB is read, and binary content is detected
            self._preview_text(e)

    # ----- a folder: what's inside, folders first, read on a worker thread ------------------------
    FOLDER_LIMIT = 500

    def _preview_folder(self, e: Entry):
        self._shown = e.path
        self._archive_req = getattr(self, "_archive_req", 0) + 1
        req, path = self._archive_req, e.path
        hidden = bool(self.prefs.get("show_hidden", False))
        from .model import scan_dir

        def read():
            try:
                return scan_dir(path, hidden), ""
            except OSError as ex:
                return [], ex.strerror or str(ex)
        places.run_async(read, lambda res: self._folder_ready(path, res, req))

    def _folder_ready(self, path: str, res, req: int):
        if self._shown != path or req != getattr(self, "_archive_req", 0):
            return                                   # the selection moved on
        entries, err = res if isinstance(res, tuple) else ([], str(res))
        if err:
            self._set_note(f"Can't read this folder: {err}")
            return
        if not entries:
            self._set_note("This folder is empty.")
            return
        from .model import natural_key
        entries.sort(key=lambda x: (not x.is_dir, natural_key(x.name)))
        icons = icon_cache()
        t = self.folder_list
        t.setUpdatesEnabled(False)
        items = {}
        for x in entries[:self.FOLDER_LIMIT]:
            it = QTreeWidgetItem([x.name, "" if x.is_dir else human_size(x.size)])
            it.setIcon(0, icons.icon(x))
            it.setData(0, Qt.ItemDataRole.UserRole, x.path)
            it.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            it.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.DontShowIndicator)
            t.addTopLevelItem(it)
            items[os.path.normcase(x.path)] = it
        t.setUpdatesEnabled(True)
        if len(entries) > self.FOLDER_LIMIT:
            self._set_note(f"Showing the first {self.FOLDER_LIMIT:,} of {len(entries):,} items.")
        self.preview.clear()                         # the list is the preview: no big icon
        self.preview.setFixedHeight(0)
        t.show()
        self.update_stage()
        # subfolder sizes from Everything, filled in when they arrive (a few ms)
        c = everything.client() if self.use_everything and not places.is_remote(path) else None
        if c is not None and any(x.is_dir for x in entries):
            def got(sizes, path=path, req=req):
                if self._shown != path or req != getattr(self, "_archive_req", 0):
                    return
                for key, size in sizes.items():
                    it = items.get(key)
                    if it is not None:
                        it.setText(1, human_size(size))
            c.folder_sizes(path, got)

    def _item_activated(self, item, _col=0):
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if path:
            self.openRequested.emit(path)

    def _preview_archive(self, e: Entry):
        """List what's inside without extracting anything. Read on a worker thread."""
        self._shown = e.path
        self._archive_req = getattr(self, "_archive_req", 0) + 1
        req = self._archive_req
        self._set_note("Reading archive…")
        path = e.path
        places.run_async(lambda: archive.list_archive(path), lambda res: self._archive_ready(path, res, req))

    def _archive_ready(self, path: str, res: dict, req: int = 0):
        """The archive listing arrived: show it as a folder tree with totals."""
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
            """The tree item for a folder path inside the archive (created on demand)."""
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

    def _preview_media_staged(self, e: Entry):
        self._preview_media(e)
        self.update_stage()

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

    def _preview_epub(self, e: Entry):
        """EPUB: cover, book details and a chapter reader. The book is read off-thread."""
        from . import epub
        self._shown = e.path
        path = e.path
        self._epub_req = getattr(self, "_epub_req", 0) + 1
        req = self._epub_req
        self._set_note("Opening book…")
        places.run_async(lambda: epub.read_book(path), lambda res: self._epub_ready(path, res, req))

    def _epub_ready(self, path, res, req=0):
        """The book's details arrived: cover, title, author and the reader."""
        if self._shown != path or req != getattr(self, "_epub_req", 0):
            return
        self._set_note("")
        if not isinstance(res, dict):
            self._set_note(f"Can't read this book: {res}")
            return
        if res.get("cover"):
            img = QPixmap()
            if img.loadFromData(res["cover"]):
                h = min(220, int(self.height() * 0.3))
                self._pix = None
                self.preview.setPixmap(img.scaledToHeight(h, Qt.TransformationMode.SmoothTransformation))
                self.preview.setFixedHeight(h + 6)
        if res.get("title"):
            self.title.setText(res["title"])
        self._row("Author", res.get("author", ""))
        self._row("Publisher", res.get("publisher", ""))
        self._row("Published", res.get("date", ""))
        self._row("Language", res.get("language", ""))
        self._row("Chapters", str(res.get("chapters", "")))
        desc = res.get("description", "")
        if desc:
            self._set_note(desc[:420] + ("…" if len(desc) > 420 else ""))
        if res.get("spine"):
            self.epub.open(path, res["spine"])
            self.epub.show()
            self.lay.setStretchFactor(self.epub, 1)
            self.lay.setStretch(self._stretch_index, 0)

    def _pop_out_epub(self):
        if self._epub is not None and self._epub.book:
            import os
            from .epub import EpubWindow
            EpubWindow(self.epub, os.path.basename(self.epub.book), self.window()).show()

    def _preview_raw(self, e: Entry):
        """Camera RAW: the embedded full-size JPEG (or Windows' RAW thumbnail), read off-thread."""
        self._shown = e.path
        self._raw_req = getattr(self, "_raw_req", 0) + 1
        req, path = self._raw_req, e.path
        self._set_note("Reading RAW preview…")
        places.run_async(lambda: raw.read_raw(path), lambda res: self._raw_ready(path, req, res))

    def _raw_ready(self, path, req, res):
        """A camera RAW's embedded preview and shooting details arrived."""
        if self._shown != path or req != getattr(self, "_raw_req", 0) or not isinstance(res, dict):
            return
        self._set_note("")
        info = res["info"]
        cam = " ".join(x for x in (info.get("make", ""), info.get("model", "")) if x)
        if info.get("make") and info.get("model", "").lower().startswith(info["make"].lower()):
            cam = info["model"]
        self._row("Camera", cam)
        self._row("Lens", info.get("lens", ""))
        shot = "  ·  ".join(x for x in (info.get("exposure"), info.get("aperture"), info.get("iso"), info.get("focal")) if x)
        self._row("Exposure", shot)
        taken = info.get("taken", "")
        if len(taken) >= 16 and taken[4] == ":":
            taken = taken[:10].replace(":", "-") + taken[10:16]
        self._row("Taken", taken)
        img = res["image"]
        if img.isNull():
            self._set_note(res["error"])
            return
        self._row("Preview", f"{img.width()} × {img.height()} px · "
                  + ("embedded camera JPEG" if res["source"] == "embedded" else "Windows RAW thumbnail"))
        self.zoom.load_qimage(img, path)
        self.preview.clear()
        self.preview.setFixedHeight(0)
        self._show_zoom(True)

    def _preview_braw(self, e: Entry):
        """Blackmagic RAW: clip details only (frames need Blackmagic's own decoder)."""
        self._shown = e.path
        path = e.path
        places.run_async(lambda: raw.braw_info(path), lambda res: self._braw_ready(path, res))

    def _braw_ready(self, path, res):
        """Blackmagic RAW clip details arrived."""
        if self._shown != path or not isinstance(res, dict):
            return
        if res.get("error"):
            self._set_note(res["error"])
            return
        from .media import fmt_time
        vid = res.get("resolution", "")
        if res.get("fps"):
            vid += f" @ {res['fps']:.3g} fps"
        self._row("Video", vid)
        if res.get("duration_s"):
            self._row("Duration", fmt_time(int(res["duration_s"] * 1000)))
        self._row("Codec", "Blackmagic RAW" + (f" ({res['codec']})" if res.get("codec") else ""))
        self._set_note("Blackmagic RAW frames can't be previewed here: they need Blackmagic's own decoder. "
                       "Double-click opens the clip in Blackmagic RAW Player.")

    def _preview_pdf(self, e: Entry):
        """Show a PDF in the zoomable viewer."""
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
        if on or self._zoom is not None:
            self.zoom.setVisible(on)
        self.update_stage()

    def update_stage(self):
        """Show the stage (with the user's height) when there's a photo/PDF/video to show."""
        busy = (self._zoom is not None and not self._zoom.isHidden()) or not self.media.isHidden()             or not self.folder_list.isHidden()
        was = not self.stage.isHidden()
        self.stage.setVisible(busy)
        if busy and not was:
            self._apply_split()

    def _apply_split(self):
        total = max(200, self.split.height())
        if self.details.isVisible():
            top = int(total * self.split_ratio)
        else:                                   # details folded: the preview gets almost everything
            top = total - max(60, self.body.sizeHint().height() if self.body.sizeHint().height() < total // 3 else 90)
        self.split.setSizes([top, max(60, total - top)])

    def set_resizable(self, on: bool):
        h = self.split.handle(1)
        h.setEnabled(on)
        self.split.setHandleWidth(9 if on else 4)
        h.setCursor(Qt.CursorShape.SplitVCursor if on else Qt.CursorShape.ArrowCursor)

    def _split_moved(self, *_):
        sizes = self.split.sizes()
        if sum(sizes) and self.details.isVisible():
            self.split_ratio = sizes[0] / sum(sizes)
            self.splitChanged.emit(self.split_ratio)

    def _pop_out(self):
        if self._zoom is not None and self._zoom.kind:
            import os
            from .zoomview import ViewerWindow
            ViewerWindow(self.zoom, os.path.basename(self.zoom.path), self.window()).show()

    def _preview_text(self, e: Entry):
        """Show the start of a text file (TEXT_LIMIT bytes) with syntax colors."""
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
        self._hl = PreviewHighlighter(self.text.document(), e.ext, theme.colors()) \
            if self.prefs.get("text_syntax_colors", True) else None
        self._show_text(True)
