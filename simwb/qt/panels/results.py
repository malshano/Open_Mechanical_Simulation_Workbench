"""The results step panel."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from .. import colormaps
from ..ids import wid
from .common import _h, _muted, KPI, _Panel


# ---------------------------------------------------------------------------------------------------------- results
class ResultsPanel(_Panel):
    def __init__(self, engine, parent=None):
        super().__init__(engine, parent)
        self.fields: list = []
        self.kpis = QVBoxLayout()
        self.v.addWidget(_h("Summary"))
        self.v.addLayout(self.kpis)
        self.v.addWidget(_h("Show"))
        self.field = wid(QComboBox(), "results.field")
        self.field.currentIndexChanged.connect(lambda _=0: self.changed.emit())
        self.v.addWidget(self.field)
        row = QHBoxLayout()
        self.cmap = wid(QComboBox(), "results.colormap")
        self.cmap.addItems(colormaps.NAMES)
        self.cmap.currentIndexChanged.connect(lambda _=0: self.changed.emit())
        self.rev = wid(QCheckBox("Reverse"), "results.reverse")
        self.rev.toggled.connect(lambda _=False: self.changed.emit())
        row.addWidget(QLabel("Colours"))
        row.addWidget(self.cmap, 1)
        row.addWidget(self.rev)
        self.v.addLayout(row)
        row = QHBoxLayout()
        self.bands = wid(QSpinBox(), "results.bands")
        self.bands.setRange(0, 40)
        self.bands.setSpecialValueText("smooth")
        self.bands.valueChanged.connect(lambda _=0: self.changed.emit())
        row.addWidget(QLabel("Colour bands"))
        row.addWidget(self.bands)
        row.addStretch(1)
        self.v.addLayout(row)
        self.deform = wid(QCheckBox("Show the deformation"), "results.deform")
        self.deform.setChecked(True)
        self.deform.toggled.connect(lambda _=False: self.changed.emit())
        self.v.addWidget(self.deform)
        self.edges = wid(QCheckBox("Show the mesh edges"), "results.edges")
        self.edges.toggled.connect(lambda _=False: self.changed.emit())
        self.v.addWidget(self.edges)
        self.text = wid(_muted(), "results.details")
        self.v.addWidget(_h("Details"))
        self.v.addWidget(self.text)
        self.v.addStretch(1)

    def request(self):
        return {"field": self.field.currentText(), "cmap": self.cmap.currentText(), "reverse": self.rev.isChecked(),
                "bands": self.bands.value(), "deform": None if self.deform.isChecked() else 0, "edges": self.edges.isChecked()}

    def _tiles(self, st):
        while self.kpis.count():
            w = self.kpis.takeAt(0).widget()
            if w:
                w.deleteLater()
        s = (st["solve"].get("summary") or {})
        tiles = []
        if "max_von_mises_MPa" in s:
            sf = s.get("min_safety_factor")
            tiles.append(("Max von Mises stress", f"{s['max_von_mises_MPa']:.1f}", "MPa", "", ""))
            tiles.append(("Max deformation", f"{s.get('max_displacement_mm', 0):.4f}", "mm", "", ""))
            if sf:
                tiles.append(("Safety factor (yield)", f"{sf:.2f}", "", "ok" if sf >= 2 else "warn" if sf >= 1.2 else "bad",
                              "adequate margin" if sf >= 2 else "thin margin" if sf >= 1.2 else "yields"))
        elif "max_temperature_C" in s:
            tiles.append(("Max temperature", f"{s['max_temperature_C']:.1f}", "°C", "", ""))
            if "min_temperature_C" in s:
                tiles.append(("Min temperature", f"{s['min_temperature_C']:.1f}", "°C", "", ""))
        elif "modes" in s and s["modes"]:
            f = s["modes"][0].get("frequency_Hz")
            tiles.append(("First natural frequency", f"{f:.1f}" if f else "-", "Hz", "", ""))
        grid = QWidget()
        g = QGridLayout(grid)
        g.setContentsMargins(0, 0, 0, 0)
        for i, t in enumerate(tiles):
            g.addWidget(KPI(*t), i // 2, i % 2)
        self.kpis.addWidget(grid)

    def refresh(self, st):
        if not st:
            return
        try:
            infos = self.engine.result_fields() if st["solve"].get("status") in ("done", "stale") else []
        except Exception:  # noqa: BLE001
            infos = []
        names = [f["name"] for f in infos]
        if names != [self.field.itemText(i) for i in range(self.field.count())]:
            cur = self.field.currentText()
            self.field.blockSignals(True)
            self.field.clear()
            self.field.addItems(names)
            if cur in names:
                self.field.setCurrentText(cur)
            self.field.blockSignals(False)
        self.fields = infos
        self._tiles(st)
        try:
            self.text.setText(self.engine.summary_text().replace("\n", "<br>") if infos else "No results yet. Run the simulation.")
        except Exception:  # noqa: BLE001
            self.text.setText("")
