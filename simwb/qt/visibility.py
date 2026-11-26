"""The "Show in the view" list of a flow result with an object (external flow): one tick box per thing the view can draw, so that with a moving
(sliding) mesh the part that moves can be looked at alone, also while the time bar plays.

Items: every surface group of the result (the airframe, the propeller that turns with its moving zone, parts with a condition of their own ...),
the air box (the same switch as the 3D view's Air box button), and the outlines of the moving zones and of the refinement regions. The ticks are
remembered per study while the program runs. The list only says what is wanted; the window draws it (main_window.update_view) from `selection()`."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from .. import studies
from .ids import wid
from .panels.common import _h, _muted, clear_layout


def group_label(g: str, zones: list) -> tuple:
    """(text, moves) of a surface group: 'body' is the object, '<zone>_walls' is what turns with that moving zone."""
    from ..solvers.openfoam.foamio import sanitize
    for z in zones:
        if g == sanitize(f"{z['name']}_walls"):
            return f"{z['name']} (moving)", True
    return ("Object" if g == "body" else g.replace("_", " ")), False


def zone_outline(z: dict) -> dict:
    """A moving zone (centre, axis, radius, length) as the cylinder region the 3D view draws."""
    c = np.asarray(z["center"], float)
    a = np.asarray(studies.AXES.get(z["axis"], z["axis"]), float)
    a = a / (np.linalg.norm(a) or 1.0)
    return {"shape": "cylinder", "name": z["name"], "start": (c - a * z["length"] / 2).tolist(), "end": (c + a * z["length"] / 2).tolist(),
            "radius": z["radius"], "size": 0.0}


class VisibilityList(QWidget):
    changed = Signal()
    airToggled = Signal(bool)             # (the tick box of the air box was clicked: the 3D view's Air box button follows)

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        v.addWidget(_h("Show in the view"))
        v.addWidget(_muted("Tick what is drawn: hide the airframe to see the propeller turn, or the other way round. It follows the time bar."))
        self.lay = QVBoxLayout()
        self.lay.setSpacing(2)
        v.addLayout(self.lay)
        row = QHBoxLayout()
        self.all_btn = wid(QPushButton("Show all"), "results.vis.all")
        self.all_btn.setObjectName("ghostBorder")
        self.all_btn.setToolTip("Draw every part again (the outlines and the air box stay as they are)")
        self.all_btn.clicked.connect(lambda _=False: self.apply(all=True))
        self.moving_btn = wid(QPushButton("Only what moves"), "results.vis.moving")
        self.moving_btn.setObjectName("ghostBorder")
        self.moving_btn.clicked.connect(lambda _=False: self.apply(only_moving=True))
        row.addWidget(self.all_btn)
        row.addWidget(self.moving_btn)
        row.addStretch(1)
        v.addLayout(row)
        self.checks: dict = {}                  # key -> tick box: 'g:<surface group>', 'air', 'zones', 'regions'
        self.moving_keys: list = []
        self._pref: dict = {}                   # study id -> {key: ticked}
        self._pid = None
        self.setVisible(False)

    # ------------------------------------------------------------------ what the result has
    def set_items(self, pid, groups: list, zones: list, regions: list, air_on: bool):
        """groups: the surface groups the view draws; zones: the moving zones of the setup; regions: the refinement regions. Rebuilt only when they change."""
        items = []
        for g in groups:
            text, moves = group_label(g, zones)
            items.append(("g:" + g, text, True, moves, f"Draw the surface '{g}'" + (": it moves with the mesh" if moves else "")))
        if groups:
            items.append(("air", "Air box (outer boundary)", False, False, "The translucent box of air round the model (the same switch as the Air box button of the 3D view)"))
            if zones:
                items.append(("zones", "Moving zones (outline)", False, False, "The outline of the cylinders of air that turn: " + ", ".join(z["name"] for z in zones)))
            if regions:
                items.append(("regions", "Refinement regions (outline)", False, False, "Where the mesh is finer"))
        if pid == self._pid and [(k, c.text()) for k, c in self.checks.items()] == [(k, t) for k, t, *_ in items]:
            self._sync_air(air_on)
            return
        self._pid = pid
        clear_layout(self.lay)
        self.checks, self.moving_keys = {}, []
        pref = self._pref.setdefault(pid, {})
        for i, (key, text, default, moves, tip) in enumerate(items):
            c = wid(QCheckBox(text), f"results.vis.{i}")
            c.setChecked(air_on if key == "air" else pref.get(key, default))
            c.setToolTip(tip)
            c.toggled.connect(lambda on, key=key: self._toggled(key, on))
            self.lay.addWidget(c)
            self.checks[key] = c
            if moves:
                self.moving_keys.append(key)
        self.moving_btn.setEnabled(bool(self.moving_keys))
        self.moving_btn.setToolTip("Show only the parts that move with the mesh, with the outline of their zone: the rest is hidden" if self.moving_keys
                                   else "There is no moving part in this result (a sliding zone is added in Setup)")
        self.setVisible(bool(items))

    def _sync_air(self, on: bool):
        c = self.checks.get("air")
        if c is not None and c.isChecked() != bool(on):
            c.blockSignals(True)
            c.setChecked(bool(on))
            c.blockSignals(False)

    def air_changed(self, on: bool):
        """The 3D view's Air box button changed: the tick box follows."""
        self._sync_air(on)
        if self._pid is not None and "air" in self.checks:
            self._pref.setdefault(self._pid, {})["air"] = bool(on)

    def _toggled(self, key: str, on: bool):
        self._pref.setdefault(self._pid, {})[key] = bool(on)
        if key == "air":
            self.airToggled.emit(bool(on))
        self.changed.emit()

    # ------------------------------------------------------------------ what is wanted
    def selection(self):
        """None when the list has nothing to say; else {'groups': the surface groups ticked (None: every one, the study's own choice), 'air', 'zones', 'regions'}."""
        if not self.checks:
            return None
        gs = {k[2:]: c.isChecked() for k, c in self.checks.items() if k.startswith("g:")}
        on = lambda k: k in self.checks and self.checks[k].isChecked()  # noqa: E731
        return {"groups": None if all(gs.values()) else [g for g, t in gs.items() if t], "air": on("air"), "zones": on("zones"), "regions": on("regions")}

    def apply(self, items: dict | None = None, all: bool = False, only_moving: bool = False):  # noqa: A002
        """Tick boxes by name -> bool (a surface group, its text, or 'air' / 'zones' / 'regions'); all: every surface ticked; only_moving: just what moves
        with the mesh, and the outline of its zone. One redraw."""
        if not self.checks:
            raise ValueError("There is nothing to show or hide here: this is for flow results with an object")
        if only_moving and not self.moving_keys:
            raise ValueError("There is no moving part in this result: add a sliding zone in Setup")
        chk = list(self.checks.items())
        for _k, c in chk:
            c.blockSignals(True)
        try:
            if all or only_moving:
                for k, c in chk:
                    if k.startswith("g:"):
                        c.setChecked(True if all else k in self.moving_keys)
                    elif k == "zones" and only_moving:
                        c.setChecked(True)
            for name, on in (items or {}).items():
                key = next((k for k, c in chk if name in (k, k[2:], c.text(), c.text().replace(" (moving)", ""))), None)
                if key is None:
                    raise ValueError(f"{name!r} is not in the list: " + ", ".join(c.text() for _k, c in chk))
                self.checks[key].setChecked(bool(on))
        finally:
            for k, c in chk:
                c.blockSignals(False)
                self._pref.setdefault(self._pid, {})[k] = c.isChecked()
        if "air" in self.checks:
            self.airToggled.emit(self.checks["air"].isChecked())
        self.changed.emit()

    def state(self) -> dict:
        return {"items": {c.text(): c.isChecked() for c in self.checks.values()}, "moving": [self.checks[k].text() for k in self.moving_keys],
                "selection": self.selection()}
