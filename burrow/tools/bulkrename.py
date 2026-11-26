"""Rename many files at once, with a live preview.

Find & replace (plain or regular expression), then a name pattern with {name}, {n}, {date}
and {parent}, then the case. Extensions stay as they are unless you change their case. Nothing
is renamed until you press Rename, and names that would clash are shown in red first. Swaps
(a→b while b→a) work because renaming happens in two steps.
"""
from __future__ import annotations

import os
import re
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QGridLayout, QHeaderView,
                               QLabel, QLineEdit, QMessageBox, QSpinBox, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout)

from .. import tags, theme
from ..model import INVALID_NAME_CHARS, natural_key

CASES = ["Keep", "lower case", "UPPER CASE", "Title Case"]
EXT_CASES = ["Keep", "lower case", "UPPER CASE"]


def split_name(name: str, is_dir: bool) -> tuple[str, str]:
    if is_dir or "." not in name[1:]:
        return name, ""
    i = name.rfind(".")
    return name[:i], name[i:]


def new_names(paths: list[str], find: str, replace: str, regex: bool, match_case: bool, pattern: str,
              start: int, step: int, digits: int, case: int, ext_case: int) -> list[str]:
    """The new name for each path (same order). Raises re.error for a bad expression."""
    flags = 0 if match_case else re.IGNORECASE
    rx = re.compile(find if regex else re.escape(find), flags) if find else None
    width = digits or len(str(start + step * max(len(paths) - 1, 0)))
    out = []
    for i, p in enumerate(paths):
        name = os.path.basename(p)
        base, ext = split_name(name, os.path.isdir(p))
        if rx is not None:
            base = rx.sub(replace if regex else replace.replace("\\", "\\\\"), base)
        try:
            mtime = os.path.getmtime(p)
        except OSError:
            mtime = time.time()
        n = start + step * i
        new = (pattern or "{name}")
        new = new.replace("{name}", base).replace("{n}", str(n).zfill(width)) \
                 .replace("{date}", time.strftime("%Y-%m-%d", time.localtime(mtime))) \
                 .replace("{parent}", os.path.basename(os.path.dirname(p)))
        if case == 1:
            new = new.lower()
        elif case == 2:
            new = new.upper()
        elif case == 3:
            new = new.title()
        if ext_case == 1:
            ext = ext.lower()
        elif ext_case == 2:
            ext = ext.upper()
        out.append(new.strip().rstrip(".") + ext)
    return out


def problems(paths: list[str], names: list[str]) -> dict[int, str]:
    """Row -> why that new name can't be used."""
    bad = {}
    taken: dict = {}
    for i, (p, n) in enumerate(zip(paths, names)):
        folder = os.path.dirname(p)
        if not n or n in (".", ".."):
            bad[i] = "empty name"
        elif any(c in INVALID_NAME_CHARS for c in n):
            bad[i] = 'can\'t contain \\ / : * ? " < > |'
        key = (os.path.normcase(folder), n.lower())
        if key in taken:
            bad[i] = bad[taken[key]] = "two files would get this name"
        taken[key] = i
    renaming = {os.path.normcase(p) for p in paths}
    for i, (p, n) in enumerate(zip(paths, names)):
        target = os.path.join(os.path.dirname(p), n)
        if i not in bad and os.path.normcase(target) not in renaming and os.path.lexists(target) \
                and os.path.normcase(target) != os.path.normcase(p):
            bad[i] = "a file with this name already exists"
    return bad


def rename_all(pairs: list[tuple[str, str]]) -> list[str]:
    """Rename (old_path, new_path) pairs in two steps so swaps work. Returns errors; a failed
    second step puts the files that didn't make it back under their old names."""
    errors, staged = [], []
    for i, (old, new) in enumerate(pairs):
        tmp = os.path.join(os.path.dirname(old), f".burrow-rename-{os.getpid()}-{i}")
        try:
            os.rename(old, tmp)
            staged.append((old, tmp, new))
        except OSError as ex:
            errors.append(f"{os.path.basename(old)}: {ex.strerror or ex}")
    for old, tmp, new in staged:
        try:
            os.rename(tmp, new)
            tags.store().renamed(old, new)
        except OSError as ex:
            errors.append(f"{os.path.basename(old)} → {os.path.basename(new)}: {ex.strerror or ex}")
            try:
                os.rename(tmp, old)
            except OSError:
                pass
    return errors


class BulkRenameDialog(QDialog):
    def __init__(self, config, paths: list[str], parent=None):
        super().__init__(parent)
        self.config = config
        self.paths = sorted(paths, key=lambda p: natural_key(os.path.basename(p)))
        self.setWindowTitle(f"Rename {len(self.paths)} items")
        self.resize(760, 560)
        last = config.get("rename_recent") or {}
        lay = QVBoxLayout(self)
        g = QGridLayout()
        g.setHorizontalSpacing(8)
        self.find = QLineEdit(last.get("find", ""))
        self.find.setPlaceholderText("Text to find in the names")
        self.replace = QLineEdit(last.get("replace", ""))
        self.replace.setPlaceholderText("Replace with (empty removes it)")
        self.regex = QCheckBox("Regular expression")
        self.regex.setChecked(bool(last.get("regex")))
        self.match_case = QCheckBox("Match case")
        self.match_case.setChecked(bool(last.get("match_case")))
        self.pattern = QLineEdit(last.get("pattern", "{name}"))
        self.pattern.setToolTip("{name} the name (after Find), {n} a counter, {date} modified date, "
                                "{parent} the folder's name. Example: Holiday {n} or {date} {name}")
        self.start = QSpinBox()
        self.start.setRange(0, 999999)
        self.start.setValue(int(last.get("start", 1)))
        self.step = QSpinBox()
        self.step.setRange(1, 1000)
        self.step.setValue(int(last.get("step", 1)))
        self.digits = QComboBox()
        self.digits.addItems(["Auto", "1", "2", "3", "4", "5"])
        self.digits.setCurrentIndex(int(last.get("digits", 0)))
        self.case = QComboBox()
        self.case.addItems(CASES)
        self.case.setCurrentIndex(int(last.get("case", 0)))
        self.ext_case = QComboBox()
        self.ext_case.addItems(EXT_CASES)
        self.ext_case.setCurrentIndex(int(last.get("ext_case", 0)))
        g.addWidget(QLabel("Find"), 0, 0)
        g.addWidget(self.find, 0, 1, 1, 3)
        g.addWidget(self.regex, 0, 4)
        g.addWidget(QLabel("Replace"), 1, 0)
        g.addWidget(self.replace, 1, 1, 1, 3)
        g.addWidget(self.match_case, 1, 4)
        g.addWidget(QLabel("New name"), 2, 0)
        g.addWidget(self.pattern, 2, 1, 1, 3)
        hint = QLabel("{name}  {n}  {date}  {parent}")
        hint.setStyleSheet(f"color: {theme.colors()['dim']};")
        g.addWidget(hint, 2, 4)
        g.addWidget(QLabel("Counter"), 3, 0)
        row = QGridLayout()
        for i, (lbl, w) in enumerate((("from", self.start), ("step", self.step), ("digits", self.digits))):
            row.addWidget(QLabel(lbl), 0, i * 2)
            row.addWidget(w, 0, i * 2 + 1)
        g.addLayout(row, 3, 1, 1, 3)
        g.addWidget(QLabel("Case"), 4, 0)
        g.addWidget(self.case, 4, 1)
        g.addWidget(QLabel("Extension"), 4, 2, Qt.AlignmentFlag.AlignRight)
        g.addWidget(self.ext_case, 4, 3)
        g.setColumnStretch(1, 1)
        g.setColumnStretch(3, 1)
        lay.addLayout(g)

        self.preview = QTreeWidget()
        self.preview.setHeaderLabels(["Now", "After"])
        self.preview.setRootIsDecorated(False)
        self.preview.setUniformRowHeights(True)
        self.preview.header().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self.preview, 1)
        self.status = QLabel()
        lay.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.ok = self.buttons.addButton("Rename", QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.accepted.connect(self._apply)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        for w in (self.find, self.replace, self.pattern):
            w.textChanged.connect(self._update)
        for w in (self.regex, self.match_case):
            w.toggled.connect(self._update)
        for w in (self.start, self.step):
            w.valueChanged.connect(self._update)
        for w in (self.digits, self.case, self.ext_case):
            w.currentIndexChanged.connect(self._update)
        self.names = []
        self._update()
        self.pattern.setFocus()
        self.pattern.selectAll()

    def _args(self):
        return (self.find.text(), self.replace.text(), self.regex.isChecked(), self.match_case.isChecked(),
                self.pattern.text(), self.start.value(), self.step.value(), self.digits.currentIndex(),
                self.case.currentIndex(), self.ext_case.currentIndex())

    def _update(self, *_):
        try:
            self.names = new_names(self.paths, *self._args())
        except re.error as ex:
            self.status.setText(f"The expression has a mistake: {ex}")
            self.ok.setEnabled(False)
            return
        bad = problems(self.paths, self.names)
        c = theme.colors()
        dim, red = QColor(c["dim"]), QColor("#e5484d")
        self.preview.setUpdatesEnabled(False)
        self.preview.clear()
        changed = 0
        for i, (p, n) in enumerate(zip(self.paths, self.names)):
            old = os.path.basename(p)
            it = QTreeWidgetItem(self.preview, [old, n])
            if i in bad:
                it.setForeground(1, red)
                it.setToolTip(1, bad[i])
            elif n == old:
                it.setForeground(1, dim)
            else:
                changed += 1
        self.preview.setUpdatesEnabled(True)
        if bad:
            self.status.setText(f"{len(bad)} name(s) can't be used (red): {next(iter(bad.values()))}")
        else:
            self.status.setText(f"{changed} of {len(self.paths)} will be renamed")
        self.ok.setEnabled(not bad and changed > 0)

    def _apply(self):
        f, r, rx, mc, pat, st, sp, dg, cs, ec = self._args()
        self.config.set("rename_recent", {"find": f, "replace": r, "regex": rx, "match_case": mc, "pattern": pat,
                                          "start": st, "step": sp, "digits": dg, "case": cs, "ext_case": ec})
        pairs = [(p, os.path.join(os.path.dirname(p), n)) for p, n in zip(self.paths, self.names)
                 if os.path.basename(p) != n]
        errors = rename_all(pairs)
        if errors:
            QMessageBox.warning(self, "Rename", f"{len(errors)} item(s) couldn't be renamed:\n\n" + "\n".join(errors[:12]))
        self.renamed = pairs
        self.accept()
