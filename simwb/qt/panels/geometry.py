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
from ..forms import fmt_num

SCALES = [(1.0, "×1 (STL/OBJ: mm)"), (0.001, "×0.001"), (25.4, "inch → mm  ×25.4"), (100.0, "×100"), (1000.0, "×1000")]
AXES = ("+x", "-x", "+y", "-y", "+z", "-z")
PLANES = (("XY plane", ("+z", "-z")), ("YZ plane", ("+x", "-x")), ("XZ plane", ("+y", "-y")))


def _item() -> QFrame:
    f = QFrame()
    f.setObjectName("item")
    f.setAttribute(Qt.WA_StyledBackground, True)
    return f


def nearest_axis(normal) -> str:
    n = np.asarray(normal, float)
    k = int(np.argmax(np.abs(n)))
    return ("+" if n[k] >= 0 else "-") + "xyz"[k]


class GeometryPanel(_Panel):
    def __init__(self, engine, parent=None):
        super().__init__(engine, parent)
        self._sig = None
        self.align_pick = False        # waiting for the face to align
        self.align: dict | None = None  # {face, info, wall, away}
        self.align_wall_pick = False
        self.note = ""
        self.holder = QVBoxLayout()
        self.holder.setSpacing(10)
        self.v.addLayout(self.holder)
        self.v.addStretch(1)

    # ------------------------------------------------------------------ picking (the face to align)
    def bind(self, win):
        super().bind(win)
        win.view.facePicked.connect(self._face_picked)

    def shown(self):
        self.view.set_pick_mode("face" if (self.align_pick or self.align_wall_pick) else None)

    def left(self):
        self.view.set_pick_mode(None)
        self.view.set_selection([])
        self._end_align()

    def _face_picked(self, fid, add):
        if self.win is None or self.win.step != "geometry":
            return
        pr = self.engine.project()
        try:
            if self.align_wall_pick and self.align:
                pr.ref_info(fid, exclude=self.align["face"])          # (raises if this cannot be the reference)
                self.align["wall"] = fid
                self.align_wall_pick = False
                self._align_to_wall()
            elif self.align_pick:
                info = pr.align_info(fid)
                self.align = {"face": fid, "info": info, "wall": None, "away": False}
                self.align_pick = False
                self.view.set_selection([fid])
            else:
                return
        except Exception as e:  # noqa: BLE001
            self.note = str(e)
        self._sig = None
        self.engine.changed.emit()

    def _end_align(self):
        self.align_pick = self.align_wall_pick = False
        self.align = None
        self.note = ""
        self._sig = None
        if self.view is not None:
            self.view.set_pick_mode(None)
            self.view.set_selection([])

    # ------------------------------------------------------------------ the panel
    def refresh(self, st):
        if not st:
            return
        g = st["geometry"]
        busy = any((j or {}).get("status") == "running" for j in (st.get("jobs") or {}).values())
        sig = repr([st["study"], g.get("status"), g.get("rotation"), g.get("original_name"), g.get("error"), busy,
                    [(b["id"], b["name"], b["role"]) for b in st["body_list"]], self.align_pick, self.align_wall_pick,
                    None if not self.align else (self.align["face"], self.align["wall"], self.align["away"]), self.note,
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
        v.addWidget(_h("Orientation"))
        v.addWidget(_muted("Turn the parts about their middle to line them up with the axes (for example the nose along +x, the top along +z)"
                           + (" · rotated" if g.get("rotation") else "") + "."))
        al = wid(QPushButton("⬓  Align a face to a plane…"), "geometry.align.start")
        al.setObjectName("ghostBorder")
        al.setEnabled(not busy)
        al.setToolTip("Click a flat face of the model, then choose the coordinate plane to lay it on")
        al.clicked.connect(self._start_align)
        v.addWidget(al)
        self._align_box(v, busy)
        grid = QGridLayout()
        grid.setSpacing(4)
        for r, ax in enumerate("xyz"):
            for c, deg in enumerate((-90, 90)):
                b = wid(QPushButton(f"{ax.upper()} {'−' if deg < 0 else '+'}90°"), f"geometry.turn.{ax}{'-' if deg < 0 else '+'}90")
                b.setObjectName("ghostBorder")
                b.setEnabled(not busy)
                b.setToolTip(f"Turn {deg:+d}° about {ax} (right-handed)")
                b.clicked.connect(lambda _=False, ax=ax, deg=deg: self.turn(ax, deg))
                grid.addWidget(b, r, c)
        v.addLayout(grid)
        row = QHBoxLayout()
        self.axis = wid(QComboBox(), "geometry.turn.axis")
        self.axis.addItems(["x", "y", "z"])
        self.axis.setCurrentText("z")
        self.axis.setToolTip("Axis to turn about")
        self.deg = wid(QLineEdit("45"), "geometry.turn.degrees")
        self.deg.setValidator(QDoubleValidator(-360, 360, 4, self.deg))
        self.deg.setToolTip("Angle in degrees")
        go = wid(QPushButton("Turn"), "geometry.turn.apply")
        go.setObjectName("ghostBorder")
        go.setEnabled(not busy)
        go.setToolTip("Turn the model by this angle about this axis")
        go.clicked.connect(lambda: self.turn(self.axis.currentText(), float(self.deg.text() or 0)))
        row.addWidget(self.axis)
        row.addWidget(self.deg)
        row.addWidget(go)
        v.addLayout(row)
        if g.get("rotation"):
            rs = wid(QPushButton("Reset orientation"), "geometry.turn.reset")
            rs.setObjectName("ghostBorder")
            rs.setEnabled(not busy)
            rs.setToolTip("Back to the orientation the CAD file had")
            rs.clicked.connect(lambda: self.turn(reset=True))
            v.addWidget(rs)
        self.holder.addWidget(card)

    def _import_replace(self):
        path, _ = QFileDialog.getOpenFileName(self, "Replace the CAD file", "", "CAD files (*.step *.stp *.stl *.obj);;All files (*)")
        if path:
            try:
                self.engine.import_step(path)
            except Exception as e:  # noqa: BLE001
                self._fail(e)

    def turn(self, axis=None, degrees=90.0, reset=False):
        """Turn the parts about their middle (a job: the geometry is rebuilt)."""
        try:
            self.engine.project().rotate_geometry(axis, degrees, reset=reset)
            self.note = ""
        except Exception as e:  # noqa: BLE001
            self.note = str(e)
            self._fail(e)
        self._sig = None
        self.engine.changed.emit()

    # ---- align a face to a plane
    def _start_align(self):
        self.align = None
        self.align_pick = True
        self.align_wall_pick = False
        self.note = ""
        self.view.set_selection([])
        self.view.set_pick_mode("face")
        self._sig = None
        self.engine.changed.emit()

    def _align_box(self, v, busy):
        if self.align_pick:
            v.addWidget(_muted("Click a flat face of the model in the 3D view…"))
        if self.note:
            n = QLabel(self.note)
            n.setWordWrap(True)
            n.setStyleSheet(f"color: {theme.tokens()['warn_text']};")
            v.addWidget(n)
        a = self.align
        if not a:
            return
        info, near = a["info"], nearest_axis(a["info"]["normal"])
        box = _item()
        b = QVBoxLayout(box)
        b.setContentsMargins(8, 6, 8, 6)
        b.setSpacing(4)
        lab = QLabel(f"<b>Face {a['face']}</b> ({fmt_num(info['area_m2'] * 1e6)} mm²) points {near.replace('-', '−')} now (★ = nearest). "
                     "Choose the plane to lay it on:")
        lab.setWordWrap(True)
        b.addWidget(lab)
        for plane, targets in PLANES:
            row = QHBoxLayout()
            row.addWidget(QLabel(plane))
            for t in targets:
                btn = wid(QPushButton(f"facing {t.replace('-', '−')}{' ★' if t == near else ''}"), f"geometry.align.to.{t}")
                btn.setObjectName("ghostBorder")
                btn.setEnabled(not busy)
                btn.setToolTip(f"Lay the face on the {plane}, facing {t.replace('-', '−')}")
                btn.clicked.connect(lambda _=False, t=t: self.align_to(t))
                row.addWidget(btn)
            b.addLayout(row)
        b.addWidget(_muted("… or align it to a plane in the 3D view (a tunnel wall or another flat face):"))
        wb = wid(QPushButton("Click the plane in the 3D view…" if self.align_wall_pick else "Pick the plane"), "geometry.align.wall")
        wb.setObjectName("ghostBorder")
        wb.setToolTip("Then click the face to line this one up with")
        wb.clicked.connect(self._pick_wall)
        b.addWidget(wb)
        if a.get("wall") is not None:
            b.addWidget(_muted(f"Plane: face {a['wall']}"))
            row = QHBoxLayout()
            for away, label, tip in ((False, "Same direction", "The face points the same way as the plane"),
                                     (True, "Face to face", "The face points the opposite way")):
                btn = wid(QPushButton(label), f"geometry.align.{'away' if away else 'same'}")
                btn.setObjectName("ghostBorder")
                btn.setToolTip(tip)
                btn.clicked.connect(lambda _=False, away=away: self._align_to_wall(away))
                row.addWidget(btn)
            b.addLayout(row)
        done = wid(QPushButton("Done"), "geometry.align.done")
        done.setObjectName("ghostBorder")
        done.setToolTip("Stop aligning")
        done.clicked.connect(lambda: (self._end_align(), self.engine.changed.emit()))
        b.addWidget(done, 0, Qt.AlignLeft)
        v.addWidget(box)

    def align_to(self, target: str):
        """Lay the picked face on the plane that faces `target` (a job: the geometry is rebuilt)."""
        if not self.align:
            raise ValueError("Pick a face first (Align a face to a plane…)")
        try:
            self.engine.project().align_face(self.align["face"], target)
            self.note = ""
            self.align = None
            self.view.set_selection([])
        except Exception as e:  # noqa: BLE001
            self.note = str(e)
        self._sig = None
        self.engine.changed.emit()

    def _pick_wall(self):
        self.align_wall_pick = True
        self.view.set_pick_mode("face")
        self._sig = None
        self.engine.changed.emit()

    def _align_to_wall(self, away=None):
        a = self.align
        if not a or a.get("wall") is None:
            return
        if away is not None:
            a["away"] = bool(away)
        try:
            self.engine.project().align_face(a["face"], None, a["wall"], a["away"])
            self.note = ""
            self.align = None
            self.view.set_selection([])
        except Exception as e:  # noqa: BLE001
            self.note = str(e)
        self._sig = None
        self.engine.changed.emit()

    # ---- the parts
    def _bodies(self, st):
        self.holder.addWidget(_h("Bodies"))
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
