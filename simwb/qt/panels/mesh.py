"""The mesh step panel: resolution, mesh type, generate, statistics with a verdict and a quality histogram, how to look at the mesh (colour, cut,
the poorest cells), and the advanced options (sizes, part sizing, refinement regions, wall layers)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import QButtonGroup, QCheckBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSlider, QWidget

from ... import project as P

from .. import mode, theme
from ..charts import QualityHist
from ..forms import fmt_num
from ..ids import wid
from ..meshtools import Budget, LayerCard, MeshCheck, StatsGrid, mesh_notes, stats_rows
from .common import _h, _muted, _Panel, _seg
from .mesh_adv import MeshAdvanced


def verdict(q: dict, stats: dict | None = None) -> tuple:
    """(tone, words) for the cell quality of a mesh. A hex-dominant mesh is judged by checkMesh's numbers, its layers and warnings."""
    s = stats or {}
    if s.get("method") == "hex" and s.get("hex"):
        f, x = s.get("foam_quality") or {}, s["hex"]
        warn = []
        if f.get("non_orthogonality_max", 0) > 70:
            warn.append(f"max non-orthogonality {f['non_orthogonality_max']:.0f}° (above 70° OpenFOAM may be unstable)")
        if f.get("skewness_max", 0) > 4:
            warn.append(f"max skewness {f['skewness_max']:.1f} is above 4")
        warn += [f"checkMesh: {t}" for t in x.get("problems") or []]
        layers = (s.get("layers") or {})
        if x.get("layers_extruded_percent") is not None and x["layers_extruded_percent"] < 50 and layers.get("n"):
            warn.append(f"only {x['layers_extruded_percent']:.0f} % of the wall faces got layers: refine the thin parts")
        warn += [w for w in x.get("warnings") or [] if "concave" not in w.lower()]
        return ("warn", "; ".join(warn)) if warn else ("ok", "Mesh looks good")
    n_poor, mn = q.get("n_poor", 0), q.get("min", 1)
    if n_poor == 0 and mn >= 0.2:
        return "ok", "Good: no poor cells"
    if n_poor == 0:
        return "warn", f"Acceptable: no cell below 0.1, the worst is {fmt_num(round(mn, 2))}"
    return "bad", f"{n_poor} poor cell(s) (below 0.1): look at them with 'Show cells below', or refine there"


class MeshPanel(_Panel):
    PRESETS = [("quick", "Quick"), ("coarse", "Coarse"), ("medium", "Medium"), ("fine", "Fine"), ("very_fine", "Very fine")]
    COLORS = [("quality", "Quality", "Colour each cell by its shape quality (1 ideal, 0 flat or degenerate)"),
              ("size", "Cell size", "Colour each cell by its size (edge length in mm, logarithmic): where the cells are fine and where coarse"),
              ("region", "Region", "Colour the cells by what they belong to: each body, the object's surface and the far field"),
              ("aspect", "Aspect ratio", "Colour each cell by its aspect ratio (longest edge / smallest height; 1.22 is a regular "
                                         "tetrahedron): slivers and needles stand out"),
              ("growth", "Size jump", "Colour each cell by the largest size ratio to a neighbour (1 = same size; a smooth mesh stays "
                                      "below about 1.5)"),
              ("volume", "Cell volume", "Colour each cell by its volume (mm³)"),
              ("layers", "Wall layers", "Show the mesh the solver runs, coloured by layer (needs wall layers on a tetrahedral mesh): a cut through "
                                        "the object shows each layer next to the wall")]
    TET_ONLY = ("size", "aspect", "growth", "volume")

    def bind(self, win):
        super().bind(win)
        win.view.facePicked.connect(self._face_picked)

    def shown(self):
        self.draw_marks()

    def left(self):
        self.set_picking_part(False)
        self.adv.xform.stop()
        v = self.view
        v.region_view.clear()
        v.layer_view.clear()
        v.set_labels([])
        v.set_face_tints({})
        v.set_selection([])

    def set_picking_part(self, on: bool):
        """Clicking a part in the 3D view gives it a cell size (Part sizing)."""
        on = bool(on)
        if on == getattr(self, "picking_part", False):
            return
        self.picking_part = on
        if self.view is not None:
            self.view.set_pick_mode("face" if on else None)
            self.view.set_selection([])
        b = getattr(self.adv, "pick_btn", None)
        if b is not None and b.isChecked() != on:
            b.blockSignals(True)
            b.setChecked(on)
            b.blockSignals(False)
        if self.win is not None:
            self.win.shell.sync()

    def _face_picked(self, fid, add):
        if getattr(self, "picking_part", False) and self.win is not None and self.win.step == "mesh":
            self.adv.part_from_face(int(fid))

    def draw_marks(self):
        """The refinement regions as translucent boxes and cylinders with their names, and the faces that have a part sizing in colour."""
        if self.win is None or self.win.step != "mesh" or not self.engine.pid:
            return
        st = self.engine.state()
        m = st["mesh"]
        regs = (m.get("regions") or []) if self.adv.show_regions else []
        if self.adv.xform.active:
            self.adv.xform._preview()                                 # (a region is being moved or turned: the preview, not the stored one)
        else:
            self.view.region_view.set(regs, self.adv.active_region)
        from ..meshtools import layer_numbers
        lay = layer_numbers(st) if (self.adv.show_layers and self.color != "layers") else None     # (the real layers are drawn: no shell beside them)
        faces = [f for f in self.engine.project().faces() if f.get("kind") == "body"] if lay else []
        self.view.layer_view.set(faces, lay["total"] if lay else 0.0, (self.engine.pid, st["geometry"].get("updated")))
        from ..regionview import colour_of
        self.view.set_labels([{"point": self.view.region_view.label_point(r), "text": f"{r.get('name', '')}: {fmt_num(round(r['size'] * 1000, 3))} mm",
                               "color": colour_of(i), "key": f"region{i}"} for i, r in enumerate(regs)] if regs else [])
        tints = {}
        for r in m.get("refinements") or []:
            for f in r.get("faces") or []:
                tints[int(f)] = "#14b8a6"
        self.view.set_face_tints(tints)

    def __init__(self, engine, parent=None):
        super().__init__(engine, parent)
        self.picking_part = False
        self.color = "quality"
        self.axis = ""
        self.frac = 0.5
        self.poor_on = False
        self.poor_thr = 0.1
        self.method_h = _h("Mesh type")
        self.v.addWidget(self.method_h)
        w, self.method_grp, self.method_btns = _seg([("tet", "Tetrahedral"), ("hex", "Hex-dominant")], "tet", prefix="mesh.method")
        for k, b in self.method_btns.items():
            b.clicked.connect(lambda _=False, k=k: self._method(k))
        self.method_box = w
        self.method_note = wid(_muted(), "mesh.method.note")
        self.v.addWidget(w)
        self.v.addWidget(self.method_note)
        self.v.addWidget(_h("Resolution"))
        w, self.preset_grp, self.preset_btns = _seg(self.PRESETS, "medium", prefix="mesh.preset")
        for k, b in self.preset_btns.items():
            b.clicked.connect(lambda _=False, k=k: self._preset(k))
        self.v.addWidget(w)
        self.adv = MeshAdvanced(self)
        self.v.addWidget(self.adv)
        self.gen = wid(QPushButton("Generate mesh"), "mesh.generate")
        self.gen.setObjectName("primary")
        self.gen.clicked.connect(self._generate)
        self.v.addWidget(self.gen)
        self.stale_note = wid(QLabel(""), "mesh.stale")
        self.stale_note.setWordWrap(True)
        self.v.addWidget(self.stale_note)
        self.stats_h = _h("Statistics")
        self.v.addWidget(self.stats_h)
        self.stats = wid(_muted(), "mesh.stats")
        self.v.addWidget(self.stats)
        self.grid = wid(StatsGrid(), "mesh.stats.table")
        self.v.addWidget(self.grid)
        self.layers_h = _h("Wall layers made")
        self.v.addWidget(self.layers_h)
        self.layer_card = wid(LayerCard(), "mesh.layers.card")
        self.layer_card.makeRequested.connect(self.make_layers)
        self.layer_card.zoomRequested.connect(self.zoom_layers)
        self.v.addWidget(self.layer_card)
        self.cut_h = _h("Cut through the mesh")
        self.v.addWidget(self.cut_h)
        w, self.cut_grp, self.cut_btns = _seg([("", "Off"), ("x", "X"), ("y", "Y"), ("z", "Z")], "", prefix="mesh.cut")
        for k, b in self.cut_btns.items():
            b.clicked.connect(lambda _=False, k=k: self._set(axis=k))
            b.setToolTip("No cut: the outside of the mesh" if not k else f"Cut the mesh across the {k.upper()} axis (move the cut with the slider)")
        self.cut_box = w
        self.v.addWidget(w)
        self.slider = wid(QSlider(Qt.Horizontal), "mesh.cut_position")
        self.slider.setRange(0, 1000)
        self.slider.setValue(500)
        self.slider.setToolTip("Where the cut is, from one end of the mesh to the other")
        self.slider.valueChanged.connect(lambda x: self._set(frac=x / 1000))
        self.v.addWidget(self.slider)
        self.cut_hint = _muted("A cut shows the cells inside: the air around the object, where the object was cut out of it, "
                               "and how the cells grow with distance (colour by Cell size).")
        self.v.addWidget(self.cut_hint)
        self.hist_h = _h("Cell quality")
        self.v.addWidget(self.hist_h)
        self.hist = QualityHist()
        self.v.addWidget(self.hist)
        self.color_h = _h("Colour the mesh by")
        self.v.addWidget(self.color_h)
        self.color_btns, self.color_grp = {}, QButtonGroup(self)
        self.color_grp.setExclusive(True)
        self.color_rows = []
        for row in (self.COLORS[:3], self.COLORS[3:6], self.COLORS[6:]):
            w, _grp, btns = _seg([(k, t) for k, t, _ in row], "quality", prefix="mesh.color")
            for (k, _t, tip), b in zip(row, btns.values()):
                _grp.removeButton(b)
                self.color_grp.addButton(b)
                b.setToolTip(tip)
                b.clicked.connect(lambda _=False, k=k: self._set(color=k))
                self.color_btns[k] = b
            self.color_rows.append(w)
            self.v.addWidget(w)
        self.color_note = wid(_muted(""), "mesh.color.note")
        self.v.addWidget(self.color_note)
        self.budget = wid(Budget(), "mesh.budget")
        self.v.addWidget(self.budget)
        row = QHBoxLayout()
        self.poor_chk = wid(QCheckBox("Show cells below"), "mesh.poor.on")
        self.poor_chk.setToolTip("Show only the cells below this quality, on the model, to see where the mesher struggles (thin gaps, small faces)")
        self.poor_chk.toggled.connect(self._poor_toggled)
        self.poor_edit = wid(QLineEdit("0.1"), "mesh.poor.threshold")
        self.poor_edit.setValidator(QDoubleValidator(0.01, 0.9, 3, self.poor_edit))
        self.poor_edit.setFixedWidth(60)
        self.poor_edit.setToolTip("Quality threshold (0.01 to 0.9)")
        self.poor_edit.editingFinished.connect(self._poor_thr)
        self.poor_zoom = wid(QPushButton("Zoom to worst"), "mesh.poor.zoom")
        self.poor_zoom.setObjectName("ghostBorder")
        self.poor_zoom.setToolTip("Look at the poorest cell")
        self.poor_zoom.clicked.connect(self.zoom_worst)
        row.addWidget(self.poor_chk)
        row.addWidget(self.poor_edit)
        row.addWidget(self.poor_zoom)
        row.addStretch(1)
        self.poor_row = QWidget()
        self.poor_row.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        self.v.addWidget(self.poor_row)
        self.poor_info = wid(_muted(), "mesh.poor.info")
        self.v.addWidget(self.poor_info)
        self.verdict = wid(QLabel(""), "mesh.verdict")
        self.verdict.setWordWrap(True)
        self.v.addWidget(self.verdict)
        self.notes = wid(QLabel(""), "mesh.verdict.notes")
        self.notes.setWordWrap(True)
        self.notes.setTextFormat(Qt.RichText)
        self.v.addWidget(self.notes)
        self.next = wid(QPushButton("Next: solve →"), "mesh.next")
        self.next.setObjectName("primary")
        self.next.setToolTip("Go on to step 4, Solve")
        self.next.clicked.connect(lambda: self.win.go("solve"))
        self.v.addWidget(self.next)
        self.check_h = _h("Mesh sensitivity")
        self.check_h.setToolTip("Solves the study again on a finer mesh and compares the key results: how much they still change with the mesh")
        self.v.addWidget(self.check_h)
        self.check = MeshCheck(self)
        self.v.addWidget(self.check)
        self.v.addStretch(1)
        self._budget_key = None
        self.hex = False
        self.layers_ok = False                              # (a layered mesh exists: the colour "Wall layers" can be drawn)

    # ------------------------------------------------------------------ choices
    def _set(self, **kw):
        for k, val in kw.items():
            setattr(self, k, val)
        if kw.get("color") == "layers" and not self.axis and "axis" not in kw:
            self._default_cut()                 # (the layers lie on the walls: they are seen in a cut through the object, so open one)
        self.slider.setEnabled(bool(self.axis))
        self.changed.emit()
        if "color" in kw:
            self.draw_marks()                   # (the shell that previews the layers is not drawn beside the real ones)

    def _default_cut(self):
        try:
            ax, fr = self.engine.project().layers_default_cut()
        except Exception:  # noqa: BLE001 - a help: without it the user chooses the cut
            return
        self.axis, self.frac = ax, fr
        self.cut_btns[ax].setChecked(True)
        self.slider.blockSignals(True)
        self.slider.setValue(int(round(fr * 1000)))
        self.slider.blockSignals(False)

    def make_layers(self):
        """Make the wall layers again on the mesh that is there (a job)."""
        try:
            j = self.engine.project().generate_layers()
        except Exception as e:  # noqa: BLE001
            self._fail(e)
            raise
        self.engine.message.emit("Making the wall layers (OpenFOAM snappyHexMesh, as the run will)", False)
        self.engine.changed.emit()
        return j

    def layers_focus(self):
        """The box the camera fits to while the layers are shown: round the edge of the object in the cut, as wide as ten times the layers, or None."""
        if self.color != "layers" or not self.axis or not self.engine.pid:
            return None
        pr = self.engine.project()
        key = (self.engine.pid, self.axis, round(self.frac, 4), (pr.data["mesh"].get("stats") or {}).get("seconds"),
               ((pr.data["mesh"].get("stats") or {}).get("layers") or {}).get("seconds"))
        if getattr(self, "_focus_key", None) == key:
            return self._focus_box
        self._focus_key = key
        try:
            m = pr.load_mesh()
            ax = "xyz".index(self.axis)
            lo, hi = float(m["nodes"][:, ax].min()), float(m["nodes"][:, ax].max())
            f = pr.layer_focus(self.axis, lo + (hi - lo) * self.frac)
        except Exception:  # noqa: BLE001
            f = None
        self._focus_box = f["box"] if f else None
        return self._focus_box

    def zoom_layers(self):
        """Turn the 3D view to the cut and zoom in to the layers at the edge of the object."""
        if self.color != "layers":
            self._set(color="layers")
            self.color_btns["layers"].setChecked(True)
        box = self.layers_focus()
        if box is None:
            raise ValueError("There are no layers to zoom to: make the layers first")
        self.view.set_focus(box)
        self.view.fit(direction={"x": [1, 0, 0], "y": [0, 1, 0], "z": [0, 0, 1]}[self.axis])
        return {"box": box}

    def _preset(self, k):
        try:
            self.engine.set_mesh_preset(k)
        except Exception as e:  # noqa: BLE001
            self._fail(e)

    def _method(self, k):
        try:
            self.engine.project().set_mesh(method=k)
            self.engine.changed.emit()
        except Exception as e:  # noqa: BLE001
            self._fail(e)
            self.engine.changed.emit()

    def _generate(self):
        try:
            self.engine.generate_mesh()
            self.engine.changed.emit()
            self.stats.setText("Meshing…")
        except Exception as e:  # noqa: BLE001
            self._fail(e)

    def _poor_toggled(self, on):
        self.poor_on = on
        self.poor_zoom.setEnabled(on)
        self.poor_zoom.setToolTip("Look at the poorest cell" if on else "Switch on 'Show cells below' first")
        self.changed.emit()

    def _poor_thr(self):
        try:
            self.poor_thr = min(0.9, max(0.01, float(self.poor_edit.text())))
        except ValueError:
            return
        if self.poor_on:
            self.changed.emit()

    def zoom_worst(self):
        """Turn the 3D view to the poorest cell."""
        d = self.engine.mesh_payload(None, 0.0, "quality", self.poor_thr)
        w = d.get("worst")
        if not w:
            raise ValueError("There are no cells below that quality")
        c, r = w["center"], max(w["size"], 1e-4) * 4
        self.view.set_camera(position=[c[0] + r, c[1] - r, c[2] + r * 0.7], focal_point=c, view_up=[0, 0, 1])
        self.view.ren.ResetCameraClippingRange()
        return w

    def request(self):
        color = self.color
        if self.hex and color in self.TET_ONLY:              # (the hex-dominant mesh is shown by its quality only)
            color = "quality"
        if color == "layers" and not self.layers_ok:
            color = "quality"
        return self.axis or None, self.frac, color, (self.poor_thr if self.poor_on else None)

    def mesh_check(self, levels: int):
        """The mesh-sensitivity check: one level finer (levels=1) or the three-level grid-convergence check (levels=2); a job."""
        try:
            j = self.engine.project().mesh_check(levels=levels)
        except Exception as e:  # noqa: BLE001
            self._fail(e)
            raise
        self.engine.message.emit("Three-level mesh check started" if levels == 2 else
                                 "Mesh check started (re-meshing one level finer and re-solving)", False)
        self.engine.changed.emit()
        return j

    # ------------------------------------------------------------------ what the panel shows
    def refresh(self, st):
        if not st:
            return
        m = st["mesh"]
        aero = st["study"] == "external_aero"
        self.method_box.setVisible(aero)
        self.method_h.setVisible(aero)
        self.method_note.setVisible(aero)
        if aero:
            self.method_btns["hex"].setEnabled(not st.get("hex_unsupported"))
            self.method_btns["hex"].setToolTip(st.get("hex_unsupported") or "Mostly hexahedral cells made by OpenFOAM snappyHexMesh: usually fewer, "
                                                                           "more orthogonal cells and a finer wake")
            self.method_btns["tet" if m.get("method", "tet") != "hex" else "hex"].setChecked(True)
            self.method_note.setText(st.get("hex_unsupported") and f"Hex-dominant is not available: {st['hex_unsupported']}" or
                                     ("snappyHexMesh (OpenFOAM): about a minute per 100,000 cells." if m.get("method") == "hex"
                                      else "Tetrahedral cells made by Gmsh: works for everything (rotors, moving zones, symmetry planes)."))
        if m.get("preset") in self.preset_btns:
            self.preset_btns[m["preset"]].setChecked(True)
        self.adv.refresh(st)
        self.adv.setVisible(not mode.simple())              # (Simple mode: the presets only)
        s = m.get("stats")
        t = theme.tokens()
        have = bool(s) and m.get("status") in ("ready", "stale")
        self.hex = bool(s and s.get("method") == "hex")
        if have:
            q = s["quality"]
            extra = ""
            if s.get("method") == "hex" and s.get("cell_types"):
                ct = s["cell_types"]
                extra = f"<br>{100 * ct['hexahedra'] / max(s['cells'], 1):.0f} % hexahedra"
            if s.get("method") == "hex" and s.get("hex"):
                sizes = f"surface {fmt_num(round(s['hex']['surface_cell_mm'], 3))} mm, background {fmt_num(round(s['hex']['base_mm'], 3))} mm"
                if s["hex"].get("layers_extruded_percent") is not None:
                    extra += f"<br>{s['hex']['layers_extruded_percent']:.0f} % of the wall faces got layers"
            else:
                sizes = (f"near-wall {fmt_num(round(s['sizes']['near_wall'] * 1000, 3))} mm, "
                         f"bulk {fmt_num(round(s['sizes']['body'] * 1000, 3))} mm")
            self.stats.setText(f"<b>{s['cells']:,}</b> cells · {s['nodes']:,} nodes · {sizes}{extra}<br>quality: min {q['min']:.2f}, mean {q['mean']:.2f}, "
                               f"{q['n_poor']} poor cells")
            self.grid.set_rows(stats_rows(st), theme.effective())
            tone, words = verdict(q, s)
            col = {"ok": t["ok_text"], "warn": t["warn_text"], "bad": t["bad_text"]}[tone]
            self.verdict.setStyleSheet(f"color: {col}; font-weight: 600;")
            self.verdict.setText(("✓ " if tone == "ok" else "⚠ ") + words)
            _tone, warn, info = mesh_notes(st)
            html = "".join(f"<div style='color:{t['warn_text']}'>⚠ {w}</div>" for w in warn) + \
                "".join(f"<div style='color:{t['muted']}'>{x}</div>" for x in info)
            self.notes.setText(html)
            self.notes.setProperty("api_text", " | ".join(warn + info))
            self.hist.set_counts(q.get("histogram"))
            self.gen.setText("Regenerate mesh")
        else:
            self.stats.setText("No mesh yet: choose a resolution and press Generate mesh." if m.get("status") != "running" else "Meshing…")
            self.grid.set_rows([])
            self.verdict.setText("")
            self.notes.setText("")
            self.notes.setProperty("api_text", "")
            self.hist.set_counts([])
            self.gen.setText("Generate mesh")
        j = P.JOBS.latest(self.engine.pid, "mesh") if self.engine.pid else None
        running = bool(j and j.status == "running")
        geo = st["geometry"].get("status") == "ready"
        self.gen.setEnabled(not running)                    # (without geometry it stays pressable: the click says what is missing)
        self.gen.setToolTip("Meshing is running" if running else "Make the cells of the model with these settings" if geo
                            else "Not yet: import the geometry first (step 1, Geometry)")
        if running:
            self.gen.setText("Meshing…")
        stale = m.get("status") == "stale"
        self.stale_note.setVisible(stale)
        self.stale_note.setText("⚠ The setup changed since this mesh was made: regenerate it." if stale else "")
        self.stale_note.setStyleSheet(f"color: {t['warn_text']};")
        for w in (self.stats_h, self.grid, self.hist_h, self.hist, self.poor_row, self.poor_info, self.next, self.notes):
            w.setVisible(have)
        lay_st = (s or {}).get("layers") or {}
        self.layers_ok = bool(have and not self.hex and lay_st.get("made"))
        want = int((m.get("layers") or {}).get("n") or 0) > 0
        show_card = bool(have and aero and not self.hex and (want or lay_st))
        self.layers_h.setVisible(show_card)
        self.layer_card.setVisible(show_card)
        if show_card:
            jm = P.JOBS.latest(self.engine.pid, "mesh") if self.engine.pid else None
            self.layer_card.set_data(st, t, bool(jm and jm.status == "running"))
        lb = self.color_btns["layers"]
        self.color_rows[2].setVisible(bool(aero and not self.hex))
        lb.setEnabled(self.layers_ok)
        lb.setToolTip(next(tip for c, _l, tip in self.COLORS if c == "layers") if self.layers_ok else
                      ("A hex-dominant mesh makes its layers itself and is shown by its quality and regions" if self.hex else
                       "No mesh with layers yet: set a number of layers (Wall layers, below) and generate the mesh, with OpenFOAM installed"))
        if self.color == "layers" and not self.layers_ok:
            self.color = "quality"
        tips = {c: tip for c, _l, tip in self.COLORS}
        for k in self.TET_ONLY:
            b = self.color_btns[k]
            b.setEnabled(not self.hex)
            b.setToolTip(tips[k] if not self.hex else
                         "Not for a hex-dominant mesh: it is shown by its cell quality only (these metrics need the tetrahedral mesh)")
        self.color_btns[self.request()[2] if self.color in self.color_btns else "quality"].setChecked(True)
        self.color_note.setVisible(have and self.hex)
        self.color_note.setText("The hex-dominant mesh is shown by its cell quality and its regions only." if self.hex else "")
        self._budget(st, have)
        jc = P.JOBS.latest(self.engine.pid, "check") if self.engine.pid else None
        ck = bool(jc and jc.status == "running")
        show_check = (have and st["solve"].get("status") in ("done", "stale")) or bool(st.get("mesh_check"))
        self.check_h.setVisible(show_check)
        self.check.setVisible(show_check)
        if show_check:
            self.check.refresh(st, ck, t)
        self.poor_zoom.setEnabled(bool(s) and self.poor_on)
        self.poor_zoom.setToolTip("Look at the poorest cell" if self.poor_on else "Switch on 'Show cells below' first")
        self.slider.setEnabled(bool(self.axis))
        if not (self.poor_on and s):
            self.poor_info.setText("")          # (when they are shown, the window writes how many cells are below the threshold)
        self.draw_marks()

    def _budget(self, st, have):
        """The cell budget of a tetrahedral mesh (computed once per mesh)."""
        s = st["mesh"].get("stats") or {}
        key = (self.engine.pid, s.get("cells"), s.get("seconds"), self.hex, theme.effective()) if have else None
        if key == self._budget_key:
            return
        self._budget_key = key
        d = None
        if have and not self.hex:
            try:
                d = self.engine.project().mesh_metrics()
            except Exception:  # noqa: BLE001
                d = None
        self.budget.set_data(d, theme.tokens())
