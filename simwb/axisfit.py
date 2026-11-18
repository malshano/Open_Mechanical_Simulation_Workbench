"""Axis and centre of a round feature, from the display triangulation of a face: a cylindrical face (a shaft, a hub
bore, a motor can) or a circular edge (the rim of a hole, the outline of a disc).

Used to place a rotating zone or a rotor disc exactly on the thing that turns, instead of on the bounding box or the
centroid of a part (the centroid of a three-blade propeller is off the hub).

All functions take a face record's mesh: ``vertices`` (flat list, 3 per vertex) and ``triangles`` (flat list of vertex
indices). Faces tessellated by the CAD kernel have vertices ON the surface and on the edges, so the fits are exact up to
rounding.
"""
from __future__ import annotations

import numpy as np

MAX_RESIDUAL = 0.02  # rms distance from the fitted circle / cylinder, relative to its radius


def _mesh(face: dict):
    m = face.get("mesh") or {}
    v = np.asarray(m.get("vertices") or [], float).reshape(-1, 3)
    t = np.asarray(m.get("triangles") or [], int).reshape(-1, 3)
    if len(v) < 3 or len(t) < 1:
        raise ValueError("This face has no display mesh to measure")
    return v, t


def _basis(axis: np.ndarray):
    """Two unit vectors that, with the axis, make a right-handed frame."""
    ref = np.array([1.0, 0, 0]) if abs(axis[0]) < 0.9 else np.array([0, 1.0, 0])
    u = np.cross(axis, ref)
    u /= np.linalg.norm(u)
    return u, np.cross(axis, u)


def _circle2d(xy: np.ndarray):
    """Least-squares circle through 2-D points (Kasa): (cx, cy, r, rms residual)."""
    A = np.c_[2 * xy[:, 0], 2 * xy[:, 1], np.ones(len(xy))]
    b = (xy ** 2).sum(1)
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    cx, cy = sol[0], sol[1]
    r2 = sol[2] + cx * cx + cy * cy
    if r2 <= 0:
        raise ValueError("The points do not lie on a circle")
    r = float(np.sqrt(r2))
    rms = float(np.sqrt(np.mean((np.hypot(xy[:, 0] - cx, xy[:, 1] - cy) - r) ** 2)))
    return float(cx), float(cy), r, rms


def fit_cylinder(face: dict) -> dict:
    """Axis, centre, radius and length of a cylindrical face (a full cylinder or an arc of one). The axis comes from
    the triangle normals (all of them are perpendicular to it), the circle from the vertices seen along it; the centre
    is on the axis, half way along the extent of the face. Returns {center, axis, radius, length}; the axis sign is
    arbitrary."""
    v, t = _mesh(face)
    n = np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
    area = np.linalg.norm(n, axis=1)
    keep = area > 1e-18
    if keep.sum() < 3:
        raise ValueError("This face is too small to measure")
    n = n[keep] / area[keep, None]
    w, V = np.linalg.eigh((n[:, :, None] * n[:, None, :] * area[keep, None, None]).sum(0))
    if w[0] > 0.02 * w.sum():
        raise ValueError("This face is not a cylinder (its surface normals do not all turn about one axis)")
    axis = V[:, 0]
    u, wv = _basis(axis)
    pts = np.unique(v[np.unique(t)], axis=0)
    cx, cy, r, rms = _circle2d(np.c_[pts @ u, pts @ wv])
    if rms > MAX_RESIDUAL * r:
        raise ValueError("This face is not a cylinder (its cross-section is not a circle)")
    s = pts @ axis
    mid = 0.5 * (s.min() + s.max())
    return {"center": (cx * u + cy * wv + mid * axis).tolist(), "axis": axis.tolist(), "radius": r,
            "length": float(s.max() - s.min())}


def boundary_loops(face: dict) -> list:
    """The closed boundary loops of a face as arrays of vertex positions (edges used by exactly one triangle,
    chained end to end)."""
    v, t = _mesh(face)
    cnt: dict = {}
    for a, b, c in t:
        for e in ((a, b), (b, c), (c, a)):
            k = (min(e), max(e))
            cnt[k] = cnt.get(k, 0) + 1
    nxt: dict = {}
    for (a, b), k in cnt.items():
        if k == 1:
            nxt.setdefault(a, []).append(b)
            nxt.setdefault(b, []).append(a)
    seen, loops = set(), []
    for start in nxt:
        if start in seen:
            continue
        loop, cur, prev = [start], start, None
        seen.add(start)
        while True:
            options = [x for x in nxt[cur] if x != prev and x not in seen]
            if not options:
                break
            prev, cur = cur, options[0]
            seen.add(cur)
            loop.append(cur)
        if len(loop) >= 6:
            loops.append(v[loop])
    return loops


def fit_circle3d(pts: np.ndarray) -> dict:
    """Centre, plane normal and radius of points on a circle in space; raises when they are not on one."""
    c0 = pts.mean(0)
    _, sv, Vt = np.linalg.svd(pts - c0, full_matrices=False)
    if sv[0] < 1e-15 or sv[2] > 0.02 * sv[0]:
        raise ValueError("not planar")
    normal = Vt[2]
    u, w = _basis(normal)
    cx, cy, r, rms = _circle2d(np.c_[(pts - c0) @ u, (pts - c0) @ w])
    if rms > MAX_RESIDUAL * r:
        raise ValueError("not circular")
    return {"center": (c0 + cx * u + cy * w).tolist(), "axis": normal.tolist(), "radius": r}


def nearest_circular_edge(face: dict, point) -> dict:
    """The circular boundary loop of a face that is closest to `point` (a click on the face, near its edge).
    Returns {center, axis, radius, length: None}."""
    p = np.asarray(point, float)
    best, bd = None, 1e300
    for loop in boundary_loops(face):
        try:
            c = fit_circle3d(loop)
        except ValueError:
            continue
        ax = np.asarray(c["axis"], float)
        rel = p - np.asarray(c["center"], float)
        off = float(rel @ ax)
        rad = float(np.linalg.norm(rel - off * ax))
        d = float(np.hypot(rad - c["radius"], off))  # distance of the point from the circle
        if d < bd:
            best, bd = c, d
    if best is None:
        raise ValueError("This face has no circular edge: click a cylindrical face, or a face with a round edge "
                         "(a hole, a disc, a boss) near that edge")
    return dict(best, length=None)


def pick_axis(face: dict, point=None) -> dict:
    """Centre and axis of what was clicked: a cylindrical face as a whole, or the circular edge of another face
    nearest to the click. Adds kind ('cylinder' | 'edge')."""
    if "Cylinder" in str(face.get("surface_type", "")):
        return dict(fit_cylinder(face), kind="cylinder")
    if point is None:
        v, _ = _mesh(face)
        point = v.mean(0)
    return dict(nearest_circular_edge(face, point), kind="edge")


def _chains(face: dict) -> list:
    """Boundary chains of a face: closed loops and open arcs, as (vertex positions, closed). An open chain is
    followed from one of its ends, so an arc is not cut in two."""
    v, t = _mesh(face)
    cnt: dict = {}
    for a, b, c in t:
        for e in ((a, b), (b, c), (c, a)):
            k = (min(e), max(e))
            cnt[k] = cnt.get(k, 0) + 1
    nxt: dict = {}
    for (a, b), k in cnt.items():
        if k == 1:
            nxt.setdefault(a, []).append(b)
            nxt.setdefault(b, []).append(a)
    seen, out = set(), []
    for start in sorted(nxt, key=lambda x: len(nxt[x])):  # chain ends (one neighbour) first
        if start in seen:
            continue
        loop, cur, prev = [start], start, None
        seen.add(start)
        while True:
            options = [x for x in nxt[cur] if x != prev and x not in seen]
            if not options:
                break
            prev, cur = cur, options[0]
            seen.add(cur)
            loop.append(cur)
        if len(loop) >= 6:
            out.extend(_split_corners(v[loop], start in nxt[cur]))
    return out


def _split_corners(pts: np.ndarray, closed: bool) -> list:
    """Cut an outline at its sharp corners (the end of an arc against a straight side), so that each arc between two
    corners can be fitted on its own. A corner is a turn of more than 30 degrees and more than three times the usual
    turn between neighbouring points of the outline (a circle that is tessellated coarsely turns that much everywhere
    and is not cut)."""
    n = len(pts)
    prv, nxt_ = (np.roll(pts, 1, 0), np.roll(pts, -1, 0)) if closed else (np.vstack([pts[:1], pts[:-1]]), np.vstack([pts[1:], pts[-1:]]))
    d1, d2 = pts - prv, nxt_ - pts
    cosang = np.einsum("ij,ij->i", d1, d2) / np.maximum(np.linalg.norm(d1, axis=1) * np.linalg.norm(d2, axis=1), 1e-300)
    turn = np.degrees(np.arccos(np.clip(cosang, -1, 1)))
    if not closed:
        turn[0] = turn[-1] = 0.0
    bent = turn[turn > 0.5]
    thr = max(30.0, 3.0 * float(np.median(bent))) if len(bent) else 30.0
    corners = [i for i in range(n) if turn[i] > thr]
    if not corners:
        return [(pts, closed)]
    if closed:
        order = np.roll(np.arange(n), -corners[0])
        seq = pts[order]
        ci = [int(np.where(order == c)[0][0]) for c in corners] + [n]
        pieces = [seq[ci[k]:ci[k + 1] + 1] for k in range(len(ci) - 1)]
        pieces[-1] = np.vstack([pieces[-1], seq[:1]]) if not np.allclose(pieces[-1][-1], seq[0]) else pieces[-1]
    else:
        ci = [0] + corners + [n - 1]
        pieces = [pts[ci[k]:ci[k + 1] + 1] for k in range(len(ci) - 1)]
    return [(q, False) for q in pieces if len(q) >= 6]


def circular_edges(faces: list, ref_length: float, max_points: int = 96) -> list:
    """Every circular edge of the model (a full circle or an arc), once: an edge shared by two faces (a hole's rim
    between the flat face and the bore) is listed with both. Each: {center, axis (positive along its dominant
    coordinate), radius, closed, points (flat, along the edge), faces}. Edges smaller than 0.2 % of the model are left
    out (fillets and threads would only clutter the view)."""
    found: list = []
    for f in faces:
        try:
            chains = _chains(f)
        except ValueError:
            continue
        for pts, closed in chains:
            try:
                c = fit_circle3d(pts)
            except ValueError:
                continue
            if c["radius"] < 0.002 * max(ref_length, 1e-12):
                continue
            tol = 1e-4 * max(ref_length, 1e-12)
            same = next((e for e in found if abs(e["radius"] - c["radius"]) < tol
                         and np.linalg.norm(np.asarray(e["center"]) - np.asarray(c["center"])) < tol
                         and abs(abs(float(np.dot(e["axis"], c["axis"]))) - 1) < 1e-6
                         and e["closed"] == closed), None)
            if same is not None:
                same["faces"].append(int(f["id"]))
                continue
            ax = np.asarray(c["axis"], float)
            ax = ax / np.linalg.norm(ax)
            if ax[int(np.argmax(np.abs(ax)))] < 0:
                ax = -ax
            step = max(1, int(np.ceil(len(pts) / max_points)))
            sel = pts[::step]
            if closed is False and not np.allclose(sel[-1], pts[-1]):
                sel = np.vstack([sel, pts[-1]])
            found.append({"center": c["center"], "axis": ax.tolist(), "radius": c["radius"], "closed": bool(closed),
                          "points": sel.ravel().tolist(), "faces": [int(f["id"])]})
    for i, e in enumerate(found):
        e["id"] = i
    return found
