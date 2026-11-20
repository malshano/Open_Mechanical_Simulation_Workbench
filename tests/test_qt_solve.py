"""The Solve step and the bottom Messages / Solver log dock."""
import pytest
from qt_support import ctl, from_thread  # noqa: F401
from test_qt_theme import best_contrast, visible_controls  # noqa: F401


def wait(c):
    assert from_thread(c.app, lambda: c.wait_idle(600), 620)["idle"]
    c._settle()


def test_before_there_is_a_mesh_the_step_says_what_to_do(ctl):
    ctl.do("study.new", {"name": "Plate", "study": "heat_conduction"})
    ctl.do("step.go", {"step": "solve"})
    run = ctl.widget_do("solve.run")
    assert run["enabled"] is False and "mesh" in ctl._index()["solve.run"].toolTip().lower()
    assert ctl.widget_do("solve.results")["enabled"] is False and "run" in ctl._index()["solve.results"].toolTip().lower()
    assert ctl.widget_do("solve.cancel")["visible"] is False and ctl.widget_do("solve.stop")["visible"] is False   # (only while running)
    assert ctl._index()["solve.log"].placeholderText()                                    # an empty log explains itself, not a black box
    assert ctl.widget_do("solve.case")["visible"] is False                                # (a flow-study button)
    assert ctl.widgets("solve.software")["count"] == 1                                    # the "software used" fold
    assert [w["id"] for w in ctl.widgets("log.")["widgets"] if w["id"].startswith("log.")] == ["log.messages", "log.solver", "log.tab.messages", "log.tab.solver"]


def test_the_log_dock_lists_what_is_missing(ctl):
    ctl.do("study.new", {"name": "Plate", "study": "heat_conduction"})
    txt = ctl.widget_do("log.messages")["value"]
    assert "Missing" in txt and "Geometry" in txt                                          # a word, and the step it belongs to
    assert ctl.state()["docks"]["log"] is True
    ctl.do("dock.show", {"name": "log", "visible": False})
    assert ctl.state()["docks"]["log"] is False
    ctl.do("dock.show", {"name": "log"})


def test_a_whole_study_from_setup_to_results(ctl):
    """Heat plate: a heat source, a cold plate, mesh, run: the solve step shows the run and offers the results."""
    c = ctl
    c.do("study.example", {"study": "heat_conduction"})
    wait(c)
    pr = c.engine.project()
    bodies = c.engine.state()["body_list"]
    small = min(bodies, key=lambda b: b["volume_cm3"])["id"]
    c.do("step.go", {"step": "setup"})
    c.widget_do(f"setup.body.{small}.param.heat_W", "set", 10)
    bottom = [f["id"] for f in pr.faces() if f["surface_type"] == "Plane" and f["normal"][2] < -0.99 and not f.get("interface")]
    c.do("setup.bc_add", {"type": "fixed_temperature", "faces": bottom[:1], "name": "Cold plate"})
    c.do("step.go", {"step": "mesh"})
    c.do("mesh.preset", {"preset": "coarse"})
    c.do("mesh.generate")
    wait(c)
    c.do("step.go", {"step": "solve"})
    assert c.widget_do("solve.run")["enabled"] is True
    c.do("solve.run")
    wait(c)
    assert c.engine.state()["solve"]["status"] == "done"
    assert c.widget_do("solve.progress")["value"]["value"] == 1000
    assert "Done" in str(c.widget_do("solve.state")["value"])
    assert c.widget_do("solve.results")["enabled"] is True and c.widget_do("solve.cancel")["visible"] is False
    ev = c.widget_do("log.messages")["value"]
    assert "Solver" in ev or "Mesh" in ev                                                  # what finished is listed
    c.widget_do("solve.results", "click")
    assert c.state()["window"]["step"] == "results" and c.state()["view"]["overlay"]["kind"] == "scalar"


@pytest.mark.parametrize("name", ("dark", "light"))
def test_the_solve_step_is_legible(ctl, name):
    ctl.do("theme.set", {"mode": name})
    ctl.do("study.new", {"name": "Plate", "study": "static_structural"})
    ctl.do("step.go", {"step": "solve"})
    ctl._settle()
    weak, seen = [], 0
    for i, o in visible_controls(ctl):
        seen += 1
        bg, fg, r = best_contrast(o)
        if fg is None or r < 3.0:
            weak.append((i, round(r, 2)))
    assert seen > 8 and not weak, weak[:6]
    assert ctl.coverage()["complete"]
