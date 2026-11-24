"""Left sidebar: bookmarks, common places, and drives. Accepts drops."""
from __future__ import annotations

import os

from PySide6.QtCore import QStandardPaths, Qt, QTimer, Signal
from PySide6.QtWidgets import QInputDialog, QMenu, QTreeWidget, QTreeWidgetItem

from . import fileops
from .config import Config
from .model import DRIVES, icon_cache, list_drives

PATH_ROLE = Qt.ItemDataRole.UserRole
KIND_ROLE = Qt.ItemDataRole.UserRole + 1
INDEX_ROLE = Qt.ItemDataRole.UserRole + 2

PLACES = [
    ("Home", QStandardPaths.StandardLocation.HomeLocation),
    ("Desktop", QStandardPaths.StandardLocation.DesktopLocation),
    ("Documents", QStandardPaths.StandardLocation.DocumentsLocation),
    ("Downloads", QStandardPaths.StandardLocation.DownloadLocation),
    ("Pictures", QStandardPaths.StandardLocation.PicturesLocation),
    ("Music", QStandardPaths.StandardLocation.MusicLocation),
    ("Videos", QStandardPaths.StandardLocation.MoviesLocation),
]


class Sidebar(QTreeWidget):
    openRequested = Signal(str, bool)          # path, in new tab?
    dropRequested = Signal(list, str, bool)    # sources, target, move?

    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setHeaderHidden(True)
        self.setRootIsDecorated(False)
        self.setItemsExpandable(False)
        self.setIndentation(12)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropMode(QTreeWidget.DragDropMode.DropOnly)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.itemClicked.connect(self._clicked)
        config.bookmarksChanged.connect(self.rebuild)
        self._drive_sig = None
        self._current = None
        self._timer = QTimer(self)
        self._timer.setInterval(4000)
        self._timer.timeout.connect(self._check_drives)
        self._timer.start()
        self.rebuild()

    def _section(self, title, path=None):
        it = QTreeWidgetItem(self, [title])
        f = it.font(0)
        f.setBold(True)
        it.setFont(0, f)
        it.setData(0, KIND_ROLE, "section")
        it.setData(0, PATH_ROLE, path)
        flags = Qt.ItemFlag.ItemIsEnabled
        if path:
            flags |= Qt.ItemFlag.ItemIsSelectable
            it.setIcon(0, icon_cache().computer)
        it.setFlags(flags)
        return it

    def _item(self, parent, text, path, kind, icon, tip=None):
        it = QTreeWidgetItem(parent, [text])
        it.setData(0, PATH_ROLE, path)
        it.setData(0, KIND_ROLE, kind)
        it.setIcon(0, icon)
        it.setToolTip(0, tip or path)
        return it

    def rebuild(self):
        icons = icon_cache()
        self.clear()
        bm = self._section("Bookmarks")
        bm.setData(0, KIND_ROLE, "bookmarks")
        for i, b in enumerate(self.config.bookmarks()):
            it = self._item(bm, b["name"], b["path"], "bookmark", icons.folder)
            it.setData(0, INDEX_ROLE, i)
            if not os.path.isdir(b["path"]):
                it.setForeground(0, Qt.GlobalColor.gray)
                it.setToolTip(0, f"{b['path']} (not available)")
        if not self.config.bookmarks():
            hint = QTreeWidgetItem(bm, ["Ctrl+D bookmarks a folder"])
            hint.setFlags(Qt.ItemFlag.NoItemFlags)
        places = self._section("Places")
        for label, loc in PLACES:
            p = QStandardPaths.writableLocation(loc)
            if p and os.path.isdir(p):
                self._item(places, label, os.path.normpath(p), "place", icons.folder)
        pc = self._section("This PC", DRIVES)
        drives = list_drives()
        self._drive_sig = [(d.path, d.name) for d in drives]
        for d in drives:
            self._item(pc, d.name, d.path, "drive", icons.icon(d), f"{d.path}  —  {d.note}")
        self.expandAll()
        self.highlight(self._current)

    def _check_drives(self):
        sig = [(d.path, d.name) for d in list_drives()]
        if sig != self._drive_sig:
            self.rebuild()

    def highlight(self, path):
        self._current = path
        target = None
        if path:
            key = path if path == DRIVES else os.path.normcase(path)
            for i in range(self.topLevelItemCount()):
                top = self.topLevelItem(i)
                for it in [top] + [top.child(j) for j in range(top.childCount())]:
                    p = it.data(0, PATH_ROLE)
                    if p and (p == key if path == DRIVES else os.path.normcase(p) == key):
                        target = it
                        break
                if target:
                    break
        self.blockSignals(True)
        self.setCurrentItem(target) if target else self.clearSelection()
        self.blockSignals(False)

    def _clicked(self, item, _col):
        p = item.data(0, PATH_ROLE)
        if p:
            self.openRequested.emit(p, False)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.MiddleButton:
            it = self.itemAt(ev.position().toPoint())
            if it and it.data(0, PATH_ROLE):
                self.openRequested.emit(it.data(0, PATH_ROLE), True)
            return
        super().mousePressEvent(ev)

    def _menu(self, pos):
        it = self.itemAt(pos)
        if not it or not it.data(0, PATH_ROLE):
            return
        path = it.data(0, PATH_ROLE)
        kind = it.data(0, KIND_ROLE)
        m = QMenu(self)
        m.addAction("Open").triggered.connect(lambda: self.openRequested.emit(path, False))
        m.addAction("Open in new tab").triggered.connect(lambda: self.openRequested.emit(path, True))
        if kind == "bookmark":
            idx = it.data(0, INDEX_ROLE)
            m.addSeparator()
            m.addAction("Rename bookmark…").triggered.connect(lambda: self._rename(idx, it.text(0)))
            up = m.addAction("Move up")
            up.setEnabled(idx > 0)
            up.triggered.connect(lambda: self.config.move_bookmark(idx, -1))
            down = m.addAction("Move down")
            down.setEnabled(idx < len(self.config.bookmarks()) - 1)
            down.triggered.connect(lambda: self.config.move_bookmark(idx, 1))
            m.addAction("Remove bookmark").triggered.connect(lambda: self.config.remove_bookmark(path))
        elif path != DRIVES and not self.config.is_bookmarked(path):
            m.addSeparator()
            m.addAction("Add bookmark").triggered.connect(lambda: self.config.add_bookmark(path))
        m.exec(self.viewport().mapToGlobal(pos))

    def _rename(self, idx, old):
        name, ok = QInputDialog.getText(self, "Rename bookmark", "Name:", text=old)
        if ok and name.strip():
            self.config.rename_bookmark(idx, name.strip())

    # ----- drops: onto a place/drive transfers, onto "Bookmarks" adds bookmarks ------
    @staticmethod
    def _paths(md):
        if not md.hasUrls():
            return []
        return [os.path.normpath(u.toLocalFile()) for u in md.urls() if u.isLocalFile()]

    def _drop_info(self, ev):
        paths = self._paths(ev.mimeData())
        it = self.itemAt(ev.position().toPoint())
        if not paths or not it:
            return None
        kind = it.data(0, KIND_ROLE)
        if kind == "bookmarks" or (it.parent() and it.parent().data(0, KIND_ROLE) == "bookmarks"
                                   and kind != "bookmark"):
            dirs = [p for p in paths if os.path.isdir(p)]
            return ("bookmark", dirs, None) if dirs else None
        target = it.data(0, PATH_ROLE)
        if not target or target == DRIVES or any(fileops.same_path(p, target) for p in paths):
            return None
        return ("transfer", paths, target)

    def dragEnterEvent(self, ev):
        if self._paths(ev.mimeData()):
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dragMoveEvent(self, ev):
        info = self._drop_info(ev)
        if not info:
            ev.ignore()
            return
        if info[0] == "bookmark":
            ev.setDropAction(Qt.DropAction.LinkAction)
        else:
            move = fileops.decide_move(ev.modifiers(), info[1], info[2])
            ev.setDropAction(Qt.DropAction.MoveAction if move else Qt.DropAction.CopyAction)
        ev.accept()

    def dropEvent(self, ev):
        info = self._drop_info(ev)
        if not info:
            ev.ignore()
            return
        kind, paths, target = info
        if kind == "bookmark":
            ev.setDropAction(Qt.DropAction.LinkAction)
            ev.accept()
            for p in paths:
                self.config.add_bookmark(p)
            return
        move = fileops.decide_move(ev.modifiers(), paths, target)
        ev.setDropAction(Qt.DropAction.CopyAction)
        ev.accept()
        QTimer.singleShot(0, lambda: self.dropRequested.emit(paths, target, move))
