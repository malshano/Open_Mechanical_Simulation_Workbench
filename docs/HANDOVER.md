# Handover, 2026-11-28

State of the work and what to do next. Read `CLAUDE.md` first (branch flow, working practice), then this file.

## Where things are

- **`staging`** is the beta line (version still `0.4.0b5`; raise it to `b6` when a beta is cut and add the notes to `docs/BETA_0.4.md`). It is on the public remote (`public`). **`main` has none of the work below**; it only receives `staging` by pull request after the owner accepts a beta.
- Everything is branched from `staging` in a worktree per feature (`C:\Github\MechanicalSimulationWorkbench-<name>`), merged into `staging` with a merge commit, then pushed. The main folder `C:\Github\MechanicalSimulationWorkbench` is on `main` and is not to be used for feature work.
- Run Python and tests with `C:\Github\MechanicalSimulationWorkbench\.venv\Scripts\python.exe` from inside a worktree (`QT_QPA_PLATFORM=offscreen`). The system Python has no PySide6 or VTK and silently skips the Qt tests. One pytest process at a time, and expect the Qt control and shell files to take 5 to 10 minutes. OpenFOAM is installed at `C:\SimWB\openfoam` (set `SIMWB_OPENFOAM_DIR` and `SIMWB_MPI_DIR=C:\SimWB\mpi` in a worktree that has none).
- Only work on GitHub issues authored by `malshano` (`gh issue view <n> --json author`).

## Issues, by state (public repo `malshano/Open_Mechanical_Simulation_Workbench`)

**Merged into `staging` and closed on GitHub:** #7, #10, #12, #14, #20, #21, #33, #34, #35, #40, #41, #42, #43, #47, #48, #49, #50, #58 to #63.

**Merged, left open on purpose:** #13 (closing the app leaves an invisible process). The mechanism is fixed (a waiting assistant call kept the process alive; the X button did not stop jobs) but the reporter's own 6 GB session was not reproduced. Close it when the owner confirms.

**In progress:** `feature/mesh-sizing-methods` (worktree `-sizing`): #40, #33, #34 and #35 are done and merged. Left: #38 (the growth-limited coarsening is wired in the plan and mesher in `simwb/sizingmethods.py` but is not in the window and is untested; anisotropic tets are not done) and #32 (own prism layers in Gmsh). Findings so far: Gmsh 4.15.2 has no 3D BoundaryLayer field and the OCC kernel has no boundary-layer extrusion, so #32 needs its own extrusion (offset the wall surface mesh along smoothed normals, clamped with `simwb/localgeom.py`, mesh the interior, write prisms plus tets) and a mixed prism/tet polyMesh writer; anisotropic tets could come from splitting those prisms. Do not put `Fixes #38` or `Fixes #32` on a commit until it is wired, tested and in the window. Merge `staging` into the branch before continuing. Owed: native-window screenshots of the sizing methods and layer-clamp controls.

**Not started:** #39 (symmetry and 2D reductions of the domain), #37 (polyhedral conversion), #36 (octree with immersed boundary), #31 (solution-adaptive refinement), #46 (click a cell-budget band: done as part of #47, check it and close), #52 (commit sign-off, DCO), #51 (update the manuals, do it last), #11 (re-run the Qt tests and add a free-memory check before the layer and mesh tests).

**Needs real solver runs (not touched):** #15 to #19, #22 to #30, #44, #45, #53 to #57. The machine had 9 to 12 GB free in the last session, so one small OpenFOAM run at a time is possible (check the process list first).

## Process used, and how it was relaxed

- Each feature was built by an implementer agent in its own worktree, then judged by a separate reviewing agent (1 to 7 for user experience and for accuracy, pass at 6.5 on both). The first ten issues needed up to three rounds. On 2026-11-28 the owner relaxed this: build features properly, one light review at most, tighten the proof later in a combined test phase. Issues #50 and later were merged without a final pass review.
- Lessons: agents re-send the same report many times when they are idle with background work (stop them with TaskStop once their result is in); usage limits cut agents off mid-task (resume them with a message after the reset); long Qt test runs stall under load and pass when the machine is quiet; in Git Bash write Python files with a file tool, not heredocs with nested quotes, and use Windows paths inside Python.

## Known limits to prove properly (the combined test phase)

- **#63 mirror:** only tested on synthetic half-model results. Needs a real half-model solve against a whole-model solve (forces and contours), the sliding-zone video on a real result, and the saved picture pixels.
- **#20 and #21 memory planner:** estimate within about plus or minus 50 % (median -12 % on 27 real meshes at the quick preset, no layers); not measured above 1.4 million cells, for the hex mesher or for the run's memory. Its sizing code is a copy of the worker's and can drift (a test compares them).
- **#50 edge refinement:** estimate validated only at the quick preset, smart sizing on, no layers (0.84 to 1.19 of real); small caps run 1.1 to 1.7 times larger; with Smart sizing off the numbers are for the edge zone only. Turning edge refinement on changes meshes made earlier with it (release note in `docs/BETA_0.4.md`). One mesh advisor test was loosened by one failed check because the 4x edge refinement is now really applied.
- **#42 size preview:** leaves out curvature sizing, narrow-gap boxes and the global minimum, so real cells near the body are finer than shown. **#43 wall layers:** tested on a synthetic result only; per OpenFOAM patch, hex-dominant gives y+ only.
- **#7 and #10:** the hex-dominant turned refinement box was checked as written files only (no real snappyHexMesh run). Zone axes turn in quarter turns only (the solver takes six axis directions).
- **#14:** one laminar flat plate, tetrahedral mesher, two presets. The 5-layer setup is 17 to 22 % below Blasius in Cf, so those runs show steadiness, not correctness. The viscous-force stop condition is not measured on real unsteady bodies.
- **#12 and #13:** no real OpenFOAM or reconstructPar in the tests (fake programs); the adopt check compares only what the case record stores.
- **#33 layer clamp:** not run through snappyHexMesh, so it is unmeasured whether it keeps more of the wall than without it. **#34/#35 sizing methods:** estimates are rough upper bounds on narrow gaps (real/estimate 0.21 to 0.74 on a few tiny geometries); curvature and proximity sizing refine whole faces. **#40:** real/estimate 0.52 to 1.47 (median 1.1) over 12 non-flow meshes; the conjugate-heat-transfer enclosure study is not measured. Gmsh's threaded 3D mesher sometimes leaves a non-manifold face (1 of 4 runs of the narrow-gap geometry); a retry works.
- Small review follow-ups not done: a refused cell size leaves the typed number in the field; region outer arrow tips still take the resize handle; a lost mouse release can leave a stale hint; a resume with a changed save interval can fall to one save when the remaining step count is prime; the mesh legend swatch label overlaps the range text; the dialog for "use this setup" says "found no difference" even when a key could not be compared.
- Not yet run as a whole: the full test suite (about 64 files), and `test_qt_control` after the last merges.

## Open decisions for the owner

- Promote `staging` to `main` (pull request), and bump the version to `0.4.0b6`.
- Delete the old remote branches `release/0.4.0-beta.1`, `.3`, `.5` (the content is in `main`/`staging`; `.3` holds two release-note commits that exist nowhere else).
- Whether a contour plane across the symmetry normal should show as one plane instead of two in the mirrored view (cheap either way).
- Whether the memory budget should cover the run (4,200 bytes a cell, as now) or only the mesh job, and whether percent mode should be re-evaluated at solve time.
- Open one follow-up issue for a real snappyHexMesh run of the turned hex refinement box.

## Branches and worktrees

Merged into `staging` and safe to remove (worktrees were removed; the branches remain): `fix/backend-robustness`, `feature/region-zone-handles`, `feature/evidence-stopping-rule`, `feature/edge-layer-controls`, `feature/ram-planner`, `feature/mirror-symmetry-results`, `feature/mesh-views`.
Still in use: `feature/mesh-sizing-methods` (worktree `-sizing`); `multiphysics-flutter` (worktree `-multiphysics`, uncommitted work, owner's); `feature/topology-optimisation` (worktree `-topopt`, uncommitted edits, content already in `staging`); `topopt/literature-benchmark` (data-only orphan branch holding the benchmark data, worktree `-topopt-literature`); `qt/zones-preview` (merged into `staging` through the handles branch). The old private repo (`origin`) is archived; never push to it.

## Where the rest is written down

- Per-feature detail: the commit messages, `docs/USER_MANUAL.md`, `docs/TECHNICAL_MANUAL.md`, `docs/QT_PARITY.md`, `docs/MESHING_GUIDE.md`, `docs/BETA_0.4.md`.
- The progress page for the owner (plain words, screenshots): the artifact "Issue Fixes Before and After".
- The assistant's own notes on the owner's rules live in the memory folder `C:\Users\malsh\.claude\projects\C--Github-MechanicalSimulationWorkbench\memory\` (`staging-branch-flow.md`, `issue-fix-grading-protocol.md`, `issues-by-owner-only.md`, `public-repo-only.md`, `worktree-per-feature.md`).
