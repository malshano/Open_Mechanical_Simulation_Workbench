"""Mesh benchmark: accuracy against a known answer, cells, time and RAM, for every mesh method and setting.

Case: a flat plate (1.0 x 0.5 m) along the flow in air.
  lam  : laminar flow at 3 m/s (Re_L = 2e5). On a plate with a sharp leading edge the mean friction coefficient has an
         exact answer (Blasius): Cf = 1.328 / sqrt(Re_L). The error of the computed Cf is how well the mesh resolves
         the boundary layer.
  turb : k-omega SST at 30 m/s. No exact reference (the model starts laminar at the leading edge); Cf and y+ are
         reported to compare methods with each other.
  shape sharp : 4 mm thick with 50 mm wedges at both ends (half-angle 2.3 degrees): Blasius applies within a percent or
         two. shape blunt : a 10 mm slab whose square nose separates the flow (no exact reference).
The workbench reports the viscous force on the plate; Cf = F_viscous / (q * 2 * L * B).

Every case runs in its own fresh process (so the RAM it adds is not mixed with the previous case) and is marked
`valid` only when the solver converged: an unconverged run says nothing about the mesh.

    python benchmarks/mesh_benchmark.py <out.jsonl> <spec> [<spec> ...]
    spec = method:preset[:key=value,...]     keys: n (layers, default 5), g (growth, default 1.2),
                                                   mode (lam|turb), shape (sharp|blunt), it (iterations, default 1500),
                                                   stop (the app's stop rule "Stop when forces settle", % of the total load;
                                                         default 0.2 = the app's default, 0 = never stop early),
                                                   solves (solve the SAME mesh this many times; the first fills the row, the others
                                                         go to `solve_repeats`: the variation of the solver side on one fixed mesh),
                                                   procs (CPU cores of the solver, default Auto), procs2 / solves2 (after the
                                                         repeats: solve the same mesh `solves2` more times, default 3, on procs2 cores),
                                                   le (cell size in mm on the faces at the leading edge, sharp plate),
                                                   edge (sharp-edge refinement factor, e.g. 6), tol (1 = tolerant layers)
    e.g.  tet:coarse:n=14,g=1.2   hex:medium:n=10,g=1.3,shape=blunt

Needs OpenFOAM (and SIMWB_MPI_DIR when run from a checkout without an mpi folder). One JSON line per case is appended
to <out.jsonl>; benchmarks/mesh_analyze.py turns them into tables and a grid-convergence estimate.
"""
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

L, B, T, NU, RHO = 1.0, 0.5, 0.01, 1.516e-5, 1.204
SPEED = {"lam": 3.0, "turb": 30.0}


def plate_step(path: Path, sharp: bool = True) -> Path:
    import gmsh

    gmsh.initialize(["-nopopup"])
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("plate")
        occ = gmsh.model.occ
        if not sharp:
            occ.addBox(0, -B * 500, -T * 500, L * 1000, B * 1000, T * 1000)   # (mm), centred on y and z
        else:
            h, w, y0 = 2.0, 50.0, -B * 500                                   # half thickness, wedge length (mm)
            xz = [(0, 0), (w, h), (L * 1000 - w, h), (L * 1000, 0), (L * 1000 - w, -h), (w, -h)]
            pts = [occ.addPoint(x, y0, z) for x, z in xz]
            lines = [occ.addLine(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))]
            s = occ.addPlaneSurface([occ.addCurveLoop(lines)])
            occ.extrude([(2, s)], 0, B * 1000, 0)
        occ.synchronize()
        gmsh.write(str(path))
    finally:
        gmsh.finalize()
    return path


def reference_cf(mode: str):
    """Blasius for the laminar plate; None where there is no exact answer."""
    return 1.328 / math.sqrt(SPEED[mode] * L / NU) if mode == "lam" else None


def _last(path: Path, pattern: str):
    if path.exists():
        m = re.findall(pattern, path.read_text(errors="ignore"))
        if m:
            return m[-1]
    return None


def final_cells(pr, mesh_cells: int) -> int:
    """The cells the solver really ran on. A tetrahedral mesh gets its wall layers when the case runs (snappyHexMesh
    in layer-only mode), so the mesh statistics do not include them: read the count from that step's log."""
    case = Path(pr.case_dir)
    for name, pat in (("log.snappyHexMesh", r"Layer mesh : cells:(\d+)"), ("log.decomposePar", r"(?m)^Number of cells = (\d+)"),
                      ("log.renumberMesh", r"(?m)^\s*cells:\s+(\d+)")):
        v = _last(case / name, pat)
        if v:
            return int(v)
    return int(mesh_cells)


def layer_table(case: Path):
    """The layer step's own final table: the mean number of layers the wall got and the share of the target thickness
    (snappyHexMesh: 'patch faces layers overall thickness / target mesh [m] [%]'). Faces that merely kept a thin layer
    count as extruded in the 'Extruding' lines, so this is the honest figure. None when the log has no such table."""
    p = Path(case) / "log.snappyHexMesh"
    if not p.exists():
        return None
    txt = p.read_text(errors="ignore")
    i = txt.rfind("overall thickness")
    if i < 0:
        return None
    rows = re.findall(r"(?m)^\s*(\S+)\s+(\d+)\s+(\d+)\s+([\d.]+)\s+([\d.eE+-]+)\s+([\d.]+)\s*$", txt[i:i + 600])
    if not rows:
        return None
    faces = [int(r[1]) for r in rows]
    w = lambda k: sum(float(r[k]) * f for r, f in zip(rows, faces)) / max(sum(faces), 1)  # noqa: E731
    return {"layers_mean": round(w(3), 2), "layers_target": int(rows[0][2]), "thickness_percent": round(w(5), 1)}


def layers_ok(row: dict) -> bool:
    """One rule for 'the layers are there': at least 80 % of the target thickness when the log says so, else at least
    90 % of the wall faces extruded; a run with layers requested and neither figure cannot be checked: not ok."""
    if not row.get("layers"):
        return True
    if row.get("layers_thickness_percent") is not None:
        return row["layers_thickness_percent"] >= 80.0
    return row.get("layers_kept_percent") is not None and row["layers_kept_percent"] >= 90.0


def cf_history(case: Path, speed: float, every: int = 25):
    """Cf from the solver's own force output (postProcessing/forces/*/force.dat, the viscous x force column) at every
    `every`-th iteration and at the last one: {"it": [...], "cf": [...]}, or None when there is no such file."""
    files = sorted(Path(case).glob("postProcessing/forces/*/force.dat"), key=lambda q: float(q.parent.name))
    rows = []
    for f in files:
        for ln in f.read_text(errors="ignore").splitlines():
            if ln.strip() and not ln.startswith("#"):
                v = ln.replace("(", " ").replace(")", " ").split()
                if len(v) >= 10:
                    rows.append((int(float(v[0])), abs(float(v[7]))))
    if not rows:
        return None
    q = 0.5 * RHO * speed * speed * 2 * L * B
    keep = [r for r in rows if r[0] % every == 0 or r is rows[-1]]
    return {"it": [r[0] for r in keep], "cf": [round(r[1] / q, 7) for r in keep]}


def drift_percent(hist: dict, window: int = 100):
    """Change of Cf over the last `window` iterations of the history, in percent of the final value."""
    if not hist or len(hist["it"]) < 2:
        return None
    last = hist["it"][-1]
    j = max((i for i, t in enumerate(hist["it"]) if t <= last - window), default=None)
    return None if j is None else round(100 * (hist["cf"][-1] / hist["cf"][j] - 1), 4)


def layer_share(pr, stats: dict):
    """Percent of the wall faces that kept their layers (the last 'Extruding' line of the layer step)."""
    if (stats.get("hex") or {}).get("layers_extruded_percent") is not None:
        return stats["hex"]["layers_extruded_percent"]
    v = _last(Path(pr.case_dir) / "log.snappyHexMesh", r"Extruding \d+ out of \d+ faces \(([\d.]+)%\)")
    return float(v) if v else None


def run_one(method: str, preset: str, opts: dict) -> dict:
    layers, growth = int(opts.get("n", 5)), float(opts.get("g", 1.2))
    mode, shape, iterations = opts.get("mode", "lam"), opts.get("shape", "sharp"), int(opts.get("it", 1500))
    U = SPEED[mode]
    home = tempfile.mkdtemp(prefix="simwb_bench_")
    os.environ["SIMWB_HOME"] = home
    os.environ.setdefault("SIMWB_AUTO_OPENFOAM", "0")
    import simwb.project as P

    try:   # which code made the row: results of an older version of a method must not be mixed with the current one
        rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain", "--", "simwb"], capture_output=True, text=True, cwd=ROOT).stdout.strip())
    except Exception:  # noqa: BLE001
        rev, dirty = "unknown", False
    out = {"git": rev + ("+uncommitted" if dirty else ""), "edge_field": "v2", "method": method, "preset": preset,
           "layers": layers, "growth": growth, "mode": mode, "shape": shape,
           "cf_reference": reference_cf(mode) if shape == "sharp" else None}   # (no exact answer for the blunt slab)
    pr = P.create_project("Plate", "external_aero")
    j = pr.import_step(plate_step(Path(home) / "plate.step", sharp=(shape == "sharp")), original_name="plate.step")
    assert j.wait(300) and j.status == "done", j.error
    pr.set_settings(speed=U, pitch_deg=0, iterations=iterations, turbulence="laminar" if mode == "lam" else "kOmegaSST")
    stop = float(opts.get("stop", 0.2))               # the app's stop rule; 0 = off (run to the iteration limit)
    pr.set_settings(auto_stop_tol=stop)
    out["stop_tol_percent"] = stop
    pr.set_mesh(preset=preset, layers={"n": layers, "growth": growth, "tolerant": opts.get("tol") == "1"})
    out["tolerant_layers"] = opts.get("tol") == "1"
    if opts.get("edge"):
        pr.set_mesh(edge_refinement=float(opts["edge"]))
        out["edge_factor"] = float(opts["edge"])
    if opts.get("le") and shape == "sharp":
        # fine cells on the wedge faces at the leading edge (x < 60 mm): the first 5 % of a laminar plate carries about
        # 22 % of its friction, so the streamwise resolution there decides the mean Cf
        ids = [f["id"] for f in pr.faces() if f.get("kind") != "farfield" and f["centroid"][0] < 0.06]
        pr.set_mesh(refinements=[{"faces": ids, "size_mm": float(opts["le"])}])
        out["le_faces"], out["le_mm"] = len(ids), float(opts["le"])
    if method != "tet":
        pr.set_mesh(method=method)
    t0 = time.time()
    j = pr.generate_mesh()
    ok = j.wait(3600)
    out["mesh_seconds"] = round(time.time() - t0, 1)
    if not ok or j.status != "done":
        out["error"] = f"mesh: {j.error}"
        return out
    st = pr.data["mesh"]["stats"]
    out.update(cells=st["cells"], mesh_ram_mb=(st.get("ram") or {}).get("added_ram_mb"),
               nonortho_max=(st.get("foam_quality") or {}).get("non_orthogonality_max"),
               skew_max=(st.get("foam_quality") or {}).get("skewness_max"),
               hex_percent=round(100 * st["cell_types"]["hexahedra"] / st["cells"], 1) if st.get("cell_types") else 0)
    if opts.get("procs"):
        pr.set_settings(n_procs=int(opts["procs"]))
        out["procs"] = int(opts["procs"])
    t0 = time.time()
    j = pr.solve()
    ok = j.wait(7200)
    out["solve_seconds"] = round(time.time() - t0, 1)
    sm = (pr.data["solve"].get("summary") or {})
    if not ok or pr.data["solve"].get("status") not in ("done",) or not sm:
        out["error"] = f"solve: {j.error or pr.data['solve'].get('status')}"
        return out
    ram = sm.get("ram") or {}
    out["cells_final"] = final_cells(pr, out["cells"])
    out["layers_kept_percent"] = layer_share(pr, st)
    hist = cf_history(pr.case_dir, U)
    if hist:
        out.update(cf_history=hist, cf_drift_last100_percent=drift_percent(hist), stopped_at=hist["it"][-1])
    lt = layer_table(pr.case_dir)
    if lt:
        out.update(layers_mean=lt["layers_mean"], layers_thickness_percent=lt["thickness_percent"])
    visc = sm.get("viscous_force_N") or [0, 0, 0]
    q = 0.5 * RHO * U * U
    out.update(cf=abs(visc[0]) / (q * 2 * L * B), viscous_force_N=visc[0], drag_N=sm.get("drag_N"), yplus=sm.get("yPlus"),
               solve_ram_mb=ram.get("solver_added_ram_mb") if ram.get("solver_added_ram_mb") is not None else ram.get("added_ram_mb"),
               converged=bool((sm.get("convergence") or {}).get("converged")), iterations=(sm.get("convergence") or {}).get("iterations"))
    # valid = converged AND the wall layers it was meant to have are there (layers_ok): a mesh that lost its layers
    # (a leading edge without a boundary-layer mesh) says nothing about the method, and its error can be anything
    out["valid"] = out["converged"] and layers_ok({**out, "layers": layers})
    if out["converged"] and not out["valid"]:
        out["invalid_reason"] = (f"the wall got {out['layers_mean']} of {layers} layers ({out['layers_thickness_percent']} % of the "
                                 "target thickness)" if out.get("layers_mean") is not None else "layer coverage could not be checked")
    mr, sr = out.get("mesh_ram_mb"), out.get("solve_ram_mb")
    if mr is not None and sr is not None:
        out["total_ram_mb"] = max(mr, sr)       # the mesher and the solver do not run together: the larger one is the need
        out["ram_kb_per_final_cell"] = round(out["total_ram_mb"] * 1024 / out["cells_final"], 2)
    if out["cf_reference"]:
        out["cf_error_percent"] = round(100 * (out["cf"] / out["cf_reference"] - 1), 2)
    plan = [out.get("procs", 0)] * (int(opts.get("solves", 1)) - 1)       # the same mesh again: only the solve side can differ now
    if opts.get("procs2"):                                                # ... and with another number of cores
        plan += [int(opts["procs2"])] * int(opts.get("solves2", 3))
    for procs in plan:
        pr.set_settings(n_procs=procs)
        t0 = time.time()
        j = pr.solve()
        ok = j.wait(7200)
        sm = (pr.data["solve"].get("summary") or {})
        if not ok or not sm:
            out.setdefault("solve_repeats", []).append({"error": str(j.error or pr.data["solve"].get("status"))})
            continue
        h = cf_history(pr.case_dir, U)
        v = sm.get("viscous_force_N") or [0, 0, 0]
        out.setdefault("solve_repeats", []).append(
            {"procs": procs, "cf": abs(v[0]) / (q * 2 * L * B), "cells_final": final_cells(pr, out["cells"]), "stopped_at": h["it"][-1] if h else None,
             "solve_seconds": round(time.time() - t0, 1), "cf_history": h})
    return out


def parse_spec(spec: str):
    m, p, *rest = spec.split(":")
    opts = dict(kv.split("=") for kv in rest[0].split(",")) if rest and rest[0] else {}
    return m, p, opts


if __name__ == "__main__":
    if sys.argv[1] == "--one":                       # worker: one case, the JSON on the last line of stdout
        m, p, o = parse_spec(sys.argv[2])
        try:
            r = run_one(m, p, o)
        except Exception as e:  # noqa: BLE001
            r = {"method": m, "preset": p, "error": f"{type(e).__name__}: {e}"}
        print("RESULT " + json.dumps(r), flush=True)
        sys.exit(0)
    dest = Path(sys.argv[1])
    for spec in sys.argv[2:]:
        for attempt in (1, 2, 3):
            proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--one", spec], capture_output=True, text=True)
            line = next((ln[7:] for ln in reversed(proc.stdout.splitlines()) if ln.startswith("RESULT ")), None)
            r = json.loads(line) if line else {"spec": spec, "error": "worker failed: " + (proc.stderr or proc.stdout)[-300:]}
            # the OpenFOAM install of this machine is shared: another process starting without the MPI folder swaps
            # its parallel library for a serial stub and a run that starts at that moment dies. Such a run is repeated.
            if not str(r.get("error", "")).startswith("solve: OpenFOAM run failed"):
                break
            r["retried"] = attempt
        r["spec"] = spec
        print(json.dumps(r), flush=True)
        with open(dest, "a", encoding="utf-8") as f:
            f.write(json.dumps(r) + "\n")
