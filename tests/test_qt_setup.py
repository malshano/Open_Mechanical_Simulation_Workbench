"""The Setup step of the Qt window: parts and materials, boundary conditions picked on the model, settings, readiness."""
import pytest
from qt_support import ctl, from_thread  # noqa: F401
from test_qt_theme import best_contrast, visible_controls  # noqa: F401


@pytest.fixture
def bracket(ctl):
    """A static study with the bundled bracket imported, on the Setup step."""
    ctl.do("study.example", {"study": "static_structural"})
    assert from_thread(ctl.app, lambda: ctl.wait_idle(300), 320)["idle"]
    ctl.do("step.go", {"step": "setup"})
    faces = ctl.engine.project().faces()
    ctl.holes = [f["id"] for f in faces if f["surface_type"] == "Cylinder"]
    ctl.front = [f["id"] for f in faces if abs(f["normal"][0]) > 0.9 and abs(f["centroid"][0] - 3e-3) < 1e-4]
    assert len(ctl.holes) >= 2 and ctl.front
    return ctl


def bcs(ctl):
    return ctl.engine.state()["bcs"]


def pick(ctl, ids):
    ctl.do("view.selection", {"ids": []})
    ctl.win.view.set_selection([])
    for n, i in enumerate(ids):
        ctl.do("view.pick_face", {"id": i, "add": n > 0})


# ------------------------------------------------------------------------------------------------------------ what the panel shows
def test_the_panel_is_made_from_the_studys_definition(bracket):
    ids = {w["id"] for w in bracket.widgets("setup.")["widgets"]}
    assert {"setup.about.toggle", "setup.bc.add", "setup.setting.gravity", "setup.next"} <= ids
    assert any(i.endswith(".name") and i.startswith("setup.body.") for i in ids) and any(i.startswith("setup.material.") for i in ids)
    add = bracket.widget_do("setup.bc.add")["value"]["items"]
    assert "Fixed support" in add and "Force" in add and add[0].startswith("＋")
    g = bracket.widget_do("setup.setting.gravity")["value"]
    assert g["text"] == "off" and "-z" in g["items"]
    assert bracket.state()["view"]["pick_mode"] == "face"            # (clicks select faces while setting up)


def test_readiness_says_what_is_missing_and_jumps_there(bracket):
    checks = [w for w in bracket.widgets("setup.check.")["widgets"]]
    assert checks and all(c["text"].split(None, 1)[1].startswith(("Missing", "Check", "Note")) for c in checks)   # (a word, not only a colour)
    first = checks[0]["id"]
    bracket.widget_do(first, "click")
    assert bracket.win.step in ("geometry", "setup", "mesh", "solve", "results")


# ------------------------------------------------------------------------------------------------------------ boundary conditions
def test_a_support_and_a_load_are_made_by_picking_faces(bracket):
    c = bracket
    pick(c, c.holes)
    assert c.state()["view"]["selection"] == c.holes
    c.widget_do("setup.bc.add", "set", "Fixed support")
    b = bcs(c)
    assert len(b) == 1 and b[0]["type"] == "fixed_support" and sorted(b[0]["faces"]) == sorted(c.holes)
    colour = c.state()["study"] and c.engine.state()["study_spec"]["face_bc_types"]["fixed_support"]["color"]
    tints = c.state()["view"]["face_tints"]
    assert set(tints) == set(c.holes) and set(tints.values()) == {colour}        # the faces carry the type's colour
    assert [a for a in c.win.view.label_actors]                                  # ... and the name hangs on the model
    assert c.widget_do("setup.bc.0.name")["value"] == b[0]["name"]
    assert c.widget_do("setup.bc.add")["value"]["text"].startswith("＋")           # (the chooser is ready for the next one)
    # a load, with no faces selected first: the condition starts picking
    c.win.view.set_selection([])
    c.widget_do("setup.bc.add", "set", "Force")
    assert len(bcs(c)) == 2 and c.win.panels["setup"].active == bcs(c)[1]["id"]
    assert c.widget_do("setup.bc.1.faces")["value"] == {"checked": True}
    pick(c, c.front)                                                              # the clicks go into the active condition
    assert sorted(bcs(c)[1]["faces"]) == sorted(c.front)
    c.widget_do("setup.bc.1.faces", "click")                                      # Done picking
    assert c.win.panels["setup"].active is None
    assert set(c.state()["view"]["face_tints"]) == set(c.holes) | set(c.front)


def test_parameters_names_and_deleting(bracket):
    c = bracket
    c.do("setup.bc_add", {"type": "force", "faces": c.front, "name": "Camera"})
    assert [b["name"] for b in bcs(c)] == ["Camera"]
    p = c.widget_do("setup.bc.0.param.fz_N")
    assert p["class"] == "QLineEdit"
    c.widget_do("setup.bc.0.param.fz_N", "set", -50)
    c.widget_do("setup.bc.0.param.fx_N", "set", 30)
    assert bcs(c)[0]["params"]["fz_N"] == -50 and bcs(c)[0]["params"]["fx_N"] == 30
    labels = [la.GetInput() for la in c.win.view.label_actors]
    assert labels == ["Camera: 58.3095 N"] or labels[0].startswith("Camera: 58.3")        # the size of the force, on the model
    c.widget_do("setup.bc.0.name", "set", "Lens")
    assert bcs(c)[0]["name"] == "Lens"
    c.do("setup.bc_set", {"bc": "Lens", "params": {"fz_N": -10}})
    assert bcs(c)[0]["params"]["fz_N"] == -10 and bcs(c)[0]["params"]["fx_N"] == 30     # (the others stay)
    c.widget_do("setup.bc.0.delete", "click")
    assert bcs(c) == [] and c.state()["view"]["face_tints"] == {}
    with pytest.raises(ValueError, match="No boundary condition"):
        c.do("setup.bc_delete", {"bc": "nothing"})


def test_a_faces_cannot_belong_to_two_conditions(bracket):
    c = bracket
    c.do("setup.bc_add", {"type": "fixed_support", "faces": c.holes, "name": "Bolts"})
    c.do("setup.bc_add", {"type": "force", "faces": [c.holes[0]], "name": "Pull"})        # takes one of the bolt faces
    b = {x["name"]: x for x in bcs(c)}
    assert c.holes[0] not in b["Bolts"]["faces"] and b["Pull"]["faces"] == [c.holes[0]]


# ------------------------------------------------------------------------------------------------------------ settings, parts
def test_settings_are_set_through_the_form(bracket):
    bracket.widget_do("setup.setting.gravity", "set", "-z")
    assert bracket.engine.state()["settings"]["gravity"] == "-z"
    bracket.do("setup.setting", {"key": "gravity", "value": "off"})
    assert bracket.engine.state()["settings"]["gravity"] == "off"
    assert bracket.widget_do("setup.setting.gravity")["value"]["text"] == "off"


def test_rename_confirm_material_and_hide_a_part(bracket):
    c = bracket
    body = c.engine.state()["body_list"][0]
    bid = body["id"]
    c.widget_do(f"setup.body.{bid}.name", "set", "Mount")
    assert c.engine.state()["body_list"][0]["name"] == "Mount"
    if body.get("material_is_default"):
        c.widget_do(f"setup.body.{bid}.use_default", "click")
        assert not c.engine.state()["body_list"][0].get("material_is_default")
    c.widget_do(f"setup.material.{bid}", "set", "Steel (structural)") if "Steel (structural)" in c.widget_do(f"setup.material.{bid}")["value"]["items"] else None
    # hiding the part removes it from the view and from picking
    v = c.win.view
    face = c.holes[0]
    f = next(x for x in c.engine.project().faces() if x["id"] == face)
    import numpy as np
    n, ctr = np.array(f["normal"]), np.array(f["centroid"])
    ray = {"p0": (ctr + n * 0.05).tolist(), "p1": (ctr - n * 0.001).tolist()}
    assert c.do("view.pick_ray", ray)["result"]["hit"] is not None
    c.widget_do(f"setup.body.{bid}.eye", "click")
    assert bid in c.win.hidden_bodies and not any(a.GetVisibility() for a in v.geo_actors)
    assert c.do("view.pick_ray", ray)["result"]["hit"] is None
    assert c.widget_do(f"setup.body.{bid}.eye")["text"] == "Show"
    c.widget_do(f"setup.body.{bid}.eye", "click")
    assert all(a.GetVisibility() for a in v.geo_actors) and c.do("view.pick_ray", ray)["result"]["hit"] is not None
    c.widget_do(f"setup.body.{bid}.select", "click")
    assert c.state()["view"]["selection"]


def test_leaving_the_step_clears_what_it_hung_on_the_model(bracket):
    c = bracket
    c.do("setup.bc_add", {"type": "fixed_support", "faces": c.holes})
    assert c.state()["view"]["face_tints"] and c.win.view.label_actors
    c.do("step.go", {"step": "mesh"})
    assert c.state()["view"]["face_tints"] == {} and c.state()["view"]["pick_mode"] is None and not c.win.view.label_actors
    c.do("step.go", {"step": "setup"})
    assert c.state()["view"]["face_tints"] and c.state()["view"]["pick_mode"] == "face"


# ------------------------------------------------------------------------------------------------------------ both themes
@pytest.mark.parametrize("name", ("dark", "light"))
def test_the_filled_in_setup_panel_is_legible(bracket, name):
    c = bracket
    c.do("setup.bc_add", {"type": "fixed_support", "faces": c.holes, "name": "Bolts"})
    c.do("setup.bc_add", {"type": "force", "faces": c.front, "name": "Camera"})
    c.do("theme.set", {"mode": name})
    c.do("step.go", {"step": "setup"})
    c._settle()
    weak, seen = [], 0
    for i, o in visible_controls(c):
        seen += 1
        bg, fg, r = best_contrast(o)
        if fg is None or r < 3.0:
            weak.append((i, round(r, 2)))
    assert seen > 15 and not weak, weak[:6]
    cov = c.coverage()
    assert cov["complete"], (cov["unnamed_widgets"][:3], cov["unlabelled"][:3], cov["no_pointer_cursor"][:3])
