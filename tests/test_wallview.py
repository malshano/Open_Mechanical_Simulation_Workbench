"""The wall-layer view after a solve (#43): layers per wall and the y+ of the result. The result is made here (a small box of air with a wall y+ of known
values), not by a solver run."""
import numpy as np
import pytest
from qt_support import ctl, from_thread  # noqa: F401
from test_qt_flow_results import _box_results  # noqa: F401

from simwb import wallview


def result_with_yplus():
    """The box of air of the flow tests: the 'body' wall (lowest x slab) gets y+ 3 on one half and 120 on the other, the 'farfield' has none."""
    rs = _box_results()
    n = len(rs.surf_tris)
    body = rs.surf_group == rs.groups.index("body")
    c = rs.nodes[rs.surf_tris].mean(1)
    y = np.zeros(n)
    y[body] = np.where(c[body, 1] < 0.5, 3.0, 120.0)
    rs.surf_values = {"y+": y}
    rs.fields["y+"] = ("cell", np.full(len(rs.tets), np.nan), "-")
    return rs, body, c


def test_the_rows_carry_the_layers_and_the_bands_of_each_wall():
    rs, body, c = result_with_yplus()
    layers = [{"name": "body", "faces": 100, "target": 3, "made": 2.6, "first_mm": 0.4, "total_mm": 1.1, "percent": 88.0}]
    rows = wallview.patch_rows(rs, layers)
    assert [r["name"] for r in rows] == ["body"]                                    # (the far field has no y+: no row)
    r = rows[0]
    assert r["layers"] == {"made": 2.6, "target": 3, "first_mm": 0.4, "total_mm": 1.1, "percent_of_plan": 88.0}
    sh = r["yplus"]["shares"]
    area = 0.5 * np.linalg.norm(np.cross(rs.nodes[rs.surf_tris[body][:, 1]] - rs.nodes[rs.surf_tris[body][:, 0]],
                                         rs.nodes[rs.surf_tris[body][:, 2]] - rs.nodes[rs.surf_tris[body][:, 0]]), axis=1)
    low = area[c[body, 1] < 0.5].sum() / area.sum() * 100
    assert sh["resolved"] == pytest.approx(low, abs=0.1) and sh["log"] == pytest.approx(100 - low, abs=0.1) and sh["buffer"] == sh["high"] == 0
    assert r["yplus"]["min"] == 3.0 and r["yplus"]["max"] == 120.0 and 3.0 <= r["yplus"]["mean"] <= 120.0
    assert wallview.patch_rows(rs, None)[0]["layers"] is None                       # (no layers known: said so, not invented)


def test_the_verdict_says_what_the_y_plus_means_for_the_wall_treatment():
    ok = [{"area_m2": 1.0, "yplus": {"shares": {"resolved": 0, "buffer": 5, "log": 90, "high": 5}}}]
    assert wallview.verdict(ok)[0] == "ok"
    bad = [{"area_m2": 1.0, "yplus": {"shares": {"resolved": 50, "buffer": 30, "log": 20, "high": 0}}}]
    tone, words = wallview.verdict(bad)
    assert tone == "warn" and "20 %" in words and "buffer layer" in words
    assert wallview.verdict(bad, laminar=True)[0] == "warn" and wallview.verdict([{"area_m2": 1.0, "yplus": {"shares": {"resolved": 95, "buffer": 5, "log": 0, "high": 0}}}], True)[0] == "ok"


def test_the_view_needs_a_flow_study_and_a_result_with_y_plus(ctl):
    ctl.do("study.new", {"name": "Solid", "study": "static_structural"})
    with pytest.raises(ValueError, match="external aerodynamics"):
        ctl.engine.project().wall_layer_view()
    ctl.do("study.new", {"name": "Flow", "study": "external_aero"})
    pr = ctl.engine.project()
    out = pr.wall_layer_view()
    assert not out["available"] and "No results" in out["reason"]
    rs, *_ = result_with_yplus()
    del rs.surf_values["y+"]
    pr._results = rs
    assert "no wall y+" in pr.wall_layer_view()["reason"]


def test_the_results_summary_draws_the_layers_and_the_y_plus_of_each_wall(ctl):
    ctl.do("study.new", {"name": "Flow", "study": "external_aero"})
    pr = ctl.engine.project()
    pr._results, *_ = result_with_yplus()
    pr.data["mesh"]["stats"] = {"method": "tet", "cells": 10, "layers": {"made": True, "stale": False, "patches": [
        {"name": "body", "faces": 100, "target": 3, "made": 2.6, "first_mm": 0.4, "total_mm": 1.1, "percent": 88.0}]}}
    pn = ctl.win.panels["results"]
    pn._summary({"study": "external_aero"}, {"drag_N": 0.01, "speed_m_s": 10.0, "convergence": {"converged": True}}, True)
    t = ctl.widget_do("results.walls.0.text")["text"]
    assert "2.6 of 3 layers" in t and "first 0.4 mm" in t and "1.1 mm in all" in t and "88 % of the planned thickness" in t and "y+ mean" in t
    assert ctl.widget_do("results.walls.0.bar")["visible"] is not None and ctl.widget_do("results.walls.verdict")["value"].startswith("⚠")
    pr.data["mesh"]["stats"]["layers"]["stale"] = True                              # (the layers are those of another mesh: not shown against this run)
    pn._summary({"study": "external_aero"}, {"drag_N": 0.01, "speed_m_s": 10.0, "convergence": {"converged": True}}, True)
    assert "not known" in ctl.widget_do("results.walls.0.text")["text"]
