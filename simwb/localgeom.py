"""Local geometry of the walls for the mesh methods: how thick a body is behind a point of its surface and how narrow the gap in front of it is.

Both are the distance along a ray from the surface (against the normal for the thickness, along it for a gap) to the next wall, found on the triangles of
the display triangulation of the faces (meshplan.Surface). Used by the layer planner (layermesh.clamp_layers: layers must fit inside half the local thickness),
the proximity sizing (sizingmethods) and the prism layers of the mesher. Pure numpy / scipy: no Gmsh, no Qt."""
from __future__ import annotations

import numpy as np
from scipy.spatial import cKDTree

MAX_SAMPLES = 30_000


def ray_hits(surf, origins, dirs, max_dist: float, chunk: int = 2000):
    """Distance along each ray (origin + t dir, t > 0, unit dirs) to the first triangle of `surf` within `max_dist`; inf where there is none. The triangles
    whose circumradius is under `max_dist` are found with a KD-tree of their centres, the larger ones (a big flat face is a few huge triangles) are tested for
    every ray. Moller-Trumbore, the triangle of the origin itself is not counted (a hit nearer than 1e-4 max_dist is not a hit)."""
    O = np.asarray(origins, float).reshape(-1, 3)
    D = np.asarray(dirs, float).reshape(-1, 3)
    n = len(O)
    out = np.full(n, np.inf)
    if not n or not len(surf.T):
        return out
    a, b, c = (surf.V[surf.T[:, k]] for k in range(3))
    rad = np.maximum.reduce([np.linalg.norm(x - surf.centroid, axis=1) for x in (a, b, c)])
    big = rad > max_dist
    small = np.nonzero(~big)[0]
    bigi = np.nonzero(big)[0]
    reach = 0.5 * max_dist + (float(rad[small].max()) if len(small) else 0.0)           # (a ball round the middle of the ray that holds the centre of every triangle it can hit)
    tree = cKDTree(surf.centroid[small]) if len(small) else None
    e1, e2 = b - a, c - a
    eps = 1e-4 * max_dist

    def test(idx_o, idx_t):
        """t of the ray idx_o[i] against the triangle idx_t[i] (inf when it misses)."""
        o, d = O[idx_o], D[idx_o]
        h = np.cross(d, e2[idx_t])
        det = np.einsum("ij,ij->i", e1[idx_t], h)
        ok = np.abs(det) > 1e-18
        f = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        s = o - a[idx_t]
        u = f * np.einsum("ij,ij->i", s, h)
        q = np.cross(s, e1[idx_t])
        v = f * np.einsum("ij,ij->i", d, q)
        t = f * np.einsum("ij,ij->i", e2[idx_t], q)
        hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1.0) & (t > eps) & (t <= max_dist)
        return np.where(hit, t, np.inf)

    for s0 in range(0, n, chunk):
        sl = np.arange(s0, min(n, s0 + chunk))
        if tree is not None:
            lists = tree.query_ball_point(O[sl] + 0.5 * max_dist * D[sl], reach, workers=-1)
            cnt = np.fromiter((len(x) for x in lists), np.int64, len(sl))
            if cnt.sum():
                io = np.repeat(sl, cnt)
                it = small[np.concatenate([np.asarray(x, np.int64) for x in lists if len(x)])]
                np.minimum.at(out, io, test(io, it))
        if len(bigi):
            io = np.repeat(sl, len(bigi))
            it = np.tile(bigi, len(sl))
            np.minimum.at(out, io, test(io, it))
    return out


def surface_samples(surf, spacing: float, cap: int = MAX_SAMPLES):
    """Points on the triangles of `surf` about `spacing` apart with the normal out of the body, the face id and the area each stands for:
    (points, normals, faces, areas). Triangles smaller than the spacing give their centre; larger ones are cut into a grid. The spacing grows when more than
    `cap` points would be needed."""
    if not len(surf.T):
        z = np.zeros((0, 3))
        return z, z, np.zeros(0, np.int64), np.zeros(0)
    spacing = max(spacing, float(np.sqrt(surf.area.sum() / cap)) * 1.2)
    a, b, c = (surf.V[surf.T[:, k]] for k in range(3))
    edge = np.maximum.reduce([np.linalg.norm(b - a, axis=1), np.linalg.norm(c - b, axis=1), np.linalg.norm(a - c, axis=1)])
    k = np.maximum(np.ceil(edge / spacing).astype(int), 1)
    P, N, F, A = [], [], [], []
    for kk in np.unique(k):
        sel = np.nonzero(k == kk)[0]
        if kk == 1:
            P.append(surf.centroid[sel])
            N.append(surf.normal_out[sel])
            F.append(surf.tri_face[sel])
            A.append(surf.area[sel])
            continue
        w = np.array([((i + 1.0 / 3) / kk, (j + 1.0 / 3) / kk) for i in range(kk) for j in range(kk - i)] +
                     [((i + 2.0 / 3) / kk, (j + 2.0 / 3) / kk) for i in range(kk - 1) for j in range(kk - 1 - i)])          # (centres of the sub-triangles)
        u, v = w.T
        P.append((a[sel][:, None] * (1 - u - v)[None, :, None] + b[sel][:, None] * u[None, :, None] + c[sel][:, None] * v[None, :, None]).reshape(-1, 3))
        N.append(np.repeat(surf.normal_out[sel], len(w), axis=0))
        F.append(np.repeat(surf.tri_face[sel], len(w)))
        A.append(np.repeat(surf.area[sel] / len(w), len(w)))
    return np.vstack(P), np.vstack(N), np.concatenate(F), np.concatenate(A)


def wall_distances(surf, spacing: float, max_dist: float, side: str = "in"):
    """How far it is from the walls to the next wall: `side` 'in' (into the body: its local thickness) or 'out' (into the air: the width of the gap in
    front of the wall). {points, normals, faces, areas, dist}; dist is inf where there is no wall within max_dist."""
    P, N, F, A = surface_samples(surf, spacing)
    d = -N if side == "in" else N
    return {"points": P, "normals": N, "faces": F, "areas": A, "dist": ray_hits(surf, P + 1e-4 * max_dist * d, d, max_dist)}


def weighted_quantile(values, weights, q: float) -> float:
    """The value below which a share q of the total weight lies (inf values last)."""
    v = np.asarray(values, float)
    w = np.asarray(weights, float)
    if not len(v):
        return float("inf")
    o = np.argsort(v, kind="stable")
    cw = np.cumsum(w[o])
    i = int(np.searchsorted(cw, q * cw[-1], side="left"))
    return float(v[o][min(i, len(v) - 1)])
