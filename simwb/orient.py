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
