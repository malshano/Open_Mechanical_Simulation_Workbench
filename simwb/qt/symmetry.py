"""Symmetry planes in the Setup step: choose which way the plane cuts (across x, y or z, or through a flat face you click), and it is drawn at
once in the 3D view as a translucent sheet with an arrow into the side that is kept. Move it by dragging the arrow along its axis (Shift snaps
to round steps) or by typing its position, flip the side, then press Apply: only then is the geometry rebuilt, in a background job
(simwb.project add_symmetry). The planes that are set have their position, Flip, Remove and, in an external flow, whether the model beyond
the plane is deleted too (update_symmetry / remove_symmetry)."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from .. import symmetry as engine_sym
from .forms import fmt_num
from .ids import wid
from .panels.common import FlowLayout, _h, _muted
from . import theme

HELP = ("A mirror plane cuts the model (or the air volume of an external flow): only the side you keep is meshed and solved (half the "
        "cells for one plane), and the results can show the mirror image again. Use it when the shape AND the loads or the flow are "
        "mirror-symmetric. Choose which way the plane cuts, move it (drag the arrow or type its position), flip it to keep the other side, "
        "then press Apply.")
MAX_PLANES = 3


def plane_axis(pl: dict) -> int:
    """The axis a plane is normal to (0, 1, 2), or -1 for an oblique plane."""
    n = [abs(x) for x in pl["normal"]]
    a = n.index(max(n))
    return a if n[a] > 0.9999 else -1


def keeps(pl: dict) -> str:
    a = plane_axis(pl)
    return f"{'+' if pl['normal'][a] > 0 else '−'}{'xyz'[a]}" if a >= 0 else "oblique"


def _card(active=False) -> QFrame:
    f = QFrame()
    f.setObjectName("item")
    f.setAttribute(Qt.WA_StyledBackground, True)
    if active:
        f.setProperty("active", True)
    return f


def _swatch(colour: str, tip: str) -> QLabel:
    sw = QLabel("")
    sw.setFixedSize(14, 14)
    sw.setStyleSheet(f"background: {colour}; border: 1px solid {theme.tokens()['line_strong']}; border-radius: 3px;")
    sw.setToolTip(tip)
    return sw


def _btn(text, id, tip, kind="ghostBorder") -> QPushButton:
    b = wid(QPushButton(text), id)
    b.setObjectName(kind)
    b.setToolTip(tip)
    b.setCursor(Qt.PointingHandCursor)
    return b


def _mm_field(value_m: float, id: str, tip: str) -> QLineEdit:
    from PySide6.QtGui import QDoubleValidator
    e = wid(QLineEdit(fmt_num(round(value_m * 1000, 3))), id)
    v = QDoubleValidator(e)
    v.setNotation(QDoubleValidator.StandardNotation)
    e.setValidator(v)
    e.setToolTip(tip)
    e.setAccessibleName(tip)
    return e


class Symmetry:
    """The symmetry-plane block of the Setup panel. `draft`: the plane being placed - {axis ("x"|"y"|"z", or None for a plane at an angle),
    normal (unit, toward the kept side), point (a point on it, m), origin/n0 (where a face-based plane started and its first normal: its
    offset is measured from there), face (the face it follows, or None), keep (+1/-1), cut}; `picking`: the next flat face clicked in the 3D
    view gives the draft its plane."""

    def __init__(self, panel):
        self.panel = panel
        self.draft: dict | None = None
        self.picking = False
        self._pos_edit = None            # the position field of the draft card (updated while the arrow is dragged, without drawing the panel again)

    # ------------------------------------------------------------------ the draft
    def _bbox(self):
        info = (self.panel.engine.state()["geometry"].get("info") or {})
        return info.get("parts_bbox") or info.get("bbox")

    def _middle(self) -> np.ndarray:
        bb = self._bbox()
        return np.array([(bb[k] + bb[k + 3]) / 2 for k in range(3)], float) if bb else np.zeros(3)

    def start_draft(self, axis: str):
        """A new plane across the axis, through the middle of the parts (the side kept and the choice to cut the model carry over)."""
        a = "xyz".index(axis)
        old = self.draft or {}
        keep = old.get("keep", 1)
        e = np.zeros(3)
        e[a] = 1.0
        c = self._middle()
        self.draft = {"axis": axis, "normal": (keep * e).tolist(), "point": c.tolist(), "origin": c.tolist(), "n0": e.tolist(), "face": None,
                      "keep": keep, "cut": old.get("cut", False)}
        self.picking = False
        self.panel.redo()

    def pos_mm(self) -> float:
        """What the field shows: the coordinate on the axis for a plane across x, y or z; the offset from the face (positive: into the part)
        for a plane that follows a face."""
        d = self.draft
        if d["axis"] and d.get("face") is None:
            return d["point"]["xyz".index(d["axis"])] * 1000
        return float((np.asarray(d["point"]) - np.asarray(d["origin"])) @ np.asarray(d["n0"])) * 1000

    def set_pos_mm(self, v: float):
        d = self.draft
        if d["axis"] and d.get("face") is None:
            pt = list(d["point"])
            pt["xyz".index(d["axis"])] = float(v) / 1000
            d["point"] = pt
        else:
            d["point"] = (np.asarray(d["origin"]) + np.asarray(d["n0"]) * float(v) / 1000).tolist()

    def draft_set(self, **kw):
        if self.draft is None:
            raise ValueError("No new plane is being placed: choose which way it cuts first (across x, y or z, or through a face)")
        if "axis" in kw and kw["axis"] != self.draft["axis"]:
            self.start_draft(kw.pop("axis"))
        if "pos_mm" in kw:
            self.set_pos_mm(float(kw.pop("pos_mm")))
        if kw.pop("flip", False):
            d = self.draft
            d["normal"] = [-x for x in d["normal"]]
            d["keep"] *= -1
        if "cut" in kw:
            self.draft["cut"] = bool(kw.pop("cut"))
        self.panel.redo()

    def cancel(self):
        self.draft = None
        self.picking = False
        self.show_preview()
        self.panel.redo()

    def _dragged(self, point, final: bool):
        """The arrow was dragged in the 3D view: the position follows (the field shows it at once; the panel is drawn again when it is let go)."""
        d = self.draft
        if d is None:
            return
        d["point"] = [float(x) for x in point]
        e = self._pos_edit
        if e is not None:
            try:
                e.setText(fmt_num(round(self.pos_mm(), 3)))
            except RuntimeError:                       # (the field was deleted by a redraw)
                self._pos_edit = None
        if final:
            self.panel.redo()

    def show_preview(self):
        """Draw (or take away) the plane in the 3D view: the sheet, the arrow into the kept side and the handle to move it."""
        v = self.panel.view
        if v is None:
            return
        d = self.draft
        bb = self._bbox() if self.panel.engine.pid else None
        if d is None or not bb or self.panel.win is None or self.panel.win.step != "setup":
            v.plane_handle.clear()
            return
        from .gizmo import nice_step_mm
        size = max(bb[3] - bb[0], bb[4] - bb[1], bb[5] - bb[2])
        v.plane_handle.set(d["point"], d["normal"], bb, self._dragged, nice_step_mm(size) / 1000.0)

    # ------------------------------------------------------------------ what it does
    def _job(self, fn, msg):
        try:
            job = fn()
        except Exception as e:  # noqa: BLE001
            self.panel._fail(e)
            return None
        self.panel.say(msg if job is not None else "Renamed.")
        self.panel.redo()
        return job

    def set_draft(self):
        """Apply: rebuild the geometry with the plane."""
        d = self.draft
        if d is None:
            raise ValueError("No new plane to set")
        pr = self.panel.engine.project
        self.draft = None
        self.show_preview()
        if d["axis"]:
            mm = d["point"]["xyz".index(d["axis"])] * 1000
            return self._job(lambda: pr().add_symmetry(axis=d["axis"], pos_mm=mm, keep="+" if d["keep"] > 0 else "-", cut_parts=bool(d["cut"])),
                             "Rebuilding the geometry with the plane…")
        return self._job(lambda: pr().add_symmetry(point=d["point"], normal=d["normal"], cut_parts=bool(d["cut"])),
                         "Rebuilding the geometry with the plane…")

    def from_face(self, fid: int):
        """The face clicked while a plane through a face is wanted: a draft that follows it (flat faces only)."""
        self.picking = False
        pr = self.panel.engine.project()
        f = pr.face_map().get(int(fid))
        if f is None or "Plane" not in str(f.get("surface_type", "")):
            self.panel.say(f"Face {fid} is not flat: a symmetry plane goes through a flat face. Press 'Through a flat face' and click a flat one.")
            self.panel.redo()
            return None
        try:
            pl = engine_sym.plane_from_face(f, keep_toward=pr._parts_center())
        except ValueError as e:
            self.panel._fail(e)
            return None
        n, pt = np.asarray(pl["normal"], float), np.asarray(pl["point"], float)
        old = self.draft or {}
        axis = None
        a = int(np.argmax(np.abs(n)))
        keep = 1
        if abs(n[a]) > 0.9999:                              # (a face across an axis: an ordinary x, y or z plane, with the coordinate as its position)
            axis, keep = "xyz"[a], (1 if n[a] > 0 else -1)
            n = np.eye(3)[a] * keep
        self.draft = {"axis": axis, "normal": n.tolist(), "point": pt.tolist(), "origin": pt.tolist(), "n0": n.tolist(), "face": int(fid),
                      "keep": keep, "cut": old.get("cut", False)}
        self.panel.say(f"A plane through face {fid}: move it with the arrow in the 3D view or type an offset, then press Apply.")
        self.panel.redo()
        return self.draft

    def flip(self, pid):
        return self._job(lambda: self.panel.engine.project().update_symmetry(pid, flip=True), "Rebuilding the geometry: keeping the other side…")

    def move(self, pid, pos_mm):
        return self._job(lambda: self.panel.engine.project().update_symmetry(pid, pos_mm=float(pos_mm)), "Rebuilding the geometry with the plane moved…")

    def set_cut(self, pid, on):
        return self._job(lambda: self.panel.engine.project().update_symmetry(pid, cut_parts=bool(on)), "Rebuilding the geometry…")

    def rename(self, pid, name):
        if name.strip():
            self._job(lambda: self.panel.engine.project().update_symmetry(pid, name=name.strip()), "")

    def remove(self, pid):
        return self._job(lambda: self.panel.engine.project().remove_symmetry(pid), "Rebuilding the geometry without the plane: the removed side comes back…")

    def toggle_pick(self, on: bool):
        self.picking = bool(on)
        if on:
            self.panel.active = None
            self.panel.axis_pick = None
            self.panel.say("Click a flat face of the model in the 3D view: the symmetry plane goes through it.")
        self.panel.redo()

    # ------------------------------------------------------------------ the block
    def build(self, st, busy: bool) -> list:
        planes = st.get("symmetry") or []
        aero = st["study"] == "external_aero"
        out = []
        h = _h("Symmetry planes")
        h.setToolTip(HELP)
        out.append(h)
        out.append(_muted("Cut a symmetric model in half (or a quarter): fewer cells, the same answer. Choose which way the plane cuts: "
                          "it appears in the 3D view, move it, then press Apply."))
        why_busy = "A job is running: wait until it has finished" if busy else ""
        for i, pl in enumerate(planes):
            card = _card()
            v = QVBoxLayout(card)
            v.setContentsMargins(10, 8, 10, 8)
            v.setSpacing(6)
            head = QHBoxLayout()
            head.addWidget(_swatch("#4cc9f0", "A symmetry plane"))
            nm = wid(QLineEdit(pl["name"]), f"setup.sym.{i}.name")
            nm.setToolTip("Rename this plane (no rebuild)")
            nm.editingFinished.connect(lambda pid=pl["id"], w=nm, old=pl["name"]: self.rename(pid, w.text()) if w.text().strip() != old else None)
            head.addWidget(nm, 1)
            k = QLabel(f"keeps {keeps(pl)}")
            k.setObjectName("muted")
            head.addWidget(k)
            fl = _btn("⇄ Flip", f"setup.sym.{i}.flip", why_busy or "Keep the other side of the plane (the geometry is rebuilt)")
            fl.setEnabled(not busy)
            fl.clicked.connect(lambda _=False, pid=pl["id"]: self.flip(pid))
            head.addWidget(fl)
            rm = _btn("✕", f"setup.sym.{i}.remove", why_busy or "Remove the plane: the removed side comes back", "ghost")
            rm.setAccessibleName(f"Remove {pl['name']}")
            rm.setEnabled(not busy)
            rm.clicked.connect(lambda _=False, pid=pl["id"]: self.remove(pid))
            head.addWidget(rm)
            v.addLayout(head)
            linked = next((b for b in st.get("bcs") or [] if b.get("plane_id") == pl["id"]), None)
            if linked:
                n = len(linked.get("faces") or [])
                v.addWidget(_muted(f"🔗 Its boundary condition (symmetry, {n} face{'' if n == 1 else 's'}) is listed under Boundary conditions, "
                                   "greyed out: it follows this plane."))
            a = plane_axis(pl)
            if a >= 0:
                row = QHBoxLayout()
                row.addWidget(QLabel(f"Position {'xyz'[a]} [mm]"))
                e = _mm_field(pl["point"][a], f"setup.sym.{i}.pos", why_busy or f"Where the plane crosses the {'xyz'[a]} axis (the geometry is rebuilt)")
                e.setEnabled(not busy)
                e.editingFinished.connect(lambda pid=pl["id"], w=e, old=fmt_num(round(pl["point"][a] * 1000, 3)):
                                          self.move(pid, float(w.text())) if w.text() not in ("", old) else None)
                row.addWidget(e, 1)
                v.addLayout(row)
            if aero:
                cb = wid(QCheckBox("Also delete the model beyond the plane"), f"setup.sym.{i}.cut")
                cb.setChecked(bool(pl.get("cut_parts")))
                cb.setEnabled(not busy)
                cb.setToolTip("Off: the plane only ends the air volume and the model stays whole. On: the part of the model beyond the plane "
                              "is deleted from the geometry too.")
                cb.toggled.connect(lambda on, pid=pl["id"]: self.set_cut(pid, on))
                v.addWidget(cb)
            out.append(card)
        full = len(planes) >= MAX_PLANES
        reason = why_busy or ("Three planes at most (an eighth of the model)" if full else "")
        if not full:
            out.append(wid(_muted("Add a plane. Which way does it cut?"), "setup.sym.choose"))
        row = QWidget()
        hl = FlowLayout(row)
        names = {"x": "YZ plane, across x", "y": "XZ plane, across y", "z": "XY plane, across z"}
        for a in "xyz":
            b = _btn(names[a], f"setup.sym.add_{a}", reason or f"A plane across the {a} axis (the {names[a].split(',')[0]}), through the middle of "
                                                           "the parts: it appears in the 3D view, move it there or type its position, then press Apply")
            b.setCheckable(True)
            b.setObjectName("toggle")
            b.setChecked(bool(self.draft and self.draft["axis"] == a and self.draft.get("face") is None))
            b.setEnabled(not reason)
            b.clicked.connect(lambda _=False, a=a: self.start_draft(a) if not (self.draft and self.draft["axis"] == a) else self.panel.redo())
            hl.addWidget(b)
        fb = _btn("Through a flat face", "setup.sym.from_face", reason or "Then click a flat face of the model: the plane follows it, and you can "
                                                                         "move it parallel to the face")
        fb.setCheckable(True)
        fb.setObjectName("toggle")
        fb.setChecked(self.picking or bool(self.draft and self.draft.get("face") is not None))
        fb.setEnabled(not reason)
        fb.toggled.connect(self.toggle_pick)
        hl.addWidget(fb)
        out.append(row)
        if self.picking:
            out.append(_muted("Click a flat face in the 3D view: the symmetry plane goes through it. Press 'Through a flat face' again to stop."))
        if self.draft:
            out.append(self._draft_card(aero, busy))
        if planes:
            frac = {1: "Half", 2: "A quarter"}.get(len(planes), "An eighth")
            out.append(_muted(f"{frac} of the model is solved. " + (
                "Forces and moments in the results are those of the whole body. The oncoming flow must run along the plane (no sideslip)."
                if aero else "Loads and heat sources you enter act on the kept part only.") + " The Results step can show the mirrored half."))
        return out

    def _draft_card(self, aero: bool, busy: bool):
        d = self.draft
        card = _card(active=True)
        v = QVBoxLayout(card)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(6)
        head = QHBoxLayout()
        head.addWidget(_swatch("#ffb703", "The plane being placed (not set yet): drawn in the 3D view"))
        t = QLabel("New symmetry plane")
        t.setStyleSheet("font-weight: 600;")
        head.addWidget(t, 1)
        cx = _btn("✕", "setup.sym.draft.cancel", "Discard the new plane", "ghost")
        cx.setAccessibleName("Discard the new plane")
        cx.clicked.connect(self.cancel)
        head.addWidget(cx)
        v.addLayout(head)
        if d.get("face") is not None:
            what = f"Through face {d['face']}"
        else:
            what = f"Across the {d['axis']} axis"
        v.addWidget(wid(_muted(f"{what}. It is the amber sheet in the 3D view: drag its arrow to move it (hold Shift for round steps), or type "
                               "the position."), "setup.sym.draft.what"))
        row = QHBoxLayout()
        if d["axis"] and d.get("face") is None:
            row.addWidget(QLabel(f"Position {d['axis']} [mm]"))
            tip = f"Where the plane crosses the {d['axis']} axis"
        else:
            row.addWidget(QLabel(f"Offset from face {d['face']} [mm]" if d.get("face") is not None else "Offset [mm]"))
            tip = "How far the plane is moved from the face, parallel to it (positive: into the part, negative: out of it)"
        pe = _mm_field(self.pos_mm() / 1000, "setup.sym.draft.pos", tip)
        pe.editingFinished.connect(lambda w=pe: self.draft_set(pos_mm=float(w.text())) if w.text() else None)
        self._pos_edit = pe
        row.addWidget(pe, 1)
        v.addLayout(row)
        kr = QHBoxLayout()
        side = f"{'+' if d['keep'] > 0 else '−'}{d['axis']}" if d["axis"] else "the side the arrow points to"       # (an axis plane: the axis side)
        k = wid(QLabel(f"keeps {side}"), "setup.sym.draft.keeps")
        k.setObjectName("muted")
        k.setToolTip("The arrow in the 3D view points into the side that stays")
        kr.addWidget(k, 1)
        fl = _btn("⇄ Flip", "setup.sym.draft.flip", "Keep the other side (the arrow turns round)")
        fl.clicked.connect(lambda: self.draft_set(flip=True))
        kr.addWidget(fl)
        v.addLayout(kr)
        if aero:
            v.addWidget(_muted("The air volume ends at the plane (the side the arrow points away from is left out). The model stays whole "
                               "unless you tick the box."))
            cb = wid(QCheckBox("Also delete the model beyond the plane"), "setup.sym.draft.cut")
            cb.setChecked(bool(d.get("cut")))
            cb.setToolTip("On: the part of the model beyond the plane is deleted from the geometry too")
            cb.toggled.connect(lambda on: self.draft.update(cut=bool(on)) if self.draft else None)
            v.addWidget(cb)
        else:
            v.addWidget(_muted("The side the arrow points away from will be removed from the model."))
        br = QHBoxLayout()
        s = _btn("Apply", "setup.sym.draft.set", "A job is running: wait until it has finished" if busy else
                 "Rebuild the geometry with this plane (a few seconds)", "primary")
        s.setEnabled(not busy)
        s.clicked.connect(self.set_draft)
        br.addWidget(s, 1)
        c2 = _btn("Cancel", "setup.sym.draft.cancel2", "Discard the new plane", "ghostBorder")
        c2.clicked.connect(self.cancel)
        br.addWidget(c2)
        v.addLayout(br)
        return card
