"""End-to-end pipeline tests through the Project API (OpenFOAM cases are written and re-read, not run)."""
import json
import math
import os
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMWB_HOME", str(tmp_path / "ws"))
    import importlib

    import simwb.project as P

    importlib.reload(P)
    yield P


def _wait(job, timeout=600):
    assert job.wait(timeout), "job timed out"
    assert job.status == "done", (job.error, job.log[-20:])
    return job.result


def test_heat_conduction_pipeline(workspace):
    P = workspace
    pr = P.create_project("Plate", "heat_conduction")
    _wait(pr.import_step(ROOT / "examples" / "heated_plate.step"))
    assert len(pr.bodies()) == 2
    plate, block = sorted(pr.bodies(), key=lambda b: -b["volume"])
    pr.set_body(plate["id"], material="aluminium_6061", name="Plate")
    pr.set_body(block["id"], material="ceramic_alumina", heat_W=10, name="Resistor")
    bottom = pr.find_faces(normal="-z", at="min_z")
    assert len(bottom) == 1
    pr.add_bc("Cold plate", "fixed_temperature", [bottom[0]["id"]], {"T": 30})
    assert not [c for c in pr.check() if c["level"] == "error" and c["step"] == "setup"]
    pr.set_mesh(preset="coarse")
    _wait(pr.generate_mesh())
    rep = _wait(pr.solve())
    assert rep["energy_balance"]["imbalance_percent"] < 0.5
    assert rep["T_min"] >= 30 - 1e-6
    assert rep["T_max"] > 30
    rs = pr.results()
    s = rs.surface("Temperature")
    assert s["n_tris"] > 0
    c = rs.clip("Temperature", 0, 0.05)
    assert c["n_tris"] > 0


def test_cht_case_written(workspace, tmp_path):
    from simwb.solvers.openfoam import foamio
    from simwb.solvers.openfoam.results_loader import tets_from_polymesh

    P = workspace
    pr = P.create_project("Heatsink", "cht_enclosure")
    _wait(pr.import_step(ROOT / "examples" / "heatsink_duct.step"))
    roles = sorted(b["role"] for b in pr.bodies())
    assert roles == ["fluid", "solid", "solid"]
    io = pr.suggest_inlet_outlet()
    assert io["axis"] == "x"
    chip = min((b for b in pr.bodies() if b["role"] == "solid"), key=lambda b: b["volume"])
    pr.set_body(chip["id"], material="silicon", heat_W=15)
    for b in pr.bodies():
        if b["role"] == "solid" and b["id"] != chip["id"]:
            pr.set_body(b["id"], material="aluminium_6063")
    pr.set_mesh(preset="coarse")
    _wait(pr.generate_mesh())
    q = pr.data["mesh"]["stats"]["foam_quality"]
    assert q["non_orthogonality_max"] < 85
    job = pr.solve(export_only=True)
    _wait(job)
    case = pr.case_dir
    meta = json.loads((case / "simwb_case.json").read_text())
    assert len(meta["regions"]) == 3
    # regime from the fin channel (5.43 mm gap between fins), not the 5 mm heatsink-to-duct-wall bypass:
    # short developing channel at Re_Dh ~2200 -> laminar
    fr = meta["flow_regime"]
    assert fr["channel_Dh"] is not None and 0.007 < fr["channel_Dh"] < 0.011, fr
    assert fr["channel_L_over_Dh"] < 10 and fr["suggested"] == "laminar", fr
    # inputs of the plate-fin correlation cross-check: 24 mm fins, 50 mm long, 1.5 mm thick, ~250 cm2 wetted
    assert abs(fr["channel_height"] - 0.024) < 1e-4 and abs(fr["channel_length"] - 0.05) < 1e-4
    assert abs(fr["fin_thickness"] - 0.0015) < 1e-4 and fr["fin_thickness_from_pitch"]
    assert len(fr["channel_boxes"]) == 7 and fr["flow_axis"] == 0
    assert 0.02 < max(fr["wetted_area_by_body"].values()) < 0.03
    # the example's outlet is > 5 duct diameters behind the heatsink: no position-dependent pressure-drop warning
    dist, size = pr._outlet_distance()
    assert dist > 0.24, dist
    assert not any("pressure has not recovered" in c["message"] for c in pr.check())
    for f in ["Allrun", "constant/regionProperties", "constant/polyMesh/cellZones", "0.orig/air/U",
              "system/air/fvSolution"]:
        assert (case / f).exists(), f
    pm = foamio.read_polymesh(case / "constant" / "polyMesh")
    assert pm["n_cells"] == pr.data["mesh"]["stats"]["cells"]
    # upper-triangular ordering & orientation owner -> neighbour
    ni = len(pm["neighbour"])
    assert (pm["owner"][:ni] < pm["neighbour"]).all()
    t = tets_from_polymesh(pm)
    assert len(t) == pm["n_cells"]
    p = pm["points"]
    f = pm["faces"]
    cc = p[t].mean(1)
    n = np.cross(p[f[:, 1]] - p[f[:, 0]], p[f[:, 2]] - p[f[:, 0]])
    fc = p[f].mean(1)
    assert (np.einsum("ij,ij->i", n, fc - cc[pm["owner"]]) > 0).all()
    names = [b["name"] for b in pm["patches"]]
    assert "inlet" in names and "outlet" in names
    assert "\r" not in (case / "Allrun").read_text()


def test_input_validation(workspace, tmp_path):
    P = workspace
    pr = P.create_project("Val", "heat_conduction")
    with pytest.raises(ValueError, match="Scale"):
        pr.import_step(ROOT / "examples" / "heated_plate.step", scale=-1)
    with pytest.raises(ValueError, match="Scale"):
        pr.import_step(ROOT / "examples" / "heated_plate.step", scale=0)
    _wait(pr.import_step(ROOT / "examples" / "heated_plate.step"))
    face = pr.faces()[0]["id"]
    with pytest.raises(ValueError, match="Unknown parameter"):
        pr.add_bc("Conv", "convection", [face], {"htc": 50})
    bc = pr.add_bc("Conv", "convection", [face], {"h": 50})
    with pytest.raises(ValueError, match="Unknown parameter"):
        pr.update_bc(bc["id"], params={"T_ambient": 40})
    # convection with h = 0 is no way out for the heat: the setup check must say so
    pr.update_bc(bc["id"], params={"h": 0})
    assert any("heat can leave" in c["message"] for c in pr.check() if c["level"] == "error")
    pr.update_bc(bc["id"], params={"h": 50})
    assert not any("heat can leave" in c["message"] for c in pr.check() if c["level"] == "error")
    # a new import while a job runs must not touch the project's geometry
    src = (pr.geo_dir / "source.step").read_bytes()
    job = pr.generate_mesh()
    with pytest.raises(RuntimeError, match="Please wait"):
        pr.import_step(ROOT / "examples" / "heatsink_duct.step")
    assert (pr.geo_dir / "source.step").read_bytes() == src
    dom = dict(pr.data.get("domain", {}))
    with pytest.raises(RuntimeError, match="Please wait"):
        pr.set_domain(padding=[1, 1, 1, 1, 1, 1])
    assert pr.data.get("domain", {}) == dom  # refused before anything changed
    # an edit while a job runs is remembered: the job's results will be marked stale
    pr.set_settings()
    assert pr.data.get("edited_during_run")
    with pytest.raises(ValueError, match="drone"):
        pr.apply_trim()
    with pytest.raises(ValueError, match="heatsink"):
        pr.model_check()
    job.wait(600)
    # an unreadable STEP file fails in gmsh: the previous geometry, BCs and mesh stay usable
    bad = pr.geo_dir.parent / "My Bracket v2.step"
    bad.write_text("ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n")
    job = pr.import_step(bad)
    job.wait(600)
    assert job.status == "failed" and "My Bracket v2.step" in job.error
    assert (pr.geo_dir / "source.step").read_bytes() == src
    assert not (pr.geo_dir / "incoming.step").exists()
    assert pr.data["geometry"]["status"] == "ready" and pr.data["geometry"]["original_name"] == "heated_plate.step"
    assert pr.faces() and any(b["name"] == "Conv" for b in pr.data["bcs"])
    assert pr.data["mesh"]["status"] == "ready"


def test_aero_case_written(workspace):
    P = workspace
    pr = P.create_project("Quad", "external_aero")
    _wait(pr.import_step(ROOT / "examples" / "quadcopter.step"))
    assert len(pr.data["rotors"]) == 4, "rotors should be auto-detected on the motor tops"
    r = pr.data["rotors"][0]
    assert 0.11 < r["radius"] < 0.15
    # disks must sit above the motors and clear the hub (hub top z = 20 mm)
    assert all(rr["center"][2] - rr["thickness"] / 2 > 0.020 for rr in pr.data["rotors"])
    pr.set_settings(speed=8, pitch_deg=12, mass_kg=1.5)
    pr.set_mesh(preset="coarse")
    _wait(pr.generate_mesh())
    _wait(pr.solve(export_only=True))
    meta = json.loads((pr.case_dir / "simwb_case.json").read_text())
    assert len(meta["rotors"]) == 4
    assert all(z["cells"] > 50 for z in meta["rotors"])
    import math
    assert abs(sum(z["thrust_N"] for z in meta["rotors"]) - 1.5 * 9.81 / math.cos(math.radians(12))) < 1e-6
    U = np.array(meta["U_inf"])
    assert abs(np.linalg.norm(U) - 8) < 1e-9 and U[0] < 0 and U[2] < 0
    fvo = (pr.case_dir / "constant" / "fvOptions").read_text()
    assert fvo.count("vectorSemiImplicitSource") == 4
    # rotor drag H = k T V_inplane acts on the vehicle along the in-plane relative wind (rearward here)
    for z in meta["rotors"]:
        a, H = np.array(z["axis"]), np.array(z["H_N"])
        Vt = U - (U @ a) * a
        assert abs(H @ a) < 1e-9 and H @ Vt > 0
        assert abs(np.linalg.norm(H) - 0.025 * z["thrust_N"] * np.linalg.norm(Vt)) < 1e-9
    assert "freestreamVelocity" in (pr.case_dir / "0" / "U").read_text()
    # moments about the CG: the offsets move the moment reference point
    pr.set_settings(cg_up_mm=10, cg_forward_mm=-5)
    _wait(pr.solve(export_only=True))
    meta2 = json.loads((pr.case_dir / "simwb_case.json").read_text())
    d = np.array(meta2["CofR"]) - np.array(meta["CofR"])
    assert np.allclose(d, [-0.005, 0, 0.010], atol=1e-9)
    # apply_trim: pitch and a moment-balancing thrust split scaled to the trim thrust
    pr.data["solve"]["summary"] = {"suggested_pitch_deg": 15.83, "trim_thrust_total_N": 14.6,
                                   "rotor_thrust_trim_N": [3.0, 3.0, 4.0, 4.0]}
    r = pr.apply_trim()
    assert r["pitch_deg"] == 15.8 and abs(sum(r["rotor_thrust_N"]) - 14.6) < 1e-3
    assert abs(r["rotor_thrust_N"][2] / r["rotor_thrust_N"][0] - 4 / 3) < 1e-3
    # fixed thrusts from a trim carry the weight; after a mass change they no longer do -> warning, then reset
    heavy = lambda: any("fixed rotor thrusts" in c["message"] for c in pr.check())  # noqa: E731
    assert not heavy()
    pr.set_settings(mass_kg=2.0)
    assert heavy()
    pr.reset_thrusts()
    assert all(rr["thrust_N"] is None for rr in pr.data["rotors"]) and not heavy()


def test_tool_layer_options_and_rounding():
    from simwb import tools
    out = json.loads(tools.call("list_options", None, {"study": "heat_conduction"}))
    assert "convection" in out["boundary_condition_types"] and "settings" in out
    assert json.loads(tools._fmt({"x": 0.6004188947217198, "v": [1.23456789, "a"]})) == {"x": 0.6004, "v": [1.235, "a"]}


def test_parallel_steps_and_auto_cores():
    from simwb.solvers.openfoam import runner
    steps = [{"app": "checkMesh", "args": [], "log": "log.checkMesh"},
             {"app": "chtMultiRegionSimpleFoam", "args": [], "log": "log.chtMultiRegionSimpleFoam"}]
    assert runner.parallel_steps(steps, "chtMultiRegionSimpleFoam", 1, True) == steps
    par = runner.parallel_steps(steps, "chtMultiRegionSimpleFoam", 6, True)
    assert [s.get("app") or s["action"] for s in par] == [
        "checkMesh", "decomposePar", "balance_split", "chtMultiRegionSimpleFoam", "reconstructPar", "rm_processors"]
    # (the split is measured and redone when one process holds more cells; the pieces are dropped once joined)
    assert par[1]["args"] == ["-allRegions", "-force", "-latestTime"] and par[2]["redo"] == par[1]
    assert par[3]["mpi"] == 6 and par[3]["main"] and par[3]["args"] == ["-parallel"]
    assert runner.auto_procs(5000, 8) == 1 and runner.auto_procs(90000, 8) == 4 and runner.auto_procs(10 ** 6, 8) == 8
    assert runner.physical_cores() >= 1


def test_teertstra_correlation():
    from simwb.solvers.openfoam.results_loader import teertstra_nu
    # 5.43 mm x 50 mm fin channel at Re_b 1320, Pr 0.71: Nu_b ~ 8.1 (hand calculation)
    assert 7.9 < teertstra_nu(1320, 0.00543, 0.05, 0.71) < 8.3
    # long channel / low Re: the thermally fully developed limit Nu_b = Re_b* Pr / 2
    assert abs(teertstra_nu(10, 0.005, 1.0, 0.71) - 10 * 0.005 / 1.0 * 0.71 / 2) < 1e-3


def test_moment_balance_counts_set_thrusts():
    """A run whose thrust split already balances the aerodynamic moment is balanced: no second correction."""
    from simwb.solvers.openfoam.results_loader import moment_balance
    up, fwd = np.array([0, 0, 1.0]), np.array([1.0, 0, 0])
    x = 0.1344
    rot = lambda cx, cy, T: {"center": [cx, cy, 0.036], "axis": [0, 0, 1.0], "thrust_N": T}  # noqa: E731
    M_aero = np.array([0, -0.139, 0])  # 0.139 N·m nose-up (about -y)
    even = [rot(-x, -x, 3.6375), rot(x, -x, 3.6375), rot(-x, x, 3.6375), rot(x, x, 3.6375)]
    r1 = moment_balance(M_aero, even, [0, 0, 0], up, fwd)
    assert abs(r1["pitch_moment_Nm"] - 0.139) < 1e-9
    split = [rot(c[0], c[1], T) for c, T in zip([(-x, -x), (x, -x), (-x, x), (x, x)], r1["rotor_thrust_trim_N"])]
    assert split[0]["thrust_N"] > split[1]["thrust_N"]  # rear rotors push harder against a nose-up moment
    r2 = moment_balance(M_aero, split, [0, 0, 0], up, fwd)
    assert abs(r2["pitch_moment_Nm"]) < 1e-9 and abs(r2["aero_pitch_moment_Nm"] - 0.139) < 1e-9
    assert np.allclose(r2["rotor_thrust_trim_N"], r1["rotor_thrust_trim_N"])


def _cht_log(T_of_it, n=1500):
    lines = []
    for it in range(1, n + 1):
        lines.append(f"Time = {it}\n")
        if it % 10 == 0:
            lines.append(f"fieldMinMax chip write:\n    min/max(T) = 298.15 {T_of_it(it) + 273.15:.6f}\n")
    return "".join(lines)


def test_cht_stop_test_rejects_linear_creep():
    from simwb.solvers.openfoam.runner import monitor_outputs
    meta = {"study": "cht_enclosure", "T_initial_C": 25.0}
    # settles geometrically: stops, and the remaining drift is estimated
    mon = monitor_outputs(_cht_log(lambda it: 40.0 - 5.0 * math.exp(-it / 150)), meta)
    assert mon["stable"] and mon["remaining_drift_K"] < 0.01
    # creeps linearly by 0.008 K / 100 iterations: the half-window change alone (0.012 K) is below the 0.015 K
    # tolerance, but the drift bound must keep the run going
    mon = monitor_outputs(_cht_log(lambda it: 40.0 + 8e-5 * it), meta)
    assert mon["dT_window_K"] < mon["tolerance_K"] and not mon["stable"]


def test_job_dict_is_a_copy():
    """Tools reshape job dicts (get_run_status): the job's own residual history must survive."""
    from simwb.jobs import Job
    j = Job("solve", "x")
    j.extra["residuals"] = {"iters": [1, 2], "residuals": {"Ux": [1.0, 0.5]}}
    d = j.to_dict()
    d["extra"]["residuals"] = {"Ux": 0.5}
    assert j.extra["residuals"]["iters"] == [1, 2]


def test_gci_three_levels():
    from simwb.project import _gci
    # f = 1 + h^2 on meshes with h = 1, 1/2, 1/4 (cells 1, 8, 64): order 2, extrapolated 1, GCI = 1.25*0.1765/3
    g = _gci(2.0, 1.25, 1.0625, 1, 8, 64)
    assert abs(g["order"] - 2) < 1e-6 and abs(g["extrapolated"] - 1) < 1e-9 and abs(g["gci_percent"] - 7.3529) < 1e-3
    assert _gci(1.0, 1.2, 1.1, 1, 8, 64)["convergence_type"] == "oscillatory"
    # f = 1 + h^4 has apparent order 4: with a formal order of 2 the GCI is estimated with 2 (conservative)
    g = _gci(2.0, 1.0625, 1.00390625, 1, 8, 64)
    # (limited: the coarse difference scaled to the finest step, 0.9375 / 2^2, is larger than e32 and is used)
    assert g["order_limited_to"] == 2.0 and abs(g["gci_percent"] - 1.25 * (0.234375 / 1.00390625) / 3 * 100) < 1e-6
    osc = _gci(1.0, 1.2, 1.1, 1, 8, 64)
    assert abs(osc["gci_percent"] - 0.1 / 1.1 * 100) < 1e-9  # oscillatory: half the range
    assert "order_limited_to" not in _gci(2.0, 1.0625, 1.00390625, 1, 8, 64, formal_order=4.0)


def test_check_result_readable_after_timeout():
    """A finished check's result is part of the job dict (wait_for_run after a tool timeout)."""
    from simwb.jobs import Job
    j = Job("check", "x")
    j.status, j.result = "done", {"rows": [1], "note": "n"}
    assert j.to_dict()["result"]["note"] == "n"
    j2 = Job("solve", "x")
    j2.status, j2.result = "done", {"big": 1}
    assert "result" not in j2.to_dict()


def _beam_mesh(L, b, h, nx, ny, nz):
    """Structured tet mesh of a box (6 tets per hex); face 1 = x=0 end, 3 = x=L end, 2 = the rest."""
    from simwb.results import boundary_from_tets

    xs, ys, zs = np.linspace(0, L, nx + 1), np.linspace(0, b, ny + 1), np.linspace(0, h, nz + 1)
    X, Y, Z = np.meshgrid(xs, ys, zs, indexing="ij")
    nodes = np.c_[X.ravel(), Y.ravel(), Z.ravel()]
    idx = lambda i, j, k: (i * (ny + 1) + j) * (nz + 1) + k  # noqa: E731
    kuhn = [(0, 1, 3, 7), (0, 1, 5, 7), (0, 2, 3, 7), (0, 2, 6, 7), (0, 4, 5, 7), (0, 4, 6, 7)]
    tets = np.array([[c[a] for a in tt] for i in range(nx) for j in range(ny) for k in range(nz)
                     for c in [[idx(i + (q >> 2 & 1), j + (q >> 1 & 1), k + (q & 1)) for q in range(8)]] for tt in kuhn])
    p = nodes
    v = np.einsum("ij,ij->i", np.cross(p[tets[:, 1]] - p[tets[:, 0]], p[tets[:, 2]] - p[tets[:, 0]]), p[tets[:, 3]] - p[tets[:, 0]])
    tets[v < 0] = tets[v < 0][:, [0, 2, 1, 3]]
    tris, _ = boundary_from_tets(tets)
    cx = p[tris].mean(1)[:, 0]
    surf = np.where(cx < 1e-9, 1, np.where(cx > L - 1e-9, 3, 2)).astype(np.int32)
    return {"nodes": nodes, "tets": tets, "tet_vol": np.ones(len(tets), np.int32), "tris": tris, "tri_surf": surf}


def test_modal_cantilever_matches_beam_theory():
    """Quadratic tets vs Euler-Bernoulli: cantilever frequencies, effective masses, tip mass, free-free rigid modes."""
    from simwb.solvers import modal_fem

    assert abs(modal_fem.M_REF.sum() - 1) < 1e-12 and abs(modal_fem.M_REF[0, 0] * 420 - 6) < 1e-9
    L, b, h, E, nu, rho = 0.1, 0.01, 0.005, 69e9, 0.33, 2700.0
    lam = lambda beta, I: beta ** 2 / (2 * math.pi) * math.sqrt(E * I / (rho * b * h * L ** 4))  # noqa: E731
    m = _beam_mesh(L, b, h, 20, 4, 2)
    mat = {1: {"E": E, "nu": nu, "rho": rho}}
    clamp = {"name": "clamp", "type": "fixed_support", "faces": [1], "params": {}}
    r = modal_fem.solve(m, mat, [clamp], n_modes=4, log=lambda s: None)
    f = [x["frequency_Hz"] for x in r["modes"]]
    assert abs(f[0] / lam(1.87510407, b * h ** 3 / 12) - 1) < 0.015   # 1st bending, thin direction
    assert abs(f[1] / lam(1.87510407, h * b ** 3 / 12) - 1) < 0.015   # 1st bending, wide direction
    assert abs(f[2] / lam(4.69409113, b * h ** 3 / 12) - 1) < 0.02    # 2nd bending (shear makes it slightly lower)
    assert abs(r["modes"][0]["effective_mass_fraction"]["z"] - 0.613) < 0.01
    assert abs(r["report"]["mass_kg"] - rho * L * b * h) < 1e-9
    # tip mass equal to the beam mass vs Rayleigh's estimate with 0.236 of the beam mass
    tip = {"name": "tip", "type": "added_mass", "faces": [3], "params": {"mass_kg": rho * L * b * h}}
    r = modal_fem.solve(m, mat, [clamp, tip], n_modes=1, log=lambda s: None)
    k = 3 * E * b * h ** 3 / 12 / L ** 3
    f_r = math.sqrt(k / (rho * L * b * h * 1.2357)) / (2 * math.pi)
    assert abs(r["modes"][0]["frequency_Hz"] / f_r - 1) < 0.02
    # free-free: six rigid-body modes, then the first free-free bending mode (beta L = 4.730)
    r = modal_fem.solve(m, mat, [], n_modes=2, log=lambda s: None)
    assert r["report"]["n_rigid_body_modes"] == 6
    assert abs(r["report"]["first_frequency_Hz"] / lam(4.73004074, b * h ** 3 / 12) - 1) < 0.02
    # SuperLU fallback (installs without the optional PARDISO package) gives the same answer
    f_pd = r["report"]["first_frequency_Hz"]
    orig = modal_fem._pardiso
    modal_fem._pardiso = lambda: None
    try:
        r = modal_fem.solve(m, mat, [], n_modes=2, log=lambda s: None)
    finally:
        modal_fem._pardiso = orig
    assert r["report"]["n_rigid_body_modes"] == 6 and abs(r["report"]["first_frequency_Hz"] / f_pd - 1) < 1e-6


def test_shake_test_matches_beam_theory():
    """Sine sweep of a clamped beam shaken at its root. At the first resonance the tip moves 1.566 A / (2 zeta w1^2)
    (first-mode participation x tip shape of a uniform cantilever = 1.566), its acceleration is 1.566 / (2 zeta)
    times the table's, and the root stress is E (h/2) beta1^2 u_tip. Far below the first mode the response is the
    static sag under the same acceleration times 1 / (1 - (f/f1)^2)."""
    from simwb.solvers import modal_fem, static_fem

    L, b, h, E, nu, rho, zeta = 0.1, 0.01, 0.005, 69e9, 0.33, 2700.0, 0.02
    m = _beam_mesh(L, b, h, 20, 4, 2)
    mat = {1: {"E": E, "nu": nu, "rho": rho, "yield": 276e6, "name": "beam"}}
    clamp = {"name": "clamp", "type": "fixed_support", "faces": [1], "params": {}}
    r = modal_fem.solve(m, mat, [clamp], n_modes=8, log=lambda s: None,
                        sine={"axis": "z", "accel_g": 1.0, "damping_percent": 100 * zeta})
    s = r["report"]["sine"]
    f1 = r["modes"][0]["frequency_Hz"]
    pk = s["peaks"][0]
    u_tip = 1.566 * 9.81 / (2 * zeta * (2 * math.pi * f1) ** 2)
    assert pk["mode"] == 1 and abs(pk["max_displacement_mm"] / (u_tip * 1e3) - 1) < 0.01
    assert abs(pk["max_acceleration_g"] / (1.566 / (2 * zeta)) - 1) < 0.01
    assert abs(pk["max_stress_MPa"] / (E * h / 2 * (1.87510407 / L) ** 2 * u_tip / 1e6) - 1) < 0.03
    assert s["worst"]["frequency_Hz"] == pytest.approx(f1) and s["amplification_at_resonance"] == pytest.approx(25)
    assert s["worst"]["safety_factor_yield"] == pytest.approx(276 / pk["max_stress_MPa"], rel=1e-5)
    # the sweep has a point on every resonance in its range and is sorted
    fs = np.array(s["frequency_Hz"])
    assert (np.diff(fs) > 0).all() and all(np.abs(fs - x["frequency_Hz"]).min() < 1e-6 for x in r["modes"])
    # quasi-static end of the sweep against a static run with the same acceleration as body load
    g = static_fem.solve(m, mat, [clamp], gravity=[0, 0, -9.81], log=lambda *a: None)
    amp = 1 / (1 - (fs[0] / f1) ** 2)
    assert abs(s["max_displacement_mm"][0] / (g["report"]["max_displacement_mm"] * amp) - 1) < 0.01
    assert abs(s["max_stress_MPa"][0] / (g["report"]["max_von_mises_MPa"] * amp) - 1) < 0.05
    assert set(r["fields"]) == {"Shake test: stress amplitude", "Shake test: displacement amplitude"}
    # shaking along the beam axis does not excite the bending modes: far smaller response
    rx = modal_fem.solve(m, mat, [clamp], n_modes=8, log=lambda s: None,
                         sine={"axis": "x", "accel_g": 1.0, "damping_percent": 100 * zeta})
    assert rx["report"]["sine"]["peaks"][0]["max_displacement_mm"] < 1e-3 * pk["max_displacement_mm"]
    # a free part has no supports to shake: skipped with a note, the modes are still there
    rf = modal_fem.solve(m, mat, [], n_modes=2, log=lambda s: None, sine={"axis": "z"})
    assert "sine" not in rf["report"] and any("Shake test skipped" in w for w in rf["report"]["warnings"])
    # the largest value over a cycle of two out-of-phase parts: a cos + b sin has the peak sqrt(a^2 + b^2)
    assert modal_fem.cycle_peak(9.0, 16.0, 0.0) == pytest.approx(16.0)
    assert modal_fem.cycle_peak(1.0, 1.0, 1.0) == pytest.approx(2.0)


def test_vibration_pipeline(workspace):
    P = workspace
    pr = P.create_project("Bracket", "vibration_modal")
    _wait(pr.import_step(ROOT / "examples" / "camera_mount.step"))
    assert pr.bodies()[0]["name"] == "Camera bracket"
    holes = [f["id"] for f in pr.faces() if f["surface_type"] == "Cylinder"]
    front = [f["id"] for f in pr.faces() if abs(f["normal"][0]) > 0.9 and abs(f["centroid"][0] - 3e-3) < 1e-4]
    assert len(holes) == 2 and len(front) == 1
    assert any("free-free" in c["message"] for c in pr.check())  # no support yet
    pr.add_bc("Bolts", "fixed_support", holes)
    pr.add_bc("Camera", "added_mass", front, {"mass_kg": 0.06})
    pr.set_settings(excitation_min_hz=100, excitation_max_hz=400, n_modes=6)
    pr.set_mesh(preset="coarse")
    _wait(pr.generate_mesh())
    rep = _wait(pr.solve())
    el = [m for m in rep["modes"] if not m["rigid_body"]]
    assert len(el) == 6 and rep["n_rigid_body_modes"] == 0
    assert all(a["frequency_Hz"] <= b["frequency_Hz"] for a, b in zip(el, el[1:]))
    assert abs(rep["added_mass_kg"] - 0.06) < 1e-12 and rep["mass_kg"] > 0.06
    assert rep["modes_in_band"] and any("resonance" in w for w in rep["warnings"])  # 60 g camera: ~350 Hz
    s = pr.results().surface("Mode 1")
    assert "disp" in s and s["n_tris"] > 0
    assert set(pr.results().fields) == {f"Mode {i}" for i in range(1, 7)}
    # "from 0 up to X" is the whole range 0..X (round-11 critic: it used to check only X +- 15 %)
    pr.set_settings(excitation_min_hz=0, excitation_max_hz=1000)
    with pytest.raises(ValueError):
        pr.set_settings(n_modes=2.5)
    rep = _wait(pr.solve())
    assert rep["excitation_band_Hz"] == [0.0, 1000.0] and 1 in rep["modes_in_band"]
    assert [m["mode"] for m in rep["modes"]] == list(range(1, 7))
    # shake test through the project: the sweep covers the excitation range, the report is plain JSON,
    # the amplitudes at the worst frequency are result fields
    with pytest.raises(ValueError):
        pr.set_settings(shake_axis="up")
    pr.set_settings(shake_axis="z", shake_accel_g=2.0, damping_percent=1.0)
    rep = _wait(pr.solve())
    sn = json.loads(json.dumps(rep["sine"]))
    assert sn["axis"] == "z" and sn["accel_g"] == pytest.approx(2.0) and sn["range_Hz"][1] == 1000.0
    assert sn["peaks"] and sn["worst"]["max_stress_MPa"] == pytest.approx(max(sn["max_stress_MPa"]))
    assert len(sn["frequency_Hz"]) == len(sn["max_stress_MPa"]) == len(sn["max_acceleration_g"])
    assert {"Shake test: stress amplitude", "Shake test: displacement amplitude"} <= set(pr.results().fields)
    # linear: half the amplitude and twice the damping give a quarter of the resonant stress
    pr.set_settings(shake_accel_g=1.0, damping_percent=2.0)
    rep2 = _wait(pr.solve())
    assert rep2["sine"]["peaks"][0]["max_stress_MPa"] == pytest.approx(sn["peaks"][0]["max_stress_MPa"] / 4, rel=0.02)


def test_modal_free_slender_bar_passes_the_residual_check():
    """A free slender bar (nearly singular K - sigma M): PARDISO's factorisation gave residuals of 1e-2 and the solve
    was refused, run to run. It is solved again with SuperLU then. Free-free bending of a 200 x 10 x 10 mm bar:
    1286.6 Hz with shear and rotary inertia (Euler-Bernoulli 1298)."""
    from simwb.solvers import modal_fem

    m = _beam_mesh(0.2, 0.01, 0.01, 80, 4, 4)
    mat = {1: {"E": 68.9e9, "nu": 0.33, "rho": 2700.0}}
    for _ in range(2):
        r = modal_fem.solve(m, mat, [], n_modes=4, log=lambda s: None)
        assert r["report"]["n_rigid_body_modes"] == 6 and r["report"]["max_relative_residual"] < 1e-3
        assert abs(r["report"]["first_frequency_Hz"] / 1286.6 - 1) < 0.02


def test_modal_offset_mass_and_soft_free_part():
    """Added mass with a CG height = an explicitly modelled tip block; a free part with a soft pad stays accurate."""
    from simwb.solvers import modal_fem

    L, b, h, E, nu, rho = 0.1, 0.01, 0.005, 69e9, 0.33, 2700.0
    mb = rho * L * b * h
    clamp = {"name": "c", "type": "fixed_support", "faces": [1], "params": {}}
    # explicit dense, stiff 20 mm block beyond the tip (region 2), CG 10 mm beyond the end face
    m = _beam_mesh(L + 0.02, b, h, 48, 4, 2)
    cen = m["nodes"][m["tets"]].mean(1)[:, 0]
    m["tet_vol"] = np.where(cen > L, 2, 1).astype(np.int32)
    m["tri_surf"] = np.where(m["tri_surf"] == 1, 1, 2).astype(np.int32)
    r = modal_fem.solve(m, {1: {"E": E, "nu": nu, "rho": rho}, 2: {"E": 10 * E, "nu": nu, "rho": mb / (0.02 * b * h)}},
                        [clamp], n_modes=2, log=lambda s: None)
    f_block = [x["frequency_Hz"] for x in r["modes"]]
    m = _beam_mesh(L, b, h, 40, 4, 2)
    tip = {"name": "tip", "type": "added_mass", "faces": [3], "params": {"mass_kg": mb, "cg_height_mm": 10.0}}
    r = modal_fem.solve(m, {1: {"E": E, "nu": nu, "rho": rho}}, [clamp, tip], n_modes=2, log=lambda s: None)
    for fa, fb in zip([x["frequency_Hz"] for x in r["modes"]], f_block):
        assert abs(fa / fb - 1) < 0.01
    assert abs(r["report"]["mass_kg"] - 2 * mb) < 1e-9
    # a CG height on faces facing different ways (the four sides) has no direction: thin layer + warning
    side = {"name": "wrap", "type": "added_mass", "faces": [2], "params": {"mass_kg": mb, "cg_height_mm": 10.0}}
    r = modal_fem.solve(m, {1: {"E": E, "nu": nu, "rho": rho}}, [clamp, side], n_modes=1, log=lambda s: None)
    assert r["report"]["boundaries"]["wrap"].get("cg_ignored") and any("flat" in w for w in r["report"]["warnings"])
    # free copper cube on a soft pad: six rigid modes, accurate (small residual) and repeatable
    m2 = _beam_mesh(0.03, 0.03, 0.035, 6, 6, 7)
    cz = m2["nodes"][m2["tets"]].mean(1)[:, 2]
    m2["tet_vol"] = np.where(cz < 0.005, 2, 1).astype(np.int32)
    mats = {1: {"E": 117e9, "nu": 0.34, "rho": 8940}, 2: {"E": 1e6, "nu": 0.45, "rho": 2500}}
    f = []
    for _ in range(2):
        r = modal_fem.solve(m2, mats, [], n_modes=3, log=lambda s: None)
        assert r["report"]["n_rigid_body_modes"] == 6 and r["report"]["max_relative_residual"] < 1e-4
        f.append(r["report"]["first_frequency_Hz"])
    assert abs(f[0] / f[1] - 1) < 1e-6


def test_static_cantilever_matches_beam_theory():
    """Tip-loaded cantilever: deflection P L^3 / 3 E I and bending stress M c / I; reactions balance the loads."""
    from simwb.solvers import static_fem

    L, b, h, E, P = 0.2, 0.02, 0.01, 200e9, 100.0
    m = _beam_mesh(L, b, h, 20, 3, 3)
    props = {1: {"E": E, "nu": 0.3, "rho": 7850.0, "yield": 250e6, "name": "beam"}}
    bcs = [{"name": "root", "type": "fixed_support", "faces": [1]},
           {"name": "tip", "type": "force", "faces": [3], "params": {"fx_N": 0, "fy_N": 0, "fz_N": -P}}]
    r = static_fem.solve(m, props, bcs, log=lambda *a: None)
    I = b * h ** 3 / 12
    assert abs(r["report"]["max_displacement_mm"] / (P * L ** 3 / (3 * E * I) * 1e3) - 1) < 0.02
    x, z = m["nodes"][:, 0], m["nodes"][:, 2]
    top_mid = (abs(x - L / 2) < 1e-9) & (abs(z - h) < 1e-9)
    assert abs(r["von_mises"][top_mid].mean() / (P * L / 2 * h / 2 / I) - 1) < 0.02
    assert abs(r["report"]["reaction_force_N"][2] - P) < 1e-6 and r["report"]["force_balance_error"] < 1e-6
    # own weight: w L^4 / 8 E I
    g = static_fem.solve(m, props, bcs[:1], gravity=[0, 0, -9.81], log=lambda *a: None)
    w = 7850.0 * b * h * 9.81
    assert abs(g["report"]["max_displacement_mm"] / (w * L ** 4 / (8 * E * I) * 1e3) - 1) < 0.03
    # a frictionless end alone cannot hold the beam: clear message, no garbage result
    with pytest.raises(ValueError, match="not held|hold the part"):
        static_fem.solve(m, props, [dict(bcs[0], type="frictionless_support"), bcs[1]], log=lambda *a: None)
    # bending: the largest principal stress is the fibre stress on the tension side, the smallest on the other;
    # a smooth field on a regular mesh has a small error estimate
    f = r["fields"]
    assert abs(f["Max principal stress"][0][top_mid].mean() / (P * L / 2 * h / 2 / I) - 1) < 0.03
    bot_mid = (abs(x - L / 2) < 1e-9) & (abs(z) < 1e-9)
    assert abs(f["Min principal stress"][0][bot_mid].mean() / (-P * L / 2 * h / 2 / I) - 1) < 0.03
    assert 0 < r["report"]["stress_error_percent"] < 15
    assert f["Safety factor"][0].max() <= 15 and abs(f["Safety factor"][0].min() - r["report"]["min_safety_factor"]) < 1e-9


def test_static_stress_is_not_averaged_across_materials():
    """Two bars side by side, stretched together (nu = 0: exact uniaxial stress E * strain in each). At the shared
    nodes the stress jumps by the stiffness ratio: each part must report its own stress, not a mixture."""
    from simwb.solvers import static_fem

    L, b, h, eps = 0.1, 0.02, 0.01, 1e-4
    m = _beam_mesh(L, b, h, 8, 4, 2)
    cy = m["nodes"][m["tets"]].mean(1)[:, 1]
    m["tet_vol"] = np.where(cy < b / 2, 1, 2).astype(np.int32)
    props = {1: {"E": 200e9, "nu": 0.0, "rho": 7850.0, "yield": 250e6, "name": "steel"},
             2: {"E": 70e9, "nu": 0.0, "rho": 2700.0, "yield": 250e6, "name": "aluminium"}}
    bcs = [{"name": "root", "type": "displacement", "faces": [1], "params": {"ux_mm": 0, "uy_mm": 0, "uz_mm": 0}},
           {"name": "pull", "type": "displacement", "faces": [3], "params": {"ux_mm": eps * L * 1e3, "uy_mm": 0, "uz_mm": 0}}]
    r = static_fem.solve(m, props, bcs, log=lambda *a: None)
    by = {b_["name"]: b_["max_von_mises_MPa"] for b_ in r["report"]["bodies"]}
    assert abs(by["steel"] / (200e9 * eps / 1e6) - 1) < 1e-6
    assert abs(by["aluminium"] / (70e9 * eps / 1e6) - 1) < 1e-6  # (was the mean of both at the interface nodes)
    assert r["report"]["stress_error_percent"] < 1e-4  # constant stress in each part: nothing to average away
    assert abs(r["report"]["max_principal_MPa"] - 20.0) < 1e-6


def test_static_pipeline(workspace):
    P = workspace
    pr = P.create_project("Bracket static", "static_structural")
    _wait(pr.import_step(ROOT / "examples" / "camera_mount.step"))
    holes = [f["id"] for f in pr.faces() if f["surface_type"] == "Cylinder"]
    front = [f["id"] for f in pr.faces() if abs(f["normal"][0]) > 0.9 and abs(f["centroid"][0] - 3e-3) < 1e-4]
    assert any(c["level"] == "error" and "support" in c["message"] for c in pr.check())
    pr.add_bc("Bolts", "fixed_support", holes)
    assert any(c["level"] == "error" and "load" in c["message"] for c in pr.check())
    pr.add_bc("Camera", "force", front, {"fx_N": -20.0, "fy_N": 0, "fz_N": 0})
    pr.add_bc("Pre-set", "displacement", [], {"ux_mm": 0.1})  # blank components stay free (None)
    assert pr.data["bcs"][-1]["params"]["uy_mm"] is None
    pr.delete_bc(pr.data["bcs"][-1]["id"])
    pr.set_settings(gravity="-z")
    pr.set_mesh(preset="coarse")
    _wait(pr.generate_mesh())
    rep = _wait(pr.solve())
    assert rep["max_von_mises_MPa"] > 0 and rep["max_displacement_mm"] > 0 and rep["min_safety_factor"] > 0
    assert rep["force_balance_error"] < 1e-6
    assert {"Von Mises stress", "Total deformation", "Max principal stress", "Min principal stress",
            "Stress error estimate", "Safety factor"} == set(pr.results().fields)
    assert "disp" in pr.results().surface("Total deformation")


def _write_shape(path, make):
    import gmsh

    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.model.add("s")
    make(gmsh.model.occ)
    gmsh.model.occ.synchronize()
    gmsh.write(str(path))
    gmsh.finalize()


def test_sphere_and_cone_import(workspace, tmp_path):
    """Degenerate edges (sphere poles, cone apex) used to break STEP healing: 'Identical points in triangulation',
    '1D mesh not a closed loop'. Unhealed import first; the aero far-field box faces must still be found."""
    P = workspace
    sphere, cone = tmp_path / "sphere.step", tmp_path / "cone.step"
    _write_shape(sphere, lambda occ: occ.addSphere(0, 0, 0, 20))
    _write_shape(cone, lambda occ: occ.addCone(0, 0, 0, 0, 0, 40, 15, 0))
    for name, f in (("sphere", sphere), ("cone", cone)):
        pr = P.create_project(f"{name}-heat", "heat_conduction")
        _wait(pr.import_step(f))
        pr.set_mesh(preset="coarse")
        _wait(pr.generate_mesh())
    pr = P.create_project("sphere-aero", "external_aero")
    _wait(pr.import_step(sphere))
    kinds = [f["kind"] for f in pr.faces()]
    assert kinds.count("farfield") == 6 and kinds.count("body") == 1


def test_stl_and_obj_import(workspace, tmp_path):
    """Triangle meshes: faces from sharp edges, a solid per closed shell, an air box for flow studies."""
    import gmsh

    P = workspace
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    b = gmsh.model.occ.addBox(0, 0, 0, 40, 20, 10)
    c = gmsh.model.occ.addCylinder(20, 10, -1, 0, 0, 12, 3)
    gmsh.model.occ.cut([(3, b)], [(3, c)])
    gmsh.model.occ.synchronize()
    gmsh.option.setNumber("Mesh.MeshSizeMax", 2)
    gmsh.model.mesh.generate(2)
    gmsh.option.setNumber("Mesh.Binary", 1)
    gmsh.write(str(tmp_path / "part.stl"))
    nt, xyz, _ = gmsh.model.mesh.getNodes()
    xyz = np.asarray(xyz).reshape(-1, 3)
    idx = {int(t): i + 1 for i, t in enumerate(nt)}
    lines = [f"v {p[0]} {p[1]} {p[2]}" for p in xyz]
    for _, s in gmsh.model.getEntities(2):
        en = gmsh.model.mesh.getElements(2, s)[2][0]
        lines += ["f " + " ".join(str(idx[int(x)]) for x in tri) for tri in np.asarray(en).reshape(-1, 3)]
    (tmp_path / "part.obj").write_text("\n".join(lines))
    gmsh.finalize()
    exact = (40 * 20 * 10 - math.pi * 9 * 10) * 1e-9
    pr = P.create_project("stl", "static_structural")
    _wait(pr.import_step(tmp_path / "part.stl"))
    fs = pr.faces()
    assert abs(pr.bodies()[0]["volume"] / exact - 1) < 0.01
    left = [f["id"] for f in fs if abs(f["normal"][0] + 1) < 1e-6]
    right = [f["id"] for f in fs if abs(f["normal"][0] - 1) < 1e-6]
    assert len(left) == 1 and len(right) == 1  # flat ends are single faces
    pr.add_bc("fix", "fixed_support", left)
    pr.add_bc("pull", "force", right, {"fx_N": 1000.0, "fy_N": 0, "fz_N": 0})
    pr.set_mesh(preset="coarse")
    _wait(pr.generate_mesh())
    rep = _wait(pr.solve())
    assert rep["force_balance_error"] < 1e-6 and rep["max_von_mises_MPa"] > 1000 / 200  # above the gross stress
    pr = P.create_project("obj-aero", "external_aero")
    _wait(pr.import_step(tmp_path / "part.obj"))
    kinds = [f["kind"] for f in pr.faces()]
    assert kinds.count("farfield") == 6 and "body" in kinds
    # an open mesh is refused with a clear message
    (tmp_path / "open.obj").write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
    job = P.create_project("open", "heat_conduction").import_step(tmp_path / "open.obj")
    job.wait(120)
    assert job.status == "failed" and "not a closed solid" in job.error
