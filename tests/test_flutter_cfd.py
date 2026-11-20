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


# ---------------------------------------------------------------------------------- forced motion, lock-in, restart state
def _tm(md):
    return md.T.reshape(md.m, -1)  # generalised force = _tm @ F.flatten() (moments zero)


def test_lockin_recovers_a_linear_fluid_from_unevenly_sampled_noisy_forces():
    k, c, A, f = 640.0, 2.5, 1e-3, 23.0
    w = 2 * math.pi * f
    rng = np.random.default_rng(3)
    t = np.sort(np.cumsum(rng.uniform(0.6e-3, 1.4e-3, 700)))
    q, qd = A * np.sin(w * t), A * w * np.cos(w * t)
    Q = 12.0 - k * q - c * qd + 0.8 * (t - t.mean()) + 0.01 * rng.standard_normal(len(t))  # mean, drift, noise
    kk, cc = FC.aero_from_lockin(FC.lockin(t, Q, f, n_skip_periods=1, t_origin=0.0), A, w)
    assert kk == pytest.approx(k, rel=0.01) and cc == pytest.approx(c, rel=0.01)
    with pytest.raises(ValueError):
        FC.lockin(t[:50], Q[:50], f, n_skip_periods=1)  # (not even one period after the skipped start-up)


def test_prescribed_run_drives_the_mode_and_the_force_gives_stiffness_and_damping(tmp_path):
    md = _model()
    k, c, A, f = 480.0, 3.2, 1.5e-3, 20.0
    comms, dt, n = tmp_path / "comms", 0.0005, 420
    run = FC.PrescribedRun(md, comms, t_release=0.01, mode=0, amp=A, f_hz=f, poll=0.0005)
    Tm = _tm(md)
    pinv = np.linalg.pinv(Tm)

    def fluid(t):  # a linear fluid: the force follows the wall motion q(t) it is given (stiffness k, damping c on mode 0)
        q, v = run.drive(t)
        Qd = np.array([-k * q[0] - c * v[0], 0.0])
        F = (pinv @ Qd).reshape(-1, 3)
        return F, np.zeros_like(F)

    done, got = threading.Event(), []
    th = threading.Thread(target=_fake_openfoam, args=(comms, md.x0, dt, n, fluid, done, got), daemon=True)
    th.start()
    run.run(alive=lambda: not done.is_set(), turn_timeout=20)
    th.join(5)
    kk, cc = run.aero(n_skip_periods=1)
    assert kk == pytest.approx(k, rel=0.01) and cc == pytest.approx(c, rel=0.01)
    t, q = run.series()
    assert abs(q[0]).max() < 1e-12 and abs(q[:, 0]).max() == pytest.approx(A, rel=0.01) and abs(q[:, 1]).max() == 0.0
    # smooth start: the first samples after the release follow the raised-cosine ramp (no jump)
    assert abs(q[1, 0]) < 0.02 * A


def test_coupled_run_state_round_trip_reproduces_the_history(tmp_path):
    import json
    md = _model()
    rng = np.random.default_rng(7)
    forces = [(rng.normal(0, 1.0, (5, 3)), rng.normal(0, 0.1, (5, 3))) for _ in range(120)]
    dt = 0.001

    def drive(run, a, b):
        for k in range(a, b):
            run.step({"time": k * dt, "forces": forces[k][0], "moments": forces[k][1]})

    ref = FC.CoupledRun(md, tmp_path / "c1", t_release=0.03, perturbation=[0.2, 0.05], t_ref_from=0.01)
    ref.step(None)
    drive(ref, 1, 120)
    part = FC.CoupledRun(md, tmp_path / "c2", t_release=0.03, perturbation=[0.2, 0.05], t_ref_from=0.01)
    part.step(None)
    drive(part, 1, 70)  # (past the release)
    state = json.loads(json.dumps(part.to_state()))  # through the file format
    rows = [(t, q.copy(), Q.copy(), r) for t, q, Q, r in part.history]
    again = FC.CoupledRun.from_state(state, md, tmp_path / "c3", t_release=0.03, perturbation=[0.2, 0.05], t_ref_from=0.01,
                                     history=rows)
    assert again.released and again.t_prev == part.t_prev and state["n_history"] == len(rows)
    # a restored run answers the start-up handshake with the state the mesh already has, and adds no history row
    t0, pos0, ang0 = again.step(None)
    t1, pos1, ang1 = part.step(None)
    assert t0 == t1 == part.t_prev and np.allclose(pos0, pos1) and len(again.history) == len(rows)
    drive(again, 70, 120)
    assert len(again.history) == len(ref.history)
    for a, b in zip(again.history, ref.history):
        assert a[0] == b[0] and a[3] == b[3]
        assert np.allclose(a[1], b[1], atol=1e-9, rtol=0) and np.allclose(a[2], b[2], atol=1e-9, rtol=0)
