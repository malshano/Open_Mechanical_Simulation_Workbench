"""Mesh and solve jobs record their RAM; turbulent cases keep the y+ layer rule through the case writer."""
import importlib
import json

import pytest

import simwb.project as P_
from simwb.solvers.openfoam import cases


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMWB_HOME", str(tmp_path / "ws"))
    importlib.reload(P_)
    yield P_


def _project(P, tmp_path, **settings):
    import gmsh

    gmsh.initialize(["-nopopup"])
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("b")
        gmsh.model.occ.addBox(0, 0, 0, 200, 80, 40)
        gmsh.model.occ.synchronize()
        step = tmp_path / "box.step"
        gmsh.write(str(step))
    finally:
        gmsh.finalize()
    pr = P.create_project("Ram", "external_aero")
    j = pr.import_step(step, original_name="box.step")
    assert j.wait(300) and j.status == "done", j.error
    pr.set_settings(speed=10, **settings)
    return pr


def test_a_mesh_job_records_its_ram(workspace, tmp_path):
    pr = _project(workspace, tmp_path)
    pr.set_mesh(preset="quick")
    j = pr.generate_mesh()
    assert j.wait(600) and j.status == "done", (j.error, j.log[-8:])
    ram = pr.data["mesh"]["stats"]["ram"]
    assert ram["measured"] is True
    assert ram["peak_ram_mb"] > 50 and ram["added_ram_mb"] is not None and 0 <= ram["added_ram_mb"] <= ram["peak_ram_mb"]
    assert ram["own_peak_ram_mb"] is not None and ram["peak_working_set_mb"] >= 1
    # saved with the project
    saved = json.loads((pr.dir / "project.json").read_text(encoding="utf-8-sig"))
    assert saved["mesh"]["stats"]["ram"]["peak_ram_mb"] == ram["peak_ram_mb"]


def test_ram_of_a_solver_outside_this_machine_is_not_invented():
    rec = P_.Project._ram_record({"peak_ram_mb": 300, "added_ram_mb": 50}, {"backend": "wsl", "added_ram_mb": 5})
    assert rec["measured"] is False and "wsl" in rec["note"] and "peak_ram_mb" not in rec
    rec = P_.Project._ram_record({"peak_ram_mb": 900, "added_ram_mb": 700, "own_peak_ram_mb": 400},
                                 {"backend": "windows", "added_ram_mb": 600, "peak_ram_mb": 800})
    assert rec["measured"] and rec["solver_added_ram_mb"] == 600 and rec["added_ram_mb"] == 700   # solver alone vs whole job
    assert P_.Project._ram_record({"peak_ram_mb": 1, "added_ram_mb": 1}, None)["measured"]       # (no solver phase seen)


def test_turbulent_case_keeps_the_y_plus_layer_rule_and_laminar_resolves_the_layer(workspace, tmp_path):
    """Through the real case writer: the snappyHexMeshDict of the layer step carries the first layer the planner gives."""
    pr = _project(workspace, tmp_path)
    pr.set_mesh(preset="quick", layers={"n": 6, "growth": 1.2})
    j = pr.generate_mesh()
    assert j.wait(600) and j.status == "done", j.error
    got = {}
    for model, speed in (("kOmegaSST", 30.0), ("laminar", 3.0)):
        pr.set_settings(speed=speed, turbulence=model)
        j = pr.solve(export_only=True)
        assert j.wait(300) and j.status == "done", (j.error, j.log[-6:])
        txt = (pr.case_dir / "system" / "snappyHexMeshDict").read_text()
        got[model] = float(__import__("re").search(r"firstLayerThickness\s+([0-9.e+-]+);", txt).group(1))
    L = pr.data["geometry"]["info"]["ref_length"]
    air = P_.materials.get_fluid("air")
    nu = air["mu"] / air["rho"]                  # (the viscosity the case writer uses)
    assert got["kOmegaSST"] == pytest.approx(cases.layer_first_thickness(30.0, L, nu), rel=1e-6)      # unchanged rule
    plan = cases.layer_plan(3.0, L, nu, "laminar", 6, 1.2)
    assert got["laminar"] == pytest.approx(plan["first"], rel=1e-6)
    assert got["laminar"] < got["kOmegaSST"] * 20 and plan["total"] == pytest.approx(1.3 * plan["delta"], rel=1e-6)
