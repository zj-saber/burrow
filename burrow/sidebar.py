"""Left sidebar: bookmarks, common places, and drives. Accepts drops."""
from __future__ import annotations

import os

from PySide6.QtCore import QMimeData, QStandardPaths, Qt, QTimer, Signal
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QDrag, QFont, QPainter, QPalette, QPen
from PySide6.QtWidgets import (QApplication, QFrame, QInputDialog, QMenu, QStyle, QStyledItemDelegate,
                               QStyleOptionViewItem, QTreeWidget, QTreeWidgetItem)

from . import fileops, places, tags, theme
from .config import Config
from .model import DRIVES, NETWORK, icon_cache, list_drives

PATH_ROLE = Qt.ItemDataRole.UserRole
KIND_ROLE = Qt.ItemDataRole.UserRole + 1
INDEX_ROLE = Qt.ItemDataRole.UserRole + 2
SECTION_ROLE = Qt.ItemDataRole.UserRole + 3   # stable id of a section, for remembering collapse
EJECT_ROLE = Qt.ItemDataRole.UserRole + 4     # drive rows with an eject button
SECTION_MIME = "application/x-burrow-sidebar-section"
EJECT_W = 22                                  # the eject button at a row's right end
DEFAULT_ORDER = ["bookmarks", "places", "cloud", "tags", "pc", "network"]

PLACES = [
    ("Home", QStandardPaths.StandardLocation.HomeLocation),
    ("Desktop", QStandardPaths.StandardLocation.DesktopLocation),
    ("Documents", QStandardPaths.StandardLocation.DocumentsLocation),
    ("Downloads", QStandardPaths.StandardLocation.DownloadLocation),
    ("Pictures", QStandardPaths.StandardLocation.PicturesLocation),
    ("Music", QStandardPaths.StandardLocation.MusicLocation),
    ("Videos", QStandardPaths.StandardLocation.MoviesLocation),
]


class _SidebarDelegate(QStyledItemDelegate):
    """Rounded hover/selection fill drawn inside the item's own rect, so nothing bleeds into
    the indentation strip. Cheap: the sidebar has a few dozen rows."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.select = QColor(0, 0, 0, 0)
        self.hover = QColor(0, 0, 0, 0)
        self.seltext = QColor(0, 0, 0)
        self.eject_hover = None        # the row whose eject button the mouse is on
        self.show_eject = True

    def sizeHint(self, option, index):
        s = super().sizeHint(option, index)
        s.setHeight(max(s.height(), 26))
        return s

    def paint(self, painter, option, index):
        """Rounded hover/selection fill, the row itself, then its eject mark."""
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        hovered = (bool(opt.state & QStyle.StateFlag.State_MouseOver) and bool(index.flags() & Qt.ItemFlag.ItemIsSelectable)) \
            or bool(opt.state & QStyle.StateFlag.State_HasFocus)
        if selected or hovered:
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self.select if selected else self.hover)
            painter.drawRoundedRect(QRectF(opt.rect).adjusted(0, 1, -2, -1), 6, 6)
            painter.restore()
        if selected:
            opt.palette.setColor(QPalette.ColorRole.Text, self.seltext)
            opt.palette.setColor(QPalette.ColorRole.HighlightedText, self.seltext)
        opt.state &= ~(QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver | QStyle.StateFlag.State_HasFocus)
        opt.rect = opt.rect.adjusted(4, 0, 0, 0)
        eject = self.show_eject and index.data(EJECT_ROLE)
        if eject:
            opt.rect = opt.rect.adjusted(0, 0, -EJECT_W, 0)
        w = opt.widget
        (w.style() if w else QApplication.style()).drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, w)
        if eject:
            self._paint_eject(painter, option.rect, opt.palette.color(QPalette.ColorRole.Text),
                              self.eject_hover == index)

    @staticmethod
    def _paint_eject(painter, rect, color, hot):
        """A small eject mark (triangle over a bar), faint until hovered."""
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(color)
        c.setAlpha(230 if hot else 110)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(c)
        cx, cy = rect.right() - EJECT_W / 2 - 1, rect.center().y() + 0.5
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QPolygonF
        painter.drawPolygon(QPolygonF([QPointF(cx - 4.5, cy + 0.5), QPointF(cx + 4.5, cy + 0.5), QPointF(cx, cy - 4.5)]))
        painter.drawRoundedRect(QRectF(cx - 4.5, cy + 2.2, 9, 1.8), 0.9, 0.9)
        painter.restore()


class Sidebar(QTreeWidget):
    """The left panel: bookmarks, places, cloud, tags, drives and network; accepts drops."""
    openRequested = Signal(str, bool)          # path, in new tab?
    dropRequested = Signal(list, str, bool)    # sources, target, move?
    mapDriveRequested = Signal()
    disconnectRequested = Signal(str)          # drive letter
    browseRequested = Signal(str)              # arrowing through: open, but keep focus here
    ejectRequested = Signal(str, str)          # drive root, kind (drive / netdrive)
    focusListRequested = Signal()              # → / Enter: back to the file list

    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setHeaderHidden(True)
        # No branch column: Qt's style sheet engine paints it as a separate rounded block next
        # to the highlight. Headings carry their own chevron icon instead.
        self.setRootIsDecorated(False)
        self._delegate = _SidebarDelegate(self)
        self.setItemDelegate(self._delegate)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
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
        self.itemExpanded.connect(self._section_toggled)
        self.itemCollapsed.connect(self._section_toggled)
        self._building = False
        self._press = None          # (pos, heading key) when a heading is pressed
        self._drop_y = None         # where the reorder line is drawn while dragging
        self.drag_reorder = True    # Settings switch
        self._kbd = False           # the current item moved by keyboard
        self._browse_timer = QTimer(self)
        self._browse_timer.setSingleShot(True)
        self._browse_timer.setInterval(160)   # arrowing fast doesn't load every folder
        self._browse_timer.timeout.connect(self._browse_current)
        self.currentItemChanged.connect(self._current_moved)
        config.bookmarksChanged.connect(self.rebuild)
        tags.store().changed.connect(self.rebuild)
        self._current = None
        self._timer = QTimer(self)
        # Drives arriving/leaving come from DeviceWatcher (no polling). This slow check only
        # catches the rare change Windows doesn't broadcast, and only while the sidebar shows.
        self._timer.setInterval(30000)
        self._timer.timeout.connect(self._check_drives)
        self._timer.start()
        self.rebuild()

    def _section(self, title, path=None, icon=None, key=None):
        """A section heading (plain, or a link like This PC and Network)."""
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
        """Fill every section from scratch (cheap: a few dozen rows, no network I/O)."""
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
            key = "Ctrl+D" if self.config.get("shortcut_scheme") == "classic" else "Ctrl+B"
            hint = QTreeWidgetItem(bm, [f"{key} bookmarks a folder"])
            hint.setFlags(Qt.ItemFlag.NoItemFlags)
        cfg = self.config
        sect = self._section("Places", key="places") if cfg.get("sidebar_places") else None
        for label, loc in (PLACES if sect is not None else []):
            p = QStandardPaths.writableLocation(loc)
            if p and os.path.isdir(p):
                self._item(sect, label, os.path.normpath(p), "place", icons.folder_icon(p))

        entries = list_drives()
        self._sig = ([(d.path, d.name, d.note) for d in entries], list(places.RECENT_SERVERS))
        cloud = [d for d in entries if d.kind == "cloud"]
        local = [d for d in entries if d.kind not in ("cloud", "network")]
        network = [d for d in entries if d.kind == "network"]
        if cloud and cfg.get("sidebar_cloud"):
            sect = self._section("Cloud", key="cloud")
            for d in cloud:
                self._item(sect, d.name, d.path, "cloud", icons.icon(d), f"{d.name}  —  {d.path}")
        used = [t for t in tags.TAGS if tags.store().count(t[0])]
        show_tags = cfg.get("sidebar_tags") and tags.ENABLED
        sect = self._section("Tags", key="tags") if show_tags else None
        for tid, name, color in (used if show_tags else []):
            self._item(sect, f"{name}  ·  {tags.store().count(tid)}", tags.tag_location(tid), "tag",
                       tags.color_icon(color), f"Everything tagged {name}")
        if show_tags and not used:
            hint = QTreeWidgetItem(sect, ["Right-click ▸ Tags to tag items"])
            hint.setFlags(Qt.ItemFlag.NoItemFlags)
        pc = self._section("This PC", DRIVES, key="pc")
        for d in local:
            it = self._item(pc, d.name, d.path, "drive", icons.icon(d), f"{d.path}  —  {d.note}")
            it.setData(0, EJECT_ROLE, d.ejectable)
        net = self._section("Network", NETWORK, icons.network, key="network") if cfg.get("sidebar_network") else None
        if net is not None:
            net.setData(0, KIND_ROLE, "network-root")
        for d in (network if net is not None else []):
            it = self._item(net, d.name, d.path, "netdrive", icons.network, f"{d.path}  —  {d.note}")
            if d.note.startswith("Disconnected"):
                it.setForeground(0, QColor(theme.colors()["dim"]))
            else:
                it.setData(0, EJECT_ROLE, True)
        for s in (places.RECENT_SERVERS if net is not None else []):
            self._item(net, s[2:], s, "server", icons.network, s)
        self._apply_order()
        collapsed = set(self.config.get("sidebar_collapsed"))
        for i in range(self.topLevelItemCount()):
            top = self.topLevelItem(i)
            top.setExpanded(top.data(0, SECTION_ROLE) not in collapsed)
            if top.data(0, SECTION_ROLE):
                top.setIcon(0, theme.icon("chevron_down" if top.isExpanded() else "chevron", 12))
        self._building = False
        self.highlight(self._current)

    # ----- section order (drag a heading to move the whole section) ---------------
    def order(self) -> list:
        saved = [k for k in (self.config.get("sidebar_order") or []) if k in DEFAULT_ORDER]
        return saved + [k for k in DEFAULT_ORDER if k not in saved]

    def _apply_order(self):
        rank = {k: i for i, k in enumerate(self.order())}
        tops = [self.takeTopLevelItem(0) for _ in range(self.topLevelItemCount())]
        tops.sort(key=lambda it: rank.get(it.data(0, SECTION_ROLE), 99))
        self.addTopLevelItems(tops)

    def move_section(self, key: str, before: str | None):
        """Put section `key` just above section `before` (None = at the bottom)."""
        order = [k for k in self.order() if k != key]
        order.insert(order.index(before) if before in order else len(order), key)
        self.config.set("sidebar_order", order)
        self.rebuild()

    def _drop_slot(self, y: int):
        """(section key to insert before or None, y of the line) for a pointer height."""
        shown = [self.topLevelItem(i) for i in range(self.topLevelItemCount())]
        for top in shown:
            r = self.visualItemRect(top)
            if r.isValid() and y < r.center().y():
                return top.data(0, SECTION_ROLE), r.top()
        last = shown[-1] if shown else None
        bottom = 0
        if last is not None:
            tail = last.child(last.childCount() - 1) if last.isExpanded() and last.childCount() else last
            bottom = self.visualItemRect(tail).bottom()
        return None, bottom

    def rebuild_soon(self):
        """Coalesce rebuilds asked for in one go (startup, theme + switches)."""
        if not getattr(self, "_rebuild_pending", False):
            self._rebuild_pending = True
            QTimer.singleShot(0, self._do_rebuild)

    def _do_rebuild(self):
        self._rebuild_pending = False
        self.rebuild()

    def apply_theme(self, c: dict):
        self.setStyleSheet(theme.sidebar_qss(c))
        self._delegate.select = QColor(c["select"])
        self._delegate.hover = QColor(c["chip"])
        self._delegate.seltext = QColor(c["seltext"])
        pal = self.palette()                       # the delegate paints selection; Qt must not
        for g in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
            pal.setColor(g, QPalette.ColorRole.Highlight, QColor(0, 0, 0, 0))
        self.setPalette(pal)
        self.rebuild_soon()

    def showEvent(self, ev):
        super().showEvent(ev)
        self._timer.start()

    def hideEvent(self, ev):
        super().hideEvent(ev)
        self._timer.stop()                 # hidden in the tray: no wake-ups at all

    def _check_drives(self):
        if self._signature() != getattr(self, "_sig", None):
            self.rebuild()

    def highlight(self, path):
        """Select the entry for the folder the active tab shows."""
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

    def _section_toggled(self, item):
        if item.parent() is None and item.data(0, SECTION_ROLE):
            item.setIcon(0, theme.icon("chevron_down" if item.isExpanded() else "chevron", 12))
        self._remember_collapsed()

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

    def drawBranches(self, painter, rect, index):
        """Paint nothing in the indentation strip. Headings draw their own chevrons, and Qt
        would otherwise fill this strip as a separate block beside the rounded highlight."""
        return

    @property
    def show_eject(self) -> bool:
        return self._delegate.show_eject

    @show_eject.setter
    def show_eject(self, on: bool):
        self._delegate.show_eject = on
        self.viewport().update()

    def _eject_at(self, pos):
        """The drive item whose eject button is under pos, if any."""
        it = self.itemAt(pos)
        if it is None or not self.show_eject or not it.data(0, EJECT_ROLE):
            return None
        r = self.visualItemRect(it)
        return it if pos.x() >= r.right() - EJECT_W - 2 else None

    def mousePressEvent(self, ev):
        """Eject buttons, heading chevrons, heading drags and middle-click to a new tab."""
        self._press = None
        if ev.button() == Qt.MouseButton.LeftButton:
            it = self.itemAt(ev.position().toPoint())
            ej = self._eject_at(ev.position().toPoint())
            if ej is not None:
                self.ejectRequested.emit(ej.data(0, PATH_ROLE), ej.data(0, KIND_ROLE))
                return
            if self.drag_reorder and it is not None and it.parent() is None and it.data(0, SECTION_ROLE):
                self._press = (ev.position().toPoint(), it.data(0, SECTION_ROLE))
            # the chevron at the start of a heading toggles it, even on link headings
            if (it is not None and it.parent() is None and it.data(0, SECTION_ROLE)
                    and ev.position().x() < self.visualItemRect(it).left() + 22):
                it.setExpanded(not it.isExpanded())
                return
        if ev.button() == Qt.MouseButton.MiddleButton:
            it = self.itemAt(ev.position().toPoint())
            if it and it.data(0, PATH_ROLE):
                self.openRequested.emit(it.data(0, PATH_ROLE), True)
            return
        super().mousePressEvent(ev)

    # ----- keyboard: ↑/↓ browse as you go, ← / → collapse, expand or go back to the list ----
    def focus_from_list(self):
        """Land on the current folder's entry, or the first real entry."""
        sel = self.selectedItems()
        target = sel[0] if sel else None
        if target is None:
            for i in range(self.topLevelItemCount()):
                top = self.topLevelItem(i)
                kids = [top.child(j) for j in range(top.childCount())] if top.isExpanded() else []
                target = next((k for k in [top] + kids if k.data(0, PATH_ROLE)), None)
                if target is not None:
                    break
        if target is not None:
            self.setCurrentItem(target)
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def keyPressEvent(self, ev):
        """↑/↓ browse, ← / → fold or go back to the list, Enter opens."""
        it = self.currentItem()
        key = ev.key()
        if ev.modifiers() & ~Qt.KeyboardModifier.KeypadModifier:
            return super().keyPressEvent(ev)
        if key == Qt.Key.Key_Right:
            if it is not None and it.parent() is None and it.childCount() and not it.isExpanded():
                it.setExpanded(True)
            else:
                self.focusListRequested.emit()
            return
        if key == Qt.Key.Key_Left:
            if it is not None and it.parent() is not None:
                self.setCurrentItem(it.parent())
            elif it is not None and it.isExpanded():
                it.setExpanded(False)
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            if it is not None and it.parent() is None and not it.data(0, PATH_ROLE):
                it.setExpanded(not it.isExpanded())
            elif it is not None and it.data(0, PATH_ROLE):
                self._browse_timer.stop()
                self.openRequested.emit(it.data(0, PATH_ROLE), False)
            return
        if key == Qt.Key.Key_Escape:
            self.focusListRequested.emit()
            return
        self._kbd = key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Home, Qt.Key.Key_End,
                            Qt.Key.Key_PageUp, Qt.Key.Key_PageDown)
        super().keyPressEvent(ev)
        self._kbd = False

    def _current_moved(self, cur, _prev):
        if self._kbd and cur is not None and cur.data(0, PATH_ROLE):
            self._browse_timer.start()

    def _browse_current(self):
        it = self.currentItem()
        if it is not None and it.data(0, PATH_ROLE) and self.hasFocus():
            self.browseRequested.emit(it.data(0, PATH_ROLE))

    def mouseMoveEvent(self, ev):
        """Drag a heading to reorder sections; track the eject button under the mouse."""
        if self._press and ev.buttons() & Qt.MouseButton.LeftButton and \
                (ev.position().toPoint() - self._press[0]).manhattanLength() >= QApplication.startDragDistance():
            key = self._press[1]
            self._press = None
            md = QMimeData()
            md.setData(SECTION_MIME, key.encode())
            drag = QDrag(self)
            drag.setMimeData(md)
            it = next((self.topLevelItem(i) for i in range(self.topLevelItemCount())
                       if self.topLevelItem(i).data(0, SECTION_ROLE) == key), None)
            if it is not None:
                r = self.visualItemRect(it)
                drag.setPixmap(self.viewport().grab(r))
                drag.setHotSpot(ev.position().toPoint() - r.topLeft())
            drag.exec(Qt.DropAction.MoveAction)
            self._drop_y = None
            self.viewport().update()
            return
        ej = self._eject_at(ev.position().toPoint())
        hot = self.indexFromItem(ej) if ej is not None else None
        if hot != self._delegate.eject_hover:
            self._delegate.eject_hover = hot
            self.viewport().update()
        super().mouseMoveEvent(ev)

    def leaveEvent(self, ev):
        if self._delegate.eject_hover is not None:
            self._delegate.eject_hover = None
            self.viewport().update()
        super().leaveEvent(ev)

    def viewportEvent(self, ev):
        from PySide6.QtCore import QEvent
        if ev.type() == QEvent.Type.ToolTip:
            ej = self._eject_at(ev.pos())
            if ej is not None:
                from PySide6.QtWidgets import QToolTip
                QToolTip.showText(ev.globalPos(), "Disconnect" if ej.data(0, KIND_ROLE) == "netdrive" else "Eject", self)
                return True
        return super().viewportEvent(ev)

    def paintEvent(self, ev):
        super().paintEvent(ev)
        if self._drop_y is not None:
            p = QPainter(self.viewport())
            p.setPen(QPen(QColor(theme.colors()["accent"]), 2))
            p.drawLine(6, self._drop_y, self.viewport().width() - 6, self._drop_y)
            p.end()

    def _menu(self, pos):
        """Right-click: open, bookmark, eject, map / disconnect network drives."""
        it = self.itemAt(pos)
        if it is not None and it.parent() is None and it.data(0, SECTION_ROLE) and not it.data(0, PATH_ROLE):
            m = QMenu(self)
            m.addAction("Drag a heading to reorder sections").setEnabled(False)
            reset = m.addAction("Reset section order")
            reset.setEnabled(self.order() != DEFAULT_ORDER)
            reset.triggered.connect(lambda: (self.config.set("sidebar_order", []), self.rebuild()))
            m.exec(self.viewport().mapToGlobal(pos))
            return
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
        if kind == "drive" and it.data(0, EJECT_ROLE):
            m.addSeparator()
            m.addAction("Eject").triggered.connect(lambda: self.ejectRequested.emit(path, kind))
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
        """What a drop at this point would do: copy/move to a place, or add bookmarks."""
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
        if ev.mimeData().hasFormat(SECTION_MIME) and ev.source() is self:
            ev.acceptProposedAction()
        elif self._paths(ev.mimeData()):
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dragLeaveEvent(self, ev):
        self._drop_y = None
        self.viewport().update()
        super().dragLeaveEvent(ev)

    def dragMoveEvent(self, ev):
        """Highlight the drop target (or the reorder line for a heading)."""
        if ev.mimeData().hasFormat(SECTION_MIME):
            _before, y = self._drop_slot(int(ev.position().y()))
            if y != self._drop_y:
                self._drop_y = y
                self.viewport().update()
            ev.setDropAction(Qt.DropAction.MoveAction)
            ev.accept()
            return
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
        """Finish a drop: transfer files, add bookmarks or move a section."""
        if ev.mimeData().hasFormat(SECTION_MIME):
            key = bytes(ev.mimeData().data(SECTION_MIME)).decode()
            before, _y = self._drop_slot(int(ev.position().y()))
            self._drop_y = None
            ev.setDropAction(Qt.DropAction.MoveAction)
            ev.accept()
            if before != key:
                QTimer.singleShot(0, lambda: self.move_section(key, before))
            return
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
