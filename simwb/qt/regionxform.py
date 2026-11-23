"""Moving and turning a refinement region (the Mesh step), the way the model is moved and turned in the Geometry step: a gizmo at the region's middle
(arrows move it along x, y, z, the ball moves it freely, rings turn it about x, y, z) or typed numbers (move by dx dy dz in mm, turn about x y z
in degrees). The region is previewed as it goes; Apply stores it (a box keeps its turn, a cylinder moves its end points), Cancel puts it back.

The change is one thing: x' = R (x - c) + c + shift, with c the region's middle; the gizmo, the numbers and Apply all work on it."""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from .. import regions as region_tools
from .forms import fmt_num
from .gizmo import rot_about
from .ids import wid

M = 1000.0


def euler_deg(R: np.ndarray) -> tuple:
    """(rx, ry, rz) in degrees with R = Rz Ry Rx (the turns about x, then y, then z of the fixed axes)."""
    ry = -math.asin(max(-1.0, min(1.0, float(R[2, 0]))))
    if abs(math.cos(ry)) < 1e-9:                                   # (looking straight along an axis: all the turn is put in rx)
        return math.degrees(math.atan2(-R[1, 2], R[1, 1])), math.degrees(ry), 0.0
    return (math.degrees(math.atan2(R[2, 1], R[2, 2])), math.degrees(ry), math.degrees(math.atan2(R[1, 0], R[0, 0])))


def rotation_from_euler(rx: float, ry: float, rz: float) -> np.ndarray:
    return rot_about(2, math.radians(rz)) @ rot_about(1, math.radians(ry)) @ rot_about(0, math.radians(rx))


class RegionTransform:
    def __init__(self, adv):
        self.adv = adv
        self.index: int | None = None
        self.R = np.eye(3)
        self.shift = np.zeros(3)
        self.base = (np.eye(3), np.zeros(3))               # the change when the drag in progress started
        self.live = False
        self.fields: dict = {}                              # "mx" ... "tz" -> the line edit (updated while a handle is dragged)
        self.readout: QLabel | None = None

    # ------------------------------------------------------------------ the session
    @property
    def view(self):
        return self.adv.panel.view

    @property
    def active(self) -> bool:
        return self.index is not None

    def region(self) -> dict | None:
        regs = self.adv._regions_state()
        return regs[self.index] if self.index is not None and self.index < len(regs) else None

    def start(self, i: int):
        self.index = i
        self.R, self.shift, self.live = np.eye(3), np.zeros(3), False
        self.adv.active_region = i
        self._gizmo()
        self._preview()
        self.adv.panel.win.shell.sync()

    def stop(self):
        was = self.index is not None
        self.index = None
        self.R, self.shift, self.live = np.eye(3), np.zeros(3), False
        self.fields = {}
        g = self.view.region_gizmo
        if g.on or was:
            g.clear()
        self.view.region_view.clear()
        if was:
            self.adv._sig = None
            self.adv.panel.draw_marks()
            if self.adv.panel.win is not None:
                self.adv.panel.win.shell.sync()

    def cancel(self):
        self.stop()
        self.adv.panel.refresh(self.adv.engine.state())

    def pending(self) -> bool:
        return not np.allclose(self.R, np.eye(3), atol=1e-9) or bool(np.abs(self.shift).max() > 1e-12)

    def preview(self) -> dict | None:
        r = self.region()
        return None if r is None else region_tools.moved_and_turned(r, self.R, self.shift)

    def apply(self):
        i, new = self.index, self.preview()
        if new is None:
            return
        try:
            regs = self.adv._regions_state()
            regs[i] = new
            self.stop()
            self.adv._save_regions(regs)
        except Exception as e:  # noqa: BLE001
            self.adv.panel._fail(e)

    # ------------------------------------------------------------------ what the 3D view shows
    def _gizmo(self):
        r = self.region()
        if r is None:
            return
        lo, hi = region_tools.bounds(r)
        size = float(max(np.asarray(hi) - np.asarray(lo))) or 0.1
        g = self.view.region_gizmo
        g.set(region_tools.centre(r), size, self.on_handle, None, 0.5 * float(np.linalg.norm(np.asarray(hi) - np.asarray(lo))) or size)
        g.shift = self.shift.copy()
        g._draw()

    def _preview(self):
        """Draw the region as it would be after Apply, in place of the stored one; put the numbers in the fields."""
        regs = self.adv._regions_state()
        if self.index is None or self.index >= len(regs):
            return
        regs[self.index] = self.preview()
        self.view.region_view.set(regs, self.index)
        g = self.view.region_gizmo
        g.shift = self.shift.copy()
        if g.on and g.drag is None:
            g._draw()
        self._fill_fields()
        self.view.render()

    def on_handle(self, kind: str, axis: int, value, final: bool):
        """A ring or an arrow is dragged (called by the gizmo); the first call (zero, not final) starts the drag."""
        if not final and not self.live:
            self.base = (self.R.copy(), self.shift.copy())
            self.live = True
        R0, s0 = self.base
        if kind == "rot":
            self.R, self.shift = rot_about(axis, float(value)) @ R0, s0.copy()
            text = f"Turning about {'xyz'[axis]}: {math.degrees(float(value)):+.1f}°"
        elif kind == "move":
            e = np.zeros(3)
            e[axis] = 1.0
            self.R, self.shift = R0.copy(), s0 + e * float(value)
            text = f"Moving along {'xyz'[axis]}: {float(value) * M:+.1f} mm"
        else:
            d = np.asarray(value, float)
            self.R, self.shift = R0.copy(), s0 + d
            text = f"Moving: ({d[0] * M:+.1f}, {d[1] * M:+.1f}, {d[2] * M:+.1f}) mm"
        self._preview()
        g = self.view.region_gizmo
        g.show_angle(text)
        if self.readout is not None:
            self.readout.setText(text)
        if final:
            g.show_angle(None)
            self.live = False
            self._preview()

    # ------------------------------------------------------------------ typed numbers
    def _fill_fields(self):
        if not self.fields:
            return
        rx, ry, rz = euler_deg(self.R)
        vals = {"mx": self.shift[0] * M, "my": self.shift[1] * M, "mz": self.shift[2] * M, "tx": rx, "ty": ry, "tz": rz}
        for k, e in list(self.fields.items()):
            try:
                if not e.hasFocus():
                    e.setText(fmt_num(round(vals[k], 3)))
            except RuntimeError:
                self.fields.pop(k, None)

    def typed(self):
        """A number in the fields changed: the change is what the fields say."""
        try:
            v = {k: float(e.text().replace(",", ".") or 0) for k, e in self.fields.items()}
        except (ValueError, RuntimeError):
            return
        self.shift = np.array([v["mx"], v["my"], v["mz"]]) / M
        self.R = rotation_from_euler(v["tx"], v["ty"], v["tz"])
        self.live = False
        g = self.view.region_gizmo
        g.shift = self.shift.copy()
        self._preview()

    def reset(self):
        self.R, self.shift, self.live = np.eye(3), np.zeros(3), False
        self._preview()

    # ------------------------------------------------------------------ the block in the region's card
    def build(self, i: int) -> QWidget:
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 4, 0, 0)
        v.setSpacing(4)
        note = QLabel("Drag an arrow to move the region, the ball to move it freely, a ring to turn it (Shift: round steps) - or type the numbers. "
                      "Nothing is stored until you press Apply.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        v.addWidget(note)
        self.fields = {}
        for title, keys, unit in (("Move by", ("mx", "my", "mz"), "mm"), ("Turn about", ("tx", "ty", "tz"), "°")):
            row = QHBoxLayout()
            row.addWidget(QLabel(f"{title} [{unit}]"))
            for k, ax in zip(keys, "xyz"):
                e = wid(QLineEdit("0"), f"mesh.region.{i}.xf.{k}")
                e.setValidator(QDoubleValidator(-1e7, 1e7, 4, e))
                e.setToolTip(f"{title} {ax} [{unit}]" + (" (the region is moved by this much)" if title == "Move by" else " (the region is turned this much about its middle)"))
                e.setAccessibleName(f"{title} {ax}")
                e.editingFinished.connect(self.typed)
                row.addWidget(e, 1)
                self.fields[k] = e
            v.addLayout(row)
        self.readout = wid(QLabel(""), f"mesh.region.{i}.xf.readout")
        self.readout.setObjectName("muted")
        v.addWidget(self.readout)
        br = QHBoxLayout()
        ap = wid(QPushButton("Apply"), f"mesh.region.{i}.xf.apply")
        ap.setObjectName("primary")
        ap.setToolTip("Store the region where it is shown now")
        ap.clicked.connect(self.apply)
        rs = wid(QPushButton("Reset"), f"mesh.region.{i}.xf.reset")
        rs.setObjectName("ghostBorder")
        rs.setToolTip("Put the preview back to where the region is")
        rs.clicked.connect(self.reset)
        cn = wid(QPushButton("Cancel"), f"mesh.region.{i}.xf.cancel")
        cn.setObjectName("ghostBorder")
        cn.setToolTip("Leave the region as it is")
        cn.clicked.connect(self.cancel)
        for b in (ap, rs, cn):
            br.addWidget(b)
        v.addLayout(br)
        self._fill_fields()
        return box
