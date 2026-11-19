"""Flutter solver (simwb.solvers.flutter): analytic checks of the aerodynamics and the p-k method."""
import math

import numpy as np
import pytest
from scipy.optimize import brentq

from simwb.solvers import flutter as F

RHO, B = 1.0, 1.0


def section(a=-0.2, mu=20.0, x_alpha=0.2, r2=0.25, wh=0.3, kappa=1.0):
    """Typical section with the reference point (elastic axis) a*b from mid-chord. Units: b = 1, rho = 1, omega_alpha = 1.
    h up, alpha nose up: a centre of gravity aft of the axis (x_alpha > 0) gives the coupling -m x_alpha b."""
    m = mu * math.pi * RHO * B ** 2
    S, I = m * x_alpha * B, m * r2 * B ** 2
    M = np.array([[m, -S], [-S, I]])
    K = np.diag([m * wh ** 2, I * 1.0 ** 2])
    st = {"b": np.array([B]), "a": np.array([a]), "w": np.array([1.0]), "ph": np.array([[1.0, 0.0]]),
          "pa": np.array([[0.0, 1.0]]), "fac": kappa}
    return M, K, st


def test_theodorsen_limits():
    assert abs(F.theodorsen(0.0)[()] - 1) < 1e-4
    assert abs(F.theodorsen(50.0)[()] - 0.5) < 5e-3
    c = F.theodorsen(np.array([0.1, 0.5, 1.0]))
    assert (c.imag < 0).all() and (np.diff(c.real) < 0).all()  # phase lag, magnitude falling to 1/2


def test_isa_sea_level_and_altitude():
    a = F.isa(0)
    assert a["rho"] == pytest.approx(1.225, rel=2e-3) and a["a"] == pytest.approx(340.3, rel=2e-3)
    assert F.isa(11000)["rho"] == pytest.approx(0.3639, rel=3e-3)
    with pytest.raises(ValueError):
        F.isa(30000)


def test_apparent_mass_in_still_air():
    """V = 0: a flat plate in air has the apparent mass pi rho b^2 (heave) and pi rho b^4 (1/8 + a^2) (pitch)."""
    M, K, st = section()
    sysm = F.Flutter(M, K, st, RHO)
    m_add_h = math.pi * RHO * B ** 2
    I_add = math.pi * RHO * B ** 4 * (0.125 + 0.2 ** 2)
    A2, _, _ = sysm.aero(0.0, np.array([1.0]))
    assert A2[0, 0] == pytest.approx(-m_add_h) and A2[1, 1] == pytest.approx(-I_add)
    roots = sysm.initial_roots()
    # uncoupled heave only: omega^2 = K / (m + m_add) when the mass coupling is removed
    M2, K2, st2 = section(x_alpha=0.0, a=0.0)
    r2 = F.Flutter(M2, K2, st2, RHO).initial_roots()
    assert r2[0][0].imag == pytest.approx(math.sqrt(K2[0, 0] / (M2[0, 0] + m_add_h)), rel=1e-9)
    assert len(roots) == 2


@pytest.mark.parametrize("a", [-0.2, 0.0, 0.4])
def test_divergence_speed_is_analytic(a):
    """Static stability limit: K_alpha = 2 pi rho V^2 b^2 (a + 1/2)  (C(0) = 1), no matter how the mass is arranged."""
    M, K, st = section(a=a, x_alpha=0.1, wh=1.4)  # heave stiff: no flutter before the divergence
    vd = math.sqrt(K[1, 1] / (2 * math.pi * RHO * B ** 2 * (a + 0.5)))
    grid = np.linspace(0.05 * vd, 1.4 * vd, 60)
    res = F.solve(M, K, st, RHO, grid)
    ev = res["events"]
    assert ev["divergence"] is not None
    assert ev["divergence"]["V"] == pytest.approx(vd, rel=2e-3)
    if ev["flutter"] is not None:
        assert ev["flutter"]["V"] > ev["divergence"]["V"] * 0.99


def test_no_instability_when_axis_is_ahead_of_the_quarter_chord_and_cg_forward():
    """a < -1/2 (axis ahead of the aerodynamic centre) with the centre of gravity at the axis: no divergence, no flutter."""
    M, K, st = section(a=-0.7, x_alpha=0.0, wh=0.5)
    res = F.solve(M, K, st, RHO, np.linspace(0.1, 8.0, 50))
    assert res["events"] == {"flutter": None, "divergence": None}
    assert (res["P"].real < 1e-9).all()


@pytest.mark.parametrize("a_ref", [-0.9, -0.2, 0.0, 0.7])
def test_result_does_not_depend_on_the_reference_point(a_ref):
    """The same plate described at another reference point (strips fitted from the same displacement field) flutters
    at the same speed: tests every a-dependent term of the lift and moment together with the kinematics."""
    # a flat plate chord 2 (b = 1) span 2, nodes on a grid; modes = rigid heave and a pitch about x = -0.2
    xs, ys = np.meshgrid(np.linspace(-1, 1, 9), np.linspace(0, 2, 9))
    nodes = np.column_stack([xs.ravel(), ys.ravel(), np.zeros(xs.size)])
    heave = np.zeros_like(nodes)
    heave[:, 2] = 1.0
    pitch = np.zeros_like(nodes)
    pitch[:, 2] = -(nodes[:, 0] + 0.2)  # nose-up pitch about the axis at x = -0.2 (the leading edge, x < axis, rises)
    shapes = np.array([heave, pitch])
    flow, span = [1, 0, 0], [0, 1, 0]
    M0, K0, _ = section(a=-0.2, x_alpha=0.2)
    # the modal mass matrix of these two shapes: per unit span 2 (plate length), as in the single-strip section
    # (width 2 here): scale the mass matrix by the width so that the physical section is the same
    ref = F.build_strips(nodes, np.arange(len(nodes)), shapes, flow, span, n_strips=1, a_ref=-0.2, finite_wing=False)
    other = F.build_strips(nodes, np.arange(len(nodes)), shapes, flow, span, n_strips=1, a_ref=a_ref, finite_wing=False)
    assert ref["b"][0] == pytest.approx(1.0) and ref["w"][0] == pytest.approx(2.0)
    Mw, Kw = 2.0 * M0, 2.0 * K0
    # in the reference description pitch is about the axis: alpha = -slope of the displacement, so these modes are
    # (h at the reference point, alpha) with M, K as in the typical section
    grid = np.linspace(0.3, 6.0, 70)
    f_ref = F.solve(Mw, Kw, ref, RHO, grid)["events"]["flutter"]
    f_oth = F.solve(Mw, Kw, other, RHO, grid)["events"]["flutter"]
    assert f_ref is not None and f_oth is not None
    assert f_oth["V"] == pytest.approx(f_ref["V"], rel=2e-3)
    assert f_oth["omega"] == pytest.approx(f_ref["omega"], rel=2e-3)


def jones_flutter_speed(M, K, st, rho, lo, hi):
    """Independent solution: Theodorsen's C replaced by Jones' two-pole rational form, exact eigenvalues of the
    state-space system with the aerodynamic lag states (no p-k iteration, no root tracking)."""
    sysm = F.Flutter(M, K, st, rho)
    n = sysm.n
    b, a, w, ph, pa, fac = st["b"], st["a"], st["w"], st["ph"], st["pa"], st["fac"]
    ns = len(b)
    betas = (0.0455, 0.3)
    gains = (0.165, 0.335)

    def max_real(V):
        A2, A1, A0 = sysm.aero(V, np.zeros(ns))  # everything except the circulatory parts
        Me = sysm.M - A2.real
        d0 = V * pa
        d1 = -ph + (b * (0.5 - a))[:, None] * pa
        g = (w * 2 * math.pi * rho * V * b * fac)[:, None] * (ph + (b * (a + 0.5))[:, None] * pa)
        N = 2 * n + 2 * ns
        H = np.zeros((N, N))
        H[:n, n:2 * n] = np.eye(n)
        Minv = np.linalg.inv(Me)
        Kq = sysm.K - A0.real
        Cq = sysm.Cs - A1.real
        Bq = np.zeros((n, n))
        Bd = np.zeros((n, n))
        for j in range(ns):
            Bq += np.outer(g[j], d0[j]) * 0.5
            Bd += np.outer(g[j], d1[j]) * 0.5
        H[n:2 * n, :n] = Minv @ (-Kq + Bq)
        H[n:2 * n, n:2 * n] = Minv @ (-Cq + Bd)
        for j in range(ns):
            for i, (be, ga) in enumerate(zip(betas, gains)):
                col = 2 * n + 2 * j + i
                H[n:2 * n, col] = Minv @ g[j] * ga  # C W = W/2 + 0.165 y1 + 0.335 y2, y' = (V/b) beta (W - y)
                H[col, :n] = (V / b[j]) * be * d0[j]
                H[col, n:2 * n] = (V / b[j]) * be * d1[j]
                H[col, col] = -(V / b[j]) * be
        lam = np.linalg.eigvals(H)
        return lam[np.abs(lam.imag) > 1e-3].real.max()

    grid = np.linspace(lo, hi, 80)
    vals = [max_real(v) for v in grid]
    i = next(i for i, x in enumerate(vals) if x > 0)
    return brentq(max_real, grid[i - 1], grid[i], xtol=1e-6)


@pytest.mark.parametrize("kw", [dict(), dict(a=-0.1, x_alpha=0.3, wh=0.5), dict(mu=40.0, x_alpha=0.25, r2=0.3, wh=0.2),
                                dict(kappa=0.6)])
def test_p_k_matches_the_state_space_solution(kw):
    """Flutter speed by the p-k method against the eigenvalues of the lag-state system (Jones approximation of C:
    agreement within the approximation, about 1.5 %)."""
    M, K, st = section(**kw)
    res = F.solve(M, K, st, RHO, np.linspace(0.2, 6.0, 90))
    ev = res["events"]["flutter"]
    assert ev is not None
    vj = jones_flutter_speed(M, K, st, RHO, 0.2, 6.0)
    assert ev["V"] == pytest.approx(vj, rel=0.02)
    assert 0.2 < ev["omega"] < 1.5  # between the two uncoupled frequencies' neighbourhood


def test_flutter_is_a_coalescence_of_the_two_modes():
    """Below the flutter speed both roots are damped; the frequencies approach each other; at flutter one goes unstable."""
    M, K, st = section()
    res = F.solve(M, K, st, RHO, np.linspace(0.2, 6.0, 120))
    P, V = res["P"], res["V"]
    vf = res["events"]["flutter"]["V"]
    below = V < 0.9 * vf
    assert (P.real[below] < 0).all()
    gap0 = abs(P[0, 0].imag - P[0, 1].imag)
    i = np.argmin(abs(V - 0.95 * vf))
    assert abs(P[i, 0].imag - P[i, 1].imag) < 0.7 * gap0
    unstable = P.real[V > 1.05 * vf].max()
    assert unstable > 0


def test_structural_damping_raises_the_flutter_speed():
    M, K, st = section()
    grid = np.linspace(0.2, 8.0, 110)
    v0 = F.solve(M, K, st, RHO, grid)["events"]["flutter"]["V"]
    w = np.sqrt(np.diag(K) / np.diag(M))
    Cs = np.diag(2 * 0.03 * w * np.diag(M))
    v1 = F.solve(M, K, st, RHO, grid, Cs=Cs)["events"]["flutter"]["V"]
    assert v1 > v0


def test_denser_air_lowers_the_flutter_speed():
    M, K, st = section()
    grid = np.linspace(0.2, 10.0, 100)
    v1 = F.solve(M, K, st, 1.0, grid)["events"]["flutter"]["V"]
    v2 = F.solve(M, K, st, 1.5, grid)["events"]["flutter"]["V"]
    assert v2 < v1


def test_build_strips_geometry_and_fit():
    xs, ys = np.meshgrid(np.linspace(0, 0.2, 6), np.linspace(0, 1, 11))
    nodes = np.column_stack([xs.ravel(), ys.ravel(), 0.01 * xs.ravel()])
    shape = np.zeros_like(nodes)
    shape[:, 2] = 0.3 - 2.0 * (nodes[:, 0] - 0.1)  # h = 0.3 at mid-chord, nose-up alpha = 2 rad
    st = F.build_strips(nodes, np.arange(len(nodes)), np.array([shape]), [1, 0, 0], [0, 1, 0], n_strips=5)
    assert len(st["b"]) == 5 and st["b"] == pytest.approx(0.1)
    assert st["w"].sum() == pytest.approx(1.0)
    assert st["ph"][:, 0] == pytest.approx(0.3) and st["pa"][:, 0] == pytest.approx(2.0)
    assert st["aspect_ratio"] == pytest.approx(2 * 1.0 / 0.2)  # the whole wing: root mirrored
    assert st["fac"] == pytest.approx(10.0 / 12.0)
    with pytest.raises(ValueError):
        F.build_strips(nodes, np.arange(len(nodes)), np.array([shape]), [0, 1, 0], [0, 1, 0])
