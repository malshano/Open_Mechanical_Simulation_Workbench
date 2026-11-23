"""Refinement regions of the mesh (metres): helpers shared by the mesh generators and the window.

A region is a box {name, size, min, max, [rotation]} or a cylinder {name, size, shape: "cylinder", start, end, radius, [inner_radius], min, max}.
A box may be turned: `min` and `max` are then the box before the turn, `rotation` (a 3 x 3 matrix, local -> world) turns it about its middle.
A cylinder is turned by moving its end points; its min / max are the bounding box of the whole cylinder."""
from __future__ import annotations

import numpy as np


def check_rotation(rot, tol: float = 1e-6):
    """A rotation matrix as a list of lists, or None when it is the identity; raises ValueError when it is not a proper rotation."""
    try:
        R = np.asarray(rot, float)
    except (TypeError, ValueError):
        raise ValueError("A region's rotation must be a 3 x 3 matrix of numbers") from None
    if R.shape != (3, 3) or not np.all(np.isfinite(R)):
        raise ValueError("A region's rotation must be a 3 x 3 matrix of numbers")
    if not np.allclose(R @ R.T, np.eye(3), atol=1e-4) or abs(float(np.linalg.det(R)) - 1.0) > 1e-4:
        raise ValueError("A region's rotation must be a proper rotation (orthonormal, no mirroring)")
    if np.allclose(R, np.eye(3), atol=tol):
        return None
    return R.tolist()


def rotation_of(r: dict) -> np.ndarray:
    R = r.get("rotation")
    return np.eye(3) if R is None else np.asarray(R, float)


def is_turned(r: dict) -> bool:
    return r.get("shape") != "cylinder" and r.get("rotation") is not None and not np.allclose(rotation_of(r), np.eye(3), atol=1e-9)


def centre(r: dict) -> np.ndarray:
    if r.get("shape") == "cylinder":
        return (np.asarray(r["start"], float) + np.asarray(r["end"], float)) / 2
    return (np.asarray(r["min"], float) + np.asarray(r["max"], float)) / 2


def box_corners(r: dict) -> np.ndarray:
    """The 8 corners of a box region in the world (turned about its middle)."""
    lo, hi = np.asarray(r["min"], float), np.asarray(r["max"], float)
    c = (lo + hi) / 2
    h = (hi - lo) / 2
    signs = np.array([[sx, sy, sz] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)], float)
    return c + (signs * h) @ rotation_of(r).T


def bounds(r: dict) -> tuple:
    """(min, max) of the axis-aligned box that holds the region: what a mesher that only knows axis-aligned boxes is given."""
    if r.get("shape") == "cylinder" or not is_turned(r):
        return list(r["min"]), list(r["max"])
    c = box_corners(r)
    return c.min(axis=0).tolist(), c.max(axis=0).tolist()


def moved_and_turned(r: dict, R: np.ndarray, shift: np.ndarray) -> dict:
    """The region after x' = R (x - c) + c + shift (c: its middle): the move and turn that the window's gizmo previews."""
    R, shift = np.asarray(R, float), np.asarray(shift, float)
    c = centre(r)
    out = dict(r)
    if r.get("shape") == "cylinder":
        for k in ("start", "end"):
            out[k] = (R @ (np.asarray(r[k], float) - c) + c + shift).tolist()
        a, b = np.asarray(out["start"]), np.asarray(out["end"])
        ax = (b - a) / (np.linalg.norm(b - a) or 1.0)
        ext = float(r["radius"]) * np.sqrt(np.clip(1 - ax ** 2, 0, 1))
        out["min"], out["max"] = (np.minimum(a, b) - ext).tolist(), (np.maximum(a, b) + ext).tolist()
        return out
    out["min"] = (np.asarray(r["min"], float) + shift).tolist()
    out["max"] = (np.asarray(r["max"], float) + shift).tolist()
    R2 = R @ rotation_of(r)
    out["rotation"] = None if np.allclose(R2, np.eye(3), atol=1e-9) else R2.tolist()
    if out["rotation"] is None:
        out.pop("rotation")
    return out


MIN_SIZE = 1e-3          # a region is never made smaller than this (m): a 1 mm box or a 1 mm long, 1 mm radius cylinder


def size_of(r: dict) -> list:
    """The size of a region in its own axes (m): a box [x, y, z]; a cylinder [length, radius, inner radius]."""
    if r.get("shape") == "cylinder":
        return [float(np.linalg.norm(np.asarray(r["end"], float) - np.asarray(r["start"], float))), float(r["radius"]), float(r.get("inner_radius") or 0.0)]
    return (np.asarray(r["max"], float) - np.asarray(r["min"], float)).tolist()


def grown(r: dict, g) -> dict:
    """The region with its faces moved outwards by g (m; negative moves a face in). A box: g[k][0] / g[k][1] for the faces at -axis k / +axis k of
    its own axes, the opposite face stays where it is. A cylinder: g[0][0] and g[0][1] move its start and its end along the axis, g[1][0] its radius.
    Nothing gets smaller than MIN_SIZE."""
    g = np.asarray(g, float).reshape(3, 2)
    out = dict(r)
    if not g.any():
        return out
    if r.get("shape") == "cylinder":
        a, b = np.asarray(r["start"], float), np.asarray(r["end"], float)
        u = (b - a) / (np.linalg.norm(b - a) or 1.0)
        length = float(np.linalg.norm(b - a))
        gs, ge = float(g[0, 0]), float(g[0, 1])
        if length + gs + ge < MIN_SIZE:                        # (not shorter than the minimum: the moved face stops there)
            over = MIN_SIZE - (length + gs + ge)
            gs, ge = (gs + over, ge) if abs(gs) >= abs(ge) else (gs, ge + over)
        out["start"], out["end"] = (a - u * gs).tolist(), (b + u * ge).tolist()
        out["radius"] = max(float(r["radius"]) + float(g[1, 0]), float(r.get("inner_radius") or 0.0) + MIN_SIZE, MIN_SIZE)
        a2, b2 = np.asarray(out["start"]), np.asarray(out["end"])
        ext = out["radius"] * np.sqrt(np.clip(1 - u ** 2, 0, 1))
        out["min"], out["max"] = (np.minimum(a2, b2) - ext).tolist(), (np.maximum(a2, b2) + ext).tolist()
        return out
    lo, hi = np.asarray(r["min"], float), np.asarray(r["max"], float)
    c, h = (lo + hi) / 2, (hi - lo) / 2
    lo_l, hi_l = -h - g[:, 0], h + g[:, 1]
    small = hi_l - lo_l < MIN_SIZE
    if small.any():                                            # (the face being moved stops at the minimum size)
        for k in np.where(small)[0]:
            if abs(g[k, 0]) >= abs(g[k, 1]):
                lo_l[k] = hi_l[k] - MIN_SIZE
            else:
                hi_l[k] = lo_l[k] + MIN_SIZE
    centre_w = c + rotation_of(r) @ ((lo_l + hi_l) / 2)
    half = (hi_l - lo_l) / 2
    out["min"], out["max"] = (centre_w - half).tolist(), (centre_w + half).tolist()
    return out


def face_handles(r: dict) -> list:
    """The places to grab a region to resize it: [{key, pos, dir}] - a box has a handle on each of its six faces (pointing out of the face), a
    cylinder one at each end (along the axis) and one on its side (the radius). `key` says which growth it changes: (axis k, side) or ("len", side)
    or ("rad", 0)."""
    out = []
    if r.get("shape") == "cylinder":
        a, b = np.asarray(r["start"], float), np.asarray(r["end"], float)
        u = (b - a) / (np.linalg.norm(b - a) or 1.0)
        out.append({"key": ("len", 0), "pos": a, "dir": -u})
        out.append({"key": ("len", 1), "pos": b, "dir": u})
        h = np.array([1.0, 0.0, 0.0]) if abs(u[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
        side = np.cross(u, h)
        side /= np.linalg.norm(side)
        out.append({"key": ("rad", 0), "pos": (a + b) / 2 + side * float(r["radius"]), "dir": side})
        return out
    c, R = centre(r), rotation_of(r)
    half = (np.asarray(r["max"], float) - np.asarray(r["min"], float)) / 2
    for k in range(3):
        for side, s in ((0, -1.0), (1, 1.0)):
            d = s * R[:, k]
            out.append({"key": (k, side), "pos": c + d * half[k], "dir": d})
    return out


def growth_index(key) -> tuple:
    """Where in the 3 x 2 growth array a handle's key points."""
    if key[0] == "len":
        return (0, int(key[1]))
    if key[0] == "rad":
        return (1, 0)
    return (int(key[0]), int(key[1]))
