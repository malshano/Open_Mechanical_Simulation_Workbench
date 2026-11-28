"""The size field of the mesher before any mesh exists: the target cell size at every point, from the same decisions the tetrahedral mesher takes (wall
size, growth, thin parts, refinement boxes, wake, rotors, zones, part sizing, sharp edges), so the user sees where the cells will be small.

`gmsh_worker.task_mesh` builds gmsh fields from these decisions; this module evaluates the same fields as plain formulas (a distance to the walls from a
cloud of points on them, the boxes and cylinders as the worker's own expressions) and takes their minimum as gmsh's Min field does. The decisions use the
worker's own functions where it has them (`aero_wall_size`, `smallest_feature`, `face_width`, `PRESETS`) and the mesh advisor's recipe; the rest is written
here once and checked against a real mesh by tests/test_sizefield.py (the measured cell sizes of a quick mesh against the preview).

Not in the preview, because they need the mesher: the sizes the curvature of a face asks for, the narrow-gap boxes of an enclosure study, and the global
smallest size. All lengths in metres."""
from __future__ import annotations

import base64

import math

import numpy as np

from . import gmsh_worker as gw

WALL_POINTS = 200_000         # (points sampled on the walls: the distance to them is the distance to the surface, to about one point spacing)


# ------------------------------------------------------------------------------------------------------------------------ the pieces of the field
def _threshold(d, size_min, size_max, dist_min, dist_max):
    """gmsh's Threshold field: size_min up to dist_min, linear to size_max at dist_max, size_max beyond."""
    t = np.clip((d - dist_min) / max(dist_max - dist_min, 1e-300), 0.0, 1.0)
    return size_min + (size_max - size_min) * t


def _grown(d, size, size_far, growth):
    """The worker's wall-type field: `size` up to one cell from the surface, then growing by the mesh growth ratio to the far-field size."""
    n = math.log(max(size_far / size, 1.0001)) / math.log(growth)
    return _threshold(d, size, size_far, size, size * (growth ** n - 1) / (growth - 1))


def _box_dist(p, lo, hi, rot=None):
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    if rot is not None:                                       # (a box turned about its middle: the point in the box's own axes)
        c = (lo + hi) / 2
        p = (p - c) @ np.asarray(rot, float)
        lo, hi = lo - c, hi - c
    return np.linalg.norm(np.maximum(np.maximum(lo - p, p - hi), 0.0), axis=1)


def _cyl_axes(p, start, end):
    s = np.asarray(start, float)
    a = np.asarray(end, float) - s
    length = float(np.linalg.norm(a))
    u = a / length
    rel = p - s
    t = rel @ u
    rho = np.linalg.norm(rel - np.outer(t, u), axis=1)
    return t, rho, length


def _cyl_dist(p, start, end, radius, inner=0.0):
    t, rho, length = _cyl_axes(p, start, end)
    dr = np.maximum(0.0, np.maximum(rho - radius, inner - rho)) if inner > 0 else np.maximum(0.0, rho - radius)
    da = np.maximum(0.0, np.maximum(-t, t - length))
    return np.hypot(dr, da)


def _shell_dist(p, center, axis, radius, length):
    """Distance to the surface (the sides and the two ends) of a capped cylinder: the sliding interface of a rotating zone."""
    c, a = np.asarray(center, float), np.asarray(axis, float)
    a = a / np.linalg.norm(a)
    rel = p - c
    t, rho = rel @ a, np.linalg.norm(rel - np.outer(rel @ a, a), axis=1)
    h = length / 2
    inside = (np.abs(t) <= h) & (rho <= radius)
    return np.where(inside, np.minimum(radius - rho, h - np.abs(t)), np.hypot(np.maximum(rho - radius, 0.0), np.maximum(np.abs(t) - h, 0.0)))


def _wall_cloud(pr, face_ids, n: int, seed: int = 3) -> np.ndarray:
    """About n points spread over the given faces (their display triangles, in proportion to the area): the surface the distance is taken to."""
    rng = np.random.default_rng(seed)
    by_id = {f["id"]: f for f in pr.faces()}
    tris, area = [], []
    for i in face_ids:
        m = (by_id.get(i) or {}).get("mesh")
        if not m or not m.get("triangles"):
            continue
        v = np.asarray(m["vertices"], float).reshape(-1, 3)
        t = np.asarray(m["triangles"], int).reshape(-1, 3)
        tri = v[t]
        tris.append(tri)
        area.append(0.5 * np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1))
    if not tris:
        return np.zeros((0, 3))
    tri, ar = np.vstack(tris), np.concatenate(area)
    corners = tri.reshape(-1, 3)
    if ar.sum() <= 0:
        return corners
    pick = rng.choice(len(tri), size=int(n), p=ar / ar.sum())
    r1, r2 = rng.random(int(n)), rng.random(int(n))
    flip = r1 + r2 > 1
    r1[flip], r2[flip] = 1 - r1[flip], 1 - r2[flip]
    a, b, c = tri[pick, 0], tri[pick, 1], tri[pick, 2]
    return np.vstack([a + (b - a) * r1[:, None] + (c - a) * r2[:, None], corners])      # (the corners too: the edges and corners of the surface)


# ------------------------------------------------------------------------------------------------------------------------ the decisions
_CACHE: dict = {}


def decisions(pr) -> dict:
    """The decisions for the stored settings, kept until the geometry, the mesh settings, the flow or the zones change (the preview follows a slider)."""
    import json
    key = json.dumps([pr.data["geometry"].get("updated"), pr.data["mesh"], pr.data.get("settings"), pr.data.get("moving_zones"), pr.data.get("rotors"),
                      pr.data["geometry"].get("status")], sort_keys=True, default=str)
    hit = _CACHE.get(pr.id)
    if hit and hit[0] == key:
        return hit[1]
    spec = _decisions(pr)
    _CACHE[pr.id] = (key, spec)
    return spec


def _decisions(pr) -> dict:
    """What the mesher would decide for the stored settings: {study, h_wall, h_body, size_far, growth, terms, notes}. terms is a list of (name, function of
    points (n, 3) -> sizes (n,)); the preview is their minimum."""
    from . import meshadvisor

    study = pr.study
    ms = pr.data["mesh"]
    info = pr.data["geometry"].get("info")
    if pr.data["geometry"].get("status") != "ready" or not info:
        raise ValueError("Import the geometry first")
    faces = pr.faces()
    solid = study in gw.SOLID_STUDIES
    aero = study == "external_aero"
    L = float(info["ref_length"])
    preset = ms.get("preset", "medium")
    n_per, _nc = gw.PRESETS.get(preset, gw.PRESETS["medium"])
    rc = None
    if aero and ms.get("smart"):
        an = meshadvisor.analyse(pr.data, faces, info)
        rc = meshadvisor.recipe(an, preset, ms.get("growth"))
    ms = dict(ms, recipe=rc) if rc else dict(ms)
    h_body = float(ms.get("size_max") or (rc["h_near"] if rc else L / n_per))
    feature = gw.smallest_feature(faces, L)
    thin = []                                                 # (size, face ids): thin parts refined locally
    if aero:
        n_feat = gw.FEATURE_CELLS.get(preset, 4)
        h_wall = gw.aero_wall_size(ms, feature, L, h_body, rc)
        if not ms.get("size_min"):
            h_thin_min = h_wall / 3 if rc else L / (12 * n_per)
            by_size = {}
            for f in faces:
                if f["kind"] in ("farfield", "enclosure"):
                    continue
                w = gw.face_width(f)
                if L * 2e-3 < w and w / n_feat < 0.9 * h_wall:
                    by_size.setdefault(round(max(w / n_feat, h_thin_min), 6), []).append(f["id"])
            thin = sorted(by_size.items())
    else:
        floor = L / ((8 if solid else 3) * n_per)
        h_wall = float(ms.get("size_min") or min(max(feature / 2.0, floor), h_body / 2))
    growth = float(rc["growth"]) if rc else float(ms.get("growth", 1.25))
    size_far = h_body
    dom = info.get("domain") or {}
    if aero and dom.get("box_min") is not None:
        size_far = max(b - a for a, b in zip(dom["box_min"], dom["box_max"])) / 12
    wall_ids = [f["id"] for f in faces if f["kind"] not in ("farfield", "enclosure")] if not solid else [f["id"] for f in faces]
    notes = ["The curvature of the faces, narrow air gaps and the global smallest cell are decided by the mesher and are not in this preview."]
    terms = []
    cloud = _wall_cloud(pr, wall_ids, WALL_POINTS)
    if len(cloud):
        from scipy.spatial import cKDTree
        tree = cKDTree(cloud)
        terms.append(("Walls", lambda p, tree=tree: _grown(tree.query(p)[0], h_wall, size_far, growth)))
        for sz, ids in thin:
            c2 = _wall_cloud(pr, ids, 20_000)
            if len(c2):
                t2 = cKDTree(c2)
                terms.append((f"Thin parts ({sz * 1000:.2g} mm)", lambda p, t2=t2, sz=sz: _grown(t2.query(p)[0], sz, size_far, growth)))
        for k, r in enumerate(ms.get("refinements") or []):
            c3 = _wall_cloud(pr, [int(x) for x in r["faces"]], 20_000)
            if len(c3):
                t3 = cKDTree(c3)
                sz = float(r["size"])
                terms.append((f"Part sizing: {r.get('part') or r.get('name') or k + 1}",
                              lambda p, t3=t3, sz=sz: _threshold(t3.query(p)[0], sz, size_far, sz, sz * 10)))
    if not aero and not solid:
        terms.append(("Bulk air", lambda p: np.full(len(p), h_body)))
    if aero:
        for rz in pr._rotors_with_wake():
            c = np.asarray(rz["center"], float)
            a = np.asarray(rz["axis"], float)
            a = a / np.linalg.norm(a)
            R = float(rz["radius"])
            th = float(rz.get("thickness", 0.1 * R))
            n_wake = gw.WAKE_CELLS.get(preset, 8)
            h_disk = min(h_body, th / 2, R / n_wake)
            w = np.asarray(rz.get("wake_dir") or (-a).tolist(), float)
            w = w / np.linalg.norm(w)
            Lw = 6.0 * R
            for nm, start, end, rad, sz in (("Rotor disk", c - a * th, c + a * th, R * 1.1, h_disk),
                                            ("Rotor wake", c, c + w * Lw, R * 1.15, R / n_wake)):
                terms.append((nm, lambda p, s=start, e=end, rad=rad, sz=sz: np.where(_cyl_dist(p, s, e, rad) <= 0, sz, size_far)))
        src = info["parts_bbox"]
        if rc:
            for rb in rc["boxes"]:
                sz = float(rb["size"])
                thick = max((size_far - sz) / (growth - 1.0), sz)
                terms.append((rb.get("name") or "Refinement box", lambda p, rb=rb, sz=sz, thick=thick:
                              sz + (size_far - sz) * np.minimum(1.0, _box_dist(p, rb["min"], rb["max"]) / thick)))
        else:
            pad = 0.5 * L
            lo = [src[0] - pad, src[1] - pad, src[2] - pad]
            hi = [src[3] + pad, src[4] + pad, src[5] + pad]
            terms.append(("Box round the body", lambda p: h_body * 1.5 + (size_far - h_body * 1.5) * np.minimum(1.0, _box_dist(p, lo, hi) / L)))
    for z in pr.data.get("moving_zones") or []:
        from . import studies
        ax = np.asarray(studies.AXES[z["axis"]], float)
        hz = min(h_body, 2 * math.pi * float(z["radius"]) / gw.ZONE_CELLS.get(preset, 36), float(z.get("cell_size") or 1e9))
        terms.append((f"Zone boundary: {z['name']}", lambda p, z=z, ax=ax, hz=hz:
                      _threshold(_shell_dist(p, z["center"], ax, float(z["radius"]), float(z["length"])), hz, size_far, hz, hz * 12)))
        if z.get("cell_size"):
            start = np.asarray(z["center"], float) - ax * float(z["length"]) / 2
            end = np.asarray(z["center"], float) + ax * float(z["length"]) / 2
            terms.append((f"Zone cells: {z['name']}", lambda p, z=z, s=start, e=end:
                          np.where(_cyl_dist(p, s, e, float(z["radius"])) <= 0, float(z["cell_size"]), size_far)))

    def transition(sz):
        return max((size_far - sz) / (growth - 1.0), 8 * sz) if rc else sz * 8

    for i, r in enumerate(ms.get("regions") or []):
        sz = float(r["size"])
        th = transition(sz)
        nm = r.get("name") or f"Region {i + 1}"
        if r.get("shape") == "cylinder":
            terms.append((nm, lambda p, r=r, sz=sz, th=th: sz + (size_far - sz) * np.minimum(1.0, _cyl_dist(p, r["start"], r["end"], r["radius"], r.get("inner_radius", 0.0)) / th)))
        else:
            terms.append((nm, lambda p, r=r, sz=sz, th=th: sz + (size_far - sz) * np.minimum(1.0, _box_dist(p, r["min"], r["max"], r.get("rotation")) / th)))
    edge = None
    if aero:
        try:
            from . import edgepreview
            ep = edgepreview.preview(pr)
            if ep.get("plan") and ep["plan"].get("applied") and ep.get("lines"):
                h_edge = float(ep["plan"]["h_edge"])
                pts = []
                for line in ep["lines"]:
                    q = np.asarray(line, float)
                    seg = np.linalg.norm(np.diff(q, axis=0), axis=1)
                    t = np.concatenate([[0], np.cumsum(seg)])
                    s = np.linspace(0, t[-1], int(min(50_000, max(t[-1] / max(h_edge * 0.5, 1e-9), 2))))
                    pts.append(np.stack([np.interp(s, t, q[:, k]) for k in range(3)], axis=1))
                from scipy.spatial import cKDTree
                te = cKDTree(np.vstack(pts))
                terms.append(("Sharp edges", lambda p, te=te, h=h_edge: h + (growth - 1.0) * te.query(p)[0]))
                edge = h_edge
            elif ep.get("on") and ep.get("scan") != "ready":
                notes.append("The sharp edges are still being searched for: they are added to the preview when the search is done.")
        except Exception:  # noqa: BLE001 - the edges are an extra: without them the rest is still right
            pass
    return {"study": study, "h_wall": h_wall, "h_body": h_body, "size_far": size_far, "growth": growth, "terms": terms, "notes": notes, "h_edge": edge,
            "smart": bool(rc)}


def evaluate(spec: dict, pts: np.ndarray, which: bool = False):
    """The target size at the points (n, 3): the smallest of the fields, at most the far-field size. which=True also returns the index of the field that gave it."""
    pts = np.asarray(pts, float).reshape(-1, 3)
    vals = np.stack([fn(pts) for _n, fn in spec["terms"]], axis=0) if spec["terms"] else np.full((1, len(pts)), spec["size_far"])
    best = vals.min(axis=0)
    best = np.minimum(best, spec["size_far"])
    return (best, vals.argmin(axis=0)) if which else best


# ------------------------------------------------------------------------------------------------------------------------ what is drawn
def subdivide(v: np.ndarray, t: np.ndarray, levels: int = 1):
    """Split every triangle in four, `levels` times (shared vertices): a finer surface for the colours of the field."""
    for _ in range(levels):
        e = np.sort(np.vstack([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]]), axis=1)
        u, inv = np.unique(e, axis=0, return_inverse=True)
        mid = (v[u[:, 0]] + v[u[:, 1]]) / 2
        n = len(t)
        m01, m12, m20 = (len(v) + inv[k * n:(k + 1) * n] for k in range(3))
        v = np.vstack([v, mid])
        t = np.vstack([np.c_[t[:, 0], m01, m20], np.c_[m01, t[:, 1], m12], np.c_[m20, m12, t[:, 2]], np.c_[m01, m12, m20]])
    return v, t


def _payload(pos, idx, vals):
    from .results import _b64
    fin = vals[np.isfinite(vals)]
    return {"positions": _b64(np.asarray(pos, np.float32)), "indices": _b64(np.asarray(idx, np.uint32)), "values": _b64(np.asarray(vals, np.float32)),
            "per": "vertex", "n_tris": int(len(idx)), "min": float(fin.min()) if len(fin) else 0.0, "max": float(fin.max()) if len(fin) else 0.0,
            "unit": "mm", "scale": "log"}


def surface_points(pr, max_tris: int = 250_000):
    """The walls' display triangles, split until they are about as fine as the wall cells: vertices (n, 3) and triangles (m, 3)."""
    info = pr.data["geometry"]["info"]
    faces = pr.faces()
    study = pr.study
    solid = study in gw.SOLID_STUDIES
    vs, ts, off = [], [], 0
    for f in faces:
        if not solid and f["kind"] in ("farfield", "enclosure"):
            continue
        m = f.get("mesh")
        if not m or not m.get("triangles"):
            continue
        v = np.asarray(m["vertices"], float).reshape(-1, 3)
        t = np.asarray(m["triangles"], int).reshape(-1, 3)
        vs.append(v)
        ts.append(t + off)
        off += len(v)
    if not vs:
        return np.zeros((0, 3)), np.zeros((0, 3), int)
    v, t = np.vstack(vs), np.vstack(ts)
    for _ in range(4):
        if len(t) * 4 > max_tris:
            break
        v, t = subdivide(v, t, 1)
    return v, t


def section_grid(pr, axis: str, frac: float, n: int = 220):
    """A grid across the domain at the position `frac` (0..1) along `axis`, closer together near the body (a domain many body lengths wide holds the
    body in a few cells otherwise): vertices (k, 3) and triangles (m, 3)."""
    info = pr.data["geometry"]["info"]
    dom = info.get("domain") or {}
    bb = np.asarray(info["parts_bbox"], float)
    if dom.get("box_min") is not None:
        lo, hi = np.asarray(dom["box_min"], float), np.asarray(dom["box_max"], float)
    else:
        pad = 0.15 * (bb[3:] - bb[:3]).max()
        lo, hi = bb[:3] - pad, bb[3:] + pad
    ax = "xyz".index(axis)
    pos = lo[ax] + (hi[ax] - lo[ax]) * frac
    u, v = [a for a in range(3) if a != ax]
    c = (bb[:3] + bb[3:]) / 2

    def line(a):
        # sinh mapping: dense round the body's middle, sparse far away (the body's own extent is resolved by 1/3 of the points)
        s = np.linspace(-1.0, 1.0, n)
        k = 4.0 if (hi[a] - lo[a]) > 3 * (bb[3 + a] - bb[a]) else 0.0
        mapped = np.sinh(k * s) / np.sinh(k) if k else s
        left, right = c[a] - lo[a], hi[a] - c[a]
        return np.where(mapped < 0, c[a] + mapped * left, c[a] + mapped * right)

    gu, gv = line(u), line(v)
    U, V = np.meshgrid(gu, gv, indexing="ij")
    pts = np.zeros((n * n, 3))
    pts[:, ax], pts[:, u], pts[:, v] = pos, U.ravel(), V.ravel()
    ii = np.arange(n * n).reshape(n, n)
    a, b, c_, d = ii[:-1, :-1].ravel(), ii[1:, :-1].ravel(), ii[1:, 1:].ravel(), ii[:-1, 1:].ravel()
    return pts, np.vstack([np.c_[a, b, c_], np.c_[a, c_, d]]), pos


def preview(pr, axis: str | None = None, frac: float = 0.5) -> dict:
    """The planned cell sizes: {surface: payload on the walls, section: payload of a cut (when `axis`), h_wall, h_body, size_far, growth, fields: names, notes,
    range: [low, high] mm}. Sizes in mm in the payloads (the geometry in metres)."""
    spec = decisions(pr)
    out = {"h_wall": spec["h_wall"], "h_body": spec["h_body"], "size_far": spec["size_far"], "growth": spec["growth"], "h_edge": spec["h_edge"],
           "fields": [n for n, _ in spec["terms"]], "notes": spec["notes"], "smart": spec["smart"], "surface": None, "section": None}
    v, t = surface_points(pr)
    if len(t):
        out["surface"] = _payload(v, t, evaluate(spec, v) * 1000.0)
    lo = hi = None
    if axis:
        pts, tris, pos = section_grid(pr, axis, frac)
        s = evaluate(spec, pts) * 1000.0
        out["section"] = _payload(pts, tris, s)
        out["section"]["position"] = float(pos)
        out["section_axis"] = axis
    vals = [p for p in (out["surface"], out["section"]) if p]
    if vals:
        lo, hi = min(p["min"] for p in vals), max(p["max"] for p in vals)
        out["range"] = [lo, hi]
    return out


def combined(out: dict) -> dict:
    """One surface payload of the preview (the walls and the cut together) for the 3D view, with its colour range, label and unit."""
    parts = [p for p in (out.get("surface"), out.get("section")) if p]
    if not parts:
        return {"n_tris": 0}
    from .results import _b64
    pos, idx, val, off = [], [], [], 0
    for p in parts:
        a = np.frombuffer(base64.b64decode(p["positions"]), np.float32).reshape(-1, 3)
        i = np.frombuffer(base64.b64decode(p["indices"]), np.uint32).reshape(-1, 3)
        v = np.frombuffer(base64.b64decode(p["values"]), np.float32)
        pos.append(a)
        idx.append(i + off)
        val.append(v)
        off += len(a)
    v = np.concatenate(val)
    d = _payload(np.vstack(pos), np.vstack(idx), v)
    d.update({"metric": "size_plan", "label": "Planned cell size", "range": out["range"]})
    return d
