"""The Mesh step's advisor card: the smart-sizing switch, the recommended setup, what the recipe asks for and, after meshing, the scorecard."""
import re

import pytest
from qt_support import ctl, from_thread  # noqa: F401
from test_qt_theme import best_contrast, visible_controls  # noqa: F401

import simwb.project as P


def wait(c, t=300):
    assert from_thread(c.app, lambda: c.wait_idle(t), t + 20)["idle"]
    c._settle()


def text(c, i):
    return re.sub("<[^>]+>", "", c.widget_do(i)["value"] or "")


@pytest.fixture
def flow(ctl, box_step, monkeypatch):
    monkeypatch.setattr(P.Project, "_make_layers_inner", lambda self, *a, **k: {"made": False, "reason": "none in this test"})
    ctl.do("study.new", {"name": "Flow", "study": "external_aero"})
    ctl.do("geometry.import", {"path": str(box_step)})
    wait(ctl)
    ctl.engine.project().set_settings(speed=10)
    ctl.engine.project().set_mesh(preset="quick")
    ctl.do("step.go", {"step": "mesh"})
    ctl.do("app.mode", {"mode": "simple"})                                         # (the advisor is not an expert-only block)
    ctl._settle()
    return ctl


def test_the_advisor_is_there_for_a_flow_study_even_in_simple_mode_and_says_what_it_asks_for(flow):
    c = flow
    assert c.widget_do("mesh.advisor")["visible"] is True and c.widget_do("mesh.smart")["visible"] is True
    assert c.widget_do("mesh.smart")["value"] == {"checked": False}
    why = text(c, "mesh.advisor.why")
    assert why.startswith("Off: the old sizing is used.") and "wall cells" in why and "near field" in why and "wake" in why
    assert "Cells along the" in c.widget_do("mesh.advisor.why")["tooltip"]            # (the reasons, line by line, in the tooltip)
    assert c.widget_do("mesh.advisor.table")["visible"] is False                      # (nothing to score yet)


def test_the_switch_turns_smart_sizing_on_and_off(flow):
    c = flow
    c.widget_do("mesh.smart", "click")
    assert c.engine.state()["mesh"]["smart"] is True and not text(c, "mesh.advisor.why").startswith("Off")
    c.widget_do("mesh.smart", "click")
    assert not c.engine.state()["mesh"].get("smart")


def test_the_recommended_setup_button_sets_smart_sizing_and_the_layers(flow):
    c = flow
    c.do("mesh.recipe")
    m = c.engine.state()["mesh"]
    assert m["smart"] is True and m["layers"]["y_plus"] == 40.0 and m["layers"]["n"] >= 1
    assert c.widget_do("mesh.smart")["value"] == {"checked": True}
    assert "Wall layers:" in text(c, "mesh.advisor.why")


def test_after_meshing_the_scorecard_lists_every_check_with_what_to_do(flow):
    c = flow
    c.do("mesh.recipe")
    c.do("mesh.generate")
    wait(c)
    sc = c.engine.state()["mesh"]["stats"]["advice"]
    assert sc["checks"] and c.engine.state()["mesh"]["stats"]["recipe"]["h_surf"] > 0
    assert c.widget_do("mesh.advisor.score")["visible"] and c.widget_do("mesh.advisor.table")["visible"]
    s = text(c, "mesh.advisor.score")
    assert s.startswith("Mesh score") and f"{sc['ok']} checks ok" in s
    t = text(c, "mesh.advisor.table")
    for label in ("Wall cells", "Near field", "Wake", "Change of cell size", "Distance to the domain walls", "Poorest cell"):
        assert label in t, label
    adv = c.do("mesh.advice")["result"]
    assert adv["smart"] is True and adv["scorecard"]["score"] == sc["score"]


def test_changed_settings_say_the_mesh_is_out_of_date_in_the_score(flow):
    c = flow
    c.do("mesh.generate")
    wait(c)
    c._settle()
    assert "the setup changed" not in text(c, "mesh.advisor.score")
    c.widget_do("mesh.smart", "click")
    c._settle()
    assert "generate the mesh again" in text(c, "mesh.advisor.score")


def test_a_solid_study_has_no_advisor(ctl):
    ctl.do("study.new", {"name": "Solid", "study": "static_structural"})
    ctl.do("step.go", {"step": "mesh"})
    p = ctl.win.panels["mesh"]
    assert not p.advisor.isVisibleTo(p) and not p.advisor_h.isVisibleTo(p)


@pytest.mark.parametrize("name", ("dark", "light"))
def test_the_advisor_is_legible_in_both_themes(flow, name):
    c = flow
    c.do("mesh.recipe")
    c.do("mesh.generate")
    wait(c)
    c.do("theme.set", {"mode": name})
    from PySide6.QtWidgets import QScrollArea
    p = c.win.panels["mesh"].advisor.parent()
    while p is not None and not isinstance(p, QScrollArea):
        p = p.parent()
    p.ensureWidgetVisible(c.win.panels["mesh"].advisor.table, 0, 200)
    c._settle()
    weak, seen = [], 0
    for i, o in visible_controls(c):
        if not i.startswith("mesh.advisor") and i != "mesh.smart":
            continue
        seen += 1
        bg, fg, r = best_contrast(o)
        if fg is not None and fg != "off screen" and r < 3.0:
            weak.append((i, round(r, 2)))
    assert seen >= 3 and not weak, weak


def test_the_statistics_the_layer_card_and_the_advisor_wrap_instead_of_widening_the_panel(flow):
    """A widget that cannot be narrower than 400 px makes the whole panel wider than its dock (about 360 px of content): the right edge of the buttons and text
    is then clipped, as the statistics table did when the layer rows were added. These wrap their text (the old cell budget bars are about 400 px)."""
    c = flow
    c.do("mesh.recipe")
    c.do("mesh.generate")
    wait(c)
    p = c.win.panels["mesh"]
    for name, w in (("statistics", p.grid), ("layer card", p.layer_card), ("advisor", p.advisor)):
        assert w.minimumSizeHint().width() <= 400, (name, w.minimumSizeHint().width())
    assert p.advisor.table.minimumSizeHint().width() < 250 and p.advisor.why.minimumSizeHint().width() < 250     # (they take the width there is)
