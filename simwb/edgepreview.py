"""The sharp-edge refinement of the Mesh step before there is a mesh: what is switched on and why, which edges of the geometry it would refine, and about how
many extra cells that adds. The decision (which edges, what cell size, the cap) is gmsh_worker.edge_refine_plan - the mesher uses the same function - so the
estimate shown before meshing is what the mesher then does. Only the finding of the edges needs the CAD kernel: it runs in the mesher's worker program
(task "edges", a second or two) on its own thread, once per geometry, and the answer is kept."""
from __future__ import annotations

import json
import subprocess
import sys
import threading

from . import gmsh_worker

SMART_FACTOR = 4.0          # what smart sizing (the mesh advisor's recipe) asks for when no setting of your own is there (meshadvisor.recipe)
FACTOR_RANGE = (2.0, 20.0)  # the edge cells are the wall cells divided by this
ANGLE_RANGE = (20.0, 175.0)  # degrees between the two faces at the edge; 180 is smooth
CAP_RANGE = (1_000.0, 5_000_000.0)
_LOCK = threading.Lock()


def check_options(er) -> dict:
    """The stored form of an edge refinement setting: {factor, [max_cells], [max_angle]} from True, a factor or a dictionary; ValueError says what is wrong."""
    from . import studies

    if er is True:
        return {"factor": 6.0}
    if not isinstance(er, dict):
        er = {"factor": er}
    out = {"factor": 6.0 if er.get("factor") in (None, "") else studies.finite(er["factor"], "Edge refinement factor")}
    if not FACTOR_RANGE[0] <= out["factor"] <= FACTOR_RANGE[1]:
        raise ValueError("Edge refinement factor must be 2..20 (cells at the edge are the wall size / factor)")
    if er.get("max_cells") not in (None, ""):
        cap = studies.finite(er["max_cells"], "Edge refinement cell cap")
        if not CAP_RANGE[0] <= cap <= CAP_RANGE[1]:
            raise ValueError("The cap on the extra cells of the edge refinement must be 1,000 to 5,000,000")
        out["max_cells"] = cap
    if er.get("max_angle") not in (None, ""):
        ang = studies.finite(er["max_angle"], "Edge refinement angle")
        if not ANGLE_RANGE[0] <= ang <= ANGLE_RANGE[1]:
            raise ValueError("The edge angle must be 20 to 175 degrees (edges where the faces meet at less than this are refined)")
        out["max_angle"] = ang
    return out


def unavailable(pr) -> str | None:
    """Why the edge refinement cannot be used in this study, in words; None when it can."""
    if pr.study != "external_aero":
        return "Not for this type of study: edge refinement is for external aerodynamics (a body in a flow)"
    if pr.data["mesh"].get("method") == "hex":
        return "Not for a hex-dominant mesh: snappyHexMesh makes it and does not use this setting (switch to the tetrahedral mesh)"
    if pr.data["geometry"].get("status") != "ready":
        return "Not yet: import the geometry first (step 1, Geometry)"
    return None


def settings(pr) -> dict:
    """What the mesher will do about edges for the stored settings: {on, source: 'setting' | 'smart' | None, factor, max_cells, max_angle}.
    Smart sizing refines the edges by itself (factor 4) unless a setting of your own says otherwise (edge_refinement off, or a factor)."""
    m = pr.data["mesh"]
    er = m.get("edge_refine")
    if er:
        return {"on": True, "source": "setting", "factor": float(er.get("factor", 6.0)), "max_cells": float(er.get("max_cells", gmsh_worker.EDGE_CAP)),
                "max_angle": float(er.get("max_angle", gmsh_worker.EDGE_ANGLE))}
    smart = bool(m.get("smart")) and not m.get("no_edge_refine")
    return {"on": smart, "source": "smart" if smart else None, "factor": SMART_FACTOR if smart else 6.0,
            "max_cells": float(gmsh_worker.EDGE_CAP), "max_angle": float(gmsh_worker.EDGE_ANGLE)}


# ---------------------------------------------------------------------------------------------------------------------------------- finding the edges
def _key(pr):
    return (pr.id, pr.data["geometry"].get("updated"), pr.data["geometry"].get("status"))


def scan_state(pr) -> dict:
    """{state: 'none' | 'running' | 'ready' | 'failed', result, error} of the edge search of the current geometry (it does not start one)."""
    sc = getattr(pr, "_edge_scan", None)
    if not sc or sc["key"] != _key(pr):
        return {"state": "none"}
    return {k: sc.get(k) for k in ("state", "result", "error")}


def run_scan(pr) -> dict:
    """Find the sharp edges now (a subprocess of the mesher's worker); the edges {edges, count, ref_length, flow_axis}."""
    from .jobs import ROOT
    from .memlimit import LimitedPopen

    job = {"brep": str(pr.geo_dir / "domain.brep"), "faces_json": str(pr.geo_dir / "faces.json"), "domain_info": str(pr.geo_dir / "domain_info.json")}
    jp = pr.mesh_dir / "_edges_job.json"
    pr.mesh_dir.mkdir(parents=True, exist_ok=True)
    jp.write_text(json.dumps(job))
    lp = LimitedPopen([sys.executable, "-u", "-m", "simwb.gmsh_worker", "edges", str(jp)], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    result, error = None, None
    try:
        for line in lp.proc.stdout:
            if line.startswith("RESULT "):
                result = json.loads(line[7:])
            elif line.startswith("ERROR "):
                error = line[6:].strip()
        lp.proc.wait()
    finally:
        lp.close()
        jp.unlink(missing_ok=True)
    if result is None:
        raise RuntimeError(error or "The search for sharp edges failed")
    return result


def ensure_scan(pr, wait: bool = False) -> dict:
    """Start the edge search of the current geometry when there is none (on a thread of its own: the window stays alive), and say where it is. With `wait`
    the call returns when it is done. The project's change event tells the window when the answer is in."""
    key = _key(pr)
    with _LOCK:
        sc = getattr(pr, "_edge_scan", None)
        if not sc or sc["key"] != key:
            sc = {"key": key, "state": "running", "result": None, "error": None}
            pr._edge_scan = sc

            def work():
                try:
                    sc["result"] = run_scan(pr)
                    sc["state"] = "ready"
                except Exception as e:  # noqa: BLE001 - a help: it says why it has no numbers
                    sc["error"], sc["state"] = str(e), "failed"
                from .jobs import JOBS

                JOBS.emit({"type": "project_changed", "project": pr.id})

            t = threading.Thread(target=work, name="edge-scan", daemon=True)
            sc["thread"] = t
            t.start()
    if wait and sc.get("thread") is not None:
        sc["thread"].join()
    return scan_state(pr)


# ---------------------------------------------------------------------------------------------------------------------------------- the preview
def preview(pr, scan: bool = False, wait: bool = False) -> dict:
    """The sharp-edge refinement as the stored settings would run it: {available, reason, on, source, factor, max_cells, max_angle, smart, blocked,
    scan, h_wall, plan: {applied, reason, edges (count), total_length, h_edge, factor_used, estimated_extra_cells, capped, cap_cells} | None,
    lines: [polyline of each refined edge]}. `scan` starts the edge search when there is none (needed for the plan); `wait` waits for it."""
    m = pr.data["mesh"]
    why = unavailable(pr)
    s = settings(pr)
    out = {"available": why is None, "reason": why, **s, "smart": bool(m.get("smart")), "blocked": None, "scan": "none", "scan_error": None,
           "h_wall": None, "plan": None, "lines": []}
    if why is not None:
        return out
    if m.get("size_min") and not m.get("smart"):
        out["blocked"] = "size_min"                      # (the mesher keeps to the smallest cell you set by hand, and leaves the edges alone)
    sc = ensure_scan(pr, wait=wait) if scan else scan_state(pr)
    out["scan"], out["scan_error"] = sc["state"], sc.get("error")
    if sc["state"] != "ready":
        return out
    from . import meshadvisor

    info = pr.data["geometry"]["info"]
    faces = pr.faces()
    L = float(info["ref_length"])
    rc = None
    if m.get("smart"):
        an = meshadvisor.analyse(pr.data, faces, info)
        rc = meshadvisor.recipe(an, m.get("preset", "medium"), m.get("growth"))
    n_per, _c = gmsh_worker.PRESETS.get(m.get("preset", "medium"), gmsh_worker.PRESETS["medium"])
    h_body = float(m.get("size_max") or (rc["h_near"] if rc else L / n_per))
    h_wall = gmsh_worker.aero_wall_size(m, gmsh_worker.smallest_feature(faces, L), L, h_body, rc)
    growth = float(rc["growth"]) if rc else float(m.get("growth", 1.25))
    edges = [(e["tag"], e["angle"], e["length"], e["points"]) for e in sc["result"]["edges"]]
    out["h_wall"], out["growth"] = h_wall, growth
    out["edges_found"] = len(edges)
    out["edges_sharp"] = len([e for e in edges if e[1] < s["max_angle"]])
    plan = gmsh_worker.edge_refine_plan(edges, h_wall, L, growth, s["factor"], s["max_cells"], s["max_angle"])
    out["lines"] = [e[3] for e in plan["edges"]] if plan["applied"] else []
    out["plan"] = {k: v for k, v in plan.items() if k != "edges"}
    out["plan"]["edges"] = len(plan["edges"])
    return out
