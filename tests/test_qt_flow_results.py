"""Flow and cooling results: the object and a plane through the air are drawn (not the outside of the air box), and the Summary reads in
the terms of the study (drag, temperatures, units, verdicts)."""
import base64

import numpy as np
from qt_support import ctl  # noqa: F401
from test_qt_results import solved  # noqa: F401

from simwb.results import ResultSet, boundary_from_tets, merge_payloads


def _box_results():
    """A 4 x 4 x 4 grid of tets (a unit cube of 'air'); the faces in the lowest x slab are the 'body' group, the others 'farfield'."""
    from scipy.spatial import Delaunay
    g = np.linspace(0, 1, 5)
    nodes = np.array(np.meshgrid(g, g, g, indexing="ij")).reshape(3, -1).T
    tets = Delaunay(nodes).simplices
    tris, cells = boundary_from_tets(tets)
    body = nodes[tris][:, :, 0].max(1) < 0.3
    fields = {"Temperature": ("node", nodes[:, 0] * 10.0 + 20.0, "°C")}
    return ResultSet(nodes, tets, np.zeros(len(tets)), ["air"], fields, tris, cells, np.where(body, 1, 0), ["farfield", "body"])


def _vals(p):
    return np.frombuffer(base64.b64decode(p["values"]), np.float32)


def test_a_half_of_the_object_is_kept_below_the_plane():
    rs = _box_results()
    whole = rs.surface("Temperature", groups=["farfield"])
    half = rs.surface("Temperature", groups=["farfield"], below=(1, 0.5))
    assert 0 < half["n_tris"] < whole["n_tris"]
    pos = np.frombuffer(base64.b64decode(half["positions"]), np.float32).reshape(-1, 3)
    assert pos[:, 1].max() <= 0.5 + 0.3                                                # (whole triangles: they may reach a little past the plane)


def test_a_cut_plane_and_the_object_become_one_picture():
    rs = _box_results()
    plane = rs.slice("Temperature", 1, 0.5)
    obj = rs.surface("Temperature", groups=["body"], below=(1, 0.5))
    both = merge_payloads(plane, obj)
    assert both["n_tris"] == plane["n_tris"] + obj["n_tris"]
    assert len(_vals(both)) == len(_vals(plane)) + len(_vals(obj))
    idx = np.frombuffer(base64.b64decode(both["indices"]), np.uint32)
    assert idx.max() < len(_vals(both)) and both["min"] <= both["max"] and both["unit"] == "°C"


def test_a_picture_without_values_does_not_blank_the_other():
    """A wall-only field (y+) cut through the air has no value on the plane: only the walls are drawn."""
    rs = _box_results()
    obj = rs.surface("Temperature", groups=["body"])
    empty = dict(obj, values=base64.b64encode(np.full(len(_vals(obj)), np.nan, np.float32).tobytes()).decode())
    both = merge_payloads(empty, obj)
    assert both["n_tris"] == obj["n_tris"] and np.isfinite(_vals(both)).all()


def _tiles(ctl, st_study, summary):
    from simwb.qt.panels.common import KPI
    p = ctl.win.panels["results"]
    p._summary({"study": st_study}, summary, True)
    return [k.property("api_text") for k in p.pages["summary"].parentWidget().findChildren(KPI)]


def test_the_aerodynamics_summary_reads_in_drag_lift_and_checks(ctl):
    s = {"time": 516.0, "speed_m_s": 10.0, "drag_N": 0.0132, "CdA_m2": 0.00022, "coefficients": {"CD": 0.69, "lift_N": -0.00026, "A_ref_source": "projected frontal area"},
         "yPlus": {"mean": 8.0, "max": 52.0}, "convergence": {"converged": True, "outputs_stable": True, "iterations": 516}}
    t = " | ".join(_tiles(ctl, "external_aero", s))
    assert "Drag force: 0.0132 N" in t and "Drag area CdA: 2.2 cm²" in t and "Lift force:" in t
    assert "Wall y+ (mean): 8 (warn)" in t and "30 to 300" in t                       # (a verdict, not a bare number)
    assert "Solution: Converged (ok)" in t and "Time" not in t and "Speed M S" not in t


def test_the_cooling_summary_has_units_and_a_heat_balance(ctl):
    s = {"time": 1577.0, "hottest_part": {"label": "Chip", "T_max": 37.87}, "thermal_resistance_K_per_W": 1.287, "outlet_T_mixed": 26.76, "inlet_T": 25.0,
         "pressure_drop_Pa": 2.77, "flow_m3_per_h": 17.3, "heat_input_W": 10.0, "heat_carried_by_air_W": 9.95, "energy_imbalance_percent": 0.53,
         "convergence": {"converged": False, "iterations": 2000}}
    t = " | ".join(_tiles(ctl, "cht_enclosure", s))
    assert "Hottest part (Chip): 37.9 °C" in t and "Thermal resistance: 1.29 K/W" in t and "Air leaves at: 26.8 °C" in t
    assert "Pressure drop: 2.77 Pa" in t and "Heat taken by the air: 9.95 W (ok)" in t and "Solution: Not converged (warn)" in t


def test_a_label_can_be_moved_and_keeps_its_place(ctl):
    """The Max and Min labels are dragged with the mouse (move_label is what the drag calls): the place is kept when the labels are drawn
    again, until another result is shown or the labels are reset."""
    v = ctl.win.view
    items = [{"point": [0, 0, 0], "text": "Max 5 Pa", "key": "max"}, {"point": [0, 0, 0], "text": "Min 1 Pa", "key": "min", "below": True}]
    v.set_labels(items)
    assert len(v._label_state) == 2
    before = v._label_state[0]["actor"].GetDisplayOffset()
    v.move_label(0, 120, 80)
    assert tuple(v._label_state[0]["actor"].GetDisplayOffset()) == (120, 80) and tuple(before) != (120, 80)
    items[0]["text"] = "Max 5.1 Pa"                                                        # (drawn again with another value: same label)
    v.set_labels(items)
    assert tuple(v._label_state[0]["actor"].GetDisplayOffset()) == (120, 80)
    assert tuple(v._label_state[1]["actor"].GetDisplayOffset())[0] == 0                   # (the other one stays where it was put)
    v.reset_labels()
    assert tuple(v._label_state[0]["actor"].GetDisplayOffset()) != (120, 80)
    v.set_labels([])


# ---------------------------------------------------------------- the flow tools (vortex surfaces, cloud, arrows, particle paths)
def _grid_payloads():
    """Payloads like the engine's, from a small synthetic flow: arrows on a plane, particle paths, a volume."""
    from simwb.qt.engine import resample as _r  # noqa: F401  (the helpers the engine calls)
    from simwb.resample import _b64 as b64
    pts = np.array([[0, 0, 0], [0.1, 0, 0], [0, 0.1, 0]], np.float32)
    vec = np.array([[1, 0, 0], [2, 0, 0], [3, 0, 0]], np.float32)
    arrows = {"n": 3, "h": 0.1, "positions": b64(pts), "vectors": b64(vec), "min": 1.0, "max": 3.0, "p95": 2.9, "unit": "m/s"}
    P = np.full((2, 5, 3), np.nan, np.float32)
    P[0, :4] = [[0, 0, 0], [0.1, 0, 0], [0.2, 0, 0], [0.3, 0, 0]]
    P[1, :2] = [[0, 0.1, 0], [0.1, 0.1, 0]]
    S = np.full((2, 5), np.nan, np.float32)
    S[0, :4], S[1, :2] = 5.0, 7.0
    paths = {"n": 2, "steps": 5, "paths": b64(P), "speed": b64(S), "min": 5.0, "max": 7.0, "unit": "m/s"}
    data = np.zeros((4, 5, 6), np.uint8)
    data[1:3, 1:4, 1:5] = np.arange(1, 25, dtype=np.uint8).reshape(2, 3, 4) * 10
    vol = {"dims": [6, 5, 4], "origin": [0, 0, 0], "h": 0.1, "log": False, "lo": 0.0, "hi": 10.0, "unit": "m/s", "data": b64(data),
           "box": [0, 0, 0, 0.5, 0.4, 0.3]}
    return arrows, paths, vol


def test_the_flow_drawings_go_with_the_overlay(ctl):
    from simwb.qt import flowdraw
    v = ctl.win.view
    arrows, paths, vol = _grid_payloads()
    tab = flowdraw.table("turbo", False, 0)
    n0 = len(v.overlay_actors)
    assert flowdraw.add_arrows(v, arrows, tab, 1.0, 3.0, 0.02) is not None
    assert flowdraw.add_paths(v, paths, tab, 5.0, 7.0) is not None            # (a path of one point is not drawn: both here have two or more)
    assert flowdraw.add_volume(v, vol, tab, (2.0, 9.0), 5.0, "map") is not None
    assert flowdraw.add_volume(v, vol, tab, (2.0, 9.0), 5.0, "smoke") is not None
    assert len(v.overlay_actors) == n0 + 4
    v.clear_overlay()
    assert not v.overlay_actors


def test_the_cloud_shows_the_strongest_decades_of_a_log_field_and_the_slipstream():
    from simwb.qt.flowdraw import volume_window
    assert volume_window({"lo": 1e-6, "hi": 1.0, "log": True}, "Q-criterion")[0] == 1e-3          # (the top three decades)
    assert volume_window({"lo": 0.0, "hi": 30.0, "log": False}, "Velocity", free_stream=10.0) == (11.5, 30.0)   # (faster than the free stream)
    assert volume_window({"lo": 0.0, "hi": 30.0, "log": False}, "Pressure") == (0.0, 30.0)


def test_the_flow_tab_only_exists_for_flow_studies_and_deformation_only_for_results_that_deform(ctl, solved):
    ctl.do("study.import", {"path": solved["static"]})
    ctl.do("step.go", {"step": "results"})
    p = ctl.win.panels["results"]
    assert not p.tab_btns["flow"].isVisibleTo(p) and not p.deform_box.isHidden()                   # a bracket deforms
    ctl.do("study.import", {"path": solved["heat"]})
    ctl.do("step.go", {"step": "results"})
    ctl._settle()
    assert p.deform_box.isHidden()                                                                # a temperature field does not
    import pytest
    with pytest.raises(Exception, match="flow and cooling"):
        ctl.do("results.flow", {"iso": True})


def test_the_flow_tools_follow_the_fields_of_the_result(ctl):
    from simwb.qt.panels.flowtools import FlowTools
    f = FlowTools()
    f.set_fields([{"name": "Velocity", "max": 12.0}, {"name": "Pressure", "max": 5.0}], {"gap_mm": 0.0, "free_stream": 10.0})
    assert not f.iso_box.isVisibleTo(f)                                                            # no Q-criterion: no vortex surfaces
    assert f.request()["volume"]["field"] == "Velocity" and not f.any_on()
    f.set_fields([{"name": "Velocity", "max": 12.0}, {"name": "Q-criterion", "max": 1000.0}], {"gap_mm": 1.2, "free_stream": 10.0})
    assert f.iso_box.isVisibleTo(f) and f.request()["iso"]["gap"] == 1.2 and f.request()["volume"]["field"] == "Q-criterion"
    assert "Q = " in f.iso_note.text()
    f.iso_on.setChecked(True)
    assert f.any_on() and f.request()["iso"]["on"]


def test_contour_lines_are_part_of_the_display(ctl, solved):
    """Lines of equal value: on or off in the Display tab, a number of evenly spaced ones and extra values to add."""
    ctl.do("study.import", {"path": solved["static"]})
    ctl.do("step.go", {"step": "results"})
    ctl.do("results.tab", {"name": "display"})
    ov = lambda: ctl.state()["view"]["overlay"]  # noqa: E731
    assert ov()["contours"] == 0 and ctl.widget_do("results.contours")["value"] == {"checked": False}
    assert ctl.widget_do("results.contours.n")["enabled"] is False                         # (waits for the switch)
    ctl.do("results.show", {"contours": True, "contour_lines": 6})
    assert ov()["contours"] == 6 and ctl.widget_do("results.contours.n")["enabled"] is True
    lo, hi = ov()["range"]
    ctl.do("results.show", {"contour_levels": [(lo + hi) / 2 * 1.013, hi * 10]})           # one inside the range, one outside it
    assert ov()["contours"] == 7
    assert 1 <= ov()["contour_labels"] <= 7                                                 # (a value on each line that is drawn)
    ctl.do("results.show", {"contour_labels": False})
    assert ov()["contour_labels"] == 0 and ov()["contours"] == 7
    ctl.do("results.show", {"contours": False})
    assert ov()["contours"] == 0
