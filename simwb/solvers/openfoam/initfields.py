"""Initial flow from another solution of the same geometry.

A transient sliding-mesh propeller run started from uniform flow spends several revolutions forming its slipstream
(each revolution is hundreds of time steps); started from a converged steady MRF run of the same mesh it needs one
or two. The same helps a steady run at a new speed started from a neighbouring speed.

The cells are paired by their centres (exact on the same mesh, nearest cell on another one), so the case writer's
boundary conditions stay as they are and only the internal fields are replaced - before the mesh tools of the run
(createBaffles, renumberMesh, decomposePar), which carry the fields along.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from .foamio import exists as foam_exists, read_field, read_polymesh
from .results_loader import latest_time

FIELDS = ("U", "p", "k", "omega", "nut", "nuTilda", "epsilon")


def cell_centres(pm: dict) -> np.ndarray:
    """Mean of the face centres of each cell (the centroid for a tetrahedron; used only to pair cells)."""
    pts, nc = pm["points"], pm["n_cells"]
    if pm["faces"] is not None:
        fc = pts[pm["faces"]].mean(axis=1)
    else:
        fc = np.array([pts[f].mean(axis=0) for f in pm["poly_faces"]])
    own, nei = pm["owner"], pm["neighbour"]
    acc, cnt = np.zeros((nc, 3)), np.zeros(nc)
    np.add.at(acc, own, fc)
    np.add.at(cnt, own, 1)
    ni = len(nei)
    np.add.at(acc, nei, fc[:ni])
    np.add.at(cnt, nei, 1)
    return acc / np.maximum(cnt, 1)[:, None]


def _fmt(v: np.ndarray) -> str:
    if v.ndim == 1:
        return "\n".join(np.char.mod("%.7g", v))
    a = np.char.mod("%.7g", v)
    return "\n".join("(" + " ".join(r) + ")" for r in a)


def _replace_internal(path: Path, values: np.ndarray) -> None:
    txt = path.read_text()
    kind = "vector" if values.ndim == 2 else "scalar"
    new = f"internalField   nonuniform List<{kind}>\n{len(values)}\n(\n{_fmt(values)}\n)\n;"
    m = re.search(r"^internalField\s+uniform\s+[^;]*;", txt, re.M)
    if not m:
        raise ValueError(f"{path.name}: no uniform internal field to replace")
    path.write_text(txt[:m.start()] + new + txt[m.end():], newline="\n")


def map_initial(case: Path, source_case: Path, log=None) -> dict:
    """Write the latest solution of source_case as the initial internal fields of case (single-region cases).
    Returns {"time", "fields", "max_distance_m", "same_mesh"}."""
    case, source_case = Path(case), Path(source_case)
    t = latest_time(source_case)
    if t is None:
        raise ValueError("The study to start from has no saved solution")
    src_pm = read_polymesh(source_case / "constant" / "polyMesh")
    tdir = source_case / t
    if foam_exists(tdir / "polyMesh" / "points"):  # source was a moving mesh: its cells are where they ended
        from .foamio import read_points

        src_pm = {**src_pm, "points": read_points(tdir / "polyMesh" / "points")}
    dst_pm = read_polymesh(case / "constant" / "polyMesh")
    cs, cd = cell_centres(src_pm), cell_centres(dst_pm)
    from scipy.spatial import cKDTree

    dist, idx = cKDTree(cs).query(cd, k=1, workers=-1)
    done = []
    for f in FIELDS:
        dst = case / "0" / f
        if not dst.exists() or not foam_exists(tdir / f):
            continue
        vals = read_field(tdir / f, src_pm["n_cells"], src_pm["patches"])["internal"]
        if np.ndim(vals) == 0 or len(vals) != src_pm["n_cells"]:
            continue  # (a uniform field in the source: keep the case's own)
        _replace_internal(dst, np.asarray(vals)[idx])
        done.append(f)
    out = {"time": t, "fields": done, "max_distance_m": float(dist.max()),
           "same_mesh": bool(len(cs) == len(cd) and dist.max() < 1e-6)}  # (rounding of the written points)
    if log:
        log(f"Initial flow from {source_case.parent.name} at {t}: {', '.join(done)} "
            + ("(same mesh)" if out["same_mesh"] else f"(nearest cell, up to {out['max_distance_m'] * 1000:.2g} mm away)"))
    return out
