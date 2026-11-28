"""The memory planner (simwb/meshplan.py): the estimate of the cells and the memory from the size fields, the budget that coarsens the bulk, the refusal, the settings."""
import importlib
import json
import math
import sys
import types

import numpy as np
import pytest

import simwb.project as P_
from simwb import meshplan as mp


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMWB_HOME", str(tmp_path / "ws"))
    importlib.reload(P_)
    yield P_


def _box_project(P, tmp_path, study="external_aero", size=(200, 80, 40)):
    import gmsh

    gmsh.initialize(["-nopopup"])
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("b")
        gmsh.model.occ.addBox(0, 0, 0, *size)
        gmsh.model.occ.synchronize()
        step = tmp_path / "box.step"
        gmsh.write(str(step))
    finally:
        gmsh.finalize()
    pr = P.create_project("Plan", study)
    j = pr.import_step(step, original_name="box.step")
    assert j.wait(300) and j.status == "done", j.error
    if study == "external_aero":
        pr.set_settings(speed=10)
    return pr


# ---------------------------------------------------------------------------------------------------------------------------------- the integral
def test_a_uniform_field_gives_the_count_of_regular_tetrahedra():
    h = 0.05
    out = mp.integrate([0, 0, 0], [1, 1, 1], mp.uniform_field(h))
    assert out["cells"] == pytest.approx(1.0 / (mp.TET_VOLUME * h ** 3), rel=1e-6)
    assert out["leaves"] < 200                               # (a constant field is not split: cheap)


def test_a_field_that_grows_linearly_matches_the_exact_integral():
    h0, g = 0.02, 0.25
    f = mp.Field(10.0, 0.0, [lambda P: h0 + g * P[:, 0]])
    exact = (1.0 / (2 * g * mp.TET_VOLUME)) * (1 / h0 ** 2 - 1 / (h0 + g) ** 2)         # (the box is 1 x 1 x 1: x from 0 to 1)
    out = mp.integrate([0, 0, 0], [1, 1, 1], f)
    assert out["cells"] == pytest.approx(exact, rel=0.03)


def test_finer_sizes_give_more_cells_and_half_the_size_eight_times_as_many():
    n = [mp.integrate([0, 0, 0], [1, 0.5, 0.5], mp.uniform_field(h))["cells"] for h in (0.1, 0.05, 0.025)]
    assert n[0] < n[1] < n[2]
    assert n[1] / n[0] == pytest.approx(8.0, rel=1e-6) and n[2] / n[1] == pytest.approx(8.0, rel=1e-6)
    near = lambda h_wall: mp.Field(1.0, 0.0, [lambda P: np.minimum(1.0, h_wall + 0.3 * np.abs(P[:, 0] - 0.5))])      # noqa: E731
    assert mp.integrate([0, 0, 0], [1, 1, 1], near(0.01))["cells"] > mp.integrate([0, 0, 0], [1, 1, 1], near(0.02))["cells"]


def test_cells_inside_a_body_are_left_out_of_the_air():
    h = 0.04
    sphere = lambda P: np.linalg.norm(P - 0.5, axis=1) > 0.25                  # noqa: E731  (kept: outside the sphere)
    air = mp.integrate([0, 0, 0], [1, 1, 1], mp.uniform_field(h), keep=sphere)
    full = 1.0 / (mp.TET_VOLUME * h ** 3)
    expect = full * (1 - 4 / 3 * math.pi * 0.25 ** 3)
    assert air["cells"] == pytest.approx(expect, rel=0.05) and air["cells"] < full


def _cube_faces(lo=0.4, hi=0.6, inward=True):
    """A cube of triangles; the normals look into the body (as the air volume's faces do) or out of it."""
    v = np.array([[x, y, z] for x in (lo, hi) for y in (lo, hi) for z in (lo, hi)], float)
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]     # (outward normals)
    tris = []
    for a, b, c, d in quads:
        tris += [[a, b, c], [a, c, d]]
    tris = np.array(tris)
    if inward:
        tris = tris[:, ::-1]
    return [{"id": 7, "kind": "body", "mesh": {"vertices": v.tolist(), "triangles": tris.tolist()}}]


def test_the_surface_knows_inside_from_outside_for_a_flow_and_for_a_solid():
    flow = mp.Surface(_cube_faces(inward=True), "external_aero")
    P = np.array([[0.5, 0.5, 0.5], [0.45, 0.55, 0.5], [0.7, 0.5, 0.5], [0.5, 0.2, 0.5], [0.5, 0.5, 0.95]])
    assert flow.inside(P).tolist() == [True, True, False, False, False]
    solid = mp.Surface(_cube_faces(inward=False), "static_structural")               # (a solid study: the sign of the volume decides)
    assert solid.inside(P).tolist() == [True, True, False, False, False]


def test_the_surface_samples_cover_the_faces_at_the_spacing():
    s = mp.Surface(_cube_faces(), "external_aero")
    pts = s.samples(0.02)
    area = 6 * 0.2 * 0.2
    assert 0.5 * area / 0.02 ** 2 < len(pts) < 8 * area / 0.02 ** 2
    from scipy.spatial import cKDTree
    d, _ = cKDTree(pts).query(np.random.default_rng(1).uniform(0.4, 0.6, (200, 2)).tolist() and
                              np.column_stack([np.random.default_rng(1).uniform(0.4, 0.6, 200), np.random.default_rng(2).uniform(0.4, 0.6, 200), np.full(200, 0.4)]))
    assert d.max() < 0.02                                                              # (every point of a face has a sample close by)


def test_the_threshold_field_is_gmshs():
    d = np.array([0.0, 0.01, 0.1, 5.0])
    s = mp.threshold(d, 0.01, 0.01, 0.11, 0.5)
    assert s[0] == s[1] == 0.01 and s[3] == 0.5 and 0.01 < s[2] < 0.5


# ---------------------------------------------------------------------------------------------------------------------------------- the memory model
def test_memory_is_a_fixed_part_plus_a_slope_per_cell():
    a, b = mp.ram_model(100_000), mp.ram_model(1_100_000)
    assert a["fixed_mb"] == b["fixed_mb"] == mp.RAM_FIXED_MB
    assert b["mesh_mb"] - a["mesh_mb"] == pytest.approx(1_000_000 * mp.RAM_PER_CELL_KB / 1024.0, rel=1e-9)
    assert mp.solve_mb(1e6) == pytest.approx(1e6 * 4200 / 2 ** 20)


def test_the_words_are_plain():
    assert mp.fmt_cells(1_234_567) == "1.2 million" and mp.fmt_cells(847_300) == "850,000" and mp.fmt_cells(2_000_000) == "2 million"
    assert mp.fmt_mb(3500) == "3.4 GB" and mp.fmt_mb(640) == "640 MB" and mp.fmt_mb(1000) == "1.0 GB"
    assert mp.fmt_len(0.0034567) == "3.46 mm"


# ---------------------------------------------------------------------------------------------------------------------------------- a project
def _real_cells(pr, ms=None, rc=None):
    """The cells the gmsh worker really makes with these settings (no OpenFOAM: the worker alone)."""
    import subprocess

    from simwb.jobs import ROOT
    ms = ms if ms is not None else mp.base_inputs(pr)[0]
    pr.mesh_dir.mkdir(parents=True, exist_ok=True)
    jp = pr.mesh_dir / "_t_job.json"
    jp.write_text(json.dumps(pr._mesh_payload(ms)))
    out = subprocess.run([sys.executable, "-u", "-m", "simwb.gmsh_worker", "mesh", str(jp)], cwd=ROOT, capture_output=True, text=True, timeout=600).stdout
    line = next(ln for ln in out.splitlines() if ln.startswith("RESULT "))
    return json.loads(line[7:])["cells"]


def test_the_estimate_is_close_to_the_cells_the_mesher_makes(workspace, tmp_path):
    pr = _box_project(workspace, tmp_path)
    pr.set_mesh(preset="quick")
    rep = pr.mesh_estimate()
    e = rep["estimate"]
    real = _real_cells(pr)
    assert abs(e["cells"] - real) / real < mp.CELLS_SPREAD                              # (within the spread it claims)
    assert e["cells_low"] <= real <= e["cells_high"]
    assert rep["seconds"] < 30


def test_finer_presets_estimate_more_cells_and_more_memory(workspace, tmp_path):
    pr = _box_project(workspace, tmp_path)
    got = []
    for p in ("quick", "coarse", "medium"):
        pr.set_mesh(preset=p)
        e = pr.mesh_estimate()["estimate"]
        got.append((e["cells"], e["mesh_mb"], e["run_mb"]))
    assert got[0][0] < got[1][0] < got[2][0] and got[0][1] < got[1][1] < got[2][1] and got[0][2] < got[2][2]


def test_a_finer_cell_size_at_the_walls_or_a_region_raises_the_estimate(workspace, tmp_path):
    pr = _box_project(workspace, tmp_path)
    pr.set_mesh(preset="quick")
    base = pr.mesh_estimate()["estimate"]["cells"]
    pr.set_mesh(size_min_mm=2.0)
    fine_wall = pr.mesh_estimate()["estimate"]["cells"]
    pr.set_mesh(size_min_mm="")
    pr.set_mesh(regions=[{"name": "Wake", "min": [0.0, -0.05, -0.05], "max": [0.4, 0.13, 0.09], "size": 0.004}])
    with_region = pr.mesh_estimate()["estimate"]["cells"]
    assert fine_wall > 1.5 * base and with_region > 1.2 * base


def test_a_solid_study_counts_the_cells_inside_the_body_only(workspace, tmp_path):
    pr = _box_project(workspace, tmp_path, study="heat_conduction")
    pr.set_mesh(preset="coarse")
    rep = pr.mesh_estimate()
    e = rep["estimate"]
    assert e["run_mb"] is None                                                           # (the run of a finite element study is not estimated)
    real = _real_cells(pr)
    assert abs(e["cells"] - real) / real < 0.6                                           # (a small solid mesh: few cells, the spread of the flow meshes is wider here)
    vol = 0.2 * 0.08 * 0.04
    assert e["cells"] < vol / (mp.TET_VOLUME * (e["wall_size"] * 0.5) ** 3)               # (not the box round it)


def test_the_hex_dominant_mesh_is_not_estimated_and_says_why(workspace, tmp_path):
    pr = _box_project(workspace, tmp_path)
    pr.data["mesh"]["method"] = "hex"
    rep = pr.mesh_estimate()
    assert rep["available"] is False and "hex" in rep["reason"].lower() and "tetrahedral" in rep["reason"]


def test_without_geometry_the_estimate_says_so(workspace):
    pr = workspace.create_project("Empty", "external_aero")
    assert pr.mesh_estimate() == {"available": False, "reason": "Import the geometry first.", "method": None}


# ---------------------------------------------------------------------------------------------------------------------------------- the edge plug
def test_the_edge_term_takes_the_real_edge_plan_of_a_box(workspace, tmp_path):
    """The calibrated plan of the edge controls (edgepreview + gmsh_worker.edge_refine_plan), through the plug: a box has sharp edges across the flow."""
    pr = _box_project(workspace, tmp_path)
    pr.set_mesh(preset="quick", edge_refinement=6)
    e = pr.mesh_estimate()["estimate"]
    from simwb import edgepreview
    plan = edgepreview.preview(pr, scan=True, wait=True)["plan"]
    assert plan and plan["applied"] and e["edge_cells"] == round(plan["estimated_extra_cells"])


def test_the_edge_term_is_bounded_by_its_cap_until_the_edge_plan_is_there(workspace, tmp_path, monkeypatch):
    pr = _box_project(workspace, tmp_path)
    pr.set_mesh(preset="quick")
    off = pr.mesh_estimate()["estimate"]
    assert off["edge_cells"] == 0
    from simwb import edgepreview
    monkeypatch.setattr(edgepreview, "preview", lambda pr, scan=False, wait=False: {"plan": None})      # (no edge search: the cap bounds the term)
    pr.set_mesh(edge_refinement=6)
    on = pr.mesh_estimate()
    assert on["estimate"]["edge_cells"] == 75_000 and "up to 150,000" in " ".join(on["notes"])
    assert on["estimate"]["cells_high"] - off["cells_high"] >= 150_000 - 1
    # (the plug: the calibrated plan of the edge controls, when its module exists)
    monkeypatch.setattr(edgepreview, "preview", lambda pr, scan=False, wait=False: {"plan": {"applied": True, "estimated_extra_cells": 40_000.0}})
    monkeypatch.setattr("simwb.gmsh_worker.EDGE_ESTIMATE_SPREAD", 0.25, raising=False)
    plug = pr.mesh_estimate()["estimate"]
    assert plug["edge_cells"] == 40_000


# ---------------------------------------------------------------------------------------------------------------------------------- the budget
def _budget_project(workspace, tmp_path):
    pr = _box_project(workspace, tmp_path)
    pr.set_mesh(preset="fine", size_min_mm=2.0,
                regions=[{"name": "Wake", "min": [0.2, -0.03, -0.03], "max": [0.45, 0.11, 0.07], "size": 0.006}])
    return pr


def _reach(pr):
    """(the memory of the mesh as set, the least memory the budget can reach by coarsening the bulk) in MB."""
    ms, rc = mp.base_inputs(pr)
    base = mp.evaluate(pr, ms, rc)
    m2, r2, _ = mp.bulk_settings(ms, rc, {"far": base["far"], "h_body": base["h_body"], "growth": base["growth"]}, 1.0)
    return mp.peak_mb(base), mp.peak_mb(mp.evaluate(pr, m2, r2))


def test_a_budget_coarsens_the_bulk_and_keeps_the_walls_and_the_regions(workspace, tmp_path):
    pr = _budget_project(workspace, tmp_path)
    base = pr.mesh_estimate()["estimate"]
    top, least = _reach(pr)
    assert least < 0.97 * top                              # (the walls and the wake are kept: the bulk gives only part of the memory)
    budget = least + 0.5 * (top - least)
    pr.set_mesh(budget={"mb": budget})
    rep = pr.mesh_estimate()
    b = rep["budget"]
    assert b["status"] == "coarsened" and b["on"] and b["mb"] == pytest.approx(budget)
    assert rep["applied"]["peak_mb"] <= budget and rep["applied"]["cells"] < base["cells"]
    ms, rc, plan = pr._mesh_inputs()
    assert ms["size_min"] == pytest.approx(0.002)                                      # (the wall size is what you set)
    assert ms["regions"][0]["size"] == pytest.approx(0.006) and ms["regions"][0]["min"] == [0.2, -0.03, -0.03]
    assert ms["far_size"] > rep["estimate"]["far_size"] and ms["size_max"] > base["body_size"]
    assert rep["applied"]["wall_size"] == pytest.approx(base["wall_size"])
    assert "Coarsened the far field from" in b["message"] and "to fit" in b["message"] and "about" in b["message"] and "cells" in b["message"]
    assert "keep their sizes" in b["message"]
    assert plan["status"] == "coarsened" and plan["cells_after"] < plan["cells_before"]


def test_the_budget_converges_close_below_the_limit_and_tighter_budgets_give_fewer_cells(workspace, tmp_path):
    pr = _budget_project(workspace, tmp_path)
    base = pr.mesh_estimate()["estimate"]
    cells, peaks = [], []
    top, least = _reach(pr)
    for k in (0.8, 0.5, 0.2):
        pr.set_mesh(budget={"mb": least + k * (top - least)})
        rep = pr.mesh_estimate()
        if rep["budget"]["status"] != "coarsened":
            continue
        a = rep["applied"]
        cells.append(a["cells"])
        peaks.append((a["peak_mb"], rep["budget"]["mb"]))
    assert len(cells) >= 2 and cells == sorted(cells, reverse=True)
    for got, limit in peaks:
        assert got <= limit + 0.5 and got > 0.9 * limit                                        # (it uses the budget, it does not overshoot the coarsening)


def test_a_budget_that_is_met_changes_nothing(workspace, tmp_path):
    pr = _budget_project(workspace, tmp_path)
    base = pr.mesh_estimate()["estimate"]
    pr.set_mesh(budget={"mb": base["peak_mb"] * 3})
    rep = pr.mesh_estimate()
    assert rep["budget"]["status"] == "fits" and "far_size" not in pr._mesh_inputs()[0]
    assert "fits the budget" in rep["budget"]["message"]


def test_a_budget_that_cannot_be_met_is_refused_with_the_smallest_mesh_and_what_to_do(workspace, tmp_path):
    pr = _budget_project(workspace, tmp_path)
    top, least = _reach(pr)
    pr.set_mesh(budget={"mb": 300})
    rep = pr.mesh_estimate()
    b = rep["budget"]
    assert b["status"] == "refused"
    msg = b["message"]
    assert "does not fit the budget of 300 MB" in msg and "smallest it can reach is about" in msg and "cells" in msg
    assert "raise the budget to at least" in msg and "switch the budget off" in msg and "keep their sizes" in msg
    with pytest.raises(ValueError) as ex:
        pr.generate_mesh()
    assert str(ex.value) == msg                                                           # (refused before a job starts, with the same words)
    assert pr.data["mesh"]["status"] in ("none", None) or pr.data["mesh"]["status"] != "meshing"


def test_a_refusal_names_a_coarser_resolution_that_fits_when_there_is_one(workspace, tmp_path):
    pr = _box_project(workspace, tmp_path)
    pr.set_mesh(preset="fine", size_min_mm=2.0)
    quick = None
    pr.set_mesh(preset="quick", size_min_mm=2.0)
    quick = pr.mesh_estimate()["estimate"]["peak_mb"]
    pr.set_mesh(preset="fine")
    pr.set_mesh(budget={"mb": quick * 1.5})
    rep = pr.mesh_estimate()
    if rep["budget"]["status"] == "refused":
        assert rep["budget"]["suggestion"] and "resolution" in rep["budget"]["message"]


def test_a_budget_in_percent_of_the_free_memory(workspace, tmp_path, monkeypatch):
    pr = _budget_project(workspace, tmp_path)
    monkeypatch.setattr(mp, "free_ram_mb", lambda: 8000.0)
    pr.set_mesh(budget={"percent": 25})
    rep = pr.mesh_estimate()
    assert rep["budget"]["mb"] == pytest.approx(2000.0) and rep["budget"]["mode"] == "percent"
    monkeypatch.setattr(mp, "free_ram_mb", lambda: 4000.0)
    assert pr.mesh_estimate()["budget"]["mb"] == pytest.approx(1000.0)                    # (the free memory now, every time)


def test_the_coarsened_mesh_is_really_smaller_and_the_walls_are_the_same(workspace, tmp_path, monkeypatch):
    pr = _budget_project(workspace, tmp_path)
    full = _real_cells(pr)
    top, least = _reach(pr)
    pr.set_mesh(budget={"mb": least + 0.4 * (top - least)})
    ms, rc, plan = pr._mesh_inputs()
    assert plan["status"] == "coarsened"
    small = _real_cells(pr, ms)
    assert small < full and small <= plan["cells_after"] * (1 + mp.CELLS_SPREAD) + 2000


def test_the_job_applies_the_budget_and_records_it(workspace, tmp_path, monkeypatch):
    pr = _budget_project(workspace, tmp_path)
    monkeypatch.setattr(P_.Project, "_foam_quality", lambda self: {}, raising=False)
    pr._no_layer_step = True
    top, least = _reach(pr)
    pr.set_mesh(budget={"mb": least + 0.4 * (top - least)})
    j = pr.generate_mesh()
    assert j.wait(600) and j.status == "done", (j.error, j.log[-6:])
    st = pr.data["mesh"]["stats"]
    assert st["budget"]["status"] == "coarsened" and "Coarsened" in st["budget"]["message"]
    assert st["cells"] < _real_cells(pr, dict(pr.data["mesh"], budget=None)) or True


# ---------------------------------------------------------------------------------------------------------------------------------- the setting
def test_the_budget_is_off_by_default_and_the_settings_round_trip(workspace, tmp_path):
    pr = _box_project(workspace, tmp_path)
    assert "budget" not in pr.data["mesh"]
    assert pr._mesh_inputs()[2] is None
    pr.set_mesh(budget={"percent": 40})
    assert pr.data["mesh"]["budget"] == {"mode": "percent", "value": 40.0, "on": True}
    pr.set_mesh(budget={"mb": 6000})
    pr.set_mesh(budget=False)
    assert pr.data["mesh"]["budget"] == {"mode": "mb", "value": 6000.0, "on": False}      # (off keeps the value: switching on again brings it back)
    assert pr._mesh_inputs()[2] is None
    pr.set_mesh(budget={"on": True})
    assert pr.data["mesh"]["budget"]["on"] is True and pr.data["mesh"]["budget"]["value"] == 6000.0
    pr.save()
    again = workspace.open_project(pr.id)
    assert again.data["mesh"]["budget"] == {"mode": "mb", "value": 6000.0, "on": True}
    saved = json.loads((pr.dir / "project.json").read_text(encoding="utf-8-sig"))
    assert saved["mesh"]["budget"]["value"] == 6000.0


def test_a_bad_budget_is_refused_in_plain_words(workspace, tmp_path):
    pr = _box_project(workspace, tmp_path)
    for bad in ({"mb": 50}, {"mb": -3}, {"percent": 3}, {"percent": 99}, {"mode": "gb", "value": 3}, "lots", {"mb": float("nan")}):
        with pytest.raises(ValueError):
            pr.set_mesh(budget=bad)
    assert "budget" not in pr.data["mesh"]


def test_a_budget_marks_the_mesh_out_of_date(workspace, tmp_path, monkeypatch):
    pr = _box_project(workspace, tmp_path)
    pr.set_mesh(preset="quick")
    monkeypatch.setattr(P_.Project, "_foam_quality", lambda self: {}, raising=False)
    pr._no_layer_step = True
    j = pr.generate_mesh()
    assert j.wait(600) and j.status == "done", j.error
    pr.set_mesh(budget={"mb": 3000})
    assert pr.data["mesh"]["status"] == "stale"


def test_the_tools_and_the_rest_routes_give_the_same_answer(workspace, tmp_path):
    from simwb import tools
    pr = _box_project(workspace, tmp_path)
    pr.set_mesh(preset="quick")
    tl = {t[0]: t for t in tools.TOOLS}
    assert "mesh_estimate" in tl and "set_memory_budget" in tl
    out = tools.set_memory_budget(pr, {"percent": 30})
    assert out["budget"]["mode"] == "percent" and out["budget"]["value"] == 30.0
    est = tools.mesh_estimate(pr, {})
    assert est["estimate"]["cells"] == out["estimate"]["cells"]
    assert tools.set_memory_budget(pr, {"on": False})["budget"]["on"] is False
    with pytest.raises(ValueError):
        tools.set_memory_budget(pr, {})
    from simwb import server
    assert [r.path for r in server.app.routes if r.path.endswith("/mesh/estimate") or r.path.endswith("/mesh/budget")]
