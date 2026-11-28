"""The memory planner of the Mesh step: how many cells and how much memory a tetrahedral mesh will need, worked out from the size fields BEFORE meshing,
and a memory budget that coarsens the bulk of the mesh until the estimate fits.

How the estimate works (docs/TECHNICAL_MANUAL.md, "Memory planner"):

1. The size the mesher will ask for at every point of the air (or the solid) is rebuilt from the same settings the mesher reads (`sizing`): the wall size growing
   away from the walls, thin and small faces, curved faces, part sizings, refinement regions and boxes, rotors and moving zones, the far-field size.
2. The cells are the integral of 1 / (the volume of a regular tetrahedron of that size), over the volume that is meshed (the air without the body, or the
   solid): `integrate`, an adaptive octree that only splits where the size changes quickly or a wall cuts the box. About 0.1 to 2 seconds.
3. Gmsh does not make regular tetrahedra: the integral is multiplied by CELLS_CALIBRATION, measured on real meshes (benchmarks/results/mesh_plan_*.jsonl), and
   the range is the spread of those measurements (CELLS_SPREAD).
4. Memory is the fixed part (Python and gmsh, about 0.8 GB) plus a share per cell, measured separately (`ram_model`).

The part that needs the CAD kernel (the sharp edges that the edge refinement finds) is a separate call, `edge_extra_cells`: see the note there.

Pure Python (numpy, scipy): no Qt, no Gmsh, so it can be tested and reused by the assistant."""
from __future__ import annotations

import math
import threading

import numpy as np

TET_VOLUME = 1.0 / (6.0 * math.sqrt(2.0))       # volume of a regular tetrahedron of edge 1

# --- calibration: measured on real meshes with the gmsh worker (benchmarks/mesh_plan_calibrate.py, results in benchmarks/results/mesh_plan_*.jsonl) ---
CELLS_CALIBRATION = 0.45    # real cells / the integral of regular tetrahedra, fitted on 22 real meshes of 3 example geometries (benchmarks/mesh_plan_fit.py; docs/MESHING_GUIDE.md)
SURFACE_CELLS_SOLID = 6.0   # the same for a solid study (5 real meshes: heated plate, bracket; the flow value over-counts there)
SURFACE_CELLS = 8.0         # cells on the walls per (area / wall size^2), same fit: the surface triangles and the tetrahedra under them, which the volume integral misses
SMART_FACTOR = 0.75         # smart sizing's meshes came out 0.64 to 0.8 of the estimate in 7 meshes (it keeps the edge sizes off the surface: Mesh.MeshSizeExtendFromBoundary 0)
CELLS_SPREAD = 0.5          # the estimate is within this share of the real cells in those measurements (kept with a margin)
RAM_FIXED_MB = 800.0        # what a mesh job holds before the first cell: Python, numpy, gmsh and the OpenCASCADE kernel (fit: 794 MB)
RAM_PER_CELL_KB = 0.5       # the memory of the mesh job per cell (the slope), kilobytes (fit: 0.46 KB over 22 meshes, 55,000 to 1.4 million cells)
RAM_SPREAD = 0.25           # share of the memory estimate for what the slope does not capture (surface mesh, the sizes, the system)
SOLVE_BYTES_PER_CELL = 4200.0   # peak of the solver run per cell, measured on a 845,000-cell run (3.4 GB): docs/MESHING_GUIDE.md; the same constant as meshadvisor.BYTES_PER_CELL

MAX_LEAVES = 600_000        # the integration stops splitting boxes beyond this many leaves (the estimate then says so)
RATIO_LEAF = 1.7            # a box is one leaf when the size varies by less than this across it
GROWTH_MAX = 1.45           # the budget never raises the growth above this (the scorecard fails more than 1.6 between neighbours)
FAR_STEPS = 4.0             # ... nor the far-field size above this many times what it was
BULK_STEPS = 3.0            # ... nor the sizes of the body and the refinement boxes of the recipe

SOLID_STUDIES = ("heat_conduction", "vibration_modal", "static_structural", "topology_optimization", "aeroelastic_flutter")


# ---------------------------------------------------------------------------------------------------------------------------------- the surface
class Surface:
    """The walls of the meshed domain as points and triangles, from the display triangulation of the faces (stored with the geometry)."""

    def __init__(self, faces: list, study: str):
        self.study = study
        self.ids, self.tri_face = [], []
        V, T, off = [], [], 0
        for f in faces:
            if f.get("kind") in ("farfield", "enclosure") and study != "cht_enclosure":
                continue
            if f.get("kind") in ("farfield",):
                continue
            m = f.get("mesh") or {}
            v = np.asarray(m.get("vertices", []), float).reshape(-1, 3)
            t = np.asarray(m.get("triangles", []), np.int64).reshape(-1, 3)
            if not len(t):
                continue
            V.append(v)
            T.append(t + off)
            self.tri_face += [int(f["id"])] * len(t)
            off += len(v)
        self.V = np.vstack(V) if V else np.zeros((0, 3))
        self.T = np.vstack(T) if T else np.zeros((0, 3), np.int64)
        self.tri_face = np.asarray(self.tri_face, np.int64)
        a, b, c = (self.V[self.T[:, k]] for k in range(3))
        cr = np.cross(b - a, c - a)
        self.area = 0.5 * np.linalg.norm(cr, axis=1)
        self.normal = cr / np.maximum(2 * self.area, 1e-300)[:, None]
        self.centroid = (a + b + c) / 3.0
        # normals out of the body: in the air the stored triangles face out of the air (into the body); in a solid study the sign of the volume says
        vol = float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)
        self.normal_out = -self.normal if study == "external_aero" else (self.normal if vol >= 0 else -self.normal)
        self._ctree = None
        self._curv = None

    @property
    def ctree(self):
        if self._ctree is None:
            from scipy.spatial import cKDTree
            self._ctree = cKDTree(self.centroid) if len(self.centroid) else None
        return self._ctree

    def tree(self, spacing: float, face_ids=None):
        """A KD-tree of the surface points (of these faces) at about `spacing`, built once and kept; None when there are no points."""
        key = (None if face_ids is None else tuple(sorted(int(i) for i in face_ids)), round(math.log(max(spacing, 1e-12)) * 8))
        cache = self.__dict__.setdefault("_trees", {})
        if key not in cache:
            from scipy.spatial import cKDTree
            pts = self.samples(spacing, face_ids)
            cache[key] = cKDTree(pts) if len(pts) else None
        return cache[key]

    def samples(self, spacing: float, face_ids=None, cap: int = 200_000):
        """Points on the surface (of the given faces) at about `spacing` apart: [(n, 3)]. Spacing grows when the area would need more than `cap` points."""
        sel = np.ones(len(self.T), bool) if face_ids is None else np.isin(self.tri_face, np.asarray(list(face_ids), np.int64))
        if not sel.any():
            return np.zeros((0, 3))
        area = float(self.area[sel].sum())
        spacing = max(spacing, math.sqrt(max(area, 1e-300) / max(cap, 1)) * 1.1)
        a, b, c = (self.V[self.T[sel][:, k]] for k in range(3))
        edge = np.maximum.reduce([np.linalg.norm(b - a, axis=1), np.linalg.norm(c - b, axis=1), np.linalg.norm(a - c, axis=1)])
        n = np.maximum(np.ceil(edge / spacing).astype(int), 1)
        out = [self.centroid[sel]]
        for k in np.unique(n):
            if k == 1:
                out.append((a[n == 1] + b[n == 1] + c[n == 1]) / 3.0)
                continue
            m = n == k
            w = [(i / k, j / k) for i in range(k + 1) for j in range(k + 1 - i)]
            u, v = np.array(w).T
            P = (a[m][:, None, :] * (1 - u - v)[None, :, None] + b[m][:, None, :] * u[None, :, None] + c[m][:, None, :] * v[None, :, None])
            out.append(P.reshape(-1, 3))
        return np.vstack(out)

    def curvature_sizes(self, n_curv: float, floor: float, ceil: float):
        """(points, sizes) of the curved parts of the surface: the mesher's curvature sizing, 2 pi R / n_curv with R estimated from the angle between neighbouring
        triangles of the display triangulation (R = distance between their centres / angle). Only the places finer than `ceil`, never finer than `floor`."""
        if self._curv is None:
            self._curv = self._radii()
        cen, R = self._curv
        if not len(R):
            return np.zeros((0, 3)), np.zeros(0)
        h = np.maximum(2.0 * math.pi * R / max(n_curv, 1.0), floor)
        keep = h < 0.98 * ceil
        return cen[keep], h[keep]

    def _radii(self):
        T = self.T
        e = np.stack([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]], axis=1).reshape(-1, 2)
        e = np.sort(e, axis=1)
        tri = np.repeat(np.arange(len(T)), 3)
        key = e[:, 0].astype(np.int64) * (int(self.T.max(initial=0)) + 1) + e[:, 1]
        order = np.argsort(key, kind="stable")
        ks, ts = key[order], tri[order]
        same = np.nonzero(ks[1:] == ks[:-1])[0]
        i, j = ts[same], ts[same + 1]
        ok = (i != j) & (self.tri_face[i] == self.tri_face[j])         # (neighbours inside one face; the angle between faces is an edge, not a curvature)
        i, j = i[ok], j[ok]
        if not len(i):
            return np.zeros((0, 3)), np.zeros(0)
        cosang = np.clip(np.einsum("ij,ij->i", self.normal[i], self.normal[j]), -1.0, 1.0)
        ang = np.arccos(cosang)
        dist = np.linalg.norm(self.centroid[i] - self.centroid[j], axis=1)
        good = ang > 1e-3
        R = dist[good] / ang[good]
        cen = 0.5 * (self.centroid[i][good] + self.centroid[j][good])
        return cen, R

    def inside(self, P: np.ndarray) -> np.ndarray:
        """True for the points inside a body (the nearest facet looks away from them): used to leave the bodies out of the air, and the air out of the solids."""
        if self.ctree is None or not len(P):
            return np.zeros(len(P), bool)
        d, i = self.ctree.query(P, workers=-1)
        return np.einsum("ij,ij->i", P - self.centroid[i], self.normal_out[i]) < 0.0


# ---------------------------------------------------------------------------------------------------------------------------------- the size field
def threshold(d, s_min, d_min, d_max, s_max):
    """The size of a Gmsh Threshold field at distance d."""
    if d_max <= d_min:
        return np.where(d <= d_min, s_min, s_max)
    return s_min + (s_max - s_min) * np.clip((d - d_min) / (d_max - d_min), 0.0, 1.0)


def box_distance(P, lo, hi, rotation=None):
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    if rotation is not None:
        c = 0.5 * (lo + hi)
        P = (P - c) @ np.asarray(rotation, float)        # (R^T x for the rows: local axes)
        h = 0.5 * (hi - lo)
        return np.linalg.norm(np.maximum(np.abs(P) - h, 0.0), axis=1)
    return np.linalg.norm(np.maximum(np.maximum(lo - P, P - hi), 0.0), axis=1)


def cylinder_distance(P, start, end, radius, inner=0.0):
    s = np.asarray(start, float)
    a = np.asarray(end, float) - s
    ln = float(np.linalg.norm(a))
    u = a / max(ln, 1e-300)
    q = P - s
    t = q @ u
    rho = np.linalg.norm(q - t[:, None] * u, axis=1)
    dr = np.maximum(0.0, np.maximum(rho - radius, inner - rho)) if inner > 0 else np.maximum(0.0, rho - radius)
    da = np.maximum(0.0, np.maximum(-t, t - ln))
    return np.hypot(dr, da)


def cylinder_surface_distance(P, start, end, radius):
    """Distance to the surface of a capped cylinder (outside or inside)."""
    s = np.asarray(start, float)
    a = np.asarray(end, float) - s
    ln = float(np.linalg.norm(a))
    u = a / max(ln, 1e-300)
    q = P - s
    t = q @ u
    rho = np.linalg.norm(q - t[:, None] * u, axis=1)
    out = np.hypot(np.maximum(rho - radius, 0.0), np.maximum(0.0, np.maximum(-t, t - ln)))
    depth = np.minimum(radius - rho, np.minimum(t, ln - t))
    return np.where(out > 0, out, np.maximum(depth, 0.0))


class Field:
    """The size field of a mesh: size(P) for an array of points. Built by `sizing`; `sources` is a list of callables P -> size."""

    def __init__(self, far: float, floor: float, sources: list, notes: list | None = None):
        self.far, self.floor, self.sources, self.notes = float(far), float(floor), sources, notes or []

    def size(self, P: np.ndarray) -> np.ndarray:
        s = np.full(len(P), self.far)
        for f in self.sources:
            np.minimum(s, f(P), out=s)
        return np.maximum(s, self.floor)


def uniform_field(h: float) -> Field:
    """A field of one size everywhere (tests, the first guess)."""
    return Field(h, 0.0, [])


# ---------------------------------------------------------------------------------------------------------------------------------- the integral
def integrate(lo, hi, field: Field, keep=None, max_leaves: int = MAX_LEAVES, ratio: float = RATIO_LEAF) -> dict:
    """The number of regular tetrahedra that fill the box lo..hi with the sizes of `field` (and only where keep(P) is True): the integral of dV / (TET_VOLUME s^3),
    by an adaptive octree. {cells, leaves, evaluations, volume, truncated}."""
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    ext = hi - lo
    n0 = np.maximum(np.round(ext / (ext.max() / 4.0)).astype(int), 1)
    side = float((ext / n0).max())
    n0 = np.maximum(np.ceil(ext / side - 1e-9).astype(int), 1)
    ax = [lo[k] + side * np.arange(n0[k]) for k in range(3)]
    corner = np.stack(np.meshgrid(*ax, indexing="ij"), axis=-1).reshape(-1, 3)
    offs = np.array([[i, j, k] for i in (0, 1) for j in (0, 1) for k in (0, 1)], float)
    pts = np.vstack([np.full((1, 3), 0.5), offs])                  # centre first
    total, leaves, evals, vol_in, truncated = 0.0, 0, 0, 0.0, False
    h = side
    while len(corner):
        n = len(corner)
        P = corner[:, None, :] + pts[None, :, :] * h               # (n, 9, 3)
        # (the box may stick out of the domain by the rounding up of the root grid: points outside count as not kept)
        flat = P.reshape(-1, 3)
        S = field.size(flat).reshape(n, 9)
        evals += len(flat)
        inside = np.all((flat >= lo - 1e-12) & (flat <= hi + 1e-12), axis=1)
        if keep is not None:
            inside &= keep(flat)
        K = inside.reshape(n, 9).astype(float)
        dens = K / (TET_VOLUME * S ** 3)
        value = (0.5 * dens[:, 0] + 0.5 * dens[:, 1:].mean(axis=1)) * h ** 3
        kfrac = 0.5 * K[:, 0] + 0.5 * K[:, 1:].mean(axis=1)
        smin, smax = S.min(axis=1), S.max(axis=1)
        mixed = (K.min(axis=1) < 1.0) & (K.max(axis=1) > 0.0)
        empty = K.max(axis=1) == 0.0
        small = h <= (1.0 if keep is not None else 0.8) * smin
        smooth = smax <= ratio * smin
        leaf = empty | (smooth & ~mixed) | small
        if len(corner) + leaves > max_leaves:
            leaf[:] = True
            truncated = True
        total += float(value[leaf].sum())
        vol_in += float((kfrac[leaf] * h ** 3).sum())
        leaves += int(leaf.sum())
        split = corner[~leaf]
        if not len(split):
            break
        h *= 0.5
        corner = (split[:, None, :] + offs[None, :, :] * h).reshape(-1, 3)
    return {"cells": total, "leaves": leaves, "evaluations": evals, "volume": vol_in, "truncated": truncated}


# ---------------------------------------------------------------------------------------------------------------------------------- the sizing of a project
_SURFACES: dict = {}
_LOCK = threading.Lock()


def surface_of(pr) -> Surface:
    """The Surface of the project's geometry, built once per geometry."""
    key = (pr.id, pr.data["geometry"].get("updated"), pr.data["geometry"].get("status"), pr.study)
    with _LOCK:
        s = _SURFACES.get(key)
        if s is None:
            if len(_SURFACES) > 4:
                _SURFACES.pop(next(iter(_SURFACES)))
            s = _SURFACES[key] = Surface(pr.faces(), pr.study)
    return s


def smallest_feature(faces: list, L: float) -> float:
    """The width of the thinnest face of the parts (as the mesher sees it; edgectl's gmsh_worker.smallest_feature does the same)."""
    from .gmsh_worker import face_width
    feat = [face_width(f) for f in faces if f["kind"] not in ("farfield", "enclosure") and face_width(f) > L * 2e-3]
    return min(feat) if feat else L / 20


def wall_size(ms: dict, study: str, feature: float, L: float, h_body: float, rc: dict | None) -> float:
    """The cell size at the walls, as gmsh_worker.task_mesh sets it (edgectl's gmsh_worker.aero_wall_size is this function)."""
    from .gmsh_worker import FEATURE_CELLS, PRESETS
    n_per, _ = PRESETS.get(ms.get("preset", "medium"), PRESETS["medium"])
    if study == "external_aero":
        n_feat = FEATURE_CELLS.get(ms.get("preset", "medium"), 4)
        if rc:
            return float(ms.get("size_min") or rc["h_surf"])
        return float(ms.get("size_min") or min(max(feature / n_feat, L / (4 * n_per)), h_body / 2))
    floor = L / ((8 if study in SOLID_STUDIES else 3) * n_per)
    return float(ms.get("size_min") or min(max(feature / 2.0, floor), h_body / 2))


def sizing(study: str, faces: list, info: dict, ms: dict, rc: dict | None = None, rotors=(), zones=(), surf: Surface | None = None) -> dict:
    """The size field of the mesh the mesher would make for these settings, and the region it fills: {field, region{lo, hi, mode}, h_wall, h_body, far, growth,
    notes}. `ms` is the mesh settings as the mesher gets them (after the recipe and the budget), `rc` the recipe of smart sizing."""
    from .gmsh_worker import FEATURE_CELLS, PRESETS, SOLID_CURVATURE, face_extents, face_width
    surf = surf or Surface(faces, study)
    L = float(info["ref_length"])
    preset = ms.get("preset", "medium")
    n_per, n_curv = PRESETS.get(preset, PRESETS["medium"])
    if study in SOLID_STUDIES:
        n_curv = SOLID_CURVATURE.get(preset, n_curv)
    h_body = float(ms.get("size_max") or L / n_per)
    if rc:
        h_body = float(ms.get("size_max") or rc["h_near"])
        n_curv = int(rc.get("n_curv", n_curv))
    feature = smallest_feature(faces, L)
    h_wall = wall_size(ms, study, feature, L, h_body, rc)
    growth = float(rc["growth"]) if rc else float(ms.get("growth", 1.25))
    notes: list = []
    thin: dict = {}
    if study == "external_aero" and not ms.get("size_min"):
        n_feat = FEATURE_CELLS.get(preset, 4)
        h_thin_min = h_wall / 3 if rc else L / (12 * n_per)
        for f in faces:
            if f["kind"] in ("farfield", "enclosure"):
                continue
            w = face_width(f)
            if L * 2e-3 < w and w / n_feat < 0.9 * h_wall:
                thin[int(f["id"])] = max(w / n_feat, h_thin_min)
    small: dict = {}
    if study not in SOLID_STUDIES and not ms.get("size_min"):
        local = dict(thin)
        for r in ms.get("refinements", []):
            for i in r["faces"]:
                local[int(i)] = min(local.get(int(i), 1e30), float(r["size"]))
        for f in faces:
            if f["kind"] in ("farfield", "enclosure"):
                continue
            _, w, long = face_extents(f)
            h = min(local.get(f["id"], h_wall), h_wall)
            if long < 2 * h:
                continue
            if w < 0.5 * h and long < 3 * h and long < 8 * max(w, L * 2e-4):
                small[int(f["id"])] = max(w, L * 2e-4)
    # the far field: a box of the domain / 12 in a flow, the body size elsewhere
    dom = info.get("domain") or {}
    if study == "external_aero" and dom.get("box_min") is not None:
        bmin, bmax = np.asarray(dom["box_min"], float), np.asarray(dom["box_max"], float)
        far = float(max(bmax - bmin)) / 12
        region = {"lo": bmin, "hi": bmax, "mode": "outside"}
    else:
        bb = np.asarray(info["bbox"], float)
        far = h_body
        region = {"lo": bb[:3], "hi": bb[3:], "mode": "inside" if study in SOLID_STUDIES else "all"}
    if ms.get("far_size"):
        far = float(ms["far_size"])
    smallest = [h_wall * 0.5] + [s * 0.5 for s in thin.values()]
    for r in ms.get("regions", []):
        smallest.append(float(r["size"]) * 0.5)
    for r in ms.get("refinements", []):
        smallest.append(float(r["size"]) * 0.5)
    for rz in rotors:
        smallest.append(float(rz.get("thickness", 0.1 * rz["radius"])) / 2 * 0.9)
    for z in zones:
        smallest.append(float(z["radius"]) / 40)
        if z.get("cell_size"):
            smallest.append(float(z["cell_size"]) * 0.5)
    floor = min(smallest)
    src: list = []

    def wall_source(ids, size):
        """Distance to the walls (the faces `ids`, all when None), the Threshold field of the mesher: the size up to `size` away, then growing to the far size."""
        n = math.log(max(far / size, 1.0001)) / math.log(growth)
        d_max = size * (growth ** n - 1) / (growth - 1)

        def f(P, ids=ids, size=size, d_max=d_max):
            t = surf.tree(0.7 * size, ids)
            if t is None:
                return np.full(len(P), 1e30)
            d, _ = t.query(P, workers=-1)
            return threshold(d, size, size, d_max, far)
        return f

    wall_ids = [int(f["id"]) for f in faces if f["kind"] not in ("farfield", "enclosure")] if study not in SOLID_STUDIES else None
    src.append(wall_source(wall_ids, h_wall))
    by: dict = {}
    for i, s in thin.items():
        by.setdefault(round(s, 6), []).append(i)
    for i, s in small.items():
        by.setdefault(round(s, 7), []).append(i)
    for s, ids in by.items():
        src.append(wall_source(ids, s))
    for r in ms.get("refinements", []):
        ids, sz = [int(x) for x in r["faces"]], float(r["size"])

        def fr(P, ids=ids, sz=sz):
            t = surf.tree(0.7 * sz, ids)
            if t is None:
                return np.full(len(P), 1e30)
            d, _ = t.query(P, workers=-1)
            return threshold(d, sz, sz, sz * 10, far)
        src.append(fr)
    # curved faces: the mesher's curvature sizing (MeshSizeFromCurvature) made finer than the wall size on small radii, spread into the volume with the growth
    extend = not (rc and rc.get("extend_boundary") is False)         # (smart sizing keeps the fine surface cells of the edges on the surface: MeshSizeExtendFromBoundary 0)
    cen, hc = surf.curvature_sizes(float(ms.get("curvature_elements", n_curv)), floor, h_wall) if extend else (np.zeros((0, 3)), np.zeros(0))
    if len(cen):
        tc = {}

        def fc(P, cen=cen, hc=hc, tc=tc):
            if "t" not in tc:
                from scipy.spatial import cKDTree
                tc["t"] = cKDTree(cen)
            d, i = tc["t"].query(P, workers=-1)
            return hc[i] + (growth - 1.0) * d
        src.append(fc)
    # boxes: the recipe's near field and wake (Thickness = how far the size takes to grow), the old sizing's box, the user's regions, rotors, zones
    def transition(sz):
        return max((far - sz) / (growth - 1.0), 8 * sz) if rc else sz * 8

    def ramp(vin, d, thick):
        return vin + (far - vin) * np.minimum(1.0, d / max(thick, 1e-300))

    if study == "external_aero":
        if rc:
            for rb in rc["boxes"]:
                sz = float(rb["size"])
                thick = max((far - sz) / (growth - 1.0), sz)
                src.append(lambda P, rb=rb, sz=sz, thick=thick: ramp(sz, box_distance(P, rb["min"], rb["max"]), thick))
        else:
            pb = np.asarray(info["parts_bbox"], float)
            pad = 0.5 * L
            src.append(lambda P: ramp(h_body * 1.5, box_distance(P, pb[:3] - pad, pb[3:] + pad), L))
        for rz in rotors:
            c = np.asarray(rz["center"], float)
            a = np.asarray(rz["axis"], float)
            a = a / np.linalg.norm(a)
            R = float(rz["radius"])
            th = float(rz.get("thickness", 0.1 * R))
            n_wake = {"quick": 5, "coarse": 5, "medium": 8, "fine": 11, "very_fine": 15}.get(preset, 8)
            h_disk = min(h_body, th / 2, R / n_wake)
            w = np.asarray(rz.get("wake_dir") or (-a).tolist(), float)
            w = w / np.linalg.norm(w)
            for ctr, half, rad, sz in ((c, a * th, R * 1.1, h_disk), (c + w * 3 * R, w * 3 * R, R * 1.15, R / n_wake)):
                src.append(lambda P, ctr=ctr, half=half, rad=rad, sz=sz:
                           np.where(cylinder_distance(P, ctr - half, ctr + half, rad) <= 0, sz, far))
    for r in ms.get("regions", []):
        sz = float(r["size"])
        if r.get("shape") == "cylinder":
            src.append(lambda P, r=r, sz=sz: ramp(sz, cylinder_distance(P, r["start"], r["end"], r["radius"], r.get("inner_radius", 0.0)), transition(sz)))
        else:
            src.append(lambda P, r=r, sz=sz: ramp(sz, box_distance(P, r["min"], r["max"], r.get("rotation")), transition(sz)))
    for z in zones:
        ax = np.asarray(z["axis"], float)
        half = ax * float(z["length"]) / 2
        c = np.asarray(z["center"], float)
        R = float(z["radius"])
        hz = min(h_body, 2 * math.pi * R / {"quick": 36, "coarse": 48, "medium": 64, "fine": 96, "very_fine": 128}.get(preset, 36),
                 float(z.get("cell_size") or 1e9))
        src.append(lambda P, c=c, half=half, R=R, hz=hz: threshold(cylinder_surface_distance(P, c - half, c + half, R), hz, hz, hz * 12, far))
        if z.get("cell_size"):
            cs = float(z["cell_size"])
            src.append(lambda P, c=c, half=half, R=R, cs=cs: np.where(cylinder_distance(P, c - half, c + half, R) <= 0, cs, far))
    if study not in SOLID_STUDIES and study != "external_aero":
        src.append(lambda P: np.full(len(P), h_body))
    # the cells that sit on the walls: one or more per surface triangle of the mesh, whatever the volume field says (the surface of a body at the wall size h has
    # about area / h^2 triangles, and the mesher makes finer ones where the surface curves)
    local = dict(thin)
    local.update(small)
    for r in ms.get("refinements", []):
        for i in r["faces"]:
            local[int(i)] = min(local.get(int(i), 1e30), float(r["size"]))
    units = 0.0
    if len(surf.T):
        for fid in np.unique(surf.tri_face):
            a = float(surf.area[surf.tri_face == fid].sum())
            units += a / min(local.get(int(fid), h_wall), h_wall) ** 2
    if study == "cht_enclosure":
        notes.append("Narrow air gaps between parts are refined by the mesher and are not in this estimate: it can be a little low for finned parts.")
    return {"field": Field(far, floor, src, notes), "region": region, "h_wall": h_wall, "h_body": h_body, "far": far, "growth": growth, "notes": notes,
            "n_curv": n_curv, "surface": surf, "surface_units": units, "smart": bool(rc)}


# ---------------------------------------------------------------------------------------------------------------------------------- the memory model
def ram_model(cells: float) -> dict:
    """Memory in MB of a mesh job with `cells` cells: the fixed part (Python and gmsh) plus the slope per cell. {fixed_mb, slope_mb, mesh_mb}."""
    slope = cells * RAM_PER_CELL_KB / 1024.0
    return {"fixed_mb": RAM_FIXED_MB, "slope_mb": slope, "mesh_mb": RAM_FIXED_MB + slope}


def solve_mb(cells: float) -> float:
    """The peak memory of the run on a mesh of this many cells (earlier measurement, 4,200 bytes a cell: not re-measured here)."""
    return cells * SOLVE_BYTES_PER_CELL / 2 ** 20


# ---------------------------------------------------------------------------------------------------------------------------------- the edge term
def edge_extra_cells(pr, sz: dict, ms: dict, rc: dict | None) -> dict:
    """The extra cells of the sharp-edge refinement: {cells, low, high, source, note}. THE PLUG for the edge term.

    Finding the edges needs the CAD kernel: simwb/edgepreview.py runs the search in the mesher's worker (once per geometry, kept) and
    gmsh_worker.edge_refine_plan gives the calibrated estimate of the extra cells (EDGE_ESTIMATE_SPREAD is its spread). That is the first choice below. When the
    search is not possible or failed, the refinement is bounded by its cap: the term is 0 to the cap (150,000 cells unless set), taken as half of it, and the note says so."""
    er = ms.get("edge_refine") or ({"factor": rc["edge_refinement"]} if rc and rc.get("edge_refinement") and not ms.get("no_edge_refine") else None)
    if not er:
        return {"cells": 0.0, "low": 0.0, "high": 0.0, "source": None, "note": None}
    try:                                                    # (after the merge of the edge controls: the calibrated plan, when the search of the edges is done)
        from . import edgepreview                           # noqa: F401  (absent on this branch)
        pv = edgepreview.preview(pr, scan=True, wait=True)
        plan = pv.get("plan") if pv else None
        if plan and plan.get("applied"):
            from . import gmsh_worker
            est, sp = float(plan["estimated_extra_cells"]), float(gmsh_worker.edge_spread(pr.study))
            return {"cells": est, "low": est * (1 - sp), "high": est * (1 + sp), "source": "edge plan", "note": None}
    except ImportError:
        pass
    except Exception:  # noqa: BLE001 - a help: the bound below stays
        pass
    cap = float(er.get("max_cells", 150_000))
    return {"cells": 0.5 * cap, "low": 0.0, "high": cap, "source": "cap",
            "note": f"The refinement of the sharp edges adds up to {cap:,.0f} extra cells (its cap); the estimate takes half of that."}


# ---------------------------------------------------------------------------------------------------------------------------------- the estimate
def free_ram_mb() -> float | None:
    try:
        from .memlimit import memory
        return memory()[1] / 2 ** 20
    except Exception:  # noqa: BLE001
        return None


def estimate_sizing(sz: dict, extra: dict | None = None) -> dict:
    """{cells, cells_low, cells_high, ram: {...}} for a `sizing` result and the extra cells of the edges."""
    r = sz["region"]
    keep = None
    if r["mode"] in ("outside", "inside"):
        surf = sz["surface"]
        if len(surf.T):
            keep = (lambda P: ~surf.inside(P)) if r["mode"] == "outside" else surf.inside
    out = integrate(r["lo"], r["hi"], sz["field"], keep)
    base = (CELLS_CALIBRATION * out["cells"] + (SURFACE_CELLS_SOLID if r["mode"] == "inside" else SURFACE_CELLS) * sz.get("surface_units", 0.0)) * (SMART_FACTOR if sz.get("smart") else 1.0)
    ex = extra or {"cells": 0.0, "low": 0.0, "high": 0.0}
    cells = base + ex["cells"]
    low = base * (1 - CELLS_SPREAD) + ex["low"]
    high = base * (1 + CELLS_SPREAD) + ex["high"]
    m = ram_model(cells)
    lo_mb = ram_model(low)["mesh_mb"] * (1 - RAM_SPREAD / 2)
    hi_mb = ram_model(high)["mesh_mb"] * (1 + RAM_SPREAD / 2)
    return {"cells": cells, "cells_low": low, "cells_high": high, "volume_m3": out["volume"], "leaves": out["leaves"], "truncated": out["truncated"],
            "edge_cells": ex["cells"], "edge_source": ex.get("source"), "integral": out["cells"], "surface_units": sz.get("surface_units", 0.0),
            "ram": {**m, "mesh_low_mb": lo_mb, "mesh_high_mb": hi_mb, "solve_mb": solve_mb(cells), "solve_low_mb": solve_mb(low), "solve_high_mb": solve_mb(high)}}


# ---------------------------------------------------------------------------------------------------------------------------------- a project
def base_inputs(pr, ms: dict | None = None) -> tuple:
    """(mesh settings, recipe) as the mesher gets them before a budget is applied: the stored settings, with the recipe of smart sizing (and the edge
    refinement it asks for) when that is on. The mesher (`Project._mesh_inputs`) and the estimate both call this, so they cannot differ."""
    from . import meshadvisor
    ms = dict(ms if ms is not None else pr.data["mesh"])
    rc = None
    if pr.study == "external_aero" and ms.get("smart"):
        an = meshadvisor.analyse(pr.data, pr.faces(), pr.data["geometry"]["info"])
        rc = meshadvisor.recipe(an, ms.get("preset", "medium"), ms.get("growth"))
        ms["recipe"] = rc
        if not ms.get("edge_refine") and rc.get("edge_refinement") and not ms.get("no_edge_refine"):
            ms["edge_refine"] = {"factor": rc["edge_refinement"]}
    return ms, rc


def _zones(pr) -> list:
    from . import studies
    return [dict(z, axis=list(studies.AXES[z["axis"]])) for z in pr.data.get("moving_zones") or []]


def evaluate(pr, ms: dict, rc: dict | None, surf: Surface | None = None) -> dict:
    """The estimate for these mesh settings: {cells, cells_low, cells_high, ram{...}, h_wall, h_body, far, growth, edge, notes}."""
    info = pr.data["geometry"]["info"]
    surf = surf or surface_of(pr)
    sz = sizing(pr.study, pr.faces(), info, ms, rc, rotors=pr._rotors_with_wake() if pr.study == "external_aero" else (), zones=_zones(pr), surf=surf)
    edge = edge_extra_cells(pr, sz, ms, rc)
    out = estimate_sizing(sz, edge)
    out.update(h_wall=sz["h_wall"], h_body=sz["h_body"], far=sz["far"], growth=sz["growth"], notes=list(sz["notes"]) + ([edge["note"]] if edge.get("note") else []))
    if pr.study in SOLID_STUDIES:
        out["ram"]["solve_mb"] = out["ram"]["solve_low_mb"] = out["ram"]["solve_high_mb"] = None      # (the run of a finite element study is not measured)
    return out


def peak_mb(est: dict, which: str = "mid") -> float:
    """The memory the budget is held against: the larger of the mesh job and the run (the run is not estimated for the solid studies)."""
    r = est["ram"]
    key = {"mid": ("mesh_mb", "solve_mb"), "low": ("mesh_low_mb", "solve_low_mb"), "high": ("mesh_high_mb", "solve_high_mb")}[which]
    return max(r[key[0]] or 0.0, r[key[1]] or 0.0)


def budget_to_mb(budget: dict | None, free_mb: float | None = None) -> float | None:
    """The budget in MB: {"mode": "mb", "value": 4000} or {"mode": "percent", "value": 50} (of the free memory now); None when it is off or cannot be worked out."""
    if not budget or not budget.get("on", True):
        return None
    v = float(budget.get("value") or 0)
    if v <= 0:
        return None
    if budget.get("mode") == "percent":
        free = free_mb if free_mb is not None else free_ram_mb()
        return None if free is None else v / 100.0 * free
    return v


def bulk_settings(ms: dict, rc: dict | None, base: dict, t: float) -> tuple:
    """The mesh settings coarsened by the fraction t (0 = as set, 1 = as coarse as the budget may make the bulk): the far-field size, the body size (the
    largest cell, and the near field and wake of the recipe) and the growth. The cells at the walls, at thin and small faces, at the part sizings, at the
    sharp edges and in the regions you drew are not touched. Returns (ms, rc, changes) with the changes as {name: (from, to)} in metres."""
    ms2 = dict(ms)
    rc2 = None
    far0, body0, g0 = base["far"], base["h_body"], base["growth"]
    f_far, f_body = FAR_STEPS ** t, BULK_STEPS ** t
    g1 = max(g0, g0 + t * (GROWTH_MAX - g0))
    ms2["far_size"] = far0 * f_far
    ch = {"far_size": (far0, far0 * f_far)}
    if rc:
        import copy
        rc2 = copy.deepcopy(rc)
        rc2["growth"] = g1
        for k in ("h_near", "h_wake"):
            rc2[k] = rc[k] * f_body
        for b2, b in zip(rc2["boxes"], rc["boxes"]):
            b2["size"] = b["size"] * f_body
        ms2["recipe"] = rc2
        ch["near_field"] = (rc["h_near"], rc2["h_near"])
        ch["wake"] = (rc["h_wake"], rc2["h_wake"])
        if ms.get("size_max"):
            ms2["size_max"] = float(ms["size_max"]) * f_body
    else:
        ms2["size_max"] = body0 * f_body
        ms2["growth"] = g1
        ch["body_size"] = (body0, ms2["size_max"])
    ch["growth"] = (g0, g1)
    return ms2, rc2, ch


def fit(pr, ms: dict, rc: dict | None, budget_mb: float, surf: Surface | None = None, steps: int = 6, before: dict | None = None) -> dict:
    """Coarsen the bulk until the estimate fits `budget_mb`: {status: 'fits' | 'coarsened' | 'refused', ms, rc, changes, before, after, smallest, t}. The walls, thin
    and small faces, part sizings, edge refinement and the regions you drew keep their sizes. 'refused': even the coarsest bulk does not fit; `after` is that
    mesh (the smallest this setup can reach)."""
    surf = surf or surface_of(pr)
    before = before or evaluate(pr, ms, rc, surf)
    out = {"budget_mb": budget_mb, "before": before, "after": before, "ms": ms, "rc": rc, "changes": {}, "t": 0.0}
    if peak_mb(before) <= budget_mb:
        out["status"] = "fits"
        return out
    base = {"far": before["far"], "h_body": before["h_body"], "growth": before["growth"]}
    hi = bulk_settings(ms, rc, base, 1.0)
    est_hi = evaluate(pr, hi[0], hi[1], surf)
    if peak_mb(est_hi) > budget_mb:
        out.update(status="refused", smallest=est_hi, t=1.0, ms=hi[0], rc=hi[1], changes=hi[2], after=est_hi)
        return out
    lo_t, hi_t, best = 0.0, 1.0, (1.0, hi, est_hi)
    for _ in range(steps):
        mid = 0.5 * (lo_t + hi_t)
        cand = bulk_settings(ms, rc, base, mid)
        e = evaluate(pr, cand[0], cand[1], surf)
        if peak_mb(e) <= budget_mb:
            hi_t, best = mid, (mid, cand, e)
        else:
            lo_t = mid
    t, cand, e = best
    out.update(status="coarsened", ms=cand[0], rc=cand[1], changes=cand[2], after=e, t=t)
    return out


# ---------------------------------------------------------------------------------------------------------------------------------- words
def sig(n: float, d: int = 2) -> float:
    """n rounded to d significant digits."""
    if not n:
        return 0.0
    return round(n, d - 1 - int(math.floor(math.log10(abs(n)))))


def fmt_cells(n: float) -> str:
    """'1.2 million', '850,000' (two significant digits: the estimate has no more)."""
    n = sig(n)
    if n >= 1e6:
        return f"{n / 1e6:.1f} million".replace(".0 million", " million")
    return f"{int(n):,}"


def fmt_mb(mb: float) -> str:
    """'3.4 GB', '640 MB'."""
    if mb >= 1000:
        return f"{mb / 1024:.1f} GB"
    return f"{int(sig(mb, 2))} MB"


def fmt_len(m: float) -> str:
    """A length in metres as mm with three significant digits."""
    return f"{sig(m * 1000, 3):g} mm"


def _budget_words(b: dict | None, mb: float | None) -> str:
    if mb is None:
        return ""
    if b and b.get("mode") == "percent":
        return f"{fmt_mb(mb)} ({b['value']:g} % of the free memory)"
    return fmt_mb(mb)


def _list(parts: list) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


_PRESET_ORDER = ["very_fine", "fine", "medium", "coarse", "quick"]
_PRESET_NAME = {"quick": "Quick", "coarse": "Coarse", "medium": "Medium", "fine": "Fine", "very_fine": "Very fine"}


def coarser_preset_that_fits(pr, ms: dict, budget_mb: float, surf: Surface) -> dict | None:
    """The finest resolution preset coarser than the one set whose mesh (bulk as set) fits the budget: {preset, name, est}; None when none does."""
    cur = ms.get("preset", "medium")
    for p in (_PRESET_ORDER[_PRESET_ORDER.index(cur) + 1:] if cur in _PRESET_ORDER else []):
        try:
            m2, r2 = base_inputs(pr, dict(ms, preset=p))
            e = evaluate(pr, m2, r2, surf)
        except Exception:  # noqa: BLE001
            continue
        if peak_mb(e) <= budget_mb:
            return {"preset": p, "name": _PRESET_NAME[p], "est": e}
    return None


def budget_plan(pr, ms: dict, rc: dict | None, surf: Surface | None = None, free_mb: float | None = None, before: dict | None = None) -> dict:
    """Apply the memory budget in `ms['budget']`: the fit (see `fit`) with the words for the user: {status: 'off' | 'fits' | 'coarsened' | 'refused', message, ms, rc,
    changes, budget_mb, cells_before, cells_after, before, after, suggestion}."""
    surf = surf or surface_of(pr)
    b = ms.get("budget")
    mb = budget_to_mb(b, free_mb)
    if mb is None:
        return {"status": "off", "message": "", "ms": ms, "rc": rc, "changes": {}, "budget_mb": None}
    f = fit(pr, ms, rc, mb, surf, before=before)
    f["budget"] = b
    bw = _budget_words(b, mb)
    after, before = f["after"], f["before"]
    f["cells_before"], f["cells_after"] = before["cells"], after["cells"]
    if f["status"] == "fits":
        f["message"] = f"The mesh fits the budget of {bw}: about {fmt_cells(before['cells'])} cells, about {fmt_mb(peak_mb(before))}."
        return f
    ch = f["changes"]
    names = {"far_size": "the far field", "near_field": "the near field", "wake": "the wake", "body_size": "the cells away from the body"}
    parts = [f"{names[k]} from {fmt_len(v[0])} to {fmt_len(v[1])}" for k, v in ch.items() if k in names]
    g0, g1 = ch["growth"]
    if g1 > g0 * 1.0005:
        parts.append(f"the growth from {g0:.2f} to {g1:.2f}")
    kept = "The cells at the walls, thin parts, part sizings, sharp edges and the regions you drew keep their sizes."
    if f["status"] == "coarsened":
        f["message"] = (f"Coarsened {_list(parts)} to fit {bw}: about {fmt_cells(after['cells'])} cells, about {fmt_mb(peak_mb(after))}. {kept}")
        return f
    need = peak_mb(after, "mid")
    sug = coarser_preset_that_fits(pr, ms, mb, surf)
    f["suggestion"] = {"preset": sug["preset"], "cells": sug["est"]["cells"], "mb": peak_mb(sug["est"])} if sug else None
    todo = [f"raise the budget to at least {fmt_mb(need * 1.05)}"]
    if sug:
        todo.append(f"choose the {sug['name']} resolution (about {fmt_cells(sug['est']['cells'])} cells, about {fmt_mb(peak_mb(sug['est']))})")
    else:
        todo.append("choose a coarser resolution or larger cells at the walls (Advanced sizes: Smallest cell)")
    todo.append("close other programs to free memory" if (b or {}).get("mode") == "percent" else "switch the budget off")
    f["message"] = (f"The mesh does not fit the budget of {bw}. With {_list(parts)} as far as the budget allows, the smallest it can reach is about "
                    f"{fmt_cells(after['cells'])} cells and about {fmt_mb(need)}. {kept} To mesh it: " + ", or ".join(todo[:2]) + (f", or {todo[2]}" if len(todo) > 2 else "") + ".")
    return f


def report(pr, budget=None) -> dict:
    """What the Mesh step shows and the project API returns: the estimate of the mesh from the stored settings (and of what the budget makes of it) before
    there is a mesh. JSON-safe."""
    import time
    out = {"available": False, "reason": None, "method": None}
    if pr.data["geometry"].get("status") != "ready" or not pr.data["geometry"].get("info"):
        out["reason"] = "Import the geometry first."
        return out
    m = pr.data["mesh"]
    if m.get("method") == "hex" and pr.study == "external_aero":
        out.update(method="hex", reason="The estimate is for the tetrahedral mesh. The hex-dominant mesh (snappyHexMesh) is not estimated: its cell count depends on how "
                                         "the cut of the surface goes, which is not known before it is made.")
        return out
    t0 = time.time()
    ms, rc = base_inputs(pr)
    surf = surface_of(pr)
    b = budget if budget is not None else ms.get("budget")
    base = evaluate(pr, ms, rc, surf)
    free = free_ram_mb()
    out.update(available=True, method="tet", estimate=_plain(base), free_mb=free, notes=list(base["notes"]))
    bmb = budget_to_mb(b, free)
    out["budget"] = {"on": bool(b and b.get("on", True) and bmb), "mode": (b or {}).get("mode"), "value": (b or {}).get("value"), "mb": bmb}
    if bmb is not None:
        plan = budget_plan(pr, dict(ms, budget=dict(b, on=True)), rc, surf, free, before=base)
        out["budget"].update(status=plan["status"], message=plan["message"], changes={k: list(v) for k, v in plan.get("changes", {}).items()},
                             suggestion=plan.get("suggestion"))
        out["applied"] = _plain(plan["after"]) if plan["status"] != "fits" else out["estimate"]
    out["seconds"] = round(time.time() - t0, 2)
    return out


def _plain(e: dict) -> dict:
    """The numbers of an estimate for the API: cells, the range, the memory in MB."""
    r = e["ram"]

    def mb(v):
        return None if v is None else int(round(v))
    return {"cells": int(round(e["cells"])), "cells_low": int(round(e["cells_low"])), "cells_high": int(round(e["cells_high"])),
            "mesh_mb": mb(r["mesh_mb"]), "mesh_low_mb": mb(r["mesh_low_mb"]), "mesh_high_mb": mb(r["mesh_high_mb"]),
            "fixed_mb": mb(r["fixed_mb"]), "slope_mb": mb(r["slope_mb"]),
            "run_mb": mb(r["solve_mb"]), "run_low_mb": mb(r["solve_low_mb"]), "run_high_mb": mb(r["solve_high_mb"]),
            "peak_mb": mb(peak_mb(e)), "peak_low_mb": mb(peak_mb(e, "low")), "peak_high_mb": mb(peak_mb(e, "high")),
            "wall_size": e["h_wall"], "body_size": e["h_body"], "far_size": e["far"], "growth": e["growth"], "edge_cells": int(round(e["edge_cells"])),
            "leaves": e["leaves"], "truncated": e["truncated"]}
