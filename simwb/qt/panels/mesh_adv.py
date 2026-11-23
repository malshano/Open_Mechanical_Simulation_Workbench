"""The advanced options of the Mesh step, as in the web version: cell sizes and defeaturing, part sizing, refinement regions (boxes and
cylinders) and wall layers (flow studies). Built on the engine's set_mesh; every field is in mm, the engine works in metres."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from .. import theme
from ..forms import ParamForm, fmt_num
from ..ids import wid
from ..meshtools import fit_box, region_cells, tip_rings, wake_regions
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
        self.show_regions = True            # the refinement regions are drawn in the 3D view
        self.show_layers = True             # the wall layers are drawn in the 3D view (a shell of their total thickness)
        self.active_region: int | None = None   # the one being edited or looked at: drawn stronger
        self.pick_btn = None

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
                    self.show_regions, self.show_layers, self.active_region, st.get("settings"), getattr(self.panel, "picking_part", False),
                    [(b["id"], b["name"]) for b in st["body_list"]], st.get("moving_zones"), st.get("flow_dir"), m.get("method"),
                    theme.effective()])
        if sig == self._sig:
            return
        self._sig = sig
        clear_layout(self.v)
        self._sizes(m)
        self._parts(st, m)
        self._regions(st, m)
        if st["study"] == "external_aero":
            self._layers(st, m)

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
        s.body.addWidget(_muted("A cell size on the surface of a part, growing to the surrounding size within about ten cells: cheaper "
                                "than a box round the part."))
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
        for kind, key, name in self.parts_to_size(st):
            self.part_pick.addItem(name, f"{kind}:{key}")
        self.part_pick.addItem("Faces selected in the 3D view", None)
        add = wid(QPushButton("＋ Part sizing"), "mesh.part.add")
        add.setObjectName("ghostBorder")
        add.setToolTip("Set a cell size on the part chosen here, or on the faces selected in the 3D view")
        add.clicked.connect(self._part_add)
        row.addWidget(self.part_pick, 1)
        row.addWidget(add)
        s.body.addLayout(row)
        self.pick_btn = wid(QPushButton("Pick a part in the 3D view"), "mesh.part.pick_3d")
        self.pick_btn.setObjectName("toggle")
        self.pick_btn.setCheckable(True)
        self.pick_btn.setChecked(bool(getattr(self.panel, "picking_part", False)))
        self.pick_btn.setToolTip("Then click the part in the 3D view: all its faces get a cell size (you can change it here afterwards)")
        self.pick_btn.toggled.connect(self.panel.set_picking_part)
        s.body.addWidget(self.pick_btn)
        s.body.addWidget(_muted("The faces that have a part sizing are tinted in the 3D view."))
        self.v.addWidget(s)

    def parts_to_size(self, st) -> list:
        """[(kind, key, name)] of what can be sized: the solid parts of a structural or heat study (bodies), and the parts of the object in a flow
        study (named on their faces: the object is not a body there, the only body is the air)."""
        out = [("body", b["id"], b["name"]) for b in st["body_list"] if b["role"] == "solid"]
        seen = {n for _k, _i, n in out}
        for f in self.engine.project().faces():
            n = f.get("part")
            if n and f.get("kind") not in ("farfield", "enclosure") and n not in seen:
                seen.add(n)
                out.append(("part", n, n))
        return out

    def _faces_of(self, kind: str, key):
        """The faces of a part to size: its own surface, never the walls of the air volume."""
        out = []
        for f in self.engine.project().faces():
            if f.get("kind") in ("farfield", "enclosure"):
                continue
            if kind == "body" and int(key) in (f.get("volumes") or []):
                out.append(f["id"])
            elif kind == "part" and f.get("part") == key:
                out.append(f["id"])
        return out

    def part_from_face(self, fid: int):
        """A face was clicked while 'Pick a part in the 3D view' is on: its whole part gets a cell size."""
        try:
            f = self.engine.project().face_map().get(int(fid))
            if f is None:
                return
            if f.get("kind") in ("farfield", "enclosure"):
                raise ValueError("That is the air volume, not a part: click the object itself")
            if f.get("part"):
                kind, key, name = "part", f["part"], f["part"]
            else:
                bid = (f.get("volumes") or [None])[0]
                body = next((b for b in self.engine.state()["body_list"] if b["id"] == bid), None)
                kind, key, name = "body", bid, (body["name"] if body else f"Part {bid}")
            faces = self._faces_of(kind, key)
            self.panel.set_picking_part(False)
            self._add_sizing(faces, name)
            self.panel.view.set_selection(faces)
        except Exception as e:  # noqa: BLE001
            self.panel._fail(e)

    def _refs(self):
        return [dict(r) for r in (self.engine.state()["mesh"].get("refinements") or [])]

    def _save_refs(self, refs):
        self.engine.project().set_mesh(refinements=[{"faces": r["faces"], "size_mm": r["size"] * M, **({"part": r["part"]} if r.get("part") else {})}
                                                    for r in refs])
        self.engine.changed.emit()

    def _part_add(self):
        try:
            data = self.part_pick.currentData()
            if data is None:
                faces = list(self.panel.view.selection)
                if not faces:
                    raise ValueError("Select faces in the 3D view first (or press 'Pick a part in the 3D view'), or choose a part")
                self._add_sizing(faces, None)
                return
            kind, key = str(data).split(":", 1)
            self._add_sizing(self._faces_of(kind, int(key) if kind == "body" else key), self.part_pick.currentText())
        except Exception as e:  # noqa: BLE001
            self.panel._fail(e)

    def _add_sizing(self, faces: list, part: str | None):
        if not faces:
            raise ValueError("That part has no faces to size")
        refs = self._refs()
        if part and any(r.get("part") == part for r in refs):
            raise ValueError(f"{part} already has a part sizing: change its size in the list")
        size = (self.engine.state()["mesh"].get("size_max") or 0.01) / 2
        self._save_refs(refs + [{"faces": faces, "size": size, **({"part": part} if part else {})}])

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
        s.body.addWidget(_muted("Boxes or cylinders of finer cells (bodies of influence), e.g. round a propeller, in a wake or a gap; the "
                                "cells grow smoothly to the surrounding size outside them."))
        sw = wid(QCheckBox("Show the regions in the 3D view"), "mesh.regions.show")
        sw.setChecked(self.show_regions)
        sw.setToolTip("Draw each region as a translucent box or cylinder over the model, in its own colour (the one you are editing is stronger)")
        sw.toggled.connect(self._show_regions)
        s.body.addWidget(sw)
        for i, r in enumerate(regs):
            card = _item()
            cv = QVBoxLayout(card)
            cv.setContentsMargins(8, 6, 8, 6)
            cv.setSpacing(4)
            head = QHBoxLayout()
            from ..regionview import colour_of
            sw_c = QLabel("")
            sw_c.setFixedSize(14, 14)
            sw_c.setStyleSheet(f"background: {colour_of(i)}; border: 1px solid {theme.tokens()['line_strong']}; border-radius: 3px;")
            sw_c.setToolTip("Its colour in the 3D view")
            head.addWidget(sw_c)
            name = wid(QLineEdit(r["name"]), f"mesh.region.{i}.name")
            name.setToolTip("Name of this refinement region")
            name.editingFinished.connect(lambda i=i, w=name: self._region_edit(i, name=w.text()))
            head.addWidget(name, 1)
            head.addWidget(_muted("cylinder" if r.get("shape") == "cylinder" else "box"))
            look = wid(QPushButton("◎"), f"mesh.region.{i}.show")
            look.setObjectName("ghost")
            look.setToolTip("Show this region in the 3D view: zoom to it and draw it stronger")
            look.clicked.connect(lambda _=False, i=i: self._region_look(i))
            head.addWidget(look)
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
            n, txt, big = region_cells(r)
            est = wid(QLabel(txt), f"mesh.region.{i}.cells")
            est.setWordWrap(True)
            est.setStyleSheet(f"color: {theme.tokens()['warn_text' if big else 'muted']};")
            est.setToolTip("A rough count of the cells this region alone makes (tetrahedra of the region's cell size)")
            cv.addWidget(est)
            if r.get("shape") != "cylinder":
                fit = wid(QComboBox(), f"mesh.region.{i}.fit")
                fit.addItem("Fit around part…", None)
                for b in st["body_list"]:
                    if b["role"] == "solid":
                        fit.addItem(b["name"], b["id"])
                fit.setToolTip("Put this box round a part (15 % of its size clear of it on every side)")
                fit.activated.connect(lambda k, i=i, fit=fit: fit.itemData(k) is not None and self._region_fit(i, fit.itemData(k)))
                cv.addWidget(fit)
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
        if st["study"] == "external_aero":                 # (regions made from the rotating zones of a flow study)
            zones = bool(st.get("moving_zones")) and bool(st.get("flow_dir"))
            row = QHBoxLayout()
            for key, label, tip in (
                    ("wake", "＋ Wake behind rotating zones",
                     "A cylinder of zone-size cells downstream of each rotating zone (two diameters long), to carry the wake and its tip vortices"),
                    ("tips", "＋ Tip-vortex rings",
                     "A ring of fine cells along the path of the blade-tip vortices (0.65-1.0 zone radius, from the zone into the wake, one "
                     "radius long): resolves the tip vortices without refining the whole wake. Use with the DES flow model.")):
                b = wid(QPushButton(label), f"mesh.region.add_{key}")
                b.setObjectName("ghostBorder")
                b.setEnabled(zones)
                b.setToolTip(tip if zones else "Not yet: add a rotating (moving) zone in Setup first - these regions follow the zones")
                b.clicked.connect(lambda _=False, k=key: self._region_add(k))
                row.addWidget(b)
            row.addStretch(1)
            s.body.addLayout(row)
            if not zones:
                s.body.addWidget(_muted("Wake and tip-vortex regions are made from the rotating zones: add one in Setup to use them."))
        self.v.addWidget(s)

    def _show_regions(self, on: bool):
        self.show_regions = bool(on)
        self._sig = None
        self.panel.draw_marks()

    def _region_look(self, i):
        """Zoom to a region and draw it stronger."""
        try:
            r = self._regions_state()[i]
            self.active_region = i
            self.show_regions = True
            v = self.panel.view
            v.set_focus(list(r["min"]) + list(r["max"]))
            v.fit()
            QTimer.singleShot(250, lambda: v.set_focus(None))        # (after the fit has settled: the Fit button shows everything again)
            self._sig = None
            self.panel.draw_marks()
        except Exception as e:  # noqa: BLE001
            self.panel._fail(e)

    def _region_fit(self, i, body_id):
        try:
            b = next(b for b in self.engine.state()["body_list"] if b["id"] == body_id)
            regs = self._regions_state()
            regs[i]["min"], regs[i]["max"] = fit_box(b["bbox"])
            self._save_regions(regs)
        except Exception as e:  # noqa: BLE001
            self.panel._fail(e)

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
            if kind in ("wake", "tips"):
                st = self.engine.state()
                self._save_regions(wake_regions(st) if kind == "wake" else tip_rings(st))
                self.panel.engine.message.emit("Wake regions added downstream of the rotating zones (zone cell size, two diameters long)"
                                               if kind == "wake" else "Tip-vortex rings added behind the rotating zones (cells of 1/50 of the "
                                               "zone radius)", False)
                return
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
    def _layers(self, st, m):
        ly = m.get("layers") or {}
        s = self._sec("Wall layers (inflation / prism)", "mesh.layers", True)       # (open: this is where the layers are set)
        s.body.addWidget(_muted("Thin cells stacked on the walls to resolve the boundary layer (friction, separation, y+). 0 layers = off. "
                                + ("They are made together with the hex-dominant mesh (snappyHexMesh); the statistics report how much of the "
                                   "wall got them." if m.get("method") == "hex" else
                                   "For the tetrahedral mesh OpenFOAM adds them just before the run (snappyHexMesh, layer-only), so you see them in "
                                   "the run's mesh, not in the mesh drawn here; where the space is too tight it leaves them out, and the run log "
                                   "reports the coverage.")))
        f = ParamForm([{"key": "n", "label": "number of layers", "unit": "", "default": 0, "min": 0, "max": 20},
                       {"key": "growth", "label": "growth ratio", "unit": "", "default": 1.2, "min": 1.0, "max": 2.0},
                       {"key": "first", "label": "first layer thickness (0 = automatic)", "unit": "mm", "default": 0.0, "min": 0.0}],
                      {"n": ly.get("n", 0), "growth": ly.get("growth", 1.2), "first": (ly.get("first") or 0) * M}, "mesh.layers")
        f.changed.connect(lambda k, v, f=f: self._layers_set(f.values()))
        s.body.addWidget(f)
        from ..meshtools import layer_numbers
        ln = layer_numbers(st)
        if ln:
            txt = (f"{ln['n']} layers, growing by {fmt_num(round(ln['growth'], 3))}: the first is {fmt_num(round(ln['first'] * M, 4))} mm"
                   f"{' (automatic)' if not ly.get('first') else ''}, all together {fmt_num(round(ln['total'] * M, 3))} mm.")
            if ln.get("delta"):
                txt += (f" The boundary layer is about {fmt_num(round(ln['delta'] * M, 2))} mm thick: {fmt_num(round(ln['cells_across_delta'], 1))} "
                        f"of the layers lie inside it" + (" (a laminar boundary layer needs about ten)." if ln.get("laminar") else "."))
        elif int(ly.get("n") or 0) > 0:
            txt = "Set a flow speed above 0 in the Setup step to see the thickness of the layers."
        else:
            txt = "No layers: the cells next to the walls are as big as the surface cells."
        summ = wid(QLabel(txt), "mesh.layers.summary")
        summ.setWordWrap(True)
        summ.setObjectName("muted")
        s.body.addWidget(summ)
        sw = wid(QCheckBox("Show the layers in the 3D view"), "mesh.layers.show")
        sw.setChecked(self.show_layers)
        sw.setEnabled(int(ly.get("n") or 0) > 0)
        sw.setToolTip("Draw a translucent shell round the object as thick as all the layers together (an approximation at sharp corners)"
                      if sw.isEnabled() else "Set a number of layers first")
        sw.toggled.connect(self._show_layers)
        s.body.addWidget(sw)
        self.v.addWidget(s)

    def _show_layers(self, on: bool):
        self.show_layers = bool(on)
        self._sig = None
        self.panel.draw_marks()

    def _layers_set(self, v):
        try:
            self.engine.project().set_mesh(layers={"n": int(v["n"]), "growth": v["growth"], "first_mm": v["first"] or None})
            self.engine.changed.emit()
        except Exception as e:  # noqa: BLE001
            self.panel._fail(e)
