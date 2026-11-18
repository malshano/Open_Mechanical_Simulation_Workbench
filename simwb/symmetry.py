"""Symmetry planes: a model that is mirror-symmetric is cut by the plane, only the kept half is meshed and solved, and
the other half is the reflected image of the result.

A plane is ``{"id", "name", "point": [x, y, z] (m), "normal": [x, y, z]}``. The normal is a unit vector pointing INTO the
kept side: the model that stays is  n . (x - point) >= 0. Flipping the plane negates the normal.

Several planes must be mutually perpendicular (one plane = half the model, two = a quarter, three = an eighth):
reflections in planes at other angles do not generate a finite set of images that tile space.

The images of the kept half are its reflections in every non-empty subset of the planes (2^k - 1 of them). A force
on the half model F acts on the mirror image as R F; a moment M about a point c on the image as det(R) R M(g^-1 c),
which is what :func:`expand_force_moment` sums, so the totals of the whole body follow from the half that was solved.
"""
from __future__ import annotations

import itertools
import math
import uuid

import numpy as np

MAX_PLANES = 3
PERP_TOL = 1e-6


def _vec(v, what) -> np.ndarray:
    try:
        a = np.asarray([float(x) for x in v], float)
    except (TypeError, ValueError):
        raise ValueError(f"{what} needs three numbers (x, y, z)") from None
    if a.shape != (3,) or not np.all(np.isfinite(a)):
        raise ValueError(f"{what} needs three finite numbers (x, y, z)")
    return a


def make_plane(point, normal, name: str | None = None, plane_id: str | None = None, cut_parts: bool = False) -> dict:
    """A validated plane: point on it (m), normal toward the kept side (normalised). cut_parts: also delete the part
    of the model beyond the plane (default: only the control volume is cut there; flow studies)."""
    p, n = _vec(point, "A plane's point"), _vec(normal, "A plane's normal")
    ln = float(np.linalg.norm(n))
    if ln < 1e-9:
        raise ValueError("A plane's normal must not be zero")
    return {"id": plane_id or uuid.uuid4().hex[:8], "name": (name or "").strip(),
            "point": p.tolist(), "normal": (n / ln).tolist(), "cut_parts": bool(cut_parts)}


def validate(planes: list) -> list:
    """The list of planes, checked: at most MAX_PLANES, mutually perpendicular, named, unique ids."""
    if len(planes) > MAX_PLANES:
        raise ValueError(f"At most {MAX_PLANES} symmetry planes (half, quarter or eighth of the model)")
    out, ids = [], set()
    for k, pl in enumerate(planes):
        q = make_plane(pl["point"], pl["normal"], pl.get("name") or f"Symmetry plane {k + 1}", pl.get("id"),
                       bool(pl.get("cut_parts")))
        while q["id"] in ids:
            q["id"] = uuid.uuid4().hex[:8]
        ids.add(q["id"])
        out.append(q)
    for a, b in itertools.combinations(out, 2):
        if abs(float(np.dot(a["normal"], b["normal"]))) > PERP_TOL:
            raise ValueError(f"'{a['name']}' and '{b['name']}' must be perpendicular (or the same plane twice is "
                             "not allowed): mirrors at other angles do not tile the model")
    return out


def reflection(plane: dict):
    """(R, t) of the reflection x' = R x + t in the plane."""
    n = np.asarray(plane["normal"], float)
    p = np.asarray(plane["point"], float)
    return np.eye(3) - 2.0 * np.outer(n, n), 2.0 * float(n @ p) * n


def signed_distance(plane: dict, x) -> np.ndarray:
    """Distance of points (..., 3) from the plane, positive on the kept side."""
    return (np.asarray(x, float) - np.asarray(plane["point"], float)) @ np.asarray(plane["normal"], float)


def images(planes: list) -> list:
    """The mirror images of the kept half: [(R, t, subset)] for every non-empty subset of the planes (as indices),
    each reflection composed in order. The identity (the kept half itself) is not included."""
    out = []
    k = len(planes)
    for r in range(1, k + 1):
        for sub in itertools.combinations(range(k), r):
            R, t = np.eye(3), np.zeros(3)
            for i in sub:
                Ri, ti = reflection(planes[i])
                R, t = Ri @ R, Ri @ t + ti
            out.append((R, t, sub))
    return out


def group(planes: list) -> list:
    """The identity and all images: [(R, t)]."""
    return [(np.eye(3), np.zeros(3))] + [(R, t) for R, t, _ in images(planes)]


def expand_force_moment(planes: list, force, moment, about) -> tuple[np.ndarray, np.ndarray]:
    """Force and moment (about the point `about`) of the whole body from those of the kept half: the sum over the
    half and its images. See the module text for the transformation of the moment."""
    F, M, c = (np.asarray(v, float) for v in (force, moment, about))
    totF, totM = np.zeros(F.shape), np.zeros(M.shape)
    for R, t in group(planes):
        cg = R.T @ (c - t)  # the point that is mapped onto `about`
        Mg = M + np.cross(c - cg, F)  # moment of the half about that point
        totF = totF + F @ R.T  # (one vector, or a column of them: one row per time)
        totM = totM + float(np.linalg.det(R)) * (Mg @ R.T)
    return totF, totM


def expand_force(planes: list, force) -> np.ndarray:
    """Total of a force (or a column of them) over the half and its images."""
    F = np.asarray(force, float)
    return sum((F @ R.T for R, _ in group(planes)), np.zeros(F.shape))


def mirror_rotors(planes: list, rotors: list) -> list:
    """The rotors of the half model and their images: positions and directions reflected, the thrust the same."""
    out = [dict(r) for r in rotors]
    for R, t, _ in images(planes):
        for r in rotors:
            q = dict(r, center=(R @ np.asarray(r["center"], float) + t).tolist(),
                     axis=(R @ np.asarray(r["axis"], float)).tolist(), mirrored=True)
            if r.get("H_N") is not None:
                q["H_N"] = (R @ np.asarray(r["H_N"], float)).tolist()
            out.append(q)
    return out


def area_factor(planes: list, direction) -> float:
    """How many times larger the projected area of the whole body is than that of the kept half, looking along
    `direction`: each plane that contains the direction places the mirror image beside the half (x2); a plane the
    direction crosses squarely puts it exactly behind (x1, the silhouette is unchanged)."""
    d = np.asarray(direction, float)
    d = d / max(float(np.linalg.norm(d)), 1e-30)
    f = 1.0
    for pl in planes:
        c = abs(float(np.dot(d, pl["normal"])))
        f *= 2.0 if c < 0.05 else 1.0
    return f


def mass_factor(planes: list) -> int:
    """How many copies of the kept half make the whole body (2 ** planes)."""
    return 2 ** len(planes)


def crossing_issues(planes: list, flow_dir=None, gravity=None, tol: float = 0.02) -> list:
    """Reasons why a symmetric model would be wrong: the flow (or gravity) direction must lie in every plane."""
    out = []
    for pl in planes:
        n = np.asarray(pl["normal"], float)
        for label, v in (("The oncoming flow", flow_dir), ("Gravity", gravity)):
            if v is None:
                continue
            v = np.asarray(v, float)
            if np.linalg.norm(v) < 1e-9:
                continue
            c = abs(float(np.dot(v / np.linalg.norm(v), n)))
            if c > tol:
                out.append(f"{label} crosses '{pl['name']}' (angle to the plane {math.degrees(math.asin(min(c, 1))):.1f}"
                           "°): a mirror-symmetric model needs it to run along the plane (no side-slip, no yaw)")
    return out


def plane_from_face(face: dict, keep_toward=None, name: str | None = None) -> dict:
    """The plane of a flat face. keep_toward: a point on the side to keep (e.g. where the parts are); without one
    the face normal as stored decides."""
    n = np.asarray(face["normal"], float)
    if np.linalg.norm(n) < 1e-9:
        raise ValueError("This face has no usable normal: select a flat face")
    n = n / np.linalg.norm(n)
    p = np.asarray(face["centroid"], float)
    if keep_toward is not None and float(np.dot(np.asarray(keep_toward, float) - p, n)) < 0:
        n = -n
    return make_plane(p, n, name)


def axis_plane(axis: str, pos: float, keep_positive: bool = True, name: str | None = None) -> dict:
    """A plane normal to x, y or z at `pos` (m)."""
    if axis not in ("x", "y", "z"):
        raise ValueError("axis must be x, y or z")
    n = np.zeros(3)
    n["xyz".index(axis)] = 1.0 if keep_positive else -1.0
    p = np.zeros(3)
    p["xyz".index(axis)] = float(pos)
    return make_plane(p, n, name)


def turn(plane: dict, D, t=None) -> dict:
    """The plane after the parts were moved by x' = D x + t (a rotation D about the origin, then a shift t)."""
    D = np.asarray(D, float)
    t = np.zeros(3) if t is None else np.asarray(t, float)
    return dict(plane, point=(D @ np.asarray(plane["point"], float) + t).tolist(),
                normal=(D @ np.asarray(plane["normal"], float)).tolist())


def grazing_nudges(planes: list, faces: list, parts_bbox, max_reach: float = 1e-3, grazing: float = 0.03) -> list:
    """Planes that only just cut the model (the thin side is under `grazing` of the model's extent along the normal)
    and have tiny faces right above them (faces of the parts wholly within `reach` of the plane on the kept side:
    the bits of the model's surface that the cut leaves 0.1 mm tall). A plane like that makes micro-edges and cells
    of a few micrometres. Returns [(index, t)]: move plane `index` by t (m) into the kept side to clear them.
    reach = the smaller of max_reach and 1 % of the extent; t = the tallest such face plus a margin."""
    if not parts_bbox:
        return []
    lo, hi = np.asarray(parts_bbox[:3], float), np.asarray(parts_bbox[3:], float)
    corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    out = []
    for i, pl in enumerate(planes):
        n = np.asarray(pl["normal"], float)
        n = n / max(np.linalg.norm(n), 1e-12)
        c = (corners - np.asarray(pl["point"], float)) @ n
        if not c.min() < 0 < c.max():
            continue
        total = c.max() - c.min()
        if min(-c.min(), c.max()) >= grazing * total:
            continue
        reach = min(max_reach, 0.01 * total)
        top = 0.0
        for f in faces:
            if f.get("kind") in ("farfield", "enclosure"):
                continue
            v = np.asarray((f.get("mesh") or {}).get("vertices") or [], float).reshape(-1, 3)
            if not len(v):
                continue
            d = (v - np.asarray(pl["point"], float)) @ n
            if d.min() >= -1e-7 and 1e-7 < d.max() <= reach:   # (a face lying ON the plane is the cut itself)
                top = max(top, float(d.max()))
        if top > 0:
            out.append((i, min(reach, 1.2 * top + 2e-5)))
    return out


def on_plane(plane: dict, face: dict, tol: float) -> bool:
    """A flat face lying in the plane (normal parallel to it, centroid on it)."""
    if "Plane" not in str(face.get("surface_type", "Plane")):
        return False
    n = np.asarray(face.get("normal") or [0, 0, 0], float)
    if np.linalg.norm(n) < 1e-9:
        return False
    if abs(float(np.dot(n / np.linalg.norm(n), plane["normal"]))) < 0.9999:
        return False
    return abs(float(signed_distance(plane, face["centroid"]))) <= tol


def intersects_cylinder(plane: dict, center, axis, radius: float, length: float) -> bool:
    """Whether the plane cuts a cylinder (a rotor disk or a rotating zone): centre, axis, radius, length."""
    n = np.asarray(plane["normal"], float)
    a = np.asarray(axis, float)
    a = a / max(float(np.linalg.norm(a)), 1e-30)
    reach = radius * math.sqrt(max(0.0, 1.0 - float(a @ n) ** 2)) + 0.5 * length * abs(float(a @ n))
    return abs(float(signed_distance(plane, center))) < reach


def side_of(plane: dict, center, axis=None, radius: float = 0.0, length: float = 0.0) -> str:
    """'kept' / 'removed' / 'cut': where a placed object (a point or a cylinder) is relative to the plane."""
    if axis is not None and intersects_cylinder(plane, center, axis, radius, length):
        return "cut"
    return "kept" if float(signed_distance(plane, center)) >= 0 else "removed"
