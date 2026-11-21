"""Project model and the high-level operations used by the GUI, the Claude agent and the MCP server."""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import numpy as np

from . import materials, studies
from . import axisfit, cad_edit, orient
from . import symmetry as sym
from .jobs import Cancelled, Job, JobManager, run_gmsh_task
from .results import ResultSet, boundary_from_tets

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = Path(os.environ.get("SIMWB_HOME", ROOT / "projects"))
EXAMPLES = ROOT / "examples"

JOBS = JobManager()


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def slug(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", name.strip()).strip("-")
    return s or "project"


def _locations_file() -> Path:
    return WORKSPACE / "locations.json"


def _locations() -> dict:
    """Projects saved outside the workspace: {id: folder}. The workspace itself holds only this list for them."""
    try:
        d = json.loads(_locations_file().read_text())
        return {k: v for k, v in d.items() if isinstance(v, str)}
    except (OSError, ValueError):
        return {}


def _save_locations(loc: dict):
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    _locations_file().write_text(json.dumps(loc, indent=1))


def _unique_id(base: str, folder: Path | None) -> str:
    """A project id that is free in the workspace/registry and (for a chosen folder) has a free sub-folder."""
    taken = set(_locations()) | ({d.name for d in WORKSPACE.iterdir()} if WORKSPACE.exists() else set())
    pid, i = base, 2
    while pid in taken or (folder is not None and (folder / pid).exists()):
        pid, i = f"{base}-{i}", i + 1
    return pid


def list_projects(include_hidden=False) -> list:
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    out = []
    loc = _locations()
    dirs = sorted(WORKSPACE.iterdir()) + [Path(v) for v in loc.values()]
    for d in dirs:
        pj = d / "project.json"
        if pj.exists():
            try:
                data = json.loads(pj.read_text())
                if data.get("hidden") and not include_hidden:
                    continue  # e.g. mesh-check twins
                out.append({"id": d.name, "name": data.get("name", d.name), "study": data.get("study"),
                            "updated": data.get("updated"), "path": str(d)})
            except Exception:  # noqa: BLE001
                pass
    return sorted(out, key=lambda p: p.get("updated") or "", reverse=True)


_OPEN: dict[str, "Project"] = {}
_LOCK = threading.RLock()


_PID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,80}$")


def _project_dir(pid: str) -> Path:
    if not isinstance(pid, str) or not _PID_RE.match(pid):
        raise KeyError(f"Invalid project id '{pid}'")
    ext = _locations().get(pid)
    d = Path(ext).resolve() if ext else (WORKSPACE / pid).resolve()
    if not ext and d.parent != WORKSPACE.resolve():
        raise KeyError(f"No project '{pid}'")
    if not (d / "project.json").exists():
        raise KeyError(f"No project '{pid}'")
    return d


def open_project(pid: str) -> "Project":
    with _LOCK:
        d = _project_dir(pid)
        pr = _OPEN.get(pid)
        if pr is None:
            pr = _OPEN[pid] = Project(d)
        else:
            pr.reload_if_changed()  # another process (MCP server) may have edited it
        return pr


def check_folder(location: str) -> Path:
    """A folder to save projects in: absolute, created if missing, writable."""
    f = Path(str(location)).expanduser()
    if not f.is_absolute():
        raise ValueError("Give the full path of the folder (for example C:\\Simulations)")
    f = f.resolve()
    try:
        f.mkdir(parents=True, exist_ok=True)
        probe = f / ".simwb_write_test"
        probe.write_text("x")
        probe.unlink()
    except OSError as e:
        raise ValueError(f"Cannot save in {f}: {e.strerror or e}") from None
    return f


CLAUDE_MARK = "<!-- written by the workbench: rewritten whenever the study is opened (edit CLAUDE.local.md for your own notes) -->"


def write_claude_files(d: Path, port: int | None = None) -> None:
    """CLAUDE.md and .mcp.json in a study folder, so that Claude Code started in this folder knows which study it is
    in, where its files are, and can drive it through the workbench's MCP server (the running GUI is used when it
    is up: the same jobs, the same view). Never raises: the study works without them."""
    try:
        data = json.loads((d / "project.json").read_text())
        port = port or int(os.environ.get("SIMWB_PORT", "8765"))
        label = studies.study(data.get("study", "")).get("label", data.get("study", ""))
        (d / "CLAUDE.md").write_text(
            f"# Simulation study: {data.get('name', d.name)}\n\n{CLAUDE_MARK}\n\n"
            f"- Type: {label} (`{data.get('study')}`)\n- Folder (this one): `{d}`\n"
            f"- Made with the Mechanical Simulation Workbench (repository `{ROOT}`), a local web app at "
            f"http://127.0.0.1:{port}\n\n"
            "## What is where\n"
            "- `project.json`: the whole setup (parts, materials, boundary conditions, settings, mesh, solve status)\n"
            "- `geometry/`: the imported CAD and the surface/volume mesh files\n"
            "- `case/`: the solver case (OpenFOAM studies) - written by the workbench for each run\n"
            "- `results/`: the results (`result.npz`), `videos/`: saved MP4 files\n\n"
            "## Working in this folder\n"
            "- Change the study through the workbench tools (the `simwb` MCP server from `.mcp.json`: "
            "approve it when Claude Code asks), not by editing `project.json`, `case/` or `results/` by hand: "
            "the workbench owns them, above all while a job is running.\n"
            "- Start with `get_project_state`. Units: mm for geometry, degC, W.\n"
            "- When the desktop window (`python -m simwb.qt`) is running you can see and drive it: `ui_state` (what it shows), "
            "`ui_screenshot`, `ui_do` (any operation), `ui_widget`, `ui_log` (errors and warnings), `ui_wait`; `ui_eval` too when it "
            "was started with `--debug-api`.\n"
            "- Before starting a solve check that no other job is running and that the machine has free memory: "
            "large CFD runs use most of it.\n"
            "- Put your own notes in `CLAUDE.local.md` (this file is rewritten).\n", encoding="utf-8")
        mcp = d / ".mcp.json"
        try:
            cfg = json.loads(mcp.read_text())
        except (OSError, ValueError):
            cfg = {}
        cfg.setdefault("mcpServers", {})["simwb"] = {
            "command": sys.executable, "args": ["-m", "simwb.mcp_server"],
            "env": {"PYTHONPATH": str(ROOT), "SIMWB_PORT": str(port), "SIMWB_HOME": str(WORKSPACE)}}
        mcp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def create_project(name: str, study: str, location: str | None = None) -> "Project":
    """location: a folder to save the project in (a sub-folder named after the study is made there); None = the
    workspace."""
    studies.study(study)
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    folder = check_folder(location) if location and Path(location).resolve() != WORKSPACE.resolve() else None
    pid = _unique_id(slug(name), folder)
    d = (folder or WORKSPACE) / pid
    d.mkdir(parents=True)
    if folder is not None:
        _save_locations({**_locations(), pid: str(d)})
    data = {
        "name": name, "study": study, "created": _now(), "updated": _now(),
        "geometry": {"status": "none"},
        "domain": {"enclosure_mode": "body"} if study == "cht_enclosure" else {},
        "bodies": {}, "bcs": [], "rotors": [],
        "settings": studies.default_settings(study),
        # OpenFOAM studies start on Coarse (a first run in minutes, not hours); the built-in FEM is instant
        "mesh": {"preset": {"heat_conduction": "medium", "vibration_modal": "medium", "static_structural": "medium",
                            "aeroelastic_flutter": "medium",
                            "cht_enclosure": "quick"}.get(study, "coarse"),
                 "refinements": [], "status": "none"},
        "solve": {"status": "none"},
    }
    (d / "project.json").write_text(json.dumps(data, indent=1))
    write_claude_files(d)
    with _LOCK:
        _OPEN[pid] = Project(d)
    return _OPEN[pid]


def duplicate_project(pid: str, name: str | None = None) -> "Project":
    """A copy of a study next to it: geometry, setup and mesh (a valid mesh is kept, so it can be solved at once),
    without the solver case, results and videos. For variants (another speed, a transient run of a steady setup)."""
    src = open_project(pid)
    if src.busy():
        raise RuntimeError("Please wait: a job is still running for this project (or cancel it).")
    name = (name or f"{src.data['name']} (copy)").strip()[:120]
    folder = src.dir.parent
    pid2 = _unique_id(slug(name), None if folder.resolve() == WORKSPACE.resolve() else folder)
    d = folder / pid2
    shutil.copytree(src.dir, d, ignore=shutil.ignore_patterns("case", "results", "videos", "*.tmp", "checks", "_*_job.json"))
    data = json.loads((d / "project.json").read_text())
    data.update(name=name, created=_now(), updated=_now(), solve={"status": "none"})
    for k in ("mesh_check", "model_check", "hidden", "edited_during_run"):
        data.pop(k, None)
    (d / "project.json").write_text(json.dumps(data, indent=1))
    if folder.resolve() != WORKSPACE.resolve():
        _save_locations({**_locations(), pid2: str(d)})
    write_claude_files(d)
    with _LOCK:
        _OPEN[pid2] = Project(d)
    return _OPEN[pid2]


# files that belong to one machine or one run and are not part of a saved case
_CASE_SKIP = ("*.tmp", "checks", "_*_job.json", ".mcp.json", "CLAUDE.md")


def _register(pid: str, d: Path):
    """Remember a project folder outside the workspace under its id."""
    if d.parent.resolve() != WORKSPACE.resolve():
        _save_locations({**_locations(), pid: str(d)})


def save_project_as(pid: str, location: str, name: str | None = None, keep_results: bool = True) -> "Project":
    """Save a study as a new one in another folder ('Save as'): setup, geometry, mesh and - unless keep_results is False -
    the solver case, results and videos too. The copy becomes a study of its own (a sub-folder named after it is made in
    location); the original is not touched."""
    src = open_project(pid)
    if src.busy():
        raise RuntimeError("Please wait: a job is still running for this project (or cancel it).")
    src.save(quiet=True)
    name = (name or src.data["name"]).strip()[:120] or src.data["name"]
    folder = check_folder(location)
    pid2 = _unique_id(slug(name), folder)
    d = folder / pid2
    skip = _CASE_SKIP + (() if keep_results else ("case", "results", "videos"))
    shutil.copytree(src.dir, d, ignore=shutil.ignore_patterns(*skip))
    data = json.loads((d / "project.json").read_text())
    data.update(name=name, created=_now(), updated=_now())
    if not keep_results:
        data["solve"] = {"status": "none"}
    for k in ("hidden", "edited_during_run"):
        data.pop(k, None)
    (d / "project.json").write_text(json.dumps(data, indent=1))
    _register(pid2, d)
    write_claude_files(d)
    with _LOCK:
        _OPEN[pid2] = Project(d)
    return _OPEN[pid2]


def export_project(pid: str, zip_path: str, keep_results: bool = True) -> Path:
    """The whole study in ONE file (a .zip with project.json at its top): to send, archive or open on another computer
    with import_project. keep_results False leaves out the solver case, results and videos (the file is much smaller)."""
    import fnmatch
    import zipfile

    src = open_project(pid)
    if src.busy():
        raise RuntimeError("Please wait: a job is still running for this project (or cancel it).")
    src.save(quiet=True)
    out = Path(str(zip_path)).expanduser()
    if not out.is_absolute():
        raise ValueError("Give the full path of the file (for example C:\\Simulations\\bracket.simwb.zip)")
    out.parent.mkdir(parents=True, exist_ok=True)
    skip = _CASE_SKIP + (() if keep_results else ("case", "results", "videos"))
    tmp = out.with_name(out.name + ".part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(src.dir.rglob("*")):
            rel = f.relative_to(src.dir)
            if f.is_dir() or any(fnmatch.fnmatch(part, pat) for part in rel.parts for pat in skip):
                continue
            z.write(f, rel.as_posix())
    tmp.replace(out)
    return out


def import_project(zip_path: str, location: str | None = None, name: str | None = None) -> "Project":
    """Open a study saved with export_project: unpacked into a new folder (in location, or the workspace), as a study of its
    own. Refuses anything that is not such a file, or whose entries would land outside the new folder."""
    import zipfile

    zp = Path(str(zip_path)).expanduser()
    try:
        z = zipfile.ZipFile(zp)
    except (OSError, zipfile.BadZipFile):
        raise ValueError(f"{zp.name} is not a saved study (not a zip file)") from None
    with z:
        names = z.namelist()
        if "project.json" not in names:
            raise ValueError(f"{zp.name} is not a saved study (no project.json at its top)")
        try:
            data = json.loads(z.read("project.json"))
            studies.study(data.get("study", ""))
        except (ValueError, KeyError):
            raise ValueError(f"{zp.name} is not a saved study (project.json is unreadable)") from None
        WORKSPACE.mkdir(parents=True, exist_ok=True)
        folder = check_folder(location) if location and Path(location).resolve() != WORKSPACE.resolve() else None
        title = (name or data.get("name") or zp.stem).strip()[:120]
        pid = _unique_id(slug(title), folder)
        d = (folder or WORKSPACE) / pid
        d.mkdir(parents=True)
        try:
            root = d.resolve()
            for info in z.infolist():
                target = (d / info.filename).resolve()
                if root != target and root not in target.parents:  # '..' or an absolute path in the file
                    raise ValueError(f"{zp.name} holds an entry outside the study folder: {info.filename}")
                z.extract(info, d)
        except Exception:
            shutil.rmtree(d, ignore_errors=True)
            raise
    data = json.loads((d / "project.json").read_text())
    data.update(name=title, updated=_now())
    for k in ("hidden", "edited_during_run"):
        data.pop(k, None)
    (d / "project.json").write_text(json.dumps(data, indent=1))
    _register(pid, d)
    write_claude_files(d)
    with _LOCK:
        _OPEN[pid] = Project(d)
    return _OPEN[pid]


def open_project_folder(path: str) -> "Project":
    """Add an existing project folder (one with a project.json) from anywhere on the disk to the project list."""
    d = Path(str(path)).expanduser().resolve()
    if not (d / "project.json").exists():
        raise ValueError(f"{d} is not a project folder (no project.json in it)")
    if d.parent == WORKSPACE.resolve():
        write_claude_files(d)
        return open_project(d.name)
    for k, v in _locations().items():
        if Path(v).resolve() == d:
            write_claude_files(d)
            return open_project(k)
    pid = _unique_id(slug(d.name), None)
    _save_locations({**_locations(), pid: str(d)})
    write_claude_files(d)
    return open_project(pid)


def delete_project(pid: str):
    d = _project_dir(pid)
    if any(j.status == "running" for j in JOBS.jobs.values() if j.project == pid):
        raise ValueError("Stop the running job before deleting this project")
    with _LOCK:
        _OPEN.pop(pid, None)
        _OPEN.pop(f"{pid}-meshcheck", None)
    shutil.rmtree(d)
    loc = _locations()
    if loc.pop(pid, None) is not None:
        _save_locations(loc)
    shutil.rmtree(WORKSPACE / ".checks" / pid, ignore_errors=True)


class Project:
    def __init__(self, path: Path):
        self.dir = Path(path)
        self.id = self.dir.name
        self._lock = threading.RLock()
        self._load()
        self._repair_stale_status()
        self._refresh_gci()

    def _refresh_gci(self):
        mc = self.data.get("mesh_check") or {}
        if not mc.get("to2") or mc.get("gci_rules") == GCI_RULES or mc.get("running"):
            return
        a = (self.data.get("solve") or {}).get("summary") or {}
        for r in mc.get("rows", []):
            if r.get("fine2") is None:
                continue
            ref = (a.get("inlet_T", a.get("T_min", 0.0)) or 0.0) if r["unit"] == "°C" else 0.0
            for k in ("gci_percent", "order", "extrapolated", "convergence_type", "order_limited_to", "gci_basis"):
                r.pop(k, None)
            r.update(_gci(r["coarse"] - ref, r["fine"] - ref, r["fine2"] - ref, mc["cells_from"], mc["cells_to"],
                          mc["cells_to2"], 2.0))
            if r.get("extrapolated") is not None:
                r["extrapolated"] += ref
        head = _three_level_verdict(mc["rows"], a)
        rest = mc.get("verdict", "").split(". ", 1)  # keep the caveats that followed the old three-level sentence
        mc["verdict"] = (head + ((" " + rest[1].split("discretisation uncertainty.", 1)[-1].strip())
                                 if len(rest) > 1 and "discretisation uncertainty." in rest[1] else "")).strip()
        mc["gci_rules"] = GCI_RULES
        mc["note_recomputed"] = "GCI recomputed from the stored values with the current rules."
        try:
            self.save(quiet=True)
        except Exception:  # noqa: BLE001 - read-only workspace: keep the refreshed values in memory
            pass

    def _load(self):
        pj = self.dir / "project.json"
        self.data = json.loads(pj.read_text())
        try:  # settings added to a study after the project was made: their defaults
            self.data["settings"] = {**studies.default_settings(self.data["study"]), **self.data.get("settings", {})}
        except Exception:  # noqa: BLE001
            pass
        self._mtime = pj.stat().st_mtime
        self._faces = None
        self._bodies = None
        self._results = None

    def reload_if_changed(self):
        pj = self.dir / "project.json"
        try:
            if pj.stat().st_mtime != self._mtime and not self.busy():
                self._load()
        except OSError:
            pass

    def busy(self) -> bool:
        return any(j.status == "running" for j in JOBS.jobs.values() if j.project == self.id)

    def _repair_stale_status(self):
        """Statuses left behind by a crash / server restart."""
        if self.busy():
            return
        changed = False
        if self.data["geometry"].get("status") == "importing":
            self.data["geometry"]["status"] = "ready" if (self.geo_dir / "faces.json").exists() else "error"
            changed = True
        if self.data["mesh"].get("status") == "meshing":
            self.data["mesh"]["status"] = "stale" if (self.mesh_dir / "mesh.npz").exists() else "none"
            changed = True
        if self.data["solve"].get("status") == "running":
            self.data["solve"]["status"] = "interrupted"
            changed = True
        if changed:
            self.save(quiet=True)

    # ------------------------------------------------------------------ basics
    @property
    def study(self) -> str:
        return self.data["study"]

    @property
    def spec(self) -> dict:
        return studies.study(self.study)

    @property
    def geo_dir(self):
        return self.dir / "geometry"

    @property
    def mesh_dir(self):
        return self.dir / "mesh"

    @property
    def case_dir(self):
        return self.dir / "case"

    @property
    def results_dir(self):
        return self.dir / "results"

    def save(self, quiet=False):
        with self._lock:
            self.data["updated"] = _now()
            try:
                text = json.dumps(self.data, indent=1, allow_nan=False)
            except ValueError:
                self._load()  # drop the bad in-memory change
                raise ValueError("Invalid number (NaN or infinity) in the project data - change not saved")
            tmp = self.dir / "project.json.tmp"
            tmp.write_text(text)
            tmp.replace(self.dir / "project.json")
            self._mtime = (self.dir / "project.json").stat().st_mtime
        if not quiet:
            JOBS.emit({"type": "project_changed", "project": self.id})

    def faces(self) -> list:
        if self._faces is None:
            p = self.geo_dir / "faces.json"
            self._faces = json.loads(p.read_text()) if p.exists() else []
        return self._faces

    def bodies(self) -> list:
        if self._bodies is None:
            p = self.geo_dir / "bodies.json"
            self._bodies = json.loads(p.read_text()) if p.exists() else []
        return self._bodies

    def face_map(self):
        return {f["id"]: f for f in self.faces()}

    def _remember_previous(self):
        """Keep the key numbers of the last result (for before/after comparisons) and expire the mesh check."""
        old = self.data.get("solve", {})
        if old.get("summary") and old.get("finished"):
            self.data["previous_result"] = {"finished": old.get("finished"), "values": {
                k: g(old["summary"]) for k, _, g in self.KEY_OUTPUTS.get(self.study, [])}}
        for k in ("mesh_check", "model_check"):
            if self.data.get(k) and not self.data[k].get("running"):
                self.data[k]["stale"] = True

    def mark_stale(self, what=("mesh", "solve")):
        if self.busy():
            self.data["edited_during_run"] = True  # the running job's results will not match the setup
        for k in ("mesh_check", "model_check"):
            if self.data.get(k):
                self.data[k]["stale"] = True
        if "mesh" in what and self.data["mesh"].get("status") == "ready":
            self.data["mesh"]["status"] = "stale"
        if "solve" in what and self.data["solve"].get("status") in ("done",):
            self.data["solve"]["status"] = "stale"
        elif "solve" in what and self.data["solve"].get("status") in ("interrupted", "failed"):
            self.data["solve"]["setup_changed"] = True

    def state(self, include_faces=False) -> dict:
        d = dict(self.data)
        d["id"] = self.id
        d["path"] = str(self.dir)  # where the project is saved
        d["study_spec"] = self.spec
        d["body_list"] = [self._body_view(b) for b in self.bodies()]
        d["face_count"] = len(self.faces())
        if self.data["geometry"].get("status") == "ready":
            d["geometry"] = dict(d["geometry"], pivot=self._parts_center().tolist())  # (what a turn pivots on)
        if self.study == "external_aero":
            d["flow_dir"] = self._flow_dir().tolist()  # free-stream direction (drawn on the air volume)
            d["hex_unsupported"] = self.hex_unsupported()  # why the hex-dominant mesh cannot be used (None = it can)
        d["edits"] = self.cad_edits()
        d["checks"] = self.check()
        jobs = {}
        for kind in ("prepare", "mesh", "solve", "check"):
            j = JOBS.latest(self.id, kind)
            if j:
                jobs[kind] = j.to_dict(log_tail=0)
        d["jobs"] = jobs
        if include_faces:
            d["faces"] = self.faces()
        return d

    def _body_view(self, b):
        cfg = self.data["bodies"].get(str(b["id"]), {})
        mat = cfg.get("material")
        return {
            "id": b["id"], "name": cfg.get("name") or b["name"], "role": b["role"],
            "material": mat, "material_label": (materials.SOLIDS.get(mat) or {}).get("label"),
            "material_is_default": bool(cfg.get("material_is_default")),
            "heat_W": cfg.get("heat_W", 0.0), "volume_cm3": b["volume"] * 1e6, "bbox": b["bbox"],
            "centroid": b["centroid"],
        }

    def summary_text(self) -> str:
        """Compact plain-text description of the project for the LLM."""
        s = self.state()
        lines = [f"Project '{s['name']}' (id {self.id}) - study: {self.spec['label']} [{self.study}]",
                 f"Geometry: {s['geometry'].get('status')} {s['geometry'].get('original_name', '')}"]
        if self.bodies():
            lines.append("Bodies:")
            for b in s["body_list"]:
                bb = b["bbox"]
                size = [round((bb[i + 3] - bb[i]) * 1000, 1) for i in range(3)]
                extra = (f" material={b['material']}" + (f" heat={b['heat_W']} W" if self.spec["body_params"] else "")
                         if b["role"] == "solid" else "")
                lines.append(f"  - id {b['id']} '{b['name']}' role={b['role']} size(mm)={size}{extra}")
        if self.study == "external_aero" and self.parts():  # (a flow study's bodies are the air; the solids are its parts)
            lines.append("Parts (the CAD solids; their names are what edit_geometry takes):")
            for b in self.parts():
                lines.append(f"  - '{b['name']}' size(mm)={b['size_mm']} volume={b['volume_cm3']} cm3")
        lines.append(f"Faces: {len(self.faces())} (use find_faces to locate them)")
        for e in self.cad_edits():
            lines.append(f"CAD edit {e['index']}: {e['text']}")
        for pl in self.data.get("symmetry") or []:
            n = np.asarray(pl["normal"], float)
            a = int(np.argmax(np.abs(n)))
            where = (f"normal to {'xyz'[a]} at {pl['point'][a] * 1e3:.1f} mm, keeps the {'+' if n[a] > 0 else '-'}"
                     f"{'xyz'[a]} side" if abs(n[a]) > 0.9999 else f"point {pl['point']} normal {pl['normal']}")
            lines.append(f"Symmetry plane '{pl['name']}': {where} (the other side is removed; the results can "
                         "show the mirror image)")
        lines.append("Boundary conditions:")
        for bc in self.data["bcs"]:
            lines.append(f"  - '{bc['name']}' {bc['type']} faces={bc['faces']} params={bc['params']}")
        if not self.data["bcs"]:
            lines.append("  (none yet; unassigned faces use: " + self.spec["default_face_bc"]["label"] + ")")
        lines.append(f"Settings: {self.data['settings']}")
        if self.study == "external_aero":
            lines.append(f"Rotors: {self.data['rotors']}")
        m = self.data["mesh"]
        lines.append(f"Mesh: {m.get('status')} preset={m.get('preset')} "
                     f"cells={m.get('stats', {}).get('cells')}")
        lines.append(f"Solve: {self.data['solve'].get('status')}")
        chk = [f"[{c['level']}] {c['message']}" for c in s["checks"] if c["level"] != "ok"]
        if chk:
            lines.append("Open issues:\n  " + "\n  ".join(chk))
        return "\n".join(lines)

    # ------------------------------------------------------------------ geometry
    def import_step(self, src: str | Path, original_name: str | None = None, scale: float = 1.0) -> Job:
        src = Path(src)
        fmt = {".step": "step", ".stp": "step", ".stl": "stl", ".obj": "obj"}.get(src.suffix.lower())
        if not fmt:
            raise ValueError("Please provide a STEP (.step / .stp), STL (.stl) or OBJ (.obj) file")
        if not src.is_file():
            raise ValueError(f"STEP file not found: {src}")
        scale = studies.finite(scale, "Scale")
        if not 1e-6 < scale <= 1e6:
            raise ValueError("Scale must be a positive factor (e.g. 0.001 for a model drawn in micrometres -> mm, "
                             "25.4 for inches)")
        running = JOBS.latest(self.id)
        if running and running.status == "running":
            # refuse BEFORE touching the files: a running mesh/solve must not lose its geometry
            what = {"prepare": "geometry import", "mesh": "mesh generation", "solve": "solver run",
                    "check": running.extra.get("check_kind", "check")}.get(running.kind, running.kind)
            raise RuntimeError(f"Please wait: a {what} is still running for this project (or cancel it).")
        self.geo_dir.mkdir(parents=True, exist_ok=True)
        # the new file only replaces source.step (and the geometry data) once gmsh has read it successfully
        incoming = self.geo_dir / "incoming.step"
        shutil.copy(src, incoming)
        # a bundled example arrives with the settings it needs to run (only the ones the user has not set yet)
        if src.parent.resolve() == EXAMPLES.resolve():
            for k, v in studies.EXAMPLE_SETTINGS.get(src.name, {}).items():
                if k in self.data["settings"] and not self.data["settings"][k]:
                    self.data["settings"][k] = v
        self.data["geometry"]["pending"] = {"original_name": original_name or src.name, "scale": scale, "format": fmt}
        if src.parent.resolve() == EXAMPLES.resolve():
            self.data["geometry"]["pending"]["example"] = src.name  # (the example's own materials: studies.EXAMPLE_MATERIALS)
        if self.data["geometry"].get("status") in (None, "none", "error"):
            self.data["geometry"]["status"] = "importing"
        self.save()
        return self.prepare_geometry(new_file=True)

    GEOMETRY_FILES = ("domain.brep", "faces.json", "bodies.json", "domain_info.json", "discrete.json", "edges.json")

    # ------------------------------------------------------------------ CAD edits
    def parts(self) -> list:
        """The solids of the model as the CAD edits name them: [{name, size_mm, volume_cm3}] (a flow study's own body
        list holds the air; the solids are in the geometry info)."""
        out = []
        for b in (self.data["geometry"].get("info") or {}).get("source_bodies") or []:
            bb = b.get("bbox") or [0] * 6
            out.append({"name": b["name"], "size_mm": [round((bb[i + 3] - bb[i]) * 1000, 1) for i in range(3)],
                        "volume_cm3": round(float(b.get("volume") or 0) * 1e6, 3)})
        return out

    def cad_edits(self) -> list:
        """The CAD edits of the geometry, oldest first: [{index (from 1), op, text, edit}]."""
        return [{"index": i + 1, "op": e["op"], "text": cad_edit.describe(e), "edit": e}
                for i, e in enumerate(self.data["geometry"].get("edits") or [])]

    def edit_geometry(self, action: str, op: dict | None = None, index=None) -> Job:
        """Change the CAD edits and rebuild the geometry from the imported file with them. action: 'add' (op: the
        edit; index: where in the list, default the end), 'update' (index and the parameters to change, merged into the
        edit), 'remove' (index), 'clear'. The new list is kept only if the geometry builds with it."""
        if self.busy():
            raise RuntimeError("Please wait: a job is still running for this project (or cancel it).")
        if not (self.geo_dir / "source.step").exists() or self.data["geometry"].get("status") != "ready":
            raise ValueError("Import a geometry first")
        cur = [dict(e) for e in (self.data["geometry"].get("edits") or [])]

        def at(i, allow_end=False):
            n = len(cur) + (1 if allow_end else 0)
            if i is None and allow_end:
                return len(cur)
            try:
                i = int(i)
            except (TypeError, ValueError):
                raise ValueError("index must be the number of an edit (see the list of CAD edits)") from None
            if not 1 <= i <= n:
                raise ValueError(f"There is no CAD edit {i}: " + (f"the list has {len(cur)}" if cur else "the list is empty"))
            return i - 1
        if action == "add":
            if len(cur) >= cad_edit.MAX_EDITS:
                raise ValueError(f"At most {cad_edit.MAX_EDITS} CAD edits")
            cur.insert(at(index, True) if index is not None else len(cur), cad_edit.validate(op or {}))
        elif action == "update":
            i = at(index)
            cur[i] = cad_edit.validate({**cur[i], **(op or {}), "op": cur[i]["op"]})
        elif action == "remove":
            cur.pop(at(index))
        elif action == "clear":
            if not cur:
                raise ValueError("There are no CAD edits")
            cur = []
        else:
            raise ValueError("action must be add, update, remove or clear")
        return self.prepare_geometry(edits=cur)

    def find_edges(self, body=None, near=None, radius_mm=None, parallel=None, length_mm=None, edge_type=None,
                   in_box=None, limit=60) -> dict:
        """The edges of the model (not of the air volume), for picking what a fillet / chamfer goes on. Coordinates mm."""
        p = self.geo_dir / "edges.json"
        if not p.exists():
            raise ValueError("No edge list yet: (re)build the geometry first")
        es = json.loads(p.read_text())
        fm = self.face_map()
        bnames = {b["id"]: b["name"] for b in self.bodies()}
        out = []
        for e in es:
            # (a flow study's faces name their part; in the others the body is the volume the face belongs to)
            parts = {(fm.get(f) or {}).get("part") for f in e["faces"]} - {None}
            vols = {bnames[v] for f in e["faces"] for v in (fm.get(f) or {}).get("volumes", []) if v in bnames}
            out.append(dict(e, bodies=sorted(parts or vols)))
        if body:
            if body not in {n for e in out for n in e["bodies"]}:
                raise ValueError(f"No body '{body}'. Bodies: " + ", ".join(sorted({n for e in out for n in e["bodies"]})))
            out = [e for e in out if body in e["bodies"]]
        if edge_type:
            t = str(edge_type).lower()
            out = [e for e in out if t in e["type"].lower()]
        if parallel is not None:
            if isinstance(parallel, str):
                if parallel.strip().lower().lstrip("+-") not in ("x", "y", "z"):
                    raise ValueError("parallel must be 'x', 'y', 'z' or a direction [dx, dy, dz]")
                parallel = [1.0 if c == parallel.strip().lower().lstrip("+-") else 0.0 for c in "xyz"]
            v = np.array(parallel, float)
            v /= np.linalg.norm(v)
            out = [e for e in out if e["direction"] is not None and abs(float(np.dot(e["direction"], v))) > math.cos(math.radians(2))]
        if length_mm:
            lo, hi = (list(length_mm) + [None])[:2]
            out = [e for e in out if (lo is None or e["length_mm"] >= lo) and (hi is None or e["length_mm"] <= hi)]
        if in_box:
            b = [float(c) for c in in_box]
            out = [e for e in out if all(b[i] <= e["center_mm"][i] <= b[i + 3] for i in range(3))]
        if near is not None:
            c = np.asarray(near, float)
            for e in out:
                e["distance_mm"] = round(float(np.linalg.norm(np.asarray(e["center_mm"]) - c)), 2)
            out = [e for e in out if radius_mm is None or e["distance_mm"] <= float(radius_mm)]
            out.sort(key=lambda e: e["distance_mm"])
        n = len(out)
        return {"count": n, "edges": out[:int(limit)], "truncated": n > int(limit)}

    def rotate_geometry(self, axis: str | None = None, degrees: float = 90.0, reset: bool = False,
                        matrix=None, snap_zones: bool = False) -> Job:
        """Turn the imported parts about the origin (x, y or z axis, right-handed), e.g. to align the model with
        the flow axes. Rotations add up; reset returns to the file's own orientation. The geometry is rebuilt
        from the imported file; boundary conditions, rotors and moving zones turn with it."""
        if self.busy():
            raise RuntimeError("Please wait: a job is still running for this project (or cancel it).")
        if not (self.geo_dir / "source.step").exists():
            raise ValueError("Import a geometry first")
        old = np.asarray(self.data["geometry"].get("rotation") or np.eye(3), float)
        old_t = np.asarray(self.data["geometry"].get("translation") or np.zeros(3), float)
        if reset:
            new = np.eye(3)
        elif matrix is not None:  # a turn composed in the 3D view (live preview), applied in one rebuild
            M = np.asarray([[studies.finite(x, "Rotation") for x in row] for row in matrix], float)
            if M.shape != (3, 3) or not np.allclose(M @ M.T, np.eye(3), atol=1e-6) or np.linalg.det(M) < 0:
                raise ValueError("The rotation must be a proper 3x3 rotation matrix")
            new = M @ old
        else:
            if axis not in ("x", "y", "z"):
                raise ValueError("axis must be x, y or z")
            t = math.radians(studies.finite(degrees, "Angle"))
            c, s_ = round(math.cos(t), 12), round(math.sin(t), 12)  # (exact for multiples of 90 degrees)
            i, j = {"x": (1, 2), "y": (2, 0), "z": (0, 1)}[axis]
            step = np.eye(3)
            step[i, i], step[i, j], step[j, i], step[j, j] = c, -s_, s_, c
            new = step @ old
        if np.allclose(new, old):
            raise ValueError("The orientation would not change")
        D = new @ old.T
        if reset:  # back to the file's own place
            new_t = np.zeros(3)
        else:  # a turn about the middle of the parts: they stay where they are (x' = D (x - c) + c)
            c = self._parts_center()
            new_t = D @ old_t + (c - D @ c)
        t_step = new_t - D @ old_t
        back = np.allclose(new, np.eye(3)) and np.allclose(new_t, 0)
        return self.prepare_geometry(turn=D, turn_t=t_step, rotation=None if back else new.tolist(),
                                     translation=None if back else new_t.tolist(), snap_zones=snap_zones)

    def align_info(self, face_id) -> dict:
        """Which way a flat face of the model points (out of the part), for lining it up with a coordinate plane."""
        fm = self.face_map()
        f = fm.get(int(face_id))
        if f is None:
            raise ValueError(f"Face {face_id} does not exist")
        if "Plane" not in str(f.get("surface_type", "")):
            raise ValueError(f"Face {face_id} is not flat: select a flat face to align")
        if f.get("kind") in ("farfield", "enclosure"):
            raise ValueError(f"Face {face_id} belongs to the air volume, not to the model: select a face of a part")
        if f.get("interface"):
            raise ValueError(f"Face {face_id} is a contact face between two parts: select an outer face")
        roles = {b["id"]: b["role"] for b in self.bodies()}
        res = orient.outward_normal(f, fm, solid=roles.get((f.get("volumes") or [None])[0]) == "solid")
        if res is None:
            raise ValueError(f"Cannot tell which way face {face_id} points")
        n, pt = res
        return {"face": int(face_id), "normal": n.tolist(), "point": pt.tolist(), "area_m2": f["area"],
                "nearest": orient.nearest_axis(n), "kind": "face"}

    def axis_info(self, face_id, point=None) -> dict:
        """Centre and axis of a round feature for placing a rotating zone or a rotor disc: a cylindrical face (a
        shaft, a hub bore) or the circular edge of a face nearest to `point` (the click). The axis sign is made
        positive along its dominant coordinate; `nearest` / `angle_deg` say how far it is from x, y or z."""
        f = self.face_map().get(int(face_id))
        if f is None:
            raise ValueError(f"Face {face_id} does not exist")
        res = axisfit.pick_axis(f, point)
        a = np.asarray(res["axis"], float)
        a = a / np.linalg.norm(a)
        if a[int(np.argmax(np.abs(a)))] < 0:
            a = -a
        near = orient.nearest_axis(a)
        ang = math.degrees(math.acos(min(1.0, abs(float(a @ np.asarray(studies.AXES[near], float))))))
        return {"face": int(face_id), "kind": res["kind"], "center": res["center"], "axis": a.tolist(),
                "radius": res["radius"], "length": res.get("length"), "nearest": near, "angle_deg": ang}

    def circular_edges(self) -> list:
        """The circular edges of the parts (holes, bosses, shafts, discs), for picking a centre of rotation in the 3D
        view. Edges of the air volume are left out."""
        fs = [f for f in self.faces() if f.get("kind") not in ("farfield", "enclosure")]
        L = (self.data["geometry"].get("info") or {}).get("ref_length") or 1.0
        out = axisfit.circular_edges(fs, L)
        for e in out:
            near = orient.nearest_axis(e["axis"])
            e["nearest"] = near
            e["angle_deg"] = math.degrees(math.acos(min(1.0, abs(float(np.dot(e["axis"], studies.AXES[near]))))))
        return out

    def wall_info(self, face_id) -> dict:
        """A flat wall of the tunnel (the air volume around the parts): which way it faces out of the tunnel."""
        fm = self.face_map()
        f = fm.get(int(face_id))
        if f is None:
            raise ValueError(f"Face {face_id} does not exist")
        if f.get("kind") not in ("farfield", "enclosure"):
            raise ValueError(f"Face {face_id} is not a wall of the air volume (the tunnel): select one of its walls "
                             "(shown translucent)")
        if "Plane" not in str(f.get("surface_type", "")):
            raise ValueError(f"Face {face_id} is not flat: select a flat wall of the tunnel")
        res = orient.outward_normal(f, fm, solid=True)  # (out of the air volume = out of the tunnel)
        if res is None:
            raise ValueError(f"Cannot tell which way face {face_id} faces")
        n, pt = res
        return {"face": int(face_id), "normal": n.tolist(), "point": pt.tolist(), "nearest": orient.nearest_axis(n),
                "role": f.get("domain_role"), "kind": "wall"}

    def ref_info(self, face_id, exclude=None) -> dict:
        """The plane to align to: a wall of the tunnel or another flat face of the model (its normal points out)."""
        if exclude is not None and int(face_id) == int(exclude):
            raise ValueError("Choose a different face as the plane to align to")
        f = self.face_map().get(int(face_id))
        if f is not None and f.get("kind") in ("farfield", "enclosure"):
            return self.wall_info(face_id)
        return self.align_info(face_id)

    def align_face(self, face_id, target: str | None = None, wall=None, away: bool = False) -> Job:
        """Turn the parts (about their middle) so that the flat face lies against a plane: the plane normal to
        `target` ('+x' ... '-z', the face then faces that way), or the plane of another face (`wall`: a wall of the
        tunnel or a flat face of the model): the face then points the same way as that one (a tunnel wall's outward
        normal: the face lies against the wall), or the opposite way when `away` (face to face)."""
        info = self.align_info(face_id)
        if wall is not None:
            w = np.asarray(self.ref_info(wall, exclude=face_id)["normal"], float)
            vec, name = (-w if away else w), f"the plane of face {wall}" + (" (facing away)" if away else "")
        else:
            if target not in orient.AXES:
                raise ValueError(f"The plane's direction must be one of {', '.join(orient.AXES)}")
            vec, name = target, target
        D = orient.align_matrix(info["normal"], vec)
        if np.allclose(D, np.eye(3), atol=1e-9):
            raise ValueError(f"Face {face_id} already faces {name}")
        return self.rotate_geometry(matrix=D.tolist())

    def _turn_setup(self, D: np.ndarray, t=None, snap_zones: bool = False):
        """The parts were moved by x' = D x + t (turned about the origin, then shifted): move what is placed in
        space with them (points move, directions only turn). A rotating zone has to lie along x, y or z: after a turn
        that tips its axis it is dropped, or with snap_zones kept on the nearest axis (the caller re-places its
        centre afterwards, e.g. from a round feature that was just lined up)."""
        t = np.zeros(3) if t is None else np.asarray(t, float)

        def vec(v):  # a direction
            return (D @ np.asarray(v, float)).tolist()

        def pt(v):  # a point
            return (D @ np.asarray(v, float) + t).tolist()

        for bc in self.data["bcs"]:  # (face signatures: the faces are matched where they are now)
            for sg in bc.get("signature") or []:
                sg["centroid"] = pt(sg["centroid"])
            bc.pop("lost", None)  # (their normals and extents are in the old orientation)

        for r in self.data.get("rotors") or []:
            for k, fn in (("center", pt), ("axis", vec)):
                if isinstance(r.get(k), list) and len(r[k]) == 3:
                    r[k] = fn(r[k])
        keep = []
        for z in self.data.get("moving_zones") or []:
            v = D @ np.asarray(studies.AXES[z["axis"]], float)
            k = int(np.argmax(np.abs(v)))
            ax, tipped = ("+" if v[k] > 0 else "-") + "xyz"[k], abs(v[k]) < 0.999
            if tipped and not snap_zones:  # zones are aligned with x, y or z: an oblique turn drops them
                self.data.setdefault("notes", []).append(
                    f"Moving zone {z['name']} was removed: after this rotation its axis is not along x, y or z.")
                continue
            if tipped:
                self.data.setdefault("notes", []).append(
                    f"Moving zone {z['name']} was kept on the {ax[1]} axis after the turn: pick its cylinder or "
                    "circular edge again to put it on the turned part.")
            keep.append(dict(z, center=pt(z["center"]), axis=ax))
        if "moving_zones" in self.data:
            self.data["moving_zones"] = keep

    def prepare_geometry(self, new_file: bool = False, turn=None, rotation=False, symmetry=None, turn_t=None,
                         translation=False, snap_zones: bool = False, edits=None) -> Job:
        """Read the stored file again and rebuild the geometry. symmetry: a new list of symmetry planes to apply
        (kept only if the rebuild succeeds); None = the planes the study has. edits: a new list of CAD edits (kept only
        if the rebuild succeeds); None = the edits the geometry has (a new file has none; a turn moves them with it)."""
        step = self.geo_dir / ("incoming.step" if new_file else "source.step")
        if not step.exists():
            raise ValueError("No STEP file imported yet")
        old_faces = {f["id"]: f for f in self.faces()}
        old_bodies = self.bodies()
        old_planes = [dict(pl) for pl in (self.data.get("symmetry") or [])]
        shift = np.zeros(3) if turn_t is None else np.asarray(turn_t, float)
        if turn is not None:  # the parts will be turned (copies: matched where they will be)
            D = np.asarray(turn, float)
            old_faces = {i: dict(f, centroid=(D @ np.asarray(f["centroid"], float) + shift).tolist())
                         for i, f in old_faces.items()}
            old_bodies = [dict(b, centroid=(D @ np.asarray(b["centroid"], float) + shift).tolist()) for b in old_bodies]
        rotation = self.data["geometry"].get("rotation") if rotation is False else rotation
        translation = self.data["geometry"].get("translation") if translation is False else translation
        planes = [dict(pl) for pl in (self.data.get("symmetry") or [])] if symmetry is None else \
            [dict(pl) for pl in symmetry]
        if turn is not None:  # (the planes are placed in space with the parts)
            planes = [sym.turn(pl, np.asarray(turn, float), shift) for pl in planes]
        pending = self.data["geometry"].get("pending") if new_file else None
        if edits is None:
            edits = [] if new_file else [dict(e) for e in (self.data["geometry"].get("edits") or [])]
            if turn is not None and edits:
                edits = cad_edit.turn(edits, np.asarray(turn, float), shift)
        if edits and (pending or self.data["geometry"]).get("format") in ("stl", "obj"):
            raise ValueError("CAD edits work on STEP files: an STL / OBJ model is a surface mesh and cannot be edited")
        name = (pending or self.data["geometry"]).get("original_name") or "the file"
        stage = self.geo_dir / "staging"  # gmsh writes here; files are moved into place only on success

        grazed: list = []
        old_edits = [dict(e) for e in (self.data["geometry"].get("edits") or [])]

        def work(job: Job):
            shutil.rmtree(stage, ignore_errors=True)
            grazed.clear()
            payload = {
                "study": self.study, "step_file": str(step), "out_dir": str(stage),
                "domain": self._air_dom(),
                "scale": (pending or self.data["geometry"]).get("scale", 1.0),
                "rotation": rotation, "translation": translation, "symmetry": planes, "edits": edits,
                # (the file is stored as source.step whatever its format; STL/OBJ are read as millimetres)
                "format": (pending or self.data["geometry"]).get("format", "step"),
                "flow_dir": self._flow_dir().tolist() if self.study == "external_aero" else [1, 0, 0],
                "up_axis": "xyz".index(self.data["settings"].get("up_axis", "+z")[1])
                if self.study == "external_aero" else None,
            }
            try:
                info = run_gmsh_task(JOBS, job, "prepare", payload, stage, 0, 0.7)
                for _ in range(2):  # a plane that grazes the model: clear the tiny faces it leaves, then cut again
                    moves = sym.grazing_nudges(planes, json.loads((stage / "faces.json").read_text()),
                                               info.get("parts_bbox")) if planes else []
                    if not moves:
                        break
                    for i, t in moves:
                        n = np.asarray(planes[i]["normal"], float)
                        planes[i]["point"] = (np.asarray(planes[i]["point"], float) + n / np.linalg.norm(n) * t).tolist()
                        grazed.append(f"'{planes[i]['name']}' was moved {t * 1e3:.2f} mm into the model: it only just "
                                      "cut the model and left tiny faces (and cells of a few micrometres) at the plane.")
                    JOBS.update(job, 0.6, "Moving the symmetry plane clear of tiny faces")
                    payload["symmetry"] = planes
                    info = run_gmsh_task(JOBS, job, "prepare", payload, stage, 0.6, 0.7)
            except BaseException as e:
                with self._lock:
                    had = (self.geo_dir / "faces.json").exists() and self.data["geometry"].get("info")
                    msg = (str(e) or "Import failed").replace(str(step), name)
                    if "access violation" in msg.lower() or "segmentation" in msg.lower():
                        msg = (f"Could not read {name}: it contains no readable solid geometry. Re-export it from "
                               "your CAD tool as STEP (AP214/AP242) with solid bodies.")
                    if new_file:
                        step.unlink(missing_ok=True)
                        msg = msg.rstrip() + ("" if msg.rstrip().endswith(".") else ".")
                        msg += " The previous geometry was kept." if had else ""
                    self.data["geometry"].pop("pending", None)
                    self.data["geometry"].update(status="ready" if had else "error", error=msg)
                    self.save()
                shutil.rmtree(stage, ignore_errors=True)
                if isinstance(e, Cancelled):
                    raise
                raise RuntimeError(msg) from e
            self.data["geometry"].pop("error", None)
            if self.study == "external_aero":
                # rotors usually extend beyond the airframe: size the air box from airframe + rotor span
                self._faces, self._bodies = None, None
                tmp_info = self.data["geometry"].get("info")
                self.data["geometry"]["info"] = {k: v for k, v in info.items() if k != "bodies"}
                rot = self.data["rotors"] or self.detect_rotors(apply=False)
                saved = self.data["rotors"]
                self.data["rotors"] = rot
                span = self.rotor_span() if rot else 0.0
                self.data["rotors"] = saved
                self.data["geometry"]["info"] = tmp_info
                if span > 1.1 * info["ref_length"]:
                    JOBS.update(job, 0.72, "Enlarging the air box to include the rotors")
                    payload["span"] = span
                    info = run_gmsh_task(JOBS, job, "prepare", payload, stage, 0.72, 1.0)
            with self._lock:
                for fn in self.GEOMETRY_FILES:
                    if (stage / fn).exists():
                        os.replace(stage / fn, self.geo_dir / fn)
                    else:  # e.g. switching from STL (discrete.json) to STEP (domain.brep)
                        (self.geo_dir / fn).unlink(missing_ok=True)
                shutil.rmtree(stage, ignore_errors=True)
                if new_file:
                    os.replace(step, self.geo_dir / "source.step")
                    self.data["geometry"].update(source="source.step", **pending)
                    self.data["geometry"].pop("pending", None)
                self._faces = None
                self._bodies = None
                self.data["geometry"].update(status="ready", info={k: v for k, v in info.items() if k != "bodies"})
                if edits:
                    self.data["geometry"]["edits"] = edits
                else:
                    self.data["geometry"].pop("edits", None)
                mode = (info.get("domain") or {}).get("enclosure_mode")
                if self.study == "cht_enclosure" and mode and mode != self.data["domain"].get("enclosure_mode"):
                    self.data["domain"]["enclosure_mode"] = mode  # e.g. single part: air box built automatically
                if turn is not None:
                    self.data["geometry"]["rotation"] = rotation
                    self.data["geometry"]["translation"] = translation
                    self._turn_setup(np.asarray(turn, float), shift, snap_zones)
                if symmetry is not None or turn is not None or grazed:
                    self.data["symmetry"] = planes
                    if symmetry is not None:
                        self._symmetry_setup_after(planes)
                    for msg in grazed:
                        self.data.setdefault("notes", []).append(msg)
                self._remap_after_geometry(old_faces, old_bodies, allow_cut=turn is None, old_planes=old_planes)
                if edits != old_edits:
                    gone = sum(len(b.get("lost") or []) for b in self.data["bcs"] if not b.get("plane_id"))
                    emptied = [b["name"] for b in self.data["bcs"] if not b.get("plane_id") and not b["faces"]]
                    if gone or emptied:
                        self.data.setdefault("notes", []).append(
                            f"The CAD edit changed faces that had boundary conditions ({gone} face(s) gone"
                            + (f"; no faces left on: {', '.join(emptied)}" if emptied else "")
                            + "): check them, assign the new faces (find_faces) or undo the edit.")
                self.data["mesh"]["status"] = "none" if self.data["mesh"].get("status") == "none" else "stale"
                self.data["solve"]["status"] = "none"
                self._results = None
                self.save()
            return info

        return JOBS.start("prepare", self.id, work)

    def _remap_after_geometry(self, old_faces: dict, old_bodies: list, allow_cut: bool = True, old_planes=()):
        new_faces = self.faces()
        L = self.data["geometry"]["info"]["ref_length"]
        self.data["bcs"] = [b for b in self.data["bcs"] if not b.get("plane_id")]  # (rebuilt below from the planes)

        def exact(of):
            best, bd = None, 1e300
            for nf in new_faces:
                d = math.dist(of["centroid"], nf["centroid"]) / L + abs(of["area"] - nf["area"]) / max(of["area"], 1e-12)
                if d < bd:
                    best, bd = nf, d
            return best["id"] if best and bd < 0.02 else None

        def mirrored(of, plane):
            """The old face as seen in the old symmetry plane's mirror: where it would be on the other side."""
            R, t = sym.reflection(plane)
            bb = np.asarray(of["bbox"], float)
            corners = np.array([[bb[i], bb[j], bb[k]] for i in (0, 3) for j in (1, 4) for k in (2, 5)]) @ R.T + t
            return dict(of, centroid=(R @ np.asarray(of["centroid"], float) + t).tolist(),
                        normal=(R @ np.asarray(of["normal"], float)).tolist(),
                        bbox=[*corners.min(0).tolist(), *corners.max(0).tolist()])

        def match(of):
            m = exact(of)
            if m is not None or not allow_cut:
                return m
            m = match_cut(of)
            if m is None:  # a flipped plane keeps the other side: the same face is the old one's mirror image
                for pl in old_planes:
                    if of.get("bbox") and of.get("normal"):
                        ofr = mirrored(of, pl)
                        m = exact(ofr) or match_cut(ofr)
                        if m is not None:
                            break
            return m

        def match_cut(of):
            """A symmetry plane that cuts a face (or no longer does) changes its area and centre: the new face is the
            old one cut smaller, or the old one regrown - same surface, same side, one inside the other."""
            tol = max(1e-4 * L, 1e-6)
            n0 = np.asarray(of.get("normal") or [0, 0, 0], float)
            if np.linalg.norm(n0) < 1e-9 or not of.get("bbox"):
                return None
            n0 = n0 / np.linalg.norm(n0)
            best, ba = None, -1.0
            for nf in new_faces:
                n1 = np.asarray(nf.get("normal") or [0, 0, 0], float)
                if (np.linalg.norm(n1) < 1e-9 or nf.get("surface_type") != of.get("surface_type")
                        or float(n1 @ n0) / np.linalg.norm(n1) < 0.999
                        or abs(float((np.asarray(nf["centroid"]) - np.asarray(of["centroid"])) @ n0)) > tol):
                    continue
                inside = lambda pt, bb: all(bb[k] - tol <= pt[k] <= bb[k + 3] + tol for k in range(3))  # noqa: E731
                cut = nf["area"] <= of["area"] * 1.001 and inside(nf["centroid"], of["bbox"])
                grown = nf["area"] >= of["area"] * 0.999 and inside(of["centroid"], nf["bbox"])
                if (cut or grown) and nf["area"] > ba:
                    best, ba = nf, nf["area"]
            return best["id"] if best else None

        for bc in self.data["bcs"]:
            # (the old face itself where it is known: its normal and extent also tell a cut face from another one)
            sigs = [old_faces[f] for f in bc["faces"] if f in old_faces] or (bc.get("signature") or [])
            # faces that were lost earlier (cut away by a symmetry plane) come back when the geometry does
            new, lost = [], []
            for sg in sigs + (bc.get("lost") or []):
                m = match(sg)
                if m is not None:
                    new.append(m)
                elif sg.get("bbox") and sg.get("normal"):
                    lost.append({k: sg[k] for k in ("centroid", "area", "normal", "bbox", "surface_type") if k in sg})
            bc["faces"] = sorted(set(new))
            bc["signature"] = [self._sig(f) for f in bc["faces"]]
            if lost:
                bc["lost"] = lost[:200]
            else:
                bc.pop("lost", None)
        # mesh sizing: part sizing goes to the faces of the part with the same name (a new CAD version of the part),
        # face sizing to the matching faces like the boundary conditions; sizing with no faces left is dropped
        kept, lost = [], []
        for r in self.data["mesh"].get("refinements", []):
            if r.get("part"):
                new = [f["id"] for f in new_faces if f.get("part") == r["part"]
                       and f["kind"] not in ("farfield", "enclosure")]
            else:
                new = [m for m in (match(old_faces[f]) for f in r["faces"] if f in old_faces) if m is not None]
            if new:
                kept.append(dict(r, faces=sorted(set(new))))
            else:
                lost.append(r.get("part") or f"{len(r['faces'])} faces")
        self.data["mesh"]["refinements"] = kept
        if lost:
            self.data.setdefault("notes", []).append(
                f"Mesh sizing removed - not found in the new geometry: {', '.join(lost)}. Add it again in the Mesh step.")
        # bodies: carry over config by centroid
        old_cfg = self.data.get("bodies", {})
        new_cfg = {}
        for b in self.bodies():
            if b["role"] != "solid":
                continue
            src = None
            for ob in old_bodies:
                if ob["role"] == "solid" and math.dist(ob["centroid"], b["centroid"]) < 1e-3 * L:
                    src = old_cfg.get(str(ob["id"]))
            if src is None:  # (cut by a symmetry plane: the centre moved; the name stays)
                same = [ob for ob in old_bodies if ob["role"] == "solid" and ob["name"] == b["name"]]
                if len(same) == 1:
                    src = old_cfg.get(str(same[0]["id"]))
            ex_mat = studies.EXAMPLE_MATERIALS.get(self.data["geometry"].get("example"), {}).get(b["name"])
            new_cfg[str(b["id"])] = src or ({"material": ex_mat, "heat_W": 0.0, "name": b["name"]} if ex_mat else
                                            {"material": "aluminium_6061", "heat_W": 0.0, "name": b["name"],
                                             "material_is_default": True})
        self.data["bodies"] = new_cfg
        self._sync_symmetry_bcs()
        if self.study == "external_aero" and not self.data["rotors"] and not self.data.get("symmetry"):
            try:
                self.data["rotors"] = self.detect_rotors(apply=False)
                if self.data["rotors"]:
                    msg = f"{len(self.data['rotors'])} rotors were placed automatically on the motor tops - check them."
                    if not float(self.data["settings"].get("pitch_deg") or 0):  # multirotors fly nose-down
                        self.data["settings"]["pitch_deg"] = 10.0
                        msg += " Pitch set to 10° nose-down (typical forward flight); enter the vehicle mass."
                    self.data.setdefault("notes", []).append(msg)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ symmetry planes
    def _n_rotors_total(self) -> int:
        """All rotors of the vehicle: those modelled and their mirror images."""
        return len(self.data.get("rotors") or []) * sym.mass_factor(self.data.get("symmetry") or [])

    def symmetry_planes(self) -> list:
        return [dict(pl) for pl in (self.data.get("symmetry") or [])]

    def _sync_symmetry_bcs(self):
        """One derived boundary condition per symmetry plane, holding the faces that lie in it."""
        self.data["bcs"] = [b for b in self.data["bcs"] if not b.get("plane_id")]
        by_plane = {}
        for f in self.faces():
            if f.get("symmetry") is not None:
                by_plane.setdefault(int(f["symmetry"]), []).append(f["id"])
        for k, pl in enumerate(self.data.get("symmetry") or []):
            faces = sorted(by_plane.get(k, []))
            self._unassign(faces)
            self.data["bcs"].append({"id": "sym-" + pl["id"], "name": pl["name"], "type": "symmetry", "faces": faces,
                                     "params": {}, "plane_id": pl["id"], "auto": True,
                                     "signature": [self._sig(f) for f in faces]})

    def _parts_center(self):
        """The point the model is turned about: the centre of mass of the parts (volume weighted). It moves with the
        parts and is the same however they are turned, so a turn and its inverse bring the model back exactly (the
        middle of the bounding box of a tilted part is not the same point)."""
        info = self.data["geometry"].get("info") or {}
        src = [b for b in info.get("source_bodies") or [] if b.get("centroid") and b.get("volume")]
        solids = src or [b for b in self.bodies() if b["role"] == "solid"]
        if solids:
            w = np.array([max(b["volume"], 1e-30) for b in solids])
            return (np.array([b["centroid"] for b in solids]) * w[:, None]).sum(0) / w.sum()
        bb = np.asarray(info.get("parts_bbox") or info.get("bbox") or [0, 0, 0, 0, 0, 0], float)
        return (bb[:3] + bb[3:]) / 2

    def _symmetry_issues(self, planes: list) -> list:
        """Rotors and rotating zones that a plane cuts through (a half of a rotor is not a rotor)."""
        out = []
        for pl in planes:
            for r in self.data.get("rotors") or []:
                if sym.side_of(pl, r["center"], r["axis"], r["radius"], r.get("thickness") or 0.0) == "cut":
                    out.append(f"'{pl['name']}' cuts through a rotor: move the plane or flip it so that the rotor is "
                               "wholly on one side")
            for z in self.data.get("moving_zones") or []:
                ax = studies.AXES[z["axis"]] if isinstance(z["axis"], str) else z["axis"]
                if sym.side_of(pl, z["center"], ax, z["radius"], z["length"]) == "cut":
                    out.append(f"'{pl['name']}' cuts through the rotating zone '{z['name']}': a mirror would turn it "
                               "the other way. Move the plane or flip it so that the zone is wholly on one side")
        return out

    def _symmetry_grazing(self, planes: list) -> list:
        """A plane that only just cuts the model (a few percent of its size) leaves a thin slice with micro-edges and
        tiny faces: the mesh gets cells of a few micrometres there and a transient run diverges at that spot."""
        pb = (self.data["geometry"].get("info") or {}).get("parts_bbox")
        if not pb:
            return []
        lo, hi = np.asarray(pb[:3], float), np.asarray(pb[3:], float)
        corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
        out = []
        for pl in planes:
            n = np.asarray(pl["normal"], float)
            d = (corners - np.asarray(pl["point"], float)) @ (n / max(np.linalg.norm(n), 1e-12))
            if d.min() < 0 < d.max():
                thin, total = min(-d.min(), d.max()), d.max() - d.min()
                if thin < 0.03 * total:
                    out.append(f"'{pl['name']}' only just cuts the model: the thin side is {thin * 1e3:.1f} mm of "
                               f"{total * 1e3:.0f} mm. A plane that grazes the model leaves tiny faces and cells of a "
                               "few micrometres there, and a run can diverge at that spot. Put the plane in the "
                               "middle of the model (its plane of symmetry), or clear of it.")
        return out

    def _symmetry_setup_after(self, planes: list):
        """Rotors and zones on the removed side are gone with it (their mirror images stand in for them)."""
        gone = []
        keep = []
        for r in self.data.get("rotors") or []:
            if all(sym.side_of(pl, r["center"]) == "kept" for pl in planes):
                keep.append(r)
            else:
                gone.append("a rotor")
        if "rotors" in self.data:
            self.data["rotors"] = keep
        zkeep = []
        for z in self.data.get("moving_zones") or []:
            if all(sym.side_of(pl, z["center"]) == "kept" for pl in planes):
                zkeep.append(z)
            else:
                gone.append(f"the rotating zone {z['name']}")
        if "moving_zones" in self.data:
            self.data["moving_zones"] = zkeep
        if gone:
            self.data.setdefault("notes", []).append(
                f"{len(gone)} item(s) on the removed side of a symmetry plane were dropped ({', '.join(gone)}): the "
                "mirror image stands in for them (rotors and zones mirror to the opposite turning direction, as "
                "on a quadcopter with alternating propellers).")

    def _apply_symmetry(self, planes: list) -> Job:
        if self.busy():
            raise RuntimeError("Please wait: a job is still running for this project (or cancel it).")
        if self.data["geometry"].get("status") != "ready" or not (self.geo_dir / "source.step").exists():
            raise ValueError("Import a geometry first")
        if self.data["geometry"].get("format", "step") != "step":
            raise ValueError("Symmetry planes need a STEP file (an STL / OBJ mesh cannot be cut): export the part as "
                             "STEP, or cut the mesh in your CAD / mesh tool.")
        planes = sym.validate(planes)
        issues = self._symmetry_issues(planes)
        if issues:
            raise ValueError("; ".join(issues))
        return self.prepare_geometry(symmetry=planes)

    def add_symmetry(self, axis=None, pos_mm=None, faces=None, keep=None, name=None, point=None, normal=None,
                     cut_parts=False) -> Job:
        """A new symmetry plane: from flat faces (all in one plane), normal to an axis at a position (default the
        middle of the parts), or from a point and a normal. keep: '+' or '-': the side of the axis that stays."""
        planes = self.symmetry_planes()
        keep_toward = self._parts_center()
        if faces:
            fm = self.face_map()
            fs = []
            for f in faces:
                f = int(f)
                if f not in fm:
                    raise ValueError(f"Face {f} does not exist")
                if "Plane" not in str(fm[f].get("surface_type", "")):
                    raise ValueError(f"Face {f} is not flat: a symmetry plane needs flat faces")
                fs.append(fm[f])
            new = sym.plane_from_face(fs[0], keep_toward=keep_toward, name=name)
            tol = 1e-4 * (self.data["geometry"].get("info") or {}).get("ref_length", 1.0)
            if not all(sym.on_plane(new, f, max(tol, 1e-6)) for f in fs):
                raise ValueError("The selected faces do not lie in one plane: select the faces of one flat wall")
        elif axis:
            lo_hi = np.asarray((self.data["geometry"].get("info") or {}).get("parts_bbox")
                               or (self.data["geometry"].get("info") or {}).get("bbox") or [0] * 6, float)
            a = "xyz".index(axis) if axis in ("x", "y", "z") else None
            if a is None:
                raise ValueError("axis must be x, y or z")
            pos = studies.finite(pos_mm, "Position") * 1e-3 if pos_mm is not None else float((lo_hi[a] + lo_hi[a + 3]) / 2)
            new = sym.axis_plane(axis, pos, keep != "-", name)
        elif point is not None and normal is not None:
            new = sym.make_plane(point, normal, name)
        else:
            raise ValueError("Give faces, an axis (x, y or z) or a point and a normal")
        if not new["name"]:
            new["name"] = f"Symmetry plane {len(planes) + 1}"
        new["cut_parts"] = bool(cut_parts)
        return self._apply_symmetry(planes + [new])

    def update_symmetry(self, plane_id: str, pos_mm=None, flip=False, name=None, normal=None, point=None,
                        cut_parts=None) -> Job | None:
        """Move (position along the normal, in mm), flip (keep the other side), rename or re-aim a plane. A rename
        alone changes nothing in the geometry and returns None."""
        planes = self.symmetry_planes()
        pl = next((q for q in planes if q["id"] == plane_id or q["name"].lower() == str(plane_id).lower()), None)
        if pl is None:
            raise ValueError(f"No symmetry plane '{plane_id}'")
        geometry = False
        if name is not None and name.strip():
            pl["name"] = name.strip()
        if cut_parts is not None and bool(cut_parts) != bool(pl.get("cut_parts")):
            pl["cut_parts"] = bool(cut_parts)
            geometry = True
        n = np.asarray(pl["normal"], float)
        if normal is not None:
            n = sym.make_plane([0, 0, 0], normal)["normal"]
            n = np.asarray(n)
            geometry = True
        if flip:
            n = -n
            geometry = True
        pl["normal"] = n.tolist()
        if pos_mm is not None:
            d = studies.finite(pos_mm, "Position") * 1e-3
            pt = np.asarray(pl["point"], float)
            a = int(np.argmax(np.abs(n)))
            if abs(n[a]) > 0.9999:  # normal to an axis: the position is the coordinate along that axis
                pt = pt.copy()
                pt[a] = d
            else:  # an oblique plane: the position is the distance from the origin along the normal
                pt = pt + n * (d - float(pt @ n))
            pl["point"] = pt.tolist()
            geometry = True
        if point is not None:
            pl["point"] = sym.make_plane(point, n)["point"]
            geometry = True
        if not geometry:
            self.data["symmetry"] = sym.validate(planes)
            for b in self.data["bcs"]:
                if b.get("plane_id") == pl["id"]:
                    b["name"] = pl["name"]
            self.save()
            return None
        return self._apply_symmetry(planes)

    def remove_symmetry(self, plane_id: str) -> Job:
        planes = self.symmetry_planes()
        keep = [q for q in planes if q["id"] != plane_id and q["name"].lower() != str(plane_id).lower()]
        if len(keep) == len(planes):
            raise ValueError(f"No symmetry plane '{plane_id}'")
        return self._apply_symmetry(keep)

    def _sig(self, fid):
        f = self.face_map()[fid]
        return {"centroid": f["centroid"], "area": f["area"]}

    def set_domain(self, **kw) -> Job:
        clean = {}
        for k, v in kw.items():
            if k == "enclosure_mode":
                if v not in ("body", "auto_box"):
                    raise ValueError("enclosure_mode must be 'body' or 'auto_box'")
                clean[k] = v
            elif k == "enclosure_body":
                n = len(self.data["geometry"].get("info", {}).get("source_bodies", []))
                if not isinstance(v, int) or not 0 <= v < max(n, 1):
                    raise ValueError(f"enclosure_body must be a body index 0..{n - 1}")
                clean[k] = v
            elif k == "padding":
                vals = [studies.finite(x, "Padding") for x in (v if isinstance(v, list) else [v] * 6)]
                if len(vals) != 6 or any(x <= 0 or x > 50 for x in vals):
                    raise ValueError("padding needs 6 values between 0 and 50 (x part size)")
                clean[k] = vals
            elif k == "shape":
                from . import domain as air

                if v not in air.SHAPES:
                    raise ValueError(f"shape must be one of {', '.join(air.SHAPES)}")
                if v != self.data.get("domain", {}).get("shape", "box"):
                    clean["size"] = None  # a new shape starts fitted to the parts
                clean[k] = v
            elif k == "size":  # explicit air-volume size (metres); None = fit to the parts
                from . import domain as air

                if v is None:
                    clean[k] = None
                    continue
                shape = kw.get("shape", self.data.get("domain", {}).get("shape", "box"))
                keys = air.SIZE_KEYS[shape]
                if not isinstance(v, dict) or set(v) - set(keys):
                    raise ValueError(f"size for a {shape} takes {', '.join(keys)}")
                sz = {}
                for kk, vv in v.items():
                    if kk in ("min", "max"):
                        if not isinstance(vv, list) or len(vv) != 3:
                            raise ValueError(f"size.{kk} needs 3 values (x, y, z) in metres")
                        sz[kk] = [studies.finite(x, f"Air box {kk}") for x in vv]
                    else:
                        sz[kk] = studies.finite(vv, f"Air volume {kk}")
                        if kk in ("radius", "upstream", "downstream") and sz[kk] <= 0:
                            raise ValueError(f"Air volume {kk} must be > 0")
                if "min" in sz and "max" in sz and any(a >= b for a, b in zip(sz["min"], sz["max"])):
                    raise ValueError("Air box: every max must be larger than its min")
                clean[k] = sz
            elif k == "fit":  # back to the automatic size around the parts
                if v:
                    clean["size"] = None
            elif k in ("flow_axis", "span_axis"):
                ok = ("+x", "-x", "+y", "-y", "+z", "-z") if k == "flow_axis" else ("x", "y", "z", None)
                if v not in ok:
                    raise ValueError(f"{k} must be one of {ok}")
                clean[k] = v
            elif k == "farfield":
                if not isinstance(v, dict) or set(v) - {"upstream", "downstream", "side"}:
                    raise ValueError("farfield takes upstream, downstream, side")
                ff = {kk: studies.finite(vv, f"Far-field {kk}") for kk, vv in v.items()}
                if any(x < 0.5 or x > 100 for x in ff.values()):  # (0.5-1: quick test runs, the walls are close)
                    raise ValueError("Far-field distances must be between 0.5 and 100 spans")
                clean[k] = ff
            else:
                raise ValueError(f"Unknown domain option '{k}'")
        if self.busy():
            raise RuntimeError("Please wait: a job is still running for this project (or cancel it).")
        d = {**self.data.get("domain", {}), **clean}
        for k in [k for k, v in d.items() if v is None]:
            d.pop(k)
        info = self.data["geometry"].get("info") or {}
        auto = self.study == "external_aero" or (self.study == "cht_enclosure" and d.get("enclosure_mode") == "auto_box")
        if auto and info.get("parts_bbox") and d.get("size"):  # check now, not in the background job
            from . import domain as air

            spec = self.air_spec(d)
            msg = air.check_encloses(spec, info["parts_bbox"], info["ref_length"])
            if msg:
                raise ValueError(msg)
        self.data["domain"] = d
        self.save()
        if not (self.geo_dir / "source.step").exists():
            return None  # no geometry yet: the domain options are used by the next import
        return self.prepare_geometry()

    def _air_dom(self, dom=None) -> dict:
        """Domain options for the air-volume builder (+ the up direction when the air rises on its own, no fan)."""
        dom = dict(self.data.get("domain", {}) if dom is None else dom)
        s = self.data["settings"]
        if self.study == "cht_enclosure" and s.get("air_flow") == "natural" and s.get("gravity", "-z") != "off":
            g = s.get("gravity", "-z")
            dom["natural_up"] = ("+" if g[0] == "-" else "-") + g[1]
        return dom

    # ------------------------------------------------------------------ advanced OpenFOAM
    def set_foam_advanced(self, **kw):
        if self.spec["solver"] == "builtin_fem":
            raise ValueError("Advanced OpenFOAM settings apply to the CFD studies only")
        clean = studies.validate_advanced(kw)
        defaults = {p["key"]: p["default"] for p in studies.FOAM_ADVANCED}
        adv = {**self.data.get("foam_advanced", {}), **clean}
        self.data["foam_advanced"] = {k: v for k, v in adv.items() if v != defaults.get(k)}
        self.mark_stale(("solve",))
        self.save()

    @property
    def preview_dir(self) -> Path:
        return self.dir / "case_preview"

    def foam_preview(self) -> dict:
        """Write the OpenFOAM case as the next run would (into case_preview/, the real case is untouched)."""
        from .solvers.openfoam import advanced, cases

        if self.spec["solver"] == "builtin_fem":
            raise ValueError("This study uses the built-in solver: there are no OpenFOAM case files")
        if self.data["mesh"].get("status") not in ("ready", "stale"):
            raise ValueError("Generate the mesh first: the case files are written for the mesh")
        if self.busy():
            raise RuntimeError("Please wait: a job is still running for this project.")
        cases.write_case(self.preview_dir, self.data, self.load_mesh(), self.faces(),
                         self.data["geometry"]["info"], self.bodies())
        advanced.apply(self.preview_dir, self.data.get("foam_advanced"), self.data.get("foam_overrides"))
        return self.case_files()

    def case_files(self) -> dict:
        from .solvers.openfoam import advanced

        files = advanced.list_files(self.preview_dir) if self.preview_dir.exists() else []
        ov = self.data.get("foam_overrides", {})
        return {"files": files, "overridden": sorted(ov), "generated": bool(files)}

    def case_file(self, rel: str) -> dict:
        from .solvers.openfoam import advanced

        rel = advanced.check_rel(rel)
        ov = self.data.get("foam_overrides", {})
        f = self.preview_dir / rel
        gen = f.read_text(errors="replace") if f.exists() else ""
        return {"path": rel, "text": ov.get(rel, gen), "overridden": rel in ov}

    def set_case_file(self, rel: str, text: str | None):
        """Keep an edited dictionary (written over the generated one before every run); None = back to generated."""
        from .solvers.openfoam import advanced

        rel = advanced.check_rel(rel)
        ov = dict(self.data.get("foam_overrides", {}))
        if text is None:
            ov.pop(rel, None)
        else:
            if not isinstance(text, str) or len(text.encode()) > advanced.MAX_FILE_BYTES:
                raise ValueError("The file text is missing or too large")
            if text.count("{") != text.count("}"):
                raise ValueError("Unbalanced braces { } - the dictionary would not load")
            advanced.check_safe(rel, text)
            ov[rel] = text
        self.data["foam_overrides"] = ov
        self.mark_stale(("solve",))
        self.save()
        if self.preview_dir.exists() and text is not None:
            (self.preview_dir / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.preview_dir / rel).write_text(text, newline="\n")
        return self.case_file(rel)

    def air_spec(self, dom=None) -> dict:
        """The automatic air volume (shape and absolute size) for the domain options dom (default: current)."""
        from . import domain as air

        info = self.data["geometry"]["info"]
        dom = self._air_dom(dom)
        aero = self.study == "external_aero"
        return air.resolve(dom, self.study, info["parts_bbox"], info["ref_length"],
                           (info.get("domain") or {}).get("span") if aero else None,
                           self._flow_dir() if aero else (1, 0, 0),
                           "xyz".index(self.data["settings"].get("up_axis", "+z")[1]) if aero else None)

    # ------------------------------------------------------------------ setup
    def set_study(self, study: str):
        studies.study(study)
        if study == self.study:
            return
        if self.busy():  # refuse BEFORE changing anything: a running job reads these settings
            raise RuntimeError("Please wait: a job is still running for this project (or cancel it).")
        self.data["study"] = study
        self.data["settings"] = studies.default_settings(study)
        self.data["bcs"] = []
        self.data["rotors"] = []
        self.data["domain"] = {"enclosure_mode": "body"} if study == "cht_enclosure" else {}
        self.save()
        if (self.geo_dir / "source.step").exists():
            return self.prepare_geometry()

    def set_settings(self, **kw):
        spec = {p["key"]: p for p in self.spec["settings"]}
        staged = {}
        for k, v in kw.items():
            if k not in spec:
                raise ValueError(f"Unknown setting '{k}'. " + (f"Options: {', '.join(spec)}" if spec else
                                                                "This study has no solver settings."))
            p = spec[k]
            if "options" in p:
                if v not in p["options"]:
                    raise ValueError(f"{p['label']} must be one of {p['options']}")
            else:
                v = studies.finite(v, p["label"])
                if "min" in p and v < p["min"]:
                    raise ValueError(f"{p['label']} must be ≥ {p['min']}")
                if "max" in p and v > p["max"]:
                    raise ValueError(f"{p['label']} must be ≤ {p['max']}")
                if p.get("unit") == "°C" and v <= -273.15:
                    raise ValueError(f"{p['label']} must be above absolute zero")
                if k in ("iterations", "n_procs", "n_modes", "n_frames"):
                    if v != int(v):
                        raise ValueError(f"{p['label']} must be a whole number")
                    v = int(v)
            staged[k] = v
        before = self._air_dom()
        self.data["settings"].update(staged)
        if (self.study == "cht_enclosure" and self._air_dom() != before and not self.busy()
                and self.data["domain"].get("enclosure_mode") == "auto_box" and not self.data["domain"].get("size")
                and (self.geo_dir / "source.step").exists()):
            self.mark_stale(("solve",))
            self.save()
            return self.prepare_geometry()  # (fan <-> no fan: the fitted air box changes, plume room above)
        if self.study == "external_aero" and any(k in kw for k in ("speed", "pitch_deg", "yaw_deg", "forward_axis", "up_axis")):
            self.data.setdefault("notes", [])
        self.mark_stale(("solve",))
        self.save()

    def set_body(self, body_id: int, material: str | None = None, heat_W: float | None = None,
                 name: str | None = None):
        b = next((b for b in self.bodies() if b["id"] == int(body_id)), None)
        if b is None:
            raise ValueError(f"No body with id {body_id}")
        if b["role"] != "solid" and (material or heat_W):
            raise ValueError(f"Body {body_id} is the air region; it has no material/heat source")
        cfg = self.data["bodies"].setdefault(str(body_id), {})
        if material is not None:
            materials.get_solid(material)
            cfg["material"] = material
            cfg.pop("material_is_default", None)
        if heat_W is not None:
            q = studies.finite(heat_W, "Heat generated")
            if q < 0 or q > 1e6:
                raise ValueError("Heat generation must be between 0 and 1e6 W")
            cfg["heat_W"] = q
        if name:
            cfg["name"] = name
        self.mark_stale(("solve",))
        self.save()
        return self._body_view(b)

    def add_bc(self, name: str, bc_type: str, faces: list, params: dict | None = None) -> dict:
        params = studies.validate_params(self.study, bc_type, params or {})
        if bc_type == "symmetry":  # a mirror is a plane: the geometry is cut there (a background job)
            if not faces:
                raise ValueError("Select the flat face the symmetry plane goes through")
            self.add_symmetry(faces=faces, name=(name or "").strip() or None)
            return {"id": None, "name": name or "Symmetry plane", "type": "symmetry", "faces": [], "params": {},
                    "note": "The geometry is being rebuilt with the plane; the condition appears when it is done."}
        faces = self._check_faces(faces, bc_type, allow_empty=True)
        name = (name or self.spec["face_bc_types"][bc_type]["label"]).strip()
        names = {b["name"].lower() for b in self.data["bcs"]}
        base, i = name, 2
        while name.lower() in names:
            name, i = f"{base} {i}", i + 1
        # a face can only have one BC: remove from others
        self._unassign(faces)
        bc = {"id": uuid.uuid4().hex[:8], "name": name, "type": bc_type, "faces": faces, "params": params,
              "signature": [self._sig(f) for f in faces]}
        self.data["bcs"].append(bc)
        self.mark_stale(("solve",))
        self.save()
        return bc

    def update_bc(self, bc_id: str, name=None, bc_type=None, faces=None, params=None, add_faces=None,
                  remove_faces=None) -> dict:
        bc = self._bc(bc_id)
        if bc.get("plane_id"):
            if bc_type not in (None, "symmetry") or faces is not None or add_faces or remove_faces:
                raise ValueError("The faces of a symmetry plane follow the plane: move or flip the plane instead")
            if name:
                self.update_symmetry(bc["plane_id"], name=name)
            return self._bc(bc_id)
        t = bc_type or bc["type"]
        if params is not None or bc_type:
            merged = {**(bc["params"] if t == bc["type"] else {}), **(params or {})}
            bc["params"] = studies.validate_params(self.study, t, merged)
        bc["type"] = t
        new_faces = list(bc["faces"])
        if faces is not None:
            new_faces = list(faces)
        if add_faces:
            new_faces += list(add_faces)
        if remove_faces:
            new_faces = [f for f in new_faces if f not in set(int(x) for x in remove_faces)]
        new_faces = self._check_faces(new_faces, t, allow_empty=True)
        self._unassign(new_faces, keep=bc["id"])
        bc["faces"] = new_faces
        bc.pop("lost", None)  # (the user chose the faces: nothing to bring back)
        bc["signature"] = [self._sig(f) for f in new_faces]
        if name:
            bc["name"] = name
        self.mark_stale(("solve",))
        self.save()
        return bc

    def delete_bc(self, bc_id: str):
        bc = self._bc(bc_id)
        if bc.get("plane_id"):  # the plane goes, the removed side comes back (a background job)
            self.remove_symmetry(bc["plane_id"])
            return
        self.data["bcs"].remove(bc)
        self.mark_stale(("solve",))
        self.save()

    def _bc(self, bc_id):
        for b in self.data["bcs"]:
            if b["id"] == bc_id or b["name"].lower() == str(bc_id).lower():
                return b
        raise ValueError(f"No boundary condition '{bc_id}'")

    def _unassign(self, faces, keep=None):
        fs = set(faces)
        for b in self.data["bcs"]:
            if b["id"] != keep and fs & set(b["faces"]):
                b["faces"] = [f for f in b["faces"] if f not in fs]
                b["signature"] = [self._sig(f) for f in b["faces"]]

    def _check_faces(self, faces, bc_type, allow_empty=False):
        fm = self.face_map()
        out = []
        kinds = self.spec["face_bc_types"][bc_type].get("face_kinds")
        for f in faces:
            f = int(f)
            if f not in fm:
                raise ValueError(f"Face {f} does not exist")
            face = fm[f]
            if face.get("interface"):
                raise ValueError(f"Face {f} is an internal contact face between two bodies; "
                                 f"boundary conditions only apply to outer faces")
            if kinds and face.get("kind") not in kinds:
                raise ValueError(f"Face {f} ({face.get('kind')}) can't take '{bc_type}' "
                                 f"(allowed on: {', '.join(kinds)})")
            out.append(f)
        if not out and not allow_empty:
            raise ValueError("Select at least one face")
        return sorted(set(out))

    # rotors (external aero)
    def set_moving_zones(self, zones: list):
        """Rotating zones (cylinders of air turning about their axis): MRF (steady) or sliding mesh (transient)."""
        if self.busy():
            raise RuntimeError("Please wait: a job is still running for this project (or cancel it).")
        if self.study not in ("external_aero", "cht_enclosure"):
            raise ValueError("Moving zones are available in external aerodynamics and electronics cooling")
        if self.study == "cht_enclosure" and any(z.get("method") == "sliding" for z in zones or []):
            raise ValueError("Electronics cooling: fan zones use MRF (a sliding mesh is available in external aero)")
        clean = []
        for i, z in enumerate(zones or []):
            nm = re.sub(r"[^A-Za-z0-9_]", "_", str(z.get("name") or f"zone{i + 1}"))[:30] or f"zone{i + 1}"
            if nm[0].isdigit():
                nm = "z" + nm
            c = z.get("center", [])
            if len(c) != 3:
                raise ValueError(f"{nm}: the centre needs three values (x, y, z)")
            c = [studies.finite(x, f"{nm} centre") for x in c]
            if z.get("axis") not in studies.AXES:
                raise ValueError(f"{nm}: axis must be one of {', '.join(studies.AXES)}")
            R = studies.finite(z.get("radius"), f"{nm} radius")
            Lz = studies.finite(z.get("length"), f"{nm} length")
            rpm = studies.finite(z.get("rpm"), f"{nm} speed")
            if R <= 0 or Lz <= 0:
                raise ValueError(f"{nm}: radius and length must be > 0")
            if abs(rpm) > 1e6:
                raise ValueError(f"{nm}: speed must be below 1e6 rpm")
            if z.get("method", "mrf") not in ("mrf", "sliding"):
                raise ValueError(f"{nm}: method must be 'mrf' or 'sliding'")
            cs = z.get("cell_size")  # cell size inside the zone (m); None = automatic
            cs = None if cs in (None, "", 0) else studies.finite(cs, f"{nm} cell size")
            if cs is not None and not 0 < cs < R:
                raise ValueError(f"{nm}: the cell size inside the zone must be > 0 and smaller than its radius")
            clean.append({"name": nm, "center": c, "axis": z["axis"], "radius": R, "length": Lz, "rpm": rpm,
                          "method": z.get("method", "mrf"), "cell_size": cs})
        if len({z["name"] for z in clean}) != len(clean):
            raise ValueError("Moving zones need different names")
        for i, z1 in enumerate(clean):  # the zones must not overlap (a cell belongs to one zone)
            for z2 in clean[i + 1:]:
                if self._zones_touch(z1, z2):
                    raise ValueError(f"Moving zones {z1['name']} and {z2['name']} overlap or touch: move them apart "
                                     "or make them smaller (the air between two turning zones needs cells)")
        # the mesh depends on where the zones are, not on their speed or method (MRF and sliding zones are meshed
        # the same way): a new speed keeps the mesh (a 2.7 M cell mesh takes long to rebuild)
        shape = lambda zs: [{k: z.get(k) for k in ("name", "center", "axis", "radius", "length", "cell_size")}  # noqa: E731
                            for z in zs]
        same_mesh = shape(clean) == shape(self.data.get("moving_zones") or [])
        self.data["moving_zones"] = clean
        self.mark_stale(("solve",) if same_mesh else ("mesh", "solve"))
        self.save()
        return self.state()

    @staticmethod
    def _zones_touch(z1: dict, z2: dict, gap: float = 0.0) -> bool:
        """Whether two cylindrical zones are closer than `gap` (conservative: a cylinder is compared by the
        distance of axis-aligned boxes around the two when the axes differ, exactly when they are parallel)."""
        def box(z):
            c, a = np.asarray(z["center"], float), np.asarray(studies.AXES[z["axis"]], float)
            h = a * z["length"] / 2 + (1 - np.abs(a)) * z["radius"]
            return c - np.abs(h), c + np.abs(h)
        if z1["axis"][1] == z2["axis"][1]:  # parallel: distance between the discs' circles in the plane + axial
            a = np.asarray(studies.AXES[z1["axis"]], float)
            d = np.asarray(z2["center"], float) - np.asarray(z1["center"], float)
            ax, rad = abs(float(d @ a)), float(np.linalg.norm(d - (d @ a) * a))
            axial_gap = ax - (z1["length"] + z2["length"]) / 2
            radial_gap = rad - (z1["radius"] + z2["radius"])
            return axial_gap < gap and radial_gap < gap
        (l1, h1), (l2, h2) = box(z1), box(z2)
        return bool(np.all(l1 - gap < h2) and np.all(l2 - gap < h1))

    def _zone_problems(self, warnings: list | None = None) -> list:
        """Each zone must lie in the air, not cut through a part's surface (its boundary is meshed exactly) and keep
        a gap to the parts: cells squeezed into a gap much smaller than the zone's surface cells are badly shaped
        (a blade tip 0.1 mm inside the zone made the run diverge). Errors are returned; warnings (a small gap) are
        appended to `warnings`."""
        from .gmsh_worker import ZONE_CELLS
        out = []
        preset = self.data["mesh"].get("preset", "medium")
        info = self.data["geometry"].get("info") or {}
        dom = (info.get("domain") or {})
        lo, hi = dom.get("box_min"), dom.get("box_max")
        for z in self.data.get("moving_zones") or []:
            c = np.asarray(z["center"], float)
            a = np.asarray(studies.AXES[z["axis"]], float)
            ends = [c - a * z["length"] / 2, c + a * z["length"] / 2]
            if lo and hi:
                r_ext = z["radius"] * (1 - np.abs(a))
                if any(np.any(e - r_ext < np.asarray(lo)) or np.any(e + r_ext > np.asarray(hi)) for e in ends):
                    out.append(f"Moving zone {z['name']} reaches outside the air volume: make it smaller or the air "
                               "volume larger")
            gap, gap_face, cut = math.inf, None, False
            for f in self.faces():
                if f.get("kind") in ("farfield", "enclosure"):
                    continue
                v = np.asarray((f.get("mesh") or {}).get("vertices") or [], float).reshape(-1, 3)
                if not len(v):
                    continue
                rel = v - c
                ax = rel @ a
                rad = np.linalg.norm(rel - np.outer(ax, a), axis=1)
                inside = (np.abs(ax) <= z["length"] / 2) & (rad <= z["radius"])
                if inside.any() and not inside.all():
                    out.append(f"Moving zone {z['name']} cuts through face {f['id']} ({f.get('part') or 'part'}): "
                               "enlarge it to enclose the whole rotating part, or shrink it to clear the part")
                    cut = True
                    break
                # distance of the face (its display vertices) to the zone boundary, from inside or outside
                dr, da = rad - z["radius"], np.abs(ax) - z["length"] / 2
                d = np.where(inside, np.minimum(-dr, -da),
                             np.hypot(np.maximum(dr, 0), np.maximum(da, 0))).min()
                if d < gap:
                    gap, gap_face = float(d), f
            if not cut and gap_face is not None:
                hz = 2 * math.pi * z["radius"] / ZONE_CELLS.get(preset, 40)  # cell size on the zone boundary
                where = f"face {gap_face['id']} ({gap_face.get('part') or 'part'})"
                if gap < 0.25 * hz:
                    out.append(f"Moving zone {z['name']} is only {gap * 1e3:.2g} mm from {where}: the cells in that "
                               f"gap would be badly shaped. Keep at least {0.5 * hz * 1e3:.1f} mm (half a zone cell "
                               f"at this mesh resolution) between the zone boundary and every part")
                elif gap < 0.5 * hz and warnings is not None:
                    warnings.append(f"Moving zone {z['name']} is {gap * 1e3:.2g} mm from {where}: a gap of at "
                                    f"least {0.5 * hz * 1e3:.1f} mm gives better cells (and a steadier run)")
            if z["method"] == "sliding" and self.data["settings"].get("analysis") != "transient":
                out.append(f"Moving zone {z['name']} uses a sliding mesh: set Analysis to transient (or use MRF "
                           "for a steady run)")
        return out

    def set_rotors(self, rotors: list):
        if self.busy():
            raise RuntimeError("Please wait: a job is still running for this project (or cancel it).")
        if self.study != "external_aero":
            raise ValueError("Rotors only exist in the drone / external aerodynamics study")
        clean = []
        for r in rotors:
            if len(r.get("center", [])) != 3 or len(r.get("axis", [0, 0, 1])) != 3:
                raise ValueError("Rotor centre and axis need three components (x, y, z)")
            for x in r.get("axis", [0, 0, 1]):
                studies.finite(x, "Rotor axis")
            if r.get("thickness") not in (None, ""):
                if studies.finite(r["thickness"], "Rotor disk thickness") <= 0:
                    raise ValueError("Rotor disk thickness must be > 0")
            c = [studies.finite(x, "Rotor centre") for x in r["center"]]
            if r.get("thrust_N") not in (None, ""):
                studies.finite(r["thrust_N"], "Rotor thrust")
            studies.finite(r["radius"], "Rotor radius")
            a = np.asarray(r.get("axis", [0, 0, 1]), float)
            if np.linalg.norm(a) < 1e-12:
                raise ValueError("Rotor axis must be non-zero")
            a = (a / np.linalg.norm(a)).tolist()
            rad = float(r["radius"])
            if rad <= 0:
                raise ValueError("Rotor radius must be > 0")
            th = float(r.get("thickness") or 0.15 * rad)
            t = r.get("thrust_N")
            clean.append({"center": c, "axis": a, "radius": rad, "thickness": th,
                          "thrust_N": None if t in (None, "") else float(t)})
        self.data["rotors"] = clean
        self.mark_stale(("mesh", "solve"))
        self.save()
        return clean

    def detect_rotors(self, apply=True, radius=None):
        """Find motor tops: flat, roughly circular faces perpendicular to 'up', away from the centre.

        Face normals of the flow domain point into the solid, so both orientations are accepted and the
        top of each motor is the circular face with the highest position along 'up'."""
        up = np.array(studies.AXES[self.data["settings"].get("up_axis", "+z")], float)
        cands = []
        for f in self.faces():
            if f.get("kind") != "body" or "Plane" not in f.get("surface_type", "Plane"):
                continue
            n = np.asarray(f["normal"])
            if abs(n @ up) < 0.97:
                continue
            bb = np.array(f["bbox"])
            ext = sorted((bb[3:] - bb[:3]).tolist())[1:]
            if ext[0] <= 0 or ext[1] / ext[0] > 1.15:
                continue
            fill = f["area"] / (ext[0] * ext[1])
            if 0.70 < fill < 0.86:  # circle: pi/4 = 0.785
                cands.append(f)
        # one candidate per motor: cluster by position in the plane perpendicular to up, keep the top face
        clusters = []
        for f in cands:
            c = np.asarray(f["centroid"])
            r = math.sqrt(f["area"] / math.pi)
            for cl in clusters:
                d = c - np.asarray(cl["centroid"])
                if np.linalg.norm(d - (d @ up) * up) < r:
                    if c @ up > np.asarray(cl["centroid"]) @ up:
                        cl.update(f)
                    break
            else:
                clusters.append(dict(f))
        if len(clusters) < 2:
            if apply:
                raise ValueError("Could not find motor tops automatically; add rotors manually.")
            return []
        cen = np.array([c["centroid"] for c in clusters])
        d = np.linalg.norm(cen[:, None] - cen[None], axis=2) + np.eye(len(cen)) * 1e9
        spacing = d.min()
        R = float(radius or 0.48 * spacing)
        # all body vertices, to keep the disks clear of the airframe
        verts = np.vstack([np.asarray(f["mesh"]["vertices"]).reshape(-1, 3) for f in self.faces()
                           if f.get("kind") == "body" and f["mesh"]["vertices"]])
        rotors = []
        for c in clusters:
            c0 = np.asarray(c["centroid"])
            rel = verts - c0
            h = rel @ up
            radial = np.linalg.norm(rel - np.outer(h, up), axis=1)
            top = max(float(h[radial <= R * 1.02].max(initial=0.0)), 0.0)  # highest geometry under the disk
            # a uniform actuator disk is insensitive to its thickness; ~0.15 R keeps the disk cells (th / 2)
            # within 2x of the wake cells instead of wasting a fifth of the mesh on the disks
            th = 0.15 * R
            center = (c0 + up * (top + 0.05 * R + th / 2)).tolist()
            rotors.append({"center": center, "axis": up.tolist(), "radius": R, "thickness": th, "thrust_N": None})
        if apply:
            return self.set_rotors(rotors)
        return rotors

    def rotor_span(self):
        """Size of airframe + rotor disks (for far-field sizing)."""
        bb = np.array(self.data["geometry"]["info"].get("parts_bbox") or self.data["geometry"]["info"]["bbox"])
        lo, hi = bb[:3].copy(), bb[3:].copy()
        for r in self.data.get("rotors", []):
            c = np.asarray(r["center"])
            lo = np.minimum(lo, c - r["radius"])
            hi = np.maximum(hi, c + r["radius"])
        return float((hi - lo).max())

    def suggest_inlet_outlet(self, apply=True):
        """Pick the two enclosure faces at the opposite ends of the longest axis (automatic air volume: its
        upstream and downstream faces; a curved inlet gets uniform flow along the flow axis)."""
        enc = [f for f in self.faces() if f.get("kind") == "enclosure"]
        if not enc:
            raise ValueError("No enclosure faces")
        if any(f.get("domain_role") for f in enc) and self.data["domain"].get("enclosure_mode") == "auto_box":
            spec = self.data["geometry"]["info"]["domain"]["spec"]
            res = {"inlet": [f["id"] for f in enc if f.get("domain_role") == "inlet"],
                   "outlet": [f["id"] for f in enc if f.get("domain_role") == "outlet"],
                   "axis": "xyz"[spec["flow_axis"]]}
            ax = ("+" if spec["flow_sign"] > 0 else "-") + res["axis"]
            if apply and res["inlet"] and res["outlet"]:
                old = next((b for b in self.data["bcs"] if b["type"] == "velocity_inlet"), None)
                prm = dict(old["params"]) if old else {}
                prm["direction"] = "normal" if spec["shape"] == "box" else ax
                for b in [b for b in self.data["bcs"] if b["type"] in ("velocity_inlet", "pressure_outlet")]:
                    self.data["bcs"].remove(b)
                self.add_bc("Inlet", "velocity_inlet", res["inlet"], prm)
                self.add_bc("Outlet", "pressure_outlet", res["outlet"], {})
            return res
        bb = self.data["geometry"]["info"]["bbox"]
        fl = [b for b in self.bodies() if b["role"] == "fluid"][0]["bbox"]
        ax = int(np.argmax([fl[i + 3] - fl[i] for i in range(3)]))
        lo = [f for f in enc if abs(f["bbox"][ax] - fl[ax]) < 1e-6 and abs(f["bbox"][ax + 3] - fl[ax]) < 1e-6]
        hi = [f for f in enc if abs(f["bbox"][ax] - fl[ax + 3]) < 1e-6 and abs(f["bbox"][ax + 3] - fl[ax + 3]) < 1e-6]
        if not lo or not hi:
            raise ValueError("Could not identify end faces; mark inlet and outlet manually")
        res = {"inlet": [f["id"] for f in lo], "outlet": [f["id"] for f in hi], "axis": "xyz"[ax]}
        if apply:
            for b in [b for b in self.data["bcs"] if b["type"] in ("velocity_inlet", "pressure_outlet")]:
                self.data["bcs"].remove(b)
            self.add_bc("Inlet", "velocity_inlet", res["inlet"], {})
            self.add_bc("Outlet", "pressure_outlet", res["outlet"], {})
        return res

    def find_faces(self, kind=None, normal=None, at=None, body=None, plane_type=None, tol_deg=10.0,
                   min_area_mm2=None, extreme=None, part=None):
        """Spatial query helper.
        normal: '+x'.. or [x,y,z]; at: e.g. 'max_x' / 'min_z' (faces lying at the extreme coordinate);
        body: body id (faces touching that body); kind: face kind; extreme kept for compatibility."""
        fs = self.faces()
        if kind:
            fs = [f for f in fs if f.get("kind") == kind]
        if part:
            names = {f.get("part") for f in fs if f.get("part")}
            if part not in names:
                raise ValueError(f"No part '{part}'. Parts: {', '.join(sorted(names)) or 'none (use body_id)'}")
            fs = [f for f in fs if f.get("part") == part]
        if body is not None:
            fs = [f for f in fs if int(body) in f["volumes"]]
        if plane_type:
            fs = [f for f in fs if plane_type.lower() in f.get("surface_type", "").lower()]
        if normal is not None:
            n = np.array(studies.AXES[normal] if isinstance(normal, str) else normal, float)
            n /= np.linalg.norm(n)
            c = math.cos(math.radians(tol_deg))
            fs = [f for f in fs if np.asarray(f["normal"]) @ n >= c]
        if min_area_mm2:
            fs = [f for f in fs if f["area"] * 1e6 >= float(min_area_mm2)]
        at = at or extreme
        if at and fs:
            m = re.match(r"(min|max)_([xyz])", at)
            if not m:
                raise ValueError("'at' must look like 'max_x' or 'min_z'")
            ax = "xyz".index(m.group(2))
            if m.group(1) == "max":
                v = max(f["bbox"][ax + 3] for f in fs)
                fs = [f for f in fs if abs(f["bbox"][ax] - v) < 1e-6 and abs(f["bbox"][ax + 3] - v) < 1e-6]
            else:
                v = min(f["bbox"][ax] for f in fs)
                fs = [f for f in fs if abs(f["bbox"][ax] - v) < 1e-6 and abs(f["bbox"][ax + 3] - v) < 1e-6]
        assigned = {f: b["name"] for b in self.data["bcs"] for f in b["faces"]}
        return [{"id": f["id"], "kind": f.get("kind"), "part": f.get("part"), "type": f.get("surface_type"),
                 "area_mm2": round(f["area"] * 1e6, 2), "centroid_mm": [round(c * 1000, 2) for c in f["centroid"]],
                 "normal": [round(x, 3) for x in f["normal"]], "bodies": f["volumes"],
                 "bc": assigned.get(f["id"])} for f in fs]

    # ------------------------------------------------------------------ checks
    def rename(self, name):
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise ValueError("Project name must be 1-120 characters of text")
        self.data["name"] = name.strip()
        self.save()

    def frame_plan(self) -> dict | None:
        """A transient sliding-mesh flow run: how many times it saves, how many degrees of turn apart, and the disk it
        takes (about 150 bytes per cell and saved time in the case plus 20 for the results file) against the free
        space. None when the run has no sliding zone or is steady."""
        import math
        import shutil

        s = self.data["settings"]
        zs = [z for z in self.data.get("moving_zones") or [] if z.get("method") == "sliding" and z.get("rpm")]
        cells = (self.data["mesh"].get("stats") or {}).get("cells") or 0
        if s.get("analysis") != "transient" or not zs or not cells:
            return None
        w = max(abs(z["rpm"]) for z in zs) * math.pi / 30
        dt = float(s.get("time_step") or 0) or math.radians(0.5) / w
        deg = float(s.get("frame_deg") or 0)
        end = float(s.get("end_time") or 0)
        if deg > 0:
            wi = max(1, round(math.radians(deg) / w / dt)) * dt
            frames = max(1, round(end / wi)) + 1
            deg = math.degrees(w * wi)
        else:
            frames = max(2, int(s.get("n_frames") or 21))
            deg = math.degrees(w * end / max(frames - 1, 1))
        gb = frames * cells * 170 / 2 ** 30
        try:
            free = shutil.disk_usage(WORKSPACE).free / 2 ** 30
        except OSError:
            free = float("inf")
        return {"frames": int(frames), "deg": deg, "gb": gb, "free_gb": free}

    def check(self) -> list:
        out = []
        # target: a CSS selector of the input in the step's panel that fixes the message (the GUI jumps to it on a click)
        ok = lambda m, step, target=None: out.append({"level": "ok", "message": m, "step": step, "target": target})  # noqa: E731
        warn = lambda m, step, target=None: out.append({"level": "warning", "message": m, "step": step, "target": target})  # noqa: E731
        err = lambda m, step, target=None: out.append({"level": "error", "message": m, "step": step, "target": target})  # noqa: E731
        g = self.data["geometry"]
        if g.get("status") != "ready":
            err("Import a STEP file", "geometry")
            return out
        ok(f"Geometry: {len(self.bodies())} region(s), {len(self.faces())} faces", "geometry")
        # lettering / logos / tessellated CAD: many faces far below the mesh size, which the mesher cannot
        # follow (their facets cross each other: "PLC Error ... intersect")
        L = (self.data["geometry"].get("info") or {}).get("ref_length") or 0
        tiny = {}
        for f in self.faces():
            if f.get("kind") not in ("farfield", "enclosure") and L and f["area"] < (2e-3 * L) ** 2:
                tiny[f.get("part") or "part"] = tiny.get(f.get("part") or "part", 0) + 1
        for part, n in tiny.items():
            if n >= 30:
                warn(f"{part} has {n} very small faces (under {(2e-3 * L) ** 2 * 1e6:.2g} mm²) - usually embossed "
                     "text, logos or a model converted from a mesh. The mesher cannot follow them and may fail with "
                     "'surfaces intersect'. Suppress the text/logo features in CAD (they do not change the flow) "
                     f"and import again, or try a defeature size of about {2e-3 * L * 1e3:.2g} mm (Mesh step, "
                     "Advanced: merges them into the face they sit on).", "geometry")
        st = self.study
        solids = [b for b in self.bodies() if b["role"] == "solid"]
        for b in solids:
            cfg = self.data["bodies"].get(str(b["id"]), {})
            nm = cfg.get("name") or b["name"]
            if not cfg.get("material"):
                err(f"Body '{nm}' needs a material", "setup")
            elif cfg.get("material_is_default"):
                warn(f"Body '{nm}' uses the default material (aluminium 6061) - confirm or change it", "setup")
        planes = self.data.get("symmetry") or []
        if planes:
            ok(f"Symmetry: {len(planes)} plane(s): this is the kept {'half' if len(planes) == 1 else 'part'} of the "
               "model; the results can show the mirrored rest", "setup")
            g = studies.AXES.get(self.data["settings"].get("gravity", ""))
            fd = self._flow_dir() if self.study == "external_aero" else (
                studies.AXES.get(self.data.get("domain", {}).get("flow_axis", ""))
                if self.study == "cht_enclosure" and self.data["settings"].get("air_flow") == "fan" else None)
            for msg in sym.crossing_issues(planes, flow_dir=fd, gravity=g):
                err(msg, "setup")
            for msg in self._symmetry_issues(planes):
                err(msg, "setup")
            for msg in self._symmetry_grazing(planes):
                warn(msg, "setup")
            if self.study in ("static_structural", "heat_conduction", "cht_enclosure"):
                warn("Symmetry: loads and heat sources you enter act on the kept half only - enter the share that "
                     "belongs to it (half of a total that is shared between two halves).", "setup")
        types = [b["type"] for b in self.data["bcs"] if b["faces"]]
        for b in self.data["bcs"]:
            if not b["faces"]:
                warn(f"Boundary condition '{b['name']}' has no faces", "setup")
        if st == "heat_conduction":
            sinks = [b for b in self.data["bcs"] if b["faces"] and (
                b["type"] in ("fixed_temperature", "natural_convection")
                or (b["type"] in ("convection", "radiation_convection")
                    and (float(b["params"].get("h") or 0) > 0 or float(b["params"].get("emissivity") or 0) > 0)))]
            transient = self.data["settings"].get("analysis") == "transient"
            if not sinks and not transient:
                err("Add a fixed temperature, or convection with h > 0, on some faces so heat can leave the part",
                    "setup")
            elif not sinks:
                warn("No cooling: the parts only heat up (fine for a short transient, e.g. a pulse into a heat "
                     "sink)", "setup")
            heat = sum(float(self.data["bodies"].get(str(b["id"]), {}).get("heat_W") or 0) for b in solids)
            if heat == 0 and not any(t in types for t in ("heat_flux", "heat_load")) and \
                    len({b["params"].get("T") for b in self.data["bcs"] if b["type"] == "fixed_temperature"}) < 2:
                warn("No heat source: set heat generated on a body, or add a heat load / flux", "setup")
        elif st == "vibration_modal":
            s = self.data["settings"]
            if "fixed_support" not in types:
                warn("No fixed support: the part is analysed floating free (free-free modes). Mark the bolted or "
                     "clamped faces as 'Fixed support' for a mounted part.", "setup")
            for b in self.data["bcs"]:
                if b["type"] == "added_mass" and b["faces"] and not float(b["params"].get("mass_kg") or 0):
                    warn(f"Added mass '{b['name']}' is 0 kg", "setup")
            lo, hi = float(s.get("excitation_min_hz") or 0), float(s.get("excitation_max_hz") or 0)
            if hi and lo > hi:
                err("Excitation 'from' is above 'up to' - swap them", "setup")
        elif st == "aeroelastic_flutter":
            s = self.data["settings"]
            if "fixed_support" not in types:
                err("Mark the root of the wing (the clamped or bolted face) as 'Fixed support': flutter of a free "
                    "structure is not covered", "setup")
            if "aero_surface" not in types:
                warn("No aerodynamic surface: every face is treated as carrying air loads. Select the upper and "
                     "lower skin of the wing and add 'Aerodynamic surface'.", "setup")
            else:
                ok("Aerodynamic surface marked", "setup")
            flow = s.get("flow_direction", "+x")
            if str(flow).lstrip("+-") == s.get("span_axis", "y"):
                err("The air flows along the span direction: choose a span axis across the flow", "setup",
                    '[data-setting="span_axis"]')
            for b in self.data["bcs"]:
                if b["type"] == "added_mass" and b["faces"] and not float(b["params"].get("mass_kg") or 0):
                    warn(f"Added mass '{b['name']}' is 0 kg", "setup")
            if float(s.get("design_speed_ms") or 0) and float(s.get("speed_max_ms") or 0)                     and float(s["design_speed_ms"]) * 1.15 > float(s["speed_max_ms"]):
                warn("'Highest air speed' is below 1.15 x the design speed: the flutter margin cannot be shown "
                     "unless the sweep reaches it", "setup", '[data-setting="speed_max_ms"]')
        elif st == "static_structural":
            if not any(t in types for t in ("fixed_support", "frictionless_support", "displacement")):
                err("Add a support (fixed, frictionless/pin or prescribed displacement) so the part is held", "setup")
            elif "fixed_support" not in types and "displacement" not in types:
                warn("Only frictionless supports: check they stop the part sliding and turning in every direction "
                     "(otherwise the solve reports that the part is not held)", "setup")
            loads = [b for b in self.data["bcs"] if b["faces"] and (
                (b["type"] == "force" and any(float(b["params"].get(k) or 0) for k in ("fx_N", "fy_N", "fz_N")))
                or (b["type"] == "pressure" and float(b["params"].get("pressure_Pa") or 0))
                or (b["type"] == "displacement" and any(b["params"].get(k) not in (None, "", 0, 0.0)
                                                        for k in ("ux_mm", "uy_mm", "uz_mm"))))]
            if not loads and self.data["settings"].get("gravity", "off") == "off":
                err("Add a load: a force, a pressure, a non-zero displacement, or switch on gravity", "setup")
        elif st == "external_aero" and self.data["rotors"]:
            fixed = [r.get("thrust_N") for r in self.data["rotors"]]
            if all(t not in (None, "") for t in fixed):
                s = self.data["settings"]
                W = float(s.get("mass_kg") or 0) * 9.81
                lift = sum(float(t) for t in fixed) * math.cos(math.radians(float(s.get("pitch_deg") or 0)))
                if W > 0 and abs(lift - W) > 0.10 * W:  # (a trim also carries frame forces: allow 10 %)
                    warn(f"The fixed rotor thrusts ({sum(float(t) for t in fixed):.2f} N) carry {lift:.2f} N vertically "
                         f"but the weight is {W:.2f} N: reset the thrusts to automatic, or apply the trim again",
                         "setup")
        if st == "cht_enclosure":
            zone_warn = []
            for msg in self._zone_problems(zone_warn):
                err(msg, "setup")
            for msg in zone_warn:
                warn(msg, "setup")
            for z in self.data.get("moving_zones") or []:
                ok(f"Fan zone {z['name']}: {z['rpm']:g} rpm (MRF)", "setup")
        if st == "cht_enclosure" and self.data["settings"].get("air_flow") == "natural":
            s = self.data["settings"]
            if s.get("gravity", "-z") == "off":
                err("No fan: natural convection needs gravity - set the gravity direction", "setup")
            if "velocity_inlet" in types:
                err("No fan is selected but there is an air inlet: remove it, or set Air flow to 'fan'", "setup")
            box = self.data["domain"].get("enclosure_mode") == "auto_box"
            if not box and not {"pressure_outlet", "wall_fixed_temperature", "wall_convection"} & set(types):
                err("No fan: the heat needs a way out - mark vents as 'Air outlet / opening', or give the enclosure "
                    "walls a heat loss to the room (or use an automatic air box, open to the room)", "setup")
            elif box:
                ok("No fan: the sides of the air box are open to the room", "setup")
            if s.get("radiation") != "on":
                warn("No fan and no radiation: radiation often carries 30-50 % of the heat - switch it on", "setup")
        if st == "cht_enclosure":
            if self.data["settings"].get("air_flow") == "natural":
                pass
            elif "velocity_inlet" not in types:
                err("Mark the air inlet face(s) (or use 'Auto-detect inlet/outlet')", "setup")
            if self.data["settings"].get("air_flow") != "natural" and "pressure_outlet" not in types:
                err("Mark the air outlet face(s)", "setup")
            heat = sum(float(self.data["bodies"].get(str(b["id"]), {}).get("heat_W") or 0) for b in solids)
            if heat <= 0:
                err("Set the heat generated by at least one part (e.g. the chip)", "setup")
            else:
                ok(f"Total heat load {heat:g} W", "setup")
            gap = self._outlet_distance()
            if gap and gap[0] < 5 * gap[1]:
                warn(f"The outlet is only {gap[0] * 1e3:.0f} mm ({gap[0] / gap[1]:.1f} duct diameters) behind the parts: "
                     f"the pressure has not recovered there, so the pressure drop depends on where the outlet is. "
                     f"Temperatures are hardly affected. For a reliable pressure drop extend the duct to "
                     f">= {5 * gap[1] * 1e3:.0f} mm behind the parts.", "setup")
        elif st == "external_aero":
            s = self.data["settings"]
            zone_warn = []
            for msg in self._zone_problems(zone_warn):
                err(msg, "setup")
            for msg in zone_warn:
                warn(msg, "setup")
            for z in self.data.get("moving_zones") or []:
                ok(f"Moving zone {z['name']}: {z['rpm']:g} rpm, "
                   f"{'sliding mesh (transient)' if z['method'] == 'sliding' else 'MRF (rotating frame)'}", "setup")
                if z["method"] == "mrf" and s.get("analysis") == "transient":
                    warn(f"Moving zone {z['name']} uses MRF with a transient analysis: MRF is a steady method (the mesh "
                         "does not turn), so the run takes many time steps for the same answer. Use Analysis = steady "
                         "(much faster), or Sliding mesh to rotate the part in time", "setup")
            if float(s.get("speed", 0)) <= 0 and not self.data["rotors"]:
                err("Set a flow speed or add rotors", "setup", '[data-setting="speed"]')
            if not self.data["rotors"]:
                ok("No rotors: forces, moments and coefficients of the body are computed", "setup")
            elif (not float(s.get("mass_kg") or 0)
                  and any(r.get("thrust_N") in (None, "") for r in self.data["rotors"])):
                err("Enter the vehicle mass: automatic rotor thrust carries the weight (or set each rotor's thrust)",
                    "setup", '[data-setting="mass_kg"]')
            else:
                ok(f"{len(self.data['rotors'])} rotors, thrust "
                   f"{self._rotor_thrust_text()}", "setup")
        m = self.data["mesh"]
        if m.get("status") == "ready":
            q = m.get("stats", {}).get("quality", {})
            ok(f"Mesh: {m['stats']['cells']:,} cells", "mesh")
            fp = self.frame_plan() if st == "external_aero" else None
            if fp:
                msg = (f"Saved times: {fp['frames']} (every {fp['deg']:.3g}° of turn), about {fp['gb']:.0f} GB on the disk "
                       f"({fp['free_gb']:.0f} GB free)")
                if fp["gb"] > 0.9 * fp["free_gb"]:
                    err(msg + ". The disk will not hold them: raise 'Saved frame every' (degrees), shorten the end time "
                        "or free disk space", "setup")
                elif fp["gb"] > 0.5 * fp["free_gb"]:
                    warn(msg + ": more than half of the free space. Raise 'Saved frame every' if you do not need a "
                         "smooth video", "setup")
                else:
                    ok(msg, "setup")
            if q.get("n_poor", 0) > 0:
                warn(f"{q['n_poor']} poor-quality cells (quality < 0.1)", "mesh")
            foam = m.get("stats", {}).get("foam_quality")
            if foam and foam.get("non_orthogonality_max", 0) > 75:
                warn(f"Max non-orthogonality {foam['non_orthogonality_max']:.0f}° (>75° can make OpenFOAM unstable)",
                     "mesh")
        elif m.get("status") == "stale":
            warn("Mesh is out of date - regenerate it", "mesh")
        else:
            err("Generate the mesh", "mesh")
        return out

    def _outlet_distance(self):
        """(distance from the parts to the outlet plane, duct hydraulic diameter) in m, or None."""
        fa = self.face_map()
        out = [fa[i] for b in self.data["bcs"] if b["type"] == "pressure_outlet" for i in b["faces"] if i in fa]
        solids = [b for b in self.bodies() if b["role"] == "solid" and b.get("bbox")]
        if not out or not solids:
            return None
        A = sum(f["area"] for f in out)
        n = np.asarray(out[0]["normal"], float)
        n = n / (np.linalg.norm(n) or 1.0)
        c = sum(np.asarray(f["centroid"], float) * f["area"] for f in out) / A
        # furthest extent of the parts towards the outlet (bbox corners projected on the outlet direction)
        far = max(float(np.dot([bb[i] if (k >> i) & 1 == 0 else bb[i + 3] for i in range(3)], n))
                  for bb in (s["bbox"] for s in solids) for k in range(8))
        d = float(np.dot(c, n)) - far
        return (d, math.sqrt(A)) if d > 0 else None

    def _rotor_thrust_text(self):
        from .solvers.openfoam.cases import rotor_thrusts

        t = rotor_thrusts(self.data["settings"], self.data["rotors"], self._n_rotors_total())
        return ", ".join(f"{x:.2f} N" for x in t)

    def _flow_dir(self):
        from .solvers.openfoam.cases import freestream_velocity

        s = dict(self.data["settings"])
        s["speed"] = 1.0
        return freestream_velocity(s)

    # ------------------------------------------------------------------ mesh
    def hex_unsupported(self):
        """Why this study cannot use the hex-dominant (snappyHexMesh) mesh; None when it can."""
        if self.study != "external_aero":
            return "The hex-dominant mesh is for external aerodynamics."
        if self.data["geometry"].get("status") != "ready":
            return "Import geometry first."
        from .solvers.openfoam import hexmesh

        return hexmesh.unsupported(self.data, self.faces(), self.data["geometry"].get("info") or {})

    def set_mesh(self, preset=None, size_max_mm=None, size_min_mm=None, refinements=None, regions=None,
                 layers=None, defeature_mm=False, method=None, edge_refinement=None):
        if self.busy():
            raise RuntimeError("Please wait: a job is still running for this project (or cancel it).")
        m = self.data["mesh"]
        if method is not None:
            if method not in ("tet", "hex"):
                raise ValueError("Mesh method must be 'tet' (tetrahedral, Gmsh) or 'hex' (hex-dominant, snappyHexMesh)")
            if method == "hex":
                why = self.hex_unsupported()
                if why:
                    raise ValueError(why)
            m["method"] = method
        if preset:
            from .gmsh_worker import PRESETS

            if preset not in PRESETS:
                raise ValueError(f"preset must be one of {list(PRESETS)}")
            m["preset"] = preset
        for key, val in (("size_max", size_max_mm), ("size_min", size_min_mm)):
            if val is None:
                continue
            if val in ("", 0):
                m[key] = None  # automatic
                continue
            v = studies.finite(val, "Mesh size")
            if v <= 0:
                raise ValueError("Mesh sizes must be > 0 mm (leave empty for automatic)")
            m[key] = v / 1000
        if refinements is not None:
            fm = self.face_map()
            out = []
            for r in refinements:
                size = studies.finite(r["size_mm"], "Refinement size")
                if size <= 0:
                    raise ValueError("Refinement size must be > 0 mm")
                faces = [int(f) for f in r["faces"]]
                bad = [f for f in faces if f not in fm]
                if bad:
                    raise ValueError(f"Unknown faces {bad}")
                out.append({"faces": faces, "size": size / 1000, **({"part": str(r["part"])} if r.get("part") else {})})
            m["refinements"] = out
        if regions is not None:  # refinement regions (metres): boxes {name, min, max, size} or
            out = []             # cylinders {name, shape: "cylinder", start, end, radius, [inner_radius], size}
            for i, r in enumerate(regions):
                nm = str(r.get("name") or f"region{i + 1}").strip()[:40]
                size = studies.finite(r.get("size"), f"{nm} cell size")
                if size <= 0:
                    raise ValueError(f"{nm}: cell size must be > 0")
                if r.get("shape") == "cylinder":
                    a = [studies.finite(x, f"{nm} start") for x in r.get("start", [])]
                    b = [studies.finite(x, f"{nm} end") for x in r.get("end", [])]
                    rad = studies.finite(r.get("radius"), f"{nm} radius")
                    if len(a) != 3 or len(b) != 3 or math.dist(a, b) <= 0:
                        raise ValueError(f"{nm}: the cylinder needs different start and end points")
                    if rad <= 0:
                        raise ValueError(f"{nm}: radius must be > 0")
                    ri = studies.finite(r.get("inner_radius") or 0, f"{nm} inner radius")
                    if not 0 <= ri < rad:
                        raise ValueError(f"{nm}: the inner radius must be 0 (solid) or less than the radius")
                    # (min / max: the bounding box, for the views and checks that work on boxes)
                    ax = np.subtract(b, a) / math.dist(a, b)
                    ext = rad * np.sqrt(np.clip(1 - ax ** 2, 0, 1))
                    lo = (np.minimum(a, b) - ext).tolist()
                    hi = (np.maximum(a, b) + ext).tolist()
                    out.append({"name": nm, "shape": "cylinder", "start": a, "end": b, "radius": rad,
                                **({"inner_radius": ri} if ri > 0 else {}), "min": lo, "max": hi, "size": size})
                    continue
                lo = [studies.finite(x, f"{nm} min") for x in r.get("min", [])]
                hi = [studies.finite(x, f"{nm} max") for x in r.get("max", [])]
                if len(lo) != 3 or len(hi) != 3 or any(b <= a for a, b in zip(lo, hi)):
                    raise ValueError(f"{nm}: the box needs min < max in x, y and z")
                out.append({"name": nm, "min": lo, "max": hi, "size": size})
            m["regions"] = out
        if layers is not None:  # prism (inflation) layers on the walls, added by OpenFOAM before the run
            if self.study != "external_aero":
                raise ValueError("Wall layers are available in external aerodynamics")
            n = int(studies.finite(layers.get("n", 0), "Number of layers"))
            g = studies.finite(layers.get("growth", 1.2), "Layer growth")
            first = layers.get("first_mm")
            first = None if first in (None, "", 0) else studies.finite(first, "First layer thickness")
            if not 0 <= n <= 20:
                raise ValueError("Number of wall layers must be 0..20 (0 = off)")
            if not 1.0 <= g <= 2.0:
                raise ValueError("Layer growth must be between 1.0 and 2.0")
            if first is not None and first <= 0:
                raise ValueError("First layer thickness must be > 0 mm (empty = automatic)")
            m["layers"] = {"n": n, "growth": g, "first": None if first is None else first / 1000,
                           "tolerant": bool(layers.get("tolerant", False))}
        if edge_refinement is not None:  # fine cells along sharp edges (leading / trailing edges): off | True | factor
            if edge_refinement in (False, 0, "off"):
                m.pop("edge_refine", None)
            else:
                f = 6.0 if edge_refinement is True else studies.finite(edge_refinement, "Edge refinement factor")
                if not 2.0 <= f <= 20.0:
                    raise ValueError("Edge refinement factor must be 2..20 (cells at the edge are the wall size / factor)")
                m["edge_refine"] = {"factor": f}
        if defeature_mm is not False:  # small-face groups merged into the face they sit on: None / 0 = off
            d = studies.finite(defeature_mm or 0, "Defeature size")
            if not 0 <= d <= 50:
                raise ValueError("Defeature size must be 0..50 mm (0 = off)")
            if d:
                m["defeature"] = d / 1000
            else:
                m.pop("defeature", None)
        # (the tetrahedral mesh gets its layers at the run; the hex-dominant mesh makes them with the mesh)
        only_layers = (layers is not None and preset is None and size_max_mm is None and size_min_mm is None
                       and refinements is None and regions is None and method is None and edge_refinement is None
                       and m.get("method") != "hex")
        self.mark_stale(("solve",) if only_layers else ("mesh", "solve"))  # (layers are added at the run)
        self.save()

    def generate_mesh(self) -> Job:
        if self.data["geometry"].get("status") != "ready":
            raise ValueError("Import geometry first")
        return JOBS.start("mesh", self.id, self._mesh_work)

    def _hex_mesh_work(self, job: Job, p0=0.0, p1=1.0):
        """Hex-dominant mesh with snappyHexMesh (see solvers/openfoam/hexmesh.py): plan, write the meshing case, run
        OpenFOAM's blockMesh / surfaceFeatureExtract / snappyHexMesh / createPatch / checkMesh, then store the result
        like the tetrahedral mesh (display arrays in mesh.npz, the real OpenFOAM mesh in polyMesh_hex)."""
        from .solvers.openfoam import cases, hexmesh, runner

        t0 = time.time()
        why = self.hex_unsupported()
        if why:
            raise ValueError(why + " Choose the tetrahedral mesh.")
        det = runner.detect(refresh=True)
        if not det["available"]:
            raise RuntimeError("The hex-dominant mesh needs OpenFOAM, which is not installed: " + runner.install_help())
        data, faces = self.data, self.faces()
        info = data["geometry"]["info"]
        fl = materials.get_fluid(data["settings"].get("fluid", "air"))
        s2p, patches = cases.build_patches("external_aero", faces, data.get("bcs", []))
        plan = hexmesh.make_plan(data, faces, info, s2p, patches, cases.freestream_velocity(data["settings"]),
                                 fl["mu"] / fl["rho"])
        case = self.mesh_dir / "hexcase"
        shutil.rmtree(case, ignore_errors=True)
        case.mkdir(parents=True, exist_ok=True)
        JOBS.update(job, p0 + 0.05 * (p1 - p0), "Writing the meshing case", log=f"Hex-dominant mesh: {plan['cells_bg']} background "
                    f"cells of {plan['base'] * 1000:.3g} mm, {plan['levels']} levels, surface cells {plan['h_wall'] * 1000:.3g} mm")
        hexmesh.write_case(case, plan, faces)
        prev = data["mesh"].get("status")
        data["mesh"]["status"] = "meshing"
        try:
            ok = False
            if det["backend"] == "windows":
                def on_proc(p):
                    job.proc = p
                    job.check_cancel()

                def on_line(line):
                    JOBS.update(job, None, line if line.startswith("$") else None, log=line)

                JOBS.update(job, p0 + 0.1 * (p1 - p0), "Meshing with snappyHexMesh (about a minute per 100,000 cells)")
                ok = runner.run_steps_windows(case, hexmesh.STEPS, det, on_proc, on_line)
            else:
                lines = ["#!/bin/bash", 'cd "${0%/*}" || exit 1']
                for st in hexmesh.STEPS:
                    lines.append(f"{st['app']} {' '.join(st['args'])} > {st['log']} 2>&1" + ("" if st.get("allow_fail") else " || exit 1"))
                (case / "Allmesh").write_text("\n".join(lines) + "\n", newline="\n")
                cmd = runner.build_command(case, "Allmesh")
                proc = subprocess.Popen(cmd, cwd=case, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                        bufsize=1, shell=isinstance(cmd, str))
                job.proc = proc
                for line in proc.stdout:
                    if line.strip():
                        JOBS.update(job, None, None, log=line.rstrip(), quiet=True)
                ok = proc.wait() == 0
            job.proc = None
            job.check_cancel()
            hexmesh.finalize(case)
            if not ok:
                bad = next((st for st in hexmesh.STEPS if not st.get("allow_fail") and (case / st["log"]).exists()
                            and "FOAM FATAL" in (case / st["log"]).read_text(errors="ignore")), None)
                tail = ""
                if bad:
                    txt = (case / bad["log"]).read_text(errors="ignore")
                    i = txt.find("FOAM FATAL")
                    tail = txt[i:i + 700].strip()
                raise RuntimeError(f"The hex-dominant mesh could not be made ({bad['app'] if bad else 'OpenFOAM'} failed)."
                                   + (f"\n{tail}" if tail else "") + "\nTry the tetrahedral mesh or a coarser preset.")
            JOBS.update(job, p0 + 0.85 * (p1 - p0), "Reading the mesh")
            fluid_id = next(b["id"] for b in self.bodies() if b["role"] == "fluid")
            arr, pm = hexmesh.display_arrays(case, plan, s2p, patches, faces, fluid_id)
            lg = hexmesh.parse_logs(case)
            if not lg["cells"]:
                raise RuntimeError("The meshing case produced no cells: see " + str(case / "log.snappyHexMesh"))
            hexdir = self.mesh_dir / "polyMesh_hex"
            shutil.rmtree(hexdir, ignore_errors=True)
            shutil.copytree(case / "constant" / "polyMesh", hexdir)
            np.savez_compressed(self.mesh_dir / "mesh.npz", **arr)
        except BaseException:
            data["mesh"]["status"] = "failed" if prev in (None, "none", "failed") else "stale"
            self.save()
            raise
        q = arr["quality"]
        hist, edges = np.histogram(q, bins=20, range=(0, 1))
        stats = {
            "method": "hex", "cells": lg["cells"], "nodes": int(len(pm["points"])), "boundary_triangles": int(len(arr["tris"])),
            "cells_per_body": {str(fluid_id): lg["cells"]},
            "cell_types": lg["types"],
            "quality": {"metric": "1 - worst non-orthogonality of the cell / 90 degrees (display; 1 = orthogonal)",
                        "min": float(q.min()), "mean": float(q.mean()), "p01": float(np.percentile(q, 1)),
                        "n_poor": int((q < 0.1).sum()), "histogram": hist.tolist(), "edges": edges.tolist()},
            "foam_quality": {"non_orthogonality_max": lg["non_orthogonality_max"] or 0.0,
                             "non_orthogonality_mean": lg["non_orthogonality_mean"] or 0.0, "n_non_ortho_gt70": 0,
                             "skewness_max": lg["skewness_max"] or 0.0, "n_skew_gt4": 0},
            "hex": {"levels": plan["levels"], "base_mm": plan["base"] * 1000, "background_cells": plan["cells_bg"],
                    "surface_cell_mm": plan["h_wall"] * 1000, "aspect_ratio_max": lg["aspect_ratio_max"],
                    "mesh_ok": lg["mesh_ok"], "failed_checks": lg["failed_checks"], "problems": lg["problems"],
                    "concave_cells": lg["concave_cells"], "layers": lg["layers"],
                    "layers_extruded_percent": lg["layers_extruded_percent"], "warnings": lg["warnings"],
                    "wall_patches": [{"name": p["name"], "levels": sorted({g["level"] for g in p["groups"]})}
                                     for p in plan["wall_patches"]]},
            "sizes": {"near_wall": plan["h_wall"], "body": plan["h_body"], "far": plan["base"], "feature": plan["h_wall"],
                      "min_gap": None, "channels": [], "gap_cell": None, "cells_across_gap": None},
            "seconds": round(time.time() - t0, 1),
        }
        with self._lock:
            self.data["mesh"]["status"] = "ready"
            self.data["mesh"]["stats"] = stats
            self.data["solve"]["status"] = "none"
            self._results = None
            self.save()
        JOBS.update(job, p1, f"Mesh ready: {stats['cells']:,} cells", log=f"Mesh ready: {stats['cells']:,} cells "
                    f"({lg['types']['hexahedra']:,} hexahedra)")
        return stats

    def _mesh_work(self, job: Job, p0=0.0, p1=1.0):
        """Mesh the project and record the RAM the work needed (stats['ram']: peak of this process and the programs it
        started, minus what they held before)."""
        from . import memwatch

        with memwatch.PeakMemory() as pm:
            stats = self._mesh_work_inner(job, p0, p1)
        with self._lock:
            if isinstance(stats, dict) and (self.data["mesh"].get("stats") or {}).get("cells") == stats.get("cells"):
                backend = None
                if self.data["mesh"].get("method") == "hex" and self.study == "external_aero":
                    from .solvers.openfoam import runner

                    backend = runner.detect().get("backend")        # snappyHexMesh runs where OpenFOAM does
                if backend is not None and not memwatch.measurable_backend(backend):
                    stats["ram"] = {"measured": False, "note": f"not measured: the mesher runs in {backend}"}
                else:
                    stats["ram"] = dict(pm.result(), measured=True)
                self.data["mesh"]["stats"] = stats
                self.save()
        return stats

    def _mesh_work_inner(self, job: Job, p0=0.0, p1=1.0):
        if self.data["mesh"].get("method") == "hex" and self.study == "external_aero":
            return self._hex_mesh_work(job, p0, p1)
        payload = {
            "study": self.study, "out_dir": str(self.mesh_dir), "brep": str(self.geo_dir / "domain.brep"),
            "bodies_json": str(self.geo_dir / "bodies.json"), "faces_json": str(self.geo_dir / "faces.json"),
            "domain_info": str(self.geo_dir / "domain_info.json"),
            "mesh": self.data["mesh"], "rotors": self._rotors_with_wake(),
            "moving_zones": [dict(z, axis=list(studies.AXES[z["axis"]])) for z in self.data.get("moving_zones") or []],
        }
        prev = self.data["mesh"].get("status")
        self.data["mesh"]["status"] = "meshing"
        try:
            stats = run_gmsh_task(JOBS, job, "mesh", payload, self.mesh_dir, p0, p0 + 0.9 * (p1 - p0))
        except BaseException:
            self.data["mesh"]["status"] = "failed" if prev in (None, "none", "failed") else "stale"
            self.save()
            raise
        if self.spec["solver"].startswith("openfoam"):
            JOBS.update(job, 0.92, "Checking OpenFOAM mesh quality")
            stats["foam_quality"] = self._foam_quality()
        with self._lock:
            self.data["mesh"]["status"] = "ready"
            self.data["mesh"]["stats"] = stats
            self.data["solve"]["status"] = "none"
            self._results = None
            self.save()
        return stats

    # ------------------------------------------------------------------ mesh sensitivity
    KEY_OUTPUTS = {
        "heat_conduction": [("Max temperature", "°C", lambda s: s.get("T_max"))],
        "static_structural": [("Max deformation", "mm", lambda s: s.get("max_displacement_mm")),
                              ("Max von Mises stress", "MPa", lambda s: s.get("max_von_mises_MPa"))],
        "aeroelastic_flutter": [("Flutter speed", "m/s", lambda s: (s.get("flutter") or {}).get("speed_m_s")),
                                ("Flutter frequency", "Hz", lambda s: (s.get("flutter") or {}).get("frequency_Hz")),
                                ("First natural frequency", "Hz", lambda s: s.get("first_frequency_Hz"))],
        "vibration_modal": [(f"Mode {i} frequency", "Hz",
                             (lambda i: lambda s: next((m["frequency_Hz"] for m in s.get("modes", [])
                                                        if m.get("elastic_index") == i), None))(i)) for i in (1, 2, 3)],
        "cht_enclosure": [("Hottest part temperature", "°C", lambda s: (s.get("hottest_part") or {}).get("T_max")),
                          ("Thermal resistance", "K/W", lambda s: s.get("thermal_resistance_K_per_W")),
                          ("Pressure drop", "Pa", lambda s: s.get("pressure_drop_Pa"))],
        "external_aero": [("Drag", "N", lambda s: s.get("drag_N")),
                          ("Total drag (frame + rotor model)", "N", lambda s: s.get("total_drag_N")),
                          ("Vertical aero force", "N", lambda s: s.get("vertical_aero_force_N")),
                          ("Suggested pitch", "deg", lambda s: s.get("suggested_pitch_deg")),
                          ("Trim thrust", "N", lambda s: s.get("trim_thrust_total_N")),
                          ("Aero pitch moment", "N·m", lambda s: s.get("aero_pitch_moment_Nm"))],
    }
    # changes of quantities that can be close to zero are judged against a meaningful scale
    CHECK_SCALE = {
        "Vertical aero force": lambda s: abs(s.get("drag_N") or 0.0) + 0.02 * abs(s.get("rotor_thrust_total_N") or 0.0),
        "Suggested pitch": lambda s: 5.0,
        # pitch moment relative to the moment of the total drag about a 0.1 m lever
        "Aero pitch moment": lambda s: 0.1 * abs(s.get("total_drag_N") or s.get("drag_N") or 0.0) + 1e-3,
    }

    def mesh_check(self, levels: int = 1) -> Job:
        """Re-mesh one preset finer in a copy of the project, re-solve, and compare the key outputs."""
        from .gmsh_worker import PRESETS

        if self.busy():
            raise RuntimeError("Please wait: a job is still running for this project (or cancel it).")
        if self.data["solve"].get("status") == "stale":
            raise ValueError("The results are out of date (the setup changed): run the simulation again first")
        if self.data["solve"].get("status") != "done":
            raise ValueError("Solve the project first; the check compares against the current results")
        if not ((self.data["solve"].get("summary") or {}).get("convergence") or {}).get("converged", True):
            raise ValueError("The current run has not converged: continue it first - otherwise the check would "
                             "compare iteration error, not mesh error")
        order = list(PRESETS)
        cur = self.data["mesh"].get("preset", "medium")
        if cur not in order or order.index(cur) == len(order) - 1:
            raise ValueError("Already on the finest preset - set a smaller near-wall size manually instead")
        nxt = order[order.index(cur) + 1]
        nxt2 = None
        if int(levels) == 2:
            if order.index(cur) + 2 >= len(order):
                raise ValueError("A three-level check needs two finer presets - use the single-level check here")
            nxt2 = order[order.index(cur) + 2]
        job = JOBS.start("check", self.id, self._mesh_check_work, cur, nxt, nxt2)
        job.extra["check_kind"] = "mesh check"
        return job

    def _mesh_check_work(self, job: Job, cur: str, nxt: str, nxt2: str | None = None):
        cid = f"{self.id}-meshcheck"
        cdir = WORKSPACE / ".checks" / self.id
        if cdir.exists():
            with _LOCK:
                _OPEN.pop(cid, None)
            shutil.rmtree(cdir)
        cdir.parent.mkdir(exist_ok=True)
        shutil.copytree(self.dir, cdir, ignore=shutil.ignore_patterns("mesh", "case", "results", "*.tmp"))
        twin = Project(cdir)
        twin.data["name"] = f"{self.data['name']} (mesh check: {nxt})"
        twin.data["hidden"] = True
        twin.data["mesh"].update(preset=nxt, status="none", stats={})
        twin.data["solve"] = {"status": "none"}
        twin.save(quiet=True)
        with _LOCK:
            _OPEN[cid] = twin
        JOBS.update(job, 0.02, f"Meshing with the '{nxt}' preset", log=f"Mesh check: {cur} -> {nxt}"
                    + (f" -> {nxt2}" if nxt2 else ""))
        job.phase = (0.0, 0.5, f"Level 2 of 3 ({nxt})") if nxt2 else None
        try:
            twin._mesh_work(job, 0.0, 0.3)
            JOBS.update(job, 0.3, "Solving on the finer mesh")
            if self.spec["solver"] == "builtin_fem":
                twin._solve_builtin(job)
            else:
                # start from the current solution mapped onto the finer mesh: far fewer iterations to converge
                twin._solve_openfoam(job, map_from=self.case_dir)
        finally:
            job.phase = None
        c, cells3 = None, None
        if nxt2:  # third level for a grid-convergence index (Roache / Celik et al. 2008)
            cid3, cdir3 = f"{self.id}-meshcheck3", WORKSPACE / ".checks" / f"{self.id}-l3"
            if cdir3.exists():
                with _LOCK:
                    _OPEN.pop(cid3, None)
                shutil.rmtree(cdir3)
            shutil.copytree(cdir, cdir3, ignore=shutil.ignore_patterns("mesh", "case", "results", "*.tmp"))
            twin3 = Project(cdir3)
            twin3.data["name"] = f"{self.data['name']} (mesh check: {nxt2})"
            twin3.data["mesh"].update(preset=nxt2, status="none", stats={})
            twin3.data["solve"] = {"status": "none"}
            twin3.save(quiet=True)
            with _LOCK:
                _OPEN[cid3] = twin3
            job.phase = (0.5, 1.0, f"Level 3 of 3 ({nxt2})")
            try:
                twin3._mesh_work(job, 0.0, 0.3)
                JOBS.update(job, 0.3, "Solving on the finest mesh")
                if self.spec["solver"] == "builtin_fem":
                    twin3._solve_builtin(job)
                else:
                    twin3._solve_openfoam(job, map_from=twin.case_dir)
            finally:
                job.phase = None
            c, cells3 = twin3.data["solve"]["summary"], twin3.data["mesh"]["stats"]["cells"]
        a, b = self.data["solve"]["summary"], twin.data["solve"]["summary"]
        N1, N2 = self.data["mesh"]["stats"]["cells"], twin.data["mesh"]["stats"]["cells"]
        rows = []
        worst = 0.0
        # temperature drift still to come in each run (Aitken estimate of the stop test): the iteration error
        drift = max(((s.get("convergence") or {}).get("monitor") or {}).get("remaining_drift_K") or 0.0 for s in (a, b))
        for name, unit, get in self.KEY_OUTPUTS[self.study]:
            va, vb = get(a), get(b)
            if va is None or vb is None:
                continue
            # temperatures: compare the rise above the reference, not the absolute value
            ref = 0.0
            if unit == "°C":
                ref = a.get("inlet_T", a.get("T_min", 0.0)) or 0.0
            denom = max(abs(vb - ref), self.CHECK_SCALE.get(name, lambda _s: 0.0)(b), 1e-9)
            ch = abs(vb - va) / denom * 100
            worst = max(worst, ch)
            raw = abs(vb - va) / max(abs(vb - ref), 1e-9) * 100  # unscaled, e.g. a small vertical force
            row = {"name": name, "unit": unit, "coarse": va, "fine": vb, "change_percent": ch,
                   "raw_change_percent": raw}
            if c is not None and get(c) is not None:
                # formal order in the cell size: 2 for the CFD schemes; 4 for eigenvalues of quadratic elements
                # formal order 2 in the cell size: CFD schemes; for modes the clamp edges and faceted curved faces
                # limit quadratic elements to about 2 as well (measured)
                row.update(fine2=get(c), **_gci(va - ref, vb - ref, get(c) - ref, N1, N2, cells3, 2.0))
            unc = drift if unit == "°C" else drift / (a.get("heat_input_W") or 1e9) if unit == "K/W" else None
            if unit == "Pa":  # pressure-drop unsteadiness band of the two runs
                band = max(((x.get("convergence") or {}).get("monitor") or {}).get("dp_band_percent") or 0.0
                           for x in (a, b))
                unc = band / 100 * abs(vb) if band else None
            if unc:
                row["iteration_uncertainty"] = unc
                row["resolved"] = abs(vb - va) > 2 * unc
            rows.append(row)
        verdict = ("Mesh-independent within 2 %." if worst < 2 else
                   "Close to mesh-independent (2-5 % change)." if worst < 5 else
                   f"Results changed by up to {worst:.0f} % - use the finer mesh (and consider checking one level "
                   f"finer again).")
        big_raw = [r for r in rows if r["raw_change_percent"] > 10 and r["raw_change_percent"] > r["change_percent"] + 1]
        if big_raw:
            verdict += " " + "; ".join(f"{r['name']} changed by {r['raw_change_percent']:.0f} % of its own (small) value"
                                       for r in big_raw) + " - small compared with the loads it is judged against."
        if c is not None:
            verdict = _three_level_verdict(rows, a)
            conv_c = (c.get("convergence") or {})
            if not conv_c.get("converged", True):
                verdict = "Not conclusive: the finest run has not settled. " + verdict
        conv_b = (b.get("convergence") or {})
        if not conv_b.get("converged", True):
            verdict = (f"Not conclusive: the finer run has not settled ({conv_b.get('iterations')} iterations) - "
                       "continue the check project or re-run the check. ") + verdict
        elif any(r.get("resolved") is False for r in rows):
            verdict += (" Not resolved (change within twice the iteration uncertainty): "
                        + ", ".join(r["name"] for r in rows if r.get("resolved") is False)
                        + " - mesh and iteration error cannot be separated at this level.")
        if self.study == "vibration_modal":  # modes are matched by order: close pairs can swap between meshes
            close = sorted({(i + 1, i + 2) for x in (a, b, c) if x
                            for fe in [[m["frequency_Hz"] for m in x.get("modes", []) if not m.get("rigid_body")][:4]]
                            for i in range(len(fe) - 1) if fe[i + 1] - fe[i] < 0.02 * fe[i]})
            if close:
                verdict += (" Modes " + ", ".join(f"{x} and {y}" for x, y in close) + " are within 2 % of each other, "
                            "so they may swap order between meshes - compare their shapes, not only the numbers.")
        res = {"stale": False, "from": cur, "to": nxt, "to2": nxt2, "cells_to2": cells3, "gci_rules": GCI_RULES,
               "cells_from": self.data["mesh"]["stats"]["cells"],
               "cells_to": twin.data["mesh"]["stats"]["cells"], "rows": rows, "max_change_percent": worst,
               "verdict": verdict, "check_project": cid, "finished": _now()}
        self.data["mesh_check"] = res
        self.save()
        return res

    def model_check(self) -> Job:
        """Heatsink in the transitional range: solve the same mesh with the other flow model (laminar <-> k-omega SST)
        in a copy of the project and compare the key outputs (brackets the regime uncertainty)."""
        if self.study != "cht_enclosure":
            raise ValueError("The flow-model comparison is for heatsink (CHT) studies")
        if self.busy():
            raise RuntimeError("Please wait: a job is still running for this project (or cancel it).")
        if self.data["solve"].get("status") != "done":
            raise ValueError("Run the simulation first (the results must be current)")
        if not ((self.data["solve"].get("summary") or {}).get("convergence") or {}).get("converged", True):
            raise ValueError("The current run has not converged: continue it first")
        meta = json.loads((self.case_dir / "simwb_case.json").read_text())
        used = meta.get("turbulence") or "laminar"
        other = "kOmegaSST" if used == "laminar" else "laminar"
        job = JOBS.start("check", self.id, self._model_check_work, used, other)
        job.extra["check_kind"] = "flow-model comparison"
        return job

    def _model_check_work(self, job: Job, used: str, other: str):
        cid = f"{self.id}-modelcheck"
        cdir = WORKSPACE / ".checks" / f"{self.id}-model"
        if cdir.exists():
            with _LOCK:
                _OPEN.pop(cid, None)
            shutil.rmtree(cdir)
        cdir.parent.mkdir(exist_ok=True)
        # same geometry, setup and mesh; only the flow model differs
        shutil.copytree(self.dir, cdir, ignore=shutil.ignore_patterns("case", "results", "*.tmp"))
        twin = Project(cdir)
        twin.data["name"] = f"{self.data['name']} (flow model: {other})"
        twin.data["hidden"] = True
        twin.data["settings"]["turbulence"] = other
        twin.data["solve"] = {"status": "none"}
        twin.save(quiet=True)
        with _LOCK:
            _OPEN[cid] = twin
        label = {"laminar": "laminar", "kOmegaSST": "k-ω SST"}
        JOBS.update(job, 0.02, f"Solving the same mesh with the {label[other]} model",
                    log=f"Flow-model comparison: {used} -> {other}")
        twin._solve_openfoam(job, map_from=self.case_dir)
        a, b = self.data["solve"]["summary"], twin.data["solve"]["summary"]
        rows = []
        drift = max(((s.get("convergence") or {}).get("monitor") or {}).get("remaining_drift_K") or 0.0 for s in (a, b))
        band = max(((s.get("convergence") or {}).get("monitor") or {}).get("dp_band_percent") or 0.0 for s in (a, b))
        mc = self.data.get("mesh_check") or {}
        for name, unit, get in self.KEY_OUTPUTS[self.study]:
            if name == "Thermal resistance":
                continue  # same percentage as the hottest-part temperature rise
            va, vb = get(a), get(b)
            if va is None or vb is None:
                continue
            ref = (a.get("inlet_T") or 0.0) if unit == "°C" else 0.0
            row = {"name": name, "unit": unit, "a": va, "b": vb,
                   "change_percent": abs(vb - va) / max(abs(va - ref), 1e-9) * 100,
                   # higher temperature / resistance / pressure drop is the safe side for design
                   "conservative": max(va, vb), "conservative_model": label[used] if va >= vb else label[other]}
            unc = (drift if unit == "°C" else drift / (a.get("heat_input_W") or 1e9) if unit == "K/W"
                   else band / 100 * abs(vb) if unit == "Pa" and band else None)
            if unc:
                row["iteration_uncertainty"] = unc
                row["resolved"] = abs(vb - va) > 2 * unc
            mrow = next((r for r in mc.get("rows", []) if r["name"] == name), None) if not mc.get("stale") else None
            mesh_pct = None
            if mrow:
                mesh_pct = mrow["gci_percent"] if mrow.get("gci_percent") is not None else mrow.get("change_percent")
            if mesh_pct is not None:
                # the model spread is a range, not a 1-sigma error: it is covered by taking the conservative value;
                # mesh and iteration uncertainties are independent estimates and are combined around that value
                it_pct = (unc or 0.0) / max(abs(va - ref), 1e-9) * 100
                row["combined_uncertainty_percent"] = (mesh_pct ** 2 + it_pct ** 2) ** 0.5
            rows.append(row)
        conv_b = (b.get("convergence") or {}).get("converged", False)
        worst = max((r["change_percent"] for r in rows), default=0.0)
        mesh_ok = bool(mc) and not mc.get("stale") and (mc.get("max_change_percent") or 99) < 5
        dp = next((r for r in rows if r["unit"] == "Pa"), None)
        note = (f"{label[used]} (this run) vs {label[other]} on the same mesh: the outputs differ by up to {worst:.0f} %. "
                "This is the spread between the two models; real transitional behaviour (turbulence from the fan, "
                "separation at the fin leading edges) can fall outside it."
                + (f" For design margins use the higher values, e.g. pressure drop {dp['conservative']:.2f} Pa "
                   f"({dp['conservative_model']}) for the fan selection." if dp else "")
                + ("" if mesh_ok else " Both runs use the current mesh, which has not passed a mesh-sensitivity "
                                      "check: near-wall heat transfer on a coarse mesh makes the models look more alike "
                                      "than they are, so treat the spread as indicative.")
                + ("" if conv_b else " The comparison run has not converged, so treat the difference as indicative."))
        res = {"stale": False, "from": used, "to": other, "rows": rows, "max_change_percent": worst, "note": note,
               "converged": conv_b, "check_project": cid, "finished": _now()}
        self.data["model_check"] = res
        self.save()
        return res

    def _rotors_with_wake(self):
        """Rotor list with the slipstream direction: free stream plus the far-wake induced velocity (2 v_i)."""
        if self.study != "external_aero" or not self.data.get("rotors"):
            return self.data.get("rotors", [])
        from .solvers.openfoam.cases import freestream_velocity, rotor_thrusts

        s = self.data["settings"]
        rho = materials.get_fluid(s.get("fluid", "air"))["rho"]
        Uinf = freestream_velocity(s)
        out = []
        for r, T in zip(self.data["rotors"], rotor_thrusts(s, self.data["rotors"], self._n_rotors_total())):
            a = np.asarray(r["axis"], float)
            A = math.pi * r["radius"] ** 2
            # Glauert momentum theory in forward flight: v_i = T / (2 rho A |V_inf + v_i a|), fixed-point iteration
            Vn = float(-Uinf @ a)              # free-stream component through the disk (along -axis)
            Vt = float(np.linalg.norm(Uinf + Vn * a))  # in-plane component
            vi = math.sqrt(abs(T) / (2 * rho * A)) if T else 0.0
            for _ in range(50):
                vi = abs(T) / (2 * rho * A * max(math.hypot(Vt, Vn + vi), 1e-6)) if T else 0.0
            w = Uinf - 2 * vi * a
            if np.linalg.norm(w) < 1e-9:
                w = -a
            out.append(dict(r, wake_dir=(w / np.linalg.norm(w)).tolist()))
        return out

    def _foam_quality(self):
        from .solvers.openfoam.foamio import polymesh_quality, tet_mesh_to_polymesh

        st = self.data["mesh"].get("stats") or {}
        if st.get("method") == "hex" and st.get("foam_quality"):
            return st["foam_quality"]  # (checkMesh's numbers of the real snappyHexMesh mesh)
        m = self.load_mesh()
        pm = tet_mesh_to_polymesh(m["nodes"], m["tets"], np.zeros((0, 3), int), [], [], [])
        return polymesh_quality(pm)

    def load_mesh(self):
        p = self.mesh_dir / "mesh.npz"
        if not p.exists():
            raise ValueError("No mesh yet")
        m = dict(np.load(p))
        if "hex" in m:  # a snappyHexMesh mesh: the arrays are for display, the case takes the OpenFOAM mesh itself
            m["hex_dir"] = str(self.mesh_dir / "polyMesh_hex")
        return m

    def mesh_view(self, clip_axis=None, clip_pos=None, hide_fluid=False, poor=None, color="quality"):
        """Surface (or crinkle-clipped) mesh triangles for inspection, coloured by color: 'quality' (1 = a perfect cell),
        'size' (edge length of the cell, mm: how the mesh grades away from the walls) or 'region' (each body, the
        surface of the object and the far-field of a flow study: how the domain was put together). poor: only the cells
        below this quality (all their faces; the worst 20000), with the box round the worst one."""
        if color not in ("quality", "size", "region"):
            raise ValueError("color must be 'quality', 'size' or 'region'")
        m = self.load_mesh()
        if poor is not None:
            q = m["quality"]
            sel = np.where(q < float(poor))[0]
            sel = sel[np.argsort(q[sel])[:20000]]
            t = m["tets"][sel].astype(np.int64)
            tris = np.vstack([t[:, [0, 2, 1]], t[:, [0, 1, 3]], t[:, [1, 2, 3]], t[:, [0, 3, 2]]])
            cells = np.tile(np.arange(len(sel)), 4)
            rs = ResultSet(m["nodes"], t, np.zeros(len(t), int), ["all"], {"Quality": ("cell", q[sel].astype(float), "-")},
                           np.zeros((0, 3), np.int64), np.zeros(0, np.int64), np.zeros(0, np.int32), [])
            d = rs._tri_payload(tris, cells, "Quality") if len(sel) else {"n_tris": 0}
            d["n_poor"] = int((q < float(poor)).sum())
            if len(sel):
                w = m["nodes"][t[0]]
                c, r = w.mean(0), max(float(np.ptp(w, 0).max()), 1e-6)
                d["worst"] = {"quality": float(q[sel[0]]), "center": c.tolist(), "size": r}
            return d
        bodies = self.bodies()
        regions = [str(b["id"]) for b in bodies]
        reg_index = {b["id"]: i for i, b in enumerate(bodies)}
        tr = np.vectorize(reg_index.get)(m["tet_vol"]) if len(bodies) > 1 else np.zeros(len(m["tets"]), int)
        # the other ways to colour the cells: the edge of the equal-volume regular tet (mm) and the body each one is in
        corner = m["nodes"][m["tets"]]
        vol = np.abs(np.einsum("ij,ij->i", corner[:, 1] - corner[:, 0],
                               np.cross(corner[:, 2] - corner[:, 0], corner[:, 3] - corner[:, 0]))) / 6.0
        size_mm = (6.0 * math.sqrt(2.0) * vol) ** (1.0 / 3.0) * 1000.0
        rs = ResultSet(m["nodes"], m["tets"], tr, regions,
                       {"Quality": ("cell", m["quality"].astype(float), "-"), "Cell size": ("cell", size_mm, "mm")},
                       *((m["tris"].astype(np.int64), m["tri_owner"]) if "tri_owner" in m else boundary_from_tets(m["tets"])),
                       np.zeros(0), [])
        rs.surf_group = np.zeros(len(rs.surf_tris), np.int32)

        def paint(tris, cells):
            if color == "region":
                return self._region_payload(rs, m, tris, cells, bodies, tr)
            d = rs._tri_payload(tris, cells, "Cell size" if color == "size" else "Quality")
            if color == "size":
                d["scale"] = "log"  # (sizes span a factor of 50 or more: equal steps of the ratio, not of the size)
            return d

        hide = [str(b["id"]) for b in bodies if b["role"] == "fluid"] if hide_fluid else []
        if clip_axis is None:
            if hide:
                keep = ~np.isin(tr, [regions.index(h) for h in hide])
                sub = np.where(keep)[0]
                tris, cells = boundary_from_tets(m["tets"][sub])
                return paint(tris, sub[cells])
            # show the CAD boundary (exterior + body interfaces), without the far-field box planes
            far = [f["id"] for f in self.faces() if f.get("kind") == "farfield"]
            keep = ~np.isin(m["tri_surf"], far)
            d = paint(m["tris"][keep].astype(np.int64), _owner_of_tris(m)[keep])
            zt = m.get("zone_tris")
            if zt is not None and len(zt):  # moving-zone boundaries (inside the air): their mesh edges as lines
                e = np.sort(np.vstack([zt[:, [0, 1]], zt[:, [1, 2]], zt[:, [2, 0]]]), axis=1)
                e = np.unique(e, axis=0)
                d["zone_lines"] = m["nodes"][e].astype(np.float32).ravel().tolist()
            return d
        tris, cells = rs.clip_tris("xyz".index(clip_axis), float(clip_pos), hide_regions=hide)
        return paint(tris, cells)

    def _region_payload(self, rs, m, tris, cells, bodies, tr):
        """The triangles coloured by what they belong to: a category per body and, in an external flow study, the surface
        of the object (the wall left where it was cut out of the air) and the far-field planes. 'categories' name and
        colour them, 'present' lists the ones drawn."""
        pal = ["#4e9fd8", "#6fbf73", "#e0b341", "#c07be0", "#e07b7b", "#5cc7c7", "#a3b86c", "#d98c5f"]
        cats = [{"name": str(b.get("name") or b["id"]), "color": pal[i % len(pal)]} for i, b in enumerate(bodies)]
        fv = tr[cells].astype(np.float32) if len(cells) else np.zeros(0, np.float32)
        if len(tris) and "tri_surf" in m and self.study == "external_aero":
            kind = {f["id"]: f.get("kind") for f in self.faces()}

            def digest(T):  # one number per triangle, the same for its corners in any order
                T = np.sort(np.asarray(T, np.int64), axis=1).astype(np.uint64)
                with np.errstate(over="ignore"):
                    return T[:, 0] * np.uint64(73856093) ^ T[:, 1] * np.uint64(19349663) ^ T[:, 2] * np.uint64(83492791)
            h = digest(tris)
            for label, col, want in (("Object surface", "#f08a24", "body"), ("Far-field", "#7d8799", "farfield")):
                sel = np.isin(m["tri_surf"], [i for i, k in kind.items() if k == want])
                if sel.any():
                    hit = np.isin(h, digest(m["tris"][sel]))
                    if hit.any():
                        fv[hit] = len(cats)
                        cats.append({"name": label, "color": col})
        d = rs._tri_payload(tris, cells, "Cell size", face_vals=fv)
        d["unit"] = "-"
        d["categories"] = cats
        d["present"] = [int(i) for i in np.unique(fv)] if len(fv) else []
        return d

    # ------------------------------------------------------------------ solve
    def solver_status(self):
        if self.spec["solver"] == "builtin_fem":
            return {"available": True, "backend": "builtin", "detail": "Built-in finite element solver"}
        from .solvers.openfoam import runner

        return runner.detect()

    def init_source(self, init_from) -> Path | None:
        """Case folder of the study whose latest solution starts this run (init_from: its id), checked."""
        if not init_from:
            return None
        if init_from == self.id:
            raise ValueError("A study cannot start from its own solution: continue the run instead")
        if not self.spec["solver"].startswith("openfoam") or self.study != "external_aero":
            raise ValueError("Starting from another solution is available for external aerodynamics studies")
        try:
            src = open_project(str(init_from))
        except (KeyError, FileNotFoundError, ValueError):
            raise ValueError(f"No study with the id {init_from!r}") from None
        if src.study != self.study:
            raise ValueError("The study to start from must be of the same type")
        if src.data["solve"].get("status") != "done":
            raise ValueError(f"{src.data['name']} has no finished run to start from")
        g1, g2 = (self.data["geometry"].get("info") or {}), (src.data["geometry"].get("info") or {})
        if g1.get("parts_bbox") and g2.get("parts_bbox") and not np.allclose(g1["parts_bbox"], g2["parts_bbox"], atol=1e-6):
            raise ValueError(f"{src.data['name']} has other geometry: start from a study of the same parts")
        return src.case_dir

    def solve(self, export_only=False, continue_iterations=None, init_from=None, end_time=None,
              write_interval=None) -> Job:
        if end_time is not None:  # carry a finished transient run on to a later end time
            return self._extend_transient(end_time, write_interval)
        if continue_iterations:
            if self.spec["solver"] == "builtin_fem":
                raise ValueError("The built-in solver is direct - there is nothing to continue")
            if not (self.case_dir / "simwb_case.json").exists():
                raise ValueError("No previous OpenFOAM run to continue")
            if self.busy():
                raise RuntimeError("Please wait: a job is still running for this project (or cancel it).")
            if self.data["solve"].get("status") in ("stale", "none") or self.data["solve"].get("setup_changed"):
                raise ValueError("The setup or mesh changed since the last run: run the simulation again instead of "
                                 "continuing the old case")
            meta = json.loads((self.case_dir / "simwb_case.json").read_text())
            if meta.get("transient"):
                # an interrupted transient run (Cancel, a crash, the machine stopped) resumes from its last saved time
                # to its end time; a finished one has to be run again with a later end time
                from .solvers.openfoam import runner

                if self.data["solve"].get("status") not in ("interrupted", "failed"):
                    raise ValueError("A finished transient run is continued to a later end time (end_time)")
                t0 = runner.saved_transient_time(self.case_dir)
                if t0 is None or t0 <= 0:
                    raise ValueError("The interrupted run saved no time step yet: run it again")
                if t0 >= meta["transient"]["end_time"] * (1 - 1e-6):
                    raise ValueError("The interrupted run had already reached its end time: run it again")
                return JOBS.start("solve", self.id, self._solve_openfoam, False, None, resume_transient=True)
            n = int(studies.finite(continue_iterations, "Extra iterations"))
            if not 1 <= n <= 100000:
                raise ValueError("Extra iterations must be between 1 and 100000")
            return JOBS.start("solve", self.id, self._solve_openfoam, False, n)
        if self.busy():
            raise RuntimeError("Please wait: a job is still running for this project (or cancel it).")
        if self.data["mesh"].get("status") != "ready":
            raise ValueError("Generate the mesh first")
        errs = [c["message"] for c in self.check() if c["level"] == "error"]
        if errs:
            raise ValueError("Setup is not complete:\n- " + "\n- ".join(errs))
        if self.spec["solver"] == "builtin_fem":
            return JOBS.start("solve", self.id, self._solve_builtin)
        init_case = self.init_source(init_from)
        if init_case is not None:
            return JOBS.start("solve", self.id, self._solve_openfoam, export_only, init_case=init_case)
        if (self.study == "external_aero" and not export_only and self.data["rotors"]
                and float(self.data["settings"].get("mass_kg") or 0) > 0
                and self.data["settings"].get("auto_trim", "on") == "on"
                and self.data["settings"].get("analysis", "steady") == "steady"):
            return JOBS.start("solve", self.id, self._solve_aero_trimmed)
        return JOBS.start("solve", self.id, self._solve_openfoam, export_only)

    def apply_trim(self, _internal=False) -> dict:
        """Set the pitch and rotor thrusts of the last result's level-flight trim estimate (aero)."""
        if self.study != "external_aero":
            raise ValueError("Trim applies to drone (external aerodynamics) studies only")
        if self.busy() and not _internal:
            raise RuntimeError("Please wait: a job is still running for this project (or cancel it).")
        summ = self.data["solve"].get("summary") or {}
        pitch, T = summ.get("suggested_pitch_deg"), summ.get("trim_thrust_total_N")
        if self.study != "external_aero" or pitch is None or not T:
            raise ValueError("No trim estimate available - run the simulation with a vehicle mass and flight speed")
        split = summ.get("rotor_thrust_trim_N") or []
        if len(split) != len(self.data["rotors"]) or sum(split) <= 0:
            split = [1.0] * len(self.data["rotors"])
        for r, t in zip(self.data["rotors"], split):
            r["thrust_N"] = round(T * t / sum(split), 4)  # moment-balancing split, scaled to the trim thrust
        self.data["settings"]["pitch_deg"] = round(float(pitch), 1)
        had_edit = self.data.get("edited_during_run")
        self.mark_stale(("solve",))
        if _internal and not had_edit:  # auto trim's own change is not a user edit during the run
            self.data.pop("edited_during_run", None)
        self.save()
        return {"pitch_deg": self.data["settings"]["pitch_deg"], "rotor_thrust_N": [r["thrust_N"] for r in self.data["rotors"]]}

    def _solve_aero_trimmed(self, job: Job):
        """Solve; if the result is clearly off trim, apply the trim estimate and solve once more."""
        job.phase = (0.0, 0.5, "Run 1 of up to 2")
        try:
            summ = self._solve_openfoam(job)
        finally:
            job.phase = None
        pitch, T = summ.get("suggested_pitch_deg"), summ.get("trim_thrust_total_N")
        p0, T0 = float(self.data["settings"].get("pitch_deg", 0.0)), summ.get("rotor_thrust_total_N") or 0.0
        if pitch is None or not T or not T0 or (abs(pitch - p0) <= 1.0 and abs(T - T0) <= 0.03 * T0):
            return summ
        if job.extra.get("user_stop") or not (summ.get("convergence") or {}).get("converged") \
                or self.data["solve"].get("status") != "done":
            # a stopped, unsettled or since-edited first run is no basis for changing the user's pitch and thrusts
            return summ
        first = {"pitch_deg": p0, "thrust_N": T0, "drag_N": summ.get("drag_N"), "total_drag_N": summ.get("total_drag_N"),
                 "suggested_pitch_deg": pitch, "suggested_thrust_N": T}
        msg = f"Not trimmed at {p0:g}° / {T0:.2f} N: re-running at {pitch:.1f}° / {T:.2f} N (auto trim)"
        JOBS.update(job, 0.5, msg, log=msg)
        job.extra.update(solver_started=False, trim_phase=True)  # Stop is refused until run 2's case is written
        run1 = json.loads(json.dumps(self.data["solve"]))  # to fall back on if run 2 does not finish
        user = {"pitch_deg": p0, "thrusts": [r.get("thrust_N") for r in self.data["rotors"]]}
        self.apply_trim(_internal=True)
        remeshed = abs(self.data["settings"]["pitch_deg"] - p0) > 2.0
        job.phase = (0.5, 1.0, f"Run 2 of 2 (trimmed {self.data['settings']['pitch_deg']:g}°)")
        try:
            if remeshed:  # the rotor-wake refinement follows the wake direction, which depends on the pitch
                JOBS.update(job, 0.02, "Regenerating the mesh for the new pitch (rotor wake refinement)",
                            log="Re-meshing for the trimmed pitch")
                self._mesh_work(job, 0.0, 0.1)
            summ = self._solve_openfoam(job, keep_edit_flag=True)
        except BaseException as e:
            # cancelled or failed: keep the finished run 1 and give the user their pitch and thrusts back
            job.phase = None
            with self._lock:
                self.data["settings"]["pitch_deg"] = user["pitch_deg"]
                for r, t in zip(self.data["rotors"], user["thrusts"]):
                    r["thrust_N"] = t
                what = "cancelled" if isinstance(e, Cancelled) else "failed"
                if run1.get("summary"):
                    run1["summary"]["trim_history_note"] = (f"Auto-trim run 2 (pitch {pitch:.1f}°) was {what}; these "
                                                            f"are the results of run 1 at {p0:g}°.")
                self.data["solve"] = dict(run1, note=f"Auto-trim run 2 {what}: showing run 1 at {p0:g}°, with your "
                                                     "pitch and thrusts restored." + (
                    " The mesh was regenerated for the trimmed pitch, so the results are marked out of date."
                    if remeshed else ""))
                if remeshed:
                    self.data["solve"]["status"] = "stale"
                    self.data["mesh"]["status"] = "stale"  # its wake refinement follows the trimmed pitch
                    self.data.setdefault("notes", []).append(
                        f"The mesh was regenerated for {pitch:.1f}° during auto trim; regenerate it for {p0:g}° "
                        "before the next run.")
                self.save()
            job.extra["final_message"] = f"run 2 {what}, showing run 1 at {p0:g}°"
            JOBS.update(job, None, f"Run 2 {what} - showing run 1 at {p0:g}°", log=f"Run 2 {what}: run 1 restored")
            raise
        finally:
            job.phase = None
        settled2 = (summ.get("convergence") or {}).get("converged", False)
        summ["trim_history"] = [first, {"pitch_deg": self.data["settings"]["pitch_deg"],
                                        "thrust_N": summ.get("rotor_thrust_total_N"), "drag_N": summ.get("drag_N"),
                                        "total_drag_N": summ.get("total_drag_N"),
                                        "suggested_pitch_deg": summ.get("suggested_pitch_deg") if settled2 else None}]
        summ["trim_history_note"] = (f"First run at {p0:g}° was not trimmed; these results are from the re-run at "
                                     f"{self.data['settings']['pitch_deg']:g}° with the rotor thrusts set to the trim "
                                     "values" + (" on a mesh regenerated for the new wake direction." if remeshed else
                                                 " (pitch change < 2°: same mesh).")
                                     + (f" Still {abs(summ['suggested_pitch_deg'] - self.data['settings']['pitch_deg']):.1f}° "
                                        "from trim: apply the trim again." if summ.get("suggested_pitch_deg") is not None
                                        and abs(summ["suggested_pitch_deg"] - self.data["settings"]["pitch_deg"]) > 1.0
                                        else ""))
        if not settled2:
            summ["trim_history_note"] += (f" Run 2 was stopped after {(summ.get('convergence') or {}).get('iterations')} "
                                          "iterations: its forces are not final - continue it.")
        self.data.setdefault("notes", []).append("Auto trim set the pitch and explicit rotor thrusts; clear a rotor's "
                                                 "thrust to make it automatic again.")
        self.data["solve"]["summary"] = summ
        self.save()
        return summ

    def reset_thrusts(self) -> dict:
        """Rotor thrusts back to automatic (carry the weight at the set pitch)."""
        if self.busy():
            raise RuntimeError("Please wait: a job is still running for this project (or cancel it).")
        for r in self.data["rotors"]:
            r["thrust_N"] = None
        self.mark_stale(("solve",))
        self.save()
        return self.state()

    def stop_solve(self) -> Job:
        """Ask the running OpenFOAM solve to write the current solution and finish (results are kept)."""
        job = JOBS.latest(self.id)
        if not job or job.status != "running" or job.kind != "solve":
            raise ValueError("No solver run is in progress")
        if self.spec["solver"] == "builtin_fem":
            raise ValueError("This run cannot be stopped early - cancel it instead")
        if not job.extra.get("solver_started"):
            raise ValueError(("Auto trim is preparing the second run (new pitch and mesh)" if job.extra.get("trim_phase")
                              else "The solver has not started yet (the case is still being prepared)")
                             + " - try again in a few seconds, or cancel the run")
        cd = self.case_dir / "system" / "controlDict"
        cd.write_text(re.sub(r"^stopAt\s+\w+;", "stopAt          writeNow;", cd.read_text(), flags=re.M), newline="\n")
        job.extra["user_stop"] = True
        JOBS.update(job, None, "Stopping: writing the current solution", log="Stop requested: writing results now")
        return job

    def _solve_builtin(self, job: Job):
        if self.study == "vibration_modal":
            return self._solve_modal(job)
        if self.study == "aeroelastic_flutter":
            return self._solve_flutter(job)
        if self.study == "static_structural":
            return self._solve_static(job)
        from .solvers import thermal_fem

        self.data["solve"] = {"status": "running", "backend": "builtin", "started": _now()}
        self.save()
        m = self.load_mesh()
        props = {}
        for b in self.bodies():
            cfg = self.data["bodies"].get(str(b["id"]), {})
            mat = materials.get_solid(cfg.get("material") or "aluminium_6061")
            props[b["id"]] = {"k": mat["k"], "heat_W": cfg.get("heat_W", 0.0), "name": cfg.get("name") or b["name"],
                              "rho_cp": mat["rho"] * mat["cp"]}
        log = lambda msg: JOBS.update(job, None, msg, log=msg)  # noqa: E731
        s = self.data["settings"]
        trans = None
        if s.get("analysis") == "transient":
            trans = {"end_time": float(s["end_time"]), "dt": float(s.get("time_step") or 0), "T0": float(s["T_initial"]),
                  "n_frames": int(s.get("n_frames") or 21)}
        JOBS.update(job, 0.1, "Solving heat conduction" + (" over time" if trans else ""))
        try:
            res = thermal_fem.solve(m, props, self.data["bcs"], log=log, transient=trans,
                                    progress=lambda f, msg: JOBS.update(job, 0.1 + 0.8 * f, msg))
            imb = res["report"].get("energy_balance", {}).get("imbalance_percent", 0.0)
            if imb > 1.0 or not np.isfinite(res["T"]).all():
                raise RuntimeError(f"The heat balance does not close ({imb:.0f} % off): the heat has no effective way "
                                   "out. Add a fixed temperature or a convection boundary with h > 0.")
        except BaseException as e:
            self.data["solve"] = {"status": "failed", "backend": "builtin", "error_tail": str(e)}
            self.save()
            raise
        JOBS.update(job, 0.9, "Saving results")
        bodies = self.bodies()
        reg_index = {b["id"]: i for i, b in enumerate(bodies)}
        tr = np.array([reg_index[int(v)] for v in m["tet_vol"]]) if len(bodies) > 1 else np.zeros(len(m["tets"]), int)
        groups, sgroup = self._tri_groups(m)
        fr = res.get("frames")
        rs = ResultSet(m["nodes"], m["tets"], tr, [self._body_view(b)["name"] for b in bodies],
                       {"Temperature": ("node", res["T"], "°C"), "Heat flux": ("cell", res["q_mag"], "W/m²")},
                       m["tris"], _owner_of_tris(m), sgroup, groups,
                       times=fr and fr["times"], frames=fr and {"Temperature": fr["T"], "Heat flux": fr["q_mag"]})
        self.results_dir.mkdir(exist_ok=True)
        rs.save(self.results_dir / "result.npz")
        self._results = rs
        rep = res["report"]
        self._remember_previous()
        self.data["solve"] = {"status": "done", "backend": "builtin", "finished": _now(), "summary": rep}
        self.save()
        return rep

    def _solve_modal(self, job: Job):
        from .solvers import modal_fem

        self.data["solve"] = {"status": "running", "backend": "builtin", "started": _now()}
        self.save()
        m = self.load_mesh()
        props = {}
        for b in self.bodies():
            cfg = self.data["bodies"].get(str(b["id"]), {})
            mat = materials.get_solid(cfg.get("material") or "aluminium_6061")
            props[b["id"]] = {"E": mat["E"], "nu": mat["nu"], "rho": mat["rho"], "yield": mat.get("Sy"),
                              "name": cfg.get("name") or b["name"]}
        s = self.data["settings"]
        sine = None
        if s.get("shake_axis") in ("x", "y", "z"):
            sine = {"axis": s["shake_axis"], "accel_g": float(s.get("shake_accel_g") or 1.0),
                    "damping_percent": float(s.get("damping_percent") or 2.0)}
        lo, hi = float(s.get("excitation_min_hz") or 0), float(s.get("excitation_max_hz") or 0)
        # from 0 up to X = the whole range 0..X; only "from" set = that single frequency
        band = (lo, hi) if hi else ((lo, lo) if lo else None)
        log = lambda msg: JOBS.update(job, None, msg, log=msg)  # noqa: E731
        engine = self._structural_engine()
        self.data["solve"]["backend"] = engine
        JOBS.update(job, 0.1, "Computing natural frequencies" + (" (CalculiX)" if engine == "calculix" else ""))
        try:
            if engine == "calculix":
                from .solvers import calculix

                res = calculix.solve_modal(m, props, self.data["bcs"], n_modes=int(s.get("n_modes") or 10),
                                           band_hz=band, log=log, workdir=self.case_dir.parent / "calculix_case",
                                           on_proc=self._job_proc(job))
            else:
                res = modal_fem.solve(m, props, self.data["bcs"], n_modes=int(s.get("n_modes") or 10), band_hz=band,
                                      log=log, sine=sine)
        except BaseException as e:
            self.data["solve"] = {"status": "failed", "backend": engine, "error_tail": str(e)}
            self.save()
            raise
        JOBS.update(job, 0.9, "Saving results")
        bodies = self.bodies()
        reg_index = {b["id"]: i for i, b in enumerate(bodies)}
        tr = np.array([reg_index[int(v)] for v in m["tet_vol"]]) if len(bodies) > 1 else np.zeros(len(m["tets"]), int)
        groups, sgroup = self._tri_groups(m)
        fields = {}
        for mode, shape in zip(res["modes"], res["shapes"]):
            if not mode["rigid_body"]:  # (rigid-body motion of a free part is not a vibration mode)
                fields[f"Mode {mode['elastic_index']}"] = ("node", shape.astype(np.float32), "relative")
        if not fields:
            raise RuntimeError("Only rigid-body modes were found: request more modes")
        for name, (val, unit) in (res.get("fields") or {}).items():  # shake test: amplitudes at the worst frequency
            scale, u2 = {"Pa": (1e-6, "MPa"), "m": (1e3, "mm")}.get(unit, (1.0, unit))
            fields[name] = ("node", (val * scale).astype(np.float32), u2)
        if sine and engine == "calculix":
            res["report"].setdefault("warnings", []).append(
                "Shake test skipped: it is computed by the built-in solver only (set Solver to built-in).")
        rs = ResultSet(m["nodes"], m["tets"], tr, [self._body_view(b)["name"] for b in bodies], fields,
                       m["tris"], _owner_of_tris(m), sgroup, groups)
        self.results_dir.mkdir(exist_ok=True)
        rs.save(self.results_dir / "result.npz")
        self._results = rs
        rep = res["report"]
        self._remember_previous()
        self.data["solve"] = {"status": "done", "backend": engine, "finished": _now(), "summary": rep}
        self.save()
        return rep

    def _solve_flutter(self, job: Job):
        """Flutter study: modal FEM of the structure coupled to strip aerodynamics (simwb.solvers.flutter_study)."""
        from .solvers import flutter_study

        self.data["solve"] = {"status": "running", "backend": "builtin", "started": _now()}
        self.save()
        m = self.load_mesh()
        props = {}
        for b in self.bodies():
            cfg = self.data["bodies"].get(str(b["id"]), {})
            mat = materials.get_solid(cfg.get("material") or "aluminium_6061")
            props[b["id"]] = {"E": mat["E"], "nu": mat["nu"], "rho": mat["rho"], "yield": mat.get("Sy"),
                              "name": cfg.get("name") or b["name"]}
        log = lambda msg: JOBS.update(job, None, msg, log=msg)  # noqa: E731
        JOBS.update(job, 0.1, "Structure modes and air forces")
        try:
            res = flutter_study.solve_study(m, props, self.data["bcs"], self.data["settings"], log=log)
        except BaseException as e:
            self.data["solve"] = {"status": "failed", "backend": "builtin", "error_tail": str(e)}
            self.save()
            raise
        JOBS.update(job, 0.9, "Saving results")
        bodies = self.bodies()
        reg_index = {b["id"]: i for i, b in enumerate(bodies)}
        tr = np.array([reg_index[int(v)] for v in m["tet_vol"]]) if len(bodies) > 1 else np.zeros(len(m["tets"]), int)
        groups, sgroup = self._tri_groups(m)
        fields = {}
        for name, vec in res["fields"].items():  # the flutter and divergence shapes first
            fields[name] = ("node", vec.astype(np.float32), "relative")
        for mode, shape in zip(res["modes"], res["shapes"]):
            if not mode["rigid_body"]:
                fields[f"Mode {mode['elastic_index']}"] = ("node", shape.astype(np.float32), "relative")
        rs = ResultSet(m["nodes"], m["tets"], tr, [self._body_view(b)["name"] for b in bodies], fields,
                       m["tris"], _owner_of_tris(m), sgroup, groups)
        self.results_dir.mkdir(exist_ok=True)
        rs.save(self.results_dir / "result.npz")
        self._results = rs
        rep = res["report"]
        self._remember_previous()
        self.data["solve"] = {"status": "done", "backend": "builtin", "finished": _now(), "summary": rep}
        self.save()
        return rep

    def _solve_static(self, job: Job):
        from .solvers import static_fem
        from .studies import AXES

        self.data["solve"] = {"status": "running", "backend": "builtin", "started": _now()}
        self.save()
        m = self.load_mesh()
        props = {}
        for b in self.bodies():
            cfg = self.data["bodies"].get(str(b["id"]), {})
            mat = materials.get_solid(cfg.get("material") or "aluminium_6061")
            props[b["id"]] = {"E": mat["E"], "nu": mat["nu"], "rho": mat["rho"], "yield": mat.get("Sy"),
                              "name": cfg.get("name") or b["name"]}
        gdir = self.data["settings"].get("gravity", "off")
        grav = None if gdir == "off" else (9.81 * np.array(AXES[gdir], float)).tolist()
        log = lambda msg: JOBS.update(job, None, msg, log=msg)  # noqa: E731
        engine = self._structural_engine()
        self.data["solve"]["backend"] = engine
        JOBS.update(job, 0.1, "Solving for stress and deformation" + (" (CalculiX)" if engine == "calculix" else ""))
        try:
            if engine == "calculix":
                from .solvers import calculix

                res = calculix.solve_static(m, props, self.data["bcs"], gravity=grav, log=log,
                                            workdir=self.case_dir.parent / "calculix_case", on_proc=self._job_proc(job))
            else:
                res = static_fem.solve(m, props, self.data["bcs"], gravity=grav, log=log)
        except BaseException as e:
            self.data["solve"] = {"status": "failed", "backend": engine, "error_tail": str(e)}
            self.save()
            raise
        JOBS.update(job, 0.9, "Saving results")
        bodies = self.bodies()
        reg_index = {b["id"]: i for i, b in enumerate(bodies)}
        tr = np.array([reg_index[int(v)] for v in m["tet_vol"]]) if len(bodies) > 1 else np.zeros(len(m["tets"]), int)
        groups, sgroup = self._tri_groups(m)
        d = res["displacement"]
        fields = {"Von Mises stress": ("node", (res["von_mises"] / 1e6).astype(np.float32), "MPa"),
                  "Total deformation": ("node", (d * 1e3).astype(np.float32), "mm")}
        for name, (val, unit) in (res.get("fields") or {}).items():  # principal stresses, error estimate, safety
            fields[name] = ("node", (val / 1e6 if unit == "Pa" else val).astype(np.float32),
                            "MPa" if unit == "Pa" else unit)
        rs = ResultSet(m["nodes"], m["tets"], tr, [self._body_view(b)["name"] for b in bodies], fields,
                       m["tris"], _owner_of_tris(m), sgroup, groups)
        self.results_dir.mkdir(exist_ok=True)
        rs.save(self.results_dir / "result.npz")
        self._results = rs
        rep = res["report"]
        self._remember_previous()
        self.data["solve"] = {"status": "done", "backend": engine, "finished": _now(), "summary": rep}
        self.save()
        return rep

    def _structural_engine(self) -> str:
        """Which program solves a structural study: "builtin" (the workbench's own FEM) or "calculix"."""
        e = self.data["settings"].get("structural_solver", "built-in")
        return "calculix" if str(e).lower().startswith("calculix") else "builtin"

    @staticmethod
    def _job_proc(job):
        def on_proc(p):
            job.proc = p
            job.check_cancel()
        return on_proc

    def _tri_groups(self, m):
        f2bc = {f: b["name"] for b in self.data["bcs"] for f in b["faces"]}
        fm = self.face_map()
        names = []
        idx = {}
        sg = np.zeros(len(m["tri_surf"]), np.int32)
        for s in np.unique(m["tri_surf"]):
            f = fm.get(int(s), {})
            nm = f2bc.get(int(s)) or ("contact" if f.get("interface") else "unassigned")
            if nm not in idx:
                idx[nm] = len(names)
                names.append(nm)
            sg[m["tri_surf"] == s] = idx[nm]
        return names, sg

    def _extend_transient(self, end_time, write_interval=None) -> Job:
        """Run a finished (or stopped) transient run on from its last saved time to a later end time, saving every
        write_interval seconds from there (default: as before). The saved times so far are kept."""
        from .solvers.openfoam import runner

        if not (self.case_dir / "simwb_case.json").exists():
            raise ValueError("No previous OpenFOAM run to continue")
        if self.busy():
            raise RuntimeError("Please wait: a job is still running for this project (or cancel it).")
        if self.data["solve"].get("status") in ("stale", "none") or self.data["solve"].get("setup_changed"):
            raise ValueError("The setup or mesh changed since the last run: run the simulation again instead of "
                             "continuing the old case")
        meta = json.loads((self.case_dir / "simwb_case.json").read_text())
        if not meta.get("transient"):
            raise ValueError("Only a transient run has an end time to extend: continue a steady run by iterations")
        t0 = runner.saved_transient_time(self.case_dir)
        if t0 is None or t0 <= 0:
            raise ValueError("The run saved no time step yet: run it again")
        end = float(studies.finite(end_time, "End time"))
        wi = float(studies.finite(write_interval, "Save interval")) if write_interval else             float(meta["transient"]["write_interval"])
        if end <= t0 * (1 + 1e-9):
            raise ValueError(f"The new end time must be later than the last saved time ({t0:.6g} s)")
        if not 0 < wi <= end - t0:
            raise ValueError("The save interval must be positive and at most the extra time")
        dt = float(meta["transient"].get("dt") or 0) if meta["transient"].get("fixed_dt") else 0.0
        if dt > 0:  # whole time steps: every saved time is a solved step, and the run ends on one
            wi = max(1, round(wi / dt)) * dt
            end = t0 + max(1, round((end - t0) / wi)) * wi
        if (end - t0) / wi > 400:
            raise ValueError("That saves more than 400 more time points: use a longer save interval")
        return JOBS.start("solve", self.id, self._solve_openfoam, False, None, resume_transient=True,
                          extend=(end, wi))

    def _solve_openfoam(self, job: Job, *args, **kwargs):
        """Run the OpenFOAM case and record the RAM it needed (summary['ram'])."""
        from . import memwatch

        with memwatch.PeakMemory() as pm:
            summ = self._solve_openfoam_inner(job, *args, **kwargs)
        if isinstance(summ, dict) and "case_dir" not in summ:
            with self._lock:
                summ["ram"] = self._ram_record(pm.result(), job.extra.get("solver_ram"))
                if (self.data["solve"].get("summary") or {}) is not summ and self.data["solve"].get("summary"):
                    self.data["solve"]["summary"]["ram"] = summ["ram"]
                self.save()
        return summ

    @staticmethod
    def _ram_record(whole: dict, solver: dict | None) -> dict:
        """The RAM figures of a solve: the solver run alone (solver_added_ram_mb / solver_peak_ram_mb) and the whole
        job including reading the results back (added_ram_mb / peak_ram_mb). A solver that runs in WSL or Docker is
        invisible to the meter (it would see only wsl.exe), so nothing is reported for it."""
        from . import memwatch

        if solver is not None and not memwatch.measurable_backend(solver.get("backend")):
            return {"measured": False, "note": f"not measured: the solver runs in {solver.get('backend')}, outside this process tree"}
        rec = dict(whole, measured=True)
        if solver:
            rec.update(solver_added_ram_mb=solver.get("added_ram_mb"), solver_peak_ram_mb=solver.get("peak_ram_mb"))
        return rec

    def _solve_openfoam_inner(self, job: Job, export_only=False, continue_iterations=None, map_from=None,
                              keep_edit_flag=False, init_case=None, resume_transient=False, extend=None):
        from .solvers.openfoam import cases, runner
        from .solvers.openfoam import results_loader

        from .solvers.openfoam import installer

        if not export_only:
            installer.wait_until_ready(JOBS, job)  # the built-in OpenFOAM may still be setting itself up
        det = runner.detect(refresh=True)
        job.extra.update(solver_started=False, user_stop=False)
        if not keep_edit_flag:  # (auto trim run 2 keeps edits made while it re-meshed)
            self.data.pop("edited_during_run", None)
        prev_solve = dict(self.data["solve"])
        data = json.loads(json.dumps(self.data))
        n_req = int(self.data["settings"].get("n_procs") or 0)
        if det.get("available") and det.get("backend") == "windows" and not det.get("parallel"):
            n_procs = 1  # no MPI runtime on this machine
        else:  # 0 = Auto: from the mesh size and the physical cores
            n_procs = n_req or runner.auto_procs((self.data["mesh"].get("stats") or {}).get("cells", 0), det.get("cores"))
        data["settings"]["n_procs"] = n_procs
        resume_steps = None
        t_from = 0.0  # (a continued transient run: progress counts from here)
        if resume_transient:
            meta = json.loads((self.case_dir / "simwb_case.json").read_text())
            start = runner.saved_transient_time(self.case_dir, prune=True)  # (drops half-written later times)
            cd = self.case_dir / "system" / "controlDict"
            txt = re.sub(r"^startFrom\s+\w+;", "startFrom       latestTime;", cd.read_text(), flags=re.M)
            txt = re.sub(r"^stopAt\s+\w+;", "stopAt          endTime;", txt, flags=re.M)  # undo Stop's writeNow
            if extend:  # a later end time (and the save interval from here on)
                end, wi = extend
                for key, v in (("endTime", end), ("writeInterval", wi), ("maxDeltaT", wi)):
                    txt = re.sub(rf"^{key}\s+[^;]+;", f"{key:<16}{v:.8g};", txt, flags=re.M)
                meta["transient"].update(end_time=end, write_interval=wi,
                                         n_frames=len(runner._time_dirs(self.case_dir)) + int(round((end - start) / wi)))
                (self.case_dir / "simwb_case.json").write_text(json.dumps(meta, indent=1))
                self.data["settings"]["end_time"] = end
                self.save()
            cd.write_text(txt, newline="\n")
            old = self.case_dir / f"log.{meta['app']}"
            if old.exists():  # keep the history: the new log continues where the old one ends
                k = len(list(self.case_dir.glob(f"log.{meta['app']}.part*"))) + 1
                old.rename(self.case_dir / f"log.{meta['app']}.part{k}")
            # (an old join log would make runApplication skip the join of the new times)
            (self.case_dir / "log.reconstructPar").unlink(missing_ok=True)
            solver = [dict(st, keep_logs=True) for st in meta["steps"] if st.get("app") == meta["app"]]
            n_split = runner.processor_count(self.case_dir)
            if det.get("backend") == "windows" and n_split > 1:
                # the run is still split: carry on with the same pieces (no decomposePar - it would delete them)
                n_procs = n_split
                resume_steps = runner.parallel_steps(solver, meta["app"], n_procs, bool(meta["regions"]), all_times=True)
                resume_steps = [st for st in resume_steps if st.get("app") != "decomposePar"
                                and st.get("action") != "balance_split"]
            meta = dict(meta, steps=solver)
            (self.case_dir / "Allcontinue").write_text(  # (WSL / Linux backends; a split case runs in parallel)
                '#!/bin/bash\ncd "${0%/*}" || exit 1\n. ${WM_PROJECT_DIR:?}/bin/tools/RunFunctions\n'
                'rm -f case.done case.failed\n'
                + (f'runParallel {meta["app"]}\nrunApplication reconstructPar\n' if n_split > 1
                   else f'runApplication {meta["app"]}\n')
                + f'if grep -q "^End" log.{meta["app"]}; then touch case.done; else touch case.failed; fi\n',
                newline="\n")
            it_from = 0
            t_from = start
            msg = (f"Continuing the run from t = {start:.4g} s to {meta['transient']['end_time']:.4g} s, saving every "
                   f"{meta['transient']['write_interval']:.4g} s" if extend else
                   f"Resuming the interrupted run from t = {start:.4g} s to {meta['transient']['end_time']:.4g} s")
            JOBS.update(job, 0.05, msg, log=msg)
        elif continue_iterations:
            meta = json.loads((self.case_dir / "simwb_case.json").read_text())
            if det.get("backend") == "windows":  # a cancelled parallel run: its newest result is still split
                runner.reconstruct_windows(self.case_dir, det, bool(meta["regions"]))
            start = float(results_loader.latest_time(self.case_dir, meta["regions"][0] if meta["regions"] else None) or 0)
            cd = self.case_dir / "system" / "controlDict"
            wi = int(re.search(r"^writeInterval\s+(\d+);", cd.read_text(), re.M).group(1))
            end = int(math.ceil((start + continue_iterations) / wi) * wi)  # always finish on a write time
            txt = re.sub(r"^endTime\s+[^;]+;", f"endTime         {end};", cd.read_text(), flags=re.M)
            txt = re.sub(r"^stopAt\s+\w+;", "stopAt          endTime;", txt, flags=re.M)  # undo an auto-stop
            cd.write_text(txt, newline="\n")
            (self.case_dir / "system" / "decomposeParDict").write_text(re.sub(
                r"numberOfSubdomains\s+\d+;", f"numberOfSubdomains {n_procs};",
                (self.case_dir / "system" / "decomposeParDict").read_text()), newline="\n")
            for r in meta["regions"]:
                rd = self.case_dir / "system" / r / "decomposeParDict"
                if rd.exists():
                    rd.write_text((self.case_dir / "system" / "decomposeParDict").read_text(), newline="\n")
            solver_step = [dict(st, keep_logs=True) for st in meta["steps"] if st.get("app") == meta["app"]]
            meta = dict(meta, steps=solver_step)
            # keep the history: the new log continues where the old one ends
            old = self.case_dir / f"log.{meta['app']}"
            if old.exists():
                k = len(list(self.case_dir.glob(f"log.{meta['app']}.part*"))) + 1
                old.rename(self.case_dir / f"log.{meta['app']}.part{k}")
            (self.case_dir / "Allcontinue").write_text(
                '#!/bin/bash\ncd "${0%/*}" || exit 1\n. ${WM_PROJECT_DIR:?}/bin/tools/RunFunctions\n'
                f'rm -f case.done case.failed\nrunApplication {meta["app"]}\n'
                f'if grep -q "^End" log.{meta["app"]}; then touch case.done; else touch case.failed; fi\n',
                newline="\n")
            it_from = int(start)
            msg = f"Continuing from iteration {it_from} to {end}" + (
                f" (rounded up to the next write interval of {wi})" if end - start != continue_iterations else "")
            JOBS.update(job, 0.05, msg, log=msg)
        else:
            JOBS.update(job, 0.02, "Writing OpenFOAM case", log="Writing OpenFOAM case")
            m = self.load_mesh()
            info = self.data["geometry"]["info"]
            meta = cases.write_case(self.case_dir, data, m, self.faces(), info, self.bodies())
            from .solvers.openfoam import advanced

            advanced.apply(self.case_dir, data.get("foam_advanced"), data.get("foam_overrides"),
                           log=lambda m_: JOBS.update(job, None, None, log=m_))
            if map_from and (Path(map_from) / "case.done").exists():
                # initial fields interpolated from a previous solution on another mesh (same geometry)
                maps = [{"app": "mapFields", "args": [str(Path(map_from).resolve()), "-sourceTime", "latestTime", "-consistent"]
                         + (["-sourceRegion", r, "-targetRegion", r] if r else []), "log": f"log.mapFields{'.' + r if r else ''}",
                         "allow_fail": True} for r in (meta["regions"] or [None])]
                k = next(i for i, st in enumerate(meta["steps"]) if st.get("app") == meta["app"])
                meta["steps"][k:k] = maps
                JOBS.update(job, None, "Initialising from the previous solution (mapFields)", log="mapFields from " + str(map_from))
            if init_case is not None:
                from .solvers.openfoam import initfields

                JOBS.update(job, 0.04, "Initial flow from the other study", log=f"Initial flow from {init_case}")
                job.extra["init_from"] = initfields.map_initial(self.case_dir, init_case,
                                                                log=lambda m_: JOBS.update(job, None, None, log=m_))
            it_from = 0
            JOBS.update(job, 0.08, f"Case written: {self.case_dir}", log=f"Case written to {self.case_dir}")
        if export_only:
            self.data["solve"] = {"status": "exported", "case_dir": str(self.case_dir), "finished": _now()}
            self.save()
            return {"case_dir": str(self.case_dir)}
        if not det["available"]:
            self.data["solve"] = {"status": "exported", "case_dir": str(self.case_dir),
                                  "message": "OpenFOAM not found - case exported only"}
            self.save()
            raise RuntimeError(det.get("help") or "OpenFOAM not found")
        self.data["solve"] = {"status": "running", "backend": det["backend"], "started": _now(),
                              "case_dir": str(self.case_dir)}
        self.save()
        it_target = (end if continue_iterations else
                     it_from + int(self.data["settings"].get("iterations", 1000)))
        app_log = self.case_dir / f"log.{meta['app']}"
        state = {"running": True}

        def tail():
            last = 0
            while state["running"]:
                time.sleep(2)
                if app_log.exists():
                    txt = "".join(q.read_text(errors="ignore") for q in _log_parts(app_log)) \
                        + app_log.read_text(errors="ignore")
                    if len(txt) != last:
                        last = len(txt)
                        h = runner.parse_log(txt)
                        job.extra["residuals"] = h
                        job.extra["solver_tail"] = txt.splitlines()[-40:]  # the solver's own output, shown live
                        if h["iters"] and meta.get("transient"):
                            t_end = meta["transient"]["end_time"]
                            it = h["iters"][-1]
                            frac = min(max(it - t_from, 0) / max(t_end - t_from, 1e-30), 1)
                            el = time.time() - t_start
                            eta = f", ~{int(el / frac * (1 - frac) / 60) + 1} min more" if frac > 0.02 else ""
                            JOBS.update(job, 0.1 + 0.85 * frac,
                                        f"Time {it:.4g} of {t_end:.4g} s ({len(h['iters'])} steps){eta}")
                        elif h["iters"]:
                            it = h["iters"][-1]
                            frac = min((it - it_from) / max(it_target - it_from, 1), 1)
                            el = time.time() - t_start
                            eta = (f", at most ~{int(el / frac * (1 - frac) / 60) + 1} min more (usually stops earlier "
                                   f"once the results settle)") if frac > 0.02 else ""
                            JOBS.update(job, 0.1 + 0.85 * frac, f"Iteration {int(it)} / {it_target}{eta}")
                        mon = runner.monitor_outputs(txt, meta)
                        job.extra["monitor"] = mon
                        if mon.get("max_T_C") is not None:
                            job.extra["max_T_C"] = mon["max_T_C"]
                        if job.extra.get("user_stop") and not state.get("stopped"):
                            state["stopped"] = True  # stop_solve() already asked the solver to write and stop
                        why = None if state.get("stopped") else runner.divergence(txt)
                        if why:  # blown up: stop at once (the results would be garbage), say why
                            state["stopped"] = True
                            job.extra["diverged"] = why
                            JOBS.update(job, None, "The solution has diverged - stopping the solver", log=why)
                            pr_ = job.proc
                            if pr_ is not None and pr_.poll() is None:
                                try:
                                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pr_.pid)], capture_output=True)                                         if sys.platform == "win32" else pr_.kill()
                                except Exception:  # noqa: BLE001
                                    pass
                        lim_h = float(self.data["settings"].get("max_hours") or 0)
                        if lim_h > 0 and not state.get("stopped") and time.time() - job.started > lim_h * 3600:
                            # the wall-clock limit: write the current solution and finish, the results are kept
                            cd = self.case_dir / "system" / "controlDict"
                            cd.write_text(re.sub(r"^stopAt\s+\w+;", "stopAt          writeNow;", cd.read_text(),
                                                 flags=re.M), newline="\n")
                            state["stopped"] = True
                            job.extra["user_stop"] = job.extra["time_limit"] = True
                            JOBS.update(job, None, f"Time limit of {lim_h:g} h reached - writing the current solution",
                                        log=f"Time limit of {lim_h:g} h reached: writing the solution and stopping")
                        if mon.get("stable") and not state.get("stopped"):
                            # key outputs no longer change: ask the solver to write and stop
                            cd = self.case_dir / "system" / "controlDict"
                            cd.write_text(re.sub(r"^stopAt\s+\w+;", "stopAt          writeNow;", cd.read_text(),
                                                 flags=re.M), newline="\n")
                            state["stopped"] = True
                            state["auto_stop"] = mon["reason"]
                            JOBS.update(job, None, "Results have stopped changing - finishing",
                                        log=f"Auto-stop: {mon['reason']}")

        t_start = time.time()
        job.extra["solver_started"] = True  # the case (and its controlDict) is final: Stop can act on it now
        th = threading.Thread(target=tail, daemon=True)
        th.start()
        from . import memwatch

        solver_mem = memwatch.PeakMemory().__enter__()   # (the solver run alone: not the results loading after it)
        try:
            if det["backend"] == "windows":
                cores = f"{n_procs} cores" if n_procs > 1 else "1 core"
                JOBS.update(job, 0.1, f"Running {meta['app']} on {cores}", log=f"Running {meta['app']} on {cores}")
                steps = resume_steps or runner.parallel_steps(meta["steps"], meta["app"], n_procs, bool(meta["regions"]),
                                                              all_times=bool(meta.get("transient")))

                def on_proc(p):
                    job.proc = p
                    job.check_cancel()

                def on_line(line):
                    if job.cancel_requested and " failed (exit code" in line:
                        line = "Solver stopped by Cancel"
                    JOBS.update(job, None, line if line.startswith("$") else None, log=line)

                runner.run_steps_windows(self.case_dir, steps, det, on_proc, on_line)
            else:
                cmd = runner.build_command(self.case_dir, "Allcontinue" if continue_iterations or resume_transient
                                           else "Allrun")
                JOBS.update(job, 0.1, f"Running {meta['app']} via {det['backend']}", log=f"$ {cmd}")
                proc = subprocess.Popen(cmd, cwd=self.case_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True, bufsize=1, shell=isinstance(cmd, str))
                job.proc = proc
                for line in proc.stdout:
                    line = line.rstrip()
                    if line:
                        JOBS.update(job, None, None, log=line, quiet=True)
                proc.wait()
        finally:
            solver_mem.__exit__(None, None, None)
            job.extra["solver_ram"] = dict(solver_mem.result(), backend=det["backend"])
            state["running"] = False
            job.proc = None
            th.join(timeout=5)
            if job.cancel_requested:
                if continue_iterations and prev_solve.get("summary"):
                    # a cancelled continuation keeps the results it started from
                    self.data["solve"] = dict(prev_solve, note="The continuation was cancelled; these are the "
                                                               "results from before it.")
                else:
                    # killed mid-run: the last written time step is still there and the run can be continued
                    self.data["solve"] = {"status": "interrupted", "backend": det["backend"],
                                          "case_dir": str(self.case_dir)}
                self.save()
        job.check_cancel()
        if app_log.exists():
            job.extra["residuals"] = runner.parse_log(app_log.read_text(errors="ignore"))
        if not (self.case_dir / "case.done").exists():
            tail_txt = ""
            logs = sorted(self.case_dir.glob("log.*"), key=lambda p: p.stat().st_mtime)
            for lf in logs[-1:]:
                tail_txt = f"{lf.name}:\n" + "\n".join(lf.read_text(errors="ignore").splitlines()[-30:])
                job.log.append(f"--- {lf.name} (last lines) ---\n{tail_txt}")
            self.data["solve"] = {"status": "failed", "backend": det["backend"], "case_dir": str(self.case_dir),
                                  "error_tail": tail_txt}
            self.save()
            hint = job.extra.get("diverged") or runner.failure_hint(self.case_dir, tail_txt)
            if hint:
                job.log.append("Likely cause: " + hint)
                self.data["solve"]["hint"] = hint
                self.save()
            raise RuntimeError("OpenFOAM run failed" + (f": {hint}" if hint else " - see the log for details"))
        JOBS.update(job, 0.97, "Loading results")
        rs, summary = results_loader.load(self.case_dir, self.data)
        if meta.get("transient"):
            summary["convergence"] = _transient_run(app_log, meta, user_stop=bool(job.extra.get("user_stop")),
                                                    time_limit=bool(job.extra.get("time_limit")))
        else:
            summary["convergence"] = _energy_check(_convergence(app_log, it_target, meta, auto_stop=state.get("auto_stop"),
                                                                user_stop=bool(job.extra.get("user_stop"))), summary)
            _qualify_unconverged(summary)
        summary["solve_seconds"] = round(time.time() - t_start)
        if job.extra.get("init_from"):
            src = Path(str(init_case)).parent
            summary["initial_flow"] = dict(job.extra["init_from"], study=src.name)
            summary.setdefault("assumptions", []).append(
                f"Started from the solution of study {src.name} (time {job.extra['init_from']['time']}) instead of "
                "uniform flow")
        self.results_dir.mkdir(exist_ok=True)
        rs.save(self.results_dir / "result.npz")
        self._results = rs
        self._remember_previous()
        stale = bool(self.data.pop("edited_during_run", None))
        self.data["solve"] = {"status": "stale" if stale else "done", "backend": det["backend"], "finished": _now(),
                              "summary": summary, "case_dir": str(self.case_dir)}
        self.save()
        return summary

    # ------------------------------------------------------------------ results
    def reload_results(self) -> dict:
        """Re-read the OpenFOAM results from the case directory (e.g. after updating post-processing)."""
        from .solvers.openfoam import results_loader

        if self.spec["solver"] == "builtin_fem" or not (self.case_dir / "case.done").exists():
            raise ValueError("No finished OpenFOAM run to reload")
        meta = json.loads((self.case_dir / "simwb_case.json").read_text())
        rs, summary = results_loader.load(self.case_dir, self.data)
        app_log = self.case_dir / f"log.{meta['app']}"
        old = self.data["solve"].get("summary") or {}
        oc = old.get("convergence") or {}
        if meta.get("transient"):
            summary["convergence"] = _transient_run(app_log, meta)
        else:
            summary["convergence"] = _energy_check(
                _convergence(app_log, int(self.data["settings"].get("iterations", 1000)), meta,
                             auto_stop=(oc.get("monitor") or {}).get("reason") if oc.get("outputs_stable") else None),
                summary)
            _qualify_unconverged(summary)
        for k in ("trim_history", "trim_history_note", "solve_seconds"):  # not derivable from the case files
            if k in old:
                summary.setdefault(k, old[k])
        self.results_dir.mkdir(exist_ok=True)
        rs.save(self.results_dir / "result.npz")
        self._results = rs
        self.data["solve"].update(status="done", summary=summary)
        self.save()
        return summary

    def results(self) -> ResultSet:
        if self._results is None:
            p = self.results_dir / "result.npz"
            if not p.exists():
                raise ValueError("No results yet - run the solver first")
            self._results = ResultSet.load(p)
        return self._results


def _transient_run(app_log: Path, meta: dict, user_stop: bool = False, time_limit: bool = False) -> dict:
    """A transient run has no convergence in the steady sense: report how far it got."""
    from .solvers.openfoam import runner

    txt = "".join(q.read_text(errors="ignore") for q in _log_parts(app_log) + ([app_log] if app_log.exists() else []))
    times = [float(t) for t in runner.RE_TIME.findall(txt)]
    t_end = meta["transient"]["end_time"]
    reached = times[-1] if times else 0.0
    done = reached >= t_end * (1 - 1e-6)
    co = [float(x) for x in re.findall(r"Courant Number mean: [0-9.eE+-]+ max: ([0-9.eE+-]+)", txt)]
    out = {"transient": True, "converged": True, "iterations": len(times), "time_reached": reached, "end_time": t_end,
           "completed": done, "max_courant": max(co[len(co) // 10:]) if co else None}
    if times:
        dts = [b - a for a, b in zip(times, times[1:])] or [times[0]]
        out["time_step_s"] = {"min": min(dts), "max": max(dts)}
    out["note"] = (f"Transient run: {len(times)} time steps to t = {reached:.4g} s"
                   + ("" if done else f" (stopped before the end time {t_end:.4g} s)" + (" by the time limit" if time_limit else " by Stop" if user_stop else ""))
                   + ".")
    return out


def _log_parts(app_log: Path) -> list:
    """Earlier pieces of a continued run's solver log, oldest first."""
    return sorted(app_log.parent.glob(app_log.name + ".part*"), key=lambda q: int(q.name.rsplit("part", 1)[1]))


def _convergence(app_log: Path, it_target: int, meta: dict | None = None, auto_stop: str | None = None,
                 user_stop: bool = False) -> dict:
    """Converged = residual targets reached, or the key engineering outputs stopped changing.
    auto_stop: the monitor's reason when the run was stopped because the outputs had settled (re-evaluating the
    monitor on the final log can land just on the other side of the tolerance)."""
    from .solvers.openfoam import runner

    txt = "".join(q.read_text(errors="ignore") for q in _log_parts(app_log) + ([app_log] if app_log.exists() else []))
    h = runner.parse_log(txt)
    last = {k: v[-1] for k, v in h["residuals"].items() if v and v[-1] is not None}
    res_conv = bool(re.search(r"converged in \d+ iterations", txt))
    mon = runner.monitor_outputs(txt, meta or {}) if meta else {}
    if auto_stop and not mon.get("stable"):
        mon = dict(mon, stable=True, reason=auto_stop)
    converged = res_conv or bool(mon.get("stable"))
    worst = max(last.values()) if last else None
    plateau = worst is not None and worst > 1e-3 and not res_conv
    note = ("Residual targets reached." if res_conv else
            f"Key outputs stable: {mon.get('reason')}." if mon.get("stable") else
            "Stopped by you before the results settled - continue the run for final numbers." if user_stop else
            ("Stopped at the iteration limit while the results were still changing - continue the run."
             if h["iters"] and h["iters"][-1] >= it_target - 1 else
             "Stopped before the iteration limit while the results were still changing - continue the run."))
    if plateau and converged:
        note += " Residuals plateau above 1e-3, which usually means some local unsteadiness (e.g. vortex shedding" \
                " behind fins); the averaged quantities are still meaningful."
    return {"converged": converged, "residuals_converged": res_conv, "outputs_stable": bool(mon.get("stable")),
            "iterations": int(h["iters"][-1]) if h["iters"] else 0, "max_iterations": it_target,
            "final_residuals": last, "worst_residual": worst, "monitor": mon, "note": note,
            "history": _residual_history(h)}


def _residual_history(h: dict, points: int = 160, keep: int = 8) -> dict | None:
    """The residuals of a run for the plot of the results: at most `points` iterations (the last one always) and the
    `keep` equations that ended highest (the ones that decide when the run is converged)."""
    iters, res = h.get("iters") or [], h.get("residuals") or {}
    series = {k: v for k, v in res.items() if any(x is not None for x in v)}
    if len(iters) < 2 or not series:
        return None
    idx = sorted(set(np.linspace(0, len(iters) - 1, min(points, len(iters))).round().astype(int).tolist()))
    last = lambda v: next((x for x in reversed(v) if x is not None), 0.0)  # noqa: E731
    top = sorted(series, key=lambda k: -last(series[k]))[:keep]
    return {"iters": [iters[i] for i in idx],
            "series": {k: [series[k][i] if i < len(series[k]) else None for i in idx] for k in top}}


GCI_RULES = 2  # bump when _gci or the three-level verdict changes: stored checks are recomputed on load


def _three_level_verdict(rows: list, a: dict) -> str:
    g = [r for r in rows if r.get("gci_percent") is not None and r.get("convergence_type") == "monotonic"]
    osc = [r for r in rows if r.get("convergence_type") == "oscillatory"]
    conv = [r["name"] for r in rows if r.get("convergence_type") == "converged"]
    parts = []
    if g:
        parts.append(", ".join(f"{r['name']} GCI {r['gci_percent']:.1f} % (order {r['order']:.1f}"
                               + (f", above the formal {r['order_limited_to']:g}: estimated with {r['order_limited_to']:g}"
                                  if r.get("order_limited_to") else "") + ")" for r in g))
    if osc:
        parts.append("oscillating with refinement (uncertainty = half the range): " + ", ".join(
            f"{r['name']} ±{r['gci_percent']:.2g} %" if r.get("gci_percent") is not None else r["name"] for r in osc))
    if conv:
        parts.append("unchanged on the two finer meshes: " + ", ".join(conv))
    unc = [r["gci_percent"] for r in g + osc if r.get("gci_percent") is not None]
    worst = max(unc, default=0.0)
    verdict = "Three-level check: " + "; ".join(parts) + ". " + (
        f"Mesh-independent within {worst:.2g} %: the finest-mesh values are within this of the mesh-independent answer."
        if worst < 2 else
        "Close to mesh-independent: quote the uncertainty above with the finest-mesh values." if worst < 5 else
        "Use the finest mesh and quote the uncertainty above as the discretisation error.")
    ts = a.get("trim_sensitivity") or []
    if len(ts) == 3:  # drone: the empirical rotor-drag model dominates pitch and total drag
        verdict += (f" Mesh error is not the largest uncertainty: the rotor-drag coefficient k ±40 % moves the "
                    f"pitch {ts[0]['pitch_deg']:.1f}-{ts[2]['pitch_deg']:.1f}° and the total drag "
                    f"{ts[0]['total_drag_N']:.2f}-{ts[2]['total_drag_N']:.2f} N.")
    return verdict


def _gci(f1: float, f2: float, f3: float, n1: int, n2: int, n3: int, formal_order: float = 2.0) -> dict:
    """Grid-convergence index from three meshes, coarse (1) -> fine (3), with cell counts n (Celik et al. 2008,
    J. Fluids Eng. 130). Values are relative to a reference (temperatures as rise). Returns order, GCI of the finest
    mesh in % and the extrapolated value, or the convergence type when no order can be found."""
    r32, r21 = (n3 / n2) ** (1 / 3), (n2 / n1) ** (1 / 3)
    e32, e21 = f3 - f2, f2 - f1
    if abs(e32) < 1e-12 * max(abs(f3), 1e-12):
        return {"convergence_type": "converged", "order": 0.0, "gci_percent": 0.0, "extrapolated": f3}
    if abs(e21) < 1e-15 or e32 / e21 < 0:
        # oscillating with refinement: half the range of the three values as the uncertainty (Celik et al. 2008)
        half = 0.5 * (max(f1, f2, f3) - min(f1, f2, f3))
        return {"convergence_type": "oscillatory", "order": None,
                "gci_percent": half / abs(f3) * 100 if f3 else None, "gci_basis": "half range (oscillatory)"}
    s = 1.0 if e32 / e21 > 0 else -1.0
    p = 2.0
    for _ in range(50):  # fixed-point iteration for the apparent order with unequal refinement ratios
        q = math.log((r32 ** p - s) / (r21 ** p - s))
        p_new = abs(math.log(abs(e21 / e32)) + q) / math.log(r32)
        if abs(p_new - p) < 1e-6:
            break
        p = max(min(p_new, 8.0), 0.3)
    # an apparent order above the scheme's formal order is not credible (the two finer values agree by chance or are
    # limited by something else): estimate with the formal order instead, which is the conservative choice
    limited = p > formal_order
    pe = min(p, formal_order)
    f_ext = (r32 ** pe * f3 - f2) / (r32 ** pe - 1)
    # when limited, the two finer values may agree by chance: also scale the coarser difference to the finest step
    err = max(abs(e32), abs(e21) / r21 ** pe) if limited else abs(e32)
    gci = 1.25 * err / abs(f3) / (r32 ** pe - 1) * 100 if f3 else None
    return {"convergence_type": "monotonic", "order": p, "gci_percent": gci, "extrapolated": f_ext,
            **({"order_limited_to": formal_order} if limited else {})}


def _qualify_unconverged(summary: dict):
    """Results of a run that has not converged: no hand-calculation verdict, and the trim advice says so."""
    if (summary.get("convergence") or {}).get("converged", True):
        return
    if summary.get("correlation_check"):
        summary["correlation_check"] = {"skipped": "the run has not converged - continue it before comparing"}
    if summary.get("trim_note"):
        note = summary["trim_note"].replace("Forces are balanced: the drone is close to trimmed level flight at "
                                            "this pitch.", "").strip()
        summary["trim_note"] = "Run not converged - forces may still change. " + note


def _energy_check(conv: dict, summary: dict) -> dict:
    """A CHT run whose energy balance does not close is not converged, whatever the monitors say."""
    imb = summary.get("energy_imbalance_percent")
    if imb is not None and imb > 2 and conv.get("converged"):
        conv = dict(conv, converged=False, energy_ok=False,
                    note=f"Energy balance off by {imb:.1f} % (> 2 %): the temperatures are not settled yet - "
                         "continue the run.")
    elif imb is not None:
        conv = dict(conv, energy_ok=imb <= 2)
    return conv


def _owner_of_tris(m):
    """Index of a tet owning each CAD boundary triangle (for cell-located values)."""
    if "tri_owner" in m:  # (a snappyHexMesh mesh stores it: the tets there are display tets, far too many to search)
        return m["tri_owner"]
    loc = np.array([[1, 2, 3], [0, 3, 2], [0, 1, 3], [0, 2, 1]])
    F = np.sort(m["tets"][:, loc].reshape(-1, 3).astype(np.int64), axis=1)
    cells = np.repeat(np.arange(len(m["tets"])), 4)
    T = np.sort(m["tris"].astype(np.int64), axis=1)
    allk = np.vstack([F, T])
    _, inv = np.unique(allk, axis=0, return_inverse=True)
    inv = inv.ravel()
    first = np.full(inv.max() + 1, -1)
    first[inv[: len(F)][::-1]] = cells[::-1]
    own = first[inv[len(F):]]
    own[own < 0] = 0
    return own
