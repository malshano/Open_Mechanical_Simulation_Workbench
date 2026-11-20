"""Simple / Expert: Simple shows the essential settings, Expert every setting and tool (as in the web version). One switch for the whole window."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class _Bus(QObject):
    changed = Signal(bool)       # True = simple


bus = _Bus()
_state = {"simple": False}


def simple() -> bool:
    return _state["simple"]


def set_simple(on: bool):
    on = bool(on)
    if on != _state["simple"]:
        _state["simple"] = on
        bus.changed.emit(on)
