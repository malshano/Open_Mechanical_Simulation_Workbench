"""Refinement regions that are moved and turned (issue #7): the maths, the engine's acceptance of a turned box and the Mesh step's Move / turn session."""
import math

import numpy as np
import pytest
from qt_support import ctl, from_thread  # noqa: F401

from simwb import regions
from simwb.gmsh_worker import box_size_expr, cylinder_size_expr
from simwb.qt.regionxform import euler_deg, rotation_from_euler


def rz(deg):
    t = math.radians(deg)
    return np.array([[math.cos(t), -math.sin(t), 0], [math.sin(t), math.cos(t), 0], [0, 0, 1.0]])


BOX = {"name": "b", "min": [0, 0, 0], "max": [2, 1, 1], "size": 0.01}
CYL = {"name": "c", "shape": "cylinder", "start": [0, 0, 0], "end": [2, 0, 0], "radius": 0.5, "min": [0, -0.5, -0.5], "max": [2, 0.5, 0.5], "size": 0.01}


def test_a_box_turns_about_its_middle_and_moves():
    r = regions.moved_and_turned(BOX, rz(90), [0, 0, 0.5])
    assert np.allclose(r["min"], [0, 0, 0.5]) and np.allclose(r["max"], [2, 1, 1.5])        # (min / max: the box before the turn, moved)
    lo, hi = regions.bounds(r)
    assert np.allclose(lo, [0.5, -0.5, 0.5]) and np.allclose(hi, [1.5, 1.5, 1.5])             # (the box round the turned one)
    assert regions.is_turned(r) and not regions.is_turned(BOX)
    back = regions.moved_and_turned(r, rz(-90), [0, 0, -0.5])
    assert "rotation" not in back and np.allclose(back["min"], BOX["min"])


def test_a_cylinder_turns_by_moving_its_end_points():
    r = regions.moved_and_turned(CYL, rz(90), [0, 0, 0])
    assert np.allclose(r["start"], [1, -1, 0]) and np.allclose(r["end"], [1, 1, 0])           # (about its middle (1, 0, 0))
    assert np.allclose(r["min"], [0.5, -1, -0.5]) and np.allclose(r["max"], [1.5, 1, 0.5])
    assert "rotation" not in r


def test_rotations_are_checked():
    assert regions.check_rotation(np.eye(3)) is None
    assert regions.check_rotation(rz(30)) is not None
    with pytest.raises(ValueError, match="proper rotation"):
        regions.check_rotation(np.diag([1, 1, -1.0]))                                            # (a mirror is not a turn)
    with pytest.raises(ValueError):
        regions.check_rotation([[1, 2], [3, 4]])


def test_euler_angles_round_trip():
    for a in ((0, 0, 0), (20, -30, 45), (-70, 10, 170)):
        assert np.allclose(euler_deg(rotation_from_euler(*a)), a, atol=1e-6)


def _eval(expr, x, y, z):
    # test only: evaluates the size expression that box_size_expr / cylinder_size_expr generate (numbers, + - * ^, max, min, sqrt) - nothing external
    return float(eval(expr.replace("^", "**").replace("max", "np.maximum").replace("min", "np.minimum").replace("sqrt", "np.sqrt"),
                      {"np": np, "x": x, "y": y, "z": z}))


def test_the_turned_box_size_expression_is_fine_inside_and_coarse_far_away():
    r = regions.moved_and_turned(BOX, rz(90), [0, 0, 0])
    e = box_size_expr(r["min"], r["max"], r["rotation"], 0.01, 0.1, 0.08)
    assert _eval(e, 1.0, 0.5, 0.5) == pytest.approx(0.01)                                       # (the middle)
    assert _eval(e, 1.0, 0.5 + 0.9, 0.5) == pytest.approx(0.01)                                  # (inside only because the box is turned: it is 2 long in y now)
    assert _eval(e, 1.9, 0.5, 0.5) > 0.05                                                        # (outside: it was inside before the turn)
    assert _eval(e, 20, 20, 20) == pytest.approx(0.1)
    c = cylinder_size_expr([0, 0, 0], [1, 0, 0], 0.5, 0.01, 0.1, 0.08)
    assert _eval(c, 0.5, 0, 0) == pytest.approx(0.01)


@pytest.fixture
def aero(ctl, box_step):
    ctl.do("study.new", {"name": "Flow", "study": "external_aero"})
    ctl.do("geometry.import", {"path": str(box_step)})
    assert from_thread(ctl.app, lambda: ctl.wait_idle(300), 320)["idle"]
    ctl._settle()
    ctl.do("step.go", {"step": "mesh"})
    return ctl


def test_the_engine_keeps_a_turned_box_and_refuses_a_bad_turn(aero):
    pr = aero.engine.project()
    pr.set_mesh(regions=[{**BOX, "rotation": rz(30).tolist()}])
    got = aero.engine.state()["mesh"]["regions"][0]
    assert np.allclose(got["rotation"], rz(30)) and got["min"] == BOX["min"]
    pr.set_mesh(regions=[{**BOX, "rotation": np.eye(3).tolist()}])
    assert "rotation" not in aero.engine.state()["mesh"]["regions"][0]                           # (no turn: nothing stored)
    with pytest.raises(ValueError):
        pr.set_mesh(regions=[{**BOX, "rotation": np.diag([1, 1, -1.0]).tolist()}])


def test_move_and_turn_session_previews_applies_and_cancels(aero):
    c = aero
    c.do("mesh.region_add", {"kind": "box"})
    before = c.engine.state()["mesh"]["regions"][0]
    c.widget_do("mesh.region.0.xform", "set", True)
    assert c.win.view.handles.on and c.win.panels["mesh"].adv.xform.active
    c.widget_do("mesh.region.0.xf.mx", "set", "25")
    c.widget_do("mesh.region.0.xf.tz", "set", "40")
    x = c.win.panels["mesh"].adv.xform
    assert x.shift[0] == pytest.approx(0.025) and euler_deg(x.R)[2] == pytest.approx(40.0)
    assert c.engine.state()["mesh"]["regions"][0]["min"] == before["min"]                         # (nothing stored yet)
    c.widget_do("mesh.region.0.xf.cancel", "click")
    assert not x.active and not c.win.view.handles.on
    assert c.engine.state()["mesh"]["regions"][0] == before
    c.widget_do("mesh.region.0.xform", "set", True)
    c.widget_do("mesh.region.0.xf.mx", "set", "25")
    c.widget_do("mesh.region.0.xf.tz", "set", "40")
    c.widget_do("mesh.region.0.xf.apply", "click")
    got = c.engine.state()["mesh"]["regions"][0]
    assert got["min"][0] == pytest.approx(before["min"][0] + 0.025) and euler_deg(np.array(got["rotation"]))[2] == pytest.approx(40.0)


def test_the_handles_of_a_region_are_the_shared_ones_and_a_drag_of_each_kind_changes_the_preview(aero):
    c = aero
    c.do("mesh.region_add", {"kind": "cylinder"})
    c.widget_do("mesh.region.0.xform", "set", True)
    hs, x = c.win.view.handles, c.win.panels["mesh"].adv.xform
    kinds = {k: s["kind"] for k, s in hs.specs.items()}
    assert kinds == {"move": "point", "turn.x": "ring", "turn.y": "ring", "turn.z": "ring", "face.len.0": "face", "face.len.1": "face", "face.rad.0": "face"}
    p0 = np.array(hs.specs["move"]["point"])
    hs.simulate_move("move", p0 + [0.01, 0, 0], part="axis0")                       # (the arrow along x: only x changes)
    assert x.shift[0] == pytest.approx(0.01, abs=1e-6) and abs(x.shift[1]) < 1e-9 and abs(x.shift[2]) < 1e-9
    assert np.allclose(hs.specs["move"]["point"], p0 + [0.01, 0, 0])               # (the handle went with the region)
    got = hs.simulate_move("turn.z", 30.0)                                            # (a ring: 30 degrees about z)
    assert got == pytest.approx(30.0, abs=0.05) and euler_deg(x.R)[2] == pytest.approx(30.0, abs=0.05)
    before = regions.size_of(x.preview())
    hs.simulate_move("face.len.1", 0.02)                                              # (the face at the end of the axis: 20 mm longer)
    assert regions.size_of(x.preview())[0] == pytest.approx(before[0] + 0.02, abs=1e-6)
    assert x.shift[0] == pytest.approx(0.01, abs=1e-6)                                # (the move is kept)


# ------------------------------------------------------------------------------------------------------------ resizing a region
def test_a_box_face_moves_out_and_the_opposite_face_stays():
    g = np.zeros((3, 2))
    g[0, 1], g[1, 0] = 0.5, 0.25
    r = regions.grown(BOX, g)
    assert np.allclose(r["min"], [0, -0.25, 0]) and np.allclose(r["max"], [2.5, 1, 1])
    assert regions.size_of(r) == pytest.approx([2.5, 1.25, 1.0])


def test_a_turned_box_grows_along_its_own_axes():
    turned = regions.moved_and_turned(BOX, rz(90), [0, 0, 0])
    handle = next(h for h in regions.face_handles(turned) if h["key"] == (0, 1))
    assert np.allclose(handle["dir"], [0, 1, 0])                                                  # (its +x face now looks along world y)
    g = np.zeros((3, 2))
    g[0, 1] = 0.5
    r = regions.grown(turned, g)
    assert regions.size_of(r) == pytest.approx([2.5, 1.0, 1.0])
    lo, hi = regions.bounds(r)
    assert hi[1] - lo[1] == pytest.approx(2.5) and hi[0] - lo[0] == pytest.approx(1.0)           # (it got longer in y, not in x)


def test_a_cylinder_grows_at_its_ends_and_in_radius_and_nothing_gets_smaller_than_the_minimum():
    g = np.zeros((3, 2))
    g[0, 1], g[1, 0] = 1.0, -0.2
    r = regions.grown(CYL, g)
    assert np.allclose(r["end"], [3, 0, 0]) and r["radius"] == pytest.approx(0.3)
    small = regions.grown(BOX, np.array([[0, -5.0], [0, 0], [0, 0]]))
    assert regions.size_of(small)[0] == pytest.approx(regions.MIN_SIZE)
    assert [h["key"] for h in regions.face_handles(CYL)] == [("len", 0), ("len", 1), ("rad", 0)]
    assert len(regions.face_handles(BOX)) == 6


def test_resizing_in_the_session_by_handle_by_size_and_by_percentage(aero):
    c = aero
    c.do("mesh.region_add", {"kind": "box"})
    before = c.engine.state()["mesh"]["regions"][0]
    c.widget_do("mesh.region.0.xform", "set", True)
    x, hs = c.win.panels["mesh"].adv.xform, c.win.view.handles
    assert sum(1 for sp in hs.specs.values() if sp["kind"] == "face") == 6
    hs.simulate_move("face.0.1", 0.05)
    assert x.grow[0, 1] == pytest.approx(0.05, abs=1e-6) and not x.grow[0, 0] and not x.grow[1].any()          # (only that face moved)
    assert c.engine.state()["mesh"]["regions"][0] == before                                       # (nothing stored yet)
    old = regions.size_of(before)
    c.widget_do("mesh.region.0.xf.sy", "set", "500")
    assert float(c.widget_do("mesh.region.0.xf.sy")["value"]) == pytest.approx(500)
    c.widget_do("mesh.region.0.xf.reset", "click")
    c.widget_do("mesh.region.0.xf.sc", "set", "200")
    assert regions.size_of(x.preview()) == pytest.approx([2 * s for s in old])
    c.widget_do("mesh.region.0.xf.apply", "click")
    assert regions.size_of(c.engine.state()["mesh"]["regions"][0]) == pytest.approx([2 * s for s in old])
    assert not hs.on


def test_the_resize_handles_go_when_the_session_ends(aero):
    c = aero
    c.do("mesh.region_add", {"kind": "cylinder"})
    c.widget_do("mesh.region.0.xform", "set", True)
    assert [k for k in c.win.view.handles.specs if k.startswith("face")] == ["face.len.0", "face.len.1", "face.rad.0"]
    c.widget_do("mesh.region.0.xf.cancel", "click")
    assert not c.win.view.handles.on


def test_gmsh_makes_small_cells_in_the_turned_box_and_not_in_the_box_round_it():
    """The size field of a turned box, in a real (tiny) gmsh mesh: cells inside the turned box are fine, cells in the corners of the axis-aligned box round it are not."""
    import gmsh
    turned = regions.moved_and_turned({"name": "t", "min": [0.3, 0.45, 0.45], "max": [0.7, 0.55, 0.55], "size": 0.02}, rz(45), [0, 0, 0])
    expr = box_size_expr(turned["min"], turned["max"], turned["rotation"], 0.02, 0.2, 0.1)
    gmsh.initialize(["-nopopup"])
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("t")
        gmsh.model.occ.addBox(0, 0, 0, 1, 1, 1)
        gmsh.model.occ.synchronize()
        f = gmsh.model.mesh.field.add("MathEval")
        gmsh.model.mesh.field.setString(f, "F", expr)
        gmsh.model.mesh.field.setAsBackgroundMesh(f)
        for k in ("Mesh.MeshSizeFromPoints", "Mesh.MeshSizeExtendFromBoundary", "Mesh.MeshSizeFromCurvature"):
            gmsh.option.setNumber(k, 0)
        gmsh.option.setNumber("Mesh.MeshSizeMax", 0.2)
        gmsh.model.mesh.generate(3)
        _t, coords, _p = gmsh.model.mesh.getNodes()
    finally:
        gmsh.finalize()
    P = np.asarray(coords).reshape(-1, 3)
    R = np.asarray(turned["rotation"])
    local = (P - 0.5) @ R                                              # (R^T (x - c): the points in the box's own axes)
    inside = (np.abs(local) <= np.array([0.2, 0.05, 0.05]) + 0.02).all(axis=1)
    corner = (np.abs(P[:, 0] - 0.5) > 0.15) & (np.abs(P[:, 1] - 0.5) > 0.15) & (np.abs(P[:, 0] - 0.5) < 0.25) & (np.abs(P[:, 1] - 0.5) < 0.25) \
        & (np.abs(P[:, 2] - 0.5) < 0.05)                              # (inside the box round the turned one, far from the turned box)
    assert inside.sum() > 100                                          # (many nodes: the turned box is meshed finely)
    assert inside.sum() > 5 * max(int(corner.sum()), 1) or corner.sum() == 0


def test_a_turned_region_is_the_same_after_the_study_is_closed_and_opened_again(aero, tmp_path):
    """Save, close and reopen the study: the turned, moved and resized region comes back unchanged (project.json keeps min, max and the rotation)."""
    c = aero
    c.do("mesh.region_add", {"kind": "box"})
    c.widget_do("mesh.region.0.xform", "set", True)
    c.widget_do("mesh.region.0.xf.mx", "set", "12")
    c.widget_do("mesh.region.0.xf.tz", "set", "35")
    c.widget_do("mesh.region.0.xf.sx", "set", "300")
    c.widget_do("mesh.region.0.xf.apply", "click")
    saved = c.engine.state()["mesh"]["regions"][0]
    assert saved["rotation"] is not None
    folder = str(c.engine.project().dir)
    c.do("study.new", {"name": "Other", "study": "external_aero"})                      # (the first study is closed: it was saved on the way)
    c.do("study.open_folder", {"path": folder})
    again = c.engine.state()["mesh"]["regions"][0]
    assert again == saved
    assert np.allclose(again["rotation"], rz(35)) or euler_deg(np.array(again["rotation"]))[2] == pytest.approx(35.0)
    c.widget_do("mesh.region.0.xform", "set", True)                                     # (and the session starts from it: no change pending, the handles are on it)
    assert not c.win.panels["mesh"].adv.xform.pending() and c.win.view.handles.on
    assert regions.size_of(c.win.panels["mesh"].adv.xform.preview())[0] == pytest.approx(0.3)


def test_the_cell_estimate_of_a_turned_box_is_that_of_the_box():
    from simwb.qt.meshtools import region_cells
    turned = regions.moved_and_turned(BOX, rz(37), [0, 0, 0])
    assert region_cells(turned)[0] == pytest.approx(region_cells(BOX)[0])               # (turning does not change the volume: the estimate is of the box in its own axes, not the box round it)
    lo, hi = regions.bounds(turned)
    assert np.prod(np.subtract(hi, lo)) > 1.2 * np.prod(np.subtract(BOX["max"], BOX["min"]))     # (the box round it is larger: using it would over-count)


def test_a_region_turns_and_moves_with_the_model_it_was_placed_on(aero):
    """'Relative to the model': turn or move the whole model in the Geometry step and the regions go with it (as the rotating zones and rotors do)."""
    pr = aero.engine.project()
    pr.set_mesh(regions=[{**BOX, "name": "tilted", "min": [0.02, 0.0, 0.0], "max": [0.06, 0.01, 0.01], "rotation": rz(30).tolist()},
                         {"name": "cyl", "shape": "cylinder", "start": [0.0, 0.0, 0.0], "end": [0.05, 0.0, 0.0], "radius": 0.01, "size": 0.005}])
    before = [dict(r) for r in aero.engine.state()["mesh"]["regions"]]
    c = np.asarray(pr._parts_center(), float)
    D = rz(90)
    j = pr.transform_geometry(matrix=D.tolist())                                           # (the Geometry step's turn: about the middle of the parts)
    assert j.wait(300) and j.status == "done", j.error
    after = aero.engine.state()["mesh"]["regions"]
    box0, box1 = before[0], after[0]
    mid0 = (np.asarray(box0["min"]) + np.asarray(box0["max"])) / 2
    mid1 = (np.asarray(box1["min"]) + np.asarray(box1["max"])) / 2
    assert mid1 == pytest.approx(D @ (mid0 - c) + c, abs=1e-9)                              # (the middle went with the model: turned about the middle of the parts)
    assert np.asarray(box1["max"]) - np.asarray(box1["min"]) == pytest.approx(np.asarray(box0["max"]) - np.asarray(box0["min"]))
    assert np.asarray(box1["rotation"]) == pytest.approx(D @ rz(30))                         # (and it kept its place against the model: turned by the same 90 degrees)
    cyl0, cyl1 = before[1], after[1]
    assert np.asarray(cyl1["start"]) == pytest.approx(D @ (np.asarray(cyl0["start"]) - c) + c, abs=1e-9)
    assert np.asarray(cyl1["end"]) == pytest.approx(D @ (np.asarray(cyl0["end"]) - c) + c, abs=1e-9)
    j = pr.transform_geometry(shift=[0.01, 0.0, 0.0])                                           # (a move of the whole model)
    assert j.wait(300) and j.status == "done", j.error
    moved = aero.engine.state()["mesh"]["regions"]
    assert np.asarray(moved[0]["min"]) == pytest.approx(np.asarray(box1["min"]) + [0.01, 0, 0], abs=1e-9)
    assert np.asarray(moved[1]["end"]) == pytest.approx(np.asarray(cyl1["end"]) + [0.01, 0, 0], abs=1e-9)
    assert regions.transformed(BOX, np.eye(3), [0, 0, 0])["min"] == BOX["min"] and "rotation" not in regions.transformed(BOX, np.eye(3))


def test_the_turn_of_a_region_goes_back_to_none_when_the_model_is_turned_back():
    t = regions.transformed({**BOX, "rotation": rz(30).tolist()}, rz(-30))
    assert "rotation" not in t
