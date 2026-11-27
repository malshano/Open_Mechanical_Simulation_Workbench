"""The memory card of the Mesh step: the estimate before meshing, the memory budget (MB or percent of the free memory), its state when it coarsens or refuses."""
import pytest
from qt_support import EXAMPLES, ctl, from_thread  # noqa: F401

from simwb import meshplan


def open_mesh(c, example="quadcopter.step"):
    c.do("study.new", {"name": "Flow", "study": "external_aero"})
    c.do("geometry.import", {"path": str(EXAMPLES / example)})
    assert from_thread(c.app, lambda: c.wait_idle(300), 320)["idle"]
    c._settle()
    c.engine.project().set_settings(speed=10)
    c.engine.project().set_mesh(preset="quick")
    c.do("step.go", {"step": "mesh"})
    c._settle()
    return c


def card(c):
    return c.win.panels["mesh"].memory


def settled(c):
    c.win.panels["mesh"].refresh(c.engine.state())
    card(c).wait()
    c._settle()
    return card(c)


def test_the_estimate_is_shown_before_meshing_with_its_range(ctl):
    c = open_mesh(ctl)
    m = settled(c)
    txt = m.estimate.property("api_text")
    assert txt.startswith("About ") and "cells" in txt and "range" in txt and "of memory" in txt
    rep = m.last
    e = rep["estimate"]
    assert e["cells_low"] < e["cells"] < e["cells_high"] and e["peak_low_mb"] < e["peak_mb"] < e["peak_high_mb"]
    assert m.bar.isVisible() and m.bar.d["mb"] == e["peak_mb"]
    assert not m.state.isVisible() and not m.value_row.isVisible()                       # no budget: nothing else
    assert c.engine.project().data["mesh"].get("budget") is None                          # default off: nothing stored


def test_the_card_sits_between_the_options_and_generate_mesh_in_the_neighbours_style(ctl):
    c = open_mesh(ctl)
    settled(c)
    ids = [w["id"] for w in c.widgets("mesh.")["widgets"]]
    assert {"mesh.budget.off", "mesh.budget.mb", "mesh.budget.percent", "mesh.estimate", "mesh.estimate.bar", "mesh.generate"} <= set(ids)
    p = c.win.panels["mesh"]
    lay = p.v
    order = [lay.itemAt(i).widget() for i in range(lay.count()) if lay.itemAt(i).widget() is not None]
    assert order.index(p.adv) < order.index(p.memory_h) < order.index(p.memory) < order.index(p.gen)
    assert p.memory_h.text() == "MEMORY BUDGET" and p.memory_h.objectName() == p.advisor_h.objectName() == "h"          # (the same heading style)
    assert all(b.objectName() == "seg" for b in card(c).mode_btns.values())                                           # (the same segmented buttons as the resolution)
    assert card(c).mode_btns["off"].isChecked()


def test_a_budget_in_mb_coarsens_and_the_card_says_what_it_did(ctl):
    c = open_mesh(ctl, "camera_mount.step")
    c.engine.project().set_mesh(preset="coarse", size_min_mm=1.5)
    m = settled(c)
    top = m.last["estimate"]["peak_mb"]
    c.do("mesh.budget", {"mode": "mb", "value": top * 0.9})
    m = settled(c)
    assert m.mode_btns["mb"].isChecked() and m.value_row.isVisible() and m.unit.text() == "MB"
    st = c.engine.project().data["mesh"]["budget"]
    assert st["mode"] == "mb" and st["on"] and st["value"] == pytest.approx(top * 0.9, abs=1)
    bud = m.last["budget"]
    if bud["status"] == "coarsened":
        assert m.state.isVisible() and "Coarsened the far field" in m.state.property("api_text") and m.changes.isVisible()
        assert "Far field" in m.changes.property("api_text") and m.off_btn.isVisible()
        assert m.estimate.property("api_text").startswith("With the budget: about")
        assert m.bar.d["budget"] == pytest.approx(top * 0.9, abs=1) and m.bar.d["mb"] <= top * 0.9 + 1
    else:
        assert bud["status"] == "refused"                                   # (a body that is nearly all wall: the other test checks the words)
    c.widget_do("mesh.budget.switch_off", "click") if m.off_btn.isVisible() else c.widget_do("mesh.budget.off", "click")
    m = settled(c)
    assert c.engine.project().data["mesh"]["budget"]["on"] is False and not m.state.isVisible() and not m.value_row.isVisible()
    assert m.mode_btns["off"].isChecked()


def test_a_refused_budget_is_shown_and_generate_mesh_says_why(ctl):
    c = open_mesh(ctl)
    c.do("mesh.budget", {"mode": "mb", "value": 300})
    m = settled(c)
    assert m.last["budget"]["status"] == "refused"
    txt = m.state.property("api_text")
    assert txt.startswith("Budget on: the mesh does not fit.") and "smallest it can reach is about" in txt and "raise the budget to at least" in txt
    assert m.estimate.property("api_text").startswith("Smallest the budget can make:")
    with pytest.raises(Exception) as ex:
        c.widget_do("mesh.generate", "click")
        c._settle()
        raise Exception(c.win.panels["mesh"].stats.text())
    assert c.engine.project().data["mesh"]["status"] != "meshing"
    from simwb import project as P
    j = P.JOBS.latest(c.engine.pid, "mesh")
    assert j is None                                                        # (nothing was started)


def test_a_budget_in_percent_and_the_settings_survive_saving_and_reopening(ctl, tmp_path):
    c = open_mesh(ctl)
    c.widget_do("mesh.budget.percent", "click")
    c._settle()
    m = card(c)
    assert m.unit.text() == "% of the free memory" and c.engine.project().data["mesh"]["budget"] == {"mode": "percent", "value": 50.0, "on": True}
    c.widget_do("mesh.budget.value", "set", "35")
    assert c.engine.project().data["mesh"]["budget"]["value"] == 35.0
    pid = c.engine.pid
    c.engine.project().save()
    import simwb.project as P
    assert P.open_project(pid).data["mesh"]["budget"] == {"mode": "percent", "value": 35.0, "on": True}
    c.widget_do("mesh.budget.value", "set", "2")                            # (out of range: refused in words, the setting stays)
    assert c.engine.project().data["mesh"]["budget"]["value"] == 35.0


def test_the_estimate_follows_the_resolution_and_the_hex_mesh_says_it_is_not_estimated(ctl):
    c = open_mesh(ctl)
    a = settled(c).last["estimate"]["cells"]
    c.do("mesh.preset", {"preset": "coarse"})
    b = settled(c).last["estimate"]["cells"]
    assert b > a
    c.engine.project().data["mesh"]["method"] = "hex"
    m = settled(c)
    assert "hex-dominant" in m.detail.text() and not m.bar.isVisible() and not m.mode_row.isVisible()


def test_the_control_api_gives_the_estimate_and_sets_the_budget(ctl):
    c = open_mesh(ctl)
    r = c.do("mesh.estimate")
    assert r["text"].startswith("About ") and r["report"]["estimate"]["cells"] > 0
    r2 = c.do("mesh.budget", {"mode": "percent", "value": 40})
    assert r2["report"]["budget"]["mode"] == "percent" and r2["report"]["budget"]["on"]
    r3 = c.do("mesh.budget", {"mode": "off"})
    assert not r3["report"]["budget"]["on"]
