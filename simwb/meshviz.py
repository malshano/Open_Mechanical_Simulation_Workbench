"""Mesh inspection metrics for a tetrahedral mesh: what each cell looks like and where the cells are spent.

The quality number of the viewer (1 - worst non-orthogonality / 90 degrees) says whether a cell is *shaped* well. A
good mesh is also the right *size* in the right places, with no abrupt size jumps, and with as few cells as the
accuracy needs. These metrics show that:

    size     equivalent edge length (6*sqrt(2)*V)^(1/3) in mm: the edge of the regular tetrahedron of the same volume
    aspect   longest edge / smallest altitude (1.22 for a regular tetrahedron; flat or needle cells are large)
    growth   largest size ratio to a neighbouring cell (1 = same size; a smooth mesh stays below about 1.5)
    volume   cell volume in mm^3
    quality  the viewer's shape quality (from the project's stored mesh)

and `summary` adds the "cell budget": for every size band, the share of cells and the share of the fluid volume, so one
sees at a glance which sizes consume the cells (and therefore the memory). Wall layers are added at solve time and
are not in this mesh.
"""
from __future__ import annotations

import numpy as np

METRICS = {
    # name: (label, unit, higher_is_worse, display range rule)
    "quality": ("Cell quality", "-", False),
    "size": ("Cell size", "mm", None),
    "aspect": ("Aspect ratio", "-", True),
    "growth": ("Size jump to neighbours", "-", True),
    "volume": ("Cell volume", "mm^3", None),
}

_FACES = np.array([[1, 2, 3], [0, 3, 2], [0, 1, 3], [0, 2, 1]])


def _vol_and_edges(p: np.ndarray, t: np.ndarray):
    a, b, c, d = (p[t[:, i]].astype(np.float64) for i in range(4))
    vol = np.abs(np.einsum("ij,ij->i", np.cross(b - a, c - a), d - a)) / 6.0
    pairs = [(a, b), (a, c), (a, d), (b, c), (b, d), (c, d)]
    e = np.stack([np.linalg.norm(q - r, axis=1) for q, r in pairs], axis=1)
    area = np.stack([np.linalg.norm(np.cross(q - r, s - r), axis=1) / 2 for q, r, s in
                     ((b, c, d), (a, c, d), (a, b, d), (a, b, c))], axis=1)
    return vol, e, area


def neighbour_pairs(tets: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Pairs of tetrahedra that share a face: (cell i, cell j) arrays."""
    n = len(tets)
    f = np.sort(tets[:, _FACES].reshape(-1, 3).astype(np.int64), axis=1)
    owner = np.repeat(np.arange(n), 4)
    if int(f.max(initial=0)) < (1 << 21):
        key = (f[:, 0] << 42) | (f[:, 1] << 21) | f[:, 2]            # one integer per face: a fast, small sort
        order = np.argsort(key, kind="stable")
        ks = key[order]
    else:
        order = np.lexsort((f[:, 2], f[:, 1], f[:, 0]))
        fs = f[order]
        ks = np.concatenate([[True], np.any(fs[1:] != fs[:-1], axis=1)]).cumsum()
    same = ks[1:] == ks[:-1]
    i = owner[order][:-1][same]
    j = owner[order][1:][same]
    return i, j


def cell_metrics(nodes: np.ndarray, tets: np.ndarray, which=("size", "aspect", "growth", "volume")) -> dict:
    """Per-cell arrays (float32), lengths in mm (the mesh is in metres)."""
    out = {}
    vol, e, area = _vol_and_edges(nodes, tets)
    size = (6.0 * np.sqrt(2.0) * vol) ** (1.0 / 3.0)
    if "size" in which or "growth" in which:
        out["size"] = (size * 1000.0).astype(np.float32)
    if "volume" in which:
        out["volume"] = (vol * 1e9).astype(np.float32)
    if "aspect" in which:
        altitude = 3.0 * vol / np.maximum(area.max(1), 1e-300)        # the smallest altitude is over the largest face
        out["aspect"] = (e.max(1) / np.maximum(altitude, 1e-300)).astype(np.float32)
    if "growth" in which:
        i, j = neighbour_pairs(tets)
        g = np.ones(len(tets), np.float32)
        s = np.maximum(size, 1e-300)
        r = (s[j] / s[i]).astype(np.float32)
        r = np.maximum(r, 1.0 / r)
        np.maximum.at(g, i, r)
        np.maximum.at(g, j, r)
        out["growth"] = g
    return {k: v for k, v in out.items() if k in which}


def _stats(a: np.ndarray) -> dict:
    a = a[np.isfinite(a)]
    if not len(a):
        return {}
    p = np.percentile(a, [1, 50, 99])
    return {"min": float(a.min()), "mean": float(a.mean()), "p01": float(p[0]), "median": float(p[1]),
            "p99": float(p[2]), "max": float(a.max())}


def display_range(metric: str, a: np.ndarray) -> list:
    """A colour range that is not stretched by a few extreme cells."""
    if metric == "quality":
        return [0.0, 1.0]
    lo, hi = np.percentile(a, [2, 98])
    if metric == "growth":
        return [1.0, float(max(hi, 1.5))]
    if metric == "aspect":
        return [float(min(lo, 1.5)), float(max(hi, 3.0))]
    return [float(lo), float(max(hi, lo * 1.01 + 1e-12))]


def summary(nodes: np.ndarray, tets: np.ndarray, quality: np.ndarray | None = None, bins: int = 20) -> dict:
    """Statistics, histograms and the cell budget of the mesh."""
    m = cell_metrics(nodes, tets)
    if quality is not None:
        m["quality"] = np.asarray(quality, np.float32)
    out = {"cells": int(len(tets)), "metrics": {}}
    for name, a in m.items():
        label, unit, worse = METRICS[name]
        s = _stats(a)
        if name == "quality":
            h, ed = np.histogram(a, bins=bins, range=(0, 1))
        elif name == "growth":
            h, ed = np.histogram(a, bins=bins, range=(1.0, max(1.5, float(np.percentile(a, 99.9)))))
        else:                                                           # sizes, volumes and aspect ratios: log-spaced
            lo, hi = max(float(a.min()), 1e-12), max(float(a.max()), 1e-12 * 1.0001)
            ed = np.geomspace(lo, hi, bins + 1) if hi > lo else np.array([lo, lo * 1.0001])
            h, ed = np.histogram(a, bins=ed)
        out["metrics"][name] = {"label": label, "unit": unit, "higher_is_worse": worse, **s,
                                "histogram": h.tolist(), "edges": [float(x) for x in ed]}
    # the cell budget: size bands a factor 1.5 apart (about every second band is a visible change of the cell size)
    size, vol = m["size"], m["volume"].astype(np.float64)
    lo = float(size.min())
    band = np.floor(np.log(np.maximum(size, lo) / lo) / np.log(1.5)).astype(int)
    nb = int(band.max()) + 1
    cells = np.bincount(band, minlength=nb)
    volume = np.bincount(band, weights=vol, minlength=nb)
    out["budget"] = [{"size_from": round(lo * 1.5 ** k, 4), "size_to": round(lo * 1.5 ** (k + 1), 4),
                      "cells": int(cells[k]), "cells_percent": round(100.0 * cells[k] / len(tets), 2),
                      "volume_percent": round(100.0 * volume[k] / max(volume.sum(), 1e-300), 2)}
                     for k in range(nb) if cells[k]]
    g = m["growth"]
    out["findings"] = findings(m, len(tets))
    out["growth_over_2_percent"] = round(100.0 * float((g > 2.0).mean()), 3)
    return out


def findings(m: dict, n: int) -> list[str]:
    """Plain-language remarks about what the metrics show (empty when nothing stands out)."""
    f = []
    g, asp, size = m["growth"], m["aspect"], m["size"]
    if (g > 2.0).mean() > 0.01:
        f.append(f"{100 * float((g > 2.0).mean()):.1f} % of the cells have a neighbour more than twice as large or small: "
                 "an abrupt size change costs accuracy; lower the growth between the fine and the coarse regions.")
    if (asp > 20).mean() > 0.005:
        f.append(f"{int((asp > 20).sum()):,} cells have an aspect ratio above 20 (slivers or needles).")
    top = float(np.percentile(size, 99))
    if top > 0:
        small = float(np.median(size))
        share_fine = float((size < 0.5 * small).mean())
        if share_fine > 0.4:
            f.append(f"{100 * share_fine:.0f} % of the cells are less than half the median size ({small:.2f} mm): "
                     "the fine regions use most of the cells, check they are needed.")
    return f
