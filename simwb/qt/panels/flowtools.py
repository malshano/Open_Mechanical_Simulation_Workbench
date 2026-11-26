"""The Flow tab of the results step (flow and cooling studies): the ways of looking at a flow that the web version has - contour planes (as
many as wanted, each moved along its axis with a slider), vortex surfaces (Q-criterion), volume rendering, vector arrows on a plane and particle
paths. Every tool has its own switch and they can be combined; the
object is drawn in grey under them. Plain widgets with ids (results.flow.*), so that the control API and the tests reach every one."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSlider, QSpinBox,
                               QVBoxLayout, QWidget)

from ..ids import wid
from .common import _h, _muted, _seg


class FlowTools(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.infos: list = []
        self.defaults = {"gap_mm": 0.0, "free_stream": 0.0}
        self._names = None
        self._names_before = None
        self._quiet = False               # (set while several controls are set at once: one redraw at the end)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 6, 0, 0)
        v.setSpacing(8)
        v.addWidget(_muted("Ways of looking at the flow. Switch on one or several: they are drawn together over the object (grey)."))
        # ---- the region
        v.addWidget(_h("Region"))
        row = QHBoxLayout()
        self.region = self._combo("results.flow.region", [("object", "Around the object"), ("all", "Whole air box")],
                                  "The box the tools look at: close around the object and its wake, or all the air")
        self.res = self._combo("results.flow.grid", [(str(n), f"Grid {n}") for n in (48, 64, 96, 128)],
                               "Grid points along the longest side of the region: more is finer and slower")
        self.res.setCurrentIndex(2)
        row.addWidget(self.region, 1)
        row.addWidget(self.res)
        v.addLayout(row)
        # ---- contour planes
        v.addWidget(_h("Contour planes"))
        v.addWidget(_muted("Cuts through the flow, coloured by a field. Add as many as you like; move each along its axis with its slider."))
        self.planes: list = []                   # what each plane shows: {field, axis, frac 0..1 along the region, opacity 0.1..1}
        self._box = None                         # (the region in metres, for the positions in mm)
        self._plane_rows: list = []
        self.planes_box = QVBoxLayout()
        self.planes_box.setSpacing(8)
        v.addLayout(self.planes_box)
        self.plane_add = wid(QPushButton("+  Add contour plane"), "results.flow.planes.add")
        self.plane_add.setObjectName("ghostBorder")
        self.plane_add.setToolTip("Another cut through the flow (a new plane starts across Y, in the middle)")
        self.plane_add.clicked.connect(lambda _=False: self.add_plane())
        v.addWidget(self.plane_add)
        self.plane_model = self._combo("results.flow.planes.model", [("ghost", "Whole model, see-through"), ("solid", "Whole model, solid"),
                                                                     ("cut", "Cut open at plane 1 (section view)")],
                                       "How the model is drawn with the planes. A plane never cuts the model by itself: choose the section view here if you want the model opened at plane 1")
        self.plane_model_row = QWidget()
        mr = QVBoxLayout(self.plane_model_row)
        mr.setContentsMargins(0, 0, 0, 0)
        mr.addLayout(self._row("Model", self.plane_model))
        v.addWidget(self.plane_model_row)
        self.plane_model_row.setVisible(False)
        # ---- vortex surfaces
        self.iso_box = QWidget()
        iv = QVBoxLayout(self.iso_box)
        iv.setContentsMargins(0, 0, 0, 0)
        iv.setSpacing(6)
        iv.addWidget(_h("Vortex surfaces (turbulence)"))
        iv.addWidget(_muted("Surfaces of equal Q-criterion wrap the vortex cores: rotation beats strain there."))
        self.iso_on = self._check("results.flow.iso.on", "Show vortex surfaces", "Draw the surfaces of the Q-criterion")
        iv.addWidget(self.iso_on)
        self.iso_k = self._slider("results.flow.iso.k", 2, 60, 48, "Weaker vortices shown: to the right, weaker (smaller) vortices appear too")
        iv.addLayout(self._row("Weaker vortices shown", self.iso_k))
        self.iso_note = _muted("")
        self.iso_note.setObjectName("muted")
        iv.addWidget(self.iso_note)
        self.iso_color = self._combo("results.flow.iso.color", [], "The field the surfaces are coloured by")
        iv.addLayout(self._row("Coloured by", self.iso_color))
        self.iso_gap = QDoubleSpinBox()
        wid(self.iso_gap, "results.flow.iso.gap")
        self.iso_gap.setRange(0.0, 500.0)
        self.iso_gap.setDecimals(1)
        self.iso_gap.setSingleStep(0.5)
        self.iso_gap.setSuffix(" mm")
        self.iso_gap.setToolTip("Leave out the surfaces this close to the walls: the shear of the boundary layer gives a large Q there too and would hide the vortices")
        self.iso_gap.valueChanged.connect(lambda *_: self._emit())
        iv.addLayout(self._row("Keep clear of walls", self.iso_gap))
        self.iso_smooth = QSpinBox()
        wid(self.iso_smooth, "results.flow.iso.smooth")
        self.iso_smooth.setRange(0, 3)
        self.iso_smooth.setValue(1)
        self.iso_smooth.setToolTip("Smoothing passes of the surfaces (0 = as the mesh made them)")
        self.iso_smooth.valueChanged.connect(lambda *_: self._emit())
        iv.addLayout(self._row("Smoothing", self.iso_smooth))
        v.addWidget(self.iso_box)
        # ---- volume rendering
        v.addWidget(_h("Volume rendering"))
        v.addWidget(_muted("The whole flow as a see-through cloud: colour and opacity follow the value, so wakes and slipstreams show in 3D."))
        self.vol_on = self._check("results.flow.volume.on", "Show the cloud", "Volume rendering of a field")
        v.addWidget(self.vol_on)
        self.vol_field = self._combo("results.flow.volume.field", [], "The field the cloud shows")
        v.addLayout(self._row("Field", self.vol_field))
        self.vol_density = self._slider("results.flow.volume.density", 5, 300, 50, "Density: higher is a thicker cloud")
        v.addLayout(self._row("Density", self.vol_density))
        self.vol_style = self._combo("results.flow.volume.style", [("map", "Colour map"), ("smoke", "Smoke")],
                                     "Colour map: the colours of the scale. Smoke: one colour that thickens where the value is high")
        v.addLayout(self._row("Look", self.vol_style))
        self.vol_scale = self._scale(v, "results.flow.volume.scale", "the cloud")
        # ---- vector arrows
        v.addWidget(_h("Vector arrows"))
        v.addWidget(_muted("Arrows of the flow on a plane, pointing the way the air moves, coloured by speed."))
        self.vec_on = self._check("results.flow.vectors.on", "Show arrows", "Arrows of the velocity on a plane")
        v.addWidget(self.vec_on)
        w, self.vec_axis_grp, self.vec_axis = _seg([("x", "X"), ("y", "Y"), ("z", "Z")], "y", prefix="results.flow.vectors.axis")
        for k, b in self.vec_axis.items():
            b.setToolTip(f"The plane across {k.upper()}")
            b.clicked.connect(lambda *_: self._emit())
        v.addLayout(self._row("Plane across", w))
        self.vec_pos = self._slider("results.flow.vectors.pos", 0, 1000, 500, "Where the plane cuts the region (from one side to the other)")
        v.addLayout(self._row("Position", self.vec_pos))
        self.vec_n = QSpinBox()
        wid(self.vec_n, "results.flow.vectors.n")
        self.vec_n.setRange(4, 80)
        self.vec_n.setValue(24)
        self.vec_n.setToolTip("Arrows across the region")
        self.vec_n.valueChanged.connect(lambda *_: self._emit())
        v.addLayout(self._row("Arrows across", self.vec_n))
        self.vec_scale = self._scale(v, "results.flow.vectors.scale", "the arrows")
        # ---- particle paths
        v.addWidget(_h("Particle paths"))
        v.addWidget(_muted("Paths of particles carried by the flow (streamlines), coloured by speed."))
        self.par_on = self._check("results.flow.streams.on", "Show paths", "Streamlines of the velocity")
        v.addWidget(self.par_on)
        self.par_n = QSpinBox()
        wid(self.par_n, "results.flow.streams.n")
        self.par_n.setRange(50, 6000)
        self.par_n.setSingleStep(100)
        self.par_n.setValue(800)
        self.par_n.setToolTip("Number of particles")
        self.par_n.valueChanged.connect(lambda *_: self._emit())
        v.addLayout(self._row("Particles", self.par_n))
        self.par_seed = self._combo("results.flow.streams.seed", [("upstream", "Upstream face"), ("everywhere", "Everywhere")],
                                    "Where the particles start: across the upstream face of the region, or anywhere in the air")
        v.addLayout(self._row("Start", self.par_seed))
        self.par_steps = QSpinBox()
        wid(self.par_steps, "results.flow.streams.steps")
        self.par_steps.setRange(20, 400)
        self.par_steps.setSingleStep(20)
        self.par_steps.setValue(160)
        self.par_steps.setToolTip("Steps along each path: more = longer paths")
        self.par_steps.valueChanged.connect(lambda *_: self._emit())
        v.addLayout(self._row("Length", self.par_steps))
        self.par_anim = self._check("results.flow.streams.animate", "Animate the particles",
                                    "Dots carried along the paths with a short tail, as the air moves (the web version's animated particles)")
        self.par_anim.setChecked(True)
        v.addWidget(self.par_anim)
        self.par_speed = self._slider("results.flow.streams.speed", 1, 40, 10, "Playback speed of the dots (1× in the middle)")
        v.addLayout(self._row("Dot speed", self.par_speed))
        self.par_scale = self._scale(v, "results.flow.streams.scale", "the paths")
        v.addStretch(1)
        self.iso_k.valueChanged.connect(lambda _=0: self._iso_note())

    def _emit(self):
        if not self._quiet:
            self.changed.emit()

    # ------------------------------------------------------------------ contour planes
    def _names_now(self) -> list:
        return [f["name"] for f in self.infos]

    def add_plane(self, field=None, axis="y", frac=0.5, opacity=1.0, emit=True, range=None):  # noqa: A002
        names = self._names_now()
        if field is None:
            field = "Velocity" if "Velocity" in names else (names[0] if names else "")
        if names and field not in names:
            raise ValueError(f"{field!r} is not a field of this result: " + ", ".join(names))
        if axis not in ("x", "y", "z"):
            raise ValueError("axis must be x, y or z")
        self.planes.append({"field": field, "axis": axis, "frac": min(max(float(frac), 0.0), 1.0),
                            "opacity": min(max(float(opacity), 0.1), 1.0),
                            "range": (float(range[0]), float(range[1])) if range and float(range[1]) > float(range[0]) else None})
        self._rebuild_planes()
        if emit:
            self._emit()

    def remove_plane(self, i: int, emit=True):
        if not 0 <= i < len(self.planes):
            raise ValueError(f"There is no plane {i + 1} (there are {len(self.planes)})")
        self.planes.pop(i)
        self._rebuild_planes()
        if emit:
            self._emit()

    def set_planes(self, planes: list, emit=True):
        """Replace the list: [{field, axis, frac (or position), opacity}]."""
        self.planes = []
        for p in planes or []:
            p = dict(p)
            self.add_plane(p.get("field"), p.get("axis", "y"), p.get("frac", p.get("position", 0.5)), p.get("opacity", 1.0), emit=False,
                           range=p.get("range"))
        self._rebuild_planes()
        if emit:
            self._emit()

    def set_box(self, box):
        """The region the planes cross (metres): their positions are then written in mm."""
        self._box = list(box) if box else None
        for i, _ in enumerate(self._plane_rows):
            self._pos_label(i)

    def _pos_text(self, i: int, frac=None) -> str:
        p = self.planes[i]
        frac = p["frac"] if frac is None else frac
        if not self._box:
            return f"{frac * 100:.0f} %"
        ax = "xyz".index(p["axis"])
        lo, hi = self._box[ax], self._box[3 + ax]
        return f"{p['axis']} = {(lo + frac * (hi - lo)) * 1000:.4g} mm"

    def _pos_label(self, i: int, frac=None):
        if i < len(self._plane_rows):
            self._plane_rows[i]["pos_label"].setText(self._pos_text(i, frac))

    def _rebuild_planes(self):
        while self.planes_box.count():
            it = self.planes_box.takeAt(0)
            if it.widget() is not None:
                it.widget().deleteLater()
        self._plane_rows = []
        names = self._names_now()
        self.plane_model_row.setVisible(bool(self.planes))       # (the choice only means something with a plane)
        for i, p in enumerate(self.planes):
            n = i + 1
            card = QFrame()
            card.setObjectName("card")
            cv = QVBoxLayout(card)
            cv.setContentsMargins(10, 8, 10, 8)
            cv.setSpacing(6)
            head = QHBoxLayout()
            title = QLabel(f"<b>Plane {n}</b>")
            head.addWidget(title)
            head.addStretch(1)
            rm = wid(QPushButton("Remove"), f"results.flow.plane.{n}.remove")
            rm.setObjectName("ghostBorder")
            rm.setToolTip(f"Delete plane {n}")
            rm.clicked.connect(lambda _=False, i=i: self.remove_plane(i))
            head.addWidget(rm)
            cv.addLayout(head)
            field = wid(QComboBox(), f"results.flow.plane.{n}.field")
            for nm in names or [p["field"]]:
                field.addItem(nm, nm)
            field.setCurrentText(p["field"])
            field.setToolTip("The field the plane is coloured by")
            field.currentTextChanged.connect(lambda t, i=i: self._plane_set(i, "field", t))
            cv.addLayout(self._row("Field", field))
            w, _grp, btns = _seg([("x", "X"), ("y", "Y"), ("z", "Z")], p["axis"], prefix=f"results.flow.plane.{n}.axis")
            for k, b in btns.items():
                b.setToolTip(f"The plane across {k.upper()}")
                b.clicked.connect(lambda _=False, i=i, k=k: self._plane_set(i, "axis", k))
            cv.addLayout(self._row("Plane across", w))
            pos = wid(QSlider(Qt.Horizontal), f"results.flow.plane.{n}.pos")
            pos.setRange(0, 1000)
            pos.setValue(int(round(p["frac"] * 1000)))
            pos.setTracking(False)                 # (the plane is cut again when the handle is let go; the label follows the drag)
            pos.setToolTip("Move the plane along its axis, across the region")
            pos.sliderMoved.connect(lambda val, i=i: self._pos_label(i, val / 1000.0))
            pos.valueChanged.connect(lambda val, i=i: self._plane_set(i, "frac", val / 1000.0))
            cv.addLayout(self._row("Position", pos))
            pos_label = wid(_muted(""), f"results.flow.plane.{n}.where")
            cv.addWidget(pos_label)
            op = wid(QSlider(Qt.Horizontal), f"results.flow.plane.{n}.opacity")
            op.setRange(10, 100)
            op.setValue(int(round(p["opacity"] * 100)))
            op.setTracking(False)
            op.setToolTip("Opacity: lower to see the object and the other tools behind the plane")
            op.valueChanged.connect(lambda val, i=i: self._plane_set(i, "opacity", val / 100.0))
            cv.addLayout(self._row("Opacity", op))
            sc = self._scale(cv, f"results.flow.plane.{n}.scale", f"plane {n}", emit=False)
            if p.get("range"):
                sc["auto"].blockSignals(True)
                sc["auto"].setChecked(False)
                sc["auto"].blockSignals(False)
                for e, val in ((sc["lo"], p["range"][0]), (sc["hi"], p["range"][1])):
                    e.setEnabled(True)
                    e.setText(f"{val:g}")
            for w_ in (sc["auto"], sc["lo"], sc["hi"]):
                (w_.toggled if w_ is sc["auto"] else w_.editingFinished).connect(lambda *_, i=i, sc=sc: self._plane_range(i, sc))
            self.planes_box.addWidget(card)
            self._plane_rows.append({"card": card, "field": field, "axis": btns, "pos": pos, "pos_label": pos_label, "opacity": op, "scale": sc})
            self._pos_label(i)

    def _plane_range(self, i: int, sc: dict):
        if i < len(self.planes):
            self.planes[i]["range"] = self._scale_value(sc)
            self._emit()

    def _plane_set(self, i: int, key: str, value):
        if i < len(self.planes) and self.planes[i].get(key) != value:
            self.planes[i][key] = value
            if key in ("frac", "axis"):
                self._pos_label(i)
            self._emit()

    # ------------------------------------------------------------------ the colour range of each tool
    def _scale(self, layout, id_: str, what: str, emit: bool = True) -> dict:
        """Automatic (the range of the data, or of nearly all the flow) or a range of its own: a tick box and two boxes."""
        row = QHBoxLayout()
        auto = wid(QCheckBox("Automatic colour range"), id_ + ".auto")
        auto.setChecked(True)
        auto.setToolTip(f"The colours of {what} run over the range of the data; untick to type a range of your own")
        lo, hi = wid(QLineEdit(), id_ + ".lo"), wid(QLineEdit(), id_ + ".hi")
        for e, t in ((lo, "Lowest value of the colour range"), (hi, "Highest value of the colour range")):
            e.setValidator(QDoubleValidator(e))
            e.setEnabled(False)
            e.setToolTip(t + " (untick Automatic to type it)")
            e.setMaximumWidth(80)
            if emit:
                e.editingFinished.connect(lambda: self._emit())
        auto.toggled.connect(lambda on: (lo.setEnabled(not on), hi.setEnabled(not on), self._emit() if emit else None))
        row.addWidget(auto)
        row.addStretch(1)
        row.addWidget(QLabel("from"))
        row.addWidget(lo)
        row.addWidget(QLabel("to"))
        row.addWidget(hi)
        layout.addLayout(row)
        return {"auto": auto, "lo": lo, "hi": hi}

    @staticmethod
    def _scale_value(sc: dict):
        """None (automatic) or (lo, hi) typed in."""
        if sc["auto"].isChecked():
            return None
        try:
            lo, hi = float(sc["lo"].text()), float(sc["hi"].text())
        except ValueError:
            return None
        return (lo, hi) if hi > lo else None

    def show_range(self, key, lo: float, hi: float):
        """The range a tool is drawn over (shown in its boxes while they are automatic). key: volume | vectors | streams | plane index."""
        sc = {"volume": self.vol_scale, "vectors": self.vec_scale, "streams": self.par_scale}.get(key)
        if sc is None and isinstance(key, int) and key < len(self._plane_rows):
            sc = self._plane_rows[key]["scale"]
        if sc is not None and sc["auto"].isChecked():
            for e, v in ((sc["lo"], lo), (sc["hi"], hi)):
                e.blockSignals(True)
                e.setText(f"{v:.4g}")
                e.blockSignals(False)

    def set_scale(self, key, lo=None, hi=None):
        """Give a tool a colour range of its own (lo, hi), or automatic again (no lo / hi). key: volume | vectors | streams | plane number (1..)."""
        if isinstance(key, int) or str(key).isdigit():
            i = int(key) - 1
            if not 0 <= i < len(self._plane_rows):
                raise ValueError(f"There is no plane {key}")
            sc = self._plane_rows[i]["scale"]
        else:
            sc = {"volume": self.vol_scale, "vectors": self.vec_scale, "streams": self.par_scale}.get(key)
            if sc is None:
                raise ValueError("key must be volume, vectors, streams or a plane number")
        if lo is None or hi is None:
            sc["auto"].setChecked(True)
            return None
        if not float(hi) > float(lo):
            raise ValueError("the range must run from a lower to a higher value")
        q, self._quiet = self._quiet, True
        sc["auto"].setChecked(False)
        sc["lo"].setText(f"{float(lo):g}")
        sc["hi"].setText(f"{float(hi):g}")
        self._quiet = q
        self._emit()
        return (float(lo), float(hi))

    # ------------------------------------------------------------------ small builders
    def _combo(self, id_: str, items: list, tip: str) -> QComboBox:
        c = wid(QComboBox(), id_)
        for key, text in items:
            c.addItem(text, key)
        c.setToolTip(tip)
        c.currentIndexChanged.connect(lambda *_: self._emit())
        return c

    def _check(self, id_: str, text: str, tip: str) -> QCheckBox:
        c = wid(QCheckBox(text), id_)
        c.setToolTip(tip)
        c.toggled.connect(lambda *_: self._emit())
        return c

    def _slider(self, id_: str, lo: int, hi: int, val: int, tip: str) -> QSlider:
        s = wid(QSlider(Qt.Horizontal), id_)
        s.setRange(lo, hi)
        s.setValue(val)
        s.setToolTip(tip)
        s.setTracking(False)               # (one redraw when the handle is let go, not one per pixel: a volume takes a moment)
        s.valueChanged.connect(lambda *_: self._emit())
        return s

    @staticmethod
    def _row(label: str, w) -> QHBoxLayout:
        r = QHBoxLayout()
        lab = QLabel(label)
        lab.setMinimumWidth(110)
        r.addWidget(lab)
        r.addWidget(w, 1)
        return r

    # ------------------------------------------------------------------ what the results offer
    def set_fields(self, infos: list, defaults: dict):
        """The fields of the result: the colour and cloud choices follow them; the vortex tools only exist with a Q-criterion field."""
        names = [f["name"] for f in infos]
        self.infos, self.defaults = infos, defaults
        has_q = "Q-criterion" in names
        self.iso_box.setVisible(has_q)
        if names != self._names:
            self._names_before, self._names = self._names, names
            new_q = has_q and not (self._names_before is not None and "Q-criterion" in self._names_before)
            for combo, pick in ((self.iso_color, "Velocity"), (self.vol_field, "Q-criterion" if has_q else "Velocity")):
                cur = combo.currentText()
                if combo is self.vol_field and new_q:          # (a result with turbulence quantities starts the cloud on them)
                    cur = ""
                combo.blockSignals(True)
                combo.clear()
                for n in names:
                    combo.addItem(n, n)
                combo.setCurrentText(cur if cur in names else (pick if pick in names else (names[0] if names else "")))
                combo.blockSignals(False)
            self.iso_gap.blockSignals(True)
            self.iso_gap.setValue(float(defaults.get("gap_mm") or 0.0))
            self.iso_gap.blockSignals(False)
            for p in self.planes:                                    # (a plane on a field this result does not have takes the first)
                if p["field"] not in names and names:
                    p["field"] = "Velocity" if "Velocity" in names else names[0]
            self._rebuild_planes()
        self._iso_note()

    def _iso_note(self):
        q = next((f for f in self.infos if f["name"] == "Q-criterion"), None)
        if q and q.get("max"):
            self.iso_note.setText(f"Q = {q['max'] * 10 ** (-self.iso_k.value() / 10.0):.3g} 1/s²")

    def request(self) -> dict:
        axis = next((k for k, b in self.vec_axis.items() if b.isChecked()), "y")
        return {"region": self.region.currentData(), "res": int(self.res.currentData()),
                "iso": {"on": self.iso_on.isChecked() and self.iso_box.isVisibleTo(self), "k": self.iso_k.value(), "color": self.iso_color.currentText(),
                        "gap": self.iso_gap.value(), "smooth": self.iso_smooth.value()},
                "volume": {"on": self.vol_on.isChecked(), "field": self.vol_field.currentText(), "density": self.vol_density.value() / 10.0,
                           "style": self.vol_style.currentData(), "range": self._scale_value(self.vol_scale)},
                "vectors": {"on": self.vec_on.isChecked(), "axis": axis, "frac": self.vec_pos.value() / 1000.0, "n": self.vec_n.value(),
                            "range": self._scale_value(self.vec_scale)},
                "streams": {"on": self.par_on.isChecked(), "n": self.par_n.value(), "seed": self.par_seed.currentData(), "steps": self.par_steps.value(),
                            "animate": self.par_anim.isChecked(), "speed": self.par_speed.value() / 10.0, "range": self._scale_value(self.par_scale)},
                "planes": [dict(p) for p in self.planes], "plane_model": self.plane_model.currentData()}

    def any_on(self) -> bool:
        r = self.request()
        return any(r[k]["on"] for k in ("iso", "volume", "vectors", "streams")) or bool(r["planes"])
