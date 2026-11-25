"""Left sidebar: bookmarks, common places, and drives. Accepts drops."""
from __future__ import annotations

import os

from PySide6.QtCore import QStandardPaths, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QFrame, QInputDialog, QMenu, QTreeWidget, QTreeWidgetItem

from . import fileops, places, theme
from .config import Config
from .model import DRIVES, NETWORK, icon_cache, list_drives

PATH_ROLE = Qt.ItemDataRole.UserRole
KIND_ROLE = Qt.ItemDataRole.UserRole + 1
INDEX_ROLE = Qt.ItemDataRole.UserRole + 2
SECTION_ROLE = Qt.ItemDataRole.UserRole + 3   # stable id of a section, for remembering collapse

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
    mapDriveRequested = Signal()
    disconnectRequested = Signal(str)          # drive letter

    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setHeaderHidden(True)
        self.setRootIsDecorated(True)          # arrows on section headings
        self.setItemsExpandable(True)
        self.setExpandsOnDoubleClick(False)    # single click toggles plain headings instead
        self.setAnimated(False)
        self.setIndentation(14)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMouseTracking(True)
        self.setMinimumWidth(170)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropMode(QTreeWidget.DragDropMode.DropOnly)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.itemClicked.connect(self._clicked)
        self.itemExpanded.connect(self._remember_collapsed)
        self.itemCollapsed.connect(self._remember_collapsed)
        self._building = False
        config.bookmarksChanged.connect(self.rebuild)
        self._drive_sig = None
        self._current = None
        self._timer = QTimer(self)
        self._timer.setInterval(4000)
        self._timer.timeout.connect(self._check_drives)
        self._timer.start()
        self.rebuild()

    def _section(self, title, path=None, icon=None, key=None):
        it = QTreeWidgetItem(self, [title if path else title.upper()])
        f = it.font(0)
        if path:
            f.setBold(True)
        else:
            f.setPointSizeF(max(7.0, f.pointSizeF() - 1.0))
            f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 108)
            it.setForeground(0, QColor(theme.colors()["dim"]))
        it.setFont(0, f)
        it.setData(0, KIND_ROLE, "section")
        it.setData(0, SECTION_ROLE, key)
        it.setData(0, PATH_ROLE, path)
        flags = Qt.ItemFlag.ItemIsEnabled
        if path:
            flags |= Qt.ItemFlag.ItemIsSelectable
            it.setIcon(0, icon or icon_cache().computer)
        it.setFlags(flags)
        return it

    def _item(self, parent, text, path, kind, icon, tip=None):
        it = QTreeWidgetItem(parent, [text])
        it.setData(0, PATH_ROLE, path)
        it.setData(0, KIND_ROLE, kind)
        it.setIcon(0, icon)
        it.setToolTip(0, tip or path)
        return it

    def _signature(self):
        """Cheap snapshot of what the sidebar shows (no network I/O), polled every 4 s."""
        return ([(d.path, d.name, d.note) for d in list_drives()], list(places.RECENT_SERVERS))

    def rebuild(self):
        icons = icon_cache()
        self.clear()
        self._building = True
        bm = self._section("Bookmarks", key="bookmarks")
        bm.setData(0, KIND_ROLE, "bookmarks")
        for i, b in enumerate(self.config.bookmarks()):
            it = self._item(bm, b["name"], b["path"], "bookmark", icons.folder_icon(b["path"]))
            it.setData(0, INDEX_ROLE, i)
            # don't probe network bookmarks here: an offline server would freeze the sidebar
            if not places.is_remote(b["path"]) and not places.is_special(b["path"]) \
                    and not os.path.isdir(b["path"]):
                it.setForeground(0, Qt.GlobalColor.gray)
                it.setToolTip(0, f"{b['path']} (not available)")
        if not self.config.bookmarks():
            hint = QTreeWidgetItem(bm, ["Ctrl+D bookmarks a folder"])
            hint.setFlags(Qt.ItemFlag.NoItemFlags)
        sect = self._section("Places", key="places")
        for label, loc in PLACES:
            p = QStandardPaths.writableLocation(loc)
            if p and os.path.isdir(p):
                self._item(sect, label, os.path.normpath(p), "place", icons.folder_icon(p))

        entries = list_drives()
        self._sig = ([(d.path, d.name, d.note) for d in entries], list(places.RECENT_SERVERS))
        cloud = [d for d in entries if d.kind == "cloud"]
        local = [d for d in entries if d.kind not in ("cloud", "network")]
        network = [d for d in entries if d.kind == "network"]
        if cloud:
            sect = self._section("Cloud", key="cloud")
            for d in cloud:
                self._item(sect, d.name, d.path, "cloud", icons.icon(d), f"{d.name}  —  {d.path}")
        pc = self._section("This PC", DRIVES, key="pc")
        for d in local:
            self._item(pc, d.name, d.path, "drive", icons.icon(d), f"{d.path}  —  {d.note}")
        net = self._section("Network", NETWORK, icons.network, key="network")
        net.setData(0, KIND_ROLE, "network-root")
        for d in network:
            it = self._item(net, d.name, d.path, "netdrive", icons.network, f"{d.path}  —  {d.note}")
            if d.note.startswith("Disconnected"):
                it.setForeground(0, QColor(theme.colors()["dim"]))
        for s in places.RECENT_SERVERS:
            self._item(net, s[2:], s, "server", icons.network, s)
        collapsed = set(self.config.get("sidebar_collapsed"))
        for i in range(self.topLevelItemCount()):
            top = self.topLevelItem(i)
            top.setExpanded(top.data(0, SECTION_ROLE) not in collapsed)
        self._building = False
        self.highlight(self._current)

    def apply_theme(self, c: dict):
        self.setStyleSheet(theme.sidebar_qss(c))
        self.rebuild()

    def _check_drives(self):
        if self._signature() != getattr(self, "_sig", None):
            self.rebuild()

    def highlight(self, path):
        self._current = path
        target = None
        if path:
            special = places.is_special(path)
            key = path if special else os.path.normcase(path)
            for i in range(self.topLevelItemCount()):
                top = self.topLevelItem(i)
                for it in [top] + [top.child(j) for j in range(top.childCount())]:
                    p = it.data(0, PATH_ROLE)
                    if p and (p == key if special else os.path.normcase(p) == key):
                        target = it
                        break
                if target:
                    break
        if target is not None and target.parent() is not None and not target.parent().isExpanded():
            target = None          # selecting it would make Qt re-open the collapsed section
        self.blockSignals(True)
        self.setCurrentItem(target) if target else self.clearSelection()
        self.blockSignals(False)

    def _remember_collapsed(self, _item=None):
        if self._building:
            return
        collapsed = [self.topLevelItem(i).data(0, SECTION_ROLE) for i in range(self.topLevelItemCount())
                     if not self.topLevelItem(i).isExpanded() and self.topLevelItem(i).data(0, SECTION_ROLE)]
        if collapsed != self.config.get("sidebar_collapsed"):
            self.config.set("sidebar_collapsed", collapsed)

    def _clicked(self, item, _col):
        if item.parent() is None and not item.data(0, PATH_ROLE):
            item.setExpanded(not item.isExpanded())   # plain headings (Bookmarks, Places, Cloud) toggle
            return
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
        elif not places.is_special(path) and not self.config.is_bookmarked(path):
            m.addSeparator()
            m.addAction("Add bookmark").triggered.connect(lambda: self.config.add_bookmark(path))
        if kind in ("network-root", "netdrive", "server"):
            m.addSeparator()
            m.addAction("Map network drive…").triggered.connect(self.mapDriveRequested)
            if kind == "netdrive":
                letter = path[:2]
                m.addAction(f"Disconnect {letter}").triggered.connect(
                    lambda: self.disconnectRequested.emit(letter))
            if kind == "server":
                m.addAction("Forget this computer").triggered.connect(lambda: self._forget(path))
        m.exec(self.viewport().mapToGlobal(pos))

    def _forget(self, server):
        if server in places.RECENT_SERVERS:
            places.RECENT_SERVERS.remove(server)
            self.config.set("recent_servers", list(places.RECENT_SERVERS))
            self.rebuild()

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
        if not target or places.is_special(target) or places.is_server(target) or any(fileops.same_path(p, target) for p in paths):
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
