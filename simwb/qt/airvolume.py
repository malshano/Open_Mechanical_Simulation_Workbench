"""The air volume in the Geometry step (flow and cooling studies): its shape (box, C-shape, cylinder along the flow), its sizes, Apply, Fit to
parts, Show all; for a cooling study whether a body of the file is the air or an air box is made; for an external-flow study the automatic
size round the parts in spans (Minimal / Compact / Standard / Large presets, Fit with these).

The engine does the work (project.set_domain, a job that rebuilds the geometry). Sizes are typed in mm: the sizes are changed in a draft
that Apply sends, as in the web version. (The web version also drags orange handles in the view; here the 3D view shows the whole air volume
with Show all and the sizes are typed.)"""
from __future__ import annotations

import copy
import json

from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import QComboBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

from .forms import fmt_num
from .ids import wid
from .orient import _h, _item, _muted, notice

AIR_SHAPES = {"box": "Box", "c_domain": "C-shape (curved inlet)", "cylinder": "Cylinder along the flow"}
AIR_SIZES = {
    "box": [("min.0", "x from"), ("max.0", "x to"), ("min.1", "y from"), ("max.1", "y to"), ("min.2", "z from"), ("max.2", "z to")],
    "c_domain": [("radius", "Radius (inlet curve)"), ("downstream", "Downstream"), ("span_min", "Span from"), ("span_max", "Span to")],
    "cylinder": [("radius", "Radius"), ("upstream", "Upstream"), ("downstream", "Downstream")],
}
FF_PRESETS = {"Minimal (test run)": (1, 2, 1), "Compact": (3, 6, 3), "Standard": (5, 10, 5), "Large": (8, 16, 8)}
AXES = ["+x", "-x", "+y", "-y", "+z", "-z"]


def _get(sp: dict, k: str):
    if "." in k:
        a, i = k.split(".")
        return sp[a][int(i)]
    return sp[k]


def _set(sp: dict, k: str, v: float):
    if "." in k:
        a, i = k.split(".")
        sp[a][int(i)] = v
    else:
        sp[k] = v


class AirVolumeBox:
    def __init__(self, host):
        self.h = host
        self.draft: dict | None = None      # the air volume as built, with the sizes typed since
        self.key: str | None = None
        self.dirty = False
        self.notice: tuple | None = None
        self.apply_btn: QPushButton | None = None

    # ------------------------------------------------------------------ the spec being edited
    def spec(self, st: dict) -> dict | None:
        """The air volume as built (None when there is none to edit: no air volume, or a cooling study whose air is a body of the file)."""
        g = st["geometry"]
        d = (g.get("info") or {}).get("domain") or {}
        if not d.get("spec") or g.get("status") != "ready":
            return None
        if st["study"] == "cht_enclosure" and (st.get("domain", {}).get("enclosure_mode") or "body") != "auto_box":
            return None
        key = json.dumps(d["spec"], sort_keys=True)
        if self.key != key or self.draft is None:
            self.key, self.draft, self.dirty = key, copy.deepcopy(d["spec"]), False
        return self.draft

    def _pr(self):
        return self.h.engine.project()

    def _job(self, say: str, **kw):
        try:
            job = self._pr().set_domain(**kw)
            self.notice = ("info", say)
            self.dirty = False
            self.h.rebuild()
            return job
        except Exception as e:  # noqa: BLE001
            self.notice = ("bad", f"<b>The air volume was not changed.</b> {e}")
            self.h.rebuild()
            return None

    # ------------------------------------------------------------------ operations (the buttons and the control API)
    def set_size(self, key: str, mm: float):
        """Type a size (mm) into the draft."""
        if self.draft is None:
            raise ValueError("There is no air volume to edit here")
        _set(self.draft, key, float(mm) / 1000.0)
        self.dirty = True
        if self.apply_btn is not None:
            try:
                self.apply_btn.setEnabled(True)
            except RuntimeError:
                self.apply_btn = None

    def apply(self):
        sp = self.draft
        if sp is None:
            raise ValueError("There is no air volume to edit here")
        size = {}
        for k, _ in AIR_SIZES[sp["shape"]]:
            a, _, i = k.partition(".")
            if i:
                size.setdefault(a, list(sp[a]))[int(i)] = sp[a][int(i)]
            else:
                size[k] = sp[k]
        return self._job("Rebuilding the air volume…", size=size)

    def fit(self):
        return self._job("Fitting the air volume to the parts…", fit=True)

    def set_shape(self, shape: str):
        return self._job("Rebuilding the air volume…", shape=shape)

    def set_flow(self, axis: str):
        return self._job("Rebuilding the air volume…", flow_axis=axis, fit=True)

    def set_span(self, axis: str):
        return self._job("Rebuilding the air volume…", span_axis=axis, fit=True)

    def set_mode(self, mode: str):
        return self._job("Changing where the air comes from…", enclosure_mode=mode)

    def set_body(self, index: int):
        return self._job("Using that body as the air…", enclosure_body=int(index))

    def farfield(self, up: float, down: float, side: float):
        return self._job("Rebuilding the air volume…", farfield={"upstream": up, "downstream": down, "side": side}, fit=True)

    def preset(self, name: str):
        key = next((k for k in FF_PRESETS if k.lower().startswith(name.lower())), None)
        if key is None:
            raise ValueError(f"No such preset. Presets: {', '.join(FF_PRESETS)}")
        return self.farfield(*FF_PRESETS[key])

    def show_all(self):
        """Zoom out to the whole air volume (it is shown, translucent, for this)."""
        w = self.h.win
        if w is None:
            return
        if not w.chk_air.isChecked():
            w.chk_air.setChecked(True)                  # (shows it and fits the view to everything drawn)
        self.h.view.fit()
        self.notice = ("info", "The whole air volume is shown. The Air box switch of the 3D view hides it again.")
        self.h.rebuild()

    # ------------------------------------------------------------------ the widgets
    def build(self, v: QVBoxLayout, st: dict, busy: bool):
        study = st["study"]
        if study not in ("cht_enclosure", "external_aero"):
            return
        g = st["geometry"]
        info = g.get("info") or {}
        dom = st.get("domain") or {}
        v.addWidget(_h("Air volume"))
        if self.notice:
            v.addWidget(notice(*self.notice, name="geometry.air.notice"))
        if study == "cht_enclosure":
            mode = dom.get("enclosure_mode") or "body"
            mc = wid(QComboBox(), "geometry.air.mode")
            mc.addItem("A body in the STEP is the air (duct / enclosure interior)", "body")
            mc.addItem("Create an air box around the parts", "auto_box")
            mc.setCurrentIndex(0 if mode == "body" else 1)
            mc.setEnabled(not busy)
            mc.setToolTip("Where the air of a cooling study comes from: a body of your file, or an air volume made around the parts")
            mc.activated.connect(lambda i: self.set_mode(mc.itemData(i)))
            v.addWidget(mc)
            if mode == "body":
                src = info.get("source_bodies") or []
                cur = dom.get("enclosure_body", (info.get("domain") or {}).get("enclosure_body"))
                v.addWidget(_muted("Which body is the air?"))
                bc = wid(QComboBox(), "geometry.air.body")
                for i, b in enumerate(src):
                    bc.addItem(f"{b['name']} ({fmt_num(b['volume'] * 1e6)} cm³)", i)
                if cur is not None and 0 <= cur < bc.count():
                    bc.setCurrentIndex(cur)
                bc.setEnabled(not busy)
                bc.setToolTip("The body of the file that is the air (the duct or the enclosure's inside)")
                bc.activated.connect(lambda i: self.set_body(bc.itemData(i)))
                v.addWidget(bc)
                return
        sp = self.spec(st)
        if sp is None:
            return
        self._editor(v, sp, st, info, busy)
        if study == "external_aero":
            self._automatic(v, dom, info, busy)

    def _editor(self, v: QVBoxLayout, sp: dict, st: dict, info: dict, busy: bool):
        stl = (info.get("source_format") or "step") != "step"
        sc = wid(QComboBox(), "geometry.air.shape")
        for k, label in AIR_SHAPES.items():
            sc.addItem(label, k)
        sc.setCurrentIndex(list(AIR_SHAPES).index(sp["shape"]))
        sc.setEnabled(not stl and not busy)
        sc.setToolTip("A curved air volume needs a STEP file" if stl else "The shape of the air volume around the parts")
        sc.activated.connect(lambda i: self.set_shape(sc.itemData(i)))
        v.addWidget(QLabel("Shape"))
        v.addWidget(sc)
        if st["study"] == "cht_enclosure":
            cur = ("+" if sp.get("flow_sign", 1) > 0 else "-") + "xyz"[sp.get("flow_axis", 0)]
            fc = wid(QComboBox(), "geometry.air.flow")
            fc.addItems(AXES)
            fc.setCurrentText(cur)
            fc.setEnabled(not busy)
            fc.setToolTip("The direction the air flows through the air volume")
            fc.activated.connect(lambda i: self.set_flow(fc.itemText(i)))
            v.addWidget(QLabel("Flow direction"))
            v.addWidget(fc)
        if sp["shape"] == "c_domain":
            cur = "xyz"[sp["span_axis"]]
            spn = wid(QComboBox(), "geometry.air.span")
            spn.addItems([a for a in "xyz" if a != "xyz"[sp["flow_axis"]]])
            spn.setCurrentText(cur)
            spn.setEnabled(not busy)
            spn.setToolTip("The axis along the curve of the C-shape (across the span of a wing)")
            spn.activated.connect(lambda i: self.set_span(spn.itemText(i)))
            v.addWidget(QLabel("Span axis (along the curve)"))
            v.addWidget(spn)
        grid = QGridLayout()
        grid.setSpacing(4)
        for n, (k, label) in enumerate(AIR_SIZES[sp["shape"]]):
            grid.addWidget(QLabel(f"{label} [mm]"), n, 0)
            f = wid(QLineEdit(f"{_get(sp, k) * 1000:.5g}"), f"geometry.air.size.{k}")
            f.setValidator(QDoubleValidator(-1e9, 1e9, 4, f))
            f.setToolTip(f"{label} in mm" + ("" if sp["shape"] == "box" else " (from the centre of the parts)"))
            f.editingFinished.connect(lambda k=k, f=f: self._typed(k, f))
            grid.addWidget(f, n, 1)
        v.addLayout(grid)
        row = QHBoxLayout()
        ap = wid(QPushButton("Apply"), "geometry.air.apply")
        ap.setObjectName("primary")
        ap.setEnabled(self.dirty and not busy)
        ap.setToolTip("Rebuild the air volume with these sizes (takes a moment)" if self.dirty else "Type a size first: Apply sends the sizes you changed")
        ap.clicked.connect(lambda: self._try(self.apply))
        self.apply_btn = ap
        fit = wid(QPushButton("Fit to parts"), "geometry.air.fit")
        fit.setObjectName("ghostBorder")
        fit.setEnabled(not busy)
        fit.setToolTip("Size the air volume automatically around the parts")
        fit.clicked.connect(lambda: self._try(self.fit))
        sa = wid(QPushButton("Show all"), "geometry.air.showall")
        sa.setObjectName("ghostBorder")
        sa.setToolTip("Show the whole air volume in the 3D view (zoom out to it)")
        sa.clicked.connect(lambda: self._try(self.show_all))
        for b in (ap, fit, sa):
            row.addWidget(b)
        v.addLayout(row)
        extra = ""
        if sp["shape"] == "c_domain":
            extra = " The curved face is the inlet" + (" (Auto inlet/outlet in Setup assigns it, with uniform flow along the flow direction)"
                                                       if st["study"] == "cht_enclosure" else "") + "; the flat end downstream is the outlet."
        v.addWidget(_muted(("Fitted to the parts. " if sp.get("fitted") else "") + "Type the sizes in mm"
                           + ("" if sp["shape"] == "box" else " (from the centre of the parts)") + ", then press Apply." + extra))

    def _typed(self, key: str, field: QLineEdit):
        try:
            v = float(field.text())
        except ValueError:
            return
        cur = _get(self.draft, key) * 1000 if self.draft else None
        if cur is not None and abs(cur - v) < 1e-9:
            return
        self.set_size(key, v)

    def _try(self, fn):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            self.h.fail(e)

    def _automatic(self, v: QVBoxLayout, dom: dict, info: dict, busy: bool):
        v.addWidget(_h("Automatic size (spans of the parts)"))
        ref = (info.get("ref_length") or 0) * 1000
        v.addWidget(_muted(f"Distance from the parts to the air volume's faces, in spans of the parts ({fmt_num(ref)} mm). Compact boxes mesh and solve faster; "
                           "the far-field faces then sit closer and can bias drag by a few per cent."))
        ff = dom.get("farfield") or {"upstream": 8, "downstream": 16, "side": 8}
        row = QGridLayout()
        for n, (name, vals) in enumerate(FF_PRESETS.items()):
            b = wid(QPushButton(name.split(" (")[0]), f"geometry.air.preset.{name.split(' (')[0].lower()}")
            b.setObjectName("ghostBorder")
            b.setEnabled(not busy)
            b.setToolTip(f"{vals[0]} / {vals[1]} / {vals[2]} spans upstream / downstream / side: sizes the air volume and rebuilds it")
            b.clicked.connect(lambda _=False, name=name: self._try(lambda: self.preset(name)))
            row.addWidget(b, n // 2, n % 2)
        v.addLayout(row)
        fields = {}
        grid = QGridLayout()
        for n, k in enumerate(("upstream", "downstream", "side")):
            grid.addWidget(QLabel(f"{k.capitalize()} [spans]"), n, 0)
            f = wid(QLineEdit(f"{ff.get(k, 8):g}"), f"geometry.air.ff.{k}")
            f.setValidator(QDoubleValidator(0.5, 100, 3, f))
            f.setToolTip(f"Distance {k} of the parts to the air volume, in spans of the parts (0.5 to 100)")
            grid.addWidget(f, n, 1)
            fields[k] = f
        v.addLayout(grid)
        go = wid(QPushButton("Fit with these"), "geometry.air.ff.apply")
        go.setObjectName("ghostBorder")
        go.setEnabled(not busy)
        go.setToolTip("Size the air volume with these distances and rebuild it")
        go.clicked.connect(lambda: self._try(lambda: self.farfield(*[float(fields[k].text() or 0) for k in ("upstream", "downstream", "side")])))
        v.addWidget(go)
