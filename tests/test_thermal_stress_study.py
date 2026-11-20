"""Thermal stress study end to end through the project API (heat conduction -> static stress, one way)."""
import importlib
from pathlib import Path

import numpy as np
import pytest

ROOT =Path(__file__).resolve().parents[1]


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMWB_HOME", str(tmp_path / "ws"))
    import simwb.project as P

    importlib.reload(P)
    yield P


def _wait(job, timeout=600):
    assert job.wait(timeout), "job timed out"
    assert job.status == "done", (job.error, job.log[-20:])
    return job.result


def _plate(P, name="Plate"):
    pr = P.create_project(name, "thermal_stress")
    assert pr.spec["category"] == "multiphysics" and "heat_conduction" not in pr.spec["label"].lower()
    _wait(pr.import_step(ROOT / "examples" / "heated_plate.step"))
    plate, block = sorted(pr.bodies(), key=lambda b: -b["volume"])
    pr.set_body(plate["id"], material="aluminium_6061", name="Plate")
    pr.set_body(block["id"], material="aluminium_6061", name="Block")
    return pr, plate, block


def test_uniform_soak_between_two_clamps_gives_the_textbook_stress(workspace):
    """A plate held at both ends against lengthwise expansion (the supports hold only the length direction, so the
    sides are free to grow) and heated 60 K above its stress-free temperature: sigma = -E alpha dT, uniform (97.6 MPa
    for aluminium), whatever the Poisson ratio."""
    P = workspace
    pr, plate, block = _plate(P)
    left, right = pr.find_faces(normal="-x", at="min_x"), pr.find_faces(normal="+x", at="max_x")
    assert len(left) == 1 and len(right) == 1
    pr.add_bc("Left stop", "frictionless_support", [left[0]["id"]])
    pr.add_bc("Right stop", "frictionless_support", [right[0]["id"]])
    pr.add_bc("y stop", "frictionless_support", [pr.find_faces(normal="-y", at="min_y")[0]["id"]])
    pr.add_bc("z stop", "frictionless_support", [pr.find_faces(normal="-z", at="min_z")[0]["id"]])
    pr.set_settings(T_uniform=80.0, T_ref=20.0)
    assert not [c for c in pr.check() if c["level"] == "error" and c["step"] == "setup"]
    pr.set_mesh(preset="coarse")
    _wait(pr.generate_mesh())
    rep = _wait(pr.solve())
    assert rep["thermal"]["T_max"] == pytest.approx(80.0) and rep["thermal"]["T_ref"] == pytest.approx(20.0)
    expected = 68.9e9 * 23.6e-6 * 60 / 1e6  # MPa
    rs = pr.results()
    assert set(rs.fields) >= {"Von Mises stress", "Total deformation", "Temperature"}
    vm = np.asarray(rs.fields["Von Mises stress"][1], float)
    assert np.median(vm) == pytest.approx(expected, rel=0.04)  # (the small block on top is bonded and differs a little)
    p25, p75 = np.percentile(vm, [25, 75])
    assert 0.9 * expected < p25 and p75 < 1.1 * expected  # (the peak is local: where the block meets the plate)


def test_free_expansion_has_no_stress_and_heating_moves_the_part(workspace):
    """Frictionless supports let the plate expand freely: displacement but (almost) no stress."""
    P = workspace
    pr, plate, block = _plate(P, "Free")
    left = pr.find_faces(normal="-x", at="min_x")[0]
    bottom = pr.find_faces(normal="-z", at="min_z")[0]
    front = pr.find_faces(normal="-y", at="min_y")[0]
    pr.add_bc("x stop", "frictionless_support", [left["id"]])
    pr.add_bc("z stop", "frictionless_support", [bottom["id"]])
    pr.add_bc("y stop", "frictionless_support", [front["id"]])
    pr.set_settings(T_uniform=80.0, T_ref=20.0)
    pr.set_mesh(preset="coarse")
    _wait(pr.generate_mesh())
    rep = _wait(pr.solve())
    assert rep["max_von_mises_MPa"] < 2.0
    # alpha dT L = 23.6e-6 * 60 * 0.1 m = 0.14 mm along the plate
    assert rep["max_displacement_mm"] == pytest.approx(0.14, rel=0.35)


def test_heated_block_on_a_cooled_clamped_plate(workspace):
    """The heat problem and the stress problem together: a 10 W block, a cold bottom face, both ends clamped."""
    P = workspace
    pr, plate, block = _plate(P, "Heated")
    pr.set_body(block["id"], material="aluminium_6061", heat_W=10, name="Block")
    bottom = pr.find_faces(normal="-z", at="min_z")[0]
    pr.add_bc("Cold plate", "fixed_temperature", [bottom["id"]], {"T": 30})
    pr.add_bc("Left clamp", "fixed_support", [pr.find_faces(normal="-x", at="min_x")[0]["id"]])
    pr.add_bc("Right clamp", "fixed_support", [pr.find_faces(normal="+x", at="max_x")[0]["id"]])
    pr.set_settings(T_ref=30.0)
    assert not [c for c in pr.check() if c["level"] == "error" and c["step"] == "setup"]
    pr.set_mesh(preset="coarse")
    _wait(pr.generate_mesh())
    rep = _wait(pr.solve())
    assert rep["thermal"]["T_min"] >= 30 - 1e-6 and rep["thermal"]["T_max"] > 30.5
    assert rep["max_von_mises_MPa"] > 0.1
    s = pr.results().surface("Von Mises stress")
    assert s["n_tris"] > 0
    assert pr.data["solve"]["backend"] == "builtin"


def test_readiness_asks_for_supports_and_a_temperature(workspace):
    P = workspace
    pr, plate, block = _plate(P, "Checks")
    msgs = [c["message"] for c in pr.check() if c["level"] == "error"]
    assert any("supports" in m.lower() for m in msgs)
    assert any("temperature" in m.lower() for m in msgs)
    pr.set_settings(T_uniform=60.0)
    pr.add_bc("Stop", "frictionless_support", [pr.find_faces(normal="-x", at="min_x")[0]["id"]])
    assert not [c for c in pr.check() if c["level"] == "error" and c["step"] == "setup"]
