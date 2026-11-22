"""Numbers read off the result drawn in the 3D view (not drawing): the distribution of the field over the surface shown, as the web version's
histogram has it (share of the area per value band, the area-weighted mean, the share below and above the colour range)."""
from __future__ import annotations

import numpy as np


def surface_histogram(ov: dict | None, lo: float, hi: float, n: int = 40) -> dict | None:
    """ov: the drawn surface (`Viewport._ov`: pos (P,3), idx (T,3), val (P or T), per 'face'|'point'). Returns {bins (share of the area in each
    of n bands over lo..hi), mean, below, above} or None when nothing with values is drawn."""
    if not ov or ov.get("val") is None or not len(ov.get("idx", [])):
        return None
    pos, idx, val = np.asarray(ov["pos"], float), np.asarray(ov["idx"], np.int64), np.asarray(ov["val"], float)
    a, b, c = pos[idx[:, 0]], pos[idx[:, 1]], pos[idx[:, 2]]
    area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
    v = val if ov.get("per") == "face" and len(val) == len(idx) else val[idx].mean(1)
    ok = np.isfinite(v) & (area > 0)
    if not ok.any() or not hi > lo:
        return None
    v, area = v[ok], area[ok]
    tot = float(area.sum()) or 1.0
    inside = (v >= lo) & (v <= hi)
    bins = np.histogram(v[inside], bins=n, range=(lo, hi), weights=area[inside])[0] if inside.any() else np.zeros(n)
    return {"bins": (bins / tot).tolist(), "mean": float((v * area).sum() / tot),
            "below": float(area[v < lo].sum() / tot), "above": float(area[v > hi].sum() / tot), "lo": float(lo), "hi": float(hi)}
