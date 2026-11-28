"""The Export block of the Mesh step: the mesh report (simwb/meshreport.py) as a web page (HTML) or a PDF. The buttons look and work like the Export buttons of the
Results step: a file chooser, then the file (`export(key, path)` is what the control API's `mesh.export` calls, without the chooser)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from ..ids import wid
from .common import _h


class MeshExport(QWidget):
    def __init__(self, panel, parent=None):
        super().__init__(parent)
        self.panel = panel
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        self.head = _h("Export")
        v.addWidget(self.head)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self.btns = {}
        for key, label, tip in (("html", "Mesh report (HTML)", "The mesh as a web page: statistics, settings, cell budget, findings, histograms and cuts through the mesh"),
                                ("pdf", "Mesh report (PDF)", "The same report as a PDF file")):
            b = wid(QPushButton(label), f"mesh.export.{key}")
            b.setObjectName("ghostBorder")
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, k=key: self._dialog(k))
            row.addWidget(b)
            self.btns[key] = b
        row.addStretch(1)
        v.addLayout(row)
        self.setVisible(False)

    def _dialog(self, key):
        ext = {"html": ("HTML report (*.html)", "html"), "pdf": ("PDF file (*.pdf)", "pdf")}[key]
        path, _ = QFileDialog.getSaveFileName(self, "Export the mesh report", f"{self.panel.engine.pid}-mesh.{ext[1]}", ext[0])
        if path:
            try:
                self.export(key, path)
                self.panel.win.statusBar().showMessage(f"Exported to {path}", 8000)
            except Exception as e:  # noqa: BLE001
                self.panel._fail(e)

    def export(self, key: str, path: str) -> str:
        """Write the mesh report of the open study: key html or pdf."""
        from ... import meshreport

        pr = self.panel.engine.project()
        if key == "html":
            return meshreport.write_html(pr, Path(path))
        if key == "pdf":
            return meshreport.write_pdf(pr, Path(path))
        raise ValueError("format must be html or pdf")
