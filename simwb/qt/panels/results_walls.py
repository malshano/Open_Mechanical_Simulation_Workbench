"""The wall-layer view in the Results step of a flow study (#43): for each wall the layers the mesh got (made of planned, the first layer and the total
thickness) and the y+ of the result, as a bar of how much of the wall lies in each y+ band, with a verdict for the wall treatment and a button that
shows y+ on the walls in the 3D view. The numbers are simwb/wallview.py's; this file only draws them.

`add_wall_card(panel, lay)` is the one call the results panel makes."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QFrame, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from ..forms import fmt_num
from ..ids import wid
from .. import theme
from .common import _h, _muted

# one colour per y+ band, mid tones that read on both themes (green = where a wall function is at home)
COLOURS = {"resolved": "#3d8bfd", "buffer": "#e09b1a", "log": "#2a9d8f", "high": "#d62828"}
SHORT = {"resolved": "below 5", "buffer": "5 to 30", "log": "30 to 300", "high": "above 300"}


def layer_text(l: dict | None, hex_percent=None) -> str:
    """The layers of a wall in words."""
    if not l:
        return ("Layers: " + f"{hex_percent:.0f} % of the wall faces got them" if hex_percent is not None else "Layers: not known for this mesh")
    made, target = l.get("made"), l.get("target")
    parts = []
    if made is not None:
        parts.append(f"{fmt_num(round(made, 2))} of {target} layers" if target else f"{fmt_num(round(made, 2))} layers")
    if l.get("first_mm"):
        parts.append(f"first {fmt_num(float(f'{l['first_mm']:.3g}'))} mm")
    if l.get("total_mm"):
        parts.append(f"{fmt_num(float(f'{l['total_mm']:.3g}'))} mm in all")
    if l.get("percent_of_plan") is not None:
        parts.append(f"{l['percent_of_plan']:.0f} % of the planned thickness")
    return "Layers: " + ", ".join(parts) if parts else "Layers: none on this wall"


def yplus_text(y: dict) -> str:
    return f"y+ mean {y['mean']:.0f}, 95 % of the wall below {y['p95']:.0f}, highest {y['max']:.0f}"


class BandBar(QWidget):
    """One bar split by the y+ bands (share of the wall area), the share written under the bands that have any."""

    def __init__(self, shares: dict, parent=None):
        super().__init__(parent)
        self.shares = {k: v for k, v in shares.items() if v > 0}
        self.setFixedHeight(38)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        txt = "; ".join(f"y+ {SHORT[k]}: {v:.0f} % of the wall" for k, v in self.shares.items())
        self.setAccessibleName(txt)
        self.setToolTip(txt)
        theme.follow(self, lambda o, _m=None: o.update())

    def paintEvent(self, _e):  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        tot = sum(self.shares.values()) or 1.0
        p.setPen(QPen(QColor(t["line_strong"]), 1))
        p.setBrush(QColor(t["panel2"]))
        p.drawRect(QRectF(0, 2, self.width() - 1, 12))
        x = 0.0
        fm = QFontMetrics(self.font())
        for k, v in self.shares.items():
            w = v / tot * (self.width() - 1)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(COLOURS[k]))
            p.drawRect(QRectF(x, 2, w, 12))
            lab = f"{v:.0f} %"
            if w >= fm.horizontalAdvance(lab) + 4:
                p.setPen(QColor(t["ink"]))
                p.drawText(QPointF(x + 2, 28), lab)
            x += w
        p.end()


def add_wall_card(panel, lay) -> None:
    """Add the card to the layout of the Results summary: nothing when the study has no wall y+ in its result."""
    try:
        d = panel.engine.project().wall_layer_view()
    except Exception:  # noqa: BLE001 - a help: without it the summary is as it was
        return
    if not d.get("available"):
        return
    lay.addWidget(_h("Wall layers and y+"))
    v = d["verdict"]
    note = QFrame()
    nv = QVBoxLayout(note)
    nv.setContentsMargins(0, 0, 0, 0)
    from PySide6.QtWidgets import QLabel
    lab = wid(QLabel(("✓ " if v["tone"] == "ok" else "⚠ ") + v["words"]), "results.walls.verdict")
    lab.setWordWrap(True)
    if v["tone"]:
        lab.setStyleSheet(f"color: {theme.tokens()[v['tone'] + '_text']}; font-weight: 600;")
    nv.addWidget(lab)
    lay.addWidget(note)
    for i, r in enumerate(d["rows"]):
        head = QLabel(f"<b>{r['name']}</b> · {fmt_num(round(r['area_m2'] * 1e4, 1))} cm²")
        head.setWordWrap(True)
        lay.addWidget(head)
        info = _muted(layer_text(r["layers"], d.get("hex_layers_percent")) + "\n" + yplus_text(r["yplus"]))
        info.setObjectName("muted")
        wid(info, f"results.walls.{i}.text")
        lay.addWidget(info)
        lay.addWidget(wid(BandBar(r["yplus"]["shares"]), f"results.walls.{i}.bar"))
    legend = QLabel("".join(f"<span style='color:{COLOURS[b['key']]}'>■</span> y+ {SHORT[b['key']]}&nbsp;&nbsp; " for b in d["bands"]))
    legend.setTextFormat(Qt.RichText)
    legend.setWordWrap(True)
    legend.setToolTip("\n".join(b["text"] for b in d["bands"]))
    lay.addWidget(legend)
    lay.addWidget(_muted("Bars: share of the wall area in each band of y+. " + ("The layers are the ones the run used." if d["layers_used"] else
                         "This run had no wall layers (or a hex-dominant mesh made them itself).")))
    btn = wid(QPushButton("Show y+ on the walls"), "results.walls.show")
    btn.setObjectName("ghostBorder")
    btn.setToolTip("Colour the walls in the 3D view by y+")
    btn.clicked.connect(lambda: panel.show_field("y+"))
    lay.addWidget(btn)
