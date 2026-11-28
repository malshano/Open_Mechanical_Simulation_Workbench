"""The size preview of the Mesh step: a switch that draws the cell sizes the mesher would use (simwb/sizefield.py), on the walls and, with a cut open, across
the domain, before there is any mesh; and a few words about it (the wall, body and far-field sizes, the fields at work). Kept apart from the panel
(simwb/qt/panels/mesh.py), which makes one of these and calls `refresh`; the window asks `active()` and draws it."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QVBoxLayout, QWidget

from ... import sizefield
from .. import theme
from ..forms import fmt_num
from ..ids import wid
from .common import _h, _muted


def mm(v: float) -> str:
    return f"{fmt_num(float(f'{v:.3g}'))} m" if v >= 1.0 else f"{fmt_num(float(f'{v * 1000:.3g}'))} mm"


def words(d: dict) -> str:
    """The sizes of a preview in plain words."""
    a = [f"Walls {mm(d['h_wall'])}"]
    if d.get("h_body") and abs(d["h_body"] - d["h_wall"]) > 1e-9:
        a.append(f"near the body {mm(d['h_body'])}")
    if d.get("h_edge"):
        a.append(f"sharp edges {mm(d['h_edge'])}")
    a.append(f"far away {mm(d['size_far'])}")
    s = ", ".join(a) + f", growing by {fmt_num(round(d['growth'], 2))} from the walls."
    extra = [n for n in d.get("fields") or [] if n not in ("Walls", "Bulk air")]
    if extra:
        s += " Finer where: " + ", ".join(extra) + "."
    return s


class SizePreview(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        self.head = _h("Planned cell sizes")
        v.addWidget(self.head)
        self.show_chk = wid(QCheckBox("Show the planned cell sizes"), "mesh.sizes.show")
        self.show_chk.setToolTip("Draw the cell size the mesher will use, on the model and, with a cut open, through the air round it: where the cells will be small. "
                                 "No mesh is needed; it follows every setting")
        self.show_chk.toggled.connect(self._toggled)
        self._last = None
        v.addWidget(self.show_chk)
        self.text = wid(_muted(""), "mesh.sizes.text")
        v.addWidget(self.text)
        self.error = None
        self.setVisible(False)

    def _toggled(self, _on):
        if self._last:
            self.refresh(*self._last)                       # (the words follow the switch at once)
        self.changed.emit()

    def active(self) -> bool:
        return self.isVisibleTo(self.parentWidget()) and self.show_chk.isChecked()

    def refresh(self, st: dict, pr) -> None:
        self._last = (st, pr)
        ok = st["geometry"].get("status") == "ready" and (st["mesh"].get("method") != "hex")
        self.setVisible(bool(ok))
        if not ok:
            self.text.setText("")
            return
        if not self.show_chk.isChecked():
            self.text.setText("")
            self.text.setProperty("api_text", "")
            return
        try:
            d = sizefield.decisions(pr)
            d = {**d, "fields": [n for n, _ in d["terms"]]}
            txt = words(d)
            self.error = None
        except Exception as e:  # noqa: BLE001 - a help: say why there is none
            txt, self.error = f"No preview: {e}", str(e)
        self.text.setText(txt + ("" if self.error else " The curvature of the faces and narrow gaps are decided by the mesher and not drawn."))
        self.text.setProperty("api_text", self.text.text())
