"""Pieces shared by the step panels: small label helpers, a segmented button row, a KPI tile and the panel base class."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from ..ids import wid


def _h(text: str) -> QLabel:
    lab = QLabel(text.upper())
    lab.setObjectName("h")
    return lab


def _muted(text: str = "") -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("muted")
    lab.setWordWrap(True)
    return lab


def _seg(labels: list, current: str, parent=None, prefix: str = ""):
    """A row of exclusive buttons [(key, label)] -> (widget, group, {key: button}). prefix: the API id of the row ("mesh.color"); each
    button is prefix.key (prefix.off for the empty key)."""
    box = QWidget(parent)
    row = QHBoxLayout(box)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(0)
    grp = QButtonGroup(box)
    grp.setExclusive(True)
    btns = {}
    for key, text in labels:
        b = QPushButton(text)
        b.setObjectName("seg")
        b.setCheckable(True)
        b.setChecked(key == current)
        if prefix:
            wid(b, f"{prefix}.{key or 'off'}")
        grp.addButton(b)
        row.addWidget(b)
        btns[key] = b
    return box, grp, btns


class KPI(QFrame):
    """A number with a label and a verdict colour (ok / warn / bad)."""

    def __init__(self, label: str, value: str, unit: str = "", tone: str = "", hint: str = ""):
        super().__init__()
        self.setObjectName("kpi")
        self.setProperty("tone", tone)
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(1)
        l = QLabel(label.upper())
        l.setObjectName("kpil")
        from .. import theme
        val = QLabel(f"{value} <span style='font-size:12px;color:{theme.tokens()['muted']}'>{unit}</span>")
        val.setObjectName("kpiv")
        wid(self, "results.kpi." + "".join(c if c.isalnum() else "_" for c in label.lower()).strip("_"))
        self.setProperty("api_text", f"{label}: {value} {unit}".strip() + (f" ({tone})" if tone else ""))
        v.addWidget(l)
        v.addWidget(val)
        if hint:
            h = _muted(hint)
            h.setStyleSheet("font-size:11px")
            v.addWidget(h)


class _Panel(QWidget):
    changed = Signal()      # what the 3D view draws must be redone
    actionDone = Signal()

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.engine = engine
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(12, 10, 12, 12)
        self.v.setSpacing(8)

    def refresh(self, st: dict | None):
        raise NotImplementedError

    def _fail(self, e: Exception):
        QMessageBox.warning(self, "Cannot do that", str(e))
