"""The geometry step panel."""
from __future__ import annotations

from PySide6.QtWidgets import QFileDialog, QPushButton
from ..ids import wid
from .common import _h, _muted, _Panel


# ---------------------------------------------------------------------------------------------------------- geometry
class GeometryPanel(_Panel):
    def __init__(self, engine, parent=None):
        super().__init__(engine, parent)
        self.v.addWidget(_h("Geometry"))
        self.btn = wid(QPushButton("Import CAD file (STEP, STL, OBJ)…"), "geometry.import")
        self.btn.setObjectName("primary")
        self.btn.clicked.connect(self._import)
        self.v.addWidget(self.btn)
        self.info = wid(_muted(), "geometry.info")
        self.v.addWidget(self.info)
        self.v.addStretch(1)

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import CAD", "", "CAD files (*.step *.stp *.stl *.obj);;All files (*)")
        if not path:
            return
        try:
            self.engine.import_step(path)
            self.info.setText("Importing… (the part appears here when it is done)")
        except Exception as e:  # noqa: BLE001
            self._fail(e)

    def refresh(self, st):
        if not st:
            return
        g = st["geometry"]
        if g.get("status") == "ready":
            rows = [f"<b>{b['name']}</b> — {b['role']}, {b['volume_cm3']:.2f} cm³" for b in st["body_list"]]
            self.info.setText(f"{st['face_count']} faces, {len(rows)} bod{'y' if len(rows) == 1 else 'ies'}<br>" + "<br>".join(rows))
        else:
            self.info.setText("No geometry yet. Import a CAD file to begin.")
