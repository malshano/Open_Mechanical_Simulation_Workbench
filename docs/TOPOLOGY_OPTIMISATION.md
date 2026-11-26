# Topology optimisation module: research and design

Status: **iteration 1 built** (the engine, `simwb/topopt/`); the study type and the window follow (written 2026-11-25). Build in its own worktree and branch (`feature/topology-optimisation`), implement first, tests at the end, as for the other features.

## What topology optimisation is for here

The user fixes a design space (the part's volume), the supports and the loads, and a target (for example "keep 30 % of the volume"). The optimiser removes material where it carries little load and keeps it where it carries a lot, and gives back a shape: the stiffest structure for the mass you allow (the same shape is also the lightest one for that stiffness). The result is a starting point for a design, not a finished part. It must be seen in the 3D view, checked with an ordinary static study, and exported to CAD.

## What exists (checked 2026-11-25)

- **PrePoMax has no topology optimisation.** Its manuals list static, frequency, buckling and heat-transfer steps only. A forum thread ([PrePoMax forum](https://prepomax.discourse.group/t/topology-optimization-simp-for-calculix-view-results-in-prepomax/3025)) is a researcher planning to add SIMP to CalculiX some day. So "like PrePoMax" can only mean: a clean pre/post-processor around CalculiX, with an optimisation loop driving it from outside.
- **beso** ([github.com/calculix/beso](https://redirect.github.com/calculix/beso), LGPLv3): Python, bi-directional evolutionary structural optimisation. It runs CalculiX (2.17 or newer) once per iteration on an ordinary `.inp` deck, reads the element results, and removes or adds elements. The proven reference for a CalculiX loop. Hard to use without Python skills, which is the gap a window can fill.
- **ToOptiX** ([DMST1990/ToOptiX](https://github.com/DMST1990/ToOptiX)): a second CalculiX-based tool, also with a FreeCAD wrapper.
- **ccx-shape** ([fandaL/ccx-shape](https://www.github.com/fandaL/ccx-shape)): shape (not topology) optimisation from CalculiX sensitivities. Useful later for smoothing the result.
- **PyTopo3D** ([arXiv 2504.05604](https://arxiv.org/html/2504.05604v1)): a Python 3D SIMP code with an optimality-criteria update, sensitivity filter through a KD-tree, STL in and out. The proof that SIMP with scipy is enough for models of a few hundred thousand elements.
- **55-line FEniCS/PETSc code** ([arXiv 2012.08208](https://arxiv.org/pdf/2012.08208)): the compact reference for SIMP on unstructured meshes with a fast linear solver.

CalculiX itself is already in the workbench (`simwb/solvers/calculix`: `run_ccx`, the deck builder, the `.frd` reader) and so are Gmsh tetrahedral meshes, a built-in quadratic-tetrahedron static solver (`simwb/solvers/static_fem.py`) and VTK (iso-surfaces).

## Recommendation

One optimiser, two interchangeable analysis backends, so the answer can be checked against the proven tool:

1. **Fast backend (built in): SIMP on the project's own tetrahedral mesh.** Element density x in [x_min, 1], stiffness E = x^p E0 (p = 3), sensitivity from the element strain energy, density filter by neighbours within a radius, update by the optimality criteria (OC) method with a bisection on the volume constraint. Linear algebra: scipy sparse (Pardiso/CHOLMOD when present, as `static_fem` already does). Open-source parts: numpy, scipy, VTK; NLopt's MMA can be an optional alternative update.
2. **Reference backend (CalculiX, beso style):** the same loop with `run_ccx` and the existing deck builder, strain energy read from CalculiX (`*EL PRINT, ENER`), elements removed or added by the BESO rule. Slower, but it handles every load and support the workbench can already send to CalculiX and is the same method as the proven open-source tool.

Both write the same result: a per-element density, shown as a normal result field ("Density") with a threshold slider (the 3D view already has a clip and a colour range), plus the compliance and volume history as a chart in the Summary tab.

## What the user sees

- New study type **Topology optimisation** (structural group of the start screen), built on the **static structural** setup: import, parts and materials, supports and loads as today.
- Setup adds: **Design space** (which parts may change; the faces that carry supports and loads are always kept), **Target** (volume fraction, default 30 %), **Minimum member size** (the filter radius, default 3 average element sizes), **Symmetry** (planes, reusing the symmetry-plane tool), **Keep-out regions** (reuse refinement-region boxes and cylinders).
- Mesh: the ordinary tetrahedral mesh, with a note that a finer mesh gives finer members (the cell estimate and the advisor already exist).
- Solve: progress per iteration (compliance, volume, change), Stop and keep the best so far.
- Results: density field, threshold, compliance and volume history, a **smoothed surface** (marching tetrahedra at the threshold, a few Taubin smoothing passes) that can be exported as STL, and **Check this shape**: make a new static study from the smoothed shape with the same loads and report the stress and displacement, so the user sees the optimised part is not only stiff but also strong enough.

## Phases (each ends with something that can be used)

1. **SIMP on a tetrahedral mesh, built in**: design space = whole body, one load case, volume constraint, OC update, density filter. Benchmarks with published numbers (a cantilever and an MBB-type beam, compared with the 88-line code results and with beso on the same mesh).
2. **Window**: study type, Setup block, Solve progress, Results density with threshold and history chart, assistant tools (`topopt_setup`, `topopt_run`, `topopt_result`), control API ids for every control.
3. **CalculiX/BESO backend** and the cross-check of the two on the benchmarks.
4. **Shape out**: smoothed STL, "Check this shape" study.
5. Later: several load cases (weighted compliance), stress constraint, minimum and maximum member size, symmetry and pattern repetition, mass-vs-stiffness plots, frequency-based objective (the modal solver exists).

## Risks and open questions

- **Checkerboard and mesh dependence**: a density filter is required; the filter radius is in metres, not elements, so a refined mesh gives the same members, only smoother.
- **Quadratic tetrahedra** cost more than linear ones in the loop; start with the linear (4-node) stiffness of the same mesh for the loop and keep the quadratic solve for the final check.
- **Compliance only** measures stiffness. A stiff shape can have a high stress at a sharp re-entrant corner: hence "Check this shape" in phase 4.
- **Where the loop runs**: in-process thread like the other jobs (progress and Stop through the existing jobs panel); a very large mesh must be refused with the memory estimate first (the cell budget code already knows the free memory).
- **Licence**: beso is LGPLv3 (compatible with GPLv3). Prefer calling it as a tool or re-implementing the published method; do not copy its code without keeping its licence notice.

## Decisions taken for the first version (2026-11-25)

- **Objective and constraint.** Minimise compliance (stiffness) with the **mass** held at a target (mass, not volume, so several materials weigh right). Not strength, not vibration: a modal check beside the original part is planned for the "Check this shape" step.
- **Loads and conditions supported.** Fixed support, frictionless support / symmetry plane, prescribed displacement, force, pressure. Anything else is refused with the name of the condition. **Own weight is left out** (it changes with the shape) and the study says so.
- **Kept solid.** The tetrahedra that own a supported or loaded face (one element layer; `keep_layers` can thicken it). If those already weigh more than the target, the run is refused with the percentage.
- **Filter and member size.** Cone density filter, radius in metres. Default: 4 % of the part's largest size, never below two element sizes; raised to 1.5 element sizes if the user asks for less, and said. The window shows it in mm.
- **Black and white.** Smooth Heaviside projection with beta 1, 2, 4, 8 (a stage ends when the shape settles, the compliance stalls, or after 30 iterations). The mass target is met exactly on the projected density; the report also gives the mass of the sharp shape (density at least 0.5) and the share of grey.
- **Convergence** is the change of the filtered density (the shape itself), not of the design variables, which keep flipping without changing the shape. The step limit shrinks as the shape sharpens.
- **Accuracy evidence (iteration 1).** Gradient against finite differences: error below 1e-3. Solid stiffness: within 4 % of the quadratic static solver and 7 % of beam theory. Mesh dependence: stiffness kept 0.459 / 0.456 on two meshes of a 2D-like cantilever, 0.325 / 0.322 at a larger member size. Mass target met to 1e-9. Size and memory are checked before a run starts.
- **Known limits.** Linear tetrahedra are stiffer than quadratic ones. One load case gives a shape that is good only for that load (the window warns). **Symmetry is not enforced:** a symmetric 3D problem with only a few cells across the width can settle on an asymmetric shape (measured: outer layers uncorrelated at four cells across; a 2D-like slab extrudes uniformly, layer correlation at least 0.99). An optional mirror constraint is planned. A symmetry plane given as a frictionless support models the half correctly for the stiffness, but the filter does not see across it.
- **BESO cross-check** compares the final compliance of both shapes in one solver (the quadratic static solver), not the paths of the two methods.

## Validation (2026-11-26)

- **Published benchmark.** The 88-line paper prints no compliance values, so its algorithm (appendix code) was ported line by line (`benchmarks.top88_reference`) and compared on the paper's half MBB beam, 60 x 20, keep 50 %, penalty 3, density filter radius 3 cells (our smallest feature is 3 cells): reference compliance 249.2, this optimiser 257.0 (+3 %), density correlation 0.992 (`tests/test_topopt_engine.py::test_the_published_mbb_benchmark_is_reproduced_by_the_tetrahedron_optimiser`). With the paper's own settings (sensitivity filter, radius 1.5) the port gives 203.2 after 94 iterations; that setting is below our minimum feature size and is not compared.
- **Second solver.** The shape check agrees with CalculiX 2.23 on the same sub-mesh: largest movement 0.087651 vs 0.087744 mm, von Mises 47.74 vs 47.74 MPa.
- **Floating pieces** are emptied at the end of a run; faces can be marked **Keep solid**; a feature size above the wall thickness is reported with a one-click mesh refinement.
- **Known and stated:** a fully symmetric 3D model is not forced symmetric; one load case; the shape at a threshold depends on thin members near it (the optimiser's own stiffness figure repeats to 0.1 %, the quadratic check at 50 % varied between 0.60 and 0.75 over three runs).
