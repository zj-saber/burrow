"""Window chrome: a tab bar + stack (tabs above the toolbar) and a breadcrumb address bar."""
from __future__ import annotations

import os

from PySide6.QtCore import QMimeData, QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QDrag, QMouseEvent
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QMenu, QStackedWidget, QTabBar,
                               QToolButton, QWidget)

from .model import DRIVES, NETWORK


TAB_MIME = "application/x-burrow-tab"


class SnapTabBar(QTabBar):
    """A tab bar whose tabs can be dragged out (downwards) to snap them into a split pane.
    Sideways drags still reorder tabs as usual."""
    dragging = None            # (TabHost, index) while a tab is being dragged out

    def __init__(self, host):
        super().__init__()
        self.host = host
        self._press = None

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            i = self.tabAt(ev.position().toPoint())
            self._press = (ev.position().toPoint(), i) if i >= 0 else None
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._press and ev.buttons() & Qt.MouseButton.LeftButton:
            start, index = self._press
            if abs(ev.position().y() - start.y()) > 28:          # pulled out of the bar
                self._press = None
                # end the bar's own reorder drag first
                super().mouseReleaseEvent(QMouseEvent(ev.Type.MouseButtonRelease, start.toPointF(),
                                                      ev.globalPosition(), Qt.MouseButton.LeftButton,
                                                      Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
                drag = QDrag(self)
                md = QMimeData()
                md.setData(TAB_MIME, str(index).encode())
                drag.setMimeData(md)
                drag.setPixmap(self.grab(self.tabRect(index)))
                SnapTabBar.dragging = (self.host, index)
                try:
                    drag.exec(Qt.DropAction.MoveAction)
                finally:
                    SnapTabBar.dragging = None
                return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        self._press = None
        super().mouseReleaseEvent(ev)


class TabHost(QObject):
    """The subset of QTabWidget's API the window uses, built from a free-standing QTabBar and a
    QStackedWidget so the tab strip can sit above the navigation bar, like a browser."""
    currentChanged = Signal(int)
    tabCloseRequested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        b = self.bar = SnapTabBar(self)
        self.pane = None
        b.setMovable(True)
        self.close_icon = None
        b.setDocumentMode(True)
        b.setExpanding(False)
        b.setDrawBase(False)
        b.setUsesScrollButtons(True)
        b.setElideMode(Qt.TextElideMode.ElideRight)
        self.stack = QStackedWidget()
        self._w: list[QWidget] = []
        b.currentChanged.connect(self._on_current)
        b.tabMoved.connect(self._moved)

    def _close_button(self, w):
        btn = QToolButton()
        btn.setObjectName("tabclose")
        btn.setAutoRaise(True)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setFixedSize(18, 18)
        btn.setToolTip("Close tab (Ctrl+W)")
        if self.close_icon is not None and not self.close_icon.isNull():
            btn.setIcon(self.close_icon)
        else:
            btn.setText("×")
        btn.clicked.connect(lambda: self.tabCloseRequested.emit(self.indexOf(w)))
        return btn

    def _sync_close_buttons(self):
        many = len(self._w) > 1
        for i in range(self.bar.count()):
            btn = self.bar.tabButton(i, QTabBar.ButtonPosition.RightSide)
            if btn is not None:
                btn.setVisible(many)

    def set_close_icon(self, icon):
        self.close_icon = icon
        for i in range(self.bar.count()):
            btn = self.bar.tabButton(i, QTabBar.ButtonPosition.RightSide)
            if btn is not None:
                btn.setIcon(icon)
                btn.setText("")

    def count(self):
        return len(self._w)

    def widget(self, i):
        return self._w[i] if 0 <= i < len(self._w) else None

    def indexOf(self, w):
        return self._w.index(w) if w in self._w else -1

    def insertTab(self, i, w, text):
        i = max(0, min(i, len(self._w)))
        self._w.insert(i, w)
        self.stack.addWidget(w)
        self.bar.insertTab(i, text)
        self.bar.setTabButton(i, QTabBar.ButtonPosition.RightSide, self._close_button(w))
        self._sync_close_buttons()
        return i

    def removeTab(self, i):
        w = self._w.pop(i)
        self.bar.removeTab(i)
        self.stack.removeWidget(w)
        self._sync_close_buttons()

    def currentIndex(self):
        return self.bar.currentIndex()

    def currentWidget(self):
        return self.widget(self.bar.currentIndex())

    def setCurrentIndex(self, i):
        self.bar.setCurrentIndex(i)

    def setCurrentWidget(self, w):
        self.setCurrentIndex(self.indexOf(w))

    def setTabText(self, i, text):
        self.bar.setTabText(i, text)

    def setTabToolTip(self, i, tip):
        self.bar.setTabToolTip(i, tip)

    def tabBar(self):
        return self.bar

    def _on_current(self, i):
        w = self.widget(i)
        if w is not None:
            self.stack.setCurrentWidget(w)
        self.currentChanged.emit(i)

    def _moved(self, frm, to):
        self._w.insert(to, self._w.pop(frm))


def path_segments(path: str) -> list[tuple[str, str]]:
    if path.startswith("::tag:"):
        from .folderview import display_path
        return [(display_path(path), path)]
    if path == NETWORK:
        return [("Network", NETWORK)]
    if path.startswith("\\\\"):
        # \\server\share\rest  ->  Network › server › share › ...
        parts = [p for p in path[2:].split("\\") if p]
        segs = [("Network", NETWORK)]
        if parts:
            server = "\\\\" + parts[0]
            segs.append((parts[0], server))
            cur = server
            for part in parts[1:]:
                cur = cur + "\\" + part
                segs.append((part, cur))
        return segs
    segs = [("This PC", DRIVES)]
    if path == DRIVES:
        return segs
    drive, rest = os.path.splitdrive(path)
    segs.append((drive, drive + "\\"))
    cur = drive + "\\"
    for part in [p for p in rest.split("\\") if p]:
        cur = os.path.join(cur, part)
        segs.append((part, cur))
    return segs


class CrumbButton(QToolButton):
    middleClicked = Signal()

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.MouseButton.MiddleButton:
            self.middleClicked.emit()
            return
        super().mouseReleaseEvent(ev)


class Breadcrumb(QWidget):
    navigate = Signal(str, bool)     # path, in new tab?
    editRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("crumbs")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.IBeamCursor)
        self.h = QHBoxLayout(self)
        self.h.setContentsMargins(4, 0, 4, 0)
        self.h.setSpacing(0)
        self._items: list[tuple[QWidget, QWidget | None, str, str]] = []   # (button, separator, label, path)
        self.more = QToolButton()
        self.more.setText("…")
        self.more.setCursor(Qt.CursorShape.ArrowCursor)
        self.more.clicked.connect(self._show_hidden_menu)
        self.h.addWidget(self.more)
        self.more.hide()
        self._path = None

    def set_path(self, path: str):
        if path == self._path:
            return
        self._path = path
        for btn, sep, _, _ in self._items:
            for wdg in (btn, sep):
                if wdg is not None:
                    wdg.hide()              # deleteLater is deferred; hide now so nothing overlaps
                    wdg.deleteLater()
        self._items.clear()
        while self.h.count() > 1:                      # keep the "…" button at index 0
            item = self.h.takeAt(1)
            del item
        for i, (label, p) in enumerate(path_segments(path)):
            sep = None
            if i:
                sep = QLabel("›")
                sep.setContentsMargins(2, 0, 2, 0)
                self.h.addWidget(sep)
            btn = CrumbButton()
            btn.setText(label)
            btn.setCursor(Qt.CursorShape.ArrowCursor)
            btn.setToolTip({DRIVES: "This PC", NETWORK: "Network"}.get(p, p))
            btn.clicked.connect(lambda _=False, p=p: self.navigate.emit(p, False))
            btn.middleClicked.connect(lambda p=p: self.navigate.emit(p, True))
            self.h.addWidget(btn)
            self._items.append((btn, sep, label, p))
        self.h.addStretch(1)
        QTimer.singleShot(0, self._fit)

    def _fit(self):
        if not self._items:
            return
        avail = self.width() - 12 - self.more.sizeHint().width()
        used = 0
        first_visible = 0
        for i in range(len(self._items) - 1, -1, -1):
            btn, sep, _, _ = self._items[i]
            w = btn.sizeHint().width() + (sep.sizeHint().width() if sep else 0)
            if used + w > avail and i < len(self._items) - 1:
                first_visible = i + 1
                break
            used += w
        for i, (btn, sep, _, _) in enumerate(self._items):
            vis = i >= first_visible
            btn.setVisible(vis)
            if sep is not None:
                sep.setVisible(vis and i > first_visible)
        self.more.setVisible(first_visible > 0)
        self._first_visible = first_visible

    def _show_hidden_menu(self):
        m = QMenu(self)
        for btn, _, label, p in self._items[:getattr(self, "_first_visible", 0)]:
            m.addAction(label).triggered.connect(lambda _=False, p=p: self.navigate.emit(p, False))
        m.exec(self.more.mapToGlobal(self.more.rect().bottomLeft()))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._fit()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self.editRequested.emit()
            return
        super().mousePressEvent(ev)


class AddressBar(QLineEdit):
    escaped = Signal()
    focusLost = Signal()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key.Key_Escape:
            self.escaped.emit()
            return
        super().keyPressEvent(ev)

    def focusInEvent(self, ev):
        super().focusInEvent(ev)
        QTimer.singleShot(0, self.selectAll)

    def focusOutEvent(self, ev):
        super().focusOutEvent(ev)
        if ev.reason() != Qt.FocusReason.PopupFocusReason:
            self.focusLost.emit()


class AddressArea(QStackedWidget):
    """Breadcrumbs normally; a text field while editing (click, Ctrl+L, Alt+D or F4)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.crumbs = Breadcrumb()
        self.edit = AddressBar()
        self.edit.setPlaceholderText("Type a path, %USERPROFILE%, or This PC")
        self.addWidget(self.crumbs)
        self.addWidget(self.edit)
        self.setFixedHeight(30)
        self.crumbs.editRequested.connect(self.start_edit)
        self.edit.escaped.connect(self.stop_edit)
        self.edit.focusLost.connect(self.stop_edit)

    def set_path(self, path: str, text: str):
        self.crumbs.set_path(path)
        self.edit.setText(text)

    def start_edit(self):
        self.setCurrentWidget(self.edit)
        self.edit.setFocus()
        self.edit.selectAll()

    def stop_edit(self):
        if self.currentWidget() is self.edit:
            self.setCurrentWidget(self.crumbs)


class UsagePill(QWidget):
    """Status bar: how full the current drive is, as a skinny pill."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.used = None
        self.on = True                 # Settings switch
        self.setFixedSize(64, 14)

    def set_usage(self, total, free):
        self.used = (1 - free / total) if total and free is not None else None
        self.setVisible(self.used is not None and self.on)
        self.update()

    def paintEvent(self, _ev):
        if self.used is None:
            return
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QPainter
        from . import theme
        p = QPainter(self)
        h = 4
        theme.paint_usage(p, QRectF(2, (self.height() - h) / 2, self.width() - 4, h), self.used)
        p.end()
