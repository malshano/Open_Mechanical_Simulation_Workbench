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
