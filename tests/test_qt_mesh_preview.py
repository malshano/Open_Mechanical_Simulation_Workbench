"""The Mesh step's local control: refinement regions are drawn in the 3D view (a translucent box or cylinder, its name on it) and a part can be
chosen for sizing - a body of a structural study, the named parts of the object in a flow study - from the drop-down or by clicking it."""
import pytest
from qt_support import ctl, from_thread  # noqa: F401

from simwb.qt import regionview


def wait(c, t=300):
    assert from_thread(c.app, lambda: c.wait_idle(t), t + 20)["idle"]
    c._settle()


@pytest.fixture
def bracket(ctl):
    ctl.do("study.example", {"study": "static_structural"})
    wait(ctl)
    ctl.do("step.go", {"step": "mesh"})
    return ctl


@pytest.fixture
def aero(ctl, box_step):
    ctl.do("study.new", {"name": "Flow", "study": "external_aero"})
    ctl.do("geometry.import", {"path": str(box_step)})
    wait(ctl)
    ctl.do("step.go", {"step": "mesh"})
    return ctl


def test_box_and_cylinder_geometry_for_the_drawing():
    pts, tris, lines = regionview.box_geometry([0, 0, 0], [1, 2, 3])
    assert len(pts) == 8 and len(tris) == 12 and len(lines) == 6
    pts, tris, lines = regionview.cylinder_geometry([0, 0, 0], [0, 0, 2], 0.5)
    assert max(abs(p[2]) for p in pts) == pytest.approx(2.0) and len(lines) == 6                   # (two circles and four axial lines)
    ring = regionview.cylinder_geometry([0, 0, 0], [0, 0, 2], 0.5, inner=0.3)
    assert len(ring[0]) == 2 * len(pts[:2 * regionview.SEGMENTS]) and len(ring[2]) == 12             # (the inner wall too, and no end discs)


def test_a_region_is_drawn_when_added_and_follows_its_numbers(aero):
    c = aero
    assert not c.win.view.region_view.actors
    c.do("mesh.region_add", {"kind": "box"})
    rv = c.win.view.region_view
    assert len(rv.regions) == 1 and rv.actors
    lab = [la.GetInput() for la in c.win.view.label_actors]
    assert any("region1" in t for t in lab)                                                        # (its name and cell size hang on it)
    c.do("mesh.region_add", {"kind": "cylinder"})
    assert len(c.win.view.region_view.regions) == 2
    c.widget_do("mesh.regions.show", "set", False)
    assert not c.win.view.region_view.actors
    c.widget_do("mesh.regions.show", "set", True)
    assert c.win.view.region_view.actors


def test_the_look_button_draws_that_region_stronger_and_leaving_the_step_takes_the_drawing_away(aero):
    c = aero
    c.do("mesh.region_add", {"kind": "box"})
    c.do("mesh.region_add", {"kind": "box"})
    c.widget_do("mesh.region.1.show", "click")
    assert c.win.view.region_view.active == 1
    c.do("step.go", {"step": "setup"})
    assert not c.win.view.region_view.actors and not c.win.view.label_actors
    c.do("step.go", {"step": "mesh"})
    assert c.win.view.region_view.actors


def test_a_solid_body_is_offered_for_part_sizing_and_sized_once(bracket):
    c = bracket
    from PySide6.QtWidgets import QComboBox
    cb = next(w for w in c.win.panels["mesh"].findChildren(QComboBox) if w.property("wid") == "mesh.part.pick")
    assert cb.count() >= 2 and cb.itemText(cb.count() - 1).startswith("Faces selected")
    c.widget_do("mesh.part.add", "click")
    refs = c.engine.state()["mesh"]["refinements"]
    assert len(refs) == 1 and refs[0]["part"] == cb.itemText(0) and refs[0]["faces"]
    c.widget_do("mesh.part.add", "click")                                                           # (a second one for the same part is refused)
    assert len(c.engine.state()["mesh"]["refinements"]) == 1


def test_clicking_a_part_in_the_3d_view_gives_it_a_sizing(bracket):
    c = bracket
    c.widget_do("mesh.part.pick_3d", "set", True)
    assert c.win.panels["mesh"].picking_part and c.win.view.pick_mode == "face"
    assert "Click a part" in c.do("view.hint")["result"]["text"]
    fid = c.engine.project().faces()[0]["id"]
    c.do("view.pick_face", {"id": fid})
    assert len(c.engine.state()["mesh"]["refinements"]) == 1
    assert not c.win.panels["mesh"].picking_part                                                    # (one click: the picking switches itself off)


def test_the_air_volume_is_not_a_part(aero):
    c = aero
    far = next(f["id"] for f in c.engine.project().faces() if f.get("kind") == "farfield")
    c.widget_do("mesh.part.pick_3d", "set", True)
    c.do("view.pick_face", {"id": far})
    assert not c.engine.state()["mesh"]["refinements"] and c.win.panels["mesh"].picking_part      # (refused; still waiting for a part)
