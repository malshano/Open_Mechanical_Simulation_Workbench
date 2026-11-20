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
        self.setProperty("api_text", f"{label}: {value} {unit}".strip() + (f" ({tone})" if tone else "") + (f": {hint}" if hint else ""))
        v.addWidget(l)
        v.addWidget(val)
        if hint:
            h = _muted(hint)
            h.setStyleSheet("font-size:11px")
            v.addWidget(h)


class ClickLabel(QLabel):
    """A line of text that wraps and can be pressed (a button's behaviour without a button's inability to wrap): hand cursor, underline on
    hover, keyboard focus, Enter/Space presses it."""
    clicked = Signal()

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setTextInteractionFlags(Qt.NoTextInteraction)
        self.setMinimumWidth(0)

    def click(self):
        self.clicked.emit()

    def mousePressEvent(self, e):  # noqa: N802
        if e.button() == Qt.LeftButton:
            self.clicked.emit()

    def keyPressEvent(self, e):  # noqa: N802
        if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.clicked.emit()
        else:
            super().keyPressEvent(e)


def clear_layout(layout):
    """Remove (and delete) everything in a layout, nested layouts included."""
    while layout.count():
        it = layout.takeAt(0)
        w = it.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()
        elif it.layout() is not None:
            clear_layout(it.layout())


class Section(QWidget):
    """A heading that folds its content away (the 'About this simulation' and 'Advanced' blocks)."""

    def __init__(self, title: str, prefix: str, expanded: bool = True, parent=None):
        super().__init__(parent)
        self.title = title
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        self.head = wid(QPushButton(), f"{prefix}.toggle")
        self.head.setObjectName("secHead")
        self.head.setCheckable(True)
        self.head.setToolTip(f"Show or hide: {title}")
        self.content = QWidget()
        self.body = QVBoxLayout(self.content)
        self.body.setContentsMargins(8, 0, 0, 0)
        self.body.setSpacing(6)
        v.addWidget(self.head)
        v.addWidget(self.content)
        self.head.toggled.connect(self._toggled)
        self.head.setChecked(expanded)
        self._toggled(expanded)

    def _toggled(self, on: bool):
        self.head.setText(("▼  " if on else "▶  ") + self.title)
        self.content.setVisible(on)


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
        self.win = None
        self.view = None

    def bind(self, win):
        """The window this panel lives in (its 3D view, its step switch): set by the window after it makes the panels."""
        self.win = win
        self.view = win.view

    def shown(self):
        """The panel's step became the current one (put the 3D view's picking in the state this step needs)."""

    def draw_marks(self):
        """Draw what this step hangs on the 3D model (tints, labels): called after the model was (re)drawn."""

    def left(self):
        """The step was left."""

    def refresh(self, st: dict | None):
        raise NotImplementedError

    def _fail(self, e: Exception):
        QMessageBox.warning(self, "Cannot do that", str(e))
