"""The results step panel, in four tabs like the web version: Summary (the numbers that matter, with verdicts, parts bars, cautions, export),
Display (field, colours, range, deformation, labels), Probe (pinned values, a line plot) and Checks (warnings, assumptions, mesh sensitivity)."""
from __future__ import annotations

import base64
import json

import numpy as np
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QDoubleValidator, QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QSlider, QSpinBox, QStackedWidget, QVBoxLayout, QWidget)

from ... import resample
from .. import colormaps, theme
from ..charts import HBar, LineChart
from ..forms import fmt_num
from ..ids import wid
from .common import KPI, ClickLabel, _h, _muted, _Panel, _seg, clear_layout
from .flowtools import FlowTools


def _item() -> QFrame:
    f = QFrame()
    f.setObjectName("item")
    f.setAttribute(Qt.WA_StyledBackground, True)
    return f


def _tone_sf(sf: float):
    return ("ok", "adequate margin") if sf >= 2 else ("warn", "thin margin") if sf >= 1.2 else ("bad", "below the yield strength")


class ResultsPanel(_Panel):
    TABS = [("summary", "Summary"), ("display", "Display"), ("flow", "Flow"), ("probe", "Probe"), ("checks", "Checks")]

    def __init__(self, engine, parent=None):
        super().__init__(engine, parent)
        self.fields: list = []
        self.pins: list = []            # [{point(base), value, text}]
        self.line_ends: list = []       # base points of the line being picked
        self.line_pick = False
        self.probe_on = False
        self._sig = None
        self._sec_pid = None
        self.line_data = None
        tabs, self.tab_grp, self.tab_btns = _seg(self.TABS, "summary", prefix="results.tab")
        self.v.addWidget(tabs)
        self.stack = QStackedWidget()
        self.v.addWidget(self.stack)
        self.pages = {}
        for i, (k, _) in enumerate(self.TABS):
            w = QWidget()
            lay = QVBoxLayout(w)
            lay.setContentsMargins(0, 6, 0, 0)
            lay.setSpacing(8)
            self.stack.addWidget(w)
            self.pages[k] = lay
            self.tab_btns[k].clicked.connect(lambda _=False, i=i: self.stack.setCurrentIndex(i))
        self._build_display()
        self.flow = FlowTools()
        self.flow.changed.connect(self._display_changed)
        self.pages["flow"].addWidget(self.flow)
        self.tab_btns["flow"].setVisible(False)           # (flow and cooling studies only)
        self._build_probe()
        self.v.addStretch(1)

    # ------------------------------------------------------------------ the 3D view
    def bind(self, win):
        super().bind(win)
        win.view.pointPicked.connect(self._point_picked)

    def shown(self):
        self.view.set_pick_mode("point" if (self.probe_on or self.line_pick) else None)

    def left(self):
        self.view.set_pick_mode(None)
        self.view.set_labels([])
        self.probe_on = self.line_pick = False
        self.probe_btn.setChecked(False)

    # ------------------------------------------------------------------ the Display tab
    def _build_display(self):
        v = self.pages["display"]
        v.addWidget(_h("Field"))
        self.field = wid(QComboBox(), "results.field")
        self.field.setToolTip("The result drawn on the model")
        self.field.currentIndexChanged.connect(lambda _=0: self._display_changed())
        v.addWidget(self.field)
        v.addWidget(_h("Colours"))
        row = QHBoxLayout()
        self.cmap = wid(QComboBox(), "results.colormap")
        self.cmap.addItems(colormaps.NAMES)
        self.cmap.setToolTip("Colour map")
        self.cmap.currentIndexChanged.connect(lambda _=0: self._display_changed())
        self.rev = wid(QCheckBox("Reverse"), "results.reverse")
        self.rev.setToolTip("Turn the colour map around")
        self.rev.toggled.connect(lambda _=False: self._display_changed())
        row.addWidget(self.cmap, 1)
        row.addWidget(self.rev)
        v.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("Colour bands"))
        self.bands = wid(QSpinBox(), "results.bands")
        self.bands.setRange(0, 40)
        self.bands.setSpecialValueText("smooth (no bands)")
        self.bands.setSuffix(" bands")
        self.bands.setMinimumWidth(110)
        self.bands.setToolTip("Number of flat colour bands (smooth = continuous)")
        self.bands.valueChanged.connect(lambda _=0: self._display_changed())
        row.addWidget(self.bands)
        row.addStretch(1)
        v.addLayout(row)
        row = QHBoxLayout()
        self.auto_range = wid(QCheckBox("Automatic range"), "results.range.auto")
        self.auto_range.setChecked(True)
        self.auto_range.setToolTip("Colour range from the lowest to the highest value; untick to set it")
        self.auto_range.toggled.connect(self._range_toggled)
        row.addWidget(self.auto_range)
        v.addLayout(row)
        row = QHBoxLayout()
        self.rmin = wid(QLineEdit(), "results.range.min")
        self.rmax = wid(QLineEdit(), "results.range.max")
        for e, tip in ((self.rmin, "Lowest value of the colour range"), (self.rmax, "Highest value of the colour range")):
            e.setValidator(QDoubleValidator(e))
            e.setToolTip(tip)
            e.setEnabled(False)
            e.editingFinished.connect(self._display_changed)
        row.addWidget(QLabel("from"))
        row.addWidget(self.rmin)
        row.addWidget(QLabel("to"))
        row.addWidget(self.rmax)
        v.addLayout(row)
        v.addWidget(_h("Contours"))
        self.cont_on = wid(QCheckBox("Show contour lines"), "results.contours")
        self.cont_on.setToolTip("Draw lines of equal value on the result, with the colours")
        self.cont_on.toggled.connect(self._contours_toggled)
        v.addWidget(self.cont_on)
        row = QHBoxLayout()
        self.cont_n = wid(QSpinBox(), "results.contours.n")
        self.cont_n.setRange(1, 100)
        self.cont_n.setValue(10)
        self.cont_n.setSuffix(" lines")
        self.cont_n.setEnabled(False)
        self.cont_n.setToolTip("How many evenly spaced contour lines between the lowest and highest value")
        self.cont_n.valueChanged.connect(lambda _=0: self._display_changed())
        row.addWidget(QLabel("Number"))
        row.addWidget(self.cont_n, 1)
        v.addLayout(row)
        row = QHBoxLayout()
        self.cont_labels = wid(QCheckBox("Write the value on each line"), "results.contours.labels")
        self.cont_labels.setChecked(True)
        self.cont_labels.setEnabled(False)
        self.cont_labels.setToolTip("A small label with the value of each contour line")
        self.cont_labels.toggled.connect(lambda _=False: self._display_changed())
        v.addWidget(self.cont_labels)
        self.cont_levels = wid(QLineEdit(), "results.contours.levels")
        self.cont_levels.setPlaceholderText("add values, e.g. 120, 180")
        self.cont_levels.setEnabled(False)
        self.cont_levels.setToolTip("Contour lines at values you choose, besides the evenly spaced ones (separate them with commas)")
        self.cont_levels.editingFinished.connect(self._display_changed)
        row.addWidget(QLabel("Add at"))
        row.addWidget(self.cont_levels, 1)
        v.addLayout(row)
        self.deform_box = QWidget()          # (only for results that deform: a flow or a temperature field does not)
        dv = QVBoxLayout(self.deform_box)
        dv.setContentsMargins(0, 0, 0, 0)
        dv.setSpacing(8)
        dv.addWidget(_h("Deformation"))
        self.deform = wid(QCheckBox("Show the deformation"), "results.deform")
        self.deform.setChecked(True)
        self.deform.setToolTip("Draw the part as it deforms (exaggerated so that it can be seen)")
        self.deform.toggled.connect(lambda _=False: self._display_changed())
        dv.addWidget(self.deform)
        row = QHBoxLayout()
        self.deform_mode = wid(QComboBox(), "results.deform_mode")
        self.deform_mode.addItems(["End state", "Animated"])
        self.deform_mode.setToolTip("Show the final shape, or the load being applied and released")
        self.deform_mode.currentIndexChanged.connect(lambda _=0: self._display_changed())
        row.addWidget(QLabel("As"))
        row.addWidget(self.deform_mode, 1)
        dv.addLayout(row)
        row = QHBoxLayout()
        self.dscale = wid(QLineEdit(), "results.deform_scale")
        self.dscale.setPlaceholderText("auto")
        self.dscale.setValidator(QDoubleValidator(0, 1e9, 4, self.dscale))
        self.dscale.setToolTip("Exaggeration of the deformation (empty: chosen so that it is visible)")
        self.dscale.editingFinished.connect(self._display_changed)
        auto = wid(QPushButton("Auto"), "results.deform_auto")
        auto.setObjectName("ghostBorder")
        auto.setToolTip("Let the workbench choose the exaggeration")
        auto.clicked.connect(lambda: (self.dscale.setText(""), self._display_changed()))
        true = wid(QPushButton("True size ×1"), "results.deform_true")
        true.setObjectName("ghostBorder")
        true.setToolTip("Draw the deformation at its real size (usually too small to see)")
        true.clicked.connect(lambda: (self.dscale.setText("1"), self._display_changed()))
        row.addWidget(QLabel("Scale"))
        row.addWidget(self.dscale, 1)
        row.addWidget(auto)
        row.addWidget(true)
        dv.addLayout(row)
        v.addWidget(self.deform_box)
        self.section_box = QWidget()                 # (parts only: a flow has its contour planes in the Flow tab)
        sv = QVBoxLayout(self.section_box)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(6)
        sv.addWidget(_h("Section"))
        self.section_hint = _muted("Cut the part to see the inside: the field on the cut faces, for the side below the plane.")
        sv.addWidget(self.section_hint)
        w, self.section_grp, self.section_btns = _seg([("", "Off"), ("x", "X"), ("y", "Y"), ("z", "Z")], "", prefix="results.section")
        for k, b in self.section_btns.items():
            b.clicked.connect(lambda _=False, k=k: self._section_changed())
            b.setToolTip("No section" if not k else f"Cut at a plane across {k.upper()}")
        sv.addWidget(w)
        self.section_pos = wid(QSlider(Qt.Horizontal), "results.section.pos")
        self.section_pos.setRange(0, 1000)
        self.section_pos.setValue(500)
        self.section_pos.setEnabled(False)
        self.section_pos.setToolTip("Where the plane cuts the part (from one end to the other)")
        self.section_pos.valueChanged.connect(lambda _=0: self._display_changed())
        sv.addWidget(self.section_pos)
        v.addWidget(self.section_box)
        v.addWidget(_h("Model"))
        self.edges = wid(QCheckBox("Show the mesh edges"), "results.edges")
        self.edges.setToolTip("Draw the edges of the mesh cells on the surface")
        self.edges.toggled.connect(lambda _=False: self._display_changed())
        v.addWidget(self.edges)
        self.minmax = wid(QCheckBox("Label the highest and lowest values"), "results.minmax")
        self.minmax.setChecked(True)
        self.minmax.setToolTip("Hang the highest and lowest values on the model. Drag a label with the mouse to move it.")
        self.minmax.toggled.connect(lambda _=False: self._display_changed())
        v.addWidget(self.minmax)
        v.addStretch(1)

    def _range_toggled(self, auto):
        for e in (self.rmin, self.rmax):
            e.setEnabled(not auto)
        if not auto and not self.rmin.text():
            info = next((f for f in self.fields if f["name"] == self.field.currentText()), {})
            self.rmin.setText(fmt_num(info.get("min", 0)))
            self.rmax.setText(fmt_num(info.get("max", 1)))
        self._display_changed()

    def _display_changed(self):
        self.changed.emit()

    def _contours_toggled(self, on):
        self.cont_n.setEnabled(bool(on))
        self.cont_levels.setEnabled(bool(on))
        self.cont_labels.setEnabled(bool(on))
        self._display_changed()

    def contour_levels(self) -> list:
        out = []
        for tok in self.cont_levels.text().replace(";", ",").split(","):
            try:
                out.append(float(tok))
            except ValueError:
                pass
        return out

    def _section_changed(self):
        self.section_pos.setEnabled(any(k and b.isChecked() for k, b in self.section_btns.items()))
        self._display_changed()

    def request(self):
        scale = None
        try:
            scale = float(self.dscale.text()) if self.dscale.text().strip() else None
        except ValueError:
            pass
        rng = None
        if not self.auto_range.isChecked():
            try:
                lo, hi = float(self.rmin.text()), float(self.rmax.text())
                rng = (lo, hi) if hi > lo else None
            except ValueError:
                rng = None
        on = self.deform.isChecked()
        return {"field": self.field.currentText(), "cmap": self.cmap.currentText(), "reverse": self.rev.isChecked(),
                "bands": self.bands.value(), "deform": (scale if on else 0), "animate": on and self.deform_mode.currentText() == "Animated",
                "edges": self.edges.isChecked(), "range": rng, "minmax": self.minmax.isChecked(),
                "contours": {"on": self.cont_on.isChecked(), "n": self.cont_n.value(), "levels": self.contour_levels(), "labels": self.cont_labels.isChecked()},
                "flow": self.flow.request() if self.tab_btns["flow"].isVisibleTo(self) else None,
                "section": next(((k, self.section_pos.value() / 1000) for k, b in self.section_btns.items() if k and b.isChecked()), None)}

    # ------------------------------------------------------------------ the Probe tab
    def _build_probe(self):
        v = self.pages["probe"]
        v.addWidget(_h("Probe"))
        v.addWidget(_muted("Click points on the result to read their value and keep it in the list. Click the model with Probe on."))
        self.probe_btn = wid(QPushButton("⌖  Probe: off. Click to start probing"), "results.probe")
        self.probe_btn.setCheckable(True)
        self.probe_btn.setObjectName("toggle")
        self.probe_btn.setToolTip("While this is on, a click on the model pins the value there")
        self.probe_btn.toggled.connect(self._probe_toggled)
        v.addWidget(self.probe_btn)
        self.pin_box = QVBoxLayout()
        self.pin_box.setSpacing(4)
        v.addLayout(self.pin_box)
        v.addWidget(_h("Line plot"))
        v.addWidget(_muted("The field along a straight line between two points."))
        row = QHBoxLayout()
        self.pick2 = wid(QPushButton("Pick two points"), "results.line.pick")
        self.pick2.setObjectName("ghostBorder")
        self.pick2.setToolTip("Click two points on the model: the plot shows the field between them")
        self.pick2.clicked.connect(self._start_line)
        self.minmax_btn = wid(QPushButton("Min → max"), "results.line.minmax")
        self.minmax_btn.setObjectName("ghostBorder")
        self.minmax_btn.setToolTip("The line from the lowest to the highest value")
        self.minmax_btn.clicked.connect(self.line_min_max)
        row.addWidget(self.pick2)
        row.addWidget(self.minmax_btn)
        v.addLayout(row)
        self.chart = LineChart()
        self.chart.setObjectName("linechart")
        v.addWidget(self.chart)
        self.line_info = wid(_muted(""), "results.line.info")
        v.addWidget(self.line_info)
        row = QHBoxLayout()
        cp = self.copy_btn = wid(QPushButton("Copy data"), "results.line.copy")
        cp.setObjectName("ghostBorder")
        cp.setToolTip("Copy the plotted values as text (distance and value)")
        cp.clicked.connect(self.copy_line)
        cl = self.clear_btn = wid(QPushButton("Clear"), "results.line.clear")
        cl.setObjectName("ghostBorder")
        cl.setToolTip("Remove the line and the pinned values")
        cl.clicked.connect(self.clear_probes)
        row.addWidget(cp)
        row.addWidget(cl)
        row.addStretch(1)
        v.addLayout(row)
        v.addStretch(1)
        self._probe_enable()

    def _probe_enable(self):
        """Copy and Clear wait until there is something to copy or clear."""
        has = bool(self.pins or self.line_data)
        self.copy_btn.setEnabled(bool(self.line_data))
        self.clear_btn.setEnabled(has)
        self.copy_btn.setToolTip("Copy the plotted values as text (distance and value)" if self.line_data else "There is no line plot to copy yet")
        self.clear_btn.setToolTip("Remove the line and the pinned values" if has else "Nothing is pinned yet")

    def _probe_toggled(self, on):
        self.probe_btn.setText("✓  Probe is ON: click points on the model" if on else "⌖  Probe: off. Click to start probing")
        self.probe_on = on
        self.view.set_pick_mode("point" if (on or self.line_pick) else None)

    def _start_line(self):
        self.line_pick = True
        self.line_ends = []
        self.view.set_pick_mode("point")
        self.line_info.setText("Click the first point on the model…")

    def _point_picked(self, x, y, z):
        if self.win is None or self.win.step != "results":
            return
        h = self.view.last_hit
        if not h or h.get("kind") != "surface":
            return
        base = self.view.base_point(h["cell"], h["point"])
        if self.line_pick:
            self.line_ends.append(base)
            if len(self.line_ends) == 1:
                self.line_info.setText("Click the second point…")
            else:
                self.line_pick = False
                self.view.set_pick_mode("point" if self.probe_on else None)
                self.fetch_line()
        elif self.probe_on:
            val = self.view.value_at(h["cell"], h["point"])
            unit = self.view.overlay_info.get("unit", "")
            self.pins.append({"point": list(h["point"]), "value": val, "unit": unit, "name": self.field.currentText()})
            self._pins()
        self.draw_marks()

    def pin_at(self, point):
        """Pin the value at a world point of the drawn surface (what a Probe click does): returns the pin."""
        h = self.view.pick_ray([point[0], point[1], point[2] + 1.0], [point[0], point[1], point[2] - 1.0])
        if not h or h["kind"] != "surface":
            raise ValueError("That point is not on the drawn result")
        val = self.view.value_at(h["cell"], h["point"])
        self.pins.append({"point": list(h["point"]), "value": val, "unit": self.view.overlay_info.get("unit", ""), "name": self.field.currentText()})
        self._pins()
        self.draw_marks()
        return self.pins[-1]

    def _pins(self):
        self._probe_enable()
        clear_layout(self.pin_box)
        for i, p in enumerate(self.pins):
            row = QHBoxLayout()
            lab = QLabel(f"<b>{chr(65 + i % 26)}</b>  {p['name']}: {fmt_num(p['value'])} {p['unit']}  <span style='color:{theme.tokens()['muted']}'>"
                         f"at {', '.join(fmt_num(c * 1000) for c in p['point'])} mm</span>")
            lab.setWordWrap(True)
            row.addWidget(lab, 1)
            d = wid(QPushButton("✕"), f"results.pin.{i}.delete")
            d.setObjectName("ghost")
            d.setToolTip("Remove this pinned value")
            d.clicked.connect(lambda _=False, i=i: self._del_pin(i))
            row.addWidget(d)
            w = QWidget()
            w.setLayout(row)
            self.pin_box.addWidget(w)

    def _del_pin(self, i):
        if 0 <= i < len(self.pins):
            del self.pins[i]
            self._pins()
            self.draw_marks()

    def line_min_max(self):
        ex = self.view.extrema()
        if not ex:
            raise ValueError("Nothing is drawn to take the lowest and highest value from")
        self.line_ends = [ex["min"]["base"], ex["max"]["base"]]
        self.fetch_line()

    def fetch_line(self, field=None):
        a, b = self.line_ends[:2]
        f = field or self.field.currentText()
        try:
            d = resample.line_payload(self.engine.project().results(), f, a, b, 160)
        except Exception as e:  # noqa: BLE001
            self.line_info.setText(str(e))
            self.line_data = None
            return None
        self.line_data = d
        self._probe_enable()
        self.chart.set_data(d["s"], d["values"], "distance along the line [m]", d.get("unit", ""))
        if "min" in d:
            self.line_info.setText(f"{f}: min {fmt_num(d['min'])}, max {fmt_num(d['max'])}, mean {fmt_num(d['mean'])} {d.get('unit', '')} "
                                   f"over {fmt_num(d['length'] * 1000)} mm")
        else:
            self.line_info.setText("The line is outside the mesh.")
        self.draw_marks()
        return d

    def copy_line(self):
        if not self.line_data:
            raise ValueError("There is no line plot yet")
        d = self.line_data
        text = "distance_m\tvalue\n" + "\n".join(f"{s:.6g}\t{'' if v is None else f'{v:.6g}'}" for s, v in zip(d["s"], d["values"]))
        QGuiApplication.clipboard().setText(text)
        return text

    def clear_probes(self):
        self.pins, self.line_ends, self.line_data, self.line_pick = [], [], None, False
        self.chart.clear()
        self.line_info.setText("")
        self._probe_enable()
        self._pins()
        self.draw_marks()

    # ------------------------------------------------------------------ labels on the model
    def draw_marks(self):
        """The highest/lowest values, the pinned values and the ends of the line, hung on the drawn result."""
        if self.win is None or self.win.step != "results" or not self.engine.pid:
            return
        items = []
        t = theme.tokens()
        if self.minmax.isChecked() and self.view.overlay_info.get("name") != "Object":      # (a grey object under flow tools has no values to label)
            ex = self.view.extrema()
            if ex:
                u = self.view.overlay_info.get("unit", "")
                for k, word in (("max", "Max"), ("min", "Min")):
                    items.append({"point": ex[k]["point"], "text": f"{word} {ex[k]['value']:.4g} {u}".strip(),
                                  "color": t["bad"] if k == "max" else t["accent"], "below": k == "min", "key": k})
        for i, p in enumerate(self.pins):
            items.append({"point": p["point"], "text": f"{chr(65 + i % 26)}: {p['value']:.4g} {p['unit']}".strip(), "color": t["warn"]})
        for k, e in enumerate(self.line_ends[:2]):
            items.append({"point": e, "text": "AB"[k], "color": t["ink"]})
        self.view.set_labels(items)

    # ------------------------------------------------------------------ the Summary and Checks tabs
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
        flow = st["study"] in ("external_aero", "cht_enclosure")
        self.tab_btns["flow"].setVisible(flow and bool(infos))
        if flow and infos:
            self.flow.set_fields(infos, self.engine.flow_defaults())
        elif self.stack.currentIndex() == 2:
            self.tab_btns["summary"].click()
        self.deform_box.setVisible(any(f["name"] == "Total deformation" or f["name"].startswith("Mode ") for f in infos))
        self.section_box.setVisible(not flow)
        if flow and not self.section_btns[""].isChecked():          # (a flow is cut with the contour planes of the Flow tab)
            self.section_btns[""].setChecked(True)
            self._section_changed()
        if flow and infos and self._sec_pid != self.engine.pid:
            self._sec_pid = self.engine.pid           # (a flow result opens with one contour plane through the middle: the air box would hide the object)
            self.flow.set_planes([{"axis": "y", "frac": 0.5}], emit=False)
        sig = json.dumps([st["study"], st["solve"].get("status"), st["solve"].get("summary"), names, theme.effective(),
                          st.get("mesh_check")], default=str, sort_keys=True)
        if sig == self._sig:
            return
        self._sig = sig
        s = st["solve"].get("summary") or {}
        self._summary(st, s, bool(infos))
        self._checks(st, s)

    def show_field(self, name: str):
        """'Show on model': switch to the Display field and look at it."""
        i = self.field.findText(name)
        if i >= 0:
            self.field.setCurrentIndex(i)

    def _link(self, v, text, field, wid_id):
        b = wid(ClickLabel(f"{text} →"), wid_id)
        b.setStyleSheet(f"color: {theme.tokens()['accent_text']}; font-size: 11px; padding: 0 4px 4px 4px; text-decoration: underline;")
        b.setToolTip(f"Show {field} on the model")
        b.clicked.connect(lambda f=field: self.show_field(f))
        v.addWidget(b)

    @staticmethod
    def _converged_tile(s):
        c = s.get("convergence") or {}
        ok = bool(c.get("converged"))
        return ("Solution", "Converged" if ok else "Not converged", "", "ok" if ok else "warn",
                f"{c.get('iterations', 0):,} iterations; the forces and temperatures stopped changing" if ok and c.get("outputs_stable")
                else f"{c.get('iterations', 0):,} iterations: check the residuals", None)

    def _summary(self, st, s, has_results):
        lay = self.pages["summary"]
        clear_layout(lay)
        if not has_results:
            lay.addWidget(_muted("No results yet. Run the simulation first (step 4, Solve)."))
            lay.addStretch(1)
            return
        study = st["study"]
        tiles, extra = [], []
        if "max_von_mises_MPa" in s:
            sf = s.get("min_safety_factor")
            tiles = [("Max von Mises stress", fmt_num(round(s["max_von_mises_MPa"], 1)), "MPa", "", "", "Von Mises stress"),
                     ("Max deformation", fmt_num(round(s.get("max_displacement_mm", 0), 4)), "mm", "", "", "Total deformation")]
            if sf:
                tone, word = _tone_sf(sf)
                tiles.append(("Safety factor (yield)", f"{sf:.2f}", "", tone, word, "Safety factor"))
            if s.get("mass_kg") is not None:
                tiles.append(("Mass", fmt_num(round(s["mass_kg"] * 1000, 1)), "g", "", "", None))
        elif "T_max" in s:
            eb = s.get("energy_balance") or {}
            tiles = [("Max temperature", fmt_num(round(s["T_max"], 1)), "°C", "", "", "Temperature"),
                     ("Min temperature", fmt_num(round(s["T_min"], 1)), "°C", "", "", None)]
            if eb:
                tiles += [("Heat in", fmt_num(round(eb.get("heat_in_W", 0), 2)), "W", "", "", None),
                          ("Heat out", fmt_num(round(eb.get("heat_out_W", 0), 2)), "W",
                           "ok" if abs(eb.get("imbalance_percent", 0)) < 1 else "warn",
                           "heat balance closes" if abs(eb.get("imbalance_percent", 0)) < 0.01 else f"{eb.get('imbalance_percent', 0):.2g} % off", None)]
        elif "first_frequency_Hz" in s:
            band = s.get("excitation_band_Hz")
            inband = s.get("modes_in_band") or []
            f1 = s["first_frequency_Hz"]
            hit = band and band[0] <= f1 <= band[1]
            tiles = [("First natural frequency", fmt_num(round(f1, 1)), "Hz", "bad" if hit else ("ok" if band else ""),
                      "inside the excitation range" if hit else ("clear of the excitation range" if band else ""), "Mode 1")]
            if band:
                tiles.append(("Modes in excitation range", str(len(inband)), f"of {len(s.get('modes') or [])}", "bad" if inband else "ok", "", None))
            if s.get("mass_kg") is not None:
                tiles.append(("Mass", fmt_num(round(s["mass_kg"] * 1000, 1)), "g", "", "", None))
            for m in (s.get("modes") or [])[:8]:
                extra.append((m.get("mode"), m.get("frequency_Hz")))
        elif "drag_N" in s:                      # external aerodynamics
            co = s.get("coefficients") or {}
            yp = s.get("yPlus") or {}
            tiles = [("Drag force", f"{s['drag_N']:.3g}", "N", "", f"at {s.get('speed_m_s', 0):g} m/s", "Pressure"),
                     ("Drag area CdA", f"{s['CdA_m2'] * 1e4:.3g}", "cm²", "", f"Cd {co.get('CD', 0):.3f}" + (f", on the {co['A_ref_source']}" if co.get("A_ref_source") else ""), None),
                     ("Lift force", f"{co.get('lift_N', 0):.3g}", "N", "", "negative = pushes the body down", None)]
            if yp.get("mean") is not None:
                good = 30 <= yp["mean"] <= 300
                tiles.append(("Wall y+ (mean)", f"{yp['mean']:.0f}", "", "ok" if good else "warn",
                              "inside the 30 to 300 the wall functions expect" if good else f"outside the 30 to 300 the wall functions expect (max {yp.get('max', 0):.0f})", "y+"))
            tiles.append(self._converged_tile(s))
        elif "outlet_T_mixed" in s:               # electronics cooling
            hot = s.get("hottest_part") or {}
            imb = abs(s.get("energy_imbalance_percent") or 0)
            tiles = [(f"Hottest part ({hot.get('label', '')})".replace(" ()", ""), f"{hot.get('T_max', 0):.1f}", "°C", "", "", "Temperature"),
                     ("Thermal resistance", f"{s['thermal_resistance_K_per_W']:.2f}" if s.get("thermal_resistance_K_per_W") is not None else "-", "K/W", "", "hottest part above the inlet air, per watt", None),
                     ("Air leaves at", f"{s['outlet_T_mixed']:.1f}", "°C", "", f"it came in at {s.get('inlet_T', 0):g} °C", None),
                     ("Pressure drop", f"{s['pressure_drop_Pa']:.2f}", "Pa", "", f"what a fan must supply ({s.get('flow_m3_per_h', 0):.1f} m³/h)", "Pressure"),
                     ("Heat taken by the air", f"{s.get('heat_carried_by_air_W', 0):.2f}", "W", "ok" if imb < 2 else "warn",
                      f"of {s.get('heat_input_W', 0):g} W put in ({imb:.1f} % off)", None),
                     self._converged_tile(s)]
        else:
            for k, val in list(s.items())[:6]:
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    tiles.append((k.replace("_", " ").capitalize(), fmt_num(val), "", "", "", None))
        grid = QWidget()
        g = QGridLayout(grid)
        g.setContentsMargins(0, 0, 0, 0)
        g.setSpacing(8)
        for i, (lab, val, unit, tone, hint, field) in enumerate(tiles):
            cell = QWidget()
            cv = QVBoxLayout(cell)
            cv.setContentsMargins(0, 0, 0, 0)
            cv.setSpacing(0)
            cv.addWidget(KPI(lab, val, unit, tone, hint))
            if field:
                self._link(cv, "show on model", field, f"results.show.{i}")
            last_odd = i == len(tiles) - 1 and len(tiles) % 2 == 1
            g.addWidget(cell, i // 2, 0 if last_odd else i % 2, 1, 2 if last_odd else 1)
        g.setColumnStretch(0, 1)
        g.setColumnStretch(1, 1)
        lay.addWidget(grid)
        # modes (a modal study): each one can be looked at
        if extra:
            lay.addWidget(_h("Natural frequencies"))
            for k, f in extra:
                b = wid(ClickLabel(f"Mode {k} - {fmt_num(round(f, 1))} Hz  →"), f"results.mode.{k}")
                b.setStyleSheet(f"color: {theme.tokens()['accent_text']}; padding: 2px 4px;")
                b.setToolTip(f"Show the shape of mode {k} on the model")
                b.clicked.connect(lambda kk=k: self.show_field(f"Mode {kk}"))
                lay.addWidget(b)
        # the parts: each against its limit
        bodies = s.get("bodies")
        if isinstance(bodies, list) and bodies and "yield_MPa" in bodies[0]:
            lay.addWidget(_h("Parts"))
            for b in bodies:
                sf = b.get("safety_factor") or 0
                tone, word = _tone_sf(sf) if sf else ("ok", "")
                lay.addWidget(HBar(b["name"], f"{fmt_num(round(b['max_von_mises_MPa'], 1))} MPa · SF {sf:.1f}" + (f" · {word}" if word else ""),
                                   b["max_von_mises_MPa"] / b["yield_MPa"], 1.0 if b["max_von_mises_MPa"] / b["yield_MPa"] <= 1 else None, tone))
            lay.addWidget(_muted("Bars: stress as a share of the yield strength (the line is 100 %)."))
        elif isinstance(bodies, dict) and bodies and "T_max" in next(iter(bodies.values())):
            lay.addWidget(_h("Parts"))
            lo, hi = s["T_min"], s["T_max"]
            for b in bodies.values():
                frac = (b["T_max"] - lo) / ((hi - lo) or 1)
                lay.addWidget(HBar(b["name"], f"{fmt_num(round(b['T_max'], 1))} °C · mean {fmt_num(round(b['T_mean'], 1))}", frac, None, "accent"))
            lay.addWidget(_muted(f"Bars run from {fmt_num(round(lo, 1))} to {fmt_num(round(hi, 1))} °C."))
        # loads and reactions
        if "applied_force_N" in s:
            lay.addWidget(_muted("Load " + " / ".join(fmt_num(round(x, 3)) for x in s["applied_force_N"]) + " N, reaction "
                                 + " / ".join(fmt_num(round(x, 3)) for x in s["reaction_force_N"]) + " N (x / y / z)."))
        # cautions
        warns = s.get("warnings") or []
        if warns:
            box = _item()
            bv = QVBoxLayout(box)
            bv.setContentsMargins(10, 8, 10, 8)
            title = QLabel("⚠  Read before you rely on these numbers")
            title.setStyleSheet(f"font-weight: 600; color: {theme.tokens()['warn_text']};")
            title.setWordWrap(True)
            bv.addWidget(title)
            for w in warns:
                lab = QLabel("• " + w)
                lab.setWordWrap(True)
                bv.addWidget(lab)
            lay.addWidget(box)
        # the small print
        bits = []
        if s.get("n_dof"):
            bits.append(f"{s['n_dof']:,} unknowns")
        if s.get("solve_time_s") or s.get("seconds"):
            bits.append(f"{(s.get('solve_time_s') or s.get('seconds')):.2f} s")
        if s.get("peak_von_mises_unaveraged_MPa"):
            bits.append(f"unaveraged peak {s['peak_von_mises_unaveraged_MPa']:.0f} MPa")
        if s.get("stress_error_percent") is not None:
            bits.append(f"estimated stress error {s['stress_error_percent']:.0f} %")
        if bits:
            d = wid(_muted(" · ".join(bits)), "results.details")
            lay.addWidget(d)
        # export
        lay.addWidget(_h("Export"))
        row = QHBoxLayout()
        for key, label, tip in (("report", "Report", "A report of this study as a web page (HTML) with the picture of the view"),
                                ("csv", "CSV table", "The result values at the mesh points, as a table"),
                                ("vtk", "VTK (ParaView)", "The results in a VTK file, to open in ParaView")):
            b = wid(QPushButton(label), f"results.export.{key}")
            b.setObjectName("ghostBorder")
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, k=key: self._export_dialog(k))
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addStretch(1)

    def _export_dialog(self, key):
        ext = {"report": ("HTML report (*.html)", "html"), "csv": ("CSV table (*.csv)", "csv"), "vtk": ("VTK file (*.vtu)", "vtu")}[key]
        path, _ = QFileDialog.getSaveFileName(self, "Export the results", f"{self.engine.pid}.{ext[1]}", ext[0])
        if path:
            try:
                self.export(key, path)
                self.win.statusBar().showMessage(f"Exported to {path}", 8000)
            except Exception as e:  # noqa: BLE001
                self._fail(e)

    def export(self, key: str, path: str):
        """Write the results: key report (HTML with the picture), csv or vtk."""
        from pathlib import Path

        from ... import export as E
        pr = self.engine.project()
        out = Path(path)
        if key == "report":
            ba, buf = QByteArray(), QBuffer()
            buf.setBuffer(ba)
            buf.open(QIODevice.WriteOnly)
            self.view.image().save(buf, "PNG")
            img = "data:image/png;base64," + base64.b64encode(bytes(ba)).decode()
            out.write_text(E.report_html(pr, img), encoding="utf-8")
        elif key == "csv":
            E.write_csv(pr.results(), out)
        elif key == "vtk":
            E.write_vtu(pr.results(), out)
        else:
            raise ValueError("format must be report, csv or vtk")
        return str(out)

    def _checks(self, st, s):
        lay = self.pages["checks"]
        clear_layout(lay)
        lay.addWidget(_h("Warnings"))
        warns = s.get("warnings") or []
        if warns:
            for w in warns:
                lab = QLabel("⚠  " + w)
                lab.setWordWrap(True)
                lay.addWidget(lab)
        else:
            lab = QLabel("✓  Nothing to warn about.")
            lab.setStyleSheet(f"color: {theme.tokens()['ok_text']};")
            lay.addWidget(lab)
        lay.addWidget(_h("Model assumptions"))
        for a in s.get("assumptions") or []:
            lab = QLabel("• " + a)
            lab.setWordWrap(True)
            lay.addWidget(lab)
        if not s.get("assumptions"):
            lay.addWidget(_muted("No assumptions recorded."))
        lay.addWidget(_h("Mesh sensitivity"))
        lay.addWidget(_muted("Re-mesh one level finer in a copy of the study and solve again: if the key numbers hardly change, the mesh is fine enough."))
        run = wid(QPushButton("Run mesh-sensitivity check"), "results.meshcheck.run")
        run.setObjectName("ghostBorder")
        run.setToolTip("Solve the study again on a finer mesh (in a copy) and compare")
        run.clicked.connect(self._meshcheck)
        lay.addWidget(run)
        mc = st.get("mesh_check")
        if mc:
            lay.addWidget(wid(_muted("Last check: " + (str(mc.get("status") or mc.get("verdict") or "see the report")[:300])), "results.meshcheck.info"))
        lay.addStretch(1)

    def _meshcheck(self):
        try:
            self.engine.project().mesh_check(levels=1)
            self.win.statusBar().showMessage("Mesh-sensitivity check started (a job)", 6000)
        except Exception as e:  # noqa: BLE001
            self._fail(e)
