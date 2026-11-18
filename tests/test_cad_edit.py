"""CAD edits (simwb.cad_edit): every operation against a closed-form volume, the validation, and the edits turning
with the model."""
import json
import math

import numpy as np
import pytest

from simwb import cad_edit
from simwb.cad_edit import EditError

MM3 = 1e-9  # one mm3 in m3


@pytest.fixture()
def gm():
    import gmsh

    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.model.add("t")
    yield gmsh
    gmsh.finalize()


def bar(gm, name="Bar", size=(100, 20, 20)):
    """A bar (mm) with a corner at the origin, named like a STEP part."""
    t = gm.model.occ.addBox(0, 0, 0, *(s * 1e-3 for s in size))
    gm.model.occ.synchronize()
    gm.model.setEntityName(3, t, name)
    return t


def bodies(gm):
    """{name: volume in mm3}"""
    gm.model.occ.synchronize()
    return {gm.model.getEntityName(3, t): gm.model.occ.getMass(3, t) / MM3 for _, t in gm.model.getEntities(3)}


def test_cut_by_a_cylinder_and_a_through_hole(gm):
    bar(gm)
    cad_edit.apply(gm, [
        {"op": "cylinder", "name": "Pin", "base": [50, 10, -5], "axis": [0, 0, 30], "radius_mm": 4},
        {"op": "cut", "target": "Bar", "tools": ["Pin"]}])
    v = bodies(gm)
    assert list(v) == ["Bar"]                                   # the tool is consumed, the target keeps its name
    assert v["Bar"] == pytest.approx(100 * 20 * 20 - math.pi * 16 * 20, rel=1e-3)
    # a hole: the same thing in one edit, from the face
    cad_edit.apply(gm, [{"op": "hole", "body": "Bar", "at": [20, 10, 20], "direction": [0, 0, -1], "diameter_mm": 6}])
    assert bodies(gm)["Bar"] == pytest.approx(100 * 20 * 20 - math.pi * 16 * 20 - math.pi * 9 * 20, rel=1e-3)
    cad_edit.apply(gm, [{"op": "hole", "body": "Bar", "at": [80, 10, 20], "direction": [0, 0, -1], "diameter_mm": 4,
                         "depth_mm": 5}])     # a blind hole
    assert bodies(gm)["Bar"] == pytest.approx(100 * 20 * 20 - math.pi * 16 * 20 - math.pi * 9 * 20 - math.pi * 4 * 5, rel=1e-3)


def test_fillet_and_chamfer_remove_the_expected_volume(gm):
    bar(gm)
    cad_edit.apply(gm, [{"op": "fillet", "body": "Bar", "radius_mm": 2, "edges": {"parallel": "x"}}])
    # four edges along x, each loses (1 - pi/4) r^2 L
    assert bodies(gm)["Bar"] == pytest.approx(100 * 20 * 20 - 4 * (1 - math.pi / 4) * 4 * 100, rel=2e-3)
    gm.model.setEntityName(3, gm.model.getEntities(3)[0][1], "Bar")
    # (a fresh bar for the chamfer)
    for _, t in gm.model.getEntities(3):
        gm.model.occ.remove([(3, t)], recursive=True)
    bar(gm)
    cad_edit.apply(gm, [{"op": "chamfer", "body": "Bar", "distance_mm": 3, "edges": {"near": [[50, 0, 0]]}}])
    assert bodies(gm)["Bar"] == pytest.approx(100 * 20 * 20 - 0.5 * 9 * 100, rel=1e-3)   # one edge along x at y=z=0


def test_edge_selector_by_face_box_and_length(gm):
    bar(gm)
    M = cad_edit._Model(gm)
    M.sync()
    M.name_all()
    vol = M.one("Bar")
    top, _ = cad_edit._pick_edges(M, vol, cad_edit._selector({"of_face_near": [[50, 10, 20]]}))
    assert len(top) == 4                                         # the four edges of the top face
    short, _ = cad_edit._pick_edges(M, vol, cad_edit._selector({"all": True, "length_mm": [None, 25]}))
    assert len(short) == 8                                       # the eight 20 mm edges
    inbox, _ = cad_edit._pick_edges(M, vol, cad_edit._selector({"all": True, "in_box": [-1, -1, -1, 101, 1, 21]}))
    assert len(inbox) == 4                                       # the edges whose middle lies in the y = 0 slab
    with pytest.raises(EditError, match="No edge of the body is near"):
        cad_edit._pick_edges(M, vol, cad_edit._selector({"near": [[500, 500, 500]]}))
    with pytest.raises(EditError, match="matches no edge"):
        cad_edit._pick_edges(M, vol, cad_edit._selector({"all": True, "type": "circle"}))


def test_union_intersect_and_the_names(gm):
    bar(gm, "A")
    cad_edit.apply(gm, [
        {"op": "box", "name": "B", "min": [80, 0, 0], "size": [50, 20, 20]},     # overlaps A by 20 x 20 x 20
        {"op": "union", "bodies": ["A", "B"], "name": "AB"}])
    assert bodies(gm) == {"AB": pytest.approx(100 * 400 + 50 * 400 - 20 * 400, rel=1e-6)}
    cad_edit.apply(gm, [
        {"op": "box", "name": "C", "min": [0, 0, 0], "size": [30, 20, 20]},
        {"op": "intersect", "target": "AB", "tools": ["C"]}])
    assert bodies(gm) == {"AB": pytest.approx(30 * 400, rel=1e-6)}


def test_union_of_bodies_that_do_not_touch_is_refused(gm):
    bar(gm, "A")
    with pytest.raises(EditError, match="do not touch"):
        cad_edit.apply(gm, [{"op": "box", "name": "B", "min": [200, 0, 0], "size": [10, 10, 10]},
                            {"op": "union", "bodies": ["A", "B"]}])


def test_move_rotate_scale_mirror_and_patterns(gm):
    bar(gm)
    cad_edit.apply(gm, [{"op": "move", "bodies": ["Bar"], "by": [10, 0, 5]}])
    bb = gm.model.getBoundingBox(3, gm.model.getEntities(3)[0][1])
    assert np.allclose(bb[:3], [0.010, 0, 0.005], atol=1e-6)
    cad_edit.apply(gm, [{"op": "rotate", "point": [0, 0, 0], "axis": [0, 0, 1], "angle_deg": 90}])
    bb = gm.model.getBoundingBox(3, gm.model.getEntities(3)[0][1])
    assert np.allclose(bb[:3], [-0.020, 0.010, 0.005], atol=1e-6) and np.allclose(bb[3:], [0, 0.110, 0.025], atol=1e-6)
    cad_edit.apply(gm, [{"op": "scale", "factor": 2}])           # about its own middle
    assert bodies(gm)["Bar"] == pytest.approx(8 * 100 * 400, rel=1e-6)
    cad_edit.apply(gm, [{"op": "mirror", "point": [0, 0, 0], "normal": [1, 0, 0]}])
    v = bodies(gm)
    assert set(v) == {"Bar", "Bar (mirror)"} and v["Bar (mirror)"] == pytest.approx(v["Bar"], rel=1e-9)
    ids = {gm.model.getEntityName(3, t): gm.model.getBoundingBox(3, t) for _, t in gm.model.getEntities(3)}
    assert ids["Bar (mirror)"][0] == pytest.approx(-ids["Bar"][3], abs=1e-9)   # the mirror image across x = 0


def test_patterns(gm):
    bar(gm, "Fin", (10, 10, 10))
    cad_edit.apply(gm, [{"op": "pattern_linear", "body": "Fin", "count": 4, "step": [15, 0, 0]}])
    assert sorted(bodies(gm)) == ["Fin", "Fin 2", "Fin 3", "Fin 4"]
    for _, t in gm.model.getEntities(3):
        gm.model.occ.remove([(3, t)], recursive=True)
    bar(gm, "Blade", (30, 4, 2))
    cad_edit.apply(gm, [{"op": "move", "by": [0, -2, -1]},
                        {"op": "pattern_polar", "body": "Blade", "count": 3, "point": [0, 0, 0], "axis": [0, 0, 1],
                         "union": True}])
    v = bodies(gm)
    assert len(v) == 1 and 2 * 240 < list(v.values())[0] < 3 * 240         # three blades through the hub, fused into one


def test_prism_revolve_and_loft_volumes(gm):
    cad_edit.apply(gm, [
        {"op": "prism", "name": "Tri", "points": [[0, 0, 0], [30, 0, 0], [0, 40, 0]], "direction": [0, 0, 10]}])
    assert bodies(gm)["Tri"] == pytest.approx(0.5 * 30 * 40 * 10, rel=1e-6)
    cad_edit.apply(gm, [
        {"op": "revolve", "name": "Cyl", "points": [[0, 0, 100], [10, 0, 100], [10, 0, 130], [0, 0, 130]],
         "axis_point": [0, 0, 0], "axis_direction": [0, 0, 1]}])
    assert bodies(gm)["Cyl"] == pytest.approx(math.pi * 100 * 30, rel=1e-4)
    sq = lambda z, w: [[-w, -w, z], [w, -w, z], [w, w, z], [-w, w, z]]  # noqa: E731
    cad_edit.apply(gm, [{"op": "loft", "name": "Frustum", "sections": [sq(200, 20), sq(260, 10)], "ruled": True}])
    A1, A2 = 40 * 40, 20 * 20
    assert bodies(gm)["Frustum"] == pytest.approx(60 / 3 * (A1 + A2 + math.sqrt(A1 * A2)), rel=1e-4)


def test_cut_plane_keeps_one_side(gm):
    bar(gm)
    cad_edit.apply(gm, [{"op": "cut_plane", "point": [30, 0, 0], "normal": [1, 0, 0], "keep": "+"}])
    assert bodies(gm)["Bar"] == pytest.approx(70 * 400, rel=1e-6)
    # nothing is left on the - side of x = 30 any more: the edit says so instead of leaving an empty model
    with pytest.raises(EditError, match="nothing is left"):
        cad_edit.apply(gm, [{"op": "cut_plane", "point": [30, 0, 0], "normal": [1, 0, 0], "keep": "-"}])


def test_errors_say_what_to_do(gm):
    bar(gm)
    with pytest.raises(EditError, match="No body named 'Wing'.*'Bar'"):
        cad_edit.apply(gm, [{"op": "move", "bodies": ["Wing"], "by": [1, 0, 0]}])
    with pytest.raises(EditError, match="exists already"):
        cad_edit.apply(gm, [{"op": "box", "name": "Bar", "min": [0, 0, 0], "size": [1, 1, 1]}])
    with pytest.raises(EditError, match="CAD edit 1 \\(fillet\\).*smaller radius"):
        cad_edit.apply(gm, [{"op": "fillet", "body": "Bar", "radius_mm": 15, "edges": {"all": True}}])
    with pytest.raises(EditError, match="every body"):
        cad_edit.apply(gm, [{"op": "delete", "bodies": ["Bar"]}])
    with pytest.raises(EditError, match="not in one plane"):
        cad_edit.apply(gm, [{"op": "prism", "name": "Bad", "points": [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 5]],
                             "direction": [0, 0, 1]}])
    assert list(bodies(gm)) == ["Bar"]


def test_unnamed_step_bodies_get_the_names_the_workbench_shows(gm):
    for x in (0, 50):
        t = gm.model.occ.addBox(x * 1e-3, 0, 0, 0.02, 0.02, 0.02)
        gm.model.occ.synchronize()
        gm.model.setEntityName(3, t, "Shapes/Open CASCADE STEP translator 7.8 1/1/Open CASCADE STEP translator 7.8 1.1")
    cad_edit.apply(gm, [{"op": "move", "bodies": ["Body 2"], "by": [0, 0, 100]}])
    bbs = {gm.model.getEntityName(3, t): gm.model.getBoundingBox(3, t)[2] for _, t in gm.model.getEntities(3)}
    assert bbs["Body 1"] == pytest.approx(0, abs=1e-6) and bbs["Body 2"] == pytest.approx(0.1, abs=1e-6)


def test_validation():
    ok = cad_edit.validate({"op": "fillet", "body": " A ", "radius_mm": "2", "edges": {"parallel": "x"}})
    assert ok == {"op": "fillet", "body": "A", "radius_mm": 2.0, "edges": {"parallel": [1.0, 0.0, 0.0]}}
    bad = [({"op": "weld"}, "Unknown operation"),
           ({"op": "box", "name": "A", "min": [0, 0, 0]}, "'size' is required"),
           ({"op": "box", "name": "A", "min": [0, 0], "size": [1, 1, 1]}, "list of 3 numbers"),
           ({"op": "box", "name": "A", "min": [0, 0, 0], "size": [1, 1, -1]}, "at least"),
           ({"op": "box", "name": "A", "min": [0, 0, 0], "size": [1, 1, 1], "colour": "red"}, "unknown parameter"),
           ({"op": "cylinder", "name": "A", "base": [0, 0, 0], "axis": [0, 0, 0], "radius_mm": 1}, "zero vector"),
           ({"op": "fillet", "body": "A", "radius_mm": 1, "edges": {"colour": 1}}, "Unknown edge selector"),
           ({"op": "fillet", "body": "A", "radius_mm": 1, "edges": {}}, "selector"),
           ({"op": "fillet", "body": "A", "radius_mm": float("nan"), "edges": {"all": True}}, "finite"),
           ({"op": "pattern_linear", "body": "A", "count": 1, "step": [1, 0, 0]}, "at least 2"),
           ({"op": "loft", "name": "L", "sections": [[[0, 0, 0], [1, 0, 0], [1, 1, 0]]]}, "at least 2"),
           ({"op": "mirror", "point": [0, 0, 0], "normal": [0, 0, 0]}, "zero vector"),
           ({"op": "cut_plane", "point": [0, 0, 0], "normal": [1, 0, 0], "keep": "up"}, "one of")]
    for op, msg in bad:
        with pytest.raises(EditError, match=msg):
            cad_edit.validate(op)


def test_edits_turn_with_the_model():
    """A box 100 x 20 x 20 at the origin, the model turned 90 degrees about z: the box now lies along y."""
    D = np.array([[0.0, -1, 0], [1, 0, 0], [0, 0, 1]])
    ed = cad_edit.turn([cad_edit.validate(e) for e in [
        {"op": "box", "name": "B", "min": [10, 0, 0], "size": [100, 20, 20]},
        {"op": "hole", "body": "B", "at": [50, 10, 20], "direction": [0, 0, -1], "diameter_mm": 6},
        {"op": "fillet", "body": "B", "radius_mm": 1, "edges": {"parallel": "x", "in_box": [0, 0, 0, 10, 10, 10]}}]],
        D, [0.001, 0, 0])
    assert np.allclose(ed[0]["min"], [1, 10, 0]) and np.allclose(ed[0]["rot"], D)
    assert np.allclose(ed[1]["at"], [1 - 10, 50, 20]) and np.allclose(ed[1]["direction"], [0, 0, -1])
    assert np.allclose(ed[2]["edges"]["parallel"], [0, 1, 0])
    assert np.allclose(ed[2]["edges"]["in_box"], [1 - 10, 0, 0, 1, 10, 10])


def test_turned_box_is_the_same_box_turned(gm):
    D = np.array([[0.0, -1, 0], [1, 0, 0], [0, 0, 1]])
    ed = cad_edit.turn([{"op": "box", "name": "B", "min": [10, 0, 0], "size": [100, 20, 20]}], D, [0, 0, 0])
    cad_edit.apply(gm, ed)
    bb = np.array(gm.model.getBoundingBox(3, gm.model.getEntities(3)[0][1])) * 1000
    assert np.allclose(bb, [-20, 10, 0, 0, 110, 20], atol=1e-3)


def test_describe_is_one_short_line():
    t = cad_edit.describe(cad_edit.validate({"op": "loft", "name": "Wing", "sections": [[[0, 0, 0], [1, 0, 0], [1, 1, 0]]] * 3}))
    assert t.startswith("loft") and len(t) <= 150 and "\n" not in t


# ---------------------------------------------------------------- through the project (the worker process, the faces)
@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMWB_HOME", str(tmp_path / "ws"))
    import importlib

    import simwb.project as P

    importlib.reload(P)
    yield P


def _wait(job, timeout=300):
    assert job.wait(timeout), "job timed out"
    assert job.status == "done", (job.error, job.log[-10:])
    return job.result


def _bar(P, box_step):
    pr = P.create_project("Edit", "static_structural")
    _wait(pr.import_step(box_step))
    return pr


def test_edits_in_a_project_hole_fillet_undo_and_the_boundary_conditions(workspace, box_step):
    P = workspace
    pr = _bar(P, box_step)
    name, vol0 = pr.bodies()[0]["name"], pr.bodies()[0]["volume"]
    root = pr.add_bc("Root", "fixed_support", [f["id"] for f in pr.faces() if f["normal"][0] < -0.9])
    n_faces = len(pr.faces())

    _wait(pr.edit_geometry("add", {"op": "hole", "body": name, "at": [50, 10, 20], "direction": [0, 0, -1],
                                   "diameter_mm": 8}))
    assert pr.bodies()[0]["volume"] == pytest.approx(vol0 - math.pi * 16 * 20 * 1e-9, rel=1e-3)
    assert len(pr.faces()) == n_faces + 1 and pr.bodies()[0]["name"] == name          # one new face: the bore
    assert any(f["surface_type"] == "Cylinder" for f in pr.faces())
    bc = next(b for b in pr.data["bcs"] if b["name"] == "Root")
    assert len(bc["faces"]) == 1 and pr.face_map()[bc["faces"][0]]["normal"][0] < -0.9   # the support follows its face

    _wait(pr.edit_geometry("add", {"op": "fillet", "body": name, "radius_mm": 2, "edges": {"parallel": "x"}}))
    assert len(pr.cad_edits()) == 2 and "CAD edit 2: fillet" in pr.summary_text()
    assert pr.bodies()[0]["volume"] == pytest.approx(vol0 - math.pi * 16 * 20 * 1e-9 - 4 * (1 - math.pi / 4) * 4 * 100 * 1e-9, rel=2e-3)
    saved = P.open_project(pr.id)                                              # (it is part of the saved study)
    assert [e["op"] for e in saved.cad_edits()] == ["hole", "fillet"]

    # a bad edit: refused with the reason, the geometry and the list stay as they were
    job = pr.edit_geometry("add", {"op": "fillet", "body": name, "radius_mm": 15, "edges": {"all": True}})
    job.wait(300)
    assert job.status == "failed" and "smaller radius" in job.error
    assert len(pr.cad_edits()) == 2 and pr.data["geometry"]["status"] == "ready"
    with pytest.raises(ValueError, match="no CAD edit 5"):
        pr.edit_geometry("remove", index=5)
    with pytest.raises(Exception, match="unknown parameter"):
        pr.edit_geometry("add", {"op": "fillet", "body": name, "radius": 1, "edges": {"all": True}})

    _wait(pr.edit_geometry("remove", index=2))                                 # undo the fillet
    assert pr.bodies()[0]["volume"] == pytest.approx(vol0 - math.pi * 16 * 20 * 1e-9, rel=1e-3)
    _wait(pr.edit_geometry("clear"))
    assert pr.bodies()[0]["volume"] == pytest.approx(vol0, rel=1e-9) and not pr.cad_edits() and len(pr.faces()) == n_faces
    del root


def test_edits_follow_a_turn_of_the_model(workspace, box_step):
    P = workspace
    pr = _bar(P, box_step)
    name = pr.bodies()[0]["name"]
    _wait(pr.edit_geometry("add", {"op": "hole", "body": name, "at": [20, 5, 20], "direction": [0, 0, -1],
                                   "diameter_mm": 8}))
    v1 = pr.bodies()[0]["volume"]
    c = np.asarray(pr._parts_center()) * 1000            # (the turn pivots on the centre of the parts)
    D = np.array([[0.0, -1, 0], [1, 0, 0], [0, 0, 1]])
    _wait(pr.rotate_geometry("z", 90))                    # the bar now lies along y; the hole must still be in the bar
    assert pr.bodies()[0]["volume"] == pytest.approx(v1, rel=1e-6)
    bore = next(f for f in pr.faces() if f["surface_type"] == "Cylinder")
    assert bore["area"] == pytest.approx(math.pi * 8e-3 * 20e-3, rel=1e-3)
    bb = pr.bodies()[0]["bbox"]
    assert bb[4] - bb[1] > 0.099 and bb[3] - bb[0] < 0.021                        # (along y now)
    assert pr.cad_edits()[0]["edit"]["at"] == pytest.approx((D @ [20, 5, 20] + c - D @ c).tolist(), abs=1e-6)
    assert pr.cad_edits()[0]["edit"]["direction"] == pytest.approx([0, 0, -1], abs=1e-9)


def test_the_tools_for_the_assistant(workspace, box_step):
    P = workspace
    from simwb import tools

    pr = _bar(P, box_step)
    name = pr.bodies()[0]["name"]
    r = json.loads(tools.call("find_edges", pr, {"parallel": "x"}))
    assert r["count"] == 4 and all(abs(e["length_mm"] - 100) < 1e-6 for e in r["edges"]) and r["edges"][0]["bodies"] == [name]
    near = json.loads(tools.call("find_edges", pr, {"near_mm": [50, 0, 0], "limit": 1}))
    assert near["edges"][0]["center_mm"] == pytest.approx([50, 0, 0], abs=1e-3)
    out = json.loads(tools.call("edit_geometry", pr, {"edit": {"op": "chamfer", "body": name, "distance_mm": 2,
                                                                "edges": {"near": [[50, 0, 0]]}}}))
    assert out["geometry"] == "rebuilt" and any("mm" in line and name in line for line in out["report"])
    assert "CAD edit 1" in out["state"]
    lst = json.loads(tools.call("edit_geometry", pr, {"action": "list"}))
    assert lst["edits"][0]["number"] == 1 and lst["bodies"] == [name]
    bad = json.loads(tools.call("edit_geometry", pr, {"edit": {"op": "cut", "target": name, "tools": ["Nothing"]}}))
    assert "No body named 'Nothing'" in json.dumps(bad) and bad["geometry"]["status"] == "failed"
    assert tools.call("edit_geometry", pr, {"edit": "fillet"}).startswith("ERROR")
    assert tools.call("edit_geometry", pr, {"action": "remove", "number": 9}).startswith("ERROR")
    upd = json.loads(tools.call("edit_geometry", pr, {"action": "update", "number": 1, "edit": {"distance_mm": 3}}))
    assert upd["geometry"] == "rebuilt" and pr.cad_edits()[0]["edit"]["distance_mm"] == 3.0


def test_a_new_file_starts_without_edits_and_stl_cannot_be_edited(workspace, box_step, tmp_path):
    P = workspace
    pr = _bar(P, box_step)
    _wait(pr.edit_geometry("add", {"op": "rename", "body": pr.bodies()[0]["name"], "name": "Beam"}))
    assert pr.bodies()[0]["name"] == "Beam"
    _wait(pr.import_step(box_step))
    assert not pr.cad_edits() and pr.bodies()[0]["name"] != "Beam"


def _naca(c, y, x0=0.0, t=0.12, n=20):
    """A closed NACA 00xx section (mm): upper surface leading edge to trailing edge, then the lower back."""
    xs = [0.5 * (1 - math.cos(math.pi * i / n)) for i in range(n + 1)]
    yt = lambda x: 5 * t * (0.2969 * math.sqrt(x) - 0.126 * x - 0.3516 * x ** 2 + 0.2843 * x ** 3 - 0.1036 * x ** 4)  # noqa: E731
    pts = [(x * c, yt(x) * c) for x in xs] + [(x * c, -yt(x) * c) for x in reversed(xs[1:-1])]
    return [[x0 + px, y, pz] for px, pz in pts]


def test_a_wing_lofted_through_airfoils_builds_and_meshes_for_display(workspace, box_step):
    """A smooth loft through NACA sections (a sharp trailing edge) was a surface the display mesher could not finish
    (minutes, then nothing): each smooth run of a section is its own spline now."""
    P = workspace
    pr = _bar(P, box_step)
    seed = pr.bodies()[0]["name"]
    secs = [_naca(300, 0), _naca(220, 400, 40), _naca(120, 800, 90)]
    _wait(pr.edit_geometry("add", {"op": "loft", "name": "Wing", "sections": secs, "smooth": True}), 120)
    _wait(pr.edit_geometry("add", {"op": "delete", "bodies": [seed]}), 120)
    b = pr.bodies()[0]
    bb = b["bbox"]
    assert b["name"] == "Wing" and bb[4] - bb[1] == pytest.approx(0.8, abs=1e-4) and bb[3] - bb[0] == pytest.approx(0.3, abs=2e-3)
    # volume of a tapered 12 % section: area = 0.0822 c^2 per section (NACA 0012: 0.0822), the loft between 300, 220, 120 mm
    a = lambda c: 0.0822 * c * c  # noqa: E731
    expected = 400 / 3 * (a(300) + a(220) + math.sqrt(a(300) * a(220))) + 400 / 3 * (a(220) + a(120) + math.sqrt(a(220) * a(120)))
    assert b["volume"] * 1e9 == pytest.approx(expected, rel=0.06)
    assert any(f["surface_type"] != "Plane" for f in pr.faces())


def test_a_flow_study_names_its_parts_and_their_edges(workspace, box_step):
    """The body list of a flow study is the air: the assistant needs the names of the solids (the parts) to edit them."""
    P = workspace
    from simwb import tools

    pr = P.create_project("Flow", "external_aero")
    _wait(pr.import_step(box_step))
    name = pr.parts()[0]["name"]
    assert f"'{name}'" in pr.summary_text() and "Parts (the CAD solids" in pr.summary_text()
    e = json.loads(tools.call("find_edges", pr, {"parallel": "x", "body": name}))
    assert e["count"] == 4 and e["edges"][0]["bodies"] == [name]
    out = json.loads(tools.call("edit_geometry", pr, {"edit": {"op": "fillet", "body": name, "radius_mm": 2,
                                                                "edges": {"parallel": "x"}}}))
    assert out["geometry"] == "rebuilt" and json.loads(tools.call("edit_geometry", pr, {"action": "list"}))["bodies"] == [name]
    assert pr.parts()[0]["volume_cm3"] == pytest.approx(40 - 4 * (1 - math.pi / 4) * 4 * 100 * 1e-3, rel=2e-3)