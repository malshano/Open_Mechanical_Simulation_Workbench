"""The mesh step panel: resolution, mesh type, generate, statistics with a verdict and a quality histogram, how to look at the mesh (colour, cut,
the poorest cells), and the advanced options (sizes, part sizing, refinement regions, wall layers)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSlider

from .. import mode, theme
from ..charts import QualityHist
from ..forms import fmt_num
from ..ids import wid
from .common import _h, _muted, _Panel, _seg
from .mesh_adv import MeshAdvanced


def verdict(q: dict) -> tuple:
    """(tone, words) for the cell quality of a mesh."""
    n_poor, mn = q.get("n_poor", 0), q.get("min", 1)
    if n_poor == 0 and mn >= 0.2:
        return "ok", "Good: no poor cells"
    if n_poor == 0:
        return "warn", f"Acceptable: no cell below 0.1, the worst is {fmt_num(round(mn, 2))}"
    return "bad", f"{n_poor} poor cell(s) (below 0.1): look at them with 'Show cells below', or refine there"


class MeshPanel(_Panel):
    PRESETS = [("quick", "Quick"), ("coarse", "Coarse"), ("medium", "Medium"), ("fine", "Fine"), ("very_fine", "Very fine")]

    def __init__(self, engine, parent=None):
        super().__init__(engine, parent)
        self.color = "quality"
        self.axis = ""
        self.frac = 0.5
        self.poor_on = False
        self.poor_thr = 0.1
        self.v.addWidget(_h("Mesh type"))
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
        self.stats = wid(_muted(), "mesh.stats")
        self.v.addWidget(self.stats)
        self.verdict = wid(QLabel(""), "mesh.verdict")
        self.verdict.setWordWrap(True)
        self.v.addWidget(self.verdict)
        self.v.addWidget(_h("Cell quality"))
        self.hist = QualityHist()
        self.v.addWidget(self.hist)
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
        self.v.addLayout(row)
        self.poor_info = wid(_muted(), "mesh.poor.info")
        self.v.addWidget(self.poor_info)
        self.v.addWidget(_h("Colour the mesh by"))
        w, self.color_grp, self.color_btns = _seg([("quality", "Quality"), ("size", "Cell size"), ("region", "Region")], "quality",
                                                  prefix="mesh.color")
        for k, b in self.color_btns.items():
            b.clicked.connect(lambda _=False, k=k: self._set(color=k))
        self.v.addWidget(w)
        self.v.addWidget(_h("Cut through the mesh"))
        w, self.cut_grp, self.cut_btns = _seg([("", "Off"), ("x", "X"), ("y", "Y"), ("z", "Z")], "", prefix="mesh.cut")
        for k, b in self.cut_btns.items():
            b.clicked.connect(lambda _=False, k=k: self._set(axis=k))
        self.v.addWidget(w)
        self.slider = wid(QSlider(Qt.Horizontal), "mesh.cut_position")
        self.slider.setRange(0, 1000)
        self.slider.setValue(500)
        self.slider.valueChanged.connect(lambda x: self._set(frac=x / 1000))
        self.v.addWidget(self.slider)
        self.v.addWidget(_muted("A cut shows the cells inside: the air around the object, where the object was cut out of it, "
                                "and how the cells grow with distance (colour by Cell size)."))
        self.v.addStretch(1)

    # ------------------------------------------------------------------ choices
    def _set(self, **kw):
        for k, val in kw.items():
            setattr(self, k, val)
        self.slider.setEnabled(bool(self.axis))
        self.changed.emit()

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
            self.stats.setText("Meshing…")
        except Exception as e:  # noqa: BLE001
            self._fail(e)

    def _poor_toggled(self, on):
        self.poor_on = on
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
        return self.axis or None, self.frac, self.color, (self.poor_thr if self.poor_on else None)

    # ------------------------------------------------------------------ what the panel shows
    def refresh(self, st):
        if not st:
            return
        m = st["mesh"]
        aero = st["study"] == "external_aero"
        self.method_box.setVisible(aero)
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
        if s and m.get("status") in ("ready", "stale"):
            q = s["quality"]
            extra = ""
            if s.get("method") == "hex" and s.get("cell_types"):
                ct = s["cell_types"]
                extra = f"<br>{100 * ct['hexahedra'] / max(s['cells'], 1):.0f} % hexahedra"
            self.stats.setText(f"<b>{s['cells']:,}</b> cells · {s['nodes']:,} nodes · near-wall {fmt_num(round(s['sizes']['near_wall'] * 1000, 3))} mm, "
                               f"bulk {fmt_num(round(s['sizes']['body'] * 1000, 3))} mm{extra}<br>quality: min {q['min']:.2f}, mean {q['mean']:.2f}, "
                               f"{q['n_poor']} poor cells" + ("<br><i>The setup changed: regenerate.</i>" if m["status"] == "stale" else ""))
            tone, words = verdict(q)
            col = {"ok": t["ok_text"], "warn": t["warn_text"], "bad": t["bad_text"]}[tone]
            self.verdict.setStyleSheet(f"color: {col}; font-weight: 600;")
            self.verdict.setText(("✓ " if tone == "ok" else "⚠ ") + words)
            self.hist.set_counts(q.get("histogram"))
            self.gen.setText("Regenerate mesh")
        else:
            self.stats.setText("No mesh yet." if m.get("status") != "running" else "Meshing…")
            self.verdict.setText("")
            self.hist.set_counts([])
            self.gen.setText("Generate mesh")
        self.poor_zoom.setEnabled(bool(s))
        self.slider.setEnabled(bool(self.axis))
        n = self.view.overlay_info if self.view is not None else {}
        if self.poor_on and s:
            self.poor_info.setText(f"Cells below {fmt_num(self.poor_thr)}: {s['quality']['n_poor'] if self.poor_thr == 0.1 else 'see the view'}")
        else:
            self.poor_info.setText("")
