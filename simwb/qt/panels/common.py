"""Pieces shared by the step panels: small label helpers, a segmented button row, a KPI tile and the panel base class."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from ..ids import wid


class FlowLayout(QLayout):
    """A row of widgets that goes on to the next line when the panel is too narrow (Qt has no wrapping row of its own): the buttons of a
    card then never make the whole panel wider than its dock. Same calls as a QHBoxLayout: addWidget / addStretch is not needed."""

    def __init__(self, parent=None, spacing: int = 6, vspacing: int | None = None, fill: bool = False):
        super().__init__(parent)
        self._items: list = []
        self._fill = fill                  # (each line is stretched to the full width, as a row of a QHBoxLayout would be)
        self._sp = spacing
        self._vsp = spacing if vspacing is None else vspacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):  # noqa: N802
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):  # noqa: N802
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):  # noqa: N802
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self):  # noqa: N802
        return True

    def heightForWidth(self, w):  # noqa: N802
        return self._layout(QRect(0, 0, w, 0), True)

    def setGeometry(self, rect):  # noqa: N802
        super().setGeometry(rect)
        self._layout(rect, False)

    def sizeHint(self):  # noqa: N802
        return self.minimumSize()

    def minimumSize(self):  # noqa: N802
        w = h = 0
        for it in self._items:
            s = it.minimumSize()
            w, h = max(w, s.width()), max(h, s.height())
        m = self.contentsMargins()
        return QSize(w + m.left() + m.right(), h + m.top() + m.bottom())

    def _layout(self, rect, test: bool) -> int:
        m = self.contentsMargins()
        x0, y0 = rect.x() + m.left(), rect.y() + m.top()
        avail = max(rect.right() - m.right() - x0 + 1, 1)
        lines, cur, cur_w = [], [], 0
        for it in self._items:
            w = it.sizeHint().width()
            if cur and cur_w + self._sp + w > avail:
                lines.append((cur, cur_w))
                cur, cur_w = [], 0
            cur_w += (self._sp if cur else 0) + w
            cur.append(it)
        if cur:
            lines.append((cur, cur_w))
        y = y0
        for n, (line, total) in enumerate(lines):
            extra = (avail - total) // len(line) if (self._fill and len(lines) >= 1) else 0
            x, line_h = x0, max(i.sizeHint().height() for i in line)
            for it in line:
                w = min(it.sizeHint().width() + max(extra, 0), avail)
                if not test:
                    it.setGeometry(QRect(QPoint(x, y), QSize(w, it.sizeHint().height())))
                x += w + self._sp
            y += line_h + (self._vsp if n < len(lines) - 1 else 0)
        return y - rect.y() + m.bottom()


def _h(text: str) -> QLabel:
    lab = QLabel(text.upper())
    lab.setObjectName("h")
    lab.setWordWrap(True)
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
    if len(labels) > 2:
        row = FlowLayout(box, 0, 4, fill=True)
    else:
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
        self.head.setMinimumWidth(10)                      # (a long title is cut at the panel edge: it must not widen the panel)
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
        import logging

        toasts = getattr(getattr(self, "win", None), "toasts", None)
        if toasts is not None:                                             # (a toast: it fades by itself and is logged; no box to click away)
            toasts.error(f"Cannot do that: {e}")
            return
        logging.getLogger("simwb.qt").warning("Cannot do that: %s", e)
        box = QMessageBox(QMessageBox.Warning, "Cannot do that", str(e), QMessageBox.Ok, self)
        box.setAttribute(Qt.WA_DeleteOnClose)
        box.open()                                                         # (shown over the window; does not stop the program)
