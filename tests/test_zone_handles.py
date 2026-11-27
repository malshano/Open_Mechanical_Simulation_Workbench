"""Rotating zones are moved, resized and turned in the 3D view with the shared handles (issue #10), and the numbers of the zone's card and the handles follow each
other. The zone is stored when a handle is let go (a drag changes only the drawing, the numbers and a line in the corner of the view)."""
import numpy as np
import pytest
from qt_support import ctl, from_thread  # noqa: F401

from simwb import regions
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
    g[1, 0] = -5.0                                                        # (never smaller than the minimum, and never smaller than the cell size inside)
    assert Z.grown(ZONE, g)["radius"] == pytest.approx(regions.MIN_SIZE)
    assert Z.grown(dict(ZONE, cell_size=0.005), g)["radius"] == pytest.approx(0.005 * 1.01)
    g = np.zeros((3, 2))
    g[0, 0] = -5.0
    assert Z.grown(ZONE, g)["length"] == pytest.approx(regions.MIN_SIZE)


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
    return ctl


def zone(c):
    return c.engine.state()["moving_zones"][0]


def test_a_new_zone_shows_its_handles_and_the_box_hides_them(aero):
    c = aero
    hs = c.win.view.handles
    kinds = {k: s["kind"] for k, s in hs.specs.items()}
    assert kinds == {"move": "point", "tilt.x": "ring", "tilt.y": "ring", "face.length.0": "face", "face.length.1": "face", "face.radius": "face"}      # (no ring about its own axis)
    assert c.widget_do("setup.zone.0.handles")["value"]["checked"] is True
    assert "Drag an arrow to move the rotating zone" in c.win.shell.hint()[1]
    c.widget_do("setup.zone.0.handles", "set", False)
    assert not hs.on
    c.widget_do("setup.zone.0.handles", "set", True)
    assert hs.on
    c.do("step.go", {"step": "mesh"})                                      # (the zone is only drawn in the Mesh step: it is edited in Setup)
    assert not hs.on and c.win.view.zone_view.actors
    c.do("step.go", {"step": "setup"})
    assert hs.on


def test_dragging_the_move_arrow_changes_the_drawing_and_the_numbers_and_stores_the_zone_on_release(aero):
    c = aero
    hs, rot = c.win.view.handles, c.win.panels["setup"].rot
    z0 = zone(c)
    p0 = np.array(hs.specs["move"]["point"])
    start = hs.grab_px("move", "axis0")
    assert hs.press(*start, force=("move", "axis0"))
    end = hs._px(hs._arrows(hs.specs["move"])[0][1] + (hs._arrows(hs.specs["move"])[0][2] - hs._arrows(hs.specs["move"])[0][1]) / 2 + [0.02, 0, 0])
    hs.drag_to(*end)
    assert zone(c) == z0                                                   # (nothing stored while dragging)
    assert float(c.widget_do("setup.zone.0.cx")["value"]) == pytest.approx(z0["center"][0] * 1000 + 20, abs=0.01)     # (the number follows the handle)
    assert c.win.view.zone_view.actors and np.allclose(hs.specs["move"]["point"], p0 + [0.02, 0, 0], atol=1e-6)
    assert "Moving" in c.win.view.gizmo.text and "20.0" in c.win.view.gizmo.text           # (what the drag does, in the corner of the view)
    hs.release(*end)
    z1 = zone(c)
    assert z1["center"][0] == pytest.approx(z0["center"][0] + 0.02, abs=1e-6) and z1["center"][1:] == pytest.approx(z0["center"][1:])
    assert c.win.view.gizmo.text is None                                   # (and gone when it is let go)
    assert np.allclose(hs.specs["move"]["point"], z1["center"])           # (the handle is where the zone is)
    assert float(c.widget_do("setup.zone.0.cx")["value"]) == pytest.approx(z1["center"][0] * 1000, abs=0.01)
    assert rot.handles.base is None


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


def test_the_face_handles_resize_the_zone(aero):
    c = aero
    hs = c.win.view.handles
    z0 = zone(c)
    hs.simulate_move("face.length.1", 0.01)                               # (the far flat face 10 mm out)
    z1 = zone(c)
    assert z1["length"] == pytest.approx(z0["length"] + 0.01, abs=1e-6)
    assert z1["center"][2] == pytest.approx(z0["center"][2] + 0.005, abs=1e-6)       # (the other face stayed)
    assert z1["radius"] == pytest.approx(z0["radius"])
    hs.simulate_move("face.radius", -0.01)
    assert zone(c)["radius"] == pytest.approx(z0["radius"] - 0.01, abs=1e-6)
    assert float(c.widget_do("setup.zone.0.radius")["value"]) == pytest.approx((z0["radius"] - 0.01) * 1000, abs=0.01)
    hs.simulate_move("face.length.0", -10.0)                              # (far past nothing: the zone stops at the smallest size, it never vanishes)
    assert zone(c)["length"] == pytest.approx(regions.MIN_SIZE, abs=1e-9)


def test_the_ring_turns_the_axis_a_quarter_at_a_time_and_a_part_turn_goes_back(aero):
    c = aero
    hs = c.win.view.handles
    assert zone(c)["axis"] == "+z"
    got = hs.simulate_move("tilt.x", 90.0)
    assert got == pytest.approx(90.0, abs=0.5)
    assert zone(c)["axis"] == "-y"
    assert c.widget_do("setup.zone.0.axis")["value"]["text"] == "-y"
    assert set(hs.specs) == {"move", "tilt.x", "tilt.z", "face.length.0", "face.length.1", "face.radius"}        # (the rings about the axes it is not along now)
    before = dict(zone(c))
    hs.simulate_move("tilt.x", 30.0)                                       # (30 degrees is not a quarter turn: the zone stays along its axis)
    assert zone(c) == before
    assert c.widget_do("setup.zone.0.axis")["value"]["text"] == "-y"


def test_the_control_api_drags_the_handles_of_a_zone(aero):
    c = aero
    z0 = zone(c)
    c.do("view.handle", {"key": "face.radius", "value": 5})
    assert zone(c)["radius"] == pytest.approx(z0["radius"] + 0.005, abs=1e-6)
    c.do("view.handle", {"key": "tilt.y", "value": 90})
    assert zone(c)["axis"] == "+x"
    c.do("view.handle", {"key": "move", "value": [(z0["center"][0] + 0.01) * 1000, z0["center"][1] * 1000, z0["center"][2] * 1000], "part": "axis0"})
    assert zone(c)["center"][0] == pytest.approx(z0["center"][0] + 0.01, abs=1e-5)
    with pytest.raises(Exception):
        c.do("view.handle", {"key": "tilt.x", "value": [1, 2]})
    assert c.do("view.handle", {"key": "face.radius", "value": 0})["result"]["handle"]["kind"] == "face"
    assert "move" in c.state()["view"]["handles"]["handles"]


def test_a_zone_that_would_touch_another_is_refused_on_release_and_goes_back(aero):
    c = aero
    c.widget_do("setup.zone.add", "click")                                 # (the second zone sits beside the first)
    c.widget_do("setup.zone.0.handles", "set", True)
    z0 = zone(c)
    far = c.engine.state()["moving_zones"][1]
    hs = c.win.view.handles
    hs.simulate_move("face.radius", 5.0)                                   # (5 m wider: it covers the other zone)
    assert zone(c)["radius"] == pytest.approx(z0["radius"])                # (stored zone unchanged)
    assert float(c.widget_do("setup.zone.0.radius")["value"]) == pytest.approx(z0["radius"] * 1000, abs=0.01)
    assert far["name"] == c.engine.state()["moving_zones"][1]["name"]


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
