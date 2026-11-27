"""Fit the constants of the memory planner (simwb/meshplan.py) to the measurements of benchmarks/mesh_plan_calibrate.py.

    python benchmarks/mesh_plan_fit.py <measurements.jsonl> <workspace dir> [--write]

For every measured case the two terms of the estimate are worked out again (the volume integral of regular tetrahedra, and the wall units area / h^2) and
    cells = CELLS_CALIBRATION x integral + SURFACE_CELLS x units (+ the edge term)
is fitted by least squares on the relative error, then the memory per cell is fitted on the measured 'added_ram_mb' of the same runs. Prints the constants, the
error of every case and the spread (the largest relative error) that goes into CELLS_SPREAD. Run it from the repository root with the project's Python."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np


def terms(pr, case: dict):
    from simwb import meshplan as mp
    kw = {"preset": case.get("preset", "quick")}
    if case.get("smart") == "1":
        kw["smart"] = True
    if case.get("size_max_mm"):
        kw["size_max_mm"] = float(case["size_max_mm"])
    if case.get("size_min_mm"):
        kw["size_min_mm"] = float(case["size_min_mm"])
    pr.data["mesh"] = {"preset": "medium", "refinements": [], "status": "none"}
    pr.set_mesh(**kw)
    if case.get("edge"):
        pr.set_mesh(edge_refinement=float(case["edge"]))
    ms, rc = mp.base_inputs(pr)
    sz = mp.sizing(pr.study, pr.faces(), pr.data["geometry"]["info"], ms, rc, rotors=pr._rotors_with_wake() if pr.study == "external_aero" else (),
                   zones=mp._zones(pr), surf=mp.surface_of(pr))
    r = sz["region"]
    surf = sz["surface"]
    keep = None
    if r["mode"] in ("outside", "inside") and len(surf.T):
        keep = (lambda P: ~surf.inside(P)) if r["mode"] == "outside" else surf.inside
    integral = mp.integrate(r["lo"], r["hi"], sz["field"], keep)["cells"]
    edge = mp.edge_extra_cells(pr, sz, ms, rc)["cells"] if pr.study == "external_aero" else 0.0
    return integral, sz["surface_units"], edge


def main(argv):
    rows = [json.loads(ln) for ln in open(argv[0], encoding="utf-8") if ln.strip()]
    os.environ["SIMWB_HOME"] = argv[1]
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import simwb.project as P
    root = Path(__file__).resolve().parents[1] / "examples"
    prs = {}
    data = []
    for r in rows:
        if not r.get("cells"):
            continue
        c = r["case"]
        key = (c["example"], c.get("study", "external_aero"))
        if key not in prs:
            pr = P.create_project("fit", key[1])
            j = pr.import_step(root / key[0], original_name=key[0])
            assert j.wait(600) and j.status == "done", j.error
            if key[1] == "external_aero":
                pr.set_settings(speed=10)
            prs[key] = pr
        i, u, e = terms(prs[key], c)
        data.append((c, i, u, e, float(r["cells"]), float(r["ram"]["added_ram_mb"])))
    for smart in (False, True):
        sel = [d for d in data if (d[0].get("smart") == "1") == smart and d[0].get("study", "external_aero") == "external_aero"]
        if len(sel) < 3:
            continue
        A = np.array([[d[1] / d[4], d[2] / d[4]] for d in sel])
        y = np.ones(len(sel))
        (kb, cs), *_ = np.linalg.lstsq(A, y, rcond=None)
        print(f"smart={smart}: CELLS_CALIBRATION={kb:.3f} SURFACE_CELLS={cs:.3f} (relative least squares, {len(sel)} meshes)")
    ext = [d for d in data if d[0].get("study", "external_aero") == "external_aero"]
    A = np.array([[d[1] / d[4], d[2] / d[4]] for d in ext])
    (kb, cs), *_ = np.linalg.lstsq(A, np.array([(d[4] - d[3]) / d[4] for d in ext]), rcond=None)
    err = [(kb * d[1] + cs * d[2] + d[3]) / d[4] - 1 for d in ext]
    print(f"all flow meshes (the edge term as the estimator takes it): CELLS_CALIBRATION={kb:.3f} SURFACE_CELLS={cs:.2f}; estimate/actual - 1: "
          f"median {np.median(err):+.2f}, mean abs {np.mean(np.abs(err)):.2f}, worst {max(err, key=abs):+.2f}")
    solid = [d for d in data if d[0].get("study", "external_aero") != "external_aero"]
    from simwb import meshplan as mp
    print("
| geometry | study | preset | smart | edge | estimate | actual | estimate / actual | mesh job MB |
|---|---|---|---|---|---|---|---|---|")
    errs = []
    for c, i, u, e, act, mb in data:
        solid = c.get("study", "external_aero") in ("heat_conduction", "static_structural", "vibration_modal")
        est = mp.CELLS_CALIBRATION * i + (mp.SURFACE_CELLS_SOLID if solid else mp.SURFACE_CELLS) * u + e
        errs.append(est / act - 1)
        print(f"| {c['example'][:-5]} | {c.get('study', 'external_aero')} | {c.get('preset')} | {'yes' if c.get('smart') == '1' else 'no'}"
              f" | {c.get('edge', '-')} | {est:,.0f} | {act:,.0f} | {est / act:.2f} | {mb:.0f} |")
    print(f"
{len(errs)} meshes: median {np.median(errs):+.2f}, mean absolute {np.mean(np.abs(errs)):.2f}, worst {max(errs, key=abs):+.2f}")
    cells = np.array([d[4] for d in data])
    mb = np.array([d[5] for d in data])
    slope, fixed = np.polyfit(cells, mb, 1)
    print(f"memory: added_ram_mb = {fixed:.0f} MB + {slope * 1024:.2f} KB x cells (fit over {len(cells)} meshes; residual max {np.abs(mb - (fixed + slope * cells)).max():.0f} MB)")


if __name__ == "__main__":
    main(sys.argv[1:])
