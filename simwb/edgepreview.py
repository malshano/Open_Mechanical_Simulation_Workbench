"""The sharp-edge refinement of the Mesh step before there is a mesh: what is switched on and why, which edges of the geometry it would refine, and about how
many extra cells that adds. The decision (which edges, what cell size, the cap) is gmsh_worker.edge_refine_plan - the mesher uses the same function - so the
estimate shown before meshing is what the mesher then does. Only the finding of the edges needs the CAD kernel: it runs in the mesher's worker program
(task "edges", a second or two) on its own thread, once per geometry, and the answer is kept."""
from __future__ import annotations

import atexit
import json
import subprocess
import sys
import threading
import uuid

from . import gmsh_worker

SMART_FACTOR = 4.0          # what smart sizing (the mesh advisor's recipe) asks for when no setting of your own is there (meshadvisor.recipe)
FACTOR_RANGE = (2.0, 20.0)  # the edge cells are the wall cells divided by this
ANGLE_RANGE = (20.0, 175.0)  # degrees between the two faces at the edge; 180 is smooth
CAP_RANGE = (1_000.0, 5_000_000.0)
_LOCK = threading.Lock()


def check_options(er, base: dict | None = None, default_factor: float = 6.0) -> dict:
    """The stored form of an edge refinement setting: {factor, [max_cells], [max_angle]} from True, a factor or a dictionary; ValueError says what is wrong.
    What the call does not name keeps its value in `base` (the stored setting): a call that only gives the cap must not reset the factor."""
    from . import studies

    out = dict(base or {})
    if er is not True:
        if not isinstance(er, dict):
            er = {"factor": er}
        if er.get("factor") not in (None, ""):
            out["factor"] = studies.finite(er["factor"], "Edge refinement factor")
        if er.get("max_cells") not in (None, ""):
            out["max_cells"] = studies.finite(er["max_cells"], "Edge refinement cell cap")
        if er.get("max_angle") not in (None, ""):
            out["max_angle"] = studies.finite(er["max_angle"], "Edge refinement angle")
    out.setdefault("factor", default_factor)
    if not FACTOR_RANGE[0] <= out["factor"] <= FACTOR_RANGE[1]:
        raise ValueError("Edge refinement factor must be 2..20 (cells at the edge are the wall size / factor)")
    if "max_cells" in out and not CAP_RANGE[0] <= out["max_cells"] <= CAP_RANGE[1]:
        raise ValueError("The cap on the extra cells of the edge refinement must be 1,000 to 5,000,000")
    if "max_angle" in out and not ANGLE_RANGE[0] <= out["max_angle"] <= ANGLE_RANGE[1]:
        raise ValueError("The edge angle must be 20 to 175 degrees (edges where the faces meet at less than this are refined)")
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
    return {"on": smart, "source": "smart" if smart else None, "factor": SMART_FACTOR if m.get("smart") else 6.0,       # (with smart sizing the default is its own 4)
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


_PROCS: dict = {}            # project id -> the running edge search (LimitedPopen), so that a new geometry or the end of the program can stop it


def cancel_scan(pr_id: str) -> bool:
    """Stop the edge search of this project that is still running (the geometry changed, or the program is closing); True when there was one."""
    lp = _PROCS.pop(pr_id, None)
    if lp is None or lp.proc.poll() is not None:
        return False
    lp.close()                                          # (the worker and what it started end with the job object)
    return True


def cancel_all_scans():
    for k in list(_PROCS):
        cancel_scan(k)


atexit.register(cancel_all_scans)


def run_scan(pr) -> dict:
    """Find the sharp edges now (a subprocess of the mesher's worker); the edges {edges, count, ref_length, flow_axis}. Each search has a job file of its
    own: two searches (an old one finishing, a new one starting) never share or delete each other's."""
    from .jobs import ROOT
    from .memlimit import LimitedPopen

    job = {"brep": str(pr.geo_dir / "domain.brep"), "faces_json": str(pr.geo_dir / "faces.json"), "domain_info": str(pr.geo_dir / "domain_info.json")}
    pr.mesh_dir.mkdir(parents=True, exist_ok=True)
    jp = pr.mesh_dir / f"_edges_job_{uuid.uuid4().hex[:10]}.json"
    jp.write_text(json.dumps(job))
    lp = LimitedPopen([sys.executable, "-u", "-m", "simwb.gmsh_worker", "edges", str(jp)], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    _PROCS[pr.id] = lp
    result, error = None, None
    try:
        for line in lp.proc.stdout:
            if line.startswith("RESULT "):
                result = json.loads(line[7:])
            elif line.startswith("ERROR "):
                error = line[6:].strip()
        lp.proc.wait()
    finally:
        if _PROCS.get(pr.id) is lp:
            _PROCS.pop(pr.id, None)
        lp.close()
        jp.unlink(missing_ok=True)
    if result is None:
        raise RuntimeError(error or "The search for sharp edges was stopped or failed")
    return result


def _background(pr, attr: str, key, fn, wait: bool = False) -> dict:
    """Run fn() once per key on a thread of its own and keep the answer on the project as pr.<attr> = {key, state, result, error, thread}; the window is told
    (project change event) when it is in. Returns {state, result, error} of the current key."""
    with _LOCK:
        sc = getattr(pr, attr, None)
        if not sc or sc["key"] != key:
            if sc and attr == "_edge_scan" and sc["state"] == "running":
                cancel_scan(pr.id)                       # (the geometry changed under a search that is still running: it is of no use any more)
            sc = {"key": key, "state": "running", "result": None, "error": None}
            setattr(pr, attr, sc)

            def work():
                try:
                    sc["result"] = fn()
                    sc["state"] = "ready"
                except Exception as e:  # noqa: BLE001 - a help: it says why it has no numbers
                    sc["error"], sc["state"] = str(e), "failed"
                from .project import JOBS

                JOBS.emit({"type": "project_changed", "project": pr.id})

            t = threading.Thread(target=work, name=attr, daemon=True)
            sc["thread"] = t
            t.start()
    if wait and sc.get("thread") is not None:
        sc["thread"].join()
    return {k: sc.get(k) for k in ("state", "result", "error")}


def ensure_scan(pr, wait: bool = False) -> dict:
    """Start the edge search of the current geometry when there is none (on a thread of its own: the window stays alive), and say where it is. With `wait`
    the call returns when it is done."""
    return _background(pr, "_edge_scan", _key(pr), lambda: run_scan(pr), wait)


def measure(pr, wait: bool = False) -> dict:
    """What the last mesh holds at the refined edges, counted in the mesh itself (not estimated): {state, result: {zone_cells, zone_share, extra_cells,
    distance}} for the tetrahedra whose centre lies within the edge field's reach (stats.edge_refine.dist_max) of the edges that were refined; `extra_cells`
    is those cells less what the same volume holds at the wall cell size. None without a mesh made with edge refinement and a finished edge search."""
    st = (pr.data["mesh"].get("stats") or {})
    er = st.get("edge_refine")
    sc = scan_state(pr)
    if not er or sc["state"] != "ready":
        return {"state": "none"}
    return _background(pr, "_edge_measure", (_key(pr), st.get("seconds"), st.get("cells")), lambda: _measure(pr, er, sc["result"]), wait)


def _measure(pr, er: dict, scan: dict) -> dict:
    import numpy as np
    from scipy.spatial import cKDTree

    m = pr.load_mesh()
    if "hex" in m:
        raise ValueError("a hex-dominant mesh")
    nodes, tets = np.asarray(m["nodes"], float), np.asarray(m["tets"])
    h_wall = float(er["size_far_of_edge_field"])
    reach = float(er["dist_max"])
    # the refined edges again, as the plan chooses them from the same search
    edges = [(e["tag"], e["angle"], e["length"], e["points"]) for e in scan["edges"]]
    L = float(scan["ref_length"])
    plan = gmsh_worker.edge_refine_plan(edges, h_wall, L, float(er["growth"]), float(er["factor"]), float(er["cap_cells"]), float(er.get("angle_limit", gmsh_worker.EDGE_ANGLE)))
    pts = []
    for e in plan["edges"]:
        p = np.asarray(e[3], float)
        seg = np.linalg.norm(np.diff(p, axis=0), axis=1)
        n = max(int(np.ceil(seg.sum() / max(er["size"] * 0.5, 1e-9))), 2)
        t = np.concatenate([[0], np.cumsum(seg)])
        s = np.linspace(0, t[-1], min(n, 200_000))
        pts.append(np.stack([np.interp(s, t, p[:, k]) for k in range(3)], axis=1))
    if not pts:
        return {"zone_cells": 0, "zone_share": 0.0, "extra_cells": 0.0, "distance": reach}
    tree = cKDTree(np.vstack(pts))
    a, b, c, d = (nodes[tets[:, k]] for k in range(4))
    cen = (a + b + c + d) / 4.0
    dist = tree.query(cen)[0]
    vol = np.abs(np.einsum("ij,ij->i", np.cross(b - a, c - a), d - a)) / 6.0
    near = dist < reach
    # what the same volume would hold at the wall size: the mesh's own cells just outside the reach of the edge field (a shell as wide as half the reach), where the
    # field has come back to the wall size; a regular tetrahedron's volume would count the mesher's larger cells as extra
    shell = (dist >= reach) & (dist < 1.5 * reach)
    v_wall = float(vol[shell].mean()) if int(shell.sum()) >= 50 else 1.5 ** 3 * h_wall ** 3 / (6.0 * np.sqrt(2.0))
    zone = int(near.sum())
    return {"zone_cells": zone, "zone_share": zone / max(len(tets), 1), "extra_cells": max(zone - float(vol[near].sum()) / v_wall, 0.0), "distance": reach}


# ---------------------------------------------------------------------------------------------------------------------------------- the preview
def preview(pr, scan: bool = False, wait: bool = False) -> dict:
    """The sharp-edge refinement as the stored settings would run it: {available, reason, on, source, factor, max_cells, max_angle, smart, blocked,
    scan, measured (what the last mesh holds at the edges, see measure), h_wall, plan: {applied, reason, edges (count), total_length, h_edge, factor_used, estimated_extra_cells, capped, cap_cells} | None,
    lines: [polyline of each refined edge]}. `scan` starts the edge search when there is none (needed for the plan); `wait` waits for it."""
    m = pr.data["mesh"]
    why = unavailable(pr)
    s = settings(pr)
    out = {"available": why is None, "reason": why, **s, "smart": bool(m.get("smart")), "blocked": None, "scan": "none", "scan_error": None,
           "h_wall": None, "plan": None, "lines": [], "measured": None, "estimating": False}
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
    # the plan (with a cap that binds the estimate is computed a dozen times: 1 to 5 s) is made once per setting, on a thread of its own, and kept
    key = (_key(pr), id(sc["result"]), round(h_wall, 9), round(growth, 6), s["factor"], s["max_cells"], s["max_angle"])
    pc = _background(pr, "_edge_plan", key, lambda: gmsh_worker.edge_refine_plan(edges, h_wall, L, growth, s["factor"], s["max_cells"], s["max_angle"]), wait=wait)
    out["estimating"] = pc["state"] == "running"
    if pc["state"] == "failed":
        out["scan"], out["scan_error"] = "failed", pc["error"]
    if pc["state"] != "ready":
        return out
    plan = pc["result"]
    out["lines"] = [e[3] for e in plan["edges"]] if plan["applied"] else []
    out["plan"] = {k: v for k, v in plan.items() if k != "edges"}
    out["plan"]["edges"] = len(plan["edges"])
    mm = measure(pr, wait=wait)
    out["measured"] = mm if mm["state"] != "none" else None
    return out
