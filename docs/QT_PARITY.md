# Qt app vs the web version: feature parity

The web version (`python -m simwb.server`, `simwb/web/`) is the reference. This list was made by running it, stepping through all
five steps of a static, a thermal and an external-flow study in Expert mode and recording every control (about 140 distinct ones),
plus the toolbar and application bar. Status: ✅ done in the Qt app · 🟡 partly · ⬜ missing. **Owner** = the worktree in
`docs/PARALLEL_WORK.md`. Update the status when a row is finished and tested.


> **Last reviewed:** after the fifth critic round of the Qt interface (overall 5.4 / 7). Rows marked ✅ have tests in `tests/test_qt_*.py`;
> 🟡 rows say what is missing. The largest remaining gaps are: CAD edits and the air-volume editor (Geometry), symmetry planes, rotors and
> moving zones (Setup), continue / extend runs, cores and advanced OpenFOAM numerics (Solve), CFD result views (contours, vectors,
> streamlines), the time bar for transient results, and the colour legend as an overlay inside the 3D view.

## Application bar, view toolbar, status

| Web feature | Qt | Owner |
|---|---|---|
| Start page with a card per study type, examples, open, recent | ✅ | - |
| Project switcher (dropdown of studies), brand = back to start page | ✅ (study switcher in the application bar, New) | viewtools |
| Simple / Expert mode (hides advanced settings) | ✅ (remembered; hides advanced settings and the Mesh advanced options) | setup |
| Jobs menu (all running jobs, stop them) | ✅ (running jobs, Stop) | solve-mesh |
| Claude assistant: chat, login, new chat, suggestions | ✅ | - |
| Claude: Stop button, Claude Code button (terminal), `/login /logout /status /help`, history after reload | ⬜ (Stop button only while busy) | - |
| Close button (stops all jobs) | ✅ | solve-mesh |
| View buttons Iso, X, Y, Z, Fit (toolbar) | ✅ (in the 3D view's own bar) | viewtools |
| Edges toggle | ✅ | viewtools |
| Air box toggle (translucent) | ✅ | - |
| Section: axis, slider, value, play sweep, Hide air | 🟡 (Results > Display tab and the Mesh cut; not in the 3D view's toolbar; no play sweep) | viewtools |
| Probe, Picture (PNG with legend) buttons | 🟡 (Picture ✅; Probe is on the Results > Probe tab, not in the toolbar) | viewtools / results |
| Hover tooltip with face info, selection hint banner | 🟡 (hover highlight and tooltips; no selection-hint banner) | viewtools |
| Colour legend (round ticks, log) | ✅ | - |
| Orientation triad | ✅ | - |
| Status bar: study, mesh, solve state, job progress bar + Cancel | 🟡 (study, type, mesh, solution, path in the tooltip; progress in Jobs; no Cancel here) | solve-mesh |
| Toasts, dialogs | 🟡 | - |
| Faces picked in the 3D view (click, hover highlight, multi-select) | ✅ (click, Ctrl-add, hover highlight; picks through the air box) | viewtools |

## 1 Geometry

| Web feature | Qt | Owner |
|---|---|---|
| Import STEP / STL / OBJ; Replace… | ✅ (import, Replace) | geometry |
| Units of the file (×1, ×0.001, inch→mm, ×100 ...) | ✅ | geometry |
| **Align a face to a plane** (pick a face, choose the plane) | ✅ (pick a face, choose the plane) | geometry (+ viewtools picking) |
| Turn the model: X/Y/Z ±90°, free angle about an axis, Reset, Apply orientation, Cancel | 🟡 (±90° buttons, free angle, Apply, Reset; no drag-to-turn rings) | geometry |
| Bodies list with show/hide (eye), highlight, rename | ✅ (eye, select; hide in the view) | geometry |
| Parts list | ✅ | geometry |
| **CAD edits** (add/cut shapes, holes, fillet, chamfer, mirror, pattern, scale, move, loft): list, undo last, remove, remove all | ⬜ | geometry |
| Air volume (flow studies): shape Box / C-shape / Cylinder, sizes, Apply, Fit to parts, Show all | ⬜ | geometry |
| Automatic domain size presets (Minimal / Compact / Standard / Large, Fit with these) | ⬜ | geometry |
| Hide rings; next-step button | ⬜ / ✅ | geometry |

## 2 Setup

| Web feature | Qt | Owner |
|---|---|---|
| About this simulation (what the study does) | ✅ | setup |
| Parts & materials: rename, material list per body, confirm default material (✓ Use ...), eye, highlight | ✅ | setup |
| **Boundary conditions**: add by type, name, faces (pick in the view / by chip), type-specific parameters, delete, highlight, colour glyphs in the view | ✅ (add by type, name, faces from the view, parameters, delete) | setup (+ viewtools) |
| Boundary-condition glyphs drawn on the model | 🟡 (face tints, labels, load and gravity arrows; no symbols for every type) | setup / viewtools |
| Loads: gravity direction | ✅ (setting, arrow in the view) | setup |
| Solver choice for the study (built-in, CalculiX ...) | ⬜ | setup |
| **Symmetry planes**: plane ⟂ x/y/z, from a face, flip, remove | ⬜ | setup |
| Rotors: detect on motor tops, thrusts, reset to automatic, edit | ⬜ | setup |
| Moving (rotating) zones: add, edit, delete | ⬜ | setup |
| Flow condition: fluid (air/water), speed, direction, turbulence model, steady/transient, flow & solver settings | ⬜ | setup |
| Readiness checklist (errors/warnings that jump to the control) | ✅ (items jump to their control) | setup |
| Auto-detect inlet & outlet (cooling studies) | ⬜ | setup |
| Settings of every study type (generic form from the study spec) | ✅ (generated from the study spec, Advanced hidden in Simple mode) | setup |

## 3 Mesh

| Web feature | Qt | Owner |
|---|---|---|
| Mesh type (tet / hex-dominant), resolution presets, generate | ✅ (tet / hex-dominant, presets, generate) | solve-mesh |
| Advanced sizes and defeaturing | ✅ | solve-mesh |
| Part sizing (per part size), delete | ✅ | solve-mesh |
| Refinement regions: box, cylinder, wake behind rotating zones, tip-vortex rings, delete | 🟡 (box and cylinder; no automatic wake or tip-vortex rings) | solve-mesh |
| Wall layers (prism / inflation): number, growth ratio | ✅ | solve-mesh |
| Statistics (cells, nodes, near-wall/bulk size, quality, non-orthogonality, skewness, hex share ...) | 🟡 (cells, nodes, sizes, quality, verdict in words; hex share and layers; no skewness table) | solve-mesh |
| Colour the mesh by quality / cell size / region | ✅ (new, not in the web version before) | - |
| Cut through the mesh | ✅ | - |
| Cell quality histogram; show cells below a threshold; zoom to worst | ✅ (histogram, verdict, show cells below a threshold, zoom to the worst) | solve-mesh |
| Mesh-sensitivity check, three-level (GCI) check; mesh verdict | ⬜ | solve-mesh / results |

## 4 Solve

| Web feature | Qt | Owner |
|---|---|---|
| Run, progress, status, log | ✅ (Run first on the step, progress, state, Run again after a finished run) | solve-mesh |
| Solver selection, software used per step, install OpenFOAM / CalculiX, re-check | 🟡 (solver and the software behind each step, folded; no install buttons) | solve-mesh |
| Cores (Auto / 1..N), initialise from another study, uniform flow | ⬜ | solve-mesh |
| Advanced (OpenFOAM) numerics: schemes, limits, reset to defaults, revert to generated, save | ⬜ | solve-mesh |
| Continue run, extend a transient run, resume, Stop and keep results, Cancel | ⬜ | solve-mesh |
| Write OpenFOAM case only; case files viewer; download case zip | 🟡 (button; no case file viewer or zip download) | solve-mesh |
| Solver output (latest lines) + full log | ✅ (also in the Output dock) | solve-mesh |
| View results → | ✅ | solve-mesh |

## 5 Results

| Web feature | Qt | Owner |
|---|---|---|
| Tabs Summary / Display / Probe / Checks | ✅ | results |
| KPI tiles with verdicts, parts bars (stress vs yield, temperatures, allowed temperature), heat balance, force/moment bars | ✅ (tiles, parts bars against yield, cautions box; temperatures and force bars partly) | results |
| Modal spectrum plot, flutter plots, sweep plot, residual history | ⬜ | results |
| Field choice, time bar for transient results, animate, turntable / MP4 | 🟡 (field, colours, animated deformation; no time bar for transient results, no MP4) | results |
| Colours: map, reverse, bands | ✅ | - |
| Range: auto / manual, histogram with drag-to-set | 🟡 (auto / manual boxes; no histogram with drag-to-set) | results |
| Model: surface opacity, edges, hide parts, hide air | 🟡 (edges) | results |
| Deformation: animated / end state / off, scale, auto, true size ×1, outline of the undeformed part | 🟡 (animated / end state / off, scale, auto, true size; no outline of the undeformed part) | results |
| Min / max labels on the model | ✅ | results |
| Probe: click points, pinned values list | ✅ (Probe tab: toggle, pinned values, labels) | results (+ viewtools) |
| Line plot: pick two points / min → max, hover marker, copy data | ✅ (pick two points, Min to max, hover value, copy data) | results (+ viewtools) |
| Contour planes (CFD); iso surfaces; vectors; streamlines; particles; volume rendering | ⬜ | results |
| Export: Report (HTML), CSV table, VTK (ParaView) | ✅ (report, CSV, VTK) | results |
| Mesh sensitivity, three-level check, model assumptions | 🟡 (assumptions and the Checks tab; mesh check run) | results |
| Section along x/y/z while looking at results | ✅ (Display tab: axis and position; a control action) | viewtools |

## Definition of "done" for a row

Works in the real window and through the control API, has an id (and an action if it changes anything), has a test in
`tests/` that fails without it, looks like the web version's step in a side-by-side screenshot, and the row above is updated.
