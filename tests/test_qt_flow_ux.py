"""Beta 5 user-experience fixes of the Results step, on a synthetic flow result (tests/flow_support.py) - no solver is run."""
from flow_support import make_flow_study
from qt_support import ctl  # noqa: F401


def _ov(ctl):
    return ctl.state()["view"]["overlay"]


def _whole_object_triangles(ctl):
    return ctl.engine.flow_object("Velocity", -1)["n_tris"]


# ---------------------------------------------------------------- #60: a contour plane does not clip the model
def test_adding_a_contour_plane_leaves_the_whole_model_visible(ctl):
    """Fixes #60. The model was cut open at the plane as a side effect of a single contour plane; now it is whole (and see-through, so the
    plane behind its near half still shows). Cutting is the explicit 'section view' choice."""
    make_flow_study(ctl)
    whole = _whole_object_triangles(ctl)
    ctl.do("results.tab", {"name": "flow"})
    ov = _ov(ctl)
    assert ov["flow_tools"] == ["planes"] and ov["planes"][0]["axis"] == "y"          # (the result opens with one plane through the middle)
    assert ov["triangles"] == whole                                                    # the model is not clipped
    assert ov["opacity"] < 1.0                                                         # (see-through by default)
    for axis, pos in (("x", 0.3), ("z", 0.7)):                                         # another contour, on any axis: still whole
        ctl.do("results.flow", {"planes": [{"axis": axis, "position": pos}]})
        assert _ov(ctl)["triangles"] == whole and _ov(ctl)["planes"][0]["axis"] == axis
    ctl.do("results.flow", {"planes": [{"axis": "y"}, {"axis": "x", "position": 0.8}, {"axis": "z"}]})
    assert _ov(ctl)["triangles"] == whole and len(_ov(ctl)["planes"]) == 3


def test_a_solid_model_with_planes_is_whole_and_opaque(ctl):
    make_flow_study(ctl)
    whole = _whole_object_triangles(ctl)
    ctl.do("results.tab", {"name": "flow"})
    ctl.do("results.flow", {"plane_model": "solid"})
    assert _ov(ctl)["triangles"] == whole and _ov(ctl)["opacity"] == 1.0


def test_the_section_view_is_an_explicit_choice(ctl):
    """The cut still exists, but only when asked for in the Flow tab ('Cut open at plane 1'), and it goes away again."""
    make_flow_study(ctl)
    whole = _whole_object_triangles(ctl)
    ctl.do("results.tab", {"name": "flow"})
    ctl.do("results.flow", {"plane_model": "cut"})
    cut = _ov(ctl)["triangles"]
    assert 0 < cut < whole
    ctl.do("results.flow", {"plane_model": "ghost"})
    assert _ov(ctl)["triangles"] == whole


def test_the_model_choice_is_a_labelled_control_that_only_shows_with_a_plane(ctl):
    make_flow_study(ctl)
    ctl.do("results.tab", {"name": "flow"})
    f = ctl.win.panels["results"].flow
    assert f.plane_model_row.isVisibleTo(f)
    assert [f.plane_model.itemText(i) for i in range(3)] == ["Whole model, see-through", "Whole model, solid", "Cut open at plane 1 (section view)"]
    ctl.do("results.flow", {"planes": []})
    assert not f.plane_model_row.isVisibleTo(f)


# ---------------------------------------------------------------- #58: recording says what it records and survives a minimised window
def _record_setup(ctl, tmp_path, n=6):
    pr, rs = make_flow_study(ctl, moving=n)
    ctl.do("results.tab", {"name": "display"})
    p = ctl.win.panels["results"]
    assert p.timebar.transient and len(p.timebar.times) == n
    return p, ctl.win, tmp_path / "rec.mp4"


def test_recording_says_the_current_window_is_recorded_and_not_to_minimise_it(ctl, tmp_path):
    """Fixes #58: a toast at the start, a bar with a Stop button and '● Recording' in the window title while it runs; all gone afterwards."""
    p, win, out = _record_setup(ctl, tmp_path)
    title0, seen = win.windowTitle(), []
    p.timebar.timeChanged.connect(lambda i: seen.append((win.view.banner.text(), win.view.banner.button.text(), win.windowTitle(),
                                                          ctl.state()["shell"]["recording"], [t["text"] for t in win.toasts.listing()])))
    v = ctl.do("results.video", {"path": str(out), "fps": 4})["result"]["video"]
    assert v["frames"] == 6 and not v["stopped"] and out.exists()
    bar, button, title, rec, toasts = seen[2]
    assert "Recording the 3D view of this window" in bar and "do not minimise it" in bar and "picture 3 of 6" in bar
    assert button == "Stop recording" and title.startswith("● Recording") and rec["active"] and not rec["paused"]
    assert any("Recording the 3D view of this window" in t and "do not minimise" in t for t in toasts)
    assert win.view.banner.text() == "" and win.windowTitle() == title0                    # (nothing is left on the screen)
    assert not ctl.state()["shell"]["recording"]["active"]


def test_minimising_the_window_pauses_the_recording_with_a_warning_and_it_goes_on_when_restored(ctl, tmp_path):
    from PySide6.QtCore import QTimer
    p, win, out = _record_setup(ctl, tmp_path)
    log = []

    def on_time(i):
        if i == 2:
            win.showMinimized()
            QTimer.singleShot(400, win.showNormal)
    p.timebar.timeChanged.connect(on_time)

    def sample(i):                                                                       # (the gate is asked before every picture)
        log.append((i, win.isMinimized(), ctl.state()["shell"]["recording"]["paused"]))
    p.timebar.timeChanged.connect(sample)
    v = ctl.do("results.video", {"path": str(out), "fps": 4})["result"]["video"]
    rec = p.recording.state()
    assert rec["pauses"] == 1 and v["frames"] == 6 and not v["stopped"]                  # paused once, then went on to the end: nothing lost
    assert all(not minimised for _, minimised, _p in log if _ > 2)                       # (no picture was made while it was minimised)
    texts = [t["text"] for t in win.toasts.history]
    assert any("minimised, so the recording is paused" in t for t in texts) and "Recording goes on." in texts


def test_stop_recording_keeps_the_pictures_made_so_far(ctl, tmp_path):
    p, win, out = _record_setup(ctl, tmp_path)
    p.timebar.timeChanged.connect(lambda i: win.view.banner.button.click() if i == 3 else None)       # (the user presses Stop recording)
    v = ctl.do("results.video", {"path": str(out), "fps": 4})["result"]["video"]
    assert v["stopped"] and v["frames"] == 4 and out.exists()
    assert any("Recording stopped" in t["text"] for t in win.toasts.history) and win.view.banner.text() == ""


def test_stopping_before_two_pictures_makes_no_video_and_says_so(ctl, tmp_path):
    import pytest

    from simwb.qt.recording import RecordingStopped
    p, win, out = _record_setup(ctl, tmp_path)
    p.timebar.timeChanged.connect(lambda i: win.view.banner.button.click() if i == 0 else None)
    with pytest.raises(RecordingStopped):
        p.record_video(str(out), fps=4)
    assert not out.exists() and win.view.banner.text() == "" and not p.recording.active and p.timebar.rec.isEnabled()


def test_a_second_recording_cannot_start_while_one_runs(ctl, tmp_path):
    import pytest
    p, win, out = _record_setup(ctl, tmp_path)
    errs = []

    def again(i):
        if i == 1:
            try:
                p.record_video(str(tmp_path / "b.mp4"), fps=4)
            except ValueError as e:
                errs.append(str(e))
    p.timebar.timeChanged.connect(again)
    p.record_video(str(out), fps=4)
    assert errs and "already running" in errs[0]


# ---------------------------------------------------------------- #59: moving mesh - show and hide each geometry
def _moving(ctl, **kw):
    pr, rs = make_flow_study(ctl, moving=5, prop=True, **kw)
    ctl.do("results.tab", {"name": "display"})
    return ctl.win.panels["results"], rs


def _drawn_points(ctl):
    """The vertices of the surface the view draws now (the first overlay actor)."""
    import numpy as np
    from vtkmodules.util.numpy_support import vtk_to_numpy
    a = ctl.win.view.overlay_actors[0]
    return vtk_to_numpy(a.GetMapper().GetInput().GetPoints().GetData()) if a.GetMapper().GetInput().GetNumberOfPoints() else np.zeros((0, 3))


def test_the_list_names_every_geometry_and_the_moving_one(ctl):
    """Fixes #59: a tick box per surface of a moving-mesh result (the object, the propeller of the sliding zone), the air box, and the outlines."""
    p, rs = _moving(ctl)
    assert p.vis.isVisibleTo(p)
    texts = [c.text() for c in p.vis.checks.values()]
    assert texts == ["Object", "zone1 (moving)", "Air box (outer boundary)", "Moving zones (outline)"], texts
    assert p.vis.moving_keys == ["g:zone1_walls"] and p.vis.moving_btn.isEnabled()
    assert [c.isChecked() for c in p.vis.checks.values()] == [True, True, False, False]            # (defaults: every part drawn, the air box and outlines off)
    assert ctl.state()["shell"]["visibility"]["moving"] == ["zone1 (moving)"]


def test_hiding_a_geometry_leaves_only_the_others_drawn_and_it_follows_the_time_bar(ctl):
    import numpy as np
    p, rs = _moving(ctl)
    tb = p.timebar
    both = ctl.state()["view"]["overlay"]["triangles"]
    ctl.do("results.visibility", {"show": {"Object": False}})                                       # hide the airframe: the propeller stays
    only_prop = ctl.state()["view"]["overlay"]["triangles"]
    assert 0 < only_prop < both
    tb.set_index(0)
    pts0 = _drawn_points(ctl)
    assert pts0[:, 0].min() > 1.1                                                                   # (all that is drawn is the propeller block, x from 1.14)
    tb.set_index(4)                                                                                 # the time bar: the mesh moves, the choice stays
    assert ctl.state()["view"]["overlay"]["triangles"] == only_prop
    pts4 = _drawn_points(ctl)
    assert pts4[:, 0].min() > 1.1 and abs(pts4[:, 1].mean() - pts0[:, 1].mean()) > 0.1             # (it moved: the propeller is seen moving, alone)
    ctl.do("results.visibility", {"show": {"Object": True, "zone1 (moving)": False}})
    assert ctl.state()["view"]["overlay"]["triangles"] == both - only_prop
    ctl.do("results.visibility", {"all": True})
    assert ctl.state()["view"]["overlay"]["triangles"] == both


def test_only_what_moves_is_one_click_and_shows_the_zone_outline(ctl):
    p, rs = _moving(ctl)
    both = ctl.state()["view"]["overlay"]["triangles"]
    p.vis.moving_btn.click()
    st = p.vis.state()
    assert st["items"] == {"Object": False, "zone1 (moving)": True, "Air box (outer boundary)": False, "Moving zones (outline)": True}
    assert 0 < ctl.state()["view"]["overlay"]["triangles"] < both
    assert ctl.win.view.region_view.actors                                                          # (the zone outline is drawn)
    p.vis.all_btn.click()
    assert ctl.state()["view"]["overlay"]["triangles"] == both and p.vis.selection()["groups"] is None


def test_hiding_everything_draws_nothing_and_does_not_fail(ctl):
    p, rs = _moving(ctl)
    ctl.do("results.visibility", {"show": {"Object": False, "zone1 (moving)": False}})
    assert ctl.state()["view"]["overlay"].get("triangles", 0) == 0
    ctl.do("results.tab", {"name": "flow"})                                                         # (with a contour plane on: the plane is still there)
    assert ctl.state()["view"]["overlay"]["flow_tools"] == ["planes"]


def test_the_choice_is_kept_per_study_and_works_with_the_flow_tools_on(ctl):
    p, rs = _moving(ctl)
    first = ctl.engine.pid
    ctl.do("results.visibility", {"show": {"Object": False}})
    ctl.do("results.tab", {"name": "flow"})
    pl = ctl.state()["view"]["overlay"]
    assert pl["flow_tools"] == ["planes"] and pl["triangles"] < _whole(ctl)                         # (the planes and the hidden object together)
    make_flow_study(ctl, name="Other flow", moving=5, prop=True)                                    # another study: its own, default list
    assert [c.isChecked() for c in p.vis.checks.values()][:2] == [True, True]
    ctl.do("study.open", {"id": first})
    ctl.do("step.go", {"step": "results"})
    assert p.vis.checks["g:body"].isChecked() is False and p.vis.checks["g:zone1_walls"].isChecked() is True      # (back as it was left)


def _whole(ctl):
    return ctl.engine.flow_object("Velocity", -1, groups=None)["n_tris"]


def test_the_air_box_tick_and_the_view_button_are_one_switch_on_the_results_step(ctl):
    p, rs = _moving(ctl)
    assert p.vis.checks["air"].isChecked() is False and not ctl.win.chk_air.isChecked()
    p.vis.checks["air"].setChecked(True)
    assert ctl.win.chk_air.isChecked() and ctl.win.view.air_over_results
    ctl.win.chk_air.setChecked(False)
    assert not p.vis.checks["air"].isChecked()


def test_a_result_without_moving_parts_says_so_in_the_button_tooltip(ctl):
    pr, rs = make_flow_study(ctl, moving=0, prop=False)
    ctl.do("results.tab", {"name": "display"})
    p = ctl.win.panels["results"]
    assert not p.vis.moving_btn.isEnabled() and "no moving part" in p.vis.moving_btn.toolTip()
    import pytest
    with pytest.raises(ValueError, match="no moving part"):
        p.vis.apply(only_moving=True)
