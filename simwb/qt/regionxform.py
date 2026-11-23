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
from .gizmo import nice_step_mm, rot_about
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
        self.grow = np.zeros((3, 2))                       # how far each face was moved out (m): the box's six faces; a cylinder: start, end, radius
        self.grow_base = np.zeros((3, 2))
        self.scaling = False
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
        self.grow, self.scaling = np.zeros((3, 2)), False
        self.adv.active_region = i
        self._gizmo()
        self._preview()
        self.adv.panel.win.shell.sync()

    def stop(self):
        was = self.index is not None
        self.index = None
        self.R, self.shift, self.live = np.eye(3), np.zeros(3), False
        self.grow, self.scaling = np.zeros((3, 2)), False
        self.fields = {}
        g = self.view.region_gizmo
        if g.on or was:
            g.clear()
        self.view.scale_handles.clear()
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
        return not np.allclose(self.R, np.eye(3), atol=1e-9) or bool(np.abs(self.shift).max() > 1e-12) or bool(np.abs(self.grow).max() > 1e-12)

    def preview(self) -> dict | None:
        r = self.region()
        return None if r is None else region_tools.moved_and_turned(region_tools.grown(r, self.grow), self.R, self.shift)

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
        r0 = regs[self.index]
        grown = region_tools.grown(r0, self.grow)
        regs[self.index] = region_tools.moved_and_turned(grown, self.R, self.shift)
        self.view.region_view.set(regs, self.index)
        g = self.view.region_gizmo
        g.center = region_tools.centre(grown)
        g.shift = self.shift.copy()
        if g.on and g.drag is None:
            g._draw()
        lo, hi = region_tools.bounds(regs[self.index])
        size = float(max(np.asarray(hi) - np.asarray(lo))) or 0.1
        self.view.scale_handles.set(region_tools.face_handles(regs[self.index]), 0.1 * size, self.on_scale, nice_step_mm(size) / M)
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

    def on_scale(self, key, delta: float, final: bool):
        """A resize handle is dragged: its face moves out by `delta` (called by the handles); the first call (zero, not final) starts the drag."""
        if not final and not self.scaling:
            self.grow_base = self.grow.copy()
            self.scaling = True
        self.grow = self.grow_base.copy()
        self.grow[region_tools.growth_index(key)] += float(delta)
        text = f"Moving this face {float(delta) * M:+.1f} mm"
        self._preview()
        self.view.region_gizmo.show_angle(text)
        if self.readout is not None:
            self.readout.setText(text)
        if final:
            self.view.region_gizmo.show_angle(None)
            self.scaling = False

    # ------------------------------------------------------------------ typed numbers
    def _fill_fields(self):
        if not self.fields:
            return
        rx, ry, rz = euler_deg(self.R)
        vals = {"mx": self.shift[0] * M, "my": self.shift[1] * M, "mz": self.shift[2] * M, "tx": rx, "ty": ry, "tz": rz, "sc": 100.0}
        p = self.preview()
        if p is not None:
            sz = region_tools.size_of(p)
            vals.update({"sl": sz[0] * M, "sr": sz[1] * M} if p.get("shape") == "cylinder" else {"sx": sz[0] * M, "sy": sz[1] * M, "sz": sz[2] * M})
        for k, e in list(self.fields.items()):
            try:
                if not e.hasFocus():
                    e.setText(fmt_num(round(vals[k], 3)))
            except RuntimeError:
                self.fields.pop(k, None)

    def typed(self, key: str | None = None):
        """A number in the fields changed: the change is what the fields say (a size or the scale for the faces, the move and the turn otherwise)."""
        try:
            v = {k: float(e.text().replace(",", ".") or 0) for k, e in self.fields.items()}
        except (ValueError, RuntimeError):
            return
        r = self.region()
        if r is None:
            return
        if key in ("sx", "sy", "sz", "sl", "sr", "sc"):
            orig = region_tools.size_of(r)
            g = np.zeros((3, 2))
            if key == "sc":                                         # (all sizes by a percentage of the stored region)
                f = max(v["sc"], 0.1) / 100.0
                want = [orig[0] * f, orig[1] * f, orig[2] * f]
            elif r.get("shape") == "cylinder":
                want = [v["sl"] / M, v["sr"] / M, orig[2]]
            else:
                want = [v["sx"] / M, v["sy"] / M, v["sz"] / M]
            if r.get("shape") == "cylinder":
                g[0, :] = (want[0] - orig[0]) / 2
                g[1, 0] = want[1] - orig[1]
            else:
                for k in range(3):
                    g[k, :] = (want[k] - orig[k]) / 2
            self.grow = g
            self._preview()
            return
        self.shift = np.array([v["mx"], v["my"], v["mz"]]) / M
        self.R = rotation_from_euler(v["tx"], v["ty"], v["tz"])
        self.live = False
        g = self.view.region_gizmo
        g.shift = self.shift.copy()
        self._preview()

    def reset(self):
        self.R, self.shift, self.live = np.eye(3), np.zeros(3), False
        self.grow, self.scaling = np.zeros((3, 2)), False
        self._preview()

    # ------------------------------------------------------------------ the block in the region's card
    def build(self, i: int) -> QWidget:
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 4, 0, 0)
        v.setSpacing(4)
        note = QLabel("Drag an arrow to move the region, the ball to move it freely, a ring to turn it, or a square-ended arrow on a face to resize it "
                      "(Shift: round steps) - or type the numbers. Nothing is stored until you press Apply.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        v.addWidget(note)
        self.fields = {}
        cyl = (self.region() or {}).get("shape") == "cylinder"
        rows = [("Move by", ("mx", "my", "mz"), "mm", "xyz"), ("Turn about", ("tx", "ty", "tz"), "°", "xyz"),
                ("Size", ("sl", "sr") if cyl else ("sx", "sy", "sz"), "mm", ("length", "radius") if cyl else "xyz"), ("Scale all", ("sc",), "%", ("",))]
        tips = {"Move by": " (the region is moved by this much)", "Turn about": " (the region is turned this much about its middle)",
                "Size": " (in the region's own axes; the region grows or shrinks about its middle)", "Scale all": " (a percentage of the region as it is stored)"}
        for title, keys, unit, names in rows:
            row = QHBoxLayout()
            row.addWidget(QLabel(f"{title} [{unit}]"))
            for k, ax in zip(keys, names):
                e = wid(QLineEdit("100" if k == "sc" else "0"), f"mesh.region.{i}.xf.{k}")
                e.setValidator(QDoubleValidator(-1e7, 1e7, 4, e))
                e.setToolTip(f"{title} {ax} [{unit}]".replace("  ", " ") + tips[title])
                e.setAccessibleName(f"{title} {ax}".strip())
                e.editingFinished.connect(lambda k=k: self.typed(k))
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
