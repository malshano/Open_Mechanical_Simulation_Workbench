"""Planning and summary of the CFD-coupled flutter sweep (simwb.flutter_plan): pure Python."""
import json
import math

import numpy as np
import pytest

from simwb import flutter_plan as FP

STRIP = {
    "flutter": {"speed_m_s": 100.0, "frequency_Hz": 21.0},
    "critical": {"type": "flutter", "speed_m_s": 100.0},
    "sweep": {"speed_m_s": [0.0, 50.0, 100.0, 150.0],
              "roots": [{"label": "Mode 1", "frequency_Hz": [10.0, 10.5, 11.0, 11.5], "damping_percent": [2.0, 3.0, 4.0, 5.0]},
                        {"label": "Mode 2", "frequency_Hz": [30.0, 26.0, 21.0, 20.0], "damping_percent": [2.0, 1.0, 0.0, -2.0]}]},
}


def linear_points(u_flutter=100.0, slope=-1e-3, us=(70, 85, 100, 115), sigma=0.002, ok=True):
    return [{"U": float(u), "zeta": slope * (u - u_flutter), "sigma": sigma, "ok": ok} for u in us]


# --------------------------------------------------------------------------------------------- plan_speeds
def test_plan_speeds_around_the_strip_flutter_speed():
    assert FP.plan_speeds(STRIP) == pytest.approx([70, 85, 100, 115])
    assert FP.plan_speeds(STRIP, n=1) == [100.0]
    s = FP.plan_speeds(STRIP, n=6)
    assert len(s) == 6 and s == sorted(s) and s[0] == pytest.approx(70) and s[-1] == pytest.approx(115)


def test_plan_speeds_respects_v_max_and_deduplicates():
    s = FP.plan_speeds(STRIP, v_max=105.0)
    assert s == pytest.approx([70, 85, 100, 105]) and max(s) <= 105.0
    s = FP.plan_speeds(STRIP, v_max=80.0)
    assert s == pytest.approx([70, 80])  # 85, 100 and 115 all cap to 80: one entry
    assert FP.plan_speeds(STRIP, v_max=1000.0)[-1] == pytest.approx(115)


def test_plan_speeds_fall_back_to_divergence_then_half_vmax():
    div = {"flutter": None, "critical": {"type": "divergence", "speed_m_s": 60.0}}
    assert FP.plan_speeds(div) == pytest.approx([42, 51, 60, 69])
    assert FP.plan_speeds({"flutter": None, "critical": None}, v_max=200.0) == pytest.approx([70, 85, 100, 115])
    assert FP.plan_speeds(None, v_max=200.0)[2] == pytest.approx(100.0)
    with pytest.raises(ValueError):
        FP.plan_speeds({"flutter": None, "critical": None})


# --------------------------------------------------------------------------------------------- next_speed
def test_next_speed_secant_on_a_bracket():
    pts = [{"U": 70, "zeta": 0.03, "sigma": 0.002, "ok": True}, {"U": 130, "zeta": -0.03, "sigma": 0.002, "ok": True}]
    assert FP.next_speed(pts) == pytest.approx(100.0)  # linear: the secant is the root
    # nearly all of the change at one end: the step stays inside the middle of the bracket
    pts = [{"U": 70, "zeta": 0.5, "sigma": 0.002, "ok": True}, {"U": 130, "zeta": -0.001, "sigma": 0.002, "ok": True}]
    assert 70 + 0.2 * 60 <= FP.next_speed(pts) <= 130 - 0.2 * 60 + 1e-9


def test_next_speed_ignores_unreliable_points_and_stops_when_bracketed():
    pts = [{"U": 70, "zeta": 0.03, "sigma": 0.002, "ok": True}, {"U": 100, "zeta": -0.5, "sigma": 0.2, "ok": False},
           {"U": 130, "zeta": -0.03, "sigma": 0.002, "ok": True}]
    assert FP.next_speed(pts) == pytest.approx(100.0, abs=0.5)  # the unreliable run at 100 may be repeated, longer
    tight = [{"U": 99.0, "zeta": 0.001, "sigma": 0.001, "ok": True}, {"U": 102.0, "zeta": -0.002, "sigma": 0.001, "ok": True}]
    assert FP.next_speed(tight, bracket_tol=0.05) is None  # 3 m/s < 5 % of 100
    assert FP.next_speed(tight, bracket_tol=0.01) is not None
    assert FP.next_speed([]) is None
    assert FP.next_speed([{"U": 80, "zeta": None, "sigma": None, "ok": False}]) is None


@pytest.mark.parametrize("u_true,curve", [
    (100.0, lambda u, ut: -1e-3 * (u - ut)),
    (92.0, lambda u, ut: -0.04 * math.tanh((u - ut) / 25.0)),  # saturating
    (140.0, lambda u, ut: 0.02 - 0.02 * (u / ut) ** 4),  # strongly curved (zeta falls faster and faster)
])
def test_next_speed_converges_to_the_flutter_speed(u_true, curve):
    pts = [{"U": u, "zeta": curve(u, u_true), "sigma": 0.001, "ok": True} for u in (u_true * 0.7, u_true * 1.15)]
    for it in range(20):
        u = FP.next_speed(pts, bracket_tol=0.02, v_min=10.0, v_max=400.0)
        if u is None:
            break
        pts.append({"U": u, "zeta": curve(u, u_true), "sigma": 0.001, "ok": True})
    assert it < 15, "no convergence"
    pts.sort(key=lambda p: p["U"])
    lo = max(p["U"] for p in pts if p["zeta"] > 0)
    hi = min(p["U"] for p in pts if p["zeta"] <= 0 and p["U"] > lo)
    assert lo <= u_true <= hi and (hi - lo) <= 0.02 * 0.5 * (lo + hi) + 1e-9


def test_next_speed_without_a_bracket_looks_up_or_down_and_respects_limits():
    stable = [{"U": 60, "zeta": 0.05, "sigma": 0.002, "ok": True}, {"U": 80, "zeta": 0.03, "sigma": 0.002, "ok": True}]
    u = FP.next_speed(stable, v_max=500)
    assert 80 < u <= 80 * 1.25 + 1e-9
    assert FP.next_speed(stable, v_max=80) is None  # nothing above the limit
    assert FP.next_speed(stable, v_max=82.0) == pytest.approx(82.0)
    unstable = [{"U": 60, "zeta": -0.02, "sigma": 0.002, "ok": True}]
    d = FP.next_speed(unstable, v_min=20)
    assert 20 < d < 60
    assert FP.next_speed([{"U": 20.0, "zeta": -0.02, "sigma": 0.002, "ok": True}], v_min=20.0) is None
    one = FP.next_speed([{"U": 60, "zeta": 0.02, "sigma": 0.002, "ok": True}])
    assert one > 60  # one stable point: go up


# --------------------------------------------------------------------------------------------- cost
def test_cost_estimate():
    c = FP.cost_estimate(dt=7e-5, f_hz=20.0, n_periods=8)
    steps = math.ceil((8 / 20.0) / 7e-5)
    assert c["steps"] == steps and c["wall_s"] == pytest.approx(steps * 1.35)
    assert c["wall_h"] == pytest.approx(c["wall_s"] / 3600) and "estimate" in c["note"].lower()
    c2 = FP.cost_estimate(7e-5, 20.0, 8, t_settle=0.1, phase_a_step_s=0.2)
    assert c2["steps_phase_a"] == math.ceil(0.1 / 7e-5)
    assert c2["wall_s"] == pytest.approx(c2["steps_phase_a"] * 0.2 + steps * 1.35)
    assert c2["steps"] == c2["steps_phase_a"] + c2["steps_phase_b"]
    # three runs in parallel: each step 1.6 x slower, three speeds at a time
    c3 = FP.cost_estimate(7e-5, 20.0, 8, par_runs=3)
    assert c3["batch_wall_s"] == pytest.approx(c["wall_s"] * 1.6)
    assert c3["wall_s"] == pytest.approx(c["wall_s"] * 1.6 / 3)
    assert c3["wall_s"] < c["wall_s"]
    assert FP.cost_estimate(7e-5, 20.0, 8, n_speeds=4)["sweep_wall_s"] == pytest.approx(4 * c["wall_s"])
    with pytest.raises(ValueError):
        FP.cost_estimate(0.0, 20.0, 8)


# --------------------------------------------------------------------------------------------- fit_flutter_speed
def test_fit_flutter_speed_on_linear_damping():
    r = FP.fit_flutter_speed(linear_points(100.0))
    assert r["speed_m_s"] == pytest.approx(100.0, rel=1e-9) and r["basis"] == "interpolated"
    assert r["bracket"] == [85.0, 100.0]  # zeta = 0.015 at 85, exactly 0 at 100 (counts as crossed)
    assert r["sigma_m_s"] > 0
    r = FP.fit_flutter_speed(linear_points(160.0, us=(70, 85, 100, 115)))
    assert r["basis"] == "extrapolated" and r["speed_m_s"] == pytest.approx(160.0, rel=1e-9) and r["bracket"] is None


def test_fit_flutter_speed_uncertainty_follows_the_noise():
    rng = np.random.default_rng(0)
    us = np.array([70, 80, 90, 100, 110, 120.0])
    est = []
    sigs = []
    for _ in range(300):
        z = -1e-3 * (us - 100.0) + 0.004 * rng.standard_normal(len(us))
        r = FP.fit_flutter_speed([{"U": u, "zeta": zz, "sigma": 0.004, "ok": True} for u, zz in zip(us, z)])
        est.append(r["speed_m_s"])
        sigs.append(r["sigma_m_s"])
    assert np.mean(est) == pytest.approx(100.0, abs=0.7)
    assert np.std(est) == pytest.approx(np.median(sigs), rel=0.3)  # the propagated sigma is the real scatter


def test_fit_flutter_speed_uses_the_weights_and_the_ok_flag():
    pts = linear_points(100.0)
    pts.append({"U": 130.0, "zeta": 0.5, "sigma": 0.002, "ok": False})  # an unreliable point must not count
    assert FP.fit_flutter_speed(pts)["speed_m_s"] == pytest.approx(100.0, rel=1e-9)
    pts = linear_points(100.0) + [{"U": 125.0, "zeta": 0.1, "sigma": 5.0, "ok": True}]  # hugely uncertain: ~no weight
    assert FP.fit_flutter_speed(pts)["speed_m_s"] == pytest.approx(100.0, abs=0.5)


def test_fit_flutter_speed_none_cases():
    assert FP.fit_flutter_speed([])["basis"] == "none"
    assert FP.fit_flutter_speed(linear_points(100.0, us=(100,)))["basis"] == "none"
    assert FP.fit_flutter_speed(linear_points(100.0, ok=False))["basis"] == "none"
    r = FP.fit_flutter_speed(linear_points(100.0, slope=+1e-3))  # damping rises with speed: no crossing
    assert r["basis"] == "none" and r["speed_m_s"] is None
    r = FP.fit_flutter_speed([{"U": 70, "zeta": 0.03, "sigma": 0.002, "ok": True}, {"U": 90, "zeta": 0.03, "sigma": 0.002, "ok": True}])
    assert r["basis"] == "none"


# --------------------------------------------------------------------------------------------- compare_to_strip
def test_compare_to_strip_follows_the_root_nearest_in_frequency():
    rows = FP.compare_to_strip([{"U": 75.0, "critical": {"f_Hz": 23.0, "zeta": 0.012}},
                                {"U": 125.0, "critical": {"f_Hz": 11.2, "zeta": 0.045}},
                                {"U": 90.0, "critical": None}], STRIP)
    assert [r["U"] for r in rows] == [75.0, 125.0]
    r0, r1 = rows
    assert r0["strip_f"] == pytest.approx(23.5) and r0["strip_zeta"] == pytest.approx(0.005)  # Mode 2 at 75 m/s
    assert r0["ratio"] == pytest.approx(0.012 / 0.005)
    assert r1["strip_f"] == pytest.approx(11.25) and r1["strip_zeta"] == pytest.approx(0.045)  # Mode 1
    assert FP.compare_to_strip([{"U": 75.0, "zeta": 0.01, "f": 23.0}], STRIP)[0]["strip_f"] == pytest.approx(23.5)
    assert FP.compare_to_strip([{"U": 100.0, "critical": {"f_Hz": 21.0, "zeta": 0.01}}], STRIP)[0]["ratio"] is None  # strip zeta = 0
    nostrip = FP.compare_to_strip([{"U": 75.0, "critical": {"f_Hz": 23.0, "zeta": 0.012}}], None)
    assert nostrip[0]["strip_zeta"] is None and nostrip[0]["ratio"] is None


# --------------------------------------------------------------------------------------------- build_summary
def speed_entry(U, zeta, ok=True, sigma=0.002, f=21.0, n=2000, lco=False, with_series=True, other_ok=True):
    t = np.linspace(0, 0.4, n)
    e = {"U": U, "mach": U / 340.0, "t_release": 0.1, "duration_s": 0.4, "n_exchanges": n, "wall_s": 1234.5, "lco": lco,
         "estimate": {"poles": [{"f_Hz": f, "zeta": zeta, "zeta_sigma": sigma, "spread": 0.001, "n_periods": 8.4, "ok": ok,
                                 "reasons": [], "hint_Hz": 20.0},
                                {"f_Hz": 11.0, "zeta": 0.05, "zeta_sigma": 0.003, "spread": 0.002, "n_periods": 4.4, "ok": other_ok,
                                 "reasons": [], "hint_Hz": 10.0}]}}
    if with_series:
        e.update({"t": t, "q": np.column_stack([np.sin(40 * t), np.cos(90 * t)]), "labels": ["Mode 1", "Mode 2"]})
    return e


MODES = [{"index": 1, "frequency_Hz": 10.0, "generalized_mass_kg": 1.5}, {"index": 2, "frequency_Hz": 20.0, "generalized_mass_kg": 0.4}]
TOP = {"kind", "status", "strip", "modes", "mesh", "settings", "speeds", "damping_curve", "flutter", "warnings", "assumptions", "seconds"}


def test_build_summary_schema_and_json_round_trip():
    speeds = [speed_entry(70, 0.02), speed_entry(85, 0.01), speed_entry(100, -0.004), speed_entry(115, -0.02)]
    s = FP.build_summary(STRIP, MODES, speeds, {"dt": 7e-5, "rpm": None}, 120000, ["w1"], ["a1"], 3600.0)
    assert set(s) == TOP and s["kind"] == "flutter_cfd" and s["status"] == "complete"
    assert s == json.loads(json.dumps(s, allow_nan=False))  # plain, finite, round-trips
    assert s["strip"]["flutter_speed_m_s"] == 100.0 and s["strip"]["frequency_Hz"] == 21.0 and s["strip"]["sweep"] == STRIP["sweep"]
    assert s["modes"] == [{"index": 1, "frequency_Hz": 10.0, "generalized_mass_kg": 1.5},
                          {"index": 2, "frequency_Hz": 20.0, "generalized_mass_kg": 0.4}]
    assert s["mesh"] == {"cells": 120000} and s["settings"]["dt"] == 7e-5
    assert s["warnings"] == ["w1"] and s["assumptions"] == ["a1"] and s["seconds"] == 3600.0
    sp = s["speeds"][2]
    assert set(sp) == {"U", "mach", "state", "t_release", "duration_s", "n_exchanges", "wall_s", "poles", "critical", "verdict", "series"}
    assert set(sp["poles"][0]) == {"f_Hz", "zeta", "zeta_sigma", "mode_label", "ok", "spread"}
    assert [p["mode_label"] for p in sp["poles"]] == ["Mode 2", "Mode 1"]
    assert sp["critical"] == {"f_Hz": 21.0, "zeta": -0.004, "zeta_sigma": 0.002}
    assert set(sp["series"]) == {"t", "q", "labels"} and len(sp["series"]["q"]) == 2
    assert all(len(x) <= 600 for x in sp["series"]["q"]) and len(sp["series"]["t"]) <= 600
    assert len(sp["series"]["t"]) == len(sp["series"]["q"][0]) and sp["series"]["t"][-1] == pytest.approx(0.4)
    assert [x["verdict"] for x in s["speeds"]] == ["decaying", "decaying", "growing", "growing"]
    assert set(s["damping_curve"][0]) == {"U", "zeta", "sigma", "strip_zeta", "f", "strip_f"}
    assert set(s["flutter"]) == {"speed_m_s", "sigma_m_s", "bracket", "frequency_Hz", "basis", "vs_strip_percent"}


def test_build_summary_verdicts_and_flutter_speed():
    speeds = [speed_entry(70, 0.03), speed_entry(85, 0.015), speed_entry(100, 0.0005, sigma=0.002),
              speed_entry(115, -0.02), speed_entry(130, -0.03, lco=True), speed_entry(145, 0.01, ok=False, other_ok=False)]
    s = FP.build_summary(STRIP, MODES, speeds, {}, None, [], [], 1.0)
    # the critical pole is the lowest ok zeta of the speed: mode 1's 0.05 never wins over these
    assert [x["verdict"] for x in s["speeds"]] == ["decaying", "decaying", "neutral", "growing", "lco", "unreliable"]
    assert s["speeds"][-1]["critical"] is None
    assert s["mesh"] == {"cells": None}
    f = s["flutter"]
    assert f["basis"] == "interpolated" and 95.0 < f["speed_m_s"] < 108.0 and f["sigma_m_s"] > 0
    assert f["vs_strip_percent"] == pytest.approx(100 * (f["speed_m_s"] - 100.0) / 100.0)
    assert f["frequency_Hz"] == pytest.approx(21.0)
    assert len(s["damping_curve"]) == 5  # the unreliable speed has no point on the curve
    assert s["damping_curve"][0]["strip_zeta"] == pytest.approx(np.interp(70, STRIP["sweep"]["speed_m_s"], [0.02, 0.01, 0.0, -0.02]))


def test_build_summary_partial_and_downsampling_and_empty():
    long = speed_entry(70, 0.02, n=50000)
    long["state"] = "running"
    s = FP.build_summary(STRIP, MODES, [long], {}, 10, [], [], 5.0)
    assert s["status"] == "partial" and s["speeds"][0]["state"] == "running"
    assert len(s["speeds"][0]["series"]["t"]) == 600 and s["speeds"][0]["series"]["t"][0] == 0.0
    assert s["flutter"]["basis"] == "none" and s["flutter"]["speed_m_s"] is None
    assert FP.build_summary(STRIP, MODES, [], {}, 10, [], [], 0.0, status="partial")["status"] == "partial"
    e = FP.build_summary(None, [], [speed_entry(60, 0.02, with_series=False)], {}, None, [], [], 0.0)
    assert e["speeds"][0]["series"] == {"t": [], "q": [], "labels": []}
    assert e["strip"]["flutter_speed_m_s"] is None and e["flutter"]["vs_strip_percent"] is None
    json.dumps(e, allow_nan=False)


def test_speed_points_feed_next_speed():
    s = FP.build_summary(STRIP, MODES, [speed_entry(70, 0.03), speed_entry(130, -0.03)], {}, None, [], [], 0.0)
    pts = FP.speed_points(s["speeds"])
    assert pts[0] == {"U": 70.0, "zeta": 0.03, "sigma": 0.002, "ok": True}
    assert FP.next_speed(pts) == pytest.approx(100.0)
