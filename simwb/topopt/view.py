"""Looking at an optimised shape: the part of the mesh whose density is above a threshold, as a result set that the 3D view and the cut tools already draw.
No Qt here, so it can be checked on its own."""
from __future__ import annotations

import numpy as np

from ..results import ResultSet, boundary_from_tets


def subset(rs: ResultSet, keep) -> ResultSet:
    """The elements of rs selected by `keep` (a mask or indices) as a result set of their own, with their outside faces: the shape."""
    idx = np.flatnonzero(keep) if np.asarray(keep).dtype == bool else np.asarray(keep, np.int64)
    tets = rs.tets[idx]
    tris, cells = boundary_from_tets(tets)
    fields = {}
    for k, (loc, arr, unit) in rs.fields.items():
        a = np.asarray(arr)
        fields[k] = (loc, a[idx] if loc == "cell" and len(a) == len(rs.tets) else a, unit)
    return ResultSet(rs.nodes, tets, rs.tet_region[idx], rs.regions, fields, tris, cells, np.zeros(len(tris), np.int32), ["shape"])


def live_base(nodes, tets, density) -> ResultSet:
    """A result set holding only the density of a run that is still going (no faces yet): `subset` of it is the shape so far."""
    n = len(tets)
    return ResultSet(nodes, tets, np.zeros(n, np.int32), ["part"], {"Density": ("cell", np.asarray(density, np.float32), "-")},
                     np.zeros((0, 3), np.int64), np.zeros(0, np.int64), np.zeros(0, np.int32), [])


def volumes(nodes, tets) -> np.ndarray:
    p = np.asarray(nodes, float)[tets]
    return np.abs(np.einsum("ij,ij->i", np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]), p[:, 3] - p[:, 0])) / 6.0


def share_above(nodes, tets, density, threshold: float) -> float:
    """The share of the part's volume (so of its mass, for one material) that has a density of at least `threshold`."""
    v = volumes(nodes, tets)
    return float(v[np.asarray(density) >= threshold].sum() / v.sum())
