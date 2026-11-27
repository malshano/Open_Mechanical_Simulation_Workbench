"""Edge refinement and tolerant layers in the Mesh step of the window (issue #50): the controls, their values going both ways between the panel and the settings
(what the API and the Claude tools read and write), the estimate of the extra cells before meshing, the words after meshing, the places where they are not
available, saving and opening the study, and a real small mesh made with the refinement switched on from the panel."""
import json

import pytest
from qt_support import EXAMPLES, ctl, from_thread  # noqa: F401

import simwb.project as P
from simwb import edgepreview
from test_qt_layers_mesh import fake_layers, wait  # noqa: F401


def mesh(c):
    return c.engine.state()["mesh"]


def val(c, wid_):
    return c.widget_do(wid_)["value"]


def open_group(c, name):
    if not c.widget_do(f"mesh.{name}.toggle")["value"]["checked"]:
        c.widget_do(f"mesh.{name}.toggle", "click")
    c._settle()


def ids(c, visible=False):
    return {w["id"] for w in c.widgets(visible_only=visible)["widgets"]}


def scanned(c):
    """The edge search of the open study, finished (the window asks for it when the group is open)."""
    edgepreview.ensure_scan(c.engine.project(), wait=True)
    c.engine.changed.emit()
    c._settle()


@pytest.fixture
def flow(ctl, box_step, monkeypatch):
    monkeypatch.setattr(P.Project, "_make_layers_inner", fake_layers)
    ctl.do("study.new", {"name": "Flow", "study": "external_aero"})
    ctl.do("geometry.import", {"path": str(box_step)})
    wait(ctl)
    ctl.engine.project().set_settings(speed=10)
    ctl.engine.project().set_mesh(preset="quick", layers={"n": 3, "growth": 1.2})
    ctl.do("step.go", {"step": "mesh"})
    ctl.do("app.mode", {"mode": "expert"})
    ctl._settle()
    return ctl


# ---------------------------------------------------------------------------------------------------------------------------------- the controls
def test_the_group_and_its_controls_exist(flow):
    c = flow
    have = ids(c)
    for i in ("mesh.edges.toggle", "mesh.edges.on", "mesh.edges.factor", "mesh.edges.angle", "mesh.edges.cap", "mesh.edges.estimate", "mesh.edges.show",
              "mesh.layers.tolerant", "mesh.layers.tolerant.note"):
        assert i in have, i
    assert not val(c, "mesh.edges.on")["checked"]
    assert not val(c, "mesh.layers.tolerant")["checked"]


def test_the_panel_sets_each_edge_setting(flow):
    c = flow
    assert "edge_refine" not in mesh(c)
    c.widget_do("mesh.edges.on", "set", True)
    assert mesh(c)["edge_refine"] == {"factor": 6.0, "max_cells": 150000.0, "max_angle": 140.0}
    assert mesh(c)["status"] != "ready"                                                    # (the setup changed: the mesh is out of date)
    c.widget_do("mesh.edges.factor", "set", "8")
    c.widget_do("mesh.edges.angle", "set", "100")
    c.widget_do("mesh.edges.cap", "set", "40000")
    assert mesh(c)["edge_refine"] == {"factor": 8.0, "max_cells": 40000.0, "max_angle": 100.0}
    assert c.widget_do("mesh.edges.factor")["value"] == "8"
    c.widget_do("mesh.edges.on", "set", False)
    assert "edge_refine" not in mesh(c)
    assert not val(c, "mesh.edges.on")["checked"]


def test_bad_numbers_are_refused_and_the_old_setting_stays(flow):
    c = flow
    c.widget_do("mesh.edges.on", "set", True)
    pr = c.engine.project()
    for bad in ({"factor": 30}, {"factor": 1}, {"max_angle": 10}, {"max_angle": 179}, {"max_cells": 10}):
        with pytest.raises(ValueError):
            pr.set_mesh(edge_refinement=bad)
    assert mesh(c)["edge_refine"]["factor"] == 6.0


def test_the_settings_api_changes_the_panel(flow):
    c = flow
    c.engine.project().set_mesh(edge_refinement={"factor": 10, "max_angle": 120, "max_cells": 50000}, layers={"n": 3, "growth": 1.3, "tolerant": True})
    c.engine.changed.emit()
    c._settle()
    assert val(c, "mesh.edges.on")["checked"]
    assert c.widget_do("mesh.edges.factor")["value"] == "10"
    assert c.widget_do("mesh.edges.angle")["value"] == "120"
    assert c.widget_do("mesh.edges.cap")["value"] == "50000"
    assert val(c, "mesh.layers.tolerant")["checked"]
    assert c.widget_do("mesh.edges.toggle")["value"]["checked"]                           # (a group with a setting is open)
    c.engine.project().set_mesh(edge_refinement=False)
    c.engine.changed.emit()
    c._settle()
    assert not val(c, "mesh.edges.on")["checked"]


def test_a_bare_factor_through_the_api_keeps_the_cap_and_the_angle(flow):
    pr = flow.engine.project()
    pr.set_mesh(edge_refinement={"factor": 6, "max_cells": 40000, "max_angle": 100})
    pr.set_mesh(edge_refinement=9)
    assert flow.engine.state()["mesh"]["edge_refine"] == {"factor": 9.0, "max_cells": 40000.0, "max_angle": 100.0}


def test_tolerant_layers_switch_and_the_other_layer_settings(flow):
    c = flow
    c.widget_do("mesh.layers.tolerant", "set", True)
    assert mesh(c)["layers"]["tolerant"] is True and mesh(c)["layers"]["n"] == 3 and mesh(c)["layers"]["growth"] == pytest.approx(1.2)
    c.widget_do("mesh.layers.n", "set", "4")                                                # (changing the number keeps the switch)
    assert mesh(c)["layers"]["n"] == 4 and mesh(c)["layers"]["tolerant"] is True
    c.engine.project().set_mesh(layers={"n": 4, "tolerant": False})
    c.engine.changed.emit()
    c._settle()
    assert not val(c, "mesh.layers.tolerant")["checked"]
    assert "Looser limits" in c.widget_do("mesh.layers.tolerant.note")["text"]
    c.widget_do("mesh.layers.tolerant", "set", True)
    assert val(c, "mesh.layers.tolerant")["checked"]


def test_tolerant_layers_only_make_the_layer_step_looser(flow):
    """The switch reaches the layer step's limits (the snappyHexMeshDict), not only the settings."""
    from simwb.solvers.openfoam import cases

    assert cases.LAYER_TOLERANT["mfr"] > cases.LAYER_STRICT["mfr"] and cases.LAYER_TOLERANT["iters"] > cases.LAYER_STRICT["iters"]


def test_values_survive_saving_and_opening_the_study(flow, tmp_path):
    c = flow
    c.widget_do("mesh.edges.on", "set", True)
    c.widget_do("mesh.edges.factor", "set", "7")
    c.widget_do("mesh.edges.cap", "set", "60000")
    c.widget_do("mesh.layers.tolerant", "set", True)
    c.engine.save()
    folder = c.engine.project().dir
    saved = json.loads((folder / "project.json").read_text())["mesh"]
    assert saved["edge_refine"]["factor"] == 7.0 and saved["edge_refine"]["max_cells"] == 60000.0 and saved["layers"]["tolerant"] is True
    c.do("study.new", {"name": "Other", "study": "external_aero"})
    c.do("study.open_folder", {"path": str(folder)})
    c.do("step.go", {"step": "mesh"})
    c._settle()
    assert val(c, "mesh.edges.on")["checked"] and c.widget_do("mesh.edges.factor")["value"] == "7"
    assert c.widget_do("mesh.edges.cap")["value"] == "60000"
    assert val(c, "mesh.layers.tolerant")["checked"]
    # and a copy made with Save as keeps them too
    c.do("study.save_as", {"location": str(tmp_path / "copy")})
    assert c.engine.state()["mesh"]["edge_refine"]["factor"] == 7.0


# ---------------------------------------------------------------------------------------------------------------------------------- the estimate
def test_the_estimate_line_follows_the_settings_and_the_mesher_plan(flow):
    c = flow
    open_group(c, "edges")
    scanned(c)
    pr = c.engine.project()
    pv = edgepreview.preview(pr)
    assert pv["scan"] == "ready" and pv["plan"]["applied"]
    off = c.widget_do("mesh.edges.estimate")["text"]
    assert off.startswith("Off. Switched on it would:") and f"{pv['plan']['estimated_extra_cells']:,.0f}" in off
    c.widget_do("mesh.edges.on", "set", True)
    scanned(c)
    on = c.widget_do("mesh.edges.estimate")["text"]
    assert on.startswith("About ") and "extra cells" in on and "edge cells" in on and "wall cells" in on
    pv = edgepreview.preview(pr)
    n1 = pv["plan"]["estimated_extra_cells"]
    assert f"{n1:,.0f}" in on
    c.widget_do("mesh.edges.factor", "set", "12")                                           # finer edge cells: more cells
    scanned(c)
    n2 = edgepreview.preview(pr)["plan"]["estimated_extra_cells"]
    assert n2 > n1 and f"{n2:,.0f}" in c.widget_do("mesh.edges.estimate")["text"]
    c.widget_do("mesh.edges.cap", "set", "1000")                                            # a cap that bites
    scanned(c)
    t = c.widget_do("mesh.edges.estimate")["text"]
    assert "cap" in t and ("⚠" in t or "nothing is refined" in t)
    c.widget_do("mesh.edges.cap", "set", "150000")
    c.widget_do("mesh.edges.angle", "set", "20")                                            # only knife edges: a box has none
    scanned(c)
    assert "no sharp edge across the flow" in c.widget_do("mesh.edges.estimate")["text"]


def test_smart_sizing_refines_edges_by_itself_and_off_is_respected(flow, monkeypatch):
    c = flow
    c.engine.project().set_mesh(smart=True)
    c.engine.changed.emit()
    c._settle()
    pv = edgepreview.preview(c.engine.project())
    assert pv["on"] and pv["source"] == "smart" and pv["factor"] == edgepreview.SMART_FACTOR
    assert val(c, "mesh.edges.on")["checked"]                                               # (the box says what the mesher will do)
    seen = []
    monkeypatch.setattr(P, "run_gmsh_task", lambda jm, job, task, payload, *a, **k: seen.append(payload) or (_ for _ in ()).throw(RuntimeError("stop")))
    pr = c.engine.project()
    with pytest.raises(RuntimeError):
        pr._mesh_work_inner(_FakeJob(), 0, 1)
    assert seen[-1]["mesh"]["edge_refine"] == {"factor": edgepreview.SMART_FACTOR}
    c.widget_do("mesh.edges.on", "set", False)                                              # switched off with smart sizing on: stays off
    assert not val(c, "mesh.edges.on")["checked"] and mesh(c)["no_edge_refine"] is True
    with pytest.raises(RuntimeError):
        pr._mesh_work_inner(_FakeJob(), 0, 1)
    assert "edge_refine" not in seen[-1]["mesh"]
    c.widget_do("mesh.edges.on", "set", True)
    assert "no_edge_refine" not in mesh(c) and mesh(c)["edge_refine"]["factor"] == edgepreview.SMART_FACTOR


class _FakeJob:
    status = "running"

    def __getattr__(self, k):
        return lambda *a, **kw: None


def test_a_smallest_cell_set_by_hand_blocks_it_and_the_panel_says_so(flow):
    c = flow
    c.engine.project().set_mesh(size_min_mm=2, edge_refinement=True)
    c.engine.changed.emit()
    open_group(c, "edges")
    t = c.widget_do("mesh.edges.estimate")["text"]
    assert "Not used while a smallest cell size is set" in t
    c.engine.project().set_mesh(smart=True)
    c.engine.changed.emit()
    c._settle()
    assert "Not used" not in c.widget_do("mesh.edges.estimate")["text"]


def test_the_sharp_edges_can_be_shown_in_the_3d_view(flow):
    c = flow
    open_group(c, "edges")
    scanned(c)
    assert c.win.view.sharp_view.count == 0
    c.widget_do("mesh.edges.show", "set", True)
    scanned(c)
    n = edgepreview.preview(c.engine.project())["plan"]["edges"]
    assert n > 0 and c.win.view.sharp_view.count == n and len(c.win.view.sharp_view.actors) == 1
    c.do("step.go", {"step": "setup"})                                                      # (the marks belong to the Mesh step)
    assert c.win.view.sharp_view.count == 0
    c.do("step.go", {"step": "mesh"})
    c._settle()
    assert c.win.view.sharp_view.count == n
    c.widget_do("mesh.edges.show", "set", False)
    assert c.win.view.sharp_view.count == 0


# ---------------------------------------------------------------------------------------------------------------------------------- not available
def test_not_available_in_other_studies_with_the_reason(ctl, tmp_path, box_step):
    ctl.do("study.new", {"name": "Heat", "study": "heat_conduction"})
    ctl.do("geometry.import", {"path": str(box_step)})
    wait(ctl)
    ctl.do("step.go", {"step": "mesh"})
    ctl.do("app.mode", {"mode": "expert"})
    ctl._settle()
    on = ctl.widget_do("mesh.edges.on")
    assert not on["enabled"] and not on["value"]["checked"]
    assert ctl.widget_do("mesh.edges.note")["text"].startswith("Not for this type of study")
    assert "external aerodynamics" in ctl.widget_do("mesh.edges.note")["text"]
    assert "mesh.edges.factor" not in ids(ctl) and "mesh.layers.tolerant" not in ids(ctl)
    with pytest.raises(ValueError):
        ctl.engine.project().set_mesh(layers={"n": 2, "tolerant": True})                    # (the engine says the same for layers)


def test_not_available_for_the_hex_dominant_mesh(flow):
    c = flow
    try:
        c.engine.project().set_mesh(method="hex")
    except ValueError as e:
        pytest.skip(f"hex-dominant not possible here: {e}")
    c.engine.changed.emit()
    c._settle()
    assert not c.widget_do("mesh.edges.on")["enabled"]
    assert c.widget_do("mesh.edges.note")["text"].startswith("Not for a hex-dominant mesh")
    assert not c.widget_do("mesh.layers.tolerant")["enabled"]


def test_simple_mode_hides_the_advanced_groups_and_the_marks(flow):
    c = flow
    open_group(c, "edges")
    scanned(c)
    c.widget_do("mesh.edges.show", "set", True)
    assert c.win.view.sharp_view.count > 0
    c.do("app.mode", {"mode": "simple"})
    c._settle()
    vis = ids(c, True)
    assert "mesh.edges.on" not in vis and "mesh.layers.tolerant" not in vis and "mesh.preset.quick" in vis
    assert c.win.view.sharp_view.count == 0
    c.do("app.mode", {"mode": "expert"})
    c._settle()
    assert "mesh.edges.on" in ids(c, True)


def test_the_actions_api_reports_the_preview(flow):
    c = flow
    c.widget_do("mesh.edges.on", "set", True)
    scanned(c)
    r = c.do("mesh.edges", {})["result"]
    assert r["on"] and r["plan"]["applied"] and r["scan"] == "ready" and "lines" not in r


# ---------------------------------------------------------------------------------------------------------------------------------- after meshing
def test_after_meshing_the_panel_shows_what_the_mesher_did_and_the_layers_kept(flow):
    c = flow
    c.widget_do("mesh.edges.on", "set", True)
    c.widget_do("mesh.layers.tolerant", "set", True)
    c.do("mesh.generate")
    wait(c)
    scanned(c)
    s = mesh(c)["stats"]
    assert s["edge_refine"] and s["edge_refine"]["edges"] > 0 and s["edge_refine_skipped"] is None
    t = c.widget_do("mesh.edges.made")["text"]
    assert t.startswith("Last mesh:") and f"{s['edge_refine']['estimated_extra_cells']:,.0f}" in t
    edgepreview.measure(c.engine.project(), wait=True)
    c.engine.changed.emit()
    c._settle()
    assert "Counted in the mesh:" in c.widget_do("mesh.edges.made")["text"]
    lt = c.widget_do("mesh.layers.tolerant.made")["text"]
    assert "3 layers made" in lt and "tolerant limits" in lt
    c.widget_do("mesh.layers.tolerant", "set", False)
    assert "the setting changed since" in c.widget_do("mesh.layers.tolerant.made")["text"]
    rows = dict(__import__("simwb.qt.meshtools", fromlist=["x"]).stats_rows(c.engine.state()))
    assert "Edge refinement" in rows and "extra cells (estimate)" in rows["Edge refinement"]


def test_a_real_mesh_of_the_wing_made_from_the_panel_uses_the_settings(ctl):
    """The whole way: the setting made in the panel reaches the mesher (a real gmsh run on the example wing, quick preset) and the numbers come back; the
    estimate shown before meshing is the one the mesher then reports."""
    ctl.do("study.new", {"name": "Wing", "study": "external_aero"})
    ctl.do("geometry.import", {"path": str(EXAMPLES / "airplane_wing.step")})
    wait(ctl)
    ctl.engine.project().set_settings(speed=30)
    ctl.engine.project().set_mesh(preset="quick")
    ctl.do("step.go", {"step": "mesh"})
    ctl.do("app.mode", {"mode": "expert"})
    open_group(ctl, "edges")
    ctl.widget_do("mesh.edges.on", "set", True)
    ctl.widget_do("mesh.edges.factor", "set", "5")
    scanned(ctl)
    pr = ctl.engine.project()
    pv = edgepreview.preview(pr)
    shown = ctl.widget_do("mesh.edges.estimate")["text"]
    assert f"{pv['plan']['estimated_extra_cells']:,.0f}" in shown
    ctl.do("mesh.generate")
    wait(ctl, 600)
    s = mesh(ctl)["stats"]
    er = s["edge_refine"]
    assert er and er["factor"] == 5.0 and er["edges"] == pv["plan"]["edges"]
    assert er["estimated_extra_cells"] == pytest.approx(pv["plan"]["estimated_extra_cells"])      # (the preview is the mesher's own plan)
    assert er["size"] == pytest.approx(pv["plan"]["h_edge"]) and s["sizes"]["near_wall"] == pytest.approx(pv["h_wall"])
    assert s["edge_refine_skipped"] is None
    assert f"{er['estimated_extra_cells']:,.0f}" in ctl.widget_do("mesh.edges.made")["text"]
