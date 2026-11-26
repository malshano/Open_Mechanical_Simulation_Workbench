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


def smooth_surface(verts, tris, fixed, passes: int = 12, lam: float = 0.5, mu: float = -0.53):
    """Taubin smoothing of a triangle surface (it does not shrink it): the nodes in `fixed` (a mask) stay where they are, so the faces that were the part's own
    surface keep their exact place and only the faces the optimiser made are smoothed."""
    import scipy.sparse as sp
    v = np.asarray(verts, float).copy()
    t = np.asarray(tris, np.int64)
    n = len(v)
    e = np.vstack([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
    A = sp.coo_matrix((np.ones(len(e)), (e[:, 0], e[:, 1])), shape=(n, n)).tocsr()
    A = ((A + A.T) > 0).astype(float)
    deg = np.maximum(np.asarray(A.sum(axis=1)).ravel(), 1.0)
    free = ~np.asarray(fixed, bool)
    for k in range(int(passes)):
        step = lam if k % 2 == 0 else mu
        mean = (A @ v) / deg[:, None]
        v[free] += step * (mean - v)[free]
    return v


def write_stl(path, verts, tris, name: str = "shape"):
    """A binary STL of a triangle surface (metres are written as millimetres: the unit CAD and slicers expect)."""
    import struct
    v = np.asarray(verts, float) * 1000.0
    t = np.asarray(tris, np.int64)
    a, b, c = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    nrm = np.cross(b - a, c - a)
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1), 1e-30)[:, None]
    rec = np.zeros(len(t), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    rec["n"], rec["v"][:, 0], rec["v"][:, 1], rec["v"][:, 2] = nrm, a, b, c
    with open(path, "wb") as f:
        f.write(name.encode("ascii", "replace")[:80].ljust(80, b" "))
        f.write(struct.pack("<I", len(t)))
        f.write(rec.tobytes())
    return len(t)


def shape_surface(sub: ResultSet, original: ResultSet, smooth: bool = True):
    """(vertices, triangles outward) of the outside of a shape; the nodes that lie on the part's own surface (`original`) are not moved by the smoothing."""
    tris = np.asarray(sub.surf_tris, np.int64)
    used, inv = np.unique(tris.ravel(), return_inverse=True)
    t = inv.reshape(-1, 3)
    v = np.asarray(sub.nodes, float)[used]
    own = np.asarray(sub.surf_cell, np.int64)                       # (the element each face belongs to: faces point away from its middle)
    mid = np.asarray(sub.nodes, float)[sub.tets[own]].mean(axis=1)
    n = np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
    flip = np.einsum("ij,ij->i", n, v[t].mean(axis=1) - mid) < 0
    t[flip] = t[flip][:, [0, 2, 1]]
    if smooth and len(t):
        on_part = np.zeros(len(original.nodes), bool)
        on_part[np.unique(np.asarray(original.surf_tris, np.int64).ravel())] = True
        v = smooth_surface(v, t, on_part[used])
    return v, t
