"""Small pieces shared by the tools."""
from __future__ import annotations

import fnmatch
import os
import threading

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLineEdit, QToolButton, QWidget


def parse_patterns(text: str) -> list[str]:
    return [p.strip().lower() for p in text.replace(",", ";").split(";") if p.strip()]


def excluded(name: str, patterns: list[str]) -> bool:
    n = name.lower()
    return any(fnmatch.fnmatchcase(n, p) for p in patterns)


class FolderField(QWidget):
    """A path box with a Browse button."""

    def __init__(self, text: str = "", placeholder: str = "", parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.edit = QLineEdit(text)
        self.edit.setPlaceholderText(placeholder)
        self.edit.setClearButtonEnabled(True)
        b = QToolButton()
        b.setText("…")
        b.setToolTip("Browse")
        b.clicked.connect(lambda: self._browse())
        lay.addWidget(self.edit, 1)
        lay.addWidget(b)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Choose a folder", self.text() or os.path.expanduser("~"))
        if d:
            self.edit.setText(os.path.normpath(d))

    def text(self) -> str:
        t = self.edit.text().strip().strip('"')
        return os.path.normpath(t) if t else ""

    def setText(self, t: str):
        self.edit.setText(t)


class _Relay(QObject):
    """Lives on the GUI thread; its @Slot runs callbacks there. (In PySide a plain callable
    connected to a signal emitted from another thread runs on that other thread.)"""
    sig = Signal(object, object)

    def __init__(self):
        super().__init__()
        self.sig.connect(self._call)

    @Slot(object, object)
    def _call(self, fn, value):
        try:
            fn(value)
        except RuntimeError:                 # the window was closed meanwhile
            pass


class _Channel:
    def __init__(self, relay):
        self.relay = relay
        self.slots = []

    def connect(self, fn):
        self.slots.append(fn)

    def emit(self, value):
        for fn in self.slots:
            self.relay.sig.emit(fn, value)


class Worker:
    """Runs fn(worker) on a daemon thread. fn reports with worker.progress.emit(...) and
    checks worker.cancelled; its return value arrives through done, on the GUI thread."""

    def __init__(self, fn, parent=None):
        self.fn = fn
        self.cancelled = False
        self.relay = _Relay()
        self.progress = _Channel(self.relay)
        self.done = _Channel(self.relay)
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        try:
            res = self.fn(self)
        except Exception as ex:              # noqa: BLE001 - shown to the user
            res = ex
        self.done.emit(res)

    def start(self):
        self._thread.start()

    def cancel(self):
        self.cancelled = True

    def wait(self):
        self.cancelled = True
        self._thread.join(5)
