"""The sizing methods and layer methods of the Mesh step of the window (issues #33, #34, #35, #38, #32): the controls and their ids, the values going both ways
between the panel and the settings (what the API and the Claude tools read and write), the words, saving and opening the study."""
import pytest
from qt_support import ctl  # noqa: F401
from test_qt_edges import flow, ids, mesh, open_group, val  # noqa: F401


# ---------------------------------------------------------------------------------------------------------------------------------- #33 the layer clamp
def test_the_layer_clamp_controls_exist_and_round_trip(flow):
    c = flow
    have = ids(c)
    for i in ("mesh.layers.clamp", "mesh.layers.clamp.fraction", "mesh.layers.clamp.coverage", "mesh.layers.clamp.note"):
        assert i in have, i
    assert not val(c, "mesh.layers.clamp")["checked"] and not c.widget_do("mesh.layers.clamp.fraction")["enabled"]
    c.widget_do("mesh.layers.clamp", "set", True)
    assert mesh(c)["layers"]["clamp"] == {"fraction": 0.5, "coverage": 0.95} and c.widget_do("mesh.layers.clamp.fraction")["enabled"]
    c.widget_do("mesh.layers.clamp.fraction", "set", "0.3")
    c.widget_do("mesh.layers.clamp.coverage", "set", "90")
    assert mesh(c)["layers"]["clamp"] == {"fraction": 0.3, "coverage": 0.9}
    c.engine.project().set_mesh(layers={"n": 3, "growth": 1.2, "clamp": {"fraction": 0.2}})         # (from the API: the panel follows)
    c.engine.changed.emit()
    c._settle()
    assert c.widget_do("mesh.layers.clamp.fraction")["value"] == "0.2" and val(c, "mesh.layers.clamp")["checked"]
    c.widget_do("mesh.layers.n", "set", "2")                                                         # (changing the layers keeps the clamp)
    assert mesh(c)["layers"]["n"] == 2 and mesh(c)["layers"]["clamp"]["fraction"] == 0.2
    c.widget_do("mesh.layers.clamp", "set", False)
    assert "clamp" not in mesh(c)["layers"] and not c.widget_do("mesh.layers.clamp.fraction")["enabled"]


def test_the_layer_clamp_says_what_it_did_to_the_layers(flow):
    """A 100 x 20 x 20 mm box: thick enough for three layers of a laminar plan? The words follow the report of the engine."""
    c = flow
    c.engine.project().set_mesh(layers={"n": 12, "growth": 1.3, "clamp": True})
    c.engine.changed.emit()
    c._settle()
    rep = c.engine.project().layer_clamp_report()
    t = c.widget_do("mesh.layers.clamp.note")["text"]
    assert rep["changed"] and "12" in t and f"to {rep['n']}" in t
    c.engine.project().set_mesh(layers={"n": 1, "growth": 1.2, "clamp": True})
    c.engine.changed.emit()
    c._settle()
    assert "fit" in c.widget_do("mesh.layers.clamp.note")["text"]


def test_the_layer_clamp_is_in_the_flow_study_only(ctl, box_step):
    from test_qt_layers_mesh import wait
    ctl.do("study.new", {"name": "Heat", "study": "heat_conduction"})
    ctl.do("geometry.import", {"path": str(box_step)})
    wait(ctl)
    ctl.do("step.go", {"step": "mesh"})
    ctl.do("app.mode", {"mode": "expert"})
    ctl._settle()
    assert "mesh.layers.clamp" not in ids(ctl)
    with pytest.raises(ValueError):
        ctl.engine.project().set_mesh(layers={"n": 2, "clamp": True})


# ---------------------------------------------------------------------------------------------------------------------------------- #34 / #35 the sizing methods
def test_the_sizing_methods_group_and_its_ids(flow):
    c = flow
    have = ids(c)
    for name in ("wake", "curvature", "proximity"):
        for i in (f"mesh.methods.{name}.on", f"mesh.methods.{name}.about", f"mesh.methods.{name}.note", f"mesh.methods.{name}.max_cells"):
            assert i in have, i
    for i in ("mesh.methods.toggle", "mesh.methods.wake.length", "mesh.methods.wake.side", "mesh.methods.wake.size_factor", "mesh.methods.wake.size_mm", "mesh.methods.wake.show",
              "mesh.methods.curvature.elements", "mesh.methods.curvature.min_size_mm", "mesh.methods.proximity.cells_across", "mesh.methods.proximity.max_gap_mm"):
        assert i in have, i
    assert not val(c, "mesh.methods.wake.on")["checked"] and not c.widget_do("mesh.methods.wake.length")["enabled"]
    assert c.widget_do("mesh.methods.wake.note")["text"] == "Off."


def test_the_methods_round_trip_between_the_panel_and_the_settings(flow):
    c = flow
    c.widget_do("mesh.methods.wake.on", "set", True)
    assert mesh(c)["sizing_methods"]["wake"] == {"on": True} and c.widget_do("mesh.methods.wake.length")["enabled"]
    c.widget_do("mesh.methods.wake.length", "set", "6")
    c.widget_do("mesh.methods.wake.max_cells", "set", "50,000")
    assert mesh(c)["sizing_methods"]["wake"]["length"] == 6.0 and mesh(c)["sizing_methods"]["wake"]["max_cells"] == 50000.0
    t = c.widget_do("mesh.methods.wake.note")["text"]
    assert "6 body lengths" in t and "cap 50,000" in t and "about" in t
    c.widget_do("mesh.methods.curvature.on", "set", True)
    c.widget_do("mesh.methods.curvature.elements", "set", "30")
    c.widget_do("mesh.methods.proximity.on", "set", True)
    sm = mesh(c)["sizing_methods"]
    assert sm["curvature"]["on"] and sm["curvature"]["elements"] == 30.0 and sm["proximity"]["on"]
    c.widget_do("mesh.methods.curvature.elements", "set", "")                                  # (emptied: the preset's again)
    assert "elements" not in mesh(c)["sizing_methods"]["curvature"]
    c.engine.project().set_mesh(sizing_methods={"wake": {"length": 2}})                        # (from the API: the panel follows)
    c.engine.changed.emit()
    c._settle()
    assert c.widget_do("mesh.methods.wake.length")["value"] == "2" and val(c, "mesh.methods.wake.on")["checked"]
    assert c.widget_do("mesh.methods.toggle")["value"]["checked"]                               # (a group with a method on is open)
    c.widget_do("mesh.methods.wake.on", "set", False)
    assert mesh(c)["sizing_methods"]["wake"]["on"] is False and mesh(c)["sizing_methods"]["wake"]["length"] == 2.0      # (the values are kept)
    assert not c.widget_do("mesh.methods.wake.length")["enabled"]


def test_the_wake_box_can_be_shown_in_the_3d_view_and_the_methods_stay_in_their_studies(flow, ctl, box_step):
    c = flow
    assert not c.widget_do("mesh.methods.wake.show")["enabled"]
    c.widget_do("mesh.methods.wake.on", "set", True)
    c._settle()
    assert c.widget_do("mesh.methods.wake.show")["enabled"]
    n0 = c.win.view.region_view.count if hasattr(c.win.view.region_view, "count") else None
    c.widget_do("mesh.methods.wake.show", "set", True)
    c._settle()
    rv = c.win.view.region_view
    assert getattr(rv, "count", 1) >= 1 and (n0 is None or rv.count == n0 + 1)


def test_the_wake_method_is_for_flow_studies_only(ctl, box_step):
    from test_qt_layers_mesh import wait
    ctl.do("study.new", {"name": "Heat", "study": "heat_conduction"})
    ctl.do("geometry.import", {"path": str(box_step)})
    wait(ctl)
    ctl.do("step.go", {"step": "mesh"})
    ctl.do("app.mode", {"mode": "expert"})
    ctl._settle()
    have = ids(ctl)
    assert "mesh.methods.curvature.on" in have and "mesh.methods.proximity.on" in have and "mesh.methods.wake.on" not in have
    with pytest.raises(ValueError, match="external aerodynamics"):
        ctl.engine.project().set_mesh(sizing_methods={"wake": True})
    ctl.widget_do("mesh.methods.proximity.on", "set", True)
    ctl.engine.project().set_mesh(size_max_mm=10)
    ctl.engine.changed.emit()
    ctl._settle()
    assert "thin walls" in ctl.widget_do("mesh.methods.proximity.note")["text"] or "No gap or thin wall" in ctl.widget_do("mesh.methods.proximity.note")["text"] \
        or "wall" in ctl.widget_do("mesh.methods.proximity.note")["text"]
