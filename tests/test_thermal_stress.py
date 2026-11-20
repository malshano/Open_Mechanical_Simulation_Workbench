"""Thermal stress (heat -> structure, one-way) against closed-form solutions: free expansion, full restraint, the
Timoshenko bimetal strip, bending by a through-thickness gradient, symmetry planes, transient frames and the CalculiX
deck (ccx itself is replaced by a writer of its output files; the cross-check against the real program runs only
when it is installed)."""
import pathlib

import numpy as np
import pytest

from simwb.solvers import static_fem, thermal_stress
from simwb.solvers.calculix import api, inputdeck, installer

from test_calculix import frd_text, quiet

E, NU, ALPHA, K = 70e9, 0.33, 23e-6, 150.0


def box(nx, ny, nz, Lx, Ly, Lz, x0=0.0, y0=0.0, z0=0.0, body=None):
    """Structured box of 6 conforming tetrahedra per cell. Faces: 1/2 = x min/max, 3/4 = y min/max, 5/6 = z min/max.
    body(centroid) -> body id (default 1)."""
    idx = lambda i, j, k: i + (nx + 1) * (j + (ny + 1) * k)  # noqa: E731
    nodes = np.array([[x0 + i * Lx / nx, y0 + j * Ly / ny, z0 + k * Lz / nz]
                      for k in range(nz + 1) for j in range(ny + 1) for i in range(nx + 1)])
    tets = []
    for k in range(nz):
        for j in range(ny):
            for i in range(nx):
                for path in ((0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0)):
                    cur, v = [0, 0, 0], [(0, 0, 0)]
                    for ax in path:
                        cur[ax] += 1
                        v.append(tuple(cur))
                    tets.append([idx(i + a, j + b, k + c) for a, b, c in v])
    tets = np.array(tets)
    lo, hi = nodes.min(0), nodes.max(0)
    tris, surf = [], []
    for t in tets:
        for f in ([1, 2, 3], [0, 3, 2], [0, 1, 3], [0, 2, 1]):
            X = nodes[t[f]]
            for ax in range(3):
                for side, val in ((0, lo[ax]), (1, hi[ax])):
                    if np.allclose(X[:, ax], val):
                        tris.append(t[f])
                        surf.append(1 + 2 * ax + side)
    tv = np.ones(len(tets), int) if body is None else np.array([body(nodes[t].mean(0)) for t in tets])
    return {"nodes": nodes, "tets": tets, "tet_vol": tv, "tris": np.array(tris), "tri_surf": np.array(surf)}


def bc(name, kind, faces, **params):
    return {"name": name, "type": kind, "faces": list(faces), "params": params}


def mat(**kw):
    d = {"E": E, "nu": NU, "rho": 2700.0, "cp": 900.0, "k": K, "alpha": ALPHA, "yield": 275e6, "name": "m"}
    d.update(kw)
    return d


def rigid_free(faces=(1, 3, 5)):
    """Frictionless supports on three mutually perpendicular faces: stop rigid-body motion, restrain nothing else."""
    return [bc(f"f{f}", "frictionless_support", [f]) for f in faces]


# ---- static_fem with a thermal load -----------------------------------------------------------------------------

def test_free_bar_uniform_heating_expands_without_stress():
    m = box(6, 2, 2, 0.3, 0.05, 0.05)
    dT = 100.0
    r = static_fem.solve(m, {1: mat()}, rigid_free(), log=quiet,
                         thermal={"T_C": np.full(len(m["nodes"]), 20 + dT), "T_ref_C": 20.0})
    assert np.abs(r["displacement"] - ALPHA * dT * m["nodes"]).max() < 0.01 * ALPHA * dT * 0.3
    assert r["report"]["max_von_mises_MPa"] < 0.01 * E * ALPHA * dT / 1e6
    assert r["report"]["force_balance_error"] < 1e-6
    assert r["report"]["thermal"]["max_abs_dT_K"] == pytest.approx(dT)


def test_bar_restrained_along_its_length_has_stress_E_alpha_dT():
    m = box(6, 2, 2, 0.3, 0.05, 0.05)
    dT = 80.0
    bcs = [bc("ends", "frictionless_support", [1, 2]), bc("y", "frictionless_support", [3]),
           bc("z", "frictionless_support", [5])]
    r = static_fem.solve(m, {1: mat()}, bcs, log=quiet,
                         thermal={"T_C": np.full(len(m["nodes"]), 20 + dT), "T_ref_C": 20.0})
    exact = E * ALPHA * dT / 1e6
    assert r["report"]["max_von_mises_MPa"] == pytest.approx(exact, rel=0.01)
    assert r["report"]["min_principal_MPa"] == pytest.approx(-exact, rel=0.01)  # compression
    bx = r["report"]["boundary_conditions"]["ends"]["reaction_N"][0]
    assert bx == pytest.approx(0.0, abs=1e-3 * exact * 1e6 * 0.05 * 0.05)  # the two ends push with equal and opposite force
    assert r["report"]["force_balance_error"] < 1e-6


def test_stress_free_at_the_reference_and_superposition_with_a_mechanical_load():
    m = box(6, 2, 2, 0.3, 0.05, 0.05)
    bcs = [bc("wall", "fixed_support", [1]), bc("pull", "force", [2], fx_N=5000.0)]
    plain = static_fem.solve(m, {1: mat()}, bcs, log=quiet)
    Tref = np.full(len(m["nodes"]), 35.0)
    same = static_fem.solve(m, {1: mat()}, bcs, log=quiet, thermal={"T_C": Tref, "T_ref_C": 35.0})
    assert same["displacement"] == pytest.approx(plain["displacement"], rel=1e-12, abs=1e-15)
    assert same["von_mises"] == pytest.approx(plain["von_mises"], rel=1e-9)
    hot = np.full(len(m["nodes"]), 85.0)
    th = static_fem.solve(m, {1: mat()}, [bcs[0]], log=quiet, thermal={"T_C": hot, "T_ref_C": 35.0})
    both = static_fem.solve(m, {1: mat()}, bcs, log=quiet, thermal={"T_C": hot, "T_ref_C": 35.0})
    assert both["displacement"] == pytest.approx(plain["displacement"] + th["displacement"], rel=1e-9, abs=1e-12)
    # the wall holds the bar against its expansion: the reaction is the mechanical one only along x? (fixed face is
    # laterally restrained too, so just check the force balance is honest)
    assert both["report"]["force_balance_error"] < 1e-6


def test_missing_expansion_coefficient_is_an_error():
    m = box(2, 1, 1, 0.1, 0.02, 0.02)
    p = mat()
    del p["alpha"]
    with pytest.raises(ValueError, match="thermal expansion"):
        static_fem.solve(m, {1: p}, rigid_free(), log=quiet,
                         thermal={"T_C": np.full(len(m["nodes"]), 50.0), "T_ref_C": 20.0})


# ---- Timoshenko bimetal strip ------------------------------------------------------------------------------------

def test_bimetal_strip_curvature_matches_timoshenko():
    """Two bonded layers, different alpha and E, uniform temperature change: curvature (Timoshenko 1925)
    k = 6 (a2 - a1) dT (1+m)^2 / (h (3 (1+m)^2 + (1+mn)(m^2 + 1/(mn)))), m = t1/t2, n = E1/E2. A half strip with a
    mirror plane at midspan; the plane is held in the length direction and against vertical translation."""
    t1 = t2 = 0.001
    h, Lh, w = t1 + t2, 0.04, 0.002  # half length, strip width (half modelled: 0.001)
    a1, a2, E1, E2 = 12e-6, 23e-6, 200e9, 70e9
    m = box(16, 1, 4, Lh, w / 2, h, body=lambda c: 1 if c[2] < t1 else 2)
    props = {1: mat(E=E1, alpha=a1, name="steel"), 2: mat(E=E2, alpha=a2, name="aluminium")}
    dT = 60.0
    bcs = [bc("mid", "displacement", [1], ux_mm=0.0, uz_mm=0.0), bc("sym", "symmetry", [3])]
    r = thermal_stress.solve(m, props, bcs, {"T_uniform": 20 + dT, "T_ref": 20.0}, log=quiet)
    mm, n = t1 / t2, E1 / E2
    kappa = 6 * (a2 - a1) * dT * (1 + mm) ** 2 / (h * (3 * (1 + mm) ** 2 + (1 + mm * n) * (mm ** 2 + 1 / (mm * n))))
    p = m["nodes"]
    tip = (np.abs(p[:, 0] - Lh) < 1e-12) & (np.abs(p[:, 2] - t1) < 1e-12) & (np.abs(p[:, 1]) < 1e-12)  # interface, free end
    w_tip = r["displacement"][tip, 2].mean()
    # the layer with the larger alpha (aluminium, on top) is longer: the strip curves down (concave towards steel)
    assert w_tip == pytest.approx(-kappa * Lh ** 2 / 2, rel=0.03)


# ---- through-thickness gradient: free plate bends with curvature alpha dT / h and carries no stress -----------------

def test_temperature_gradient_bends_a_free_plate_per_thin_plate_theory():
    """T from the heat solver: z = 0 face at 20 C, z = h face at 120 C (linear in z). A free plate: kappa = alpha *
    gradient, zero stress apart from the supports' local effect."""
    h, Lh, w = 0.004, 0.06, 0.004  # (a narrow strip: the clamped-section constraint then hardly touches the y bending)
    m = box(24, 2, 4, Lh, w / 2, h)
    bcs = [bc("cold", "fixed_temperature", [5], T=20.0), bc("hot", "fixed_temperature", [6], T=120.0),
           bc("mid", "displacement", [1], ux_mm=0.0, uz_mm=0.0), bc("sym", "symmetry", [3])]
    r = thermal_stress.solve(m, {1: mat()}, bcs, {"T_ref": 70.0}, log=quiet)  # (mean temperature = reference)
    z = m["nodes"][:, 2]
    assert np.abs(r["temperature"] - (20 + 100 * z / h)).max() < 1e-6
    kappa = ALPHA * 100.0 / h
    p = m["nodes"]
    tip = (np.abs(p[:, 0] - Lh) < 1e-12) & (np.abs(p[:, 2] - h / 2) < 1e-12) & (np.abs(p[:, 1]) < 1e-12)
    # hot side (top) longer: the strip is convex upward, the ends droop below the midspan plane
    assert r["displacement"][tip, 2].mean() == pytest.approx(-kappa * Lh ** 2 / 2, rel=0.03)
    rep = r["report"]
    far = p[:, 0] > 3 * h  # (the clamped section at x = 0 holds the thickness expansion: a local peak there)
    assert r["von_mises"][far].max() < 0.03 * E * ALPHA * 100  # (vs the 160 MPa a fully restrained plate has)
    assert rep["thermal"]["T_min"] == pytest.approx(20.0) and rep["thermal"]["T_max"] == pytest.approx(120.0)
    assert rep["thermal"]["T_ref"] == 70.0 and rep["thermal"]["max_dT"] == pytest.approx(50.0)


def test_gradient_plate_clamped_flat_has_the_constrained_bending_stress():
    """The same gradient with the plate held flat (both mid-plane supports and a mirror at each end): the thermal
    bending moment is resisted, sigma_x = -E alpha dT_gradient (z - h/2)/(1 - nu) ... checked as E alpha dT/2 /
    (1 - nu) (plate, all lateral strain restrained) on the surfaces within 5 %."""
    h, Lh, w = 0.004, 0.02, 0.02
    m = box(8, 4, 4, Lh, w / 2, h)
    bcs = [bc("cold", "fixed_temperature", [5], T=20.0), bc("hot", "fixed_temperature", [6], T=120.0),
           bc("xm", "symmetry", [1]), bc("xp", "symmetry", [2]), bc("ym", "symmetry", [3]), bc("yp", "symmetry", [4]),
           bc("zm", "displacement", [5], uz_mm=0.0)]
    # a plate held flat in both directions everywhere (all four edges are mirror planes) is a 1-D problem in z:
    # sigma_x = sigma_y = -E alpha (T - T_ref) / (1 - nu) with the linear-in-z part not restrained in z
    r = thermal_stress.solve(m, {1: mat()}, bcs, {"T_ref": 70.0}, log=quiet)
    exact = E * ALPHA * 50.0 / (1 - NU) / 1e6  # at the faces: dT = +-50
    s1 = r["report"]["max_principal_MPa"]
    assert s1 == pytest.approx(exact, rel=0.05)


# ---- symmetry: a mirror plane is adiabatic for the heat and a sliding wall for the structure ---------------------

def test_symmetry_plane_gives_the_full_model_answer():
    a, hh = 0.04, 0.02
    hot, ends = 100.0, 100.0

    def run(mesh, bcs):
        return thermal_stress.solve(mesh, {1: mat()}, bcs, {"T_ref": 20.0}, log=quiet)

    side = lambda faces: bc("air", "convection", faces, h=60.0, T_inf=20.0)  # noqa: E731
    full = box(16, 2, 2, 2 * a, hh, hh, x0=-a)
    rf = run(full, [bc("endsT", "fixed_temperature", [1, 2], T=ends), side([3, 4, 5, 6]),
                    bc("wall", "fixed_support", [1, 2])])
    half = box(8, 2, 2, a, hh, hh)
    rh = run(half, [bc("endT", "fixed_temperature", [2], T=ends), side([3, 4, 5, 6]), bc("mirror", "symmetry", [1]),
                    bc("wall", "fixed_support", [2])])
    assert rh["report"]["thermal"]["T_min"] == pytest.approx(rf["report"]["thermal"]["T_min"], rel=2e-3, abs=0.05)
    assert rh["report"]["max_von_mises_MPa"] == pytest.approx(rf["report"]["max_von_mises_MPa"], rel=0.03)
    assert rh["report"]["max_displacement_mm"] == pytest.approx(rf["report"]["max_displacement_mm"], rel=0.03)
    assert rf["report"]["thermal"]["T_min"] < ends - 1.0  # (the air really cools the middle)
    assert hot == ends


# ---- transient ---------------------------------------------------------------------------------------------------

def test_transient_run_returns_frames_consistent_with_single_solves():
    m = box(8, 2, 2, 0.08, 0.02, 0.02)
    bcs = [bc("hot", "fixed_temperature", [2], T=150.0), bc("air", "convection", [3, 4, 5, 6], h=20.0, T_inf=20.0),
           bc("wall", "fixed_support", [1])]
    s = {"analysis": "transient", "end_time": 40.0, "time_step": 1.0, "T_initial": 20.0, "n_frames": 5}
    r = thermal_stress.solve(m, {1: mat(k=20.0, rho=7800.0, cp=500.0)}, bcs, s, log=quiet)
    fr = r["frames"]
    assert len(fr["times"]) == len(fr["temperature"]) == len(fr["displacement"]) == len(fr["von_mises"]) == 5
    assert fr["times"][0] == 0.0 and fr["times"][-1] == pytest.approx(40.0)
    assert fr["temperature"][0][m["nodes"][:, 0] < 0.04].max() == pytest.approx(20.0)  # (starts at T_initial)
    assert fr["temperature"][-1].mean() > fr["temperature"][1].mean() > 20.0  # (and heats up)
    assert np.allclose(r["displacement"], fr["displacement"][-1]) and np.allclose(r["von_mises"], fr["von_mises"][-1])
    # frame 2 solved on its own with the same matrix gives the same answer as the back-substitution
    k = 2
    single = static_fem.solve(m, {1: mat(k=20.0, rho=7800.0)}, [bcs[2]], log=quiet,
                              thermal={"T_C": fr["temperature"][k], "T_ref_C": 20.0})
    assert fr["displacement"][k] == pytest.approx(single["displacement"], rel=1e-9, abs=1e-14)
    assert fr["von_mises"][k] == pytest.approx(single["von_mises"], rel=1e-9)
    th = r["report"]["thermal"]
    assert th["analysis"] == "transient" and th["peak_von_mises_over_time_MPa"] >= r["report"]["max_von_mises_MPa"] - 1e-9
    assert r["fields"]["Temperature"][0] == pytest.approx(fr["temperature"][-1])


# ---- validation --------------------------------------------------------------------------------------------------

def test_validation_messages():
    m = box(3, 1, 1, 0.1, 0.02, 0.02)
    heat = [bc("hot", "fixed_temperature", [2], T=80.0)]
    with pytest.raises(ValueError, match="not supported"):  # no support
        thermal_stress.solve(m, {1: mat()}, heat, {"T_ref": 20.0}, log=quiet)
    with pytest.raises(ValueError, match="temperature level"):  # a heat load and nowhere for the heat to go
        thermal_stress.solve(m, {1: mat()}, rigid_free() + [bc("q", "heat_load", [2], Q=1.0)], {"T_ref": 20.0},
                             log=quiet)
    with pytest.raises(ValueError, match="not supported in a thermal stress"):
        thermal_stress.solve(m, {1: mat()}, rigid_free() + heat + [bc("x", "added_mass", [1])], {}, log=quiet)
    with pytest.raises(ValueError, match="No temperature"):
        thermal_stress.solve(m, {1: mat()}, rigid_free(), {}, log=quiet)
    r = thermal_stress.solve(m, {1: mat()}, rigid_free() + heat + [bc("air", "convection", [1], h=10.0, T_inf=20.0)],
                             {}, log=quiet)  # no T_ref given: the default is said
    assert any("reference temperature not set" in w for w in r["report"]["warnings"])
    r = thermal_stress.solve(m, {1: mat()}, rigid_free(), {"T_uniform": 20.0, "T_ref": 20.0}, log=quiet)
    assert any("no thermal load" in w for w in r["report"]["warnings"])


def test_split_bcs_sends_symmetry_to_both():
    th, st = thermal_stress.split_bcs([bc("a", "convection", [1], h=1.0, T_inf=1.0), bc("b", "fixed_support", [2]),
                                       bc("c", "symmetry", [3]), bc("d", "free", [4])])
    assert [b["name"] for b in th] == ["a", "c"] and [b["name"] for b in st] == ["b", "c"]


# ---- CalculiX deck and reader (ccx replaced) ---------------------------------------------------------------------

def test_calculix_deck_has_expansion_reference_and_nodal_temperatures():
    m = box(2, 1, 1, 0.1, 0.02, 0.02)
    T = 20.0 + 100.0 * m["nodes"][:, 0] / 0.1  # linear in x
    th = {"T_C": T, "T_ref_C": 20.0, "alpha": {1: ALPHA}}
    d = inputdeck.build(m, {1: mat()}, rigid_free(), "static", log=quiet, thermal=th)
    txt = d.text()
    assert "*EXPANSION, ZERO=20\n2.300000000e-05" in txt
    assert "*INITIAL CONDITIONS, TYPE=TEMPERATURE\nNALL, 20" in txt
    assert txt.index("*INITIAL CONDITIONS") < txt.index("*STEP") < txt.index("*TEMPERATURE\n")
    assert "*STATIC\n*TEMPERATURE\n1, 20" in txt
    rows = txt.split("*TEMPERATURE\n")[1].split("*NODE FILE")[0].strip().splitlines()
    assert len(rows) == d.n_nodes  # every node, mid-edge ones included
    nodes = np.array([[float(x) for x in ln.split(",")[1:]] for ln in txt.split("*NODE, NSET=NALL\n")[1].split("*ELEMENT")[0]
                      .strip().splitlines()])
    tv = {int(ln.split(",")[0]): float(ln.split(",")[1]) for ln in rows}
    for i in range(d.n_nodes):  # (linear field: the temperature of every node follows its coordinate, mids included)
        assert tv[i + 1] == pytest.approx(20.0 + 100.0 * nodes[i, 0] / 0.1, abs=1e-6)
    assert "*TEMPERATURE" not in inputdeck.build(m, {1: mat()}, rigid_free(), "static", log=quiet).text()
    with pytest.raises(ValueError, match="only written for static"):
        inputdeck.build(m, {1: mat()}, rigid_free()[:1], "modal", log=quiet, thermal=th)
    with pytest.raises(ValueError, match="thermal expansion"):
        inputdeck.build(m, {1: mat(alpha=None)}, rigid_free(), "static", log=quiet, thermal=dict(th, alpha={}))


@pytest.fixture
def fake_ccx(monkeypatch):
    def make(sol):
        def run(workdir, deck_text, log=print, on_proc=None, threads=None):
            wd = pathlib.Path(workdir)
            wd.mkdir(parents=True, exist_ok=True)
            (wd / "job.inp").write_text(deck_text)
            d, vm = sol["displacement"], sol["von_mises"]
            (wd / "job.frd").write_text(frd_text([
                ("DISP", ["D1", "D2", "D3"], d), ("STRESS", list("123456"), np.column_stack([vm, np.zeros((len(vm), 5))]))]))
            r = np.asarray(sol["report"]["reaction_force_N"])
            (wd / "job.dat").write_text(" total force (fx,fy,fz) for set SUPPORTS and time  0.1\n\n  "
                                        + "  ".join(f"{v:.6E}" for v in r) + "\n")
            return "ok"
        monkeypatch.setattr(api, "run_ccx", run)
    return make


def test_thermal_stress_through_the_calculix_engine(fake_ccx, tmp_path):
    m = box(4, 1, 1, 0.1, 0.02, 0.02)
    bcs = [bc("hot", "fixed_temperature", [2], T=100.0), bc("air", "convection", [1, 3, 4, 5, 6], h=30.0, T_inf=20.0)]
    sup = [bc("x", "frictionless_support", [1]), bc("y", "frictionless_support", [3]), bc("z", "frictionless_support", [5])]
    ref = thermal_stress.solve(m, {1: mat()}, bcs + sup, {"T_ref": 20.0}, log=quiet)
    fake_ccx(ref)
    r = thermal_stress.solve(m, {1: mat()}, bcs + sup, {"T_ref": 20.0}, log=quiet, engine="calculix", workdir=tmp_path)
    assert r["displacement"] == pytest.approx(ref["displacement"], rel=1e-4, abs=1e-9)
    assert r["temperature"] == pytest.approx(ref["temperature"])
    assert r["report"]["solver"].startswith("CalculiX") and r["report"]["thermal"]["T_max"] == pytest.approx(100.0)
    deck = (tmp_path / "job.inp").read_text()
    assert "*EXPANSION, ZERO=20" in deck and "*TEMPERATURE" in deck and "COUPLED" not in deck
    assert r["report"]["force_balance_error"] is None or r["report"]["force_balance_error"] < 1e-3


@pytest.mark.skipif(not installer.detect()["available"], reason="CalculiX (ccx) is not installed")
def test_real_ccx_matches_builtin_thermal_stress(tmp_path):
    m = box(8, 2, 2, 0.1, 0.02, 0.02)
    bcs = [bc("hot", "fixed_temperature", [2], T=100.0), bc("air", "convection", [3, 4, 5, 6], h=30.0, T_inf=20.0),
           bc("wall", "fixed_support", [1])]
    a = thermal_stress.solve(m, {1: mat()}, bcs, {"T_ref": 20.0}, log=quiet)
    b = thermal_stress.solve(m, {1: mat()}, bcs, {"T_ref": 20.0}, log=quiet, engine="calculix", workdir=tmp_path)
    assert b["report"]["max_displacement_mm"] == pytest.approx(a["report"]["max_displacement_mm"], rel=0.02)
    assert b["report"]["max_von_mises_MPa"] == pytest.approx(a["report"]["max_von_mises_MPa"], rel=0.05)
