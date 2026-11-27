"""Workbench operations exposed to Claude (in-app chat via the Agent SDK, and the stdio MCP server).

Each tool: name, description, JSON schema, handler(project, args) -> str|dict.
Handlers that need no project accept project=None.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import materials, project as P, studies

NUM = {"type": "number"}
INT = {"type": "integer"}
STR = {"type": "string"}


def _round(x):
    """4 significant figures for floats: no false precision, fewer tokens."""
    if isinstance(x, float):
        return float(f"{x:.4g}") if x == x and abs(x) != float("inf") else x
    if isinstance(x, dict):
        return {k: _round(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_round(v) for v in x]
    return x


def _fmt(x):
    return x if isinstance(x, str) else json.dumps(_round(x), indent=1, default=str)


def _wait(job, timeout):
    job.wait(timeout)
    if job.status == "running":
        return {"status": "still running", "job_id": job.id, "progress": job.progress, "message": job.message}
    if job.status != "done":
        return {"status": job.status, "error": job.error, "log_tail": job.log[-15:]}
    return {"status": "done", "result": job.result}


# ---------------------------------------------------------------------------- handlers

def get_project_state(pr, a):
    return pr.summary_text()


def list_options(pr, a):
    st = pr.spec if pr else (studies.study(a["study"]) if a.get("study") else None)
    mech = st is not None and (st is studies.STUDIES["vibration_modal"] or st is studies.STUDIES["static_structural"] or st is studies.STUDIES["topology_optimization"]
                               or st is studies.STUDIES["aeroelastic_flutter"])
    out = {
        "solid_materials": {k: (f"{v['label']} (E={v['E'] / 1e9:g} GPa, density={v['rho']:g} kg/m3"
                                + (f", yield {v['Sy'] / 1e6:g} MPa)" if v.get("Sy") else ", no yield strength)")
                                if mech else f"{v['label']} (k={v['k']} W/mK)") for k, v in materials.SOLIDS.items()},
        "fluids": {k: f"{v['label']} ({v.get('kind', 'gas')}: density {v['rho']:g} kg/m3, viscosity {v['mu']:g} Pa s)"
                   for k, v in materials.FLUIDS.items()},
        "your_own_materials": {"solids": [k for k in materials.SOLIDS if materials.is_custom("solid", k)],
                               "fluids": [k for k in materials.FLUIDS if materials.is_custom("fluid", k)]},
        "studies": {k: v["label"] for k, v in studies.STUDIES.items()},
        "example_step_files": sorted(x.name for x in P.EXAMPLES.glob("*.step")),
    }
    if st:
        out["boundary_condition_types"] = {
            k: {"label": v["label"], "help": v["help"],
                "params": {p["key"]: f"{p['label']} [{p['unit']}] default {p['default']}" for p in v["params"]},
                "allowed_face_kinds": v.get("face_kinds", "any outer face")}
            for k, v in st["face_bc_types"].items()}
        out["unassigned_faces_default"] = st["default_face_bc"]["label"]
        out["settings"] = {p["key"]: {"label": p["label"], "unit": p["unit"], "default": p["default"],
                                      **({"options": p["options"]} if "options" in p else {}),
                                      **({"help": p["help"]} if "help" in p else {})} for p in st["settings"]}
    return out


def find_faces(pr, a):
    res = pr.find_faces(kind=a.get("kind"), normal=a.get("normal"), at=a.get("at"), body=a.get("body_id"),
                        plane_type=a.get("surface_type"), min_area_mm2=a.get("min_area_mm2"),
                        tol_deg=a.get("tolerance_deg", 10), part=a.get("part"))
    lim = int(a.get("limit", 60))
    return {"count": len(res), "faces": res[:lim], "truncated": len(res) > lim}


def highlight_faces(pr, a):
    faces = [int(f) for f in a["faces"]]
    P.JOBS.emit({"type": "highlight", "project": pr.id, "faces": faces})
    if REMOTE_GUI:  # MCP server process: forward to the GUI server if it is running
        try:
            import urllib.request

            req = urllib.request.Request(f"{REMOTE_GUI}/api/projects/{pr.id}/highlight",
                                         data=json.dumps({"faces": faces}).encode(), method="POST",
                                         headers={"Content-Type": "application/json", "X-SimWB": "1"})
            urllib.request.urlopen(req, timeout=3)
        except Exception:  # noqa: BLE001
            return "The workbench GUI is not running, so nothing could be highlighted."
    return f"Highlighted {len(faces)} faces in the 3D view."


REMOTE_GUI = None  # set by mcp_server


def set_body(pr, a):
    return pr.set_body(a["body_id"], material=a.get("material"), heat_W=a.get("heat_W"), name=a.get("name"))


def add_material(pr, a):
    kind = a.get("kind")
    fields = {k: v for k, v in a.items() if k != "kind"}
    if kind == "solid":
        key = materials.add_solid(**fields)
    elif kind == "fluid":
        fields["kind"] = fields.pop("phase", None) or "liquid"       # (the tool's 'kind' says solid or fluid; the fluid's own is gas or liquid)
        key = materials.add_fluid(**fields)
    else:
        raise ValueError("kind must be 'solid' or 'fluid'")
    return {"key": key, "material": (materials.SOLIDS if kind == "solid" else materials.FLUIDS)[key],
            "use_it": "set_body (material=key) for a solid; set_settings (fluid=key) for a fluid"}


def remove_material(pr, a):
    materials.remove_custom(a.get("kind"), a.get("key"))
    return f"Removed {a.get('key')} from your materials. Studies that use it keep their own copy."


def add_bc(pr, a):
    bc = pr.add_bc(a.get("name", ""), a["type"], a["faces"], a.get("params") or {})
    if a["type"] == "symmetry":  # (a plane: the geometry is rebuilt before the condition exists)
        job = P.JOBS.latest(pr.id, "prepare")
        return {"symmetry": _wait(job, 300) if job else None, "state": pr.summary_text()}
    return {k: v for k, v in bc.items() if k != "signature"}


def align_face(pr, a):
    """Turn the model so that a flat face lies on a coordinate plane (facing the given direction)."""
    job = pr.align_face(a["face"], a.get("direction"), a.get("wall"), bool(a.get("away")))
    return {"rebuild": _wait(job, 300), "state": pr.summary_text()}


def set_symmetry(pr, a):
    """Add, move, flip or remove a mirror plane; the geometry is rebuilt (the part on the other side is removed)."""
    act = a.get("action", "add")
    if act == "add":
        job = pr.add_symmetry(axis=a.get("axis"), pos_mm=a.get("position_mm"), faces=a.get("faces"),
                              keep=a.get("keep"), name=a.get("name"), cut_parts=bool(a.get("delete_beyond")))
    elif act == "update":
        job = pr.update_symmetry(a["plane"], pos_mm=a.get("position_mm"), flip=bool(a.get("flip")), name=a.get("name"),
                                 cut_parts=a.get("delete_beyond"))
    elif act == "remove":
        job = pr.remove_symmetry(a["plane"])
    else:
        raise ValueError("action must be add, update or remove")
    res = _wait(job, 300) if job else {"status": "done"}
    return {"rebuild": res, "planes": pr.symmetry_planes(), "state": pr.summary_text()}


def update_bc(pr, a):
    bc = pr.update_bc(a["bc"], name=a.get("name"), bc_type=a.get("type"), faces=a.get("faces"),
                      params=a.get("params"), add_faces=a.get("add_faces"), remove_faces=a.get("remove_faces"))
    return {k: v for k, v in bc.items() if k != "signature"}


def delete_bc(pr, a):
    pr.delete_bc(a["bc"])
    return "deleted"


def set_settings(pr, a):
    pr.set_settings(**a["settings"])
    return pr.data["settings"]


def auto_inlet_outlet(pr, a):
    out = pr.suggest_inlet_outlet(apply=True)
    inlet = next((b for b in pr.data["bcs"] if b["type"] == "velocity_inlet"), None)
    if inlet and isinstance(out, dict):
        out["inlet_params"] = inlet["params"]  # e.g. the default air speed, so it can be changed if needed
    return out


def detect_rotors(pr, a):
    r = a.get("radius_mm")
    return pr.detect_rotors(apply=True, radius=float(r) / 1000 if r else None)


def set_rotors(pr, a):
    rot = []
    for r in a["rotors"]:
        rot.append({"center": [c / 1000 for c in r["center_mm"]], "axis": r.get("axis", [0, 0, 1]),
                    "radius": r["radius_mm"] / 1000, "thickness": (r.get("thickness_mm") or 0) / 1000 or None,
                    "thrust_N": r.get("thrust_N")})
    return pr.set_rotors(rot)


def set_domain(pr, a):
    kw = {k: v for k, v in a.items() if v is not None}
    job = pr.set_domain(**kw)
    return _wait(job, 300) if job else {"saved": True, "note": "Domain options saved; they apply when a STEP is imported."}


def set_mesh(pr, a):
    pr.set_mesh(preset=a.get("preset"), size_max_mm=a.get("size_max_mm"), size_min_mm=a.get("size_min_mm"),
                refinements=a.get("refinements"), method=a.get("method"), layers=a.get("layers"),
                edge_refinement=a.get("edge_refinement"), smart=a.get("smart"))
    return pr.data["mesh"]


def mesh_advice(pr, a):
    return pr.mesh_advice()


def mesh_estimate(pr, a):
    return pr.mesh_estimate()


def set_memory_budget(pr, a):
    b = a.get("budget")
    if b is None:
        b = {k: a[k] for k in ("mb", "percent", "on") if k in a}
    if not b:
        raise ValueError("Give mb (MB), percent (of the free memory) or on: false")
    pr.set_mesh(budget=b)
    return pr.mesh_estimate()


def mesh_apply_recipe(pr, a):
    return pr.apply_mesh_recipe(preset=a.get("preset"), layers=a.get("layers", True))


def mesh_metrics(pr, a):
    return pr.mesh_metrics()


def generate_mesh(pr, a):
    r = _wait(pr.generate_mesh(), float(a.get("timeout_s", 600)))
    if r.get("status") == "done":
        s = r["result"]
        return {"status": "done", "cells": s["cells"], "nodes": s["nodes"], "quality": {
            k: s["quality"][k] for k in ("min", "mean", "n_poor")}, "sizes_mm": {k: round(v * 1000, 3) for k, v in s["sizes"].items()
                                                if isinstance(v, (int, float)) and k != "cells_across_gap"},
            "cells_across_narrowest_gap": s["sizes"].get("cells_across_gap"),
            "openfoam_quality": s.get("foam_quality"), "seconds": s["seconds"]}
    return r


def check_setup(pr, a):
    return [c for c in pr.check()]


def run_simulation(pr, a):
    job = pr.solve(export_only=bool(a.get("export_only")), continue_iterations=a.get("continue_iterations"),
                   init_from=a.get("init_from") or None)
    if pr.spec["solver"] == "builtin_fem":
        return _wait(job, 600)
    job.wait(20)
    return {"status": job.status, "job_id": job.id, "message": job.message, "error": job.error,
            "note": "OpenFOAM runs can take minutes to hours; call wait_for_run to wait for it."}


def get_run_status(pr, a):
    j = P.JOBS.latest(pr.id, "solve")
    if not j:
        return "No solver run yet."
    d = j.to_dict(log_tail=int(a.get("log_lines", 10)))
    res = d.get("extra", {}).get("residuals")
    if res:
        d["extra"]["residuals"] = {k: v[-1] for k, v in res["residuals"].items() if v}
        d["extra"]["iteration"] = res["iters"][-1] if res["iters"] else 0
    return d


def wait_for_run(pr, a):
    j = P.JOBS.latest(pr.id)
    if not j:
        return "No job has been started for this project."
    j.wait(min(float(a.get("timeout_s", 300)), 600))
    return get_run_status(pr, {"log_lines": 5}) if j.kind == "solve" else j.to_dict(log_tail=5)


def mesh_check(pr, a):
    j = pr.mesh_check(levels=int(a.get("levels") or 1))
    j.wait(float(a.get("timeout_s", 600)))
    if j.status == "running":
        return {"status": "running", "note": "Mesh check still running; call wait_for_run."}
    return j.result if j.status == "done" else {"status": j.status, "error": j.error}


def model_check(pr, a):
    j = pr.model_check()
    j.wait(float(a.get("timeout_s", 600)))
    if j.status == "running":
        return {"status": "running", "note": "Flow-model comparison still running; call wait_for_run."}
    return j.result if j.status == "done" else {"status": j.status, "error": j.error}


def cancel_job(pr, a):
    j = P.JOBS.latest(pr.id)
    if not j or j.status != "running":
        return "Nothing is running."
    if a.get("keep_results"):
        pr.stop_solve()
        return "Stop requested: the solver writes the current solution and the results are loaded (not converged)."
    P.JOBS.cancel(j.id)
    return f"Cancel requested for the {j.kind} job."


def apply_trim(pr, a):
    r = pr.apply_trim()
    return (f"Pitch set to {r['pitch_deg']}°, rotor thrusts {r['rotor_thrust_N']} N (trim estimate of the last run). "
            "Call run_simulation to solve at the trimmed attitude.")


def rename_project(pr, a):
    pr.rename(a.get("name"))
    return f"Project renamed to {pr.data['name']}"


def probe_results(pr, a):
    """Field statistics per part (volume) and per boundary group (surface)."""
    import numpy as np

    rs = pr.results()
    stale = pr.data["solve"].get("status") == "stale"
    field = a.get("field") or rs.field_info()[0]["name"]
    if field not in rs.fields:
        return f"ERROR: unknown field '{field}'. Fields: {list(rs.fields)}"
    loc, arr, unit = rs.fields[field]
    v = rs._scalar(arr)
    out = {"field": field, "unit": unit, "parts": {}, "boundaries": {}}
    if stale:
        out["warning"] = "The setup changed after this result was computed - re-run before relying on it."
    for i, name in enumerate(rs.regions):
        cells = np.where(rs.tet_region == i)[0]
        vals = v[np.unique(rs.tets[cells])] if loc == "node" else v[cells]
        vals = vals[np.isfinite(vals)]
        if len(vals):
            out["parts"][name] = {"min": float(vals.min()), "max": float(vals.max()), "mean": float(vals.mean())}
    sv = rs.surf_values.get(field)
    for gi, g in enumerate(rs.groups):
        m = rs.surf_group == gi
        if not m.any():
            continue
        vals = sv[m] if sv is not None else (v[rs.surf_tris[m]].mean(1) if loc == "node" else v[rs.surf_cell[m]])
        vals = vals[np.isfinite(vals)]
        if len(vals):
            out["boundaries"][g] = {"min": float(vals.min()), "max": float(vals.max()), "mean": float(vals.mean())}
    return out


def get_results(pr, a):
    s = pr.data["solve"]
    if s.get("status") not in ("done", "stale"):
        return f"No results available (solve status: {s.get('status')})."
    out = {"summary": s.get("summary")}
    for k in ("mesh_check", "model_check"):  # finished checks, also when the tool call that started them timed out
        if pr.data.get(k):
            out[k] = pr.data[k]
    if s.get("status") == "stale":
        out["warning"] = "The setup or mesh changed after this result was computed - re-run before relying on it."
    try:
        out["fields"] = pr.results().field_info()
    except Exception as e:  # noqa: BLE001
        out["fields_error"] = str(e)
    return out


def openfoam_status(pr, a):
    from .solvers.openfoam import runner

    return runner.detect(refresh=True)


def install_openfoam(pr, a):
    from .solvers.openfoam import installer

    job = installer.start(P.JOBS)
    timeout = min(float(a.get("timeout_s") or 600), 600)
    job.wait(timeout)
    out = {"status": job.status, "job_id": job.id, "message": job.message, "error": job.error}
    if job.status == "running":
        out["note"] = "Still installing; call install_openfoam again to keep waiting (it will not start twice)."
    return out


# MCP-only (outside the GUI the client must pick the project)
def list_projects(pr, a):
    return P.list_projects()


def create_project(pr, a):
    step = a.get("step_path")
    if a.get("example"):
        step = P.EXAMPLES / Path(a["example"]).name
        if not step.exists():  # "heatsink" -> heatsink_duct.step when the name is unambiguous
            hits = [x for x in P.EXAMPLES.glob("*.step") if Path(a["example"]).stem.lower() in x.stem.lower()]
            step = hits[0] if len(hits) == 1 else step
        if not step.exists():
            raise ValueError(f"Unknown example '{a['example']}'. Examples: "
                             + ", ".join(sorted(x.name for x in P.EXAMPLES.glob("*.step"))))
    if not str(a.get("location") or "").strip():
        raise ValueError("Ask the user where to save this study (the full path of a folder, for example "
                         "C:\\Simulations) and call create_project again with it as location. Never pick a folder "
                         "yourself: every study is saved where the user chooses.")
    p = P.create_project(a["name"], a["study"], str(a["location"]).strip())
    if step:
        r = _wait(p.import_step(step, original_name=Path(step).name), 300)
        return {"project_id": p.id, "import": r.get("status"), "state": p.summary_text()}
    return {"project_id": p.id}


def import_step(pr, a):
    r = _wait(pr.import_step(a["step_path"], scale=float(a.get("scale", 1.0))), 300)
    return {"import": r.get("status"), "error": r.get("error"), "state": pr.summary_text()}


def edit_geometry(pr, a):
    """CAD edits of the model: add / update / remove an operation, list them, clear them (the geometry is rebuilt)."""
    act = a.get("action", "add")
    if act == "list":
        return {"edits": [{"number": e["index"], "edit": e["edit"]} for e in pr.cad_edits()],
                "bodies": [b["name"] for b in pr.parts()]}
    edit = a.get("edit")
    if act in ("add", "update") and not isinstance(edit, dict):
        raise ValueError("'edit' must be an object such as {\"op\": \"fillet\", \"body\": \"Plate\", \"radius_mm\": 2, "
                         "\"edges\": {\"parallel\": \"x\"}}")
    r = _wait(pr.edit_geometry(act, op=edit, index=a.get("number")), 600)
    if r.get("status") != "done":
        return {"geometry": r, "note": "The geometry is as it was before this edit.", "edits": [e["text"] for e in pr.cad_edits()]}
    info = (pr.state()["geometry"].get("info") or {})
    return {"geometry": "rebuilt", "report": info.get("edit_report", []), "state": pr.summary_text(),
            "notes": [n for n in pr.data.get("notes", [])[-3:] if "CAD edit" in n]}


def find_edges(pr, a):
    return pr.find_edges(body=a.get("body"), near=a.get("near_mm"), radius_mm=a.get("radius_mm"),
                         parallel=a.get("parallel"), length_mm=a.get("length_mm"), edge_type=a.get("type"),
                         in_box=a.get("in_box_mm"), limit=int(a.get("limit", 60)))


# ---------------------------------------------------------------------------- registry

FACE_LIST = {"type": "array", "items": INT, "description": "Face ids (from find_faces / get_project_state)"}

TOOLS = [
    ("get_project_state", "Summary of the current project: study, bodies (ids, sizes, materials), boundary "
                          "conditions, settings, mesh and solve status, open issues. Call this first.",
     {"type": "object", "properties": {}}, get_project_state),
    ("list_options", "Available materials, studies and examples; with a project (or a study key) also the boundary "
                     "condition types (with parameters and units) and the study settings.",
     {"type": "object", "properties": {"study": STR}}, list_options),
    ("reset_rotor_thrusts", "Drone: set all rotor thrusts back to automatic (they carry the weight at the set pitch).",
     {"type": "object", "properties": {}}, lambda pr, a: (pr.reset_thrusts(), "Rotor thrusts are automatic again.")[1]),
    ("find_faces", "Find CAD faces by geometry. All filters optional and combined. Coordinates are in mm. "
                   "kind: 'body'|'farfield' (aero), 'enclosure'|'solid_outer'|'interface' (cht), "
                   "'boundary'|'interface' (conduction). normal: '+x','-z' or [x,y,z]. at: 'max_x','min_z' = faces "
                   "lying flat at the extreme coordinate of the filtered set. body_id: faces touching that body.",
     {"type": "object", "properties": {
         "kind": STR, "normal": {"description": "'+x' style axis or [x,y,z]"}, "at": STR, "body_id": INT,
         "part": {"type": "string", "description": "STEP part name (drone study), e.g. 'Arm 2'"},
         "surface_type": {"type": "string", "description": "Plane, Cylinder, Cone, Sphere, BSplineSurface..."},
         "min_area_mm2": NUM, "tolerance_deg": NUM, "limit": INT}}, find_faces),
    ("find_edges", "Find the EDGES of the model (not the air volume) by geometry, to choose what a fillet or chamfer goes "
                   "on or to read dimensions. Coordinates mm. Filters (all optional, combined): body (name), near_mm "
                   "[x,y,z] (+ radius_mm; sorted by distance of the edge's middle), parallel ('x'|'y'|'z' or a vector; "
                   "straight edges), length_mm [min, max], type (line|circle|ellipse|spline), in_box_mm [x0,y0,z0,x1,y1,z1]. "
                   "Each edge: type, center_mm, length_mm, direction, bodies, faces. A circle's diameter is its length / pi.",
     {"type": "object", "properties": {"body": STR, "near_mm": {"type": "array", "items": NUM}, "radius_mm": NUM,
                                       "parallel": {"description": "'x'|'y'|'z' or [dx,dy,dz]"},
                                       "length_mm": {"type": "array", "items": NUM}, "type": STR,
                                       "in_box_mm": {"type": "array", "items": NUM}, "limit": INT}}, find_edges),
    ("edit_geometry", "CAD editing of the imported model: add, change or remove operations, which are replayed on the "
                      "imported file every time the geometry is built (non-destructive: the file is never changed, any "
                      "edit can be removed or changed again, 'list' shows them). action: add (default; 'edit' = the "
                      "operation, optional 'number' = position), update ('number' + 'edit' with the parameters to "
                      "change), remove ('number'; undo the last one = its number), clear, list. The geometry is "
                      "rebuilt, the mesh and results become stale; boundary conditions on faces that survive follow them. "
                      "Everything is in mm, degrees, in the frame of the 3D view; bodies are referred to by NAME "
                      "(get_project_state), never by id. 'edit' = {\"op\": ..., params}. Operations:\n"
                      "NEW BODIES: box {name, min:[x,y,z], size:[dx,dy,dz]}; cylinder {name, base:[x,y,z], axis:[dx,dy,dz] "
                      "(its length is the height), radius_mm}; sphere {name, center, radius_mm}; cone {name, base, axis, "
                      "radius1_mm, radius2_mm}; prism {name, points:[[x,y,z],...] (a flat polygon, 3+ points), direction:"
                      "[dx,dy,dz] (pushed along it: plates, ribs, wing planforms)}; revolve {name, points (flat profile in "
                      "a plane that contains the axis), axis_point, axis_direction, angle_deg=360}; loft {name, sections:"
                      "[[[x,y,z],...], ...] (closed cross-sections, 2+, the same number of points going round the same way, "
                      "e.g. airfoils along a wing), smooth: true (splines through the points), ruled: true (straight "
                      "between sections)}.\n"
                      "BOOLEAN: cut {target, tools:[names]} (tools are consumed), union {bodies:[names], name?} (they must "
                      "touch or overlap), intersect {target, tools}.\n"
                      "MOVE / COPY (bodies omitted = all): move {bodies?, by:[dx,dy,dz]}; rotate {bodies?, point, axis, "
                      "angle_deg}; scale {bodies?, factor (or [sx,sy,sz]), about?}; mirror {bodies?, point, normal, copy: "
                      "true} (a copy is named '<name> (mirror)'); pattern_linear {body, count, step:[dx,dy,dz], union?}; "
                      "pattern_polar {body, count, point, axis, angle_deg=360, union?} (copies are '<name> 2', '<name> 3'...).\n"
                      "FEATURES: fillet {body, radius_mm, edges: SELECTOR}; chamfer {body, distance_mm, edges: SELECTOR}; "
                      "hole {body, at:[x,y,z] (a point on the face where it starts), direction:[dx,dy,dz] (into the "
                      "material), diameter_mm, depth_mm? (omitted = through)}.\n"
                      "BODIES: delete {bodies}; rename {body, name}; cut_plane {bodies?, point, normal, keep: '+'|'-'} "
                      "(keeps the side the normal points to).\n"
                      "SELECTOR for edges: {all: true} or any of near:[[x,y,z],...] (the edge nearest each point), "
                      "of_face_near:[[x,y,z],...] (all edges of the face nearest each point), parallel:'x'|'y'|'z'|[v], "
                      "length_mm:[min,max], type:'line'|'circle', in_box:[x0,y0,z0,x1,y1,z1] (edges whose middle is in the "
                      "box); near / of_face_near choose, the rest filter. Get coordinates from find_edges / find_faces. "
                      "Too large a fillet radius or chamfer distance is refused: try smaller. After an edit read the "
                      "returned bodies (sizes, volume) and check them against what the user asked for. Faces get new ids: "
                      "use find_faces again before assigning boundary conditions. STEP only (not STL/OBJ).",
     {"type": "object", "properties": {"action": STR, "edit": {"type": "object"}, "number": INT}}, edit_geometry),
    ("highlight_faces", "Highlight faces in the user's 3D view (to show them what you mean).",
     {"type": "object", "properties": {"faces": FACE_LIST}, "required": ["faces"]}, highlight_faces),
    ("set_body", "Set material (id from list_options), heat generated [W] and/or display name of a solid body.",
     {"type": "object", "properties": {"body_id": INT, "material": STR, "heat_W": NUM, "name": STR},
      "required": ["body_id"]}, set_body),
    ("add_material", "Add your own material to the library (saved; every study can then use it). kind 'solid': label, k [W/m/K], rho [kg/m3], "
                     "cp [J/kg/K], E [Pa], nu, Sy [Pa, optional]. kind 'fluid': label, phase 'gas' or 'liquid', rho [kg/m3], mu [Pa s], cp [J/kg/K], k [W/m/K], Pr, beta [1/K] (optional), M [kg/kmol, gases]. "
                     "Returns the key to use with set_body / set_settings.",
     {"type": "object", "properties": {"kind": STR, "label": STR, "phase": STR, "k": NUM, "rho": NUM, "cp": NUM, "E": NUM, "nu": NUM,
                                       "Sy": NUM, "mu": NUM, "Pr": NUM, "beta": NUM, "M": NUM}, "required": ["kind", "label"]}, add_material),
    ("remove_material", "Remove one of the materials you added (built-in ones cannot be removed). kind 'solid' or 'fluid', key from list_options.",
     {"type": "object", "properties": {"kind": STR, "key": STR}, "required": ["kind", "key"]}, remove_material),
    ("add_boundary_condition", "Create a boundary condition on faces. A face can only be in one boundary "
                               "condition (it is moved). params use the keys/units from list_options; "
                               "temperatures in degC.",
     {"type": "object", "properties": {"name": STR, "type": STR, "faces": FACE_LIST, "params": {"type": "object"}},
      "required": ["type", "faces"]}, add_bc),
    ("update_boundary_condition", "Change a boundary condition (by id or name): params, type, name, or faces "
                                  "(replace with 'faces', or use add_faces/remove_faces).",
     {"type": "object", "properties": {"bc": STR, "name": STR, "type": STR, "faces": FACE_LIST,
                                       "add_faces": FACE_LIST, "remove_faces": FACE_LIST,
                                       "params": {"type": "object"}}, "required": ["bc"]}, update_bc),
    ("align_face", "Turn the whole model (about its middle) so that a flat face lies on a plane: either a coordinate "
                   "plane (give 'direction': '+x', '-x', '+y', '-y', '+z' or '-z': the face then faces that way, e.g. "
                   "the underside of a part to '-z') or a wall of the tunnel (give 'wall': the id of an air-volume "
                   "face, e.g. the floor; the face then faces the wall, or away from it with 'away': true). Use "
                   "find_faces to locate the faces. Rebuilds the geometry; boundary conditions, rotors and zones turn "
                   "with it.",
     {"type": "object", "properties": {"face": INT, "direction": STR, "wall": INT, "away": {"type": "boolean"}},
      "required": ["face"]}, align_face),
    ("set_symmetry", "Mirror-symmetric model: cut it at a symmetry plane so only one side is meshed and solved "
                     "(half the cells), the results show the mirror image again. action 'add': axis x|y|z with "
                     "position_mm (default the middle of the parts) and keep '+'|'-' (the side of the axis that "
                     "stays), or faces: flat faces of one plane (the plane goes through them). 'update': plane "
                     "(id or name) with position_mm and/or flip true (keep the other side). 'remove': plane. Up to "
                     "three mutually perpendicular planes. In flow studies the plane trims the air volume only; the parts stay whole "
                     "unless delete_beyond is true (then the part of the model beyond the plane is deleted too). "
                     "Requirements: the shape AND the loads/flow must be symmetric; flow must run along the plane "
                     "(no sideslip); rotors/rotating zones must not cross it; loads you enter act on the kept part "
                     "only (enter half of a total shared by two halves). Forces/moments/heat flows in the results "
                     "are those of the whole body.",
     {"type": "object", "properties": {"action": STR, "axis": STR, "position_mm": NUM, "keep": STR, "faces": FACE_LIST,
                                       "plane": STR, "flip": {"type": "boolean"}, "name": STR,
                                       "delete_beyond": {"type": "boolean"}}}, set_symmetry),
    ("delete_boundary_condition", "Delete a boundary condition by id or name (a symmetry condition removes its plane).",
     {"type": "object", "properties": {"bc": STR}, "required": ["bc"]}, delete_bc),
    ("set_settings", "Change study settings (keys from list_options), e.g. {\"speed\": 12, \"pitch_deg\": 8}.",
     {"type": "object", "properties": {"settings": {"type": "object"}}, "required": ["settings"]}, set_settings),
    ("auto_inlet_outlet", "Heatsink/enclosure study: mark the two end faces of the air volume along its longest "
                          "axis as inlet (low end) and outlet (high end).",
     {"type": "object", "properties": {}}, auto_inlet_outlet),
    ("detect_rotors", "Drone study: place actuator-disk rotors on the motor tops automatically.",
     {"type": "object", "properties": {"radius_mm": NUM}}, detect_rotors),
    ("set_rotors", "Drone study: define rotors explicitly (replaces existing). thrust_N omitted = weight/n.",
     {"type": "object", "properties": {"rotors": {"type": "array", "items": {"type": "object", "properties": {
         "center_mm": {"type": "array", "items": NUM}, "axis": {"type": "array", "items": NUM},
         "radius_mm": NUM, "thickness_mm": NUM, "thrust_N": NUM}, "required": ["center_mm", "radius_mm"]}}},
      "required": ["rotors"]}, set_rotors),
    ("set_domain", "Rebuild the computational domain. Heatsink study: enclosure_mode 'body' (a STEP body is the "
                   "air) with enclosure_body = index of that body in the STEP, or 'auto_box' with padding "
                   "[-x,+x,-y,+y,-z,+z] as fractions of part size. Drone: farfield {upstream, downstream, side} "
                   "in body lengths. Air-volume shape (automatic air volume): shape box | c_domain (half cylinder "
                   "upstream = curved inlet, box downstream) | cylinder (along the flow); size in metres - box "
                   "{min:[x,y,z], max:[x,y,z]}, c_domain {radius, downstream, span_min, span_max}, cylinder "
                   "{radius, upstream, downstream} (radius/upstream/downstream from the parts' centre); fit: true "
                   "= automatic size. flow_axis (+x..-z, cooling study), span_axis (x|y|z, c_domain). "
                   "Re-maps existing boundary conditions.",
     {"type": "object", "properties": {"enclosure_mode": STR, "enclosure_body": INT,
                                       "padding": {"type": "array", "items": NUM},
                                       "farfield": {"type": "object"}, "shape": STR, "size": {"type": "object"},
                                       "fit": {"type": "boolean"}, "flow_axis": STR, "span_axis": STR}}, set_domain),
    ("set_mesh_settings", "Mesh settings: preset quick|coarse|medium|fine|very_fine, optional size_max_mm (bulk), "
                          "size_min_mm (near walls), refinements [{faces:[..], size_mm}]. method 'tet' (Gmsh "
                          "tetrahedra, the default) or 'hex' (hex-dominant snappyHexMesh: external aerodynamics round "
                          "bodies in the automatic box, no rotors, moving zones or symmetry planes; usually fewer, "
                          "more orthogonal cells; needs OpenFOAM, takes about a minute per 100,000 cells). "
                          "edge_refinement: true (or a factor 2-20, default 6; false = off) refines the mesh along "
                          "sharp edges across the flow (leading / trailing edges, which carry much of the friction "
                          "of a thin body) with a capped number of extra cells (see mesh.stats.edge_refine). layers: "
                          "smart: true/false switches smart sizing (see mesh_apply_recipe). {n, growth, first_mm, y_plus, tolerant} wall layers (flow studies); y_plus = the target of the "
                          "first cell (empty = automatic, about 30 for the wall-function models; about 1 resolves the "
                          "viscous sublayer and needs many more layers); tolerant = looser limits so thin or sharp "
                          "parts keep their layers. A tetrahedral mesh gets its layers when the mesh is generated "
                          "(OpenFOAM installed): mesh.stats.layers says how many were made on each surface, their "
                          "thickness and the share of the wall that got them; GET /mesh/view?metric=layers colours "
                          "that mesh by layer.",
     {"type": "object", "properties": {"preset": STR, "size_max_mm": NUM, "size_min_mm": NUM, "method": STR,
                                       "edge_refinement": {"description": "true | false | factor 2-20"},
                                       "layers": {"type": "object"}, "smart": {"type": "boolean"},
                                       "refinements": {"type": "array", "items": {"type": "object"}}}},
     set_mesh),
    ("mesh_advice", "External aerodynamics: what a good mesh of this body in this flow needs, and how the current mesh measures up. Returns the analysis "
                    "(length along the flow, span, thinnest part, Reynolds number, domain size in body lengths), the recipe (wall, near-field and wake cell sizes, growth, wall "
                    "layers that fit the thinnest part, with the reasons) and, once a mesh exists, a scorecard (score 0 to 1 and checks: wall cells, cells across the thin part, "
                    "near field, wake, gradation, domain size, layers and their y+, non-orthogonality, skewness) with advice for each check that is not ok. Use it before meshing "
                    "to choose settings and after to judge the result.",
     {"type": "object", "properties": {}}, mesh_advice),
    ("mesh_apply_recipe", "External aerodynamics: switch smart sizing on and set the wall layers the recipe recommends (preset optional: quick, coarse, medium, fine, "
                          "very_fine; layers: false to leave the layers alone). Smart sizing sizes the mesh from the length along the flow, refines a near-field box and the wake "
                          "with a growth of 1.2 to 1.3 and keeps the cell count to what the free memory holds. The mesh is out of date afterwards: call generate_mesh, then "
                          "mesh_advice to see the score.",
     {"type": "object", "properties": {"preset": STR, "layers": {"type": "boolean"}}}, mesh_apply_recipe),
    ("mesh_estimate", "Before meshing: how many cells and how much memory (MB) the tetrahedral mesh will need for the current mesh settings, worked out from the size fields "
                      "(no mesh is made, a second or two). Returns estimate {cells, cells_low, cells_high, mesh_mb (the meshing job: about 830 MB fixed plus a share per cell), "
                      "run_mb (the simulation run, flow studies), peak_mb and its range, the sizes}, free_mb, and - when a memory budget is on - budget {mb, status "
                      "fits|coarsened|refused, message, changes} and applied (the estimate of the mesh the budget makes). On average within 20 % of the real cell count (the range allows 50 %), "
                      "see docs/MESHING_GUIDE.md. The hex-dominant mesh is not estimated.",
     {"type": "object", "properties": {}}, mesh_estimate),
    ("set_memory_budget", "Set the memory budget of the mesh: mb (a number of MB) or percent (of the free memory now, 5 to 95), or on: false to switch it off. With a budget on, "
                          "generate_mesh coarsens the bulk (the far field, the cells away from the body, the growth) until the estimate fits and keeps the cells at the walls, "
                          "thin parts, part sizings, sharp edges and your own regions; when even that does not fit, generate_mesh refuses with a message that says what the "
                          "smallest mesh is and what to do. Returns the mesh_estimate. Default: off.",
     {"type": "object", "properties": {"mb": NUM, "percent": NUM, "on": {"type": "boolean"}}}, set_memory_budget),
    ("mesh_metrics", "Inspect the generated tetrahedral mesh: statistics and histograms of cell size, aspect ratio, "
                     "growth (size jump to the neighbours), volume and quality, the cell budget (for each size band "
                     "the share of cells and of the volume: where the cells - and the memory - are spent) and plain "
                     "findings (abrupt size jumps, slivers, too many fine cells). Use it after generate_mesh to "
                     "judge whether a mesh is accurate for its cell count. In the viewer the same metrics colour the "
                     "mesh: GET /mesh/view?metric=size|aspect|growth|volume|quality|region|layers.",
     {"type": "object", "properties": {}}, mesh_metrics),
    ("generate_mesh", "Generate the mesh (waits until done). Returns cell count and quality.",
     {"type": "object", "properties": {"timeout_s": NUM}}, generate_mesh),
    ("check_setup", "Readiness checklist (errors must be fixed before solving).",
     {"type": "object", "properties": {}}, check_setup),
    ("run_simulation", "Run the solver. Built-in conduction waits and returns results; OpenFOAM studies start a "
                       "background run (then use wait_for_run). export_only=true writes the OpenFOAM case without "
                       "running it. continue_iterations=N continues the previous OpenFOAM run for N more iterations. For an interrupted transient run (cancelled or the workbench stopped) any continue_iterations resumes it from its last saved time to its end time. "
                       "init_from=<study id> starts from the latest solution of another study of the same "
                       "geometry (e.g. a transient sliding-mesh run from a converged steady MRF run).",
     {"type": "object", "properties": {"export_only": {"type": "boolean"}, "continue_iterations": INT,
                                       "init_from": STR}},
     run_simulation),
    ("get_run_status", "Progress of the latest solver run (iteration, residuals, log tail).",
     {"type": "object", "properties": {"log_lines": INT}}, get_run_status),
    ("get_results", "Engineering summary of the latest results (temperatures, forces, pressure drop, convergence).",
     {"type": "object", "properties": {}}, get_results),
    ("probe_results", "Min/max/mean of a result field for every part and every boundary group (e.g. which part "
                      "is hottest, temperature of the outlet). Fields: Temperature, Velocity, Pressure, Heat flux, y+.",
     {"type": "object", "properties": {"field": STR}}, probe_results),
    ("wait_for_run", "Block until the current job (mesh or solver run) finishes or timeout_s (max 600) passes; "
                     "returns progress/residuals. Use instead of polling.",
     {"type": "object", "properties": {"timeout_s": NUM}}, wait_for_run),
    ("mesh_check", "Mesh-sensitivity check: re-mesh one preset finer (in a copy of the project), re-solve and "
                   "compare the key outputs. Use before trusting numbers for design decisions.",
     {"type": "object", "properties": {"timeout_s": NUM, "levels": {"type": "integer", "enum": [1, 2],
                                        "description": "2 = two finer meshes and a grid-convergence index (GCI)"}}},
     mesh_check),
    ("model_check", "Heatsink: solve the same mesh with the other flow model (laminar <-> k-omega SST) in a copy and "
                    "compare thermal resistance and pressure drop - brackets the answer in the transitional range.",
     {"type": "object", "properties": {"timeout_s": NUM}}, model_check),
    ("cancel_job", "Cancel the running mesh/solver job of this project. keep_results=true stops an OpenFOAM run "
     "gracefully: it writes the current solution and loads it as (unconverged) results.",
     {"type": "object", "properties": {"keep_results": {"type": "boolean"}}}, cancel_job),
    ("apply_trim", "Drone: set the pitch and rotor thrusts to the level-flight trim estimate of the last run "
     "(runs re-trim automatically once when the setting auto_trim is on).", {"type": "object", "properties": {}},
     apply_trim),
    ("rename_project", "Rename the project.", {"type": "object", "properties": {"name": STR}, "required": ["name"]},
     rename_project),
    ("openfoam_status", "Check whether OpenFOAM is installed/available and how to install it.",
     {"type": "object", "properties": {}}, openfoam_status),
    ("install_openfoam", "Windows only: set up (or wait for) the built-in OpenFOAM in the workbench folder. The "
                         "workbench normally does this by itself on the first start. "
                         "Waits up to timeout_s (max 600); calling again while it runs just keeps waiting.",
     {"type": "object", "properties": {"timeout_s": NUM}}, install_openfoam),
]

# ---------------------------------------------------------------------------------------------------- the desktop window
# Tools to see and drive the user interface of the running desktop application (simwb.qt): what it shows, every operation of
# it, every control by id, pictures of it, its log. They work only while that window is running (else: a clear error).
def _ui(op, args=None):
    from . import ui_bridge

    try:
        return ui_bridge.call(op, args or {})
    except ui_bridge.NoWindow as e:
        raise ValueError(str(e)) from None


def ui_state(pr, a):
    return _ui("state")


def ui_actions(pr, a):
    return _ui("actions")


# Operations that name a file or a folder. Through these tools the caller is a language model, which text inside a study (a part
# name, an imported file, a message) may have steered, so: anything that WRITES (or opens a folder the application then writes
# into) stays inside the workspace or the system temp folder, in the kinds of file the workbench makes; anything that only READS
# a file must be of the kind it expects. Deny by default: an action not listed here that takes a path-like argument is refused,
# so a new action cannot slip through. (A person at the menus, and the REST window control, are not limited.)
_CAD = {".step", ".stp", ".stl", ".obj"}
_PATHS = {  # tool or action: {argument: (where, allowed suffixes or None)}; where: True = inside the workspace or temp, "local" = a place on
            # a local drive the USER chose (a new study's folder), False = an existing file to read
    "view.picture": {"path": (True, {".png"})},
    "study.export": {"path": (True, {".zip"})},
    "solve.case_zip": {"path": (True, {".zip"})},
    "results.export": {"path": (True, {".csv", ".vtk", ".vtu", ".html", ".pdf", ".md", ".txt", ".json"})},
    "results.video": {"path": (True, {".mp4"})},
    "study.new": {"location": (True, None)},
    "study.save_as": {"location": (True, None)},
    "study.import": {"location": (True, None), "path": (False, {".zip"})},
    "study.open_folder": {"path": (True, None)},                  # (the study then writes into that folder)
    "geometry.import": {"path": (False, _CAD)},
    "create_project": {"location": ("local", None), "step_path": (False, _CAD)},       # (MCP tools: the model names them for the user)
    "import_step": {"step_path": (False, _CAD)},
}
_PATH_KEYS = {"path", "paths", "location", "folder", "file", "files", "dir", "directory", "target", "dest", "destination", "out",
              "step_path", "filename", "output", "output_path", "input", "input_path", "src", "url", "uri"}
_RESERVED_NAMES = {"project.json", "locations.json", "control.json", ".mcp.json", "claude.md", "claude.local.md"}
_RESERVED_DIRS = {".claude", ".ui", ".git"}
_SENSITIVE_DIRS = {".ssh", ".aws", ".azure", ".gnupg", ".kube", ".docker"}       # (credentials: nothing of the workbench goes there)


def _refuse_reserved(action: str, target: Path, folder: bool = False) -> None:
    """The workbench's own files (a study's project.json, the list of studies, the window's token, an agent's settings) are never
    written by a path that comes through the assistant: an export could otherwise replace one of them."""
    low = [x.lower() for x in target.parts]
    if low and low[-1] in _RESERVED_NAMES or any(x in _RESERVED_DIRS for x in low):
        raise ValueError(f"{action}: '{target.name}' is one of the workbench's own files; choose another name")
    if folder and (any(x in _SENSITIVE_DIRS for x in low) or ("start menu" in low and "startup" in low)):
        raise ValueError(f"{action}: a study is not kept in a settings, credentials or start-up folder ('{target}'); choose another folder")


_DEVICE_NAMES = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def _refuse_links(action: str, lexical: str) -> None:
    """No part of the path may be a symbolic link or a junction (which can lead to a network share or anywhere else): each part is
    looked at WITHOUT following it. (Cloud-sync placeholders, also reparse points, are fine.) Parts that do not exist yet end the walk."""
    import os
    import stat

    symlink_or_junction = {0xA000000C, 0xA0000003}               # IO_REPARSE_TAG_SYMLINK, IO_REPARSE_TAG_MOUNT_POINT
    parts = Path(lexical).parts
    for i in range(1, len(parts) + 1):
        here = os.path.join(*parts[:i]) if i > 1 else parts[0]
        try:
            st = os.lstat(here)
        except FileNotFoundError:
            return
        except OSError as e:
            raise ValueError(f"{action}: cannot look at '{here}' ({e.strerror or e})") from None
        if stat.S_ISLNK(st.st_mode) or getattr(st, "st_reparse_tag", 0) in symlink_or_junction:
            raise ValueError(f"{action}: '{here}' is a link; links are not followed in a path that comes through the assistant")


def _local_path(action: str, raw) -> Path:
    """The path as a local file or folder, checked WITHOUT touching the disk until it is known to be local: it must be a full path
    that begins with a drive letter (so not a UNC path, a device or NT object path, a path of the current drive, a relative or
    '~' path), without alternate data streams or device names, on a drive that is not a network drive. It is never resolved: links
    are not followed but refused (see _refuse_links), because following one would itself reach the host a link points to. The caller
    uses the returned path, not the text it was given, so what was checked is what is opened."""
    import os
    import re
    import sys

    text = str(raw)
    posix = sys.platform != "win32"
    if "\0" in text or not (re.match(r"^[A-Za-z]:[\\/]", text) if not posix else text.startswith("/")):
        raise ValueError(f"{action}: the path must be a full local path starting with a drive letter (not a network, device or "
                         f"relative path) when it comes through the assistant")
    lexical = os.path.normpath(text)                              # (text only: no file access yet)
    tail = lexical[2:] if not posix else lexical
    if ":" in tail:
        raise ValueError(f"{action}: a path with an alternate data stream is not allowed through the assistant")
    for part in (x for x in re.split(r"[\\/]", tail) if x):
        if part != part.rstrip(" ."):                              # (Windows drops the trailing dot or space: 'project.json.' is project.json)
            raise ValueError(f"{action}: a name ending in a dot or a space is not allowed through the assistant")
        if re.search(r"~[0-9]", part):                             # (an 8.3 short name, such as PROJEC~1.JSO, is another name of a file)
            raise ValueError(f"{action}: a short (8.3) file name is not allowed through the assistant; give the full name")
    if any(P.is_device_name(part) for part in re.split(r"[\\/]", tail) if part):
        raise ValueError(f"{action}: a device name in the path is not allowed through the assistant")
    if not posix:
        import ctypes

        if ctypes.windll.kernel32.GetDriveTypeW(lexical[:3]) == 4:   # DRIVE_REMOTE: a mapped network drive
            raise ValueError(f"{action}: a file on a network drive is not allowed through the assistant")
    _refuse_links(action, lexical)
    return Path(lexical)


def _check_paths(action: str, args: dict) -> dict:
    """The arguments to pass on: every path-like one replaced by the checked, resolved path (raises when one is not allowed)."""
    import tempfile

    args = dict(args)
    spec = _PATHS.get(action)
    if spec is None:
        named = sorted(k for k in args if k.lower() in _PATH_KEYS)
        if named:
            raise ValueError(f"{action} takes a file or folder ({', '.join(named)}), which is not allowed through the assistant")
        return args
    unknown = sorted(k for k in args if k.lower() in _PATH_KEYS and k not in spec)
    if unknown:
        raise ValueError(f"{action}: the argument(s) {', '.join(unknown)} are not allowed through the assistant")
    roots = [Path(P.WORKSPACE).resolve(), Path(tempfile.gettempdir()).resolve()]
    for key, (inside, kinds) in spec.items():
        if args.get(key) is None:
            continue                                              # (no place named: the application's own default)
        if isinstance(args[key], str) and not args[key].strip():
            raise ValueError(f"{action}: the argument '{key}' is empty; give a full path or leave it out")
        target = _local_path(action, args[key])
        if inside is True and not any(target == r or r in target.parents for r in roots):
            raise ValueError(f"{action} through the assistant works only inside the workspace ({roots[0]}) or the temp folder: "
                             f"'{target}' is elsewhere. Use a place there and ask the user to move it, or let them do it from the menu.")
        if kinds is not None and target.suffix.lower() not in kinds:
            raise ValueError(f"{action} takes {', '.join(sorted(kinds))} files only (not '{target.suffix or 'no extension'}')")
        if inside is False and not target.is_file():              # (a read: an existing ordinary file of this computer)
            raise ValueError(f"{action}: '{target}' is not an existing file")
        if inside == "local":
            _refuse_reserved(action, target, folder=True)             # (a folder the user chose: still not a settings or credentials folder)
        if inside is True:
            _refuse_reserved(action, target)
            if kinds is not None and target.exists():             # (a file is written, never replaced: choose a new name)
                raise ValueError(f"{action}: '{target.name}' exists already, and a file is not replaced through the assistant; "
                                 f"choose another name")
        args[key] = str(target)
    return args


_ACTIONS_NOT_FOR_A_MODEL = {"assistant.command", "assistant.claude_code"}      # (the claude command line: sign in and out; Claude Code in a terminal, which has no tool limits)


def ui_do(pr, a):
    if a["action"] in _ACTIONS_NOT_FOR_A_MODEL:
        raise ValueError(f"The action '{a['action']}' is not operated through the assistant (it signs in or out of the account, or opens an "
                         f"unrestricted terminal). The person uses the button.")
    args = _check_paths(a["action"], a.get("args") or {})
    return _ui("do", {"action": a["action"], "args": args})


def ui_widgets(pr, a):
    return _ui("widgets", {"filter": a.get("filter"), "visible_only": bool(a.get("visible_only"))})


_NOT_FOR_A_MODEL = {"file.exit", "chrome.close", "assistant.login", "assistant.logout", "assistant.claude_code"}   # (quit; sign in and out of the account)


def ui_widget(pr, a):
    if str(a["id"]) in _NOT_FOR_A_MODEL and a.get("op", "get") != "get":
        raise ValueError(f"The control '{a['id']}' is not operated through the assistant (it quits the application or changes the "
                         f"sign-in): ask the user to use it")
    return _ui("widget", {"id": a["id"], "op": a.get("op", "get"), "value": a.get("value")})


def ui_screenshot(pr, a):
    d = _ui("screenshot", {"what": a.get("what", "window")})
    # (the MCP server turns the first line into an image the model can see; a person can open the file)
    return f"SCREENSHOT: {d['path']}\n{d['what']} picture, {d['width']} x {d['height']} px, saved at {d['path']}"


def ui_log(pr, a):
    return _ui("log", {k: a[k] for k in ("after", "level", "source", "contains", "limit") if a.get(k) is not None})


def ui_wait(pr, a):
    return _ui("wait", {"timeout_s": min(float(a.get("timeout_s", 120)), 900)})


def ui_eval(pr, a):
    return _ui("eval", {"code": a["code"], "token": a.get("token", "")})


UI_TOOLS = [
    ("ui_state", "DESKTOP WINDOW: a snapshot of everything the window shows - step, open study, the value of every control, what the 3D "
                 "view draws (colour map, range, deformation, camera, legend), running jobs, the assistant, error counts. Start here when "
                 "something looks wrong.", {"type": "object", "properties": {}}, ui_state),
    ("ui_actions", "DESKTOP WINDOW: every operation of the user interface by name (study.new, mesh.generate, results.show ...) with its "
                   "parameters.", {"type": "object", "properties": {}}, ui_actions),
    ("ui_do", "DESKTOP WINDOW: perform one operation of the user interface, as a person would (see ui_actions). Returns the new step, what "
              "the 3D view draws and any warnings or errors the operation logged.",
     {"type": "object", "properties": {"action": STR, "args": {"type": "object"}}, "required": ["action"]}, ui_do),
    ("ui_widgets", "DESKTOP WINDOW: list the controls by stable id (mesh.generate, results.colormap ...) with their text, value, enabled and "
                   "visible state and place in the window. filter = part of an id.",
     {"type": "object", "properties": {"filter": STR, "visible_only": {"type": "boolean"}}}, ui_widgets),
    ("ui_widget", "DESKTOP WINDOW: read or operate one control by id. op: get | click | set (value) | select (a tree item path). Opens no "
                  "dialogs: controls that would open a file chooser name the ui_do action to use instead.",
     {"type": "object", "properties": {"id": STR, "op": STR, "value": {}}, "required": ["id"]}, ui_widget),
    ("ui_screenshot", "DESKTOP WINDOW: a picture of the window (what='window'), the 3D view ('viewport') or one control (its id). Returns "
                      "the PNG file path and, in Claude Code, the image itself.",
     {"type": "object", "properties": {"what": STR}}, ui_screenshot),
    ("ui_log", "DESKTOP WINDOW: the application's log - Python logging, uncaught exceptions, Qt and VTK messages, anything printed. "
               "after = the last id you saw (returns only newer); level = debug|info|warning|error; source; contains; limit.",
     {"type": "object", "properties": {"after": INT, "level": STR, "source": STR, "contains": STR, "limit": INT}}, ui_log),
    ("ui_wait", "DESKTOP WINDOW: wait until the running jobs (mesh, solve ...) are finished and the window has caught up; timeout_s max 900.",
     {"type": "object", "properties": {"timeout_s": NUM}}, ui_wait),
]
TOOLS += UI_TOOLS

PROJECT_FREE = {"list_projects", "create_project", "openfoam_status", "install_openfoam", "list_options", "add_material", "remove_material"} | {t[0] for t in UI_TOOLS} | {"ui_eval"}
PROJECT_OPTIONAL = {"list_options"}  # project-free, but uses the project when one is given

MCP_EXTRA = [
    ("ui_eval", "DESKTOP WINDOW, DEBUG ONLY: run Python inside the running application (win, engine, view, control, app, P, np are in "
                "scope; the last expression's value is returned). Needs the window to be started with --debug-api. Use it to inspect or "
                "change anything the other ui_ tools cannot reach; it is logged.",
     {"type": "object", "properties": {"code": STR, "token": STR}, "required": ["code"]}, ui_eval),
    ("list_projects", "List workbench projects.", {"type": "object", "properties": {}}, list_projects),
    ("create_project", "Create a project. study: heat_conduction | static_structural | topology_optimization | vibration_modal | aeroelastic_flutter | cht_enclosure | "
                       "external_aero. "
                       "Optional step_path imports a STEP file (absolute path); or example = a bundled example name "
                       "(heated_plate.step, camera_mount.step, heatsink_duct.step, quadcopter.step, topology_bracket.step). "
                       "location = the folder the user chose to save the study in (full path; a sub-folder named "
                       "after the study is made there): ASK the user first, never choose it yourself.",
     {"type": "object", "properties": {"name": STR, "study": STR, "step_path": STR, "example": STR, "location": STR},
      "required": ["name", "study"]},
     create_project),
    ("import_step", "Import a STEP, STL or OBJ file (absolute path) into the project, replacing the geometry. "
                    "STL/OBJ must be closed (watertight) and are read in mm (use scale otherwise).",
     {"type": "object", "properties": {"step_path": STR, "scale": NUM}, "required": ["step_path"]}, import_step),
]


TOOL_ALIASES = {"set_mesh": "set_mesh_settings", "mesh": "generate_mesh", "solve": "run_simulation"}


def call(name: str, pr, args: dict) -> str:
    spec = {t[0]: t for t in TOOLS + MCP_EXTRA}
    name = name if name in spec else TOOL_ALIASES.get(name, name)  # common guesses of tool names
    if name not in spec:
        return f"ERROR: unknown tool '{name}'"
    _, _, schema, fn = spec[name]
    allowed = set(schema.get("properties", {}))
    unknown = set(args or {}) - allowed
    if unknown:
        return (f"ERROR: unknown argument(s) {sorted(unknown)} for {name}. "
                f"Allowed: {sorted(allowed) or 'none'}")
    missing = [k for k in schema.get("required", []) if k not in (args or {})]
    if missing:
        return f"ERROR: missing argument(s) {missing} for {name}"
    try:
        if name != "ui_do":                                       # (ui_do checks the action it carries: see _check_paths)
            args = _check_paths(name, args or {})
        return _fmt(fn(pr, args or {}))
    except Exception as e:  # noqa: BLE001
        return f"ERROR: {e}"


SYSTEM_PROMPT = """You are the assistant inside the Mechanical Simulation Workbench, a GUI that imports STEP CAD,
lets users tag faces with boundary conditions, meshes with gmsh and solves with built-in FEM solvers (heat
conduction; static stress and deformation; vibration modes = natural frequencies) or OpenFOAM (external
aerodynamics of any body - cars, UAVs, drones with actuator-disk rotors; conjugate heat transfer / electronics
cooling). Users bring their own parts: do not assume the geometry is one of the bundled examples.

How to help:
- Before creating a study ask the user which folder to save it in (every study lives in a folder the user chooses) and
  pass it as location; tell them the folder afterwards.
- Start by calling get_project_state. Use find_faces to locate faces by direction/position instead of guessing ids,
  and highlight_faces to show the user what you selected before/after changing things.
- Make the setup easy: propose sensible engineering defaults (materials, h coefficients, inlet speeds, mesh preset),
  apply them when the user asks you to, and explain briefly in plain language. Units: mm for geometry, degC, W.
- No fan / still air on a solid part: use 'natural_convection' (h is computed per face, plus radiation) rather
  than guessing h. Typical values: natural convection h = 5-10 W/m2K, small fan 20-60, strong forced air 50-150, liquid cooling
  500-5000. Aluminium 6063 for extruded heatsinks, silicon/alumina for chips. Small fan duct flow 1-5 m/s.
  Multirotor cruise pitch 5-20 deg nose-down; rotor thrust in hover = weight / number of rotors.
- Vibration: mark bolted/clamped faces 'fixed_support' (bolt-hole bores or the clamped face) and unmodelled
  components (camera, battery, PCB parts) as 'added_mass' on the faces they sit on. Excitation range: motor or
  rotor speed rpm/60 (1x) up to rpm/60 x blades (blade passing) at full throttle. Keep the first mode at least
  ~1.4x above the highest excitation; modes with large effective mass matter most. Fixed supports are stiffer
  than real mounts, so real frequencies are usually lower. Shake test (vibration-table sine sweep): settings
  'shake_axis' (x/y/z), 'shake_accel_g', 'damping_percent' (0.5-2 % one-piece or welded metal, 2-5 % bolted
  assemblies and plastics); the summary key 'sine' has the response over frequency and the resonance peaks.
  Peaks scale with 1 / damping, so say which damping was assumed; vibration damage is fatigue, not yield.
- Static structural: 'fixed_support' on bolted/clamped faces, 'frictionless_support' for pins, bearings and
  symmetry planes, 'force' (total N, spread over the faces) or 'pressure' for loads; gravity in the settings.
  Symmetry: when the part and its loads are mirror-symmetric, set_symmetry cuts the model at a plane and solves one
  side (half the cells); say what was assumed (loads on the kept half only, flow along the plane).
  Report the max von Mises stress, the deformation and the safety factor against yield; peak stresses at sharp
  corners are mesh-dependent - suggest the mesh-sensitivity check before trusting them. 'stress_error_percent'
  (energy norm) says how well the mesh resolves the stress: below ~10 % good, above 15 % refine. For brittle
  materials judge the max / min principal stress ('max_principal_MPa', 'min_principal_MPa'), not von Mises.
- External aero: no rotors = a plain body (car, wing, housing): set the flow speed, pitch (angle) and a reference
  area/length if the user quotes coefficients for a wing (area) or car (frontal area).
- CAD editing: edit_geometry changes the model itself (add or cut shapes, holes, fillets, chamfers, mirror, patterns,
  scale, move, a wing lofted through airfoil sections ...). Work in small steps, one edit per call, and after each one
  compare the returned body sizes and volume with what you meant (a wrong sign or unit shows up there); say in a sentence
  what you did. Measure from the model first (get_project_state sizes, find_faces / find_edges positions) instead of
  guessing coordinates; ask the user for a dimension you cannot read from the model. Edits can be undone (remove by
  number), so try the cheap version first. Faces get new ids after an edit: find them again before setting boundary
  conditions, and tell the user which conditions need checking. A fillet that is refused needs a smaller radius.
  Rotors, rotating zones, symmetry planes and mesh sizing keep their positions: re-check them after you move or delete
  the parts they belong to.
- Physics sanity: check energy balance, Reynolds number (laminar if Re < ~2000 in fin channels), y+ and mesh
  quality; warn when results look implausible.
- Before running, call check_setup and fix errors. Use coarse meshes for quick looks, finer for final answers,
  and suggest a mesh-sensitivity check for important decisions.
- After a CFD run, check get_results -> convergence; if not converged, say so and offer to continue the run.
- OpenFOAM is built in on Windows: the workbench sets it up by itself on the first start, and a CFD run started
  meanwhile waits for it. If openfoam_status says it is missing and no setup is running, call install_openfoam.
  On Linux/macOS relay the install instructions; the case can still be exported.
- When the user has the desktop window open you can see and drive it: ui_state (what it shows), ui_screenshot (a picture), ui_do (any
  operation of the user interface), ui_widget (one control), ui_log (errors and warnings), ui_wait (for jobs). If something on the
  screen looks wrong, look at ui_state and ui_log before guessing, and say what you found.
- Be concise. Do not invent results; only report numbers returned by tools."""
