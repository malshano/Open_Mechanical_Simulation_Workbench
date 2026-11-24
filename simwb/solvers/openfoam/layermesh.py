"""Wall (inflation) layers on the tetrahedral mesh, made WITH the mesh so that they can be seen and counted before the run.

The solver case adds the layers with snappyHexMesh in its layer-only mode just before the run (cases.write_external_aero). The Mesh step runs the
first steps of that very case (check, add layers) in a folder of its own, so the layers are the ones the run gets, and reads the result back:

* `parse_log`      what snappyHexMesh says: the layers asked for and made on each surface, the thickness, the share of the wall that got layers;
* `display_arrays` the layered mesh in the form the viewer uses (display tetrahedra, boundary triangles, quality) with the number of the layer
                   of every cell (0 = an ordinary tetrahedron, 1 = the layer at the wall, 2 = the next one ...).

Cells: snappyHexMesh leaves the tetrahedra as they are (moved away from the wall) and adds a prism for every wall triangle and layer. A prism (or the
rare other cell) is split into tetrahedra round its centre for display; a tetrahedron is drawn as it is."""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from .hexmesh import cell_quality
from .foamio import read_polymesh

# the steps of the case that make the mesh: what the layer case runs (the log names are those of the case)
STEP_APPS = ("checkMesh", "snappyHexMesh")


def why_not(data: dict, study: str, method: str | None) -> str | None:
    """Why the layers are not made with the mesh, or None when they are."""
    lay = (data.get("mesh") or {}).get("layers") or {}
    if study != "external_aero":
        return "Wall layers are for flow studies"
    if method == "hex":
        return "A hex-dominant mesh makes its layers itself"
    if int(lay.get("n") or 0) <= 0:
        return "No wall layers are asked for (the number of layers is 0)"
    return None


# ---------------------------------------------------------------------------------------------------------------------- what fits
def layers_that_fit(first_mm, growth, feature_mm) -> int | None:
    """The most layers that fit on a part `feature_mm` thick. A layer grows from each side of it, so all the layers together may take about half the
    thickness: wider stacks run into each other and snappyHexMesh removes them there (measured on a wing whose thinnest part is 6.3 mm: 5 layers of
    growth 1.2 from 0.85 mm, 6.3 mm in all, kept 70 % of the wall; 3 layers, 3.1 mm in all, kept 99 %). None when a size is unknown; 0 when even one
    layer would not fit."""
    if not first_mm or not feature_mm or first_mm <= 0 or feature_mm <= 0:
        return None
    g = max(float(growth or 1.2), 1.0001)
    n = 0
    while n < 40 and first_mm * (g ** (n + 1) - 1.0) / (g - 1.0) <= 0.5 * feature_mm:
        n += 1
    return n


def layer_warnings(extruded_percent, total_mm, first_mm, growth, n_asked, feature_mm, non_orth=None) -> tuple:
    """([warnings in words], layers_that_fit) for a layer step that is done: what to change when the wall lost layers, and why."""
    warn = []
    fit = layers_that_fit(first_mm, growth, feature_mm)
    lost = extruded_percent is not None and extruded_percent < 98
    thick = bool(feature_mm and total_mm and 2 * total_mm > feature_mm)
    if lost and thick and fit is not None:
        warn.append(f"Only {extruded_percent:.0f}% of the wall got layers. A layer grows from each side of the thinnest part ({feature_mm:.1f} mm) and "
                    f"{n_asked} layers are {total_mm:.2f} mm thick each side, so they run into each other and OpenFOAM removes them there. "
                    + (f"{fit} layer{'s' if fit != 1 else ''} of this growth fit{'s' if fit == 1 else ''}: lower the number of layers, or the growth ratio, "
                       "or the target y+ for a thinner first layer." if fit >= 1 else
                       "Not even one layer fits: use a finer mesh at the thin part (Face sizing) or fewer layers."))
    elif lost:
        warn.append(f"Only {extruded_percent:.0f}% of the wall got layers. Sharp edges and small cells lose them: use fewer or thinner layers, or give "
                    "those faces a finer Face sizing.")
    if non_orth and non_orth > 70:
        warn.append(f"The largest non-orthogonality is {non_orth:.0f} degrees: the solver may need more correctors.")
    return warn, fit


# ---------------------------------------------------------------------------------------------------------------------- the log
_NUM = r"([\d.]+(?:[eE][+-]?\d+)?)"


def _table(text: str, header: str, ncols: int) -> list:
    """The rows of a table of the log: after the line that has `header` and the dashed line under it, every line of `ncols` words whose second word
    is a number (patch name, then numbers), up to the first line that is not one."""
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if header in ln:
            break
    else:
        return []
    j = i + 1
    while j < len(lines) and not lines[j].startswith("---"):
        j += 1
    rows = []
    for ln in lines[j + 1:]:
        f = ln.split()
        if len(f) != ncols or not f[1].isdigit():
            break
        rows.append(f)
    return rows


def parse_log(case: Path) -> dict:
    """What the layer step reports. {patches: [{name, faces, target, made, first_mm, total_mm, percent}], extruded_percent, faces_extruded, faces_total,
    cells} - made = the layers the mesh got on that surface (the average over its faces: a number like 4.58 when some faces lost one), percent = how much of the planned thickness it got, extruded_percent = the share of the
    wall faces that got layers at all (the last 'Extruding X out of N faces' line). Missing parts are None / empty."""
    case = Path(case)
    p = case / "log.snappyHexMesh"
    text = p.read_text(errors="ignore") if p.exists() else ""
    plan = {}
    for f in _table(text, "avg thickness[m]", 5):
        plan[f[0]] = {"faces": int(f[1]), "target": int(f[2]), "first_mm": float(f[3]) * 1000, "total_mm": float(f[4]) * 1000}
    final = {}
    last = text.rfind("overall thickness")
    for f in (_table(text[last:], "overall thickness", 6) if last >= 0 else []):
        final[f[0]] = {"faces": int(f[1]), "target": int(f[2]), "made": float(f[3]), "total_mm": float(f[4]) * 1000, "percent": float(f[5])}
    patches = []
    for name in dict.fromkeys(list(plan) + list(final)):
        a, b = plan.get(name, {}), final.get(name, {})
        patches.append({"name": name, "faces": b.get("faces", a.get("faces")), "target": b.get("target", a.get("target")), "made": b.get("made"),
                        "first_mm": a.get("first_mm"), "total_mm": b.get("total_mm", a.get("total_mm")), "percent": b.get("percent")})
    ext = re.findall(r"Extruding (\d+) out of (\d+) faces \(" + _NUM + r"%\)", text)
    cells = re.findall(r"Layer mesh : cells:(\d+)", text)
    return {"patches": patches, "extruded_percent": float(ext[-1][2]) if ext else None,
            "faces_extruded": int(ext[-1][0]) if ext else None, "faces_total": int(ext[-1][1]) if ext else None,
            "cells": int(cells[-1]) if cells else None}


def parse_check(case: Path, name: str = "log.checkMesh_layers") -> dict:
    """The numbers of a checkMesh log: cells by type, the worst non-orthogonality, the worst skewness, the number of failed checks."""
    p = Path(case) / name
    t = p.read_text(errors="ignore") if p.exists() else ""

    def num(pat):
        m = re.findall(pat, t)
        return float(m[-1]) if m else None
    types = {k: int(num(rf"{k}:\s+(\d+)") or 0) for k in ("tetrahedra", "prisms", "hexahedra", "pyramids", "polyhedra")}
    return {"types": types, "cells": int(num(r"(?m)^\s*cells:\s+(\d+)") or 0),
            "non_orthogonality_max": num(r"Mesh non-orthogonality Max:\s*([\d.eE+-]+)"),
            "non_orthogonality_mean": num(r"Mesh non-orthogonality Max:\s*[\d.eE+-]+\s+average:\s*([\d.eE+-]+)"),
            "skewness_max": num(r"Max skewness\s*=\s*([\d.eE+-]+)"), "aspect_ratio_max": num(r"Max aspect ratio\s*=\s*([\d.eE+-]+)"),
            "mesh_ok": "Mesh OK" in t, "failed_checks": int(num(r"Failed (\d+) mesh checks") or 0)}


# ---------------------------------------------------------------------------------------------------------------------- the mesh
def layer_numbers(pm: dict, is_prism: np.ndarray, max_layers: int = 60) -> np.ndarray:
    """The number of the layer of every cell (an int per cell): 1 for the prisms whose triangular base is a boundary face (the wall; the quadrilateral
    sides of a prism can lie on a symmetry plane or the far field too, and do not make it a first layer), 2 for the prisms stacked on those (they share a
    triangle), and so on; 0 for everything else."""
    own, nei = pm["owner"], pm["neighbour"]
    nint = len(nei)
    lvl = np.zeros(pm["n_cells"], np.int16)
    polys = pm["poly_faces"] if pm["faces"] is None else pm["faces"]
    kb = np.fromiter((len(f) for f in polys[nint:]), np.int64, count=len(own) - nint)
    b_own = own[nint:][kb == 3]
    lvl[b_own[is_prism[b_own]]] = 1
    k = np.fromiter((len(f) for f in polys[:nint]), np.int64, count=nint) if nint else np.zeros(0, np.int64)
    both = (k == 3) & is_prism[own[:nint]] & is_prism[nei]
    a, b = own[:nint][both], nei[both]
    for L in range(1, max_layers):
        up = (lvl[a] == L) & (lvl[b] == 0)
        dn = (lvl[b] == L) & (lvl[a] == 0)
        if not (up.any() or dn.any()):
            break
        lvl[b[up]] = L + 1
        lvl[a[dn]] = L + 1
    return lvl


def display_arrays(case: Path, s2p: dict, patches: list, faces: list, fluid_id: int):
    """(arrays, polyMesh) of the layered mesh, like hexmesh.display_arrays: nodes, tets (display tetrahedra), tet_vol, tris (the boundary triangles),
    tri_surf (the CAD face of each), tri_owner (a display tetrahedron of the cell it belongs to), quality (per display tetrahedron) and
    tet_layer (the number of the layer of its cell: 0 = not a layer)."""
    pm = read_polymesh(Path(case) / "constant" / "polyMesh")
    P = np.asarray(pm["points"], np.float64)
    own, nei = np.asarray(pm["owner"], np.int64), np.asarray(pm["neighbour"], np.int64)
    nint, nf, nc = len(nei), len(own), int(pm["n_cells"])
    polys = pm["poly_faces"] if pm["faces"] is None else list(pm["faces"])
    k = np.fromiter((len(f) for f in polys), np.int64, count=nf)
    flat = np.concatenate(polys).astype(np.int64)
    starts = np.r_[0, np.cumsum(k)[:-1]]

    # which cell is what: a tetrahedron has four triangles; a layer prism two triangles and three quadrilaterals
    cells_inc = np.r_[own, nei]
    face_inc = np.r_[np.arange(nf), np.arange(nint)]
    ksz = k[face_inc]
    ntri = np.bincount(cells_inc, weights=(ksz == 3), minlength=nc).astype(np.int64)
    nquad = np.bincount(cells_inc, weights=(ksz == 4), minlength=nc).astype(np.int64)
    nbig = np.bincount(cells_inc, weights=(ksz > 4), minlength=nc).astype(np.int64)
    is_tet = (ntri == 4) & (nquad == 0) & (nbig == 0)
    is_prism = (ntri == 2) & (nquad == 3) & (nbig == 0)

    # the tetrahedra: the four distinct nodes of the four triangles of the cell
    m = is_tet[cells_inc]
    c, f = cells_inc[m], face_inc[m]
    o = np.argsort(c, kind="stable")
    c, f = c[o], f[o]
    tri = flat[starts[f][:, None] + np.arange(3)]
    nodes12 = tri.reshape(-1, 12)
    nodes12.sort(axis=1)
    tets_a = nodes12[:, ::3].copy()
    cell_a = c[::4]
    n_a = len(tets_a)

    # the other cells (prisms, rarely polyhedra): every face fan-triangulated, each triangle joined to the centre of the cell
    split = np.flatnonzero(~is_tet)
    cidx = np.full(nc, -1, np.int64)
    cidx[split] = np.arange(len(split))
    ms = ~is_tet[cells_inc]
    cs, fs = cells_inc[ms], face_inc[ms]
    kk = k[fs]
    nt = kk - 2
    rep_f, rep_c = np.repeat(fs, nt), np.repeat(cs, nt)
    j = np.arange(int(nt.sum())) - np.repeat(np.cumsum(nt) - nt, nt)
    s0 = starts[rep_f]
    tris_s = np.stack([flat[s0], flat[s0 + j + 1], flat[s0 + j + 2]], 1)
    vrep = np.repeat(cidx[cs], kk)
    vpos = np.repeat(starts[fs], kk) + (np.arange(int(kk.sum())) - np.repeat(np.cumsum(kk) - kk, kk))
    pv = P[flat[vpos]]
    cnt = np.bincount(vrep, minlength=len(split)).astype(np.float64)
    centres = np.stack([np.bincount(vrep, weights=pv[:, i], minlength=len(split)) for i in range(3)], 1) / np.maximum(cnt, 1)[:, None]
    tets_b = np.c_[tris_s, len(P) + cidx[rep_c]]
    o = np.argsort(rep_c, kind="stable")
    tets_b, cell_b = tets_b[o], rep_c[o]
    pts = np.vstack([P, centres]) if len(centres) else P
    tets = np.vstack([tets_a, tets_b]) if len(tets_b) else tets_a
    tet_cell = np.r_[cell_a, cell_b]
    a, b, cc, d = (pts[tets[:, i]] for i in range(4))
    neg = np.einsum("ij,ij->i", np.cross(b - a, cc - a), d - a) < 0
    tets[neg] = tets[neg][:, [0, 2, 1, 3]]

    first_tet = np.zeros(nc, np.int64)
    first_tet[cell_a] = np.arange(n_a)
    if len(split):
        first_tet[split] = n_a + np.searchsorted(cell_b, split)

    # the boundary triangles and the CAD face each comes from (the faces of the patches of the case; the others are not drawn)
    nb = k[nint:]
    ntb = nb - 2
    bf = np.repeat(np.arange(nint, nf), ntb)
    jb = np.arange(int(ntb.sum())) - np.repeat(np.cumsum(ntb) - ntb, ntb)
    sb = starts[bf]
    tris_b = np.stack([flat[sb], flat[sb + jb + 1], flat[sb + jb + 2]], 1)
    rep = {}
    for pi, p in enumerate(patches):
        ids = [x["id"] for x in faces if s2p.get(x["id"]) == pi]
        if ids:
            rep[p["name"]] = ids[0]
    far_ids = [x["id"] for x in faces if x.get("kind") == "farfield"]
    if far_ids:
        rep.setdefault("farfield", far_ids[0])
    face_rep = np.full(nf, -1, np.int64)
    for b_ in pm["patches"]:
        face_rep[b_["start"]:b_["start"] + b_["nFaces"]] = rep.get(b_["name"], -1)
    tsurf = face_rep[bf]
    good = tsurf >= 0
    tris_b, tsurf, bf = tris_b[good], tsurf[good], bf[good]
    towner = first_tet[own[bf]]

    layer = layer_numbers(pm, is_prism)
    qcell = cell_quality(pm)
    arrays = {"nodes": pts, "tets": tets.astype(np.int32), "tet_vol": np.full(len(tets), fluid_id, np.int64),
              "tris": tris_b.astype(np.int32), "tri_surf": tsurf, "tri_owner": towner.astype(np.int64),
              "quality": qcell[tet_cell].astype(np.float32), "tet_layer": layer[tet_cell].astype(np.int8),
              "zone_tris": np.zeros((0, 3), np.int32), "zone_tri_zone": np.zeros(0, np.int32)}
    return arrays, pm
