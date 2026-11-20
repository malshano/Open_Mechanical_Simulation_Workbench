"""Hex-dominant (Cartesian) meshing of external flows with OpenFOAM's snappyHexMesh.

The default mesh of the workbench is a Gmsh tetrahedral mesh. For flow round a body a snappyHexMesh mesh is usually
better: mostly hexahedra (cheaper per cell, more orthogonal), with the cell size changing in octree steps, a finer
wake, and prism layers on the walls. This module plans such a mesh from the same sizing rules as the tetrahedral
mesher (so the presets mean the same), writes the case (background box, surface files, feature extraction,
snappyHexMesh), and turns the result into the arrays the rest of the workbench uses to show a mesh.

First version: external aerodynamics round bodies in a box of air, without rotors, moving zones or symmetry planes
(`unsupported()` says why a study cannot use it; the tetrahedral mesh stays available for those).

The recipe (background box, surface features, castellate / snap / layers, quality limits) is the standard
snappyHexMesh one; the sizes, levels, wake box and layer thickness are planned here.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

import numpy as np

from . import cases
from .foamio import header, read_polymesh, sanitize

STEPS = [
    {"app": "blockMesh", "args": [], "log": "log.blockMesh"},
    {"app": "surfaceFeatureExtract", "args": [], "log": "log.surfaceFeatureExtract"},
    {"app": "snappyHexMesh", "args": ["-overwrite"], "log": "log.snappyHexMesh"},
    {"app": "createPatch", "args": ["-overwrite"], "log": "log.createPatch"},   # (refinement groups -> the user's patches)
    {"app": "checkMesh", "args": ["-allGeometry", "-allTopology"], "log": "log.checkMesh", "allow_fail": True,
     "main": True},
]
WALL_TYPES = ("wall", "default", "slip_wall")
MAX_BACKGROUND_CELLS = 2_000_000
MAX_CELLS = 10_000_000


def unsupported(data: dict, faces: list, info: dict) -> str | None:
    """Why this study cannot use the hex-dominant mesh (None when it can)."""
    if data.get("study") != "external_aero":
        return "The hex-dominant mesh is for external aerodynamics."
    if data.get("rotors"):
        return "Rotors need the tetrahedral mesh (their disks are cell zones of it): not supported by the hex-dominant mesh yet."
    if data.get("moving_zones"):
        return "Moving zones need the tetrahedral mesh: not supported by the hex-dominant mesh yet."
    if data.get("symmetry"):
        return "Symmetry planes need the tetrahedral mesh: not supported by the hex-dominant mesh yet."
    dom = (info or {}).get("domain") or {}
    spec = dom.get("spec") or {}
    if "box_min" not in dom or spec.get("shape", "box") != "box":
        return "The hex-dominant mesh needs the automatic box of air (not a cylinder or a custom air volume)."
    far = [f for f in faces if f.get("kind") == "farfield"]
    walls = [f for f in faces if f.get("kind") != "farfield" and not f.get("interface")]
    if len(far) != 6:
        return "The hex-dominant mesh needs the box of air with its six faces."
    if not walls:
        return "No wall faces."
    far_ids = {f["id"] for f in far}
    for bc in data.get("bcs") or []:
        if any(int(i) in far_ids for i in bc["faces"]):
            return f"'{bc['name']}' sets a condition on a face of the air box: not supported by the hex-dominant mesh yet."
        if bc["type"] not in WALL_TYPES and bc["faces"]:
            return f"'{bc['name']}' ({bc['type']}) is not a wall: not supported by the hex-dominant mesh yet."
    return None


def _ascii_stl(name: str, faces: list) -> tuple[str, int]:
    out = [f"solid {name}"]
    n = 0
    for f in faces:
        m = f.get("mesh") or {}
        v = np.asarray(m.get("vertices") or [], float).reshape(-1, 3)
        t = np.asarray(m.get("triangles") or [], np.int64).reshape(-1, 3)
        for a, b, c in t:
            nrm = np.cross(v[b] - v[a], v[c] - v[a])
            ln = np.linalg.norm(nrm)
            if ln < 1e-30:
                continue
            nrm = nrm / ln
            out.append(f" facet normal {nrm[0]:.6g} {nrm[1]:.6g} {nrm[2]:.6g}\n  outer loop")
            out += [f"   vertex {p[0]:.10g} {p[1]:.10g} {p[2]:.10g}" for p in (v[a], v[b], v[c])]
            out.append("  endloop\n endfacet")
            n += 1
    out.append(f"endsolid {name}")
    return "\n".join(out) + "\n", n


def make_plan(data: dict, faces: list, info: dict, surf2patch: dict, patches: list, Uinf, nu: float) -> dict:
    """Sizes, refinement levels, background box, wake box and layers, from the mesh settings and the geometry."""
    from ...gmsh_worker import FEATURE_CELLS, PRESETS

    ms = data.get("mesh") or {}
    s = data.get("settings") or {}
    L = float(info["ref_length"])
    n_per, _ = PRESETS.get(ms.get("preset", "medium"), PRESETS["medium"])
    h_body = float(ms.get("size_max") or L / n_per)
    feat = [w for w in (_face_width(f) for f in faces if f.get("kind") not in ("farfield", "enclosure"))
            if w > L * 2e-3]
    feature = min(feat) if feat else L / 20
    n_feat = FEATURE_CELLS.get(ms.get("preset", "medium"), 4)
    h_wall = float(ms.get("size_min") or min(max(feature / n_feat, L / (4 * n_per)), h_body / 2))

    dom = info["domain"]
    lo = np.asarray(dom["box_min"], float)
    hi = np.asarray(dom["box_max"], float)
    # octree levels: the surface cell is h_wall; the background cell is h_wall * 2^levels (4..7 levels, as few as the
    # size of the box and the cell limit allow)
    levels = 4
    while levels < 7 and np.prod(np.ceil((hi - lo) / (h_wall * 2 ** levels))) > MAX_BACKGROUND_CELLS:
        levels += 1
    base = h_wall * 2 ** levels
    n = np.maximum(np.ceil((hi - lo) / base), 4).astype(int)
    hi = lo + n * base                                   # cubic background cells: the box grows by < 1 cell

    # local sizes, as the tetrahedral mesher: a thin part (an arm, a fin, a wing's trailing region) gets cells about
    # a quarter of its width (preset-dependent), never finer than L/(12 n); faces the user refined get that size
    h_thin_min = L / (12 * n_per)
    local = {}
    if not ms.get("size_min"):
        for f in faces:
            if f.get("kind") in ("farfield", "enclosure"):
                continue
            w = _face_width(f)
            if L * 2e-3 < w and w / n_feat < 0.9 * h_wall:
                local[f["id"]] = max(w / n_feat, h_thin_min)
    for r in ms.get("refinements") or []:
        for i in r["faces"]:
            local[int(i)] = min(local.get(int(i), 1e30), float(r["size"]))

    def level_of(face_id):
        sz = local.get(face_id)
        if sz is None:
            return levels
        return int(np.clip(math.ceil(math.log2(base / sz) - 1e-9), levels, levels + 3))

    walls = [p for p in patches if p["type"] == "wall" and p["bc"].get("kind") != "farfield"]
    wall_patches = []
    for pi, p in enumerate(patches):
        if p not in walls:
            continue
        fl = [f for f in faces if surf2patch.get(f["id"]) == pi and f.get("kind") != "farfield"]
        if not fl:
            continue
        by_level = {}
        for f in fl:
            by_level.setdefault(level_of(f["id"]), []).append(f["id"])
        # one surface per refinement level; they are merged back into the patch after meshing (createPatch)
        groups = [{"name": f"{p['name']}_L{lev}", "level": lev, "faces": ids} for lev, ids in sorted(by_level.items())]
        wall_patches.append({"name": p["name"], "faces": [f["id"] for f in fl], "groups": groups,
                             "slip": p["bc"].get("type") == "slip_wall"})

    pb = np.asarray(info.get("parts_bbox") or [*lo, *hi], float)
    U = np.asarray(Uinf, float)
    V = float(np.linalg.norm(U))
    regions = []
    if V >= 1.0:  # the wake: round the parts and 4 body lengths downstream along the flow
        d = U / V
        a, b = pb[:3] - 0.6 * L, pb[3:] + 0.6 * L
        for i in range(3):
            if d[i] > 0.3:
                b[i] += 4 * L * d[i]
            elif d[i] < -0.3:
                a[i] += 4 * L * d[i]
        a, b = np.maximum(a, lo + base), np.minimum(b, hi - base)
        regions.append({"name": "wake", "min": a.tolist(), "max": b.tolist(), "level": max(levels - 2, 1)})
    for r in ms.get("regions") or []:
        lev = int(np.clip(round(math.log2(base / float(r["size"]))), 0, levels + 1))
        regions.append({**{k: r[k] for k in r if k not in ("size", "name")}, "name": sanitize(r["name"]), "level": lev})

    lay = ms.get("layers") or {}
    n_lay = int(lay.get("n") or 0)
    lp = cases.layer_plan(V, L, nu, s.get("turbulence", "kOmegaSST"), n_lay, float(lay.get("growth", 1.2)),
                          data.get("moving_zones") or [], lay.get("first")) if n_lay else None
    first = lp["first"] if lp else 0.0

    # a point in the air, away from every plane of the background grid and from the body
    loc = (lo + base * np.array([1.37, 1.51, 1.23])).tolist()
    return {"L": L, "h_wall": h_wall, "h_body": h_body, "levels": levels, "base": base, "box_min": lo.tolist(),
            "box_max": hi.tolist(), "cells_bg": [int(x) for x in n], "regions": regions, "wall_patches": wall_patches,
            "layers": {"n": n_lay, "growth": float(lay.get("growth", 1.2)), "first": first, "plan": lp}, "location": loc,
            "preset": ms.get("preset", "medium"), "far_patch": "farfield"}


def _face_width(f) -> float:
    """Smaller side of the face's bounding box that is not ~0 (a thin face of an arm or fin)."""
    b = f.get("bbox")
    if not b:
        return 0.0
    ext = sorted(b[3 + i] - b[i] for i in range(3))
    return float(ext[1])


def write_case(case: Path, plan: dict, faces: list) -> dict:
    """Write the meshing case: surfaces, background box, features, snappyHexMesh. Returns {patch: triangle count}."""
    case = Path(case)
    (case / "constant" / "triSurface").mkdir(parents=True, exist_ok=True)
    (case / "system").mkdir(parents=True, exist_ok=True)
    byid = {f["id"]: f for f in faces}
    counts = {}
    for p in plan["wall_patches"]:
        for g in p["groups"]:
            txt, n = _ascii_stl(g["name"], [byid[i] for i in g["faces"]])
            (case / "constant" / "triSurface" / f"{g['name']}.stl").write_text(txt, newline="\n")
            counts[g["name"]] = n
    lo, hi, nn = plan["box_min"], plan["box_max"], plan["cells_bg"]
    # (blockMesh vertex order: counter-clockwise on the lower z level, then the same on the upper one)
    verts = [(lo[0], lo[1], lo[2]), (hi[0], lo[1], lo[2]), (hi[0], hi[1], lo[2]), (lo[0], hi[1], lo[2]),
             (lo[0], lo[1], hi[2]), (hi[0], lo[1], hi[2]), (hi[0], hi[1], hi[2]), (lo[0], hi[1], hi[2])]
    far = plan["far_patch"]
    _w(case / "system" / "blockMeshDict", header("dictionary", "blockMeshDict", "system") + f"""
scale 1;
vertices
(
{chr(10).join("    (" + " ".join(f"{c:.10g}" for c in p) + ")" for p in verts)}
);
blocks ( hex (0 1 2 3 4 5 6 7) ({nn[0]} {nn[1]} {nn[2]}) simpleGrading (1 1 1) );
edges ();
boundary
(
    {far}
    {{
        type patch;
        faces ((0 4 7 3) (1 2 6 5) (0 1 5 4) (3 7 6 2) (0 3 2 1) (4 5 6 7));
    }}
);
mergePatchPairs ();
""")
    groups = [g for p in plan["wall_patches"] for g in p["groups"]]
    names = [g["name"] for g in groups]
    _w(case / "system" / "createPatchDict", header("dictionary", "createPatchDict", "system") + "\npointSync false;\npatches\n(\n"
       + "".join(f"    {{\n        name {p['name']};\n        patchInfo {{ type wall; }}\n        constructFrom patches;\n"
                 f"        patches ({' '.join(g['name'] for g in p['groups'])});\n    }}\n" for p in plan["wall_patches"]) + ");\n")
    _w(case / "system" / "surfaceFeatureExtractDict", header("dictionary", "surfaceFeatureExtractDict", "system") + "\n"
       + "".join(f"{n}.stl\n{{\n    extractionMethod extractFromSurface;\n    includedAngle 150;\n    writeObj no;\n}}\n" for n in names))
    lev, g = plan["levels"], plan["layers"]
    geom = "".join(f'    {n} {{ type triSurfaceMesh; file "{n}.stl"; }}\n' for n in names)
    refs = []
    for i, r in enumerate(plan["regions"]):
        gname = f"region_{i}_{r['name']}"
        if r.get("shape") == "cylinder":
            a, b = r["start"], r["end"]
            geom += (f"    {gname} {{ type searchableCylinder; point1 ({a[0]:.8g} {a[1]:.8g} {a[2]:.8g}); "
                     f"point2 ({b[0]:.8g} {b[1]:.8g} {b[2]:.8g}); radius {r['radius']:.8g}; }}\n")
        else:
            a, b = r["min"], r["max"]
            geom += (f"    {gname} {{ type searchableBox; min ({a[0]:.8g} {a[1]:.8g} {a[2]:.8g}); "
                     f"max ({b[0]:.8g} {b[1]:.8g} {b[2]:.8g}); }}\n")
        refs.append(f"        {gname} {{ mode inside; levels ((1E15 {r['level']})); }}\n")
    ref_surf = "".join(f"        {g_['name']} {{ level ({max(g_['level'] - 1, 0)} {g_['level']}); patchInfo {{ type wall; }} }}\n"
                       for g_ in groups)
    feats = "".join(f'        {{ file "{g_["name"]}.eMesh"; level {g_["level"]}; }}\n' for g_ in groups)
    lay_names = [g_["name"] for p in plan["wall_patches"] if not p["slip"] for g_ in p["groups"]]
    layers_on = g["n"] > 0 and bool(lay_names)
    lays = "".join(f'        "{n}" {{ nSurfaceLayers {g["n"]}; }}\n' for n in lay_names)
    loc = plan["location"]
    _w(case / "system" / "snappyHexMeshDict", header("dictionary", "snappyHexMeshDict", "system") + f"""
castellatedMesh true;
snap            true;
addLayers       {"true" if layers_on else "false"};
geometry
{{
{geom}}}
castellatedMeshControls
{{
    maxLocalCells {MAX_CELLS}; maxGlobalCells {MAX_CELLS}; minRefinementCells 10; nCellsBetweenLevels 3;
    resolveFeatureAngle 30;
    features
    (
{feats}    );
    refinementSurfaces
    {{
{ref_surf}    }}
    refinementRegions
    {{
{"".join(refs)}    }}
    locationInMesh ({loc[0]:.8g} {loc[1]:.8g} {loc[2]:.8g});
    allowFreeStandingZoneFaces true;
}}
snapControls
{{
    nSmoothPatch 3; tolerance 2.0; nSolveIter 30; nRelaxIter 5; nFeatureSnapIter 10;
    implicitFeatureSnap false; explicitFeatureSnap true; multiRegionFeatureSnap false;
}}
addLayersControls
{{
    relativeSizes false;
    firstLayerThickness {g["first"]:.8g};
    minThickness {0.1 * g["first"]:.8g};
    expansionRatio {g["growth"]:.6g};
    layers
    {{
{lays}    }}
    nGrow 0; featureAngle 130; slipFeatureAngle 30; nRelaxIter 5; nSmoothSurfaceNormals 1; nSmoothNormals 3;
    nSmoothThickness 10; maxFaceThicknessRatio 0.8; maxThicknessToMedialRatio 0.6; minMedialAxisAngle 90;
    nBufferCellsNoExtrude 0; nLayerIter 100; nRelaxedIter 3;
}}
meshQualityControls
{{
    maxNonOrtho 65; maxBoundarySkewness 20; maxInternalSkewness 4; maxConcave 80; minVol 1e-13;
    minTetQuality 1e-30; minArea -1; minTwist 0.02; minDeterminant 0.001; minFaceWeight 0.05; minVolRatio 0.01;
    minTriangleTwist -1; nSmoothScale 4; errorReduction 0.75;
    // (the relaxed limits apply to the layer step after its first iterations: with the strict ones every layer was
    // removed on a thin wing, which left the walls without a boundary layer; measured on the wing example)
    relaxed
    {{
        maxNonOrtho 75; maxBoundarySkewness 20; maxInternalSkewness 4; maxConcave 80; minTetQuality 1e-30;
        minTwist 0.001; minDeterminant 1e-5; minFaceWeight 0.02; minVolRatio 0.01;
    }}
}}
mergeTolerance 1e-6;
""")
    # (snappyHexMesh refuses to merge points at its tolerance when the files carry few digits)
    _w(case / "system" / "controlDict", header("dictionary", "controlDict", "system") + """
application     simpleFoam;
startFrom       startTime;
startTime       0;
stopAt          endTime;
endTime         1;
deltaT          1;
writeControl    timeStep;
writeInterval   1;
writeFormat     ascii;
writePrecision  10;
""")
    _w(case / "system" / "fvSchemes", header("dictionary", "fvSchemes", "system") + """
ddtSchemes { default steadyState; }
gradSchemes { default Gauss linear; }
divSchemes { default none; }
laplacianSchemes { default Gauss linear corrected; }
interpolationSchemes { default linear; }
snGradSchemes { default corrected; }
""")
    _w(case / "system" / "fvSolution", header("dictionary", "fvSolution", "system") + "\nsolvers {}\n")
    return counts


def _w(path: Path, text: str):
    path.write_text(text, newline="\n")


def finalize(case: Path) -> None:
    """createPatch -overwrite wrote the merged mesh to <case>/0/polyMesh in the OpenFOAM v2606 build (the mesh the
    other steps made is in constant/polyMesh): move it into place."""
    import shutil

    src = Path(case) / "0" / "polyMesh"
    if src.is_dir():
        dst = Path(case) / "constant" / "polyMesh"
        shutil.rmtree(dst, ignore_errors=True)
        shutil.move(str(src), str(dst))


def parse_logs(case: Path) -> dict:
    """What the meshing logs say: cell types, quality (checkMesh), layer coverage, warnings."""
    case = Path(case)

    def read(n):
        p = case / n
        return p.read_text(errors="ignore") if p.exists() else ""

    chk, sn = read("log.checkMesh"), read("log.snappyHexMesh")

    def num(pat, text=chk, first=False):
        m = re.findall(pat, text)
        return float(m[0 if first else -1]) if m else None

    types = {k: int(num(rf"{k}:\s+(\d+)") or 0) for k in ("hexahedra", "prisms", "polyhedra", "tetrahedra", "pyramids")}
    concave = int(num(r"Concave cells .*number of cells:\s*(\d+)") or 0)
    failed = [x.strip() for x in re.findall(r"^\s*\*\*\*(.+)$", chk, re.M)]
    n_failed = int(num(r"Failed (\d+) mesh checks") or 0)
    # snappyHexMesh may leave concave cells (it limits them itself): a warning, not a failure. Anything else is one.
    other = [x for x in failed if "oncave" not in x]
    # the layers: snappyHexMesh starts with all wall faces and removes extrusions that break the quality limits, one
    # iteration at a time; the last "Extruding X out of N faces" line is the final share (the table printed before
    # the iterations is only the plan)
    ext = re.findall(r"Extruding (\d+) out of (\d+) faces \(([\d.]+)%\)", sn)
    layers = []
    warnings = []
    if re.search(r"stop(?:ping)? refinement", sn, re.I):
        warnings.append("The cell limit stopped the refinement: the mesh is coarser than planned.")
    if concave:
        warnings.append(f"{concave:,} concave cells (snappyHexMesh limits them; solvers tolerate them).")
    return {
        "cells": int(num(r"(?m)^\s*cells:\s+(\d+)", first=True) or 0), "types": types,
        "non_orthogonality_max": num(r"Mesh non-orthogonality Max:\s*([\d.eE+-]+)"),
        "non_orthogonality_mean": num(r"Mesh non-orthogonality Max:\s*[\d.eE+-]+\s+average:\s*([\d.eE+-]+)"),
        "skewness_max": num(r"Max skewness\s*=\s*([\d.eE+-]+)"), "aspect_ratio_max": num(r"Max aspect ratio\s*=\s*([\d.eE+-]+)"),
        "mesh_ok": "Mesh OK" in chk or (n_failed <= (1 if concave else 0) and not other), "failed_checks": n_failed,
        "concave_cells": concave, "problems": other[:6], "layers": layers,
        "layers_extruded_percent": float(ext[-1][2]) if ext else None, "warnings": warnings,
    }


def display_arrays(case: Path, plan: dict, surf2patch: dict, patches: list, faces: list, fluid_id: int):
    """The mesh in the form the viewer and the sections use (like the tetrahedral mesh): points, display tetrahedra
    (hexahedra and other cells are split round their centre), the boundary triangles with the CAD face each belongs
    to, and a per-tetrahedron quality (1 - the cell's worst non-orthogonality / 90 degrees)."""
    from .results_loader import display_mesh, display_points

    pm = read_polymesh(Path(case) / "constant" / "polyMesh")
    dm = display_mesh(pm)
    if isinstance(dm, dict):
        tets, tris, tri_face, pts = dm["tets"], dm["tris"], dm["tri_face"], display_points(pm["points"], dm)
        tet_cell = dm["tet_cell"]
    else:  # (a pure tetrahedral mesh: not expected from snappyHexMesh)
        tets = dm
        raise ValueError("The meshing case produced a tetrahedral mesh only")
    nint = len(pm["neighbour"])
    # display_mesh makes one tetrahedron per face triangle and owner (then one per neighbour), sorted by cell: the
    # tetrahedron of a boundary triangle is its own position in that order (found again without searching)
    inner = tri_face < nint
    order = np.argsort(np.r_[pm["owner"][tri_face], pm["neighbour"][tri_face[inner]]], kind="stable")
    pos = np.empty(len(order), np.int64)
    pos[order] = np.arange(len(order))
    # patch of every boundary face -> a representative CAD face of that patch
    rep = {}
    for pi, p in enumerate(patches):
        ids = [f["id"] for f in faces if surf2patch.get(f["id"]) == pi]
        if ids:
            rep[p["name"]] = ids[0]
    far_ids = [f["id"] for f in faces if f.get("kind") == "farfield"]
    rep[plan["far_patch"]] = far_ids[0]
    face_rep = np.full(len(pm["owner"]), -1, np.int64)
    for b in pm["patches"]:
        face_rep[b["start"]:b["start"] + b["nFaces"]] = rep.get(b["name"], -1)
    sel = np.where(tri_face >= nint)[0]
    tris_b, tsurf, towner = tris[sel], face_rep[tri_face[sel]], pos[sel]
    good = tsurf >= 0
    tris_b, tsurf, towner = tris_b[good], tsurf[good], towner[good]

    # per-cell quality from the face normals and the cell centres (an approximation for display; the numbers
    # reported come from checkMesh)
    P = pm["points"]
    own, nei = pm["owner"], pm["neighbour"]
    polys = pm["poly_faces"] if pm["faces"] is None else list(pm["faces"])
    k = np.array([len(f) for f in polys], np.int64)
    flat = np.concatenate(polys).astype(np.int64)
    fid = np.repeat(np.arange(len(polys)), k)
    fc = np.zeros((len(polys), 3))
    np.add.at(fc, fid, P[flat])
    fc /= k[:, None]
    first = np.r_[0, np.cumsum(k)[:-1]]
    nxt = np.where(np.arange(len(flat)) == (first + k - 1)[fid], first[fid], np.arange(len(flat)) + 1)
    sf = np.zeros((len(polys), 3))
    np.add.at(sf, fid, 0.5 * np.cross(P[flat] - fc[fid], P[flat[nxt]] - fc[fid]))
    nc = pm["n_cells"]
    cc = np.zeros((nc, 3))
    cnt = np.zeros(nc)
    np.add.at(cc, own, fc)
    np.add.at(cnt, own, 1)
    np.add.at(cc, nei, fc[:nint])
    np.add.at(cnt, nei, 1)
    cc /= np.maximum(cnt, 1)[:, None]
    d = cc[nei] - cc[own[:nint]]
    s = sf[:nint]
    cosang = np.einsum("ij,ij->i", d, s) / (np.linalg.norm(d, axis=1) * np.linalg.norm(s, axis=1) + 1e-300)
    no = np.degrees(np.arccos(np.clip(np.abs(cosang), 0, 1)))
    worst = np.zeros(nc)
    np.maximum.at(worst, own[:nint], no)
    np.maximum.at(worst, nei, no)
    qcell = np.clip(1.0 - worst / 90.0, 0.0, 1.0)
    return {"nodes": pts, "tets": tets.astype(np.int32), "tet_vol": np.full(len(tets), fluid_id, np.int64),
            "tris": tris_b.astype(np.int32), "tri_surf": tsurf, "tri_owner": towner.astype(np.int64),
            "quality": qcell[tet_cell].astype(np.float32), "zone_tris": np.zeros((0, 3), np.int32),
            "zone_tri_zone": np.zeros(0, np.int32), "hex": np.array([1])}, pm
