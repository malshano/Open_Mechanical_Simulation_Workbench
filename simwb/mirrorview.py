"""The whole model from the solved part: what the Results step draws when a study was solved on a half, quarter or eighth of the model.

Only what is drawn is mirrored, never the volume mesh: the surface payloads (the object, sections, vortex surfaces, contour planes), the arrows,
the particle paths, the volume grid, the outlines of zones and refinement regions, the air box and the camera box. Each is a plain numpy copy
of the payload the engine already makes, so it costs about as much as one more copy of what is on the screen, and a transient result (a sliding
mesh turning with the time bar) is mirrored frame by frame: the mirror image of a propeller that turns one way turns the other way about
the mirrored axis, which is simply the mirror of the solved surface at the same time.

The images are the ones of `simwb.symmetry.images` (the reflections in every non-empty subset of the planes, each composed in order: mirror about
the first plane, then the result about the second ...). A reflection has det -1 and turns the winding of a triangle round; the composition of two
has det +1 (a half turn about the line where the planes meet) and keeps it, so the winding is turned only when det(R) < 0 and the normals of
the image always point out of the body.

Vectors (arrows, the deformation of a structural result) are reflected as v - 2 (v . n) n: the component normal to the plane changes sign. Scalars
(and the magnitude of a vector field, which is what a surface or a contour plane shows) are copied. A payload that is mirrored carries
``"mirror": {"solved_tris", "solved_points", "copies", "transforms"}`` so that the window can tell a point of the solved part from one of its
copies (probes, the status line).
"""
from __future__ import annotations

import base64

import numpy as np

from . import symmetry as sym


def image_transforms(planes: list) -> list:
    """[(R, t)] of the mirror images (the identity, the solved part itself, is not in it)."""
    return [(R, t) for R, t, _sub in sym.images(planes or [])]


def _dec(s: str, dtype) -> np.ndarray:
    return np.frombuffer(base64.b64decode(s), dtype)


def _enc(a: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(a).tobytes()).decode()


def reflect_points(pts, R, t) -> np.ndarray:
    """Points (..., 3) reflected: x' = R x + t."""
    return np.asarray(pts, float) @ np.asarray(R, float).T + np.asarray(t, float)


def reflect_vectors(v, R) -> np.ndarray:
    """Vectors (..., 3) reflected (no shift): v' = R v = v - 2 (v . n) n for one plane."""
    return np.asarray(v, float) @ np.asarray(R, float).T


def flips_winding(R) -> bool:
    return float(np.linalg.det(np.asarray(R, float))) < 0.0


def mirror_triangles(tris, R) -> np.ndarray:
    """Triangles (T, 3) of the image: the corner order is turned round when the image is a reflection, so that the normals stay outward."""
    t = np.asarray(tris)
    return t[:, [0, 2, 1]] if flips_winding(R) else t


def mirror_surface(p: dict, R, t) -> dict:
    """The mirrored copy (alone) of a surface payload {positions, indices, values, per, [disp], ...}: points reflected, winding turned for a
    reflection, values copied (a face's or a corner's scalar does not change), the deformation vectors reflected."""
    pos = _dec(p["positions"], np.float32).reshape(-1, 3)
    idx = _dec(p["indices"], np.uint32).reshape(-1, 3)
    out = dict(p)
    out["positions"] = _enc(reflect_points(pos.astype(np.float64), R, t).astype(np.float32))
    out["indices"] = _enc(mirror_triangles(idx, R).astype(np.uint32))
    if p.get("disp"):
        d = _dec(p["disp"], np.float32).reshape(-1, 3)
        out["disp"] = _enc(reflect_vectors(d.astype(np.float64), R).astype(np.float32))
    out.pop("mirror", None)
    return out


def whole_surface(p: dict, planes: list) -> dict:
    """The solved surface and all its mirror images as one payload (triangles and points of the solved part first, then each image in the order
    of `images`). ``p["mirror"]`` says where the solved part ends. No planes, or nothing drawn: p itself."""
    imgs = image_transforms(planes)
    if not p or not p.get("n_tris") or not imgs:
        return p
    pos = _dec(p["positions"], np.float32).reshape(-1, 3)
    idx = _dec(p["indices"], np.uint32).reshape(-1, 3)
    val = _dec(p["values"], np.float32)
    n_pts, n_tri = len(pos), len(idx)
    ps, ix, vs = [pos], [idx], [val]
    ds = [_dec(p["disp"], np.float32).reshape(-1, 3)] if p.get("disp") else None
    for k, (R, t) in enumerate(imgs, 1):
        ps.append(reflect_points(pos.astype(np.float64), R, t).astype(np.float32))
        ix.append((mirror_triangles(idx, R) + np.uint32(k * n_pts)).astype(np.uint32))
        vs.append(val)
        if ds is not None:
            ds.append(reflect_vectors(ds[0].astype(np.float64), R).astype(np.float32))
    out = dict(p)
    out["positions"] = _enc(np.concatenate(ps))
    out["indices"] = _enc(np.concatenate(ix))
    out["values"] = _enc(np.concatenate(vs))
    if ds is not None:
        out["disp"] = _enc(np.concatenate(ds))
    out["n_tris"] = int(n_tri * (1 + len(imgs)))
    out["mirror"] = {"solved_tris": int(n_tri), "solved_points": int(n_pts), "copies": 1 + len(imgs),
                     "transforms": [{"R": np.asarray(R).tolist(), "t": np.asarray(t).tolist()} for R, t in imgs]}
    return out


def side_of_triangle(p_or_info: dict, cell: int) -> int:
    """0 for a triangle of the solved part, k for the k-th mirror image (p_or_info: the payload or its ``"mirror"`` entry)."""
    m = p_or_info.get("mirror", p_or_info)
    n = int(m.get("solved_tris") or 0)
    return int(cell) // n if n else 0


def to_solved(point, info: dict, side: int) -> list:
    """The point of the solved part that a point of image `side` is the copy of (x = R^T (x' - t): R is orthogonal)."""
    if not side:
        return [float(c) for c in point]
    tr = info["transforms"][side - 1]
    R, t = np.asarray(tr["R"], float), np.asarray(tr["t"], float)
    return [float(c) for c in R.T @ (np.asarray(point, float) - t)]


def mirror_vectors(p: dict, planes: list) -> dict:
    """An arrow payload {n, positions, vectors, ...} with the arrows of every image added (positions reflected, vectors reflected)."""
    imgs = image_transforms(planes)
    if not p or not p.get("n") or not imgs:
        return p
    pos = _dec(p["positions"], np.float32).reshape(-1, 3).astype(np.float64)
    vec = _dec(p["vectors"], np.float32).reshape(-1, 3).astype(np.float64)
    ps, vs = [pos], [vec]
    for R, t in imgs:
        ps.append(reflect_points(pos, R, t))
        vs.append(reflect_vectors(vec, R))
    out = dict(p)
    out["positions"] = _enc(np.concatenate(ps).astype(np.float32))
    out["vectors"] = _enc(np.concatenate(vs).astype(np.float32))
    out["n"] = int(len(pos) * (1 + len(imgs)))
    return out


def mirror_paths(p: dict, planes: list) -> dict:
    """A particle-path payload {n, steps, paths (n, steps, 3), speed (n, steps)} with the paths of every image added."""
    imgs = image_transforms(planes)
    if not p or not p.get("n") or not imgs:
        return p
    n, steps = int(p["n"]), int(p["steps"])
    P = _dec(p["paths"], np.float32).reshape(n, steps, 3).astype(np.float64)
    S = _dec(p["speed"], np.float32).reshape(n, steps)
    Ps, Ss = [P], [S]
    for R, t in imgs:
        Ps.append(reflect_points(P, R, t))               # (NaN, where a particle was lost, stays NaN)
        Ss.append(S)
    out = dict(p)
    out["paths"] = _enc(np.concatenate(Ps).astype(np.float32))
    out["speed"] = _enc(np.concatenate(Ss))
    out["n"] = int(n * (1 + len(imgs)))
    return out


def axis_aligned(R, tol: float = 1e-9) -> bool:
    """True when R only flips axes (a plane normal to x, y or z, or a product of such): then a grid stays a grid."""
    R = np.asarray(R, float)
    return bool(np.allclose(R, np.diag(np.diag(R)), atol=tol))


def mirror_volumes(p: dict, planes: list) -> list:
    """The volume grids of the images: [payload] (the grid flipped along each mirrored axis, its origin moved). An image whose plane is not normal
    to an axis cannot be drawn as a grid and is left out (the second value tells how many)."""
    out, skipped = [], 0
    if not p:
        return out, skipped
    nx, ny, nz = (int(v) for v in p["dims"])
    data = _dec(p["data"], np.uint8).reshape(nz, ny, nx)
    org = np.asarray(p["origin"], float)
    h = float(p["h"])
    far = org + h * (np.array([nx, ny, nz]) - 1)
    for R, t in image_transforms(planes):
        if not axis_aligned(R):
            skipped += 1
            continue
        d = np.diag(R)
        a, b = d * org + np.asarray(t, float), d * far + np.asarray(t, float)
        lo, hi = np.minimum(a, b), np.maximum(a, b)
        g = data
        for ax in range(3):
            if d[ax] < 0:
                g = np.flip(g, axis=2 - ax)               # (the array is (z, y, x))
        q = dict(p, data=_enc(g), origin=lo.tolist(),
                 box=[float(v) for v in np.concatenate([lo, hi])])
        out.append(q)
    return out, skipped


def mirror_box(box, planes: list):
    """The box [x0, y0, z0, x1, y1, z1] that holds a box and its images (the camera is fitted to it)."""
    if box is None:
        return None
    b = np.asarray(box, float)
    imgs = image_transforms(planes)
    if not imgs:
        return list(box)
    lo, hi = b[:3], b[3:]
    corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    pts = [corners] + [reflect_points(corners, R, t) for R, t in imgs]
    allp = np.vstack(pts)
    return np.concatenate([allp.min(0), allp.max(0)]).tolist()


def _same_set(a: np.ndarray, b: np.ndarray, tol: float) -> bool:
    """The point sets a and b (n, 3) are the same set of points, up to the order."""
    if a.shape != b.shape:
        return False
    return all(np.min(np.linalg.norm(b - p, axis=1)) <= tol for p in a)


def _corners(r: dict) -> np.ndarray:
    """The points that fix a region in space: a cylinder's two ends, a box's eight corners (turned about its middle)."""
    if r.get("shape") == "cylinder":
        return np.array([r["start"], r["end"]], float)
    lo, hi = np.asarray(r["min"], float), np.asarray(r["max"], float)
    c, hs = (lo + hi) / 2, (hi - lo) / 2
    Rb = np.eye(3) if r.get("rotation") is None else np.asarray(r["rotation"], float)
    signs = np.array([[sx, sy, sz] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)], float)
    return c + (signs * hs) @ Rb.T


def _same_region(a: dict, b: dict) -> bool:
    if (a.get("shape") == "cylinder") != (b.get("shape") == "cylinder"):
        return False
    pa, pb = _corners(a), _corners(b)
    size = float(np.ptp(pa, axis=0).max()) or 1e-9
    return _same_set(pa, pb, 1e-6 * max(size, float(a.get("radius") or 0.0)))


def mirror_regions(regions: list, planes: list) -> list:
    """The regions (the 3D view's dictionaries: cylinders {start, end, radius, ...} and boxes {min, max, [rotation]}) with the images of those that are
    not their own image added: a zone centred on a plane, with its axis along the normal or in the plane, is drawn once; one off the plane twice (and
    a zone off two planes four times). The solved ones come first; an image gets ' (mirrored)' in its name."""
    out = list(regions or [])
    for R, t in image_transforms(planes):
        for r in regions or []:
            try:
                if r.get("shape") == "cylinder":
                    a, b = reflect_points(r["start"], R, t), reflect_points(r["end"], R, t)
                    q = dict(r, start=a.tolist(), end=b.tolist())
                    ax = (b - a) / (np.linalg.norm(b - a) or 1.0)
                    ext = float(r["radius"]) * np.sqrt(np.clip(1 - ax ** 2, 0, 1))
                    q["min"], q["max"] = (np.minimum(a, b) - ext).tolist(), (np.maximum(a, b) + ext).tolist()
                else:
                    lo, hi = np.asarray(r["min"], float), np.asarray(r["max"], float)
                    c, hs = (lo + hi) / 2, (hi - lo) / 2
                    Rb = np.eye(3) if r.get("rotation") is None else np.asarray(r["rotation"], float)
                    cn = reflect_points(c, R, t)
                    Rn = R @ Rb @ np.diag([-1.0, 1.0, 1.0]) if flips_winding(R) else R @ Rb          # (a box is its own image under a flip of one of its axes; this keeps a proper turn)
                    q = dict(r, min=(cn - hs).tolist(), max=(cn + hs).tolist(), rotation=Rn.tolist())
                if any(_same_region(q, o) for o in out):
                    continue
                if q.get("name"):
                    q["name"] = f"{q['name']} (mirrored)"
                out.append(q)
            except (KeyError, TypeError, ValueError):
                continue
    return out


def mirror_faces(faces: list, planes: list, skip_on_plane: bool = True) -> list:
    """CAD faces ({'id', 'kind', 'mesh': {'vertices', 'triangles'}, ...}) with their images added: the air box of a half model, whole. A face that
    lies in a symmetry plane (key 'symmetry' set) is the cut, inside the whole model: it is left out of both."""
    imgs = image_transforms(planes)
    keep = [f for f in faces if not (skip_on_plane and f.get("symmetry") is not None)]
    if not imgs:
        return list(faces)
    out = list(keep)
    for k, (R, t) in enumerate(imgs, 1):
        for f in keep:
            m = f.get("mesh") or {}
            if not m.get("vertices"):
                continue
            v = np.asarray(m["vertices"], float).reshape(-1, 3)
            tri = np.asarray(m["triangles"], np.int64).reshape(-1, 3)
            out.append(dict(f, id=int(f["id"]) + 1_000_000 * k,
                            mesh=dict(m, vertices=reflect_points(v, R, t).ravel().tolist(), triangles=mirror_triangles(tri, R).ravel().tolist())))
    return out
