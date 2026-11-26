"""Issue #62: every positioning control of the Results step has a draggable handle in the 3D view, with the numbers following the handle and the handle
following the numbers (simwb/qt/handles.py, simwb/qt/resulthandles.py). Driven through the same press / drag / release path as the mouse."""
import numpy as np
import pytest
from flow_support import make_flow_study
from qt_support import ctl  # noqa: F401


def flow_ctl(ctl, **kw):
    kw.setdefault("n", 9)
    make_flow_study(ctl, **kw)
    ctl.do("results.tab", {"name": "flow"})
    ctl.win.view.set_view("iso")                      # (not straight along an axis: the arrows can be dragged along it)
    return ctl.win.panels["results"], ctl.win.view.handles


def count_calls(ctl, name="flow_layers"):
    calls = []
    real = getattr(ctl.engine, name)
    setattr(ctl.engine, name, lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    return calls


# ---------------------------------------------------------------- the contour planes
def test_a_contour_plane_has_a_handle_and_the_numbers_follow_it(ctl):
    p, hs = flow_ctl(ctl)
    f = p.flow
    assert "plane1" in hs.specs and hs.specs["plane1"]["kind"] == "plane" and hs.on
    lo, hi = f._span("y")
    layers = count_calls(ctl)
    start = float(hs.value("plane1"))
    seen = []
    f.changed.connect(lambda: seen.append("redraw"))
    target = lo + 0.8 * (hi - lo)
    got = hs.simulate_move("plane1", target)
    assert got == pytest.approx(target, abs=(hi - lo) * 0.02)
    # the slider, the mm box and the label follow the handle ...
    assert f.planes[0]["frac"] == pytest.approx((got - lo) / (hi - lo), abs=1e-3)
    assert f._plane_rows[0]["mm"].value() == pytest.approx(got * 1000, abs=0.1)
    assert f._plane_rows[0]["pos"].value() == pytest.approx(round((got - lo) / (hi - lo) * 1000), abs=1)
    assert f._plane_rows[0]["pos_label"].text() == f"y = {got * 1000:.4g} mm"
    # ... and the expensive part (cutting the flow again) ran once, when the handle was let go
    assert seen == ["redraw"] and len(layers) == 1
    assert ctl.state()["view"]["overlay"]["planes"][0]["pos_m"] == pytest.approx(got, abs=1e-6)
    assert float(hs.value("plane1")) != pytest.approx(start)


def test_typing_a_position_moves_the_handle(ctl):
    p, hs = flow_ctl(ctl)
    f = p.flow
    lo, hi = f._span("y")
    f._plane_rows[0]["mm"].setValue((lo + 0.25 * (hi - lo)) * 1000)
    assert hs.value("plane1") == pytest.approx(lo + 0.25 * (hi - lo), abs=1e-4)
    f._plane_rows[0]["pos"].setValue(900)                                     # the slider too
    assert hs.value("plane1") == pytest.approx(lo + 0.9 * (hi - lo), abs=1e-4)


def test_dragging_stays_cheap_while_the_button_is_down(ctl):
    p, hs = flow_ctl(ctl)
    layers = count_calls(ctl)
    lo, hi = p.flow._span("y")
    start = hs.grab_px("plane1")
    assert hs.press(*start)
    for k in range(1, 7):
        hs.drag_to(*(start + np.array([0.0, 6.0 * k])))
    assert not layers                                                         # (nothing was cut again yet: the sheet moved, the numbers followed)
    assert p.flow._plane_rows[0]["mm"].value() != pytest.approx(0.5 * (lo + hi) * 1000, abs=0.5)        # (the number moved with the handle)
    hs.release(*(start + np.array([0.0, 36.0])))
    assert len(layers) == 1


def test_a_second_and_third_plane_each_get_their_own_handle(ctl):
    p, hs = flow_ctl(ctl)
    ctl.do("results.flow", {"planes": [{"axis": "y"}, {"axis": "x", "position": 0.3}, {"axis": "z", "position": 0.7}]})
    assert [k for k in hs.specs if k.startswith("plane")] == ["plane1", "plane2", "plane3"]
    assert [hs.specs[k]["axis"] for k in ("plane1", "plane2", "plane3")] == [1, 0, 2]
    lo, hi = p.flow._span("x")
    hs.simulate_move("plane2", lo + 0.6 * (hi - lo))
    assert p.flow.planes[1]["frac"] == pytest.approx(0.6, abs=0.03) and p.flow.planes[0]["frac"] == 0.5 and p.flow.planes[2]["frac"] == 0.7
    p.flow.remove_plane(0)
    assert [k for k in hs.specs if k.startswith("plane")] == ["plane1", "plane2"]


def test_a_plane_seen_straight_along_its_axis_is_dragged_up_and_down_the_screen(ctl):
    """The result opens looking along the plane's axis: the handle must not be dead then."""
    p, hs = flow_ctl(ctl)
    ctl.win.view.set_view("+y")                                               # (straight along the plane's normal)
    assert hs._straight_on(hs._normal(hs.specs["plane1"]))
    lo, hi = p.flow._span("y")
    got = hs.simulate_move("plane1", lo + 0.2 * (hi - lo))
    assert got == pytest.approx(lo + 0.2 * (hi - lo), abs=(hi - lo) * 0.02)
    assert p.flow.planes[0]["frac"] == pytest.approx(0.2, abs=0.03)


def test_shift_snaps_the_drag_to_round_steps_and_the_handle_stops_at_the_region(ctl):
    p, hs = flow_ctl(ctl)
    lo, hi = p.flow._span("y")
    snap = hs.specs["plane1"]["snap_m"]
    got = hs.simulate_move("plane1", lo + 0.31 * (hi - lo), shift=True)
    assert got / snap == pytest.approx(round(got / snap), abs=1e-6)
    got = hs.simulate_move("plane1", hi + 10 * (hi - lo))                      # (dragged far beyond the region)
    assert got == pytest.approx(hi, abs=1e-6) and p.flow.planes[0]["frac"] == 1.0


# ---------------------------------------------------------------- the arrows' plane
def test_the_arrows_plane_has_a_handle_and_a_number(ctl):
    p, hs = flow_ctl(ctl)
    f = p.flow
    assert "vectors" not in hs.specs                                           # (only while the arrows are on)
    ctl.do("results.flow", {"vectors": True, "vectors_axis": "z", "vectors_pos": 0.25})
    lo, hi = f._span("z")
    assert hs.specs["vectors"]["axis"] == 2 and hs.value("vectors") == pytest.approx(lo + 0.25 * (hi - lo), abs=1e-3)
    assert f.vec_mm.value() == pytest.approx((lo + 0.25 * (hi - lo)) * 1000, abs=0.2)
    got = hs.simulate_move("vectors", lo + 0.75 * (hi - lo))
    assert f.vec_pos.value() == pytest.approx(750, abs=25) and f.vec_mm.value() == pytest.approx(got * 1000, abs=0.2)
    f.vec_mm.setValue((lo + 0.1 * (hi - lo)) * 1000)                           # typing moves the handle
    assert hs.value("vectors") == pytest.approx(lo + 0.1 * (hi - lo), abs=1e-3)


# ---------------------------------------------------------------- the seeds of the particle paths
def test_the_start_of_the_particle_paths_can_be_a_disk_with_a_handle(ctl):
    from PySide6.QtTest import QTest
    p, hs = flow_ctl(ctl)
    f = p.flow
    ctl.do("results.flow", {"planes": [], "streams": True, "streams_n": 60, "streams_steps": 40})
    assert "seeds" not in hs.specs and not f.seed_box.isVisibleTo(f)
    ctl.do("results.flow", {"streams_seed": "disk"})
    QTest.qWait(50)                                                            # (the disk is put just upstream of the object, once the drawing is done)
    assert f.seed_disk() is not None and f.seed_box.isVisibleTo(f) and "seeds" in hs.specs
    c0 = np.array(f.seed_disk()["center"])
    assert hs.specs["seeds"]["disc"]["axis"] == 0                              # (the flow is along x: the disk is across it)
    # the disk's centre follows the handle: drag it along y (an arrow of the point handle)
    target = c0 + np.array([0.0, 0.2, 0.0])
    layers = count_calls(ctl)
    got = np.array(hs.simulate_move("seeds", target, part="axis1"))
    assert got == pytest.approx(target, abs=0.03)
    assert [b.value() for b in f.seed_c] == pytest.approx(list(got * 1000), abs=0.2)           # the three numbers follow
    assert len(layers) == 1
    # ... and the paths really start on the disk
    paths = ctl.engine.flow_layers(f.request(), -1)["paths"]
    P = np.frombuffer(__import__("base64").b64decode(paths["paths"]), np.float32).reshape(paths["n"], paths["steps"], 3)
    first = P[:, 0, :]
    assert np.abs(first[:, 0] - got[0]).max() < 0.05 and np.abs(first[:, 1] - got[1]).max() <= f.seed_disk()["radius"] + 0.05
    # typing moves the handle
    f.seed_c[2].setValue(100.0)
    assert hs.value("seeds")[2] == pytest.approx(0.1, abs=1e-6)


# ---------------------------------------------------------------- pinned points and the ends of the line plot
def probe_ctl(ctl):
    p, hs = flow_ctl(ctl, n=11)
    ctl.do("results.flow", {"planes": []})                                     # (the object alone, coloured by the field: something to pin on)
    ctl.do("results.tab", {"name": "probe"})
    ctl.win.view.set_view("iso")
    return p, hs


def test_a_pinned_point_has_a_handle_that_follows_the_surface_and_numbers_that_follow_it(ctl):
    p, hs = probe_ctl(ctl)
    v = ctl.win.view
    top = v.pick_ray([0.5, 0.0, 2.0], [0.5, 0.0, -2.0])
    assert top and top["kind"] == "surface"
    pin = p.pin_at([0.5, 0.0, 0.0])
    assert "pin1" in hs.specs and hs.specs["pin1"]["resolver"] is not None
    assert [float(e.text()) for e in p.pin_rows[0]["edits"]] == pytest.approx([c * 1000 for c in pin["point"]], abs=0.01)
    # drag it to another place on the model
    goal = v.pick_ray([0.9, 0.1, 2.0], [0.9, 0.1, -2.0])["point"]
    got = hs.simulate_move("pin1", goal)
    assert np.linalg.norm(np.array(got) - np.array(goal)) < 0.12
    assert p.pins[0]["point"] == pytest.approx(got, abs=1e-9)                                    # the pin is where the handle is
    assert [float(e.text()) for e in p.pin_rows[0]["edits"]] == pytest.approx([c * 1000 for c in got], abs=0.01)
    assert "Velocity" in p.pin_rows[0]["label"].text()


def test_typing_a_point_moves_the_pin_to_the_nearest_place_on_the_model_and_the_handle_follows(ctl):
    p, hs = probe_ctl(ctl)
    p.pin_at([0.5, 0.0, 0.0])
    for e, val in zip(p.pin_rows[0]["edits"], (800.0, 0.0, 400.0)):                            # x, y, z in mm; z is above the model
        e.setText(str(val))
    p.pin_rows[0]["edits"][2].editingFinished.emit()
    pt = p.pins[0]["point"]
    assert pt[0] == pytest.approx(0.8, abs=0.02) and abs(pt[2] - 0.3) < 0.05                    # (on the top of the block: z = 0.3)
    assert hs.value("pin1") == pytest.approx(pt, abs=1e-9)


def test_the_ends_of_the_line_plot_have_handles_and_the_plot_follows(ctl):
    p, hs = probe_ctl(ctl)
    v = ctl.win.view
    a, b = v.pick_ray([0.2, 0.0, 2.0], [0.2, 0.0, -2.0])["point"], v.pick_ray([0.8, 0.0, 2.0], [0.8, 0.0, -2.0])["point"]
    ctl.do("results.line", {"p0": a, "p1": b})
    assert "lineA" in hs.specs and "lineB" in hs.specs and p.line_ends_box.isVisibleTo(p)
    length0 = p.line_data["length"]
    goal = v.pick_ray([0.95, 0.0, 2.0], [0.95, 0.0, -2.0])["point"]
    hs.simulate_move("lineB", goal)
    assert p.line_data["length"] > length0                                                      # the plot was made again for the new line
    assert [float(e.text()) for e in p.line_edits[1]] == pytest.approx([c * 1000 for c in p.line_ends[1]], abs=0.01)
    p.line_edits[0][0].setText("100.0")                                                         # typing end A moves it (and its handle)
    p.line_edits[0][0].editingFinished.emit()
    assert p.line_ends[0][0] == pytest.approx(0.1, abs=0.03) and hs.value("lineA") == pytest.approx(p.line_ends[0], abs=1e-6)


def test_the_handles_of_a_tab_show_while_the_tab_is_shown(ctl):
    p, hs = flow_ctl(ctl)
    assert hs.on and "plane1" in hs.specs
    ctl.do("results.tab", {"name": "display"})
    assert not hs.on and not hs.specs                                                          # (the planes' handles belong to the Flow tab)
    ctl.do("results.tab", {"name": "flow"})
    assert "plane1" in hs.specs
    ctl.do("step.go", {"step": "setup"})
    assert not hs.on


# ---------------------------------------------------------------- the section of a part
from test_qt_results import solved  # noqa: E402,F401


def test_the_section_plane_of_a_part_has_a_handle_and_a_number(ctl, solved):
    ctl.do("study.import", {"path": solved["static"]})
    ctl.do("step.go", {"step": "results"})
    ctl.do("results.tab", {"name": "display"})
    p, hs = ctl.win.panels["results"], ctl.win.view.handles
    assert "section" not in hs.specs and not p.section_mm.isEnabled()                         # (no section yet)
    ctl.do("results.show", {"section": "x", "section_pos": 0.5})
    ctl.win.view.set_view("iso")
    ext = p.section_extent()
    assert "section" in hs.specs and p.section_mm.isEnabled()
    assert p.section_mm.value() == pytest.approx((ext[0] + 0.5 * (ext[1] - ext[0])) * 1000, abs=0.1)
    cuts = []
    real = ctl.engine.result_surface
    ctl.engine.result_surface = lambda *a, **k: (cuts.append(1), real(*a, **k))[1]
    target = ext[0] + 0.8 * (ext[1] - ext[0])
    got = hs.simulate_move("section", target)
    assert got == pytest.approx(target, abs=(ext[1] - ext[0]) * 0.03)
    assert p.section_pos.value() == pytest.approx((got - ext[0]) / (ext[1] - ext[0]) * 1000, abs=1.5)
    assert p.section_mm.value() == pytest.approx(got * 1000, abs=0.2)
    assert len(cuts) == 1 and ctl.state()["view"]["overlay"]["triangles"] > 0                    # (the part is cut once, when the handle is let go)
    p.section_mm.setValue((ext[0] + 0.2 * (ext[1] - ext[0])) * 1000)                            # typing moves the handle
    assert hs.value("section") == pytest.approx(ext[0] + 0.2 * (ext[1] - ext[0]), abs=1e-3)
    ctl.do("results.show", {"section": "off"})
    assert "section" not in hs.specs


def test_every_positioning_control_of_the_results_step_has_a_handle(ctl):
    """The list of positioning controls of the results panel (see resulthandles.py) and the handle each one has."""
    p, hs = flow_ctl(ctl)
    ctl.do("results.flow", {"planes": [{"axis": "y"}], "vectors": True, "streams": True, "streams_seed": "disk", "streams_n": 50})
    from PySide6.QtTest import QTest
    QTest.qWait(50)
    assert {"plane1", "vectors", "seeds"} <= set(hs.specs)
    ctl.do("results.flow", {"planes": [], "vectors": False, "streams": False})
    ctl.do("results.tab", {"name": "probe"})
    p.pin_at([0.5, 0.0, 0.0])
    ctl.do("results.line", {"p0": [0.2, 0.0, 0.3], "p1": [0.8, 0.0, 0.3]})
    assert {"pin1", "lineA", "lineB"} <= set(hs.specs)


def test_the_control_api_lists_the_handles_and_drags_them_by_name(ctl):
    p, hs = flow_ctl(ctl)
    st = ctl.state()["view"]["handles"]
    assert st["on"] and st["handles"]["plane1"]["kind"] == "plane" and st["handles"]["plane1"]["axis"] == "y"
    lo, hi = p.flow._span("y")
    out = ctl.do("view.handle", {"key": "plane1", "value": (lo + 0.7 * (hi - lo)) * 1000})["result"]["handle"]
    assert out["value"] == pytest.approx(lo + 0.7 * (hi - lo), abs=(hi - lo) * 0.02)
    assert p.flow.planes[0]["frac"] == pytest.approx(0.7, abs=0.03)
    with pytest.raises(ValueError, match="no handle 'plane9'"):
        ctl.do("view.handle", {"key": "plane9", "value": 1})
    with pytest.raises(ValueError, match="one number"):
        ctl.do("view.handle", {"key": "plane1", "value": [1, 2, 3]})
