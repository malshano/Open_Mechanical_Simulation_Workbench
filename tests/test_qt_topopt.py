"""Topology optimisation in the window: the Setup choices, the checks before the run, the shape drawing itself during the Solve step with its chart and
Stop, and a Results summary that explains itself (what is kept, the threshold, what the colours mean, what to do next)."""
import pytest
from qt_support import ctl, from_thread  # noqa: F401


def wait(c, t=900):
    assert from_thread(c.app, lambda: c.wait_idle(t), t + 20)["idle"]
    c._settle()


@pytest.fixture
def topo(ctl):
    c = ctl
    c.do("study.example", {"study": "topology_optimization"})
    wait(c)
    pr = c.engine.project()
    holes = [f["id"] for f in pr.faces() if f["surface_type"] == "Cylinder"]
    front = [f["id"] for f in pr.faces() if abs(f["normal"][0]) > 0.9 and abs(f["centroid"][0] - 3e-3) < 1e-4]
    pr.add_bc("Bolts", "fixed_support", holes)
    pr.add_bc("Camera", "force", front, {"fx_N": -20.0, "fy_N": 0, "fz_N": 0})
    c.engine.changed.emit()
    return c


def _text(c, id_):
    """The text of a widget or of the labels inside a note card."""
    from PySide6.QtWidgets import QLabel
    w = c._index()[id_]
    return w.text() if isinstance(w, QLabel) else " ".join(x.text() for x in w.findChildren(QLabel))


def _mesh(c):
    c.do("step.go", {"step": "mesh"})
    c.do("mesh.preset", {"preset": "coarse"})
    c.do("mesh.generate")
    wait(c)


def test_setup_asks_for_what_to_keep_in_plain_words_and_offers_a_per_part_choice(topo):
    c = topo
    c.do("step.go", {"step": "setup"})
    for wid_ in ("setup.setting.mass_percent", "setup.setting.member_mm"):
        assert c.widget_do(wid_)["visible"] is True
    body = c.engine.state()["body_list"][0]["id"]
    assert c.widget_do(f"setup.body.{body}.param.optimise")["visible"] is True
    tip = c._index()["setup.setting.mass_percent"].toolTip() + c._index()["setup.setting.member_mm"].toolTip()
    assert "stiffest" in tip and "mesh cells" in tip                                       # (each setting says what it does, in the user's words)
    types = [t["key"] for t in c.do("study.types")["result"]["types"]]
    assert "topology_optimization" in types


def test_before_the_run_the_step_says_what_it_will_need_and_what_blocks_it(topo):
    c = topo
    c.do("step.go", {"step": "solve"})
    assert "mesh" in c._index()["solve.run"].toolTip().lower() and c.widget_do("solve.run")["enabled"] is False
    _mesh(c)
    c.do("step.go", {"step": "solve"})
    note = _text(c, "solve.topopt.preflight")
    assert "Ready" in note and "elements" in note and "GB" in note and "kept solid" in note
    assert "Find the shape" in str(c._index()["solve.run"].text())
    # every part kept solid: nothing to optimise, said before the run with the way out
    body = c.engine.state()["body_list"][0]["id"]
    c.engine.project().set_body(body, optimise="keep solid")
    c.engine.changed.emit()
    c._settle()
    assert "cannot start" in _text(c, "solve.topopt.preflight") and "keep solid" in _text(c, "solve.topopt.preflight")
    assert c.widget_do("solve.run")["enabled"] is False
    c.engine.project().set_body(body, optimise="yes")
    c.engine.changed.emit()
    c._settle()
    assert c.widget_do("solve.run")["enabled"] is True


def test_the_shape_is_found_drawn_and_explained(topo):
    c = topo
    _mesh(c)
    c.do("step.go", {"step": "solve"})
    c.do("solve.run")
    wait(c)
    assert c.engine.state()["solve"]["status"] == "done"
    assert c.widget_do("solve.topopt.chart")["visible"] is True                            # (stiffness, mass and sharpness against the iterations)
    assert c.state()["view"]["overlay"]["name"] == "Density"                               # (the shape itself is in the 3D view, not the CAD model)
    assert c.widget_do("solve.stop")["visible"] is False                                   # (only while it runs)
    c.widget_do("solve.results", "click")
    assert c.state()["window"]["step"] == "results"
    head = _text(c, "results.topopt.headline")
    assert "keeps" in head and "mass" in head and "stiffness" in head
    for wid_ in ("results.topopt.threshold", "results.topopt.chart", "results.topopt.next"):
        assert c.widget_do(wid_)["visible"] is True
    nxt = _text(c, "results.topopt.next")
    assert "finished part" in nxt                                                          # (honest: not a finished part)
    assert "stress" in _text(c, "results.topopt.headline") and "concept" in _text(c, "results.topopt.headline")
    assert c.widget_do("results.topopt.check")["visible"] and c.widget_do("results.topopt.stl")["visible"]   # (the way on is under the headline)
    assert "Read before you rely" in _text(c, "results.topopt.cautions")
    shown = _text(c, "results.topopt.threshold.text")
    assert "% of the part's volume" in shown
    ov = c.state()["view"]["overlay"]
    assert ov["kind"] == "scalar" and ov["name"] == "Density"
    # a higher threshold leaves less material in the picture
    before = c.engine.topopt_shown()["share"]
    c.widget_do("results.topopt.threshold", "set", 90)
    assert c.engine.topopt_threshold() == pytest.approx(0.9)
    after = c.engine.topopt_shown()["share"]
    assert after < before
    assert c.state()["view"]["overlay"]["triangles"] <= ov["triangles"]
    # the summary numbers come from the run
    t = c.engine.state()["solve"]["summary"]["topopt"]
    assert 0 < t["stiffness_kept"] < 1 and t["mass_fraction"] == pytest.approx(0.3, abs=1e-6)


def test_stop_keeps_the_shape_so_far(topo, monkeypatch):
    c = topo
    _mesh(c)
    from simwb.topopt import simp
    real = simp.Optimiser.run

    def run(self, on_iter=None, should_stop=None):
        def stop_after_a_few(rec, xb):
            on_iter(rec, xb)
            if rec["iteration"] == 4:
                c.engine.project().stop_solve()                                           # (what the Stop button does)
        return real(self, on_iter=stop_after_a_few, should_stop=should_stop)
    monkeypatch.setattr(simp.Optimiser, "run", run)
    c.do("step.go", {"step": "solve"})
    c.do("solve.run")
    wait(c)
    t = c.engine.state()["solve"]["summary"]["topopt"]
    assert c.engine.state()["solve"]["status"] == "done" and t["stopped"] and t["iterations"] == 4
    assert t["mass_fraction"] == pytest.approx(0.3, abs=1e-6)


def test_the_shape_can_be_checked_and_saved_from_the_results(topo, tmp_path):
    c = topo
    _mesh(c)
    c.do("step.go", {"step": "solve"})
    c.do("solve.run")
    wait(c)
    c.widget_do("solve.results", "click")
    c.widget_do("results.topopt.check", "click")
    wait(c)
    c._settle()
    res = _text(c, "results.topopt.check.result")
    assert "von Mises" in res and "Stiffness kept" in res and "quadratic" in res
    c.widget_do("results.topopt.threshold", "set", 80)                                      # (moved on: the old check no longer describes the picture, and says so)
    assert "Press the button to check this one" in _text(c, "results.topopt.check.result")
    out = c.engine.topopt_export_stl(str(tmp_path / "shape.stl"))
    assert out["triangles"] > 100 and (tmp_path / "shape.stl").stat().st_size == 84 + 50 * out["triangles"]
