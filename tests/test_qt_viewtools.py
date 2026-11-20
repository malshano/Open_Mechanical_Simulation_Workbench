"""Face picking, selection, hover, edges and the view toolbar of the 3D view (the "viewtools" area)."""
import numpy as np
import pytest
from qt_support import EXAMPLES, ctl, from_thread  # noqa: F401


def _quad(fid, kind, volume, z=None, x=None, y=None, lo=0.0, hi=1.0):
    if z is not None:
        v = [[lo, lo, z], [hi, lo, z], [hi, hi, z], [lo, hi, z]]
    elif x is not None:
        v = [[x, lo, lo], [x, hi, lo], [x, hi, hi], [x, lo, hi]]
    else:
        v = [[lo, y, lo], [hi, y, lo], [hi, y, hi], [lo, y, hi]]
    return {"id": fid, "kind": kind, "volumes": [volume], "mesh": {"vertices": np.array(v).ravel().tolist(), "triangles": [0, 1, 2, 0, 2, 3]}}


@pytest.fixture
def cube(ctl):
    """A unit cube (faces 1-6) inside a translucent air box (faces 100, 101: the top and bottom of a box from -2 to 3)."""
    faces = [_quad(1, "body", 1, x=0), _quad(2, "body", 1, x=1), _quad(3, "body", 1, y=0), _quad(4, "body", 1, y=1),
             _quad(5, "body", 1, z=1), _quad(6, "body", 1, z=0),
             _quad(100, "farfield", 1, z=3, lo=-2, hi=3), _quad(101, "farfield", 1, z=-2, lo=-2, hi=3)]
    ctl.win.view.set_geometry(faces, ("farfield",), False)
    ctl._settle()
    return ctl


def down(x, y):
    return [x, y, 10.0], [x, y, -10.0]


# ------------------------------------------------------------------------------------------------------------ picking by ray
def test_a_ray_finds_the_face_it_hits(cube):
    h = cube.do("view.pick_ray", {"p0": [0.5, 0.5, 10], "p1": [0.5, 0.5, -10]})["result"]["hit"]
    assert h["kind"] == "face" and h["face"] == 5 and h["air"] is False and h["point"] == [0.5, 0.5, 1.0]
    h = cube.do("view.pick_ray", {"p0": [-5, 0.5, 0.5], "p1": [5, 0.5, 0.5]})["result"]["hit"]
    assert h["face"] == 1                                    # from the left: the left face first
    assert cube.do("view.pick_ray", {"p0": [9, 9, 10], "p1": [9, 9, -10]})["result"]["hit"] is None


def test_the_air_box_never_blocks_a_click_on_the_part(cube):
    p0, p1 = down(2.5, 2.5)                                   # beside the cube: only the air box is there
    assert cube.do("view.pick_ray", {"p0": p0, "p1": p1})["result"]["hit"] is None       # hidden air: not pickable
    cube.do("view.air_box", {"visible": True}) if cube.win.act_air.isVisible() else cube.win.view.set_air_visible(True)
    assert cube.do("view.pick_ray", {"p0": p0, "p1": p1})["result"]["hit"]["face"] == 100     # shown air: its faces can be picked
    p0, p1 = down(0.5, 0.5)                                   # through the cube: the part, though the air face is nearer
    assert cube.do("view.pick_ray", {"p0": p0, "p1": p1})["result"]["hit"]["face"] == 5


def test_nothing_is_pickable_while_a_result_hides_the_model(cube):
    cube.win.view.show_geometry(False)
    assert cube.do("view.pick_ray", {"p0": [0.5, 0.5, 10], "p1": [0.5, 0.5, -10]})["result"]["hit"] is None
    cube.win.view.show_geometry(True)
    assert cube.do("view.pick_ray", {"p0": [0.5, 0.5, 10], "p1": [0.5, 0.5, -10]})["result"]["hit"]["face"] == 5


# ------------------------------------------------------------------------------------------------------------ selection
def test_clicks_select_faces_in_face_mode(cube):
    got = []
    cube.win.view.selectionChanged.connect(got.append)
    cube.do("view.pick_face", {"id": 5})
    assert cube.state()["view"]["selection"] == [] and got == []                  # no mode: the panels are told, nothing is selected
    cube.do("view.pick_mode", {"mode": "face"})
    cube.do("view.pick_face", {"id": 5})
    assert cube.state()["view"]["selection"] == [5] and got[-1] == [5]
    cube.do("view.pick_face", {"id": 6})                                          # a plain click replaces
    assert cube.state()["view"]["selection"] == [6]
    cube.do("view.pick_face", {"id": 5, "add": True})                             # Ctrl adds
    assert cube.state()["view"]["selection"] == [6, 5]
    cube.do("view.pick_face", {"id": 6, "add": True})                             # Ctrl on a selected face removes it
    assert cube.state()["view"]["selection"] == [5]
    cube.win.view.click_face(-1)                                                  # a click on nothing clears
    assert cube.state()["view"]["selection"] == [] and got[-1] == []


def test_selection_and_tints_are_drawn_over_the_faces(cube):
    v = cube.win.view
    assert v.mark_actors == []
    cube.do("view.selection", {"ids": [5, 6]})
    assert len(v.mark_actors) == 1 and v.mark_actors[0].GetProperty().GetOpacity() > 0.8
    v.set_face_tints({1: "#e4572e", 2: "#e4572e", 3: "#3a86ff"})                  # two colours: two highlights, plus the selection
    assert len(v.mark_actors) == 3
    v.set_selection([])
    v._set_hover(4)                                                               # the face under the pointer is lit...
    assert len(v.mark_actors) == 3                                                # (2 tint colours + hover)
    v.set_selection([4])                                                          # ...unless it is already selected
    assert len(v.mark_actors) == 3
    v.set_hover_highlight(False)
    v.set_selection([])
    assert len(v.mark_actors) == 2
    cube.win.view.show_geometry(False)
    assert not any(a.GetVisibility() for a in v.mark_actors)                      # highlights go with the model


def test_hovering_reports_the_face_in_the_status_bar(cube):
    seen = []
    cube.win.view.faceHovered.connect(seen.append)
    cube.win.view._set_hover(5)
    cube.win.view._set_hover(5)                                                   # (only changes are reported)
    cube.win.view._set_hover(-1)
    assert seen == [5, -1]


def test_picking_a_point_on_the_result_surface(ctl):
    """In point mode a click on the drawn result reports the world point (probe, line plot)."""
    import base64

    pos = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], np.float32)
    p = {"positions": base64.b64encode(pos.tobytes()).decode(), "indices": base64.b64encode(np.array([0, 1, 2], np.uint32).tobytes()).decode(),
         "values": base64.b64encode(np.array([0, 1, 2], np.float32).tobytes()).decode(), "per": "vertex", "n_tris": 1, "min": 0.0, "max": 2.0}
    v = ctl.win.view
    v.show_surface(p, name="T", unit="C")
    h = ctl.do("view.pick_ray", {"p0": [0.25, 0.25, 5], "p1": [0.25, 0.25, -5]})["result"]["hit"]
    assert h["kind"] == "surface" and h["cell"] == 0 and h["point"] == pytest.approx([0.25, 0.25, 0.0])
    assert ctl.do("view.pick_ray", {"p0": [3, 3, 5], "p1": [3, 3, -5]})["result"]["hit"] is None


# ------------------------------------------------------------------------------------------------------------ the toolbar
def test_the_view_bar_has_the_web_versions_buttons(ctl):
    ids = {w["id"] for w in ctl.widgets("viewbar.")["widgets"]}
    assert ids == {"viewbar.iso", "viewbar.x", "viewbar.y", "viewbar.z", "viewbar.fit", "viewbar.edges", "viewbar.picture"}
    assert ctl.coverage()["complete"]


def test_view_buttons_turn_the_camera_and_a_second_press_looks_from_the_other_side(ctl):
    ctl.do("study.new", {"name": "P", "study": "static_structural"})            # (the 3D view shows once a study is open)
    c0 = ctl.state()["view"]["camera"]["position"]
    ctl.widget_do("viewbar.x", "click")
    p = ctl.state()["view"]["camera"]["position"]
    assert p[0] > 0 and abs(p[1]) < 1e-6 and abs(p[2]) < 1e-6                     # looking along -x from +x
    ctl.widget_do("viewbar.x", "click")
    assert ctl.state()["view"]["camera"]["position"][0] < 0                       # again: from the other side
    ctl.widget_do("viewbar.z", "click")
    assert ctl.state()["view"]["camera"]["position"][2] > 0
    ctl.widget_do("viewbar.iso", "click")
    assert ctl.state()["view"]["camera"]["position"] != p and c0 is not None


def test_edges_toggle_draws_or_hides_the_outlines(cube):
    v = cube.win.view
    assert v.edge_actors and all(a.GetVisibility() for a in v.edge_actors)
    assert cube.widget_do("viewbar.edges")["value"] == {"checked": True}
    cube.widget_do("viewbar.edges", "set", False)
    assert not any(a.GetVisibility() for a in v.edge_actors) and cube.win.view.edges_on is False
    cube.do("view.edges", {"visible": True})
    assert all(a.GetVisibility() for a in v.edge_actors)
    v.show_geometry(False)
    assert not any(a.GetVisibility() for a in v.edge_actors)


def test_picture_button_opens_a_dialog_so_the_action_takes_the_path(ctl, tmp_path):
    with pytest.raises(ValueError, match="view.picture"):
        ctl.widget_do("viewbar.picture", "click")
    out = tmp_path / "view.png"
    ctl.do("view.picture", {"path": str(out)})
    assert out.read_bytes()[:4] == b"\x89PNG"


# ------------------------------------------------------------------------------------------------------------ real CAD faces
def test_every_face_of_a_real_part_can_be_picked(ctl):
    """Import the heated plate and shoot a ray at each face's centre along its normal from outside: the ray finds that face."""
    ctl.do("study.example", {"study": "heat_conduction"})
    assert from_thread(ctl.app, lambda: ctl.wait_idle(300), 320)["idle"]
    faces = ctl.engine.project().faces()
    assert len(faces) > 5 and ctl.state()["view"]["geometry"]["faces"] == len(faces)
    ok = 0
    for f in faces:
        c, n = np.array(f["centroid"]), np.array(f["normal"])
        if f["surface_type"] != "Plane":
            continue
        h = ctl.do("view.pick_ray", {"p0": (c + n * 0.05).tolist(), "p1": (c - n * 0.001).tolist()})["result"]["hit"]
        if h and h["face"] == f["id"]:
            ok += 1
    planes = sum(1 for f in faces if f["surface_type"] == "Plane")
    assert ok >= planes - 2 and ok >= 8                    # (a plane whose centre lies in a hole or under another part may differ)
    ctl.do("view.pick_mode", {"mode": "face"})
    top = max((f for f in faces if f["surface_type"] == "Plane"), key=lambda f: f["centroid"][2])
    ctl.do("view.pick_face", {"id": top["id"]})
    assert ctl.state()["view"]["selection"] == [top["id"]]
