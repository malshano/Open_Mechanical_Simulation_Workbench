"""Flutter study: a published benchmark (Goland wing) and the whole pipeline (STEP -> mesh -> modes -> flutter)."""
import json
import math
from pathlib import Path

import numpy as np
import pytest

from simwb.solvers import flutter as F

ROOT = Path(__file__).resolve().parents[1]
_trapz = getattr(np, "trapezoid", None) or np.trapz


def goland_wing(nb, nt, ns=60, rho=1.225):
    """Goland wing (Goland 1945): cantilever, semi-span 6.096 m, chord 1.829 m, m = 35.72 kg/m, EI = 9.77e6 N m2,
    GJ = 0.987e6 N m2, I_alpha = 8.64 kg m (about the elastic axis at 33 % chord), centre of gravity at 43 % chord.
    Rayleigh-Ritz with the exact cantilever bending modes and the sine torsion modes; 2-D Theodorsen strips.
    Published flutter (2-D strip theory): about 137 m/s (450 ft/s) at 70-71 rad/s (11.2 Hz)."""
    L, c = 6.096, 1.829
    b = c / 2
    m, EI, GJ, Ia = 35.72, 9.77e6, 0.987e6, 8.64
    S = m * 0.1 * c
    a = (0.33 - 0.5) * c / b
    yq = np.linspace(0, L, 4001)
    bend = []
    for bl in (1.8751041, 4.6940911, 7.8547574, 10.9955407)[:nb]:
        sg = (math.cosh(bl) + math.cos(bl)) / (math.sinh(bl) + math.sin(bl))
        be = bl / L
        bend.append(lambda y, be=be, sg=sg: np.cosh(be * y) - np.cos(be * y) - sg * (np.sinh(be * y) - np.sin(be * y)))
    tors = [lambda y, k=k: np.sin((2 * k - 1) * math.pi * y / (2 * L)) for k in range(1, nt + 1)]
    n = nb + nt
    d1 = lambda f: np.gradient(f(yq), yq)  # noqa: E731
    d2 = lambda f: np.gradient(np.gradient(f(yq), yq), yq)  # noqa: E731
    M, K = np.zeros((n, n)), np.zeros((n, n))
    for i, f in enumerate(bend):
        for j, g in enumerate(bend):
            M[i, j] = m * _trapz(f(yq) * g(yq), yq)
            K[i, j] = EI * _trapz(d2(f) * d2(g), yq)
    for i, f in enumerate(tors):
        for j, g in enumerate(tors):
            M[nb + i, nb + j] = Ia * _trapz(f(yq) * g(yq), yq)
            K[nb + i, nb + j] = GJ * _trapz(d1(f) * d1(g), yq)
    for i, f in enumerate(bend):
        for j, g in enumerate(tors):
            M[i, nb + j] = M[nb + j, i] = -S * _trapz(f(yq) * g(yq), yq)  # h up, alpha nose-up, cg aft: -S
    edges = np.linspace(0, L, ns + 1)
    ym = 0.5 * (edges[1:] + edges[:-1])
    ph, pa = np.zeros((ns, n)), np.zeros((ns, n))
    for i, f in enumerate(bend):
        ph[:, i] = f(ym)
    for j, g in enumerate(tors):
        pa[:, nb + j] = g(ym)
    st = {"b": np.full(ns, b), "a": np.full(ns, a), "w": np.full(ns, L / ns), "ph": ph, "pa": pa, "fac": 1.0}
    return F.solve(M, K, st, rho, np.linspace(20, 200, 91))["events"]


@pytest.mark.parametrize("nb,nt", [(1, 1), (2, 2), (3, 3)])
def test_goland_wing_flutter_matches_the_published_value(nb, nt):
    ev = goland_wing(nb, nt)["flutter"]
    assert ev is not None
    assert 134.0 < ev["V"] < 140.0  # published 2-D strip theory: ~137 m/s (445-450 ft/s)
    assert 68.5 < ev["omega"] < 72.5  # published ~70-71 rad/s


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


def test_flutter_pipeline_on_the_plate_example(workspace):
    P = workspace
    pr = P.create_project("Plate wing", "aeroelastic_flutter")
    assert pr.spec["category"] == "multiphysics"
    _wait(pr.import_step(ROOT / "examples" / "flutter_plate.step"))
    faces = pr.faces()
    root = [f["id"] for f in faces if f["normal"][1] < -0.9]
    skin = [f["id"] for f in faces if abs(f["normal"][2]) > 0.9]
    assert len(root) == 1 and len(skin) == 2
    msgs = [c["message"] for c in pr.check()]
    assert any("fixed support" in m.lower() for m in msgs)  # no root yet
    pr.add_bc("Root", "fixed_support", root)
    assert any("No aerodynamic surface" in c["message"] for c in pr.check())
    pr.add_bc("Skin", "aero_surface", skin)
    assert not any(c["level"] == "error" and c["step"] == "setup" for c in pr.check())  # (only the mesh is missing)
    with pytest.raises(ValueError):
        pr.set_settings(span_axis="w")
    pr.set_settings(design_speed_ms=100.0, n_modes=6)
    pr.set_mesh(preset="coarse")
    _wait(pr.generate_mesh())
    rep = json.loads(json.dumps(_wait(pr.solve())))  # plain JSON
    fl, dv = rep["flutter"], rep["divergence"]
    assert fl and 40 < fl["speed_m_s"] < 160 and 10 < fl["frequency_Hz"] < 80
    assert {m["mode"] for m in fl["mode_content"][:2]} >= {1}  # bending mixes with a higher (torsion) mode
    assert rep["critical"]["type"] in ("flutter", "divergence")
    assert rep["margin"]["ratio"] == pytest.approx(rep["critical"]["speed_m_s"] / 100.0)
    assert rep["strips"]["aspect_ratio"] == pytest.approx(2 * 300 / 80)  # mirrored at the root
    assert len(rep["sweep"]["roots"]) == 6 and len(rep["sweep"]["speed_m_s"]) == 100
    assert rep["air"]["density_kg_m3"] == pytest.approx(1.225, rel=2e-3)
    assert set(pr.results().fields) >= {"Mode 1", "Flutter mode"}
    assert pr.results().surface("Flutter mode")["n_tris"] > 0
    # a denser stream flutters earlier; a stiffer (thicker) model later: here the density
    v0 = fl["speed_m_s"]
    pr.set_settings(altitude_m=-500)
    assert _wait(pr.solve())["flutter"]["speed_m_s"] < v0
    # damping and a smaller sweep range are reported, not hidden
    pr.set_settings(altitude_m=0, speed_max_ms=20.0)
    rep = _wait(pr.solve())
    assert rep["critical"] is None and any("No flutter or divergence up to 20" in w for w in rep["warnings"])


def test_flutter_needs_a_root_and_a_flow_across_the_span():
    from simwb.solvers import flutter_study

    mesh = {"nodes": np.zeros((4, 3)), "tets": np.zeros((1, 4), int), "tet_vol": np.zeros(1, int),
            "tris": np.zeros((1, 3), int), "tri_surf": np.zeros(1, int)}
    with pytest.raises(ValueError, match="fixed support"):
        flutter_study.solve_study(mesh, {}, [], {})
    with pytest.raises(ValueError, match="along the span"):
        flutter_study.solve_study(mesh, {}, [], {"flow_direction": "+y", "span_axis": "y"})


def test_airplane_wing_example_flutters_with_bending_and_torsion(workspace):
    """The bundled tapered, swept, cambered wing: starts in PLA with a design speed, flutters at a model-aircraft speed
    through the mixing of the first bending mode with a higher one, and its margin is stated."""
    P = workspace
    pr = P.create_project("Wing", "aeroelastic_flutter")
    _wait(pr.import_step(P.EXAMPLES / "airplane_wing.step"))
    assert pr.data["bodies"]["1"]["material"] == "pla" and pr.data["settings"]["design_speed_ms"] == 30.0
    faces = pr.faces()
    root = [f["id"] for f in faces if f["normal"][1] < -0.9]
    skin = [f["id"] for f in faces if f["id"] not in root and f["normal"][1] < 0.9]
    assert len(root) == 1 and len(skin) == 2
    pr.add_bc("Root", "fixed_support", root)
    pr.add_bc("Skin", "aero_surface", skin)
    pr.set_mesh(preset="coarse")
    _wait(pr.generate_mesh())
    rep = _wait(pr.solve())
    fl = rep["flutter"]
    assert fl and 40 < fl["speed_m_s"] < 130
    f1, f2 = rep["modes"][0]["frequency_Hz"], rep["modes"][1]["frequency_Hz"]
    assert f1 < fl["frequency_Hz"] < 1.3 * f2  # the flutter frequency lies between the bending and the torsion mode
    assert fl["mode_content"][0]["mode"] == 1 and any(c["mode"] >= 2 for c in fl["mode_content"][:2])
    assert rep["margin"]["ratio"] > 1.15 and rep["strips"]["aspect_ratio"] > 8  # whole-wing aspect ratio
