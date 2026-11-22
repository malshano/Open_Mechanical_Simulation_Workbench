"""Orientation helpers: which way a flat face points, and the turn that lays it on a coordinate plane.

``align_matrix(n, target)`` is the shortest rotation taking the unit vector n onto the unit vector target (Rodrigues);
applied to the whole model it lays a face with outward normal n onto the plane normal to target, facing that way.

``outward_normal`` decides which side of a flat face is outside the part. The kernel's face normals are not reliable
for that (they depend on how each surface was built), so it is decided geometrically: a point a little off the face
is inside the adjoining volume or not, by counting the crossings of a ray with the triangles of that volume's faces.
"""
from __future__ import annotations

import numpy as np

AXES = {"+x": (1, 0, 0), "-x": (-1, 0, 0), "+y": (0, 1, 0), "-y": (0, -1, 0), "+z": (0, 0, 1), "-z": (0, 0, -1)}


def _unit(v) -> np.ndarray:
    v = np.asarray(v, float)
    n = float(np.linalg.norm(v))
    if n < 1e-12:
        raise ValueError("A direction must not be zero")
    return v / n


def align_matrix(n, target) -> np.ndarray:
    """The rotation (3x3) that turns the direction n onto `target` ('+z' ... or a vector) by the shortest way."""
    a = _unit(n)
    b = _unit(AXES[target] if isinstance(target, str) else target)
    c = float(a @ b)
    if c > 1 - 1e-12:
        return np.eye(3)
    if c < -1 + 1e-12:  # opposite: a half turn about any axis normal to a
        k = int(np.argmin(np.abs(a)))
        v = _unit(np.cross(a, np.eye(3)[k]))
        return 2.0 * np.outer(v, v) - np.eye(3)
    v = np.cross(a, b)
    s = float(np.linalg.norm(v))
    K = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]]) / s
    return np.eye(3) + s * K + (1 - c) * (K @ K)


def axis_angle(R) -> tuple[np.ndarray, float]:
    """(unit axis, angle in degrees) of a rotation matrix; (z axis, 0) for no turn."""
    R = np.asarray(R, float)
    ang = float(np.arccos(np.clip((np.trace(R) - 1) / 2, -1.0, 1.0)))
    if ang < 1e-9:
        return np.array([0.0, 0.0, 1.0]), 0.0
    if np.pi - ang < 1e-6:                      # a half turn: the axis is the column of (R + I) / 2 with the most weight
        B = (R + np.eye(3)) / 2
        v = B[:, int(np.argmax(np.diag(B)))]
        return v / np.linalg.norm(v), 180.0
    v = np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]])
    return v / np.linalg.norm(v), float(np.degrees(ang))


def nearest_axis(n) -> str:
    """The signed coordinate axis ('+x' ... '-z') a direction is closest to."""
    v = np.asarray(n, float)
    k = int(np.argmax(np.abs(v)))
    return ("+" if v[k] >= 0 else "-") + "xyz"[k]


def _inside(point, tris: np.ndarray, rng: np.random.Generator) -> bool:
    """Whether `point` is inside the closed surface made of the triangles (T, 3, 3): odd number of crossings of a ray
    (three slightly different directions, majority: a ray through an edge or a vertex counts wrongly)."""
    votes = 0
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    e1, e2 = b - a, c - a
    for _ in range(3):
        d = _unit(rng.normal(size=3))
        h = np.cross(d, e2)
        det = np.einsum("ij,ij->i", e1, h)
        ok = np.abs(det) > 1e-18
        inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        s = point - a
        u = inv * np.einsum("ij,ij->i", s, h)
        q = np.cross(s, e1)
        v = inv * (q @ d)
        t = inv * np.einsum("ij,ij->i", e2, q)
        hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-12)
        votes += int(hit.sum()) % 2
    return votes >= 2


def _tri_set(face: dict) -> np.ndarray:
    v = np.asarray(face["mesh"]["vertices"], float).reshape(-1, 3)
    t = np.asarray(face["mesh"]["triangles"], int).reshape(-1, 3)
    return v[t] if len(t) else np.zeros((0, 3, 3))


def _into_sign(face: dict, faces: dict, vol: int, p, n, seed: int = 0) -> bool:
    """Whether the direction n (from the point p of the face) points INTO the volume `vol` (decided by the surface of that volume)."""
    mine = [_tri_set(f) for f in faces.values() if vol in (f.get("volumes") or []) and len(f["mesh"]["triangles"])]
    surf = np.concatenate(mine, axis=0)
    size = float(np.linalg.norm(surf.reshape(-1, 3).max(0) - surf.reshape(-1, 3).min(0)))
    probe = np.asarray(p, float) + n * 1e-4 * max(size, 1e-9)
    return _inside(probe, surf, np.random.default_rng(seed))


def _closest_triangle(tri: np.ndarray, point) -> int:
    """The triangle nearest to a point (which lies on the surface, or very near it): the one whose plane is closest among those the
    point projects into (or, failing that, the nearest by the size of the miss)."""
    p = np.asarray(point, float)
    a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
    e1, e2, d = b - a, c - a, p - a
    d11, d12, d22 = (e1 * e1).sum(1), (e1 * e2).sum(1), (e2 * e2).sum(1)
    d1, d2 = (d * e1).sum(1), (d * e2).sum(1)
    den = np.where(np.abs(d11 * d22 - d12 * d12) < 1e-30, 1e-30, d11 * d22 - d12 * d12)
    u = (d22 * d1 - d12 * d2) / den
    w = (d11 * d2 - d12 * d1) / den
    out = np.maximum(0, np.maximum(-u, np.maximum(-w, u + w - 1)))              # how far outside the triangle (in its own units)
    n = np.cross(e1, e2)
    nl = np.maximum(np.linalg.norm(n, axis=1), 1e-30)
    off = np.abs((d * n).sum(1)) / nl                                            # the distance to its plane
    edge = np.sqrt(np.maximum(d11, d22))
    return int(np.argmin(off + out * edge))


def surface_direction(face: dict, faces: dict, solid: bool, point=None, volume: int | None = None, seed: int = 0):
    """Which way a face points out of the part, flat or curved.

    A flat face has one direction. A curved face (a wing, a fillet, any face of an STL mesh) has the area-weighted AVERAGE of its triangles'
    normals; with `point` (a point on the face, a click) it also has the normal AT that point (the surface's normal there: the corner
    normals of the triangle under it, blended). Returns None for a face between two volumes unless `volume` names the one to use, else
    {normal, point, curved, coherence, point_normal, click, area_m2}:
      normal        the average direction (the flat face's own), unit, out of the part
      point         a point on the face to draw the direction from
      coherence     |sum of the area vectors| / sum of their lengths: 1 for a flat face, near 0 for one that curves right round
      point_normal  the normal at `point` (curved faces, when a point was given)
      click         the point on the surface the normal was taken at
    """
    vols = face.get("volumes") or []
    if volume is None:
        if len(vols) != 1:
            return None
        volume = vols[0]
    tri = _tri_set(face)
    if not len(tri):
        return None
    cr = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])                   # (twice the area vectors, in the winding's direction)
    ar = np.linalg.norm(cr, axis=1)
    ok = ar > 1e-30
    cr, ar, tri = cr[ok], ar[ok], tri[ok]
    if not len(ar):
        return None
    tot = cr.sum(0)
    coherence = float(np.linalg.norm(tot) / ar.sum())

    def out_sign(p, n):                       # +1 when n (a triangle's own normal) points out of the part
        into = _into_sign(face, faces, volume, p, n, seed)
        out_is_n = (not into) if solid else into
        return 1.0 if out_is_n else -1.0
    centers = tri.mean(1)
    flat = "Plane" in str(face.get("surface_type", ""))
    if flat:
        k = int(np.argmax(ar))
        n = _unit(face["normal"]) if float(np.linalg.norm(face.get("normal") or [0, 0, 0])) > 1e-9 else cr[k] / ar[k]
        into = _into_sign(face, faces, volume, centers[k], n, seed)
        out = -n if into == solid else n
        res = {"normal": out, "point": centers[k], "curved": False, "coherence": 1.0, "point_normal": None, "click": None}
    else:
        cen = (centers * ar[:, None]).sum(0) / ar.sum()                          # where the face's area is, on average
        kc = int(np.argmin(np.linalg.norm(centers - cen, axis=1)))
        k = int(np.argmax(ar))
        s = out_sign(centers[k], cr[k] / ar[k])
        avg = _unit(tot) * s if np.linalg.norm(tot) > 1e-12 * ar.sum() else None
        res = {"normal": avg, "point": centers[kc], "curved": True, "coherence": coherence, "point_normal": None, "click": None}
        if point is not None:
            j = _closest_triangle(tri, point)
            vt = np.asarray(face["mesh"]["triangles"], int).reshape(-1, 3)[ok]
            nv = int(vt.max()) + 1
            acc = np.zeros((nv, 3))
            for c in range(3):
                np.add.at(acc, vt[:, c], cr)                                       # (each corner: the area-weighted mean of its triangles)
            a, b, c3 = tri[j]
            d = np.asarray(point, float) - a
            e1, e2 = b - a, c3 - a
            d11, d12, d22, d1, d2 = e1 @ e1, e1 @ e2, e2 @ e2, d @ e1, d @ e2
            den = d11 * d22 - d12 * d12 or 1e-30
            u, w = (d22 * d1 - d12 * d2) / den, (d11 * d2 - d12 * d1) / den
            bary = np.clip([1 - u - w, u, w], 0, 1)
            bary = bary / bary.sum()
            blend = sum(bary[c] * acc[vt[j, c]] for c in range(3))
            if np.linalg.norm(blend) < 1e-12:
                blend = cr[j]
            pn = _unit(blend) * out_sign(centers[j], cr[j] / ar[j])
            res["point_normal"] = pn
            res["click"] = a + bary[1] * e1 + bary[2] * e2
    return res


def outward_normal(face: dict, faces: dict, solid: bool, seed: int = 0):
    """Unit normal of a flat face pointing out of the part.

    face: a face record (id, normal, volumes, mesh {vertices, triangles}); faces: {id: record} of the model;
    solid: True when the adjoining volume is the part itself (solid studies), False when it is the air around it (flow
    studies: the part is the empty space behind the face, so outward is INTO the air). Returns (normal, point) with
    `point` a point on the face (the centre of its largest triangle), or None for a face between two volumes."""
    vols = face.get("volumes") or []
    if len(vols) != 1:
        return None
    v = np.asarray(face["mesh"]["vertices"], float).reshape(-1, 3)
    t = np.asarray(face["mesh"]["triangles"], int).reshape(-1, 3)
    if not len(t):
        return None
    tri = v[t]
    area = 0.5 * np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
    k = int(np.argmax(area))
    p = tri[k].mean(0)
    n = _unit(face["normal"])
    # the surface of the adjoining volume: the triangles of every face that bounds it
    mine = [np.asarray(f["mesh"]["vertices"], float).reshape(-1, 3)[np.asarray(f["mesh"]["triangles"], int).reshape(-1, 3)]
            for f in faces.values() if vols[0] in (f.get("volumes") or []) and len(f["mesh"]["triangles"])]
    surf = np.concatenate(mine, axis=0)
    size = float(np.linalg.norm(surf.reshape(-1, 3).max(0) - surf.reshape(-1, 3).min(0)))
    probe = p + n * 1e-4 * max(size, 1e-9)
    inside = _inside(probe, surf, np.random.default_rng(seed))
    into_volume = n if inside else -n          # the direction from the face into the adjoining volume
    return (-into_volume if solid else into_volume), p
