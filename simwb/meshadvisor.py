"""Flow-aware mesh sizing for external aerodynamics: what a good mesh needs, worked out from the shape and the flow, and a check of a finished mesh.

Why: the mesher used to size everything from `ref_length`, the LARGEST dimension of the object. For a wing that is the span, so a 314 mm chord and a 10 mm
thickness got cells of 31 mm round the body, no wake refinement at all, and refinement boxes whose cell size jumped to the far-field size over 8 cells.
The numbers here come from what published gridding guidelines ask for (see docs/MESHING_GUIDE.md): cells in proportion to the length along the flow,
a growth of at most 1.2 to 1.25, refinement of the wake behind the body, thin parts resolved, a first layer matched to the wall treatment, and a domain
neither too small nor much too large.

Pure Python (numpy only): no Qt, no Gmsh, so it can be tested and reused by the assistant."""
from __future__ import annotations

import math

import numpy as np

# preset -> targets of the smart sizing. n_s: cells along the length of the body on smooth walls (curvature, thin parts and sharp edges get finer);
# k_near / k_wake: the cell size in the near field and in the wake as a multiple of the wall cell; g: growth of the cell size away from the walls.
TARGETS = {
    "quick": {"n_s": 40, "k_near": 3.0, "k_wake": 5.0, "g": 1.3, "wake_len": 3.0},
    "coarse": {"n_s": 60, "k_near": 3.0, "k_wake": 5.0, "g": 1.25, "wake_len": 3.0},
    "medium": {"n_s": 90, "k_near": 3.0, "k_wake": 5.0, "g": 1.2, "wake_len": 4.0},
    "fine": {"n_s": 130, "k_near": 3.0, "k_wake": 4.0, "g": 1.2, "wake_len": 4.0},
    "very_fine": {"n_s": 190, "k_near": 3.0, "k_wake": 4.0, "g": 1.15, "wake_len": 5.0},
}
NEAR_UP = 0.3            # the near-field box reaches this many body lengths upstream and to the sides of the body
NEAR_DOWN = 0.5          # ... and this many downstream
WAKE_SIDE = 0.4          # the wake box is the body's width plus this many body lengths on each side
WALL_FUNCTION_Y_PLUS = 40.0
BYTES_PER_CELL = 4200.0   # peak memory of mesh + solve per cell, measured on a 845,000-cell wing run (3.4 GB)
ESTIMATE_FACTOR = 0.8     # the zone estimate below is 10 to 30 % above the cells Gmsh makes (measured on the example wing at two presets)


def zone_cells(an: dict, h_surf: float, t: dict) -> float:
    """A rough count of the cells of the near field and the wake (tetrahedra of edge h fill 0.118 h^3), the part of a mesh that grows with the volume
    refined: for a long thin wing that is the span."""
    near = _box(an, NEAR_UP, NEAR_DOWN, NEAR_UP)
    a, b = _box(an, 0.0, t["wake_len"], WAKE_SIDE)
    near_v = float(np.prod(np.asarray(near[1]) - np.asarray(near[0])))
    wake_v = float(np.prod(np.asarray(b) - np.asarray(a)))
    return ESTIMATE_FACTOR * (near_v / (0.1179 * (t["k_near"] * h_surf) ** 3) + wake_v / (0.1179 * (t["k_wake"] * h_surf) ** 3))


def memory_cap_cells(free_bytes: float | None = None) -> float | None:
    """The most cells that fit in half of the free memory, or None when the memory is not known."""
    if free_bytes is None:
        try:
            from .memlimit import memory
            free_bytes = float(memory()[1])
        except Exception:  # noqa: BLE001
            return None
    return 0.5 * free_bytes / BYTES_PER_CELL


def flow_direction(settings: dict) -> tuple:
    """(unit vector of the flow, speed m/s). With no speed (a hovering rotor) the flow is the downwash: against the up axis."""
    from . import studies
    from .solvers.openfoam import cases
    try:
        U = np.asarray(cases.freestream_velocity(settings), float)
    except Exception:  # noqa: BLE001 - an unusable setting: the setup says so, here only the direction matters
        U = np.zeros(3)
    V = float(np.linalg.norm(U))
    if V > 0.1:
        return U / V, V
    up = np.asarray(studies.AXES.get(settings.get("up_axis", "+z"), (0.0, 0.0, 1.0)), float)
    return -up / (np.linalg.norm(up) or 1.0), V


def analyse(data: dict, faces: list, info: dict) -> dict:
    """The shape and the flow as the mesh needs them: length along the flow, lateral extents, the thinnest feature, the domain round the body in body
    lengths, the Reynolds number and the boundary layer. Lengths in metres."""
    from . import materials
    from .gmsh_worker import face_width
    from .solvers.openfoam import cases
    s = data.get("settings") or {}
    bb = np.asarray(info["parts_bbox"], float)
    lo, hi = bb[:3], bb[3:]
    ext = hi - lo
    u, V = flow_direction(s)
    ax = int(np.argmax(np.abs(u)))                                   # (the world axis nearest to the flow; the boxes of the recipe follow it)
    sign = 1.0 if u[ax] >= 0 else -1.0
    corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    proj = corners @ u
    length = float(proj.max() - proj.min())                          # (the extent along the flow: the chord of a wing, the length of a body)
    lat = [i for i in range(3) if i != ax]
    lat_ext = [float(ext[i]) for i in lat]
    span, depth = max(lat_ext), min(lat_ext)
    L = float(info.get("ref_length") or ext.max())
    feats = [face_width(f) for f in faces if f.get("kind") not in ("farfield", "enclosure") and face_width(f) > L * 2e-3]
    feature = float(min(feats)) if feats else float(ext.min())
    fl = materials.get_fluid(s.get("fluid", "air"))
    nu = fl["mu"] / fl["rho"]
    re = V * length / nu if V > 0.1 else 0.0
    delta = cases.boundary_layer_thickness(V, length, nu, s.get("turbulence") == "laminar") if V > 0.1 and length > 0 else 0.0
    out = {"lo": lo.tolist(), "hi": hi.tolist(), "ext": ext.tolist(), "flow": u.tolist(), "speed": V, "axis": ax, "sign": sign, "length": length,
           "span": span, "depth": depth, "lateral_axes": lat, "ref_length": L, "feature": feature, "thin": float(ext.min()), "nu": nu, "reynolds": re,
           "boundary_layer": delta, "turbulence": s.get("turbulence", "kOmegaSST")}
    dom = info.get("domain") or {}
    if dom.get("box_min") is not None and length > 0:
        dmin, dmax = np.asarray(dom["box_min"], float), np.asarray(dom["box_max"], float)
        front = (lo[ax] - dmin[ax]) if sign > 0 else (dmax[ax] - hi[ax])      # (from the body to the domain wall it comes from)
        back = (dmax[ax] - hi[ax]) if sign > 0 else (lo[ax] - dmin[ax])
        side = min(min(lo[i] - dmin[i], dmax[i] - hi[i]) for i in lat)
        out["domain"] = {"upstream": front / length, "downstream": back / length, "side": side / length,
                         "size": ((dmax - dmin) / length).tolist()}
    return out


def _box(an: dict, up: float, down: float, side: float, depth_pad: float | None = None) -> tuple:
    """(min, max) of a box round the body, axis aligned: `up` / `down` body lengths upstream / downstream, `side` body lengths to each side."""
    lo, hi, c = np.asarray(an["lo"], float), np.asarray(an["hi"], float), an["length"]
    ax, sign = an["axis"], an["sign"]
    a, b = lo.copy(), hi.copy()
    for i in an["lateral_axes"]:
        a[i] -= side * c
        b[i] += side * c
    if sign > 0:
        a[ax] -= up * c
        b[ax] += down * c
    else:
        a[ax] -= down * c
        b[ax] += up * c
    return a.tolist(), b.tolist()


def recipe(an: dict, preset: str = "medium", growth: float | None = None, free_bytes: float | None = None) -> dict:
    """The smart sizing for a body in a flow, at a resolution preset: {preset, h_surf, h_near, h_wake, growth, n_curv, boxes[{name, min, max, size}],
    layers{n, growth, y_plus, first_mm, fit}, edge_refinement, extend_boundary, rationale[], estimate{cells_zones, cap, reduced}}. All sizes in metres.
    The cells are capped to what half of the free memory holds: a finer preset than that is coarsened and the rationale says so."""
    t = TARGETS.get(preset, TARGETS["medium"])
    c = an["length"]
    g = float(growth or t["g"])
    h_surf = c / t["n_s"]
    cap = memory_cap_cells(free_bytes)
    reduced = None
    est0 = zone_cells(an, h_surf, t)
    if cap and est0 > cap:
        h_surf *= (est0 / cap) ** (1.0 / 3.0)                           # (cells grow as 1 / h^3)
        reduced = {"from_cells": est0, "n_s": c / h_surf}
    h_near, h_wake = t["k_near"] * h_surf, t["k_wake"] * h_surf
    near = _box(an, NEAR_UP, NEAR_DOWN, NEAR_UP)
    a, b = _box(an, 0.0, t["wake_len"], WAKE_SIDE)
    wake_lo, wake_hi = np.asarray(a), np.asarray(b)                      # (the wake starts at the rear of the body: the near-field box covers the first half length)
    ax = an["axis"]
    if an["sign"] > 0:
        wake_lo[ax] = an["hi"][ax]
    else:
        wake_hi[ax] = an["lo"][ax]
    boxes = [{"name": "Near field", "min": near[0], "max": near[1], "size": h_near},
             {"name": "Wake", "min": wake_lo.tolist(), "max": wake_hi.tolist(), "size": h_wake}]
    # the layers: the first layer for the wall treatment, the number the thinnest part leaves room for (a layer grows from each side of it)
    from .solvers.openfoam import cases, layermesh
    V, nu = an["speed"], an["nu"]
    laminar = an["turbulence"] == "laminar"
    y_plus = None if laminar else WALL_FUNCTION_Y_PLUS
    first = cases.layer_first_thickness(V, c, nu, (), y_plus) if (V > 0.1 and not laminar) else None
    n_layers, fit = 0, None
    if first:
        fit = layermesh.layers_that_fit(first * 1000, 1.2, an["feature"] * 1000)
        n_layers = max(min(int(fit if fit is not None else 3), 5), 0) if fit != 0 else 0
    rat = [
        f"Cells along the {c * 1000:.0f} mm length of the body on smooth walls: {c / h_surf:.0f} -> {h_surf * 1000:.2f} mm (curvature, thin parts and sharp edges get finer).",
        f"Near field, {NEAR_UP:g} lengths round the body and {NEAR_DOWN:g} behind it: {h_near * 1000:.1f} mm ({t['k_near']:g} wall cells), growing by {g:g} outside it.",
        f"Wake, from the rear of the body {t['wake_len']:g} lengths downstream and {WAKE_SIDE:g} lengths either side of its width: {h_wake * 1000:.1f} mm ({t['k_wake']:g} wall cells): "
        "the wake carries the drag; it used to sit in cells as big as the far field.",
        f"Growth {g:g} from the walls: published gridding guidelines keep it below 1.25.",
    ]
    if first:
        rat.append(f"Wall layers: the first layer {first * 1000:.2f} mm for y+ about {WALL_FUNCTION_Y_PLUS:g} (the wall functions of {an['turbulence']} want 30 or more; 5 to 30 is the "
                   f"buffer layer where they are least accurate); {n_layers} layer(s) fit on the thinnest part ({an['feature'] * 1000:.1f} mm).")
    elif laminar:
        rat.append("Laminar flow: the boundary layer itself must be resolved; the layer planner sizes the first layer from its thickness.")
    if reduced and reduced["n_s"] < 0.98 * t["n_s"]:
        rat.append(f"The {preset} preset would make about {reduced['from_cells']:,.0f} cells in these zones; half of the free memory holds {cap:,.0f}, so the wall cell is "
                   f"{reduced['n_s']:.0f} per length instead of {t['n_s']}. Close other programs, or choose a coarser preset, to see what is possible.")
    rat.append("Sharp edges across the flow (leading and trailing edges) are refined by a field that does not spread over the surface: spreading it put 478,000 "
               "one-millimetre triangles on the example wing where 20,000 of 3.5 mm do.")
    # (the cells are counted with the final sizes)
    cells = zone_cells(an, h_surf, t)
    return {"preset": preset, "h_surf": h_surf, "h_near": h_near, "h_wake": h_wake, "growth": g, "n_curv": 24 if preset not in ("quick", "coarse") else 16,
            "boxes": boxes, "layers": {"n": n_layers, "growth": 1.2, "y_plus": y_plus, "first_mm": first * 1000 if first else None, "fit": fit},
            "edge_refinement": 4.0, "extend_boundary": False, "rationale": rat, "estimate": {"cells_zones": cells, "cap": cap, "reduced": bool(reduced)}}


# ---------------------------------------------------------------------------------------------------------------------- measuring a mesh
def _face_width(f) -> float:
    from .gmsh_worker import face_width
    return face_width(f)


def measure(an: dict, m: dict, faces: list) -> dict:
    """What a finished tetrahedral mesh (the arrays of Project.load_mesh) does round the body, measured in zones that do not depend on how the mesh was sized:
    the cell size of the wall (all of the body's surface, and of its thin parts), of the near field (within 0.3 lengths of the body) and of the wake (from
    0.2 to 2 lengths behind it, over the width of the body plus 0.2 lengths), and how fast the size changes between neighbours. Sizes are the edge
    length of a regular tetrahedron of the cell's volume, in metres."""
    from . import meshviz
    nodes, tets = np.asarray(m["nodes"], float), np.asarray(m["tets"], np.int64)
    c = an["length"]
    lo, hi, ax, sign = np.asarray(an["lo"]), np.asarray(an["hi"]), an["axis"], an["sign"]
    cm = meshviz.cell_metrics(nodes, tets, ("size", "growth"))
    size, jump = cm["size"].astype(float) / 1000.0, cm["growth"].astype(float)
    cent = nodes[tets].mean(1)
    near = np.all((cent >= lo - 0.3 * c) & (cent <= hi + 0.3 * c), axis=1)
    r0, r1 = (hi[ax] + 0.2 * c, hi[ax] + 2.0 * c) if sign > 0 else (lo[ax] - 2.0 * c, lo[ax] - 0.2 * c)
    wake = (cent[:, ax] >= r0) & (cent[:, ax] <= r1)
    for i in an["lateral_axes"]:
        wake &= (cent[:, i] >= lo[i] - 0.2 * c) & (cent[:, i] <= hi[i] + 0.2 * c)
    body = {f["id"] for f in faces if f.get("kind") == "body"}
    thin_ids = {f["id"] for f in faces if f["id"] in body and _face_width(f) <= 1.5 * an["feature"]}

    def edge_len(sel):
        t = np.asarray(m["tris"], np.int64)[sel]
        if not len(t):
            return np.zeros(0)
        return np.concatenate([np.linalg.norm(nodes[t[:, a]] - nodes[t[:, b]], axis=1) for a, b in ((0, 1), (1, 2), (2, 0))])
    surf = edge_len(np.isin(m["tri_surf"], list(body)))
    thin = edge_len(np.isin(m["tri_surf"], list(thin_ids)))
    q = np.asarray(m["quality"], float)

    def pct(a, p):
        return float(np.percentile(a, p)) if len(a) else None
    return {"cells": int(len(tets)), "surface_median": pct(surf, 50), "surface_p90": pct(surf, 90), "thin_median": pct(thin, 50),
            "near_p90": pct(size[near], 90), "near_cells": int(near.sum()), "wake_p90": pct(size[wake], 90), "wake_cells": int(wake.sum()),
            "jump_p99": pct(jump, 99), "jump_max": float(jump.max()) if len(jump) else None, "quality_min": float(q.min()) if len(q) else None,
            "poor": int((q < 0.1).sum())}


def scorecard(an: dict, ms: dict, stats: dict | None = None) -> dict:
    """Checks of a mesh against what a good external-flow mesh needs: {score (0 to 1), checks: [{id, label, value, target, level, advice}]}, level ok | warn | bad | info.
    `an`: analyse(); `ms`: measure(); `stats`: the mesh statistics (layers, OpenFOAM quality) when known. The limits are practical targets for a RANS study on a
    workstation, laxer than the 0.1 % leading-edge spacing of research grids; every one is explained in docs/MESHING_GUIDE.md."""
    st = stats or {}
    c = an["length"]
    out = []

    def add(i, label, value, target, level, advice=""):
        out.append({"id": i, "label": label, "value": value, "target": target, "level": level, "advice": advice})

    def band(x, ok, warn, higher_worse=True):
        if x is None:
            return "info"
        if higher_worse:
            return "ok" if x <= ok else "warn" if x <= warn else "bad"
        return "ok" if x >= ok else "warn" if x >= warn else "bad"

    def mm(v):
        return f"{v * 1000:.1f} mm"

    def pc(v):
        return f"{100 * v / c:.1f} % of the length"
    s = ms.get("surface_median")
    add("surface", "Wall cells", f"{mm(s)} = {pc(s)}" if s else "–", "at most 1.5 % of the length", band(s / c if s else None, 0.015, 0.03),
        "Use a finer preset, or smart sizing, which sizes the wall from the length along the flow." if s and s / c > 0.015 else "")
    t = ms.get("thin_median")
    if t:
        across = an["feature"] / t
        add("thin", "Cells across the thinnest part", f"{across:.1f}", "at least 4", band(across, 4, 2.5, False),
            "Give the thin faces a finer Part sizing." if across < 4 else "")
    n = ms.get("near_p90")
    add("near", "Near field (within 0.3 lengths of the body)", f"cells up to {mm(n)} = {pc(n)}" if n else "–", "at most 5 % of the length",
        band(n / c if n else None, 0.05, 0.08), "Smart sizing refines a box round the body." if n and n / c > 0.05 else "")
    w = ms.get("wake_p90")
    add("wake", "Wake (0.2 to 2 lengths behind the body)", f"cells up to {mm(w)} = {pc(w)}" if w else "–", "at most 8 % of the length",
        band(w / c if w else None, 0.08, 0.15), "The wake carries the drag: refine it (smart sizing does) or add a refinement box behind the body." if w and w / c > 0.08 else "")
    j = ms.get("jump_p99")
    add("gradation", "Change of cell size between neighbours (99 % of cells)", f"{j:.2f}" if j else "–", "at most 1.6", band(j, 1.6, 2.2),
        "Abrupt steps in cell size: smart sizing grows the cells smoothly." if j and j > 1.6 else "")
    d = an.get("domain")
    if d:
        up, dn, sd = d["upstream"], d["downstream"], d["side"]
        lev = "ok" if (up >= 8 and sd >= 8 and dn >= 15) else "warn" if (up >= 4 and sd >= 4 and dn >= 8) else "bad"
        add("domain", "Distance to the domain walls (in body lengths)", f"upstream {up:.0f}, sides {sd:.0f}, downstream {dn:.0f}", "at least 8, 8 and 15", lev,
            "The walls of the domain are too near the body: the flow feels them. Enlarge the air volume." if lev != "ok" else "")
        big = max(d["size"])
        if big > 60:
            add("domain_big", "Size of the domain", f"{big:.0f} body lengths", "10 to 60", "info", "Much larger than needed: the cells there are wasted unless they are very coarse.")
    ly = st.get("layers") or {}
    if ly.get("made"):
        e = ly.get("extruded_percent")
        add("layers", "Wall layers", f"{ly.get('layers_max', 0)} layers on {e:.0f} % of the wall" if e is not None else "made", "at least 95 % of the wall",
            band(e, 95, 85, False), "; ".join(ly.get("warnings") or []) if (e or 100) < 95 else "")
        first = (ly.get("first_mm") or 0) / 1000
        if first and an["speed"] > 0.1 and an["turbulence"] != "laminar":
            cf = 0.0576 * an["reynolds"] ** -0.2
            yp = 0.5 * first * an["speed"] * math.sqrt(cf / 2) / an["nu"]
            add("yplus", "First layer y+ (estimated)", f"{yp:.0f}", "5 or less, or 30 or more", "ok" if (yp <= 5 or yp >= 30) else "warn",
                "5 to 30 is the buffer layer: the wall functions are least accurate there. Set a target y+ of about 40, or about 1 with many more layers." if 5 < yp < 30 else "")
    else:
        add("layers", "Wall layers", "not made" + (f": {ly['reason']}" if ly.get("reason") else ""), "made", "info" if not ly else "warn",
            "Set a number of layers (Mesh step, Wall layers) and generate the mesh with OpenFOAM installed." if not ly.get("reason") else "")
    fq = (ly if ly.get("made") and ly.get("non_orthogonality_max") is not None else st.get("foam_quality")) or {}
    no, sk = fq.get("non_orthogonality_max"), fq.get("skewness_max")
    if no is not None:
        add("nonortho", "Largest non-orthogonality", f"{no:.0f}°", "at most 65°", band(no, 65, 75), "Above 75° OpenFOAM may be unstable." if no > 75 else "")
    if sk is not None:
        add("skewness", "Largest skewness", f"{sk:.2f}", "at most 3", band(sk, 3, 4), "")
    if ms.get("quality_min") is not None:
        add("quality", "Poorest cell (shape quality, 1 is ideal)", f"{ms['quality_min']:.2f} ({ms['poor']} below 0.1)", "no cell below 0.1", "ok" if ms["poor"] == 0 else "bad", "")
    add("cells", "Cells", f"{ms['cells']:,}", "", "info", "")
    scored = [x for x in out if x["level"] in ("ok", "warn", "bad")]
    score = sum({"ok": 1.0, "warn": 0.5, "bad": 0.0}[x["level"]] for x in scored) / max(len(scored), 1)
    return {"score": round(score, 2), "checks": out, "ok": sum(x["level"] == "ok" for x in scored), "warn": sum(x["level"] == "warn" for x in scored),
            "bad": sum(x["level"] == "bad" for x in scored)}
