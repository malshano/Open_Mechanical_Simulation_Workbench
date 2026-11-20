"""The view toolbar above the 3D view: Iso, X, Y, Z, Fit, Edges, Picture. (The same buttons the web version has.)"""
from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QFileDialog, QHBoxLayout, QPushButton, QWidget

from .ids import wid


class ViewBar(QWidget):
    def __init__(self, view, parent=None):
        super().__init__(parent)
        self.view = view
        self.setObjectName("viewbar")
        from PySide6.QtCore import Qt
        self.setAttribute(Qt.WA_StyledBackground, True)
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 5, 8, 5)
        row.setSpacing(4)
        for key, text, tip in (("iso", "Iso", "Isometric view"), ("x", "X", "Look along X (again: from the other side)"),
                               ("y", "Y", "Look along Y (again: from the other side)"), ("z", "Z", "Look along Z (again: from the other side)"),
                               ("fit", "Fit", "Fit the view to the model")):
            b = wid(QPushButton(text), f"viewbar.{key}")
            b.setObjectName("viewbtn")
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, k=key: self.look(k))
            row.addWidget(b)
        row.addSpacing(10)
        self.edges = wid(QCheckBox("Edges"), "viewbar.edges")
        self.edges.setChecked(True)
        self.edges.setToolTip("Draw the sharp edges and face outlines of the model")
        self.edges.toggled.connect(view.set_edges_visible)
        row.addWidget(self.edges)
        row.addStretch(1)
        self.pic = wid(QPushButton("Picture"), "viewbar.picture")
        self.pic.setObjectName("viewbtn")
        self.pic.setToolTip("Save the 3D view as a picture (PNG), with the colour scale")
        self.pic.clicked.connect(self.save_picture)
        row.addWidget(self.pic)
        self._last = None

    def look(self, key: str):
        """Iso / X / Y / Z / Fit. Looking along the same axis twice turns to the opposite side."""
        v = self.view
        if key in ("iso", "fit"):
            v.set_view("iso" if key == "iso" else "fit")
            self._last = None
            return
        name = f"+{key}"
        if self._last == name:
            name = f"-{key}"
        v.set_view(name)
        self._last = name

    def save_picture(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save the 3D view as a picture", "view.png", "PNG picture (*.png)")
        if path:
            self.view.picture(path)
