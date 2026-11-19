# Qt app vs the web version: feature parity

The web version (`python -m simwb.server`, `simwb/web/`) is the reference. This list was made by running it, stepping through all
five steps of a static, a thermal and an external-flow study in Expert mode and recording every control (about 140 distinct ones),
plus the toolbar and application bar. Status: ✅ done in the Qt app · 🟡 partly · ⬜ missing. **Owner** = the worktree in
`docs/PARALLEL_WORK.md`. Update the status when a row is finished and tested.

## Application bar, view toolbar, status

| Web feature | Qt | Owner |
|---|---|---|
| Start page with a card per study type, examples, open, recent | ✅ | - |
| Project switcher (dropdown of studies), brand = back to start page | 🟡 (File menu, recent list) | viewtools |
| Simple / Expert mode (hides advanced settings) | ⬜ | setup |
| Jobs menu (all running jobs, stop them) | ⬜ | solve-mesh |
| Claude assistant: chat, login, new chat, suggestions | ✅ | - |
| Claude: Stop button, Claude Code button (terminal), `/login /logout /status /help`, history after reload | ⬜ (Stop button only while busy) | - |
| Close button (stops all jobs) | ⬜ | solve-mesh |
| View buttons Iso, X, Y, Z, Fit (toolbar) | 🟡 (View menu only) | viewtools |
| Edges toggle | 🟡 (results panel only) | viewtools |
| Air box toggle (translucent) | ✅ | - |
| Section: axis, slider, value, play sweep, Hide air | 🟡 (mesh panel cut only) | viewtools |
| Probe, Picture (PNG with legend) buttons | ⬜ | viewtools / results |
| Hover tooltip with face info, selection hint banner | ⬜ | viewtools |
| Colour legend (round ticks, log) | ✅ | - |
| Orientation triad | ✅ | - |
| Status bar: study, mesh, solve state, job progress bar + Cancel | 🟡 (path, saved, progress; no Cancel) | solve-mesh |
| Toasts, dialogs | 🟡 | - |
| Faces picked in the 3D view (click, hover highlight, multi-select) | ⬜ (contract declared in `Viewport`) | viewtools |

## 1 Geometry

| Web feature | Qt | Owner |
|---|---|---|
| Import STEP / STL / OBJ; Replace… | 🟡 (import button; no replace) | geometry |
| Units of the file (×1, ×0.001, inch→mm, ×100 ...) | ⬜ | geometry |
| **Align a face to a plane** (pick a face, choose the plane) | ⬜ | geometry (+ viewtools picking) |
| Turn the model: X/Y/Z ±90°, free angle about an axis, Reset, Apply orientation, Cancel | ⬜ | geometry |
| Bodies list with show/hide (eye), highlight, rename | 🟡 (list only) | geometry |
| Parts list | 🟡 | geometry |
| **CAD edits** (add/cut shapes, holes, fillet, chamfer, mirror, pattern, scale, move, loft): list, undo last, remove, remove all | ⬜ | geometry |
| Air volume (flow studies): shape Box / C-shape / Cylinder, sizes, Apply, Fit to parts, Show all | ⬜ | geometry |
| Automatic domain size presets (Minimal / Compact / Standard / Large, Fit with these) | ⬜ | geometry |
| Hide rings; next-step button | ⬜ / ✅ | geometry |

## 2 Setup

| Web feature | Qt | Owner |
|---|---|---|
| About this simulation (what the study does) | ⬜ | setup |
| Parts & materials: rename, material list per body, confirm default material (✓ Use ...), eye, highlight | 🟡 (material combo only) | setup |
| **Boundary conditions**: add by type, name, faces (pick in the view / by chip), type-specific parameters, delete, highlight, colour glyphs in the view | ⬜ | setup (+ viewtools) |
| Boundary-condition glyphs drawn on the model | ⬜ | setup / viewtools |
| Loads: gravity direction | ⬜ | setup |
| Solver choice for the study (built-in, CalculiX ...) | ⬜ | setup |
| **Symmetry planes**: plane ⟂ x/y/z, from a face, flip, remove | ⬜ | setup |
| Rotors: detect on motor tops, thrusts, reset to automatic, edit | ⬜ | setup |
| Moving (rotating) zones: add, edit, delete | ⬜ | setup |
| Flow condition: fluid (air/water), speed, direction, turbulence model, steady/transient, flow & solver settings | ⬜ | setup |
| Readiness checklist (errors/warnings that jump to the control) | ⬜ | setup |
| Auto-detect inlet & outlet (cooling studies) | ⬜ | setup |
| Settings of every study type (generic form from the study spec) | ⬜ | setup |

## 3 Mesh

| Web feature | Qt | Owner |
|---|---|---|
| Mesh type (tet / hex-dominant), resolution presets, generate | 🟡 (presets, generate) | solve-mesh |
| Advanced sizes and defeaturing | ⬜ | solve-mesh |
| Part sizing (per part size), delete | ⬜ | solve-mesh |
| Refinement regions: box, cylinder, wake behind rotating zones, tip-vortex rings, delete | ⬜ | solve-mesh |
| Wall layers (prism / inflation): number, growth ratio | ⬜ | solve-mesh |
| Statistics (cells, nodes, near-wall/bulk size, quality, non-orthogonality, skewness, hex share ...) | 🟡 (cells, nodes, quality) | solve-mesh |
| Colour the mesh by quality / cell size / region | ✅ (new, not in the web version before) | - |
| Cut through the mesh | ✅ | - |
| Cell quality histogram; show cells below a threshold; zoom to worst | ⬜ | solve-mesh |
| Mesh-sensitivity check, three-level (GCI) check; mesh verdict | ⬜ | solve-mesh / results |

## 4 Solve

| Web feature | Qt | Owner |
|---|---|---|
| Run, progress, status, log | 🟡 | solve-mesh |
| Solver selection, software used per step, install OpenFOAM / CalculiX, re-check | ⬜ | solve-mesh |
| Cores (Auto / 1..N), initialise from another study, uniform flow | ⬜ | solve-mesh |
| Advanced (OpenFOAM) numerics: schemes, limits, reset to defaults, revert to generated, save | ⬜ | solve-mesh |
| Continue run, extend a transient run, resume, Stop and keep results, Cancel | ⬜ | solve-mesh |
| Write OpenFOAM case only; case files viewer; download case zip | ⬜ | solve-mesh |
| Solver output (latest lines) + full log | 🟡 | solve-mesh |
| View results → | ⬜ | solve-mesh |

## 5 Results

| Web feature | Qt | Owner |
|---|---|---|
| Tabs Summary / Display / Probe / Checks | ⬜ (one panel) | results |
| KPI tiles with verdicts, parts bars (stress vs yield, temperatures, allowed temperature), heat balance, force/moment bars | 🟡 (tiles) | results |
| Modal spectrum plot, flutter plots, sweep plot, residual history | ⬜ | results |
| Field choice, time bar for transient results, animate, turntable / MP4 | 🟡 (field only) | results |
| Colours: map, reverse, bands | ✅ | - |
| Range: auto / manual, histogram with drag-to-set | ⬜ | results |
| Model: surface opacity, edges, hide parts, hide air | 🟡 (edges) | results |
| Deformation: animated / end state / off, scale, auto, true size ×1, outline of the undeformed part | 🟡 (on/off) | results |
| Min / max labels on the model | ⬜ | results |
| Probe: click points, pinned values list | ⬜ | results (+ viewtools) |
| Line plot: pick two points / min → max, hover marker, copy data | ⬜ | results (+ viewtools) |
| Contour planes (CFD); iso surfaces; vectors; streamlines; particles; volume rendering | ⬜ | results |
| Export: Report (HTML), CSV table, VTK (ParaView) | ⬜ | results |
| Mesh sensitivity, three-level check, model assumptions | ⬜ | results |
| Section along x/y/z while looking at results | ⬜ | viewtools |

## Definition of "done" for a row

Works in the real window and through the control API, has an id (and an action if it changes anything), has a test in
`tests/` that fails without it, looks like the web version's step in a side-by-side screenshot, and the row above is updated.
