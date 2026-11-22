"""The Results step of the Qt app against the web version's (docs/QT_PARITY.md): the charts of the summary, the time bar and the videos, the
distribution with drag-to-set, opacity, hidden parts, the undeformed outline, the temperature limit, the checks and the flow tools' own colour
ranges and moving particles. Each is driven through the control API or the widget the user presses."""
import importlib
from pathlib import Path

import numpy as np
import pytest
from qt_support import EXAMPLES, ctl  # noqa: F401
from test_qt_results import _wait, open_results, solved  # noqa: F401


@pytest.fixture(scope="session")
def transient(tmp_path_factory):
    """A heated plate solved as a transient (a few saved times), saved as one file."""
    home = tmp_path_factory.mktemp("transient_ws")
    mp = pytest.MonkeyPatch()
    mp.setenv("SIMWB_HOME", str(home / "ws"))
    import simwb.project as P

    importlib.reload(P)
    pr = P.create_project("Plate warm-up", "heat_conduction")
    _wait(pr.import_step(EXAMPLES / "heated_plate.step"))
    plate, block = sorted(pr.bodies(), key=lambda b: -b["volume"])
    pr.set_body(plate["id"], material="aluminium_6061", name="Plate")
    pr.set_body(block["id"], material="ceramic_alumina", heat_W=10, name="Resistor")
    pr.add_bc("Cold plate", "fixed_temperature", [pr.find_faces(normal="-z", at="min_z")[0]["id"]], {"T": 30})
    pr.set_settings(analysis="transient", end_time=60.0, n_frames=5, T_initial=25.0)
    pr.set_mesh(preset="coarse")
    _wait(pr.generate_mesh())
    _wait(pr.solve())
    out = str(P.export_project(pr.id, str(home / "transient.zip")))
    mp.undo()
    return out


def _panel(c):
    return c.win.panels["results"]


# ------------------------------------------------------------------------------------------------------------ Summary charts
def test_the_modal_spectrum_picks_a_mode_and_animate_follows(ctl, solved):
    c = open_results(ctl, solved, "modal")
    p = _panel(c)
    sp = p.spectrum
    assert sp is not None and c.widget_do("results.chart.spectrum")["id"] == "results.chart.spectrum" and len(sp.modes) >= 4
    sp.resize(400, 130)
    x = sp.stem_x(2)
    assert sp.mode_at(x) == 2
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QEvent
    ev = QMouseEvent(QEvent.MouseButtonPress, QPointF(x, 60), QPointF(x, 60), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    sp.mousePressEvent(ev)                                                               # a click on the stem of mode 2
    assert c.widget_do("results.field")["value"]["text"] == "Mode 2" and p.spectrum.selected == 2
    assert "mass" in c.widget_do("results.mode.1")["value"]                           # (each row gives the effective mass)
    c.widget_do("results.animate", "set", False)
    assert c.widget_do("results.deform_mode")["value"]["text"] == "End state"
    c.widget_do("results.animate", "set", True)
    assert c.state()["view"]["overlay"]["animated"] is True


def test_flutter_sweep_residual_and_history_charts(ctl, solved):
    c = open_results(ctl, solved, "static")
    p = _panel(c)
    fl = {"first_frequency_Hz": 20.0, "speed_range_m_s": [0, 100], "modes": [{"mode": 1, "elastic_index": 1, "frequency_Hz": 20.0}],
          "critical": {"type": "flutter", "speed_m_s": 61.0, "mach": 0.18}, "flutter": {"frequency_Hz": 25.0, "speed_m_s": 61.0, "mode_content": []},
          "margin": {"ratio": 1.1, "required": 1.15, "design_speed_m_s": 55.0},
          "sweep": {"speed_m_s": [0, 50, 100], "roots": [{"label": "Mode 1", "frequency_Hz": [20, 22, 25], "damping_percent": [2, 1, -1]},
                                                         {"label": "Mode 2", "frequency_Hz": [40, 33, 25], "damping_percent": [3, 2, 1]}]}}
    p._summary({"study": "aeroelastic_flutter", "solve": {"status": "done"}}, fl, True)
    assert len(p.flutter_f.series) == 2 and p.flutter_f.marker[0] == 61.0 and p.flutter_d.zero
    assert c.widget_do("results.shape.flutter")["visible"] is not None
    kp = {w["id"]: w["text"] for w in c.widgets("results.kpi.")["widgets"]}
    assert "Flutter speed" in kp["results.kpi.flutter_speed"] and "(bad)" in kp["results.kpi.margin_on_design_speed"]
    sine = {"first_frequency_Hz": 100.0, "modes": [{"mode": 1, "elastic_index": 1, "frequency_Hz": 100.0, "effective_mass_fraction": {"x": 0.6, "y": 0, "z": 0}}],
            "sine": {"accel_g": 1, "axis": "x", "damping_percent": 2, "frequency_Hz": [10, 100, 1000], "max_stress_MPa": [0.1, 20, 0.5],
                     "range_Hz": [10, 1000], "peaks": [{"mode": 1, "frequency_Hz": 100, "max_stress_MPa": 20, "max_displacement_mm": 0.1}],
                     "worst": {"frequency_Hz": 100, "max_stress_MPa": 20, "max_displacement_mm": 0.1, "safety_factor_yield": 3.0}}}
    p._summary({"study": "vibration_modal", "solve": {"status": "done"}}, sine, True)
    assert p.sweep_chart.logx and p.sweep_chart.logy and p.sweep_chart.dots == [(100, 20)]
    aero = {"drag_N": 1.0, "coefficients": {"CD": 0.5, "lift_N": -0.4, "side_force_N": 0.1, "CL": -0.2, "CS": 0.05},
            "aero_moment_Nm": {"pitch": 0.1, "roll": 0, "yaw": -0.05},
            "convergence": {"converged": False, "iterations": 300, "history": {"iters": [1, 2, 3], "series": {"Ux": [1, 0.1, 0.01], "p": [1, 0.5, 0.2]}}},
            "history": {"t": [0, 1, 2], "series": {"Drag": [1, 1.1, 1.0], "Drag mean": [1, 1.05, 1.03]}}}
    p._summary({"study": "external_aero", "solve": {"status": "done"}, "mesh": {"preset": "coarse"}}, aero, True)
    assert p.residuals.logy and len(p.residuals.series) == 2 and len(p.history_chart.series) == 2
    assert c.widget_do("results.force.1")["visible"] is not None and c.widget_do("results.moment.0")["visible"] is not None
    assert c.widget_do("results.coarse_note")["id"] == "results.coarse_note"            # (a coarse flow mesh: indicative only)


def test_the_static_summary_shows_the_force_at_each_support(ctl, solved):
    c = open_results(ctl, solved, "static")
    ids = {w["id"] for w in c.widgets("results.support.")["widgets"]}
    assert "results.support.0" in ids
    c.widget_do("results.animate", "set", True)
    assert c.state()["view"]["overlay"]["animated"] is True


# ------------------------------------------------------------------------------------------------------------ Display
def test_histogram_drag_sets_the_range_and_double_click_goes_back(ctl, solved):
    c = open_results(ctl, solved, "static")
    p = _panel(c)
    c.do("results.tab", {"name": "display"})
    h = p.hist_data
    assert h and abs(sum(h["bins"]) + h["below"] + h["above"] - 1) < 1e-6 and h["lo"] <= h["mean"] <= h["hi"]
    assert "mean" in str(c.widget_do("results.range.hist_info")["value"])
    p.hist.resize(300, 76)
    lo, hi = h["lo"], h["hi"]
    p.hist.drag(2 + 0.25 * 296, 2 + 0.5 * 296)                                           # the second quarter of the range
    rng = c.state()["view"]["overlay"]["range"]
    assert c.widget_do("results.range.auto")["value"] is False or c.widget_do("results.range.auto")["value"] == {"checked": False}
    assert rng[0] == pytest.approx(lo + 0.25 * (hi - lo), rel=1e-3) and rng[1] == pytest.approx(lo + 0.5 * (hi - lo), rel=1e-3)
    assert p.hist_data["above"] > 0                                                      # (part of the surface is above the range now)
    p.hist.autoRequested.emit()                                                          # a double-click
    assert c.state()["view"]["overlay"]["range"][1] == pytest.approx(hi, rel=1e-6)


def test_opacity_outline_and_the_deformation_note(ctl, solved):
    c = open_results(ctl, solved, "static")
    ov = lambda: c.state()["view"]["overlay"]  # noqa: E731
    assert ov()["outline"] is True and ov()["outline_segments"] > 10                   # the undeformed part, outlined (on by default)
    assert "times the true deformation" in str(c.widget_do("results.deform_note")["value"])
    c.do("results.show", {"outline": False, "opacity": 0.4})
    assert ov()["outline"] is False and ov()["opacity"] == pytest.approx(0.4)
    c.widget_do("results.opacity", "set", 100)
    assert ov()["opacity"] == 1.0


def test_parts_can_be_hidden(ctl, solved):
    c = open_results(ctl, solved, "heat")
    p = _panel(c)
    assert len(p.part_checks) == 2
    n = c.state()["view"]["overlay"]["triangles"]
    name = list(p.part_checks)[0]
    r = c.do("results.part", {"name": name, "visible": False})["result"]
    assert r["hidden"] == [name] and 0 < c.state()["view"]["overlay"]["triangles"] < n
    c.widget_do("results.part.0", "set", True)
    assert c.state()["view"]["overlay"]["triangles"] == n
    with pytest.raises(ValueError):
        c.do("results.part", {"name": "nope"})


def test_the_temperature_limit_marks_every_part(ctl, solved):
    c = open_results(ctl, solved, "heat")
    from simwb.qt.charts import HBar
    sm = c.engine.state()["solve"]["summary"]
    lim = (sm["T_max"] + sm["T_min"]) / 2
    c.do("results.temperature_limit", {"value": lim})
    bars = _panel(c).findChildren(HBar)
    assert bars and all(b.limit is not None for b in bars) and any(b.tone == "bad" for b in bars)
    assert any("over" in b.value_text for b in bars)
    kp = {w["id"]: w["text"] for w in c.widgets("results.kpi.")["widgets"]}
    assert "over the" in kp["results.kpi.max_temperature"]
    assert c.do("results.temperature_limit", {})["result"]["limit"] is None


# ------------------------------------------------------------------------------------------------------------ time bar and videos
def test_the_time_bar_steps_and_plays_the_saved_times(ctl, transient, tmp_path):
    c = open_results(ctl, {"t": transient}, "t")
    p = _panel(c)
    tb = p.timebar
    assert tb.transient and len(tb.times) >= 5 and c.widget_do("results.time.slider")["enabled"]
    c.do("results.tab", {"name": "display"})
    assert c.widget_do("results.time.slider")["visible"] is True
    last = c.state()["view"]["overlay"]["range"]
    c.do("results.show", {"time": 0})
    assert tb.index == 0 and "Time" in str(c.widget_do("results.time.label")["value"])
    first_vals = np.array(c.win.view._ov["val"])
    c.do("results.show", {"time": len(tb.times) - 1})
    assert not np.allclose(first_vals.mean(), np.array(c.win.view._ov["val"]).mean())   # (the plate warms up)
    assert last is not None
    r = c.do("results.play", {"on": True})["result"]
    assert r["playing"] is True and tb.index == 0
    tb._step()
    assert tb.index == 1
    c.do("results.play", {"on": False})
    out = tmp_path / "warmup.mp4"
    v = c.do("results.video", {"path": str(out), "fps": 4})["result"]["video"]
    assert out.exists() and out.stat().st_size > 1000 and v["frames"] == len(tb.times)
    assert tb.index == 1                                                                 # (back to the time shown before)
    with pytest.raises(ValueError, match="results.video"):
        c.widget_do("results.time.record", "click")                                     # a file dialog: the action takes the path


def test_a_steady_result_records_a_turntable(ctl, solved, tmp_path):
    c = open_results(ctl, solved, "static")
    tb = _panel(c).timebar
    assert not tb.transient and "Turntable" in tb.rec.text()
    cam = c.win.view.camera()
    out = tmp_path / "turn.mp4"
    v = c.do("results.video", {"path": str(out), "fps": 12, "orbit": 360})["result"]["video"]
    assert out.exists() and v["frames"] == 48 and v["orbit_deg"] == 360
    assert c.win.view.camera()["position"] == pytest.approx(cam["position"])            # the camera is put back


# ------------------------------------------------------------------------------------------------------------ Checks
def test_the_checks_show_the_mesh_check_and_both_buttons(ctl, solved):
    c = open_results(ctl, solved, "static")
    p = _panel(c)
    st = dict(c.engine.state())
    st["mesh_check"] = {"from": "coarse", "to": "medium", "to2": "fine", "rows": [
        {"name": "Max von Mises stress", "coarse": 40.0, "fine": 42.0, "fine2": 42.5, "unit": "MPa", "change_percent": 5.0, "gci_percent": 1.2, "order": 2.1}],
        "verdict": "The stress still changes with the mesh.", "max_change_percent": 5.0, "cells_from": 1000, "cells_to": 3000, "cells_to2": 9000}
    p._checks(st, st["solve"]["summary"])
    row = str(c.widget_do("results.meshcheck.row.0")["value"])
    assert "40 → 42 MPa" in row and "GCI 1.2 %" in row
    assert c.widget_do("results.meshcheck.run")["enabled"] is True and c.widget_do("results.meshcheck.three")["enabled"] is True
    st["solve"] = dict(st["solve"], status="stale")
    p._checks(st, st["solve"]["summary"])
    assert c.widget_do("results.meshcheck.three")["enabled"] is False
    assert "out of date" in str(c.widget_do("results.meshcheck.why")["value"]).lower()   # (it says why it cannot be used)
    assert "out of date" in [w for w in p.findChildren(type(p.tab_btns["summary"])) if w.property("wid") == "results.meshcheck.three"][0].toolTip().lower()


# ------------------------------------------------------------------------------------------------------------ flow tools
def test_each_flow_tool_has_its_own_colour_range(ctl):
    from simwb.qt.panels.flowtools import FlowTools
    f = FlowTools()
    f.set_fields([{"name": "Velocity", "min": 0, "max": 10}, {"name": "Pressure", "min": -5, "max": 5}], {"gap_mm": 1.0})
    assert f.request()["volume"]["range"] is None
    f.set_scale("volume", 1, 4)
    f.set_scale("streams", 0.5, 8)
    r = f.request()
    assert r["volume"]["range"] == (1.0, 4.0) and r["streams"]["range"] == (0.5, 8.0) and r["vectors"]["range"] is None
    f.set_planes([{"field": "Pressure", "axis": "x", "frac": 0.3, "range": [-1, 1]}])
    assert f.request()["planes"][0]["range"] == (-1.0, 1.0)
    f.set_scale(1)                                                                       # plane 1 automatic again
    assert f.request()["planes"][0]["range"] is None
    f.show_range("vectors", 0.0, 9.5)
    assert f.vec_scale["lo"].text() == "0" and f.vec_scale["hi"].text() == "9.5"     # (the automatic range is shown in its boxes)
    with pytest.raises(ValueError):
        f.set_scale("volume", 3, 1)


def test_particles_move_along_their_paths(ctl):
    import base64

    from simwb.qt import flowdraw
    n, steps = 6, 20
    P = np.zeros((n, steps, 3), np.float32)
    P[:, :, 0] = np.linspace(0, 1, steps)[None, :]
    P[:, :, 1] = np.arange(n)[:, None]
    P[0, 10:] = np.nan                                                                   # (one particle left the box half way)
    S = np.ones((n, steps), np.float32)
    pay = {"n": n, "steps": steps, "paths": base64.b64encode(P.tobytes()).decode(), "speed": base64.b64encode(S.tobytes()).decode()}
    v = ctl.win.view
    d = flowdraw.ParticleDots(v, pay, flowdraw.table("turbo", False, 0), 0, 2, speed=1.0)
    a, _ = d.positions()
    d.step(0.2)                                                                          # 0.2 s: about 5 path steps on
    b, _ = d.positions()
    assert len(a) == n and np.isfinite(b).all() and (np.abs(b[:, 0] - a[:, 0]) > 0).any()
    assert d.timer.isActive()
    v.clear_overlay()
    d.step(0.04)
    assert not d.timer.isActive()                                                        # (stops when it is no longer drawn)
