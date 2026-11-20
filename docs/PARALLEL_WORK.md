# Working on the Qt app in parallel (git worktrees)

Several agents (or people) can work on the desktop app at the same time because each one has **its own folder and branch** (a git
worktree) and owns **its own files**. Nobody edits the main folder while the others work.

## The folders

| Area | Branch | Folder (next to the main one) | Port | Owns (edit only these) |
|---|---|---|---|---|
| **viewtools** | `qt/viewtools` | `MechanicalSimulationWorkbench-qt-viewtools` | 8801 | `simwb/qt/viewport.py`, `simwb/qt/legend.py`, new `simwb/qt/viewtools.py` (view toolbar, section, picture, hover) |
| **geometry** | `qt/geometry` | `MechanicalSimulationWorkbench-qt-geometry` | 8802 | `simwb/qt/panels/geometry.py`, new `simwb/qt/align.py`, `orient.py`, `cadedits.py` |
| **setup** | `qt/setup` | `MechanicalSimulationWorkbench-qt-setup` | 8803 | `simwb/qt/panels/setup.py`, new `simwb/qt/forms.py` (schema-driven forms), `bcs.py`, `rotors.py`, `symmetry.py` |
| **solve-mesh** | `qt/solve-mesh` | `MechanicalSimulationWorkbench-qt-solve-mesh` | 8804 | `simwb/qt/panels/mesh.py`, `simwb/qt/panels/solve.py`, new `simwb/qt/jobs.py`, `meshtools.py` |
| **results** | `qt/results` | `MechanicalSimulationWorkbench-qt-results` | 8805 | `simwb/qt/panels/results.py`, new `simwb/qt/probes.py`, `charts.py`, `exports.py` |

(`MechanicalSimulationWorkbench-hex` is the separate hex-mesh work, `MechanicalSimulationWorkbench-multiphysics` the flutter / CFD-coupling
work (branch `multiphysics-flutter`), and `MechanicalSimulationWorkbench-secfix` a short security fix (`fix/results-no-pickle`: result files are
never unpickled); leave them alone.)

Shared files - touch them **minimally** and say so in the commit message, because several areas need them:
`simwb/qt/main_window.py` (one line to install a feature), `simwb/qt/control.py` (actions), `simwb/qt/panels/common.py`,
`simwb/qt/theme.py`, `simwb/qt/ids.py`. Engine files (`simwb/project.py`, `tools.py`, `server.py`, `results.py`, ...) belong to the
engine work: change them only for what your feature needs, in small commits, and do not reformat.

If a feature needs a file another area owns, **ask its owner** (or wait for their merge) instead of editing it.

## Contracts between areas

* **Picking** (`Viewport` in `viewport.py`, declared now, implemented by *viewtools*): signals `facePicked(id, add)`,
  `faceHovered(id)`, `pointPicked(x, y, z)`; `set_pick_mode(None|"face"|"point")`, `set_selection(ids)`, `set_face_tints({id: colour})`.
  Geometry (align a face), setup (boundary-condition faces, symmetry from a face) and results (probe, line plot) code against these.
  Until the real picking lands, `view.pick_face` / `view.pick_point` (control API) emit the signals by hand, so panels are testable.
* **Ids**: every control gets a stable id (`simwb/qt/ids.py`, `wid(widget, "area.name")`); menu entries use the action's object name.
  `tests/test_qt_control.py::test_every_control_has_an_id_*` fails if one is missing: nothing may be out of the API's reach.
* **Actions**: each new operation is also a control-API action (`simwb/qt/control.py`, `_register`) that drives the same widgets.
* **Engine first**: logic that is not about drawing (what a boundary condition needs, units, checks, verdicts) goes in the engine,
  not in a panel.

## Setting up a worktree to work in

The folders already exist. In one, every command needs these (they keep the areas from colliding on ports, workspaces and the screen):

```
cd /c/Github/MechanicalSimulationWorkbench-qt-<area>
export SIMWB_HOME="$PWD/.workspace"      # this worktree's own studies (git-ignored)
export SIMWB_PORT=88NN                   # the port in the table above
export QT_QPA_PLATFORM=offscreen SIMWB_NO_GL=1    # tests need no screen
PY=/c/Github/MechanicalSimulationWorkbench/.venv/Scripts/python.exe     # the one shared Python environment
$PY -m pytest tests/test_qt_control.py tests/test_qt_app.py -q
```

* `simwb` is not installed as a package: Python imports it from the folder you run in, so each worktree runs **its own** code.
* A real window (`$PY -m simwb.qt --debug-api --api-port 88NN`) needs the desktop: **one real window at a time across all
  agents**; make it short, close it with the control API (`ui_eval` `win.close()`), and prefer the GL-free tests.
* Heavy solver runs (OpenFOAM, large meshes): **one at a time** across all agents.
* Tests that mesh or solve use their own temporary workspace; never point `SIMWB_HOME` at another worktree's `.workspace`.

## Rules

1. Commit small and often on your own branch. Never commit to `main` or `qt-desktop` directly. Never push.
2. Before saying "done": your area's tests pass **and** `tests/test_qt_control.py` + `tests/test_qt_app.py` pass.
3. New behaviour comes with tests (see `tests/test_qt_control.py`: build the real window with `SIMWB_NO_GL=1`, drive it through
   `ctl.do(...)` / `ctl.widget_do(...)`, assert on `ctl.state()`).
4. Look at it too: a screenshot (`ctl.screenshot("window")` in a real window) and compare with the web version of the same step
   (`docs/QT_PARITY.md` lists what the web version has; run it with `python -m simwb.server`).
5. Update the status column of `docs/QT_PARITY.md` for the rows you finish.

## Merging (done by one person, in this order)

`qt/viewtools` first (it provides picking), then `qt/geometry`, `qt/setup`, `qt/solve-mesh`, `qt/results`, each:
`git switch qt-desktop && git merge --no-ff qt/<area>` then run the whole suite
(`$PY -m pytest tests -q`, about 11 minutes) before the next merge. Conflicts should be limited to the shared files above.
`qt-desktop` becomes the new base when it is merged into the main line by the owner.

## Housekeeping

* Remove a worktree when its branch is merged: `git worktree remove ../MechanicalSimulationWorkbench-qt-<area>`, then
  `git branch -d qt/<area>`.
* Delete scratch files you made (temporary studies, screenshots) when you finish; `.workspace/` is git-ignored but still disk.
