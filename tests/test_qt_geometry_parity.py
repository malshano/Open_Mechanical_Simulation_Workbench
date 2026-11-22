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


@pytest.fixture
def aero(ctl):
    """The wing in an external-aerodynamics study: the air box (far-field walls) around it, hidden as the window starts."""
    from qt_support import EXAMPLES
    ctl.do("study.new", {"name": "Aero", "study": "external_aero"})
    ctl.do("geometry.import", {"path": str(EXAMPLES / "airplane_wing.step")})
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


# ============================================================ aligning a face: curved faces, any face, the air box
def _import(c, path):
    c.do("geometry.import", {"path": path})
    wait(c)


def _normal_after(c):
    """Where the picked face's direction points once the previewed turn is applied."""
    s = c.do("geometry.state")["result"]
    R = np.array(s["matrix"]) if s["matrix"] else np.eye(3)
    return R @ np.array(s["align"]["direction"])


def test_a_curved_wing_face_is_aligned_by_its_average_direction(wing):
    c = wing
    faces = c.engine.project().faces()
    big = max(faces, key=lambda f: f["area"])
    assert big["surface_type"] != "Plane"                                            # the largest face of a wing is curved
    c.widget_do("geometry.align.start", "click")
    c.do("geometry.align_pick", {"face": big["id"]})
    a = c.do("geometry.state")["result"]["align"]
    assert a["curved"] and a["mode"] == "average"
    assert "average direction" in c.widget_do("geometry.notice")["text"].lower() or "average direction" in str(c.state()["panels"]["geometry"]["geometry.notice"]).lower()
    assert c.widget_do("geometry.align.mode.average")["enabled"]
    assert c.widget_do("geometry.align.face")["visible"] and "curved" in c.widget_do("geometry.align.face")["value"]
    assert c.win.view.selection == [big["id"]] and c.win.view.gizmo.marker                  # the face is highlighted and its direction drawn
    c.widget_do("geometry.align.to.-z", "click")                                    # lay it on the XY plane, facing down
    assert np.allclose(_normal_after(c), [0, 0, -1], atol=1e-6)
    c.widget_do("geometry.rotate.apply", "click")
    wait(c)
    again = c.engine.project().align_info(max(c.engine.project().faces(), key=lambda f: f["area"])["id"])
    assert again["curved"] and again["nearest"] == "-z" and again["normal"][2] < -0.999   # the wing's big face now looks down


def test_the_normal_at_the_click_differs_from_the_average_on_a_curved_face(wing):
    c = wing
    f = max(c.engine.project().faces(), key=lambda f: f["area"])
    v = np.asarray(f["mesh"]["vertices"]).reshape(-1, 3)
    pt = v[100]
    c.widget_do("geometry.align.start", "click")
    c.do("geometry.align_pick", {"face": f["id"], "x": pt[0], "y": pt[1], "z": pt[2]})
    avg = np.array(c.do("geometry.state")["result"]["align"]["direction"])
    c.widget_do("geometry.align.mode.point", "click")
    loc = np.array(c.do("geometry.state")["result"]["align"]["direction"])
    assert c.do("geometry.state")["result"]["align"]["mode"] == "point"
    assert np.degrees(np.arccos(np.clip(avg @ loc, -1, 1))) > 10                    # the local slope is not the average
    c.widget_do("geometry.align.to.+x", "click")
    assert np.allclose(_normal_after(c), [1, 0, 0], atol=1e-6)
    c.widget_do("geometry.align.mode.average", "click")                              # back to the average: the turn follows
    assert np.allclose(_normal_after(c), [1, 0, 0], atol=1e-6) and np.allclose(c.do("geometry.state")["result"]["align"]["direction"], avg)


def test_a_mesh_face_can_be_aligned_too(ctl, tmp_path):
    import gmsh
    c = ctl
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    b = gmsh.model.occ.addBox(0, 0, 0, 40, 20, 10)
    cy = gmsh.model.occ.addCylinder(20, 10, -1, 0, 0, 12, 3)
    gmsh.model.occ.cut([(3, b)], [(3, cy)])
    gmsh.model.occ.synchronize()
    gmsh.option.setNumber("Mesh.MeshSizeMax", 2)
    gmsh.model.mesh.generate(2)
    gmsh.option.setNumber("Mesh.Binary", 1)
    gmsh.write(str(tmp_path / "part.stl"))
    gmsh.finalize()
    c.do("study.new", {"name": "Mesh", "study": "static_structural"})
    _import(c, str(tmp_path / "part.stl"))
    pr = c.engine.project()
    hole = next(f for f in pr.faces() if f["surface_type"] == "Mesh")
    v = np.asarray(hole["mesh"]["vertices"]).reshape(-1, 3)
    c.widget_do("geometry.align.start", "click")
    c.do("geometry.align_pick", {"face": hole["id"], "x": v[0][0], "y": v[0][1], "z": v[0][2]})
    a = c.do("geometry.state")["result"]["align"]
    assert a["curved"] and a["face"] == hole["id"]
    c.do("geometry.align_mode", {"mode": "point"})
    c.widget_do("geometry.align.to.+z", "click")                                    # the radial direction at that point goes up
    assert np.allclose(_normal_after(c), [0, 0, 1], atol=1e-6)


def test_a_face_of_the_air_box_cannot_be_the_face_to_turn_and_says_why(aero):
    c = aero
    wall = c.engine.project().air_walls()[0]
    c.widget_do("geometry.align.start", "click")
    c.do("geometry.align_pick", {"face": wall["face"]})
    assert not c.do("geometry.state")["result"]["align"]["face"]
    box = c.widget_do("geometry.notice")
    assert box["visible"] and "air" in str(c.state()["panels"]["geometry"]["geometry.notice"]).lower()


def test_the_air_box_walls_are_listed_by_name_and_a_wing_face_can_be_laid_against_one(aero):
    c = aero
    pr = c.engine.project()
    walls = pr.air_walls()
    names = [w["name"] for w in walls]
    assert "upstream (inlet)" in names and "downstream (outlet)" in names and any("bottom" in n for n in names) and any("top" in n for n in names)
    assert not c.win.chk_air.isChecked()                                             # the user has the air box hidden ...
    f = max((f for f in pr.faces() if f.get("kind") == "body"), key=lambda f: f["area"])
    c.widget_do("geometry.align.start", "click")
    c.do("geometry.align_pick", {"face": f["id"]})
    n0 = np.array(c.do("geometry.state")["result"]["align"]["direction"])
    c.widget_do("geometry.align.wall", "click")                                      # Pick the plane
    assert c.win.chk_air.isChecked() and c.win.view.air_on                           # ... it is shown (and fitted) for the pick
    assert c.do("geometry.state")["result"]["align"]["picking_reference"]
    bottom = next(w for w in walls if "bottom" in w["name"])
    for w in walls:
        assert c.widget_do(f"geometry.align.airwall.{w['face']}")["enabled"]
    c.widget_do(f"geometry.align.airwall.{bottom['face']}", "click")
    assert c.win.view.face_tints.get(bottom["face"])                                 # the wall is highlighted
    assert "bottom" in c.widget_do("geometry.align.plane")["value"]
    assert np.allclose(_normal_after(c), bottom["normal"], atol=1e-6)               # the face lies against the wall: its normal is the wall's
    c.widget_do("geometry.align.away", "click")
    assert np.allclose(_normal_after(c), -np.array(bottom["normal"]), atol=1e-6)
    assert c.widget_do("geometry.rotate.apply")["enabled"]
    c.widget_do("geometry.align.done", "click")
    assert not c.win.chk_air.isChecked()                                             # the user's choice of the air box is put back
    c.widget_do("geometry.rotate.cancel", "click")


def test_a_reference_can_be_any_face_of_any_body_and_is_picked_in_the_view_while_the_air_box_is_hidden(aero):
    c = aero
    pr = c.engine.project()
    body = [f for f in pr.faces() if f.get("kind") == "body"]
    big, other = sorted(body, key=lambda f: -f["area"])[:2]
    c.widget_do("geometry.align.start", "click")
    c.do("geometry.align_pick", {"face": big["id"]})
    c.widget_do("geometry.align.body.p0", "click")                                   # (a flow study's solids are its parts)
    assert c.widget_do(f"geometry.align.bodyface.{other['id']}")["visible"]          # its faces are listed
    c.widget_do(f"geometry.align.bodyface.{other['id']}", "click")
    assert c.do("geometry.state")["result"]["align"]["reference"]["face"] == other["id"]
    ref = pr.ref_info(other["id"])
    assert np.allclose(_normal_after(c), ref["normal"], atol=1e-6)
    # the same by a click in the 3D view (a ray at the wall, with the air box hidden when the pick starts)
    c.widget_do("geometry.align.wall", "click")
    wall = next(w for w in pr.air_walls() if "top" in w["name"])
    tri = np.asarray(pr.face_map()[wall["face"]]["mesh"]["vertices"]).reshape(-1, 3)
    p = tri.mean(0)
    hit = c.do("view.pick_ray", {"p0": (p + np.array(wall["normal"]) * 0.5).tolist(), "p1": (p - np.array(wall["normal"]) * 0.01).tolist()})["result"]["hit"]
    assert hit and hit["face"] == wall["face"] and hit["air"]                        # (the air box can be clicked now)
    c.do("geometry.align_pick", {"face": wall["face"]})
    assert np.allclose(_normal_after(c), wall["normal"], atol=1e-6)


def test_the_3d_view_shows_the_rebuilt_geometry_after_apply(plate):
    """(It used to keep drawing the old orientation: the view redrew only when the geometry's status changed.)"""
    c = plate

    def drawn():
        pts = np.vstack([np.asarray(v, float).reshape(-1, 3) for _, v, _, _ in c.win.view._solid_parts])
        return pts.max(0) - pts.min(0)
    before = drawn()
    c.widget_do("geometry.turn.z+90", "click")
    c.widget_do("geometry.rotate.apply", "click")
    wait(c)
    after = drawn()
    assert abs(after[0] - before[1]) < 1e-6 and abs(after[1] - before[0]) < 1e-6 and abs(before[0] - before[1]) > 1e-3
    c.do("geometry.move_preview", {"dz": 20})
    c.do("geometry.rotate_apply")
    wait(c)
    pts = np.vstack([np.asarray(v, float).reshape(-1, 3) for _, v, _, _ in c.win.view._solid_parts])
    bb = c.engine.state()["geometry"]["info"]["parts_bbox"]
    assert abs(pts[:, 2].min() - bb[2]) < 1e-6                                    # drawn where the engine has it now


def test_a_hovered_air_wall_does_not_stay_lit_when_the_air_volume_is_hidden(aero):
    c = aero
    wall = c.engine.project().air_walls()[0]["face"]
    c.do("view.air_box", {"visible": True})
    c.win.view._set_hover(wall)                                                      # the pointer is over a wall of the air volume
    assert c.win.view.hover_id == wall and c.win.view.mark_actors
    c.do("view.air_box", {"visible": False})
    assert c.win.view.hover_id == -1                                                 # (its highlight is as large as the air volume)


# ============================================================ CAD edits
def test_cad_edits_are_listed_added_undone_and_removed(plate):
    c = plate
    assert not c.widget_do("geometry.cad.undo")["enabled"] and "no edits" in c.widget_do("geometry.cad.undo")["tooltip"].lower()
    assert not c.widget_do("geometry.cad.clear")["enabled"]
    n0 = c.engine.state()["face_count"]
    c.widget_do("geometry.cad.toggle", "click")                                       # the add form
    c.widget_do("geometry.cad.op", "set", "Drill a hole")
    pr = c.engine.project()
    bb = pr.parts()[0]
    for k, vals in (("at", (50, 30, 3)), ("direction", (0, 0, -1))):
        for ax, v in zip("xyz", vals):
            c.widget_do(f"geometry.cad.f.{k}.{ax}", "set", str(v))
    c.widget_do("geometry.cad.f.diameter_mm", "set", "6")
    c.widget_do("geometry.cad.add", "click")
    wait(c)
    edits = c.engine.project().cad_edits()
    assert len(edits) == 1 and edits[0]["op"] == "hole" and edits[0]["edit"]["diameter_mm"] == 6
    assert c.widget_do("geometry.cad.edit.1")["value"].startswith("hole")
    assert c.engine.state()["face_count"] > n0                                        # the hole has faces
    assert c.widget_do("geometry.cad.undo")["enabled"] and c.widget_do("geometry.cad.clear")["enabled"]
    c.widget_do("geometry.cad.undo", "click")
    wait(c)
    assert not c.engine.project().cad_edits() and c.engine.state()["face_count"] == n0
    r = c.do("geometry.cad_edit", {"action": "add", "edit": {"op": "move", "by": [5, 0, 0]}})["result"]
    wait(c)
    r = c.do("geometry.cad_edit", {"action": "add", "edit": {"op": "move", "by": [0, 5, 0]}})["result"]
    wait(c)
    assert len(c.engine.project().cad_edits()) == 2 and c.widget_do("geometry.cad.remove.1")["enabled"]
    c.widget_do("geometry.cad.remove.1", "click")                                     # the first goes, the second is replayed alone
    wait(c)
    assert [e["edit"]["by"] for e in c.engine.project().cad_edits()] == [[0, 5, 0]]
    c.widget_do("geometry.cad.clear", "click")
    wait(c)
    assert not c.engine.project().cad_edits()


def test_a_cad_edit_that_cannot_be_made_says_why_and_the_model_stays(plate):
    c = plate
    c.widget_do("geometry.cad.toggle", "click")
    c.widget_do("geometry.cad.op", "set", "Add a box")
    c.widget_do("geometry.cad.f.name", "set", "Block")
    c.widget_do("geometry.cad.f.min.x", "set", "0")
    c.widget_do("geometry.cad.f.min.y", "set", "0")
    c.widget_do("geometry.cad.f.min.z", "set", "0")
    c.widget_do("geometry.cad.f.size.x", "set", "10")
    c.widget_do("geometry.cad.f.size.y", "set", "10")
    c.widget_do("geometry.cad.f.size.z", "set", "-5")                                 # a negative size
    c.widget_do("geometry.cad.add", "click")
    box = c.widget_do("geometry.cad.notice")
    assert box["visible"] and "did not work" in str(c.state()["panels"]["geometry"]["geometry.cad.notice"])
    assert not c.engine.project().cad_edits()


# ============================================================ the air volume
def test_the_air_volume_editor_shape_sizes_apply_fit_and_show_all(aero):
    c = aero
    sp = c.engine.state()["geometry"]["info"]["domain"]["spec"]
    assert sp["shape"] == "box" and c.widget_do("geometry.air.shape")["value"]["text"].startswith("Box")
    assert not c.widget_do("geometry.air.apply")["enabled"] and "size" in c.widget_do("geometry.air.apply")["tooltip"].lower()
    c.widget_do("geometry.air.shape", "set", "Cylinder along the flow")
    wait(c)
    sp = c.engine.state()["geometry"]["info"]["domain"]["spec"]
    assert sp["shape"] == "cylinder" and c.widget_do("geometry.air.size.radius")["visible"]
    names = [w["name"] for w in c.engine.project().air_walls()]
    assert any(n.startswith("cylinder end, upstream") for n in names) and any(n.startswith("cylinder end, downstream") for n in names)
    assert any("cylinder wall" in n for n in names)                                      # the curved wall is listed too (by a point of it)
    r0 = sp["radius"]
    c.widget_do("geometry.air.size.radius", "set", str(r0 * 1000 * 1.5))
    assert c.widget_do("geometry.air.apply")["enabled"]
    c.widget_do("geometry.air.apply", "click")
    wait(c)
    assert c.engine.state()["geometry"]["info"]["domain"]["spec"]["radius"] == pytest.approx(r0 * 1.5, rel=1e-6)
    c.widget_do("geometry.air.fit", "click")
    wait(c)
    assert c.engine.state()["geometry"]["info"]["domain"]["spec"]["radius"] == pytest.approx(r0, rel=1e-6)
    c.widget_do("geometry.air.shape", "set", "Box")
    wait(c)
    assert not c.win.chk_air.isChecked()
    c.widget_do("geometry.air.showall", "click")
    assert c.win.chk_air.isChecked() and c.win.view.air_on                              # (shown, translucent, and fitted)


def test_the_domain_size_presets_and_fit_with_these(aero):
    c = aero
    for name, vals in (("minimal", (1, 2, 1)), ("compact", (3, 6, 3)), ("standard", (5, 10, 5)), ("large", (8, 16, 8))):
        assert c.widget_do(f"geometry.air.preset.{name}")["enabled"]
    c.widget_do("geometry.air.preset.compact", "click")
    wait(c)
    assert c.engine.state()["domain"]["farfield"] == {"upstream": 3, "downstream": 6, "side": 3}
    assert c.widget_do("geometry.air.ff.upstream")["value"] == "3"
    c.widget_do("geometry.air.ff.side", "set", "4")
    c.widget_do("geometry.air.ff.apply", "click")
    wait(c)
    assert c.engine.state()["domain"]["farfield"]["side"] == 4


def test_a_cooling_study_chooses_between_a_body_and_an_air_box(ctl):
    c = ctl
    c.do("study.example", {"study": "cht_enclosure"})
    wait(c)
    assert c.widget_do("geometry.air.mode")["visible"]
    mode = c.engine.state()["domain"].get("enclosure_mode") or "body"
    if mode == "body":
        assert c.widget_do("geometry.air.body")["visible"] and not c.widgets("geometry.air.shape")["widgets"]
        c.widget_do("geometry.air.mode", "set", "Create an air box around the parts")
        wait(c)
    assert c.engine.state()["domain"]["enclosure_mode"] == "auto_box" and c.widget_do("geometry.air.shape")["visible"]
    assert c.widget_do("geometry.air.flow")["visible"]


# ============================================================ the parts of a flow study
def test_a_part_of_a_flow_study_can_be_hidden(aero):
    c = aero
    n = c.win.view.geometry_info["faces"]
    assert c.widget_do("geometry.part.0.eye")["text"] == "Hide"
    c.widget_do("geometry.part.0.eye", "click")
    assert c.win.hidden_parts and c.widget_do("geometry.part.0.eye")["text"] == "Show"
    assert c.win.view.geometry_info["faces"] < n                                        # (its faces are not drawn)
    c.widget_do("geometry.part.0.eye", "click")
    assert not c.win.hidden_parts and c.win.view.geometry_info["faces"] == n


# ============================================================ the web version's flow: the camera stays on the model
def test_after_the_first_click_the_plane_pick_starts_by_itself_and_the_camera_stays_on_the_model(aero):
    c = aero
    pr = c.engine.project()
    view = c.win.view
    f = max((f for f in pr.faces() if f.get("kind") == "body"), key=lambda f: f["area"])
    cam0 = view.camera()
    assert not c.win.chk_air.isChecked()
    c.widget_do("geometry.align.start", "click")
    assert "click the face of the model" in c.widget_do("view.banner")["value"].lower()            # the bar under the view says what to do
    assert c.widget_do("view.banner.button")["text"] == "Done"
    c.do("geometry.align_pick", {"face": f["id"]})
    st = c.do("geometry.state")["result"]["align"]
    assert st["picking_reference"]                                                                    # (no extra 'Pick the plane' press)
    assert "now click the plane" in c.widget_do("view.banner")["value"].lower() and "air volume" in c.widget_do("view.banner")["value"]
    assert c.win.chk_air.isChecked() and view.air_on                                                  # the air volume is shown ...
    assert view.camera()["position"] == cam0["position"] and view.camera()["focal_point"] == cam0["focal_point"]   # ... the camera did not move
    # a click on a wall previews the turn at once
    wall = next(w for w in pr.air_walls() if "top" in w["name"])
    assert c.widget_do("geometry.align.summary")["visible"]
    c.do("geometry.align_pick", {"face": wall["face"]})
    s = c.do("geometry.state")["result"]
    assert s["pending"] and np.allclose(_normal_after(c), wall["normal"], atol=1e-6)
    assert "→ Air volume: top" in c.widget_do("geometry.align.summary")["value"] and "facing the wall" in c.widget_do("geometry.align.summary")["value"]
    assert view.camera()["position"] == cam0["position"]
    c.widget_do("geometry.align.away", "click")
    assert "facing away" in c.widget_do("geometry.align.summary")["value"]
    c.widget_do("geometry.align.done", "click")
    assert not c.win.chk_air.isChecked() and c.widget_do("view.banner")["value"].lower().startswith("drag a ring")   # (the air toggle is put back; the gizmo hint is back)
    c.widget_do("geometry.rotate.cancel", "click")


def test_apply_and_cancel_are_at_the_top_of_the_panel_in_sight(plate):
    c = plate
    top = c.widget_do("geometry.rotate.apply")["rect"][1]
    assert top < c.widget_do("geometry.import")["rect"][1] + 5 and c.widget_do("geometry.rotate.cancel")["rect"][1] == top
    assert c.widget_do("geometry.rotate.readout")["rect"][1] < c.widget_do("geometry.turn.x-90")["rect"][1]
    c.widget_do("geometry.align.start", "click")
    f = next(f for f in c.engine.project().faces() if f["surface_type"] == "Plane" and f.get("kind") not in ("farfield", "enclosure") and not f.get("interface"))
    c.do("view.pick_face", {"id": f["id"]})
    assert c.widget_do("geometry.align.summary")["rect"][1] < c.widget_do("geometry.import")["rect"][1]       # the align box comes first too
    assert c.widget_do("geometry.align.face")["rect"][1] < c.widget_do("geometry.import")["rect"][1]


def test_the_gizmo_stays_inside_the_picture_and_the_hint_has_a_bar_of_its_own(wing):
    c = wing
    g = c.win.view.gizmo
    w, h = g._size()
    assert "drag a ring" in c.widget_do("view.banner")["value"].lower() and "5°" in c.widget_do("view.banner")["value"]
    r_px = max(np.hypot(g.project(p)[0] - g.project(g.c)[0], g.project(p)[1] - g.project(g.c)[1]) for p in g.ring_points(0) )
    assert r_px <= 0.32 * min(w, h) + 1                                               # (at most 30 % of the picture's smaller side)
    for a in range(3):
        for p in g.ring_points(a):
            x, y, _ = g.project(p)
            assert -1 <= x <= w + 1 and -1 <= y <= h + 1                              # every ring point is inside the picture
    assert g.arrow_length < g._r


def test_the_gizmo_sits_on_the_parts_not_on_the_duct_that_is_the_air(ctl):
    """(The cooling example's duct is a body of the file that is the air: it pulled the turning point away from the chip and heatsink.)"""
    c = ctl
    c.do("study.example", {"study": "cht_enclosure"})
    wait(c)
    g = c.win.view.gizmo
    bb = c.engine.state()["geometry"]["info"]["parts_bbox"]
    assert g.on and all(bb[i] <= g.center[i] <= bb[i + 3] for i in range(3)), (g.center.tolist(), bb)
    w, h = g._size()
    x, y, _ = g.project(g.c)
    assert 0 < x < w and 0 < y < h                                                     # the gizmo is in the picture


def test_a_face_between_a_part_and_the_duct_that_is_the_air_can_be_aligned(ctl):
    """(The heatsink's fins are faces between the heatsink and the air body: they were refused as 'contact faces'.)"""
    c = ctl
    c.do("study.example", {"study": "cht_enclosure"})
    wait(c)
    pr = c.engine.project()
    roles = {b["id"]: b["role"] for b in pr.bodies()}
    fin = next(f for f in pr.faces() if f.get("interface") and f["surface_type"] == "Plane" and sorted(roles[v] for v in f["volumes"]) == ["fluid", "solid"]
               and f["area"] > 1e-4)
    d = pr.align_info(fin["id"])
    body = next(b for b in pr.bodies() if b["id"] in fin["volumes"] and b["role"] == "solid")
    assert np.dot(np.array(d["normal"]), np.array(fin["centroid"]) - np.array(body["centroid"])) > 0      # out of the part, into the air
    both = [f for f in pr.faces() if sorted(roles.get(v) for v in f["volumes"]) == ["solid", "solid"]]
    assert both
    with pytest.raises(ValueError, match="contact face between two parts"):
        pr.align_info(both[0]["id"])                                                    # (a real contact face between two parts is still refused ...)
    assert pr.ref_info(both[0]["id"])["contact"]                                          # ... but can be the plane to lay a face against
    c.widget_do("geometry.align.start", "click")
    c.do("geometry.align_pick", {"face": fin["id"]})
    assert c.do("geometry.state")["result"]["align"]["face"] == fin["id"]
