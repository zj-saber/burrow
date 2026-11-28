"""Smooth wheel scrolling for the lists: each wheel notch glides instead of jumping.

The view scrolls by pixels (Qt then moves what's already drawn and paints only the strip that
comes into view), and a short eased animation carries the scroll bar to its target. Notches that
arrive while it's moving add to the target, so a fast spin stays smooth. Touchpads already send
pixel steps and are left to Qt.
"""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation
from PySide6.QtWidgets import QAbstractItemView, QApplication


class Glide:
    def __init__(self, view: QAbstractItemView, per_notch):
        """per_notch(): how many pixels one wheel notch moves."""
        view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.view = view
        self.per_notch = per_notch
        self.anim = QPropertyAnimation(view.verticalScrollBar(), b"value", view)
        self.anim.setDuration(150)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def wheel(self, ev) -> bool:
        """Handle a wheel event; False leaves it to Qt (touchpads, modifiers, sideways)."""
        steps = ev.angleDelta().y() / 120
        if not steps or not ev.pixelDelta().isNull() or ev.modifiers():
            return False
        sb = self.view.verticalScrollBar()
        running = self.anim.state() == QPropertyAnimation.State.Running
        base = self.anim.endValue() if running else sb.value()
        target = max(sb.minimum(), min(sb.maximum(), int(base - steps * self.per_notch())))
        if target != base:
            self.anim.stop()
            self.anim.setStartValue(sb.value())
            self.anim.setEndValue(target)
            self.anim.start()
        ev.accept()
        return True


def lines() -> int:
    """Windows' "lines to scroll per notch" setting (3 by default)."""
    n = QApplication.wheelScrollLines()
    return n if 0 < n < 50 else 3
