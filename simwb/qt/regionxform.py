"""Moving, turning and resizing a refinement region (the Mesh step), the way the model is moved and turned in the Geometry step: handles at the
region's middle (arrows move it along x, y, z, the ball moves it freely, rings turn it about x, y, z), a square-ended arrow on each face (resize) or typed
numbers (move by dx dy dz in mm, turn about x y z in degrees, the size). The handles are the shared ones of the 3D view (handles.py, the same as in the
Results step and for the rotating zones); regionhandles.py says which a region gets. The region is previewed as it goes; Apply stores it (a box keeps its
turn, a cylinder moves its end points), Cancel puts it back.

The change is one thing: x' = R (x - c) + c + shift, with c the region's middle; the handles, the numbers and Apply all work on it."""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from .. import regions as region_tools
from .forms import fmt_num
from .gizmo import rot_about
from .regionhandles import RegionHandles
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
        self.handles = RegionHandles(self)                  # what the 3D view shows for the session (regionhandles.py)
        self.aligning = False                               # waiting for a click on the face to line the region up with
        self.align_move = False                             # ... and to put the region's middle on that face too

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
        self._preview()
        self.adv.panel.win.shell.sync()

    def stop(self):
        was = self.index is not None
        self.stop_align(sync=False)
        self.index = None
        self.R, self.shift, self.live = np.eye(3), np.zeros(3), False
        self.grow, self.scaling = np.zeros((3, 2)), False
        self.fields = {}
        if was:
            self.view.handles.clear()
            self.view.handles.label(None)
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
    def _preview(self):
        """Draw the region as it would be after Apply, in place of the stored one; put the numbers in the fields."""
        regs = self.adv._regions_state()
        if self.index is None or self.index >= len(regs):
            return
        r0 = regs[self.index]
        grown = region_tools.grown(r0, self.grow)
        regs[self.index] = region_tools.moved_and_turned(grown, self.R, self.shift)
        self.view.region_view.set(regs, self.index)
        self.handles.show(regs[self.index])
        self._fill_fields()
        self.view.render()

    def _sync_bar(self):
        """The drag is over: the bar under the 3D view says what is pending now."""
        if self.adv.panel.win is not None:
            self.adv.panel.win.shell.sync()

    def _text(self, text: str):
        self.view.handles.label(text)
        if self.readout is not None:
            self.readout.setText(text)

    def on_move(self, key: str, point, final: bool):
        """The ball or an arrow is dragged (called by the handles): the region's middle is where the handle is."""
        c = region_tools.centre(region_tools.grown(self.region(), self.grow))
        self.shift = np.asarray(point, float) - c
        d = self.shift
        self._preview()
        self._text(f"Moving: ({d[0] * M:+.1f}, {d[1] * M:+.1f}, {d[2] * M:+.1f}) mm")
        if final:
            self.view.handles.label(None)
            self._sync_bar()

    def on_turn(self, axis: int, key: str, angle: float, final: bool):
        """A ring is dragged; the first call (zero, not final) starts the drag and remembers where the region was."""
        if not final and not self.live:
            self.base = (self.R.copy(), self.shift.copy())
            self.live = True
        R0, s0 = self.base
        self.R, self.shift = rot_about(axis, float(angle)) @ R0, s0.copy()
        self._preview()
        self._text(f"Turning about {'xyz'[axis]}: {math.degrees(float(angle)):+.1f}°")
        if final:
            self.view.handles.label(None)
            self.live = False
            self._sync_bar()

    def on_scale(self, key, delta: float, final: bool):
        """A face handle is dragged: its face moves out by `delta` (called by the handles); the first call (zero, not final) starts the drag."""
        if not final and not self.scaling:
            self.grow_base = self.grow.copy()
            self.scaling = True
        self.grow = self.grow_base.copy()
        self.grow[region_tools.growth_index(key)] += float(delta)
        self._preview()
        self._text(f"Moving this face {float(delta) * M:+.1f} mm")
        if final:
            self.view.handles.label(None)
            self.scaling = False
            self._sync_bar()

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
                    v = round(vals[k], 3)
                    e.setText(fmt_num(0.0 if v == 0 else v))                 # (never "-0": a turn or a move of nothing)
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
        self._preview()

    # ------------------------------------------------------------------ lining the region up with a face of the model
    def start_align(self):
        """'Line up with a face…': the next click on a face of the model (or a wall of the air volume) chooses the face."""
        if self.index is None:
            return
        p = self.adv.panel
        p.set_picking_part(False)
        p.set_picking_faces(False)
        self.aligning = True
        self.view.handles.clear()                           # (the handles run over the model: a click on a face would take one instead)
        self.view.set_pick_mode("face")
        self.view.set_selection([])
        self._sync_align_button()
        if p.win is not None:
            p.win.shell.sync()

    def stop_align(self, sync: bool = True):
        if not self.aligning:
            return
        self.aligning = False
        self.view.set_pick_mode(None)
        self.view.set_selection([])
        self._sync_align_button()
        if self.index is not None:
            self._preview()                                 # (the handles come back)
        if sync and self.adv.panel.win is not None:
            self.adv.panel.win.shell.sync()

    def _sync_align_button(self):
        b = getattr(self, "align_btn", None)
        try:
            if b is not None and b.isChecked() != self.aligning:
                b.blockSignals(True)
                b.setChecked(self.aligning)
                b.blockSignals(False)
        except RuntimeError:
            self.align_btn = None

    def face_picked(self, fid: int):
        """A face was clicked while lining up: turn the region (about its middle) so its nearest side lies parallel to that face, and with
        'also move it there' put its middle on the face. Shown as a preview; Apply stores it."""
        if not self.aligning or self.index is None:
            return
        try:
            pr = self.adv.engine.project()
            h = self.view.last_hit
            point = h["point"] if h and h.get("kind") == "face" and h.get("face") == fid else None
            info = pr.ref_info(int(fid), point=point)
            n = np.asarray(info.get("point_normal") if (point is not None and info.get("curved") and info.get("point_normal")) else info["normal"], float)
            cur = self.preview()
            R, side, ang = region_tools.align_with_direction(cur, n)
            self.R = R @ self.R
            if self.align_move:
                self.shift = np.asarray(info["point"], float) - region_tools.centre(region_tools.grown(self.region(), self.grow))
            self.live = False
        except Exception as e:  # noqa: BLE001
            self.adv.panel._fail(e)
            return
        self.stop_align()
        self._preview()
        cyl = (self.region() or {}).get("shape") == "cylinder"
        what = "its axis" if cyl else f"its {'xyz'[side]} side"
        text = (f"Face {int(fid)} points {info['nearest']}: {what} is now parallel to it (turned {ang:.1f}°)"
                + ("; the middle of the region is on the face" if self.align_move else "") + ". Press Apply to keep it.")
        if self.readout is not None:
            self.readout.setText(text)
        self.adv.panel.win.statusBar().showMessage(text, 9000)

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
                      "(Shift: round steps, Esc cancels a drag) - or type the numbers. Nothing is stored until you press Apply.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        v.addWidget(note)
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
        row = QHBoxLayout()
        self.align_btn = wid(QPushButton("Line up with a face…"), f"mesh.region.{i}.xf.align")
        self.align_btn.setCheckable(True)
        self.align_btn.setChecked(self.aligning)
        self.align_btn.setObjectName("toggle")
        self.align_btn.setToolTip("Click a face of the model or a wall of the air volume: the side of the region that points closest to it is turned parallel to it "
                                  "(a cylinder: its axis). The turn is previewed; Apply stores it")
        self.align_btn.toggled.connect(lambda on: self.start_align() if on else self.stop_align())
        row.addWidget(self.align_btn)
        mv = wid(QCheckBox("and put its middle on the face"), f"mesh.region.{i}.xf.align_move")
        mv.setChecked(self.align_move)
        mv.setToolTip("Also move the region so that its middle is on the face you click")
        mv.toggled.connect(lambda on: setattr(self, "align_move", bool(on)))
        row.addWidget(mv, 1)
        v.addLayout(row)
        self.readout = wid(QLabel(""), f"mesh.region.{i}.xf.readout")
        self.readout.setObjectName("muted")
        v.addWidget(self.readout)
        self._fill_fields()
        return box
