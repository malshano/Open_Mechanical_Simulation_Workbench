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
    assert c.win.view.region_gizmo.on and c.win.panels["mesh"].adv.xform.active
    c.widget_do("mesh.region.0.xf.mx", "set", "25")
    c.widget_do("mesh.region.0.xf.tz", "set", "40")
    x = c.win.panels["mesh"].adv.xform
    assert x.shift[0] == pytest.approx(0.025) and euler_deg(x.R)[2] == pytest.approx(40.0)
    assert c.engine.state()["mesh"]["regions"][0]["min"] == before["min"]                         # (nothing stored yet)
    c.widget_do("mesh.region.0.xf.cancel", "click")
    assert not x.active and not c.win.view.region_gizmo.on
    assert c.engine.state()["mesh"]["regions"][0] == before
    c.widget_do("mesh.region.0.xform", "set", True)
    c.widget_do("mesh.region.0.xf.mx", "set", "25")
    c.widget_do("mesh.region.0.xf.tz", "set", "40")
    c.widget_do("mesh.region.0.xf.apply", "click")
    got = c.engine.state()["mesh"]["regions"][0]
    assert got["min"][0] == pytest.approx(before["min"][0] + 0.025) and euler_deg(np.array(got["rotation"]))[2] == pytest.approx(40.0)


def test_a_dragged_gizmo_changes_the_preview(aero):
    c = aero
    c.do("mesh.region_add", {"kind": "cylinder"})
    c.widget_do("mesh.region.0.xform", "set", True)
    g, x = c.win.view.region_gizmo, c.win.panels["mesh"].adv.xform
    a, b = np.array(g.project(g.c)[:2]), np.array(g.project(g.arrow_tip(0))[:2])
    start = (a + b) / 2
    assert g.press(*start)
    g.drag_to(*(start + (b - a) * 0.3))
    g.release(*(start + (b - a) * 0.3))
    assert abs(x.shift[0]) > 1e-6 and abs(x.shift[1]) < 1e-9
