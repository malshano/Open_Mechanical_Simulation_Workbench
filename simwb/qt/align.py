"""Aligning a face to a plane (the Geometry step's "Align a face to a plane..."): pick a face of the model - flat or CURVED (a wing, a smooth
body, an STL: the area-weighted average normal of the face, or the normal AT the click) - then lay it on a coordinate plane, or against ANY
other face: a wall of the air box (chosen by name or clicked), a face of any body (listed by body) ...  The choice becomes a previewed
turn (orient.py), applied with Apply orientation.

Why a list as well as clicks: the air box is much bigger than the model and mostly out of the picture, so its walls are chosen by name."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from .. import orient as engine_orient
from .forms import fmt_num
from .ids import wid
from .orient import _h, _item, _muted, ink_label, notice

PLANES = (("XY plane", ("+z", "-z")), ("YZ plane", ("+x", "-x")), ("XZ plane", ("+y", "-y")))
FACE_COLOR = "#ff9f1c"        # the face being turned (the view's own selection colour)
REF_COLOR = "#4cc9f0"          # the plane it is laid against


def _minus(t: str) -> str:
    return t.replace("-", "−")


class AlignController:
    def __init__(self, panel):
        self.p = panel
        self._reset()
        self.saved: dict | None = None

    def _reset(self):
        self.pick = False                  # waiting for the face to turn
        self.face: dict | None = None      # engine.align_info of the face to turn (+ "click")
        self.mode = "average"              # which direction of a curved face: "average" | "point"
        self.target: str | None = None     # the coordinate plane chosen (+x ... -z)
        self.ref_pick = False              # waiting for a click on the face to lay it against
        self.ref: dict | None = None       # {"info", "away", "mode", "what", "label"}
        self.body: int | None = None       # the body whose faces are listed
        self.notice: tuple | None = None   # (kind, text): the message shown in the align box
        self.base = np.eye(3)              # the turn pending when the alignment started (the picked face is in the stored orientation)

    # ------------------------------------------------------------------ state for the panel and the API
    @property
    def active(self) -> bool:
        return self.pick or self.face is not None

    @property
    def picking(self) -> bool:
        return self.pick or self.ref_pick

    def describe(self) -> dict:
        f, r = self.face, self.ref
        return {"picking_face": self.pick, "picking_reference": self.ref_pick, "face": f and f["face"],
                "curved": bool(f and f.get("curved")), "mode": self.mode, "target": self.target,
                "direction": f and self.normal(), "reference": r and {k: v for k, v in r.items() if k != "info"} or None,
                "reference_direction": r and self.ref_normal(), "notice": self.notice,
                "pending": self.p.orient.pending}

    def normal(self) -> list:
        f = self.face
        return f["point_normal"] if self.mode == "point" and f.get("point_normal") else f["normal"]

    def ref_normal(self) -> list:
        r = self.ref
        i = r["info"]
        return i["point_normal"] if r["mode"] == "point" and i.get("point_normal") else i["normal"]

    def _say(self, kind: str, text: str):
        self.notice = (kind, text)

    # ------------------------------------------------------------------ start / end
    def start(self):
        """Align a face to a plane...: wait for a click on a face of the model."""
        o = self.p.orient
        o.set_target(None)                                        # (a face is aligned by turning the whole model)
        self.end(keep_preview=True)
        self.base = o.matrix.copy()
        o.suspend(True)                                           # (faces are clicked where they are in the file's orientation)
        self.pick = True
        self._say("info", "<b>Click a face of the model</b> in the 3D view. Any face will do: a curved face (a wing, a smooth body, an STL mesh) "
                  "is aligned by its average direction, or by the direction at the point you click.")
        self.p.view.set_pick_mode("face")
        self.p.view.set_selection([])
        self.p.rebuild()

    def end(self, keep_preview: bool = False):
        """Leave the alignment: forget the faces, put the air box and the camera back as they were."""
        v = self.p.view
        was = self.active or self.ref_pick or self.saved is not None
        self._restore_air()
        self._reset()
        if v is not None:
            v.set_pick_mode(None)
            v.set_selection([])
            v.set_face_tints({})
            v.gizmo.set_marker(None, None, 0)
        if was and not keep_preview:
            self.p.orient.suspend(False)

    def after_apply(self):
        self.end(keep_preview=True)
        self.p.orient.suspend(False)

    # ------------------------------------------------------------------ the first pick: the face to turn
    def face_picked(self, fid: int):
        """A face was clicked in the 3D view: the face to turn, or (while choosing the plane) the face to lay it against."""
        pr = self.p.engine.project()
        h = self.p.view.last_hit
        point = h["point"] if h and h.get("kind") == "face" and h.get("face") == fid else None
        if self.ref_pick:
            self.choose_ref_face(fid, point)
        elif self.pick:
            self.choose_face(fid, point)

    def choose_face(self, fid: int, point=None):
        pr = self.p.engine.project()
        try:
            info = pr.align_info(fid, point)
        except Exception as e:  # noqa: BLE001
            self._say("bad", f"<b>Face {fid} cannot be aligned.</b> {e}")
            self.p.view.set_selection([])
            self.p.rebuild()
            return
        info["click"] = info.get("click") or point
        self.face, self.pick, self.target = info, False, None
        self.mode = "average" if info.get("average_defined", True) else "point"
        if info.get("curved"):
            self._say("info", f"<b>Face {fid} is curved</b> (a wing, a smooth body or a mesh): its <b>average direction</b> is used, "
                      f"{_minus(info['nearest'])} now. To use the direction at the point you clicked instead, choose <b>Normal at the click</b>."
                      if info.get("average_defined", True) else
                      f"<b>Face {fid} curves right round</b>, so it has no single average direction: the direction at the point you clicked is used.")
        else:
            self.notice = None
        self.p.view.set_selection([fid])
        self.p.view.set_pick_mode("face")
        self.p.orient.suspend(False)                              # (the preview of an earlier turn shows again, the face is in it)
        self.draw_marker()
        self.p.rebuild()

    def set_mode(self, mode: str):
        if not self.face or mode not in ("average", "point"):
            return
        if mode == "point" and not self.face.get("point_normal"):
            raise ValueError("There is no click on this face: click the face in the 3D view to use the normal at that point")
        self.mode = mode
        if self.target:
            self.choose_plane(self.target)
        elif self.ref:
            self.recompute()
        self.draw_marker()
        self.p.rebuild()

    # ------------------------------------------------------------------ choosing the plane
    def choose_plane(self, t: str):
        if not self.face:
            raise ValueError("Pick a face first (Align a face to a plane…)")
        if t not in engine_orient.AXES:
            raise ValueError(f"The plane's direction must be one of {', '.join(engine_orient.AXES)}")
        self.target, self.ref = t, None
        self._set_turn(np.asarray(engine_orient.AXES[t], float), f"face {self.face['face']} laid on {_minus(t)}")

    def _set_turn(self, target_dir, text: str):
        n = self.base @ np.asarray(self.normal(), float)
        D = engine_orient.align_matrix(n, target_dir)
        if np.allclose(D, np.eye(3), atol=1e-9):
            self.p.orient.clear()
            self._say("ok", f"<b>Nothing to turn:</b> {text.split(' laid')[0].capitalize()} already points that way.")
        else:
            self.notice = None
            self.p.orient.set_matrix(D @ self.base, "face", text)
        self.draw_marker()
        self.p.rebuild()

    # ------------------------------------------------------------------ choosing a plane that is not a coordinate plane
    def _save_air(self):
        """Show the air box (translucent) and fit the view to it while a reference is picked, so its walls can be clicked."""
        w = self.p.win
        if w is None or self.saved is not None or not w.act_air.isVisible():
            return
        self.saved = {"air": bool(w.chk_air.isChecked()), "camera": self.p.view.camera()}
        if not self.saved["air"]:
            w.chk_air.setChecked(True)                           # (shows it and fits the view to the whole air box and the model)
        else:
            self.p.view.fit()

    def _restore_air(self):
        s, self.saved = self.saved, None
        w = self.p.win
        if s is None or w is None:
            return
        if w.chk_air.isChecked() != s["air"]:
            w.chk_air.setChecked(s["air"])
        c = s["camera"]
        self.p.view.set_camera(position=c["position"], focal_point=c["focal_point"], view_up=c["view_up"], view_angle=c["view_angle"])

    def start_ref_pick(self):
        if not self.face:
            raise ValueError("Pick a face first (Align a face to a plane…)")
        self.ref_pick = True
        self._save_air()
        self.p.orient.suspend(True)
        self.p.view.set_pick_mode("face")
        has_air = self.saved is not None
        self._say("info", "<b>Click the face to lay it against</b> in the 3D view: a face of any part (flat or curved)"
                  + (", or a wall of the air box, which is shown now. The walls and the bodies are also in the lists below, so you need not hit them." if has_air
                     else ". The bodies and their faces are also in the lists below."))
        self.p.rebuild()

    def choose_ref_face(self, fid: int, point=None, what: str = "face"):
        pr = self.p.engine.project()
        try:
            info = pr.ref_info(fid, exclude=self.face["face"], point=point)
        except Exception as e:  # noqa: BLE001
            self._say("bad", f"<b>Face {fid} cannot be the plane.</b> {e}")
            self.p.rebuild()
            return
        label = self._ref_label(info)
        self.ref = {"info": info, "away": False, "mode": "average" if info.get("average_defined", True) else "point", "what": info["kind"],
                    "label": label, "face": fid}
        info["click"] = info.get("click") or point
        self.ref_pick, self.target = False, None
        self.p.orient.suspend(False)
        self.p.view.set_face_tints({fid: REF_COLOR})
        self.p.view.set_selection([self.face["face"]])
        self.recompute()
        self.p.rebuild()

    def _ref_label(self, info: dict) -> str:
        if info["kind"] == "wall":
            for w in self.p.engine.project().air_walls():
                if w["face"] == info["face"]:
                    return f"air box: {w['name']}"
            return f"air box wall {info['face']}"
        return f"face {info['face']}"

    def choose_body(self, bid: int | None):
        self.body = bid
        self.p.rebuild()

    def set_side(self, away: bool):
        if not self.ref:
            raise ValueError("Choose the plane first: an air box wall, a body's face or a face clicked in the 3D view")
        self.ref["away"] = bool(away)
        self.recompute()

    def set_ref_mode(self, mode: str):
        if not self.ref:
            return
        if mode == "point" and not self.ref["info"].get("point_normal"):
            raise ValueError("There is no click on that face: click it in the 3D view to use the normal at that point")
        self.ref["mode"] = mode
        self.recompute()

    def recompute(self):
        r = self.ref
        w = np.asarray(self.ref_normal(), float)
        if r["info"]["kind"] != "wall":                           # (a face of the model moves with the preview turn; the air box does not)
            w = self.base @ w
        t = -w if r["away"] else w
        self._set_turn(t, f"face {self.face['face']} {'facing away from' if r['away'] else 'laid against'} {r['label']}")
        self.p.view.set_face_tints({r["face"]: REF_COLOR})

    # ------------------------------------------------------------------ the arrow on the picked face
    def draw_marker(self):
        v = self.p.view
        if v is None:
            return
        if not self.face:
            v.gizmo.set_marker(None, None, 0)
            return
        pt = self.face["click"] if self.mode == "point" and self.face.get("click") is not None else self.face["point"]
        size = self.p.orient.size or 1.0
        v.gizmo.set_marker(pt, self.normal(), 0.35 * size)

    # ------------------------------------------------------------------ the widgets
    def start_button(self, busy: bool) -> QPushButton:
        b = wid(QPushButton("⬓  Align a face to a plane…"), "geometry.align.start")
        b.setObjectName("ghostBorder")
        b.setEnabled(not busy)
        b.setToolTip("Click a face of the model (flat or curved), then choose the plane to lay it on: a coordinate plane, a wall of the air "
                     "box or another face" if not busy else "Wait: the geometry is being rebuilt")
        b.clicked.connect(self.start)
        return b

    def build(self, v: QVBoxLayout, busy: bool):
        if self.notice:
            v.addWidget(notice(*self.notice))
        a = self.face
        if not a:
            return
        box = _item()
        b = QVBoxLayout(box)
        b.setContentsMargins(8, 6, 8, 6)
        b.setSpacing(5)
        near = engine_orient.nearest_axis(self.normal())
        kind = "curved, average direction" if a.get("curved") and self.mode == "average" else "curved, direction at the click" if a.get("curved") else "flat"
        lab = ink_label(f"<b>Face {a['face']}</b> ({fmt_num(a['area_m2'] * 1e6)} mm², {kind}) points <b>{_minus(near)}</b> now "
                     f"(shown by the yellow arrow; ★ = the nearest plane). Choose the plane to lay it on:", "geometry.align.face")
        b.addWidget(lab)
        if a.get("curved"):
            row = QHBoxLayout()
            for key, text, tip in (("average", "Average direction", "Use the average of the surface's directions over the whole face (a wing: roughly its chord plane)"),
                                   ("point", "Normal at the click", "Use the surface normal at the point you clicked (a wing: the local slope there)")):
                m = wid(QPushButton(text), f"geometry.align.mode.{key}")
                m.setObjectName("seg")
                m.setCheckable(True)
                m.setChecked(self.mode == key)
                ok = key == "average" and a.get("average_defined", True) or key == "point" and bool(a.get("point_normal"))
                m.setEnabled(ok)
                m.setToolTip(tip if ok else ("This face curves right round: it has no average direction" if key == "average"
                                             else "Click the face in the 3D view (rather than choosing it by number) to have a point"))
                m.clicked.connect(lambda _=False, key=key: self._try(lambda: self.set_mode(key)))
                row.addWidget(m)
            row.addStretch(1)
            b.addLayout(row)
        for plane, targets in PLANES:
            row = QHBoxLayout()
            row.addWidget(QLabel(plane))
            for t in targets:
                btn = wid(QPushButton(f"facing {_minus(t)}{' ★' if t == near else ''}"), f"geometry.align.to.{t}")
                btn.setObjectName("primary" if self.target == t else "ghostBorder")
                btn.setEnabled(not busy)
                btn.setToolTip(f"Lay the face on the {plane}, facing {_minus(t)}: shown in the 3D view, applied with Apply orientation")
                btn.clicked.connect(lambda _=False, t=t: self._try(lambda: self.choose_plane(t)))
                row.addWidget(btn)
            b.addLayout(row)
        b.addWidget(_h("…or against another face"))
        b.addWidget(_muted("Lay it against the plane of any other face: a wall of the air box, a face of a body (flat or curved), including contact faces."))
        wb = wid(QPushButton("Click the face in the 3D view…" if self.ref_pick else "Pick the plane in the 3D view"), "geometry.align.wall")
        wb.setObjectName("primary" if self.ref_pick else "ghostBorder")
        wb.setToolTip("Then click the face to line this one up with. The air box is shown while you pick, and the view fits it.")
        wb.clicked.connect(lambda: self._try(self.start_ref_pick))
        b.addWidget(wb)
        self._lists(b, busy)
        if self.ref:
            self._ref_box(b)
        done = wid(QPushButton("Done"), "geometry.align.done")
        done.setObjectName("ghostBorder")
        done.setToolTip("Stop aligning (a turn that is previewed stays until you Apply or Cancel it)")
        done.clicked.connect(self._done)
        b.addWidget(done, 0, Qt.AlignLeft)
        v.addWidget(box)

    def _done(self):
        self.end()
        self.p.rebuild()

    def _try(self, fn):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            self.p.fail(e)

    def _lists(self, b: QVBoxLayout, busy: bool):
        pr = self.p.engine.project()
        walls = pr.air_walls()
        if walls:
            b.addWidget(_muted("Air box walls (by where they are; the flow runs from upstream to downstream):"))
            row = None
            for i, w in enumerate(walls):
                if i % 2 == 0:
                    row = QHBoxLayout()
                    b.addLayout(row)
                btn = wid(QPushButton(f"Air box: {w['name']}"), f"geometry.align.airwall.{w['face']}")
                btn.setObjectName("primary" if self.ref and self.ref["face"] == w["face"] else "ghostBorder")
                btn.setToolTip(f"Lay the face against this wall of the air box (face {w['face']}, its outside faces {_minus(w['nearest'])}); "
                               "it is highlighted in the 3D view")
                btn.clicked.connect(lambda _=False, f=w["face"]: self._try(lambda: self.choose_ref_face(f, None, "wall")))
                row.addWidget(btn)
        bodies = [bd for bd in self.p.engine.state()["body_list"] if bd["role"] == "solid"]
        if bodies:
            b.addWidget(_muted("Bodies (choose one, then one of its faces; or click a face of it in the 3D view):"))
            row = None
            for i, bd in enumerate(bodies):
                if i % 2 == 0:
                    row = QHBoxLayout()
                    b.addLayout(row)
                btn = wid(QPushButton(bd["name"]), f"geometry.align.body.{bd['id']}")
                btn.setObjectName("primary" if self.body == bd["id"] else "ghostBorder")
                btn.setToolTip(f"List the faces of {bd['name']}: choose one to lay the face against")
                btn.clicked.connect(lambda _=False, i=bd["id"]: self.choose_body(None if self.body == i else i))
                row.addWidget(btn)
            if self.body is not None:
                faces = pr.body_faces(self.body)
                shown = [f for f in faces if f["face"] != self.face["face"]][:10]
                b.addWidget(_muted(f"Faces of {next((x['name'] for x in bodies if x['id'] == self.body), 'the body')}, largest first"
                                   + (f" (the largest {len(shown)} of {len(faces)})" if len(faces) > len(shown) + 1 else "") + ":"))
                for f in shown:
                    btn = wid(QPushButton(f"Face {f['face']} · {fmt_num(f['area_m2'] * 1e6)} mm² · faces {_minus(f['nearest'])}"
                                          + (" · curved" if f["curved"] else "") + (" · contact" if f["contact"] else "")),
                              f"geometry.align.bodyface.{f['face']}")
                    btn.setObjectName("primary" if self.ref and self.ref["face"] == f["face"] else "ghostBorder")
                    btn.setToolTip("Lay the face against this one" + (": its average direction is used (it is curved)" if f["curved"] else ""))
                    btn.clicked.connect(lambda _=False, i=f["face"]: self._try(lambda: self.choose_ref_face(i, None)))
                    b.addWidget(btn)

    def _ref_box(self, b: QVBoxLayout):
        r = self.ref
        i = r["info"]
        wall = i["kind"] == "wall"
        near = engine_orient.nearest_axis(self.ref_normal())
        lab = ink_label(f"Plane: <b>{r['label']}</b>, its outside faces <b>{_minus(near)}</b>"
                     + (" (curved: average direction)" if i.get("curved") and r["mode"] == "average" else " (curved: direction at the click)" if i.get("curved") else ""),
                         "geometry.align.plane")
        b.addWidget(lab)
        if i.get("curved"):
            row = QHBoxLayout()
            for key, text in (("average", "Average direction"), ("point", "Normal at the click")):
                m = wid(QPushButton(text), f"geometry.align.refmode.{key}")
                m.setObjectName("seg")
                m.setCheckable(True)
                m.setChecked(r["mode"] == key)
                ok = key == "average" and i.get("average_defined", True) or key == "point" and bool(i.get("point_normal"))
                m.setEnabled(ok)
                m.setToolTip("Which direction of the curved plane face to lay against" if ok else "Click that face in the 3D view to have a point on it")
                m.clicked.connect(lambda _=False, key=key: self._try(lambda: self.set_ref_mode(key)))
                row.addWidget(m)
            row.addStretch(1)
            b.addLayout(row)
        row = QHBoxLayout()
        for away, label, tip in ((False, "facing the wall" if wall else "Same direction",
                                  "The face lies against the wall" if wall else "The face points the same way as the plane face"),
                                 (True, "facing away" if wall else "Face to face",
                                  "The face looks away from the wall" if wall else "The face points the opposite way: face to face")):
            btn = wid(QPushButton(label), f"geometry.align.{'away' if away else 'same'}")
            btn.setObjectName("primary" if r["away"] == away else "ghostBorder")
            btn.setCheckable(False)
            btn.setToolTip(tip)
            btn.clicked.connect(lambda _=False, away=away: self._try(lambda: self.set_side(away)))
            row.addWidget(btn)
        b.addLayout(row)
