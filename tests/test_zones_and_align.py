"""Rotating zones seen in the 3D view (the cylinder, the parts inside it, the turn preview, the cells that turn), a refinement region lined up
with a face of the model, and the Results step drawing only the result (the saved CAD model stayed beside it: a turning propeller showed twice)."""
import numpy as np
import pytest
from qt_support import ctl, from_thread  # noqa: F401

from simwb import regions
from simwb import zones as Z

ZONE = {"name": "prop", "center": [0.0, 0.0, 0.0], "axis": "+z", "radius": 0.1, "length": 0.04, "rpm": 3000.0, "method": "mrf"}


# ---------------------------------------------------------------- the geometry (no window)
def test_a_point_is_inside_a_zone_only_within_its_radius_and_length():
    p = [[0, 0, 0], [0.09, 0, 0.019], [0.11, 0, 0], [0, 0, 0.03], [0, 0.05, -0.02]]
    assert Z.inside_mask(ZONE, p).tolist() == [True, True, False, False, True]
    s, e = Z.ends(ZONE)
    assert np.allclose(s, [0, 0, -0.02]) and np.allclose(e, [0, 0, 0.02])
    assert np.allclose(Z.label_point(ZONE), [0.1, 0, 0.02])                                  # (it hangs at the rim of the far face)
    lp = Z.label_point(dict(ZONE, axis="+x"))
    assert lp[0] == pytest.approx(0.02) and lp[2] == pytest.approx(0.1)


def _face(i, part, pts):
    return {"id": i, "part": part, "kind": "body", "volumes": [1], "mesh": {"vertices": pts, "triangles": [[0, 1, 2]]}}


def test_parts_are_told_apart_by_name_inside_partly_or_outside():
    faces = [_face(1, "Blade", [[0.01, 0, 0], [0.05, 0, 0.01], [0.0, 0.05, 0.0]]),
             _face(2, "Arm", [[0.05, 0, 0], [0.2, 0, 0], [0.4, 0, 0]]),
             _face(3, "Frame", [[1, 1, 1], [1.2, 1, 1], [1, 1.2, 1]]),
             {"id": 4, "kind": "farfield", "volumes": [1], "mesh": {"vertices": [[0, 0, 0]], "triangles": []}}]
    got = {r["name"]: r["where"] for r in Z.classify_parts(ZONE, faces)}
    assert got == {"Blade": Z.INSIDE, "Arm": Z.PARTLY, "Frame": Z.OUTSIDE}                         # (the far field is not a part)
    arm = next(r for r in Z.classify_parts(ZONE, faces) if r["name"] == "Arm")
    assert arm["share"] == pytest.approx(1 / 3) and arm["faces"] == [2]
    pts, tris = Z.triangles_of(faces, [1, 2])
    assert pts.shape == (6, 3) and tris.shape == (2, 3) and tris.max() == 5


def test_the_turn_is_about_the_axis_through_the_centre_of_the_zone():
    z = dict(ZONE, center=[1.0, 2.0, 3.0])
    M = Z.turn_matrix(z, 90.0)
    assert (M @ [2.0, 2.0, 3.0, 1.0])[:3] == pytest.approx([1.0, 3.0, 3.0])                        # (right-hand about +z: +x of the centre goes to +y)
    assert (M @ [1.0, 2.0, 9.0, 1.0])[:3] == pytest.approx([1.0, 2.0, 9.0])                        # (a point on the axis stays)
    assert np.allclose(Z.turn_matrix(z, 0.0), np.eye(4)) and np.allclose(Z.turn_matrix(z, 360.0), np.eye(4), atol=1e-12)
    assert Z.frame_degrees({"analysis": "transient"}) == 8.0 and Z.frame_degrees({"analysis": "transient", "frame_deg": 15}) == 15.0
    assert Z.frame_degrees({"analysis": "steady"}) == 0.0


def test_the_smallest_turn_that_takes_one_direction_onto_another():
    for a, b in (([1, 0, 0], [0, 1, 0]), ([0, 0, 1], [1, 1, 1]), ([1, 2, 3], [-1, 5, 0.5]), ([0, 0, 1], [0, 0, -1]), ([1, 0, 0], [1, 0, 0])):
        R = regions.rotation_between(a, b)
        assert np.allclose(R @ R.T, np.eye(3)) and np.linalg.det(R) == pytest.approx(1.0)
        assert R @ (np.asarray(a, float) / np.linalg.norm(a)) == pytest.approx(np.asarray(b, float) / np.linalg.norm(b), abs=1e-9)


def test_a_region_lines_one_of_its_sides_up_with_a_face_direction():
    box = {"name": "b", "size": 0.01, "min": [0, 0, 0], "max": [2, 1, 1]}
    n = np.array([0.0, 0.0, 1.0])                                    # (a face that points up: the box's own z side, which already does)
    R, side, ang = regions.align_with_direction(box, n)
    assert side == 2 and ang == pytest.approx(0.0) and np.allclose(R, np.eye(3))
    n = np.array([np.cos(np.radians(20)), np.sin(np.radians(20)), 0.0])      # (a face turned 20 degrees about z: the box's x side follows it)
    R, side, ang = regions.align_with_direction(box, n)
    assert side == 0 and ang == pytest.approx(20.0)
    turned = regions.moved_and_turned(box, R, [0, 0, 0])
    assert regions.rotation_of(turned)[:, 0] == pytest.approx(n)
    n = np.array([-np.cos(np.radians(20)), -np.sin(np.radians(20)), 0.0])    # (the same face seen from behind: the box turns the same way, not half a turn)
    R2, side, ang = regions.align_with_direction(box, n)
    assert side == 0 and ang == pytest.approx(20.0)
    cyl = {"name": "c", "size": 0.01, "shape": "cylinder", "start": [0, 0, 0], "end": [0, 0, 1], "radius": 0.1, "min": [-0.1, -0.1, 0], "max": [0.1, 0.1, 1]}
    R, side, ang = regions.align_with_direction(cyl, [1, 0, 0])
    assert side == 0 and ang == pytest.approx(90.0)
    out = regions.moved_and_turned(cyl, R, [0, 0, 0])
    ax = np.asarray(out["end"]) - np.asarray(out["start"])
    assert ax / np.linalg.norm(ax) == pytest.approx([1, 0, 0], abs=1e-9)


# ---------------------------------------------------------------- in the window
def wait(c, t=300):
    assert from_thread(c.app, lambda: c.wait_idle(t), t + 20)["idle"]
    c._settle()


@pytest.fixture
def aero(ctl, box_step):
    ctl.do("study.new", {"name": "Flow", "study": "external_aero"})
    ctl.do("geometry.import", {"path": str(box_step)})
    wait(ctl)
    return ctl


def test_a_zone_is_drawn_in_setup_and_mesh_and_taken_away_when_the_step_is_left(aero):
    c = aero
    c.do("step.go", {"step": "setup"})
    assert not c.win.view.zone_view.actors
    c.widget_do("setup.zone.add", "click")
    zv = c.win.view.zone_view
    assert zv.actors                                                                         # (the cylinder, its outline and the tick on the faces)
    c.do("step.go", {"step": "mesh"})
    assert c.win.view.zone_view.actors
    assert any(la.GetInput().startswith("zone1") for la in c.win.view.label_actors)
    c.do("step.go", {"step": "geometry"})
    assert not c.win.view.zone_view.actors
    c.do("step.go", {"step": "setup"})
    assert c.win.view.zone_view.actors
    c.widget_do("setup.zone.0.delete", "click")
    assert not c.win.view.zone_view.actors


def test_the_zone_card_says_which_parts_are_inside_and_turns_a_ghost_of_them(aero):
    c = aero
    c.do("step.go", {"step": "setup"})
    c.widget_do("setup.zone.add", "click")
    part = c.widget_do("setup.zone.0.fit")["value"]["items"][1]
    c.widget_do("setup.zone.0.fit", "set", part)                                             # (round the whole box: it is inside)
    assert part in c.widget_do("setup.zone.0.parts")["value"] and "Inside" in c.widget_do("setup.zone.0.parts")["value"]
    assert "cuts through" not in c.widget_do("setup.zone.0.parts")["value"]
    z = c.engine.state()["moving_zones"][0]
    c.win.panels["setup"].rot.set_zone(0, radius=3.0)                                        # (a zone that only reaches a part of the 100 mm box)
    assert "cuts through" in c.widget_do("setup.zone.0.parts")["value"]
    c.win.panels["setup"].rot.set_zone(0, radius=z["radius"] * 1000, length=300.0)
    rot = c.win.panels["setup"].rot
    n0 = len(c.win.view.zone_view.actors)
    rot.set_turn(0, 40.0)
    assert rot.turn[0] == pytest.approx(40.0) and len(c.win.view.zone_view.actors) == n0 + 1     # (a ghost of the part, turned)
    assert "40" in c.widget_do("setup.zone.0.turn_text")["value"] and c.widget_do("setup.zone.0.turn")["value"] == 40
    rot.set_turn(0, 0.0)
    assert len(c.win.view.zone_view.actors) == n0
    rot.play(0, True)
    rot._tick()
    assert rot.turn[0] > 0
    rot.play(0, False)
    c.do("step.go", {"step": "mesh"})
    assert rot.turn == {} and rot.playing is None


def test_a_region_is_lined_up_with_a_face_of_the_model(aero):
    c = aero
    c.do("step.go", {"step": "mesh"})
    c.do("mesh.region_add", {"kind": "box"})
    x = c.win.panels["mesh"].adv.xform
    c.widget_do("mesh.region.0.xform", "set", True)
    c.widget_do("mesh.region.0.xf.tz", "set", "35")                                          # (turned first: the numbers say 35 degrees about z)
    assert not x.aligning
    c.widget_do("mesh.region.0.xf.align", "set", True)
    assert x.aligning and c.win.view.pick_mode == "face"
    assert "Click the face to line the region up with" in c.win.shell.hint()[1]
    pr = c.engine.project()
    face = next(f for f in pr.faces() if f.get("kind") == "body" and abs(f["normal"][1]) > 0.99)      # (a long side of the 100 x 20 x 20 box: it faces +-y)
    x.face_picked(face["id"])
    assert not x.aligning and c.win.view.pick_mode is None
    prev = x.preview()
    R = regions.rotation_of(prev)
    assert abs(R[:, 1] @ np.asarray(face["normal"], float)) == pytest.approx(1.0, abs=1e-6)    # (the region's y side now lies along the face's direction)
    assert abs(R[:, 0] @ np.asarray(face["normal"], float)) == pytest.approx(0.0, abs=1e-6)
    assert "parallel" in c.widget_do("mesh.region.0.xf.readout")["value"]
    c.widget_do("mesh.region.0.xf.apply", "click")
    assert abs(np.asarray(c.engine.state()["mesh"]["regions"][0].get("rotation", np.eye(3)))[:, 1] @ np.asarray(face["normal"])) == pytest.approx(1.0, abs=1e-6)


def test_lining_up_can_also_put_the_middle_of_the_region_on_the_face(aero):
    c = aero
    c.do("step.go", {"step": "mesh"})
    c.do("mesh.region_add", {"kind": "cylinder"})
    x = c.win.panels["mesh"].adv.xform
    c.widget_do("mesh.region.0.xform", "set", True)
    c.widget_do("mesh.region.0.xf.align_move", "set", True)
    c.widget_do("mesh.region.0.xf.align", "set", True)
    face = next(f for f in c.engine.project().faces() if f.get("kind") == "body" and abs(f["normal"][2]) > 0.99)
    x.face_picked(face["id"])
    prev = x.preview()
    info = c.engine.project().ref_info(face["id"])
    assert regions.centre(prev) == pytest.approx(info["point"], abs=1e-9)
    ax = np.asarray(prev["end"]) - np.asarray(prev["start"])
    assert abs(ax @ np.asarray(face["normal"])) / np.linalg.norm(ax) == pytest.approx(1.0, abs=1e-6)       # (the axis of the cylinder lies along the face's direction)


def test_a_click_on_a_face_while_lining_up_is_taken_by_the_region_not_by_part_sizing(aero):
    c = aero
    c.do("step.go", {"step": "mesh"})
    c.do("mesh.region_add", {"kind": "box"})
    c.widget_do("mesh.region.0.xform", "set", True)
    c.widget_do("mesh.region.0.xf.align", "set", True)
    mp = c.win.panels["mesh"]
    assert not mp.picking_faces and not mp.picking_part
    face = next(f for f in c.engine.project().faces() if f.get("kind") == "body")
    mp._face_picked(face["id"], False)
    assert not mp.adv.xform.aligning and mp.picked == [] and c.engine.state()["mesh"].get("refinements") in (None, [])
    c.widget_do("mesh.region.0.xf.cancel", "click")
    assert not mp.adv.xform.active and not mp.adv.xform.aligning


# ---------------------------------------------------------------- the cells that turn, as a mesh colour
def test_the_mesh_can_be_coloured_by_the_cells_that_turn(aero):
    c = aero
    pr = c.engine.project()
    n = 5
    g = np.linspace(-1, 1, n)
    nodes = np.array(np.meshgrid(g, g, g, indexing="ij")).reshape(3, -1).T
    from scipy.spatial import Delaunay
    tets = Delaunay(nodes).simplices.astype(np.int32)
    centres = nodes[tets].mean(axis=1)
    pr.data["moving_zones"] = [{"name": "prop", "center": [0, 0, 0], "axis": "+z", "radius": 0.6, "length": 2.0, "rpm": 1000.0, "method": "mrf"}]
    m = {"nodes": nodes, "tets": tets, "zone_tris": np.zeros((0, 3), np.int32)}
    with pytest.raises(ValueError, match="no rotating zone"):
        pr.zone_cell_labels(m)                                                               # (no meshed boundary: nothing to say)
    m["zone_tris"] = np.array([[0, 1, 2]], np.int32)                                         # (any boundary: the cells are then taken by their joined pieces)
    m["zone_tri_zone"] = np.array([5], np.int32)                                             # (a zone number the study does not have: the centroid test alone)
    lab = pr.zone_cell_labels(m)
    inside = (np.linalg.norm(centres[:, :2], axis=1) <= 0.6) & (np.abs(centres[:, 2]) <= 1.0)
    assert (lab == 1).tolist() == inside.tolist() and set(np.unique(lab)) <= {0, 1}
    d = pr._zone_payload(type("R", (), {"_tri_payload": lambda self, t, ce, f, face_vals=None: {"n_tris": len(t), "fv": face_vals}})(),
                         np.zeros((3, 3), int), np.array([0, 1, 2]), lab)
    assert [k["name"] for k in d["categories"]][0].startswith("Outside") and "prop" in d["categories"][1]["name"]
    assert d["present"] == sorted(set(int(lab[i]) for i in (0, 1, 2)))


# ---------------------------------------------------------------- the result alone in the Results step
def test_the_results_step_does_not_draw_the_saved_model_beside_the_result(aero):
    """A flow result with a flow tool on (the contour plane is on by default) drew the CAD model too: the propeller showed twice when played."""
    from test_qt_flow_results import _box_results
    c = aero
    c.do("step.go", {"step": "setup"})
    assert c.win.view._geo_shown and any(a.GetVisibility() for a in c.win.view.geo_actors)
    pr = c.engine.project()
    rs = _box_results()
    rs.fields["Velocity"] = ("node", rs.nodes[:, 0] * 3.0 + 1.0, "m/s")                              # (the contour plane starts on a velocity)
    pr._results = rs
    pr.data["solve"] = {"status": "done", "summary": {"default_groups": ["body"], "default_hidden_regions": []}, "finished": "now"}
    c.win.engine.changed.emit()
    c.do("step.go", {"step": "results"})                                                      # (the first visit: the flow tab learns the fields of the result)
    c._settle()
    c.do("step.go", {"step": "setup"})
    assert c.win.view._geo_shown
    c.do("step.go", {"step": "results"})                                                      # (the second: the contour plane is on from the start)
    c._settle()
    assert c.win.view.overlay_info.get("kind") != "none", "the result is not drawn"
    assert "planes" in (c.win.view.overlay_info.get("flow_tools") or []), "the flow-tool drawing is not the one under test"
    assert not c.win.view._geo_shown
    assert not any(a.GetVisibility() for a in c.win.view.geo_actors)
    c.do("step.go", {"step": "setup"})
    assert c.win.view._geo_shown                                                              # (back in Setup the model is drawn again)
