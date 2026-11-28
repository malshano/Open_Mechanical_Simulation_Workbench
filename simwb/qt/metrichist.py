"""The histogram of a mesh metric (cell size, aspect ratio, size jump, volume, quality) in the Mesh step, and the clickable row of the cell budget.

The bars are coloured as the 3D view's legend colours the same values, so a bar and the cells it stands for have one colour; a dashed line marks
the value the mesh findings warn about (a size jump of 2, an aspect ratio of 20). A bar is clicked to show only the cells in it on the model
(`binClicked(lo, hi)`), and the selected bar is outlined.

The functions at the top are plain Python (no Qt): the bar colours, the marks and the text of a bar, so they can be tested without a window."""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from . import colormaps, theme

# the value a finding warns about (simwb/meshviz.findings), with what it says
MARKS = {"growth": [(1.5, "1.5: a smooth mesh stays below"), (2.0, "2: the findings warn above")],
         "aspect": [(20.0, "20: slivers and needles above")]}
LOG_METRICS = ("size", "aspect", "volume")          # (the histogram bins are geometric: equal steps of the ratio)


def bin_color(metric: str, value: float, rng) -> tuple:
    """(r, g, b) in 0..1 of a value on the colour bar the 3D view draws for the metric (quality: red is poor; the others turbo from low to high;
    the cell size on a logarithmic bar)."""
    lo, hi = float(rng[0]), float(rng[1])
    if metric == "size" and lo > 0 and value > 0:
        t = math.log(max(value, lo) / lo) / max(math.log(hi / lo), 1e-12)
    else:
        t = (value - lo) / max(hi - lo, 1e-12)
    t = min(max(t, 0.0), 1.0)
    c = colormaps.turbo(1 - t) if metric == "quality" else colormaps.turbo(t)
    return float(c[0]), float(c[1]), float(c[2])


def bin_centres(edges) -> list:
    e = np.asarray(edges, float)
    if len(e) > 1 and e[0] > 0:
        return list(np.sqrt(e[:-1] * e[1:]))
    return list((e[:-1] + e[1:]) / 2)


def mark_position(edges, value: float) -> float | None:
    """Where a value lies along the bars, in bins from the left edge (None outside the histogram)."""
    e = np.asarray(edges, float)
    if value < e[0] or value > e[-1]:
        return None
    i = int(min(np.searchsorted(e, value, side="right") - 1, len(e) - 2))
    return float(i + (value - e[i]) / max(e[i + 1] - e[i], 1e-300))


def fmt_value(v: float) -> str:
    a = abs(v)
    if a and (a >= 1e5 or a < 1e-3):
        return f"{v:.2e}"
    return f"{v:.3g}"


def bin_text(d: dict, i: int) -> str:
    """'3.1 % of the cells (4,120) are 1.2 to 1.5 mm' for bar i of a metric's histogram."""
    h, e = d["histogram"], d["edges"]
    n = max(sum(h), 1)
    unit = f" {d['unit']}" if d.get("unit") and d["unit"] != "-" else ""
    return f"{100.0 * h[i] / n:.1f} % of the cells ({h[i]:,}) between {fmt_value(e[i])} and {fmt_value(e[i + 1])}{unit}"


class MetricHist(QWidget):
    """Bars of a histogram dict {label, unit, histogram, edges, ...} (the mesh metrics API), clickable."""
    binClicked = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.key = "size"
        self.d: dict | None = None
        self.rng = (0.0, 1.0)
        self.selected: int | None = None
        self.setMinimumHeight(110)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        theme.follow(self, lambda o, _m=None: o.update())

    def set_data(self, metric: str, d: dict | None, rng, selected_range=None):
        self.key, self.d, self.rng = metric, d, tuple(rng) if rng else (0.0, 1.0)
        self.selected = None
        if d and selected_range:
            lo, hi = selected_range
            for i in range(len(d["histogram"])):
                if abs(d["edges"][i] - lo) <= 1e-9 * max(abs(lo), 1) and abs(d["edges"][i + 1] - hi) <= 1e-9 * max(abs(hi), 1):
                    self.selected = i
        name = d["label"] if d else "mesh metric"
        self.setAccessibleName(f"{name} histogram")
        self.setToolTip("How many cells fall in each band; click a bar to show only those cells on the model" if d else "")
        self.setProperty("api_text", self.text_summary())
        self.update()

    def text_summary(self) -> str:
        if not self.d:
            return ""
        d = self.d
        return f"{d['label']}: " + "; ".join(bin_text(d, i) for i, c in enumerate(d["histogram"]) if c)

    def _geometry(self):
        r = QRectF(2, 4, self.width() - 4, self.height() - 26)
        n = len(self.d["histogram"]) if self.d else 0
        return r, n

    def _bin_at(self, x: float):
        r, n = self._geometry()
        if not n or not r.left() <= x <= r.right():
            return None
        return min(int((x - r.left()) / (r.width() / n)), n - 1)

    def bin_range(self, i: int) -> tuple:
        """(low, high) of bar i; the last bar takes its upper edge too."""
        e = self.d["edges"]
        return float(e[i]), (float("inf") if i == len(self.d["histogram"]) - 1 else float(e[i + 1]))

    def click_bin(self, i: int):
        """The click of bar i (also what the control API calls): the cells of that bar are shown."""
        lo, hi = self.bin_range(i)
        self.binClicked.emit(lo, hi)

    def mouseMoveEvent(self, e):  # noqa: N802
        i = self._bin_at(e.position().x())
        if self.d and i is not None:
            self.setToolTip(bin_text(self.d, i) + "\nClick to show only these cells on the model")
        elif self.d:
            self.setToolTip("How many cells fall in each band; click a bar to show only those cells on the model")

    def mousePressEvent(self, e):  # noqa: N802
        i = self._bin_at(e.position().x())
        if self.d and i is not None and self.d["histogram"][i]:
            self.click_bin(i)

    def paintEvent(self, _e):  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r, n = self._geometry()
        if self.d and n:
            h, edges = self.d["histogram"], self.d["edges"]
            mx = max(h) or 1
            w = r.width() / n
            cen = bin_centres(edges)
            for i, c in enumerate(h):
                hh = (np.sqrt(c) / np.sqrt(mx)) * r.height() if c else 0
                col = bin_color(self.key, cen[i], self.rng)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor.fromRgbF(*col))
                p.drawRect(QRectF(r.left() + i * w + 0.5, r.bottom() - hh, max(w - 1, 1), hh))
                if i == self.selected:
                    p.setBrush(Qt.NoBrush)
                    p.setPen(QPen(QColor(t["ink"]), 2))
                    p.drawRect(QRectF(r.left() + i * w + 0.5, r.bottom() - max(hh, 2), max(w - 1, 1), max(hh, 2)))
            for v, _txt in MARKS.get(self.key, []):
                pos = mark_position(edges, v)
                if pos is not None:
                    x = r.left() + pos * w
                    p.setPen(QPen(QColor(t["ink"]), 1, Qt.DashLine))
                    p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
        p.setPen(QPen(QColor(t["line_strong"]), 1))
        p.drawLine(QPointF(r.left(), r.bottom()), QPointF(r.right(), r.bottom()))
        small = QFont(self.font())
        small.setPointSizeF(max(self.font().pointSizeF() - 1.5, 7))
        p.setFont(small)
        p.setPen(QColor(t["muted"]))
        fm = QFontMetrics(small)
        if self.d and n:
            e = self.d["edges"]
            unit = f" {self.d['unit']}" if self.d.get("unit") and self.d["unit"] != "-" else ""
            labs = [(0.0, fmt_value(e[0]) + unit, 0), (0.5, fmt_value(bin_centres(e)[n // 2] if n > 1 else e[0]), 1), (1.0, fmt_value(e[-1]) + unit, 2)]
            for v, txt, a in labs:
                x = r.left() + v * r.width() - (0 if a == 0 else fm.horizontalAdvance(txt) / (2 if a == 1 else 1))
                p.drawText(QPointF(x, self.height() - 6), txt)
        else:
            p.drawText(QPointF(r.left(), r.center().y()), "No data")


class ClickRow(QWidget):
    """A row of the cell budget that can be clicked: it asks for the cells of that size band (`clicked`), and shows it is the chosen one."""
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_StyledBackground, True)

    def mousePressEvent(self, e):  # noqa: N802
        if e.button() == Qt.LeftButton:
            self.clicked.emit()

    def set_chosen(self, on: bool):
        self.setStyleSheet(f"background: {theme.tokens()['accent_soft']};" if on else "")
        self.setProperty("chosen", bool(on))
