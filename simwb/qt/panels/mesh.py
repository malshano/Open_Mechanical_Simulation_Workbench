"""The mesh step panel."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton, QSlider
from ..ids import wid
from .common import _h, _muted, _seg, _Panel


# ---------------------------------------------------------------------------------------------------------- mesh
class MeshPanel(_Panel):
    PRESETS = [("quick", "Quick"), ("coarse", "Coarse"), ("medium", "Medium"), ("fine", "Fine"), ("very_fine", "Very fine")]

    def __init__(self, engine, parent=None):
        super().__init__(engine, parent)
        self.color = "quality"
        self.axis = ""
        self.frac = 0.5
        self.v.addWidget(_h("Resolution"))
        w, self.preset_grp, self.preset_btns = _seg(self.PRESETS, "medium", prefix="mesh.preset")
        for k, b in self.preset_btns.items():
            b.clicked.connect(lambda _=False, k=k: self._preset(k))
        self.v.addWidget(w)
        self.gen = wid(QPushButton("Generate mesh"), "mesh.generate")
        self.gen.setObjectName("primary")
        self.gen.clicked.connect(self._generate)
        self.v.addWidget(self.gen)
        self.stats = wid(_muted(), "mesh.stats")
        self.v.addWidget(self.stats)
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

    def _generate(self):
        try:
            self.engine.generate_mesh()
            self.stats.setText("Meshing…")
        except Exception as e:  # noqa: BLE001
            self._fail(e)

    def request(self):
        return self.axis or None, self.frac, self.color

    def refresh(self, st):
        if not st:
            return
        m = st["mesh"]
        if m.get("preset") in self.preset_btns:
            self.preset_btns[m["preset"]].setChecked(True)
        s = m.get("stats")
        if s and m.get("status") in ("ready", "stale"):
            q = s["quality"]
            self.stats.setText(f"<b>{s['cells']:,}</b> cells · {s['nodes']:,} nodes<br>quality: min {q['min']:.2f}, mean {q['mean']:.2f}, "
                               f"{q['n_poor']} poor cells" + ("<br><i>The setup changed: regenerate.</i>" if m["status"] == "stale" else ""))
            self.gen.setText("Regenerate mesh")
        else:
            self.stats.setText("No mesh yet." if m.get("status") != "running" else "Meshing…")
            self.gen.setText("Generate mesh")
        self.slider.setEnabled(bool(self.axis))
