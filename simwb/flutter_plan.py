"""Planning and reporting of a CFD-coupled flutter speed sweep (pure Python, no OpenFOAM).

A coupled run costs hours, so the speeds are chosen from the cheap strip theory result, the next speed is chosen from the
damping measured so far (bracketing the zero crossing of the damping ratio), and the cost is stated before it is spent.

    damping ratio zeta(U) of the critical pole:  > 0 decays (stable),  < 0 grows (flutter);  flutter speed = zero crossing.

Units: speeds in m/s, damping ratios as FRACTIONS of critical (0.02 = 2 %; negative = growth) everywhere in this module and
in the summary, frequencies in Hz, times in s. (The strip theory report stores percent: converted here.)

A "point" is {"U", "zeta", "sigma", "ok"}: the critical pole of one speed, its uncertainty and whether the estimator
(`solvers/openfoam/flutter_damping.estimate`) called it reliable. Only ok points are used to find the flutter speed.
"""
from __future__ import annotations

import math

import numpy as np

STRIP_FACTORS = (0.7, 0.85, 1.0, 1.15)
SIGMA_FLOOR = 1e-3  # a sigma of 0 would give a point infinite weight
MAX_SERIES_POINTS = 600
WINDOW_STEP_GROWTH = 0.15  # step beyond the last point when no sign change is bracketed yet


# --------------------------------------------------------------------------------------------- speeds
def strip_speed(strip_report):
    """The strip theory estimate of the critical speed [m/s]: flutter, else the critical (divergence) speed, else None."""
    r = strip_report or {}
    for key in ("flutter", "critical"):
        v = (r.get(key) or {}).get("speed_m_s")
        if v and v > 0:
            return float(v)
    return None


def plan_speeds(strip_report, n=4, v_max=None):
    """Speeds [m/s, ascending, unique] for the first sweep: 0.7, 0.85, 1.0, 1.15 times the strip flutter speed (for another
    n, evenly from 0.7 to 1.15; n = 1 gives 1.0). Without a strip flutter speed the divergence / critical speed is used,
    and without that 0.5 * v_max. Every speed is capped at v_max (e.g. the Mach limit of the aerodynamics) and duplicates
    produced by the cap are removed, so fewer than n speeds can come back."""
    base = strip_speed(strip_report)
    if base is None:
        if not v_max or v_max <= 0:
            raise ValueError("no strip theory speed and no v_max: nothing to base the speeds on")
        base = 0.5 * float(v_max)
    n = max(int(n), 1)
    factors = [1.0] if n == 1 else (list(STRIP_FACTORS) if n == 4 else list(np.linspace(0.7, 1.15, n)))
    speeds = []
    for fct in factors:
        v = base * fct
        if v_max and v_max > 0:
            v = min(v, float(v_max))
        v = round(v, 3)
        if v > 0 and all(abs(v - u) > 1e-3 * max(v, u) for u in speeds):
            speeds.append(v)
    return sorted(speeds)


def _ok_points(points):
    pts = [p for p in points if p.get("ok") and p.get("zeta") is not None and math.isfinite(float(p["zeta"]))]
    return sorted(pts, key=lambda p: float(p["U"]))


def next_speed(points, bracket_tol=0.05, v_min=None, v_max=None):
    """The next speed to run [m/s], or None when the flutter speed is bracketed or nothing more can be learned.

    Uses the ok points only. With a sign change of zeta between two neighbouring speeds (the lowest one: flutter is the
    LOWEST speed with negative damping) the next speed is the secant (regula falsi) estimate of the zero crossing, kept
    inside the middle 60 % of the bracket so that a one-sided secant cannot stagnate (it then converges at least
    geometrically, like bisection, and quickly when zeta(U) is nearly linear). The search stops when the bracket is
    narrower than bracket_tol * U. Without a bracket: all points stable -> go up (secant extrapolation of the two highest
    points when zeta falls, else +15 %, at most +25 % at once); all unstable -> go down likewise; None when v_max
    (v_min) is already measured or the step would repeat a speed. One ok point is enough to choose a direction."""
    pts = _ok_points(points)
    lo_lim = float(v_min) if v_min else 0.0
    hi_lim = float(v_max) if v_max else None
    if not pts:
        return None
    U = np.array([float(p["U"]) for p in pts])
    Z = np.array([float(p["zeta"]) for p in pts])
    done = [float(p["U"]) for p in pts]  # (a speed whose run was unreliable may be run again, longer)

    def fresh(v):
        if v is None or v <= lo_lim or (hi_lim is not None and v > hi_lim * (1 + 1e-9)) or v <= 0:
            return None
        return None if any(abs(v - u) <= 1e-3 * max(v, u) for u in done) else float(v)

    # bracket: the lowest adjacent pair with zeta > 0 below and <= 0 above
    for i in range(len(U) - 1):
        if Z[i] > 0 >= Z[i + 1]:
            lo, hi, zl, zh = U[i], U[i + 1], Z[i], Z[i + 1]
            if hi - lo <= bracket_tol * 0.5 * (lo + hi):
                return None
            x = lo - zl * (hi - lo) / (zh - zl)  # secant
            w = hi - lo
            return fresh(min(max(x, lo + 0.2 * w), hi - 0.2 * w))
    if Z[0] <= 0:  # unstable already at the lowest speed: look below
        base = U[0]
        if len(U) > 1 and Z[1] != Z[0] and (Z[1] - Z[0]) * (U[1] - U[0]) > 0:
            x = U[0] - Z[0] * (U[1] - U[0]) / (Z[1] - Z[0])  # crossing from the two lowest points
            step = min(max(base - x, 0.03 * base), 0.25 * base) if x < base else 0.15 * base
        else:
            step = WINDOW_STEP_GROWTH * base
        return fresh(max(base - step, lo_lim * 1.0001 if lo_lim else 0.02 * base))
    base = U[-1]  # stable everywhere measured: look above
    if len(U) > 1 and Z[-2] > Z[-1]:
        x = U[-1] - Z[-1] * (U[-1] - U[-2]) / (Z[-1] - Z[-2])
        step = min(max(x - base, 0.03 * base), 0.25 * base)
    else:
        step = WINDOW_STEP_GROWTH * base
    v = base + step
    if hi_lim is not None:
        v = min(v, hi_lim)
    return fresh(v)


# --------------------------------------------------------------------------------------------- cost
def cost_estimate(dt, f_hz, n_periods, step_wall_s=1.35, par_runs=1, t_settle=0.0, phase_a_step_s=0.2, n_speeds=1):
    """Wall time of one speed point of a coupled run, as an ESTIMATE to be replaced by a measurement.

    Phase A (flow developing with the structure held, t_settle seconds of flow time) costs phase_a_step_s per CFD step;
    phase B (released, coupled, n_periods of the mode of f_hz) costs step_wall_s per step, as measured (1.35 s per step
    of 7e-5 s on the reference machine). steps = (t_settle + n_periods / f_hz) / dt.

    Runs in parallel share the cores. ASSUMPTION (to be measured): the wall time per step grows like
    (1 + 0.3 * (par_runs - 1)) and the throughput is par_runs speeds at a time, so the wall time per speed is
    steps * s * (1 + 0.3 (par_runs - 1)) / par_runs. Returns {"steps" (per run), "steps_phase_a", "steps_phase_b",
    "wall_s" (per speed point, with the assumption above), "wall_h", "batch_wall_s" (time for par_runs simultaneous
    runs), "sweep_wall_s" (n_speeds points), "note"}."""
    if dt <= 0 or f_hz <= 0:
        raise ValueError("dt and f_hz must be positive")
    p = max(int(par_runs), 1)
    steps_a = int(math.ceil(max(float(t_settle), 0.0) / dt))
    steps_b = int(math.ceil((float(n_periods) / float(f_hz)) / dt))
    slow = 1.0 + 0.3 * (p - 1)
    batch = (steps_a * phase_a_step_s + steps_b * step_wall_s) * slow
    wall = batch / p
    return {"steps": steps_a + steps_b, "steps_phase_a": steps_a, "steps_phase_b": steps_b, "wall_s": float(wall),
            "wall_h": float(wall / 3600.0), "batch_wall_s": float(batch), "sweep_wall_s": float(wall * max(int(n_speeds), 1)),
            "note": ("Estimate from the measured cost per step; the slowdown of parallel runs "
                     f"(1 + 0.3 per extra run, {p} run{'s' if p > 1 else ''}) is an assumption to be measured.")}


# --------------------------------------------------------------------------------------------- flutter speed
def fit_flutter_speed(points):
    """Flutter speed from a weighted linear regression of zeta on U over the ok points (weights 1 / sigma^2).
    The zero crossing U0 = -a / b of zeta = a + b U; its uncertainty by error propagation of the covariance of (a, b)
    (scaled by the reduced chi-square when it exceeds 1, i.e. when the points scatter more than their sigmas say).
    Returns {"speed_m_s", "sigma_m_s", "bracket": [lo, hi] or None, "basis": "interpolated|extrapolated|none"}:
    bracket = the lowest neighbouring ok speeds with a sign change; basis = interpolated when U0 lies inside the measured
    speed range, extrapolated outside, none when there are fewer than two ok speeds or zeta does not fall with U."""
    none = {"speed_m_s": None, "sigma_m_s": None, "bracket": None, "basis": "none"}
    pts = _ok_points(points)
    if len(pts) < 2:
        return none
    U = np.array([float(p["U"]) for p in pts])
    Z = np.array([float(p["zeta"]) for p in pts])
    S = np.maximum(np.array([float(p.get("sigma") or 0.0) for p in pts]), SIGMA_FLOOR)
    if np.ptp(U) <= 0:
        return none
    bracket = None
    for i in range(len(U) - 1):
        if Z[i] > 0 >= Z[i + 1]:
            bracket = [float(U[i]), float(U[i + 1])]
            break
    X = np.column_stack([np.ones_like(U), U])
    W = 1.0 / S ** 2
    A = X.T @ (X * W[:, None])
    cov = np.linalg.inv(A)
    (a, b) = cov @ (X.T @ (W * Z))
    dof = len(U) - 2
    if dof > 0:
        chi2 = float(np.sum(W * (Z - a - b * U) ** 2) / dof)
        cov = cov * max(chi2, 1.0)
    if not b < -1e-9:  # damping must fall with speed
        return {**none, "bracket": bracket}
    u0 = -a / b
    if not (u0 > 0 and math.isfinite(u0)):
        return {**none, "bracket": bracket}
    g = np.array([-1.0 / b, a / b ** 2])
    sig = float(math.sqrt(max(g @ cov @ g, 0.0)))
    basis = "interpolated" if U.min() <= u0 <= U.max() else "extrapolated"
    return {"speed_m_s": float(u0), "sigma_m_s": sig, "bracket": bracket, "basis": basis}


# --------------------------------------------------------------------------------------------- comparison with strip theory
def _strip_curves(strip_report):
    sw = (strip_report or {}).get("sweep") or {}
    grid = np.asarray(sw.get("speed_m_s") or [], float)
    roots = []
    for r in sw.get("roots") or []:
        f = np.asarray(r.get("frequency_Hz") or [], float)
        z = np.asarray(r.get("damping_percent") or [], float) / 100.0
        if len(f) == len(grid) and len(z) == len(grid) and len(grid) > 1:
            roots.append((r.get("label"), f, z))
    return grid, roots


def _entry_critical(e):
    """(zeta, f_Hz, sigma) of a speed entry: its `critical`, or flat zeta / f keys."""
    c = e.get("critical")
    if c:
        return c.get("zeta"), c.get("f_Hz"), c.get("zeta_sigma")
    if e.get("zeta") is not None:
        return e.get("zeta"), e.get("f", e.get("f_Hz")), e.get("sigma")
    return None, None, None


def compare_to_strip(summary_speeds, strip_report):
    """For each speed entry (summary["speeds"] items: {"U", "critical": {"f_Hz", "zeta"}}, or flat {"U", "zeta", "f"}) the
    strip theory damping and frequency at the same speed, taken from the strip root whose frequency is nearest to the
    measured one (interpolated along the strip sweep). Returns [{"U", "zeta", "strip_zeta", "f", "strip_f", "ratio"}] with
    ratio = zeta / strip_zeta (None when the strip damping is below 1e-4 in magnitude); fractions, not percent."""
    grid, roots = _strip_curves(strip_report)
    out = []
    for e in summary_speeds or []:
        z, f, _ = _entry_critical(e)
        if z is None:
            continue
        U = float(e["U"])
        sz = sf = None
        if roots and f is not None:
            best = min(roots, key=lambda r: abs(float(np.interp(U, grid, r[1])) - float(f)))
            sf = float(np.interp(U, grid, best[1]))
            sz = float(np.interp(U, grid, best[2]))
        out.append({"U": U, "zeta": float(z), "strip_zeta": sz, "f": None if f is None else float(f), "strip_f": sf,
                    "ratio": None if sz is None or abs(sz) < 1e-4 else float(z) / sz})
    return out


# --------------------------------------------------------------------------------------------- the summary
def _clean(x):
    """JSON-safe copy: numpy to Python, nan / inf to None."""
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, np.ndarray):
        return _clean(x.tolist())
    if isinstance(x, (np.bool_, bool)):
        return bool(x)
    if isinstance(x, (np.integer, int)):
        return int(x)
    if isinstance(x, (np.floating, float)):
        v = float(x)
        return v if math.isfinite(v) else None
    return x


def _downsample(t, q, labels, limit=MAX_SERIES_POINTS):
    t = np.asarray(t if t is not None else [], float).ravel()
    q = np.asarray(q if q is not None else np.zeros((0, 0)), float)
    q = q.reshape(len(t), -1) if q.size else np.zeros((len(t), 0))
    if len(t) > limit:
        idx = np.unique(np.round(np.linspace(0, len(t) - 1, limit)).astype(int))
        t, q = t[idx], q[idx]
    names = list(labels) if labels else [f"Mode {i + 1}" for i in range(q.shape[1])]
    return {"t": t.tolist(), "q": [q[:, i].tolist() for i in range(q.shape[1])], "labels": names[:q.shape[1]]}


def _verdict(critical, lco):
    if critical is None:
        return "unreliable"
    z, s = critical["zeta"], critical.get("zeta_sigma") or 0.0
    if lco:
        return "lco"
    if abs(z) < s:
        return "neutral"
    return "growing" if z < 0 else "decaying"


def _mode_label(f, modes):
    if not modes or f is None:
        return None
    k = int(np.argmin([abs(float(m["frequency_Hz"]) - f) for m in modes]))
    return f"Mode {modes[k]['index']}"


def speed_points(speeds):
    """Points {"U","zeta","sigma","ok"} of the summary speed entries (their critical poles) for `next_speed` and
    `fit_flutter_speed`. A speed whose poles are all unreliable is a point with ok False and zeta None."""
    pts = []
    for e in speeds:
        c = e.get("critical")
        if c:
            pts.append({"U": float(e["U"]), "zeta": c["zeta"], "sigma": c.get("zeta_sigma"), "ok": True})
        else:
            pts.append({"U": float(e["U"]), "zeta": None, "sigma": None, "ok": False})
    return pts


def build_summary(strip_report, modes, speeds, settings, mesh_cells, warnings, assumptions, seconds, status=None):
    """The `solve.summary` of a flutter_cfd study (frozen schema, JSON-serialisable).

    strip_report: the strip theory report (flutter, sweep) or None. modes: [{"index", "frequency_Hz",
    "generalized_mass_kg"}]. speeds: one dict per speed run with "U", and optionally "mach", "state", "t_release",
    "duration_s", "n_exchanges", "wall_s", "lco" (the caller saw the amplitude saturate), "t" (nt,), "q" (nt, m) the
    modal coordinates after the release, "labels", and "estimate" = the dict returned by `flutter_damping.estimate`
    (or ready-made "poles"). Per speed the poles get a mode label (the nearest mode), `critical` is the ok pole with the
    lowest zeta (None when none is ok) and `verdict` is unreliable (no ok pole) / lco (flag) / neutral (|zeta| < sigma)
    / growing (zeta < -sigma) / decaying. series: {"t", "q": one list per mode, "labels"}, at most 600 points.
    damping_curve and flutter come from the ok critical poles (`fit_flutter_speed`, `compare_to_strip`); flutter
    vs_strip_percent = 100 (V - V_strip) / V_strip. status "complete" unless a speed is not in state complete / done
    or `status` is given."""
    strip = strip_report or {}
    sf = strip.get("flutter") or {}
    modes_out = []
    for i, m in enumerate(modes or []):
        modes_out.append({"index": int(m.get("index", i + 1)), "frequency_Hz": m.get("frequency_Hz"),
                          "generalized_mass_kg": m.get("generalized_mass_kg", m.get("generalised_mass_kg", m.get("mass_kg")))})
    sp_out = []
    for e in speeds or []:
        poles_in = (e.get("estimate") or {}).get("poles") if e.get("estimate") else e.get("poles")
        poles = []
        for p in poles_in or []:
            if p.get("zeta") is None:
                continue
            poles.append({"f_Hz": p["f_Hz"], "zeta": p["zeta"], "zeta_sigma": p.get("zeta_sigma"),
                          "mode_label": p.get("mode_label") or _mode_label(p.get("hint_Hz", p["f_Hz"]), modes_out),
                          "ok": bool(p.get("ok")), "spread": p.get("spread")})
        good = [p for p in poles if p["ok"]]
        crit = min(good, key=lambda p: p["zeta"]) if good else None
        critical = None if crit is None else {"f_Hz": crit["f_Hz"], "zeta": crit["zeta"], "zeta_sigma": crit["zeta_sigma"]}
        sp_out.append({
            "U": float(e["U"]), "mach": e.get("mach"), "state": e.get("state", "complete"), "t_release": e.get("t_release"),
            "duration_s": e.get("duration_s"), "n_exchanges": e.get("n_exchanges"), "wall_s": e.get("wall_s"),
            "poles": poles, "critical": critical, "verdict": _verdict(critical, bool(e.get("lco"))),
            "series": _downsample(e.get("t"), e.get("q"), e.get("labels"))})
    sp_out.sort(key=lambda s: s["U"])
    cmp_rows = compare_to_strip(sp_out, strip)
    cmp_by_u = {r["U"]: r for r in cmp_rows}
    curve = []
    for s in sp_out:
        if s["critical"] is None:
            continue
        r = cmp_by_u.get(s["U"], {})
        curve.append({"U": s["U"], "zeta": s["critical"]["zeta"], "sigma": s["critical"]["zeta_sigma"],
                      "strip_zeta": r.get("strip_zeta"), "f": s["critical"]["f_Hz"], "strip_f": r.get("strip_f")})
    pts = speed_points(sp_out)
    fit = fit_flutter_speed(pts)
    freq = None
    if fit["speed_m_s"] is not None and curve:
        cu = np.array([c["U"] for c in curve])
        cf = np.array([c["f"] for c in curve])
        freq = float(np.interp(fit["speed_m_s"], cu, cf))
    v_strip = sf.get("speed_m_s")
    vs = None
    if fit["speed_m_s"] is not None and v_strip:
        vs = 100.0 * (fit["speed_m_s"] - v_strip) / v_strip
    done = all(str(s["state"]) in ("complete", "done") for s in sp_out) and bool(sp_out)
    summary = {
        "kind": "flutter_cfd",
        "status": status or ("complete" if done else "partial"),
        "strip": {"flutter_speed_m_s": v_strip, "frequency_Hz": sf.get("frequency_Hz"), "sweep": strip.get("sweep")},
        "modes": modes_out,
        "mesh": {"cells": None if mesh_cells is None else int(mesh_cells)},
        "settings": dict(settings or {}),
        "speeds": sp_out,
        "damping_curve": curve,
        "flutter": {"speed_m_s": fit["speed_m_s"], "sigma_m_s": fit["sigma_m_s"], "bracket": fit["bracket"],
                    "frequency_Hz": freq, "basis": fit["basis"], "vs_strip_percent": vs},
        "warnings": list(warnings or []),
        "assumptions": list(assumptions or []),
        "seconds": seconds,
    }
    return _clean(summary)
