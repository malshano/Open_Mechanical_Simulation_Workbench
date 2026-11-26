"""Rotating zones (a cylinder of air that turns: MRF or sliding mesh), the geometry the window needs to show them (metres, no Qt).

A zone is {name, method 'mrf' | 'sliding', rpm, axis '+x'..'-z', center, radius, length, ...}: a cylinder about `axis` through `center`.
Used by the 3D view (the cylinder, the parts inside it, the turn preview) and by the checks that say whether a part is cut by the zone's boundary."""
from __future__ import annotations

import math

import numpy as np

AXIS_VEC = {"+x": (1.0, 0.0, 0.0), "-x": (-1.0, 0.0, 0.0), "+y": (0.0, 1.0, 0.0), "-y": (0.0, -1.0, 0.0), "+z": (0.0, 0.0, 1.0), "-z": (0.0, 0.0, -1.0)}
INSIDE, PARTLY, OUTSIDE = "inside", "partly", "outside"


def axis_of(z: dict) -> np.ndarray:
    return np.asarray(AXIS_VEC.get(z.get("axis"), AXIS_VEC["+z"]), float)


def ends(z: dict) -> tuple:
    """(start, end) of the zone's axis: the middles of its two flat faces."""
    c, a, h = np.asarray(z["center"], float), axis_of(z), float(z["length"]) / 2
    return c - a * h, c + a * h


def inside_mask(z: dict, pts) -> np.ndarray:
    """Which of the points (n, 3) lie inside the zone's cylinder."""
    p = np.asarray(pts, float).reshape(-1, 3) - np.asarray(z["center"], float)
    a = axis_of(z)
    ax = p @ a
    rad = np.linalg.norm(p - np.outer(ax, a), axis=1)
    return (np.abs(ax) <= float(z["length"]) / 2 + 1e-12) & (rad <= float(z["radius"]) + 1e-12)


def classify_parts(z: dict, faces: list) -> list:
    """[{name, share, where, faces}] for every part of the model: the share (0..1) of its surface points inside the zone and where it lies:
    'inside' (all of it: it turns with the zone), 'outside', or 'partly' (the zone's boundary cuts through it: the mesh cannot match there).
    A part is what the faces call their `part` (a flow study's parts all lie in one body of air), else their volume. faces: the engine's faces
    with their 'mesh' {'vertices'}."""
    pts: dict = {}
    ids: dict = {}
    for f in faces:
        if f.get("kind") in ("farfield", "enclosure"):
            continue
        v = (f.get("mesh") or {}).get("vertices")
        if v is None or not len(v):
            continue
        name = f.get("part") or f"body {(f.get('volumes') or [0])[0]}"
        pts.setdefault(name, []).append(np.asarray(v, float).reshape(-1, 3))
        ids.setdefault(name, []).append(f["id"])
    out = []
    for name, chunks in pts.items():
        p = np.vstack(chunks)
        share = float(inside_mask(z, p).mean()) if len(p) else 0.0
        where = INSIDE if share > 0.999 else OUTSIDE if share < 0.001 else PARTLY
        out.append({"name": str(name), "share": share, "where": where, "faces": ids[name]})
    return out


def triangles_of(faces: list, ids) -> tuple:
    """(points (n, 3), triangles (m, 3)) of the faces with these ids, joined into one surface."""
    want = set(ids)
    pts, tris, off = [], [], 0
    for f in faces:
        if f["id"] not in want:
            continue
        m = f.get("mesh") or {}
        if not m.get("vertices"):
            continue
        v = np.asarray(m["vertices"], float).reshape(-1, 3)
        t = np.asarray(m["triangles"], np.int64).reshape(-1, 3)
        pts.append(v)
        tris.append(t + off)
        off += len(v)
    if not pts:
        return np.zeros((0, 3)), np.zeros((0, 3), np.int64)
    return np.vstack(pts), np.vstack(tris)


def frame_degrees(settings: dict | None) -> float:
    """The turn between two saved times of a sliding-mesh run (degrees): the 'saved frame every' setting (8 by default), 0 when the run saves
    a set number of times instead."""
    s = settings or {}
    if s.get("analysis") != "transient":
        return 0.0
    try:
        return max(float(s.get("frame_deg", 8.0) or 0.0), 0.0)
    except (TypeError, ValueError):
        return 0.0


def degrees_per_second(z: dict) -> float:
    return float(z.get("rpm") or 0.0) * 6.0


def turn_matrix(z: dict, deg: float) -> np.ndarray:
    """The 4 x 4 matrix that turns a point by `deg` degrees (right-hand) about the zone's axis through its centre."""
    a = axis_of(z)
    t = math.radians(deg)
    K = np.array([[0.0, -a[2], a[1]], [a[2], 0.0, -a[0]], [-a[1], a[0], 0.0]])
    R = np.eye(3) + math.sin(t) * K + (1 - math.cos(t)) * (K @ K)
    c = np.asarray(z["center"], float)
    M = np.eye(4)
    M[:3, :3] = R
    M[:3, 3] = c - R @ c
    return M


def label_point(z: dict) -> list:
    """Where the zone's name hangs: at the rim of its far flat face."""
    a = axis_of(z)
    perp = np.array([0.0, 0.0, 1.0]) if abs(a[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    return (np.asarray(z["center"], float) + a * float(z["length"]) / 2 + perp * float(z["radius"])).tolist()
