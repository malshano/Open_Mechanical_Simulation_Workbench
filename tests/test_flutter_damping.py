"""Damping estimator of the CFD-coupled flutter run (simwb.solvers.openfoam.flutter_damping): synthetic records only."""
import math

import numpy as np
import pytest

from simwb.solvers.openfoam import flutter_damping as FD
from simwb.solvers.openfoam.flutter_cfd import NodeModel

F0 = 20.0


def osc(t, f, z, amp=1.0, phase=0.0):
    """Released damped oscillation: amp exp(-z w t) sin(w_d t + phase); w = 2 pi f is the undamped frequency."""
    w = 2 * math.pi * f
    return amp * np.exp(-z * w * t) * np.sin(w * math.sqrt(1 - z * z) * t + phase)


def grid(n_periods, f=F0, dt=1e-3):
    return np.arange(0.0, n_periods / f, dt)


def one(t, y, hints=(F0,), **kw):
    return FD.estimate(t, y, list(hints), **kw)["poles"][0]


# --------------------------------------------------------------------------------------------- clean signals
@pytest.mark.parametrize("zeta", [0.03, 0.17, -0.02, 0.0])
def test_clean_decaying_growing_and_neutral(zeta):
    t = grid(10)
    p = one(t, osc(t, F0, zeta))
    assert p["zeta"] == pytest.approx(zeta, abs=1e-4)
    assert p["f_Hz"] == pytest.approx(F0 * math.sqrt(1 - zeta ** 2), rel=1e-3)
    assert p["ok"], p["reasons"]
    assert p["n_periods"] == pytest.approx(10, rel=0.02)
    assert p["hint_Hz"] == F0 and p["reasons"] == []
    for key in ("zeta_sigma", "spread", "zeta_pencil", "f_pencil_Hz"):
        assert key in p


def test_result_structure_and_json_types():
    t = grid(8)
    r = FD.estimate(t, osc(t, F0, 0.03), [F0])
    assert set(r) >= {"poles", "drift_order", "method"} and isinstance(r["drift_order"], int) and isinstance(r["method"], str)
    import json
    json.dumps(r)  # plain floats / lists


def test_growth_is_reported_with_the_right_sign_and_a_phase_offset():
    t = grid(9)
    p = one(t, osc(t, F0 * 1.03, -0.05, phase=0.7) + 0.4)  # phase, an offset (drift order 0) and a frequency off the hint
    assert p["zeta"] == pytest.approx(-0.05, abs=2e-3) and p["ok"], p["reasons"]
    assert p["f_Hz"] == pytest.approx(F0 * 1.03 * math.sqrt(1 - 0.05 ** 2), rel=2e-3)


# --------------------------------------------------------------------------------------------- noise, drift, modes
def test_two_percent_noise_and_the_error_is_inside_the_stated_uncertainty():
    rng = np.random.default_rng(3)
    t = grid(8)
    p = one(t, osc(t, F0, 0.03) + 0.02 * rng.standard_normal(len(t)))
    assert abs(p["zeta"] - 0.03) < max(0.003, 2 * p["zeta_sigma"])
    assert 0 < p["zeta_sigma"] < 0.01 and p["ok"], p["reasons"]


def test_slow_drift_does_not_bias_the_damping():
    rng = np.random.default_rng(5)
    t = grid(10)
    u = t / t[-1]
    drift = 0.8 * (1 - u) ** 2 + 0.3 * u  # a quadratic creep (the steady load changing with the motion)
    pink = np.cumsum(rng.standard_normal(len(t)))  # random walk: 1/f^2 low-frequency noise
    pink = 0.15 * (pink - np.polyval(np.polyfit(u, pink, 1), u)) / pink.std()
    p = one(t, osc(t, F0, 0.04) + drift + pink + 0.01 * rng.standard_normal(len(t)))
    assert p["zeta"] == pytest.approx(0.04, abs=0.004)
    assert p["ok"], p["reasons"]


def test_one_over_f_drift():
    """Drift that falls like 1/(1 + t/t0): not a polynomial, but slow against the band."""
    t = grid(10)
    p = one(t, osc(t, F0, 0.03) + 0.5 / (1 + t / 0.1))
    assert p["zeta"] == pytest.approx(0.03, abs=0.003)


def test_two_close_modes_are_separated():
    t = grid(12)
    y = osc(t, 20.0, 0.02) + 0.7 * osc(t, 25.0, 0.05, phase=1.0) + 0.005 * np.random.default_rng(1).standard_normal(len(t))
    r = FD.estimate(t, y, [20.0, 25.0])["poles"]
    assert r[0]["zeta"] == pytest.approx(0.02, abs=0.003) and r[1]["zeta"] == pytest.approx(0.05, abs=0.004)
    assert r[0]["f_Hz"] == pytest.approx(20.0, rel=0.01) and r[1]["f_Hz"] == pytest.approx(25.0, rel=0.01)
    assert r[0]["hint_Hz"] == 20.0 and r[1]["hint_Hz"] == 25.0


def test_modes_far_apart_and_several_coordinates():
    t = grid(12, dt=5e-4)
    q = np.column_stack([osc(t, 20.0, 0.03) + 0.05 * osc(t, 55.0, 0.01), 0.002 * osc(t, 20.0, 0.03, 0.5) + osc(t, 55.0, 0.01)])
    r = FD.estimate(t, q, [55.0, 20.0])["poles"]  # the order of the hints is kept
    assert r[0]["hint_Hz"] == 55.0 and r[1]["hint_Hz"] == 20.0
    assert r[0]["zeta"] == pytest.approx(0.01, abs=1e-3) and r[1]["zeta"] == pytest.approx(0.03, abs=1e-3)


def test_non_uniform_time_stamps():
    rng = np.random.default_rng(11)
    t = np.sort(np.concatenate([[0.0], rng.uniform(0.0, 0.5, 700)]))  # random times: not evenly spaced at all
    p = one(t, osc(t, F0, 0.03) + 0.01 * rng.standard_normal(len(t)))
    assert p["zeta"] == pytest.approx(0.03, abs=0.004) and p["ok"], p["reasons"]
    # CFD-like: nearly even steps with a few longer ones, unsorted input with a repeated stamp
    t2 = np.cumsum(np.where(np.arange(600) % 37 == 0, 2.4e-3, 7e-4))
    perm = rng.permutation(len(t2))
    tt = np.concatenate([t2[perm], t2[:1]])
    yy = np.concatenate([osc(t2, F0, 0.03)[perm], osc(t2, F0, 0.03)[:1]])
    assert one(tt, yy)["zeta"] == pytest.approx(0.03, abs=2e-3)


# --------------------------------------------------------------------------------------------- honesty
def test_a_frequency_that_is_not_near_a_hint_is_never_a_pole():
    t = grid(12)
    rng = np.random.default_rng(2)
    for noise in (0.0, 0.02):
        y = osc(t, 40.0, 0.02) + noise * rng.standard_normal(len(t))
        p = one(t, y, hints=(20.0,))
        assert not p["ok"] and p["reasons"]
        assert p["zeta"] is None or abs(p["f_Hz"] - 40.0) > 5.0  # never reported as the 40 Hz pole
    # pure noise: nothing there
    p = one(t, 0.02 * rng.standard_normal(len(t)))
    assert not p["ok"] and p["reasons"]


def test_signal_off_the_hint_by_more_than_15_percent_is_rejected():
    t = grid(12)
    p = one(t, osc(t, 20.0 * 1.19, 0.03), hints=(20.0,))  # inside the search band, outside the 15 % acceptance
    assert not p["ok"]
    assert any("hint" in r or "edge" in r or "peak" in r for r in p["reasons"])


def test_short_record_is_not_ok_and_says_why():
    t = grid(2.7)  # the failed first CFD run: < 3 periods
    for z in (0.03, 0.17):
        p = one(t, osc(t, F0, z) + 0.02 * np.random.default_rng(4).standard_normal(len(t)))
        assert not p["ok"]
        assert any("periods" in r and "run longer" in r for r in p["reasons"]), p["reasons"]
        assert 2.5 < p["n_periods"] < 2.8
    assert one(t, osc(t, F0, 0.03), n_periods_min=2)["n_periods"] < 3  # the limit is the caller's


def test_a_longer_record_turns_the_same_signal_ok():
    rng = np.random.default_rng(8)
    noise = 0.03 * rng.standard_normal(1200)
    t = np.arange(1200) * 1e-3
    short = one(t[:70], (osc(t, F0, 0.05) + noise)[:70])
    long_ = one(t[:340], (osc(t, F0, 0.05) + noise)[:340])
    assert not short["ok"] and long_["ok"], (short["reasons"], long_["reasons"])


def test_degenerate_inputs_do_not_raise():
    r = FD.estimate([0.0, 1.0, 2.0], [0.0, 1.0, 0.0], [20.0])["poles"][0]
    assert not r["ok"] and r["reasons"]
    t = grid(8, dt=0.02)  # 2.5 samples per period
    r = FD.estimate(t, osc(t, F0, 0.03), [F0])["poles"][0]
    assert not r["ok"] and r["reasons"]
    assert FD.estimate(grid(8), osc(grid(8), F0, 0.03), [])["poles"] == []
    r = FD.estimate(grid(8), np.zeros(len(grid(8))), [F0])["poles"][0]
    assert not r["ok"]
    r = FD.estimate(grid(8), np.full(len(grid(8)), np.nan), [F0])["poles"][0]
    assert not r["ok"]


def test_accuracy_acceptance_8_periods_2_percent_noise():
    """Over 100 random noise records (not 200: each fit costs 0.1 s; sampled at 2 ms) of 8 periods with 2 % noise the error
    of zeta is below max(0.003, 2 sigma) in at least 95 % of the cases, and the stated sigma is not a fiction."""
    t = grid(8, dt=2e-3)
    good, sig, err = 0, [], []
    n = 100
    for seed in range(n):
        rng = np.random.default_rng(1000 + seed)
        zt = rng.uniform(-0.03, 0.1)
        p = one(t, osc(t, F0, zt) + 0.02 * rng.standard_normal(len(t)))
        e = abs(p["zeta"] - zt)
        good += e < max(0.003, 2 * p["zeta_sigma"])
        sig.append(p["zeta_sigma"])
        err.append(e)
    assert good >= 0.95 * n, good
    assert np.median(err) < 3 * np.median(sig) and np.median(sig) < 0.01


# --------------------------------------------------------------------------------------------- release and duration
def _model(m=3, n=4):
    T = np.zeros((m, n, 3))
    for i in range(m):
        T[i, :, 2] = (i + 1) * np.linspace(0.1, 1.0, n) ** 2  # bending-like: the tip (last node) moves most
    return NodeModel(np.zeros((n, 3)), T, np.zeros((m, n, 3)), omega=[2 * math.pi * f for f in (10.0, 30.0, 60.0)], mg=[1.0, 1.0, 1.0])


def test_excitation_single_mode_and_pair():
    md = _model()
    v = FD.excitation(md, [1], 0.002)
    assert v[0] == 0 and v[2] == 0
    # the released mode reaches the requested tip amplitude: q_max = v / omega, tip = |T_tip| q_max
    assert np.linalg.norm(md.T[1, -1]) * v[1] / md.omega[1] == pytest.approx(0.002)
    v2 = FD.excitation(md, [0, 2], 0.001, tip_node=-1)
    assert v2[1] == 0 and v2[0] > 0 and v2[2] > 0
    assert np.linalg.norm(md.T[2, 1]) * FD.excitation(md, [2], 0.001, tip_node=1)[2] / md.omega[2] == pytest.approx(0.001)
    with pytest.raises(ValueError):
        FD.excitation(md, [5], 0.001)
    with pytest.raises(ValueError):
        FD.excitation(md, [], 0.001)
    md.T[0, -1] = 0
    with pytest.raises(ValueError):
        FD.excitation(md, [0], 0.001)


def test_required_duration():
    assert FD.required_duration([20.0]) == pytest.approx(8 / 20.0)
    assert FD.required_duration([20.0, 80.0]) == pytest.approx(8 / 20.0)  # 8 periods of the slowest dominate 4 of the fastest
    assert FD.required_duration([20.0, 80.0], t_settle=0.1) == pytest.approx(0.5)
    assert FD.required_duration([20.0], n_periods=2, f_fast_hz=5.0) == pytest.approx(4 / 5.0)  # the fast-mode rule wins
    with pytest.raises(ValueError):
        FD.required_duration([])
