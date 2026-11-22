"""The Geometry step of the Qt window: import, orientation (turn, reset, align a face to a plane), the parts."""
import numpy as np
import pytest
from qt_support import ctl, from_thread  # noqa: F401
from test_qt_theme import best_contrast, visible_controls  # noqa: F401


def wait(c):
    assert from_thread(c.app, lambda: c.wait_idle(300), 320)["idle"]
    c._settle()


@pytest.fixture
def plate(ctl):
    """A heat study with the bundled heated plate imported (through the card's own example button), on the Geometry step."""
    ctl.do("study.new", {"name": "Plate", "study": "heat_conduction"})
    assert ctl.widget_do("geometry.import")["text"].startswith("Import")           # nothing imported yet: the import card
    assert ctl.widget_do("geometry.scale")["value"]["text"].startswith("×1")
    ctl.widget_do("geometry.example.0", "click")
    wait(ctl)
    return ctl


def box(c):
    b = max(c.engine.state()["body_list"], key=lambda x: x["volume_cm3"])["bbox"]
    return np.array([b[3] - b[0], b[4] - b[1], b[5] - b[2]])


def test_the_import_card_then_the_file_card(plate):
    ids = {w["id"] for w in plate.widgets("geometry.")["widgets"]}
    assert {"geometry.import", "geometry.align.start", "geometry.turn.x-90", "geometry.turn.x+90", "geometry.turn.y-90", "geometry.turn.y+90",
            "geometry.turn.z-90", "geometry.turn.z+90", "geometry.turn.axis", "geometry.turn.degrees", "geometry.turn.apply",
            "geometry.next"} <= ids
    assert plate.widget_do("geometry.import")["text"] == "Replace…"
    assert not plate.widget_do("geometry.turn.reset")["enabled"]                      # nothing to reset yet (it says why in its tooltip)
    assert plate.widget_do("geometry.info")["value"].endswith("bodies")
    assert plate.widget_do("geometry.next")["enabled"] is True
    bodies = [i for i in ids if i.startswith("geometry.body.") and i.endswith(".eye")]
    assert len(bodies) == 2


def test_turning_and_resetting(plate):
    c = plate
    before = box(c)
    c.widget_do("geometry.turn.z+90", "click")                                       # a PREVIEW first (the rings, the web version's way) ...
    assert not c.engine.state()["geometry"].get("rotation")
    c.widget_do("geometry.rotate.apply", "click")                                     # ... then Apply orientation rebuilds the geometry
    wait(c)
    after = box(c)
    assert np.allclose(sorted(after), sorted(before), rtol=1e-3) and abs(after[0] - before[1]) < 1e-6 * 100 and abs(after[1] - before[0]) < 1e-6 * 100
    assert c.engine.state()["geometry"].get("rotation")                              # it remembers the turn
    assert c.widget_do("geometry.turn.reset")["visible"]                              # ... and offers to undo it
    c.widget_do("geometry.turn.reset", "click")
    wait(c)
    assert np.allclose(box(c), before, rtol=1e-3) and not c.engine.state()["geometry"].get("rotation")
    c.widget_do("geometry.turn.axis", "set", "x")
    c.widget_do("geometry.turn.degrees", "set", "45")
    c.widget_do("geometry.turn.apply", "click")
    c.widget_do("geometry.rotate.apply", "click")
    wait(c)
    assert c.engine.state()["geometry"].get("rotation")


def test_a_picked_flat_face_is_laid_on_a_plane(plate):
    c = plate
    faces = c.engine.project().faces()
    side = next(f for f in faces if f["surface_type"] == "Plane" and abs(f["normal"][0]) > 0.99 and not f.get("interface")
                and f.get("kind") not in ("farfield", "enclosure"))
    c.widget_do("geometry.align.start", "click")
    assert c.state()["view"]["pick_mode"] == "face"
    c.do("view.pick_face", {"id": side["id"]})                                       # the click on the face
    assert c.state()["view"]["selection"] == [side["id"]]
    c.widget_do("geometry.align.done")                                               # the choice of planes is offered
    for t in ("+x", "-x", "+y", "-y", "+z", "-z"):
        assert c.widget_do(f"geometry.align.to.{t}")["enabled"]
    star = [w["text"] for w in c.widgets("geometry.align.to.")["widgets"] if "★" in w["text"]]
    assert len(star) == 1                                                            # the nearest plane is marked
    c.widget_do("geometry.align.to.+z", "click")                                     # previews the turn ...
    c.widget_do("geometry.rotate.apply", "click")                                     # ... Apply orientation rebuilds
    wait(c)
    n = c.engine.project().faces()
    after = next(f for f in n if f["surface_type"] == "Plane" and abs(f["normal"][2]) > 0.99)
    assert c.engine.state()["geometry"].get("rotation")
    # the face that was aligned now looks up: find it again by its area
    areas = [f for f in n if abs(f["area"] - side["area"]) < 1e-9 and abs(f["normal"][2] - 1) < 1e-6]
    assert areas, "the aligned face does not face +z"
    assert c.state()["view"]["selection"] == []                                      # (done: the choice is cleared)


def test_a_face_that_curves_right_round_needs_a_click_and_says_so(ctl):
    c = ctl
    c.do("study.example", {"study": "static_structural"})          # (the bracket has bolt holes: whole cylinders)
    wait(c)
    cyl = next(f for f in c.engine.project().faces() if f["surface_type"] == "Cylinder")
    c.widget_do("geometry.align.start", "click")
    c.do("view.pick_face", {"id": cyl["id"]})                      # (no point on it: its average direction cancels out)
    assert not c.widgets("geometry.align.to.")["widgets"]
    assert "right round" in c.win.panels["geometry"].note and c.widget_do("geometry.notice")["visible"]
    v = np.asarray(cyl["mesh"]["vertices"]).reshape(-1, 3)
    c.do("geometry.align_pick", {"face": cyl["id"], "x": v[3][0], "y": v[3][1], "z": v[3][2]})      # a click on it: the normal there is used
    assert c.widgets("geometry.align.to.")["widgets"]
    assert c.widget_do("geometry.align.mode.point")["enabled"] and not c.widget_do("geometry.align.mode.average")["enabled"]


def test_hide_a_part_and_leave_the_step(plate):
    c = plate
    eye = [w["id"] for w in c.widgets("geometry.body.")["widgets"] if w["id"].endswith(".eye")][0]
    c.widget_do(eye, "click")
    assert c.win.hidden_bodies and c.widget_do(eye)["text"] == "Show"
    c.do("step.go", {"step": "setup"})
    assert c.state()["window"]["step"] == "setup"
    c.do("step.go", {"step": "geometry"})
    c.widget_do("geometry.align.start", "click")
    c.do("step.go", {"step": "mesh"})
    assert c.win.panels["geometry"].align_pick is False and c.state()["view"]["pick_mode"] is None   # (leaving ends the alignment)


@pytest.mark.parametrize("name", ("dark", "light"))
def test_the_geometry_panel_is_legible(plate, name):
    c = plate
    c.do("theme.set", {"mode": name})
    c.widget_do("geometry.align.start", "click")
    f = next(f for f in c.engine.project().faces() if f["surface_type"] == "Plane" and f.get("kind") not in ("farfield", "enclosure") and not f.get("interface"))
    c.do("view.pick_face", {"id": f["id"]})
    c._settle()
    c.app.processEvents()
    weak, seen = [], 0
    for i, o in visible_controls(c):
        seen += 1
        bg, fg, r = best_contrast(o)
        if fg is None or r < 3.0:
            weak.append((i, round(r, 2)))
    assert seen > 20 and not weak, weak[:6]
    cov = c.coverage()
    assert cov["complete"], (cov["unnamed_widgets"][:3], cov["unlabelled"][:3], cov["no_pointer_cursor"][:3])
