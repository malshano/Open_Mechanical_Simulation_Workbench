"""The geometry step panel: the CAD file (import, replace, units), its orientation (turn about an axis, align a face to a plane), the parts.

The same operations as the web version, on the same engine calls: rotate_geometry, align_info / align_face, import_step."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget
from PySide6.QtGui import QDoubleValidator

from ... import project as P
from .. import theme
from ..ids import wid
from .common import _h, _muted, _Panel, clear_layout
from ..align import AlignController
from ..forms import fmt_num
from ..orient import OrientController, _item, _plain

SCALES = [(1.0, "×1 (STL/OBJ: mm)"), (0.001, "×0.001"), (25.4, "inch → mm  ×25.4"), (100.0, "×100"), (1000.0, "×1000")]
AXES = ("+x", "-x", "+y", "-y", "+z", "-z")
PLANES = (("XY plane", ("+z", "-z")), ("YZ plane", ("+x", "-x")), ("XZ plane", ("+y", "-y")))


def nearest_axis(normal) -> str:
    n = np.asarray(normal, float)
    k = int(np.argmax(np.abs(n)))
    return ("+" if n[k] >= 0 else "-") + "xyz"[k]


class GeometryPanel(_Panel):
    def __init__(self, engine, parent=None):
        super().__init__(engine, parent)
        self._sig = None
        self._st = None
        self.orient = OrientController(self)
        self.align = AlignController(self)
        self.target_note = ""
        self.holder = QVBoxLayout()
        self.holder.setSpacing(10)
        self.v.addLayout(self.holder)
        self.v.addStretch(1)

    # old names (the tests and the API read them)
    @property
    def align_pick(self) -> bool:
        return self.align.pick

    @property
    def note(self) -> str:
        return _plain(self.align.notice[1]) if self.align.notice else ""

    def fail(self, e):
        self._fail(e)

    def rebuild(self):
        """Draw the panel again now (a preview turn or an alignment choice changed what it shows)."""
        self._sig = None
        if self.engine.pid:
            self.refresh(self.engine.state())

    # ------------------------------------------------------------------ picking (the face to align)
    def bind(self, win):
        super().bind(win)
        win.view.facePicked.connect(self._face_picked)

    def shown(self):
        self.view.set_pick_mode("face" if self.align.picking or self.align.face else None)

    def left(self):
        if self.orient.pending:                                   # (as the web version does: a previewed turn is applied on leaving)
            try:
                self.orient.apply()
            except Exception as e:  # noqa: BLE001
                self._fail(e)
        self.align.end()
        self.view.gizmo.clear()
        self.view.set_pick_mode(None)
        self.view.set_selection([])

    def _face_picked(self, fid, add):
        if self.win is None or self.win.step != "geometry":
            return
        if self.align.picking:
            self.align.face_picked(int(fid))

    def note_target(self, had_pending: bool):
        """What the gizmo acts on changed: say so (a preview that was pending went)."""
        self.target_note = ("The preview was cancelled because the gizmo now acts on something else." if had_pending else "")

    def draw_marks(self):
        """The gizmo round what it acts on, the previewed change and the arrow on the face being aligned (after the model was drawn)."""
        st, v = self._st, self.view
        if v is None:
            return
        g = (st or {}).get("geometry") or {}
        info = g.get("info") or {}
        bb = info.get("parts_bbox") or info.get("bbox")
        busy = any((j or {}).get("status") == "running" for j in ((st or {}).get("jobs") or {}).values())
        o = self.orient
        if o.target is not None:
            b = next((b for b in (st or {}).get("body_list", []) if b["id"] == o.target["id"] and b["name"] == o.target["name"]), None)
            if b is None:                                          # (the body is gone or renamed: back to the whole model)
                o.target = None
            else:
                bb = b["bbox"]
        if bb:
            o.size = max(bb[3] - bb[0], bb[4] - bb[1], bb[5] - bb[2])
            o.pivot = np.asarray(g.get("pivot") if (o.target is None and g.get("pivot")) else [(bb[k] + bb[k + 3]) / 2 for k in range(3)], float)
        o.preview_done(busy)
        if g.get("status") == "ready" and bb and o.rings_on and not busy:
            if v.gizmo.drag is None:
                v.gizmo.set(o.pivot, o.size, o.on_handle, o.target["id"] if o.target else None)
        else:
            v.gizmo.clear()
        o.reapply()
        self.align.draw_marker()

    # ------------------------------------------------------------------ the panel
    def refresh(self, st):
        if not st:
            return
        self._st = st
        g = st["geometry"]
        busy = any((j or {}).get("status") == "running" for j in (st.get("jobs") or {}).values())
        o, al = self.orient, self.align
        sig = repr([self.target_note, o.target, st["study"], g.get("status"), g.get("rotation"), g.get("original_name"), g.get("error"), busy,
                    [(b["id"], b["name"], b["role"]) for b in st["body_list"]], al.describe(),
                    o.R is not None and o.R.round(6).tolist(), o.shift.round(7).tolist(), [(s_["kind"], s_["axis"], str(np.round(s_["value"], 3))) for s_ in o.steps],
                    o.rings_on,
                    theme.effective(), sorted(self.win.hidden_bodies) if self.win is not None else []])
        if sig == self._sig:
            return
        self._sig = sig
        clear_layout(self.holder)
        spec = st["study_spec"]
        self.holder.addWidget(_h("Geometry"))
        if g.get("error"):
            self.holder.addWidget(_muted(f"The last import or edit failed: {g['error']}"))
        if g.get("status") == "ready":
            self._file_card(st, g, busy)
            self._bodies(st)
        else:
            self._import_card(spec, busy)
        nxt = wid(QPushButton("Next: setup →"), "geometry.next")
        nxt.setObjectName("primary")
        nxt.setEnabled(g.get("status") == "ready")
        nxt.setToolTip("Go to the next step" if g.get("status") == "ready" else "Import a CAD file first")
        nxt.clicked.connect(lambda: self.win.go("setup"))
        self.holder.addWidget(nxt)

    # ---- import
    def _import_card(self, spec, busy):
        card = _item()
        v = QVBoxLayout(card)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(8)
        v.addWidget(_muted("Import the CAD file of the part: STEP, STL or OBJ."))
        self.btn = wid(QPushButton("Import CAD file (STEP, STL, OBJ)…"), "geometry.import")
        self.btn.setObjectName("primary")
        self.btn.setEnabled(not busy)
        self.btn.clicked.connect(self._import)
        v.addWidget(self.btn)
        row = QHBoxLayout()
        row.addWidget(QLabel("Scale"))
        self.scale = wid(QComboBox(), "geometry.scale")
        for val, label in SCALES:
            self.scale.addItem(label, val)
        self.scale.setToolTip("Units of the file: STL and OBJ files carry none (read as mm)")
        row.addWidget(self.scale, 1)
        v.addLayout(row)
        exs = spec.get("examples") or []
        if exs:
            v.addWidget(_muted("Or try an example:"))
            for i, e in enumerate(exs):
                b = wid(QPushButton(e), f"geometry.example.{i}")
                b.setObjectName("ghostBorder")
                b.setEnabled(not busy)
                b.setToolTip(f"Import the bundled example {e}")
                b.clicked.connect(lambda _=False, e=e: self._example(e))
                v.addWidget(b)
        self.info = wid(_muted("No geometry yet."), "geometry.info")
        v.addWidget(self.info)
        self.holder.addWidget(card)

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import CAD", "", "CAD files (*.step *.stp *.stl *.obj);;All files (*)")
        if not path:
            return
        try:
            scale = self.scale.currentData() if getattr(self, "scale", None) is not None else 1.0
            self.engine.import_step(path, scale=float(scale or 1.0))
            self.info.setText("Importing… (the part appears here when it is done)")
        except Exception as e:  # noqa: BLE001
            self._fail(e)

    def _example(self, name):
        try:
            self.engine.import_step(str(P.EXAMPLES / name))
        except Exception as e:  # noqa: BLE001
            self._fail(e)

    # ---- the file and its orientation
    def _file_card(self, st, g, busy):
        card = _item()
        v = QVBoxLayout(card)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(8)
        head = QHBoxLayout()
        head.addWidget(QLabel(f"<b>{g.get('original_name') or 'source.step'}</b>"), 1)
        rep = wid(QPushButton("Replace…"), "geometry.import")
        rep.setObjectName("ghostBorder")
        rep.setEnabled(not busy)
        rep.setToolTip("Import another CAD file in place of this one")
        rep.clicked.connect(self._import_replace)
        head.addWidget(rep)
        v.addLayout(head)
        self.info = wid(_muted(f"{st['face_count']} faces, {len(st['body_list'])} bod{'y' if len(st['body_list']) == 1 else 'ies'}"),
                        "geometry.info")
        v.addWidget(self.info)
        can_edit = (g.get("info") or {}).get("source_format") not in ("stl", "obj")
        self.orient.build(v, g, busy, bool(g.get("rotation") or g.get("translation")), st["body_list"], can_edit)
        self.holder.addWidget(card)

    def _import_replace(self):
        path, _ = QFileDialog.getOpenFileName(self, "Replace the CAD file", "", "CAD files (*.step *.stp *.stl *.obj);;All files (*)")
        if path:
            try:
                self.engine.import_step(path)
            except Exception as e:  # noqa: BLE001
                self._fail(e)

    def turn(self, axis=None, degrees=90.0, reset=False):
        """Turn the preview by `degrees` about an axis (or reset the orientation): what the turn buttons do."""
        try:
            if reset:
                self.orient.reset()
            else:
                self.orient.turn(axis, float(degrees))
        except Exception as e:  # noqa: BLE001
            self._fail(e)

    # ---- the parts
    def _bodies(self, st):
        self.holder.addWidget(_h("Bodies"))
        self.holder.addWidget(_muted("Press Move this body to let the gizmo turn and move one body on its own; Move all (above) puts it back on the whole model."))
        from ..viewport import BODY_COLORS
        for i, b in enumerate(st["body_list"]):
            card = _item()
            v = QVBoxLayout(card)
            v.setContentsMargins(10, 8, 10, 8)
            v.setSpacing(4)
            head = QHBoxLayout()
            sw = QLabel("")
            sw.setFixedSize(14, 14)
            col = "#9ebcf2" if b["role"] == "fluid" else BODY_COLORS[i % len(BODY_COLORS)]
            sw.setStyleSheet(f"background: {col}; border: 1px solid {theme.tokens()['line_strong']}; border-radius: 3px;")
            sw.setToolTip("The colour of this body in the 3D view")
            head.addWidget(sw)
            head.addWidget(QLabel(f"<b>{b['name']}</b>"), 1)
            head.addWidget(_muted("air" if b["role"] == "fluid" else "solid"))
            if b["role"] == "solid":
                mv = wid(QPushButton("Moving" if self.orient.target and self.orient.target["id"] == b["id"] else "Move this body"),
                         f"geometry.body.{b['id']}.move")
                mv.setObjectName("primary" if self.orient.target and self.orient.target["id"] == b["id"] else "ghostBorder")
                can = (st["geometry"].get("info") or {}).get("source_format") not in ("stl", "obj")
                mv.setEnabled(can)
                mv.setToolTip("Let the gizmo turn and move just this body, leaving the others where they are" if can else
                              "Moving one body needs a STEP file: an STL or OBJ model is a surface mesh (use Move all)")
                mv.clicked.connect(lambda _=False, b=b: self.orient.set_target({"id": b["id"], "name": b["name"]}))
                head.addWidget(mv)
                eye = wid(QPushButton("Show" if b["id"] in self.win.hidden_bodies else "Hide"), f"geometry.body.{b['id']}.eye")
                eye.setObjectName("ghostBorder")
                eye.setToolTip("Hide or show this part in the 3D view")
                eye.clicked.connect(lambda _=False, bid=b["id"]: self.win.toggle_body(bid))
                head.addWidget(eye)
            v.addLayout(head)
            bb = b["bbox"]
            size = " × ".join(fmt_num((bb[k + 3] - bb[k]) * 1000) for k in range(3))
            v.addWidget(_muted(f"{size} mm · {fmt_num(b['volume_cm3'])} cm³"))
            self.holder.addWidget(card)


def register_actions(ctl):
    """The Geometry step's operations as control-API actions: each drives the same state as the buttons and the mouse."""
    win, R = ctl.win, ctl._reg
    S, N, B = "string", "number", "boolean"

    def gp():
        if not ctl.engine.pid:
            raise ValueError("No study is open")
        win.go("geometry")
        ctl._settle()
        return win.panels["geometry"]

    def state(p=None):
        p = p or win.panels["geometry"]
        o = p.orient
        from ..orient import angle_of
        return {"pending": o.pending, "steps": [{"kind": s["kind"], "axis": s["axis"], "value": s["value"]} for s in o.steps], "angles": o.angles(),
                "move_mm": o.move_mm(), "matrix": None if o.R is None else np.asarray(o.R).round(9).tolist(), "readout": o.text(),
                "turn_degrees": None if o.R is None else round(angle_of(o.R), 4), "acts_on": o.target["name"] if o.target else "all",
                "gizmo": win.view.gizmo.describe(), "align": p.align.describe()}

    def preview(axis, degrees):
        if axis not in ("x", "y", "z"):
            raise ValueError("axis must be x, y or z")
        p = gp()
        p.orient.turn(axis, float(degrees))
        return state(p)

    def move_preview(dx=0.0, dy=0.0, dz=0.0):
        p = gp()
        p.orient.move(float(dx), float(dy), float(dz))
        return state(p)

    def acts_on(body=None, id=None):
        p = gp()
        if body is None and id is None or str(body).lower() in ("all", "model", "whole"):
            p.orient.set_target(None)
        else:
            bs = [b for b in ctl.engine.state()["body_list"] if b["role"] == "solid"]
            b = next((b for b in bs if (id is not None and b["id"] == int(id)) or (body and body.lower() in b["name"].lower())), None)
            if b is None:
                raise ValueError(f"No such body. Bodies: {[(b['id'], b['name']) for b in bs]}")
            p.orient.set_target({"id": b["id"], "name": b["name"]})
        return state(p)

    def handle_drag(handle, amount, shift=False):
        """handle: rot:x | rot:y | rot:z (amount in degrees), move:x | move:y | move:z (mm), free (mm to the right in the view's plane)."""
        p = gp()
        win.update_view()
        g = win.view.gizmo
        if not g.on:
            raise ValueError("The gizmo is not shown (Show gizmo, and wait until the geometry is rebuilt)")
        kind, _, ax = str(handle).partition(":")
        if kind == "rot" and ax in ("x", "y", "z"):
            r = g.simulate_drag("xyz".index(ax), float(amount), bool(shift))
        elif kind == "move" and ax in ("x", "y", "z"):
            r = g.simulate_move("xyz".index(ax), float(amount), bool(shift))
        elif kind == "free":
            r = g.simulate_move(3, float(amount), bool(shift))
        else:
            raise ValueError("handle must be rot:x, rot:y, rot:z, move:x, move:y, move:z or free")
        return {**state(p), "dragged": r}

    def apply():
        p = gp()
        job = p.orient.apply()
        return {**state(p), "job": job.to_dict(log_tail=0)}

    def cancel():
        p = gp()
        p.orient.cancel()
        return state(p)

    R("geometry.rotate_preview", "Turn the PREVIEW of the model by an angle about x, y or z (what the turn buttons do); nothing is rebuilt until "
      "geometry.rotate_apply. Turns add up.", {"axis": (S, "x|y|z", True), "degrees": (N, "right-handed angle", True)}, preview)
    R("geometry.rotate_apply", "Apply the previewed turn (Apply orientation): rebuilds the geometry; a job (use wait).", {}, apply)
    R("geometry.rotate_cancel", "Throw the previewed turn away (Cancel).", {}, cancel)
    R("geometry.gizmo_drag", "Drag a handle of the gizmo in the 3D view with the mouse (synthetic: the press, moves and release go through the "
      "same code as the real mouse): rot:x|rot:y|rot:z turns the preview by the angle (degrees), move:x|move:y|move:z moves it along the axis "
      "(mm), free moves it in the view's plane (mm to the right). Shift snaps to 5 degrees / a round step.",
      {"handle": (S, "rot:x|rot:y|rot:z|move:x|move:y|move:z|free", True), "amount": (N, "degrees (rot) or mm (move, free)", True),
       "shift": (B, "Shift held", False)}, handle_drag)
    R("geometry.ring_drag", "Drag a rotation ring (the same as geometry.gizmo_drag with rot:x ...).",
      {"axis": (S, "x|y|z", True), "degrees": (N, "angle", True), "shift": (B, "Shift held", False)},
      lambda axis, degrees, shift=False: handle_drag(f"rot:{axis}", degrees, shift))
    R("geometry.move_preview", "Move the PREVIEW of the model by dx, dy, dz (mm): what the Move by fields do; applied with geometry.rotate_apply.",
      {"dx": (N, "mm", False), "dy": (N, "mm", False), "dz": (N, "mm", False)}, move_preview)
    R("geometry.acts_on", "Choose what the gizmo turns and moves: the whole model (omit body) or one body (name, or id).",
      {"body": (S, "body name (part of it) or 'all'", False), "id": (N, "body id", False)}, acts_on)
    R("geometry.rings", "Show or hide the rotation rings in the 3D view.", {"visible": (B, "default true", False)},
      lambda visible=True: (gp(), setattr(win.panels["geometry"].orient, "rings_on", bool(visible)), win.panels["geometry"].rebuild(),
                            win.update_view(), state())[-1])
    R("geometry.state", "The previewed turn and the alignment in progress.", {}, lambda: state(gp()))

    # ---- align a face
    def align_start():
        p = gp()
        p.align.start()
        return state(p)

    def align_pick(face, x=None, y=None, z=None):
        p = gp()
        if not (p.align.picking or p.align.face):
            p.align.start()
        win.view.last_hit = None if x is None else {"kind": "face", "face": int(face), "point": [float(x), float(y), float(z)]}
        win.view.click_face(int(face), False)                 # as a click on the face (at the point) would
        return state(p)

    def align_plane(facing):
        p = gp()
        p.align.choose_plane(facing)
        return state(p)

    def align_reference(kind, id=None, name=None, x=None, y=None, z=None):
        p = gp()
        a = p.align
        if not a.face:
            raise ValueError("Pick the face to turn first (geometry.align_pick)")
        pr = ctl.engine.project()
        if kind == "airwall":
            walls = pr.air_walls()
            if not walls:
                raise ValueError("This study has no air box")
            w = next((w for w in walls if (id is not None and w["face"] == int(id)) or (name and name.lower() in w["name"].lower())), None)
            if w is None:
                raise ValueError(f"No such air box wall. Walls: {[(w['face'], w['name']) for w in walls]}")
            a.choose_ref_face(w["face"], None, "wall")
        elif kind == "face":
            win.view.last_hit = None if x is None else {"kind": "face", "face": int(id), "point": [float(x), float(y), float(z)]}
            if not a.ref_pick:
                a.start_ref_pick()
            win.view.click_face(int(id), False)
        elif kind == "body":
            bodies = [b for b in ctl.engine.state()["body_list"] if b["role"] == "solid"]
            b = next((b for b in bodies if (id is not None and b["id"] == int(id)) or (name and name.lower() in b["name"].lower())), None)
            if b is None:
                raise ValueError(f"No such body. Bodies: {[(b['id'], b['name']) for b in bodies]}")
            a.choose_body(b["id"])
        else:
            raise ValueError("kind must be face, airwall or body")
        if a.ref is None and kind != "body":
            raise ValueError(a.notice[1] if a.notice else "That face cannot be the plane")
        return state(p)

    def align_side(away=False):
        p = gp()
        p.align.set_side(bool(away))
        return state(p)

    def align_mode(mode, reference=False):
        p = gp()
        (p.align.set_ref_mode if reference else p.align.set_mode)(mode)
        return state(p)

    def align_end():
        p = gp()
        p.align.end()
        p.rebuild()
        return state(p)

    R("geometry.align_start", "Align a face to a plane...: wait for a click on a face of the model.", {}, align_start)
    R("geometry.align_pick", "Click a face of the model (optionally at a point, in m: the point is used for 'normal at the click' on a curved "
      "face). Picks the face to turn, or the reference while one is being picked.",
      {"face": (N, "face id", True), "x": (N, "x of the click", False), "y": (N, "y", False), "z": (N, "z", False)}, align_pick)
    R("geometry.align_plane", "Lay the picked face on a coordinate plane: previews the turn.", {"facing": (S, "+x|-x|+y|-y|+z|-z (the way the face then points)", True)},
      align_plane)
    R("geometry.align_reference", "Choose what to lay the picked face against: an air box wall (id = its face id, or name such as 'bottom'), a body "
      "(lists its faces) or any face (id, optional click point x,y,z in m).",
      {"kind": (S, "face|airwall|body", True), "id": (N, "face or body id", False), "name": (S, "wall or body name (part of it)", False),
       "x": (N, "click x", False), "y": (N, "click y", False), "z": (N, "click z", False)}, align_reference)
    R("geometry.align_side", "With a reference chosen: the face looks the same way as the plane (away false) or against it (away true).",
      {"away": (B, "face to face / facing away", False)}, align_side)
    R("geometry.align_mode", "For a curved face: use its average direction or the normal at the click.",
      {"mode": (S, "average|point", True), "reference": (B, "for the reference face instead of the picked one", False)}, align_mode)
    R("geometry.align_end", "Leave the alignment (Done).", {}, align_end)
