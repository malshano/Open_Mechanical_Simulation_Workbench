"""Mesh benchmark: accuracy against a known answer, cells, time and RAM, for every mesh method and preset.

Case: a thin flat plate (1.0 x 0.5 x 0.01 m) along the flow in air.
  lam  : laminar flow at 3 m/s (Re_L = 2e5). The mean friction coefficient has an exact answer (Blasius):
         Cf = 1.328 / sqrt(Re_L). The error of the computed Cf is how well the mesh resolves the boundary layer.
  turb : k-omega SST at 30 m/s. No exact reference (the model starts laminar at the leading edge, so the fully
         turbulent formula does not apply); Cf and y+ are reported to compare methods with each other.
The workbench reports the viscous force on the plate; Cf = F_viscous / (q * 2 * plate area).

    python benchmarks/mesh_benchmark.py <out.jsonl> <method:preset[:layers[:mode[:shape]]]> [...]
    e.g.  tet:coarse  hex:medium:14:lam  tet:fine:14:lam:sharp   (shape: sharp (default) | blunt)

Needs OpenFOAM. One line of JSON per case is appended to <out.jsonl>.
"""
import json
import math
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

L, B, T, NU, RHO = 1.0, 0.5, 0.01, 1.516e-5, 1.204


def plate_step(path: Path, sharp: bool = True) -> Path:
    """sharp: 4 mm thick with 50 mm wedges at both ends (half-angle 2.3 degrees), so the flow meets a sharp edge and
    Blasius applies within a percent or two. blunt: a 10 mm slab, whose square nose separates the flow (no exact
    reference; kept to show why)."""
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


SPEED = {"lam": 3.0, "turb": 30.0}


def reference_cf(mode: str):
    """Blasius for the laminar plate; None where there is no exact answer."""
    return 1.328 / math.sqrt(SPEED[mode] * L / NU) if mode == "lam" else None


def final_cells(pr, mesh_cells: int) -> int:
    """The cells the solver really ran on. A tetrahedral mesh gets its wall layers when the case runs (snappyHexMesh
    in layer-only mode), so the mesh statistics do not include them: read the count from that step's log, then from
    renumberMesh / decomposePar, and fall back to the mesh's own count."""
    import re

    case = Path(pr.case_dir)
    for name, pat in (("log.snappyHexMesh", r"Layer mesh : cells:(\d+)"), ("log.decomposePar", r"(?m)^Number of cells = (\d+)"),
                      ("log.renumberMesh", r"(?m)^\s*cells:\s+(\d+)")):
        p = case / name
        if p.exists():
            m = re.findall(pat, p.read_text(errors="ignore"))
            if m:
                return int(m[-1])
    return int(mesh_cells)


def run(method: str, preset: str, layers: int, mode: str = "lam", shape: str = "sharp", iterations: int = 600) -> dict:
    U = SPEED[mode]
    home = tempfile.mkdtemp(prefix="simwb_bench_")
    os.environ["SIMWB_HOME"] = home
    os.environ.setdefault("SIMWB_AUTO_OPENFOAM", "0")
    import simwb.project as P

    pr = P.create_project("Plate", "external_aero")
    j = pr.import_step(plate_step(Path(home) / "plate.step", sharp=(shape == "sharp")), original_name="plate.step")
    assert j.wait(300) and j.status == "done", j.error
    pr.set_settings(speed=U, pitch_deg=0, iterations=iterations, turbulence="laminar" if mode == "lam" else "kOmegaSST")
    pr.set_mesh(preset=preset, layers={"n": layers, "growth": 1.2})
    if method != "tet":
        pr.set_mesh(method=method)
    out = {"method": method, "preset": preset, "layers": layers, "mode": mode, "shape": shape,
           "cf_reference": reference_cf(mode) if shape == "sharp" else None}   # (no exact answer for the blunt slab)
    t0 = time.time()
    j = pr.generate_mesh()
    ok = j.wait(3600)
    out["mesh_seconds"] = round(time.time() - t0, 1)
    if not ok or j.status != "done":
        out["error"] = f"mesh: {j.error}"
        return out
    st = pr.data["mesh"]["stats"]
    out.update(cells=st["cells"], mesh_ram_mb=(st.get("ram") or {}).get("added_ram_mb"),
               mesh_peak_ram_mb=(st.get("ram") or {}).get("peak_ram_mb"),
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
    out["cells_final"] = final_cells(pr, out["cells"])
    visc = sm.get("viscous_force_N") or [0, 0, 0]
    wetted = 2 * L * B + 2 * L * T + 2 * B * T          # all faces (Cf above uses the two large faces only)
    q = 0.5 * RHO * U * U
    out.update(cf=abs(visc[0]) / (q * 2 * L * B), cf_all_faces=abs(visc[0]) / (q * wetted),
               viscous_force_N=visc[0], drag_N=sm.get("drag_N"), yplus=sm.get("yPlus"),
               solve_ram_mb=(sm.get("ram") or {}).get("added_ram_mb"), solve_peak_ram_mb=(sm.get("ram") or {}).get("peak_ram_mb"),
               converged=(sm.get("convergence") or {}).get("converged"), iterations=(sm.get("convergence") or {}).get("iterations"))
    if out["cf_reference"]:
        out["cf_error_percent"] = round(100 * (out["cf"] / out["cf_reference"] - 1), 2)
    return out


if __name__ == "__main__":
    dest = Path(sys.argv[1])
    for spec in sys.argv[2:]:
        m, p, *rest = spec.split(":")
        r = run(m, p, int(rest[0]) if rest else 5, rest[1] if len(rest) > 1 else "lam", rest[2] if len(rest) > 2 else "sharp")
        print(json.dumps(r), flush=True)
        with open(dest, "a", encoding="utf-8") as f:
            f.write(json.dumps(r) + "\n")
