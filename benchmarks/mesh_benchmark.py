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
                                                   mode (lam|turb), shape (sharp|blunt), it (iterations, default 1500)
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

    out = {"method": method, "preset": preset, "layers": layers, "growth": growth, "mode": mode, "shape": shape,
           "cf_reference": reference_cf(mode) if shape == "sharp" else None}   # (no exact answer for the blunt slab)
    pr = P.create_project("Plate", "external_aero")
    j = pr.import_step(plate_step(Path(home) / "plate.step", sharp=(shape == "sharp")), original_name="plate.step")
    assert j.wait(300) and j.status == "done", j.error
    pr.set_settings(speed=U, pitch_deg=0, iterations=iterations, turbulence="laminar" if mode == "lam" else "kOmegaSST")
    pr.set_mesh(preset=preset, layers={"n": layers, "growth": growth})
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
    visc = sm.get("viscous_force_N") or [0, 0, 0]
    q = 0.5 * RHO * U * U
    out.update(cf=abs(visc[0]) / (q * 2 * L * B), viscous_force_N=visc[0], drag_N=sm.get("drag_N"), yplus=sm.get("yPlus"),
               solve_ram_mb=ram.get("solver_added_ram_mb") if ram.get("solver_added_ram_mb") is not None else ram.get("added_ram_mb"),
               converged=bool((sm.get("convergence") or {}).get("converged")), iterations=(sm.get("convergence") or {}).get("iterations"))
    out["valid"] = out["converged"]
    mr, sr = out.get("mesh_ram_mb"), out.get("solve_ram_mb")
    if mr is not None and sr is not None:
        out["total_ram_mb"] = max(mr, sr)       # the mesher and the solver do not run together: the larger one is the need
        out["ram_kb_per_final_cell"] = round(out["total_ram_mb"] * 1024 / out["cells_final"], 2)
    if out["cf_reference"]:
        out["cf_error_percent"] = round(100 * (out["cf"] / out["cf_reference"] - 1), 2)
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
        proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--one", spec], capture_output=True, text=True)
        line = next((ln[7:] for ln in reversed(proc.stdout.splitlines()) if ln.startswith("RESULT ")), None)
        r = json.loads(line) if line else {"spec": spec, "error": "worker failed: " + (proc.stderr or proc.stdout)[-300:]}
        r["spec"] = spec
        print(json.dumps(r), flush=True)
        with open(dest, "a", encoding="utf-8") as f:
            f.write(json.dumps(r) + "\n")
