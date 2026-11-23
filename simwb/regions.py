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
