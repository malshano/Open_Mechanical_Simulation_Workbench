# Qt app vs the web version: feature parity

The web version (`python -m simwb.server`, `simwb/web/`) is the reference. This list was made by running it, stepping through all
five steps of a static, a thermal and an external-flow study in Expert mode and recording every control (about 140 distinct ones),
plus the toolbar and application bar. Status: ✅ done in the Qt app · 🟡 partly · ⬜ missing. **Owner** = the worktree in
`docs/PARALLEL_WORK.md`. Update the status when a row is finished and tested.


> **Last reviewed:** after the fifth critic round of the Qt interface (overall 5.4 / 7). Rows marked ✅ have tests in `tests/test_qt_*.py`;
> 🟡 rows say what is missing. After merging the four porting branches (Setup, Mesh and Solve, Results, Geometry) the remaining gaps are small:
> dragging the air volume, a symmetry plane, rotors and zones with the mouse, and the web version's symbols for each kind of condition.

## Application bar, view toolbar, status

| Web feature | Qt | Owner |
|---|---|---|
| Start page with a card per study type, examples, open, recent | ✅ | - |
| Project switcher (dropdown of studies), brand = back to start page | ✅ (study switcher in the application bar, New) | viewtools |
| Simple / Expert mode (hides advanced settings) | ✅ (remembered; hides advanced settings and the Mesh advanced options) | setup |
| Jobs menu (all running jobs, stop them) | ✅ (running jobs, Stop) | solve-mesh |
| Claude assistant: chat, login, new chat, suggestions | ✅ | - |
| Claude: Stop button, Claude Code button (terminal), `/login /logout /status /help`, history after reload | ✅ (Stop always shown, Claude Code, `/login /logout /status /ask /version /help` run in the chat, the chat is kept per study and read again after a restart, busy per study; the assistant itself cannot press Claude Code or sign in) | viewtools |
| Close button (stops all jobs) | ✅ | solve-mesh |
| View buttons Iso, X, Y, Z, Fit (toolbar) | ✅ (in the 3D view's own bar) | viewtools |
| Edges toggle | ✅ | viewtools |
| Air box toggle (translucent) | ✅ | - |
| Section: axis, slider, value, play sweep, Hide air | ✅ (second bar of the 3D view on Mesh and Results: Off/X/Y/Z, slider, value in mm, play, Hide air; it drives the panel's own controls; flow results too. Not ported: the section on the CAD faces of the Geometry step) | viewtools |
| Probe, Picture (PNG with legend) buttons | ✅ (both in the 3D view's bar; Probe is greyed with a reason outside Results) | viewtools / results |
| Hover tooltip with face info, selection hint banner | ✅ (tooltip: face, kind, part, area, condition; hint bar with Done / Cancel for faces, symmetry plane, centre of rotation, probe, line) | viewtools |
| Colour legend (round ticks, log) | ✅ | - |
| Orientation triad | ✅ | - |
| Status bar: study, mesh, solve state, job progress bar + Cancel | ✅ (job message, progress bar and Cancel) | viewtools |
| Toasts, dialogs | ✅ (toasts replace the boxes that only inform: saved, copied, job done or failed, errors, About; real questions stay dialogs; keys on every view button; focus ring) | viewtools |
| Faces picked in the 3D view (click, hover highlight, multi-select) | ✅ (click, Ctrl-add, hover highlight; picks through the air box) | viewtools |

## 1 Geometry

| Web feature | Qt | Owner |
|---|---|---|
| Import STEP / STL / OBJ; Replace… | ✅ (import, Replace) | geometry |
| Units of the file (×1, ×0.001, inch→mm, ×100 ...) | ✅ | geometry |
| **Align a face to a plane** (pick a face, choose the plane) | ✅ flat or curved (average normal, or the normal at the click), STL/OBJ too; to a coordinate plane, an air-volume wall of any shape (listed by name, shown and fitted while picking), any body face, or a face clicked in the view; icon message box, highlighted face, direction arrow | geometry (+ viewtools picking) |
| Turn the model: X/Y/Z ±90°, free angle about an axis, Reset, Apply orientation, Cancel; drag rings (Shift snap) | ✅ rings + move arrows + free-move ball, live preview and readout, Shift snap (5°, round mm), moves of the whole model or one body, typed move fields, Apply / Cancel / Reset, Hide gizmo | geometry |
| Bodies list with show/hide (eye), highlight, rename | ✅ (eye, select; hide in the view) | geometry |
| Parts list (flow studies) | ✅ hide / show | geometry |
| **CAD edits** (add/cut shapes, holes, fillet, chamfer, mirror, pattern, scale, move, loft): list, undo last, remove, remove all | ✅ list, undo, remove, remove all and an add form for every kind (web: list only, adding through the assistant) | geometry |
| Air volume (flow studies): shape Box / C-shape / Cylinder, sizes, Apply, Fit to parts, Show all | 🟡 all of it with typed sizes; the orange drag handles in the 3D view are not ported | geometry |
| Automatic domain size presets (Minimal / Compact / Standard / Large, Fit with these) | ✅ | geometry |
| Hide rings; next-step button | ✅ Hide gizmo; Next: setup | geometry |

## 2 Setup

| Web feature | Qt | Owner |
|---|---|---|
| About this simulation (what the study does) | ✅ | setup |
| Parts & materials: rename, material list per body, confirm default material (✓ Use ...), eye, highlight | ✅ | setup |
| **Boundary conditions**: add by type, name, faces (pick in the view / by chip), type-specific parameters, delete, highlight, colour glyphs in the view | ✅ (add by type, name, faces from the view, parameters, delete) | setup (+ viewtools) |
| Boundary-condition glyphs drawn on the model | ✅ (every type: tint + label with name/value or type; arrows for loads, flows, pressure/heat in or out, exchange; free stream, oncoming air, open sides, rotors, zones, heat sources, symmetry planes, gravity - through the tint/label/arrow API; no 3D block/roller symbols or draggable discs/planes) | setup |
| Loads: gravity direction | ✅ (setting, arrow in the view) | setup |
| Solver choice for the study (built-in, CalculiX ...) | ✅ (Solver line in About; the choice in Advanced settings, open in Expert mode, as the web shows it in Expert mode) | setup |
| **Symmetry planes**: plane ⟂ x/y/z, from a face, flip, remove | ✅ (draft with axis/position/flip/Set, from a face, position, flip, remove, cut parts (aero), linked condition greyed; no mouse drag of the plane) | setup |
| Rotors: detect on motor tops, thrusts, reset to automatic, edit | ✅ (detect, add, radius, thrust/auto, centre on a cylinder or edge, delete, reset, weight/thrust note) | setup |
| Moving (rotating) zones: add, edit, delete | ✅ (add, every field, fit around part, centre on a cylinder or edge, turn the model for a tilted axis, delete) | setup |
| Flow condition: fluid (air/water), speed, direction, turbulence model, steady/transient, flow & solver settings | ✅ (headed as in the web; expert settings open in Expert mode; suggested end time with Use it) | setup |
| Readiness checklist (errors/warnings that jump to the control) | ✅ (items jump to their control) | setup |
| Auto-detect inlet & outlet (cooling studies) | ✅ | setup |
| Settings of every study type (generic form from the study spec) | ✅ (generated from the study spec, Advanced hidden in Simple mode) | setup |

## 3 Mesh

| Web feature | Qt | Owner |
|---|---|---|
| Mesh type (tet / hex-dominant), resolution presets, generate | ✅ (tet / hex-dominant, presets, generate) | solve-mesh |
| Advanced sizes and defeaturing | ✅ | solve-mesh |
| Part sizing (per part size), delete | ✅ | solve-mesh |
| Refinement regions: box, cylinder, wake behind rotating zones, tip-vortex rings, delete | ✅ (box, cylinder, wake, tip-vortex rings, fit around part, cell estimate) | solve-mesh |
| Wall layers (prism / inflation): number, growth ratio | ✅ plus (new in the Qt app): first layer, target y+, the layers made with the mesh, the "Wall layers made" card, colour by Wall layers, Zoom to the layers, Make the layers again | solve-mesh |
| Statistics (cells, nodes, near-wall/bulk size, quality, non-orthogonality, skewness, hex share ...) | ✅ (the web version's table, row for row) | solve-mesh |
| Colour the mesh by quality / cell size / aspect ratio / size jump / volume / region; cell budget | ✅ (all six; the cell budget under them) | solve-mesh |
| Cut through the mesh | ✅ | - |
| Cell quality histogram; show cells below a threshold; zoom to worst | ✅ (histogram, verdict, show cells below a threshold, zoom to the worst) | solve-mesh |
| Mesh-sensitivity check, three-level (GCI) check; mesh verdict | ✅ (verdict in words as the web version; the check and GCI with their table in the Mesh step) | solve-mesh / results |

## 4 Solve

| Web feature | Qt | Owner |
|---|---|---|
| Run, progress, status, log | ✅ (Run first on the step, progress, state, residuals and their chart, Run again after a finished run) | solve-mesh |
| Solver selection, software used per step, install OpenFOAM / CalculiX, re-check | ✅ (install with progress and Cancel, other ways, Re-check) | solve-mesh |
| Cores (Auto / 1..N), initialise from another study, uniform flow | ✅ (also Max iterations and the expected run time) | solve-mesh |
| Advanced (OpenFOAM) numerics: schemes, limits, reset to defaults, revert to generated, save | ✅ | solve-mesh |
| Continue run, extend a transient run, resume, Stop and keep results, Cancel | ✅ | solve-mesh |
| Write OpenFOAM case only; case files viewer; download case zip | ✅ (case files editor in the panel; Save case (.zip)…) | solve-mesh |
| Solver output (latest lines) + full log | ✅ (also in the Output dock) | solve-mesh |
| View results → | ✅ | solve-mesh |

## 5 Results

| Web feature | Qt | Owner |
|---|---|---|
| Tabs Summary / Display / Probe / Checks | ✅ | results |
| KPI tiles with verdicts, parts bars (stress vs yield, temperatures, allowed temperature), heat balance, force/moment bars | ✅ (tiles; parts bars against yield; temperature bars with an allowed temperature and margins; where the heat goes; force / moment bars from a centre line; force at each support) | results |
| Modal spectrum plot, flutter plots, sweep plot, residual history | ✅ (spectrum stems pick a mode; flutter frequency and damping with the critical speed; shake-test sweep log-log with peaks; residuals; forces / temperatures over time) | results |
| Field choice, time bar for transient results, animate, turntable / MP4 | ✅ (time bar: slider, play / pause, note; ● MP4 of the saved times or a turntable, fps and orbit; Animate under the modes) - no 🎬 Promo look, no section-sweep video | results |
| Colours: map, reverse, bands | ✅ | - |
| Contour lines on the result: on / off, number, extra values, value written on each line (new, not in the web version) | ✅ | - |
| Range: auto / manual, histogram with drag-to-set | ✅ (area-weighted distribution in the map's colours, mean, share outside; drag sets, double-click resets) | results |
| Model: surface opacity, edges, hide parts, hide air | ✅ (opacity slider, edges, a tick box per part, Hide the air for cooling studies) - no mirrored half | results |
| Deformation: animated / end state / off, scale, auto, true size ×1, outline of the undeformed part | ✅ (and the outline of the undeformed part, a note with the exaggeration; hidden when nothing deforms) | results |
| Min / max labels on the model (they can be dragged with the mouse) | ✅ | results |
| Probe: click points, pinned values list | ✅ (Probe tab: toggle, pinned values, labels) | results (+ viewtools) |
| Line plot: pick two points / min → max, hover marker, copy data | ✅ (pick two points, Min to max, hover value, copy data) | results (+ viewtools) |
| Flow tools (CFD): contour planes (several, each with field, axis, position slider, opacity, remove; contour lines on them), vortex surfaces (Q-criterion: level, colour, keep clear of walls, smoothing), volume rendering (field, density, colour map / smoke), vector arrows on a plane, particle paths | ✅ (particles animated as dots along the paths, with a speed; an automatic or typed colour range per plane, cloud, arrows and paths) - the arrows have no length scale of their own | results |
| Export: Report (HTML), CSV table, VTK (ParaView) | ✅ (report, CSV, VTK) | results |
| Mesh sensitivity, three-level check, model assumptions | ✅ (table of rows with GCI, verdict, Run / Check again, Three-level check (GCI), disabled with the reason; flow-model comparison; previous run; assumptions) | results |
| Section along x/y/z while looking at results | ✅ (Display tab: axis and position; a control action) | viewtools |

## Definition of "done" for a row

Works in the real window and through the control API, has an id (and an action if it changes anything), has a test in
`tests/` that fails without it, looks like the web version's step in a side-by-side screenshot, and the row above is updated.
