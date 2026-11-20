"""Small charts drawn with Qt in the colours of the theme: a labelled horizontal bar (a value against a limit) and a line chart (a field along a
line). Words and numbers carry the meaning, the colour only backs it up, so both themes (and colour blindness) are fine."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from . import colormaps, theme


class HBar(QWidget):
    """label ........ value text
       [=========|----]   the bar is `frac` full (0-1); `limit` marks a limit on it (e.g. the yield strength)."""

    def __init__(self, label: str, value_text: str, frac: float, limit: float | None = None, tone: str = "ok", parent=None):
        super().__init__(parent)
        self.label, self.value_text, self.frac, self.limit, self.tone = label, value_text, frac, limit, tone
        self.setFixedHeight(40)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAccessibleName(f"{label}: {value_text}")
        self.setToolTip(f"{label}: {value_text}")
        theme.bus.changed.connect(lambda _: self.update())

    def paintEvent(self, _e):  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        f = QFont(self.font())
        p.setFont(f)
        fm = QFontMetrics(f)
        p.setPen(QColor(t["ink"]))
        p.drawText(QPointF(0, 14), fm.elidedText(self.label, Qt.ElideRight, int(self.width() * 0.5)))
        w = fm.horizontalAdvance(self.value_text)
        p.setPen(QColor(t["muted"]))
        p.drawText(QPointF(max(self.width() - w, 0), 14), self.value_text)
        r = QRectF(0, 22, self.width(), 10)
        p.setPen(QPen(QColor(t["line_strong"]), 1))
        p.setBrush(QColor(t["panel2"]))
        p.drawRoundedRect(r, 5, 5)
        frac = float(min(max(self.frac, 0.0), 1.0))
        if frac > 0:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(t.get(self.tone, t["ok"])))
            p.drawRoundedRect(QRectF(0, 22, max(self.width() * frac, 6), 10), 5, 5)
        if self.limit is not None and 0 < self.limit <= 1:
            x = self.width() * self.limit
            p.setPen(QPen(QColor(t["ink"]), 2))
            p.drawLine(QPointF(x, 19), QPointF(x, 35))


class LineChart(QWidget):
    """A field along a line: distance on x, value on y; hovering reads the value (and reports the distance, for a marker on the model)."""
    hovered = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.x = np.zeros(0)
        self.y = np.zeros(0)
        self.xlabel, self.ylabel = "", ""
        self.hover = None
        self.setMinimumHeight(170)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.MinimumExpanding)
        self.setAccessibleName("Line plot")
        theme.bus.changed.connect(lambda _: self.update())

    def set_data(self, x, y, xlabel="", ylabel=""):
        self.x = np.asarray(x, float)
        self.y = np.asarray([np.nan if v is None else v for v in y], float)
        self.xlabel, self.ylabel = xlabel, ylabel
        self.hover = None
        self.update()

    def clear(self):
        self.set_data([], [])

    def _area(self):
        return QRectF(46, 10, max(self.width() - 58, 10), max(self.height() - 38, 10))

    def mouseMoveEvent(self, e):  # noqa: N802
        a = self._area()
        if len(self.x) and a.left() <= e.position().x() <= a.right():
            f = (e.position().x() - a.left()) / a.width()
            lo, hi = float(self.x.min()), float(self.x.max())
            self.hover = lo + f * (hi - lo)
            self.hovered.emit(self.hover)
        else:
            self.hover = None
        self.update()

    def leaveEvent(self, e):  # noqa: N802
        self.hover = None
        self.update()

    def value_at(self, s):
        ok = np.isfinite(self.y)
        return float(np.interp(s, self.x[ok], self.y[ok])) if ok.any() else None

    def paintEvent(self, _e):  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(t["panel"]))
        a = self._area()
        small = QFont(self.font())
        small.setPointSizeF(max(self.font().pointSizeF() - 1.5, 7))
        p.setFont(small)
        ok = np.isfinite(self.y) if len(self.y) else np.zeros(0, bool)
        if not len(self.x) or not ok.any():
            p.setPen(QColor(t["muted"]))
            p.drawText(self.rect(), Qt.AlignCenter | Qt.TextWordWrap, "No line yet: pick two points on the model, or use Min → max.")
            return
        xlo, xhi = float(self.x.min()), float(self.x.max())
        ylo, yhi = float(self.y[ok].min()), float(self.y[ok].max())
        if yhi - ylo < 1e-30:
            ylo, yhi = ylo - 0.5, yhi + 0.5
        pad = 0.06 * (yhi - ylo)
        ylo, yhi = ylo - pad, yhi + pad
        sx = lambda v: a.left() + (v - xlo) / ((xhi - xlo) or 1) * a.width()  # noqa: E731
        sy = lambda v: a.bottom() - (v - ylo) / ((yhi - ylo) or 1) * a.height()  # noqa: E731
        fm = QFontMetrics(small)
        p.setPen(QPen(QColor(t["line"]), 1))
        for v in colormaps.nice_ticks(ylo, yhi, 4):
            p.drawLine(QPointF(a.left(), sy(v)), QPointF(a.right(), sy(v)))
            p.setPen(QColor(t["muted"]))
            txt = f"{v:.3g}"
            p.drawText(QPointF(a.left() - 6 - fm.horizontalAdvance(txt), sy(v) + 4), txt)
            p.setPen(QPen(QColor(t["line"]), 1))
        for v in colormaps.nice_ticks(xlo, xhi, 5):
            p.setPen(QColor(t["muted"]))
            txt = f"{v:.3g}"
            p.drawText(QPointF(sx(v) - fm.horizontalAdvance(txt) / 2, a.bottom() + 14), txt)
        p.setPen(QPen(QColor(t["line_strong"]), 1))
        p.drawRect(a)
        p.setPen(QColor(t["muted"]))
        p.drawText(QPointF(a.left(), self.height() - 4), self.xlabel)
        path, pen_up = QPainterPath(), True
        for xv, yv in zip(self.x, self.y):
            if not np.isfinite(yv):
                pen_up = True                      # (outside the mesh: a gap)
                continue
            pt = QPointF(sx(xv), sy(yv))
            if pen_up:
                path.moveTo(pt)
                pen_up = False
            else:
                path.lineTo(pt)
        p.setPen(QPen(QColor(t["accent_text"]), 2))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        if self.hover is not None:
            v = self.value_at(self.hover)
            if v is not None:
                p.setPen(QPen(QColor(t["ink"]), 1, Qt.DashLine))
                p.drawLine(QPointF(sx(self.hover), a.top()), QPointF(sx(self.hover), a.bottom()))
                txt = f"{v:.4g} {self.ylabel}  at {self.hover:.3g}"
                w = fm.horizontalAdvance(txt)
                x0 = min(max(sx(self.hover) - w / 2, a.left()), a.right() - w)
                p.fillRect(QRectF(x0 - 3, a.top() + 2, w + 6, fm.height() + 4), QColor(t["panel2"]))
                p.setPen(QColor(t["ink"]))
                p.drawText(QPointF(x0, a.top() + fm.ascent() + 4), txt)


class QualityHist(QWidget):
    """The cell-quality histogram: how many cells fall in each quality band, 0 (poor) on the left to 1 (perfect) on the right. Bars are
    drawn with the red-is-bad scale the mesh view uses, and the axis says so in words."""

    def __init__(self, counts=None, parent=None):
        super().__init__(parent)
        self.counts = list(counts or [])
        self.setMinimumHeight(96)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAccessibleName("Cell quality histogram")
        self.setToolTip("How many cells have each quality (0 = poor, 1 = perfect)")
        theme.bus.changed.connect(lambda _: self.update())

    def set_counts(self, counts):
        self.counts = list(counts or [])
        self.update()

    def paintEvent(self, _e):  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(2, 4, self.width() - 4, self.height() - 24)
        n = len(self.counts)
        if n:
            mx = max(self.counts) or 1
            w = r.width() / n
            for i, c in enumerate(self.counts):
                h = (np.sqrt(c) / np.sqrt(mx)) * r.height() if c else 0
                col = colormaps.turbo(1 - (i + 0.5) / n)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor.fromRgbF(float(col[0]), float(col[1]), float(col[2])))
                p.drawRect(QRectF(r.left() + i * w + 0.5, r.bottom() - h, max(w - 1, 1), h))
        p.setPen(QPen(QColor(t["line_strong"]), 1))
        p.drawLine(QPointF(r.left(), r.bottom()), QPointF(r.right(), r.bottom()))
        small = QFont(self.font())
        small.setPointSizeF(max(self.font().pointSizeF() - 1.5, 7))
        p.setFont(small)
        p.setPen(QColor(t["muted"]))
        for v, a in ((0, Qt.AlignLeft), (0.5, Qt.AlignHCenter), (1, Qt.AlignRight)):
            txt = {0: "0 (poor)", 0.5: "0.5", 1: "1 (perfect)"}[v]
            fm = QFontMetrics(small)
            x = r.left() + v * r.width() - (0 if a == Qt.AlignLeft else fm.horizontalAdvance(txt) / (2 if a == Qt.AlignHCenter else 1))
            p.drawText(QPointF(x, self.height() - 6), txt)
