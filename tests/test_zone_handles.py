"""Rotating zones are moved, resized and turned in the 3D view with the shared handles (issue #10), and the numbers of the zone's card and the handles follow each
other. Like a refinement region, a zone that is dragged is only previewed until Apply (a draft): the drawing, the numbers in the card and a line in the corner of
the view follow the drag; Cancel and Escape put it back."""
import numpy as np
import pytest
from qt_support import ctl, from_thread  # noqa: F401

from simwb import zones as Z

ZONE = {"name": "z", "center": [0.0, 0.0, 0.0], "axis": "+z", "radius": 0.1, "length": 0.04, "rpm": 1000.0, "method": "mrf", "cell_size": None}


# ---------------------------------------------------------------- the geometry (no window)
def test_a_zone_grows_at_its_flat_faces_and_in_radius_and_the_opposite_face_stays():
    g = np.zeros((3, 2))
    g[0, 1] = 0.02                                                       # (the +z face 20 mm out)
    z = Z.grown(ZONE, g)
    assert z["length"] == pytest.approx(0.06) and z["center"] == pytest.approx([0, 0, 0.01])        # (the -z face is still at -0.02)
    g = np.zeros((3, 2))
    g[1, 0] = -0.03
    assert Z.grown(ZONE, g)["radius"] == pytest.approx(0.07)
    g[1, 0] = -5.0                                                        # (never smaller than the least a zone can be: 1 mm without a model, 2 % of it with one)
    assert Z.grown(ZONE, g)["radius"] == pytest.approx(0.001)
    assert Z.grown(ZONE, g, size=0.5)["radius"] == pytest.approx(0.01)
    assert Z.grown(dict(ZONE, cell_size=0.005), g, size=0.5)["radius"] == pytest.approx(0.02)        # (4 cells of the zone's own cell size)
    g = np.zeros((3, 2))
    g[0, 0] = -5.0
    assert Z.grown(ZONE, g)["length"] == pytest.approx(0.001)
    assert Z.grown(ZONE, g, size=0.5)["length"] == pytest.approx(0.005)


def test_the_least_a_zone_can_be_comes_from_its_cell_size_or_the_size_of_the_model():
    assert Z.min_sizes(ZONE, 0.0)[:2] == pytest.approx((0.001, 0.001))
    r, ln, why = Z.min_sizes(ZONE, 1.0)
    assert (r, ln) == pytest.approx((0.02, 0.01)) and "2 % of the model" in why
    r, ln, why = Z.min_sizes(dict(ZONE, cell_size=0.01), 1.0)
    assert (r, ln) == pytest.approx((0.04, 0.02)) and "4 cells" in why
    assert Z.min_sizes(ZONE, 1.0, hard=True)[0] == pytest.approx(0.01)       # (the engine refuses only half of it)
    assert Z.model_size({"parts_bbox": [0, 0, 0, 0.1, 0.02, 0.02]}) == pytest.approx(0.1) and Z.model_size(None) == 0.0


def test_the_handles_of_a_zone_are_one_on_each_flat_face_and_one_on_its_side():
    hs = {h["key"]: h for h in Z.face_handles(dict(ZONE, axis="+x"))}
    assert set(hs) == {("length", 0), ("length", 1), ("radius", 0)}
    assert hs[("length", 1)]["pos"] == pytest.approx([0.02, 0, 0]) and hs[("length", 1)]["dir"] == pytest.approx([1, 0, 0])
    assert hs[("length", 0)]["dir"] == pytest.approx([-1, 0, 0])
    r = hs[("radius", 0)]
    assert np.linalg.norm(np.asarray(r["pos"])) == pytest.approx(0.1) and abs(np.dot(r["dir"], [1, 0, 0])) < 1e-12


def test_a_quarter_turn_takes_the_axis_to_the_next_axis_and_anything_else_is_rounded():
    assert Z.tilted_axis(ZONE, 0, 90.0) == "-y"                           # (right-handed about x: z goes to -y)
    assert Z.tilted_axis(ZONE, 0, -90.0) == "+y"
    assert Z.tilted_axis(ZONE, 1, 90.0) == "+x"
    assert Z.tilted_axis(ZONE, 1, 180.0) == "-z"
    assert Z.tilted_axis(ZONE, 0, 20.0) == "+z"                           # (20 degrees is not a quarter turn: still z)
    assert Z.tilted_axis(dict(ZONE, axis="-x"), 2, 90.0) == "-y"


# ---------------------------------------------------------------- in the window
def wait(c, t=300):
    assert from_thread(c.app, lambda: c.wait_idle(t), t + 20)["idle"]
    c._settle()


@pytest.fixture
def aero(ctl, box_step):
    ctl.do("study.new", {"name": "Flow", "study": "external_aero"})
    ctl.do("geometry.import", {"path": str(box_step)})
    wait(ctl)
    ctl.do("step.go", {"step": "setup"})
    ctl.widget_do("setup.zone.add", "click")
    ctl.win.view.iren.setFixedSize(900, 700)             # (without OpenGL the 3D view is 100 x 30 pixels: too small to tell handles apart)
    ctl.win.view.set_view("iso")
    return ctl


def zone(c):
    return c.engine.state()["moving_zones"][0]


def rot(c):
    return c.win.panels["setup"].rot


def drag(c, key, value, **kw):
    """A whole press-drag-release of a handle; then the panel (rebuilt on release) is let to show itself so that its buttons can be pressed."""
    out = c.win.view.handles.simulate_move(key, value, **kw)
    c._settle()
    return out


def test_a_new_zone_shows_its_handles_and_the_box_hides_them(aero):
    c = aero
    hs = c.win.view.handles
    kinds = {k: s["kind"] for k, s in hs.specs.items()}
    assert kinds == {"move": "point", "tilt.x": "ring", "tilt.y": "ring", "face.length.0": "face", "face.length.1": "face", "face.radius": "face"}      # (no ring about its own axis)
    assert c.widget_do("setup.zone.0.handles")["value"]["checked"] is True
    text = c.win.shell.hint()[1]
    assert text.startswith("Drag a ring to turn the axis") and "drag an arrow to move" in text and "Esc cancels a drag" in text       # (the wording of the Geometry hint)
    c.widget_do("setup.zone.0.handles", "set", False)
    assert not hs.on
    c.widget_do("setup.zone.0.handles", "set", True)
    assert hs.on
    c.do("step.go", {"step": "mesh"})                                      # (the zone is only drawn in the Mesh step: it is edited in Setup)
    assert not hs.on and c.win.view.zone_view.actors
    c.do("step.go", {"step": "setup"})
    assert hs.on


def test_dragging_the_move_arrow_previews_and_stores_nothing_until_apply(aero):
    c = aero
    hs = c.win.view.handles
    z0 = zone(c)
    p0 = np.array(hs.specs["move"]["point"])
    start = hs.grab_px("move", "axis0")
    assert hs.press(*start, force=("move", "axis0"))
    a, b = hs._arrows(hs.specs["move"])[0][1:]
    end = hs._px((a + b) / 2 + [0.02, 0, 0])
    hs.drag_to(*end)
    assert zone(c) == z0                                                   # (nothing stored while dragging)
    assert float(c.widget_do("setup.zone.0.cx")["value"]) == pytest.approx(z0["center"][0] * 1000 + 20, abs=0.01)     # (the number follows the handle)
    assert c.win.view.zone_view.actors and np.allclose(hs.specs["move"]["point"], p0 + [0.02, 0, 0], atol=1e-6)
    assert "Moving" in c.win.view.gizmo.text and "20.0" in c.win.view.gizmo.text           # (what the drag does, in the corner of the view)
    hs.release(*end)
    assert c.win.view.gizmo.text is None
    assert zone(c) == z0 and rot(c).draft is not None                       # (let go: still not stored, but waiting for Apply or Cancel)
    assert c.widget_do("setup.zone.0.pending")["value"].startswith("Moved with its handles and not stored yet")
    bar = c.win.view.banner
    assert bar.button.text() == "Cancel" and bar.button2.text() == "Apply" and bar.button2.isVisibleTo(bar)        # (both where the 3D view is, next to each other)
    assert float(c.widget_do("setup.zone.0.cx")["value"]) == pytest.approx(z0["center"][0] * 1000 + 20, abs=0.01)
    c.widget_do("setup.zone.0.apply", "click")
    z1 = zone(c)
    assert z1["center"][0] == pytest.approx(z0["center"][0] + 0.02, abs=1e-6) and z1["center"][1:] == pytest.approx(z0["center"][1:])
    assert rot(c).draft is None and np.allclose(hs.specs["move"]["point"], z1["center"])
    assert not bar.button2.isVisibleTo(bar)


def test_cancel_and_the_bars_buttons_put_the_zone_back(aero):
    c = aero
    hs = c.win.view.handles
    z0 = zone(c)
    drag(c, "face.radius", 0.01)
    assert rot(c).draft and zone(c) == z0
    c.widget_do("setup.zone.0.cancel", "click")
    assert rot(c).draft is None and zone(c) == z0
    assert float(c.widget_do("setup.zone.0.radius")["value"]) == pytest.approx(z0["radius"] * 1000, abs=0.01)
    assert hs.specs["face.radius"]["point"] == pytest.approx(Z.face_handles(z0)[2]["pos"], abs=1e-9)
    drag(c, "face.radius", 0.01)
    c.win.view.banner.button.click()                                       # (Cancel in the bar under the 3D view)
    assert rot(c).draft is None
    drag(c, "face.radius", 0.01)
    c.win.view.banner.button2.click()                                      # (Apply in the bar)
    assert zone(c)["radius"] == pytest.approx(z0["radius"] + 0.01, abs=1e-6)


def test_escape_during_a_drag_puts_the_zone_back_where_the_mouse_went_down(aero):
    c = aero
    hs = c.win.view.handles
    z0 = zone(c)
    for key, part, val in (("move", "axis0", 0.023), ("face.length.1", "face", 0.01), ("face.radius", "face", 0.01), ("tilt.x", "ring", 90.0)):
        spec = hs.specs[key]
        assert hs.press(*hs.grab_px(key, part), force=(key, part))
        if key == "tilt.x":
            for i in range(1, 5):
                hs.drag_to(*hs._px(hs._ring_point(spec, 20.0 + val * i / 4)))
        else:
            d = np.array([val, 0, 0]) if key == "move" else (np.asarray(spec["dir"]) * val)
            hs.drag_to(*hs._px(hs._taken(spec, part) + d))
        assert rot(c).draft is not None                                    # (it is drawn moved ...)
        assert hs.cancel()
        assert rot(c).draft is None and zone(c) == z0, key                 # (... and Escape puts it back, in the numbers and in the handles too)
        assert float(c.widget_do("setup.zone.0.cx")["value"]) == pytest.approx(z0["center"][0] * 1000, abs=0.01)
        assert float(c.widget_do("setup.zone.0.radius")["value"]) == pytest.approx(z0["radius"] * 1000, abs=0.01)
        assert c.widget_do("setup.zone.0.axis")["value"]["text"] == z0["axis"]
        assert hs.specs["move"]["point"] == pytest.approx(z0["center"])


def test_the_numbers_move_the_handles(aero):
    c = aero
    hs = c.win.view.handles
    c.widget_do("setup.zone.0.cy", "set", "12")
    assert zone(c)["center"][1] == pytest.approx(0.012)
    assert hs.specs["move"]["point"][1] == pytest.approx(0.012)
    c.widget_do("setup.zone.0.radius", "set", "50")
    r = zone(c)["radius"]
    side = hs.specs["face.radius"]["point"]
    assert np.linalg.norm(np.array(side) - np.array(hs.specs["move"]["point"])) == pytest.approx(r)       # (the side handle sits on the new radius)
    c.widget_do("setup.zone.0.length", "set", "60")
    assert np.linalg.norm(np.array(hs.specs["face.length.1"]["point"]) - np.array(hs.specs["face.length.0"]["point"])) == pytest.approx(0.06)


def test_a_number_typed_while_the_zone_is_a_draft_changes_the_draft(aero):
    c = aero
    hs = c.win.view.handles
    z0 = zone(c)
    hs.simulate_move("face.length.1", 0.01)
    assert rot(c).draft
    c.widget_do("setup.zone.0.cy", "set", "7")                                 # (typed: the draft follows, nothing is stored)
    assert zone(c) == z0 and rot(c).current(0)["center"][1] == pytest.approx(0.007)
    assert rot(c).current(0)["length"] == pytest.approx(z0["length"] + 0.01, abs=1e-6)
    assert hs.specs["move"]["point"][1] == pytest.approx(0.007)
    c.widget_do("setup.zone.0.rpm", "set", "1234")                             # (a speed is not part of the shape: stored at once, the draft stays)
    assert zone(c)["rpm"] == 1234 and rot(c).draft
    c.widget_do("setup.zone.0.apply", "click")
    z1 = zone(c)
    assert z1["center"][1] == pytest.approx(0.007) and z1["length"] == pytest.approx(z0["length"] + 0.01, abs=1e-6) and z1["rpm"] == 1234


def test_the_face_handles_resize_the_zone(aero):
    c = aero
    hs = c.win.view.handles
    z0 = zone(c)
    hs.simulate_move("face.length.1", 0.01)                               # (the far flat face 10 mm out)
    z1 = rot(c).current(0)
    assert z1["length"] == pytest.approx(z0["length"] + 0.01, abs=1e-6)
    assert z1["center"][2] == pytest.approx(z0["center"][2] + 0.005, abs=1e-6)       # (the other face stayed)
    assert z1["radius"] == pytest.approx(z0["radius"])
    hs.simulate_move("face.radius", -0.01)
    assert rot(c).current(0)["radius"] == pytest.approx(z0["radius"] - 0.01, abs=1e-6)
    assert float(c.widget_do("setup.zone.0.radius")["value"]) == pytest.approx((z0["radius"] - 0.01) * 1000, abs=0.01)
    c.widget_do("setup.zone.0.apply", "click")
    assert zone(c)["length"] == pytest.approx(z0["length"] + 0.01, abs=1e-6) and zone(c)["radius"] == pytest.approx(z0["radius"] - 0.01, abs=1e-6)


def test_a_zone_cannot_be_made_smaller_than_a_few_cells_or_a_share_of_the_model(aero):
    """Dragged, typed and through the engine: the radius stops at the least a zone can be (2 % of the model, or 4 cells of its own cell size), and says so."""
    c = aero
    hs = c.win.view.handles
    size = Z.model_size(c.engine.state()["geometry"]["info"])
    rmin = 0.02 * size
    drag(c, "face.radius", -5.0)                                              # (a long way in: it stops)
    assert rot(c).current(0)["radius"] == pytest.approx(rmin) and rmin > 0.001
    c.widget_do("setup.zone.0.cancel", "click")
    drag(c, "face.length.0", -5.0)
    assert rot(c).current(0)["length"] == pytest.approx(0.01 * size)
    c.widget_do("setup.zone.0.cancel", "click")
    c.widget_do("setup.zone.0.radius", "set", "0.5")                          # (typed: raised, and the panel and a toast say why)
    assert zone(c)["radius"] == pytest.approx(rmin)
    said = c.win.panels["setup"].status.text()
    assert "smallest this zone can be" in said and "2 % of the model" in said
    assert any("smallest this zone can be" in t["text"] for t in c.win.toasts.listing())
    c.widget_do("setup.zone.0.radius", "set", "30")
    c.widget_do("setup.zone.0.cell_size", "set", "6")                         # (a cell size inside: the least is 4 cells)
    assert zone(c)["radius"] == pytest.approx(0.03)
    c.widget_do("setup.zone.0.radius", "set", "10")
    assert zone(c)["radius"] == pytest.approx(0.024) and "4 cells" in c.win.panels["setup"].status.text()
    with pytest.raises(Exception, match="too small"):                         # (the engine: refuses half of it, with the reason)
        c.engine.project().set_moving_zones([dict(zone(c), radius=0.0005, cell_size=None)])
    assert zone(c)["radius"] == pytest.approx(0.024)


def test_the_ring_turns_the_axis_a_quarter_at_a_time_and_a_part_turn_goes_back(aero):
    c = aero
    hs = c.win.view.handles
    assert zone(c)["axis"] == "+z"
    got = hs.simulate_move("tilt.x", 90.0)
    assert got == pytest.approx(90.0, abs=0.5)
    assert rot(c).current(0)["axis"] == "-y" and zone(c)["axis"] == "+z"        # (drawn along the new axis; stored on Apply)
    assert c.widget_do("setup.zone.0.axis")["value"]["text"] == "-y"
    assert set(hs.specs) == {"move", "tilt.x", "tilt.z", "face.length.0", "face.length.1", "face.radius"}        # (the rings about the axes it is not along now)
    c.widget_do("setup.zone.0.apply", "click")
    assert zone(c)["axis"] == "-y"
    before = dict(zone(c))
    hs.simulate_move("tilt.x", 30.0)                                       # (30 degrees is not a quarter turn: the zone stays along its axis)
    assert zone(c) == before and rot(c).draft is None
    assert c.widget_do("setup.zone.0.axis")["value"]["text"] == "-y"


def test_the_label_while_the_ring_is_dragged_says_which_axis_it_shows(aero):
    c = aero
    hs = c.win.view.handles
    spec = hs.specs["tilt.x"]
    assert hs.press(*hs.grab_px("tilt.x"), force=("tilt.x", "ring"))
    for i in range(1, 4):
        hs.drag_to(*hs._px(hs._ring_point(spec, 20.0 + 30.0 * i / 3)))
    assert "axis: +z" in c.win.view.gizmo.text and "45" in c.win.view.gizmo.text               # (not yet a quarter turn: says when it will jump)
    for i in range(1, 5):
        hs.drag_to(*hs._px(hs._ring_point(spec, 50.0 + 60.0 * i / 4)))
    assert "axis: -y (was +z)" in c.win.view.gizmo.text and "quarter turn" in c.win.view.gizmo.text
    assert c.widget_do("setup.zone.0.axis")["value"]["text"] == "-y"                          # (the card shows it live)
    hs.release(*hs._px(hs._ring_point(spec, 110.0)))


def test_the_control_api_drags_the_handles_of_a_zone(aero):
    c = aero
    z0 = zone(c)
    c.do("view.handle", {"key": "face.radius", "value": 5})
    assert rot(c).current(0)["radius"] == pytest.approx(z0["radius"] + 0.005, abs=1e-6)
    c.do("view.handle", {"key": "tilt.y", "value": 90})
    assert rot(c).current(0)["axis"] == "+x"
    c.do("view.handle", {"key": "move", "value": [(z0["center"][0] + 0.01) * 1000, z0["center"][1] * 1000, z0["center"][2] * 1000], "part": "axis0"})
    assert rot(c).current(0)["center"][0] == pytest.approx(z0["center"][0] + 0.01, abs=1e-5)
    c.widget_do("setup.zone.0.apply", "click")
    assert zone(c)["axis"] == "+x" and zone(c)["center"][0] == pytest.approx(z0["center"][0] + 0.01, abs=1e-5)
    with pytest.raises(Exception):
        c.do("view.handle", {"key": "tilt.z", "value": [1, 2]})
    assert c.do("view.handle", {"key": "face.radius", "value": 0})["result"]["handle"]["kind"] == "face"
    assert "move" in c.state()["view"]["handles"]["handles"]


def test_a_zone_that_would_touch_another_is_refused_on_apply_and_the_draft_stays(aero):
    c = aero
    c.widget_do("setup.zone.add", "click")                                 # (the second zone sits beside the first)
    c.widget_do("setup.zone.0.handles", "set", True)
    z0 = zone(c)
    hs = c.win.view.handles
    drag(c, "face.radius", 5.0)                                            # (5 m wider: it covers the other zone)
    c.widget_do("setup.zone.0.apply", "click")
    assert zone(c)["radius"] == pytest.approx(z0["radius"])                # (stored zone unchanged)
    assert rot(c).draft is not None                                        # (the draft is still there to be changed or cancelled)
    assert any("overlap" in t["text"] or "touch" in t["text"] for t in c.win.toasts.listing())
    c.widget_do("setup.zone.0.cancel", "click")
    assert float(c.widget_do("setup.zone.0.radius")["value"]) == pytest.approx(z0["radius"] * 1000, abs=0.01)


def test_the_handles_are_those_of_the_zone_whose_card_was_used_last(aero):
    c = aero
    c.widget_do("setup.zone.add", "click")
    r = rot(c)
    assert r.shown_zone() == 1 and c.widget_do("setup.zone.1.handles")["value"]["checked"] and not c.widget_do("setup.zone.0.handles")["value"]["checked"]
    hs = c.win.view.handles
    p_second = np.array(hs.specs["move"]["point"])
    r._focus_moved(None, c._index()["setup.zone.0.rpm"])                       # (the first card is used: its handles are the ones shown, without ticking anything)
    assert r.shown_zone() == 0 and c.widget_do("setup.zone.0.handles")["value"]["checked"] and not c.widget_do("setup.zone.1.handles")["value"]["checked"]
    assert not np.allclose(hs.specs["move"]["point"], p_second)
    c.widget_do("setup.zone.0.handles", "set", False)                          # (the box hides them)
    assert not hs.on
    r._focus_moved(None, c._index()["setup.zone.1.rpm"])                       # (and they stay hidden when another card is used)
    assert not hs.on
    c.widget_do("setup.zone.1.handles", "set", True)
    assert hs.on and r.shown_zone() == 1


def test_picking_a_centre_hides_the_handles_so_that_a_click_reaches_the_model(aero):
    c = aero
    hs = c.win.view.handles
    assert hs.on
    c.widget_do("setup.zone.0.pick_axis", "set", True)
    assert not hs.on
    c.widget_do("setup.zone.0.pick_axis", "set", False)
    assert hs.on


def test_deleting_the_zone_with_handles_removes_them(aero):
    c = aero
    assert c.win.view.handles.on
    c.widget_do("setup.zone.0.delete", "click")
    assert not c.win.view.handles.on


def test_leaving_the_step_drops_a_draft(aero):
    c = aero
    z0 = zone(c)
    c.win.view.handles.simulate_move("face.radius", 0.01)
    assert rot(c).draft
    c.do("step.go", {"step": "mesh"})
    c.do("step.go", {"step": "setup"})
    assert rot(c).draft is None and zone(c) == z0


# ---------------------------------------------------------------- an edit of one number never changes another (round 2)
def test_a_typed_cell_size_never_changes_the_size_of_the_zone(aero):
    c = aero
    z0 = zone(c)                                                               # (radius 30 mm, length 15 mm on the 100 mm box)
    c.widget_do("setup.zone.0.cell_size", "set", "10")                         # (4 cells would be 40 mm: the radius must stay what it was)
    z1 = zone(c)
    assert z1["cell_size"] == pytest.approx(0.01)
    assert z1["radius"] == z0["radius"] and z1["length"] == z0["length"] and z1["center"] == z0["center"]
    c.widget_do("setup.zone.0.cell_size", "set", "30")                         # (a cell the zone is far too small for: refused, nothing changes)
    z2 = zone(c)
    assert z2["cell_size"] == pytest.approx(0.01) and z2["radius"] == z0["radius"] and z2["length"] == z0["length"]
    said = [t["text"] for t in c.win.toasts.listing()]
    assert any("This cell size needs a zone of at least" in t and "make the zone larger or the cell size smaller" in t for t in said)
    assert float(c.widget_do("setup.zone.0.radius")["value"]) == pytest.approx(z0["radius"] * 1000, abs=0.01)


def test_a_speed_or_method_edit_never_changes_the_geometry_of_an_older_small_zone(aero):
    """An older study's zone of 2.5 mm radius (the engine accepts it) must not be grown by editing its speed, its method, or by dragging another face."""
    c = aero
    pr = c.engine.project()
    pr.set_moving_zones([dict(zone(c), radius=0.0025, length=0.002)])
    c.win.panels["setup"].redo()
    z0 = zone(c)
    assert z0["radius"] == 0.0025 and z0["length"] == pytest.approx(0.002, rel=1e-9)
    c.widget_do("setup.zone.0.rpm", "set", "1200")
    z1 = zone(c)
    assert z1["rpm"] == 1200 and z1["radius"] == z0["radius"] and z1["length"] == pytest.approx(z0["length"], rel=1e-9)
    c.widget_do("setup.zone.0.method", "set", "sliding")
    z2 = zone(c)
    assert z2["method"] == "sliding" and z2["radius"] == z0["radius"] and z2["length"] == pytest.approx(z0["length"], rel=1e-9)
    c.win.view.iren.setFixedSize(900, 700)
    drag(c, "face.length.1", 0.001)                                            # (a drag of the length handle must not raise the radius either)
    assert rot(c).current(0)["radius"] == z0["radius"] and rot(c).current(0)["length"] == pytest.approx(0.003, rel=1e-6)
    drag(c, "face.radius", -0.01)                                              # (shrinking stops at the least, 2 mm; it is never raised)
    assert 0.002 <= rot(c).current(0)["radius"] <= z0["radius"] and rot(c).current(0)["radius"] == pytest.approx(0.002, rel=1e-3)
    assert Z.grown(z0, np.zeros((3, 2)), size=1.0)["radius"] == z0["radius"] and Z.grown(z0, np.zeros((3, 2)), size=1.0)["length"] == pytest.approx(z0["length"], rel=1e-9)


def test_a_typed_radius_is_still_raised_to_the_least_but_the_length_is_left_alone(aero):
    c = aero
    pr = c.engine.project()
    pr.set_moving_zones([dict(zone(c), radius=0.0025, length=0.002)])
    c.win.panels["setup"].redo()
    c.widget_do("setup.zone.0.radius", "set", "1.5")                           # (typed below the least, 2 mm: raised; the short length is not touched)
    z = zone(c)
    assert z["radius"] == pytest.approx(0.002, rel=1e-3) and z["length"] == 0.002
