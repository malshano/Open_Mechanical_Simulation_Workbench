"""Symmetry planes in the Setup step (as the web version's): a plane across x, y or z (a draft first: its axis, position and the side kept,
then Set), a plane through a flat face picked in the 3D view, and for each plane: its position, Flip (keep the other side), Remove, and in
an external flow whether the model beyond it is deleted too. Setting, moving, flipping or removing a plane rebuilds the geometry in a
background job (simwb.project add_symmetry / update_symmetry / remove_symmetry)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from .forms import fmt_num
from .ids import wid
from .panels.common import FlowLayout, _h, _muted
from . import theme

HELP = ("A mirror plane cuts the model (or the air volume of an external flow): only the side you keep is meshed and solved (half the "
        "cells for one plane), and the results can show the mirror image again. Use it when the shape AND the loads or the flow are "
        "mirror-symmetric. Add a plane, type its position, flip it to keep the other side, then press Set.")
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
    """The symmetry-plane block of the Setup panel. `draft`: the plane being placed ({axis, pos (m), keep ±1, cut}); `picking`: the next
    face clicked in the 3D view gets a plane through it."""

    def __init__(self, panel):
        self.panel = panel
        self.draft: dict | None = None
        self.picking = False

    # ------------------------------------------------------------------ what it does
    def _bbox(self):
        info = (self.panel.engine.state()["geometry"].get("info") or {})
        return info.get("parts_bbox") or info.get("bbox")

    def start_draft(self, axis: str):
        bb = self._bbox()
        a = "xyz".index(axis)
        old = self.draft or {}
        self.draft = {"axis": axis, "pos": (bb[a] + bb[a + 3]) / 2 if bb else 0.0, "keep": old.get("keep", 1), "cut": old.get("cut", False)}
        self.picking = False
        self.panel.redo()

    def draft_set(self, **kw):
        if self.draft is None:
            raise ValueError("No new plane is being placed: press '+ Plane ⟂ x' (y, z) first")
        if "axis" in kw and kw["axis"] != self.draft["axis"]:
            keep, cut = self.draft["keep"], self.draft["cut"]
            self.start_draft(kw.pop("axis"))
            self.draft.update(keep=keep, cut=cut)
        if "pos_mm" in kw:
            self.draft["pos"] = float(kw.pop("pos_mm")) / 1000
        if kw.pop("flip", False):
            self.draft["keep"] *= -1
        if "cut" in kw:
            self.draft["cut"] = bool(kw.pop("cut"))
        self.panel.redo()

    def cancel(self):
        self.draft = None
        self.picking = False
        self.panel.redo()

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
        d = self.draft
        if d is None:
            raise ValueError("No new plane to set")
        self.draft = None
        return self._job(lambda: self.panel.engine.project().add_symmetry(axis=d["axis"], pos_mm=d["pos"] * 1000, keep="+" if d["keep"] > 0 else "-",
                                                                            cut_parts=bool(d["cut"])),
                         "Rebuilding the geometry with the plane…")

    def from_face(self, fid: int):
        self.picking = False
        f = self.panel.engine.project().face_map().get(int(fid))
        if f is None or "Plane" not in str(f.get("surface_type", "")):
            self.panel.say(f"Face {fid} is not flat: a symmetry plane goes through a flat face. Press '+ From a face' and click a flat one.")
            self.panel.redo()
            return None
        return self._job(lambda: self.panel.engine.project().add_symmetry(faces=[int(fid)]), f"Rebuilding the geometry with a plane through face {fid}…")

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
            self.draft = None
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
        out.append(_muted("Cut a symmetric model in half (or a quarter): fewer cells, the same answer."))
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
        if self.draft:
            out.append(self._draft_card(aero, busy))
        row = QWidget()
        hl = FlowLayout(row)
        full = len(planes) >= MAX_PLANES
        reason = why_busy or ("Three planes at most (an eighth of the model)" if full else "")
        for a in "xyz":
            b = _btn(f"＋ Plane ⟂ {a}", f"setup.sym.add_{a}", reason or f"A new plane across the {a} axis, through the middle of the parts: "
                                                                          "set its position and side, then press Set")
            b.setEnabled(not reason)
            b.clicked.connect(lambda _=False, a=a: self.start_draft(a))
            hl.addWidget(b)
        fb = _btn("＋ From a face", "setup.sym.from_face", reason or "Then click a flat face of the model: the plane goes through it")
        fb.setCheckable(True)
        fb.setObjectName("toggle")
        fb.setChecked(self.picking)
        fb.setEnabled(not reason)
        fb.toggled.connect(self.toggle_pick)
        hl.addWidget(fb)
        out.append(row)
        if self.picking:
            out.append(_muted("Click a flat face in the 3D view: the symmetry plane goes through it. Press '＋ From a face' again to stop."))
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
        head.addWidget(_swatch("#ffb703", "The plane being placed (not set yet)"))
        t = QLabel("New plane")
        t.setStyleSheet("font-weight: 600;")
        head.addWidget(t, 1)
        k = wid(QLabel(f"keeps {'+' if d['keep'] > 0 else '−'}{d['axis']}"), "setup.sym.draft.keeps")
        k.setObjectName("muted")
        head.addWidget(k)
        fl = _btn("⇄ Flip", "setup.sym.draft.flip", "Keep the other side")
        fl.clicked.connect(lambda: self.draft_set(flip=True))
        head.addWidget(fl)
        cx = _btn("✕", "setup.sym.draft.cancel", "Discard the new plane", "ghost")
        cx.setAccessibleName("Discard the new plane")
        cx.clicked.connect(self.cancel)
        head.addWidget(cx)
        v.addLayout(head)
        row = QHBoxLayout()
        row.addWidget(QLabel("Normal to"))
        ax = wid(QComboBox(), "setup.sym.draft.axis")
        ax.addItems(["x", "y", "z"])
        ax.setCurrentText(d["axis"])
        ax.setToolTip("The axis the plane is at right angles to")
        ax.currentTextChanged.connect(lambda a: self.draft_set(axis=a))
        row.addWidget(ax)
        row.addWidget(QLabel("Position [mm]"))
        pe = _mm_field(d["pos"], "setup.sym.draft.pos", f"Where the plane crosses the {d['axis']} axis")
        pe.editingFinished.connect(lambda w=pe: self.draft_set(pos_mm=float(w.text())) if w.text() else None)
        row.addWidget(pe, 1)
        v.addLayout(row)
        if aero:
            v.addWidget(_muted("The air volume ends at the plane (the side the arrow in the 3D view points away from is left out). The model "
                               "stays whole unless you tick the box."))
            cb = wid(QCheckBox("Also delete the model beyond the plane"), "setup.sym.draft.cut")
            cb.setChecked(bool(d.get("cut")))
            cb.setToolTip("On: the part of the model beyond the plane is deleted from the geometry too")
            cb.toggled.connect(lambda on: self.draft.update(cut=bool(on)) if self.draft else None)
            v.addWidget(cb)
        else:
            v.addWidget(_muted("The side the arrow in the 3D view points away from will be removed from the model."))
        s = _btn("Set plane: trim the air volume" if aero else "Set plane: cut the model", "setup.sym.draft.set",
                 "A job is running: wait until it has finished" if busy else "Rebuild the geometry with this plane", "primary")
        s.setEnabled(not busy)
        s.clicked.connect(self.set_draft)
        v.addWidget(s)
        return card
