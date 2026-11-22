"""A one-line bar under the 3D view for what to do now ("Drag a ring to turn ...", "Now click the plane to align it to ...") with an optional button.
It is its own bar, not text printed over the picture, so it never covers the model or the gizmo. Several parts of the window can ask for it
(the gizmo's hint, the align step's instruction); the one with the highest priority is shown."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton

from . import theme
from .ids import wid


class Banner(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setObjectName("viewBanner")
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 4, 8, 4)
        row.setSpacing(8)
        self.label = wid(QLabel(""), "view.banner")
        self.label.setWordWrap(True)
        self.label.setMinimumWidth(0)
        self.button = wid(QPushButton(""), "view.banner.button")
        self.button.setObjectName("ghostBorder")
        row.addWidget(self.label, 1)
        row.addWidget(self.button)
        self._items: dict = {}          # key -> (priority, text, button text, callback)
        self._cb = None
        self.button.clicked.connect(lambda: self._cb and self._cb())
        theme.bus.changed.connect(lambda _: self._style())
        self._style()
        self._refresh()

    def _style(self):
        t = theme.tokens()
        self.setStyleSheet(f"QFrame#viewBanner {{ background: {t['panel2']}; border-top: 1px solid {t['line']}; }}")
        self.label.setStyleSheet(f"background: {t['panel2']};")

    def set(self, key: str, text: str, priority: int = 0, button: str | None = None, on_click=None):
        """Ask for the bar with this text (and a button) under the name `key`."""
        self._items[key] = (priority, text, button, on_click)
        self._refresh()

    def clear(self, key: str):
        if self._items.pop(key, None) is not None:
            self._refresh()

    def _refresh(self):
        if not self._items:
            self.setVisible(False)
            self._cb = None
            return
        _, text, button, cb = max(self._items.values(), key=lambda v: v[0])
        self.label.setText(text)
        self.label.setToolTip(text)
        self.button.setVisible(bool(button))
        if button:
            self.button.setText(button)
            self.button.setToolTip("Stop picking the plane: the face and any previewed turn stay")
        self._cb = cb
        self.setVisible(True)

    def text(self) -> str:
        return self.label.text() if self._items else ""
