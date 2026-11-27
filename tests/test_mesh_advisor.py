"""The mesh advisor for a body in a flow (simwb/meshadvisor.py): what a good mesh needs, worked out from the shape and the flow, and a scorecard of a finished mesh."""
import importlib
import math

import numpy as np
import pytest

import simwb.project as P_
from simwb import meshadvisor as MA
from simwb.solvers.openfoam import cases

WING = None


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMWB_HOME", str(tmp_path / "ws"))
    importlib.reload(P_)
    importlib.reload(MA)
    yield P_


def an_wing(**kw):
    """A hand-made analysis of a slender wing in a flow along +x: 0.314 m chord, 0.8 m span, 10 mm thick."""
    a = {"lo": [0.0, 0.0, -0.002], "hi": [0.314, 0.8, 0.008], "ext": [0.314, 0.8, 0.01], "flow": [1.0, 0.0, 0.0], "speed": 12.0, "axis": 0, "sign": 1.0,
         "length": 0.314, "span": 0.8, "depth": 0.01, "lateral_axes": [1, 2], "ref_length": 0.8, "feature": 0.0063, "thin": 0.01, "nu": 1.5e-5, "reynolds": 2.5e5,
         "boundary_layer": 0.0097, "turbulence": "kOmegaSST", "domain": {"upstream": 20.0, "downstream": 41.0, "side": 20.0, "size": [62.0, 43.0, 41.0]}}
    a.update(kw)
    return a


# ------------------------------------------------------------------------------------------------------------------ the recipe
def test_the_wall_cell_follows_the_length_along_the_flow_not_the_largest_side():
    r = MA.recipe(an_wing(), "medium", free_bytes=64e9)
    assert r["h_surf"] == pytest.approx(0.314 / 90)                         # (the old sizing took the 0.8 m span: 31 mm cells round a 10 mm wing)
    assert r["h_near"] == pytest.approx(3 * r["h_surf"]) and r["h_wake"] == pytest.approx(5 * r["h_surf"])
    assert r["growth"] == 1.2 and r["extend_boundary"] is False and r["edge_refinement"] == 4.0


def test_the_wake_box_starts_at_the_rear_of_the_body_and_follows_the_flow():
    r = MA.recipe(an_wing(), "medium", free_bytes=64e9)
    near, wake = r["boxes"]
    assert near["name"] == "Near field" and wake["name"] == "Wake"
    assert near["min"][0] == pytest.approx(-0.3 * 0.314) and near["max"][0] == pytest.approx(0.314 + 0.5 * 0.314)       # (0.3 lengths upstream, 0.5 behind)
    assert wake["min"][0] == pytest.approx(0.314) and wake["max"][0] == pytest.approx(0.314 + 4 * 0.314)                  # (from the rear, 4 lengths)
    assert wake["min"][1] == pytest.approx(-0.4 * 0.314) and wake["max"][1] == pytest.approx(0.8 + 0.4 * 0.314)           # (the width of the body and 0.4 lengths each side)
    # the flow the other way (-x): the same boxes, mirrored about the body
    b = MA.recipe(an_wing(flow=[-1.0, 0.0, 0.0], sign=-1.0), "medium", free_bytes=64e9)["boxes"][1]
    assert b["max"][0] == pytest.approx(0.0) and b["min"][0] == pytest.approx(-4 * 0.314)


def test_finer_presets_make_finer_cells_and_a_gentler_growth():
    sizes = [MA.recipe(an_wing(), p, free_bytes=1e15)["h_surf"] for p in ("quick", "coarse", "medium", "fine", "very_fine")]
    assert sizes == sorted(sizes, reverse=True)
    assert MA.recipe(an_wing(), "quick", free_bytes=1e15)["growth"] == 1.3 and MA.recipe(an_wing(), "very_fine", free_bytes=1e15)["growth"] == 1.15


def test_the_cells_are_kept_to_what_the_free_memory_holds():
    plenty = MA.recipe(an_wing(), "fine", free_bytes=64e9)
    short = MA.recipe(an_wing(), "fine", free_bytes=4e9)
    assert not plenty["estimate"]["reduced"] and short["estimate"]["reduced"]
    assert short["h_surf"] > plenty["h_surf"]                                 # (coarsened)
    assert short["estimate"]["cells_zones"] == pytest.approx(MA.memory_cap_cells(4e9), rel=0.01)       # (to the cap)
    assert any("free memory" in x for x in short["rationale"]) and not any("free memory" in x for x in plenty["rationale"])
    assert MA.memory_cap_cells(8e9) == pytest.approx(0.5 * 8e9 / MA.BYTES_PER_CELL)


def test_the_layers_match_the_wall_treatment_and_the_thinnest_part():
    r = MA.recipe(an_wing(), "medium", free_bytes=64e9)
    lay = r["layers"]
    assert lay["y_plus"] == MA.WALL_FUNCTION_Y_PLUS == 40.0
    # the first layer is 2 x y+ x nu / u_tau: y+ is measured at its centre
    assert lay["first_mm"] / 1000 == pytest.approx(cases.layer_first_thickness(12.0, 0.314, 1.5e-5, (), 40.0))
    assert lay["fit"] is not None and lay["n"] == min(lay["fit"], 5)           # (as many as fit, at most 5)
    thin = MA.recipe(an_wing(feature=0.0015), "medium", free_bytes=64e9)["layers"]
    assert thin["n"] == 0 or thin["n"] < lay["n"]                                # (a thinner part leaves room for fewer)
    lam = MA.recipe(an_wing(turbulence="laminar"), "medium", free_bytes=64e9)["layers"]
    assert lam["y_plus"] is None and lam["first_mm"] is None                   # (laminar: the layer planner sizes the first layer from the boundary layer)


def test_the_first_layer_is_twice_the_wall_distance_of_the_y_plus():
    """y+ is measured at the centre of the first cell: the layer is twice as thick as the distance of that y+ from the wall."""
    V, L, nu = 12.0, 0.314, 1.5e-5
    cf = 0.058 * (V * L / nu) ** -0.2
    assert cases.layer_first_thickness(V, L, nu, (), 30.0) == pytest.approx(2 * 30.0 * nu / (V * math.sqrt(cf / 2)))
    assert cases.layer_plan(V, L, nu, "kOmegaSST", 5, 1.2)["first"] == pytest.approx(cases.layer_first_thickness(V, L, nu, (), 30.0))


# ------------------------------------------------------------------------------------------------------------------ the scorecard
def ms_good(**kw):
    m = {"cells": 900000, "surface_median": 0.0032, "surface_p90": 0.006, "thin_median": 0.0015, "near_p90": 0.012, "near_cells": 1, "wake_p90": 0.02, "wake_cells": 1,
         "jump_p99": 1.4, "jump_max": 2.0, "quality_min": 0.2, "poor": 0}
    m.update(kw)
    return m


def levels(sc):
    return {c["id"]: c["level"] for c in sc["checks"]}


def test_a_good_mesh_scores_ok_on_every_geometric_check():
    sc = MA.scorecard(an_wing(), ms_good(), {"foam_quality": {"non_orthogonality_max": 55, "skewness_max": 1.5}})
    lv = levels(sc)
    assert all(lv[k] == "ok" for k in ("surface", "thin", "near", "wake", "gradation", "domain", "nonortho", "skewness", "quality"))
    assert sc["bad"] == 0 and sc["score"] == 1.0 and lv["cells"] == "info"


def test_each_check_has_its_three_bands_and_says_what_to_do():
    a = an_wing()
    c = a["length"]
    cases_ = [("surface", "surface_median", (0.01 * c, 0.02 * c, 0.05 * c)), ("near", "near_p90", (0.04 * c, 0.07 * c, 0.2 * c)),
              ("wake", "wake_p90", (0.07 * c, 0.12 * c, 0.5 * c)), ("gradation", "jump_p99", (1.5, 2.0, 3.0))]
    for cid, key, (ok, warn, bad) in cases_:
        got = [levels(MA.scorecard(a, ms_good(**{key: v})))[cid] for v in (ok, warn, bad)]
        assert got == ["ok", "warn", "bad"], (cid, got)
        advice = next(x["advice"] for x in MA.scorecard(a, ms_good(**{key: bad}))["checks"] if x["id"] == cid)
        assert advice and ("Smart sizing" in advice or "smart sizing" in advice or "refine" in advice.lower())
    thin = [levels(MA.scorecard(a, ms_good(thin_median=0.0063 / x)))["thin"] for x in (6, 3, 1.5)]
    assert thin == ["ok", "warn", "bad"]


def test_the_domain_must_be_big_enough_and_a_huge_one_is_only_noted():
    small = MA.scorecard(an_wing(domain={"upstream": 3.0, "downstream": 6.0, "side": 3.0, "size": [10.0, 6.0, 6.0]}), ms_good())
    assert levels(small)["domain"] == "bad"
    mid = MA.scorecard(an_wing(domain={"upstream": 5.0, "downstream": 10.0, "side": 5.0, "size": [16.0, 10.0, 10.0]}), ms_good())
    assert levels(mid)["domain"] == "warn"
    assert levels(MA.scorecard(an_wing(), ms_good()))["domain_big"] == "info"       # (62 lengths: noted, not scored)


def test_layers_and_their_y_plus_are_judged_from_the_first_layer():
    base = {"made": True, "layers_max": 3, "extruded_percent": 99.0, "first_mm": 2.0, "non_orthogonality_max": 50.0, "skewness_max": 2.0, "warnings": []}
    ok = levels(MA.scorecard(an_wing(), ms_good(), {"layers": base}))
    assert ok["layers"] == "ok" and ok["yplus"] == "ok"                          # (a 2 mm layer: y+ about 40 at its centre)
    buffer = levels(MA.scorecard(an_wing(), ms_good(), {"layers": dict(base, first_mm=0.85)}))
    assert buffer["yplus"] == "warn"                                             # (the buffer layer: 5 to 30)
    assert levels(MA.scorecard(an_wing(), ms_good(), {"layers": dict(base, first_mm=0.02)}))["yplus"] == "ok"      # (resolved)
    lost = levels(MA.scorecard(an_wing(), ms_good(), {"layers": dict(base, extruded_percent=70.0, warnings=["Only 70% ..."])}))
    assert lost["layers"] == "bad"
    none = levels(MA.scorecard(an_wing(), ms_good(), {"layers": {"made": False, "reason": "OpenFOAM is not installed here"}}))
    assert none["layers"] == "warn" and "yplus" not in none
    assert levels(MA.scorecard(an_wing(), ms_good(), {}))["layers"] == "info"      # (nothing asked, nothing said)


def test_quality_limits_come_from_the_layered_mesh_when_there_is_one():
    st = {"foam_quality": {"non_orthogonality_max": 40, "skewness_max": 1.0},
          "layers": {"made": True, "extruded_percent": 99.0, "first_mm": 2.0, "non_orthogonality_max": 78.0, "skewness_max": 3.6}}
    lv = levels(MA.scorecard(an_wing(), ms_good(), st))
    assert lv["nonortho"] == "bad" and lv["skewness"] == "warn"                  # (the mesh the solver runs, not the tetrahedra without the layers)
    assert levels(MA.scorecard(an_wing(), ms_good(poor=12, quality_min=0.05)))["quality"] == "bad"


def test_the_score_is_the_share_of_the_checks_that_pass_with_warnings_counting_half():
    sc = MA.scorecard(an_wing(), ms_good(wake_p90=0.12 * 0.314, near_p90=0.07 * 0.314))
    scored = [c for c in sc["checks"] if c["level"] != "info"]
    assert sc["score"] == pytest.approx(sum({"ok": 1, "warn": 0.5, "bad": 0}[c["level"]] for c in scored) / len(scored), abs=0.01)
    assert sc["warn"] == 2 and sc["ok"] + sc["warn"] + sc["bad"] == len(scored)


# ------------------------------------------------------------------------------------------------------------------ on a real study
def _project(P, step, study="external_aero"):
    pr = P.create_project("Adv", study)
    j = pr.import_step(step, original_name=step.name)
    assert j.wait(300) and j.status == "done", j.error
    return pr


@pytest.fixture
def wing(workspace):
    from pathlib import Path
    pr = _project(workspace, Path(__file__).resolve().parents[1] / "examples" / "airplane_wing.step")
    pr.set_settings(speed=12)
    return pr


def test_the_analysis_reads_the_wing_the_way_the_flow_sees_it(wing):
    adv = wing.mesh_advice()
    a = adv["analysis"]
    assert a["length"] == pytest.approx(0.314, abs=0.002) and a["span"] == pytest.approx(0.8, abs=0.002)      # (along the flow, across it)
    assert a["feature"] == pytest.approx(0.0063, abs=0.001) and 2e5 < a["reynolds"] < 3e5
    assert a["domain"]["upstream"] > 8 and adv["recipe"]["h_surf"] < 0.314 / 30 and adv["scorecard"] is None and adv["smart"] is False


def test_the_advisor_is_for_flow_studies_with_a_geometry(workspace, tmp_path):
    pr = workspace.create_project("Solid", "static_structural")
    with pytest.raises(ValueError, match="external aerodynamics"):
        pr.mesh_advice()
    with pytest.raises(ValueError, match="external aerodynamics"):
        pr.set_mesh(smart=True)
    flow = workspace.create_project("Flow", "external_aero")
    with pytest.raises(ValueError, match="Import the geometry"):
        flow.mesh_advice()


def test_the_recommended_setup_switches_smart_sizing_on_and_sets_the_layers(wing):
    r = wing.apply_mesh_recipe(preset="coarse")
    assert r["smart"] is True and wing.data["mesh"]["smart"] is True and wing.data["mesh"]["preset"] == "coarse"
    lay = wing.data["mesh"]["layers"]
    assert lay["y_plus"] == 40.0 and 1 <= lay["n"] <= 5 and lay["n"] == r["recipe"]["layers"]["n"]
    wing.set_mesh(smart=False)
    assert "smart" not in wing.data["mesh"]
    wing.apply_mesh_recipe(layers=False)                                         # (layers: false leaves them alone)
    assert wing.data["mesh"]["layers"]["y_plus"] == 40.0 and wing.data["mesh"]["smart"] is True


def test_smart_sizing_meshes_the_wake_and_the_near_field_that_the_old_sizing_left_coarse(wing):
    """The same wing, the same preset: the cells of the wake and the near field are a fraction of their old size, the mesh is still good, and the advisor says
    what it did (the recipe is kept in the statistics, the scorecard is made by the mesh job)."""
    wing.set_mesh(preset="quick", smart=False, layers={"n": 0, "growth": 1.2})
    assert wing.generate_mesh().wait(900)
    old = wing.mesh_advice()["scorecard"]
    wing.set_mesh(smart=True)
    j = wing.generate_mesh()
    assert j.wait(900) and j.status == "done", (j.error, j.log[-6:])
    st = wing.data["mesh"]["stats"]
    new = wing.mesh_advice()["scorecard"]
    assert st["recipe"]["h_surf"] > 0 and st["recipe"]["boxes"][1]["name"] == "Wake" and st["advice"]["score"] == new["score"]
    assert new["measured"]["wake_p90"] < 0.7 * old["measured"]["wake_p90"]       # (cells behind the wing)
    assert new["measured"]["near_p90"] < old["measured"]["near_p90"]
    # (one more failed check is allowed: the recipe's edge refinement (4x finer cells along the trailing edge) is now really made - it used to be cut back to the wall size
    # by the mesher's smallest size - and puts about a hundred cells just under 0.1 quality there; the other checks are the point of this test)
    assert new["bad"] <= old["bad"] + 1 and new["measured"]["quality_min"] > 0.05
    assert st["foam_quality"]["non_orthogonality_max"] < 80                      # (OpenFOAM can run it)


def test_a_stale_mesh_is_scored_once_and_the_score_is_kept(wing):
    wing.set_mesh(preset="quick", layers={"n": 0, "growth": 1.2})
    assert wing.generate_mesh().wait(900)
    wing.data["mesh"]["stats"].pop("advice")                                      # (a mesh made before the advisor existed)
    assert wing.mesh_advice()["scorecard"]["score"] > 0
    assert "advice" in wing.data["mesh"]["stats"]


def test_the_assistant_and_the_rest_interface_reach_the_advisor(wing):
    from simwb import tools
    d = tools.mesh_advice(wing, {})
    assert d["recipe"]["boxes"] and d["analysis"]["length"] > 0
    r = tools.mesh_apply_recipe(wing, {"preset": "quick", "layers": False})
    assert r["smart"] and wing.data["mesh"]["preset"] == "quick"
    assert {t[0] for t in tools.TOOLS} >= {"mesh_advice", "mesh_apply_recipe"}                  # (the assistant can call them)
    assert tools.set_mesh(wing, {"smart": False}) and "smart" not in wing.data["mesh"]
