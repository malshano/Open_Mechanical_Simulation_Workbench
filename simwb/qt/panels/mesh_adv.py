"""The advanced options of the Mesh step, as in the web version: cell sizes and defeaturing, part sizing, refinement regions (boxes and
cylinders) and wall layers (flow studies). Built on the engine's set_mesh; every field is in mm, the engine works in metres."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from ..forms import ParamForm, fmt_num
from ..ids import wid
from .common import Section, _h, _muted, clear_layout

M = 1000.0         # metres -> mm


def _item() -> QFrame:
    f = QFrame()
    f.setObjectName("item")
    f.setAttribute(Qt.WA_StyledBackground, True)
    return f


BOX = [{"key": f"{lo}{ax}", "label": f"{lo.replace('lo', 'min').replace('hi', 'max')} {ax}", "unit": "mm", "default": 0.0}
       for lo in ("lo", "hi") for ax in "xyz"]
CYL = ([{"key": f"s{ax}", "label": f"start {ax}", "unit": "mm", "default": 0.0} for ax in "xyz"]
       + [{"key": f"e{ax}", "label": f"end {ax}", "unit": "mm", "default": 0.0} for ax in "xyz"]
       + [{"key": "radius", "label": "radius", "unit": "mm", "default": 10.0, "min": 0.001},
          {"key": "inner", "label": "inner radius (0 = solid)", "unit": "mm", "default": 0.0, "min": 0.0}])


class MeshAdvanced(QWidget):
    """Placed under the resolution presets of the Mesh step."""

    def __init__(self, panel, parent=None):
        super().__init__(parent)
        self.panel = panel
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(0, 0, 0, 0)
        self.v.setSpacing(8)
        self._sig = None
        self._open: dict = {}               # which sections the user has open (kept when the content is rebuilt)

    def _sec(self, title, prefix, default):
        s = Section(title, prefix, expanded=self._open.get(prefix, default))
        s.head.toggled.connect(lambda on, k=prefix: self._open.__setitem__(k, on))
        return s

    @property
    def engine(self):
        return self.panel.engine

    # ------------------------------------------------------------------ build
    def refresh(self, st):
        m = st["mesh"]
        sig = repr([st["study"], m.get("size_max"), m.get("size_min"), m.get("defeature"), m.get("refinements"), m.get("regions"), m.get("layers"),
                    [(b["id"], b["name"]) for b in st["body_list"]]])
        if sig == self._sig:
            return
        self._sig = sig
        clear_layout(self.v)
        self._sizes(m)
        self._parts(st, m)
        self._regions(st, m)
        if st["study"] == "external_aero":
            self._layers(m)

    def _num(self, text, prefix, value_m, tip, auto=True):
        row = QHBoxLayout()
        row.addWidget(QLabel(text))
        e = wid(QLineEdit("" if value_m in (None, 0) else fmt_num(value_m * M)), prefix)
        e.setPlaceholderText("automatic" if auto else "")
        e.setValidator(QDoubleValidator(0, 1e6, 6, e))
        e.setToolTip(tip)
        row.addWidget(e, 1)
        return row, e

    def _sizes(self, m):
        s = self._sec("Advanced sizes and defeaturing", "mesh.sizes", bool(m.get("size_max") or m.get("size_min") or m.get("defeature")))
        for text, key, val, tip in (("Largest cell [mm]", "size_max", m.get("size_max"), "Largest cells of the mesh, in the bulk (empty: from the preset)"),
                                    ("Smallest cell [mm]", "size_min", m.get("size_min"), "Smallest cells, at walls and small features (empty: from the preset)"),
                                    ("Defeature [mm]", "defeature", m.get("defeature"), "Small faces below this size are merged into the face they sit on (0 = off)")):
            row, e = self._num(text, f"mesh.{key}", val, tip, auto=key != "defeature")
            e.editingFinished.connect(lambda k=key, e=e: self._set_size(k, e.text()))
            s.body.addLayout(row)
        self.v.addWidget(s)

    def _set_size(self, key, text):
        try:
            val = float(text) if text.strip() else 0
            kw = {"size_max": "size_max_mm", "size_min": "size_min_mm", "defeature": "defeature_mm"}[key]
            self.engine.project().set_mesh(**{kw: val if val else ("" if key != "defeature" else 0)})
            self.engine.changed.emit()
        except Exception as e:  # noqa: BLE001
            self.panel._fail(e)

    # ---- part sizing
    def _parts(self, st, m):
        refs = m.get("refinements") or []
        s = self._sec(f"Part sizing ({len(refs)})", "mesh.parts", bool(refs))
        names = {b["id"]: b["name"] for b in st["body_list"]}
        for i, r in enumerate(refs):
            row = QHBoxLayout()
            row.addWidget(QLabel(f"<b>{r.get('part') or 'Faces'}</b> · {len(r['faces'])} face(s)"), 1)
            e = wid(QLineEdit(fmt_num(r["size"] * M)), f"mesh.part.{i}.size")
            e.setValidator(QDoubleValidator(0.001, 1e6, 6, e))
            e.setToolTip("Cell size on these faces [mm]")
            e.setFixedWidth(80)
            e.editingFinished.connect(lambda i=i, e=e: self._part_size(i, e.text()))
            row.addWidget(e)
            row.addWidget(QLabel("mm"))
            d = wid(QPushButton("✕"), f"mesh.part.{i}.delete")
            d.setObjectName("ghost")
            d.setToolTip("Remove this part sizing")
            d.clicked.connect(lambda _=False, i=i: self._part_delete(i))
            row.addWidget(d)
            w = QWidget()
            w.setLayout(row)
            s.body.addWidget(w)
        row = QHBoxLayout()
        self.part_pick = wid(QComboBox(), "mesh.part.pick")
        self.part_pick.setToolTip("The part to size")
        for b in st["body_list"]:
            if b["role"] == "solid":
                self.part_pick.addItem(b["name"], b["id"])
        self.part_pick.addItem("Selected faces (pick them in the 3D view)", None)
        add = wid(QPushButton("＋ Part sizing"), "mesh.part.add")
        add.setObjectName("ghostBorder")
        add.setToolTip("Set a cell size on a part, or on the faces selected in the 3D view")
        add.clicked.connect(self._part_add)
        row.addWidget(self.part_pick, 1)
        row.addWidget(add)
        s.body.addLayout(row)
        self.v.addWidget(s)

    def _refs(self):
        return [dict(r) for r in (self.engine.state()["mesh"].get("refinements") or [])]

    def _save_refs(self, refs):
        self.engine.project().set_mesh(refinements=[{"faces": r["faces"], "size_mm": r["size"] * M, **({"part": r["part"]} if r.get("part") else {})}
                                                    for r in refs])
        self.engine.changed.emit()

    def _part_add(self):
        try:
            bid = self.part_pick.currentData()
            if bid is None:
                faces = list(self.panel.view.selection)
                if not faces:
                    raise ValueError("Select faces in the 3D view first, or choose a part")
                part = None
            else:
                faces = [f["id"] for f in self.engine.project().faces() if bid in (f.get("volumes") or []) and f.get("kind") not in ("farfield", "enclosure")]
                part = self.part_pick.currentText()
            size = (self.engine.state()["mesh"].get("size_max") or 0.01) / 2
            refs = self._refs() + [{"faces": faces, "size": size, **({"part": part} if part else {})}]
            self._save_refs(refs)
        except Exception as e:  # noqa: BLE001
            self.panel._fail(e)

    def _part_size(self, i, text):
        try:
            refs = self._refs()
            refs[i]["size"] = float(text) / M
            self._save_refs(refs)
        except Exception as e:  # noqa: BLE001
            self.panel._fail(e)

    def _part_delete(self, i):
        refs = self._refs()
        del refs[i]
        self._save_refs(refs)

    # ---- refinement regions
    def _regions(self, st, m):
        regs = m.get("regions") or []
        s = self._sec(f"Refinement regions ({len(regs)})", "mesh.regions", bool(regs))
        for i, r in enumerate(regs):
            card = _item()
            cv = QVBoxLayout(card)
            cv.setContentsMargins(8, 6, 8, 6)
            cv.setSpacing(4)
            head = QHBoxLayout()
            name = wid(QLineEdit(r["name"]), f"mesh.region.{i}.name")
            name.setToolTip("Name of this refinement region")
            name.editingFinished.connect(lambda i=i, w=name: self._region_edit(i, name=w.text()))
            head.addWidget(name, 1)
            head.addWidget(_muted("cylinder" if r.get("shape") == "cylinder" else "box"))
            d = wid(QPushButton("✕"), f"mesh.region.{i}.delete")
            d.setObjectName("ghost")
            d.setToolTip("Remove this refinement region")
            d.clicked.connect(lambda _=False, i=i: self._region_delete(i))
            head.addWidget(d)
            cv.addLayout(head)
            if r.get("shape") == "cylinder":
                vals = {**{f"s{ax}": r["start"][k] * M for k, ax in enumerate("xyz")}, **{f"e{ax}": r["end"][k] * M for k, ax in enumerate("xyz")},
                        "radius": r["radius"] * M, "inner": r.get("inner_radius", 0) * M}
                params = CYL
            else:
                vals = {**{f"lo{ax}": r["min"][k] * M for k, ax in enumerate("xyz")}, **{f"hi{ax}": r["max"][k] * M for k, ax in enumerate("xyz")}}
                params = BOX
            f = ParamForm(params + [{"key": "size", "label": "cell size", "unit": "mm", "default": 5.0, "min": 0.001}],
                          {**vals, "size": r["size"] * M}, f"mesh.region.{i}.param")
            f.changed.connect(lambda k, v, i=i, f=f: self._region_edit(i, values=f.values()))
            cv.addWidget(f)
            s.body.addWidget(card)
        row = QHBoxLayout()
        for key, label in (("box", "＋ Box"), ("cyl", "＋ Cylinder")):
            b = wid(QPushButton(label), f"mesh.region.add_{key}")
            b.setObjectName("ghostBorder")
            b.setToolTip(f"Add a {'box' if key == 'box' else 'cylinder'} where the mesh is made finer (around a wake, a gap ...)")
            b.clicked.connect(lambda _=False, k=key: self._region_add(k))
            row.addWidget(b)
        row.addStretch(1)
        s.body.addLayout(row)
        self.v.addWidget(s)

    def _regions_state(self):
        return [dict(r) for r in (self.engine.state()["mesh"].get("regions") or [])]

    def _save_regions(self, regs):
        self.engine.project().set_mesh(regions=regs)
        self.engine.changed.emit()

    def _region_add(self, kind):
        try:
            info = self.engine.state()["geometry"].get("info") or {}
            bb = info.get("parts_bbox") or info.get("bbox") or [0, 0, 0, 0.1, 0.1, 0.1]
            c = [(bb[k] + bb[k + 3]) / 2 for k in range(3)]
            h = [(bb[k + 3] - bb[k]) / 2 for k in range(3)]
            size = max((self.engine.state()["mesh"].get("size_max") or 0.01) / 3, 1e-4)
            n = len(self._regions_state()) + 1
            if kind == "box":
                r = {"name": f"region{n}", "min": [c[k] - 1.2 * h[k] for k in range(3)], "max": [c[k] + 1.2 * h[k] for k in range(3)], "size": size}
            else:
                r = {"name": f"region{n}", "shape": "cylinder", "start": [c[0] - h[0], c[1], c[2]], "end": [c[0] + h[0], c[1], c[2]],
                     "radius": 1.2 * max(h[1], h[2]), "size": size}
            self._save_regions(self._regions_state() + [r])
        except Exception as e:  # noqa: BLE001
            self.panel._fail(e)

    def _region_edit(self, i, name=None, values=None):
        try:
            regs = self._regions_state()
            r = regs[i]
            if name is not None:
                r["name"] = name.strip() or r["name"]
            if values is not None:
                r["size"] = values["size"] / M
                if r.get("shape") == "cylinder":
                    r["start"] = [values[f"s{a}"] / M for a in "xyz"]
                    r["end"] = [values[f"e{a}"] / M for a in "xyz"]
                    r["radius"] = values["radius"] / M
                    if values.get("inner"):
                        r["inner_radius"] = values["inner"] / M
                    else:
                        r.pop("inner_radius", None)
                else:
                    r["min"] = [values[f"lo{a}"] / M for a in "xyz"]
                    r["max"] = [values[f"hi{a}"] / M for a in "xyz"]
            self._save_regions(regs)
        except Exception as e:  # noqa: BLE001
            self.panel._fail(e)

    def _region_delete(self, i):
        regs = self._regions_state()
        del regs[i]
        self._save_regions(regs)

    # ---- wall layers
    def _layers(self, m):
        ly = m.get("layers") or {}
        s = self._sec("Wall layers (prism / inflation)", "mesh.layers", bool(ly.get("n")))
        s.body.addWidget(_muted("Thin layers of cells along the walls, for the boundary layer (added by OpenFOAM before the run). 0 layers = off."))
        f = ParamForm([{"key": "n", "label": "number of layers", "unit": "", "default": 0, "min": 0, "max": 20},
                       {"key": "growth", "label": "growth ratio", "unit": "", "default": 1.2, "min": 1.0, "max": 2.0},
                       {"key": "first", "label": "first layer thickness (0 = automatic)", "unit": "mm", "default": 0.0, "min": 0.0}],
                      {"n": ly.get("n", 0), "growth": ly.get("growth", 1.2), "first": (ly.get("first") or 0) * M}, "mesh.layers")
        f.changed.connect(lambda k, v, f=f: self._layers_set(f.values()))
        s.body.addWidget(f)
        self.v.addWidget(s)

    def _layers_set(self, v):
        try:
            self.engine.project().set_mesh(layers={"n": int(v["n"]), "growth": v["growth"], "first_mm": v["first"] or None})
            self.engine.changed.emit()
        except Exception as e:  # noqa: BLE001
            self.panel._fail(e)
