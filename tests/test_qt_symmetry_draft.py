"""Placing a symmetry plane: choose which way it cuts and the plane is drawn at once in the 3D view (a sheet, an arrow into the kept side and a
handle); it is moved by dragging the arrow or typing its position, flipped, and only Apply rebuilds the geometry."""
import numpy as np
import pytest
from qt_support import ctl, from_thread  # noqa: F401


def wait(c, t=300):
    assert from_thread(c.app, lambda: c.wait_idle(t), t + 20)["idle"]
    c._settle()


@pytest.fixture
def bracket(ctl):
    ctl.do("study.example", {"study": "static_structural"})
    wait(ctl)
    ctl.do("step.go", {"step": "setup"})
    return ctl


def draft(c):
    return c.win.panels["setup"].sym.draft


def test_choosing_a_direction_draws_the_plane_with_no_further_question(bracket):
    c = bracket
    from PySide6.QtWidgets import QComboBox
    assert not [w for w in c.win.panels["setup"].findChildren(QComboBox) if w.property("wid") == "setup.sym.draft.axis"]      # no "Normal to"
    c.widget_do("setup.sym.add_x", "click")
    h = c.win.view.plane_handle
    assert h.on and np.allclose(h.normal, [1, 0, 0])
    bb = c.engine.state()["geometry"]["info"]["parts_bbox"]
    assert h.point[0] == pytest.approx((bb[0] + bb[3]) / 2)                          # (through the middle of the parts)
    assert not c.engine.state().get("symmetry")                                      # (nothing is rebuilt until Apply)


def test_typing_a_position_moves_the_plane_and_flip_turns_the_arrow(bracket):
    c = bracket
    c.widget_do("setup.sym.add_y", "click")
    c.widget_do("setup.sym.draft.pos", "set", "12.5")
    assert c.win.view.plane_handle.point[1] == pytest.approx(0.0125)
    c.widget_do("setup.sym.draft.flip", "click")
    assert np.allclose(c.win.view.plane_handle.normal, [0, -1, 0])
    assert c.widget_do("setup.sym.draft.keeps")["value"].endswith("−y")
    assert c.win.view.plane_handle.point[1] == pytest.approx(0.0125)                 # (flipping does not move it)


def test_dragging_the_arrow_moves_the_plane_and_the_field_follows(bracket):
    c = bracket
    c.widget_do("setup.sym.add_x", "click")
    h, g = c.win.view.plane_handle, c.win.view.gizmo
    a, b = np.array(h._px(h._middle())), np.array(h._px(h._tip()))
    start = (a + b) / 2
    along = (b - a) / np.linalg.norm(b - a)
    x0 = h.point[0]
    assert h.press(*start) and h.drag is not None                                    # (the arrow is taken)
    h.drag_to(*(start + along * 40))
    assert h.point[0] != pytest.approx(x0) and draft(c)["point"][0] == pytest.approx(h.point[0])
    h.release(*(start + along * 40))
    assert float(c.widget_do("setup.sym.draft.pos")["value"]) == pytest.approx(h.point[0] * 1000, abs=0.01)


def test_a_press_away_from_the_arrow_does_not_take_it(bracket):
    c = bracket
    c.widget_do("setup.sym.add_x", "click")
    h = c.win.view.plane_handle
    far = np.array(h._px(h._middle())) + np.array([400.0, 400.0])
    assert not h.press(*far) and h.drag is None


def test_apply_rebuilds_the_geometry_with_the_plane_that_was_placed(bracket):
    c = bracket
    vol = c.engine.project().bodies()[0]["volume"]
    c.widget_do("setup.sym.add_y", "click")
    c.widget_do("setup.sym.draft.flip", "click")
    c.widget_do("setup.sym.draft.set", "click")
    wait(c)
    pl = c.engine.state()["symmetry"][0]
    assert np.allclose(pl["normal"], [0, -1, 0]) and draft(c) is None and not c.win.view.plane_handle.on
    assert c.engine.project().bodies()[0]["volume"] < 0.75 * vol


def test_cancel_takes_the_plane_away(bracket):
    c = bracket
    c.widget_do("setup.sym.add_z", "click")
    assert c.win.view.plane_handle.on
    c.widget_do("setup.sym.draft.cancel2", "click")
    assert draft(c) is None and not c.win.view.plane_handle.on


def test_a_plane_through_a_face_is_an_offset_from_that_face(bracket):
    c = bracket
    flat = [f for f in c.engine.project().faces() if "Plane" in str(f.get("surface_type", ""))]
    f = max(flat, key=lambda f: f["area"])
    c.widget_do("setup.sym.from_face", "set", True)
    c.do("view.pick_face", {"id": f["id"]})
    d = draft(c)
    assert d["face"] == f["id"] and c.win.view.plane_handle.on
    assert c.win.panels["setup"].sym.pos_mm() == pytest.approx(0.0)                  # (the offset starts at the face)
    c.widget_do("setup.sym.draft.pos", "set", "3")
    moved = (np.asarray(draft(c)["point"]) - np.asarray(f["centroid"])) @ np.asarray(d["n0"])
    assert moved == pytest.approx(0.003, abs=1e-9)
    on = [w["id"] for w in c.widgets("setup.sym.")["widgets"] if w["id"] in ("setup.sym.from_face", "setup.sym.add_x", "setup.sym.add_y",
                                                                           "setup.sym.add_z") and w.get("value") in (True, "true")]
    assert on in ([], ["setup.sym.from_face"])                                       # (only the face button is shown as chosen)


def test_leaving_the_step_hides_the_preview_and_coming_back_shows_it(bracket):
    c = bracket
    c.widget_do("setup.sym.add_x", "click")
    c.do("step.go", {"step": "mesh"})
    assert not c.win.view.plane_handle.on
    c.do("step.go", {"step": "setup"})
    assert c.win.view.plane_handle.on and draft(c) is not None
