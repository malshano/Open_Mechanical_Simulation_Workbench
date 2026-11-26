"""The results step panel, in four tabs like the web version: Summary (the numbers that matter, with verdicts, parts bars, cautions, export),
Display (field, colours, range, deformation, labels), Probe (pinned values, a line plot) and Checks (warnings, assumptions, mesh sensitivity)."""
from __future__ import annotations

import base64
import json
from pathlib import Path

import numpy as np
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QDoubleValidator, QGuiApplication
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QSlider, QSpinBox, QStackedWidget, QVBoxLayout, QWidget)

from ... import resample
from .. import colormaps, theme
from .. import exports
from ..charts import FieldHist, HBar, LineChart, SignedBar, SpectrumChart, StackBar, XYChart
from ..forms import fmt_num
from ..ids import wid
from ..probes import surface_histogram
from ..recording import Recording, RecordingStopped
from ..resulthandles import ResultHandles
from ..timebar import TimeBar
from ..visibility import VisibilityList, zone_outline
from .common import KPI, ClickLabel, FlowLayout, _h, _muted, _Panel, _seg, clear_layout
from .flowtools import FlowTools


def P_JOBS():
    from ... import project as _p
    return _p.JOBS

SOLID_STUDIES = ("static_structural", "topology_optimization", "vibration_modal", "heat_conduction", "aeroelastic_flutter")


def _item() -> QFrame:
    f = QFrame()
    f.setObjectName("item")
    f.setAttribute(Qt.WA_StyledBackground, True)
    return f


def _note(text: str, tone: str = "", rich: bool = False) -> QFrame:
    """A note box (the web version's .note): a line of text on the item background, coloured by the tone (warn / ok / bad)."""
    box = _item()
    v = QVBoxLayout(box)
    v.setContentsMargins(10, 6, 10, 6)
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setTextFormat(Qt.RichText if rich else Qt.PlainText)
    if tone:
        lab.setStyleSheet(f"color: {theme.tokens()[tone + '_text']};")
    v.addWidget(lab)
    return box


def _f(v, d: int = 4) -> str:
    """A number with d significant digits (the web version's fmt)."""
    try:
        return f"{float(v):.{d}g}"
    except (TypeError, ValueError):
        return "-"


def _tone_sf(sf: float):
    return ("ok", "adequate margin") if sf >= 2 else ("warn", "thin margin") if sf >= 1.2 else ("bad", "below the yield strength")


class ResultsPanel(_Panel):
    TABS = [("summary", "Summary"), ("display", "Display"), ("flow", "Flow"), ("probe", "Probe"), ("checks", "Checks")]

    def __init__(self, engine, parent=None):
        super().__init__(engine, parent)
        self.fields: list = []
        self.pins: list = []            # [{point(base), value, text}]
        self.pin_rows: list = []
        self.line_ends: list = []       # base points of the line being picked
        self.line_pick = False
        self.probe_on = False
        self._sig = None
        self._sec_pid = None
        self.line_data = None
        self.hist_data = None
        self._study = None
        self._summary_s: dict = {}
        self.video_info = None
        self.recording = None                  # (the Recording of the video being made, or the last one)
        self.spectrum = None
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
        self.field.currentIndexChanged.connect(lambda _=0: self._field_changed())
        v.addWidget(self.field)
        self.timebar = TimeBar()
        self.timebar.timeChanged.connect(self._time_changed)
        self.timebar.recordRequested.connect(self._video_dialog)
        v.addWidget(self.timebar)
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
        self.hist = wid(FieldHist(), "results.range.hist")
        self.hist.rangeDragged.connect(self.set_range)
        self.hist.autoRequested.connect(lambda: self.auto_range.setChecked(True))
        v.addWidget(self.hist)
        self.hist_info = wid(_muted("Drag across the bars to set the colour range to that part; double-click for the automatic range."),
                             "results.range.hist_info")
        v.addWidget(self.hist_info)
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
        self.outline = wid(QCheckBox("Outline of the undeformed part"), "results.deform_outline")
        self.outline.setChecked(True)
        self.outline.setToolTip("Draw the part before it deformed as a thin outline, to compare the shapes")
        self.outline.toggled.connect(lambda _=False: self._display_changed())
        dv.addWidget(self.outline)
        self.deform_note = wid(_muted(""), "results.deform_note")
        dv.addWidget(self.deform_note)
        v.addWidget(self.deform_box)
        self.section_box = QWidget()                 # (parts only: a flow has its contour planes in the Flow tab)
        sv = QVBoxLayout(self.section_box)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(6)
        sv.addWidget(_h("Section"))
        self.section_hint = _muted("Cut the part to see the inside: the field on the cut faces, for the side below the plane. Move the plane with the slider, the number or its handle in the 3D view.")
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
        self.section_pos.valueChanged.connect(lambda _=0: (self._section_mm_show(), self._display_changed()))
        sv.addWidget(self.section_pos)
        self.section_mm = wid(QDoubleSpinBox(), "results.section.mm")
        self.section_mm.setDecimals(1)
        self.section_mm.setRange(-1e7, 1e7)
        self.section_mm.setSuffix(" mm")
        self.section_mm.setKeyboardTracking(False)
        self.section_mm.setEnabled(False)
        self.section_mm.setToolTip("Where the plane cuts, in mm along its axis. Type a value, or drag the section plane's handle in the 3D view")
        self.section_mm.valueChanged.connect(self._section_mm_typed)
        row = QHBoxLayout()
        row.addWidget(QLabel("Position (mm)"))
        row.addWidget(self.section_mm, 1)
        sv.addLayout(row)
        v.addWidget(self.section_box)
        v.addWidget(_h("Model"))
        row = QHBoxLayout()
        row.addWidget(QLabel("Opacity"))
        self.opacity = wid(QSlider(Qt.Horizontal), "results.opacity")
        self.opacity.setRange(10, 100)
        self.opacity.setValue(100)
        self.opacity.setTracking(False)
        self.opacity.setToolTip("See-through surfaces and sections, to look at what is behind them (100 % = solid)")
        self.opacity_lab = QLabel("100 %")
        self.opacity_lab.setMinimumWidth(44)
        self.opacity.valueChanged.connect(lambda val: (self.opacity_lab.setText(f"{val} %"), self._display_changed()))
        self.opacity.sliderMoved.connect(lambda val: self.opacity_lab.setText(f"{val} %"))
        row.addWidget(self.opacity, 1)
        row.addWidget(self.opacity_lab)
        v.addLayout(row)
        self.hide_air = wid(QCheckBox("Hide the air"), "results.hide_air")
        self.hide_air.setChecked(True)
        self.hide_air.setToolTip("Draw only the solid parts; untick to see the air box and the field on its outside")
        self.hide_air.toggled.connect(lambda _=False: self._display_changed())
        self.hide_air.setVisible(False)
        v.addWidget(self.hide_air)
        self.parts_box = QWidget()
        pv = QVBoxLayout(self.parts_box)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(2)
        pv.addWidget(_muted("Parts drawn (untick one to hide it and look at the others):"))
        self.parts_lay = QVBoxLayout()
        self.parts_lay.setSpacing(2)
        pv.addLayout(self.parts_lay)
        self.part_checks: dict = {}
        self.parts_box.setVisible(False)
        v.addWidget(self.parts_box)
        self.handles = ResultHandles(self)          # the drag handles of the positioning controls (resulthandles.py)
        self.vis = VisibilityList()                 # (a flow with an object: what is drawn, parts and moving zones; see visibility.py)
        self.vis.changed.connect(self._display_changed)
        self.vis.airToggled.connect(self._air_ticked)
        v.addWidget(self.vis)
        self.edges =wid(QCheckBox("Show the mesh edges"), "results.edges")
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
        self._section_mm_show()
        self._display_changed()

    # ---- the section plane's position as a number and as a handle in the 3D view
    def section_axis(self) -> str:
        return next((k for k, b in self.section_btns.items() if k and b.isChecked()), "")

    def section_extent(self):
        """(low, high) of the result along the section's axis in m, or None."""
        ax = self.section_axis()
        if not ax or self.win is None:
            return None
        try:
            return self.win.shell._extent("results", ax)
        except Exception:  # noqa: BLE001
            return None

    def _section_mm_show(self):
        ext = self.section_extent()
        self.section_mm.blockSignals(True)
        if ext:
            self.section_mm.setRange(ext[0] * 1000, ext[1] * 1000)
            self.section_mm.setValue((ext[0] + self.section_pos.value() / 1000.0 * (ext[1] - ext[0])) * 1000)
        self.section_mm.setEnabled(bool(ext))
        self.section_mm.blockSignals(False)

    def _section_mm_typed(self, mm: float):
        ext = self.section_extent()
        if ext and ext[1] > ext[0]:
            self.section_pos.setValue(int(round(min(max((mm / 1000.0 - ext[0]) / (ext[1] - ext[0]), 0.0), 1.0) * 1000)))      # (redraws)

    def set_section_position(self, pos_m: float, final: bool = True):
        """The section plane moved (its handle): the slider and the number follow; the cut is drawn again only when final."""
        ext = self.section_extent()
        if not ext or ext[1] <= ext[0]:
            return
        frac = min(max((pos_m - ext[0]) / (ext[1] - ext[0]), 0.0), 1.0)
        self.section_pos.blockSignals(True)
        self.section_pos.setValue(int(round(frac * 1000)))
        self.section_pos.blockSignals(False)
        self._section_mm_show()
        if final:
            self.section_pos.valueChanged.emit(self.section_pos.value())            # (the cut is made again, and the 3D view's own section bar follows)

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
        return {"time": self.timebar.time_index(), "opacity": self.opacity.value() / 100.0, "outline": self.outline.isChecked(),
                "hide": self.hidden_regions(), "show": self.vis.selection(), "field": self.field.currentText(), "cmap": self.cmap.currentText(), "reverse": self.rev.isChecked(),
                "bands": self.bands.value(), "deform": (scale if on else 0), "animate": on and self.deform_mode.currentText() == "Animated",
                "edges": self.edges.isChecked(), "range": rng, "minmax": self.minmax.isChecked(),
                "contours": {"on": self.cont_on.isChecked(), "n": self.cont_n.value(), "levels": self.contour_levels(), "labels": self.cont_labels.isChecked()},
                "flow": self.flow.request() if self.tab_btns["flow"].isVisibleTo(self) else None,
                "section": next(((k, self.section_pos.value() / 1000) for k, b in self.section_btns.items() if k and b.isChecked()), None)}

    def hidden_regions(self):
        """The result regions not drawn: the parts unticked, and the air of a cooling study while Hide the air is on. None: the study's own."""
        if not self.part_checks and self._study != "cht_enclosure":
            return None
        out = [r for r, c in self.part_checks.items() if not c.isChecked()]
        if self._study == "cht_enclosure" and self.hide_air.isChecked():
            out += [r for r in (self._summary_s.get("default_hidden_regions") or []) if r not in out]
        return out

    def _parts(self, st):
        """One tick box per solid region of the result (when there is more than one part): untick it to hide that part."""
        try:
            regions = list(self.engine.project().results().regions) if st["solve"].get("status") in ("done", "stale") else []
        except Exception:  # noqa: BLE001
            regions = []
        air = set(self._summary_s.get("default_hidden_regions") or [])
        if st["study"] == "external_aero":
            regions = []
        regions = [r for r in regions if r not in air]
        if len(regions) < 2:
            regions = []
        if regions == list(self.part_checks):
            return
        clear_layout(self.parts_lay)
        self.part_checks = {}
        for i, r in enumerate(regions):
            c = wid(QCheckBox(r), f"results.part.{i}")
            c.setChecked(True)
            c.setToolTip(f"Draw {r}; untick to hide it")
            c.toggled.connect(lambda _=False: self._display_changed())
            self.parts_lay.addWidget(c)
            self.part_checks[r] = c
        self.parts_box.setVisible(bool(regions))

    def set_part_visible(self, name: str, on: bool):
        if name not in self.part_checks:
            raise ValueError((f"{name!r} is not a part of this result: " + ", ".join(self.part_checks)) if self.part_checks
                             else "This result has only one part")
        self.part_checks[name].setChecked(bool(on))

    # ------------------------------------------------------------------ "Show in the view": the surfaces, the air box, the zone and region outlines
    def _vis_items(self, st):
        groups, zones, regions = [], [], []
        if st["study"] == "external_aero" and st["solve"].get("status") in ("done", "stale"):
            try:
                rs = self.engine.project().results()
                groups = [g for g in (self._summary_s.get("default_groups") or []) if g in rs.groups] or [g for g in rs.groups if g != "farfield"]
            except Exception:  # noqa: BLE001
                groups = []
            zones, regions = st.get("moving_zones") or [], (st.get("mesh") or {}).get("regions") or []
        self.vis.set_items(self.engine.pid, groups, zones, regions, self.win.chk_air.isChecked() if self.win is not None else False)

    def _air_ticked(self, on: bool):
        """The air box tick was clicked: the 3D view's Air box button is the same switch."""
        if self.win is not None and self.win.chk_air.isChecked() != bool(on):
            self.win.chk_air.setChecked(bool(on))

    def draw_outlines(self):
        """The outlines of the moving zones and the refinement regions, when the list asks for them (translucent, over the result)."""
        sel = self.vis.selection() or {}
        regs = []
        if sel.get("zones") or sel.get("regions"):
            st = self.engine.state()
            if sel.get("zones"):
                regs += [zone_outline(z) for z in st.get("moving_zones") or []]
            if sel.get("regions"):
                regs += list((st.get("mesh") or {}).get("regions") or [])
        self.view.region_view.set(regs, None) if regs else self.view.region_view.clear()

    def left(self):
        self.view.region_view.clear()
        self.handles.clear()

    def set_range(self, lo: float, hi: float):
        """A colour range of its own (what a drag over the distribution does)."""
        if not hi > lo:
            raise ValueError("the range must run from a lower to a higher value")
        self.rmin.setText(f"{lo:.4g}")
        self.rmax.setText(f"{hi:.4g}")
        if self.auto_range.isChecked():
            self.auto_range.setChecked(False)          # (redraws)
        else:
            self._display_changed()

    def _field_changed(self):
        """Another field: its own automatic colour range; the spectrum marks the mode; the time note follows."""
        if not self.auto_range.isChecked():
            self.auto_range.blockSignals(True)
            self.auto_range.setChecked(True)
            self.auto_range.blockSignals(False)
            for e in (self.rmin, self.rmax):
                e.setEnabled(False)
        self._time_note()
        if self.spectrum is not None:
            try:
                self.spectrum.set_data(self.spectrum.modes, self.spectrum.band, self._mode_shown())
            except RuntimeError:                 # (the summary was rebuilt)
                self.spectrum = None
        self._display_changed()

    def _mode_shown(self):
        t = self.field.currentText().split()
        return int(t[1]) if len(t) == 2 and t[0] == "Mode" and t[1].isdigit() else None

    def _time_changed(self, _i):
        """Another saved time: pinned values belong to the time they were read at; the line plot follows the time."""
        if self.pins:
            self.pins = []
            self._pins()
        self._display_changed()
        if self.line_data and len(self.line_ends) >= 2:
            self.fetch_line()

    def _time_note(self):
        f = next((x for x in self.fields if x["name"] == self.field.currentText()), {})
        c = f.get("change_over_time")
        ts = self.timebar.times
        note = ""
        if c is not None and c < 0.03 and len(ts) > 1:
            note = (f"99 % of the points change by less than {c * 100:.2g} % of the {f['name']} colour range between the saved times "
                    f"({exports.fmt_time(ts[0])} to {exports.fmt_time(ts[-1])}), so the animation looks still: the solution had already "
                    "settled by the first saved time. To see the start-up, run a shorter end time.")
        self.timebar.set_times(ts, note)

    def after_draw(self):
        """What follows what is drawn: the distribution under the colour range, the numbers of the range, the deformation note."""
        info = self.view.overlay_info or {}
        rng = info.get("range")
        h = surface_histogram(self.view._ov, rng[0], rng[1]) if rng and info.get("kind") == "scalar" and info.get("name") != "Object" else None
        self.hist_data = h
        if h:
            self.hist.set_data(h["bins"], h["lo"], h["hi"], h["mean"], colormaps.table(self.cmap.currentText(), 64, self.rev.isChecked(), self.bands.value()))
            pct = lambda v: f"{v * 100:.2g} %" if v >= 5e-4 else ""  # noqa: E731
            u = info.get("unit", "")
            self.hist_info.setText(f"mean {h['mean']:.4g} {u}" + (f" · {pct(h['below'])} of the surface below the range" if pct(h["below"]) else "")
                                   + (f" · {pct(h['above'])} above it" if pct(h["above"]) else "")
                                   + ". Drag across the bars to set the range; double-click for automatic.")
        else:
            self.hist.set_data([], 0, 1)
            self.hist_info.setText("No distribution to show for what is drawn.")
        if self.auto_range.isChecked() and rng:
            self.rmin.setText(f"{rng[0]:.4g}")
            self.rmax.setText(f"{rng[1]:.4g}")
        d = info.get("deform")
        if not self.deform.isChecked():
            self.deform_note.setText("The model is drawn undeformed.")
        elif any(k and b.isChecked() for k, b in self.section_btns.items()):
            self.deform_note.setText("A section is drawn undeformed: switch the section off to see the deformed part.")
        elif d:
            self.deform_note.setText(f"Drawn {d['scale']:.3g} times the true deformation" + (" (auto)" if not self.dscale.text().strip() else
                                     f"; auto would be {d['auto']:.3g}") + ".")
        elif info.get("animated"):
            self.deform_note.setText("Mode shapes have no absolute size: the swing is drawn at about 8 % of the model size.")
        else:
            self.deform_note.setText("This field has no deformation to draw.")

    # ------------------------------------------------------------------ videos
    def _video_dialog(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save the video (this window's 3D view is recorded: keep the window open)", f"{self.engine.pid}-{self.field.currentText().replace(' ', '_')}.mp4",
                                              "MP4 video (*.mp4)")
        if path:
            try:
                self.record_video(path)
            except Exception as e:  # noqa: BLE001
                self._fail(e)

    def record_video(self, path: str, fps=None, orbit=None, seconds: float = 4.0) -> dict:
        """An MP4 of what is shown: every saved time of a transient result (the time written on each picture), or a turntable of a steady
        one. fps, orbit (degrees the camera turns over the video): the time bar's choices when omitted."""
        tb = self.timebar
        vs = tb.video_settings()
        fps = float(fps or vs["fps"])
        orbit = float(vs["orbit"] if orbit is None else orbit)
        name = (self.engine.state() or {}).get("name", "")
        if self.recording is not None and self.recording.active:
            raise ValueError("A recording is already running: stop it first (the Stop recording button under the 3D view, or Esc)")
        btn, old = tb.rec, tb.rec.text()
        n_pictures = len(tb.times) if tb.transient else exports.turntable_frames(fps, seconds)
        rec = self.recording = Recording(self.win)
        tb.stop()

        def progress(i, n, what=""):
            if what:
                rec.encoding()
            btn.setText("encoding…" if what else f"{i}/{n}")
            self.win.statusBar().showMessage("Video: encoding" if what else f"● Recording: picture {i} of {n} (keep the window open)", 4000)
            QApplication.processEvents()
        btn.setEnabled(False)
        rec.start(n_pictures)
        try:
            if tb.transient:
                keep, ts = tb.index, tb.times
                try:
                    out = exports.record(self.view, path, fps, len(ts), tb.set_index, orbit,
                                         text=lambda i: (f"t = {exports.fmt_time(ts[i])}", name), progress=progress, gate=rec.gate)
                finally:
                    tb.set_index(keep)
            else:
                out = exports.record(self.view, path, fps, n_pictures, lambda i: None, orbit or 360.0,
                                     text=lambda i: (name,), progress=progress, gate=rec.gate)
        except RecordingStopped as e:
            self.win.toasts.warn(str(e))
            raise
        finally:
            rec.finish()
            btn.setEnabled(True)
            btn.setText(old)
        self.video_info = out
        self.win.statusBar().showMessage(f"Video saved: {out['frames']} pictures at {fps:g} fps, {path}", 9000)
        self.win.toasts.ok((f"Recording stopped: the video has the {out['frames']} pictures made so far" if out.get("stopped") else "Recording finished")
                           + f" ({out['frames']} pictures at {fps:g} fps, {Path(path).name}).")
        return out

    # ------------------------------------------------------------------ the Probe tab
    def _build_probe(self):
        v = self.pages["probe"]
        v.addWidget(_h("Probe"))
        v.addWidget(_muted("Click points on the result to read their value and keep it in the list. Click the model with Probe on. Drag a point's ball in the 3D view (it slides over the model) or type its position to move it."))
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
        v.addWidget(_muted("The field along a straight line between two points. Move an end with its ball in the 3D view or by typing it: the plot follows."))
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
        self.line_ends_box = QWidget()                       # (the two ends as numbers, once there is a line: they follow the handles and move them)
        lb = QVBoxLayout(self.line_ends_box)
        lb.setContentsMargins(0, 0, 0, 0)
        lb.setSpacing(2)
        self.line_edits: list = []
        for k, nm in enumerate("AB"):
            r = QHBoxLayout()
            r.addWidget(QLabel(f"<b>{nm}</b>"))
            es = []
            for a in "xyz":
                e = wid(QLineEdit(), f"results.line.{nm.lower()}.{a}")
                e.setValidator(QDoubleValidator(e))
                e.setMaximumWidth(86)
                e.setToolTip(f"End {nm} of the line, {a.upper()} in mm. Type a value (the end moves to the nearest place on the model), or drag its handle in the 3D view")
                e.editingFinished.connect(lambda k=k: self._line_typed(k))
                r.addWidget(QLabel(a.upper()))
                r.addWidget(e)
                es.append(e)
            r.addWidget(QLabel("mm"))
            r.addStretch(1)
            lb.addLayout(r)
            self.line_edits.append(es)
        self.line_ends_box.setVisible(False)
        v.addWidget(self.line_ends_box)
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
        self.pin_rows = []
        for i, p in enumerate(self.pins):
            col = QVBoxLayout()
            row = QHBoxLayout()
            lab = QLabel("")
            lab.setWordWrap(True)
            row.addWidget(lab, 1)
            d = wid(QPushButton("✕"), f"results.pin.{i}.delete")
            d.setObjectName("ghost")
            d.setToolTip("Remove this pinned value")
            d.clicked.connect(lambda _=False, i=i: self._del_pin(i))
            row.addWidget(d)
            col.addLayout(row)
            xyz = QHBoxLayout()
            edits = []
            for a in "xyz":
                e = wid(QLineEdit(), f"results.pin.{i}.{a}")
                e.setValidator(QDoubleValidator(e))
                e.setMaximumWidth(86)
                e.setToolTip(f"The point's {a.upper()} in mm. Type a value (the point moves to the nearest place on the model), or drag the point's handle in the 3D view")
                e.editingFinished.connect(lambda i=i: self._pin_typed(i))
                xyz.addWidget(QLabel(a.upper()))
                xyz.addWidget(e)
                edits.append(e)
            xyz.addWidget(QLabel("mm"))
            xyz.addStretch(1)
            col.addLayout(xyz)
            w = QWidget()
            w.setLayout(col)
            self.pin_box.addWidget(w)
            self.pin_rows.append({"label": lab, "edits": edits})
            self._pin_texts(i)

    def _pin_texts(self, i: int):
        """The text and the number boxes of pin i, from the pin (no signals: the pin was moved by a handle or by typing)."""
        p, r = self.pins[i], self.pin_rows[i]
        r["label"].setText(f"<b>{chr(65 + i % 26)}</b>  {p['name']}: {fmt_num(p['value'])} {p['unit']}")
        for e, c in zip(r["edits"], p["point"]):
            if not e.hasFocus():
                e.blockSignals(True)
                e.setText(f"{c * 1000:.2f}")
                e.blockSignals(False)

    def _pin_typed(self, i: int):
        try:
            pt = [float(e.text()) / 1000.0 for e in self.pin_rows[i]["edits"]]
        except (ValueError, IndexError):
            return
        self.move_pin(i, pt)

    def move_pin(self, i: int, point, final: bool = True):
        """Pin i goes to the place on the drawn result nearest to `point` (m); its value is read there. final=False while a handle is dragged (cheap)."""
        h = self.view.closest_surface_point(point)
        if h is None:
            raise ValueError("Nothing is drawn to put a point on")
        self.set_pin_hit(i, h[0], h[1], final)

    def set_pin_hit(self, i: int, cell: int, point, final: bool = True):
        p = self.pins[i]
        p["point"] = [float(c) for c in point]
        val = self.view.value_at(cell, point)
        if val is not None:
            p["value"] = val
        self._pin_texts(i)
        if final:
            self._pins()
            self.draw_marks()
        else:
            self._mark_labels()                                  # (while a handle is dragged: only the label follows)

    def _del_pin(self, i):
        if 0 <= i < len(self.pins):
            del self.pins[i]
            self._pins()
            self.draw_marks()

    def _line_texts(self):
        """The numbers of the line's two ends, from the ends (no signals)."""
        self.line_ends_box.setVisible(len(self.line_ends) >= 2)
        for es, pt in zip(self.line_edits, self.line_ends):
            for e, c in zip(es, pt):
                if not e.hasFocus():
                    e.blockSignals(True)
                    e.setText(f"{c * 1000:.2f}")
                    e.blockSignals(False)

    def _line_typed(self, k: int):
        try:
            pt = [float(e.text()) / 1000.0 for e in self.line_edits[k]]
        except ValueError:
            return
        h = self.view.closest_surface_point(pt)
        if h is not None and len(self.line_ends) >= 2:
            self.set_line_end(k, self.view.base_point(h[0], h[1]))

    def set_line_end(self, k: int, base_point, final: bool = True):
        """An end of the line plot (A = 0, B = 1) was moved (its handle, or typed): the plot is made again when final."""
        self.line_ends[k] = [float(c) for c in base_point]
        self._line_texts()
        if final:
            self.fetch_line()
        else:
            self._mark_labels()

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
            d = resample.line_payload(self.engine.project().results().at(self.timebar.time_index()), f, a, b, 160)
        except Exception as e:  # noqa: BLE001
            self.line_info.setText(str(e))
            self.line_data = None
            return None
        self.line_data = d
        self._line_texts()
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
        self.win.toasts.ok("Line data copied: paste it into a spreadsheet")
        return text

    def clear_probes(self):
        self.pins, self.line_ends, self.line_data, self.line_pick = [], [], None, False
        self.line_ends_box.setVisible(False)
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
        self._mark_labels()
        self.draw_outlines()
        self.handles.sync()
        self.after_draw()

    def _mark_labels(self):
        """The labels on the model (the cheap part of draw_marks: also what follows a handle while it is dragged)."""
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
        self._study = st["study"]
        self._summary_s = st["solve"].get("summary") or {}
        try:
            times = list(self.engine.project().results().times) if infos else []
        except Exception:  # noqa: BLE001
            times = []
        if times != self.timebar.times:
            self.timebar.set_times(times)
        self._time_note()
        self.hide_air.setVisible(st["study"] == "cht_enclosure" and bool(infos))
        self._parts(st)
        self._vis_items(st)
        flow = st["study"] in ("external_aero", "cht_enclosure")
        self.tab_btns["flow"].setVisible(flow and bool(infos))
        if flow and infos:
            self.flow.set_fields(infos, self.engine.flow_defaults())
        elif self.stack.currentIndex() == 2:
            self.tab_btns["summary"].click()
        self.deform_box.setVisible(any(f["name"] == "Total deformation" or f["name"].startswith("Mode ") for f in infos))
        self.section_box.setVisible(not flow)
        if flow and infos and self._sec_pid != self.engine.pid:
            self._sec_pid = self.engine.pid           # (a flow result opens with one contour plane through the middle: the air box would hide the object)
            self.flow.set_planes([{"axis": "y", "frac": 0.5}], emit=False)
        sig = json.dumps([st["study"], st["solve"].get("status"), st["solve"].get("summary"), names, theme.effective(),
                          st.get("mesh_check"), st.get("model_check"), st.get("previous_result"), (st.get("mesh") or {}).get("preset"),
                          self._busy(), self.tlimit()], default=str, sort_keys=True)
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

    # ------------------------------------------------------------------ the Summary tab (laid out as the web version's summary)
    def _busy(self) -> bool:
        try:
            return bool(self.engine.pid) and self.engine.project().busy()
        except Exception:  # noqa: BLE001
            return False

    def tlimit(self):
        """The allowed temperature typed for this study (°C), or None: kept in the settings per study."""
        if not self.engine.pid:
            return None
        v = self.engine.settings.value(f"results/tlimit/{self.engine.pid}", "")
        try:
            return float(v) if str(v).strip() else None
        except (TypeError, ValueError):
            return None

    def set_tlimit(self, value):
        """Set (a number, °C) or clear (None / '') the allowed temperature: every part's bar then shows its margin to it."""
        v = None if value in (None, "") else float(value)
        self.engine.settings.setValue(f"results/tlimit/{self.engine.pid}", "" if v is None else repr(v))
        st = self.engine.state()
        self._sig = None
        if st:
            self.refresh(st)
        return v

    # ------------------------------------------------------------------ topology optimisation
    def _topopt_check_card(self, lay, t):
        """The stress and stiffness of the shape at the threshold, solved again with the Static structural solver; the button starts it."""
        ck = t.get("check")
        thr = self.engine.topopt_threshold()
        if not ck:
            lay.addWidget(self._wrapped("Not checked yet. The optimiser knows the stiffness but not the stress: a stiff shape can still be overstressed at a sharp corner."))
            return
        g = lambda v, nd=1: fmt_num(round(float(v), nd))      # noqa: E731
        stale = abs((ck.get("threshold") or 0) - thr) > 1e-6
        sf = ck.get("min_safety_factor")
        lines = [f"Max von Mises stress {g(ck['max_von_mises_MPa'])} MPa" + (f", lowest safety factor {g(sf, 2)}" if sf else "") + f"; largest movement {g(ck['max_displacement_mm'], 4)} mm "
                 f"(the solid part: {g(ck['solid_max_von_mises_MPa'])} MPa, {g(ck['solid_max_displacement_mm'], 4)} mm)."]
        cx = ck.get("calculix")
        if cx and cx.get("error"):
            lines.append("CalculiX check: " + cx["error"])
        elif cx:
            lines.append(f"CalculiX (a separate program) gives a largest movement of {g(cx['max_displacement_mm'], 4)} mm and {g(cx['max_von_mises_MPa'])} MPa for the shape "
                         f"(the static solver here: {g(ck['max_displacement_mm'], 4)} mm, {g(ck['max_von_mises_MPa'])} MPa); the shape moves {g(cx['movement_ratio_shape_over_solid'], 1)} times as much "
                         f"as the solid part (the static solver: {g(cx['movement_ratio_static'], 1)} times).")
        kq, ko = ck.get("stiffness_kept_quadratic"), ck.get("stiffness_kept_optimiser")
        if kq and ko:
            lines.append(f"Stiffness kept: {100 * kq:.0f} % with quadratic elements, {100 * ko:.0f} % as the optimiser predicted.")
        sm, so = ck.get("max_displacement_mm") or 0.0, ck.get("solid_max_displacement_mm") or 0.0
        if so > 0 and sm / so > 3.0:
            lines.append(f"The largest movement anywhere is {sm / so:.0f} times the solid part's, much more than the loss of stiffness: 'stiffness kept' measures the work the load does, an average, "
                         "while a slender region can move a lot. Look at the Deformation field.")
        lines.append("Stress at the held faces and under the loads depends on the mesh (a point load has no finite stress): read the peaks there as indicative"
                     + (f"; the unaveraged peak is {g(ck['peak_von_mises_unaveraged_MPa'])} MPa" if ck.get("peak_von_mises_unaveraged_MPa") else "") + ".")
        lines.append("The shape at a threshold depends on thin members near it: check at 30 %, 50 % and 70 % to see how much the answer moves.")
        if ck.get("load_area_share", 1.0) < 0.95:
            lines.append(f"Only {100 * ck['load_area_share']:.0f} % of the loaded faces is in this shape, so the force acts on that part: the stiffness above is not a like-for-like "
                         "comparison with the solid part (it can even look stiffer). Lower the threshold to bring more of the loaded faces in.")
        if ck.get("left_out_share", 0) > 0.001:
            lines.append(f"{100 * ck['left_out_share']:.1f} % of the shape is in pieces not joined to a support; they carry nothing and were left out of the check.")
        for w in ck.get("warnings") or []:
            lines.append(w)
        tone = "warn" if (sf is not None and sf < 1.5) or stale else "ok"
        if sf is not None and sf < 1.0:
            lines.insert(0, "⚠ The stress is above the yield strength somewhere: this shape would yield under the load.")
        if stale:
            lines.insert(0, f"Checked at a threshold of {100 * ck['threshold']:.0f} %; the slider is at {100 * thr:.0f} %. Press the button to check this one.")
        lay.addWidget(wid(_note("<br>".join(lines), tone, True), "results.topopt.check.result"))

    def _topopt_buttons(self, row):
        """Check stress and stiffness (solves the shape again with the Static structural solver) and Save as STL, side by side under the headline."""
        t = (self.engine.state().get("solve", {}).get("summary") or {}).get("topopt") or {}
        btn = wid(QPushButton("Check stress and stiffness"), "results.topopt.check")
        btn.setObjectName("ghostBorder" if t.get("check") else "primary")
        btn.setToolTip("Solve the shape shown (at the threshold below) again with the quadratic elements of the Static structural study, with the same supports and loads: "
                       "the stress, the safety factor, and how stiff it really is. A minute or less.")
        btn.clicked.connect(self._topopt_check)
        j = P_JOBS().latest(self.engine.pid, "check") if self.engine.pid else None
        running = bool(j and j.status == "running" and (j.extra or {}).get("check_kind") == "shape check")
        btn.setEnabled(not running)
        if running:
            btn.setText("Checking…")
        self.topo_ccx = wid(QCheckBox("also with CalculiX"), "results.topopt.check.ccx")
        self.topo_ccx.setToolTip("Also solve the shape and the solid part with CalculiX, a separate open-source program, and compare the answers: an independent check of the "
                                 "analysis. It takes a few minutes more. Shown when CalculiX is installed (Solve step).")
        try:
            from ...solvers.calculix import installer as _ccx
            self.topo_ccx.setVisible(bool(_ccx.detect().get("available")))
        except Exception:  # noqa: BLE001
            self.topo_ccx.setVisible(False)
        stl = wid(QPushButton("Save as STL…"), "results.topopt.stl")
        stl.setObjectName("ghostBorder")
        stl.setToolTip("Write the shape at the threshold below as a binary STL file (in millimetres) for CAD or 3D printing. The faces the optimiser made are smoothed; "
                       "the part's own faces are left exactly as they are.")
        stl.clicked.connect(self._topopt_stl_dialog)
        row.addWidget(btn)
        row.addWidget(self.topo_ccx)
        row.addWidget(stl)

    def _topopt_refine(self):
        from ...gmsh_worker import PRESETS
        pr = self.engine.project()
        order = list(PRESETS)
        cur = pr.data["mesh"].get("preset", "medium")
        if cur not in order or order.index(cur) == len(order) - 1:
            self.win.statusBar().showMessage("The mesh is already on the finest preset: choose a larger smallest feature size instead", 8000)
            return
        try:
            pr.set_mesh(preset=order[order.index(cur) + 1])
            pr.set_settings(member_mm=0.0)
            self.engine.changed.emit()
            self.win.go("mesh")
            self.win.statusBar().showMessage(f"Mesh preset set to {order[order.index(cur) + 1]}: generate the mesh, then find the shape again", 9000)
        except Exception as e:  # noqa: BLE001
            self._fail(e)

    def _topopt_check(self):
        try:
            self.engine.project().topopt_check(self.engine.topopt_threshold(), cross_check=bool(self.topo_ccx.isVisible() and self.topo_ccx.isChecked()))
            self.engine.changed.emit()
        except Exception as e:  # noqa: BLE001
            self._fail(e)

    def _topopt_stl_dialog(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save the shape as STL", f"{self.engine.pid}-shape.stl", "STL file (*.stl)")
        if path:
            try:
                out = self.engine.topopt_export_stl(path)
                self.win.statusBar().showMessage(f"Saved {out['triangles']:,} triangles to {path}", 9000)
            except Exception as e:  # noqa: BLE001
                self._fail(e)

    @staticmethod
    def _wrapped(text):
        lab = _muted(text)
        lab.setWordWrap(True)
        return lab

    def _topopt_tiles(self, lay, st, s):
        """The headline in plain words and the four numbers that matter: mass kept, stiffness kept, the smallest feature, how the run ended."""
        t = s.get("topopt") or {}
        if getattr(self, "_topo_pid", None) != self.engine.pid:           # (a density is shown by colour and is meant to be read as material: viridis, not the rainbow)
            self._topo_pid = self.engine.pid
            self.cmap.blockSignals(True)
            self.cmap.setCurrentText("viridis")
            self.cmap.blockSignals(False)
            for box in (self.minmax,):                                     # (the lowest and highest density are labelled on the model by default: meaningless for a density)
                box.blockSignals(True)
                box.setChecked(False)
                box.blockSignals(False)
        g = lambda kg: fmt_num(round(kg * 1000, 1)) + " g"          # noqa: E731
        kept, stiff = t.get("mass_fraction") or 0.0, t.get("stiffness_kept") or 0.0
        lay.addWidget(wid(_note(f"A stiff shape for these loads: it keeps {100 * kept:.0f} % of the mass ({g(t.get('mass_kg_kept') or 0)} of {g(t.get('mass_kg_solid') or 0)}) and, "
                                f"by the optimiser's estimate, {100 * stiff:.0f} % of the stiffness of the solid part. A concept: its stress has not been checked.",
                                "warn" if (s.get("warnings") or []) else "ok"),
                          "results.topopt.headline"))
        holder = QWidget()                                                       # (wraps: three buttons side by side are wider than the dock)
        row = FlowLayout(holder, 6, 4)
        row.setContentsMargins(0, 0, 0, 0)
        self._topopt_buttons(row)
        lay.addWidget(holder)
        self._topo_check_lay = QVBoxLayout()                                   # (rebuilt when the slider moves: the check belongs to one threshold)
        self._topo_check_lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(self._topo_check_lay)
        self._topopt_check_card(self._topo_check_lay, t)
        holder = QWidget()                                                       # (wraps: three buttons side by side are wider than the dock)
        row = FlowLayout(holder, 6, 4)
        row.setContentsMargins(0, 0, 0, 0)
        self._topopt_buttons(row)
        lay.addWidget(holder)
        self._topo_check_lay = QVBoxLayout()                                   # (rebuilt when the slider moves: the check belongs to one threshold)
        self._topo_check_lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(self._topo_check_lay)
        self._topopt_check_card(self._topo_check_lay, t)
        notes = list(s.get("warnings") or [])
        if notes:                                                           # (what looks odd, said before the numbers: not at the bottom of the page)
            box = _item()
            bv = QVBoxLayout(box)
            bv.setContentsMargins(10, 8, 10, 8)
            title = QLabel("⚠  Read before you rely on this shape")
            title.setStyleSheet(f"font-weight: 600; color: {theme.tokens()['warn_text']};")
            title.setWordWrap(True)
            bv.addWidget(title)
            for w_ in notes:
                lab = QLabel("• " + w_)
                lab.setWordWrap(True)
                bv.addWidget(lab)
            lay.addWidget(wid(box, "results.topopt.cautions"))
            if any("cannot thin" in w_ or "read" in w_ and "stiffer" in w_ for w_ in notes):
                fix = wid(QPushButton("Refine the mesh and run again"), "results.topopt.refine")
                fix.setObjectName("ghostBorder")
                fix.setToolTip("One finer mesh preset, the smallest feature size back on automatic: finer cells let the shape thin its walls and make the stiffness figure more exact. "
                               "Takes you to the Mesh step; generating the mesh and running again are two more clicks.")
                fix.clicked.connect(self._topopt_refine)
                lay.addWidget(fix, 0, Qt.AlignLeft)
        ratio = (t.get("radius_mm") or 0) / max(t.get("element_size_mm") or 1e-9, 1e-9)
        end = ("settled" if t.get("converged") else "stopped by you: usable" if t.get("stopped") else "limit reached: may still change")
        return [("Mass kept", f"{100 * kept:.0f}", "%", "", f"{g(t.get('mass_kg_kept') or 0)} of {g(t.get('mass_kg_solid') or 0)}", "Density"),
                ("Stiffness kept", f"{100 * stiff:.0f}", "%", "", "of the solid part", "Strain energy density"),
                ("Smallest feature", fmt_num(round(t.get("radius_mm") or 0, 1)), "mm", "ok" if ratio >= 4 else "warn", f"{ratio:.1f} mesh cells wide" + ("" if ratio >= 4 else ": a finer mesh is more exact"), None),
                ("Iterations", str(t.get("iterations", "")), "", "ok" if t.get("converged") else "warn", end, None)]

    def _topopt_extras(self, lay, st, s):
        """Under the numbers: the threshold that decides which material is shown, what the colours mean, the history of the run, and what to do next."""
        from ..charts import XYChart
        t = s.get("topopt") or {}
        lay.addWidget(_h("Show the shape"))
        row = QHBoxLayout()
        row.addWidget(QLabel("Show material denser than"))
        sl = wid(QSlider(Qt.Horizontal), "results.topopt.threshold")
        sl.setRange(5, 95)
        sl.setSingleStep(5)
        sl.setValue(int(round(self.engine.topopt_threshold() * 100)))
        sl.setToolTip("Every element has a density from 0 (empty) to 1 (solid). Elements below this value are left out of the picture. 50 % is the usual choice; "
                      "a lower value shows more material and a higher one less.")
        row.addWidget(sl, 1)
        val = wid(QLabel(""), "results.topopt.threshold.value")
        row.addWidget(val)
        lay.addLayout(row)
        shown = wid(_muted(""), "results.topopt.threshold.text")
        shown.setWordWrap(True)
        lay.addWidget(shown)

        def update(v=None):
            if v is not None:
                self.engine.set_topopt_threshold(v / 100.0)
                try:
                    clear_layout(self._topo_check_lay)
                    self._topopt_check_card(self._topo_check_lay, t)
                except RuntimeError:
                    pass
            val.setText(f"{self.engine.topopt_threshold() * 100:.0f} %")
            try:
                sh = self.engine.topopt_shown()
                shown.setText(f"At this threshold the picture holds {100 * sh['share']:.0f} % of the part's volume ({fmt_num(round(sh['kg'] * 1000, 1))} g). "
                              f"The run was set to keep {100 * (t.get('mass_target') or 0):.0f} %; the sharp shape at 50 % holds {100 * (t.get('mass_fraction_threshold') or 0):.0f} %.")
            except Exception:  # noqa: BLE001
                shown.setText("")
            if v is not None:
                self._display_changed()
        sl.valueChanged.connect(update)
        update()
        lay.addWidget(self._wrapped("Colours show the density: 0 is empty and 1 is solid. In-between values are material the optimiser could not decide on; "
                             f"{100 * (t.get('grey_fraction') or 0):.0f} % of the changeable material is in between here."))
        hist = t.get("history") or []
        if hist:
            lay.addWidget(_h("Stiffness and mass during the run"))
            ch = XYChart()
            ch.setMinimumHeight(170)
            c_full = t.get("compliance_solid")
            ch.set_data([{"name": "Stiffness kept (%)", "x": [h["iteration"] for h in hist], "y": [100.0 * c_full / h["compliance"] if h["compliance"] > 0 else float("nan") for h in hist]},
                         {"name": "Mass (%)", "x": [h["iteration"] for h in hist], "y": [100.0 * h["mass_fraction"] for h in hist]},
                         {"name": "Sharpness (%)", "x": [h["iteration"] for h in hist], "y": [100.0 * (1 - h["grey"]) for h in hist]}],
                        xunit="iteration", yunit="%", ylim=(0, 105), legend=True)
            lay.addWidget(wid(ch, "results.topopt.chart"))
        gap = t.get("linear_vs_quadratic")
        if gap is not None:
            lay.addWidget(self._wrapped(f"On this mesh the optimiser's linear elements read {abs(gap) * 100:.0f} % {'stiffer' if gap < 0 else 'softer'} than the quadratic ones of the Static structural study. "
                                 "'Stiffness kept' compares the optimised part with the solid part in the same model, so the two errors largely cancel; absolute deflections need the static study."))
        ks = t.get("kept_solid") or []
        if ks:
            lay.addWidget(self._wrapped("Kept solid by design: " + ", ".join(f"{k['name']} ({100 * k['share']:.0f} % of the mass)" for k in ks)
                                        + ". The optimiser never removes the material a support or a load is attached to."))
        lay.addWidget(_h("What to do next"))
        lay.addWidget(wid(_note("This is a concept shape, not a finished part. It is the stiffest for the one set of loads you gave.<br>"
                                "1. Press <b>Check stress and stiffness</b> above: it solves this shape again with the quadratic elements of the Static structural study. "
                                "2. Move the slider or re-run with a larger smallest feature size for a simpler, sturdier shape, or a different mass to keep. "
                                "3. Save the STL and rebuild or smooth the shape in CAD, then run a Static structural study on it.", "", True),
                          "results.topopt.next"))

    def _tiles(self, lay, tiles):
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

    def _chart(self, lay, chart, wid_id, caption=""):
        lay.addWidget(wid(chart, wid_id))
        if caption:
            lay.addWidget(_muted(caption))
        return chart

    def _animate_box(self, lay, text):
        """'Animate' under the modes (the web version's): the same switch as the Display tab's deformation mode."""
        b = wid(QCheckBox(text), "results.animate")
        b.setToolTip("Swing the shape (or apply and release the load) on the model; untick for the end state")
        b.setChecked(self.deform.isChecked() and self.deform_mode.currentText() == "Animated")
        b.toggled.connect(lambda on: (self.deform.setChecked(True), self.deform_mode.setCurrentText("Animated" if on else "End state")))
        lay.addWidget(b)

    def _shape_link(self, lay, text, field, wid_id, tip):
        b = wid(ClickLabel(text + "  →"), wid_id)
        b.setStyleSheet(f"color: {theme.tokens()['accent_text']}; padding: 2px 4px;")
        b.setToolTip(tip)
        b.clicked.connect(lambda f=field: self.show_field(f))
        lay.addWidget(b)

    def _temp_bars(self, lay, rows, t_lo, unit="°C"):
        """The highest temperature of each part on one scale from the coolest to the hottest, and the allowed temperature (typed in, kept per
        study) as the limit line on every bar, with each part's margin in words."""
        lim = self.tlimit()
        row = QHBoxLayout()
        row.addWidget(QLabel("Allowed temperature"))
        e = wid(QLineEdit("" if lim is None else f"{lim:g}"), "results.tlimit")
        e.setPlaceholderText("none")
        e.setValidator(QDoubleValidator(e))
        e.setMaximumWidth(90)
        e.setToolTip("The highest temperature the parts may reach (°C): every bar then shows the part's margin to it. Empty: no limit")
        e.editingFinished.connect(lambda e=e: self.set_tlimit(e.text().strip() or None) if (e.text().strip() or None) != (
            None if self.tlimit() is None else f"{self.tlimit():g}") else None)
        row.addWidget(e)
        row.addWidget(QLabel(unit))
        row.addStretch(1)
        lay.addLayout(row)
        hi = max([r["T_max"] for r in rows] + ([lim] if lim is not None else []))
        lo = min([t_lo] + [r["T_max"] for r in rows])
        span = max(hi - lo, 1e-9)
        for r in rows:
            tone = "accent" if lim is None else "bad" if r["T_max"] > lim else "warn" if lim - r["T_max"] < 0.1 * (lim - lo) else "ok"
            txt = f"{_f(r['T_max'])} {unit}" + (f" · mean {_f(r['T_mean'])}" if r.get("T_mean") is not None else "")
            if r.get("heat_W"):
                txt += f" · {_f(r['heat_W'], 3)} W"
            if lim is not None:
                txt += f" · {'+' if r['T_max'] > lim else '−'}{_f(abs(lim - r['T_max']), 3)} K {'over' if r['T_max'] > lim else 'margin'}"
            lay.addWidget(HBar(r["label"], txt, (r["T_max"] - lo) / span, None if lim is None else (lim - lo) / span, tone))
        lay.addWidget(_muted(f"Bars run from {_f(lo)} to {_f(hi)} {unit}" + ("; the line is the allowed temperature." if lim is not None else
                                                                             ". Type an allowed temperature to see the margin of every part.")))

    def _convergence(self, lay, st, c):
        if not c:
            return
        if c.get("transient"):
            txt = c.get("note") or "Transient run."
            ts = c.get("time_step_s")
            if ts:
                txt += f" Time step {exports.fmt_time(ts['min'])} to {exports.fmt_time(ts['max'])}" + (
                    f", max Courant number {_f(c['max_courant'], 2)}" if c.get("max_courant") is not None else "") + "."
            lay.addWidget(wid(_note(txt, "" if c.get("completed") else "warn"), "results.convergence"))
            return
        if c.get("converged"):
            lay.addWidget(wid(_note(f"✓  Converged after {c.get('iterations', 0):,} iterations. {c.get('note') or ''}", "ok"), "results.convergence"))
        else:
            lay.addWidget(wid(_note(f"Not converged after {c.get('iterations', 0):,} iterations: preliminary numbers. Continue the run in step 4 "
                                    "(Solve) for settled numbers.", "warn"), "results.convergence"))
        h = c.get("history")
        if h and h.get("iters"):
            ch = XYChart(120, "Solver residuals", "Solver residuals against the iteration (logarithmic): they fall as the solution settles")
            ch.set_data([{"name": k, "x": h["iters"], "y": v} for k, v in h["series"].items()], logy=True, xunit="iterations")
            self.residuals = self._chart(lay, ch, "results.chart.residuals", "Solver residuals against the iteration (logarithmic): they fall as "
                                         "the solution settles. The run also counts as converged when the key results stop changing.")

    def _history(self, lay, st, s):
        h = s.get("history")
        if not h or not h.get("t"):
            return
        lay.addWidget(_h("Forces over time" if st["study"] == "external_aero" else "Temperature over time"))
        t = np.asarray(h["t"], float)
        y0 = float(h.get("y_from_t") or 0)
        ch = XYChart(150, "History", "The key results against time")
        ch.set_data([{"name": k, "x": t, "y": [v if ti >= y0 else None for ti, v in zip(t, vals)], "dashed": k.endswith(" mean")}
                     for k, vals in h["series"].items()], xunit="s")
        note = "Solid: max, dashed: mean per part." if st["study"] == "heat_conduction" else (s.get("history_note") or "")
        if s.get("drag_fluctuation_percent") is not None:
            note += f" Drag fluctuates by ±{_f(s['drag_fluctuation_percent'], 2)} % (standard deviation) over that time."
        self.history_chart = self._chart(lay, ch, "results.chart.history", note.strip())

    def _summary(self, st, s, has_results):
        lay = self.pages["summary"]
        clear_layout(lay)
        self.spectrum = None
        if not has_results:
            lay.addWidget(_muted("No results yet. Run the simulation first (step 4, Solve)."))
            lay.addStretch(1)
            return
        study = st["study"]
        if (st.get("solve") or {}).get("status") == "stale":
            row = QHBoxLayout()
            row.addWidget(_note("Out of date: the setup changed after this run.", "warn"), 1)
            b = wid(QPushButton("Re-run"), "results.rerun")
            b.setObjectName("ghostBorder")
            b.setToolTip("Go to step 4 (Solve) to run the simulation again with the new setup")
            b.clicked.connect(lambda: self.win.go("solve"))
            row.addWidget(b)
            lay.addLayout(row)
        if (st.get("solve") or {}).get("note"):
            lay.addWidget(_note((st.get("solve") or {})["note"], "warn"))
        self._convergence(lay, st, s.get("convergence"))
        self._history(lay, st, s)
        preset = (st.get("mesh") or {}).get("preset")
        if study not in SOLID_STUDIES and preset in ("quick", "coarse"):
            mc = st.get("mesh_check")
            measured = ("Measured on this project (" + f"{mc['from']} → {mc['to']}): " + ", ".join(
                f"{r['name']} {_f(r.get('raw_change_percent', r.get('change_percent')), 2)} %" for r in mc.get("rows", [])) + "."
                        if mc and not mc.get("stale") and mc.get("from") == preset else "Run the mesh-sensitivity check (Checks tab).")
            lay.addWidget(wid(_note(f"{'Quick-look' if preset == 'quick' else 'Coarse'} mesh: indicative only. {measured}", "warn"), "results.coarse_note"))
        tiles, extra = [], []
        lim = self.tlimit()
        if study == "topology_optimization":
            tiles = self._topopt_tiles(lay, st, s)
        elif "max_von_mises_MPa" in s:
            sf = s.get("min_safety_factor")
            tiles = [("Max von Mises stress", f"{s['max_von_mises_MPa']:.1f}", "MPa", "", "", "Von Mises stress"),
                     ("Max deformation", fmt_num(round(s.get("max_displacement_mm", 0), 4)), "mm", "", "", "Total deformation")]
            if sf:
                tone, word = _tone_sf(sf)
                tiles.append(("Safety factor (yield)", f"{sf:.2f}", "", tone, word, "Safety factor"))
            if s.get("mass_kg") is not None:
                tiles.append(("Mass", fmt_num(round(s["mass_kg"] * 1000, 1)), "g", "", "", None))
        elif "T_max" in s:
            eb = s.get("energy_balance") or {}
            tr = s.get("transient")
            if tr:
                lay.addWidget(_muted(f"Transient: {exports.fmt_time(tr['end_time_s'])} in {tr['steps']} steps of {exports.fmt_time(tr['time_step_s'])}. "
                                     "The numbers below are at the end time" + (f"; the highest temperature at any time was {_f(tr['T_max_any_time'])} °C"
                                                                                  if tr.get("T_max_any_time", 0) > s["T_max"] + 0.05 else "") + "."))
            tiles = [("Max temperature", fmt_num(round(s["T_max"], 1)), "°C", "" if lim is None else ("bad" if s["T_max"] > lim else "ok"),
                      "" if lim is None else f"{'over' if s['T_max'] > lim else 'under'} the {lim:g} °C limit", "Temperature"),
                     ("Min temperature", fmt_num(round(s["T_min"], 1)), "°C", "", "", None)]
            if eb:
                tiles += [("Heat in", fmt_num(round(eb.get("heat_in_W", 0), 2)), "W", "", "", None),
                          ("Heat out", fmt_num(round(eb.get("heat_out_W", 0), 2)), "W",
                           "ok" if abs(eb.get("imbalance_percent", 0)) < 1 else "warn",
                           "heat balance closes" if abs(eb.get("imbalance_percent", 0)) < 0.01 else f"{eb.get('imbalance_percent', 0):.2g} % off", None)]
        elif "first_frequency_Hz" in s and study != "aeroelastic_flutter":
            band = s.get("excitation_band_Hz")
            inband = s.get("modes_in_band") or []
            f1 = s["first_frequency_Hz"]
            el = [m for m in (s.get("modes") or []) if not m.get("rigid_body")]
            m0 = el[0] if el else {}
            hit = band and (m0.get("in_excitation_band") if "in_excitation_band" in m0 else band[0] <= f1 <= band[1])
            near = band and m0.get("near_excitation_band")
            tiles = [("First natural frequency", fmt_num(round(f1, 1)), "Hz", "bad" if hit else "warn" if near else ("ok" if band else ""),
                      "inside the excitation range" if hit else "close to the excitation range" if near else ("clear of the excitation range" if band else ""), "Mode 1")]
            if band:
                tiles.append(("Modes in excitation range", str(len(inband)), f"of {len(el)}", "bad" if inband else "ok", "", None))
            if s.get("mass_kg") is not None:
                tiles.append(("Mass", fmt_num(round(s["mass_kg"] * 1000, 1)), "g", "", "", None))
        elif study == "aeroelastic_flutter":
            cr, fl, mg = s.get("critical"), s.get("flutter"), s.get("margin")
            if cr:
                tiles = [("Flutter speed" if cr.get("type") == "flutter" else "Divergence speed", _f(cr["speed_m_s"]), "m/s", "", "", None)]
                if fl and cr.get("type") == "flutter":
                    tiles.append(("Flutter frequency", _f(fl["frequency_Hz"]), "Hz", "", "", "Flutter mode"))
                tiles.append(("Mach", _f(cr.get("mach"), 3), "", "", "", None))
                if mg:
                    tiles.append(("Margin on design speed", _f(mg["ratio"], 3), f"(needs ≥ {_f(mg['required'], 3)})",
                                  "bad" if mg["ratio"] < mg["required"] else "ok", "", None))
            else:
                tiles = [("First natural frequency", _f(s.get("first_frequency_Hz")), "Hz", "", "", "Mode 1"),
                         ("Highest speed examined", _f((s.get("speed_range_m_s") or [0, 0])[1]), "m/s", "ok", "no flutter or divergence", None)]
        elif "drag_N" in s:                      # external aerodynamics
            co = s.get("coefficients") or {}
            yp = s.get("yPlus") or {}
            tiles = [("Drag force", f"{s['drag_N']:.3g}", "N", "", f"at {s.get('speed_m_s', 0):g} m/s", "Pressure")]
            if s.get("CdA_m2") is not None:
                tiles.append(("Drag area CdA", f"{s['CdA_m2'] * 1e4:.3g}", "cm²", "", f"Cd {co.get('CD', 0):.3f}" + (f", on the {co['A_ref_source']}" if co.get("A_ref_source") else ""), None))
            if co:
                tiles.append(("Lift force", f"{co.get('lift_N', 0):.3g}", "N", "", "negative = pushes the body down", None))
            if s.get("rotor_thrust_total_N") is not None:
                tiles.append(("Rotor thrust", _f(s["rotor_thrust_total_N"]), "N", "", "", None))
            if yp.get("mean") is not None:
                good = 30 <= yp["mean"] <= 300
                tiles.append(("Wall y+ (mean)", f"{yp['mean']:.0f}", "", "ok" if good else "warn",
                              "inside the 30 to 300 the wall functions expect" if good else f"outside the 30 to 300 the wall functions expect (max {yp.get('max', 0):.0f})", "y+"))
            tiles.append(self._converged_tile(s))
        elif "hottest_part" in s:               # electronics cooling
            hot = s.get("hottest_part") or {}
            over = lim is not None and hot.get("T_max", 0) > lim
            tiles = [(f"Hottest part ({hot.get('label', '')})".replace(" ()", ""), f"{hot.get('T_max', 0):.1f}", "°C",
                      "" if lim is None else ("bad" if over else "ok"), "" if lim is None else f"{'over' if over else 'under'} the {lim:g} °C limit", "Temperature"),
                     ("Thermal resistance", f"{s['thermal_resistance_K_per_W']:.2f}" if s.get("thermal_resistance_K_per_W") is not None else "-", "K/W", "",
                      "hottest part above the inlet air, per watt", None)]
            if s.get("natural"):
                tiles += [("Max air speed", _f(s.get("velocity_max"), 2), "m/s", "", "", "Velocity"), ("Room", _f(s.get("room_T")), "°C", "", "", None)]
            else:
                imb = abs(s.get("energy_imbalance_percent") or 0)
                tiles += [("Air leaves at", _f(round(s.get("outlet_T_mixed", s.get("outlet_T_area_avg")) or 0, 1)), "°C", "", f"it came in at {s.get('inlet_T', 0):g} °C", None),
                          ("Pressure drop", f"{s.get('pressure_drop_Pa') or 0:.2f}", "Pa", "", f"what a fan must supply ({s.get('flow_m3_per_h', 0):.1f} m³/h)", "Pressure"),
                          ("Heat taken by the air", f"{s.get('heat_carried_by_air_W', 0):.2f}", "W", "ok" if imb < 2 else "warn",
                           f"of {s.get('heat_input_W', 0):g} W put in ({imb:.1f} % off)", None)]
            tiles.append(self._converged_tile(s))
        else:
            for k, val in list(s.items())[:6]:
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    tiles.append((k.replace("_", " ").capitalize(), fmt_num(val), "", "", "", None))
        self._tiles(lay, tiles)
        if study == "topology_optimization":
            self._topopt_extras(lay, st, s)
        # ---- what each study adds under its tiles
        if study == "vibration_modal" or (study != "aeroelastic_flutter" and s.get("modes")):
            self._modal(lay, s)
        elif study == "aeroelastic_flutter":
            self._flutter(lay, s)
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
            self._temp_bars(lay, [{"label": b["name"], "T_max": b["T_max"], "T_mean": b.get("T_mean")} for b in bodies.values()], s["T_min"])
        if study == "heat_conduction" and s.get("energy_balance"):
            eb = s["energy_balance"]
            bnd = s.get("boundaries") or {}
            outs = [{"label": k, "v": b.get("heat_out_W") or 0} for k, b in bnd.items() if (b.get("heat_out_W") or 0) > 0]
            if (eb.get("heat_stored_W") or 0) > 0:
                outs.append({"label": "Stored in the parts (still heating)", "v": eb["heat_stored_W"], "color": theme.tokens()["muted"]})
            if outs:
                lay.addWidget(_h("Where the heat goes"))
                lay.addWidget(wid(StackBar(outs, eb.get("heat_in_W") or 0), "results.chart.heat"))
            for k, b in bnd.items():
                bits = [f"{_f(b['heat_out_W'])} W out" if b.get("heat_out_W") is not None else f"{_f(b['heat_in_W'])} W in" if b.get("heat_in_W") is not None else ""]
                if b.get("T_mean") is not None:
                    bits.append(f"{_f(b['T_mean'])} °C avg")
                if b.get("h_mean_W_m2K") is not None:
                    bits.append(f"h {_f(b['h_mean_W_m2K'], 3)} W/m²K mean")
                lay.addWidget(_muted(f"{k}: " + " · ".join(x for x in bits if x)))
            lay.addWidget(_muted(f"Heat in {_f(eb.get('heat_in_W'))} W = out {_f(eb.get('heat_out_W'))} W"
                                 + (f" + stored {_f(eb['heat_stored_W'])} W (at the end time)" if eb.get("heat_stored_W") is not None else "")))
        if study == "cht_enclosure" and s.get("solids"):
            lay.addWidget(_h("Parts"))
            self._temp_bars(lay, [{"label": b.get("label") or k, "T_max": b["T_max"], "heat_W": b.get("heat_W")} for k, b in s["solids"].items()],
                            s.get("room_T") if s.get("natural") else s.get("inlet_T", 0))
            lay.addWidget(_h("Where the heat goes" + (" (no fan)" if s.get("natural") else "")))
            if s.get("natural"):
                parts = [{"label": "Warm air rising out of the openings", "v": s.get("heat_carried_by_air_W") or 0}]
                if s.get("radiation"):
                    parts.append({"label": "Radiation out to the room", "v": s.get("heat_radiated_out_W") or 0})
                if s.get("heat_through_walls_W"):
                    parts.append({"label": "Through cooled walls", "v": s["heat_through_walls_W"]})
            else:
                parts = [{"label": "Carried away by the air", "v": s.get("heat_carried_by_air_W") or 0},
                         {"label": "Still heating the parts" if s.get("transient") else "Not accounted for",
                          "v": max(0.0, (s.get("heat_input_W") or 0) - (s.get("heat_carried_by_air_W") or 0)), "color": theme.tokens()["muted"]}]
            lay.addWidget(wid(StackBar(parts, s.get("heat_input_W") or 0), "results.chart.heat"))
            if (s.get("flow_regime") or {}).get("note"):
                lay.addWidget(_muted(s["flow_regime"]["note"] + "."))
            cc = s.get("correlation_check")
            if cc and not cc.get("skipped") and (not s.get("convergence") or s["convergence"].get("converged")):
                lay.addWidget(_note("Hand-calculation cross-check. " + (cc.get("note") or ""),
                                    "warn" if abs(cc.get("difference_percent") or 0) > (cc.get("expected_band_percent") or 25) else ""))
        if study == "external_aero":
            self._aero(lay, s)
        # loads and reactions
        if "applied_force_N" in s:
            lay.addWidget(_muted("Load " + " / ".join(fmt_num(round(x, 3)) for x in s["applied_force_N"]) + " N, reaction "
                                 + " / ".join(fmt_num(round(x, 3)) for x in s["reaction_force_N"]) + " N (x / y / z)."))
            sup = [(k, b["reaction_N"]) for k, b in (s.get("boundary_conditions") or {}).items() if b.get("reaction_N")]
            if sup:
                lay.addWidget(_h("Force at each support"))
                top = max(float(np.linalg.norm(r)) for _, r in sup) or 1.0
                for i, (k, r) in enumerate(sup):
                    mag = float(np.linalg.norm(r))
                    lay.addWidget(wid(SignedBar(k, " / ".join(_f(x) for x in r) + f" N (|F| {_f(mag)} N)", mag / top), f"results.support.{i}"))
                lay.addWidget(_muted("The force each support takes (x / y / z); the bar is its size against the largest."))
        if study == "static_structural":
            self._animate_box(lay, "Animate deformation")
        # cautions
        warns = [] if study == "topology_optimization" else list(s.get("warnings") or [])
        if s.get("warning_stale"):
            warns.append(s["warning_stale"])
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
        if s.get("n_dof") or s.get("unknowns"):
            bits.append(f"{(s.get('n_dof') or s.get('unknowns')):,} unknowns")
        if s.get("solve_time_s") or s.get("seconds"):
            bits.append(f"{(s.get('solve_time_s') or s.get('seconds')):.2f} s")
        if s.get("peak_von_mises_unaveraged_MPa"):
            bits.append(f"unaveraged peak {s['peak_von_mises_unaveraged_MPa']:.0f} MPa")
        if s.get("stress_error_percent") is not None:
            bits.append(f"estimated stress error {s['stress_error_percent']:.0f} %")
        if s.get("added_mass_kg"):
            bits.append(f"added mass {_f(s['added_mass_kg'] * 1000)} g")
        if s.get("n_rigid_body_modes"):
            bits.append(f"{s['n_rigid_body_modes']} rigid-body modes hidden")
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

    def _modal(self, lay, s):
        """Natural frequencies: the spectrum (click a stem), a row per mode with its effective mass (click it), Animate, and the shake test."""
        el = [m for m in (s.get("modes") or []) if not m.get("rigid_body")]
        band = s.get("excitation_band_Hz")
        if s.get("band_note"):
            lay.addWidget(_note(("△  " if s.get("band_caution") else "✓  ") + s["band_note"], "warn" if s.get("band_caution") else ""))
        lay.addWidget(_h("Natural frequencies"))
        self.spectrum = SpectrumChart()
        self.spectrum.set_data(el, band, self._mode_shown())
        self.spectrum.modeClicked.connect(lambda k: self.show_field(f"Mode {k}"))
        self._chart(lay, self.spectrum, "results.chart.spectrum",
                    "Each stem is a mode: its height is the share of the mass that moves in its main direction (x, y or z beside the number)"
                    + ("; the red band is the excitation range" if band else "") + ". Click a stem or a row to show the mode.")
        pct = lambda x: "0" if x < 5e-4 else f"{100 * x:.3g}"  # noqa: E731
        for m in el:
            k = m.get("elastic_index", m.get("mode"))
            e = m.get("effective_mass_fraction")
            mark = "⚠ " if m.get("in_excitation_band") else "△ " if m.get("near_excitation_band") else ""
            em = f" · mass {pct(e['x'])} / {pct(e['y'])} / {pct(e['z'])} %" if e else ""
            self._shape_link(lay, f"Mode {k} - {mark}{fmt_num(round(m['frequency_Hz'], 1))} Hz{em}", f"Mode {k}", f"results.mode.{k}",
                             f"Show the shape of mode {k} on the model (effective mass x / y / z: the share of the mass that moves with it)")
        cm = s.get("cumulative_mass_fraction")
        if cm:
            lay.addWidget(_muted(f"Sum of these modes: {pct(cm['x'])} / {pct(cm['y'])} / {pct(cm['z'])} % of the mass (x / y / z)."))
        self._animate_box(lay, "Animate")
        q = s.get("sine")
        if q:
            w = q.get("worst") or {}
            lay.addWidget(_h(f"Shake test: {_f(q.get('accel_g'))} g sine along {q.get('axis')}, {_f(q.get('damping_percent'))} % damping"))
            t = [("Highest stress", _f(w.get("max_stress_MPa")), "MPa", "", "", None), ("at", _f(w.get("frequency_Hz")), "Hz", "", "", None),
                 ("Largest movement", _f(w.get("max_displacement_mm")), "mm", "", "", None)]
            if w.get("safety_factor_yield") is not None:
                tone, word = _tone_sf(w["safety_factor_yield"])
                t.append(("Safety factor (yield) in the shake test", _f(w["safety_factor_yield"], 3), "", tone, word, None))
            self._tiles(lay, t)
            ch = XYChart(150, "Shake test sweep", "Highest stress anywhere in the part against the shaking frequency (both axes logarithmic)")
            ch.set_data([{"name": "max stress", "x": q["frequency_Hz"], "y": q["max_stress_MPa"], "color": "#e09b1a"}], logx=True, logy=True,
                        xunit="Hz", yunit="MPa", dots=[(k["frequency_Hz"], k["max_stress_MPa"]) for k in q.get("peaks") or []])
            rng = q.get("range_Hz") or [q["frequency_Hz"][0], q["frequency_Hz"][-1]]
            self.sweep_chart = self._chart(lay, ch, "results.chart.sweep",
                                           f"Highest stress anywhere in the part against the shaking frequency (both axes logarithmic), {_f(rng[0])} to "
                                           f"{_f(rng[1])} Hz. Each peak (dot) is a resonance; the fields 'Shake test: …' show the part at the worst one.")
            for k in q.get("peaks") or []:
                lay.addWidget(_muted(f"Mode {k['mode']}: {_f(k['frequency_Hz'])} Hz · {_f(k['max_stress_MPa'])} MPa · {_f(k['max_displacement_mm'], 3)} mm"
                                     + (f" · {_f(k['max_acceleration_g'])} g" if k.get("max_acceleration_g") is not None else "")))

    def _flutter(self, lay, s):
        fl, dv, cr, mg = s.get("flutter"), s.get("divergence"), s.get("critical"), s.get("margin")
        if cr and mg:
            lay.addWidget(_note(f"△  Flutter margin {_f(mg['ratio'], 3)} is below the {_f(mg['required'], 3)} usually required." if mg["ratio"] < mg["required"]
                                else f"✓  Margin {_f(mg['ratio'], 3)} on the design speed {_f(mg['design_speed_m_s'])} m/s.",
                                "warn" if mg["ratio"] < mg["required"] else "ok"))
        elif not cr:
            lay.addWidget(_note(f"✓  No flutter or divergence up to {_f((s.get('speed_range_m_s') or [0, 0])[1])} m/s.", "ok"))
        if fl:
            lay.addWidget(_muted("Flutter motion: " + " with ".join(f"mode {c['mode']} ({_f(c['share_percent'], 3)} %)" for c in fl.get("mode_content") or [])
                                 + f" - the modes merge at {_f(fl['frequency_Hz'])} Hz. Equivalent airspeed {_f(fl.get('equivalent_airspeed_m_s'))} m/s."))
        if dv:
            lay.addWidget(_muted(f"Divergence (static instability): {_f(dv['speed_m_s'])} m/s (Mach {_f(dv.get('mach'), 3)})."))
        elif s.get("divergence_speed_any_m_s"):
            lay.addWidget(_muted(f"Divergence would occur at {_f(s['divergence_speed_any_m_s'])} m/s, above the speeds examined."))
        sw = s.get("sweep")
        if sw and sw.get("speed_m_s"):
            vf = (cr or {}).get("speed_m_s")
            mk = (vf, f"{_f(vf)} m/s") if vf else None
            lay.addWidget(_h("Frequency against air speed"))
            ch = XYChart(140, "Flutter frequencies", "The frequency of each mode as the air speed rises")
            ch.set_data([{"name": r.get("label", f"Mode {i + 1}"), "x": sw["speed_m_s"], "y": r["frequency_Hz"]} for i, r in enumerate(sw["roots"])],
                        xunit="m/s", yunit="Hz", marker=mk)
            self.flutter_f = self._chart(lay, ch, "results.chart.flutter_frequency")
            lay.addWidget(_h("Damping against air speed"))
            ch = XYChart(120, "Flutter damping", "The damping ratio of each mode as the air speed rises: the motion grows where a line crosses zero")
            ch.set_data([{"name": r.get("label", f"Mode {i + 1}"), "x": sw["speed_m_s"], "y": r["damping_percent"]} for i, r in enumerate(sw["roots"])],
                        xunit="m/s", yunit="%", marker=mk, zero=True, ylim=(-30, 30), legend=False)
            self.flutter_d = self._chart(lay, ch, "results.chart.flutter_damping", "Each line follows one mode as the air speed rises. The motion grows "
                                         "where a damping line crosses zero (flutter); the modes approach each other in frequency just before.")
        lay.addWidget(_h("Shapes"))
        if fl:
            self._shape_link(lay, f"Flutter mode - {_f(fl['frequency_Hz'])} Hz at {_f(fl['speed_m_s'])} m/s", "Flutter mode", "results.shape.flutter",
                             "Show the flutter motion (at the phase of its largest movement)")
        if dv:
            self._shape_link(lay, f"Divergence shape - at {_f(dv['speed_m_s'])} m/s", "Divergence shape", "results.shape.divergence", "Show the divergence shape")
        for m in [m for m in (s.get("modes") or []) if not m.get("rigid_body")]:
            k = m.get("elastic_index", m.get("mode"))
            self._shape_link(lay, f"Mode {k} - {_f(m['frequency_Hz'])} Hz", f"Mode {k}", f"results.mode.{k}", f"Show the shape of mode {k}")
        self._animate_box(lay, "Animate")
        air, strips = s.get("air") or {}, s.get("strips") or {}
        if air or strips:
            lay.addWidget(_muted(f"Air {_f(air.get('density_kg_m3'), 3)} kg/m³ at {_f(air.get('altitude_m'))} m · {strips.get('count', 0)} strips · span "
                                 f"{_f((strips.get('span_m') or 0) * 1000)} mm · structure {_f((s.get('structure_mass_kg') or 0) * 1000)} g"))

    def _aero(self, lay, s):
        """The forces of the air on the body along the flow (drag), up (lift) and sideways, and its moments, as bars from zero."""
        co = s.get("coefficients") or {}
        rows = []
        if s.get("drag_N") is not None:
            rows.append(("Drag (along the flow)", s["drag_N"], f"{_f(s['drag_N'])} N" + (f" · CD {_f(co['CD'], 3)}" if co.get("CD") is not None else "")))
        if co:
            rows += [("Lift (across the flow, up)", co.get("lift_N", 0), f"{_f(co.get('lift_N', 0))} N · CL {_f(co.get('CL'), 3)}"),
                     ("Side force", co.get("side_force_N", 0), f"{_f(co.get('side_force_N', 0))} N · CS {_f(co.get('CS'), 3)}")]
        if rows:
            lay.addWidget(_h("Forces on the body"))
            top = max(abs(r[1]) for r in rows) or 1.0
            for i, (lab, v, txt) in enumerate(rows):
                lay.addWidget(wid(SignedBar(lab, txt, v / top), f"results.force.{i}"))
        m = s.get("aero_moment_Nm")
        if m:
            mrows = [("Pitch (nose-up +)", m.get("pitch", 0), co.get("Cm")), ("Roll", m.get("roll", 0), co.get("Cl")), ("Yaw", m.get("yaw", 0), co.get("Cn"))]
            lay.addWidget(_h("Moments"))
            top = max(abs(r[1]) for r in mrows) or 1.0
            for i, (lab, v, c) in enumerate(mrows):
                lay.addWidget(wid(SignedBar(lab, f"{_f(v)} N·m" + (f" · C {_f(c, 3)}" if c is not None else ""), v / top), f"results.moment.{i}"))
        if rows or m:
            lay.addWidget(_muted("Bars grow from the centre line: to the right is positive."))
        if co:
            lay.addWidget(_muted(f"Coefficients (reference area {_f(co.get('A_ref_m2', 0) * 1e4)} cm², {co.get('A_ref_source', '')}; reference length "
                                 f"{_f(co.get('L_ref_m', 0) * 1e3)} mm): CD {_f(co.get('CD'), 3)}, CL {_f(co.get('CL'), 3)}, CS {_f(co.get('CS'), 3)}."))
        for z in s.get("moving_zones") or []:
            pr = z.get("propeller")
            txt = (f"{z.get('name')}: {_f(z.get('rpm'))} rpm ({'sliding mesh' if z.get('method') == 'sliding' else 'MRF'}) · "
                   + (f"thrust {_f(pr['thrust_N'])} N, torque {_f(pr['torque_Nm'])} N·m, power {_f(pr['power_W'])} W" if pr else
                      f"torque {_f(z.get('torque_Nm'))} N·m, power {_f(abs(z.get('power_W') or 0))} W"))
            lay.addWidget(_muted(txt))
        if s.get("trim_note"):
            lay.addWidget(_note(s["trim_note"] + (f" Estimated trim for level flight: pitch {_f(s['suggested_pitch_deg'], 3)}°." if s.get("suggested_pitch_deg") is not None else "")))

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
        """Warnings, the mesh-sensitivity check (one level finer, or three levels with the grid-convergence index), the flow-model comparison of a
        cooling study, the previous run and the model assumptions: the web version's Checks."""
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
        lay.addWidget(_h("Mesh sensitivity"))
        lay.addWidget(_muted("Solves the study again on a finer mesh (in a copy) and compares the key results: how much they still change with the mesh."))
        mc = st.get("mesh_check")
        if mc and mc.get("rows"):
            lay.addWidget(wid(QLabel(f"<b>{mc.get('from')} → {mc.get('to')}" + (f" → {mc['to2']}" if mc.get("to2") else "") + "</b>"), "results.meshcheck.levels"))
            for i, r in enumerate(mc["rows"]):
                txt = f"{r['name']}: {_f(r.get('coarse'))} → {_f(r.get('fine'))} {r.get('unit', '')} ({_f(r.get('change_percent'), 2)} %"
                if r.get("resolved") is False:
                    txt += "; within the iteration error"
                txt += ")"
                if r.get("fine2") is not None:
                    txt += f" → {_f(r['fine2'])} {r.get('unit', '')}"
                    if r.get("convergence_type") == "oscillatory" and r.get("gci_percent") is not None:
                        txt += f" · oscillating: ±{_f(r['gci_percent'], 2)} % (half range)"
                    elif r.get("gci_percent") is not None:
                        txt += f" · GCI {_f(r['gci_percent'], 2)} %, order {_f(r.get('order'), 2)}"
                    elif r.get("convergence_type"):
                        txt += f" · {r['convergence_type']}"
                lab = wid(QLabel(txt), f"results.meshcheck.row.{i}")
                lab.setWordWrap(True)
                lay.addWidget(lab)
            verdict = ("Outdated: the setup or results changed after this check - run it again. " if mc.get("stale") else "") + str(mc.get("verdict") or "")
            if mc.get("cells_from"):
                verdict += f" ({mc['cells_from']:,} → {mc.get('cells_to', 0):,}" + (f" → {mc['cells_to2']:,}" if mc.get("cells_to2") else "") + " cells)"
            lay.addWidget(wid(_note(verdict, "warn" if (mc.get("max_change_percent") or 0) > 5 or mc.get("stale") else ""), "results.meshcheck.info"))
        elif mc:
            lay.addWidget(wid(_muted("Last check: " + str(mc.get("status") or mc.get("verdict") or "see the report")[:300]), "results.meshcheck.info"))
        else:
            lay.addWidget(_muted("Not run yet: re-mesh one preset finer and compare."))
        conv = s.get("convergence")
        settled = (not conv or conv.get("converged", True)) and st["solve"].get("status") != "stale"
        busy = self._busy()
        preset = (st.get("mesh") or {}).get("preset")
        why = ("A job is running for this study: wait for it to finish" if busy else
               "The results are out of date: run the simulation again first (step 4)" if st["solve"].get("status") == "stale" else
               "The run has not converged: continue it first, or the check would compare iteration error, not mesh error" if not settled else "")
        row = QHBoxLayout()
        run = wid(QPushButton("Check again (one level finer)" if mc else "Run mesh-sensitivity check"), "results.meshcheck.run")
        run.setObjectName("ghostBorder")
        if preset == "very_fine":
            why_run = "Already on the finest preset: set a smaller cell size by hand instead"
        else:
            why_run = why
        run.setEnabled(not why_run)
        run.setToolTip(why_run or "Solve the study again on a mesh one preset finer (in a copy) and compare the key results")
        run.clicked.connect(lambda: self.mesh_check(1))
        row.addWidget(run)
        three = wid(QPushButton("Three-level check (GCI)"), "results.meshcheck.three")
        three.setObjectName("ghostBorder")
        why3 = why or ("" if preset in ("quick", "coarse", "medium") else "Needs two finer presets: start from a quick, coarse or medium mesh")
        three.setEnabled(not why3)
        three.setToolTip(why3 or "Two finer meshes: the grid-convergence index of each key result (about 3 to 4 times the time of one check)")
        three.clicked.connect(lambda: self.mesh_check(2))
        row.addWidget(three)
        row.addStretch(1)
        lay.addLayout(row)
        if why:
            lay.addWidget(wid(_muted(why + "."), "results.meshcheck.why"))
        if st["study"] == "cht_enclosure":
            fc = st.get("model_check")
            used = s.get("flow_model") or ("kOmegaSST" if (s.get("flow_regime") or {}).get("suggested") == "kOmegaSST" else "laminar")
            other = "laminar" if used == "kOmegaSST" else "k-ω SST"
            lay.addWidget(_h("Flow model"))
            if fc and fc.get("rows"):
                lay.addWidget(QLabel(f"<b>{fc.get('from')} → {fc.get('to')}</b>"))
                for r in fc["rows"]:
                    lab = QLabel(f"{r['name']}: {_f(r.get('a'))} → {_f(r.get('b'))} {r.get('unit', '')} ({_f(r.get('change_percent'), 2)} %) · design value "
                                 f"{_f(r.get('conservative'))}")
                    lab.setWordWrap(True)
                    lay.addWidget(lab)
                lay.addWidget(_note(("Outdated: the setup or results changed after this comparison. " if fc.get("stale") else "") + str(fc.get("note") or ""),
                                    "warn" if fc.get("stale") else ""))
            else:
                lay.addWidget(_muted("Solve again with the other flow model on the same mesh, to bracket the answer."))
            b = wid(QPushButton(f"Compare with {other} (same mesh)"), "results.modelcheck.run")
            b.setObjectName("ghostBorder")
            b.setEnabled(not why)
            b.setToolTip(why or f"Solve the same mesh with the {other} flow model in a copy of the study and compare the key results")
            b.clicked.connect(self.model_check)
            lay.addWidget(b)
        prev = st.get("previous_result")
        if prev and st["solve"].get("status") == "done":
            cur = {"Max temperature": s.get("T_max"), "Hottest part temperature": (s.get("hottest_part") or {}).get("T_max"),
                   "Thermal resistance": s.get("thermal_resistance_K_per_W"), "Pressure drop": s.get("pressure_drop_Pa"), "Drag": s.get("drag_N"),
                   "Vertical aero force": s.get("vertical_aero_force_N"), "Suggested pitch": s.get("suggested_pitch_deg")}
            for m in [m for m in (s.get("modes") or []) if not m.get("rigid_body")]:
                cur[f"Mode {m.get('elastic_index', m.get('mode'))} frequency"] = m["frequency_Hz"]
            rows = [(k, v) for k, v in (prev.get("values") or {}).items() if v is not None]
            if rows:
                lay.addWidget(_h(f"Compared with the previous run ({prev.get('finished', '')})"))
                for k, v in rows:
                    lay.addWidget(wid(QLabel(f"{k}: {_f(v)} → {_f(cur.get(k))}"), "results.previous." + "".join(c if c.isalnum() else "_" for c in k.lower())))
        lay.addWidget(_h("Model assumptions"))
        for a in s.get("assumptions") or []:
            lab = QLabel("• " + a)
            lab.setWordWrap(True)
            lay.addWidget(lab)
        if not s.get("assumptions"):
            lay.addWidget(_muted("No assumptions recorded."))
        lay.addStretch(1)

    def mesh_check(self, levels: int = 1):
        """Start the mesh-sensitivity check (levels 1: one preset finer; 2: three levels with the grid-convergence index); a job."""
        try:
            job = self.engine.project().mesh_check(levels=levels)
        except Exception as e:  # noqa: BLE001
            self._fail(e)
            raise
        self.win.statusBar().showMessage("Three-level mesh check started (a job)" if levels == 2 else
                                         "Mesh-sensitivity check started: re-meshing one level finer and solving again (a job)", 8000)
        self._sig = None
        return job

    def model_check(self):
        """Start the flow-model comparison of a cooling study (same mesh, the other flow model); a job."""
        try:
            job = self.engine.project().model_check()
        except Exception as e:  # noqa: BLE001
            self._fail(e)
            raise
        self.win.statusBar().showMessage("Flow-model comparison started (same mesh, other model)", 8000)
        self._sig = None
        return job
