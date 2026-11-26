"""Put loose files into folders named after them. Files that share a name go together, so a
movie and its subtitles, poster and .nfo end up in one folder:

    Heat (1995).mkv, Heat (1995).en.srt, Heat (1995).nfo   ->  Heat (1995)\\...

Sidecar files (subtitles, .nfo, artwork, ...) may carry extra tags after the title, like
.en, .eng.forced or -poster; they join the file whose name they start with.
"""
from __future__ import annotations

import os

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QTreeWidget, QTreeWidgetItem, QVBoxLayout

from .. import tags

SIDECAR_EXTS = {"srt", "ass", "ssa", "sub", "idx", "vtt", "sup", "smi", "nfo", "txt", "xml", "jpg", "jpeg",
                "png", "webp", "tbn", "lrc", "cue", "log", "md5", "sfv", "par2", "json"}
SEPARATORS = ".-_ ["


def _stem(name: str) -> str:
    return os.path.splitext(name)[0] if "." in name[1:] else name


def plan(paths: list[str]) -> dict[str, list[str]]:
    """Folder name -> files that go in it. Only files are considered; folders are left alone."""
    files = [p for p in paths if os.path.isfile(p)]
    main = [p for p in files if os.path.splitext(p)[1][1:].lower() not in SIDECAR_EXTS]
    groups: dict[str, list[str]] = {}
    keys: dict[str, str] = {}                      # lower-case stem -> folder name as written
    for p in sorted(main, key=lambda x: len(os.path.basename(x))):
        s = _stem(os.path.basename(p))
        k = keys.setdefault(s.lower(), s)
        groups.setdefault(k, []).append(p)
    longest_first = sorted(keys, key=len, reverse=True)
    for p in files:
        if p in main:
            continue
        s = _stem(os.path.basename(p))
        low = s.lower()
        # the longest main title this sidecar starts with, followed by a tag separator
        k = next((m for m in longest_first if low == m or (low.startswith(m) and low[len(m)] in SEPARATORS)), None)
        if k is None:                               # no video/document of its own name: its own folder
            k = low
            keys.setdefault(k, s)
        groups.setdefault(keys[k], []).append(p)
    return groups


def apply(folder: str, groups: dict[str, list[str]]) -> list[str]:
    """Create the folders and move the files in. Returns errors."""
    errors = []
    for name, members in groups.items():
        target = os.path.join(folder, name)
        try:
            if os.path.exists(target) and not os.path.isdir(target):
                # a file without extension has the folder's name: move it aside first
                tmp = target + ".burrow-tmp"
                os.rename(target, tmp)
                os.makedirs(target)
                members = [tmp if m == target else m for m in members]
            else:
                os.makedirs(target, exist_ok=True)
        except OSError as ex:
            errors.append(f"{name}: {ex.strerror or ex}")
            continue
        for m in members:
            final = os.path.basename(m[:-len(".burrow-tmp")] if m.endswith(".burrow-tmp") else m)
            dest = os.path.join(target, final)
            if os.path.exists(dest):
                errors.append(f"{final}: already in {name}")
                continue
            try:
                os.rename(m, dest)
                tags.store().renamed(m, dest)
            except OSError as ex:
                errors.append(f"{final}: {ex.strerror or ex}")
    return errors


class FolderizeDialog(QDialog):
    """Shows the folders that will be made, and makes them on OK."""

    def __init__(self, folder: str, groups: dict[str, list[str]], parent=None):
        super().__init__(parent)
        self.folder, self.groups = folder, groups
        n_files = sum(len(v) for v in groups.values())
        self.setWindowTitle("Folders for loose files")
        self.resize(560, 460)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"{len(groups)} folder(s) for {n_files} file(s). Files with the same name share a folder."))
        tree = QTreeWidget()
        tree.setHeaderHidden(True)
        tree.setUniformRowHeights(True)
        icons = None
        try:
            from ..model import icon_cache
            icons = icon_cache()
        except ImportError:
            pass
        for name in sorted(groups, key=str.lower):
            top = QTreeWidgetItem(tree, [name])
            if icons is not None:
                top.setIcon(0, icons.folder)
            for m in sorted(groups[name], key=lambda x: os.path.basename(x).lower()):
                QTreeWidgetItem(top, [os.path.basename(m)])
            top.setExpanded(len(groups[name]) > 1)
        lay.addWidget(tree, 1)
        b = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        b.addButton("Create folders", QDialogButtonBox.ButtonRole.AcceptRole)
        b.accepted.connect(self.accept)
        b.rejected.connect(self.reject)
        lay.addWidget(b)
