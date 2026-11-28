"""The histogram of the metric the mesh is coloured by (cell size, aspect ratio, size jump, volume) under the cut controls of the Mesh step, and the
choice of cells it makes: a click on a bar of the histogram or on a band of the cell budget shows only those cells on the model.

Kept apart from the panel (simwb/qt/panels/mesh.py), which makes one of these and calls `refresh`; the panel reads `band` to ask the 3D view for the cells."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..ids import wid
from ..metrichist import MARKS, MetricHist, fmt_value
from .common import _h, _muted

METRICS = ("size", "aspect", "growth", "volume")        # (what has a histogram of its own; quality keeps the quality histogram of the panel)
WORDS = {"size": "Cell size", "aspect": "Aspect ratio", "growth": "Size jump", "volume": "Cell volume"}


class MetricViews(QWidget):
    changed = Signal()             # (the cells shown on the model changed: the window draws them)

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        self.head = _h("Cell size")
        v.addWidget(self.head)
        self.hist = wid(MetricHist(), "mesh.hist")
        self.hist.binClicked.connect(lambda lo, hi: self.highlight(self.which, lo, hi))
        v.addWidget(self.hist)
        self.marks = wid(_muted(""), "mesh.hist.marks")
        v.addWidget(self.marks)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self.info = wid(_muted(""), "mesh.band.info")
        self.clear_btn = wid(QPushButton("Show all cells"), "mesh.band.clear")
        self.clear_btn.setObjectName("ghostBorder")
        self.clear_btn.setToolTip("Show the whole mesh again")
        self.clear_btn.clicked.connect(self.clear)
        row.addWidget(self.info, 1)
        row.addWidget(self.clear_btn)
        self.band_row = QWidget()
        self.band_row.setLayout(row)
        v.addWidget(self.band_row)
        self.which: str | None = None
        self.data: dict | None = None                       # the mesh metrics (statistics, histograms, budget)
        self.band: tuple | None = None                      # (metric, low, high): only the cells in this range are drawn
        self._range = None
        self.setVisible(False)

    # ------------------------------------------------------------------ what is chosen
    def highlight(self, metric: str, lo: float, hi: float):
        """Show only the cells of `metric` between lo and hi (a click on that bar again shows all)."""
        if self.band and self.band[0] == metric and self.band[1] == lo and self.band[2] == hi:
            return self.clear()
        self.band = (metric, float(lo), float(hi))
        self.changed.emit()
        return self.band

    def clear(self):
        if self.band is None:
            return
        self.band = None
        self.changed.emit()

    def request(self):
        """The (low, high) the window draws, or None."""
        return None if self.band is None else (self.band[1], self.band[2])

    # ------------------------------------------------------------------ what is shown
    def set_metrics(self, d: dict | None):
        self.data = d
        if self.band and not d:
            self.band = None

    def refresh(self, color: str, have: bool):
        """Follow the colouring: the histogram of the metric the mesh is coloured by (none for quality, region, layers, zones: the panel's quality
        histogram is that), the marks, and the line that says which cells are shown."""
        m = color if color in METRICS and self.data and color in (self.data.get("metrics") or {}) else None
        if self.band and (m != self.band[0]):
            self.band = None
        self.which = m
        self.setVisible(bool(have and m))
        if not (have and m):
            self.hist.set_data("size", None, None)
            self.info.setText("")
            self.band_row.setVisible(False)
            return
        md = self.data["metrics"][m]
        rng = md["range"]                                    # (the colour bar of the 3D view: the bars have its colours)
        self.head.setText(f"{WORDS[m]}: how many cells")
        self.hist.set_data(m, md, rng, (self.band[1], self.band[2]) if self.band else None)
        marks = MARKS.get(m)
        self.marks.setVisible(bool(marks))
        self.marks.setText("Dashed line: " + "; ".join(t for _v, t in marks) if marks else "")
        self.band_row.setVisible(self.band is not None)
        if self.band:
            self.info.setText(self._band_text(md))
        self.setProperty("api_text", self.hist.property("api_text"))

    def _band_text(self, md: dict) -> str:
        m, lo, hi = self.band
        unit = f" {md['unit']}" if md.get("unit") and md["unit"] != "-" else ""
        upper = "" if hi == float("inf") else f" to {fmt_value(hi)}{unit}"
        n = self.cells_in_band
        shown = f"{n:,} cells" if n is not None else "These cells"
        return f"{shown} with {WORDS[m].lower()} from {fmt_value(lo)}{upper}"

    cells_in_band: int | None = None

    def set_shown(self, n):
        """How many cells the view drew for the chosen range (the window tells after it asked the engine)."""
        self.cells_in_band = n
        if self.band and self.data and self.which in (self.data.get("metrics") or {}):
            self.info.setText(self._band_text(self.data["metrics"][self.which]))
