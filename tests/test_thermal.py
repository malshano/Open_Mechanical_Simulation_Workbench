"""Verification of the built-in conduction solver against 1-D analytical solutions."""
import json

import numpy as np

from conftest import run_worker
from simwb.solvers import thermal_fem


def _mesh_box(box_step, tmp_path, preset="medium"):
    out = tmp_path / "geo"
    run_worker("prepare", {"study": "heat_conduction", "step_file": str(box_step), "out_dir": str(out)}, tmp_path)
    faces = json.loads((out / "faces.json").read_text())
    run_worker("mesh", {"study": "heat_conduction", "out_dir": str(out / "mesh"), "brep": str(out / "domain.brep"),
                        "bodies_json": str(out / "bodies.json"), "faces_json": str(out / "faces.json"),
                        "domain_info": str(out / "domain_info.json"), "mesh": {"preset": preset}}, tmp_path)
    mesh = dict(np.load(out / "mesh" / "mesh.npz"))
    xmin = [f["id"] for f in faces if abs(f["centroid"][0]) < 1e-9][0]
    xmax = [f["id"] for f in faces if abs(f["centroid"][0] - 0.1) < 1e-9][0]
    bodies = json.loads((out / "bodies.json").read_text())
    return mesh, xmin, xmax, bodies[0]["id"]


def test_slab_fixed_and_convection(box_step, tmp_path):
    mesh, x0, x1, bid = _mesh_box(box_step, tmp_path)
    k, L, h, Tinf, T0 = 200.0, 0.1, 50.0, 20.0, 100.0
    res = thermal_fem.solve(mesh, {bid: {"k": k}}, [
        {"name": "hot", "type": "fixed_temperature", "faces": [x0], "params": {"T": T0}},
        {"name": "cool", "type": "convection", "faces": [x1], "params": {"h": h, "T_inf": Tinf}},
    ], log=lambda *a: None)
    q = (T0 - Tinf) / (L / k + 1 / h)
    T_right = Tinf + q / h
    x = mesh["nodes"][:, 0]
    exact = T0 - q * x / k
    assert np.abs(res["T"] - exact).max() < 1e-6 * T0 + 1e-6
    assert abs(res["report"]["boundaries"]["cool"]["T_mean"] - T_right) < 1e-6
    A = 0.02 * 0.02
    assert abs(res["report"]["boundaries"]["cool"]["heat_out_W"] - q * A) / (q * A) < 1e-6
    assert abs(res["report"]["boundaries"]["hot"]["heat_out_W"] + q * A) / (q * A) < 1e-6


def test_volumetric_source(box_step, tmp_path):
    """Uniform source, both ends at 0 C: T(x) = S x (L-x) / (2k), Tmax = S L^2 / (8k)."""
    mesh, x0, x1, bid = _mesh_box(box_step, tmp_path, "fine")
    k, L = 10.0, 0.1
    Q = 40.0  # W
    S = Q / (0.1 * 0.02 * 0.02)
    res = thermal_fem.solve(mesh, {bid: {"k": k, "heat_W": Q}}, [
        {"name": "ends", "type": "fixed_temperature", "faces": [x0, x1], "params": {"T": 0.0}},
    ], log=lambda *a: None)
    Tmax = S * L ** 2 / (8 * k)
    assert abs(res["report"]["T_max"] - Tmax) / Tmax < 0.01
    assert res["report"]["energy_balance"]["imbalance_percent"] < 0.1


def test_needs_reference_temperature(box_step, tmp_path):
    import pytest
    mesh, x0, x1, bid = _mesh_box(box_step, tmp_path, "coarse")
    with pytest.raises(ValueError, match="temperature level"):
        thermal_fem.solve(mesh, {bid: {"k": 1.0}}, [
            {"name": "in", "type": "heat_flux", "faces": [x0], "params": {"q": 100}}], log=lambda *a: None)


def _two_box_step(tmp_path):
    import gmsh
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.clear()
    gmsh.model.occ.addBox(0, 0, 0, 50, 20, 20)
    gmsh.model.occ.addBox(50, 0, 0, 50, 20, 20)
    gmsh.model.occ.synchronize()
    p = tmp_path / "two.step"
    gmsh.write(str(p))
    gmsh.finalize()
    return p


def test_two_material_series_slab(tmp_path):
    """Composite wall: q = dT / (L1/k1 + L2/k2), linear profile in each material, continuous at the interface."""
    step = _two_box_step(tmp_path)
    out = tmp_path / "geo"
    run_worker("prepare", {"study": "heat_conduction", "step_file": str(step), "out_dir": str(out)}, tmp_path)
    faces = json.loads((out / "faces.json").read_text())
    bodies = json.loads((out / "bodies.json").read_text())
    run_worker("mesh", {"study": "heat_conduction", "out_dir": str(out / "mesh"), "brep": str(out / "domain.brep"),
                        "bodies_json": str(out / "bodies.json"), "faces_json": str(out / "faces.json"),
                        "domain_info": str(out / "domain_info.json"), "mesh": {"preset": "medium"}}, tmp_path)
    mesh = dict(np.load(out / "mesh" / "mesh.npz"))
    x0 = [f["id"] for f in faces if abs(f["centroid"][0]) < 1e-9][0]
    x1 = [f["id"] for f in faces if abs(f["centroid"][0] - 0.1) < 1e-9][0]
    left = min(bodies, key=lambda b: b["centroid"][0])["id"]
    right = max(bodies, key=lambda b: b["centroid"][0])["id"]
    k1, k2 = 200.0, 20.0
    res = thermal_fem.solve(mesh, {left: {"k": k1}, right: {"k": k2}}, [
        {"name": "hot", "type": "fixed_temperature", "faces": [x0], "params": {"T": 100.0}},
        {"name": "cold", "type": "fixed_temperature", "faces": [x1], "params": {"T": 0.0}},
    ], log=lambda *a: None)
    q = 100.0 / (0.05 / k1 + 0.05 / k2)
    T_if = 100.0 - q * 0.05 / k1
    x = mesh["nodes"][:, 0]
    exact = np.where(x <= 0.05, 100.0 - q * x / k1, T_if - q * (x - 0.05) / k2)
    assert np.abs(res["T"] - exact).max() < 1e-6
    A = 0.02 * 0.02
    assert abs(res["report"]["boundaries"]["cold"]["heat_out_W"] - q * A) / (q * A) < 1e-6


def test_heat_load_total(box_step, tmp_path):
    """A heat load of Q watts on one end with the other end fixed: everything leaves through the fixed face,
    and T rise = Q L / (k A)."""
    mesh, x0, x1, bid = _mesh_box(box_step, tmp_path, "coarse")
    k, Q = 50.0, 2.0
    res = thermal_fem.solve(mesh, {bid: {"k": k}}, [
        {"name": "load", "type": "heat_load", "faces": [x0], "params": {"Q": Q}},
        {"name": "sink", "type": "fixed_temperature", "faces": [x1], "params": {"T": 20.0}},
    ], log=lambda *a: None)
    rep = res["report"]
    assert abs(rep["boundaries"]["sink"]["heat_out_W"] - Q) < 1e-9
    assert abs(rep["T_max"] - (20 + Q * 0.1 / (k * 0.02 * 0.02))) < 1e-6
