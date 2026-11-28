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
