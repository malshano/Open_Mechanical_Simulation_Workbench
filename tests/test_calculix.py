"""CalculiX as an additional structural solver: input deck, output readers, the software table, and the solve
plumbing (ccx itself is replaced by a writer of its output files built from the built-in solver; the physics
cross-check against the real program runs only when ccx is installed)."""
import pathlib

import numpy as np
import pytest

from simwb import software
from simwb.solvers import calculix, modal_fem, static_fem
from simwb.solvers.calculix import api, inputdeck, installer, results

L = 0.2  # a 0.2 m block, six tetrahedra (Kuhn split), x = 0 face fixed, x = L face loaded


def block():
    nodes = np.array([[i * L, j * L, k * L] for k in (0, 1) for j in (0, 1) for i in (0, 1)], float)
    tets = np.array([[0, 1, 3, 7], [0, 1, 5, 7], [0, 2, 3, 7], [0, 2, 6, 7], [0, 4, 5, 7], [0, 4, 6, 7]])
    tris = np.array([[0, 2, 6], [0, 6, 4], [1, 3, 7], [1, 7, 5]])
    return {"nodes": nodes, "tets": tets, "tet_vol": np.ones(6, int), "tris": tris, "tri_surf": np.array([1, 1, 2, 2])}


PROPS = {1: {"E": 70e9, "nu": 0.33, "rho": 2700.0, "yield": 275e6, "name": "block"}}
BCS = [{"name": "wall", "type": "fixed_support", "faces": [1], "params": {}},
       {"name": "load", "type": "force", "faces": [2], "params": {"fx_N": 0.0, "fy_N": -1000.0, "fz_N": 0.0}}]


def quiet(_m):
    return None


def test_deck_has_quadratic_tets_loads_and_supports():
    d = inputdeck.build(block(), PROPS, BCS, "static", gravity=[0, 0, -9.81], log=quiet)
    txt = d.text()
    assert d.n_corner == 8 and d.n_elements == 6 and d.n_nodes > 8
    assert "*ELEMENT, TYPE=C3D10, ELSET=B1" in txt and "*SOLID SECTION, ELSET=B1, MATERIAL=M1" in txt
    assert "*ELASTIC\n7.000000000e+10, 0.330000" in txt and "*DLOAD\nB1, GRAV, 9.810000" in txt
    assert "*NODE PRINT, NSET=SUPPORTS, TOTALS=ONLY" in txt and "*EL FILE\nS" in txt
    cl = txt.split("*CLOAD\n")[1].split("*DLOAD")[0].strip().splitlines()
    fy = sum(float(x.split(",")[2]) for x in cl if x.split(",")[1].strip() == "2")
    assert fy == pytest.approx(-1000.0, rel=1e-9)  # the face load is fully distributed on the nodes
    assert len(d.support_nodes) >= 4 and d.mass == pytest.approx(2700 * L ** 3, rel=1e-9)


def test_deck_orients_tets_and_rejects_unsupported_cases():
    m = block()
    flip = m["tets"].copy()
    flip[:, [1, 2]] = flip[:, [2, 1]]  # every tet with negative volume
    d = inputdeck.build(dict(m, tets=flip), PROPS, BCS, "static", log=quiet)
    assert d.flipped > 0  # (the Kuhn block mixes orientations: the negative ones are re-ordered)
    for ln in d.text().split("*ELEMENT")[1].splitlines()[1:7]:
        ids = [int(x) - 1 for x in ln.split(",")[1:5]]
        a, b, c, e = (m["nodes"][i] for i in ids)
        assert np.dot(np.cross(b - a, c - a), e - a) > 0
    with pytest.raises(ValueError, match="not supported"):
        inputdeck.build(m, PROPS, [{"name": "x", "type": "force", "faces": [2], "params": {"fx_N": 1}}], "static",
                        log=quiet)
    am = [BCS[0], {"name": "m", "type": "added_mass", "faces": [2], "params": {"mass_kg": 1}}]
    with pytest.raises(ValueError, match="Added masses"):
        inputdeck.build(m, PROPS, am, "modal", log=quiet)
    with pytest.raises(ValueError, match="needs a fixed support"):
        inputdeck.build(m, PROPS, [], "modal", log=quiet)
    sym = [{"name": "f", "type": "frictionless_support", "faces": [1], "params": {}}]
    assert "BC1, 1, 1" in inputdeck.build(m, PROPS, sym, "static", log=quiet).text()  # flat x-normal face


def frd_text(blocks):
    out = ["    1C", "    1UUSER", " -3"]
    for name, comps, arr in blocks:
        out += ["  100CL  101 1.00000E+00          8                     0    1           1",
                f" -4  {name:<8}{len(comps):>4}    1"] + [f" -5  {c:<8}   1    2    1    0" for c in comps]
        out += [f" -1{i + 1:>10}" + "".join(f"{v:12.5E}" for v in row) for i, row in enumerate(arr)]
        out.append(" -3")
    out.append("9999")
    return "\n".join(out) + "\n"


def test_readers_parse_frd_dat(tmp_path):
    u = np.array([[1e-3, -2e-3, 0.0], [0.0, 3.5e-4, -1e-5]])
    s = np.array([[1e8, 0, 0, 0, 0, 0], [0, 0, 0, 5e7, 0, 0]])
    (tmp_path / "a.frd").write_text(frd_text([("DISP", ["D1", "D2", "D3"], u), ("STRESS", list("123456"), s)]))
    b = results.read_frd(tmp_path / "a.frd")
    assert [x["name"] for x in b] == ["DISP", "STRESS"] and b[0]["data"].shape == (3, 3)
    assert b[0]["data"][1] == pytest.approx(u[0], rel=1e-5) and b[1]["data"][2, 3] == pytest.approx(5e7, rel=1e-5)
    assert results.von_mises(s) == pytest.approx([1e8, 5e7 * np.sqrt(3)], rel=1e-9)
    lam = (2 * np.pi * 812.5) ** 2
    (tmp_path / "a.dat").write_text(
        " total force (fx,fy,fz) for set SUPPORTS and time  0.1000000E+01\n\n"
        "       0.000000E+00  1.000000E+03  0.000000E+00\n\n E I G E N V A L U E    O U T P U T\n\n"
        " MODE NO    EIGENVALUE                       FREQUENCY\n"
        f"      1   {lam:.7E}   {np.sqrt(lam):.7E}   {812.5:.7E}   0.0000000E+00\n")
    assert results.read_frequencies(tmp_path / "a.dat") == pytest.approx([812.5])
    assert results.read_total_force(tmp_path / "a.dat") == pytest.approx([0, 1000, 0])
    (tmp_path / "b.dat").write_text(" E I G E N V A L U E    O U T P U T\n      1   1.0E+06   1.0E+03   999.0   0.0\n")
    with pytest.raises(ValueError, match="disagree"):
        results.read_frequencies(tmp_path / "b.dat")


@pytest.fixture
def fake_ccx(monkeypatch):
    """ccx replaced by a function that writes job.frd / job.dat from the built-in solution of the same problem."""
    def make(kind, sol):
        def run(workdir, deck_text, log=print, on_proc=None, threads=None):
            wd = pathlib.Path(workdir)
            wd.mkdir(parents=True, exist_ok=True)
            (wd / "job.inp").write_text(deck_text)
            if kind == "static":
                d, vm = sol["displacement"], sol["von_mises"]
                (wd / "job.frd").write_text(frd_text([
                    ("DISP", ["D1", "D2", "D3"], d),
                    ("STRESS", list("123456"), np.column_stack([vm, np.zeros((len(vm), 5))]))]))
                direct = inputdeck.build(block(), PROPS, BCS, "static", gravity=[0, 0, -9.81], log=quiet).direct_load
                r = np.asarray(sol["report"]["reaction_force_N"]) + direct  # ccx's RF leaves the direct load out
                (wd / "job.dat").write_text(" total force (fx,fy,fz) for set SUPPORTS and time  0.1\n\n  "
                                            + "  ".join(f"{v:.6E}" for v in r) + "\n")
            else:
                f = [m["frequency_Hz"] for m in sol["modes"]]
                rows = [f"      {i + 1}   {(2 * np.pi * x) ** 2:.7E}   {2 * np.pi * x:.7E}   {x:.7E}   0.0\n"
                        for i, x in enumerate(f)]
                (wd / "job.dat").write_text(" E I G E N V A L U E    O U T P U T\n\n MODE NO\n" + "".join(rows))
                (wd / "job.frd").write_text(frd_text([("DISP", ["D1", "D2", "D3"], sh * 1e-3) for sh in sol["shapes"]]))
            return "ok"
        monkeypatch.setattr(api, "run_ccx", run)
    return make


def test_solve_static_reads_ccx_output(fake_ccx, tmp_path):
    ref = static_fem.solve(block(), PROPS, BCS, gravity=[0, 0, -9.81], log=quiet)
    fake_ccx("static", ref)
    r = calculix.solve_static(block(), PROPS, BCS, gravity=[0, 0, -9.81], log=quiet, workdir=tmp_path)
    assert r["displacement"] == pytest.approx(ref["displacement"], rel=1e-4, abs=1e-9)
    assert r["von_mises"] == pytest.approx(ref["von_mises"], rel=1e-4)
    rep = r["report"]
    assert rep["max_displacement_mm"] == pytest.approx(ref["report"]["max_displacement_mm"], rel=1e-4)
    assert rep["bodies"][0]["safety_factor"] == pytest.approx(ref["report"]["bodies"][0]["safety_factor"], rel=1e-4)
    assert rep["force_balance_error"] < 1e-3 and rep["solver"].startswith("CalculiX")
    assert rep["reaction_force_N"] == pytest.approx(ref["report"]["reaction_force_N"], rel=1e-4, abs=1e-6)


def test_solve_modal_reads_ccx_output(fake_ccx, tmp_path):
    bcs = [BCS[0]]
    ref = modal_fem.solve(block(), PROPS, bcs, n_modes=4, band_hz=(0.0, 1e9), log=quiet)
    fake_ccx("modal", ref)
    r = calculix.solve_modal(block(), PROPS, bcs, n_modes=4, band_hz=(0.0, 1e9), log=quiet, workdir=tmp_path)
    assert [m["frequency_Hz"] for m in r["modes"]] == pytest.approx([m["frequency_Hz"] for m in ref["modes"]], rel=1e-6)
    assert len(r["shapes"]) == len(ref["modes"]) and abs(np.linalg.norm(r["shapes"][0], axis=1).max() - 1) < 1e-9
    assert r["report"]["first_frequency_Hz"] == pytest.approx(ref["report"]["first_frequency_Hz"], rel=1e-6)
    assert "modes_in_band" in r["report"]  # the same resonance check as the built-in solver


def test_software_table_and_solver_choice():
    d = software.describe("static_structural", {})
    assert d["engine"] == "builtin" and [c["engine"] for c in d["choices"]] == ["builtin", "calculix"]
    rows = {r["element"]: r for r in d["elements"]}
    assert {"geometry", "meshing", "setup", "solver", "post", "video"} <= set(rows) and "mesh_layers" not in rows
    ccx = software.describe("static_structural", {"structural_solver": "calculix"})
    assert {r["element"]: r for r in ccx["elements"]}["solver"]["engine"] == "calculix"
    assert software.engine_for("heat_conduction", {"structural_solver": "calculix"}) == "builtin"  # not offered there
    flow = software.describe("external_aero", {})
    assert flow["engine"] == "openfoam" and any(r["element"] == "mesh_layers" for r in flow["elements"])


@pytest.mark.skipif(not installer.detect()["available"], reason="CalculiX (ccx) is not installed")
def test_real_ccx_matches_builtin_fem(tmp_path):
    """Cantilever block, same mesh and loads: the two independent solvers must agree."""
    ref = static_fem.solve(block(), PROPS, BCS, log=quiet)
    r = calculix.solve_static(block(), PROPS, BCS, log=quiet, workdir=tmp_path)
    assert r["report"]["max_displacement_mm"] == pytest.approx(ref["report"]["max_displacement_mm"], rel=0.02)
    assert r["report"]["max_von_mises_MPa"] == pytest.approx(ref["report"]["max_von_mises_MPa"], rel=0.05)
    g = [0, 0, -9.81]  # with gravity the reaction must still equal the weight (load on the support nodes included)
    rg = calculix.solve_static(block(), PROPS, [BCS[0]], gravity=g, log=quiet, workdir=tmp_path)
    assert rg["report"]["reaction_force_N"][2] == pytest.approx(2700 * L ** 3 * 9.81, rel=1e-3)
    assert rg["report"]["force_balance_error"] < 1e-3
    mref = modal_fem.solve(block(), PROPS, [BCS[0]], n_modes=3, log=quiet)
    mr = calculix.solve_modal(block(), PROPS, [BCS[0]], n_modes=3, log=quiet, workdir=tmp_path)
    assert mr["report"]["first_frequency_Hz"] == pytest.approx(mref["report"]["first_frequency_Hz"], rel=0.02)


def test_missing_ccx_gives_a_clear_message(monkeypatch, tmp_path):
    monkeypatch.setattr(installer, "detect", lambda refresh=False: {"available": False, "exe": None})
    with pytest.raises(RuntimeError, match="CalculiX is not installed"):
        calculix.solve_static(block(), PROPS, BCS, log=quiet, workdir=tmp_path)


def beam(nx=16, ny=2, length=0.4, h=0.04):
    """Cantilever bar of nx*ny*ny cells (six conforming tetrahedra each); faces: 1 = x 0 (root), 2 = x length (tip)."""
    dx, dy = length / nx, h / ny
    idx = lambda i, j, k: i + (nx + 1) * (j + (ny + 1) * k)  # noqa: E731
    nodes = np.array([[i * dx, j * dy, k * dy] for k in range(ny + 1) for j in range(ny + 1) for i in range(nx + 1)])
    tets = []
    for k in range(ny):
        for j in range(ny):
            for i in range(nx):
                for path in ((0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0)):
                    cur, v = [0, 0, 0], [(0, 0, 0)]
                    for ax in path:
                        cur[ax] += 1
                        v.append(tuple(cur))
                    tets.append([idx(i + a, j + b, k + c) for a, b, c in v])
    tets = np.array(tets)
    tris, surf = [], []
    for t in tets:
        for f in ([1, 2, 3], [0, 3, 2], [0, 1, 3], [0, 2, 1]):
            x = nodes[t[f], 0]
            if np.allclose(x, 0) or np.allclose(x, length):
                tris.append(t[f])
                surf.append(1 if np.allclose(x, 0) else 2)
    return {"nodes": nodes, "tets": tets, "tet_vol": np.ones(len(tets), int), "tris": np.array(tris),
            "tri_surf": np.array(surf)}


@pytest.mark.skipif(not installer.detect()["available"], reason="CalculiX (ccx) is not installed")
def test_cantilever_beam_both_solvers_match_beam_theory(tmp_path):
    """Tip load and first bending frequency of a cantilever against Euler-Bernoulli theory, built-in and CalculiX."""
    Lb, h, P = 0.4, 0.04, 100.0
    m = beam()
    bcs = [{"name": "w", "type": "fixed_support", "faces": [1], "params": {}},
           {"name": "l", "type": "force", "faces": [2], "params": {"fx_N": 0, "fy_N": 0, "fz_N": -P}}]
    E, rho = PROPS[1]["E"], PROPS[1]["rho"]
    I, A = h ** 4 / 12, h * h
    defl = P * Lb ** 3 / (3 * E * I) * 1e3
    f1 = (1.8751 ** 2 / (2 * np.pi)) * np.sqrt(E * I / (rho * A * Lb ** 4))
    for solve in (static_fem.solve, calculix.solve_static):
        kw = {} if solve is static_fem.solve else {"workdir": tmp_path}
        r = solve(m, PROPS, bcs, log=quiet, **kw)["report"]
        assert r["max_displacement_mm"] == pytest.approx(defl, rel=0.03)
        assert r["max_von_mises_MPa"] == pytest.approx(P * Lb * (h / 2) / I / 1e6, rel=0.1)
        assert r["reaction_force_N"][2] == pytest.approx(P, rel=1e-3)
    for solve in (modal_fem.solve, calculix.solve_modal):
        kw = {} if solve is modal_fem.solve else {"workdir": tmp_path}
        assert solve(m, PROPS, [bcs[0]], n_modes=2, log=quiet, **kw)["report"]["first_frequency_Hz"] == \
            pytest.approx(f1, rel=0.03)
