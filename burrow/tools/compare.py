"""Compare two folders and synchronize them (for backups), in the spirit of FreeFileSync.

Compare by time & size (fast, like most backup tools) or by content. Then pick a direction:
  Mirror →   make the right side an exact copy of the left (extra files on the right go)
  Update →   copy new and changed files to the right, delete nothing
  Two-way ↔  copy new and newer files both ways, delete nothing
Every row's action can be changed before you press Synchronize. Deleted and overwritten-away
files go to the Recycle Bin unless you turn that off.
"""
from __future__ import annotations

import filecmp
import os
import time

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, QThread, Slot
from PySide6.QtGui import QColor, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QMenu, QMessageBox, QProgressBar, QPushButton, QToolButton,
                               QTreeView, QVBoxLayout, QWidget)

from .. import fileops, theme
from ..model import human_size
from .common import FolderField, Worker, excluded, parse_patterns

EQUAL, LEFT_ONLY, RIGHT_ONLY, LEFT_NEWER, RIGHT_NEWER, DIFFERENT, CLASH = range(7)
COPY_R, COPY_L, DEL_R, DEL_L, SKIP = "copy_r", "copy_l", "del_r", "del_l", "skip"
STATUS_TEXT = {EQUAL: "Same", LEFT_ONLY: "Only on the left", RIGHT_ONLY: "Only on the right",
               LEFT_NEWER: "Left is newer", RIGHT_NEWER: "Right is newer",
               DIFFERENT: "Same date, different size or content", CLASH: "File on one side, folder on the other"}
ACTION_TEXT = {COPY_R: "→", COPY_L: "←", DEL_R: "× right", DEL_L: "left ×", SKIP: "·"}
MODES = [("mirror", "Mirror  →"), ("update", "Update  →"), ("twoway", "Two-way  ↔")]
MODE_TIPS = {"mirror": "Make the right folder an exact copy of the left. Files only on the right are deleted.",
             "update": "Copy new and changed files from left to right. Nothing is deleted.",
             "twoway": "Copy new and newer files in both directions. Nothing is deleted."}
TIME_SLACK = 2.0          # FAT/exFAT keep times to 2 s


class Row:
    __slots__ = ("rel", "is_dir", "l", "r", "status", "action")

    def __init__(self, rel, is_dir, l, r, status):
        self.rel, self.is_dir, self.l, self.r, self.status = rel, is_dir, l, r, status
        self.action = SKIP


def default_action(status: int, mode: str) -> str:
    table = {
        "mirror": {LEFT_ONLY: COPY_R, RIGHT_ONLY: DEL_R, LEFT_NEWER: COPY_R, RIGHT_NEWER: COPY_R, DIFFERENT: COPY_R},
        "update": {LEFT_ONLY: COPY_R, LEFT_NEWER: COPY_R},
        "twoway": {LEFT_ONLY: COPY_R, RIGHT_ONLY: COPY_L, LEFT_NEWER: COPY_R, RIGHT_NEWER: COPY_L},
    }
    return table.get(mode, {}).get(status, SKIP)


# ----- scanning (worker thread) ------------------------------------------------------
def _list(folder: str, errors: list) -> dict:
    """lower-case name -> (name, is_dir, size, mtime). Links and junctions are left alone."""
    out = {}
    try:
        with os.scandir(folder) as it:
            for de in it:
                try:
                    if de.is_symlink() or getattr(de, "is_junction", lambda: False)():
                        continue
                    st = de.stat(follow_symlinks=False)
                    is_dir = de.is_dir(follow_symlinks=False)
                    out[de.name.lower()] = (de.name, is_dir, 0 if is_dir else st.st_size, st.st_mtime)
                except OSError as ex:
                    errors.append(f"{de.path}: {ex.strerror or ex}")
    except FileNotFoundError:
        pass
    except OSError as ex:
        errors.append(f"{folder}: {ex.strerror or ex}")
    return out


def _tree_size(path: str, patterns, worker) -> tuple[int, int]:
    size = count = 0
    for root, dirs, files in os.walk(path):
        if worker is not None and worker.cancelled:
            break
        dirs[:] = [d for d in dirs if not excluded(d, patterns) and not os.path.islink(os.path.join(root, d))]
        for f in files:
            if excluded(f, patterns):
                continue
            try:
                size += os.path.getsize(os.path.join(root, f))
                count += 1
            except OSError:
                pass
    return size, count


def compare(left: str, right: str, patterns: list[str], content: bool, worker=None):
    """Returns (rows, errors). Folders present on one side only become one row each."""
    rows, errors = [], []
    last = [0.0]
    scanned = [0]

    def report(rel):
        scanned[0] += 1
        now = time.monotonic()
        if worker is not None and now - last[0] > 0.1:
            last[0] = now
            worker.progress.emit(f"Comparing… {scanned[0]:,} folders · {rel or os.path.basename(left)}")

    def walk(rel):
        if worker is not None and worker.cancelled:
            return
        report(rel)
        L = _list(os.path.join(left, rel), errors)
        R = _list(os.path.join(right, rel), errors)
        for key in sorted(set(L) | set(R)):
            l, r = L.get(key), R.get(key)
            name = (l or r)[0]
            if excluded(name, patterns):
                continue
            sub = os.path.join(rel, name)
            if l and r:
                if l[1] and r[1]:
                    walk(sub)
                    continue
                if l[1] != r[1]:
                    rows.append(Row(sub, None, (l[2], l[3], 0), (r[2], r[3], 0), CLASH))
                    continue
                lt, rt = l[3], r[3]
                if l[2] == r[2]:
                    if abs(lt - rt) <= TIME_SLACK or abs(abs(lt - rt) - 3600) <= TIME_SLACK and not content:
                        status = EQUAL                     # (an exact hour: a daylight-saving shift on FAT)
                    elif content:
                        try:
                            same = filecmp.cmp(os.path.join(left, sub), os.path.join(right, sub), shallow=False)
                        except OSError as ex:
                            errors.append(f"{sub}: {ex.strerror or ex}")
                            same = False
                        status = EQUAL if same else (LEFT_NEWER if lt > rt else RIGHT_NEWER)
                    else:
                        status = LEFT_NEWER if lt > rt else RIGHT_NEWER
                elif abs(lt - rt) <= TIME_SLACK:
                    status = DIFFERENT
                else:
                    status = LEFT_NEWER if lt > rt else RIGHT_NEWER
                rows.append(Row(sub, False, (l[2], lt, 1), (r[2], rt, 1), status))
            else:
                side = l or r
                is_dir = side[1]
                if is_dir:
                    size, count = _tree_size(os.path.join(left if l else right, sub), patterns, worker)
                    info = (size, side[3], count)
                else:
                    info = (side[2], side[3], 1)
                rows.append(Row(sub, is_dir, info if l else None, info if r else None, LEFT_ONLY if l else RIGHT_ONLY))

    walk("")
    return rows, errors


# ----- table ------------------------------------------------------------------------
COLS = ["Name", "Size", "Modified", "", "Size", "Modified"]


class RowsModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[Row] = []
        self.accent = QColor("#4d8dff")
        self.danger = QColor("#e5484d")
        self.dim = QColor(128, 128, 128)

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(COLS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLS[section]
        return None

    @staticmethod
    def _size(info, is_dir):
        if info is None:
            return ""
        if is_dir:
            return f"{human_size(info[0])} · {info[2]:,} files"
        return human_size(info[0])

    @staticmethod
    def _date(info):
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(info[1])) if info else ""

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        r = self.rows[index.row()]
        c = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if c == 0:
                return r.rel + (os.sep if r.is_dir else "")
            if c == 1:
                return self._size(r.l, r.is_dir)
            if c == 2:
                return self._date(r.l)
            if c == 3:
                return "=" if r.status == EQUAL else ACTION_TEXT[r.action]
            if c == 4:
                return self._size(r.r, r.is_dir)
            if c == 5:
                return self._date(r.r)
        elif role == Qt.ItemDataRole.ToolTipRole:
            return f"{r.rel}\n{STATUS_TEXT[r.status]}"
        elif role == Qt.ItemDataRole.TextAlignmentRole:
            if c == 3:
                return Qt.AlignmentFlag.AlignCenter
            if c in (1, 4):
                return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        elif role == Qt.ItemDataRole.ForegroundRole:
            if r.status == EQUAL or (r.action == SKIP and c != 0):
                return self.dim
            if c == 3:
                return self.danger if r.action in (DEL_R, DEL_L) else self.accent
            if (c in (1, 2) and r.status == LEFT_NEWER) or (c in (4, 5) and r.status == RIGHT_NEWER):
                return self.accent
        elif role == Qt.ItemDataRole.UserRole:
            return r
        elif role == Qt.ItemDataRole.UserRole + 1:            # sort keys
            return [r.rel.lower(), r.l[0] if r.l else -1, r.l[1] if r.l else 0, r.action,
                    r.r[0] if r.r else -1, r.r[1] if r.r else 0][c]
        return None


class RowsProxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.show_equal = False
        self.text = ""
        self.setSortRole(Qt.ItemDataRole.UserRole + 1)

    def filterAcceptsRow(self, src_row, parent):
        r = self.sourceModel().rows[src_row]
        if r.status == EQUAL and not self.show_equal:
            return False
        return not self.text or self.text in r.rel.lower()


# ----- window ------------------------------------------------------------------------
class CompareWindow(QWidget):
    _open: list = []

    @classmethod
    def open(cls, config, left, right, owner=None):
        w = cls(config, left, right, owner)
        cls._open.append(w)
        w.show()
        if left and right and os.path.isdir(left) and os.path.isdir(right):
            w.run_compare()
        return w

    def __init__(self, config, left, right, owner=None):
        super().__init__(owner, Qt.WindowType.Window)
        self.config = config
        self.owner = owner
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowTitle("Compare & sync — Burrow")
        self.resize(1080, 680)
        self.worker = None
        self.job = None
        self.rows: list[Row] = []
        self.compared = None                 # (left, right) the table belongs to

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 10)
        lay.setSpacing(8)
        top = QHBoxLayout()
        self.left = FolderField(left, "Left folder (source)")
        self.right = FolderField(right, "Right folder (backup)")
        swap = QToolButton()
        swap.setText("⇄")
        swap.setToolTip("Swap left and right")
        swap.clicked.connect(self._swap)
        recent = QToolButton()
        recent.setText("Recent")
        recent.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.recent_menu = QMenu(recent)
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        recent.setMenu(self.recent_menu)
        top.addWidget(self.left, 1)
        top.addWidget(swap)
        top.addWidget(self.right, 1)
        top.addWidget(recent)
        lay.addLayout(top)

        opts = QHBoxLayout()
        opts.addWidget(QLabel("Compare by"))
        self.by = QComboBox()
        self.by.addItems(["Time and size", "Content"])
        self.by.setToolTip("Content reads both files when the sizes match: slower, but sure.")
        opts.addWidget(self.by)
        opts.addSpacing(10)
        opts.addWidget(QLabel("Sync"))
        self.mode = QComboBox()
        for key, label in MODES:
            self.mode.addItem(label, key)
            self.mode.setItemData(self.mode.count() - 1, MODE_TIPS[key], Qt.ItemDataRole.ToolTipRole)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        opts.addWidget(self.mode)
        opts.addSpacing(10)
        opts.addWidget(QLabel("Skip"))
        self.excl = QLineEdit(config.get("sync_excludes") or "")
        self.excl.setPlaceholderText("Names to leave out, e.g. Thumbs.db; *.tmp; node_modules")
        opts.addWidget(self.excl, 1)
        self.compare_btn = QPushButton("Compare")
        self.compare_btn.setDefault(True)
        self.compare_btn.clicked.connect(self.run_compare)
        opts.addWidget(self.compare_btn)
        lay.addLayout(opts)

        self.model = RowsModel(self)
        self.proxy = RowsProxy(self)
        self.proxy.setSourceModel(self.model)
        t = self.table = QTreeView()
        t.setModel(self.proxy)
        t.setRootIsDecorated(False)
        t.setUniformRowHeights(True)
        t.setAllColumnsShowFocus(True)
        t.setSortingEnabled(True)
        t.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        t.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        t.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        t.customContextMenuRequested.connect(self._menu)
        t.doubleClicked.connect(self._cycle_action)
        h = t.header()
        h.setStretchLastSection(False)
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c, w in ((1, 120), (2, 130), (3, 64), (4, 120), (5, 130)):
            h.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
            t.setColumnWidth(c, w)
        lay.addWidget(t, 1)

        bottom = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter by name")
        self.filter.setClearButtonEnabled(True)
        self.filter.setMaximumWidth(220)
        self.filter.textChanged.connect(self._filter)
        self.show_equal = QCheckBox("Show same")
        self.show_equal.toggled.connect(self._filter)
        self.summary = QLabel("Pick two folders and press Compare.")
        self.summary.setWordWrap(True)
        self.recycle = QCheckBox("Deleted files go to the Recycle Bin")
        self.recycle.setChecked(True)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setMaximumHeight(6)
        self.bar.setMaximumWidth(160)
        self.bar.hide()
        self.sync_btn = QPushButton("Synchronize")
        self.sync_btn.setEnabled(False)
        self.sync_btn.clicked.connect(self.run_sync)
        bottom.addWidget(self.filter)
        bottom.addWidget(self.show_equal)
        bottom.addSpacing(8)
        bottom.addWidget(self.summary, 1)
        bottom.addWidget(self.bar)
        bottom.addWidget(self.recycle)
        bottom.addWidget(self.sync_btn)
        lay.addLayout(bottom)
        self._mode_changed()
        self.apply_theme()

    def apply_theme(self):
        c = theme.colors()
        self.model.accent = QColor(c["accent"])
        self.model.dim = QColor(c["dim"])

    # ----- folders ---------------------------------------------------------------
    def _swap(self):
        a, b = self.left.text(), self.right.text()
        self.left.setText(b)
        self.right.setText(a)
        if self.rows:
            self.run_compare()

    def _fill_recent(self):
        self.recent_menu.clear()
        pairs = self.config.get("sync_pairs") or []
        if not pairs:
            self.recent_menu.addAction("No recent folder pairs").setEnabled(False)
        for l, r, mode in pairs:
            a = self.recent_menu.addAction(f"{l}   ⇄   {r}")
            a.triggered.connect(lambda _=False, l=l, r=r, m=mode: self._use_pair(l, r, m))

    def _use_pair(self, l, r, mode):
        self.left.setText(l)
        self.right.setText(r)
        i = self.mode.findData(mode)
        if i >= 0:
            self.mode.setCurrentIndex(i)
        self.run_compare()

    def _remember(self):
        l, r, m = self.left.text(), self.right.text(), self.mode.currentData()
        pairs = [p for p in (self.config.get("sync_pairs") or []) if (p[0], p[1]) != (l, r)]
        self.config.set("sync_pairs", [[l, r, m]] + pairs[:7])
        self.config.set("sync_excludes", self.excl.text())

    # ----- compare -------------------------------------------------------------------
    def _busy(self, on: bool, text: str = ""):
        for w in (self.left, self.right, self.by, self.mode, self.excl, self.sync_btn):
            w.setEnabled(not on)
        self.compare_btn.setText("Stop" if on else "Compare")
        self.sync_btn.setEnabled(False)              # _summarize turns it back on when there's work
        self.bar.setVisible(on)
        if on:
            self.bar.setRange(0, 0)
            self.summary.setText(text)

    def run_compare(self):
        if self.worker is not None:
            self.worker.cancel()
            return
        if self.job is not None:
            return
        l, r = self.left.text(), self.right.text()
        if not (os.path.isdir(l) and os.path.isdir(r)):
            QMessageBox.information(self, "Compare & sync", "Choose two existing folders to compare.")
            return
        if fileops.same_path(l, r) or fileops.is_inside(l, r) or fileops.is_inside(r, l):
            QMessageBox.information(self, "Compare & sync", "Pick two separate folders (not one inside the other).")
            return
        patterns = parse_patterns(self.excl.text())
        content = self.by.currentIndex() == 1
        self._remember()
        self._busy(True, "Comparing…")
        self.worker = Worker(lambda w: compare(l, r, patterns, content, w), self)
        self.worker.progress.connect(self.summary.setText)
        self._pair = (l, r)
        self.worker.done.connect(self._compared)    # a bound method: delivered on the GUI thread
        self.worker.start()

    def _compared(self, res):
        pair = self._pair
        cancelled = self.worker.cancelled
        self.worker = None
        self._busy(False)
        if isinstance(res, Exception):
            self.summary.setText(f"Couldn't compare: {res}")
            return
        rows, errors = res
        self.compared = pair
        self.rows = rows
        self._apply_mode()
        self.model.set_rows(rows)
        self._summarize()
        if cancelled:
            self.summary.setText("Stopped. " + self.summary.text())
        if errors:
            self.summary.setText(self.summary.text() + f" · {len(errors)} couldn't be read")
            self.summary.setToolTip("\n".join(errors[:30]))

    def _apply_mode(self):
        mode = self.mode.currentData()
        for r in self.rows:
            r.action = default_action(r.status, mode)

    def _mode_changed(self, *_):
        self.mode.setToolTip(MODE_TIPS[self.mode.currentData()])
        if self.rows:
            self._apply_mode()
            self.model.dataChanged.emit(self.model.index(0, 0), self.model.index(len(self.rows) - 1, len(COLS) - 1))
            self._summarize()

    def _filter(self, *_):
        self.proxy.show_equal = self.show_equal.isChecked()
        self.proxy.text = self.filter.text().strip().lower()
        self.proxy.invalidate()

    def _plan(self):
        copy_r = [r for r in self.rows if r.action == COPY_R]
        copy_l = [r for r in self.rows if r.action == COPY_L]
        dels = [r for r in self.rows if r.action in (DEL_R, DEL_L)]
        return copy_r, copy_l, dels

    def _summarize(self):
        copy_r, copy_l, dels = self._plan()
        same = sum(1 for r in self.rows if r.status == EQUAL)
        size = sum((r.l or (0,))[0] for r in copy_r) + sum((r.r or (0,))[0] for r in copy_l)
        parts = []
        if copy_r:
            parts.append(f"{len(copy_r):,} to copy →")
        if copy_l:
            parts.append(f"{len(copy_l):,} to copy ←")
        if dels:
            parts.append(f"{len(dels):,} to delete")
        skipped = sum(1 for r in self.rows if r.status != EQUAL and r.action == SKIP)
        if skipped:
            parts.append(f"{skipped:,} left alone")
        text = " · ".join(parts) if parts else "Nothing to do: the folders match."
        if size:
            text += f" · {human_size(size)}"
        self.summary.setText(f"{text}   ({same:,} files the same)")
        self.summary.setToolTip("")
        self.sync_btn.setEnabled(bool(copy_r or copy_l or dels))

    # ----- per-row actions --------------------------------------------------------------
    def _selected_rows(self):
        return [self.proxy.data(i, Qt.ItemDataRole.UserRole) for i in self.table.selectionModel().selectedRows(0)]

    def _set_action(self, rows, action):
        for r in rows:
            if r.status == EQUAL or r.status == CLASH and action != SKIP:
                continue
            if action == COPY_R and r.l is None or action == COPY_L and r.r is None:
                continue
            if action == DEL_R and r.r is None or action == DEL_L and r.l is None:
                continue
            r.action = action
        self.model.dataChanged.emit(self.model.index(0, 0), self.model.index(len(self.rows) - 1, len(COLS) - 1))
        self._summarize()

    def _cycle_action(self, index):
        r = self.proxy.data(index, Qt.ItemDataRole.UserRole)
        if r is None or r.status in (EQUAL, CLASH):
            return
        choices = [a for a in (COPY_R, COPY_L, DEL_R, DEL_L, SKIP)
                   if not ((a == COPY_R or a == DEL_L) and r.l is None or (a == COPY_L or a == DEL_R) and r.r is None)]
        self._set_action([r], choices[(choices.index(r.action) + 1) % len(choices)] if r.action in choices else SKIP)

    def _menu(self, pos):
        rows = self._selected_rows()
        if not rows:
            return
        m = QMenu(self)
        for action, label in ((COPY_R, "Copy to the right  →"), (COPY_L, "Copy to the left  ←"),
                              (DEL_R, "Delete on the right"), (DEL_L, "Delete on the left"), (SKIP, "Leave alone")):
            m.addAction(label).triggered.connect(lambda _=False, a=action: self._set_action(rows, a))
        m.addSeparator()
        if len(rows) == 1 and self.compared:
            r = rows[0]
            for side, base, info in (("left", self.compared[0], r.l), ("right", self.compared[1], r.r)):
                if info is not None:
                    path = os.path.join(base, r.rel)
                    m.addAction(f"Show on the {side}").triggered.connect(
                        lambda _=False, p=path: self._reveal(p))
        m.exec(self.table.viewport().mapToGlobal(pos))

    def _reveal(self, path):
        win = self.owner
        if win is not None and hasattr(win, "new_tab"):
            win.new_tab(os.path.dirname(path))
            win.view().select_paths([path], path)
            win.bring_to_front() if hasattr(win, "bring_to_front") else win.activateWindow()
        else:
            fileops.show_in_explorer(path, True)

    def keyPressEvent(self, ev):
        if ev.matches(QKeySequence.StandardKey.Refresh):
            self.run_compare()
            return
        super().keyPressEvent(ev)

    # ----- synchronize ----------------------------------------------------------------
    def run_sync(self):
        if not self.compared or self.job is not None:
            return
        left, right = self.compared
        copy_r, copy_l, dels = self._plan()
        lines = []
        if copy_r:
            lines.append(f"Copy {len(copy_r):,} item(s) to the right")
        if copy_l:
            lines.append(f"Copy {len(copy_l):,} item(s) to the left")
        if dels:
            lines.append(f"Delete {len(dels):,} item(s)" + (" (to the Recycle Bin)" if self.recycle.isChecked()
                                                              else " permanently"))
        overwrite = sum(1 for r in copy_r if r.r is not None) + sum(1 for r in copy_l if r.l is not None)
        if overwrite:
            lines.append(f"{overwrite:,} of the copies replace an existing file")
        if QMessageBox.question(self, "Synchronize", "\n".join(lines) + "\n\nGo ahead?") \
                != QMessageBox.StandardButton.Yes:
            return
        plan = [[os.path.join(left, r.rel), os.path.join(right, r.rel), "overwrite"] for r in copy_r] + \
               [[os.path.join(right, r.rel), os.path.join(left, r.rel), "overwrite"] for r in copy_l]
        self._pending_deletes = [os.path.join(right if r.action == DEL_R else left, r.rel) for r in dels]
        self._busy(True, "Synchronizing…")
        self.compare_btn.setText("Stop")
        try:
            self.compare_btn.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.compare_btn.clicked.connect(lambda: self.job and self.job.cancel())
        self.job = fileops.CopyJob(plan, move=False)
        self.job_thread = QThread(self)
        self.job.moveToThread(self.job_thread)
        self.job_thread.started.connect(self.job.run)
        self.job.progress.connect(self._sync_progress)
        self.job.done.connect(self._synced)
        self.job_thread.start()

    @Slot(object, object, str)           # @Slot: runs on the GUI thread (CopyJob emits from its own)
    def _sync_progress(self, done, total, name):
        if total:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(done * 1000 / total))
        self.summary.setText(f"Copying {human_size(done)} of {human_size(total)}  {name}")

    @Slot(list, bool)
    def _synced(self, errors, cancelled):
        self.job_thread.quit()
        self.job_thread.wait(3000)
        self.job = None
        try:
            self.compare_btn.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.compare_btn.clicked.connect(self.run_compare)
        dels = [p for p in self._pending_deletes if os.path.lexists(p)]
        if dels and not cancelled:
            self.summary.setText("Deleting…")
            fileops.delete_paths(dels, int(self.winId()), permanent=not self.recycle.isChecked())
        self._busy(False)
        if errors:
            QMessageBox.warning(self, "Synchronize", f"{len(errors)} item(s) couldn't be copied:\n\n"
                                + "\n".join(errors[:12]) + ("\n…" if len(errors) > 12 else ""))
        self.run_compare()                         # show what's left (ideally nothing)

    def closeEvent(self, ev):
        if self.job is not None:
            if QMessageBox.question(self, "Synchronize", "Stop synchronizing and close?") \
                    != QMessageBox.StandardButton.Yes:
                ev.ignore()
                return
            self.job.cancel()
            self.job_thread.quit()
            self.job_thread.wait(5000)
        if self.worker is not None:
            self.worker.wait()
        if self in CompareWindow._open:
            CompareWindow._open.remove(self)
        super().closeEvent(ev)
