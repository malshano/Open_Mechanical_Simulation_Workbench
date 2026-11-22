"""Aligning a face to a plane (the Geometry step's "Align a face to a plane..."): pick a face of the model - flat or CURVED (a wing, a smooth
body, an STL: the area-weighted average normal of the face, or the normal AT the click) - then lay it on a coordinate plane, or against ANY
other face: a wall of the air volume (chosen by name or clicked), a face of any body (listed by body) ...  The choice becomes a previewed
turn (orient.py), applied with Apply orientation.

Why a list as well as clicks: the air volume is much bigger than the model and mostly out of the picture, so its walls are chosen by name."""
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
        self.body = None                   # the body (volume id, or a part's name) whose faces are listed
        self.notice: tuple | None = None   # (kind, text): the message shown in the align box
        self.base = np.eye(3)              # the turn pending when the alignment started (the picked face is in the stored orientation)

    # ------------------------------------------------------------------ state for the panel and the API
    @property
    def active(self) -> bool:
        return self.pick or self.face is not None

    @property
    def picking(self) -> bool:
        return self.pick or self.ref_pick

    def step(self) -> int:
        """Where the guided popup is: 0 not aligning, 1 click the surface, 2 click the plane to align it with, 3 check the preview and Apply."""
        if self.pick:
            return 1
        if self.face is None:
            return 0
        return 2 if (self.ref is None and not self.target) else 3

    def back(self):
        """Back in the popup: from step 3 to choosing the plane again, from step 2 to choosing the surface again."""
        s = self.step()
        if s == 3:
            self.ref, self.target = None, None
            self.p.orient.clear()
            self.p.view.set_face_tints({})
            self.start_ref_pick()
        elif s == 2:
            self.p.orient.clear()
            self.start()
        else:
            raise ValueError("There is no step to go back to")

    def _guide(self):
        g = getattr(self.p, "guide", None)
        if g is not None:
            g.refresh()

    def describe(self) -> dict:
        f, r = self.face, self.ref
        return {"step": self.step(), "picking_face": self.pick, "picking_reference": self.ref_pick, "face": f and f["face"],
                "curved": bool(f and f.get("curved")), "mode": self.mode, "target": self.target,
                "direction": f and self.normal(), "reference": r and {k: v for k, v in r.items() if k != "info"} or None,
                "reference_direction": r and self.ref_normal(), "notice": self.notice,
                "pending": self.p.orient.pending}

    def summary(self) -> str:
        """The current choice in one line: Face 7 -> Air volume: top (+z), same direction."""
        f, r = self.face, self.ref
        if self.pick:
            return "Aligning: click the face of the model to align"
        if f is None:
            return ""
        if r is not None:
            how = ("facing away" if r["away"] else "facing the wall") if r["info"]["kind"] == "wall" else ("face to face" if r["away"] else "same direction")
            return f"Face {f['face']} → {r['label'].capitalize() if r['label'].startswith('air') else r['label']}, {how}"
        if self.target:
            return f"Face {f['face']} → the plane facing {_minus(self.target)}"
        if self.ref_pick:
            return f"Face {f['face']} → click the plane to lay it against, or choose a coordinate plane"
        return f"Face {f['face']} points {_minus(engine_orient.nearest_axis(self.normal()))}: choose the plane to lay it on"

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
        self._pointer(True)
        bb = (((self.p._st or {}).get("geometry") or {}).get("info") or {}).get("parts_bbox")
        if bb:                                                    # (the picture is on the model while aligning, whatever else is shown: a duct, an air volume)
            self.p.view.set_focus([bb[0], bb[1], bb[2], bb[3], bb[4], bb[5]])
            self.p.view.fit()
        self._banner("Click the face of the model that you want to align (flat or curved), or press Done", "Done")
        self.p.scroll_top()
        self.p.rebuild()

    def _pointer(self, on: bool):
        """The pointer is a pointing hand over the picture while a face is being picked."""
        v = self.p.view
        if v is None:
            return
        v.pick_cursor = Qt.PointingHandCursor if on else None
        v.iren.setCursor(Qt.PointingHandCursor) if on else v.iren.unsetCursor()

    def _banner(self, text: str | None, button: str | None = None):
        """The instruction in the bar under the 3D view (None: take it away)."""
        bar = getattr(self.p.view, "banner", None)
        if bar is None:
            return
        if text:
            bar.set("align", text, 1, button, self.stop_picking)
        else:
            bar.clear("align")

    def stop_picking(self):
        """Done / Skip in the bar: stop waiting for a click (the face and any previewed turn stay), and put the air volume back as it was."""
        self.pick = self.ref_pick = False
        self._banner(None)
        if self.face is None:
            self.end()
        else:
            self._restore_air()
            self.p.view.set_face_tints({} if not self.ref else {self.ref["face"]: REF_COLOR})
            self.p.orient.suspend(False)
        self.p.rebuild()

    def end(self, keep_preview: bool = False):
        """Leave the alignment: forget the faces, put the air volume and the camera back as they were."""
        v = self.p.view
        was = self.active or self.ref_pick or self.saved is not None
        self._restore_air()
        self._banner(None)
        self._reset()
        self._pointer(False)
        self._guide()
        if v is not None:
            if was:
                v.set_focus(None)
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
        self.start_ref_pick()                                     # (as the web version: the next click chooses the plane)

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
        if self.ref_pick:
            self.ref_pick = False
            self._banner(None)
            self._restore_air()
        self.p.view.set_face_tints({})
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
        """Show the air volume (translucent) while a reference is picked, so its walls can be clicked. The camera stays on the model."""
        w = self.p.win
        if w is None or self.saved is not None or not w.act_air.isVisible():
            return
        self.saved = {"air": bool(w.chk_air.isChecked())}
        if not self.saved["air"]:
            w.chk_air.blockSignals(True)                         # (the switch's own handler would zoom out to the whole air volume)
            w.chk_air.setChecked(True)
            w.chk_air.blockSignals(False)
            self.p.view.set_air_visible(True)

    def _restore_air(self):
        s, self.saved = self.saved, None
        w = self.p.win
        if s is None or w is None:
            return
        if w.chk_air.isChecked() != s["air"]:
            w.chk_air.blockSignals(True)
            w.chk_air.setChecked(s["air"])
            w.chk_air.blockSignals(False)
            self.p.view.set_air_visible(s["air"])

    def start_ref_pick(self):
        if not self.face:
            raise ValueError("Pick a face first (Align a face to a plane…)")
        self.ref_pick = True
        self._save_air()
        self.p.orient.suspend(True)
        self.p.view.set_pick_mode("face")
        pr = self.p.engine.project()
        self._wall_names = {w["face"]: w["name"] for w in pr.air_walls()}
        has_air = self.saved is not None or bool(self._wall_names)
        self._banner("Now click the plane to align it to: a face of the model" + (" or a wall of the air volume (shown now; turn the view to reach another wall)" if has_air else "")
                     + " - or choose a coordinate plane in the panel", "Done")
        self.p.scroll_top()
        self.p.rebuild()

    def hovered(self, fid: int):
        """The pointer is over a face while the plane is being picked: say which one next to the cursor."""
        from PySide6.QtGui import QCursor
        from PySide6.QtWidgets import QToolTip
        if fid < 0 or not (self.ref_pick or self.pick):
            QToolTip.hideText()
            return
        name = getattr(self, "_wall_names", {}).get(fid)
        text = f"Air volume: {name}" if name else (f"Face {fid}: click to align this surface" if self.pick else f"Face {fid}")
        QToolTip.showText(QCursor.pos(), text, self.p.view.iren)

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
        self._banner(None)
        self.p.orient.suspend(False)
        self.p.view.set_face_tints({fid: REF_COLOR})
        self.p.view.set_selection([self.face["face"]])
        self.recompute()
        self.p.rebuild()

    def _ref_label(self, info: dict) -> str:
        if info["kind"] == "wall":
            for w in self.p.engine.project().air_walls():
                if w["face"] == info["face"]:
                    return f"air volume: {w['name']}"
            return f"air volume wall {info['face']}"
        return f"face {info['face']}"

    def entries(self) -> list:
        """The bodies a reference face can be taken from: [{key, wid, name}] - the solids of the study (a flow study's solids are its parts,
        named in the geometry: its own body list holds only the air)."""
        st = self.p.engine.state()
        out = [{"key": b["id"], "wid": str(b["id"]), "name": b["name"]} for b in st["body_list"] if b["role"] == "solid"]
        if not out:
            out = [{"key": p["name"], "wid": f"p{i}", "name": p["name"]} for i, p in enumerate(self.p.engine.project().parts())]
        return out

    def choose_body(self, bid):
        self.body = bid
        self.p.rebuild()

    def set_side(self, away: bool):
        if not self.ref:
            raise ValueError("Choose the plane first: an air volume wall, a body's face or a face clicked in the 3D view")
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
        if r["info"]["kind"] != "wall":                           # (a face of the model moves with the preview turn; the air volume does not)
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
        b.addWidget(_muted("Lay it against the plane of any other face: a wall of the air volume, a face of a body (flat or curved), including contact faces."))
        wb = wid(QPushButton("Click the face in the 3D view…" if self.ref_pick else "Pick the plane in the 3D view"), "geometry.align.wall")
        wb.setObjectName("primary" if self.ref_pick else "ghostBorder")
        wb.setToolTip("Then click the face to line this one up with. The air volume is shown while you pick (the Air box switch of the 3D view), and the view fits it.")
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

    def connect(self, view):
        view.faceHovered.connect(self.hovered)

    def _try(self, fn):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            self.p.fail(e)

    def _lists(self, b: QVBoxLayout, busy: bool):
        pr = self.p.engine.project()
        walls = pr.air_walls()
        if walls:
            b.addWidget(_muted("Air volume walls (by where they are; the flow runs from upstream to downstream):"))
            for w in walls:
                btn = wid(QPushButton(f"Air volume: {w['name']}"), f"geometry.align.airwall.{w['face']}")
                btn.setObjectName("primary" if self.ref and self.ref["face"] == w["face"] else "ghostBorder")
                btn.setToolTip(f"Lay the face against this wall of the air volume (face {w['face']}, its outside faces {_minus(w['nearest'])}"
                               + ("; it is curved: its average direction is used" if w["curved"] else "") + "); it is highlighted in the 3D view")
                btn.clicked.connect(lambda _=False, f=w["face"], pt=w.get("point"): self._try(lambda: self.choose_ref_face(f, pt, "wall")))
                b.addWidget(btn)
        bodies = self.entries()
        if bodies:
            b.addWidget(_muted("Bodies (choose one, then one of its faces; or click a face of it in the 3D view):"))
            for bd in bodies:
                btn = wid(QPushButton(bd["name"]), f"geometry.align.body.{bd['wid']}")
                btn.setObjectName("primary" if self.body == bd["key"] else "ghostBorder")
                btn.setToolTip(f"List the faces of {bd['name']}: choose one to lay the face against")
                btn.clicked.connect(lambda _=False, i=bd["key"]: self.choose_body(None if self.body == i else i))
                b.addWidget(btn)
            if self.body is not None:
                faces = pr.body_faces(self.body)
                shown = [f for f in faces if f["face"] != self.face["face"]][:10]
                b.addWidget(_muted(f"Faces of {next((x['name'] for x in bodies if x['key'] == self.body), 'the body')}, largest first"
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
