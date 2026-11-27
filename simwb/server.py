"""Web server: REST API + websocket (job progress, chat) + static GUI.

    python -m simwb.server        (opens http://127.0.0.1:8765)
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from . import __version__, agent, daemonthread, materials, project as P, studies

@asynccontextmanager
async def _lifespan(app):
    HUB.loop = asyncio.get_running_loop()  # job threads publish websocket events onto this loop
    from .solvers.openfoam import installer

    try:  # built-in OpenFOAM: set it up in the background on the first start (no separate install)
        await daemonthread.to_thread(installer.auto_start, P.JOBS)
    except Exception as e:  # noqa: BLE001 - never block the server start
        print(f"OpenFOAM auto-setup could not start: {e}")
    yield


app = FastAPI(title="Mechanical Simulation Workbench", version=__version__, lifespan=_lifespan)


@app.exception_handler(ValueError)
@app.exception_handler(KeyError)
@app.exception_handler(RuntimeError)
@app.exception_handler(TypeError)
async def _user_error(request: Request, exc: Exception):
    msg = exc.args[0] if exc.args else str(exc)
    if isinstance(exc, TypeError):
        msg = f"Invalid input: {msg}"
    return JSONResponse(status_code=400, content={"error": str(msg)})


def _local_origin(origin: str | None) -> bool:
    if not origin:
        return True  # non-browser clients (curl, scripts) send no Origin
    return bool(re.match(r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$", origin))


# Host names the server answers to. A web page that points its own domain name at 127.0.0.1 (DNS rebinding) sends
# that name as Host: refusing it keeps other sites from reading projects and results. SIMWB_ALLOWED_HOSTS adds names.
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"} | {
    h.strip().lower() for h in os.environ.get("SIMWB_ALLOWED_HOSTS", "").split(",") if h.strip()}


def _local_host(host: str | None) -> bool:
    m = re.fullmatch(r"(\[[0-9a-fA-F:]+\]|[^:\[\]]+)(:\d+)?", (host or "").strip())
    return bool(m) and m.group(1).lower() in LOCAL_HOSTS


@app.middleware("http")
async def _csrf_guard(request: Request, call_next):
    """Only the workbench page may change state: mutating API calls need the X-SimWB header and a local Origin
    (blocks cross-site form posts / fetches from other web pages). Every request needs a local Host header
    (blocks DNS rebinding, which would let another site read data)."""
    if not _local_host(request.headers.get("host")):
        return JSONResponse(status_code=403, content={"error": "Forbidden (the workbench only answers on 127.0.0.1 / "
                                                               "localhost; set SIMWB_ALLOWED_HOSTS for other names)"})
    if request.method in ("POST", "PATCH", "PUT", "DELETE") and request.url.path.startswith("/api/"):
        if request.headers.get("x-simwb") != "1" or not _local_origin(request.headers.get("origin")):
            return JSONResponse(status_code=403, content={"error": "Forbidden (missing X-SimWB header or foreign origin)"})
    return await call_next(request)


def _job(j):
    return {"job": j.to_dict(log_tail=0)}


# ---------------------------------------------------------------- meta
@app.get("/api/meta")
def meta():
    return {
        "version": __version__,
        "studies": studies.STUDIES,
        "foam_advanced": studies.FOAM_ADVANCED,
        "materials": {"solids": materials.SOLIDS, "fluids": materials.FLUIDS},
        "examples": sorted(p.name for p in P.EXAMPLES.glob("*.step")),
        "workspace": str(P.WORKSPACE),
    }


@app.get("/api/openfoam")
def openfoam(refresh: bool = False):
    from .solvers.openfoam import installer, runner

    j = installer.running_job(P.JOBS)
    if j:  # don't probe a tree the installer is still writing
        return {"available": False, "backend": None, "detail": "OpenFOAM is being installed",
                "installable": True, "install_job": j.to_dict(log_tail=0)}
    return runner.detect(refresh=refresh)


@app.post("/api/openfoam/install")
def openfoam_install():
    from .solvers.openfoam import installer

    if not installer.supported():
        raise RuntimeError("Built-in OpenFOAM installation is available on Windows only.")
    return _job(installer.start(P.JOBS))


@app.get("/api/calculix")
def calculix_status(refresh: bool = False):
    from .solvers.calculix import installer

    info = dict(installer.detect(refresh=refresh))
    j = installer.running_job(P.JOBS)
    if j:
        info["install_job"] = j.to_dict(log_tail=0)
    return info


@app.post("/api/calculix/install")
def calculix_install():
    from .solvers.calculix import installer

    if not installer.supported():
        raise RuntimeError("The built-in CalculiX setup is for Windows; install CalculiX with your package manager "
                           "(it is found on the PATH) or set SIMWB_CCX.")
    return _job(installer.start(P.JOBS))


@app.get("/api/projects/{pid}/software")
def project_software(pid: str):
    from . import software

    pr = P.open_project(pid)
    return software.describe(pr.study, pr.data["settings"])


@app.get("/api/claude/status")
async def claude_status():
    return await daemonthread.to_thread(agent.auth_status)


@app.post("/api/claude/login")
def claude_login():
    return {"message": agent.start_login()}


# ---------------------------------------------------------------- projects
@app.get("/api/projects")
def projects():
    return P.list_projects()


@app.post("/api/projects")
def new_project(body: dict = Body(...)):
    ex = body.get("example")
    src = None
    if ex:
        src = P.EXAMPLES / Path(ex).name
        if not src.exists():
            raise ValueError(f"Unknown example '{ex}'")
    pr = P.create_project(body.get("name") or "Untitled", body.get("study") or "heat_conduction",
                          (body.get("location") or "").strip() or None)
    if src:
        pr.import_step(src, original_name=src.name)
    return pr.state()


@app.post("/api/projects/{pid}/open_claude")
def open_claude(pid: str):
    """Claude Code in a terminal window, started in this study's folder (which has a CLAUDE.md and .mcp.json)."""
    pr = P.open_project(pid)
    P.write_claude_files(pr.dir, int(os.environ.get("SIMWB_PORT", "8765")))
    return {"path": str(pr.dir), "message": agent.open_in_claude_code(pr.dir)}


@app.post("/api/projects/open_folder")
def open_folder(body: dict = Body(...)):
    """Add a project folder from anywhere on the disk to the list."""
    return P.open_project_folder(body.get("path") or "").state()


@app.post("/api/pick_folder")
async def pick_folder(body: dict = Body(default={})):
    """A folder chooser on this computer (the workbench runs locally); '' if cancelled."""
    import subprocess
    import sys

    code = ("import tkinter as t, tkinter.filedialog as f, sys\nr = t.Tk(); r.withdraw(); r.attributes('-topmost', True)\n"
            "print(f.askdirectory(title='Folder to save the study in', initialdir=sys.argv[1] or None) or '')")
    start = str(body.get("start") or "")

    def run():
        r = subprocess.run([sys.executable, "-c", code, start], capture_output=True, text=True, timeout=300)
        return r.stdout.strip().replace("/", os.sep) if r.returncode == 0 else ""

    return {"path": await daemonthread.to_thread(run)}


terminal_args = agent.terminal_args          # (the rules live in agent.py: the desktop window uses them too)


@app.post("/api/projects/{pid}/terminal")
def terminal_run(pid: str, body: dict = Body(...)):
    """One line typed in the Claude terminal. If a command is running the line goes to its input; otherwise it is
    run as arguments of the claude CLI in the project folder."""
    P.open_project(pid)
    line = (body.get("line") or "").strip()
    if agent.TERMINAL.write(pid, line):
        return {"running": True, "stdin": True}
    args = agent.terminal_line_args(line)
    cwd = agent.TERMINAL.run(pid, args, P.JOBS.emit)
    return {"running": True, "cwd": cwd, "args": args}


@app.post("/api/projects/{pid}/terminal/stop")
def terminal_stop(pid: str):
    P.open_project(pid)
    agent.TERMINAL.stop(pid)
    return {"ok": True}


@app.delete("/api/projects/{pid}")
def del_project(pid: str):
    P.delete_project(pid)
    return {"ok": True}


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    return P.open_project(pid).state()


@app.get("/api/projects/{pid}/faces")
def get_faces(pid: str):
    pr = P.open_project(pid)
    return {"faces": pr.faces(), "bodies": pr.state()["body_list"]}


@app.post("/api/projects/{pid}/import")
async def import_step(pid: str, file: UploadFile = File(...), scale: float = 1.0):
    pr = P.open_project(pid)
    tmp = Path(tempfile.mkdtemp()) / Path(file.filename or "upload.step").name
    with open(tmp, "wb") as f:
        shutil.copyfileobj(file.file, f)
    return _job(pr.import_step(tmp, original_name=file.filename, scale=scale))


@app.post("/api/projects/{pid}/rotate")
def rotate(pid: str, body: dict = Body(...)):
    pr = P.open_project(pid)
    if body.get("align"):  # {"face": id, "to": "+z"}: lay a flat face on a coordinate plane
        a = body["align"]  # {"face": id, "to": "+z"} or {"face": id, "wall": id, "away": false}
        return _job(pr.align_face(a["face"], a.get("to"), a.get("wall"), bool(a.get("away"))))
    return _job(pr.rotate_geometry(body.get("axis"), body.get("degrees", 90.0), bool(body.get("reset")),
                                   body.get("matrix"), snap_zones=bool(body.get("snap_zones"))))


@app.get("/api/projects/{pid}/axis_info")
def axis_info(pid: str, face: int, x: float | None = None, y: float | None = None, z: float | None = None):
    """Centre and axis of a cylindrical face, or of the circular edge of a face nearest to the clicked point (x, y, z)."""
    pt = [x, y, z] if None not in (x, y, z) else None
    return P.open_project(pid).axis_info(face, pt)


@app.get("/api/projects/{pid}/circular_edges")
def circular_edges(pid: str):
    """Every circular edge of the parts (centre, axis, radius, outline points), for picking one in the 3D view."""
    return {"edges": P.open_project(pid).circular_edges()}


@app.get("/api/projects/{pid}/align_info")
def align_info(pid: str, face: int, wall: int = 0, ref: int = 0):
    """Which way a flat face points (out of the part) and the axis it is nearest to; wall=1: a wall of the tunnel;
    ref=1: the plane to align to (a tunnel wall or another flat face)."""
    pr = P.open_project(pid)
    return pr.ref_info(face) if ref else pr.wall_info(face) if wall else pr.align_info(face)


@app.post("/api/projects/{pid}/import_example")
def import_example(pid: str, body: dict = Body(...)):
    pr = P.open_project(pid)
    src = P.EXAMPLES / Path(body["name"]).name
    if not src.exists():
        raise ValueError(f"Unknown example '{body['name']}'")
    return _job(pr.import_step(src, original_name=src.name))


@app.post("/api/projects/{pid}/study")
def set_study(pid: str, body: dict = Body(...)):
    pr = P.open_project(pid)
    j = pr.set_study(body["study"])
    return _job(j) if j else pr.state()


@app.post("/api/projects/{pid}/domain")
def set_domain(pid: str, body: dict = Body(...)):
    j = P.open_project(pid).set_domain(**body)
    return _job(j) if j else {"job": None, "saved": True}


@app.post("/api/projects/{pid}/settings")
def set_settings(pid: str, body: dict = Body(...)):
    pr = P.open_project(pid)
    pr.set_settings(**body)
    return pr.state()


@app.post("/api/projects/{pid}/rename")
def rename(pid: str, body: dict = Body(...)):
    pr = P.open_project(pid)
    pr.rename(body.get("name"))
    return pr.state()


@app.post("/api/projects/{pid}/bodies/{bid}")
def set_body(pid: str, bid: int, body: dict = Body(...)):
    pr = P.open_project(pid)
    pr.set_body(bid, material=body.get("material"), heat_W=body.get("heat_W"), name=body.get("name"))
    return pr.state()


@app.post("/api/projects/{pid}/bcs")
def add_bc(pid: str, body: dict = Body(...)):
    pr = P.open_project(pid)
    pr.add_bc(body.get("name", ""), body["type"], body.get("faces", []), body.get("params", {}))
    return pr.state()


@app.patch("/api/projects/{pid}/bcs/{bcid}")
def update_bc(pid: str, bcid: str, body: dict = Body(...)):
    pr = P.open_project(pid)
    pr.update_bc(bcid, name=body.get("name"), bc_type=body.get("type"), faces=body.get("faces"),
                 params=body.get("params"), add_faces=body.get("add_faces"), remove_faces=body.get("remove_faces"))
    return pr.state()


@app.delete("/api/projects/{pid}/bcs/{bcid}")
def delete_bc(pid: str, bcid: str):
    pr = P.open_project(pid)
    pr.delete_bc(bcid)
    return pr.state()


@app.post("/api/projects/{pid}/symmetry")
def symmetry(pid: str, body: dict = Body(...)):
    """Symmetry planes. action: add (axis + pos_mm | faces | point + normal; keep '+'|'-'; name), update (id; pos_mm,
    flip, name, normal, point) or remove (id). The geometry is rebuilt in a background job (the response's job)."""
    pr = P.open_project(pid)
    action = body.get("action", "add")
    if action == "add":
        job = pr.add_symmetry(axis=body.get("axis"), pos_mm=body.get("pos_mm"), faces=body.get("faces"),
                              keep=body.get("keep"), name=body.get("name"), point=body.get("point"),
                              normal=body.get("normal"), cut_parts=bool(body.get("cut_parts")))
    elif action == "update":
        job = pr.update_symmetry(body["id"], pos_mm=body.get("pos_mm"), flip=bool(body.get("flip")),
                                 name=body.get("name"), normal=body.get("normal"), point=body.get("point"),
                                 cut_parts=body.get("cut_parts"))
    elif action == "remove":
        job = pr.remove_symmetry(body["id"])
    else:
        raise ValueError("action must be add, update or remove")
    return {"job": job.to_dict(log_tail=0) if job else None, "symmetry": pr.symmetry_planes()}


@app.post("/api/projects/{pid}/geometry/edits")
def geometry_edits(pid: str, body: dict = Body(...)):
    """CAD edits of the geometry. action: add (edit), update (number, edit), remove (number), clear, list. The geometry
    is rebuilt in a background job (the response's job); numbers count from 1."""
    pr = P.open_project(pid)
    action = body.get("action", "add")
    if action == "list":
        return {"edits": pr.cad_edits()}
    job = pr.edit_geometry(action, op=body.get("edit"), index=body.get("number"))
    return {"job": job.to_dict(log_tail=0), "edits": pr.cad_edits()}


@app.post("/api/projects/{pid}/auto_inlet_outlet")
def auto_io(pid: str):
    pr = P.open_project(pid)
    pr.suggest_inlet_outlet(apply=True)
    return pr.state()


@app.post("/api/projects/{pid}/rotors")
def set_rotors(pid: str, body: dict = Body(...)):
    pr = P.open_project(pid)
    pr.set_rotors(body["rotors"])
    return pr.state()


@app.post("/api/projects/{pid}/rotors/detect")
def detect_rotors(pid: str, body: dict = Body(default={})):
    pr = P.open_project(pid)
    r = body.get("radius")
    pr.detect_rotors(apply=True, radius=float(r) if r else None)
    return pr.state()


@app.post("/api/projects/{pid}/mesh/settings")
def mesh_settings(pid: str, body: dict = Body(...)):
    pr = P.open_project(pid)
    pr.set_mesh(preset=body.get("preset"), size_max_mm=body.get("size_max_mm"), size_min_mm=body.get("size_min_mm"),
                refinements=body.get("refinements"), regions=body.get("regions"), layers=body.get("layers"),
                defeature_mm=body["defeature_mm"] if "defeature_mm" in body else False, method=body.get("method"),
                edge_refinement=body.get("edge_refinement"), smart=body.get("smart"))
    return pr.state()


@app.get("/api/projects/{pid}/mesh/advice")
def mesh_advice(pid: str):
    """What a good mesh of this body in this flow needs (the recipe) and how the current mesh measures up (the scorecard)."""
    return P.open_project(pid).mesh_advice()


@app.post("/api/projects/{pid}/mesh/recipe")
def mesh_recipe(pid: str, body: dict = Body(default={})):
    """Smart sizing on, and the wall layers the recipe recommends; the mesh must be generated again."""
    return P.open_project(pid).apply_mesh_recipe(preset=body.get("preset"), layers=body.get("layers", True))


@app.post("/api/projects/{pid}/mesh")
def mesh(pid: str):
    return _job(P.open_project(pid).generate_mesh())


@app.get("/api/projects/{pid}/mesh/view")
def mesh_view(pid: str, clip_axis: str | None = None, clip_pos: float | None = None, hide_fluid: bool = False,
              poor: float | None = None, metric: str = "quality"):
    return P.open_project(pid).mesh_view(clip_axis, clip_pos, hide_fluid, poor, metric)


@app.get("/api/projects/{pid}/mesh/metrics")
def mesh_metrics(pid: str):
    """Statistics, histograms and the cell budget of the mesh (size, aspect ratio, growth, volume, quality)."""
    return P.open_project(pid).mesh_metrics()


@app.post("/api/projects/{pid}/solve")
def solve(pid: str, body: dict = Body(default={})):
    return _job(P.open_project(pid).solve(export_only=bool(body.get("export_only")),
                                          continue_iterations=body.get("continue_iterations"),
                                          init_from=body.get("init_from") or None,
                                          end_time=body.get("end_time"), write_interval=body.get("write_interval")))


@app.post("/api/projects/{pid}/results/load_case")
def results_load_case(pid: str):
    """Join and load the results already in the case folder, without running the solver."""
    return _job(P.open_project(pid).load_case_results())


@app.post("/api/projects/{pid}/duplicate")
def duplicate(pid: str, body: dict = Body(default={})):
    return P.duplicate_project(pid, body.get("name")).state()


@app.post("/api/projects/{pid}/moving_zones")
def moving_zones(pid: str, body: dict = Body(...)):
    return P.open_project(pid).set_moving_zones(body.get("zones", []))


@app.post("/api/projects/{pid}/foam_advanced")
def foam_advanced(pid: str, body: dict = Body(...)):
    pr = P.open_project(pid)
    pr.set_foam_advanced(**body)
    return pr.state()


@app.post("/api/projects/{pid}/case_files/generate")
def case_files_generate(pid: str):
    return P.open_project(pid).foam_preview()


@app.get("/api/projects/{pid}/case_files")
def case_files(pid: str):
    return P.open_project(pid).case_files()


@app.get("/api/projects/{pid}/case_file")
def case_file(pid: str, path: str):
    return P.open_project(pid).case_file(path)


@app.post("/api/projects/{pid}/case_file")
def case_file_save(pid: str, body: dict = Body(...)):
    return P.open_project(pid).set_case_file(body.get("path", ""), body.get("text"))


@app.post("/api/projects/{pid}/apply_trim")
def apply_trim(pid: str):
    pr = P.open_project(pid)
    pr.apply_trim()
    return pr.state()


@app.post("/api/projects/{pid}/rotors/auto_thrust")
def rotors_auto_thrust(pid: str):
    return P.open_project(pid).reset_thrusts()


@app.post("/api/projects/{pid}/solve/stop")
def stop_solve(pid: str):
    return _job(P.open_project(pid).stop_solve())


@app.post("/api/tools/{name}")
def run_tool(name: str, body: dict = Body(default={})):
    """Used by the MCP server so that jobs and project state live in this one process."""
    from . import tools as T

    names = {t[0] for t in T.TOOLS + T.MCP_EXTRA}
    name = name if name in names else T.TOOL_ALIASES.get(name, name)
    if name not in names:
        raise ValueError(f"Unknown tool '{name}'")
    pid = body.get("project_id")
    if not pid and name not in T.PROJECT_FREE:
        projects = P.list_projects()
        if not projects:
            raise ValueError("No projects yet - call create_project first.")
        if len(projects) > 1:  # never guess which project to change
            raise ValueError("Several projects exist: pass project_id (one of "
                             + ", ".join(p["id"] for p in projects[:20]) + ").")
        pid = projects[0]["id"]
    pr = P.open_project(pid) if pid and (name not in T.PROJECT_FREE or name in T.PROJECT_OPTIONAL) else None
    text = T.call(name, pr, body.get("args") or {})
    return {"text": text, "project": None if pr is None else {"id": pr.id, "name": pr.data.get("name")}}


@app.post("/api/projects/{pid}/model_check")
def model_check(pid: str):
    return _job(P.open_project(pid).model_check())


@app.post("/api/projects/{pid}/mesh_check")
def mesh_check(pid: str, body: dict = Body(default={})):
    return _job(P.open_project(pid).mesh_check(levels=int(body.get("levels") or 1)))


@app.post("/api/projects/{pid}/highlight")
def highlight(pid: str, body: dict = Body(...)):
    P.open_project(pid)
    P.JOBS.emit({"type": "highlight", "project": pid, "faces": [int(f) for f in body.get("faces", [])]})
    return {"ok": True}


@app.get("/api/projects/{pid}/results/fields")
def result_fields(pid: str):
    pr = P.open_project(pid)
    rs = pr.results()
    return {"fields": rs.field_info(), "regions": rs.regions, "groups": rs.groups, "times": rs.times,
            "summary": pr.data["solve"].get("summary")}


@app.post("/api/projects/{pid}/results/reload")
def results_reload(pid: str):
    return P.open_project(pid).reload_results()


@app.get("/api/projects/{pid}/results/surface")
def result_surface(pid: str, field: str, hide: str = "", groups: str = "", time: int = -1):
    rs = P.open_project(pid).results().at(time)
    return rs.surface(field, groups=[g for g in groups.split(",") if g] or None,
                      hide_regions=[h for h in hide.split(",") if h])


@app.get("/api/projects/{pid}/results/clip")
def result_clip(pid: str, field: str, axis: str, pos: float, hide: str = "", time: int = -1):
    if axis not in ("x", "y", "z"):
        raise ValueError("axis must be x, y or z")
    rs = P.open_project(pid).results().at(time)
    return rs.slice(field, "xyz".index(axis), pos, hide_regions=[h for h in hide.split(",") if h])


@app.get("/api/projects/{pid}/results/iso")
def result_iso(pid: str, field: str, level: float, color: str = "", hide: str = "", time: int = -1,
               wall_gap: float = 0.0, smooth: int = 0):
    pr = P.open_project(pid)
    rs = pr.results().at(time)
    walls = (pr.data["solve"].get("summary") or {}).get("default_groups") or []
    return rs.iso(field, level, color or None, hide_regions=[h for h in hide.split(",") if h],
                  wall_groups=walls, wall_gap=max(0.0, wall_gap),
                  smooth=min(max(smooth, 0), 3))


@app.post("/api/projects/{pid}/results/video")
async def results_video(pid: str, request: Request):
    """MP4 from the frames the browser rendered (one JPEG per saved time, in order), H.264 at the given rate."""
    import subprocess

    from starlette.background import BackgroundTask

    pr = P.open_project(pid)
    form = await request.form()
    frames = form.getlist("frames")
    if len(frames) < 2:
        raise ValueError("A video needs at least two frames")
    fps = min(max(float(form.get("fps") or 12), 1.0), 60.0)
    try:
        import imageio_ffmpeg
    except ImportError as e:
        raise ValueError("MP4 export needs the imageio-ffmpeg package (pip install imageio-ffmpeg)") from e
    tmp = Path(tempfile.mkdtemp())
    for i, f in enumerate(frames):
        (tmp / f"f{i:05d}.jpg").write_bytes(await f.read())
    out = tmp / "video.mp4"
    r = await daemonthread.to_thread(
        subprocess.run, [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-framerate", f"{fps:g}",
                         "-i", str(tmp / "f%05d.jpg"), "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2:out_range=tv,format=yuv420p",  # (H.264: even sizes)
                         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-movflags", "+faststart", str(out)],
        capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode or not out.exists():
        shutil.rmtree(tmp, ignore_errors=True)
        raise ValueError(f"Video encoding failed: {(r.stderr or '').strip()[-300:]}")
    headers = {}
    name = re.sub(r"[^A-Za-z0-9_-]+", "-", str(form.get("save_as") or "")).strip("-")
    if name:  # keep a copy in the study folder
        vdir = pr.dir / "videos"
        vdir.mkdir(exist_ok=True)
        shutil.copyfile(out, vdir / f"{name}.mp4")
        headers["X-Saved-To"] = str(vdir / f"{name}.mp4")
    return FileResponse(out, media_type="video/mp4", filename=f"{pid}.mp4", headers=headers,
                        background=BackgroundTask(shutil.rmtree, tmp, True))


@app.get("/api/projects/{pid}/case.zip")
def case_zip(pid: str):
    pr = P.open_project(pid)
    if not pr.case_dir.exists():
        raise ValueError("No OpenFOAM case written yet")
    out = Path(tempfile.mkdtemp()) / f"{pid}-case"
    shutil.make_archive(str(out), "zip", pr.case_dir)
    return FileResponse(str(out) + ".zip", filename=f"{pid}-openfoam-case.zip")


# ---------------------------------------------------------------- jobs
@app.get("/api/jobs")
def jobs_running():
    """Every running job, in any project (the Jobs menu)."""
    return {"jobs": [j.to_dict(log_tail=0) | {"extra": {}} for j in P.JOBS.jobs.values() if j.status == "running"]}


@app.get("/api/jobs/{jid}")
def job(jid: str, log_tail: int = 200, max_points: int = 400):
    j = P.JOBS.jobs.get(jid)
    if not j:
        raise KeyError("no such job")
    d = j.to_dict(log_tail=log_tail)
    res = d.get("extra", {}).get("residuals")
    n = len(res["iters"]) if isinstance(res, dict) and res.get("iters") else 0
    if max_points > 1 and n > max_points:  # thin the residual history for polling (keeps the last point)
        idx = sorted(set(range(0, n, -(-n // max_points))) | {n - 1})
        d["extra"] = {**d["extra"], "residuals": {
            "iters": [res["iters"][i] for i in idx],
            "residuals": {k: [v[i] if i < len(v) else None for i in idx] for k, v in res["residuals"].items()}}}
    return d


@app.post("/api/jobs/{jid}/cancel")
def cancel(jid: str):
    return {"ok": P.JOBS.cancel(jid)}


def _child_pids(pid: int) -> list[int]:
    """Processes started by this one (solvers, mpiexec, meshers, the Claude CLI)."""
    import subprocess

    try:
        if os.name == "nt":
            out = subprocess.run(["powershell", "-NoProfile", "-Command",
                                  f"(Get-CimInstance Win32_Process -Filter 'ParentProcessId={pid}').ProcessId"],
                                 capture_output=True, text=True, timeout=20,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        else:
            out = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True, timeout=20).stdout
        return [int(x) for x in out.split() if x.isdigit()]
    except Exception:  # noqa: BLE001
        return []


def _kill_tree(pid: int):
    import signal
    import subprocess

    if os.name == "nt":  # /T: with its own children (mpiexec -> solver ranks)
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return
    for c in _child_pids(pid):
        _kill_tree(c)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


@app.post("/api/shutdown")
def shutdown():
    """Close the workbench: cancel every running job, end every process it started, then stop the server."""
    running = [j for j in P.JOBS.jobs.values() if j.status == "running"]

    def close():
        for j in running:
            P.JOBS.cancel(j.id)
        for j in running:  # let the jobs record that they were cancelled
            j.wait(5)
        for _ in range(2):  # (twice: a step may have been starting while the first sweep ran)
            for c in _child_pids(os.getpid()):
                _kill_tree(c)
        print("Workbench closed from the page.", flush=True)
        os._exit(0)

    threading.Timer(0.3, close).start()
    return {"ok": True, "cancelled": [f"{j.kind} ({j.project})" for j in running]}


# ---------------------------------------------------------------- websocket
class Hub:
    def __init__(self):
        self.clients: set = set()
        self.loop = None

    def publish(self, event):
        if not self.loop:
            return
        for q in list(self.clients):
            self.loop.call_soon_threadsafe(q.put_nowait, event)


HUB = Hub()
P.JOBS.listeners.append(HUB.publish)



@app.websocket("/ws")
async def ws(sock: WebSocket):
    if not _local_origin(sock.headers.get("origin")) or not _local_host(sock.headers.get("host")):
        await sock.close(code=1008)
        return
    await sock.accept()
    q: asyncio.Queue = asyncio.Queue()
    HUB.clients.add(q)
    chats: dict[str, agent.ChatSession] = {}

    async def send(ev):
        try:
            await sock.send_text(json.dumps(ev, default=str))
        except Exception:  # noqa: BLE001
            pass

    async def pump():
        while True:
            ev = await q.get()
            await send(ev)

    pump_task = asyncio.create_task(pump())
    tasks = set()
    try:
        while True:
            msg = json.loads(await sock.receive_text())
            t = msg.get("type")
            if t == "chat":
                pid = msg["project"]
                sess = chats.get(pid)
                if sess is None:
                    async def psend(ev, pid=pid):
                        ev["project"] = pid
                        await send(ev)
                    sess = chats[pid] = agent.ChatSession(pid, psend)
                task = asyncio.create_task(sess.ask(msg["text"], msg.get("history") or []))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
            elif t == "chat_stop":
                s = chats.get(msg["project"])
                if s:
                    await s.interrupt()
            elif t == "chat_reset":
                s = chats.pop(msg["project"], None)
                if s:
                    await s.close()
    except WebSocketDisconnect:
        pass
    finally:
        pump_task.cancel()
        HUB.clients.discard(q)
        for s in chats.values():
            await s.close()


# ---------------------------------------------------------------- helpers
def _box(box: str, pr) -> list:
    """The region of a resampled view: 'x0,y0,z0,x1,y1,z1' in metres."""
    try:
        b = [float(v) for v in box.split(",")]
    except ValueError:
        b = []
    if len(b) != 6:
        raise ValueError("box must be x0,y0,z0,x1,y1,z1 (m)")
    return b


@app.get("/api/projects/{pid}/results/export")
def results_export(pid: str, format: str = "vtu", time: int = -1):
    """The results as a file: vtu (ParaView / PyVista) or csv (one row per mesh node)."""
    from starlette.background import BackgroundTask

    from . import export
    if format not in ("vtu", "csv"):
        raise ValueError("format must be vtu or csv")
    rs = P.open_project(pid).results().at(time)
    tmp = Path(tempfile.mkdtemp(prefix="simwb_export_"))
    out = (export.write_vtu if format == "vtu" else export.write_csv)(rs, tmp / f"{pid}.{format}")
    return FileResponse(out, filename=f"{pid}-results.{format}", background=BackgroundTask(shutil.rmtree, tmp, True),
                        media_type="text/csv" if format == "csv" else "application/xml")


@app.post("/api/projects/{pid}/report")
def report(pid: str, body: dict = Body(default={})):
    """The study as one self-contained HTML page (setup, mesh, key results, warnings, assumptions); body.image:
    a picture of the 3D view (data URL) to include. Prints to PDF from the browser."""
    from fastapi.responses import HTMLResponse

    from . import export
    return HTMLResponse(export.report_html(P.open_project(pid), body.get("image")),
                        headers={"Content-Disposition": f'attachment; filename="{pid}-report.html"'})


@app.get("/api/projects/{pid}/results/volume")
def result_volume(pid: str, field: str, box: str, n: int = 96, time: int = -1, hide: str = ""):
    from . import resample
    pr = P.open_project(pid)
    return resample.volume_payload(pr.results().at(time), field, _box(box, pr), min(max(n, 16), 192),
                                   [h for h in hide.split(",") if h])


@app.get("/api/projects/{pid}/results/plane")
def result_plane(pid: str, field: str, axis: str, pos: float, box: str, n: int = 96, time: int = -1, hide: str = ""):
    from . import resample
    if axis not in ("x", "y", "z"):
        raise ValueError("axis must be x, y or z")
    pr = P.open_project(pid)
    return resample.plane_payload(pr.results().at(time), field, "xyz".index(axis), pos, _box(box, pr),
                                  min(max(n, 16), 256), [h for h in hide.split(",") if h])


@app.get("/api/projects/{pid}/results/line")
def result_line(pid: str, field: str, p0: str, p1: str, n: int = 120, time: int = -1, hide: str = ""):
    """The field along the straight line between two points (x,y,z in m): for the line plot of the results step."""
    from . import resample

    def pt(s):
        try:
            v = [float(x) for x in s.split(",")]
        except ValueError:
            v = []
        if len(v) != 3:
            raise ValueError("a point must be x,y,z (m)")
        return v
    pr = P.open_project(pid)
    return resample.line_payload(pr.results().at(time), field, pt(p0), pt(p1), n, [h for h in hide.split(",") if h])


@app.get("/api/projects/{pid}/results/vectors")
def result_vectors(pid: str, axis: str, pos: float, box: str, n: int = 24, time: int = -1, hide: str = ""):
    from . import resample
    if axis not in ("x", "y", "z"):
        raise ValueError("axis must be x, y or z")
    pr = P.open_project(pid)
    return resample.vector_payload(pr.results().at(time), "xyz".index(axis), pos, _box(box, pr), min(max(n, 4), 80),
                                   hide_regions=[h for h in hide.split(",") if h])


@app.get("/api/projects/{pid}/results/particles")
def result_particles(pid: str, box: str, n: int = 1500, steps: int = 160, seed: str = "upstream", flow: str = "1,0,0",
                     grid: int = 80, time: int = -1, hide: str = "", sbox: str = ""):
    from . import resample
    pr = P.open_project(pid)
    return resample.particle_paths(pr.results().at(time), _box(box, pr), min(max(grid, 24), 160),
                                   min(max(n, 50), 6000), min(max(steps, 20), 400),
                                   seed if seed in ("everywhere", "box") else "upstream",
                                   [float(v) for v in flow.split(",")][:3],
                                   hide_regions=[h for h in hide.split(",") if h],
                                   seed_box=_box(sbox, pr) if seed == "box" else None)


def main():
    """The API without a window (REST, /api/tools, /api/ui is served only by the desktop window): for scripts and tests. The
    application itself is the desktop window: python -m simwb.qt."""
    import uvicorn

    port = int(os.environ.get("SIMWB_PORT", "8765"))
    print(f"Mechanical Simulation Workbench API (no window) at http://127.0.0.1:{port}  - the application is: python -m simwb.qt")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()

