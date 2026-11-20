"""The colour legend under the 3D view: a title, a colour bar with round tick values (a log scale when the field is shown on one)
or, for a view coloured by category, a swatch and a name for each. Drawn with Qt, so it is as sharp as the rest of the window."""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QWidget

from . import colormaps, theme


def log_ticks(lo: float, hi: float) -> list:
    out = []
    for e in range(math.floor(math.log10(lo)), math.ceil(math.log10(hi)) + 1):
        for m in (1, 2, 5):
            v = m * 10.0 ** e
            if lo * 0.999 <= v <= hi * 1.001:
                out.append(v)
    return out


def fmt(v: float) -> str:
    s = f"{v:.3g}"
    return "0" if s in ("-0", "0") else s          # (never "-0")


class Legend(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(0)
        self.spec = None
        self.setAttribute(Qt.WA_StyledBackground, True)
        theme.bus.changed.connect(lambda _: self.update())

    def clear(self):
        self.spec = None
        self.setFixedHeight(0)
        self.update()

    def set_scalar(self, name: str, unit: str, lo: float, hi: float, table: np.ndarray, log: bool = False):
        self.spec = {"kind": "scalar", "name": name, "unit": unit, "lo": lo, "hi": hi, "table": np.asarray(table), "log": log and lo > 0}
        self.setFixedHeight(66)
        self.update()

    def set_categories(self, name: str, items: list):
        self.spec = {"kind": "cats", "name": name, "items": items}
        self.setFixedHeight(40)
        self.update()

    def paintEvent(self, _e):  # noqa: N802
        s = self.spec
        if not s:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        th = theme.tokens()
        p.fillRect(self.rect(), QColor(th["panel"]))
        p.setPen(QPen(QColor(th["line"])))
        p.drawLine(0, 0, self.width(), 0)
        title = QFont(self.font())
        title.setBold(True)
        small = QFont(self.font())
        small.setPointSizeF(max(self.font().pointSizeF() - 1.5, 7))
        if s["kind"] == "cats":
            p.setFont(title)
            p.setPen(QColor(th["ink"]))
            p.drawText(QPointF(14, 25), s["name"])
            x = 14 + QFontMetrics(title).horizontalAdvance(s["name"]) + 24
            p.setFont(self.font())
            for c in s["items"]:
                p.setBrush(QColor(c["color"]))
                p.setPen(QPen(QColor(th["line_strong"])))
                p.drawRoundedRect(QRectF(x, 12, 14, 14), 3, 3)
                p.setPen(QColor(th["ink"]))
                p.drawText(QPointF(x + 21, 24), c["name"])
                x += 21 + QFontMetrics(self.font()).horizontalAdvance(c["name"]) + 22
            return
        lo, hi, log = s["lo"], s["hi"], s["log"]
        p.setFont(title)
        p.setPen(QColor(th["ink"]))
        label = s["name"] + (f"  [{s['unit']}]" if s["unit"] and s["unit"] != "-" else "")
        p.drawText(QPointF(14, 20), label)
        w = min(self.width() - 40, 560)
        x0, y0, h = 16, 28, 14
        g = QLinearGradient(x0, 0, x0 + w, 0)
        tab = s["table"]
        for i in range(0, len(tab), max(1, len(tab) // 32)):
            c = tab[i]
            g.setColorAt(i / (len(tab) - 1), QColor.fromRgbF(*[float(v) for v in c]))
        c = tab[-1]
        g.setColorAt(1.0, QColor.fromRgbF(*[float(v) for v in c]))
        p.setBrush(g)
        p.setPen(QPen(QColor(th["line_strong"])))
        p.drawRoundedRect(QRectF(x0, y0, w, h), 3, 3)
        ticks = log_ticks(lo, hi) if log else colormaps.nice_ticks(lo, hi, 6)
        p.setFont(small)
        fm = QFontMetrics(small)
        p.setPen(QColor(th["ink"]))
        last = -1e9
        for v in ticks:
            f = (math.log(v) - math.log(lo)) / (math.log(hi) - math.log(lo)) if log else ((v - lo) / (hi - lo) if hi > lo else 0)
            x = x0 + f * w
            t = fmt(v)
            tw = fm.horizontalAdvance(t)
            tx = min(max(x - tw / 2, x0 - 4), x0 + w + 4 - tw)
            if tx < last + 6:
                continue
            last = tx + tw
            p.setPen(QPen(QColor(th["muted"])))
            p.drawLine(QPointF(x, y0 + h), QPointF(x, y0 + h + 4))
            p.setPen(QColor(th["ink"]))
            p.drawText(QPointF(tx, y0 + h + 17), t)
        p.setPen(QColor(th["muted"]))
        rng = f"{fmt(lo)} to {fmt(hi)}"
        p.drawText(QPointF(x0 + w - fm.horizontalAdvance(rng), 20), rng)
