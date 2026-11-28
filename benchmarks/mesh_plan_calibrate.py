"""Measure the memory planner (simwb/meshplan.py) against real meshes: for each case the estimate (cells, memory) is worked out first, then the tetrahedral mesh is
made by the gmsh worker (no OpenFOAM: no wall layers, no checkMesh) with the memory meter around it, and one line of JSON is written.

    python benchmarks/mesh_plan_calibrate.py <examples/foo.step> <workspace dir> <out.jsonl> preset=quick smart=0 [edge=6] [size_max_mm=..] [size_min_mm=..] [growth=..]

Run it from the repository root with the project's Python. One case at a time: the meter counts everything the worker holds."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def main(argv):
    step, ws, out = Path(argv[0]).resolve(), argv[1], Path(argv[2])
    kv = dict(a.split("=", 1) for a in argv[3:])
    os.environ["SIMWB_HOME"] = ws
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import simwb.project as P
    from simwb import meshplan, memwatch
    from simwb.jobs import ROOT
    from simwb.memlimit import LimitedPopen

    study = kv.get("study", "external_aero")
    pr = P.create_project("calib", study)
    j = pr.import_step(step, original_name=step.name)
    assert j.wait(600) and j.status == "done", j.error
    if study in ("external_aero", "cht_enclosure"):
        pr.set_settings(speed=float(kv.get("speed", 10))) if study == "external_aero" else None
    kw = {"preset": kv.get("preset", "quick")}
    if kv.get("smart") == "1":
        kw["smart"] = True
    if kv.get("size_max_mm"):
        kw["size_max_mm"] = float(kv["size_max_mm"])
    if kv.get("size_min_mm"):
        kw["size_min_mm"] = float(kv["size_min_mm"])
    if kv.get("edge"):
        kw["edge_refinement"] = float(kv["edge"])
    pr.set_mesh(**kw)
    if kv.get("growth"):
        pr.data["mesh"]["growth"] = float(kv["growth"])
    if kv.get("budget_mb"):
        pr.data["mesh"]["budget"] = {"mode": "mb", "value": float(kv["budget_mb"]), "on": True}
    t = time.time()
    rep = pr.mesh_estimate()
    t_est = time.time() - t
    ms, rc, plan = pr._mesh_inputs()
    if plan and plan["status"] == "refused":
        print(json.dumps({"case": kv, "refused": plan["message"]}))
        return
    payload = pr._mesh_payload(ms)
    pr.mesh_dir.mkdir(parents=True, exist_ok=True)
    jp = pr.mesh_dir / "_mesh_job.json"
    jp.write_text(json.dumps(payload))
    t0 = time.time()
    with memwatch.PeakMemory() as pm:
        lp = LimitedPopen([sys.executable, "-u", "-m", "simwb.gmsh_worker", "mesh", str(jp)], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        result, err = None, None
        for line in lp.proc.stdout:
            if line.startswith("RESULT "):
                result = json.loads(line[7:])
            elif line.startswith("ERROR "):
                err = line[6:]
        lp.proc.wait()
        lp.close()
    rec = {"case": {"example": step.name, "study": study, **kv}, "estimate": rep.get("estimate"), "applied": rep.get("applied"), "estimate_seconds": round(t_est, 2),
           "seconds": round(time.time() - t0, 1), "ram": pm.result(), "version": "meshplan-1"}
    if result is None:
        rec["error"] = err
    else:
        rec["cells"] = result.get("cells")
        rec["nodes"] = result.get("nodes")
        er = result.get("edge_refine")
        rec["edge_extra_estimated_by_mesher"] = er and er.get("estimated_extra_cells")
        rec["sizes"] = result.get("sizes")
    with open(out, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    print(json.dumps({k: rec.get(k) for k in ("case", "cells", "ram", "seconds")}), "estimate:", (rec["estimate"] or {}).get("cells"))


if __name__ == "__main__":
    main(sys.argv[1:])
