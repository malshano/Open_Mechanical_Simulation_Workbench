"""Vibro-acoustics study end to end through the project API (structure + air meshed together, solved by openCFS)."""
import importlib
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMWB_HOME", str(tmp_path / "ws"))
    import simwb.project as P

    importlib.reload(P)
    yield P


def _wait(job, timeout=1800):
    assert job.wait(timeout), "job timed out"
    assert job.status == "done", (job.error, job.log[-20:])
    return job.result


def _cfs():
    from simwb.solvers.opencfs import installer

    return installer.detect()["available"]


def _panel(P):
    pr = P.create_project("Panel", "vibro_acoustics")
    assert pr.spec["category"] == "multiphysics" and pr.data["domain"]["enclosure_mode"] == "auto_box"
    _wait(pr.import_step(ROOT / "examples" / "heated_plate.step"))
    return pr


def test_study_is_registered_and_meshes_like_the_cooling_study(workspace):
    P = workspace
    pr = _panel(P)
    assert pr.mesh_study == "cht_enclosure" and pr.study == "vibro_acoustics"
    roles = sorted(b["role"] for b in pr.bodies())
    assert roles.count("fluid") == 1 and roles.count("solid") == 2  # the air box was made around the parts
    assert "symmetry" not in pr.spec["face_bc_types"] and {"force", "pressure", "normal_velocity"} <= set(pr.spec["face_bc_types"])


def test_readiness_needs_an_excitation_and_a_sensible_sweep(workspace):
    P = workspace
    pr = _panel(P)
    msgs = [c["message"] for c in pr.check() if c["level"] == "error"]
    assert any("harmonic force" in m for m in msgs)
    pr.set_settings(f_min_hz=500.0, f_max_hz=200.0)
    assert any("Lowest frequency" in c["message"] for c in pr.check() if c["level"] == "error")


def _own_faces(pr):
    """Faces of the PART (not of the air box): kind 'solid_outer' = a part face that touches the air."""
    flat = [f for f in pr.faces() if f.get("kind") in ("solid_outer", "interface") and "Plane" in f["surface_type"]]
    top = max(flat, key=lambda f: f["centroid"][2])
    left = min(flat, key=lambda f: f["centroid"][0])
    return top, left


def _set_materials(pr):
    plate, block = sorted((b for b in pr.bodies() if b["role"] == "solid"), key=lambda b: -b["volume"])
    pr.set_body(plate["id"], material="aluminium_6061", name="Plate")
    pr.set_body(block["id"], material="aluminium_6061", name="Block")


def test_an_excitation_on_the_air_box_is_refused_not_answered_with_silence(workspace):
    """A force selected on a face of the air box reaches nothing: the study must say so instead of returning zeros."""
    P = workspace
    pr = _panel(P)
    _set_materials(pr)
    box_top = max((f for f in pr.faces() if f.get("kind") == "enclosure" and "Plane" in f["surface_type"]),
                  key=lambda f: f["centroid"][2])
    pr.add_bc("Wrong place", "force", [box_top["id"]], {"fz_N": 1.0})
    pr.set_settings(f_min_hz=200.0, f_max_hz=400.0, n_freq=2)
    pr.set_mesh(preset="coarse")
    _wait(pr.generate_mesh())
    job = pr.solve()
    job.wait(600)
    assert job.status == "failed" and "No excitation reaches the model" in str(job.error)


@pytest.mark.skipif(not _cfs(), reason="openCFS is not installed (SIMWB_OPENCFS_DIR or SIMWB_OPENCFS)")
def test_panel_in_air_solves_and_radiates(workspace):
    P = workspace
    pr = _panel(P)
    _set_materials(pr)
    top, left = _own_faces(pr)
    pr.add_bc("Clamp", "fixed_support", [left["id"]])
    pr.add_bc("Shaker", "force", [top["id"]], {"fx_N": 0.0, "fy_N": 0.0, "fz_N": 1.0})
    pr.set_settings(f_min_hz=200.0, f_max_hz=1000.0, n_freq=5)
    assert not [c for c in pr.check() if c["level"] == "error" and c["step"] == "setup"]
    pr.set_mesh(preset="coarse")
    _wait(pr.generate_mesh())
    rep = _wait(pr.solve())
    rs = pr.results()
    assert {"Sound pressure amplitude", "Displacement amplitude"} <= set(rs.fields)
    p = np.asarray(rs.fields["Sound pressure amplitude"][1], float)
    u = np.asarray(rs.fields["Displacement amplitude"][1], float)
    assert np.isfinite(p).all() and np.isfinite(u).all()
    assert p.max() > 0 and u.max() > 0
    assert pr.data["solve"]["backend"] == "opencfs"
