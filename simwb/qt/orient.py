"""The orientation block of the Geometry step: the rings in the 3D view, the +-90 degree buttons and the free angle all drive ONE previewed
turn (the model turns in the view only); Apply orientation sends it to the engine (rotate_geometry, a job), Cancel throws it away, Reset
orientation goes back to the file's own.

The turn is kept here as a 3x3 matrix `R` in the stored orientation (x' = R (x - c) + c about the middle of the parts c) and as a list of
steps for the readout ("x +90.0°, then z +37.5°"). Aligning a face (align.py) puts its turn here too."""
from __future__ import annotations

import math

import numpy as np
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

from . import theme
from .gizmo import HINT, SNAP_DEG, rot_about
from .ids import wid


def _h(text: str) -> QLabel:
    lab = QLabel(text.upper())
    lab.setObjectName("h")
    return lab


def _muted(text: str = "") -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("muted")
    lab.setWordWrap(True)
    return lab


def ink_label(text: str, name: str) -> QLabel:
    """A text label in the normal text colour, with the panel's own background painted in it (a picture of a transparent label has no background)."""
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setStyleSheet(f"background: {theme.tokens()['panel2']};")
    wid(lab, name)
    return lab


def _item() -> QFrame:
    f = QFrame()
    f.setObjectName("item")
    f.setAttribute(Qt.WA_StyledBackground, True)
    return f


def notice(kind: str, text: str, name: str = "geometry.notice") -> QFrame:
    """A message people cannot miss: a box with an icon, a coloured stripe and full-size text (kind: info | ok | warn | bad)."""
    t = theme.tokens()
    col, icon = {"info": (t["accent"], "ℹ"), "ok": (t["ok"], "✓"), "warn": (t["warn"], "⚠"), "bad": (t["bad"], "✕")}[kind]
    ink = {"info": t["accent_text"], "ok": t["ok_text"], "warn": t["warn_text"], "bad": t["bad_text"]}[kind]
    f = QFrame()
    f.setObjectName("notice")
    f.setProperty("tone", kind)
    f.setAttribute(Qt.WA_StyledBackground, True)
    f.setStyleSheet(f"QFrame#notice {{ background: {t['panel2']}; border: 1px solid {col}; border-left: 5px solid {col}; border-radius: 6px; }}")
    h = QHBoxLayout(f)
    h.setContentsMargins(10, 8, 10, 8)
    h.setSpacing(10)
    ic = QLabel(icon)
    ic.setStyleSheet(f"font-size: 20px; font-weight: 700; color: {ink}; background: {t['panel2']};")
    ic.setFixedWidth(24)
    ic.setAlignment(Qt.AlignTop)
    ic.setAccessibleName({"info": "Information", "ok": "Done", "warn": "Warning", "bad": "Error"}[kind])
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setTextFormat(Qt.RichText)
    lab.setObjectName("noticeText")
    lab.setStyleSheet(f"background: {t['panel2']};")
    h.addWidget(ic)
    h.addWidget(lab, 1)
    wid(f, name)
    f.setProperty("api_text", f"{kind}: " + _plain(text))
    f.setAccessibleName(_plain(text))
    return f


def _plain(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s)


def angle_of(R: np.ndarray) -> float:
    """The angle (degrees) of a turn, from its matrix."""
    return math.degrees(math.acos(max(-1.0, min(1.0, (float(np.trace(R)) - 1) / 2))))


class OrientController:
    """The previewed change of the model's place: a turn R (about the middle c of what the gizmo acts on) and then a move `shift` (m):
    x' = R (x - c) + c + shift. Rings, arrows, the ball, the buttons and the typed fields all change this one thing."""

    def __init__(self, panel):
        self.panel = panel
        self.R: np.ndarray | None = None          # the previewed turn (None: no turn)
        self.shift = np.zeros(3)                   # the previewed move (m)
        self.steps: list = []                      # [{"kind": "rot"|"move"|"free"|"face", "axis": "x"|..., "value": deg | mm | [mm]*3, "text"}]
        self.base = (np.eye(3), np.zeros(3), [])   # the preview when the drag in progress started
        self.live: str | None = None               # what the drag in progress does
        self.readout: QLabel | None = None
        self.rings_on = True
        self.suspended = False                     # the preview is hidden while a face is picked in the stored orientation
        self.apply_job = None                      # the job rebuilding the geometry; the preview stays until it is done
        self.pivot = np.zeros(3)
        self.size = 1.0
        self.target: dict | None = None            # None: the whole model; else {"id": volume, "name": body name}

    # ------------------------------------------------------------------ the previewed change
    @property
    def pending(self) -> bool:
        return self.R is not None or bool(np.abs(self.shift).max() > 1e-12)

    @property
    def matrix(self) -> np.ndarray:
        return np.eye(3) if self.R is None else self.R

    def view(self):
        return self.panel.view

    def _show(self):
        v = self.view()
        if v is not None:
            off = self.suspended or not self.pending
            v.gizmo.set_preview(None if off else self.R, self.pivot, None if off else self.shift, self.target["id"] if self.target else None)

    def reapply(self):
        self._show()

    def _norm(self):
        if self.R is not None and np.allclose(self.R, np.eye(3), atol=1e-9):
            self.R = None
        if not self.steps:
            self.R, self.shift = None, np.zeros(3)

    def _step(self, kind: str, axis: str, value):
        """Add a step to the readout (steps of the same kind about the same axis are added)."""
        if self.steps and self.steps[-1]["kind"] == kind and self.steps[-1]["axis"] == axis:
            self.steps[-1]["value"] = (np.asarray(self.steps[-1]["value"]) + np.asarray(value)).tolist() if kind == "free" else self.steps[-1]["value"] + value
            v = np.asarray(self.steps[-1]["value"])
            if not np.abs(v).max() > 1e-9:
                self.steps.pop()
        elif np.abs(np.asarray(value)).max() > 1e-9:
            self.steps.append({"kind": kind, "axis": axis, "value": value if kind != "free" else list(value)})

    def turn(self, axis: str, degrees: float):
        """Turn the preview by `degrees` about the world's x, y or z axis (right-handed), through the middle of what is acted on."""
        a = "xyz".index(axis)
        self.R = rot_about(a, math.radians(degrees)) @ self.matrix
        self._step("rot", axis, float(degrees))
        self.live = None
        self._norm()
        self._changed()

    def move(self, dx_mm: float = 0.0, dy_mm: float = 0.0, dz_mm: float = 0.0):
        """Move the preview by (dx, dy, dz) mm."""
        d = np.array([dx_mm, dy_mm, dz_mm], float)
        self.shift = self.shift + d / 1000.0
        nz = [i for i in range(3) if abs(d[i]) > 1e-12]
        if len(nz) == 1:
            self._step("move", "xyz"[nz[0]], float(d[nz[0]]))
        else:
            self._step("free", "free", d.tolist())
        self.live = None
        self._norm()
        self._changed()

    def set_matrix(self, R, what: str, text: str):
        """The pending turn becomes R (a turn made by aligning a face); the readout says `text`."""
        R = np.asarray(R, float)
        moves = [s for s in self.steps if s["kind"] in ("move", "free")]            # (a move that is pending stays)
        if np.allclose(R, np.eye(3), atol=1e-9):
            self.R, self.steps = None, moves
        else:
            self.R = R
            self.steps = [{"kind": "face", "axis": what, "value": angle_of(R), "text": text}] + moves
        self.live = None
        self._changed()

    def on_handle(self, kind: str, axis: int, value, final: bool):
        """A ring or an arrow is dragged (called by the gizmo): the preview follows. The first call (zero, not final) starts the drag."""
        v = self.view()
        if not final and self.live is None:
            self.base = (self.matrix.copy(), self.shift.copy(), [dict(s) for s in self.steps])
            self.live = f"{kind}:{axis}"
        R0, s0, st0 = self.base
        self.steps = [dict(s) for s in st0]
        if kind == "rot":
            deg = math.degrees(float(value))
            self.R, self.shift = rot_about(axis, float(value)) @ R0, s0.copy()
            self._step("rot", "xyz"[axis], deg)
            text = f"Turning about {'xyz'[axis]}: {deg:+.1f}°"
        elif kind == "move":
            e = np.zeros(3)
            e[axis] = 1.0
            self.R, self.shift = R0.copy(), s0 + e * float(value)
            self._step("move", "xyz"[axis], float(value) * 1000)
            text = f"Moving along {'xyz'[axis]}: {float(value) * 1000:+.1f} mm"
        else:
            d = np.asarray(value, float)
            self.R, self.shift = R0.copy(), s0 + d
            self._step("free", "free", (d * 1000).tolist())
            text = f"Moving: ({d[0] * 1000:+.1f}, {d[1] * 1000:+.1f}, {d[2] * 1000:+.1f}) mm"
        self._norm()
        self._show()
        v.gizmo.show_angle(text)
        self._update_readout(text)
        if final:
            v.gizmo.show_angle(None)
            self.live = None
            self.panel.rebuild()

    def clear(self):
        """Forget the pending change and put the model back in the view."""
        self.R, self.shift, self.steps, self.live = None, np.zeros(3), [], None
        self.suspended = False
        v = self.view()
        if v is not None:
            v.gizmo.set_preview(None, self.pivot, None, self.target["id"] if self.target else None)
            v.gizmo.show_angle(None)

    def _changed(self):
        self._show()
        self.panel.rebuild()

    def suspend(self, on: bool):
        self.suspended = bool(on)
        self._show()

    def set_target(self, body: dict | None):
        """Choose what the gizmo acts on: None = the whole model, or one body {"id", "name"}. A change that is pending is dropped."""
        same = (body or {}).get("id") == (self.target or {}).get("id")
        if same:
            return
        had = self.pending
        self.clear()
        self.panel.align.end()
        self.target = body
        self.panel.note_target(had)
        self.panel.rebuild()
        self.panel.draw_marks()

    # ------------------------------------------------------------------ readout
    @staticmethod
    def _describe(s: dict) -> str:
        if s.get("text"):
            return s["text"]
        if s["kind"] == "rot":
            return f"turn {s['axis']} {s['value']:+.1f}°"
        if s["kind"] == "move":
            return f"move {s['axis']} {s['value']:+.1f} mm"
        v = s["value"]
        return f"move ({v[0]:+.1f}, {v[1]:+.1f}, {v[2]:+.1f}) mm"

    def text(self) -> str:
        if self.live:
            return "Turning or moving…"
        who = "the whole model" if self.target is None else self.target["name"]
        if not self.steps:
            return f"Nothing changed yet. Drag a ring (turn), an arrow (move) or the ball (move freely) in the 3D view, or use the buttons. Acting on: {who}."
        parts = [self._describe(s) for s in self.steps]
        tot = f" (in all: turn {angle_of(self.matrix):.1f}°, move {np.linalg.norm(self.shift) * 1000:.1f} mm)" if len(self.steps) > 1 else ""
        return f"On {who}: " + ", then ".join(parts) + tot

    def _update_readout(self, text: str | None = None):
        if self.readout is not None:
            try:
                self.readout.setText(text or self.text())
            except RuntimeError:                # (the label was deleted by a rebuild)
                self.readout = None

    def angles(self) -> dict:
        """The turned angle per axis (the sum of the turns about each; the order matters for the picture, not for this list)."""
        out = {"x": 0.0, "y": 0.0, "z": 0.0}
        for s in self.steps:
            if s["kind"] == "rot":
                out[s["axis"]] += s["value"]
        return out

    def move_mm(self) -> list:
        return (self.shift * 1000).round(6).tolist()

    # ------------------------------------------------------------------ to the engine
    def apply(self):
        """Apply: the engine rebuilds the geometry turned and moved (a job). The preview stays in the view until it is done."""
        if not self.pending:
            raise ValueError("Nothing to apply: turn or move the model first (drag a ring or an arrow, or use the buttons)")
        pr = self.panel.engine.project()
        R, s = self.R, self.shift.copy()
        job = pr.transform_geometry(matrix=None if R is None else R.tolist(), shift=s.tolist() if np.abs(s).max() > 1e-12 else None,
                                    bodies=[self.target["name"]] if self.target else None)
        self.apply_job = job
        self.R, self.shift, self.steps, self.live = None, np.zeros(3), [], None
        self.panel.align.after_apply()
        self.panel.rebuild()
        return job

    def cancel(self):
        self.clear()
        self.panel.align.end()
        self.panel.rebuild()

    def reset(self):
        pr = self.panel.engine.project()
        self.clear()
        self.panel.align.end()
        job = pr.rotate_geometry(reset=True)
        self.apply_job = job
        self.panel.rebuild()
        return job

    def preview_done(self, busy: bool):
        """The geometry was rebuilt (or the job failed): the previewed change is part of it now, so the preview goes."""
        j = self.apply_job
        if j is not None and not busy and j.status in ("done", "failed", "cancelled"):
            self.apply_job = None
            v = self.view()
            if v is not None and not self.pending:
                v.gizmo.set_preview(None, self.pivot, None, self.target["id"] if self.target else None)

    # ------------------------------------------------------------------ the widgets
    def build(self, v: QVBoxLayout, g: dict, busy: bool, has_rotation: bool, bodies: list | None = None, can_edit: bool = True):
        p = self.panel
        v.addWidget(_h("Orientation and position"))
        v.addWidget(_muted("Turn and move the parts with the gizmo in the 3D view, or with the buttons and fields below, to line them up with the axes "
                           "(for example the nose along +x, the top along +z)" + (" · changed" if has_rotation else "") +
                           ". Nothing is rebuilt until you press Apply."))
        if getattr(p, "target_note", ""):
            v.addWidget(notice("info", p.target_note, "geometry.target.changed"))
        hint = _muted(f"Drag a ring to turn, an arrow to move along an axis, the white ball to move freely (x red · y green · z blue). "
                      f"Hold Shift to snap to {SNAP_DEG:g}° steps and round distances.")
        wid(hint, "geometry.rings.hint")
        hint.setToolTip(HINT)
        v.addWidget(hint)
        rings = wid(QPushButton("Hide gizmo" if self.rings_on else "Show gizmo"), "geometry.rings")
        rings.setObjectName("ghostBorder")
        rings.setCheckable(True)
        rings.setChecked(self.rings_on)
        rings.setToolTip("Show or hide the rings and arrows in the 3D view that turn and move the model when dragged")
        rings.clicked.connect(self.toggle_rings)
        # what the gizmo acts on
        who = wid(QLabel(f"The gizmo acts on: <b>{'the whole model' if self.target is None else self.target['name']}</b>"), "geometry.target.label")
        who.setStyleSheet(f"background: {theme.tokens()['panel2']};")
        who.setWordWrap(True)
        v.addWidget(who)
        row = QHBoxLayout()
        allb = wid(QPushButton("Move all"), "geometry.target.all")
        allb.setObjectName("primary" if self.target is None else "ghostBorder")
        allb.setToolTip("Turn and move the whole model together (every body)")
        allb.clicked.connect(lambda: self.set_target(None))
        row.addWidget(allb)
        row.addWidget(rings)
        v.addLayout(row)
        v.addWidget(p.align.start_button(busy))
        p.align.build(v, busy)
        grid = QGridLayout()
        grid.setSpacing(4)
        for r, ax in enumerate("xyz"):
            for c, deg in enumerate((-90, 90)):
                b = wid(QPushButton(f"{ax.upper()} {'−' if deg < 0 else '+'}90°"), f"geometry.turn.{ax}{'-' if deg < 0 else '+'}90")
                b.setObjectName("ghostBorder")
                b.setEnabled(not busy)
                b.setToolTip(f"Turn {deg:+d}° about the {ax} axis (right-handed): shown in the 3D view, applied with Apply"
                             if not busy else "Wait: the geometry is being rebuilt")
                b.clicked.connect(lambda _=False, ax=ax, deg=deg: self.turn(ax, deg))
                grid.addWidget(b, r, c)
        v.addLayout(grid)
        row = QHBoxLayout()
        p.axis = wid(QComboBox(), "geometry.turn.axis")
        p.axis.addItems(["x", "y", "z"])
        p.axis.setCurrentText("z")
        p.axis.setToolTip("Axis to turn about")
        p.deg = wid(QLineEdit("45"), "geometry.turn.degrees")
        p.deg.setValidator(QDoubleValidator(-360, 360, 4, p.deg))
        p.deg.setToolTip("Angle in degrees (right-handed about the axis; negative turns the other way)")
        p.deg.setPlaceholderText("angle")
        go = wid(QPushButton("Turn by this angle"), "geometry.turn.apply")
        go.setObjectName("ghostBorder")
        go.setEnabled(not busy)
        go.setToolTip("Turn by the angle about the axis chosen on the left (preview first)" if not busy else "Wait: the geometry is being rebuilt")
        go.clicked.connect(lambda: self.turn(p.axis.currentText(), float(p.deg.text() or 0)))
        row.addWidget(QLabel("Axis"))
        row.addWidget(p.axis)
        row.addWidget(p.deg, 1)
        row.addWidget(QLabel("°"))
        v.addLayout(row)
        v.addWidget(go)
        row = QHBoxLayout()
        row.addWidget(QLabel("Move by"))
        p.move_fields = []
        for ax in "xyz":
            f = wid(QLineEdit("0"), f"geometry.move.d{ax}")
            f.setValidator(QDoubleValidator(-1e6, 1e6, 4, f))
            f.setToolTip(f"Distance to move along {ax}, in mm (negative moves the other way)")
            f.setPlaceholderText(f"d{ax}")
            f.setMinimumWidth(40)
            p.move_fields.append(f)
            row.addWidget(QLabel(ax))
            row.addWidget(f, 1)
        row.addWidget(QLabel("mm"))
        v.addLayout(row)
        mv = wid(QPushButton("Move by these"), "geometry.move.apply")
        mv.setObjectName("ghostBorder")
        mv.setEnabled(not busy)
        mv.setToolTip("Move by dx, dy, dz in mm (preview first)" if not busy else "Wait: the geometry is being rebuilt")
        mv.clicked.connect(lambda: self.move(*[float(f.text() or 0) for f in p.move_fields]))
        v.addWidget(mv)
        # the readout, Apply / Cancel / Reset
        self.readout = wid(_muted(self.text()), "geometry.rotate.readout")
        v.addWidget(self.readout)
        if self.pending:
            v.addWidget(notice("info", "<b>Preview:</b> the " + ("parts are" if self.target is None else f"body {self.target['name']} is") +
                               " turned or moved in the 3D view only (the air volume stays). Press <b>Apply</b> to rebuild the geometry this way, "
                               "or Cancel. If you go to another step the change is applied.", "geometry.rotate.note"))
        if self.target is not None and not can_edit:
            v.addWidget(notice("warn", "Moving one body needs a STEP file (an STL or OBJ model cannot be edited): choose <b>Move all</b>.",
                               "geometry.target.note"))
        row = QHBoxLayout()
        ap = wid(QPushButton("Apply"), "geometry.rotate.apply")
        ap.setObjectName("primary")
        ap.setEnabled(self.pending and not busy and (can_edit or self.target is None))
        ap.setToolTip("Rebuild the geometry turned and moved like the preview (takes a moment)" if self.pending and not busy else
                      "Wait: the geometry is being rebuilt" if busy else "Nothing to apply yet: drag a ring or an arrow, or press a button, first")
        ap.clicked.connect(lambda: self._try(self.apply))
        ca = wid(QPushButton("Cancel"), "geometry.rotate.cancel")
        ca.setObjectName("ghostBorder")
        ca.setEnabled(self.pending)
        ca.setToolTip("Throw the preview away: the model goes back to where it was" if self.pending else "Nothing is previewed")
        ca.clicked.connect(self.cancel)
        rs = wid(QPushButton("Reset orientation"), "geometry.turn.reset")
        rs.setObjectName("ghostBorder")
        rs.setEnabled(has_rotation and not busy)
        rs.setToolTip("Back to the orientation and place the CAD file had (rebuilds the geometry)" if has_rotation and not busy else
                      "Wait: the geometry is being rebuilt" if busy else "The whole model has not been turned or moved: it is as the file had it")
        rs.clicked.connect(lambda: self._try(self.reset))
        for b in (ap, ca, rs):
            row.addWidget(b)
        v.addLayout(row)

    def _try(self, fn):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            self.panel.fail(e)

    def toggle_rings(self):
        self.rings_on = not self.rings_on
        self.panel.rebuild()
        self.panel.draw_marks()
