"""Find duplicate files and clear out the extra copies.

Fast in three passes: files are grouped by size (free, from the folder listing), then by a
hash of their first 64 KB, and only files that still match are read in full. Hard links to
the same file aren't counted as copies. Deleting goes to the Recycle Bin and always leaves at
least one copy of each file.
"""
from __future__ import annotations

import hashlib
import os
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox,
                               QProgressBar, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import fileops
from ..model import human_size
from .common import FolderField, Worker, excluded, parse_patterns

HEAD = 64 * 1024
CHUNK = 1024 * 1024
MIN_SIZES = [("Any size", 1), ("100 KB and up", 100 * 1024), ("1 MB and up", 2**20),
             ("10 MB and up", 10 * 2**20), ("100 MB and up", 100 * 2**20)]
PATH_ROLE = Qt.ItemDataRole.UserRole


def _hash(path: str, limit: int | None, worker=None) -> bytes | None:
    """BLAKE2 hash of a file, or of its first `limit` bytes."""
    h = hashlib.blake2b(digest_size=16)
    try:
        with open(path, "rb") as f:
            left = limit
            while left is None or left > 0:
                if worker is not None and worker.cancelled:
                    return None
                buf = f.read(CHUNK if left is None else min(CHUNK, left))
                if not buf:
                    break
                h.update(buf)
                if left is not None:
                    left -= len(buf)
    except OSError:
        return None
    return h.digest()


def find_duplicates(folders: list[str], min_size: int, patterns: list[str], worker=None):
    """Returns (groups, errors); each group is a list of (path, size, mtime), biggest waste first."""
    errors = []
    by_size: dict[int, list] = {}
    seen_files = set()
    last = [0.0]

    def say(msg):
        now = time.monotonic()
        if worker is not None and now - last[0] > 0.1:
            last[0] = now
            worker.progress.emit(msg)

    count = 0
    for top in folders:
        for root, dirs, files in os.walk(top, onerror=lambda e: errors.append(f"{e.filename}: {e.strerror}")):
            if worker is not None and worker.cancelled:
                return [], errors
            dirs[:] = [d for d in dirs if not excluded(d, patterns) and not os.path.islink(os.path.join(root, d))]
            for name in files:
                if excluded(name, patterns):
                    continue
                p = os.path.join(root, name)
                try:
                    st = os.stat(p, follow_symlinks=False)
                except OSError:
                    continue
                key = (st.st_dev, st.st_ino) if st.st_ino else os.path.normcase(p)
                if key in seen_files or st.st_size < min_size or st.st_size == 0:
                    continue                       # the same file twice (overlapping folders, hard links)
                seen_files.add(key)
                by_size.setdefault(st.st_size, []).append((p, st.st_size, st.st_mtime))
                count += 1
            say(f"Looking… {count:,} files")

    candidates = [g for g in by_size.values() if len(g) > 1]
    total = sum(len(g) for g in candidates)
    groups, done = [], 0
    for g in candidates:
        heads: dict = {}
        for item in g:
            if worker is not None and worker.cancelled:
                return [], errors
            done += 1
            say(f"Checking {done:,} of {total:,} possible copies…")
            d = _hash(item[0], HEAD)
            if d is not None:
                heads.setdefault(d, []).append(item)
        for same_head in heads.values():
            if len(same_head) < 2:
                continue
            if same_head[0][1] <= HEAD:           # the head was the whole file
                groups.append(same_head)
                continue
            full: dict = {}
            for item in same_head:
                say(f"Reading {os.path.basename(item[0])}…")
                d = _hash(item[0], None, worker)
                if d is not None:
                    full.setdefault(d, []).append(item)
            groups += [x for x in full.values() if len(x) > 1]
    groups.sort(key=lambda x: x[0][1] * (len(x) - 1), reverse=True)
    return groups, errors


class DuplicatesWindow(QWidget):
    """Find duplicates: search, review the groups, recycle the extra copies."""
    _open: list = []

    @classmethod
    def open(cls, config, folders, owner=None):
        w = cls(config, folders, owner)
        cls._open.append(w)
        w.show()
        return w

    def __init__(self, config, folders, owner=None):
        super().__init__(owner, Qt.WindowType.Window)
        self.config = config
        self.owner = owner
        self.worker = None
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowTitle("Find duplicates — Burrow")
        self.resize(900, 620)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 10)
        lay.setSpacing(8)
        top = QHBoxLayout()
        self.folders = FolderField("; ".join(folders), "Folders to search (separate several with ;)")
        self.folders._browse = self._add_folder
        top.addWidget(QLabel("Look in"))
        top.addWidget(self.folders, 1)
        self.min_size = QComboBox()
        for label, _ in MIN_SIZES:
            self.min_size.addItem(label)
        self.min_size.setCurrentIndex(1)
        top.addWidget(self.min_size)
        self.excl = QLineEdit("Thumbs.db; desktop.ini; .git; node_modules")
        self.excl.setPlaceholderText("Names to skip")
        self.excl.setMaximumWidth(220)
        top.addWidget(self.excl)
        self.find_btn = QPushButton("Find")
        self.find_btn.setDefault(True)
        self.find_btn.clicked.connect(self.run)
        top.addWidget(self.find_btn)
        lay.addLayout(top)

        t = self.tree = QTreeWidget()
        t.setHeaderLabels(["File", "Folder", "Modified"])
        t.setUniformRowHeights(True)
        t.setAlternatingRowColors(False)
        h = t.header()
        h.setStretchLastSection(False)
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        t.setColumnWidth(0, 300)
        t.setColumnWidth(2, 130)
        t.itemChanged.connect(lambda *_: self._count())
        t.itemDoubleClicked.connect(self._reveal)
        t.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        t.customContextMenuRequested.connect(self._menu)
        lay.addWidget(t, 1)

        bottom = QHBoxLayout()
        bottom.addWidget(QLabel("Tick"))
        self.rule = QComboBox()
        self.rule.addItems(["All but the oldest", "All but the newest", "All but the shortest path", "None"])
        self.rule.activated.connect(self._apply_rule)
        bottom.addWidget(self.rule)
        self.summary = QLabel("Pick folders and press Find.")
        bottom.addWidget(self.summary, 1)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setMaximumHeight(6)
        self.bar.setMaximumWidth(160)
        self.bar.hide()
        bottom.addWidget(self.bar)
        self.del_btn = QPushButton("Move ticked to Recycle Bin")
        self.del_btn.setEnabled(False)
        self.del_btn.clicked.connect(self.delete_ticked)
        bottom.addWidget(self.del_btn)
        lay.addLayout(bottom)

    def _add_folder(self):
        from PySide6.QtWidgets import QFileDialog
        d = QFileDialog.getExistingDirectory(self, "Add a folder")
        if d:
            cur = self.folders.edit.text().strip()
            self.folders.edit.setText(f"{cur}; {os.path.normpath(d)}" if cur else os.path.normpath(d))

    def _folder_list(self):
        return [os.path.normpath(p.strip().strip('"')) for p in self.folders.edit.text().split(";") if p.strip()]

    def run(self):
        """Start the search on a worker thread (or stop it)."""
        if self.worker is not None:
            self.worker.cancel()
            return
        folders = [f for f in self._folder_list() if os.path.isdir(f)]
        if not folders:
            QMessageBox.information(self, "Find duplicates", "Choose at least one existing folder.")
            return
        min_size = MIN_SIZES[self.min_size.currentIndex()][1]
        patterns = parse_patterns(self.excl.text())
        self.tree.clear()
        self.find_btn.setText("Stop")
        self.del_btn.setEnabled(False)
        self.bar.setRange(0, 0)
        self.bar.show()
        self.worker = Worker(lambda w: find_duplicates(folders, min_size, patterns, w), self)
        self.worker.progress.connect(self.summary.setText)
        self.worker.done.connect(self._found)
        self.worker.start()

    def _found(self, res):
        """Show the groups, biggest saving first, and tick by the chosen rule."""
        cancelled = self.worker.cancelled
        self.worker = None
        self.find_btn.setText("Find")
        self.bar.hide()
        if isinstance(res, Exception):
            self.summary.setText(f"Couldn't search: {res}")
            return
        groups, _errors = res
        self.tree.blockSignals(True)
        for g in groups:
            size = g[0][1]
            top = QTreeWidgetItem(self.tree, [f"{len(g)} copies · {human_size(size)} each",
                                              f"{human_size(size * (len(g) - 1))} to free", ""])
            f = top.font(0)
            f.setBold(True)
            top.setFont(0, f)
            top.setFirstColumnSpanned(False)
            top.setFlags(Qt.ItemFlag.ItemIsEnabled)
            for path, sz, mtime in g:
                it = QTreeWidgetItem(top, [os.path.basename(path), os.path.dirname(path),
                                           time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))])
                it.setData(0, PATH_ROLE, (path, sz, mtime))
                it.setToolTip(0, path)
                it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(0, Qt.CheckState.Unchecked)
            top.setExpanded(True)
        self.tree.blockSignals(False)
        if not groups:
            self.summary.setText("Stopped." if cancelled else "No duplicates found.")
            return
        self._apply_rule()

    def _apply_rule(self, *_):
        """Tick every copy but the one the rule keeps (oldest, newest or shortest path)."""
        rule = self.rule.currentIndex()
        self.tree.blockSignals(True)
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            kids = [top.child(j) for j in range(top.childCount())]
            keep = None
            if rule == 0:
                keep = min(kids, key=lambda k: k.data(0, PATH_ROLE)[2])
            elif rule == 1:
                keep = max(kids, key=lambda k: k.data(0, PATH_ROLE)[2])
            elif rule == 2:
                keep = min(kids, key=lambda k: (len(k.data(0, PATH_ROLE)[0]), k.data(0, PATH_ROLE)[0]))
            for k in kids:
                k.setCheckState(0, Qt.CheckState.Unchecked if rule == 3 or k is keep else Qt.CheckState.Checked)
        self.tree.blockSignals(False)
        self._count()

    def _ticked(self):
        out, all_ticked = [], 0
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            kids = [top.child(j) for j in range(top.childCount())]
            ticked = [k for k in kids if k.checkState(0) == Qt.CheckState.Checked]
            if ticked and len(ticked) == len(kids):
                all_ticked += 1
            out += [k.data(0, PATH_ROLE) for k in ticked]
        return out, all_ticked

    def _count(self):
        ticked, all_ticked = self._ticked()
        groups = self.tree.topLevelItemCount()
        text = f"{groups:,} files with copies · {len(ticked):,} ticked · {human_size(sum(t[1] for t in ticked))}"
        if all_ticked:
            text += f" · every copy ticked in {all_ticked} group(s)"
        self.summary.setText(text)
        self.del_btn.setEnabled(bool(ticked))

    def delete_ticked(self):
        """Recycle the ticked copies, never the last copy of a file."""
        ticked, all_ticked = self._ticked()
        if all_ticked:
            QMessageBox.warning(self, "Find duplicates", f"In {all_ticked} group(s) every copy is ticked. "
                                "Untick at least one copy of each file to keep it.")
            return
        size = human_size(sum(t[1] for t in ticked))
        if QMessageBox.question(self, "Find duplicates", f"Move {len(ticked):,} file(s) ({size}) to the Recycle Bin?") \
                != QMessageBox.StandardButton.Yes:
            return
        ok, _aborted = fileops.delete_paths([t[0] for t in ticked], int(self.winId()))
        gone = {t[0] for t in ticked if not os.path.exists(t[0])}
        self.tree.blockSignals(True)
        for i in reversed(range(self.tree.topLevelItemCount())):
            top = self.tree.topLevelItem(i)
            for j in reversed(range(top.childCount())):
                if top.child(j).data(0, PATH_ROLE)[0] in gone:
                    top.removeChild(top.child(j))
            if top.childCount() < 2:
                self.tree.takeTopLevelItem(i)
        self.tree.blockSignals(False)
        self._count()
        if not ok and len(gone) < len(ticked):
            QMessageBox.warning(self, "Find duplicates", f"{len(ticked) - len(gone)} file(s) couldn't be deleted.")

    def _reveal(self, item, _col=0):
        data = item.data(0, PATH_ROLE)
        if not data:
            return
        win = self.owner
        if win is not None and hasattr(win, "new_tab"):
            win.new_tab(os.path.dirname(data[0]))
            win.view().select_paths([data[0]], data[0])
            win.activateWindow()
        else:
            fileops.show_in_explorer(data[0], True)

    def _menu(self, pos):
        it = self.tree.itemAt(pos)
        if it is None or not it.data(0, PATH_ROLE):
            return
        path = it.data(0, PATH_ROLE)[0]
        m = QMenu(self)
        m.addAction("Open").triggered.connect(lambda: fileops.open_path(path))
        m.addAction("Show in Burrow").triggered.connect(lambda: self._reveal(it))
        m.addAction("Tick every file in this folder").triggered.connect(lambda: self._tick_folder(os.path.dirname(path)))
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def _tick_folder(self, folder):
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            for j in range(top.childCount()):
                k = top.child(j)
                if os.path.dirname(k.data(0, PATH_ROLE)[0]) == folder:
                    k.setCheckState(0, Qt.CheckState.Checked)

    def closeEvent(self, ev):
        if self.worker is not None:
            self.worker.wait()
        if self in DuplicatesWindow._open:
            DuplicatesWindow._open.remove(self)
        super().closeEvent(ev)
