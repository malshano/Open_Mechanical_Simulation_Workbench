# The desktop (Qt) application

`python -m simwb.qt` (or `run_workbench.bat`) opens the workbench, which is a desktop program (there is no browser version any more). It uses the
same engine (`simwb.project`, the solvers, the Claude tools) **in the same process**: there is no web server in between.
The web version (`python -m simwb.server`) stays, for remote machines and for Claude Code's MCP connection.

## What is in it

| Part | Where | Notes |
|---|---|---|
| Window, docks, menus, step bar | `simwb/qt/main_window.py` | dark theme from one set of design tokens (`theme.py`) |
| 3D view | `simwb/qt/viewport.py` | VTK: colour maps, deformation, mode shapes, cuts, depth-peeled transparency (contours/glass are drawn in the right order from every side) |
| Colour legend | `simwb/qt/legend.py` | round ticks, log scale, category swatches |
| Step panels | `simwb/qt/panels.py` | Geometry, Setup (materials), Mesh (colour by quality / cell size / region, cut), Solve, Results (summary tiles, field, colour map, bands, deformation) |
| Recording notice, Stop, pause when minimised | `simwb/qt/recording.py` | the MP4 button's `Recording`; `exports.record(gate=...)` asks it before every picture |
| "Show in the view" list (parts, air box, zone / region outlines of a flow result) | `simwb/qt/visibility.py` | one list for the Results step; the window draws what `selection()` says (`main_window.update_view`) |
| Vortex-surface smoothing | `simwb/surfsmooth.py` | windowed-sinc display filter on the iso payload (does not shrink); the view shades it smooth (`show_surface(smooth_shading=)`) |
| Drag handles of the Results step | `simwb/qt/handles.py` (the one behaviour: `plane` and `point` kinds, `HandleSet`), `simwb/qt/resulthandles.py` (binds the controls to it) | a refinement region (#7) or a rotating zone preview (#10) would add specs of the same two kinds: `{key, kind, axis/point, value, box, callback(key, value, final)}` |
| Engine facade | `simwb/qt/engine.py` | cases and jobs, as Qt signals |
| Claude assistant | `simwb/qt/assistant.py` | the same `agent.ChatSession` and tools as the web chat (no shell, no file access); log in / out; suggestion chips; chat is kept per study |

## The start screen

While no study is open the window shows "What do you want to simulate?": a card for every study type (what it does, built-in or
OpenFOAM solver, "Use my CAD file" or a bundled example), the studies opened lately, Open and Import. It re-flows with the
window; the working docks come back when a study opens.

## Control API: Claude can see and drive the running window

Everything a person can do or see in the window is also a call, so that Claude (in the in-app chat, in Claude Code, or a script)
can debug the application while it runs. The window serves it on `127.0.0.1` (port 8765, or the next free one; `--no-api` turns it off):

| What | REST (`/api/ui/...`) | Tool (in-app assistant and MCP) |
|---|---|---|
| everything the window shows: step, study, every control's value, what the 3D view draws (field, range, colour map, deformation, camera, legend), jobs, assistant, error counts | `GET state` | `ui_state` |
| every operation of the user interface by name (`study.new`, `mesh.generate`, `results.show` ...) | `GET actions`, `POST do` | `ui_actions`, `ui_do` |
| every control by stable id (`mesh.generate`, `results.colormap` ...): get, click, set, select | `GET widgets`, `POST widget` | `ui_widgets`, `ui_widget` |
| a picture of the window, the 3D view or one control (PNG; Claude Code gets the image itself) | `GET screenshot?what=` | `ui_screenshot` |
| the log: Python logging, uncaught exceptions (also from threads), Qt and VTK messages, anything printed | `GET log?after=&level=` | `ui_log` |
| wait until jobs are finished and the window has caught up | `POST wait` | `ui_wait` |
| controls without an id (always empty: a test keeps it so) | `GET coverage` | |
| run Python inside the application (**debug only**) | `POST eval` | `ui_eval` (MCP only) |

* `do` and `widget` drive the very widgets and handlers a mouse click would use, and answer with the new step, what the 3D view
  draws and the warnings or errors the operation logged: a bug in the window shows up in the answer.
* Controls that open a dialog (file choosers) cannot be driven; their error names the action that takes the path directly
  (`study.open_folder`, `study.save_as`, `study.export`, `study.import`, `geometry.import`, `study.new`).
* The engine itself (every project operation the web version had) is in the same server: `/docs` lists the REST endpoints and
  `/api/tools/<name>` runs any of Claude's tools. Claude Code reaches both through the study's `.mcp.json` (MCP server `simwb`),
  which forwards to the running window.
* **Security.** Local only (127.0.0.1, local Host and Origin, `X-SimWB: 1` header on every change). `eval` is off unless the window
  was started with `--debug-api` (or `SIMWB_DEBUG_API=1`), and then every route to it - the REST endpoint, the tool endpoint, in-process
  calls - needs the secret of that run, checked inside the control layer itself (not only in one route). The secret is in
  `<workspace>/.ui/control.json`; the MCP server reads it from there and the model never sees it. Every use is logged. Do not enable
  it on a shared machine. Because the MCP server supplies the secret itself, **for a model connected through MCP `--debug-api` is the
  only barrier**: do not start the window with it while an MCP client you do not trust is connected.
* **Files named by a model.** The tools the assistant and MCP clients use (`ui_do` and the MCP tools `create_project`, `import_step`)
  check every path before anything is touched (`simwb/tools.py`, `_check_paths`): a full local path starting with a drive letter (no
  network, device, relative or `~` path, no links, no alternate streams), written only inside the workspace or the temp folder, in the
  kinds of file the workbench makes, never replacing a file or one of the workbench's own files (`project.json`, `locations.json`,
  `CLAUDE.md` ...); a file that is read must exist and be of the expected kind (CAD, `.zip`). An action not listed that takes a
  path-like argument is refused. A person at the menus and the REST window control (`/api/ui/do`) are not limited. A saved study
  (`.zip`) is unpacked with its entry names judged as text, an agent's settings (`.claude/`, `CLAUDE.md`, `.mcp.json`) left out and a
  size cap; the MCP server attaches a picture only for `ui_screenshot`, from the workspace's screenshot folder.
* Every new control must get an id (`simwb/qt/ids.py`: the `wid` property, or an object name for menu entries): `tests/test_qt_control.py`
  fails if any control is out of the API's reach.

```
python -m simwb.qt --project MyStudy --debug-api
curl http://127.0.0.1:8765/api/ui/state
curl -X POST -H "X-SimWB: 1" -H "Content-Type: application/json" -d '{"action":"results.show","args":{"colormap":"viridis"}}' http://127.0.0.1:8765/api/ui/do
```

## Cases (save and open)

A case is a study folder (`project.json`, geometry, mesh, solver case, results). It is written after every change.

| Menu | What it does |
|---|---|
| File > New study | back to the start screen (the open study is saved first); the type is chosen on its cards. A card's "Use my CAD file" asks for the file, makes the study from it and names it after the file; an example card imports the example. New studies go in the folder last used for Save as (the workspace at first) |
| File > Open study folder | any folder that holds a `project.json` |
| File > Open from the workspace / Open recent | the studies in the workspace; the last ten opened |
| File > Save | writes the study now and says when (it is also saved on every change and on exit) |
| File > Save as | a full copy (setup, geometry, mesh, and optionally case + results) in another folder; the copy becomes the open study |
| File > Export study to one file | one `.zip`, with or without the solver case and results |
| File > Import study from a file | unpacks an exported file into a new study (refuses anything that is not one, or that would write outside its folder) |

The same functions are in the engine (`save_project_as`, `export_project`, `import_project` in `simwb/project.py`), so the web
version and scripts can use them.

## Licences

Qt for Python (PySide6) is LGPLv3/GPLv3, VTK is BSD; the workbench is GPLv3. Only core Qt modules are used (Core, Gui, Widgets).

## Not done yet

Most of the web version's features are still to be ported (about 140 controls): see **`docs/QT_PARITY.md`** for the full list with
status, and **`docs/PARALLEL_WORK.md`** for how several agents port them side by side in git worktrees.
