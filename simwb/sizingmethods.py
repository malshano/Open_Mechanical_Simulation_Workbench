"""The sizing methods of the Mesh step: selectable, explained size fields with a cap on the cells they may add.

    wake       a box behind the body along the flow, sized from the body (#34; the box of smart sizing's recipe is the same mechanism, meshadvisor.wake_box)
    curvature  cells per circle on curved faces, spread into the volume with the mesh growth (#35)
    proximity  cells across small gaps between walls (the air side) and across thin walls (the material side) (#35)
    coarsen    a larger growth beyond the first cells of the walls: fewer cells in what the walls would otherwise over-resolve (#38)

The stored form is mesh['sizing_methods'] = {method: {on, ...options}}; `check` validates a call and merges it with what is stored. `plan` is the decision (which
faces get which cell size, the cap applied) that the mesher (gmsh_worker.task_mesh) and the memory planner (meshplan.sizing) both take, so the estimate before
meshing is of what the mesher does. Pure numpy: no Gmsh, no Qt."""
from __future__ import annotations

import math

import numpy as np

METHODS = ("wake", "curvature", "proximity", "coarsen")
CAP_RANGE = (1_000.0, 20_000_000.0)
CAP_DEFAULT = 300_000                                   # the most extra cells a method may add unless told otherwise
SIZE_STEP = 1.15                                        # the cell sizes of the faces are rounded to steps of this ratio (one size field for each)
CELLS_PER_METHOD_CAL = 0.5                              # real extra cells / the closed form of `bin_extra_cells`: 0.21 to 0.38 on a 1.5 mm gap between two plates (the tubes of the two faces overlap), 0.55 to 0.74 on curved faces (docs/MESHING_GUIDE.md)
OPTIONS = {
    "wake": {"length": (0.5, 20.0, 4.0), "side": (0.0, 3.0, 0.4), "size_factor": (1.5, 40.0, 5.0), "size_mm": (0.001, 100000.0, None), "max_cells": (*CAP_RANGE, CAP_DEFAULT)},
    "curvature": {"elements": (6.0, 90.0, None), "min_size_mm": (0.001, 100000.0, None), "max_cells": (*CAP_RANGE, CAP_DEFAULT)},
    "proximity": {"cells_across": (2.0, 12.0, 3.0), "min_size_mm": (0.001, 100000.0, None), "max_gap_mm": (0.001, 100000.0, None), "max_cells": (*CAP_RANGE, CAP_DEFAULT)},
    "coarsen": {"growth": (1.25, 1.6, 1.5), "keep_cells": (3.0, 30.0, 8.0)},
}
LABELS = {"length": "wake length", "side": "wake side margin", "size_factor": "wake cell size factor", "size_mm": "wake cell size", "max_cells": "cell cap",
          "elements": "cells per circle", "min_size_mm": "smallest cell", "cells_across": "cells across a gap", "max_gap_mm": "widest gap",
          "growth": "growth beyond the first cells", "keep_cells": "cells kept at the wall growth"}


def check(sm, base: dict | None = None, study: str | None = None) -> dict:
    """The stored form of a sizing-methods setting: {method: {on, ...options}} from {method: True | False | {options}}; ValueError says what is wrong. What the
    call does not name keeps its value in `base` (the stored setting)."""
    from . import studies

    out = {k: dict(v) for k, v in (base or {}).items() if k in METHODS}
    if not isinstance(sm, dict):
        raise ValueError("sizing_methods is a dictionary {method: true | false | {options}} with the methods " + ", ".join(METHODS))
    for name, v in sm.items():
        if name not in METHODS:
            raise ValueError(f"Unknown sizing method '{name}': the methods are {', '.join(METHODS)}")
        if name == "wake" and study is not None and study != "external_aero":
            raise ValueError("The wake box is for external aerodynamics (a body in a flow)")
        rec = dict(out.get(name) or {})
        if isinstance(v, bool) or v in (0, 1, None):
            rec["on"] = bool(v)
        elif isinstance(v, dict):
            for k, x in v.items():
                if k == "on":
                    rec["on"] = bool(x)
                    continue
                if k not in OPTIONS[name]:
                    raise ValueError(f"Unknown option '{k}' of the {name} method: {', '.join(OPTIONS[name])}")
                if x in (None, ""):
                    rec.pop(k, None)
                    continue
                lo, hi, _d = OPTIONS[name][k]
                val = studies.finite(x, LABELS.get(k, k))
                if not lo <= val <= hi:
                    raise ValueError(f"The {LABELS.get(k, k)} of the {name} method must be between {lo:g} and {hi:g}")
                rec[k] = val
            rec.setdefault("on", True)
        else:
            raise ValueError(f"The {name} method is true, false or a dictionary of options")
        out[name] = rec
    return out


def opts(ms: dict, name: str) -> dict | None:
    """The options of a method that is on (the defaults filled in), None when it is off."""
    rec = (ms.get("sizing_methods") or {}).get(name)
    if not rec or not rec.get("on"):
        return None
    out = {k: d for k, (_lo, _hi, d) in OPTIONS[name].items()}
    out.update({k: v for k, v in rec.items() if k != "on"})
    return out


def any_on(ms: dict) -> bool:
    return any(opts(ms, n) for n in METHODS)


# ---------------------------------------------------------------------------------------------------------------------------------- the cost of a refined face
def bin_extra_cells(area: float, s: float, h_wall: float, growth: float, solid: bool = False) -> float:
    """The cells that cells of size s on `area` of wall add beyond the wall size h_wall, in the terms of the memory planner (meshplan): the volume under the
    face where the size grows from s back to h_wall with the mesh growth (CELLS_CALIBRATION x the regular tetrahedra of the size field, one side of the wall)
    plus the cells on the wall (SURFACE_CELLS per area / size^2)."""
    from . import meshplan

    if s >= h_wall or area <= 0:
        return 0.0
    g1 = max(growth - 1.0, 1e-6)
    r_max = (h_wall - s) / g1
    vol = ((s ** -2 - h_wall ** -2) / (2.0 * g1) - r_max / h_wall ** 3) / meshplan.TET_VOLUME            # (per area: the integral of 1/(V size^3) less the wall size's)
    wall = (meshplan.SURFACE_CELLS_SOLID if solid else meshplan.SURFACE_CELLS) * (s ** -2 - h_wall ** -2)
    return float(area * (meshplan.CELLS_CALIBRATION * vol + wall) * CELLS_PER_METHOD_CAL)


def _fit_cap(face_sizes: dict, area: dict, h_wall: float, growth: float, floor: float, cap: float, solid: bool):
    """Coarsen the sizes {face: size} by one common factor until the cells they add fit `cap`: (sizes, extra cells, factor). Sizes never go above 0.95 h_wall
    (a face that would is dropped); never below `floor`."""
    def extra(f):
        return sum(bin_extra_cells(area.get(i, 0.0), max(s * f, floor), h_wall, growth, solid) for i, s in face_sizes.items() if max(s * f, floor) < 0.95 * h_wall)

    e0 = extra(1.0)
    if e0 <= cap:
        return {i: max(s, floor) for i, s in face_sizes.items()}, e0, 1.0
    lo, hi = 1.0, 1.0
    while extra(hi) > cap and hi < 1e4:
        hi *= 1.5
    for _ in range(40):
        mid = math.sqrt(lo * hi)
        if extra(mid) > cap:
            lo = mid
        else:
            hi = mid
    return {i: max(s * hi, floor) for i, s in face_sizes.items()}, extra(hi), hi


def _round_size(s: float) -> float:
    k = round(math.log(max(s, 1e-12)) / math.log(SIZE_STEP))
    return float(SIZE_STEP ** k)


def _face_percentile(face_ids, values, q: float, min_count: int = 1) -> dict:
    """{face: the q-percentile of its values} for faces with at least min_count values."""
    out = {}
    face_ids = np.asarray(face_ids)
    values = np.asarray(values, float)
    order = np.argsort(face_ids, kind="stable")
    ids_s, v_s = face_ids[order], values[order]
    cuts = np.nonzero(np.diff(ids_s))[0] + 1
    for ids, v in zip(np.split(ids_s, cuts), np.split(v_s, cuts)):
        if len(v) >= min_count:
            out[int(ids[0])] = float(np.percentile(v, q))
    return out


# ---------------------------------------------------------------------------------------------------------------------------------- the plan
def plan(study: str, faces: list, ms: dict, h_wall: float, far: float, growth: float, L: float, n_curv: float, surf=None) -> dict:
    """What the curvature, proximity and coarsening methods do: {bins: [(size, [face ids])] (sizes of faces the mesher refines like thin parts), coarsen:
    {growth, keep, d_keep} | None, n_curv: the cells per circle for the mesher (None: leave it), info: {method: {...}}}. `h_wall` is the wall cell size, `far` the
    far-field size, `n_curv` the preset's cells per circle. Same inputs, same answer, for the mesher and the estimate."""
    from . import localgeom, meshplan

    out = {"bins": [], "coarsen": None, "n_curv": None, "info": {}}
    solid = study in meshplan.SOLID_STUDIES
    per_face: dict = {}                                    # face -> the finest size any method asks for
    cu, pr_, co = opts(ms, "curvature"), opts(ms, "proximity"), opts(ms, "coarsen")
    if (cu or pr_) and surf is None:
        surf = meshplan.Surface(faces, study)
    area = {}
    if surf is not None and len(surf.T):
        for fid in np.unique(surf.tri_face):
            area[int(fid)] = float(surf.area[surf.tri_face == fid].sum())
    if cu:
        n = float(cu["elements"] or n_curv)
        floor = float(cu["min_size_mm"]) / 1000.0 if cu.get("min_size_mm") else h_wall / 5.0
        out["n_curv"] = int(round(n))
        sizes = {}
        if surf._curv is None:
            surf._curv = surf._radii()
        cen, R = surf._curv
        ids = getattr(surf, "_curv_face", np.zeros(0, np.int64))
        if len(R):
            tri_size = np.maximum(2.0 * math.pi * R / n, floor)
            sizes = {f: s for f, s in _face_percentile(ids, tri_size, 35.0, 3).items() if s < 0.9 * h_wall}
        fs, extra, factor = _fit_cap(sizes, area, h_wall, growth, floor, float(cu["max_cells"]), solid)
        out["info"]["curvature"] = {"applied": bool(fs), "faces": len(fs), "cells_per_circle": n, "smallest": min(fs.values()) if fs else None, "extra_cells": extra,
                                    "cap": float(cu["max_cells"]), "capped": factor > 1.0001, "floor": floor,
                                    "reason": None if fs else "no_curved_faces"}
        for f, s in fs.items():
            per_face[f] = min(per_face.get(f, 1e30), s)
    if pr_:
        n_across = float(pr_["cells_across"])
        floor = float(pr_["min_size_mm"]) / 1000.0 if pr_.get("min_size_mm") else h_wall / 4.0
        reach = float(pr_["max_gap_mm"]) / 1000.0 if pr_.get("max_gap_mm") else n_across * h_wall
        side = "in" if solid else "out"                    # (a solid study: the thickness of thin walls; a flow: the gaps in the air)
        wd = localgeom.wall_distances(surf, max(h_wall, L / 600.0), reach, side)
        hit = np.isfinite(wd["dist"])
        sizes = {}
        if hit.any():
            raw = np.maximum(wd["dist"][hit] / n_across, floor)
            allc = np.bincount(np.searchsorted(np.unique(wd["faces"]), wd["faces"]), minlength=len(np.unique(wd["faces"])))
            uf = np.unique(wd["faces"])
            tot = dict(zip(uf.tolist(), allc.tolist()))
            for f, s in _face_percentile(wd["faces"][hit], raw, 10.0, 1).items():
                share = float((wd["faces"][hit] == f).sum()) / max(tot.get(f, 1), 1)
                if s < 0.9 * h_wall and share >= 0.02:     # (a face with a sliver of gap on it is not refined as a whole)
                    sizes[f] = s
        fs, extra, factor = _fit_cap(sizes, area, h_wall, growth, floor, float(pr_["max_cells"]), solid)
        out["info"]["proximity"] = {"applied": bool(fs), "faces": len(fs), "smallest": min(fs.values()) if fs else None, "extra_cells": extra, "cap": float(pr_["max_cells"]),
                                    "capped": factor > 1.0001, "side": side, "reach": reach, "cells_across": n_across,
                                    "narrowest": float(wd["dist"][hit].min()) if hit.any() else None, "reason": None if fs else ("no_gaps" if not hit.any() else "gaps_resolved")}
        for f, s in fs.items():
            per_face[f] = min(per_face.get(f, 1e30), s)
    bins: dict = {}
    for f, s in per_face.items():
        bins.setdefault(round(_round_size(s), 7), []).append(int(f))
    out["bins"] = sorted((s, sorted(ids)) for s, ids in bins.items())
    if co:
        g2 = float(co["growth"])
        k = float(co["keep_cells"])
        if g2 > growth * 1.001:
            out["coarsen"] = {"growth": g2, "keep": k, "base_growth": growth}
            out["info"]["coarsen"] = {"applied": True, "growth": g2, "keep_cells": k, "base_growth": growth, "reason": None}
        else:
            out["info"]["coarsen"] = {"applied": False, "growth": g2, "keep_cells": k, "base_growth": growth, "reason": "not_coarser"}
    return out


def wall_threshold(d, h: float, far: float, growth: float, coarsen: dict | None):
    """The size at distance d from a wall whose cells are h: h up to a distance h, then growing with `growth` to `far`; with `coarsen` the growth changes to
    coarsen['growth'] after the distance that `keep` cells of the wall growth cover (the formula the mesher puts in its size field)."""
    d = np.asarray(d, float)
    s = h + (growth - 1.0) * np.maximum(d - h, 0.0)
    if coarsen:
        d_keep = keep_distance(h, growth, coarsen["keep"])
        s = s + (coarsen["growth"] - growth) * np.maximum(d - d_keep, 0.0)
    return np.minimum(s, far)


def keep_distance(h: float, growth: float, keep: float) -> float:
    """The distance from the wall that `keep` cells of growth `growth` starting from h cover."""
    return h * (growth ** keep - 1.0) / max(growth - 1.0, 1e-6)


def gmsh_expr(dist_field: int, h: float, far: float, growth: float, coarsen: dict | None) -> str:
    """The MathEval expression of wall_threshold for the Gmsh Distance field number `dist_field`."""
    e = f"({h!r}) + ({growth - 1.0!r}) * max(0, F{dist_field} - ({h!r}))"
    if coarsen:
        e += f" + ({coarsen['growth'] - growth!r}) * max(0, F{dist_field} - ({keep_distance(h, growth, coarsen['keep'])!r}))"
    return f"min({far!r}, {e})"


# ---------------------------------------------------------------------------------------------------------------------------------- the wake
def wake_options(ms: dict) -> dict | None:
    return opts(ms, "wake")


def wake_plan(an: dict, o: dict, h_wall: float) -> dict:
    """The wake box of the method for the body analysed in `an` (meshadvisor.analyse): {min, max, size, size_asked, capped, cells, cap, length, side}. The cell
    size is size_factor x the wall cell (or size_mm); it is made coarser until the regular tetrahedra of that size in the box fit the cap."""
    from . import meshadvisor, meshplan

    lo, hi = meshadvisor.wake_box(an, float(o["length"]), float(o["side"]))
    vol = float(np.prod(np.asarray(hi) - np.asarray(lo)))
    size = float(o["size_mm"]) / 1000.0 if o.get("size_mm") else float(o["size_factor"]) * h_wall
    cap = float(o["max_cells"])

    def cells(s):
        return meshplan.CELLS_CALIBRATION * vol / (meshplan.TET_VOLUME * s ** 3)

    asked, s = size, size
    if cells(s) > cap:
        s = (meshplan.CELLS_CALIBRATION * vol / (meshplan.TET_VOLUME * cap)) ** (1.0 / 3.0)
    return {"min": list(map(float, lo)), "max": list(map(float, hi)), "size": s, "size_asked": asked, "capped": s > asked * 1.0001, "cells": cells(s), "cap": cap,
            "length": float(o["length"]), "side": float(o["side"]), "volume": vol}


# ---------------------------------------------------------------------------------------------------------------------------------- a project
def unavailable(pr, name: str) -> str | None:
    """Why a method cannot be used in this study, in words; None when it can."""
    if pr.data["mesh"].get("method") == "hex":
        return "Not for a hex-dominant mesh: snappyHexMesh makes it and does not use these methods (switch to the tetrahedral mesh)"
    if pr.data["geometry"].get("status") != "ready":
        return "Not yet: import the geometry first (step 1, Geometry)"
    if name == "wake" and pr.study != "external_aero":
        return "The wake box is for external aerodynamics (a body in a flow)"
    return None


def preview(pr) -> dict:
    """What the methods that are on do for the stored settings, before there is a mesh: {info: {method: {...}}, h_wall, far, growth, wake_region | None}. Kept
    for the same settings and geometry (the plan casts rays on the surface: a fraction of a second to a few seconds)."""
    from . import meshplan

    m = pr.data["mesh"]
    key = (pr.id, pr.data["geometry"].get("updated"), repr(m.get("sizing_methods")), m.get("preset"), m.get("size_min"), m.get("size_max"), m.get("growth"), bool(m.get("smart")),
           repr(m.get("refinements")), repr(pr.data.get("settings")))
    c = getattr(pr, "_sm_preview", None)
    if c and c[0] == key:
        return c[1]
    out = {"info": {}, "h_wall": None, "far": None, "growth": None, "wake_region": None, "error": None}
    try:
        if pr.data["geometry"].get("status") == "ready" and pr.data["geometry"].get("info") and any_on(m):
            ms, rc = meshplan.base_inputs(pr)
            sz = meshplan.sizing(pr.study, pr.faces(), pr.data["geometry"]["info"], ms, rc, rotors=pr._rotors_with_wake() if pr.study == "external_aero" else (),
                                 zones=meshplan._zones(pr), surf=meshplan.surface_of(pr))
            out.update(info=dict(sz["methods"]["info"]), h_wall=sz["h_wall"], far=sz["far"], growth=sz["growth"])
            wk = (ms.get("sizing_plan") or {}).get("wake")
            if wk:
                out["info"]["wake"] = {k: v for k, v in wk.items() if k not in ("min", "max")}
                out["wake_region"] = {"name": "Wake box", "min": wk["min"], "max": wk["max"], "size": wk["size"]}
    except Exception as e:  # noqa: BLE001 - a help: it says why it has no numbers
        out["error"] = f"{type(e).__name__}: {e}"
    pr._sm_preview = (key, out)
    return out
