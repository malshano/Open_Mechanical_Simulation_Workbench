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
        theme.follow(self, lambda o, _m=None: o.update())

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
        self.setFixedHeight(190)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.MinimumExpanding)
        self.setAccessibleName("Line plot")
        theme.follow(self, lambda o, _m=None: o.update())

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
            p.setPen(QPen(QColor(t["line_strong"]), 1, Qt.DashLine))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 6, 6)
            f = QFont(self.font())
            f.setPointSizeF(self.font().pointSizeF() + 0.5)
            p.setFont(f)
            p.setPen(QColor(t["muted"]))
            p.drawText(self.rect().adjusted(12, 8, -12, -8), Qt.AlignCenter | Qt.TextWordWrap,
                       "No line yet.\nPick two points on the model, or use Min → max.")
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
        theme.follow(self, lambda o, _m=None: o.update())

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


# ------------------------------------------------------------------------------------------------ charts of the results summary
SERIES = ["#3d8bfd", "#e4572e", "#2a9d8f", "#8f5bd6", "#e09b1a", "#06aed5", "#d62828", "#6a994e"]   # (lines: mid tones, seen on both themes)
AXIS_COLOURS = {"x": "#e0524f", "y": "#4fae55", "z": "#3d8bfd"}


def _fmt(v: float) -> str:
    a = abs(v)
    return f"{v:.2e}" if a and (a >= 1e5 or a < 1e-2) else f"{v:.4g}"


class _Plot(QWidget):
    """The frame the result charts share: an area with grid lines and labels in the theme's colours, an empty-state text."""
    empty_text = "Nothing to plot."

    def __init__(self, height: int = 150, name: str = "chart", tip: str = "", parent=None):
        super().__init__(parent)
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAccessibleName(name)
        if tip:
            self.setToolTip(tip)
        theme.follow(self, lambda o, _m=None: o.update())

    def _area(self):
        return QRectF(50, 8, max(self.width() - 60, 10), max(self.height() - 30, 10))

    def _small(self, p):
        f = QFont(self.font())
        f.setPointSizeF(max(self.font().pointSizeF() - 1.5, 7))
        p.setFont(f)
        return QFontMetrics(f)

    def _empty(self, p, t):
        p.setPen(QPen(QColor(t["line_strong"]), 1, Qt.DashLine))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 6, 6)
        p.setPen(QColor(t["muted"]))
        p.drawText(self.rect().adjusted(12, 8, -12, -8), Qt.AlignCenter | Qt.TextWordWrap, self.empty_text)


class XYChart(_Plot):
    """Lines against a common x: series [{name, x, y, dashed?, color?}], linear or logarithmic axes, an optional vertical marker (a critical
    speed), a zero line, dots (peaks) and a legend in words. Used for the flutter diagrams, the shake-test sweep, the residuals and the
    histories over time."""

    def __init__(self, height=150, name="chart", tip="", parent=None):
        super().__init__(height, name, tip, parent)
        self.series: list = []
        self.logx = self.logy = False
        self.xunit = self.yunit = ""
        self.marker = None          # (x, text)
        self.zero = False
        self.dots: list = []        # [(x, y)]
        self.ylim = None            # (lo, hi) to clip to
        self.legend = True

    def set_data(self, series, *, logx=False, logy=False, xunit="", yunit="", marker=None, zero=False, dots=(), ylim=None, legend=True):
        self.series = [dict(s, x=np.asarray(s["x"], float), y=np.asarray([np.nan if v is None else v for v in s["y"]], float)) for s in series]
        self.logx, self.logy, self.xunit, self.yunit = logx, logy, xunit, yunit
        self.marker, self.zero, self.dots, self.ylim, self.legend = marker, zero, list(dots), ylim, legend
        self.update()

    def _ranges(self):
        xs = np.concatenate([s["x"] for s in self.series]) if self.series else np.zeros(0)
        ys = np.concatenate([s["y"] for s in self.series]) if self.series else np.zeros(0)
        okx = np.isfinite(xs) & ((xs > 0) if self.logx else True)
        oky = np.isfinite(ys) & ((ys > 0) if self.logy else True)
        if not okx.any() or not oky.any():
            return None
        x0, x1 = float(xs[okx].min()), float(xs[okx].max())
        y0, y1 = float(ys[oky].min()), float(ys[oky].max())
        if self.ylim:
            y0, y1 = max(y0, self.ylim[0]), min(y1, self.ylim[1])
        if self.logy:
            y0, y1 = 10 ** np.floor(np.log10(y0)), 10 ** np.ceil(np.log10(y1))
            if y1 / y0 < 10:
                y0 /= 10
        elif y1 - y0 < 1e-30:
            y0, y1 = y0 - 1, y1 + 1
        else:
            pad = 0.05 * (y1 - y0)
            y0, y1 = y0 - pad, y1 + pad
        if self.zero and not self.logy:
            y0, y1 = min(y0, 0.0), max(y1, 0.0)
        if x1 <= x0:
            x1 = x0 + 1
        return x0, x1, y0, y1

    def paintEvent(self, _e):  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(t["panel"]))
        fm = self._small(p)
        rg = self._ranges()
        if rg is None:
            self._empty(p, t)
            return
        x0, x1, y0, y1 = rg
        a = self._area()
        tx = (lambda v: np.log10(v)) if self.logx else (lambda v: v)
        ty = (lambda v: np.log10(v)) if self.logy else (lambda v: v)
        X = lambda v: a.left() + (tx(v) - tx(x0)) / ((tx(x1) - tx(x0)) or 1) * a.width()  # noqa: E731
        Y = lambda v: a.bottom() - (ty(min(max(v, y0), y1)) - ty(y0)) / ((ty(y1) - ty(y0)) or 1) * a.height()  # noqa: E731
        yt = [10.0 ** e for e in range(int(round(np.log10(y0))), int(round(np.log10(y1))) + 1)] if self.logy else colormaps.nice_ticks(y0, y1, 4)
        xt = [10.0 ** e for e in range(int(np.ceil(np.log10(x0))), int(np.floor(np.log10(x1))) + 1)] if self.logx else colormaps.nice_ticks(x0, x1, 5)
        for v in yt:
            p.setPen(QPen(QColor(t["line"]), 1))
            p.drawLine(QPointF(a.left(), Y(v)), QPointF(a.right(), Y(v)))
            p.setPen(QColor(t["muted"]))
            s = _fmt(v)
            p.drawText(QPointF(a.left() - 5 - fm.horizontalAdvance(s), Y(v) + 4), s)
        for v in xt:
            if not x0 <= v <= x1:
                continue
            p.setPen(QPen(QColor(t["line"]), 1))
            p.drawLine(QPointF(X(v), a.top()), QPointF(X(v), a.bottom()))
            p.setPen(QColor(t["muted"]))
            s = _fmt(v)
            p.drawText(QPointF(X(v) - fm.horizontalAdvance(s) / 2, a.bottom() + 13), s)
        p.setPen(QColor(t["muted"]))
        if self.xunit:
            p.drawText(QPointF(a.right() - fm.horizontalAdvance(self.xunit), self.height() - 3), self.xunit)
        if self.yunit:
            p.drawText(QPointF(2, fm.ascent()), self.yunit)
        p.setPen(QPen(QColor(t["line_strong"]), 1))
        p.drawRect(a)
        if self.zero and not self.logy and y0 < 0 < y1:
            p.setPen(QPen(QColor(t["ink"]), 1))
            p.drawLine(QPointF(a.left(), Y(0)), QPointF(a.right(), Y(0)))
        p.save()
        p.setClipRect(a)
        for i, s in enumerate(self.series):
            path, up = QPainterPath(), True
            for xv, yv in zip(s["x"], s["y"]):
                if not (np.isfinite(xv) and np.isfinite(yv)) or (self.logy and yv <= 0) or (self.logx and xv <= 0):
                    up = True
                    continue
                pt = QPointF(X(xv), Y(yv))
                if up:
                    path.moveTo(pt)
                else:
                    path.lineTo(pt)
                up = False
            p.setPen(QPen(QColor(s.get("color") or SERIES[i % len(SERIES)]), 1.8, Qt.DashLine if s.get("dashed") else Qt.SolidLine))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(t["warn"]))
        for xv, yv in self.dots:
            if (not self.logy or yv > 0) and (not self.logx or xv > 0):
                p.drawEllipse(QPointF(X(xv), Y(yv)), 3.5, 3.5)
        p.restore()
        if self.marker:
            mx, txt = self.marker
            p.setPen(QPen(QColor(t["bad"]), 1.5, Qt.DashLine))
            p.drawLine(QPointF(X(mx), a.top()), QPointF(X(mx), a.bottom()))
            p.setPen(QColor(t["bad_text"]))
            p.drawText(QPointF(min(X(mx) + 4, a.right() - fm.horizontalAdvance(txt)), a.top() + fm.ascent() + 2), txt)
        if self.legend and len(self.series) > 1:
            y = a.top() + 4
            w = max(fm.horizontalAdvance(s["name"]) for s in self.series) + 30
            n = min(len(self.series), max(int((a.height() - 8) // (fm.height() + 1)), 1))
            p.fillRect(QRectF(a.left() + 4, y, w, n * (fm.height() + 1) + 4), QColor(t["panel2"]))
            for i, s in enumerate(self.series[:n]):
                yy = y + 2 + i * (fm.height() + 1)
                p.setPen(QPen(QColor(s.get("color") or SERIES[i % len(SERIES)]), 2, Qt.DashLine if s.get("dashed") else Qt.SolidLine))
                p.drawLine(QPointF(a.left() + 8, yy + fm.height() / 2), QPointF(a.left() + 22, yy + fm.height() / 2))
                p.setPen(QColor(t["ink"]))
                p.drawText(QPointF(a.left() + 26, yy + fm.ascent()), s["name"])


class SpectrumChart(_Plot):
    """The natural frequencies as stems: the height is the share of the mass that moves in the mode's main direction (its colour, with the
    letter x / y / z beside the mode number), the excitation range shaded, the mode shown drawn bold. A click on a stem picks that mode."""
    modeClicked = Signal(int)
    empty_text = "No elastic modes."

    def __init__(self, parent=None):
        super().__init__(130, "Natural frequencies", "Each stem is a mode: its height is the share of the mass moving in its main direction "
                         "(x, y or z, written beside the mode number). Click a stem to show that mode on the model.", parent)
        self.modes: list = []
        self.band = None
        self.selected = None
        self._stems: list = []
        self.setCursor(Qt.PointingHandCursor)

    def set_data(self, modes, band=None, selected=None):
        self.modes = [m for m in (modes or []) if not m.get("rigid_body")]
        self.band, self.selected = band, selected
        self.update()

    @staticmethod
    def stem(m):
        e = m.get("effective_mass_fraction")
        if not e:
            return None, 0.04
        d = max("xyz", key=lambda k: e.get(k, 0) or 0)
        return d, float(e.get(d) or 0)

    def stem_x(self, k):
        """Where the stem of mode k is (pixels), for a click."""
        a = self._area()
        fmax = max([m["frequency_Hz"] for m in self.modes] + [(self.band or [0, 0])[1]]) * 1.06 or 1.0
        m = next((m for m in self.modes if m.get("elastic_index", m.get("mode")) == k), None)
        return None if m is None else a.left() + a.width() * m["frequency_Hz"] / fmax

    def mode_at(self, x: float):
        best = None
        for m in self.modes:
            k = m.get("elastic_index", m.get("mode"))
            d = abs(self.stem_x(k) - x)
            if d < 14 and (best is None or d < best[0]):
                best = (d, k)
        return None if best is None else best[1]

    def mousePressEvent(self, e):  # noqa: N802
        k = self.mode_at(e.position().x())
        if k is not None:
            self.modeClicked.emit(int(k))

    def paintEvent(self, _e):  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(t["panel"]))
        fm = self._small(p)
        if not self.modes:
            self._empty(p, t)
            return
        a = self._area()
        fmax = max([m["frequency_Hz"] for m in self.modes] + [(self.band or [0, 0])[1]]) * 1.06 or 1.0
        X = lambda f: a.left() + a.width() * f / fmax  # noqa: E731
        Y = lambda v: a.bottom() - a.height() * v  # noqa: E731
        for i in range(5):
            v = i / 4
            p.setPen(QPen(QColor(t["line"]), 1))
            p.drawLine(QPointF(a.left(), Y(v)), QPointF(a.right(), Y(v)))
            p.setPen(QColor(t["muted"]))
            s = f"{int(v * 100)} %"
            p.drawText(QPointF(a.left() - 5 - fm.horizontalAdvance(s), Y(v) + 4), s)
        for f in colormaps.nice_ticks(0, fmax, 5):
            p.setPen(QColor(t["muted"]))
            s = _fmt(f)
            p.drawText(QPointF(X(f) - fm.horizontalAdvance(s) / 2, a.bottom() + 13), s)
        p.drawText(QPointF(a.right() - fm.horizontalAdvance("Hz"), self.height() - 3), "Hz")
        if self.band and self.band[1] >= self.band[0]:
            c = QColor(t["bad"])
            c.setAlphaF(0.22)
            p.fillRect(QRectF(X(self.band[0]), a.top(), max(X(self.band[1]) - X(self.band[0]), 2), a.height()), c)
        p.setPen(QPen(QColor(t["line_strong"]), 1))
        p.drawRect(a)
        for m in self.modes:
            d, v = self.stem(m)
            k = m.get("elastic_index", m.get("mode"))
            sel = k == self.selected
            x = X(m["frequency_Hz"])
            col = QColor(AXIS_COLOURS.get(d, t["muted"]))
            p.setPen(QPen(col, 3.2 if sel else 2))
            p.drawLine(QPointF(x, Y(0)), QPointF(x, Y(v)))
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawEllipse(QPointF(x, Y(v)), 4.2 if sel else 3, 4.2 if sel else 3)
            if sel or len(self.modes) <= 12:
                p.setPen(QColor(t["ink"] if sel else t["muted"]))
                s = f"{k}{d or ''}"
                p.drawText(QPointF(x - fm.horizontalAdvance(s) / 2, max(Y(v) - 6, a.top() + fm.ascent())), s)


class FieldHist(QWidget):
    """The distribution of the drawn field over the surface (share of the area, square-root scale), in the colours of the colour map, over the
    colour range shown. Drag across it to set the colour range to that part (rangeDragged(lo, hi)); a double-click goes back to automatic."""
    rangeDragged = Signal(float, float)
    autoRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.bins = np.zeros(0)
        self.lo, self.hi = 0.0, 1.0
        self.mean = None
        self.tab = colormaps.table("turbo", 64)
        self._drag = None
        self._sel = None
        self.setFixedHeight(76)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.SizeHorCursor)
        self.setAccessibleName("Distribution of the field")
        self.setToolTip("How much of the surface has each value (square-root scale), over the colour range shown. Drag across the bars to set "
                        "the colour range to that part; double-click to go back to the automatic range.")
        theme.follow(self, lambda o, _m=None: o.update())

    def set_data(self, bins, lo, hi, mean=None, tab=None):
        self.bins = np.asarray(bins, float)
        self.lo, self.hi, self.mean = float(lo), float(hi), mean
        if tab is not None:
            self.tab = tab
        self.update()

    def value_at(self, x: float) -> float:
        w = max(self.width() - 4, 1)
        return self.lo + (self.hi - self.lo) * min(max((x - 2) / w, 0.0), 1.0)

    def drag(self, x0: float, x1: float):
        """A drag from pixel x0 to x1: the colour range between them."""
        a, b = sorted((self.value_at(x0), self.value_at(x1)))
        if b - a > 1e-12 * max(1.0, abs(b)):
            self.rangeDragged.emit(a, b)

    def mousePressEvent(self, e):  # noqa: N802
        self._drag = e.position().x()

    def mouseMoveEvent(self, e):  # noqa: N802
        if self._drag is not None:
            self._sel = (self._drag, e.position().x())
            self.update()

    def mouseReleaseEvent(self, e):  # noqa: N802
        if self._drag is not None:
            x0, self._drag, self._sel = self._drag, None, None
            if abs(e.position().x() - x0) > 3:
                self.drag(x0, e.position().x())
            self.update()

    def mouseDoubleClickEvent(self, e):  # noqa: N802
        self._drag = self._sel = None
        self.autoRequested.emit()

    def paintEvent(self, _e):  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(t["panel"]))
        f = QFont(self.font())
        f.setPointSizeF(max(self.font().pointSizeF() - 1.5, 7))
        p.setFont(f)
        fm = QFontMetrics(f)
        r = QRectF(2, 4, self.width() - 4, self.height() - 20)
        n = len(self.bins)
        if n:
            mx = float(np.sqrt(self.bins).max()) or 1e-9
            w = r.width() / n
            for i, b in enumerate(self.bins):
                h = np.sqrt(b) / mx * (r.height() - 2)
                c = self.tab[int((i + 0.5) / n * (len(self.tab) - 1))]
                p.setPen(Qt.NoPen)
                p.setBrush(QColor.fromRgbF(float(c[0]), float(c[1]), float(c[2])))
                p.drawRect(QRectF(r.left() + i * w + 0.5, r.bottom() - h, max(w - 1, 1), h))
        p.setPen(QPen(QColor(t["line_strong"]), 1))
        p.drawLine(QPointF(r.left(), r.bottom()), QPointF(r.right(), r.bottom()))
        if self.mean is not None and self.hi > self.lo and self.lo <= self.mean <= self.hi:
            x = r.left() + (self.mean - self.lo) / (self.hi - self.lo) * r.width()
            p.setPen(QPen(QColor(t["ink"]), 1, Qt.DashLine))
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            p.setPen(QColor(t["ink"]))
            p.drawText(QPointF(min(max(x - fm.horizontalAdvance("mean") / 2, 2), self.width() - fm.horizontalAdvance("mean") - 2), r.top() + fm.ascent()), "mean")
        if self._sel:
            c = QColor(t["accent"])
            c.setAlphaF(0.3)
            a, b = sorted(self._sel)
            p.fillRect(QRectF(a, r.top(), b - a, r.height()), c)
        p.setPen(QColor(t["muted"]))
        lo, hi = _fmt(self.lo), _fmt(self.hi)
        p.drawText(QPointF(2, self.height() - 3), lo)
        p.drawText(QPointF(self.width() - fm.horizontalAdvance(hi) - 2, self.height() - 3), hi)


class SignedBar(QWidget):
    """label ........ value text
       [-----|=====--]   a bar that grows from the middle (a force or a moment): to the right is positive, `frac` of the half width (-1..1)."""

    def __init__(self, label: str, value_text: str, frac: float, parent=None):
        super().__init__(parent)
        self.label, self.value_text, self.frac = label, value_text, frac
        self.setFixedHeight(40)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAccessibleName(f"{label}: {value_text}")
        self.setToolTip(f"{label}: {value_text} (bars grow from the centre line; to the right is positive)")
        theme.follow(self, lambda o, _m=None: o.update())

    def paintEvent(self, _e):  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        fm = QFontMetrics(self.font())
        p.setPen(QColor(t["ink"]))
        p.drawText(QPointF(0, 14), fm.elidedText(self.label, Qt.ElideRight, int(self.width() * 0.5)))
        p.setPen(QColor(t["muted"]))
        p.drawText(QPointF(max(self.width() - fm.horizontalAdvance(self.value_text), 0), 14), self.value_text)
        p.setPen(QPen(QColor(t["line_strong"]), 1))
        p.setBrush(QColor(t["panel2"]))
        p.drawRoundedRect(QRectF(0, 22, self.width(), 10), 5, 5)
        mid = self.width() / 2
        f = float(min(max(self.frac, -1.0), 1.0))
        if f:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(t["accent"] if f > 0 else t["warn"]))
            w = max(abs(f) * mid, 3)
            p.drawRect(QRectF(mid if f > 0 else mid - w, 22, w, 10))
        p.setPen(QPen(QColor(t["ink"]), 1.5))
        p.drawLine(QPointF(mid, 19), QPointF(mid, 35))


class StackBar(QWidget):
    """One bar split into parts (where the heat goes), with a line per part below it: the name, the value and the share."""

    def __init__(self, parts: list, total: float, unit: str = "W", parent=None):
        super().__init__(parent)
        self.parts = [q for q in parts if (q.get("v") or 0) > 0]
        self.total = total if total and total > 0 else (sum(q["v"] for q in self.parts) or 1.0)
        self.unit = unit
        self.setFixedHeight(22 + 18 * len(self.parts))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        txt = "; ".join(f"{q['label']}: {q['v']:.3g} {unit} ({100 * q['v'] / self.total:.3g} %)" for q in self.parts)
        self.setAccessibleName(txt)
        self.setToolTip(txt)
        theme.follow(self, lambda o, _m=None: o.update())

    def paintEvent(self, _e):  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        fm = QFontMetrics(self.font())
        p.setPen(QPen(QColor(t["line_strong"]), 1))
        p.setBrush(QColor(t["panel2"]))
        p.drawRect(QRectF(0, 2, self.width(), 12))
        x = 0.0
        for i, q in enumerate(self.parts):
            w = min(q["v"] / self.total, 1.0) * self.width()
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(q.get("color") or SERIES[i % len(SERIES)]))
            p.drawRect(QRectF(x, 2, w, 12))
            x += w
        for i, q in enumerate(self.parts):
            y = 22 + 18 * i
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(q.get("color") or SERIES[i % len(SERIES)]))
            p.drawRect(QRectF(0, y, 10, 10))
            p.setPen(QColor(t["ink"]))
            p.drawText(QPointF(16, y + 10), fm.elidedText(q["label"], Qt.ElideRight, int(self.width() * 0.6)))
            s = f"{q['v']:.3g} {self.unit} · {100 * q['v'] / self.total:.3g} %"
            p.setPen(QColor(t["muted"]))
            p.drawText(QPointF(self.width() - fm.horizontalAdvance(s), y + 10), s)
