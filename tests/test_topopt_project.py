"""Topology optimisation as a study of the project: it is offered on the start screen, takes only the conditions it can use, says what is missing, and
runs on a real Gmsh mesh of the bundled bracket in real units (Pa, N, m) and saves a density field."""
import importlib
from pathlib import Path

import numpy as np
import pytest

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _wait(job, timeout=900):
    assert job.wait(timeout) and job.status == "done", (job.error, job.log[-5:])
    return job.result


@pytest.fixture
def P(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMWB_HOME", str(tmp_path / "ws"))
    import simwb.project as mod

    importlib.reload(mod)
    return mod


@pytest.fixture
def bracket(P):
    pr = P.create_project("Bracket", "topology_optimization")
    _wait(pr.import_step(EXAMPLES / "camera_mount.step"))
    holes = [f["id"] for f in pr.faces() if f["surface_type"] == "Cylinder"]
    front = [f["id"] for f in pr.faces() if abs(f["normal"][0]) > 0.9 and abs(f["centroid"][0] - 3e-3) < 1e-4]
    return pr, holes, front


def test_the_study_is_offered_and_takes_only_the_conditions_it_can_use(P):
    from simwb import studies
    st = studies.STUDIES["topology_optimization"]
    assert st["label"] == "Topology optimisation" and st["solver"] == "builtin_fem"
    assert set(st["face_bc_types"]) == {"fixed_support", "frictionless_support", "force", "pressure", "symmetry"}   # (the plane of a half model; no prescribed displacement, no heat)
    s = studies.default_settings("topology_optimization")
    assert s["mass_percent"] == 30.0 and s["member_mm"] == 0.0 and s["max_iterations"] == 200
    pr = P.create_project("T", "topology_optimization")
    assert pr.data["mesh"]["preset"] == "medium"
    with pytest.raises(ValueError, match="between|≥|≤"):
        pr.set_settings(mass_percent=95)
    with pytest.raises(ValueError):
        pr.set_settings(max_iterations=10)
    pr.set_settings(mass_percent=45, max_iterations=120)
    assert pr.data["settings"]["mass_percent"] == 45 and pr.data["settings"]["max_iterations"] == 120
    with pytest.raises(ValueError, match="not available"):
        pr.add_bc("Move", "displacement", [1], {"uz_mm": 1})


def test_what_is_missing_is_said_before_the_run(bracket):
    pr, holes, front = bracket
    msgs = lambda: pr.check()  # noqa: E731
    text = " | ".join(str(c) for c in msgs())
    assert "support" in text.lower() and "load" in text.lower()
    pr.add_bc("Bolts", "fixed_support", holes)
    pr.add_bc("Camera", "force", front, {"fx_N": -20.0, "fy_N": 0, "fz_N": 0})
    text = " | ".join(str(c) for c in msgs())
    assert "One load only" in text                                                   # (a single load case: the shape is best for that load alone)


def test_the_bracket_is_optimised_on_a_real_mesh_and_the_density_is_saved(bracket, P):
    pr, holes, front = bracket
    pr.add_bc("Bolts", "fixed_support", holes)
    pr.add_bc("Camera", "force", front, {"fx_N": -20.0, "fy_N": 0, "fz_N": 0})
    pr.set_mesh(preset="coarse")
    _wait(pr.generate_mesh())
    pr.set_settings(mass_percent=40)
    rep = _wait(pr.solve())
    t = rep["topopt"]
    assert t["mass_fraction"] == pytest.approx(0.40, abs=1e-6) and t["mass_target"] == 0.4
    assert 0.3 < t["stiffness_kept"] < 1.0 and t["converged"] and t["iterations"] < 200
    assert t["mass_kg_solid"] == pytest.approx(0.038, rel=0.1)                       # (the bracket weighs 38 g in aluminium: real units)
    assert t["mass_kg_kept"] == pytest.approx(0.4 * t["mass_kg_solid"], rel=1e-5)
    assert t["radius_mm"] >= 4 * t["element_size_mm"] - 1e-9
    assert t["linear_vs_quadratic"] is None or -0.5 < t["linear_vs_quadratic"] < 0.2
    rs = pr.results()
    d = rs.fields["Density"][1]
    assert rs.fields["Density"][0] == "cell" and len(d) == len(rs.tets) and 0 <= d.min() and d.max() <= 1.0 + 1e-6
    assert float(d.mean()) == pytest.approx(0.4, abs=0.02)
    assert {"Density", "Strain energy density", "Deformation"} <= set(rs.fields)
    live = pr.topopt_live()
    assert live and live["iteration"] == t["iterations"] and live["density"] is not None
    assert pr.data["solve"]["status"] == "done"
    # the project keeps the study and its result: saved and reloaded
    pr.save()
    again = P.open_project(pr.id)
    assert again.data["settings"]["mass_percent"] == 40
