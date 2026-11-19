"""Results resampled onto regular grids: volume rendering (a field in a box), vector arrows (velocity on a plane) and
particle paths (streamlines of the velocity in a box). The solver mesh is unstructured: every grid point is located in
a tet (nearest centroids, then a barycentric check) and the node values are interpolated; points that lie in no tet
(inside the body, outside the air box) are NaN."""
from __future__ import annotations

import numpy as np
from scipy.ndimage import map_coordinates
from scipy.spatial import cKDTree

K_NEAR = 8          # candidate tets per point (the nearest centroids; a sliver can be the one that holds the point)
CHUNK = 100_000


def nodal_values(rs, field: str) -> np.ndarray:
    """(N,) or (N,3) values at the nodes of a result set (cell values averaged to the nodes)."""
    loc, arr, _ = rs.fields[field]
    a = np.asarray(arr, float)
    if loc == "node":
        return a
    if a.ndim == 2:
        return np.stack([rs._to_nodes(a[:, c]) for c in range(a.shape[1])], 1)
    return rs._to_nodes(a)


class Locator:
    """Finds the tet holding each point and interpolates node values to it."""

    _last: dict = {}   # the KD-tree of the last mesh: successive views of one result need it again

    def __new__(cls, rs, hide_regions=()):
        key = (id(rs.nodes), id(rs.tets), tuple(hide_regions))
        hit = cls._last.get("loc")
        if hit is not None and hit[0] == key and hit[1] is rs.nodes and hit[2] is rs.tets:
            return hit[3]
        self = super().__new__(cls)
        self._init(rs, hide_regions)
        cls._last["loc"] = (key, rs.nodes, rs.tets, self)   # (holding the arrays keeps their ids valid)
        return self

    def __init__(self, rs, hide_regions=()):
        pass

    def _init(self, rs, hide_regions=()):
        t = rs.tets
        if hide_regions:
            hid = [rs.regions.index(r) for r in hide_regions if r in rs.regions]
            t = t[~np.isin(rs.tet_region, hid)]
        self.nodes, self.tets = rs.nodes, t
        self.tree = cKDTree(self.nodes[t].mean(1))

    def _try(self, p: np.ndarray, k: int):
        """Look for the tet of each point among its k nearest centroids: -> (tet or -1, weights, found)."""
        k = min(k, len(self.tets))   # (a tiny mesh has fewer cells than candidates)
        _, cand = self.tree.query(p, k=k, workers=-1)
        cand = cand.reshape(len(p), -1)
        V = self.nodes[self.tets[cand]]                          # (m,K,4,3)
        a, b, c = V[:, :, 1] - V[:, :, 0], V[:, :, 2] - V[:, :, 0], V[:, :, 3] - V[:, :, 0]
        r = p[:, None, :] - V[:, :, 0]
        bc, ca, ab = np.cross(b, c), np.cross(c, a), np.cross(a, b)
        det = np.einsum("mki,mki->mk", a, bc)
        with np.errstate(invalid="ignore", divide="ignore"):
            l1 = np.einsum("mki,mki->mk", r, bc) / det
            l2 = np.einsum("mki,mki->mk", r, ca) / det
            l3 = np.einsum("mki,mki->mk", r, ab) / det
        L = np.stack([1 - l1 - l2 - l3, l1, l2, l3], -1)         # (m,K,4)
        ok = (np.abs(det) > 1e-30) & np.all(L > -1e-6, -1)
        first = np.argmax(ok, 1)
        rows = np.arange(len(p))
        return np.where(ok[rows, first], cand[rows, first], -1), L[rows, first], ok[rows, first]

    def locate(self, pts: np.ndarray):
        """-> (tet or -1, barycentric weights (n,4)). A point that none of the 8 nearest centroids holds (long thin
        cells of the far field) is looked for among more; what is still not found lies in a body or outside."""
        n = len(pts)
        tet = np.full(n, -1, np.int64)
        lam = np.zeros((n, 4))
        # points outside the bounding box of the mesh are outside the air for certain (the other half of a half model,
        # the far field): they are not searched for at all (each one cost a look through up to 1500 candidates)
        lo_b, hi_b = self.nodes.min(0), self.nodes.max(0)
        tol = 1e-6 * float(np.max(hi_b - lo_b))
        inside = np.all((pts >= lo_b - tol) & (pts <= hi_b + tol), axis=1)
        for s in range(0, n, CHUNK):
            sl = slice(s, s + CHUNK)
            ins = inside[sl]
            if ins.any():
                idx = np.arange(n)[sl][ins]
                tet[idx], lam[idx], _ = self._try(pts[idx], K_NEAR)
        for k, step in ((40, 20000), (160, 2000), (1500, 100)):
            miss = np.where((tet < 0) & inside)[0]
            for s in range(0, len(miss), step):
                m = miss[s:s + step]
                tet[m], lam[m], _ = self._try(pts[m], min(k, len(self.tets)))
        return tet, lam

    def interpolate(self, tet, lam, node_vals):
        """node_vals (N,) or (N,3) -> values at the located points (NaN where no tet holds the point)."""
        out_shape = (len(tet),) + node_vals.shape[1:]
        out = np.full(out_shape, np.nan)
        m = tet >= 0
        nv = node_vals[self.tets[tet[m]]]                         # (k,4[,3])
        w = lam[m] if node_vals.ndim == 1 else lam[m][:, :, None]
        out[m] = (nv * w).sum(1)
        return out


def box_grid(box, n: int):
    """A regular grid over box (x0,y0,z0,x1,y1,z1) with about n points along its longest side:
    -> (axes [3 arrays], spacing h)."""
    lo, hi = np.array(box[:3], float), np.array(box[3:], float)
    if np.any(hi <= lo):
        raise ValueError("the box must have a positive size on every axis")
    h = float((hi - lo).max()) / max(int(n), 8)
    dims = np.maximum(np.ceil((hi - lo) / h).astype(int) + 1, 2)
    return [lo[i] + h * np.arange(dims[i]) for i in range(3)], h


def sample_box(rs, field: str, box, n: int, hide_regions=()):
    """Scalar (or vector) field on a grid: -> (values with shape (nz,ny,nx[,3]), axes, h)."""
    axes, h = box_grid(box, n)
    X, Y, Z = np.meshgrid(*axes, indexing="ij")                    # (nx,ny,nz)
    pts = np.stack([X.ravel(), Y.ravel(), Z.ravel()], 1)
    loc = Locator(rs, hide_regions)
    tet, lam = loc.locate(pts)
    v = loc.interpolate(tet, lam, nodal_values(rs, field))
    shape = X.shape + v.shape[1:]
    v = v.reshape(shape)
    return np.moveaxis(v, [0, 1, 2], [2, 1, 0]), axes, h


def vorticity_grid(U: np.ndarray, h: float) -> np.ndarray:
    """|curl U| on a regular grid U (nz,ny,nx,3) with spacing h (NaN next to bodies and outside the air)."""
    with np.errstate(invalid="ignore"):
        dz = np.gradient(U, h, axis=0)
        dy = np.gradient(U, h, axis=1)
        dx = np.gradient(U, h, axis=2)
        wx = dy[..., 2] - dz[..., 1]
        wy = dz[..., 0] - dx[..., 2]
        wz = dx[..., 1] - dy[..., 0]
        return np.sqrt(wx * wx + wy * wy + wz * wz)


def volume_payload(rs, field: str, box, n: int, hide_regions=(), log=None):
    """A field in a box as bytes for a 3D texture: 0 = no data (solid / outside), 1..255 = the value between lo
    and hi (log10 of the value for fields that span decades, such as the Q-criterion)."""
    derived = field == "Vorticity"
    if not derived and field not in rs.fields:
        raise ValueError(f"Unknown field '{field}'. Available: {', '.join(rs.fields)}, Vorticity")
    if derived and ("Velocity" not in rs.fields or rs.fields["Velocity"][1].ndim != 2):
        raise ValueError("Vorticity needs a velocity field")
    v, axes, h = sample_box(rs, "Velocity" if derived else field, box, n, hide_regions)
    v = vorticity_grid(v, h) if derived else (np.linalg.norm(v, axis=-1) if v.ndim == 4 else v)
    ok = np.isfinite(v)
    if not ok.any():
        raise ValueError("the box holds no fluid")
    if log is None:
        log = field in ("Q-criterion", "Vorticity")
    x = np.where(ok, v, np.nan)
    if log:
        pos = x[ok & (x > 0)]
        if not len(pos):
            raise ValueError("no positive values in the box")
        hi = float(np.percentile(pos, 99.9))
        lo = hi / 1e6
        t = np.where(ok & (x > lo), np.log10(np.clip(x, lo, None) / lo) / np.log10(hi / lo), 0.0)
    else:
        lo, hi = float(np.nanmin(x)), float(np.nanmax(x))
        t = (x - lo) / ((hi - lo) or 1.0)
    b = np.where(ok, 1 + np.clip(np.nan_to_num(t), 0, 1) * 254, 0).astype(np.uint8)
    return {"dims": [len(a) for a in axes], "origin": [float(a[0]) for a in axes], "h": h, "log": bool(log),
            "lo": lo, "hi": hi, "unit": "1/s" if derived else rs.fields[field][2], "data": _b64(b),
            "box": [float(axes[0][0]), float(axes[1][0]), float(axes[2][0]),
                    float(axes[0][-1]), float(axes[1][-1]), float(axes[2][-1])]}


def plane_payload(rs, field: str, axis: int, pos: float, box, n: int, hide_regions=()):
    """A field on the plane axis = pos, over the box: the exact cut of the mesh (the same as a section: every cell the
    plane crosses is cut and the values interpolated, so the picture has the resolution of the mesh - 5 mm cells at
    a wing, not the cells of a grid), kept where it meets the box. Nothing is drawn inside the bodies. n is unused
    (the grid version of this view needed it)."""
    if field not in rs.fields:
        raise ValueError(f"Unknown field '{field}'. Available: {', '.join(rs.fields)}")
    lo, hi = np.array(box[:3], float), np.array(box[3:], float)
    if np.any(hi <= lo):
        raise ValueError("the box must have a positive size on every axis")
    return _crop_to_box(rs.slice(field, axis, pos, hide_regions=hide_regions), axis, lo, hi, rs.fields[field][2])


def _crop_to_box(payload: dict, axis: int, lo, hi, unit):
    """Keeps the triangles of a section payload that touch the box (their whole shape: a few of them stick out of
    the box at its edge, where the mesh is coarse)."""
    import base64
    pos = np.frombuffer(base64.b64decode(payload["positions"]), np.float32).reshape(-1, 3)
    idx = np.frombuffer(base64.b64decode(payload["indices"]), np.uint32).reshape(-1, 3).astype(np.int64)
    val = np.frombuffer(base64.b64decode(payload["values"]), np.float32)
    if not len(idx):
        return payload
    others = [i for i in range(3) if i != axis]
    corners = pos[idx][:, :, others]                                  # (T,3,2)
    keep = np.all((corners.max(1) >= lo[others]) & (corners.min(1) <= hi[others]), axis=1)
    idx = idx[keep]
    used, inv = np.unique(idx.ravel(), return_inverse=True)
    pos, val, tris = pos[used], val[used], inv.reshape(-1, 3)
    fin = val[np.isfinite(val)]
    return {"positions": _b64(pos), "indices": _b64(tris.ravel().astype(np.uint32)), "values": _b64(val),
            "per": "vertex", "n_tris": int(len(tris)), "min": float(fin.min()) if len(fin) else 0.0,
            "max": float(fin.max()) if len(fin) else 0.0, "unit": unit}


def line_payload(rs, field: str, p0, p1, n: int = 120, hide_regions=()):
    """A field sampled along the straight line p0 -> p1 (n points): distance from p0 (m) and the value at each point (a
    vector field: its magnitude); None where the line is outside the mesh (in the air of a solid study, in a body of
    a flow study). Also the statistics over the points inside."""
    if field not in rs.fields:
        raise ValueError(f"Unknown field '{field}'. Available: {', '.join(rs.fields)}")
    a, b = np.asarray(p0, float), np.asarray(p1, float)
    L = float(np.linalg.norm(b - a))
    if L <= 0:
        raise ValueError("the two points of the line are the same point")
    n = int(min(max(n, 8), 600))
    t = np.linspace(0.0, 1.0, n)
    pts = a[None, :] + t[:, None] * (b - a)[None, :]
    loc = Locator(rs, hide_regions)
    tet, lam = loc.locate(pts)
    v = loc.interpolate(tet, lam, nodal_values(rs, field))
    if v.ndim == 2:
        v = np.linalg.norm(v, axis=1)
    ok = np.isfinite(v)
    out = {"s": (t * L).tolist(), "values": [float(x) if f else None for x, f in zip(v, ok)],
           "unit": rs.fields[field][2], "length": L, "p0": a.tolist(), "p1": b.tolist(), "n_inside": int(ok.sum())}
    if ok.any():
        w = v[ok]
        out.update({"min": float(w.min()), "max": float(w.max()), "mean": float(w.mean()),
                    "s_min": float(t[ok][np.argmin(w)] * L), "s_max": float(t[ok][np.argmax(w)] * L)})
    return out


def vector_payload(rs, axis: int, pos: float, box, n: int, field: str = "Velocity", hide_regions=()):
    """Arrows on the plane axis = pos inside box: points (m,3), vectors (m,3) of the fluid points of an n-point grid."""
    if field not in rs.fields or rs.fields[field][1].ndim != 2:
        raise ValueError(f"'{field}' is not a vector field")
    lo, hi = np.array(box[:3], float), np.array(box[3:], float)
    others = [i for i in range(3) if i != axis]
    h = float(max(hi[i] - lo[i] for i in others)) / max(int(n), 4)
    g = [np.arange(lo[i], hi[i] + h * 0.01, h) for i in others]
    A, B = np.meshgrid(*g, indexing="ij")
    pts = np.zeros((A.size, 3))
    pts[:, others[0]], pts[:, others[1]], pts[:, axis] = A.ravel(), B.ravel(), pos
    loc = Locator(rs, hide_regions)
    tet, lam = loc.locate(pts)
    v = loc.interpolate(tet, lam, nodal_values(rs, field))
    ok = np.all(np.isfinite(v), 1)
    p, v = pts[ok], v[ok]
    sp = np.linalg.norm(v, axis=1)
    return {"n": int(len(p)), "h": h, "positions": _b64(p.astype(np.float32)), "vectors": _b64(v.astype(np.float32)),
            "min": float(sp.min()) if len(sp) else 0.0, "max": float(sp.max()) if len(sp) else 0.0,
            "p95": float(np.percentile(sp, 95)) if len(sp) else 0.0, "unit": rs.fields[field][2]}


def particle_paths(rs, box, n_grid: int, n_particles: int, steps: int, seed_mode: str, flow_dir, field="Velocity",
                   hide_regions=(), rng_seed=1, seed_box=None):
    """Streamlines of the velocity (frozen at this time) from seed points: paths (N, steps, 3) float32 with NaN after
    a particle leaves the box or runs into a body, and the speed along them. Steps are 40 % of a grid cell at
    the typical speed, so the arrays are the same for every particle."""
    if field not in rs.fields or rs.fields[field][1].ndim != 2:
        raise ValueError(f"'{field}' is not a vector field")
    U, axes, h = sample_box(rs, field, box, n_grid, hide_regions)   # (nz,ny,nx,3)
    ok = np.all(np.isfinite(U), -1)
    if not ok.any():
        raise ValueError("the box holds no fluid")
    Uz = np.where(ok[..., None], U, 0.0)
    sp = np.linalg.norm(Uz, axis=-1)
    ref = float(np.percentile(sp[ok], 95)) or 1.0
    dt = 0.4 * h / ref
    lo = np.array([a[0] for a in axes])
    rng = np.random.default_rng(rng_seed)
    dims = np.array([len(a) for a in axes])                         # nx, ny, nz
    # seeds: upstream face of the box or anywhere in the fluid
    if seed_mode == "box" and seed_box is not None:
        # a disk: random points of the box that lie within the inscribed circle across the flow
        sl, sh = np.array(seed_box[:3], float), np.array(seed_box[3:], float)
        ax = int(np.argmax(np.abs(np.asarray(flow_dir, float))))
        c = (sl + sh) / 2
        pl = [i for i in range(3) if i != ax]
        rad = min(sh[i] - sl[i] for i in pl) / 2
        pts = sl + rng.random((n_particles * 4, 3)) * (sh - sl)
        pts = pts[np.hypot(pts[:, pl[0]] - c[pl[0]], pts[:, pl[1]] - c[pl[1]]) <= rad]
        seeds = pts[:n_particles]
        n_particles = len(seeds)
        if not n_particles:
            raise ValueError("the seed box is empty")
    elif seed_mode == "everywhere":
        idx = np.argwhere(ok)                                       # (k,3) as (z,y,x)
        sel = idx[rng.integers(0, len(idx), n_particles)][:, ::-1]  # -> (x,y,z)
        seeds = lo + (sel + rng.random((n_particles, 3)) - 0.5) * h
    else:
        fd = np.asarray(flow_dir, float)
        ax = int(np.argmax(np.abs(fd)))
        seeds = lo + rng.random((n_particles, 3)) * (dims - 1) * h
        seeds[:, ax] = lo[ax] + (0.5 * h if fd[ax] > 0 else (dims[ax] - 1.5) * h)

    def vel(p):  # trilinear velocity at points p (m,3); NaN outside the box or in a body
        c = ((p - lo) / h)[:, ::-1]                                  # (z,y,x) grid coordinates
        inside = np.all((c >= 0) & (c <= (dims[::-1] - 1)), 1)
        cc = np.clip(c, 0, dims[::-1] - 1).T
        out = np.stack([map_coordinates(Uz[..., k], cc, order=1) for k in range(3)], 1)
        solid = map_coordinates(ok.astype(float), cc, order=1) < 0.999
        out[~inside | solid] = np.nan
        return out

    P = np.full((n_particles, steps, 3), np.nan, np.float32)
    S = np.full((n_particles, steps), np.nan, np.float32)
    p = seeds.copy()
    alive = np.ones(n_particles, bool)
    for s in range(steps):
        v1 = vel(p)
        alive &= np.all(np.isfinite(v1), 1)
        if not alive.any():
            break
        P[alive, s] = p[alive]
        S[alive, s] = np.linalg.norm(v1[alive], axis=1)
        mid = p + 0.5 * dt * np.nan_to_num(v1)
        v2 = vel(mid)
        p = np.where(alive[:, None], p + dt * np.nan_to_num(v2, nan=0.0), p)
    return {"n": n_particles, "steps": steps, "dt": dt, "h": h, "ref_speed": ref, "paths": _b64(P), "speed": _b64(S),
            "min": float(np.nanmin(S)) if np.isfinite(S).any() else 0.0,
            "max": float(np.nanmax(S)) if np.isfinite(S).any() else 0.0, "unit": rs.fields[field][2]}


def _b64(a: np.ndarray) -> str:
    import base64
    return base64.b64encode(np.ascontiguousarray(a).tobytes()).decode()
