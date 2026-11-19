"""Transient analyses (built-in heat, OpenFOAM case files) and the automatic air volume (shapes, sizes, roles)."""
import base64
import json
import math
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
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


def _bar(nx=30, L=0.1, W=0.01):
    """Structured tet mesh of a bar; faces: 1 = x=0, 2 = x=L, 3 = sides."""
    xs, ys = np.linspace(0, L, nx + 1), np.linspace(0, W, 3)
    P = np.array([[x, y, z] for x in xs for y in ys for z in ys])
    idx = lambda i, j, k: (i * 3 + j) * 3 + k  # noqa: E731
    tets = []
    for i in range(nx):
        for j in range(2):
            for k in range(2):
                c = [idx(i + a, j + b, k + d) for a in (0, 1) for b in (0, 1) for d in (0, 1)]
                v0, v1, v2, v3, v4, v5, v6, v7 = c[0], c[4], c[6], c[2], c[1], c[5], c[7], c[3]
                tets += [[v0, v1, v2, v6], [v0, v2, v3, v6], [v0, v3, v7, v6], [v0, v7, v4, v6], [v0, v4, v5, v6],
                         [v0, v5, v1, v6]]
    tets = np.array(tets)
    from collections import Counter

    cnt = Counter(tuple(sorted(t[q])) for t in tets for q in ([0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]))
    tris = np.array([k for k, v in cnt.items() if v == 1])
    cx = P[tris].mean(1)[:, 0]
    return {"nodes": P, "tets": tets, "tet_vol": np.ones(len(tets), int), "tris": tris,
            "tri_surf": np.where(cx < 1e-9, 1, np.where(cx > L - 1e-9, 2, 3))}


def test_transient_heat_matches_exact_solutions():
    from simwb.solvers import thermal_fem

    m, k, rc, L = _bar(), 100.0, 2000.0 * 500.0, 0.1
    alpha = k / rc
    props = {1: {"k": k, "heat_W": 0.0, "name": "bar", "rho_cp": rc}}
    # slab insulated at x = 0, held at 100 at x = L from t = 0, start at 0: series solution at Fo = 0.2
    t_end = 0.2 * L * L / alpha
    res = thermal_fem.solve(m, props, [{"name": "hot", "type": "fixed_temperature", "faces": [2], "params": {"T": 100.0}}],
                            log=lambda *_: None, transient={"end_time": t_end, "dt": 0, "T0": 0.0, "n_frames": 5})
    exact = 100 * (1 - sum(4 * (-1) ** n / ((2 * n + 1) * math.pi) * math.exp(-((2 * n + 1) * math.pi / 2) ** 2 * 0.2)
                           for n in range(50)))
    assert abs(res["T"][m["nodes"][:, 0] < 1e-9].mean() - exact) < 0.1
    assert len(res["frames"]["times"]) == 5 and len(res["frames"]["q_mag"]) == 5
    eb = res["report"]["energy_balance"]
    assert eb["imbalance_percent"] < 1e-6 and eb["heat_stored_W"] > 0
    # adiabatic bar with a 50 W source: T rises at Q / (rho cp V), exactly (no cooling needed in transient)
    props[1]["heat_W"] = 50.0
    res2 = thermal_fem.solve(m, props, [], log=lambda *_: None, transient={"end_time": 100.0, "dt": 5.0, "T0": 20.0})
    assert abs(res2["T"].mean() - (20 + 50 * 100 / (rc * 1e-5))) < 1e-6
    assert res2["report"]["history"]["t"][-1] == pytest.approx(100.0)


def test_result_frames_roundtrip(tmp_path):
    from simwb.results import ResultSet

    nodes = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1.0]])
    tets = np.array([[0, 1, 2, 3]])
    tris = np.array([[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]])
    fr = [np.full(4, v, float) for v in (1.0, 2.0, 3.0)]
    rs = ResultSet(nodes, tets, [0], ["a"], {"T": ("node", fr[-1], "C"), "q": ("cell", np.ones(1), "W")}, tris,
                   np.zeros(4, int), np.zeros(4, int), ["g"], {"q": np.ones(4)}, times=[1, 2, 3],
                   frames={"T": fr, "s:q": [np.full(4, v) for v in (5.0, 6.0, 7.0)]})
    rs.save(tmp_path / "r.npz")
    r2 = ResultSet.load(tmp_path / "r.npz")
    assert r2.times == [1, 2, 3]
    assert r2.at(0).fields["T"][1][0] == 1.0 and r2.at(0).surf_values["q"][0] == 5.0
    assert r2.at(-1) is r2 and r2.at(99) is r2
    info = {f["name"]: f for f in r2.field_info()}
    assert info["T"]["min"] == 1.0 and info["T"]["max"] == 3.0  # (colour range over all times)
    assert info["T"]["change_over_time"] == pytest.approx(1.0) and "change_over_time" not in info["q"]


def test_air_volume_shapes_resolve_and_roles():
    from simwb import domain

    bb = [0, 0, 0, 0.1, 0.02, 0.006]
    box = domain.resolve({}, "cht_enclosure", bb, 0.1)
    assert box["shape"] == "box" and box["fitted"] and np.allclose(box["min"], [-0.05, -0.05, -0.05])
    c = domain.resolve({"shape": "c_domain", "flow_axis": "+x"}, "cht_enclosure", bb, 0.1)
    assert c["span_axis"] == 1 and c["third_axis"] == 2 and c["downstream"] >= c["radius"]
    assert domain.check_encloses(c, bb, 0.1) is None
    small = domain.resolve({"shape": "c_domain", "size": {"radius": 0.01}}, "cht_enclosure", bb, 0.1)
    assert "radius" in domain.check_encloses(small, bb, 0.1)
    # aero: the curve lies in the flow / up plane, so the span axis is the remaining one
    a = domain.resolve({"shape": "c_domain"}, "external_aero", bb, 0.1, 0.1, (-1, 0, 0), up_axis=2)
    assert a["flow_axis"] == 0 and a["flow_sign"] == -1 and a["span_axis"] == 1
    assert a["bbox_max"][0] == pytest.approx(0.05 + a["radius"])  # inlet curve upstream (+x: flow goes to -x)
    cyl = domain.resolve({"shape": "cylinder", "size": {"radius": 0.3}}, "external_aero", bb, 0.1, 0.1, (1, 0, 0), 2)
    assert cyl["radius"] == 0.3 and not cyl["fitted"]
    # roles: flat face at the downstream end = outlet; curved face of a C = inlet; others = side
    tol = 1e-6
    x_out = c["center"][0] + c["downstream"]
    assert domain.face_role({"bbox": [x_out, -1, -1, x_out, 1, 1], "surface_type": "Plane"}, c, tol) == "outlet"
    assert domain.face_role({"bbox": [-0.2, -1, -1, 0.05, 1, 1], "surface_type": "Cylinder"}, c, tol) == "inlet"
    assert domain.face_role({"bbox": [-0.2, -1, -1, 0.3, 1, -1], "surface_type": "Plane"}, c, tol) == "side"


def test_c_domain_cooling_case(workspace, tmp_path):
    """C-shaped air volume around a part: built, inlet on the curve with uniform flow, transient case files."""
    import gmsh

    step = tmp_path / "plate.step"
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.model.occ.addBox(0, 0, 0, 100, 20, 6)
    gmsh.model.occ.synchronize()
    gmsh.write(str(step))
    gmsh.finalize()
    P = workspace
    pr = P.create_project("C", "cht_enclosure")
    pr.data["domain"]["enclosure_mode"] = "auto_box"
    pr.save()
    _wait(pr.import_step(step))
    with pytest.raises(ValueError, match="radius"):
        pr.set_domain(shape="c_domain", size={"radius": 0.01})
    _wait(pr.set_domain(shape="c_domain"))
    spec = pr.data["geometry"]["info"]["domain"]["spec"]
    assert spec["shape"] == "c_domain" and spec["fitted"]
    roles = [f.get("domain_role") for f in pr.faces() if f["kind"] == "enclosure"]
    assert roles.count("inlet") == 1 and roles.count("outlet") == 1
    io = pr.suggest_inlet_outlet()
    inlet = next(b for b in pr.data["bcs"] if b["type"] == "velocity_inlet")
    assert inlet["faces"] == io["inlet"] and inlet["params"]["direction"] == "+x"
    solid = next(b for b in pr.bodies() if b["role"] == "solid")
    pr.set_body(solid["id"], material="aluminium_6061", heat_W=5.0)
    pr.set_settings(analysis="transient", end_time=2.0, n_frames=5)
    pr.set_mesh(preset="quick")
    _wait(pr.generate_mesh())
    _wait(pr.solve(export_only=True))
    case = pr.case_dir
    meta = json.loads((case / "simwb_case.json").read_text())
    assert meta["app"] == "chtMultiRegionFoam" and meta["transient"]["write_interval"] == pytest.approx(0.5)
    cd = (case / "system" / "controlDict").read_text()
    assert "adjustTimeStep  yes" in cd and "purgeWrite      0" in cd and "endTime         2;" in cd
    assert "PIMPLE" in (case / "system" / "air" / "fvSolution").read_text()
    assert "Euler" in (case / "system" / "air" / "fvSchemes").read_text()
    U = (case / "0.orig" / "air" / "U").read_text()
    assert "uniform (2 0 0)" in U  # uniform flow along +x on the curved inlet
    # flow through the curved inlet counts its area seen along x: 2 m/s x (2 R x span)
    A = 2 * spec["radius"] * (spec["span_max"] - spec["span_min"])
    assert meta["inlets"][0]["area"] == pytest.approx(A, rel=0.03)


def test_example_quadcopter_arrives_with_its_mass_and_a_missing_mass_points_to_its_input(workspace, tmp_path):
    """The bundled quadcopter must be ready to set up (no empty vehicle mass), and when a mass is missing the message
    carries the input that fixes it (the GUI jumps there on a click)."""
    P = workspace
    pr = P.create_project("Quad", "external_aero")
    _wait(pr.import_step(ROOT / "examples" / "quadcopter.step", original_name="quadcopter.step"))
    assert pr.data["settings"]["mass_kg"] > 0
    assert not [c for c in pr.check() if c["level"] == "error" and "mass" in c["message"]]
    pr.set_settings(mass_kg=0)
    miss = [c for c in pr.check() if c["level"] == "error" and "vehicle mass" in c["message"]]
    assert miss and miss[0]["target"] == '[data-setting="mass_kg"]'
    # a user's own file with the same name, from another folder, gets no assumed mass
    import shutil
    mine = tmp_path / "quadcopter.step"
    shutil.copy(ROOT / "examples" / "quadcopter.step", mine)
    pr2 = P.create_project("Mine", "external_aero")
    _wait(pr2.import_step(mine))
    assert not pr2.data["settings"]["mass_kg"]


def test_transient_aero_case(workspace):
    P = workspace
    pr = P.create_project("Quad", "external_aero")
    _wait(pr.import_step(ROOT / "examples" / "quadcopter.step"))
    pr.set_settings(speed=8, mass_kg=1.5, analysis="transient", end_time=0.2, time_step=1e-4, n_frames=3)
    pr.set_mesh(preset="quick")
    _wait(pr.generate_mesh())
    _wait(pr.solve(export_only=True))
    meta = json.loads((pr.case_dir / "simwb_case.json").read_text())
    assert meta["app"] == "pimpleFoam" and meta["steps"][-1]["app"] == "pimpleFoam"
    cd = (pr.case_dir / "system" / "controlDict").read_text()
    assert "adjustTimeStep  no" in cd and "deltaT          0.0001;" in cd and "writeControl    runTime" in cd
    assert "backward" in (pr.case_dir / "system" / "fvSchemes").read_text()
    assert "pFinal" in (pr.case_dir / "system" / "fvSolution").read_text()
    # vortex-resolving DES: LES block with the DDES model, low-dissipation velocity scheme; steady runs refused
    pr.set_settings(turbulence="kOmegaSSTDDES")
    _wait(pr.solve(export_only=True))
    tp = (pr.case_dir / "constant" / "turbulenceProperties").read_text()
    assert "simulationType  LES;" in tp and "kOmegaSSTDDES" in tp and "DeltaOmegaTilde" in tp
    assert "div(phi,U)      Gauss LUST grad(U);" in (pr.case_dir / "system" / "fvSchemes").read_text()
    pr.set_settings(analysis="steady")
    with pytest.raises(Exception):
        _wait(pr.solve(export_only=True))


def test_ring_size_field():
    """A ring (tube) region: fine cells between the inner and outer radius, coarser towards the axis too."""
    from simwb.gmsh_worker import cylinder_size_expr
    e = cylinder_size_expr([0, 0, 0], [0, 0, 1], 1.0, 0.01, 0.1, 0.08, inner=0.6)
    f = lambda x, y, z: eval(e.replace("^", "**"), {"sqrt": math.sqrt, "max": max, "min": min, "x": x, "y": y, "z": z})
    assert f(0.8, 0, 0.5) == pytest.approx(0.01)           # in the ring
    assert f(0.0, 0, 0.5) == pytest.approx(0.1)            # on the axis: far size
    assert 0.01 < f(0.56, 0, 0.5) < 0.1                     # growing towards the axis
    assert f(1.2, 0, 0.5) == pytest.approx(0.1)            # outside


def test_force_history_and_time_average():
    from simwb.solvers.openfoam.results_loader import force_history

    blk = ("Time = {t}\n\nforces forces write:\n    Sum of forces\n        Total    : ({f} 0 0)\n"
           "        Pressure : ({f} 0 0)\n        Viscous  : (0 0 0)\n    Sum of moments\n        Total    : (0 0 {f})\n")
    txt = "".join(blk.format(t=t, f=f) for t, f in [(0.1, 5.0), (0.2, 1.0), (0.3, 2.0), (0.4, 3.0)])
    T, Ft, Fp, Fv, M = force_history(txt)
    assert list(T) == [0.1, 0.2, 0.3, 0.4] and list(Ft[:, 0]) == [5, 1, 2, 3] and list(M[:, 2]) == [5, 1, 2, 3]


def test_natural_convection_bc():
    """Still air: h per face from correlations (orientation to gravity), radiation to the room, heat balance."""
    from simwb.solvers import thermal_fem

    h = thermal_fem.h_natural(np.array([60.0, 60.0, 60.0]), 25.0, np.array([0.0, 1.0, -1.0]),
                              np.array([0.1] * 3), np.array([0.025] * 3))
    assert 5.0 < h[0] < 7.0  # vertical 10 cm plate, dT 35 K: ~6 W/m2K (textbook range)
    assert h[1] > h[0] > h[2]  # hot face up > vertical > hot face down
    m = _bar()
    props = {1: {"k": 200.0, "heat_W": 1.0, "name": "bar", "rho_cp": 2.4e6}}
    bc = {"name": "air", "type": "natural_convection", "faces": [1, 2, 3],
          "params": {"T_inf": 20.0, "emissivity": 0.8, "up": "+z"}}
    res = thermal_fem.solve(m, props, [bc], log=lambda *_: None)
    b = res["report"]["boundaries"]["air"]
    assert res["report"]["energy_balance"]["imbalance_percent"] < 1e-3
    assert b["heat_out_W"] == pytest.approx(1.0, rel=1e-4) and b["heat_out_radiation_W"] > 0
    assert b["h_range_W_m2K"][0] < b["h_mean_W_m2K"] < b["h_range_W_m2K"][1]
    assert 20.0 < res["T"].min() and res["T"].max() < 200.0


def test_natural_convection_cfd_case(workspace, tmp_path):
    """Electronics cooling with no fan: plume room in the air box, open sides at the room's hydrostatic pressure,
    radiation files, no inlet needed."""
    import gmsh

    step = tmp_path / "plate.step"
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.model.occ.addBox(0, 0, 0, 100, 2, 100)
    gmsh.model.occ.synchronize()
    gmsh.write(str(step))
    gmsh.finalize()
    P = workspace
    pr = P.create_project("N", "cht_enclosure")
    pr.data["domain"]["enclosure_mode"] = "auto_box"
    pr.save()
    _wait(pr.import_step(step))
    solid = next(b for b in pr.bodies() if b["role"] == "solid")
    pr.set_body(solid["id"], material="aluminium_6061", heat_W=5.0)
    job = pr.set_settings(air_flow="natural", radiation="on", emissivity=0.85)
    assert job is not None  # the fitted air box is rebuilt with room for the plume
    _wait(job)
    spec = pr.data["geometry"]["info"]["domain"]["spec"]
    assert spec["max"][2] == pytest.approx(0.1 + 2 * 0.1, abs=1e-3) and spec["min"][2] == pytest.approx(-0.05, abs=1e-3)
    errs = [c["message"] for c in pr.state()["checks"] if c["level"] == "error" and c["step"] == "setup"]
    assert not errs, errs
    pr.set_mesh(preset="quick")
    _wait(pr.generate_mesh())
    _wait(pr.solve(export_only=True))
    case = pr.case_dir
    prgh = (case / "0.orig" / "air" / "p_rgh").read_text()
    assert "open_sides" in prgh and "totalPressure" in prgh and "prghPressure" not in prgh
    assert "fvDOM" in (case / "constant" / "air" / "radiationProperties").read_text()
    assert "emissivity      0.85" in (case / "constant" / "air" / "boundaryRadiationProperties").read_text()
    meta = json.loads((case / "simwb_case.json").read_text())
    assert "qrNbr qr" in (case / "0.orig" / meta["solid_regions"][0] / "T").read_text()
    assert meta["natural"] and meta["radiation"] and meta["outlet_patches"] == ["open_sides"]
    assert meta["flow_regime"]["suggested"] == "laminar"


def test_advanced_openfoam_and_case_overrides(workspace):
    """Turbulence model choice, advanced numerics and user-edited case files reach the case that is run."""
    P = workspace
    pr = P.create_project("Adv", "external_aero")
    _wait(pr.import_step(ROOT / "examples" / "quadcopter.step"))
    pr.set_settings(speed=8, mass_kg=1.5, turbulence="kEpsilon")
    pr.set_mesh(preset="quick")
    _wait(pr.generate_mesh())
    pr.set_foam_advanced(convection="first order", non_orth=2, relax_p=0.2, tol_factor=0.1)
    with pytest.raises(ValueError):
        pr.set_foam_advanced(relax_p=2)
    files = pr.foam_preview()
    assert "system/controlDict" in files["files"] and not any("polyMesh" in f for f in files["files"])
    cd = pr.case_file("system/controlDict")["text"]
    pr.set_case_file("system/controlDict", cd.replace("writePrecision  8;", "writePrecision  9;"))
    with pytest.raises(ValueError):
        pr.set_case_file("constant/polyMesh/points", "x")
    with pytest.raises(ValueError):
        pr.set_case_file("../evil", "x")
    _wait(pr.solve(export_only=True))
    c = pr.case_dir
    assert "RASModel        kEpsilon;" in (c / "constant" / "turbulenceProperties").read_text()
    assert (c / "0" / "epsilon").exists() and (c / "0" / "nuTilda").exists()
    sch = (c / "system" / "fvSchemes").read_text()
    assert "div(phi,U)      bounded Gauss upwind;" in sch and "div(phi,epsilon)" in sch
    sol = (c / "system" / "fvSolution").read_text()
    assert "nNonOrthogonalCorrectors 2;" in sol and "epsilon" in sol
    assert "p               0.2;" in sol or "p 0.2;" in sol.replace("  ", " ")
    assert "writePrecision  9;" in (c / "system" / "controlDict").read_text()
    pr.set_case_file("system/controlDict", None)
    assert "system/controlDict" not in pr.data["foam_overrides"]


def test_moving_zones_mrf_and_sliding_case(workspace, tmp_path):
    """Rotating zone: boundary meshed exactly (embedded), MRFProperties for steady runs; sliding mesh writes the face
    zone, createBaffles (cyclicAMI pair) and a solid-body rotation."""
    import gmsh

    step = tmp_path / "prop.step"
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    b = gmsh.model.occ.addBox(-50, -6, -1, 100, 12, 2)
    gmsh.model.occ.rotate([(3, b)], 0, 0, 0, 1, 0, 0, math.radians(20))
    gmsh.model.occ.synchronize()
    gmsh.write(str(step))
    gmsh.finalize()
    P = workspace
    pr = P.create_project("MZ", "external_aero")
    _wait(pr.import_step(step))
    pr.set_settings(speed=5, mass_kg=0)
    zone = {"name": "prop", "center": [0, 0, 0], "axis": "+z", "radius": 0.06, "length": 0.02, "rpm": 3000, "method": "mrf"}
    pr.set_moving_zones([dict(zone, radius=0.03)])  # cuts through the blade
    assert any("cuts through" in c["message"] for c in pr.state()["checks"] if c["level"] == "error")
    pr.set_moving_zones([zone])
    assert not [c for c in pr.state()["checks"] if c["level"] == "error" and c["step"] == "setup"]
    with pytest.raises(ValueError):
        pr.set_moving_zones([zone, dict(zone, name="b", method="sliding"), dict(zone, name="c", method="sliding")])
    pr.set_mesh(preset="quick")
    _wait(pr.generate_mesh())
    m = pr.load_mesh()
    assert len(m["zone_tris"]) > 100
    _wait(pr.solve(export_only=True))
    mrf = (pr.case_dir / "constant" / "MRFProperties").read_text()
    assert "cellZone        prop;" in mrf and "omega           314.15927;" in mrf
    assert "prop" in (pr.case_dir / "constant" / "polyMesh" / "cellZones").read_text()
    # the walls inside the zone (the blade) are a patch of their own with a force monitor: thrust of the propeller
    assert "prop_walls" in (pr.case_dir / "constant" / "polyMesh" / "boundary").read_text()
    cdict = (pr.case_dir / "system" / "controlDict").read_text()
    assert "forces_prop" in cdict and "patches         (prop_walls);" in cdict and "writeCompression off;" in cdict
    mz = json.loads((pr.case_dir / "simwb_case.json").read_text())["moving_zones"][0]
    assert mz["walls_patch"] == "prop_walls" and 0.045 < mz["blade_radius"] <= 0.0606
    mrf_case = tmp_path / "mrf_case"
    import shutil

    shutil.copytree(pr.case_dir, mrf_case)
    pr.set_moving_zones([dict(zone, method="sliding")])
    assert any("transient" in c["message"] for c in pr.state()["checks"] if c["level"] == "error")
    pr.set_settings(analysis="transient", end_time=0.01)
    _wait(pr.generate_mesh())
    _wait(pr.solve(export_only=True))
    c = pr.case_dir
    assert "type            cyclicAMI;" in (c / "system" / "createBafflesDict").read_text()
    dm = (c / "constant" / "dynamicMeshDict").read_text()
    assert "solidBody" in dm and "rotatingMotion" in dm and "cellZone        prop;" in dm
    assert "prop_side_faces" in (c / "constant" / "polyMesh" / "faceZones").read_text()
    assert '"prop_AMI.*"' in (c / "0" / "U").read_text() and "movingWallVelocity" in (c / "0" / "U").read_text()
    meta = json.loads((c / "simwb_case.json").read_text())
    assert [s["app"] for s in meta["steps"]].index("createBaffles") < [s["app"] for s in meta["steps"]].index("pimpleFoam")
    assert "writeCompression on;" in (c / "system" / "controlDict").read_text()  # ~1 GB per saved time otherwise

    # initial flow from the steady run: a solved time in the MRF case (U = the cell centre, so pairing is visible)
    from simwb.solvers.openfoam import foamio, initfields

    pm = foamio.read_polymesh(mrf_case / "constant" / "polyMesh")
    cc = initfields.cell_centres(pm)
    (mrf_case / "200").mkdir()
    shutil.copy(mrf_case / "0" / "U", mrf_case / "200" / "U")
    initfields._replace_internal(mrf_case / "200" / "U", cc)
    out = initfields.map_initial(c, mrf_case)
    assert out["fields"] == ["U"] and out["time"] == "200"
    pm2 = foamio.read_polymesh(c / "constant" / "polyMesh")
    U = foamio.read_field(c / "0" / "U", pm2["n_cells"], pm2["patches"])["internal"]
    d = np.linalg.norm(U - initfields.cell_centres(pm2), axis=1)
    assert np.median(d) < 0.01 and "movingWallVelocity" in (c / "0" / "U").read_text()  # boundaries kept

    # the same through solve(init_from=...) checks the source study
    with pytest.raises(ValueError):
        pr.solve(init_from=pr.id)
    with pytest.raises(ValueError):
        pr.solve(init_from="no-such-study")

    # duplicate: setup and mesh copied (solvable at once), no case or results
    dup = P.duplicate_project(pr.id, "MZ fast")
    assert dup.data["name"] == "MZ fast" and dup.data["mesh"]["status"] == "ready"
    assert dup.data["solve"]["status"] == "none" and not (dup.dir / "case").exists()
    assert dup.data["moving_zones"] == pr.data["moving_zones"]


def test_compressed_fields_and_propeller_summary(tmp_path):
    """Fields written with writeCompression on (.gz) are read; propeller thrust, torque and coefficients from the
    zone force monitor, averaged over the last revolution."""
    import gzip

    from simwb.solvers.openfoam import foamio, results_loader

    txt = "FoamFile { class volScalarField; }\ninternalField nonuniform List<scalar> 3 ( 1 2 3 );\nboundaryField\n{\n}\n"
    with gzip.open(tmp_path / "p.gz", "wt") as f:
        f.write(txt)
    assert foamio.exists(tmp_path / "p") and not foamio.exists(tmp_path / "q")
    assert foamio.read_field(tmp_path / "p", 3, [])["internal"].tolist() == [1, 2, 3]

    omega = 2 * math.pi * 400  # 24000 rpm
    d = tmp_path / "postProcessing" / "forces_prop" / "0"
    d.mkdir(parents=True)
    t = np.linspace(0, 2 * 2 * math.pi / omega, 201)
    thrust = 20 + np.sin(3 * omega * t)  # three blades: 3 ripples per turn, mean 20 N along -x
    with open(d / "force.dat", "w") as f:
        f.write("# Time total_x total_y total_z pressure_x pressure_y pressure_z viscous_x viscous_y viscous_z\n")
        for ti, T in zip(t, thrust):
            f.write(f"{ti} {-T} 0 0 {-T} 0 0 0 0 0\n")
    with open(d / "moment.dat", "w") as f:
        f.write("# Time total_x total_y total_z\n")
        for ti in t:
            f.write(f"{ti} 0.5 0 0 0.5 0 0 0 0 0\n")
    z = {"name": "prop", "axis": [-1, 0, 0], "omega": omega, "rpm": 24000, "blade_radius": 0.0889}
    out = results_loader.propeller_summary(tmp_path, z, 1.204, 10.0, [1.0, 0, 0], {"end_time": t[-1]})
    assert abs(out["thrust_N"] - 20) < 0.05 and out["thrust_direction"] == [-1.0, 0.0, 0.0]
    assert abs(out["torque_Nm"] - 0.5) < 1e-9 and abs(out["power_W"] - 0.5 * omega) < 1e-6
    n, D = 400.0, 0.1778
    assert abs(out["CT"] - 20 / (1.204 * n ** 2 * D ** 4)) < 1e-3 and abs(out["J"] - 10 / (n * D)) < 1e-9
    assert "last revolution" in out["average_over"] and 3 < out["thrust_fluctuation_percent"] < 4


def test_fan_zone_and_moving_part_cases(workspace, tmp_path):
    """Cooling fan zone (MRF in the air region via topoSet) and an oscillating part with a deforming mesh (aero)."""
    import gmsh

    step = tmp_path / "plate.step"
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.model.occ.addBox(0, 0, 0, 100, 2, 100)
    gmsh.model.occ.synchronize()
    gmsh.write(str(step))
    gmsh.finalize()
    P = workspace
    pr = P.create_project("F", "cht_enclosure")
    pr.data["domain"]["enclosure_mode"] = "auto_box"
    pr.save()
    _wait(pr.import_step(step))
    solid = next(b for b in pr.bodies() if b["role"] == "solid")
    pr.set_body(solid["id"], material="aluminium_6061", heat_W=5.0)
    pr.suggest_inlet_outlet()
    fan = {"name": "fan", "center": [0.05, 0.03, 0.05], "axis": "+y", "radius": 0.03, "length": 0.01, "rpm": 2000,
           "method": "mrf"}
    pr.set_moving_zones([fan])
    pr.set_mesh(preset="quick")
    _wait(pr.generate_mesh())
    _wait(pr.solve(export_only=True))
    c = pr.case_dir
    assert "cylinderToCell" in (c / "system" / "air" / "topoSetDict").read_text()
    assert "cellZone        fan;" in (c / "constant" / "air" / "MRFProperties").read_text()
    assert "topoSet" in [s.get("app") for s in json.loads((c / "simwb_case.json").read_text())["steps"]]
    with pytest.raises(ValueError):
        pr.set_moving_zones([dict(fan, method="sliding")])

    pa = P.create_project("D", "external_aero")
    _wait(pa.import_step(step))
    body = [f["id"] for f in pa.faces() if f["kind"] != "farfield"]
    pa.add_bc("Flap", "moving_wall", body, {"motion": "rotate", "axis": "+z", "amplitude": 5, "frequency": 4})
    pa.set_settings(speed=5, mass_kg=0, analysis="transient", end_time=0.05)
    pa.set_mesh(preset="quick")
    _wait(pa.generate_mesh())
    _wait(pa.solve(export_only=True))
    c = pa.case_dir
    dm = (c / "constant" / "dynamicMeshDict").read_text()
    assert "displacementLaplacian" in dm and "inverseDistance (flap)" in dm
    pd = (c / "0" / "pointDisplacement").read_text()
    assert "angularOscillatingDisplacement" in pd and "amplitude       0.087266463;" in pd
    assert "movingWallVelocity" in (c / "0" / "U").read_text() and "cellDisplacement" in (c / "system" / "fvSolution").read_text()


def test_rotate_geometry_keeps_setup(workspace):
    """Orientation: the parts turn about the middle of the parts (they stay where they are); boundary conditions
    and moving zones turn with them."""
    P = workspace
    pr = P.create_project("Rot", "external_aero")
    _wait(pr.import_step(ROOT / "examples" / "quadcopter.step"))
    body = [f for f in pr.faces() if f.get("kind") == "body"]
    f0 = max(body, key=lambda f: f["area"])
    pr.add_bc("Top", "slip_wall", [f0["id"]])
    pr.set_moving_zones([{"name": "z1", "center": [0.01, 0.02, 0.0], "axis": "+z", "radius": 0.01,
                          "length": 0.005, "rpm": 1000, "method": "mrf"}])
    cen = lambda: np.asarray([b["centroid"] for b in pr.data["geometry"]["info"]["source_bodies"]])  # noqa: E731
    k0, c = cen(), np.asarray(pr._parts_center())
    D = np.array([[0.0, -1, 0], [1, 0, 0], [0, 0, 1]])  # +90 about z, as the live preview sends it
    turn = lambda X: (np.atleast_2d(X) - c) @ D.T + c  # about the middle of the parts  # noqa: E731
    _wait(pr.rotate_geometry(matrix=D.tolist()))
    k1 = cen()
    assert np.allclose(k1, turn(k0), atol=2e-4)
    assert np.allclose(np.asarray(pr._parts_center()), c, atol=2e-4)  # the parts did not move away
    assert pr.data["geometry"]["translation"] is not None
    bc = next(b for b in pr.data["bcs"] if b["name"] == "Top")
    f1 = pr.face_map()[bc["faces"][0]]
    assert len(bc["faces"]) == 1 and np.allclose(f1["centroid"], turn(f0["centroid"])[0], atol=2e-4)
    z = pr.data["moving_zones"][0]
    assert np.allclose(z["center"], turn([0.01, 0.02, 0.0])[0], atol=1e-9) and z["axis"] == "+z"
    # a second turn adds up (rotation and shift are cumulative); reset gives the file's own orientation and place
    _wait(pr.rotate_geometry(matrix=D.tolist()))
    assert np.allclose(cen(), (turn(k0) - c) @ D.T + c, atol=2e-4)
    _wait(pr.rotate_geometry(reset=True))
    assert pr.data["geometry"].get("rotation") is None and pr.data["geometry"].get("translation") is None
    assert np.allclose(cen(), k0, atol=2e-4)


def test_mesh_regions_zone_size_and_wall_layers(workspace):
    """Refinement box and zone cell size make the mesh finer there; wall layers are added by snappyHexMesh."""
    P = workspace
    pr = P.create_project("Reg", "external_aero")
    _wait(pr.import_step(ROOT / "examples" / "quadcopter.step"))
    pr.set_settings(speed=8, mass_kg=1.5)
    pr.set_mesh(preset="quick")
    _wait(pr.generate_mesh())
    nodes0 = np.load(pr.mesh_dir / "mesh.npz")["nodes"]
    lo, hi = [-0.06, -0.06, -0.03], [0.0, 0.0, 0.03]
    inbox = lambda X: np.all((X >= lo) & (X <= hi), axis=1).sum()  # noqa: E731
    pr.set_mesh(regions=[{"name": "box", "min": lo, "max": hi, "size": 0.003}],
                layers={"n": 3, "growth": 1.2, "first_mm": None})
    pr.set_moving_zones([{"name": "z1", "center": [0.0, 0.0, 0.06], "axis": "+z", "radius": 0.02,
                          "length": 0.01, "rpm": 1000, "method": "mrf", "cell_size": 0.002}])
    _wait(pr.generate_mesh())
    X = np.load(pr.mesh_dir / "mesh.npz")["nodes"]
    assert inbox(X) > 3 * inbox(nodes0)
    near = lambda Y: ((np.hypot(Y[:, 0], Y[:, 1]) < 0.02) & (np.abs(Y[:, 2] - 0.06) < 0.005)).sum()  # noqa: E731
    assert near(X) > 2 * near(nodes0)
    _wait(pr.solve(export_only=True))
    shm = (pr.case_dir / "system" / "snappyHexMeshDict").read_text()
    assert "addLayers       true;" in shm and "nSurfaceLayers 3;" in shm
    meta = json.loads((pr.case_dir / "simwb_case.json").read_text())
    assert [s["app"] for s in meta["steps"]][:2] == ["checkMesh", "snappyHexMesh"]


def test_display_mesh_prisms_and_isosurface():
    """Prism layers (non-tetrahedral cells) are split into tetrahedra round their centre with the right volume;
    an isosurface of a linear field is the exact plane."""
    from simwb.solvers.openfoam.results_loader import display_mesh, display_points, _assemble
    from simwb.results import ResultSet
    pts = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 2], [1, 0, 2], [0, 1, 2]], float)
    polys = [np.array(f) for f in ([0, 2, 1], [3, 4, 5], [0, 1, 4, 3], [1, 2, 5, 4], [2, 0, 3, 5])]
    pm = {"points": pts, "faces": None, "poly_faces": polys, "owner": np.zeros(5, np.int64),
          "neighbour": np.zeros(0, np.int64), "n_cells": 1,
          "patches": [{"name": "wall", "type": "wall", "nFaces": 5, "start": 0}]}
    dm = display_mesh(pm)
    P = display_points(pts, dm)
    t = dm["tets"]
    vol = np.einsum("ij,ij->i", np.cross(P[t[:, 1]] - P[t[:, 0]], P[t[:, 2]] - P[t[:, 0]]), P[t[:, 3]] - P[t[:, 0]]) / 6
    assert len(t) == 8 and (vol > 0).all() and abs(vol.sum() - 1.0) < 1e-12  # prism: 0.5 x 2
    out = _assemble([("air", pm, dm, {"p": np.array([5.0])}, {"p": {"wall": np.arange(5.0)}}, "")])
    assert len(out[5]) == 8 and np.allclose(out[9]["p"], [0, 1, 2, 2, 3, 3, 4, 4])  # fan triangles keep face values
    # isosurface z = 1 of the field z (node values = z): every vertex on the plane
    rs = ResultSet(P, t, np.zeros(len(t), int), ["air"], {"z": ("node", P[:, 2].copy(), "m")},
                   np.zeros((0, 3), int), np.zeros(0, int), np.zeros(0, int), [])
    iso = rs.iso("z", 1.0)
    import base64
    pos = np.frombuffer(base64.b64decode(iso["positions"]), np.float32).reshape(-1, 3)
    assert iso["n_tris"] > 0 and np.allclose(pos[:, 2], 1.0, atol=1e-6)
    # an indexed mesh: corners on a shared tet edge are one vertex, the triangles cover the cut (area 0.5)
    idx = np.frombuffer(base64.b64decode(iso["indices"]), np.uint32).reshape(-1, 3)
    assert len(pos) < 3 * len(idx) and idx.max() == len(pos) - 1
    tri = pos[idx]
    assert abs(np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1).sum() / 2 - 0.5) < 1e-6
    # pieces within the wall gap of the wall group are left out; smoothing keeps a linear field's plane
    rs.surf_tris, rs.surf_group, rs.groups = np.array([[0, 1, 2]]), np.array([0]), ["wall"]  # the z = 0 face
    assert rs.iso("z", 1.0, wall_groups=["wall"], wall_gap=0.5)["n_tris"] == iso["n_tris"]
    assert rs.iso("z", 1.0, wall_groups=["wall"], wall_gap=5.0)["n_tris"] == 0
    rs.fields["zc"] = ("cell", P[t, 2].mean(1), "m")
    assert rs.iso("zc", 1.0, smooth=2)["n_tris"] > 0


def test_merge_small_faces_into_host():
    """Defeaturing (virtual topology): an embossed letter on a plate joins the plate's top face only - the plate's
    real edges stay - and the merged model meshes."""
    import gmsh
    from simwb.gmsh_worker import merge_small_faces
    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        occ = gmsh.model.occ
        plate = occ.addBox(0, 0, 0, 0.1, 0.1, 0.01)
        bump = occ.addBox(0.05, 0.05, 0.01, 0.001, 0.001, 0.0002)  # 1 x 1 x 0.2 mm "letter"
        occ.fuse([(3, plate)], [(3, bump)])
        occ.synchronize()
        faces = [{"id": t, "area": occ.getMass(2, t), "kind": "body"} for _, t in gmsh.model.getEntities(2)]
        top = max((f for f in faces if abs(gmsh.model.getBoundingBox(2, f["id"])[2] - 0.01) < 1e-5
                   and abs(gmsh.model.getBoundingBox(2, f["id"])[5] - 0.01) < 1e-5), key=lambda f: f["area"])
        groups = merge_small_faces(gmsh, faces, max_area=(1.2e-3) ** 2)
        assert len(groups) == 1 and groups[0][0] == top["id"] and len(groups[0]) == 1 + 5  # 4 sides + top of bump
        gmsh.option.setNumber("Mesh.MeshSizeMax", 0.01)
        gmsh.model.mesh.generate(2)
        gmsh.option.setNumber("Mesh.Algorithm3D", 1)
        gmsh.model.mesh.generate(3)
        assert sum(len(t) for t in gmsh.model.mesh.getElements(3)[1]) > 0
    finally:
        gmsh.finalize()


def test_surface_intersections_found():
    """The crossing-surface diagnosis finds a triangle poking through another (where the volume mesher stops with
    'a segment and a facet intersect') and ignores triangles that only share a corner or an edge."""
    from simwb.gmsh_worker import surface_intersections
    P = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0.5, 0.5, -0.5], [0.5, 0.5, 0.5], [0.6, 0.2, 0.5],
                  [1, 0, 1]], float)
    hits, n = surface_intersections(P, np.array([[0, 1, 2], [0, 2, 3], [4, 5, 6], [1, 2, 7]]))
    assert n == 1 and {hits[0][0], hits[0][1]} == {0, 2} and np.allclose(hits[0][2], [0.55, 0.35, 0])
    assert surface_intersections(P, np.array([[0, 1, 2], [0, 2, 3], [1, 2, 7]]))[1] == 0


def test_cylinder_region_and_sizing_kept_on_reimport(workspace):
    """A cylindrical refinement region (body of influence) makes the mesh finer inside it; part sizing follows the
    part to a new version of the geometry, face sizing follows the matching faces."""
    P = workspace
    pr = P.create_project("Cyl", "external_aero")
    _wait(pr.import_step(ROOT / "examples" / "quadcopter.step"))
    pr.set_settings(speed=8, mass_kg=1.5)
    pr.set_mesh(preset="quick")
    _wait(pr.generate_mesh())
    nodes0 = np.load(pr.mesh_dir / "mesh.npz")["nodes"]
    a, b, R = np.array([0.0, 0.0, 0.02]), np.array([0.0, 0.0, 0.08]), 0.03

    def inside(X):
        t = (X - a) @ (b - a) / np.dot(b - a, b - a)
        r = np.linalg.norm(X - a - np.outer(t, b - a), axis=1)
        return ((t >= 0) & (t <= 1) & (r <= R)).sum()

    with pytest.raises(ValueError):
        pr.set_mesh(regions=[{"name": "c", "shape": "cylinder", "start": [0, 0, 0], "end": [0, 0, 0],
                              "radius": R, "size": 0.003}])
    pr.set_mesh(regions=[{"name": "c", "shape": "cylinder", "start": a.tolist(), "end": b.tolist(), "radius": R,
                          "size": 0.003}])
    reg = pr.data["mesh"]["regions"][0]
    assert np.allclose(reg["min"], [-R, -R, 0.02]) and np.allclose(reg["max"], [R, R, 0.08])
    _wait(pr.generate_mesh())
    X = np.load(pr.mesh_dir / "mesh.npz")["nodes"]
    assert inside(X) > 3 * inside(nodes0)

    body = [f for f in pr.faces() if f["kind"] == "body"]
    part = body[0]["part"]
    pf = [f["id"] for f in body if f["part"] == part]
    other = next(f for f in body if f["part"] != part) if any(f["part"] != part for f in body) else body[-1]
    pr.set_mesh(refinements=[{"faces": pf, "size_mm": 1.0, "part": part},
                             {"faces": [other["id"]], "size_mm": 2.0}])
    _wait(pr.import_step(ROOT / "examples" / "quadcopter.step"))
    ref = pr.data["mesh"]["refinements"]
    fm = pr.face_map()
    assert len(ref) == 2
    assert sorted(ref[0]["faces"]) == sorted(f["id"] for f in pr.faces() if f.get("part") == part and f["kind"] == "body")
    assert len(ref[1]["faces"]) == 1 and np.allclose(fm[ref[1]["faces"][0]]["centroid"], other["centroid"], atol=1e-4)


def test_parallel_reconstruct_split():
    """A transient parallel run joins its saved times with several processes (reconstructPar is serial)."""
    from simwb.solvers.openfoam.runner import parallel_steps
    st = [{"app": "pimpleFoam", "log": "log.pimpleFoam"}]
    rec = [s for s in parallel_steps(st, "pimpleFoam", 8, False, all_times=True) if s.get("app") == "reconstructPar"][0]
    assert rec["split_times"] == 8
    rec = [s for s in parallel_steps(st, "pimpleFoam", 8, False) if s.get("app") == "reconstructPar"][0]
    assert "split_times" not in rec  # steady: only the latest time



def test_reconstruct_rounds_free_disk(tmp_path):
    """Saved times are joined in rounds; each joined time leaves the processor folders at once."""
    import os
    import sys

    from simwb.solvers.openfoam.runner import run_split_times

    fake = tmp_path / "fake_reconstruct.py"  # stands in for reconstructPar: writes the joined time folder
    fake.write_text("import sys, pathlib\na = sys.argv\nt, c = a[a.index('-time') + 1], a[a.index('-case') + 1]\n"
                    "assert ',' not in t\n(pathlib.Path(c) / t).mkdir()\nprint('joined', t)\n")
    times = ["0.001", "0.002", "0.003", "0.004", "0.005"]
    for i in range(3):
        for t in times + ["0"]:  # (the start state "0" is not joined: reconstructPar -time 0 stops the whole step)
            (tmp_path / f"processor{i}" / t).mkdir(parents=True)
    lines = []
    rc = run_split_times(tmp_path, {"split_times": 8, "log": "log.reconstructPar", "args": [str(fake)]},
                         Path(sys.executable), dict(os.environ), on_line=lines.append)
    assert rc == 0
    assert all((tmp_path / t).is_dir() for t in times)
    assert not any((tmp_path / f"processor{i}" / t).exists() for i in range(3) for t in times)
    assert lines[-1] == "joined 5 of 5 times" and "joined 0.005" in (tmp_path / "log.reconstructPar").read_text()
    assert not (tmp_path / "0").exists() and all((tmp_path / f"processor{i}" / "0").is_dir() for i in range(3))

def test_reconstruct_uses_as_many_processes_as_the_memory_allows(tmp_path, monkeypatch):
    """The join runs several processes while the free memory covers them and one at a time when it is short."""
    import os
    import sys
    import time

    from simwb import memlimit
    from simwb.solvers.openfoam import runner

    fake = tmp_path / "fake_reconstruct.py"  # holds its place for 1.5 s, notes when it ran
    fake.write_text("import sys, pathlib, time\na = sys.argv\nt, c = a[a.index('-time') + 1], a[a.index('-case') + 1]\n"
                    "t0 = time.time()\ntime.sleep(1.5)\n(pathlib.Path(c) / t).mkdir()\n"
                    "(pathlib.Path(c) / (t + '.span')).write_text(f'{t0} {time.time()}')\n")
    times = [f"0.00{i}" for i in range(1, 7)]
    monkeypatch.setattr(runner, "JOIN_RAMP_S", 0.2)

    def run(avail_gb):
        for f in tmp_path.glob("0.00*"):
            f.unlink() if f.is_file() else f.rmdir()
        for i in range(2):
            for t in times:
                (tmp_path / f"processor{i}" / t).mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(memlimit, "memory", lambda: (32 * 2 ** 30, int(avail_gb * 2 ** 30)))
        rc = runner.run_split_times(tmp_path, {"split_times": 8, "log": "log.reconstructPar", "args": [str(fake)]},
                                    Path(sys.executable), dict(os.environ))
        assert rc == 0 and all((tmp_path / t).is_dir() for t in times)
        spans = [tuple(map(float, (tmp_path / f"{t}.span").read_text().split())) for t in times]
        return max(sum(a <= s0 < b for a, b in spans) for s0, _ in spans)  # most at once

    if (os.cpu_count() or 1) > 1:
        assert run(30) >= 2  # plenty of memory: several at once
    assert run(5) == 1  # 5 GB free, 6 GB kept for the rest: one at a time


def test_saved_times_follow_the_rotation():
    """Sliding runs save a time every 'Saved frame every' degrees of turn (a whole number of steps), ending on a
    saved time: the video is smooth and its blades, vortices and fields stay in step."""
    import math

    from simwb.solvers.openfoam.cases import frame_interval

    w = 8500 * math.pi / 30
    dt = math.radians(2) / w

    def plan(deg, fixed=True):
        tr = {"end_time": 0.02431, "fixed_dt": fixed, "dt": dt, "write_interval": 0.0006, "n_frames": 41}
        return frame_interval(tr, {"frame_deg": deg}, w)

    tr = plan(8)
    assert abs(math.degrees(w * tr["write_interval"]) - 8) < 0.5
    assert abs(tr["write_interval"] / dt - round(tr["write_interval"] / dt)) < 1e-9  # whole steps
    assert abs(tr["end_time"] / tr["write_interval"] - round(tr["end_time"] / tr["write_interval"])) < 1e-9  # ends on a frame
    assert tr["n_frames"] == round(tr["end_time"] / tr["write_interval"]) + 1 and abs(tr["end_time"] - 0.02431) < tr["write_interval"]
    assert plan(0)["n_frames"] == 41 and plan(0)["write_interval"] == 0.0006  # 0: the number of saved times as set
    assert plan(8, fixed=False)["n_frames"] == 41  # (only a fixed time step makes every saved time a solved step)
    assert abs(math.degrees(w * plan(1)["write_interval"]) - 2) < 1e-6  # never finer than one step


def test_frame_plan_estimates_the_disk(tmp_path):
    """The setup check counts the saved times of a sliding run and the disk they take."""
    from types import SimpleNamespace

    from simwb.project import Project

    fake = SimpleNamespace(data={"settings": {"analysis": "transient", "end_time": 0.02431, "frame_deg": 8, "time_step": 0.0},
                                 "moving_zones": [{"method": "sliding", "rpm": 8500}], "mesh": {"stats": {"cells": 3628677}}})
    fp = Project.frame_plan(fake)
    assert 150 < fp["frames"] < 160 and abs(fp["deg"] - 8) < 0.5
    assert 80 < fp["gb"] < 100  # 170 bytes per cell and saved time
    fake.data["settings"]["frame_deg"] = 0
    fake.data["settings"]["n_frames"] = 41
    assert Project.frame_plan(fake)["frames"] == 41
    fake.data["moving_zones"] = [{"method": "mrf", "rpm": 8500}]
    assert Project.frame_plan(fake) is None  # nothing slides: the setting does not apply


def _cube_results(n=6):
    """A unit cube of tets with the velocity (1, 0, 0) on every node."""
    from simwb.results import ResultSet
    g = np.linspace(0, 1, n)
    nodes = np.array([[x, y, z] for x in g for y in g for z in g])
    idx = lambda i, j, k: (i * n + j) * n + k
    tets = []
    for i in range(n - 1):
        for j in range(n - 1):
            for k in range(n - 1):
                v = [idx(i + a, j + b, k + c) for a in (0, 1) for b in (0, 1) for c in (0, 1)]
                for a, b, c, d in ((0, 1, 3, 7), (0, 1, 5, 7), (0, 2, 3, 7), (0, 2, 6, 7), (0, 4, 5, 7), (0, 4, 6, 7)):
                    tets.append([v[a], v[b], v[c], v[d]])
    tets = np.array(tets)
    U = np.tile([1.0, 0.0, 0.0], (len(tets), 1))
    fields = {"Velocity": ("cell", U, "m/s"), "Pressure": ("cell", nodes[tets].mean(1)[:, 0], "Pa")}
    e = np.zeros((0, 3), np.int64)
    return ResultSet(nodes, tets, np.zeros(len(tets), np.int32), ["air"], fields, e, np.zeros(0, np.int64),
                     np.zeros(0, np.int32), [])


def test_resampled_views():
    """Volume, arrows and particle paths of a uniform flow through a unit cube."""
    from simwb import resample
    rs = _cube_results()
    box = [0.1, 0.1, 0.1, 0.9, 0.9, 0.9]
    v = resample.volume_payload(rs, "Pressure", box, 16)
    data = np.frombuffer(base64.b64decode(v["data"]), np.uint8).reshape(v["dims"][::-1])
    assert (data > 0).all() and data[0, 0, 0] < data[0, 0, -1]  # pressure (= x) grows along x, the fastest axis
    a = resample.vector_payload(rs, 2, 0.5, box, 6)
    vec = np.frombuffer(base64.b64decode(a["vectors"]), np.float32).reshape(-1, 3)
    assert a["n"] > 20 and np.allclose(vec, [1, 0, 0], atol=1e-5)
    p = resample.particle_paths(rs, box, 24, 50, 30, "upstream", [1, 0, 0])
    P = np.frombuffer(base64.b64decode(p["paths"]), np.float32).reshape(50, 30, 3)
    first = P[:, 0, 0]
    assert np.allclose(first, 0.1 + 0.5 * p["h"], atol=1e-4)  # seeded at the upstream face
    moved = np.nanmax(P[:, :, 0], axis=1) - first
    assert (moved > 0).all() and np.allclose(np.nanmax(P[:, :, 1], 1) - np.nanmin(P[:, :, 1], 1), 0, atol=1e-5)


def test_plane_vorticity_and_seed_disk():
    """A plane section on a grid, the vorticity of a solid-body rotation (= 2 omega) and a disk of seeds."""
    from simwb import resample
    rs = _cube_results()
    box = [0.1, 0.1, 0.1, 0.9, 0.9, 0.9]
    p = resample.plane_payload(rs, "Pressure", 2, 0.5, box, 12)
    pos = np.frombuffer(base64.b64decode(p["positions"]), np.float32).reshape(-1, 3)
    val = np.frombuffer(base64.b64decode(p["values"]), np.float32)
    # the exact cut of the mesh (not a grid), where it meets the box; pressure = x (inside the box: the cells at the
    # wall of the cube average to the nodes there, and a few triangles reach past the box edge)
    inb = (pos[:, 0] >= 0.1) & (pos[:, 0] <= 0.9)
    assert p["n_tris"] > 100 and np.allclose(pos[:, 2], 0.5) and inb.sum() > 50 and np.allclose(val[inb], pos[inb, 0], atol=0.1)   # (cells of 0.1)
    assert pos[:, 0].min() > -0.001 and (pos[:, 1] >= -0.001).all()   # (nothing far from the box)
    g = np.linspace(-1, 1, 9)
    Z, Y, X = np.meshgrid(g, g, g, indexing="ij")
    U = np.stack([-3.0 * Y, 3.0 * X, 0 * X], -1)       # rotating at omega = 3: |curl| = 6
    w = resample.vorticity_grid(U, g[1] - g[0])
    assert np.allclose(w[1:-1, 1:-1, 1:-1], 6.0, atol=1e-9)
    d = resample.particle_paths(rs, box, 24, 60, 10, "box", [1, 0, 0], seed_box=[0.12, 0.3, 0.3, 0.16, 0.7, 0.7])
    P = np.frombuffer(base64.b64decode(d["paths"]), np.float32).reshape(d["n"], 10, 3)
    r = np.hypot(P[:, 0, 1] - 0.5, P[:, 0, 2] - 0.5)
    assert d["n"] > 20 and (r <= 0.2 + 1e-6).all()      # within the inscribed circle of the box


def test_saved_transient_time_for_resume(tmp_path):
    """An interrupted split run restarts from the newest time every processor wrote completely."""
    from simwb.solvers.openfoam import runner

    for i in range(2):
        for tm, files in (("0", 2), ("0.1", 4), ("0.2", 4), ("0.3", 4 if i == 0 else 1)):
            d = tmp_path / f"processor{i}" / tm
            d.mkdir(parents=True)
            for k in range(files):
                (d / f"f{k}.gz").write_text("x")
    (tmp_path / "processor0" / "0.4").mkdir()  # only one processor got there
    assert runner.processor_count(tmp_path) == 2
    assert runner.saved_transient_time(tmp_path) == 0.2
    assert runner.saved_transient_time(tmp_path, prune=True) == 0.2
    assert not (tmp_path / "processor0" / "0.3").exists() and not (tmp_path / "processor0" / "0.4").exists()
    assert (tmp_path / "processor1" / "0.2").exists()
    serial = tmp_path / "serial"
    for tm in ("0", "0.5"):
        (serial / tm).mkdir(parents=True)
    assert runner.processor_count(serial) == 0 and runner.saved_transient_time(serial) == 0.5


def _decompose_log(cells):
    return "".join(f"Processor {i}\n    Number of cells = {c}\n    Number of processor patches = 3\n"
                   for i, c in enumerate(cells))


def test_split_is_rebalanced_with_weights(tmp_path, monkeypatch):
    """A sliding interface kept on one process gives it more cells: the workbench splits again with weights that
    compensate, keeps the better split, and leaves a resumed run alone."""
    from simwb.solvers.openfoam import runner

    cells = [458084, 342477, 339514, 339909, 339453, 339000, 339000, 339000]
    w = runner.new_weights(cells)
    assert w[0] < 0.8 and all(0.99 < x < 1.1 for x in w[1:]) and abs(sum(w) - len(w)) < 1e-2
    assert runner.new_weights([100, 100, 100, 100]) == pytest.approx([1, 1, 1, 1])  # balanced: unchanged
    # steps: decomposePar, balance, solver, reconstruct (a resumed run drops the first two in Project)
    steps = runner.parallel_steps([{"app": "pimpleFoam", "args": [], "log": "log.pimpleFoam"}], "pimpleFoam", 8, False, True)
    kinds = [s.get("action") or s["app"] for s in steps]
    assert kinds[:3] == ["decomposePar", "balance_split", "pimpleFoam"]

    (tmp_path / "system").mkdir()
    dpd = tmp_path / "system" / "decomposeParDict"
    dpd.write_text("numberOfSubdomains 8;\nmethod          scotch;\nconstraints\n{\n}\n")
    (tmp_path / "log.decomposePar").write_text(_decompose_log(cells))
    runs = []

    def fake(case, redo, info, env, on_proc=None):
        runs.append(dpd.read_text())
        m = [float(x) for x in runner.re.search(r"processorWeights \(([^)]*)\)", dpd.read_text()).group(1).split()]
        total = float(sum(cells))
        better = [int(total * x / sum(m)) for x in m]  # scotch honours the weights; the move is ignored here
        (tmp_path / "log.decomposePar").write_text(_decompose_log(better))
        return 0

    monkeypatch.setattr(runner, "_decompose", fake)
    lines = []
    out = runner.balance_split(tmp_path, {"log": "log.decomposePar", "args": []}, {"bin_dir": "x"}, {}, None, lines.append)
    assert len(runs) == 1 and "scotchCoeffs" in dpd.read_text() and "constraints" in dpd.read_text()
    assert max(out) / (sum(out) / len(out)) <= runner.BALANCE_TOLERANCE
    assert any("splitting again" in ln for ln in lines)

    # a balanced split is left alone; weights can be removed again
    (tmp_path / "log.decomposePar").write_text(_decompose_log([100, 101, 99, 100]))
    runs.clear()
    runner.balance_split(tmp_path, {"log": "log.decomposePar", "args": []}, {"bin_dir": "x"}, {})
    assert not runs
    runner._set_weights(dpd, None)
    assert "scotchCoeffs" not in dpd.read_text() and "method          scotch;" in dpd.read_text()


def test_balance_keeps_best_split_when_weights_do_not_help(tmp_path, monkeypatch):
    from simwb.solvers.openfoam import runner

    (tmp_path / "system").mkdir()
    dpd = tmp_path / "system" / "decomposeParDict"
    dpd.write_text("numberOfSubdomains 4;\nmethod          scotch;\n")
    first = [400, 200, 200, 200]
    (tmp_path / "log.decomposePar").write_text(_decompose_log(first))

    def worse(case, redo, info, env, on_proc=None):
        (tmp_path / "log.decomposePar").write_text(_decompose_log([500, 150, 150, 200]))
        return 0

    monkeypatch.setattr(runner, "_decompose", worse)
    assert runner.balance_split(tmp_path, {"log": "log.decomposePar", "args": []}, {"bin_dir": "x"}, {}) == first
    assert "scotchCoeffs" not in dpd.read_text()  # back to the first split's dictionary


def _two_blades_step(path, y=0.07):
    """Two flat blades (100 x 12 x 2 mm, turned 20 degrees about their length) side by side along y."""
    import gmsh

    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    for sy in (-y, y):
        b = gmsh.model.occ.addBox(-50, sy * 1000 - 6, -1, 100, 12, 2)
        gmsh.model.occ.rotate([(3, b)], 0, sy * 1000, 0, 1, 0, 0, math.radians(20))
    gmsh.model.occ.synchronize()
    gmsh.write(str(path))
    gmsh.finalize()


def test_two_sliding_zones_case(workspace, tmp_path):
    """Two propellers: two sliding zones are allowed (overlapping ones are not); the case has one createBaffles with
    both interfaces, one multiSolidBodyMotion dynamicMeshDict and each interface on its own process."""
    step = tmp_path / "two.step"
    _two_blades_step(step)
    pr = workspace.create_project("Two", "external_aero")
    _wait(pr.import_step(step))
    pr.set_settings(speed=2, mass_kg=0, analysis="transient", end_time=0.01, n_procs=2)
    za = {"name": "pa", "center": [0, -0.07, 0], "axis": "+z", "radius": 0.06, "length": 0.02, "rpm": 3000,
          "method": "sliding"}
    zb = dict(za, name="pb", center=[0, 0.07, 0], rpm=-3000)
    with pytest.raises(ValueError, match="overlap"):
        pr.set_moving_zones([za, dict(zb, center=[0.03, -0.07, 0.01])])
    pr.set_moving_zones([za, zb])
    assert len(pr.data["moving_zones"]) == 2
    pr.set_mesh(preset="quick")
    _wait(pr.generate_mesh())
    _wait(pr.solve(export_only=True))
    c = pr.case_dir
    cb = (c / "system" / "createBafflesDict").read_text()
    assert "pa_AMI_side1" in cb and "pb_AMI_side1" in cb
    dm = (c / "constant" / "dynamicMeshDict").read_text()
    assert "multiSolidBodyMotionSolver" in dm and dm.count("rotatingMotionCoeffs") == 2
    assert "omega           314.15927;" in dm and "omega           -314.15927;" in dm
    dp = (c / "system" / "decomposeParDict").read_text()
    assert "(pa_AMIfaces 0)" in dp and "(pb_AMIfaces 1)" in dp
    zn = (c / "constant" / "polyMesh" / "cellZones").read_text()
    assert "pa" in zn and "pb" in zn


def test_zones_touch():
    from simwb.project import Project

    a = {"center": [0, 0, 0], "axis": "+z", "radius": 0.05, "length": 0.02}
    assert not Project._zones_touch(a, dict(a, center=[0.12, 0, 0]))
    assert Project._zones_touch(a, dict(a, center=[0.09, 0, 0]))
    assert not Project._zones_touch(a, dict(a, center=[0.0, 0, 0.03]))        # stacked, apart axially
    assert Project._zones_touch(a, dict(a, center=[0.0, 0, 0.015]))
    assert not Project._zones_touch(a, dict(a, axis="+x", center=[0.2, 0, 0]))  # different axes: boxes apart


def test_interface_weights_several_heavy_processes():
    from simwb.solvers.openfoam import runner

    w = runner.interface_weights([500, 500, 330, 330, 330, 330], 1.8)
    assert w[0] < 1 and w[1] < 1 and w[2:] == [1.0] * 4
    w = runner.interface_weights([450, 340, 340, 340], 1.8)
    assert w[0] < 1 and w[1:] == [1.0] * 3


def test_interfaces_split_over_processes_are_detected_and_pinned_together(tmp_path):
    """Two sliding zones close together share air cells, so some faces of one interface land on the other's
    process (the case that crashed MS-MPI with 'Message truncated'): detected from the processor boundaries, and
    every interface can be put on process 0."""
    from simwb.solvers.openfoam import runner

    def bnd(counts):
        return "".join(f"\n    {n}\n    {{\n        type            cyclicAMI;\n        nFaces          {k};\n    }}"
                       for n, k in counts.items())

    for i, counts in enumerate([{"z1_AMI_side1": 300, "z1_AMI_side2": 290, "z2_AMI_side1": 0},
                                {"z1_AMI_side1": 0, "z1_AMI_side2": 18, "z2_AMI_side1": 309}]):
        d = tmp_path / f"processor{i}" / "constant" / "polyMesh"
        d.mkdir(parents=True)
        (d / "boundary").write_text("FoamFile\n{\n}\n2\n(" + bnd(counts) + "\n)\n")
    assert runner.split_interfaces(tmp_path) == ["z1_AMI_side2"]
    (tmp_path / "processor1" / "constant" / "polyMesh" / "boundary").write_text(
        "2\n(" + bnd({"z1_AMI_side1": 0, "z1_AMI_side2": 0, "z2_AMI_side1": 309}) + "\n)\n")
    assert runner.split_interfaces(tmp_path) == []

    dpd = tmp_path / "decomposeParDict"
    dpd.write_text("sets ((z1_AMIfaces 0) (z2_AMIfaces 1));\n")
    assert runner.pin_interfaces_together(dpd) and "(z2_AMIfaces 0)" in dpd.read_text()
    assert not runner.pin_interfaces_together(dpd)  # already together


def test_failure_hint_names_the_likely_cause(tmp_path):
    from simwb.solvers.openfoam import runner

    assert "diverged" in runner.failure_hint(tmp_path, "sigFpe : Floating point exception")
    assert "memory" in runner.failure_hint(tmp_path, "terminate called after std::bad_alloc")
    assert "parallel" in runner.failure_hint(tmp_path, "Fatal error in MPI_Bcast: Message truncated")
    assert runner.failure_hint(tmp_path, "something else") == ""


def test_divergence_is_recognised_in_the_solver_log():
    from simwb.solvers.openfoam import runner

    assert runner.divergence("bounding k, min: -9.0e+09 max: 1.36e+22 average: 1.07e+17") is not None
    assert runner.divergence("bounding omega, min: 1 max: 4.0e+05 average: 100") is None
    sound = "time step continuity errors : sum local = 1.2e-06, global = 3e-08, cumulative = 1e-07\n" * 5
    assert runner.divergence(sound) is None
    bad = "time step continuity errors : sum local = 6.4e+11, global = 9e+09, cumulative = 1e+10\n" * 3
    assert "continuity" in runner.divergence(bad)
