"""CFD-coupled flutter engine (simwb.solvers.openfoam.flutter_cfd): structure side, without OpenFOAM."""
import math
import threading
import time

import numpy as np
import pytest

from simwb.solvers.openfoam import flutter_cfd as FC
from simwb.solvers.openfoam import lumped as L


def test_fit_rigid_recovers_a_rigid_motion():
    rng = np.random.default_rng(1)
    X = rng.uniform(-1, 1, (40, 3))
    c = np.array([0.2, -0.1, 0.3])
    t, w = np.array([0.01, -0.02, 0.03]), np.array([0.004, -0.003, 0.002])
    D = t + np.cross(w, X - c)
    t2, w2 = FC.fit_rigid(X, D, c)
    assert np.allclose(t2, t, atol=1e-12) and np.allclose(w2, w, atol=1e-12)
    # a bending-like field (not rigid) still gives its mean translation and slope
    Xp = X * [1, 1, 0]  # (a thin plate: all points in one plane)
    t3, w3 = FC.fit_rigid(Xp, np.column_stack([0 * Xp[:, 0], 0 * Xp[:, 0], 0.5 * Xp[:, 1]]), np.zeros(3))
    assert w3[0] == pytest.approx(0.5, abs=1e-9) and abs(t3[2]) < 1e-9


def _plate_nodes():
    xs, ys = np.meshgrid(np.linspace(0, 0.2, 5), np.linspace(0, 1.0, 21))
    return np.column_stack([xs.ravel(), ys.ravel(), np.zeros(xs.size)])


def test_node_model_samples_modes_at_the_lumped_nodes():
    X = _plate_nodes()
    heave = np.zeros_like(X)
    heave[:, 2] = X[:, 1] ** 2  # bending: grows with the span
    pitch = np.zeros_like(X)
    pitch[:, 2] = -(X[:, 0] - 0.08) * X[:, 1]  # nose-up twist (about +y) growing with the span
    md = FC.build_node_model(X, np.arange(len(X)), np.array([heave, pitch]), [100.0, 300.0], [2.0, 0.5], [1, 0, 0], [0, 1, 0], n_nodes=5)
    assert md.m == 2 and md.n == 5
    yj = md.x0[:, 1]
    assert np.allclose(md.T[0, :, 2], yj ** 2 + 0.0, atol=0.02)  # heave of the bending mode at each node
    assert (md.W[1, :, 1] > 0).all() and np.allclose(md.W[1, :, 1], yj, atol=1e-6)  # pitch about +y = the twist per metre
    assert np.allclose(md.W[0, :, 0], 2 * yj, atol=1e-9)  # bending slope d(z)/dy = 2y: rotation about +x
    # virtual work: a unit force along z at one node does Q = the heave there
    F = np.zeros((5, 3))
    F[2, 2] = 1.0
    assert md.generalised_force(F, np.zeros((5, 3)))[0] == pytest.approx(md.T[0, 2, 2])


def test_propagate_matches_the_damped_oscillator():
    w, z, m, f = 2 * math.pi * 20, 0.02, 3.0, 5.0
    q, v = FC.propagate(np.array([w]), z, np.array([m]), np.array([0.001]), np.array([0.0]), np.array([f]), 0.123)
    wd = w * math.sqrt(1 - z * z)
    xp = f / (m * w * w)  # static deflection under the force
    A = 0.001 - xp
    expect = xp + math.exp(-z * w * 0.123) * (A * math.cos(wd * 0.123) + (z * w * A / wd) * math.sin(wd * 0.123))
    assert q[0] == pytest.approx(expect, rel=1e-9)
    q2, v2 = FC.propagate(np.array([w]), z, np.array([m]), q, v, np.array([f]), 0.0)
    assert q2[0] == pytest.approx(q[0]) and v2[0] == pytest.approx(v[0])


@pytest.mark.parametrize("f1,z1,f2,z2", [(20.0, 0.01, 55.0, 0.03), (12.0, -0.02, 40.0, 0.01)])
def test_fit_poles_finds_frequency_and_damping(f1, z1, f2, z2):
    t = np.linspace(0, 0.8, 800)
    y = np.column_stack([
        np.exp(-z1 * 2 * math.pi * f1 * t) * np.cos(2 * math.pi * f1 * math.sqrt(1 - z1 ** 2) * t),
        0.4 * np.exp(-z2 * 2 * math.pi * f2 * t) * np.sin(2 * math.pi * f2 * math.sqrt(1 - z2 ** 2) * t + 0.3)])
    y += 1e-4 * np.random.default_rng(0).standard_normal(y.shape)
    s = FC.fit_poles(t, y, order=4)
    fz = sorted(zip(s.imag / (2 * math.pi), FC.damping_ratio(s)))
    assert len(fz) == 2
    assert fz[0][0] == pytest.approx(f1, rel=0.01) and fz[0][1] == pytest.approx(z1, abs=0.002)
    assert fz[1][0] == pytest.approx(f2, rel=0.01) and fz[1][1] == pytest.approx(z2, abs=0.003)


def _fake_openfoam(comms, nodes_x0, dt, n, force_fn, done, got):
    """The OpenFOAM side of the exchange: writes forces.out, removes the lock, waits for the answer."""
    comms.mkdir(parents=True, exist_ok=True)
    lock = comms / L.LOCK
    lock.unlink(missing_ok=True)
    while not lock.exists():  # the first answer (no forces yet)
        time.sleep(0.001)
    got.append(None)
    for k in range(1, n):
        t = k * dt
        F, M = force_fn(t)
        rows = lambda a: "".join("(%.9g %.9g %.9g)\n" % tuple(r) for r in a)  # noqa: E731
        (comms / "forces.out").write_text(
            f"time {t:.10g};\nprevTime {t - dt:.10g};\n\npoints\n{len(F)}\n(\n{rows(nodes_x0)});\n\n"
            f"forces\n{len(F)}\n(\n{rows(F)});\n\nmoments\n{len(F)}\n(\n{rows(M)});\n")
        lock.unlink()
        while not lock.exists():
            time.sleep(0.0005)
        got.append((comms / "positions.in").read_text())
    done.set()


def _model():
    X = _plate_nodes()
    heave = np.zeros_like(X)
    heave[:, 2] = X[:, 1] ** 2
    pitch = np.zeros_like(X)
    pitch[:, 2] = -(X[:, 0] - 0.08) * X[:, 1]
    return FC.build_node_model(X, np.arange(len(X)), np.array([heave, pitch]), [2 * math.pi * 15, 2 * math.pi * 42], [1.5, 0.3], [1, 0, 0],
                               [0, 1, 0], n_nodes=5, zeta=0.01)


def _run_coupled(tmp_path, force_fn, n=640, dt=0.0005, t_release=0.01):
    md = _model()
    comms = tmp_path / "comms"
    done, got = threading.Event(), []
    run = FC.CoupledRun(md, comms, t_release=t_release, perturbation=[0.2, 0.05], poll=0.0005)
    th = threading.Thread(target=_fake_openfoam, args=(comms, md.x0, dt, n, force_fn, done, got), daemon=True)
    th.start()
    run.run(alive=lambda: not done.is_set(), turn_timeout=20)
    th.join(5)
    return md, run, got


def test_coupled_loop_recovers_the_structure_without_air_forces(tmp_path):
    md, run, got = _run_coupled(tmp_path, lambda t: (np.zeros((5, 3)), np.zeros((5, 3))))
    res = FC.analyse(run)
    f1, f2 = md.omega / (2 * math.pi)
    found = {round(f): z for f, z in res["poles"]}
    assert any(abs(f - f1) < 0.6 and abs(z - 0.01) < 0.002 for f, z in res["poles"])
    assert any(abs(f - f2) < 1.5 and abs(z - 0.01) < 0.004 for f, z in res["poles"]), found
    # what OpenFOAM receives is a valid state: n points, n angles, the structure moves smoothly from rest
    assert got[0] is None and got[1].count("(") >= 2 * 5 and "angles" in got[1]
    t, q = run.series()
    assert abs(q[0]).max() < 1e-9 and abs(q).max() > 1e-3


def test_a_steady_aerodynamic_load_is_the_reference_not_a_motion(tmp_path):
    steady = np.zeros((5, 3))
    steady[:, 2] = 3.0  # constant lift on every node, present before and after the release
    md, run, _ = _run_coupled(tmp_path, lambda t: (steady, np.zeros((5, 3))), n=200)
    t, q = run.series()
    # the released motion is the free vibration only: no static offset from the constant load
    assert abs(q.mean(0)).max() < 0.5 * abs(q).max()
    assert run.q_ref is not None and np.allclose(run.q_ref, md.generalised_force(steady, np.zeros((5, 3))))
