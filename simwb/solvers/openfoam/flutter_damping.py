"""Damping of the released modal motion of a CFD-coupled flutter run, with an honest statement of how far to trust it.

A coupled run costs hours (every exchange is one CFD time step), so the estimator must not only return a damping ratio:
it must say when the number is not yet reliable and why ("only 2.7 periods: run longer"). A first 0.15 s run of
< 3 periods gave 2.7 % where two other estimators said 17 %: a short record cannot give a damping ratio, whatever the
algorithm, because the decay over the record is a small fraction of the amplitude.

Signal model. After the release every modal coordinate is a sum of damped oscillations (the structural modes, slightly
changed by the air) plus a slow drift (the steady load changes a little while the structure moves) plus flow noise:

    q(t) = sum_k  A_k exp(-s_k t) cos(w_k t + phi_k)  +  c0 + c1 t + c2 t^2  +  noise ,     zeta_k = s_k / sqrt(s_k^2 + w_k^2)

s_k > 0 decays; s_k < 0 grows (flutter). w_k is the damped angular frequency, so zeta = s / |pole| with |pole| the
undamped frequency.

Procedure for every frequency hint (the structural mode frequencies the caller expects):
  1. The record is put on a uniform time grid (the exchanges are not exactly evenly spaced) and band-passed around the
     hint (zero-phase Butterworth, hint * (1 +- band)). Hints whose bands overlap are fitted together, as one cluster.
  2. Variable projection: for given (s_k, w_k) the amplitudes (cos and sin coefficient of every mode, in every modal
     coordinate, and the drift polynomial) are linear, so the nonlinear least squares runs in (s_k, w_k) only. The start
     is the matrix pencil (`flutter_cfd.fit_poles`) restricted to the band, plus fall-back starts from the spectrum.
     The drift order (0..2) is chosen by an information criterion.
  3. Uncertainty: covariance of the full Gauss-Newton Jacobian, the residual variance scaled by the number of
     INDEPENDENT samples. A band-passed record has only about 2 * bandwidth * duration independent samples (3 to 6 for
     8 periods), not hundreds, and the residual is oscillatory; the correlation length is therefore taken from the
     autocorrelation of the DEMODULATED residual (the lag-1 correlation of an oscillation is near cos(w dt) and says
     nothing about the information content).
  3b. Window jackknife: the fit is repeated on the first 60 / 80 / 100 % of the record; spread = max - min of zeta. A
     number that still moves when the record grows is not converged.
  4. The matrix pencil value of the same band is compared with the fit.

A pole is `ok` only if ALL hold: n_periods >= n_periods_min; the fitted frequency is within 15 % of the hint; zeta_sigma
< 0.01 (absolute); fit and matrix pencil agree within max(0.01, 0.3 |zeta|); spread < 0.01; the spectrum has a clear peak
at the hint (a frequency that is not there is never accepted as the pole of a hint); the fit does not sit at the edge of
the band. Otherwise `ok` is False and `reasons` lists, in words, what to do.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.optimize import least_squares
from scipy.signal import butter, hilbert, sosfiltfilt

from .flutter_cfd import fit_poles

OK_SIGMA = 0.01  # zeta_sigma limit (absolute)
OK_SPREAD = 0.01  # window jackknife limit (absolute)
OK_AGREE_ABS = 0.01  # |zeta_fit - zeta_pencil| <= max(this, 0.3 |zeta|)
OK_FREQ_TOL = 0.15  # fitted frequency within this fraction of the hint
SIGMA_SAFETY = 1.5  # zeta_sigma = this x the covariance estimate: calibrated on synthetic records (z-score std 1.2-1.7 without)
OK_PEAK_RATIO = 10.0  # spectral peak at the hint over the median background
WINDOWS = (0.6, 0.8, 1.0)
METHOD = ("band-pass + damped-sinusoid variable projection (matrix-pencil start), drift polynomial, "
          "demodulated-residual effective sample size, window jackknife 60/80/100 %")


# --------------------------------------------------------------------------------------------- helpers
def _clean_input(t, q):
    t = np.asarray(t, float).ravel()
    q = np.asarray(q, float)
    q = q[:, None] if q.ndim == 1 else q
    if len(q) != len(t):
        raise ValueError("t and q must have the same number of samples")
    ok = np.isfinite(t) & np.isfinite(q).all(axis=1)
    t, q = t[ok], q[ok]
    order = np.argsort(t, kind="stable")
    t, q = t[order], q[order]
    if len(t) == 0:
        return t, q
    keep = np.concatenate([[True], np.diff(t) > 0])
    return t[keep], q[keep]


def _uniform(t, Y, dt):
    n = int(math.floor((t[-1] - t[0]) / dt + 1e-9)) + 1
    tg = t[0] + dt * np.arange(n)
    return tg, np.column_stack([np.interp(tg, t, Y[:, i]) for i in range(Y.shape[1])])


class _Band:
    """The zero-phase band-pass as a LINEAR operator F. The data AND the model are filtered with the same F, so the
    edges of the record (where a filter always distorts a signal that starts abruptly at the release) bias nothing:
    the fit minimises |F y - F model(p)|."""

    def __init__(self, f_lo, f_hi, dt, n):
        nyq = 0.5 / dt
        hi = min(f_hi, 0.9 * nyq)
        if not f_lo < hi:
            raise ValueError("the sampling is too coarse for this frequency")
        self.sos = butter(3, [f_lo / nyq, hi / nyq], btype="band", output="sos")
        self.padlen = max(min(3 * (2 * len(self.sos) + 1), n - 2), 1)

    def __call__(self, X):
        return sosfiltfilt(self.sos, X, axis=0, padlen=self.padlen)


def _prepare(t, Y, f_lo, f_hi, f_top):
    """Uniform grid, then block averages of 8..20 samples per period of f_top (at most ~1000 samples). A block average
    of exp(s t) is exp(s t_centre) times a constant, so it stays inside the model class (no bias, unlike a decimating
    filter) and it suppresses what lies above the working Nyquist. Returns the working time (from 0), the working data
    (not band-passed), the band operator, the fine-grid raw data and its step."""
    T = t[-1] - t[0]
    dt_med = float(np.median(np.diff(t)))
    dt_f = max(dt_med, T / 40000.0)
    tf, Yf = _uniform(t, Y, dt_f)
    dt_g = min(max(dt_f, 1.0 / (20.0 * f_top), T / 1000.0), 1.0 / (8.0 * f_top))
    k = max(1, int(dt_g / dt_f))
    nb = len(tf) // k
    Yd = Yf[:nb * k].reshape(nb, k, -1).mean(axis=1)
    tg = tf[:nb * k].reshape(nb, k).mean(axis=1) - tf[0]
    return tg, Yd, _Band(f_lo, f_hi, dt_f * k, nb), Yf, dt_f, dt_f * k


class _Model:
    """Working grid tau = w_ref t, the band operator F and the filtered drift basis (columns that the filter removes
    entirely are dropped: a slow drift outside the band is not a parameter of the fit)."""

    def __init__(self, tau, F):
        self.tau, self.F = tau, F
        self._drift = {}

    def drift(self, d):
        if d not in self._drift:
            x = (self.tau - self.tau[0]) / max(self.tau[-1] - self.tau[0], 1e-30)
            Dm = self.F(np.column_stack([x ** j for j in range(d + 1)]))
            nrm = np.linalg.norm(Dm, axis=0)
            self._drift[d] = Dm[:, nrm > 1e-3 * np.sqrt(len(x))]
        return self._drift[d]

    def design(self, p, d):
        """Filtered design matrix: columns cos_k, sin_k for the modes p = [sigma_1, nu_1, ...], then the drift."""
        cols = []
        for k in range(len(p) // 2):
            e = np.exp(-p[2 * k] * self.tau)
            cols += [e * np.cos(p[2 * k + 1] * self.tau), e * np.sin(p[2 * k + 1] * self.tau)]
        return np.column_stack([self.F(np.column_stack(cols)), self.drift(d)])


def _vp_residual(p, mdl, Y, d):
    B = mdl.design(p, d)
    C, *_ = np.linalg.lstsq(B, Y, rcond=None)
    return (Y - B @ C).ravel()


def _fit(mdl, Y, starts, lo, hi, d):
    """Best of several starts of the variable projection fit. Returns (p, rss) or None."""
    best = None
    for p0 in starts:
        p0 = np.clip(p0, lo + 1e-9, hi - 1e-9)
        try:
            r = least_squares(_vp_residual, p0, bounds=(lo, hi), args=(mdl, Y, d), x_scale=np.maximum(np.abs(p0), 0.05),
                              xtol=1e-12, ftol=1e-12, gtol=1e-12, max_nfev=200)
        except Exception:  # noqa: BLE001 - a start that fails is only a start
            continue
        if best is None or r.cost < best[1]:
            best = (r.x, r.cost)
    return None if best is None else (best[0], 2.0 * best[1])


def _tau_int(R, nu_c):
    """Integrated autocorrelation time (in samples) of the residual after demodulation at the carrier nu_c [rad per
    sample]: 1 for white noise, N/3 at most. (The lag-1 correlation of an oscillating residual is near cos(nu_c) and
    says nothing about how many independent samples there are; the demodulated residual is a slowly varying
    envelope whose correlation time is 1 / bandwidth.)"""
    N = len(R)
    z = hilbert(R, axis=0) * np.exp(-1j * nu_c * np.arange(N))[:, None]
    z = z - z.mean(axis=0)
    den = float(np.sum(np.abs(z) ** 2))
    if den <= 0:
        return 1.0
    tot = 1.0
    for j in range(1, N // 3):
        rho = float(np.real(np.sum(z[:-j] * np.conj(z[j:]))) / den) * N / (N - j)
        if rho <= 0:
            break
        tot += 2.0 * rho
    return min(max(tot, 1.0), N / 3.0)


def _covariance(mdl, Y, p, d, tau_int):
    """Covariance of (sigma_k, nu_k) from the full Jacobian; the residual variance is scaled by tau_int (sample
    correlation) and by the degrees of freedom left (N_eff - parameters). Returns (cov (2K,2K), info dict)."""
    K = len(p) // 2
    tau = mdl.tau
    N, c = Y.shape
    B = mdl.design(p, d)
    C, *_ = np.linalg.lstsq(B, Y, rcond=None)
    R = Y - B @ C
    rss = float(np.sum(R ** 2))
    nb = B.shape[1]
    P = 2 * K + nb * c
    dcols = np.zeros((N, c * 2 * K))  # d(model)/d(sigma_k), d(model)/d(nu_k) of column ci, before the filter
    for ci in range(c):
        for k in range(K):
            s, nu = p[2 * k], p[2 * k + 1]
            e = np.exp(-s * tau)
            cs, sn = np.cos(nu * tau), np.sin(nu * tau)
            a, b = C[2 * k, ci], C[2 * k + 1, ci]
            dcols[:, ci * 2 * K + 2 * k] = -tau * e * (a * cs + b * sn)
            dcols[:, ci * 2 * K + 2 * k + 1] = tau * e * (-a * sn + b * cs)
    dcols = mdl.F(dcols)
    J = np.zeros((N * c, P))
    for ci in range(c):
        rows = slice(ci * N, (ci + 1) * N)
        J[rows, :2 * K] = dcols[:, ci * 2 * K:(ci + 1) * 2 * K]
        J[rows, 2 * K + ci * nb:2 * K + (ci + 1) * nb] = B
    n_all = N * c
    n_eff = max(n_all / tau_int, 1.0)
    dof = max(n_eff - P, 1.0)
    sigma2 = rss / n_all * tau_int * max(1.0, n_eff / dof)
    sc = np.linalg.norm(J, axis=0)
    sc[sc == 0] = 1.0
    Jn = J / sc
    cov = sigma2 * (np.linalg.pinv(Jn.T @ Jn, rcond=1e-12) / np.outer(sc, sc))
    return cov[:2 * K, :2 * K], {"rss": rss, "n_eff": n_eff, "sigma2": sigma2, "C": C, "P": P}


def _zeta(sig, nu):
    return sig / math.hypot(sig, nu)


def _zeta_sigma(cov_k, sig, nu):
    r3 = math.hypot(sig, nu) ** 3
    g = np.array([nu * nu / r3, -sig * nu / r3])
    return SIGMA_SAFETY * float(math.sqrt(max(g @ cov_k @ g, 0.0)))


def _peak_ratio(Yf, dt_f, hints, band):
    """Spectral peak near each hint over the median background (the background excludes all hint bands)."""
    n = len(Yf)
    u = np.linspace(0.0, 1.0, n)
    X = np.empty_like(Yf)
    for i in range(Yf.shape[1]):
        X[:, i] = Yf[:, i] - np.polyval(np.polyfit(u, Yf[:, i], 2), u)
    X = X * np.hanning(n)[:, None]
    nfft = 1 << int(math.ceil(math.log2(max(n, 8) * 4)))
    nfft = min(nfft, 1 << 19)
    P = (np.abs(np.fft.rfft(X, nfft, axis=0)) ** 2).sum(axis=1)
    f = np.fft.rfftfreq(nfft, dt_f)
    in_any = np.zeros(len(f), bool)
    for h in hints:
        in_any |= np.abs(f - h) <= band * h
    ref = (f >= 0.25 * min(hints)) & (f <= 4.0 * max(hints)) & ~in_any
    out = []
    for h in hints:
        pk = P[np.abs(f - h) <= OK_FREQ_TOL * h]
        if len(pk) == 0 or not ref.any():
            out.append(float("inf"))
        else:
            out.append(float(pk.max() / max(np.median(P[ref]), 1e-300)))
    return out


def _clusters(hints, band):
    order = np.argsort(hints)
    groups, cur = [], [int(order[0])]
    for i in order[1:]:
        if hints[int(i)] * (1 - band) <= hints[cur[-1]] * (1 + band):
            cur.append(int(i))
        else:
            groups.append(cur)
            cur = [int(i)]
    groups.append(cur)
    return groups


def _fail(h, reason, n_periods=0.0):
    return {"f_Hz": float(h), "zeta": None, "zeta_sigma": None, "spread": None, "n_periods": float(n_periods), "ok": False,
            "reasons": [reason], "hint_Hz": float(h), "zeta_pencil": None, "f_pencil_Hz": None}


# --------------------------------------------------------------------------------------------- the estimator
def _fit_window(t, Y, hints, band, w_ref, starts_from=None, d_fixed=None, n_other=0):
    """One band-pass + variable projection fit of the record (t, Y) for the hints of one cluster.
    Returns dict(sig, nu, cov, info, pencil, drift_order, edge, n_samples, cols)."""
    K = len(hints)
    f_lo, f_hi, f_top = min(hints) * (1 - band), max(hints) * (1 + band), max(hints)
    tg, Yd, F, Yf, dt_f, dt_g = _prepare(t, Y, f_lo, f_hi, f_top)
    Yb = F(Yd)  # band-passed data; the model is filtered the same way
    N = len(tg)
    if N < 12:
        raise ValueError("too few samples in the record")
    rms = np.sqrt(np.mean(Yb ** 2, axis=0))
    if rms.max() <= 0:
        raise ValueError("no signal in the frequency band")
    use = np.argsort(-rms)[:3]
    use = np.array([i for i in use if rms[i] >= 0.3 * rms.max()])  # weaker columns are mostly leakage of other modes
    Yn = Yb[:, use] / rms[use]
    mdl = _Model(tg * w_ref, F)
    tau = mdl.tau
    # matrix pencil, restricted to the band: on the band-passed data it gives the start of the fit (the band-pass
    # distorts the first and last periods, so that value is only a start); on the raw data (order 2 x hints + 2 for a drift) it
    # is the independent second opinion that the fit is compared with
    def pencil(Z, order):
        try:
            s = fit_poles(tg, Z, order=order)
            found, used = [], set()
            for h in hints:
                cand = [(abs(sp.imag / (2 * math.pi) - h), j, sp) for j, sp in enumerate(s)
                        if abs(sp.imag / (2 * math.pi) - h) <= band * h and j not in used]
                if cand:
                    c = min(cand, key=lambda x: x[0])
                    used.add(c[1])
                    found.append(c[2])
                else:
                    found.append(None)
            return found
        except Exception:  # noqa: BLE001
            return [None] * K

    pen = pencil(Yn, 2 * K)
    Yr = Yd[:, use] / rms[use]
    pen_cmp = pencil(Yr - Yr.mean(axis=0), 2 * (K + n_other) + 2)
    # fall-back start: spectral peak of the band-passed data
    nfft = 1 << int(math.ceil(math.log2(N * 8)))
    Pw = (np.abs(np.fft.rfft(Yn * np.hanning(N)[:, None], nfft, axis=0)) ** 2).sum(axis=1)
    fr = np.fft.rfftfreq(nfft, dt_g)
    peak = []
    for h in hints:
        m = np.abs(fr - h) <= band * h
        peak.append(float(fr[m][np.argmax(Pw[m])]) if m.any() else h)
    nu_h = np.array([2 * math.pi * h / w_ref for h in hints])
    lo = np.ravel([[-0.8 * v, v * (1 - band)] for v in nu_h])
    hi = np.ravel([[0.8 * v, v * (1 + band)] for v in nu_h])

    def start(kind):
        p = []
        for k in range(K):
            if kind == "pencil" and pen[k] is not None:
                p += [-pen[k].real / w_ref, pen[k].imag / w_ref]
            elif kind == "prev" and starts_from is not None:
                p += [starts_from[2 * k], starts_from[2 * k + 1]]
            else:
                p += [{"peak": 0.02, "peak2": 0.1, "peak3": 0.0}.get(kind, 0.03) * 2 * math.pi * peak[k] / w_ref,
                      2 * math.pi * peak[k] / w_ref]
        return np.array(p)

    starts = []
    if starts_from is not None:
        starts.append(start("prev"))
    else:
        if all(x is not None for x in pen):
            starts.append(start("pencil"))
        starts += [start("peak"), start("peak2")]

    # drift order by BIC on the effective sample size of the order-0 residual
    best = None
    orders = [d_fixed] if d_fixed is not None else [0, 1, 2]
    n_eff0 = None
    for d in orders:
        r = _fit(mdl, Yn, starts if best is None else [best[2]], lo, hi, d)  # the higher orders start from the best fit so far
        if r is None:
            continue
        p, rss = r
        if n_eff0 is None:
            Bd = mdl.design(p, d)
            Cd, *_ = np.linalg.lstsq(Bd, Yn, rcond=None)
            nu_c = float(np.mean(p[1::2])) * (tau[1] - tau[0])
            n_eff0 = max(Yn.size / _tau_int(Yn - Bd @ Cd, nu_c), 4.0)
        k_par = len(p) + (2 * K + d + 1) * Yn.shape[1]
        bic = n_eff0 * math.log(max(rss / Yn.size, 1e-300)) + k_par * math.log(n_eff0)
        if best is None or bic < best[0]:
            best = (bic, d, p)
    if best is None:
        raise ValueError("the fit did not converge")
    _, d, p = best
    B = mdl.design(p, d)
    C, *_ = np.linalg.lstsq(B, Yn, rcond=None)
    nu_c = float(np.mean(p[1::2])) * (tau[1] - tau[0])
    ti = _tau_int(Yn - B @ C, nu_c)
    cov, info = _covariance(mdl, Yn, p, d, ti)
    edge = [bool(abs(p[2 * k + 1] - lo[2 * k + 1]) < 0.005 * (hi[2 * k + 1] - lo[2 * k + 1]) or
                 abs(p[2 * k + 1] - hi[2 * k + 1]) < 0.005 * (hi[2 * k + 1] - lo[2 * k + 1])) for k in range(K)]
    zp = []
    for k in range(K):
        if pen_cmp[k] is None:
            zp.append((None, None))
        else:
            zp.append((float(-pen_cmp[k].real / abs(pen_cmp[k])), float(pen_cmp[k].imag / (2 * math.pi))))
    pr = _peak_ratio(Yf, dt_f, hints, band)
    return {"p": p, "cov": cov, "info": info, "pencil": zp, "drift_order": d, "edge": edge, "n_samples": N,
            "n_eff": info["n_eff"], "peak": pr, "dt_g": dt_g}


def estimate(t, q, f_hints_hz, n_periods_min=6, band=0.2):
    """Damping ratio of the released motion. t (nt,) [s] (not necessarily uniform), q (nt,) or (nt, m) modal coordinates
    after the release, f_hints_hz the structural frequencies to look at [Hz], n_periods_min the shortest record
    (in periods of the pole) that may be called reliable, band the half width of the band-pass (fraction of the hint).

    Returns {"poles": [one entry per hint, in the order of the hints: {"f_Hz", "zeta" (fraction, negative = growth),
    "zeta_sigma", "spread" (window jackknife), "n_periods", "ok", "reasons": [str], "hint_Hz", "zeta_pencil",
    "f_pencil_Hz"}], "drift_order": int, "method": str}. When no estimate can be made at all, the entry has f_Hz =
    the hint, zeta = None and the reason."""
    hints = [float(h) for h in np.atleast_1d(f_hints_hz)]
    out = {"poles": [None] * len(hints), "drift_order": 0, "method": METHOD}
    if not hints:
        return out
    t, Y = _clean_input(t, q)
    if len(t) < 8 or t[-1] <= t[0]:
        out["poles"] = [_fail(h, "fewer than 8 samples after the release: run longer") for h in hints]
        return out
    T = float(t[-1] - t[0])
    t0 = t[0]
    t = t - t0
    dt_med = float(np.median(np.diff(t)))
    for grp in _clusters(hints, band):
        hs = [hints[i] for i in grp]
        if T * min(hs) < 1.5:  # less than 1.5 periods of the slowest mode of the group: no meaningful fit (and the filters fail)
            for i in grp:
                out["poles"][i] = _fail(hints[i], f"the record holds only {T * hints[i]:.2f} periods: run longer "
                                        f"(at least {n_periods_min:g} are needed)", T * hints[i])
            continue
        if dt_med * max(hs) > 1.0 / 6.0:
            for i in grp:
                out["poles"][i] = _fail(hints[i], f"only {1 / (dt_med * hints[i]):.1f} samples per period "
                                        "(need 6): exchange more often")
            continue
        w_ref = 2 * math.pi * float(np.mean(hs))
        try:
            full = _fit_window(t, Y, hs, band, w_ref, n_other=len(hints) - len(hs))
        except Exception as e:  # noqa: BLE001
            for i in grp:
                out["poles"][i] = _fail(hints[i], f"no fit possible: {e}", T * hints[i])
            continue
        out["drift_order"] = max(out["drift_order"], int(full["drift_order"]))
        # window jackknife: the first 60 / 80 % of the record (the 100 % fit is `full`)
        zw = {1.0: [_zeta(full["p"][2 * k], full["p"][2 * k + 1]) for k in range(len(hs))]}
        for fr in WINDOWS[:-1]:
            m = t <= fr * T + 1e-12
            if m.sum() < 16 or fr * T * min(hs) < 1.5:
                continue
            try:
                w = _fit_window(t[m], Y[m], hs, band, w_ref, starts_from=full["p"], d_fixed=full["drift_order"],
                                n_other=len(hints) - len(hs))
                zw[fr] = [_zeta(w["p"][2 * k], w["p"][2 * k + 1]) for k in range(len(hs))]
            except Exception:  # noqa: BLE001
                continue
        for k, i in enumerate(grp):
            sig, nu = float(full["p"][2 * k]), float(full["p"][2 * k + 1])
            zeta = _zeta(sig, nu)
            f_fit = nu * w_ref / (2 * math.pi)
            zs = _zeta_sigma(full["cov"][2 * k:2 * k + 2, 2 * k:2 * k + 2], sig, nu)
            vals = [zw[f][k] for f in zw]
            spread = float(max(vals) - min(vals)) if len(vals) > 1 else float("nan")
            n_per = T * f_fit
            zp, fp = full["pencil"][k]
            reasons = []
            if n_per < n_periods_min:
                reasons.append(f"only {n_per:.1f} periods: run longer (at least {n_periods_min:g} periods of {f_fit:.3g} Hz"
                               f" = {n_periods_min / f_fit:.3g} s after the release)")
            if abs(f_fit - hints[i]) > OK_FREQ_TOL * hints[i]:
                reasons.append(f"fitted frequency {f_fit:.3g} Hz is more than {OK_FREQ_TOL * 100:g} % away from the hint "
                               f"{hints[i]:.3g} Hz")
            elif full["edge"][k]:
                reasons.append("the fitted frequency sits at the edge of the search band: the mode is not clearly there")
            if not math.isfinite(zs) or zs >= OK_SIGMA:
                reasons.append(f"uncertainty of the damping ratio is {zs:.3g} (limit {OK_SIGMA:g}): longer record or less noise")
            if zp is None:
                reasons.append("the matrix pencil finds no pole near the hint: no second opinion")
            elif abs(zeta - zp) > max(OK_AGREE_ABS, 0.3 * abs(zeta)):
                reasons.append(f"fit ({zeta:.3g}) and matrix pencil ({zp:.3g}) disagree")
            if not math.isfinite(spread):
                reasons.append("record too short for the window check")
            elif spread >= OK_SPREAD:
                reasons.append(f"the damping ratio changes by {spread:.3g} between 60 / 80 / 100 % of the record: not converged")
            if full["peak"][k] < OK_PEAK_RATIO:
                reasons.append(f"no clear spectral peak near {hints[i]:.3g} Hz (peak/background {full['peak'][k]:.1f}): "
                               "that mode is not visible in this signal")
            out["poles"][i] = {
                "f_Hz": float(f_fit), "zeta": float(zeta), "zeta_sigma": float(zs), "spread": None if not math.isfinite(spread) else spread,
                "n_periods": float(n_per), "ok": not reasons, "reasons": reasons, "hint_Hz": hints[i],
                "zeta_pencil": zp, "f_pencil_Hz": fp}
    return out


# --------------------------------------------------------------------------------------------- release and duration
def excitation(node_model, mode_indices, tip_amp_m, tip_node=-1):
    """Modal release velocities v (m,) [m/s per unit mode] for a single mode or a pair: v_i = omega_i * A / |T_i,tip|
    for the chosen modes, 0 for the others, so that each chosen mode's first swing reaches the tip amplitude A [m]
    (a mode released with velocity v_i moves q_i = v_i / omega_i sin(omega_i t); its tip displacement is |T_i,tip| q_i).
    node_model is `flutter_cfd.NodeModel` (T (m,n,3), omega (m)); tip_node the lumped node index of the tip (-1 = last,
    the span-ordered node farthest from the root)."""
    T = np.asarray(node_model.T, float)
    omega = np.asarray(node_model.omega, float)
    v = np.zeros(len(omega))
    idx = [int(i) for i in np.atleast_1d(mode_indices)]
    if not idx:
        raise ValueError("choose at least one mode to excite")
    for i in idx:
        if not 0 <= i < len(omega):
            raise ValueError(f"mode index {i} is out of range (0..{len(omega) - 1})")
        tip = float(np.linalg.norm(T[i, tip_node]))
        if tip <= 1e-30:
            raise ValueError(f"mode {i} does not move at the tip node: choose another tip node or mode")
        v[i] = omega[i] * float(tip_amp_m) / tip
    return v


def required_duration(f_hints_hz, n_periods=8, t_settle=0.0, f_fast_hz=None):
    """Time to run after the release [s]: 8 periods of the slowest mode of interest, and at least 4 periods of the
    fastest (f_fast_hz, default the highest hint), whichever is longer, plus t_settle (time to let a start-up
    transient die away before the data count). Eight periods is what the estimator needs for zeta_sigma < 0.01 at
    2 % noise; fewer periods are accepted only with n_periods_min lowered by the caller."""
    f = [float(x) for x in np.atleast_1d(f_hints_hz) if float(x) > 0]
    if not f:
        raise ValueError("no frequency to plan for")
    f_fast = float(f_fast_hz) if f_fast_hz else max(f)
    return float(t_settle) + max(float(n_periods) / min(f), 4.0 / f_fast)
