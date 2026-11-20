"""The Mesh step of the Qt window: type, resolution, advanced options, the verdict and histogram, the poorest cells."""
import pytest
from qt_support import ctl, from_thread  # noqa: F401
from test_qt_results import solved  # noqa: F401
from test_qt_theme import best_contrast, visible_controls  # noqa: F401


def open_mesh(ctl, solved):
    ctl.do("study.import", {"path": solved["static"]})
    ctl.do("step.go", {"step": "mesh"})
    return ctl


def mesh(c):
    return c.engine.state()["mesh"]


def test_advanced_sizes_and_defeaturing(ctl, solved):
    c = open_mesh(ctl, solved)
    c.widget_do("mesh.size_max", "set", "4")
    assert mesh(c)["size_max"] == pytest.approx(0.004)
    c.widget_do("mesh.size_min", "set", "0.8")
    assert mesh(c)["size_min"] == pytest.approx(0.0008)
    c.widget_do("mesh.defeature", "set", "0.5")
    assert mesh(c)["defeature"] == pytest.approx(0.0005)
    c.widget_do("mesh.size_max", "set", "0")
    assert not mesh(c).get("size_max")                                                  # empty/0 = automatic again
    assert mesh(c)["status"] == "stale"                                                 # the existing mesh is marked out of date


def test_part_sizing(ctl, solved):
    c = open_mesh(ctl, solved)
    assert c.widget_do("mesh.part.pick")["value"]["items"][-1].startswith("Selected faces")
    c.widget_do("mesh.part.add", "click")                                               # the first part: all its faces
    refs = mesh(c)["refinements"]
    assert len(refs) == 1 and refs[0]["part"] == "Camera bracket" and len(refs[0]["faces"]) >= 10
    c.widget_do("mesh.part.0.size", "set", "1.5")
    assert mesh(c)["refinements"][0]["size"] == pytest.approx(0.0015)
    c.widget_do("mesh.part.0.delete", "click")
    assert mesh(c)["refinements"] == []
    first = c.engine.project().faces()[0]["id"]
    c.win.view.set_selection([first])                                                   # faces picked in the 3D view
    c.widget_do("mesh.part.pick", "set", "Selected faces (pick them in the 3D view)")
    c.widget_do("mesh.part.add", "click")
    assert mesh(c)["refinements"][0]["faces"] == [first]


def test_refinement_regions_box_and_cylinder(ctl, solved):
    c = open_mesh(ctl, solved)
    c.widget_do("mesh.region.add_box", "click")
    c.widget_do("mesh.region.add_cyl", "click")
    regs = mesh(c)["regions"]
    assert [r.get("shape", "box") for r in regs] == ["box", "cylinder"]
    assert all(a < b for a, b in zip(regs[0]["min"], regs[0]["max"]))
    c.widget_do("mesh.region.0.param.size", "set", "2")
    assert mesh(c)["regions"][0]["size"] == pytest.approx(0.002)
    c.widget_do("mesh.region.1.param.radius", "set", "7")
    assert mesh(c)["regions"][1]["radius"] == pytest.approx(0.007)
    c.widget_do("mesh.region.0.name", "set", "Wake")
    assert mesh(c)["regions"][0]["name"] == "Wake"
    c.widget_do("mesh.region.0.delete", "click")
    assert [r["name"] for r in mesh(c)["regions"]] == ["region2"]


def test_wall_layers_and_the_mesh_type_belong_to_flow_studies(ctl):
    ctl.do("study.new", {"name": "Flow", "study": "external_aero"})
    ctl.do("step.go", {"step": "mesh"})
    ids = {w["id"] for w in ctl.widgets("mesh.")["widgets"]}
    assert {"mesh.method.tet", "mesh.method.hex", "mesh.layers.toggle", "mesh.layers.n", "mesh.layers.growth", "mesh.layers.first"} <= ids
    ctl.widget_do("mesh.layers.n", "set", "5")
    assert mesh(ctl)["layers"]["n"] == 5
    ctl.widget_do("mesh.layers.growth", "set", "1.3")
    assert mesh(ctl)["layers"]["growth"] == pytest.approx(1.3)
    assert ctl.widget_do("mesh.method.tet")["value"] == {"checked": True} and ctl.widget_do("mesh.method.note")["value"]
    ctl.do("study.new", {"name": "Solid", "study": "static_structural"})
    ctl.do("step.go", {"step": "mesh"})
    shown = {w["id"] for w in ctl.widgets("mesh.")["widgets"] if w["visible"]}
    assert "mesh.layers.n" not in shown and "mesh.method.tet" not in shown              # (a solid study has neither)


def test_simple_mode_leaves_only_the_presets(ctl, solved):
    c = open_mesh(ctl, solved)
    assert c.widget_do("mesh.size_max")["visible"] is True
    c.do("app.mode", {"mode": "simple"})
    assert c.widget_do("mesh.size_max")["visible"] is False and c.widget_do("mesh.preset.coarse")["visible"] is True
    c.do("app.mode", {"mode": "expert"})
    assert c.widget_do("mesh.size_max")["visible"] is True


def test_the_verdict_and_the_histogram_say_how_good_the_mesh_is(ctl, solved):
    c = open_mesh(ctl, solved)
    v = c.widget_do("mesh.verdict")["value"]
    assert v.startswith(("✓ Good", "⚠"))                                                 # words and a mark, not only a colour
    assert len(c.win.panels["mesh"].hist.counts) == 20 and sum(c.win.panels["mesh"].hist.counts) == mesh(c)["stats"]["cells"]
    assert "poor cells" in c.widget_do("mesh.stats")["value"]


def test_the_poorest_cells_can_be_shown_and_looked_at(ctl, solved):
    c = open_mesh(ctl, solved)
    c.do("mesh.poor", {"on": True, "threshold": 0.9})
    assert c.state()["view"]["overlay"]["name"] == "Cell quality (poorest cells)" and c.state()["view"]["overlay"]["range"] == [0.0, 0.9]
    assert "cells below 0.9" in c.widget_do("mesh.poor.info")["value"]
    before = c.state()["view"]["camera"]["focal_point"]
    w = c.do("mesh.zoom_worst")["result"]["worst"]
    assert w["quality"] < 0.9 and c.state()["view"]["camera"]["focal_point"] != before
    c.do("mesh.poor", {"on": False})
    assert c.state()["view"]["overlay"]["name"] == "Cell quality"
    c.do("mesh.poor", {"on": True, "threshold": 0.01})
    with pytest.raises(ValueError, match="no cells below"):
        c.do("mesh.zoom_worst")


@pytest.mark.parametrize("name", ("dark", "light"))
def test_the_mesh_panel_is_legible_with_everything_open(ctl, solved, name):
    c = open_mesh(ctl, solved)
    c.do("theme.set", {"mode": name})
    c.widget_do("mesh.region.add_box", "click")
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
