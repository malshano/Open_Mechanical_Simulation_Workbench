> **Note (0.4.0):** the browser front end (`simwb/web/`) was removed: the application is the desktop window (`simwb/qt/`, see `docs/QT_APP.md`). The server (`simwb/server.py`) is now only the API. Sections of this manual that describe the browser GUI (the front-end sections, JavaScript gotchas) are kept as history; the last version that had it is the tag `v0.4.0-beta.1`.

---
title: Mechanical Simulation Workbench - Technical Manual
audience: developers and LLM coding agents that change the software (users: read USER_MANUAL.md)
product_version: 0.1.0
document_version: 1
language_stack: Python 3.10+ (FastAPI), JavaScript ES modules (three.js r170, no build step), gmsh, OpenFOAM v2606
how_to_use_this_file_with_an_llm: >
  Read sections 0-3 first (orientation, map, data flow). Then go to the recipe in section 12 that matches your
  change. Every path is relative to the repository root. Function names are given so you can grep for them.
  Section 14 lists mistakes that already happened once; read it before editing.
---

# Mechanical Simulation Workbench - Technical Manual

## 0. Orientation

| Item | Value |
|---|---|
| Purpose | General Ansys-Workbench-like tool: import STEP/STL/OBJ -> tag faces -> mesh -> solve -> results, in a desktop window (Qt + VTK). Not tuned to any one example. |
| Architecture | One Python process (FastAPI + uvicorn) serving a static single-page GUI and a REST + websocket API. Heavy work runs in background threads and subprocesses (gmsh, OpenFOAM, mpiexec). |
| Persistence | One folder per study with a `project.json` (plain JSON) plus mesh, case and result files. No database. |
| Front end | `simwb/web/` - `index.html`, `app.js` (UI state and panels), `viewer.js` (three.js scene), `style.css`. No bundler, no npm; `three.module.js` is vendored in `simwb/web/vendor/`. |
| Solvers | Built-in FEM in `simwb/solvers/*.py` (heat, static structural, modal). OpenFOAM driven through `simwb/solvers/openfoam/`. |
| LLM interfaces | In-app chat (`simwb/agent.py`, Claude Agent SDK), stdio MCP server (`simwb/mcp_server.py`), both over the same tool list in `simwb/tools.py`. |
| Branches | Work on `initial`. Never push or merge to `main` (the owner merges). |
| API URL | <http://127.0.0.1:8765> (`SIMWB_PORT`; the next free port if busy): REST, `/api/tools/*`, `/api/ui/*`, served by the desktop window. |

Terminology (same words are used in code and in the user manual):

| Term | Code name |
|---|---|
| Study (user word) / project (code word) | `Project` class, `project.json`, id `pid` (folder name) |
| Study type | key of `studies.STUDIES`: `heat_conduction`, `static_structural`, `vibration_modal`, `cht_enclosure`, `external_aero` |
| Face | entry of `Project.faces()`; integer face id |
| Moving zone | `project.data["moving_zones"]` entries |
| Result set | `simwb.results.ResultSet` |
| Job | `simwb.jobs.Job` |

---

## 1. Run, test, debug

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m simwb.qt                   # the desktop application; it also serves the API on 127.0.0.1:8765
.venv\Scripts\python -m simwb.server               # the API alone, no window (scripts, tests)
.venv\Scripts\python -m pytest tests -q            # about 10 minutes, 104 tests (one file at a time on a busy machine)
.venv\Scripts\python -m simwb.solvers.openfoam.installer   # one-time built-in OpenFOAM (Windows)
```

Environment variables (read in the named files):

| Variable | File | Meaning |
|---|---|---|
| `SIMWB_HOME` | `simwb/project.py` (`WORKSPACE`) | Default study folder. Tests set it to a temporary folder and `importlib.reload` the modules. |
| `SIMWB_PORT`, `SIMWB_GUI_URL` | `simwb/server.py`, `simwb/mcp_server.py` | Port; the window's API URL the MCP server forwards to. |
| `SIMWB_OPENFOAM_DIR`, `SIMWB_OPENFOAM_CMD`, `SIMWB_AUTO_OPENFOAM`, `SIMWB_OPENFOAM_URL` + `SIMWB_OPENFOAM_SHA256` (another installer: both required), `SIMWB_DOCKER_IMAGE`, `SIMWB_USE_DOCKER` (Windows only: use Docker instead of the built-in install), `SIMWB_MPI_DIR` | `simwb/solvers/openfoam/runner.py`, `installer.py` | OpenFOAM location and auto-install. |
| `SIMWB_MEM_LIMIT_GB` | `simwb/memlimit.py` | Memory cap for helper processes. |
| `SIMWB_GMSH_DEBUG` | `simwb/gmsh_worker.py` | Extra gmsh output. |
| `SIMWB_ALLOWED_HOSTS` | `simwb/server.py` | Extra host names the server answers to (comma separated; default 127.0.0.1, localhost, [::1]). |
| `SIMWB_CLAUDE_MODEL`, `ANTHROPIC_API_KEY` | `simwb/agent.py` | Chat model; API-key auth. |

Rules that apply to every API call:

- Mutating calls (POST/DELETE) need the header `X-SimWB: 1`. A browser `Origin` that is not the server itself is refused (CSRF guard in `server.py`).
- ValueError raised in a handler becomes HTTP 400 with `{"error": message}` (`@app.exception_handler(ValueError)`); KeyError -> 404.
- Project ids match `^[A-Za-z0-9][A-Za-z0-9_-]{0,80}$` (`project._PID_RE`).

Dev server with a scratch workspace (do this when testing; do not use the user's workspace):

```
set SIMWB_HOME=%TEMP%\simwb_runs
.venv\Scripts\python -m simwb.server
```

---

## 2. Repository map

| Path | Role |
|---|---|
| `simwb/server.py` | FastAPI app: all REST routes, websocket `/ws`, static files at `/static`, index at `/`. Route registration must stay above `def main()` (section 14). |
| `simwb/project.py` | `Project` class: every operation (import, faces, BCs, mesh, solve, results). Shared by GUI, chat and MCP. Also project listing, creation in any folder, `open_project_folder`, `delete_project`. About 2,600 lines. |
| `simwb/studies.py` | Study catalogue `STUDIES`: label, solver, face BC types, body params, settings (`P(...)` parameter objects with units, bounds, options, help), `FOAM_ADVANCED`, `OPTION_HELP`, validation helpers (`finite`). |
| `simwb/gmsh_worker.py` | Runs in a subprocess (`run_gmsh_task`): STEP/STL import, domain build, tessellation, meshing (size fields, fin gaps, rotor disks, wake cylinders and rings), `PRESETS`, `cylinder_size_expr`. |
| `simwb/cad_edit.py` | CAD edits (section 8.6): `FIELDS` (the operations and their parameters), `validate`, `describe`, `turn` (edits move with a turned model), `apply` (runs in the gmsh worker), `edge_records`. |
| `simwb/meshgeom.py`, `simwb/domain.py` | Mesh-from-STL path; automatic air volume shapes (box, C-shape, cylinder). |
| `simwb/jobs.py` | `Job`, `JobManager`, `run_gmsh_task`: background jobs with progress, log, cancel; `JOBS.emit` broadcasts events to websocket clients. |
| `simwb/results.py` | `ResultSet`: nodes, tets, fields, frames; `surface`, `slice`, `iso`, `clip`, `field_info`, `at(time_index)`. |
| `simwb/symmetry.py` | Symmetry planes, pure numpy: `make_plane`, `validate` (at most 3, mutually perpendicular), `reflection`, `images`, `group`, `expand_force_moment` / `expand_force`, `mirror_rotors`, `area_factor`, `crossing_issues` (flow / gravity must lie in the plane), `plane_from_face`, `axis_plane`, `turn`, `on_plane`, `side_of`. |
| `simwb/resample.py` | Regular-grid resampling of a `ResultSet`: `Locator`, `sample_box`, `volume_payload`, `vector_payload`, `particle_paths`. |
| `simwb/export.py` | Results for use elsewhere: `write_vtu` (VTK unstructured grid, inline base64 arrays), `write_csv` (one row per node), `report_html` (self-contained page from `project.json` and the result summary, generic over study types). |
| `simwb/solvers/thermal_fem.py`, `static_fem.py`, `modal_fem.py` | Built-in FEM solvers. |
| `simwb/solvers/openfoam/cases.py` | Case writers: `write_external_aero`, `write_cht`, schemes, turbulence, function objects, step lists. |
| `simwb/solvers/openfoam/foamio.py` | polyMesh writer, mesh quality, ASCII field readers. |
| `simwb/solvers/openfoam/initfields.py` | `map_initial(case, source_case)`: initial internal fields from another study's latest solution (cells paired by centre with a KD-tree, exact on the same mesh). `cell_centres(pm)`. |
| `simwb/solvers/openfoam/advanced.py` | Advanced numerics and user-edited case files, applied before every run. |
| `simwb/solvers/openfoam/runner.py` | OpenFOAM detection, `run_steps_windows`, `parallel_steps`, `run_split_times`, log parsing. |
| `simwb/solvers/openfoam/results_loader.py` | OpenFOAM output -> `ResultSet` + engineering summaries (forces, y+, Q-criterion). |
| `simwb/solvers/openfoam/installer.py` | Downloads and unpacks OpenFOAM and MS-MPI into the workbench folder. |
| `simwb/tools.py` | `TOOLS` list (name, description, JSON schema, handler) and `SYSTEM_PROMPT` for the chat and MCP. |
| `simwb/agent.py` | `ChatSession` (Agent SDK, tools only, `cwd` = study folder), `auth_status`, `start_login`, `Terminal` (claude CLI runner). |
| `simwb/mcp_server.py` | stdio MCP server; forwards to the GUI server so there is one workspace. |
| `simwb/memlimit.py` | Memory limit helper. |
| `simwb/web/app.js` | UI: state object `S`, panels, step logic, results panels, chat and terminal. About 2,900 lines. |
| `simwb/web/viewer.js` | `Viewer` class (three.js): faces, overlays, iso, contours, volume, arrows, particles, BC glyphs, region drawing. |
| `tests/` | `test_api.py`, `test_pipeline.py`, `test_thermal.py`, `test_transient_domain.py`, `test_installer.py`, `conftest.py` (STEP fixtures). |
| `examples/` | Example STEP files and `make_examples.py`. |
| `docs/` | These manuals. |
| `LICENSE`, `SECURITY.md`, `CONTRIBUTING.md`, `.github/` | GPL-3.0-or-later; security policy; how to contribute; CI (pytest on Windows) and issue templates. |
| `run_workbench.bat` | Windows one-click start. |

---

## 3. Data flow

```
STEP/STL/OBJ --import--> geometry/ (tessellated faces, face ids, bodies)
   |  Project.import_step -> job "prepare" -> gmsh_worker.task_import
   v
Setup: BCs, body params, settings, rotors, moving_zones  (project.json)
   v
Mesh: Project.generate_mesh -> job "mesh" -> gmsh_worker.task_mesh -> mesh/ (+ foamio for OpenFOAM)
   v
Solve: Project.solve -> job "solve"
         built-in: _solve_builtin/_solve_modal/_solve_static (numpy/scipy)
         OpenFOAM: cases.write_* -> case/ -> runner.run_steps_windows (decomposePar, mpiexec solver, reconstructPar)
   v
Results: results_loader -> results/result.npz  <- ResultSet.load
   v
API /results/* -> payload dicts (base64 float32/uint32/uint8 arrays) -> viewer.js
```

Job lifecycle: a handler calls `P.JOBS.start(kind, project_id, fn, ...)`; the job runs in a thread; progress and log lines are pushed over `/ws` as `{"type": "job", ...}`; the GUI polls `/api/jobs/{id}` too. Kinds: `prepare`, `mesh`, `solve`, `check`. `Project.busy()` is true while one runs; mutating operations raise "Please wait: a job is still running".

---

## 4. Project data model (`project.json`)

Top-level keys (type, meaning):

| Key | Type | Meaning |
|---|---|---|
| `name`, `study`, `created`, `updated` | str | Display name, study type key, timestamps. |
| `geometry` | dict | `status` (none/ready/...), `info` (bboxes `bbox`, `parts_bbox`, `ref_length`, `domain` info, `n_faces`), `source`, `original_name`, `scale`, `format`, `rotation`. |
| `domain` | dict | Air volume spec (`farfield` for aero; `enclosure_mode` for CHT). |
| `bodies` | dict | Per-body parameters (material, role, heat load, ...). |
| `bcs` | list | Boundary conditions: `{id, type, faces:[face ids], params}`. |
| `rotors` | list | Actuator disks. |
| `moving_zones` | list | `{name, method: "mrf"|"sliding", rpm, axis, center[3], radius, length, cell size ...}`; axis stored as a name (`"-x"`) and expanded to a vector by `state()`. |
| `settings` | dict | Study settings (keys in `studies.STUDIES[...]["settings"]`), e.g. `turbulence`, `speed`, `analysis`, `end_time`, `n_frames`, `n_procs`. |
| `mesh` | dict | `preset`, `size_max`, `size_min`, `refinements` (face sizing), `regions` (boxes and cylinders), `layers` (`{n, growth, first, y_plus, tolerant}`), `status`, `stats` (`stats.layers`: see Wall layers made with the tetrahedral mesh). A cylinder region: `{name, shape:"cylinder", start[3], end[3], radius, inner_radius?, size, min, max}`. |
| `solve` | dict | `status`, `backend`, `finished`, `summary` (engineering numbers, `default_groups`, `default_hidden_regions`, `coefficients`, `transient`, ...), `case_dir`. |
| `foam_advanced` | dict | Advanced OpenFOAM numerics, e.g. `n_outer`. |
| `mesh_check`, `model_check`, `hidden` | dict/bool | Mesh sensitivity and model comparison state; `hidden` marks internal twin projects. |

`Project.state()` returns this plus computed keys: `id`, `path` (the study folder), `study_spec`, `body_list`, `face_count`, `flow_dir` (aero), `checks` (readiness), `jobs`.

Writes: `Project.save()` writes `project.json`; `reload_if_changed()` picks up edits by another process (the MCP server). Any edit that invalidates the mesh or results calls `mark_stale()`.

### 4.1 Where studies live

- Default: `WORKSPACE/<pid>/` where `WORKSPACE = Path(os.environ.get("SIMWB_HOME", ROOT / "projects"))`.
- Chosen folder: `create_project(name, study, location)` creates `<location>/<pid>/` and records `pid -> folder` in `WORKSPACE/locations.json` (`_locations`, `_save_locations`). `_project_dir(pid)`, `list_projects()` and `delete_project()` consult it.
- `open_project_folder(path)` registers an existing folder that has a `project.json`.
- `_unique_id(base, folder)` makes ids unique against the workspace, the registry and the chosen folder.
- Mesh-check twins and model-check copies live under `WORKSPACE/.checks/` regardless of where the study is.
- API: `POST /api/projects {name, study, example?, location?}`, `POST /api/projects/open_folder {path}`, `POST /api/pick_folder {start}` (a tkinter folder dialog in a subprocess on the server machine; returns `{path}` or `""`).

---

## 5. HTTP API reference

All paths start with `/api`. `{pid}` = project id. Bodies are JSON. Payload arrays are base64 of little-endian float32 (`positions`, `values`, `vectors`, `paths`, `speed`), uint32 (`indices`) or uint8 (`data`).

### 5.1 Meta and tools

| Method path | Purpose |
|---|---|
| `GET /meta` | Version, `studies`, `foam_advanced`, materials, examples, `workspace`. |
| `GET /openfoam`, `POST /openfoam/install` | OpenFOAM detection / install job. |
| `GET /claude/status`, `POST /claude/login` | Claude auth state; opens a login terminal window (legacy; the GUI uses the terminal tab now). |
| `POST /tools/{name}` | Run one chat tool (the MCP server uses this). |
| `GET /jobs`, `GET /jobs/{jid}`, `POST /jobs/{jid}/cancel` | Job list, status, cancel. |
| `POST /shutdown` | Stop jobs and the server. |

### 5.2 Projects and setup

| Method path | Purpose |
|---|---|
| `GET /projects`, `POST /projects`, `POST /projects/open_folder`, `POST /pick_folder` | List (items have `id,name,study,updated,path`), create, register folder, folder dialog. |
| `POST /projects/{pid}/duplicate` `{name?}` | `duplicate_project`: copy next to the source with geometry, setup and mesh (no `case`, `results`, `videos`, job files); solve status reset. Returns the new state. |
| `GET /projects/{pid}`, `DELETE /projects/{pid}` | State; delete (folder removed). |
| `POST /projects/{pid}/import`, `/rotate`, `/import_example`, `/study`, `/domain`, `/settings`, `/rename` | Geometry and study changes. |
| `GET /projects/{pid}/faces` | Face list. |
| `POST /projects/{pid}/bodies/{bid}` | Body parameters. |
| `POST /projects/{pid}/bcs`, `DELETE /projects/{pid}/bcs/{bcid}`, `POST /projects/{pid}/auto_inlet_outlet` | Boundary conditions. |
| `POST /projects/{pid}/rotors`, `/rotors/detect`, `/rotors/auto_thrust`, `/apply_trim` | Actuator disks and trim. |
| `POST /projects/{pid}/moving_zones`, `/foam_advanced` | Moving zones; advanced numerics. |
| `GET/POST /projects/{pid}/case_file(s)`, `POST /case_files/generate` | Edit OpenFOAM files. |
| `GET /projects/{pid}/case.zip` | Download the OpenFOAM case. |

### 5.3 Mesh, solve, checks

| Method path | Purpose |
|---|---|
| `POST /projects/{pid}/mesh/settings` | Body keys: `preset`, `size_max_mm`, `size_min_mm`, `refinements`, `regions`, `layers` (`{n, growth, first_mm, y_plus, tolerant}`), `defeature_mm`. Validated in `Project.set_mesh`. |
| `POST /projects/{pid}/mesh` | Start the mesh job. |
| `GET /projects/{pid}/mesh/view?poor=0.1` | Poor-cell overlay payload. |
| `POST /projects/{pid}/solve`, `/solve/stop` | Start / stop the solve job. Body: `export_only`, `continue_iterations`, `init_from` (study id: initial flow from its latest solution; external aero, same parts bounding box; checked in `Project.init_source`). |
| `POST /projects/{pid}/mesh_check`, `/model_check` | Sensitivity checks. |
| `POST /projects/{pid}/highlight` | Flash faces in the GUI (websocket event). |

### 5.4 Results

| Method path | Query / body | Returns |
|---|---|---|
| `GET /results/fields` | - | `{fields:[{name,loc,unit,min,max,p01,p99,change_over_time?}], regions, groups, times, summary}`; `p01`/`p99` are the 1st and 99th percentile of the last time (the range that holds the flow; `min`/`max` are over all times and sit on walls and blade tips). The GUI uses them for the colours of contour planes and vortex surfaces. |
| `POST /results/reload` | - | Re-read results from disk. |
| `POST /results/load_case` | - | Join the saved times still in `case/processor*` (damaged ones skipped and named) and load them as the results; returns a job. |
| `GET /results/surface` | `field, hide, groups, time` | Coloured wall triangles. |
| `GET /results/clip` | `field, axis(x/y/z), pos(m), hide, time` | Planar cut payload (also used for contour planes). |
| `GET /results/iso` | `field, level, color, hide, time, wall_gap(m), smooth(0-3)` | Isosurface (Q-criterion) payload. |
| `GET /results/volume` | `field, box(x0,y0,z0,x1,y1,z1 m), n(16-192), time, hide` | `{dims[nx,ny,nz], origin, h, box, log, lo, hi, unit, data(uint8: 0 = no data, 1..255 = value)}` |
| `GET /results/plane` | `field, axis, pos(m), box, n(16-256), time, hide` | A field on the plane over the box: the exact cut of the mesh (the same marching-tetrahedra cut as `/results/clip`, so the picture has the resolution of the mesh), the triangles that touch the box kept; section payload format (`positions, indices, values, per:"vertex", n_tris, min, max, unit`). `n` is ignored (the earlier grid version used it: 4 cm cells next to a 5 mm wing mesh). Used by the contour planes. |
| `GET /results/vectors` | `axis, pos, box, n(4-80), time, hide` | `{n, h, positions, vectors, min, max, p95, unit}` |
| `GET /results/particles` | `box, n(50-6000), steps(20-400), seed(upstream/everywhere/box), sbox(x0,y0,z0,x1,y1,z1 for seed=box), flow("dx,dy,dz"), grid(24-160), time, hide` | `{n, steps, dt, h, ref_speed, paths(n*steps*3, NaN after end), speed, min, max, unit}` |
| `POST /results/video` | JPEG frames from the browser | MP4 (H.264 via `imageio-ffmpeg`). |

`time` is a saved-time index; -1 or absent = last. `ResultSet.at(i)` returns the set at that time.

### 5.5 Claude terminal

| Method path | Body | Behaviour |
|---|---|---|
| `POST /projects/{pid}/terminal` | `{line}` | If a command is running, the line is written to its stdin. Otherwise it is split into arguments, `login`/`logout`/`status` are mapped to `auth login|logout|status`, a leading `claude` is dropped, and the bundled claude CLI runs in the study folder (`Terminal.run`). Output arrives as websocket events `term_out {project,text}` and `term_done {project,code,auth}`. |
| `POST /projects/{pid}/terminal/stop` | - | Terminates the running command. |

Request checks (`_csrf_guard`, `ws`): every request needs a local `Host` header (127.0.0.1, localhost, [::1] or `SIMWB_ALLOWED_HOSTS`), which blocks DNS rebinding (another site pointing its own name at 127.0.0.1 could otherwise read projects and results); mutating calls also need `X-SimWB: 1` and a local or missing `Origin`; the WebSocket checks Origin and Host. Tests: `test_foreign_host_rejected`, `test_csrf_guard`.

Case files that could run programs (`solvers/openfoam/advanced.check_safe`): an edited dictionary is stored in `project.json` and written into the case before every run, and OpenFOAM can run code from a dictionary (`#codeStream`, `#calc`, `#{ ... #}` code blocks, `coded*` boundary conditions / sources / function objects, `systemCall`, `libs ("path/x.dll")`). These are refused when a file is saved and again for every file of the written case before a run (`advanced.apply`), so a study folder from someone else cannot carry them, through an edited file or a name copied into a dictionary. Every `*Libs` key (`libs`, `motionSolverLibs`, `functionObjectLibs`) must be a literal list of bare library names; every include form (`#include`, `#sinclude`, `#includeEtc`, ...) must name a file inside the case (no absolute, UNC, `$` or `..` paths). The rules run on the raw text and again without comments, so a comment, or a string that looks like one, cannot hide an entry. Test: `test_case_file_edit_cannot_run_programs`.

OpenFOAM installer (`solvers/openfoam/installer.py`): pinned release (`OPENFOAM_VERSION`, `OPENFOAM_URL`) and `OPENFOAM_SHA256`, checked before the installer runs (the file is not code-signed). To move to a newer release, download it, check it, and update the three constants.

Security notes: only the claude CLI binary runs (`agent._cli()` = the SDK-bundled `claude.exe`, else `claude` on PATH). The arguments are checked by `server.terminal_args` against `TERMINAL_ALLOWED` (`auth login|logout|status`, `--help`/`-h`, `--version`/`-v`, `mcp list`) or the form `-p "one question"`, which is run with `--permission-mode default --disallowedTools Bash,Edit,Write,NotebookEdit,WebFetch`; every other flag is refused (an open argv would let `--dangerously-skip-permissions`, `--allowedTools`, `--mcp-config`, `--settings` or `--add-dir` turn the terminal into command execution). The route is also behind the `X-SimWB` header and the origin check. Do not widen the list without a new threat review. Test: `test_claude_terminal_allowlist`.

### 5.6 Websocket `/ws`

Server -> client events (JSON): `job` (progress), `project_changed`, `highlight`, `chat_status|chat_text|chat_tool|chat_tool_result|chat_done|chat_error`, `term_out|term_done`. Client -> server: `{"type":"chat","project","text","history"}`, `chat_stop`, `chat_reset`.

---

## 6. Meshing (`simwb/gmsh_worker.py`)

- Presets `PRESETS = {"quick": (8, 6), "coarse": (10, 8), "medium": (16, 12), "fine": (26, 16), "very_fine": (40, 24)}`; solid-FEM curvature table `SOLID_CURVATURE`.
- Size controls: global sizes, face refinements, part sizing, boxes, cylinders. Cylinder (and ring) size fields are gmsh MathEval expressions built by `cylinder_size_expr(start, end, radius, v_in, v_out, thickness, inner=0.0)`. For a ring the distance term is `dr = max(0, max(rho - R, inner - rho))`.
- Tip-vortex ring defaults (GUI, `addTipRing` in `app.js`): start at the zone's upstream face, length one radius downstream, radius R, inner 0.65 R, size R/50.
- Rotating zones: zone surface meshed exactly; sliding zones become a cyclicAMI pair in the OpenFOAM case.
- Imports keep parts intact: exact `occ.rotate` for rotations (not an affine transform, which rebuilds faces as BSplines), parts cut one by one out of the air box when fusing loses faces, zero-volume slivers dropped.
- Quality: normalised shape ratio (1 = perfect tet, below 0.1 poor); `Project._foam_quality` reports non-orthogonality.

### 6.1 Hex-dominant mesh (`simwb/solvers/openfoam/hexmesh.py`)

An optional mesher for `external_aero` (`mesh.method == "hex"`, set with `set_mesh(method=...)`, the REST `mesh/settings` body and the `set_mesh_settings` tool). `Project._mesh_work` branches to `_hex_mesh_work`; the tetrahedral mesher is unchanged.

- **Scope**: a body in the automatic box of air; wall conditions only. `hexmesh.unsupported()` returns the reason it cannot be used (rotors, moving zones, symmetry planes, a non-box air volume, a condition on an air-box face); the GUI shows it, `set_mesh(method="hex")` and the writer refuse. The tetrahedral mesh stays the default.
- **Plan** (`make_plan`): same sizing rules as the Gmsh mesher (`PRESETS`, `FEATURE_CELLS`, `L/(4n)` wall size, thin-face sizes, user refinements and regions). The surface cell is `h_wall`; the background cell is `h_wall * 2^levels` (4..7 levels, as few as `MAX_BACKGROUND_CELLS` allows), cubic cells, a wake box downstream (level `levels - 2`) when the speed is at least 1 m/s. Faces with a local size form one surface per refinement level.
- **Case** (`write_case`): one STL per refinement group (`<patch>_L<level>`), `blockMeshDict` (single patch `farfield`), `surfaceFeatureExtractDict`, `snappyHexMeshDict` (castellate, snap, layers with `relativeSizes false` and the same y+-based first layer as the tetrahedral layers), `createPatchDict`, a `controlDict` with `writeFormat ascii; writePrecision 10;` (snappyHexMesh refuses to merge points otherwise). `locationInMesh` is a point in the air off every grid plane.
- **Turned refinement boxes** (checked as written files and in a tiny real Gmsh mesh; **never run through a real snappyHexMesh** - no OpenFOAM where this was built): a region with a `rotation` is written as `triSurfaceMesh` (`region_<i>_<name>.stl`, the 12 triangles of `regions.box_triangles`, normals out) with `mode inside`, because `searchableBox` cannot turn; the plan keeps the bounding box in `min` / `max` and the corners in `corners` / `triangles`. The tetrahedral mesh uses `gmsh_worker.box_size_expr` (a MathEval size field in the box's own axes).
- **Run**: `STEPS` = blockMesh, surfaceFeatureExtract, snappyHexMesh, createPatch (merges the groups into the patches the setup knows), checkMesh (`allow_fail`). In the v2606 Windows build `createPatch -overwrite` writes to `0/polyMesh`; `finalize()` moves it to `constant/polyMesh`. Non-Windows backends run the same steps from an `Allmesh` script (not tested).
- **Stored**: `mesh_dir/polyMesh_hex` is the real mesh. `mesh.npz` holds display arrays in the same format as the tetrahedral mesh (cells split into display tetrahedra by `results_loader.display_mesh`, boundary triangles with a representative CAD face per patch, `tri_owner`, a per-tet quality of `1 - worst non-orthogonality / 90 deg`, `hex = 1`). `load_mesh()` adds `hex_dir`. `stats` has `method: "hex"`, `cell_types`, `foam_quality` (from checkMesh) and a `hex` block (levels, layers coverage, problems, warnings).
- **Case writer**: `write_external_aero` copies `polyMesh_hex` into the case (`_use_polymesh`) instead of converting tetrahedra, does not add layers again, and refuses rotors, zones and symmetry planes.
- **Quality rules** (`parse_logs`): concave cells are a warning (snappyHexMesh limits them); any other failed `checkMesh` item is a problem. The cell limit (`MAX_CELLS`) stopping refinement is reported.
- **Known limits**: sections of the display mesh take seconds (12 display tetrahedra per hexahedron); layer coverage depends on the parts being several cells thick; the log formats are those of OpenFOAM v2606.

### Smart sizing and the mesh advisor (`meshadvisor.py`, `gmsh_worker.task_mesh`)

- **Why**: the old sizing took `ref_length` (the largest side of the object) for every size; for a slender body that is not the length along the flow and not the thickness. See `docs/MESHING_GUIDE.md`
  for the measurements, the criteria and the sources.
- **Setting**: `mesh.smart` (bool; `set_mesh(smart=...)`, `POST /mesh/settings`, tool `set_mesh`). Off by default. When on, `_mesh_work_inner` calls `meshadvisor.analyse` and `meshadvisor.recipe`
  and sends the recipe to the worker as `mesh["recipe"]`; the recipe is kept in `stats.recipe`. Sharp-edge refinement (`edge_refine`, factor 4) is switched on with it.
- **analyse**: the length along the flow (the projection of the bounding box on the flow direction; the downwash for a rotor without speed), the lateral extents, the thinnest face (`face_width`),
  the domain in body lengths, Reynolds number and boundary layer thickness.
- **recipe**: wall cell = length / n_s (preset: 40, 60, 90, 130, 190); near-field box (0.3 lengths round the body, 0.5 behind it, cells 3 x the wall cell); wake box (from the rear of the body, 3 to 5 lengths,
  the width of the body + 0.4 lengths each side, cells 4 to 5 x the wall cell); growth 1.3, 1.25, 1.2, 1.2, 1.15; layers for a first-cell y+ of 40 and `layers_that_fit` (at most 5); `extend_boundary: False`.
  The cells of the two boxes are estimated (0.118 h^3 per tetrahedron, x 0.8 measured) and the wall cell is coarsened until they fit in half of the free memory (`memory_cap_cells`, 4,200 bytes a cell).
- **Worker** (`ms["recipe"]` present): `h_wall = recipe.h_surf`, `h_body = recipe.h_near`, growth from the recipe, the old automatic box (0.5 L round the object, 1.5 x the body size, growing over L) is replaced by the two
  boxes with `Thickness = (size_far - size) / (growth - 1)` (size = inside + (growth - 1) x distance), user regions grow the same way (the old `Thickness = 8 x size` stepped to the far-field size over 8 cells),
  `Mesh.MeshSizeExtendFromBoundary = 0` (spreading the sizes of the boundary curves put the fine cells of the sharp-edge field over the whole surface), and the sharp-edge refinement runs even with a recipe.
- **Edge refinement settings and preview** (`simwb/edgepreview.py`, `gmsh_worker.edge_refine_plan`). `mesh.edge_refine = {factor 2..20, max_cells 1e3..5e6 (default 150000), max_angle 20..175 (default 140)}`, set with
  `set_mesh(edge_refinement=True | factor | {factor, max_cells, max_angle, enabled})` (a bare factor keeps the cap and the angle; `false` removes it and, with smart sizing on, sets `mesh.no_edge_refine` so that the recipe's factor 4 is not applied;
  tool `set_mesh_settings`, REST `/mesh/settings`). `edge_refine_plan(edges, h_wall, L, growth, factor, cap, max_angle)` is the one decision (which edges, the edge cell size, coarsened in steps of 8 % until the estimate fits the cap) used by the mesher and by the preview. The estimate is `gmsh_worker.edge_cells_union(polylines, face_angles, h_edge, h_wall, growth)`: the cells of the size field `h_edge + (growth - 1) d` over the union of the edge tubes (Monte Carlo, seed 7, about 1 % noise, edges weighted by the air share (360 - angle) / 360) less those of the wall size, times `EDGE_CELLS_CALIBRATION` (0.52); documented uncertainty `EDGE_ESTIMATE_SPREAD` = 25 % (measured 0.84 to 1.19 of the real extra cells on 19 meshes, see MESHING_GUIDE). With the edge field the mesher sets `Mesh.MeshSizeMin` to half the edge size and `Mesh.MeshSizeExtendFromBoundary = 0`. `mesh.no_edge_refine` is set whenever the option is switched off (smart sizing never turns it on again); a partial dictionary keeps the stored values it does not name.
  The preview finds the edges in the worker program (`gmsh_worker edges`, no meshing; one subprocess per geometry on a thread, kept on the project, the change event tells the window; each search has a job file of its own and a new geometry or the end of the program stops it), takes the wall size from `gmsh_worker.aero_wall_size` (the mesher's own)
  and `meshadvisor.recipe` with smart sizing, and returns the plan, the polylines for the 3D view and, after meshing, `measure()`: the tetrahedra whose centre lies within `dist_max` of the refined edges, counted in `mesh.npz` (the volume per wall-size cell is the mean of the mesh's own cells in a shell from 1 to 1.5 reaches of the edges).
  `stats.edge_refine` (edges, size, factor, factor_used, estimated_extra_cells, cap_cells, angle_limit, capped, dist_max ...) is what the mesher did (the extra cells are the estimate, not a count); `stats.edge_refine_skipped` is the reason when it was
  asked for and nothing was refined (`size_min`, `no_edges`, `finer_than_floor`, `cap_too_low`). Not used when `size_min` is set without smart sizing (the old rule, now reported). Qt: `qt/panels/mesh_edges.py`, `qt/edgeview.py`; action `mesh.edges`.
- **Tolerant layers**: `mesh.layers.tolerant` selects `cases.LAYER_TOLERANT` instead of `LAYER_STRICT` (the snappyHexMesh layer limits) in the tetrahedral mesh's layer step; the hex-dominant mesh does not use it.
- **Scorecard** (`measure`, `scorecard`): the mesh is measured in zones that do not depend on the sizing (the wall; thin faces; within 0.3 lengths of the body; 0.2 to 2 lengths behind it over the body's width + 0.2 lengths;
  neighbour size ratio) and every check has an `ok | warn | bad | info` band; the score is the share of passing checks, warnings counting half. `Project._score_mesh` stores it in `stats.advice` at the end of the mesh job
  and when the layers are made again (never fails a job).
- **Interfaces**: `Project.mesh_advice()` (`GET /mesh/advice`, tool `mesh_advice`, action `mesh.advice`), `Project.apply_mesh_recipe(preset, layers)` (`POST /mesh/recipe`, tool `mesh_apply_recipe`, action `mesh.recipe`).
- **First-cell y+**: `cases.layer_first_thickness` returns the thickness whose centre has the target y+ (2 x y+ x nu / u_tau).
- **Known limits**: external aerodynamics only; the domain is not changed; resolved boundary layers (y+ about 1) are not recommended (about a million extra prisms on the example wing); isotropic tetrahedra cost about a million cells for a thin 0.8 m wing.

### Wall layers made with the tetrahedral mesh (`solvers/openfoam/layermesh.py`)

- **Why**: the solver case adds the layers with snappyHexMesh in layer-only mode before the run (`cases.write_external_aero`); the Mesh step used to show the
  tetrahedra without them. `Project._make_layers` now runs the first steps of that very case in `mesh/layercase` (check, snappyHexMesh, a final checkMesh
  with `log.checkMesh_layers`), so the layers are the ones the run gets, reads the result back and removes the folder (the snappyHexMesh log is kept as
  `mesh/layers_log.txt`). The case is written by `cases.write_case` and `advanced.apply`, with every setting of the run; nothing is duplicated.
- **Job**: part of the tetrahedral `generate_mesh` job (progress 0.93 to 0.99) for external aerodynamics; `Project.generate_layers` (`mesh.layers` action)
  repeats only this step. `_make_layers` never raises for a reason of the model or OpenFOAM: it returns `{made: False, reason}` (no OpenFOAM, no wall, the
  case could not be written, snappyHexMesh failed, the log could not be read). A mesh job therefore never fails because of the layers.
- **Stored**: `stats["layers"]` = `{made, patches[{name, faces, target, made (average, may be fractional), first_mm, total_mm, percent}], extruded_percent,
  faces_extruded, faces_total, cells, cells_before, layer_cells, layers_max, first_mm, total_mm, types, non_orthogonality_max/mean, skewness_max,
  aspect_ratio_max, mesh_ok, failed_checks, warnings, y_plus, settings, stale, seconds}`; `mesh/layered.npz` holds the display arrays (the format of the hex
  mesh, without its `hex` flag, plus `tet_layer`: the layer number of each display tetrahedron, 0 = not a layer). The tetrahedral `mesh.npz` is unchanged, so
  every other view and metric still works on the Gmsh mesh. A new mesh (either method) deletes `layered.npz`; changing the layer settings sets
  `stats.layers.stale` (the tetrahedra stay valid: `mark_stale` is not called).
- **Display** (`layermesh.display_arrays`): the layered mesh is exactly the Gmsh tetrahedra (moved away from the wall) plus one prism per wall triangle and layer.
  Tetrahedra are drawn as they are (four distinct nodes of four triangles); prisms and any other cell are split into tetrahedra round their centre. Cell quality is
  `hexmesh.cell_quality` (1 - the worst non-orthogonality / 90 degrees). `layer_numbers` counts the layers: layer 1 is the prism whose triangular base is a boundary
  face (not any boundary face: the quadrilateral sides of a prism lie on symmetry planes and the far field), layer k + 1 the prism stacked on a layer-k prism
  through a shared triangle. About 9 s and 1.7 million display tetrahedra for a 950,000-cell mesh.
- **View**: `GET /mesh/view?metric=layers` (`Project.mesh_view(metric="layers")`): the layered mesh coloured by layer with `categories` (Tetrahedra, Layer 1 (at the
  wall) ... Layer n), `present` as for the region colouring. `layers_default_cut` gives a cut through the middle of the object across its longest side;
  `layer_focus(axis, position)` a box round the end of the section of layer 1 furthest back along the longer side of the section, ten times as wide as all the
  layers, for the camera.
- **Why layers are lost** (`layermesh.layers_that_fit`, `layer_warnings`): a layer grows from each side of a thin part, so the stack of layers must total less than
  about half the part's thickness (`stats.sizes.feature`, the thinnest solid feature). `stats.layers.layers_fit` is the most layers of the asked growth that fit;
  `warnings` name it. Measured on the example wing: the tolerant limits (`mesh.layers.tolerant`) changed nothing (69.2 % against 69.0 %), the number of layers did.
- **y+**: `mesh.layers.y_plus` (0.5 to 300, empty = automatic) is the target for the first cell centre of the wall-function models (default 30); `cases.layer_plan`
  turns it into the first layer thickness (`layer_first_thickness`) for the case, the hex mesh and the panel's summary. A `first` given by the user wins.
- **Known limits**: studies with sliding zones get the layers before the baffles are made, as at the run; the step needs OpenFOAM at mesh time (without it the run adds
  the layers as before); the layer count per surface is the average over its faces.

---

## 7. OpenFOAM integration

### 7.1 Case writing (`cases.py`)

`write_external_aero(...)` writes the case. Points that matter for the flow model:

| Setting | Files written |
|---|---|
| `turbulence = kOmegaSST` etc. | `constant/turbulenceProperties`: `simulationType RAS` + RASModel. |
| `turbulence = kOmegaSSTDDES` | `simulationType LES; LES { LESModel kOmegaSSTDDES; delta DeltaOmegaTilde; ... }`; `div(phi,U) Gauss LUST grad(U)` (low dissipation). Raises `ValueError` unless `analysis == "transient"`. |
| any moving zone | `split_zone_walls`: default wall faces fully inside the zone cylinder go to a wall patch `<zone>_walls` (still in `force_patches`, so whole-vehicle forces are unchanged) with a function object `forces_<zone>` (`log false`, CofR = zone centre). `meta["moving_zones"][i]` gets `walls_patch`, `blade_radius`. |
| transient | `writeCompression on` (sliding mesh: about 1 GB per saved time on 2.7 M cells uncompressed); `foamio.read_text` / `foamio.exists` read `name` or `name.gz`. |
| sliding moving zone | solidBody `rotatingMotion`, cyclicAMI interfaces; two or more sliding zones: one createBaffles with all interfaces and a `multiSolidBodyMotionSolver` dynamicMeshDict (one `rotatingMotion` per zone), zone overlap rejected in `set_moving_zones` (`_zones_touch`); PIMPLE with 5 under-relaxed outer correctors by default (the p residual control is never reached on tets with AMI, so all of them run: cost is proportional to `n_outer`; measured 2.2e-5 s x cells x outer / cores per step) and 2 non-orthogonal correctors; time step 0.5 degree of rotation. 3 outer + 1 non-orthogonal was 41 % faster from a developed flow (set in Advanced). |
| transient | `pimpleFoam`; `end_time`, `n_frames` saved times; forces averaged over the second half. |

Study settings defaults and options are defined once in `studies.py` (`P("turbulence", ...)`). Add a model by extending the `options` list and `OPTION_HELP`, then handling it in `cases.py`.

### 7.2 Running (`runner.py`)

- `parallel_steps(steps, app, n_procs, regions, all_times)` wraps the solver step: `decomposePar -force -latestTime`, solver under `mpiexec -n N ... -parallel`, `reconstructPar`, then the action `rm_processors`.
- For transient runs (`all_times=True`) the `reconstructPar` step carries `split_times = n_procs`; `run_steps_windows` calls `run_split_times`, which joins the saved times in rounds of `min(n_procs, number of times, 4)` processes (`reconstructPar -time t`, one time each) and deletes `processor*/<t>` as soon as a round has succeeded, so the split and the joined copy of a run are never on the disk together (a 2.7 M cell sliding run: about 0.6 GB per saved time even compressed). Logs are merged into `log.reconstructPar`; the worst exit code is returned. The newest time is joined first. A time that fails (exit code != 0) does not stop the others: `run_split_times(..., report=)` fills `report["joined"]`/`["skipped"]` (`{time, reason}`, the reason read from that time's log by `io_error`), removes the half-written `case/<t>`, and, when at least one time was joined, `set_unjoined_aside` moves the pieces of the skipped times to `case/unjoined/<t>/processorN` (nothing is deleted; the processor folders then hold no half-usable time). `run_steps_windows` then continues, writes `case/join_skipped.json` and a `WARNING:` line per skipped time, and `Project._join_warnings()` puts them in `summary["warnings"]`. When no time can be joined (it stops after three failures in a row) the step fails and the pieces stay. `keep_pieces=True` (used by `load_case_results`) leaves the pieces and skips times that are already joined (`time_is_joined`: the joined folder holds every file the pieces of any processor have; a half-written join does not count; `_piece_times` takes the times of all processor folders, so a time saved by only some processors is still a time), and joins a single remaining time too. `load_case_results` needs OpenFOAM (the Windows build) when `unjoined_times(case, aside=False)` > 0 and says so; it warns in `summary["warnings"]` when `newest_piece_time(case)` is newer than the time loaded. `unjoined_times` also counts `case/unjoined/<t>` (`set_aside_count`); those folders are never part of the joined times. The cap of 4 is about memory (about 1.6 GB per process on a 2.7 million cell mesh).
- Logs: `case/log.<solver>`; `results_loader` reads `postProcessing/forces/...` (`force.dat`, `moment.dat`).
- `case.failed` holds `{"step", "log"}` (the program that failed). `Project._failed_step()` reads that step's log, and `runner.failure_hint(case, tail, step)` names the cause: an I/O error (`io_error`: `FOAM FATAL IO ERROR`, the file and line) first, then the MPI/memory causes, and a divergence only when the solver itself failed and `real_fpe(text)` finds a real exception (the stack of `sigFpe::sigHandler`, or `Floating point exception (core dumped)`). The start-up banner (`trapFpe: Floating point exception trapping enabled (FOAM_SIGFPE)`, `sigFpe : Enabling floating point exception trapping`) is on every run and is ignored. A failed `reconstructPar` raises "The solver ran, but its saved times could not be joined".
- `Project.load_case_results()` (`POST /api/projects/{id}/results/load_case`, control command `solve.load_case`, the Solve button "Load results from case"): a `solve` job that joins the times still in `processor*` with `keep_pieces=True`, reads the newest joined time and sets the solve state to `done` (`stale` when the setup differs from the case's, with a warning). Nothing is deleted.
- Steady runs stop automatically on a windowed-mean convergence test; continuing a run keeps logs. An interrupted transient run (status `interrupted`/`failed`) resumes with `continue_iterations` (any value): `runner.saved_transient_time` picks the newest time every `processor*` folder holds completely (and with `prune` deletes later, half-written ones), `controlDict` gets `startFrom latestTime` / `stopAt endTime`, the old log becomes `log.<app>.partN`, and a still-split case runs the solver on `runner.processor_count` processes without `decomposePar` (which would delete the pieces), then joins all times as usual.

### 7.3 Results loading (`results_loader.py`)

`propeller_summary(case, zone, rho, V, U_dir, transient)` reads `postProcessing/forces_<zone>/*/force.dat, moment.dat` (`zone_force_history`) and puts `summary.moving_zones[i].propeller = {thrust_N, thrust_direction, torque_Nm, power_W, CT, CP, J, efficiency, tip_mach, diameter_m, average_over, thrust_fluctuation_percent}`; average over the last full revolution (transient) or the last 10 % of iterations (steady). D = 2 x `blade_radius`.

Produces `results/result.npz`: `nodes`, `tets`, `tet_region`, `regions`, per-field arrays (`loc` cell/node), `frames` for transient (list of arrays per time; moving mesh stores `__nodes` per frame), `frame_map` (cell -> display tets), surface triangles with owner cells and groups, boundary values (`surf_values`), `times`. Fields include Pressure, Velocity, y+, Q-criterion (and Temperature for CHT). Summary dict goes to `project.json["solve"]["summary"]`.

---

## 8. Results and visualisation back end

### 8.1 `ResultSet` (`simwb/results.py`)

| Method | Notes |
|---|---|
| `at(i)` | New `ResultSet` for saved time `i` (expands `frame_map`, swaps moving `nodes`). |
| `field_info()` | min/max over all times, `change_over_time`. |
| `surface`, `slice`, `clip`, `iso` | Return payload dicts. `iso` uses marching tetrahedra (`_contour`; every triangle is wound to face the way the field falls, so the surface has one orientation: consistent normals and an enclosed volume), optional smoothing of the field (node->cell->node averaging; it shrinks thin vortices, off by default in the Qt app) and a wall gap that drops triangles near wall points. The Qt app smooths the surface geometry for display with `simwb/surfsmooth.py` (vtkWindowedSincPolyDataFilter: pass band 0.1 -> 0.02 and 6 -> 30 passes over the amount 0-100, the cut border fixed); tested for the change of area and volume on noisy synthetic fields (`tests/test_surfsmooth.py`). |
| `nodal(field)`, `_to_nodes(a)` | Cell values averaged to nodes; NaN cells ignored. |

### 8.6 CAD edits (`simwb/cad_edit.py`)

Edits are a list of `{"op": name, ...}` dicts in `project.json` (`geometry.edits`), replayed on `source.step` by
`gmsh_worker._prepare` after the file is read, scaled and turned and before the symmetry cut and the air volume: the file
is never changed, any edit can be removed, the result is the same every time. Lengths mm, angles degrees, coordinates in
the frame of the 3D view. Bodies are referred to by name; edges by a selector (`all`, `near`, `of_face_near`, `parallel`,
`length_mm`, `type`, `in_box`), never by id (ids change with every edit before them).

- Operations: `box cylinder sphere cone prism revolve loft` (new bodies), `cut union intersect`, `move rotate scale mirror
  pattern_linear pattern_polar`, `fillet chamfer hole`, `delete rename cut_plane`. `validate()` checks one edit without a
  CAD kernel (types, finite numbers, unknown keys), so a wrong request is refused at once with the reason.
- `Project.edit_geometry(action, op, index)` (`add | update | remove | clear`; index counts from 1) builds the new list and
  calls `prepare_geometry(edits=...)`: the list is stored only if the rebuild succeeds, a failed edit leaves geometry and
  list as they were (the job fails with `CAD edit N (op) failed: ...`). A new file starts with no edits; STL/OBJ cannot be
  edited. Faces are matched across the rebuild by `_remap_after_geometry` (boundary conditions follow faces that survive;
  a note says when a condition lost faces).
- A turn of the model (`rotate_geometry`, align a face) passes `cad_edit.turn(edits, D, t)`: points move with the parts,
  directions only turn, a `box` carries the turn in its `rot`, an `in_box` selector becomes the box around the turned box.
- Why the names are kept in a registry and the model is rebuilt through a BREP file at the end of `apply`: gmsh puts the
  STEP part names (labels) back on every `synchronize()`, so a body that was renamed ('Body 1' for an unnamed STEP body, a
  `rename`) would get its old name again. The BREP is exact and carries no labels; volumes are matched back to their names
  by centre of mass and volume.
- `fillet` / `chamfer` are checked afterwards (the kernel does not always refuse an impossible one: a 12 mm fillet on a 20 mm
  bar returned a shape three times the size): the result must stay inside the old box with a volume within 0.3x-1.05x.
- A smooth `loft` makes one spline per run between sharp corners (the trailing edge of an airfoil): one closed spline through
  a corner gave a surface the display mesher could not finish (minutes, then nothing).
- `prepare` also writes `edges.json` (edges of the model's faces: type, middle, length, direction, faces) for `find_edges`.
- Tools (`tools.py`, also on the MCP server): `edit_geometry {action, edit, number}` and `find_edges`. A flow study's body list
  is the air, so `Project.parts()` (from `geometry.info.source_bodies`) gives the names of the solids, listed in
  `summary_text()`. Route: `POST /api/projects/{pid}/geometry/edits` `{action, edit, number}` (returns the prepare job);
  the Geometry panel lists the edits with undo and remove.

### 8.2 Resampling (`simwb/resample.py`)

| Function | Notes |
|---|---|
| `nodal_values(rs, field)` | `(N,)` or `(N,3)` node values. |
| `Locator(rs, hide_regions)` | `cKDTree` on tet centroids (the last one is kept in `Locator._last`, so successive views of one result do not rebuild it); `locate(points)` takes the `K_NEAR = 8` nearest centroids per point and picks the first tet whose barycentric coordinates are all above -1e-6 (Cramer's rule, vectorised, chunks of 100,000 points); points not found are searched again among 40, 160, then 1500 nearest centroids (long thin far-field cells otherwise left holes in sections); what is still not found lies in a body or outside. `interpolate(tet, lam, node_vals)` -> values, NaN outside. |
| `box_grid(box, n)` | Axes and spacing `h = longest side / n`. |
| `sample_box(rs, field, box, n)` | Field on the grid, array shape `(nz, ny, nx[,3])`. |
| `vorticity_grid(U, h)` | `|curl U|` on the regular velocity grid by central differences (`np.gradient`); NaN next to bodies. `volume_payload(rs, "Vorticity", ...)` uses it. |
| `plane_payload(rs, field, axis, pos, box, n)` | Exact planar cut cropped to the box -> triangles (see the API table). |
| `volume_payload(rs, field, box, n, log=None)` | uint8 texture: 0 = no data, 1..255 = value. `log` defaults to true for the Q-criterion and the vorticity: `log10(x / lo) / log10(hi / lo)` with `hi` = 99.9th percentile of positive values, `lo = hi / 1e6`. Otherwise linear between min and max. |
| `vector_payload(rs, axis, pos, box, n)` | Grid on the plane, fluid points only. |
| `particle_paths(rs, box, n_grid, n_particles, steps, seed_mode, flow_dir)` | RK2 (midpoint) streamlines through the frozen grid velocity (`map_coordinates`, trilinear); step `dt = 0.4 h / p95 speed`; a path ends (NaN) on leaving the box or entering a cell that is not fully fluid. Seeds: `seed_mode` `upstream` = random points on the upstream face (dominant axis of `flow_dir`), `everywhere` = random fluid cells, `box` = random points of `seed_box` that lie in its inscribed circle across the flow (a disk; the GUI puts one 0.9 R in front of the first moving zone). |

Cost on a 2.74 million cell result at grid 96: volume about 2.5 s, vectors about 1.6 s, particles (1,500 x 160) about 3.1 s (measured once on the development machine; each request rebuilds the KD-tree, so there is no caching between requests).

Known limits: nearest-centroid candidates can miss a containing tet in extreme slivers (the point becomes NaN); the nodal field is an average of cell values (smooth, not exact); particles are paths in the frozen field at one time, not pathlines in the transient flow.

---

## 9. Front end

### 9.1 State and structure (`simwb/web/app.js`)

- Single global state object `S`: `S.pid`, `S.p` (project state), `S.step` (geometry/setup/mesh/solve/results), `S.results` (the `/results/fields` payload), `S.field`, `S.timeIdx`, `S.iso`, `S.contours`, `S.vis`, `S.vol`, `S.vec`, `S.par`, `S.surfOpacity`, `S.clip`, ...
- Helpers: `api(path, opts)` (adds `X-SimWB`, JSON body via `{json: ...}`), `P(path)` = `/api/projects/${S.pid}${path}`, `act(fn, message)` (runs a mutation with error toast), `esc`, `fmt`, `fmt1`, `toast`.
- Panels are template-literal HTML built by `...Html()` functions and wired by `bind...()` functions after insertion.
- Results step: `showResults()` fetches the main surface or section, then `showIso`, `showContours`, `showVis` (volume, arrows, particles). `resetVis()` and `S.contours = []` run on project change.
- Pattern for scale controls (auto or custom): state `{auto, lo, hi}`; `apply...()` computes the effective range (`auto` -> data range) and writes it back into the disabled inputs; unticking `auto` copies the shown values into the state.

### 9.2 Viewer (`simwb/web/viewer.js`)

| Method | Purpose |
|---|---|
| `setOverlay(p, opts)` / `clearOverlay()` | Main coloured surface; flat colours per triangle; optional mode-shape animation. `_triGeometry` builds the geometry. |
| `setIso(p, {range, opacity})` | Vortex surface with vertex colours. |
| `setContours(list)` | `[{payload, range, opacity}]` - one mesh per plane, `MeshBasicMaterial`, `transparent` if opacity < 1, `depthWrite` off when transparent. |
| `setVolume(p, win)` / `setVolumeWindow({lo,hi,density,steps})` | `Data3DTexture` (R8) on a unit box scaled and moved to the grid box; `ShaderMaterial` (GLSL3) with front-to-back ray marching (up to 512 steps, early exit at alpha 0.97), colour from a 256-entry turbo LUT, opacity ramp `smoothstep` over the window. `depthTest` is off: the volume draws over solid parts. |
| `setArrows(p, {range, lenScale, thick})` | `InstancedMesh` of cones, matrix per arrow from `setFromUnitVectors((0,1,0), dir)`, colour per instance from speed. |
| `setParticles(p, {range, rate, size, trail})`, `setParticleStyle` | Points plus `LineSegments` tails. `_stepParticles` (called from the render loop via `_animate`) moves each particle along its path at `rate` steps per second; each has a random phase and loops over its own length. |
| `setSpinZones(zones)`, `spin(t)`, `spinOverlay(dt)` | Sliding-mesh results: CAD vertices inside a zone cylinder are turned by `omega * t` about the zone axis (right-handed, as `rotatingMotion`; checked against moved mesh points: the angle at t is omega t from the `constant/polyMesh` pose). Base positions and the zone mask are stored in `geometry.userData.spin`. `spinOverlay` turns the result surface for in-between video pictures. App side: `spinZones()`, `spinTo(t)` in `showResults`; `S.videoSub` pictures per saved time in `recordVideo`. |
| `setRegions`, `setBcGlyphs`, `setZoneWire`, `flash`, `setView`, `fit` | Setup and mesh drawing, camera. |
| `turbo(t)` (export) | Colour map used everywhere. |

### 9.3 Chat and Claude commands (one input)

- Right panel `#chat`: header (`#claudeAuth`, `#chatLogin`, `#chatLogout`, `#chatNew`), `#chatLog`, `#chatSuggest`, `#chatForm` (`#chatInput`, `#chatSend`, `#chatStop`). There is one input for both.
- `sendChat(text)` decides: if a command is running (`term.busy`) the line is its stdin (`termRun`); a line starting with `/` goes through `termLine` (`/help` is answered locally, `/ask q` becomes `-p "q"`, `/login|logout|status|version` map to the claude CLI); everything else goes to the assistant over the websocket.
- Command output is stored as chat messages `{role: "term", cmd, lines:[{t, err}], done}` (kept in `chat.log`, saved to localStorage with the rest of the conversation, ignored when history is sent to the assistant) and drawn by `chatRender` as `.msg.term` blocks. URLs are linkified after HTML escaping.
- Websocket events `term_out` / `term_done` are handled by `onTermEvent`. After a `term_done` with `auth: true` the page sends `chat_reset` for the project and calls `claudeStatus()` so the assistant picks up the new login.
- `#chatStop` stops the running command if there is one, otherwise the assistant.

---

## 10. Chat and MCP tools

`simwb/tools.py` defines `TOOLS = [(name, description, JSON schema, handler)]` and `SYSTEM_PROMPT`. Current tools: `get_project_state, list_options, reset_rotor_thrusts, find_faces, highlight_faces, set_body, add_boundary_condition, update_boundary_condition, delete_boundary_condition, set_settings, auto_inlet_outlet, detect_rotors, set_rotors, set_domain, set_mesh_settings, generate_mesh, check_setup, run_simulation, get_run_status, get_results, probe_results, wait_for_run, mesh_check, model_check, cancel_job, apply_trim, rename_project, openfoam_status, install_openfoam` (plus project listing/creation tools in `PROJECT_FREE`). Chat sessions are created in `server.py` (`chats` dict per websocket) and run with `cwd = study folder`, `tools=[]`, only the `mcp__simwb__*` tools allowed.

---

## 11. Tests

| File | Covers |
|---|---|
| `tests/test_api.py` | Header/origin guards, validation, path traversal, project in a chosen folder (`test_project_saved_in_chosen_folder`). Fixture `client` reloads `simwb.project` and `simwb.server` with a temporary `SIMWB_HOME`. |
| `tests/test_pipeline.py` | End-to-end import -> mesh -> solve for built-in studies; modal solver against beam theory. |
| `tests/test_thermal.py` | Conduction against analytical 1-D solutions. |
| `tests/test_qt_close.py` | Closing: a waiting tool call no longer keeps the process alive (subprocess test), jobs and their children are stopped, the Closing notice, the watchdog. |
| `tests/test_join_and_diagnosis.py`, `tests/test_setup_lock.py`, `tests/test_qt_setup_lock.py` | Per-time join with a damaged time, failure diagnosis (banner is not a divergence), Load results from case, the setup lock and its undo. |
| `tests/test_transient_domain.py` | Transient heat, air-volume shapes, aero case files (DES blocks, steady DES refused), ring size field, parallel reconstruct split (`test_parallel_reconstruct_split`), resampled views on a unit cube (`test_resampled_views`: uniform flow, expected arrows, seeds at the upstream face). |
| `tests/test_installer.py` | OpenFOAM installer logic (no download). |

There are no automated GUI tests. Check front-end changes in a browser: `node --check` catches syntax errors only when the file is copied to a `.mjs` name (it is an ES module): `cp simwb/web/app.js $TEMP/c.mjs && node --check $TEMP/c.mjs`.

---

## 12. Change recipes

### 12.1 Add a study setting

1. Add `P("key", "Label", "unit", default, min=, max=, options=, help=, show_if=)` to the study's `settings` in `studies.py`.
2. Use `s.get("key")` in the writer (`cases.py`) or solver.
3. If the value changes what is valid, validate in the writer (raise `ValueError` with a message a user can act on).
4. Add a test in `tests/test_transient_domain.py` that builds the case and checks the written files.

### 12.2 Add a boundary-condition type

1. Add it to `face_bc_types` of the study in `studies.py` (id, label, parameters, help).
2. Handle it where BCs are turned into solver input: `Project._solve_*` (built-in) or the patch writer in `cases.py` (OpenFOAM).
3. Add a glyph in `viewer.js` `_glyph` so it is drawn on faces.
4. Add it to the chat tool schema text in `tools.py` if the descriptions list types.

### 12.3 Add an API endpoint

1. Add the function in `server.py` **above `def main()`** (see 14.1) with `@app.get/post`. Raise `ValueError` for user errors.
2. Put the logic in `project.py` or a new module; keep `server.py` thin.
3. In `app.js` call it with `api(P('/path'), {json: {...}})`.
4. Add a test in `tests/test_api.py` using the `client` fixture; mutating calls need `H = {"X-SimWB": "1"}`.
5. Add a row to section 5 of this manual.

### 12.4 Add a result visualisation

1. Back end: a function in `results.py` or `resample.py` returning a dict of base64 arrays plus the metadata the viewer needs. Add a route `GET /results/<name>` with the query parameters validated and clamped.
2. Viewer: a `setX(payload, opts)` that creates objects, stores them on `this`, and a matching removal (`_drop('name')`). Call the removal when leaving the Results step (the line `if (step !== 'results') { viewer.setIso(null); ... }` in `app.js`).
3. App: state in `visState()`, HTML in `visHtml()`, handlers in `bindVis()`, fetch in `showVis(ti)`. Cache payloads by a key so that scale changes do not refetch. Add a scale control with `auto` and custom values (see 9.1).
4. Test the numerics on a synthetic mesh (`_cube_results` in `tests/test_transient_domain.py`).

### 12.5 Add a turbulence or flow model

`studies.py` option list + `OPTION_HELP`; `cases.py` turbulence block and schemes; `results_loader.py` label text; a case-file test.

### 12.6 Add a study type

Add the entry to `STUDIES`; `Project` operations branch on `self.study`; add a solver function `_solve_<name>`; add tests; extend `create_project`'s mesh preset table and the welcome cards (they come from `STUDIES` automatically).

---

## 13. Conventions

- Match the surrounding style: short functions with a docstring that says what and why, comments only for non-obvious reasoning, f-strings, type hints where already used.
- Keep the GUI simple: one panel per task, plain-language labels, a one-line help text, units in labels.
- Never report numbers that were not produced by a run. Tests and docs may quote only measured values, with their context.
- Keep user errors as `ValueError` with a sentence that tells the user what to change.
- Long-running work belongs in a job, never in a request handler.
- Large arrays go over the API as base64 typed arrays, not as JSON number lists.
- Privacy: do not put user data (names, emails, project names) into requests to outside services.
- Commit messages describe the user-visible change and the reason. Commits end with the attribution lines the session requires.
- Push only to `initial`. Mutating API calls from scripts need `X-SimWB: 1` and, for safety, `Origin: http://127.0.0.1:8765`.

---

## 14. Pitfalls that already happened

| # | Pitfall | What to do |
|---|---|---|
| 14.1 | Routes added after `if __name__ == "__main__": main()` in `server.py` are never registered (the server starts first): a 404 for a route that exists in the file. | Insert new routes above `def main()` (above the static mount). |
| 14.2a | A renamed body of a STEP file got its old name back (gmsh restores the STEP labels on `synchronize()`). | See 8.6: names live in the `_Model` registry, the model is rebuilt without labels at the end. |
| 14.2 | A multi-line string inserted into a one-line JS string `'...'` (a template edit) broke the whole page: a blank GUI. `node --check app.js` passes on a `.js` copy only when it is treated as a script; imports make it fail differently. | Copy to `.mjs` and run `node --check`; reload the page after every JS edit and look at it. |
| 14.3 | The browser sees `window.S` state only after `boot()`; code at module top level that calls functions defined later with `const` hits the temporal dead zone. | Define helper constants before first use or use `function` declarations. |
| 14.4 | `affineTransform` in gmsh rebuilt faces as BSplines (a hub lost a third of its area). | Use `occ.rotate` for rotations. |
| 14.5 | Sliding-mesh runs diverged in 3 steps with plain PIMPLE correctors. | Keep the under-relaxed outer correctors and 2 non-orthogonal correctors. |
| 14.6 | The Solve-panel time estimate is wrong for sliding runs (a run estimated at 197 minutes needed 12.7 hours in total). | Judge from the log; do not promise run times. |
| 14.7 | `reconstructPar` is serial and used about 1.6 GB per process on 2.7 million cells; unbounded parallelism ran out of memory. | Keep the cap in `run_split_times`. |
| 14.8 | Processor directories take about 10 GB per transient sliding run. | They are removed by the `rm_processors` step after joining; do not skip it. |
| 14.9 | Heredocs with quotes and braces broke shell edits. | Write edit scripts as files and run them; assert that each replaced string occurs exactly once. |
| 14.10 | Q-criterion is largest on walls (billions); a threshold from the global maximum shows only the blade surfaces. | Use the physical start level (half the rotation rate squared, or (U/L)^2 over reference length) as `isoRef()` does. |
| 14.11 | `Windows restarts` kill the server and any running solver. | Check `Get-CimInstance Win32_OperatingSystem` boot time before blaming the code; restart the server with the same `SIMWB_HOME`. |
| 14.13 | Videos of sliding-mesh runs showed a still propeller: a Velocity field switches to a section, and the section view draws the CAD geometry, which never moved (only the result surface had moving nodes). | Keep `spinTo(t)` in `showResults`; anything drawn from setup geometry in a results view must follow the zone rotation. |
| 14.14 | `set_moving_zones` marked the mesh stale on every change, so a new rpm forced a 2.7 M cell remesh. | Only shape keys (centre, axis, radius, length, cell size, name) invalidate the mesh now. |
| 14.15 | A CAD model with micro-edges (24 edges of 1-5 micrometres, left by a symmetry cut that grazed the body) gave tets with three nodes within 2 micrometres: volume 2e-17 m3, Courant 6000 at the first step, divergence in 3 steps. `occ.healShapes` failed or doubled the face area. | `simwb/slivers.py` collapses the short edge of bad cells in the mesher output (`collapse_slivers`: a node moves only onto a node on all its surfaces, or across a micro-edge shorter than 2e-5 of the model; no flipped cell, no folded surface triangle, the worst cell of the affected set must improve). 63 -> 8 cells below quality 0.02, worst skewness 424 -> 6.5, first Courant 6100 -> 17 on the drone. `Project._symmetry_grazing` warns about the cause, and `symmetry.grazing_nudges` + `prepare_geometry` move a grazing plane (thin side under 3 % of the extent) by 1.2 x the tallest leftover face (faces wholly within min(1 mm, 1 % of the extent) above it) and cut again, twice at most (0.16 mm on the drone: severely non-orthogonal faces 88 -> 15). The drone run still diverged at the thin blade root of the propeller (two faces 0.25 mm apart, tiny faces of 9-10 mm2 between them): a mesh-resolution problem, not the plane. |
| 14.16 | Two close sliding zones share cells: faces of one interface landed on the other's process ('MPI_Bcast: Message truncated'). | `runner.split_interfaces` reads the processor boundaries after decomposePar; `balance_split` pins every interface to process 0 and splits again; `runner.failure_hint` names the cause of a failed run. |
| 14.12 | Deleting a study registered through `open_folder` deletes that folder. | The GUI asks for confirmation; do not auto-delete. |
| 14.17 | A symmetry plane cut the model (the part below it vanished, the cut face was not drawn, the frame looked hollow) and the air box in the 3D view still reached past the plane. | Flow studies: a plane carries `cut_parts` (default false); the worker cuts the air box at every plane, but the parts only at planes with `cut_parts` (parts wholly beyond a plane are still left out, `_wholly_beyond`); the face-count check compares against the parts trimmed to the box. The stored air spec / `box_min`/`box_max` end at an axis-normal plane. UI: 'Also delete the model beyond the plane'; API/agent `cut_parts` / `delete_beyond`. The mesh is the same either way. |
| 14.18 | A two-zone sliding drone run diverged (Courant 8e6 at step 19; the steady MRF start at iteration 60-100): flat sliver tets (about 8 micrometres thick on 0.3 mm edges, shape quality 0.03) at a blade root. The old repair only looked at quality below 0.02. | `slivers.collapse_slivers` now scores a cell by min(quality / q_min, altitude ratio / a_min) (`a_min` 0.05), tries all six edges shortest first and accepts a collapse only without flips or folded surface triangles (offline on the real mesh: flat cells 27 to 3). A steady start that does not diverge uses first-order convection, relax p 0.2, U 0.5, k and omega 0.5. Cause not proven by a controlled test; do not claim it. |
| 14.19 | A run can blow up or take far longer than planned. | `runner.divergence` (k, omega or epsilon bounding above 1e9, or three continuity errors above 1e3 in a row) stops the solver at once with a hint; the setting `max_hours` (Time limit, h, 0 = none) writes `stopAt writeNow` when the wall clock passes it and keeps the results (`job.extra['time_limit']`). The wall clock counts from the job start, so it includes the setup (decomposePar with its weighted re-splits: 25-40 min at 3.6 M cells); the page's 'min more' estimate includes that setup and over-estimates. |
| 14.20 | Joining the results (reconstructPar) and reading them for the viewer used one core, and 4 joins at once with 2 GB each ran the computer out of memory (the workbench was stopped by the system while the run had just finished). | `runner.run_split_times` is a rolling pool: up to one process per solver core, capped by the free memory (0.65 GB per million cells, 4 GB kept free) and a new one starts as soon as one ends. `results_loader._read_frames` reads the saved times with `frame_worker` processes (about 1 GB per million cells each, arrays handed over as .npz in a temp folder of the case); a time a worker did not deliver is read in the server. If the server dies in the join, the pieces of the unjoined times are still in the processor folders: run `run_steps_windows` with the reconstructPar step again, then POST `/results/reload`. |
| 14.21 | The live view of a run showed the residual chart only and the log only the workbench's own lines, so the residuals looked missing. | The view prints the latest initial residual of every field above the chart and the solver's last 40 lines (`job.extra['solver_tail']`). |
| 14.22 | The video looked choppy and out of step with the results: the saved times of a sliding run were 30 degrees of blade turn apart, and the in-between pictures (2x-8x) turned only the blade surface (`viewer.spinOverlay`) while the vortex surfaces and the volume stayed at the saved time, so they jumped at every saved time. | `recordVideo` makes one picture per saved time when `isoOn()` or the volume is shown (`videoCanTurn`). The saved times themselves: setting `frame_deg` (expert, default 8), `cases.frame_interval` = a whole number of fixed time steps per saved time, end time rounded to a saved time; `Project.frame_plan` counts the frames and the disk (170 bytes per cell and frame: 150 in the case, 20 in result.npz) for the setup check (warning above 50 % of the free disk, error above 90 %); `_extend_transient` snaps the new save interval and end to whole steps; the Continue card suggests one more turn at `frame_deg`; the Results note tells when the saved times are more than 12 degrees apart. A finished run with coarse frames is made smooth by continuing it, not by interpolating the vortex surfaces. |
| 14.23 | The volume cloud buried the model: its shader ray-marched the whole box with `depthTest: false`, so the haze covered the quadcopter and its blades whatever stood in front; the colour map made the thin outskirts a flat blue. | `Viewer._volumeDepth` renders the scene without the cloud into a half-resolution depth texture (redone when the camera moves, every 250 ms, and for every video picture); the shader ends each ray at that depth (`uClipToWorld`, `uInvModel`). Style `smoke` (`setVolumeWindow({style})`, `S.vol.style`, default) uses a pale colour ramp and sqrt(w) opacity so the outskirts show. A full resolution depth pass on every frame froze the page (renderer timeouts): keep it throttled. `promoLook()` sets the showcase state; `Viewer.orbitBy/cameraState/setCameraState` and `S.videoOrbit` turn the camera during `recordVideo`. |
| 14.24 | Studies were saved in the workbench's own folder unless the user typed another; the assistant created studies without asking and a Claude Code session started elsewhere knew nothing of a study. | The welcome page opens the folder chooser (`/api/pick_folder`) before it creates anything. `project.write_claude_files(d, port)` writes `CLAUDE.md` (marker line; rewritten on create, duplicate, open folder and `open_claude`) and merges a `simwb` entry (python `-m simwb.mcp_server`, `PYTHONPATH`, `SIMWB_PORT`, `SIMWB_HOME`) into `.mcp.json` without touching other servers. The assistant tool `create_project` refuses without `location` (the message tells it to ask); `ChatSession` puts `pr.dir` in the system prompt (its `cwd` already was the folder). `POST /api/projects/{pid}/open_claude` runs `agent.open_in_claude_code` (the claude CLI, no arguments, in a new terminal window with the folder as working directory). `tests/test_location.py`. |

---

## 15. Open backlog

- Prism boundary layers (CHT and aero).
- Pathlines in transient flow (particles are in a frozen snapshot now); volume rendering with depth against solid parts (it draws over them now).
- Caching of the KD-tree between resampling requests.
- Faster joining of transient results (done: parallel reconstruct); reading the processor directories directly instead of joining them.
- Time-varying loads, transient structural dynamics, sine sweep / random vibration.
- Moving zones for electronics cooling fans; body motion with mesh deformation.
- GUI tests beyond screenshots.


## CalculiX backend and the software registry

- `simwb/software.py`: one table of which software does each workflow element (`ELEMENTS`), the engines (`SOLVERS`)
  and which engines can solve which study (`STUDY_SOLVERS`, first = default). `describe(study, settings)` backs
  `GET /api/projects/{pid}/software` and the "Software used" table in the Solve step. A new solver = a row here, a
  setting, and a package under `simwb/solvers/`.
- `simwb/solvers/calculix/`: `inputdeck.py` (mesh + materials + BCs -> Abaqus-style `.inp`; the same quadratic mesh as
  the built-in solver, C3D10 with the mid-edge order of `modal_fem.EDGES`, negative-volume tets re-ordered, face
  loads as the same consistent nodal forces, supports in the node set `SUPPORTS` for the reaction totals),
  `results.py` (ASCII `.frd` fixed-width reader, `.dat` eigenvalue table - cross-checked against sqrt(lambda)/2pi - and
  total reaction), `api.py` (`solve_static` / `solve_modal`: same call and result shape as `static_fem.solve` /
  `modal_fem.solve`; the shared report logic `static_fem.body_safety` and `modal_fem.band_check` is used by both),
  `installer.py` (finds `ccx` via `SIMWB_CCX`, `<workbench>/calculix/env`, or the PATH; installs with a pinned,
  SHA-256-checked micromamba into `<workbench>/calculix/env` from conda-forge, no admin rights; routes
  `GET /api/calculix`, `POST /api/calculix/install`).
- Selection: study setting `structural_solver` (`built-in` | `calculix`) -> `Project._structural_engine()`; the run is
  in `<project>/calculix_case/job.*`; `solve.backend` records the engine.
- Not written yet (clear errors, use the built-in solver): frictionless supports on curved or tilted faces (needs a
  rotated local system per node), added masses and free-free modes in CalculiX modal, effective modal mass.
- Tests: `tests/test_calculix.py` (deck, readers, plumbing with ccx output synthesised from the built-in solution);
  `test_real_ccx_matches_builtin_fem` runs only when ccx is installed.


## Balanced decomposition (parallel Windows runs)

`runner.parallel_steps` puts `{"action": "balance_split"}` after `decomposePar`. `runner.balance_split` reads the
cells per process from `log.decomposePar` (`cells_per_process`); above `BALANCE_TOLERANCE` (1.10 x the mean) it writes
`scotchCoeffs { processorWeights (...) }` (`_set_weights`; scotch takes integer weights: mean-1 weights x 100) from
`interface_weights(cells, a)`: only the process with the most cells (the one the `singleProcessorFaceSets`
constraint piles the sliding interface onto) gets a lower weight, 1 - a x its excess over the mean. The factors in
`BALANCE_SCAN` (1.8, 1.4, 2.2, 1.0) are tried in turn, always from the first, unweighted split, until the split is
within tolerance; the best one is kept (re-split once more if the last try was not the best). More than the excess
has to be taken off (a about 1.8) because the cells the constraint moves come out of the neighbouring partitions.
The earlier method adjusted all weights from the previous weighted split (`new_weights`, still there for
reference): the short partitions moved from one split to the next and it never settled (1.31 x after two rounds
on the 2.74 M cell propeller case, against 1.08 x with one weight at 0.36-0.40). The sliding interface is pinned
to process 0 (`(<zone>_AMIfaces 0)`, not -1) so repeated splits agree; with several sliding zones zone k is pinned to
process k (modulo the number of processes) and `interface_weights` lowers the weight of every process above 1.1 x the
mean (the one with the most cells when none is). A resumed transient run drops both steps
(`Project._solve_openfoam`): it continues on its own split.

`simwb/solvers/openfoam/cpus.py` measures the speed of every physical core under full load (`core_speeds`, cached
in `~/.simwb/core_speeds.json`) and turns it into rank weights (`rank_weights`) for hybrid processors. It is not
wired into runs: on the development machine (4 Zen 5 + 4 Zen 5c cores) all cores measured within 8 % of each
other under load, so there was nothing to gain; MS-MPI ranks would also have to be pinned (`-affinity_layout`).


## Setup lock of an old OpenFOAM run (`case_setup`)

Carrying on an old run (resume, continue, extend) needs the setup to match the case on disk. The study does not keep a "was edited" flag as the truth: when `_solve_openfoam_inner` writes a fresh case it stores `data["case_setup"] = setup_snapshot(data)` (settings without the run controls `RUN_CONTROLS` = end_time, n_procs, max_hours, iterations; BCs, rotors, moving zones, bodies and materials, domain, symmetry planes, advanced settings, edited case files, the mesh settings and cell count; empty values compare equal). `mark_stale()` compares the current snapshot with it: equal means the old run is valid again (`setup_changed` and the `stale_from` status are undone), different marks it (`done` becomes `stale` with `stale_from: "done"`, an interrupted/failed run gets `setup_changed`). `Project.setup_changes()` returns the plain phrases of what differs (`setup_differences`; `None` when the case predates the snapshot: the old rule applies and the message says it cannot tell what changed), `state()["setup_changes"]` shows them in the Solve step, and `_locked_message()` is the error of a refused resume. The end time is applied on resume: when `settings.end_time` differs from the case's by more than half a save interval, `solve(continue_iterations=...)` of an interrupted transient run goes through `_extend_transient`. `n_frames` and `frame_deg` are run controls too: `data["case_run"]` keeps what the case was written with, and when they differ on a resume `_resume_interval` computes the new interval (sliding runs: the turn per second follows from the case's `transient.frame_deg / write_interval`; otherwise `end / (points - 1)`) and the resume goes through `_extend_transient(..., keep_end=True)`, which keeps the end time the user set: `fit_resume_interval(t0, end, wi, dt)` returns the end, the interval and the number of saves so that the remaining time is a whole number of saves (the requested interval if it divides it, else the nearest divisor, in whole time steps for a fixed step); `extend=(end, wi, note)` has three items, which tells `_solve_openfoam_inner` not to overwrite `settings.end_time` and to put the note in the job message. It rewrites `writeInterval` and `maxDeltaT` of `controlDict` (OpenFOAM counts write times from the time it starts at, with `startFrom latestTime`). `state()` carries `solve_lock = {message, known, can_adopt}` when the old run is locked (the Solve step shows `message`, the short panel form of `_locked_message(panel=True)`; the longer form without `panel` is the refusal of a resume and mentions the Solve step and Load results). `state()["setup_changes"]` is the same list as data for the API; the Qt panel reads it through `message`. A study without a snapshot (run by an older version) has `known: false, can_adopt: true`; `adopt_current_setup()` makes the snapshot from the current setup only after `case_mismatches(meta)` finds nothing wrong: it compares the study kind, transient/steady, `U_inf` (speed and direction), the fluid density (`rho` or `fluid.rho`), `n_cells` against the mesh stats, the rotor and moving-zone counts and zone rpm, and the symmetry planes with what `simwb_case.json` recorded, and refuses naming each difference. The Solve button asks first (`QMessageBox.question`, default No, the risk in plain words); the control command `solve.adopt_setup` needs `confirm=true` and its button id is in `control.MODAL`. `data["case_setup_adopted"]` marks an adopted study: `_adopted_warning(summary)` adds `ADOPTED_WARNING` to the warnings of every result made from it (`_solve_openfoam_inner`, `load_case_results`, `reload_results`, and at once to the existing summary); a fresh case written by this version (or a copy without results) removes the mark. The case on disk is not touched. Names in `setup_changes()` come from `_setup_labels()` (the setting label without its bracketed hint, and the unit). Not handled: the mesh status stays `stale` after a mesh setting is changed back (remesh). Tests: `tests/test_setup_lock.py`, `tests/test_qt_setup_lock.py`, `tests/test_join_and_diagnosis.py`.

## Closing the desktop app (`MainWindow.closeEvent`, `daemonthread.py`, `qt/app.ensure_exit`)

Cause of the invisible process after closing (issue 13), reproduced with the real window on a synthetic study: the assistant runs its tool calls with `asyncio.to_thread` (a `ThreadPoolExecutor`), and a tool that waits for a job (`wait_for_run`, `_wait(job, 300-600)`) holds one of those worker threads. Python joins executor threads when the interpreter ends, so the closed window left the process alive (no window, `MainWindow` handle 0) until the wait timed out, while the job's daemon thread kept working (loading results: one busy core, memory growing; 8.5 GB in 25 s in the test with a synthetic allocation loop). The X button also did not stop running jobs (only the toolbar Close did), so a solver's child processes were left running. What was changed: (1) `simwb/daemonthread.to_thread` (a daemon thread) replaces `asyncio.to_thread` in `agent.py` and `server.py`; (2) `closeEvent` now behaves the same for every way of closing: it asks once when jobs run (`_confirm_close`), cancels them (`JOBS.cancel` kills the process tree), closes the assistant sessions on a thread, waits up to `SHUTDOWN_GRACE_S` (10 s) while showing a "Closing" toast, the status bar text and the title (only after 0.4 s), then saves, closes the viewport, releases the open projects' results (`_release_memory`) and closes for real (`_shutdown` is `None` -> `closing` -> `finishing` -> `done`; the second `close()` must not be made inside the first close event: Qt ignores it); (3) `app.ensure_exit` starts a daemon watchdog when the event loop ends that calls `os._exit` after `EXIT_GRACE_S` (8 s) and prints the names of the non-daemon threads still running; (4) `.ui/control.json` is removed on exit and the watchdog's message is also appended to `.ui/close.log` (`pythonw` has no console); (5) the reader processes of the saved times (`frame_worker`) get `SIMWB_PARENT_PID` and end themselves (`parentwatch.start`) when the workbench is gone: they used to go on, holding gigabytes, for nobody (found by a synthetic job whose child process was still running after the window closed: the old close path did not stop it). Not proven: that the 6 GB of the reported run was a results load behind a waiting tool call; the mechanism and the fix are shown, the user's workload was not available. Tests: `tests/test_qt_close.py`.

## Continue a finished transient run

`POST /api/projects/{pid}/solve` with `{"end_time": s, "write_interval": s}` -> `Project._extend_transient` ->
`_solve_openfoam(resume_transient=True, extend=(end, wi))`: `endTime`, `writeInterval` and `maxDeltaT` are rewritten
in `controlDict`, `simwb_case.json` and the `end_time` setting follow, the run restarts from `latestTime` and the
progress counts from there. Without the two keys the route starts a fresh run, which clears the case folder: a
client must check that the server has this route before relying on it (an older server ignores unknown keys).
GUI: Solve step, "Continue the run to ...", shown for a finished transient OpenFOAM run.


## Static stress post-processing (`static_fem.py`)

- `average_stress(t, tv, sc, n_nodes)`: corner stress tensors averaged per (node, body) - never across two bodies.
  Von Mises and the principal stresses (`principal`) are those of the averaged tensor (before: the mean of the
  corner von Mises values over all elements at a node, bodies mixed).
- Display value at a node shared by two bodies: the body with the higher von Mises stress. Per-body maxima and
  safety factors use the body's own values (`body_safety(..., vm_body)`).
- Error estimate (Zienkiewicz-Zhu): `error_energy(sc - averaged)` integrated with the corner values (V/4 each),
  against the strain energy u.K.u / 2 (K without the support penalties): `stress_error_percent` =
  100 sqrt(e / (U + e)); warning above 15 %. Field "Stress error estimate": largest |corner - averaged| von Mises
  at each node.
- `solve` returns `fields` {name: (values, unit)} next to `displacement` and `von_mises`; `Project._solve_static`
  adds them to the `ResultSet` (Pa -> MPa).
- Tests: `test_static_cantilever_matches_beam_theory` (principal stresses = fibre stresses),
  `test_static_stress_is_not_averaged_across_materials` (two bars side by side, nu = 0: E x strain in each).


## Flutter study (multiphysics, `flutter.py`, `flutter_study.py`)

Study key `aeroelastic_flutter` (`studies.STUDIES[...]` has `category: "multiphysics"`: such a study is listed on the
"Multiphysics" tab of the start page in `renderWelcome` (the others on "Studies"); `physics` lists the coupled solvers shown as chips).
Add the next multiphysics study the same way (`category`, `physics`, its own `solve_*` in `project.py`).

- `solvers/flutter.py`: pure numpy. `isa`, `theodorsen`, `build_strips` (cuts the aerodynamic nodes into span strips, fits
  each mode to heave + pitch per strip, lift direction = chord x span), class `Flutter` (modal M, K, Cs; `aero(V, C)` gives
  A2, A1, A0 of Theodorsen strips; `qep` solves the quadratic eigenproblem; `follow` = p-k iteration for one root, matched to
  the previous root by modal assurance; `sweep`; `divergence` from the static problem K q = V^2 A0 q; `instabilities`
  = first flutter crossing refined by bisection). The result does not depend on the reference point (tested).
- `solvers/flutter_study.py`: `solve_study(mesh, props, bcs, settings, log)`: modal FEM via `modal_fem.solve` (modes now carry
  `generalized_mass_kg` = 1 / umax^2), strips from the `aero_surface` faces, sweep, report (`flutter`, `divergence`,
  `critical`, `margin`, `sweep` curves, `warnings`, `assumptions`) and the fields `Flutter mode`, `Divergence shape`
  (unit "relative", drawn like mode shapes).
- `Project._solve_flutter`, readiness in `check()`, `software.STUDY_SOLVERS`, GUI: summary branch in `summaryHtml`,
  `drawFlutter` (V-f, V-damping).
- Tests: `tests/test_flutter.py` (apparent mass, analytic divergence, reference-point invariance, p-k against a Jones lag-state
  system, trends), `tests/test_flutter_study.py` (Goland wing, whole pipeline on `examples/flutter_plate.step`).
- CFD-coupled flutter (in preparation): `solvers/openfoam/lumped.py` couples OpenFOAM to an external structure solver through
  OpenFOAM's own `lumpedPointDisplacement` boundary condition (no compiler needed). Measured protocol, angle convention (z-x-z
  Euler), force content (pressure only, N) and the 1 s cost per exchange are in the module text; `tests/test_lumped.py`.
- Open-source alternatives considered for a CFD-coupled (high fidelity) study: preCICE (adapters for OpenFOAM, CalculiX,
  SU2; Windows build is not first-class), Kratos Multiphysics (FSI and CoSimulation wheels on PyPI), SU2 (own FSI),
  solids4foam (OpenFOAM-only). Not built yet.

## Shake test (`modal_fem.py`)

`modal_fem.solve(..., sine={"axis", "accel_g", "damping_percent"})`: after the eigen-solution the mass-normalised
shapes and their stresses (`static_fem._corner_stress` + `average_stress`) go to `sine_sweep`: modal coordinates
q_i = -Gamma_i A / (w_i^2 - w^2 + 2 i zeta w_i w), relative displacement, stress and absolute acceleration by
superposition. `sweep_frequencies` puts points on and around every resonance (a log grid would step over a peak
2 zeta f wide). Amplitudes are the largest value during a cycle: for a harmonic vector a cos + b sin and a
quadratic form (von Mises squared, length squared) the peak is (P + Q) / 2 + sqrt(((P - Q) / 2)^2 + R^2) with
P = a.A.a, Q = b.A.b, R = a.A.b (`cycle_peak`, `_vm_form`). Report key `sine`: series over frequency, `peaks` (at
each natural frequency in range), `worst`; fields at the worst frequency. Settings `shake_axis`, `shake_accel_g`,
`damping_percent`; the sweep range is the excitation band, or f1 / 3 to the highest mode. Test
`test_shake_test_matches_beam_theory`: tip motion 1.566 A / (2 zeta w1^2), root stress, quasi-static limit against
a static run with the same body acceleration. Not included: residual (static) correction for the modes above the
computed ones; random vibration (PSD); a force excitation.


## GUI additions

- Simple / Expert (`S.simple`, `localStorage simwb.mode`, body class `simple`): elements with class `expert` are
  hidden in Simple mode (CSS); settings with `adv: true` (`studies.EXPERT_SETTINGS`) are left out by
  `paramInputs`; `guideHtml()` puts the next-step hint (from `project.checks`) on top of every panel. A browser
  that already has a study open starts in Expert.
- `openProject` reads `/api/jobs` so a job that is already running shows its progress after a reload.
- `viewer.setInset(px)`: the camera treats the part of the canvas above the Messages dock as its whole view
  (`camera.setViewOffset`), so Fit and the view buttons centre the picture there; the triad is drawn above the dock.
- Probe: `setOverlay` keeps the values behind the colours in `mesh.userData.probe`; `viewer.probeAt(event)`
  interpolates in the hit triangle; `viewer.setProbes([{point, text}])` draws the pinned labels. While probing the
  deformation animation is off.
- Export: `GET /api/projects/{pid}/results/export?format=vtu|csv[&time=i]`, `POST /api/projects/{pid}/report`
  (`{"image": data URL}`); `download()` in `app.js` fetches them and reports a 404 from an older running server as
  "restart the workbench".
- Routes and tests: `tests/test_api.py::test_results_export_and_report_routes`.


## Symmetry planes

**Data.** `project.json` key `symmetry`: `[{id, name, point [x, y, z] m, normal [x, y, z]}]`, the normal pointing INTO
the kept side. One derived boundary condition per plane in `bcs` (`type "symmetry"`, `plane_id`, `auto: true`, faces =
the faces lying on the plane); `Project._sync_symmetry_bcs` rebuilds them after every geometry rebuild and
`_remap_after_geometry` drops them first. Every study has the `symmetry` face BC type (`studies.SYMMETRY_BC`).

**Geometry** (`gmsh_worker._prepare`, payload key `symmetry`). After scaling / rotation and before the bodies are
listed: remember the full bounding box, intersect every solid with the half-space of each plane (`_cut_to_kept_side`:
a box built by `_half_space`, rotated with `occ.rotate` and translated; sized from the volume being cut - a tool sized
from the parts silently trimmed a big air box, test `air volume exactly half`), restore the entity names, drop empty
results ("No part lies on the kept side"). The air volume / enclosure box is built from the FULL model's size and
intersected afterwards, so the half model keeps the far field of the full one. Faces lying in a plane get
`f["symmetry"] = plane index`; an air face in external aero becomes kind `farfield` (coarse mesh, no wall treatment);
`domain_role` is `symmetry`. `info["symmetry"]` lists the faces per plane.

**Project.** `add_symmetry(axis | faces | point + normal, pos_mm, keep, name)`, `update_symmetry(id, pos_mm, flip,
name, normal, point)` (a rename alone returns None), `remove_symmetry(id)` all end in `_apply_symmetry` ->
`prepare_geometry(symmetry=planes)`: the planes are stored only when the rebuild succeeded (a failed cut changes
nothing). `add_bc(type="symmetry", faces)` creates a plane; `delete_bc` of a derived BC removes the plane;
`update_bc` of one refuses face edits. `position` of an axis-aligned plane is the coordinate along that axis.
`_turn_setup` / `prepare_geometry(turn=...)` turn the planes with the parts. `_symmetry_setup_after` drops rotors and
zones on the removed side with a note; `_symmetry_issues` and `check()` refuse planes that cut a rotor or zone and
flow / gravity that crosses a plane. `_remap_after_geometry(allow_cut)` also matches a face that a plane cut smaller or
regrew (`match_cut`: same surface type and side, coplanar, one inside the other) and bodies by name when their
centre moved. Routes: `POST /api/projects/{pid}/symmetry` `{action: add|update|remove, ...}` (returns the prepare job).
LLM tool `set_symmetry`; `add_boundary_condition` with type `symmetry` waits for the rebuild.

**Solvers.** External aero: BC `symmetry` -> patch type `symmetry` (existing); `rotor_thrusts(..., n_total)` shares the
weight over all rotors; `meta["symmetry"]`; `results_loader.whole_body` expands the forces / pressure / viscous parts,
the moment about the CofR (det R x R x M of the half about the preimage of the CofR, plus the force's lever arm), the
force history, the rotors (`mirror_rotors`) and the reference area (`area_factor`: x2 for a plane that contains the flow
direction). Zone torques use the half's own values. Symmetry patches are left out of `default_groups`. CHT: symmetry
patch fields (`type symmetry;`) for every air field and for T and p in every solid (a constraint patch with an
ordinary patch field stops OpenFOAM), `_cht_whole_device` multiplies the extensive totals and divides the thermal
resistance by the number of copies. Static: symmetry = frictionless support; reactions are counted at the penalty dofs
too (`rho = K0 u - f` over the constrained dofs), each condition reports `reaction_N`. Modal: the normal dof of the
face nodes is fixed (planes normal to x, y or z only), warning "only symmetric modes". CalculiX deck: as frictionless.
Thermal: nothing (adiabatic is the natural condition).

**Viewer.** `Viewer.setMirrors(planes, on)` builds one reflection matrix per non-empty subset of planes;
`_syncMirrors` (every frame) clones the shown geometry / overlay / iso / contour / volume / arrow / particle objects
(sharing geometry and materials), copies each source's `matrixWorld` and effective visibility into the clone and
puts the clones under a group with the reflection matrix, so animation, a turning propeller and sections follow
automatically (Three.js flips the winding of a mirrored object itself). `bounds()` includes the images.
`setSymmetryEditor(items, onChange)` draws each plane (sheet, outline, arrow to the kept side, drag handle; a draft
also tints the removed side); `_bindSymDrag` slides a handle along the plane normal (`onChange(id, point, final)`).
`app.js`: `symmetryHtml`, `bindSymmetry`, `symApply`, `symItems`, `symDragged`, `S.symDraft`, `S.symPick`, `S.mirror`.

**Tests** (`tests/test_symmetry.py`): the force / moment expansion against explicit mirrored bodies for 1, 2 and 3
planes; geometry cuts through the project (flip, move, quarter, delete, failed cut, a plane from a face, BC and body
carry-over); flow set-up checks; the OpenFOAM `symmetry` patch; whole-body forces from the half; cooling fields in
every region; half beam against beam theory and against the full beam (static and modal); the assistant tool.

**Not covered / limits.** The cooling case is written correctly but a full OpenFOAM run with the symmetry patches has
not been run in the test suite. Mirror planes of any angle other than perpendicular are refused. A transient run
with vortex shedding forces a symmetric wake (documented in the results). The section tool's world-space clip planes
also cut the mirrored geometry (the overlay of a section is cut on the server and mirrors correctly).


## Orientation: turning about the middle of the parts, and the rotation gizmo

`geometry.rotation` (3x3) and `geometry.translation` (3) are cumulative: the stored file is read, scaled, turned about
the origin by `rotation` and shifted by `translation` (`gmsh_worker._prepare`: `occ.rotate` then `occ.translate`;
`meshgeom.build` for STL / OBJ), i.e. x' = R x + t. `Project.rotate_geometry` turns about the centre of mass of the parts
(`_parts_center`: volume-weighted centroid of `info.source_bodies`; it is the same point however the parts are turned, so a turn
and its inverse bring the model back exactly - the middle of a tilted part's bounding box is not; exposed to the view as
`state.geometry.pivot`): the step is x' = D (x - c) + c, so `t_step = c - D c`; reset returns both to None. Test:
`test_a_turn_and_its_inverse_bring_the_model_back_exactly`.
`prepare_geometry(turn=D, turn_t=t, rotation=, translation=)` moves everything placed in space with the same
(D, t): face signatures and old faces / bodies for the match, rotors (centre moves, axis only turns), moving zones,
symmetry planes (`symmetry.turn(plane, D, t)`).

Viewer: `setRotateGizmo({center, size, onRotate})` draws three rings (fixed in the world, radius 0.62 x the parts'
largest extent); `_bindRotateDrag` picks the ring whose circle passes within 9 px of the pointer, takes the angle of
the pointer ray in the ring's plane (right-handed about the axis, unwrapped, Shift = 5 degree steps) and calls
`onRotate(axis, angle, final)` (first with 0 when a drag starts). `setPreviewRotation(R, center)` turns the
non-air meshes, rotors and zones about `center`. `app.js`: `refreshRotateGizmo`, `rotAbout`, `partsCenter`,
`S.rotPending` (the cumulative preview turn, sent as `matrix` on Apply), `S.rotGizmo` (rings shown). Symmetry
planes need a STEP file (an STL / OBJ mesh cannot be cut: `_apply_symmetry` refuses). Tests:
`test_rotate_geometry_keeps_setup`, `test_planes_turn_with_the_parts_about_their_middle`.

**Aligning a face with a plane** (`simwb/orient.py`). `align_matrix(n, target)` is the shortest rotation (Rodrigues; a half
turn about an axis normal to n when they are opposite) taking the face's outward normal onto `target` ('+x' ... '-z').
`outward_normal(face, faces, solid)` decides which side of a flat face is outside geometrically, because the kernel's
`getNormal` is not consistently outward (it depends on how the surface was built; checked on a box with a hole): a point a
little off the face is inside the adjoining volume or not by ray parity (three random directions, majority) against the
triangles of every face of that volume; outward is out of the volume for a part, INTO the air for a flow study's body faces.
`Project.align_info(face)` (route `GET /align_info?face=`) returns the outward normal, a point on the face and the nearest
axis; `Project.align_face(face, target)` turns the model by that matrix through `rotate_geometry(matrix=)` (tool
`align_face`; `POST /rotate` also accepts `{"align": {"face", "to"}}`). The GUI computes the same turn in JS
(`alignRotation`) so it can preview it with the rotation gizmo's `S.rotPending` and the Apply / Cancel card; the face is
highlighted (`faceColorFn`) and `viewer.setAlignMarker` draws its normal. Tests: `test_align_matrix_and_nearest_axis`,
`test_outward_normal_is_decided_geometrically_not_from_the_stored_sign`, `test_a_flat_face_is_laid_on_a_coordinate_plane`.

`Project.wall_info(face)` (route `GET /align_info?face=&wall=1`) gives a wall of the air volume (kind `farfield` / `enclosure`, flat) with its normal out of the tunnel (`outward_normal(..., solid=True)` on the air volume); `align_face(face, wall=id, away=False)` turns the model so the face's outward normal equals the wall's (or its opposite). GUI: `S.alignWallPick` (shows the air box, `S.showAirSaved` restores the checkbox), `alignToWall`, `alignReset`. Test: `test_a_face_is_laid_against_a_wall_of_the_tunnel`.

`Project.ref_info(face, exclude)` is the plane to align to: a tunnel wall (`wall_info`) or another flat face (`align_info`); `align_face(face, wall=<that face id>, away)` makes the face's outward normal equal to its normal (`away`: the opposite). The GUI asks for it as the click right after the face (`startRefPick`, `GET /align_info?ref=1`). A previewed turn (`S.rotPending`) is applied by `goStep` when the Geometry step is left (`applyOrientation(then)`).


## Handles in the 3D view (`simwb/qt/handles.py`)

One behaviour for everything that can be dragged in the 3D view; a control describes what it wants as a list of specs and gets `callback(key, value, final)`.
Kinds: `plane` (a sheet dragged along its axis), `point` (a ball and x / y / z arrows; with a `resolver` the ball follows the surface under the pointer), `ring`
(`axis` or `normal`, `point`, `radius`; value = angle in rad since the drag began; `detent_deg` makes it jump in steps, Shift snaps to `snap_deg`), `face` (`point`, `dir`,
`length`, `limits`; value = distance in m moved outwards since the drag began). Rings and faces call back with 0 and `final=False` when the drag starts (the control
keeps what it was), then with the value, then with `final=True` on release; planes and points report the new position. **Escape**: `HandleSet.cancel()` (and `RotateGizmo.cancel()` of the Geometry step, `PlaneHandle.cancel()` of a symmetry plane; the viewport's
`cancel_drag`, called first by `Shell.escape` because the window owns the Esc shortcut) puts the handle back to what it was at the press (`restore`) and tells the control the
drag ended there (`callback(key, old, True)`, or a spec's `on_cancel`; `on_press` / `on_cancel` are how pins and line ends restore what they were). **Which handle is taken**
(`pick`): a ball, then the square end of a face handle, then an arrow (an arrow's cone counts as part of it; a move arrow before the shaft of a face handle: where a move arrow is under the pointer a resize arrow's shaft is not taken; between collinear arrows the one
whose own tip is nearer), then a ring (`RING_PX`, taken anywhere on it). A face handle may `start` away from its face (a thin line leads to it). While a handle is dragged `HandleSet.set()`
updates the other handles (a region's rings follow the arrow that moves it) and the dragged one follows the pointer itself. `"fit": True` keeps the largest
part of a spec inside 30 % of the picture's smaller side (it is drawn again when the camera moves). Everything works from pixel positions and the camera, so
`simulate_move(key, value, part=)` is a whole press-drag-release for tests and the control API (`view.handle`: mm for a point or a face, degrees for a ring).
Users: `resulthandles.py` (Results step), `regionhandles.py` + `regionxform.py` (the Move / turn session of a refinement region: `x' = R (x - c) + c + shift`, faces
through `regions.grown`), `zonehandles.py` (a rotating zone in Setup: move, `zones.grown` for the faces, a quarter-turn ring through `zones.tilted_axis`; stored on
Apply: a zone that is dragged is a **draft** (`Rotors.draft`, `current(i)`: the stored zone with the dragged shape), shown by `draw_zones` and the card, stored by `apply_draft` -> `set_moving_zones`,
dropped by `cancel_draft` or by leaving the step; typed shape numbers change the draft while one is pending. The handle layout is in `zonehandles.py` (move arrows 0.7 of the
size, resize arrows starting at 1.0 of it from the middle with a thin line to the face, rings 1.4). **Smallest zone**: `zones.min_sizes` (4 cells / 2 % of the model for the radius, 2 cells / 1 % for the length;
`hard=True` is half of it and is what `set_moving_zones` refuses); `zones.grown` stops there, `Rotors.clamp(z, only)` raises only the typed field (never grows a zone that is already smaller; a cell size the zone is too small for is refused in `set_zone`). The zone whose card was used
last (`QApplication.focusChanged`, the card's `zone_index` property) shows its handles. **Colours**: `zones.colour_of(i)` for every drawing of zone i. **Overlap**: `Rotors.overlaps` (the engine's `_zones_touch`) is shown in the card, the bar and the drag label before Apply. **Parts**: `zones.part_tints` (inside purple, cut amber; under 1 % of a part's
surface is outside) tints faces in Setup and Mesh; `ZoneView` makes its solid faint while `camera_inside`. **Sliding interface**: `Project.zone_interface()` reads the mesh's
`zone_tris` / `zone_tri_zone` (cached on the file's mtime) -> `ZoneView.set_interface`, counted by the Mesh panel. **Regions follow the model**: `regions.transformed`, called
from `Project._turn_setup` for the whole-model turn or move. The Geometry step keeps its own `RotateGizmo` (it also previews the model).
Tests: `tests/test_qt_handles.py`, `tests/test_regions_turn.py`, `tests/test_zone_handles.py`; real mouse events in a window with OpenGL: `tests/test_qt_handles_mouse.py`
(drives `tests/gl_drive_handles.py` in its own process, skipped where no OpenGL window can be made).

## Centre of rotation from a round feature (`simwb/axisfit.py`)

`fit_cylinder(face)`: axis = the eigenvector of the area-weighted `sum n n^T` of the triangle normals with the smallest
eigenvalue (all normals of a cylinder are perpendicular to its axis; refused when that eigenvalue is more than 2 % of the
total), circle = least-squares (Kasa) fit of the vertices projected on the plane normal to the axis (refused above 2 % rms
of the radius), centre on the axis half way along the face's extent; works for an arc of a cylinder. `boundary_loops(face)`
chains the edges used by one triangle into closed loops; `fit_circle3d` fits a plane and a circle to a loop;
`nearest_circular_edge(face, point)` picks the circular loop closest to the click; `pick_axis` uses the cylinder fit for
a `Cylinder` face and the nearest circular edge otherwise. `Project.axis_info(face, point)` (route
`GET /axis_info?face=&x=&y=&z=`) makes the axis sign positive along its dominant coordinate and adds `nearest`
and `angle_deg` to the nearest coordinate axis. The viewer now passes the clicked 3D point as the fourth argument of
`onPick` (and the `allowed` flag the page was dropping). GUI: `S.axisPick`, `axisPicked` (zone: centre and axis, axis
letter kept when it is the same line; more than 0.5 degree off an axis: `S.axisOblique` offers `axisTurn`; rotor: centre and
axis with the sense of the current axis), `viewer.setAxisMarker`. `rotate_geometry(..., snap_zones=True)` (body key
`snap_zones` of `POST /rotate`) keeps a zone whose axis the turn tips on its nearest coordinate axis, with a note, instead of
dropping it (the default). Fixed on the way: the delete button of a moving zone could not find its card
(`action()` now includes `[data-mzone]` in the card selector). Tests: `test_cylinder_axis_centre_radius_from_a_tilted_face_and_from_an_arc`,
`test_the_circular_edge_nearest_to_the_click_is_used`, `test_axis_info_on_the_real_bracket_holes`,
`test_a_tipped_zone_is_dropped_or_snapped_and_a_pick_puts_it_on_the_round_feature`.
