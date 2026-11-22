"""The view toolbar above the 3D view: Iso, X, Y, Z, Fit, Edges, (Air box), Probe, Picture - and, under it, the section bar (Off X Y Z, the plane's
position, play, hide the air). The same buttons the web version has, each with an id, a tooltip that names its keyboard shortcut and, when it
cannot be used now, the reason.

The bars only hold widgets and send signals; what a button does to the study lives in simwb/qt/shell.py (it knows the step panels)."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QCheckBox, QFileDialog, QLabel, QPushButton, QSlider, QWidget

from .flow import FlowLayout
from .ids import wid

# what the keyboard does (shown in the tooltips and the View menu)
KEYS = {"iso": "Ctrl+1", "x": "Ctrl+2", "y": "Ctrl+3", "z": "Ctrl+4", "fit": "F", "edges": "Ctrl+E", "picture": "Ctrl+P", "probe": "Ctrl+Shift+P",
        "play": "Ctrl+Shift+Space"}


def _tip(text: str, key: str | None) -> str:
    return f"{text} ({KEYS[key]})" if key else text


class ViewBar(QWidget):
    def __init__(self, view, parent=None):
        super().__init__(parent)
        self.view = view
        self.setObjectName("viewbar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        row = FlowLayout(self, spacing=4)                 # (wraps onto a second line in a narrow window: the 3D view need not be wide)
        row.setContentsMargins(8, 5, 8, 5)
        self.look_btns: dict = {}
        for key, text, tip in (("iso", "Iso", "Isometric view"), ("x", "X", "Look along X (press again: from the other side)"),
                               ("y", "Y", "Look along Y (press again: from the other side)"), ("z", "Z", "Look along Z (press again: from the other side)"),
                               ("fit", "Fit", "Fit the view to the model")):
            b = wid(QPushButton(text), f"viewbar.{key}")
            b.setObjectName("viewbtn")
            b.setToolTip(_tip(tip, key))
            b.clicked.connect(lambda _=False, k=key: self.look(k))
            row.addWidget(b)
            self.look_btns[key] = b
        self.edges = wid(QCheckBox("Edges"), "viewbar.edges")
        self.edges.setChecked(True)
        self.edges.setToolTip(_tip("Draw the sharp edges and face outlines of the model", "edges"))
        self.edges.setShortcut(KEYS["edges"])
        self.edges.toggled.connect(view.set_edges_visible)
        row.addWidget(self.edges)
        self.air = wid(QCheckBox("Air box"), "view.air_box")          # (flow and cooling studies only: the owner shows it)
        self.air.setToolTip("Show the box of air round the part (translucent)")
        self.air.setVisible(False)
        row.addWidget(self.air)
        self.probe = wid(QPushButton("⌖ Probe"), "viewbar.probe")
        self.probe.setObjectName("viewbtn")
        self.probe.setCheckable(True)
        self.probe.setShortcut(KEYS["probe"])
        self.set_probe_usable(False, "Probe reads the value of a result at a point: open the Results step after the simulation has run")
        row.addWidget(self.probe)
        self.pic = wid(QPushButton("Picture"), "viewbar.picture")
        self.pic.setObjectName("viewbtn")
        self.pic.setToolTip(_tip("Save the 3D view as a picture (PNG), with the colour scale", "picture"))
        self.pic.setShortcut(KEYS["picture"])
        self.pic.clicked.connect(self.save_picture)
        row.addWidget(self.pic)
        self._last = None

    def set_probe_usable(self, usable: bool, why: str = ""):
        """Probe is on the Results step only: elsewhere it stays visible but disabled, and says why."""
        self.probe.setEnabled(usable)
        self.probe.setToolTip(_tip("Probe: click a point of the result to read its value there. Press again to stop; the labels stay until the field changes", "probe")
                              if usable else f"{why} ({KEYS['probe']})")

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


class SectionBar(QWidget):
    """Section: Off X Y Z, a slider with the plane's value, play (sweeps the plane through the part), Hide the air. It is hidden where a
    section does not apply (every step but Mesh and Results); the shell links it to the panel of the step."""
    axisChosen = Signal(str)          # "" (off), "x", "y", "z"
    positionChosen = Signal(int)      # 0..1000, when the slider is let go
    moved = Signal(int)               # 0..1000 while it is dragged (only the value label follows)
    playToggled = Signal(bool)
    hideAirToggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("viewbar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        row = FlowLayout(self, spacing=4)
        row.setContentsMargins(8, 3, 8, 5)
        lab = QLabel("Section")
        lab.setObjectName("h")
        lab.setToolTip("Cut the model with a plane to see the inside")
        row.addWidget(lab)
        self.btns: dict = {}
        self.grp = QButtonGroup(self)
        self.grp.setExclusive(True)
        for key, text in (("", "Off"), ("x", "X"), ("y", "Y"), ("z", "Z")):
            b = wid(QPushButton(text), f"viewbar.section.{key or 'off'}")
            b.setObjectName("viewbtn")
            b.setCheckable(True)
            b.setChecked(not key)
            b.setToolTip("No section" if not key else f"Cut across the {key.upper()} axis: the plane is moved with the slider")
            self.grp.addButton(b)
            b.clicked.connect(lambda _=False, k=key: self.axisChosen.emit(k))
            row.addWidget(b)
            self.btns[key] = b
        self.slider = wid(QSlider(Qt.Horizontal), "viewbar.section.pos")
        self.slider.setRange(0, 1000)
        self.slider.setValue(500)
        self.slider.setFixedWidth(150)
        self.slider.setTracking(False)               # (the picture is drawn when the slider is let go, not on every pixel)
        self.slider.valueChanged.connect(self.positionChosen.emit)
        self.slider.sliderMoved.connect(self.moved.emit)
        row.addWidget(self.slider)
        self.value = wid(QLabel(""), "viewbar.section.value")
        self.value.setObjectName("muted")
        self.value.setMinimumWidth(84)
        row.addWidget(self.value)
        self.play = wid(QPushButton("▶"), "viewbar.section.play")
        self.play.setObjectName("viewbtn")
        self.play.setCheckable(True)
        self.play.setShortcut(KEYS["play"])
        self.play.toggled.connect(self.playToggled.emit)
        row.addWidget(self.play)
        self.hide_air = wid(QCheckBox("Hide air"), "viewbar.section.hide_air")
        self.hide_air.setToolTip("Draw only the solid parts; untick to see the air and the field in it")
        self.hide_air.toggled.connect(self.hideAirToggled.emit)
        self.hide_air.setVisible(False)
        row.addWidget(self.hide_air)
        self.set_axis("")

    def set_axis(self, axis: str):
        """Show the chosen axis (without sending a signal), enable what needs one, and say why when it is not usable."""
        b = self.btns.get(axis, self.btns[""])
        self.grp.blockSignals(True)
        b.setChecked(True)
        self.grp.blockSignals(False)
        on = bool(axis)
        self.slider.setEnabled(on)
        self.play.setEnabled(on)
        self.slider.setToolTip("Where the plane cuts the model, from one end to the other" if on else "Choose X, Y or Z first: the slider moves the plane")
        self.play.setToolTip(_tip("Sweep the plane through the model, from one end to the other (press again to stop)", "play") if on
                             else f"Choose X, Y or Z first: play sweeps the plane through the model ({KEYS['play']})")
        if not on:
            self.value.setText("")

    def set_pos(self, v: int):
        self.slider.blockSignals(True)
        self.slider.setValue(int(v))
        self.slider.blockSignals(False)

    def set_playing(self, on: bool):
        self.play.blockSignals(True)
        self.play.setChecked(bool(on))
        self.play.setText("❚❚" if on else "▶")
        self.play.blockSignals(False)
