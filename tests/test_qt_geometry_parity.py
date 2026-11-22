"""The Geometry step of the Qt window, parity with the web version: the rotation rings (a drag turns a preview, Apply rebuilds), aligning a
face (flat or curved, to a coordinate plane or against another face or the air box), CAD edits, the air volume editor."""
import math

import numpy as np
import pytest
from qt_support import ctl, from_thread  # noqa: F401
from test_qt_theme import best_contrast, visible_controls  # noqa: F401


def wait(c):
    assert from_thread(c.app, lambda: c.wait_idle(300), 320)["idle"]
    c._settle()


def box(c):
    b = max(c.engine.state()["body_list"], key=lambda x: x["volume_cm3"])["bbox"]
    return np.array([b[3] - b[0], b[4] - b[1], b[5] - b[2]])


@pytest.fixture
def plate(ctl):
    ctl.do("study.new", {"name": "Plate", "study": "heat_conduction"})
    ctl.widget_do("geometry.example.0", "click")
    wait(ctl)
    return ctl


@pytest.fixture
def wing(ctl):
    """The bundled wing (curved upper and lower surfaces, two flat tips) in a flutter study."""
    ctl.do("study.new", {"name": "Wing", "study": "aeroelastic_flutter"})
    ctl.widget_do("geometry.example.0", "click")
    wait(ctl)
    return ctl


def rotz(deg):
    t = math.radians(deg)
    return np.array([[math.cos(t), -math.sin(t), 0], [math.sin(t), math.cos(t), 0], [0, 0, 1]])


# ============================================================ the rotation rings
def test_the_rings_are_there_and_say_so(plate):
    c = plate
    g = c.win.view.gizmo
    assert g.on and g.describe()["rings"] and "Drag a ring" in g.describe()["hint"] and "Shift" in g.describe()["hint"]
    hint = c.widget_do("geometry.rings.hint")
    assert "ring" in hint["value"].lower() and "shift" in hint["value"].lower() and "5°" in hint["value"]
    assert c.widget_do("geometry.rings")["text"] == "Hide gizmo"
    for i in ("geometry.rotate.apply", "geometry.rotate.cancel", "geometry.rotate.readout", "geometry.turn.reset"):
        assert c.widget_do(i)["visible"]
    assert not c.widget_do("geometry.rotate.apply")["enabled"] and "drag a ring" in c.widget_do("geometry.rotate.apply")["tooltip"].lower()
    assert not c.widget_do("geometry.turn.reset")["enabled"]            # nothing to reset: it says why
    assert "as the file had it" in c.widget_do("geometry.turn.reset")["tooltip"]


def test_a_drag_on_a_ring_turns_a_live_preview_and_apply_rebuilds(plate):
    c = plate
    before = box(c)
    r = c.do("geometry.ring_drag", {"axis": "z", "degrees": 90})["result"]
    assert r["pending"] and r["steps"][0]["kind"] == "rot" and r["steps"][0]["axis"] == "z" and r["steps"][0]["value"] == pytest.approx(90.0, abs=0.5) and len(r["steps"]) == 1
    assert np.allclose(r["matrix"], rotz(90), atol=0.01)
    assert c.win.view.gizmo.previewing                                   # the drawn parts are turned ...
    assert not c.engine.state()["geometry"].get("rotation")              # ... the model is not, yet
    assert "z" in c.widget_do("geometry.rotate.readout")["value"] and "+90" in c.widget_do("geometry.rotate.readout")["value"]
    assert c.widget_do("geometry.rotate.apply")["enabled"] and c.widget_do("geometry.rotate.cancel")["enabled"]
    assert c.widgets("geometry.rotate.note")["widgets"]                  # the preview notice (an icon box)
    job = c.do("geometry.rotate_apply")["result"]["job"]
    wait(c)
    after = box(c)
    assert abs(after[0] - before[1]) < 1e-4 and abs(after[1] - before[0]) < 1e-4
    assert c.engine.state()["geometry"].get("rotation")
    assert not c.win.panels["geometry"].orient.pending and not c.win.view.gizmo.previewing    # (the rebuilt model is drawn turned)
    assert c.widget_do("geometry.turn.reset")["enabled"]
    c.widget_do("geometry.turn.reset", "click")
    wait(c)
    assert np.allclose(box(c), before, atol=1e-4)


def test_shift_snaps_the_drag_to_5_degrees(plate):
    c = plate
    r = c.do("geometry.ring_drag", {"axis": "x", "degrees": 37, "shift": True})["result"]
    assert r["steps"][0]["axis"] == "x" and r["steps"][0]["value"] == pytest.approx(35.0, abs=1e-6)
    r = c.do("geometry.ring_drag", {"axis": "x", "degrees": 37})["result"]           # free: 35 + 37
    assert r["angles"]["x"] == pytest.approx(72.0, abs=0.6)


def test_turns_add_up_and_cancel_throws_them_away(plate):
    c = plate
    c.widget_do("geometry.turn.x+90", "click")
    c.do("geometry.ring_drag", {"axis": "z", "degrees": 45, "shift": True})
    s = c.do("geometry.state")["result"]
    assert [x["axis"] for x in s["steps"]] == ["x", "z"] and "then" in s["readout"] and "in all" in s["readout"]
    c.widget_do("geometry.turn.axis", "set", "y")
    c.widget_do("geometry.turn.degrees", "set", "-30")
    c.widget_do("geometry.turn.apply", "click")
    s = c.do("geometry.state")["result"]
    assert [x["axis"] for x in s["steps"]] == ["x", "z", "y"] and s["angles"]["y"] == -30
    assert c.win.view.gizmo.previewing
    c.widget_do("geometry.rotate.cancel", "click")
    s = c.do("geometry.state")["result"]
    assert not s["pending"] and not c.win.view.gizmo.previewing and not c.engine.state()["geometry"].get("rotation")
    assert not c.widget_do("geometry.rotate.apply")["enabled"]


def test_the_ring_is_taken_only_near_its_line(plate):
    c = plate
    g = c.win.view.gizmo
    x, y, _ = g.project(g.ring_point(2, 40))
    assert g.pick(x, y) == ("rot", 2)
    assert g.pick(x + 60, y + 60) is None
    assert g.hover_at(x, y) == ("rot", 2) and g.describe()["hover"] == "rot:z"       # lit while the pointer is over it
    assert g.hover_at(x + 60, y + 60) is None and g.describe()["hover"] is None


def test_the_rings_can_be_hidden_and_leaving_the_step_applies_the_turn(plate):
    c = plate
    c.widget_do("geometry.rings", "click")
    assert not c.win.view.gizmo.on and c.widget_do("geometry.rings")["text"] == "Show gizmo"
    c.widget_do("geometry.rings", "click")
    assert c.win.view.gizmo.on
    c.widget_do("geometry.turn.z+90", "click")
    c.do("step.go", {"step": "setup"})                                   # as the web version: going on applies the turn
    wait(c)
    assert c.engine.state()["geometry"].get("rotation")
    assert not c.win.view.gizmo.on                                       # (no rings outside the Geometry step)


# ============================================================ the arrows and the ball: move, on the whole model or on one body
def test_an_arrow_moves_the_preview_and_apply_moves_the_model(plate):
    c = plate
    b0 = max(c.engine.state()["body_list"], key=lambda x: x["volume_cm3"])["bbox"]
    r = c.do("geometry.gizmo_drag", {"handle": "move:x", "amount": 25})["result"]
    assert r["move_mm"][0] == pytest.approx(25.0, abs=0.3) and r["move_mm"][1:] == [0.0, 0.0]
    assert r["gizmo"]["previewing"] and r["gizmo"]["preview_shift_mm"][0] == pytest.approx(25.0, abs=0.3)
    assert "move x" in c.widget_do("geometry.rotate.readout")["value"] and "mm" in c.widget_do("geometry.rotate.readout")["value"]
    assert not c.engine.state()["geometry"].get("translation")
    c.widget_do("geometry.rotate.apply", "click")
    wait(c)
    b1 = max(c.engine.state()["body_list"], key=lambda x: x["volume_cm3"])["bbox"]
    assert b1[0] - b0[0] == pytest.approx(r["move_mm"][0] / 1000, abs=1e-6) and abs(b1[1] - b0[1]) < 1e-9
    assert c.widget_do("geometry.turn.reset")["enabled"]                         # (a move is something to reset too)
    c.widget_do("geometry.turn.reset", "click")
    wait(c)
    b2 = max(c.engine.state()["body_list"], key=lambda x: x["volume_cm3"])["bbox"]
    assert np.allclose(b2, b0, atol=1e-6)


def test_shift_snaps_a_move_to_a_round_step_and_the_ball_moves_in_the_view_plane(plate):
    c = plate
    step = c.win.view.gizmo.snap_mm()
    assert step in (1, 2, 5, 10, 20, 50, 100, 200, 500)
    r = c.do("geometry.gizmo_drag", {"handle": "move:y", "amount": step * 2.4, "shift": True})["result"]
    assert r["move_mm"][1] == pytest.approx(2 * step, abs=1e-6)
    r = c.do("geometry.gizmo_drag", {"handle": "free", "amount": 12})["result"]
    assert np.linalg.norm(np.array(r["move_mm"]) - np.array([0, 2 * step, 0])) == pytest.approx(12.0, abs=0.5)
    assert r["steps"][-1]["kind"] == "free"


def test_typed_fields_drive_the_same_preview(plate):
    c = plate
    c.widget_do("geometry.move.dx", "set", "3")
    c.widget_do("geometry.move.dz", "set", "-4.5")
    c.widget_do("geometry.move.apply", "click")
    assert c.do("geometry.state")["result"]["move_mm"] == [3.0, 0.0, -4.5]
    assert c.win.view.gizmo.previewing and c.widget_do("geometry.rotate.apply")["enabled"]
    c.do("geometry.move_preview", {"dy": 1})                                       # the API does the same
    assert c.do("geometry.state")["result"]["move_mm"] == [3.0, 1.0, -4.5]
    c.widget_do("geometry.rotate.cancel", "click")
    assert not c.win.view.gizmo.previewing and c.do("geometry.state")["result"]["move_mm"] == [0.0, 0.0, 0.0]


def test_the_gizmo_can_act_on_one_body_only(plate):
    c = plate
    bodies = [b for b in c.engine.state()["body_list"] if b["role"] == "solid"]
    assert len(bodies) == 2
    mine, other = bodies[1], bodies[0]
    c.widget_do(f"geometry.body.{mine['id']}.move", "click")
    assert c.widget_do("geometry.target.label")["value"].count(mine["name"]) == 1
    g = c.win.view.gizmo
    assert g.target == mine["id"] and np.allclose(g.center, [(mine["bbox"][k] + mine["bbox"][k + 3]) / 2 for k in range(3)], atol=1e-6)
    c.do("geometry.gizmo_drag", {"handle": "move:z", "amount": 8})
    keys = c.win.view.geo_keys
    mats = {k: np.array([[a.GetUserMatrix().GetElement(i, j) for j in range(4)] for i in range(3)]) for k, a in zip(keys, c.win.view.geo_actors)}
    assert mats[mine["id"]][2, 3] > 1e-3 and abs(mats[other["id"]][2, 3]) < 1e-12   # only that body's picture moved
    c.widget_do("geometry.rotate.apply", "click")
    wait(c)
    after = {b["name"]: b["bbox"] for b in c.engine.state()["body_list"] if b["role"] == "solid"}
    assert after[mine["name"]][2] - mine["bbox"][2] == pytest.approx(0.008, abs=5e-4)
    assert np.allclose(after[other["name"]], other["bbox"], atol=1e-6)               # the other body stayed
    edits = c.engine.project().cad_edits()
    assert edits and edits[-1]["op"] == "move" and edits[-1]["edit"]["bodies"] == [mine["name"]]   # (it is in the CAD edits: it can be removed)
    c.widget_do("geometry.target.all", "click")
    assert c.do("geometry.state")["result"]["acts_on"] == "all"


def test_one_body_can_be_turned_and_pending_changes_go_when_the_target_changes(plate):
    c = plate
    bodies = [b for b in c.engine.state()["body_list"] if b["role"] == "solid"]
    c.do("geometry.acts_on", {"id": bodies[1]["id"]})
    c.do("geometry.gizmo_drag", {"handle": "rot:z", "amount": 90})
    assert c.do("geometry.state")["result"]["pending"]
    c.do("geometry.acts_on", {"body": "all"})
    assert not c.do("geometry.state")["result"]["pending"] and c.widget_do("geometry.target.changed")["visible"]
    c.do("geometry.acts_on", {"id": bodies[1]["id"]})
    c.do("geometry.rotate_preview", {"axis": "z", "degrees": 90})
    c.do("geometry.rotate_apply")
    wait(c)
    e = c.engine.project().cad_edits()[-1]
    assert e["op"] == "rotate" and e["edit"]["bodies"] == [bodies[1]["name"]] and abs(e["edit"]["angle_deg"]) == pytest.approx(90, abs=1e-6)
