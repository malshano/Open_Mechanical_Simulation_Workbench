"""Scrolling must only scroll. A drop-down, a slider or a number field under the pointer used to take the wheel (a laptop touchpad sends a stream of
them) and change its value: an easy way to add a boundary condition or change a material by accident while scrolling a panel.

One application-wide rule instead of a change in every control: a wheel over a combo box, spin box or slider that does not have the keyboard
focus is handed to the scroll area it sits in (or dropped when there is none). A click (or Tab) gives the control the focus, and then the wheel
changes it. The controls take the focus by click or Tab only, never by the wheel (their default would be to take it on the first notch)."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QAbstractScrollArea, QAbstractSpinBox, QApplication, QComboBox, QSlider, QWidget

GUARDED = (QComboBox, QAbstractSpinBox, QSlider)


def guarded_ancestor(w) -> QWidget | None:
    """The combo box, spin box or slider that a wheel over `w` (or over a part of it, like a spin box's line edit) is meant for."""
    for _ in range(4):
        if w is None:
            return None
        if isinstance(w, GUARDED):
            return w
        w = w.parentWidget()
    return None


def scroll_area_of(w) -> QAbstractScrollArea | None:
    p = w.parentWidget()
    while p is not None and not isinstance(p, QAbstractScrollArea):
        p = p.parentWidget()
    return p


class WheelGuard(QObject):
    def eventFilter(self, obj, ev):  # noqa: N802
        if ev.type() != QEvent.Wheel or not isinstance(obj, QWidget):
            return False
        if obj.window().windowType() == Qt.Popup:                # (the open list of a drop-down scrolls itself)
            return False
        w = guarded_ancestor(obj)
        if w is None:
            return False
        if w.focusPolicy() == Qt.WheelFocus:                 # (the default of these controls: the wheel itself would give them the focus)
            w.setFocusPolicy(Qt.StrongFocus)
        fw = QApplication.focusWidget() or w.window().focusWidget()      # (the window's own choice when it is not the active window)
        if w.hasFocus() or (fw is not None and (fw is w or w.isAncestorOf(fw))):
            return False                                       # (the control was clicked: the wheel is for it)
        sa = scroll_area_of(w)
        if sa is not None:
            self._forward(sa.viewport(), ev)
        return True                                            # (never for the control)

    @staticmethod
    def _forward(target: QWidget, ev: QWheelEvent):
        pos = target.mapFromGlobal(ev.globalPosition().toPoint())
        copy = QWheelEvent(pos, ev.globalPosition(), ev.pixelDelta(), ev.angleDelta(), ev.buttons(), ev.modifiers(), ev.phase(), ev.inverted(),
                           ev.source())
        QApplication.sendEvent(target, copy)


def install(app: QApplication):
    if getattr(app, "_simwb_wheel_guard", None) is None:
        app._simwb_wheel_guard = WheelGuard(app)
        app.installEventFilter(app._simwb_wheel_guard)
