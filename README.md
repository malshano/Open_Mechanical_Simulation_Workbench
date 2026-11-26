# Topology optimisation: literature benchmark data

This branch holds **only the benchmark data** of the topology optimisation module: the numbers, logs, figures and the report.
The code that produced them lives in the repository, not here: the reference codes and helpers are in `simwb/topopt/literature.py`
(`python -m simwb.topopt.literature --full` reruns the 2D table), with its tests in `tests/test_topopt_literature.py`.
Run on 2026-11-26 with the code of commit `ccaeda8` (branch `topopt/literature-benchmark` before it became data only); the module
itself was at `6700112` (branch `feature/topology-optimisation`).

Published sources: Andreassen, Clausen, Schevenels, Lazarov, Sigmund, *Efficient topology optimization in MATLAB using 88 lines of code*,
Struct Multidisc Optim 43 (2011), Figs. 3 and 8 (half MBB beam, volume fraction 0.5, penalty 3). Ferrari and Sigmund, *A new generation 99 line
Matlab code for compliance topology optimization and its extension to 3D*, arXiv 2005.05436, Fig. 8 and appendix C (3D cantilever 48 x 24 x 24,
volume fraction 0.12, filter radius sqrt(3) cells).

## What is in here

| Folder | Content |
|---|---|
| `results/2d/` | Half MBB beam: the reference code against the nine printed values, the module (tetrahedra slab) against the paper and the reference |
| `results/3d/` | 3D cantilever: the reference code against the two printed values, the module against the hexahedron reference on coarser meshes, solid stiffness |
| `results/superseded/` | Logs of a first 3D attempt at a radius the module could not use; kept for completeness, not used in the report |
| `figures/` | The six figures of the report as PNG |
| `report/TOPOPT_BENCHMARK_PROOF.html` | The self-contained report (figures embedded) |
| `SHA256SUMS` | Checksum of every file here |

## 1. The reference code against the nine printed 2D values (`results/2d/reference_vs_paper.json`)

Radius is 0.04 x nx cells for the sensitivity and density filters and 0.03 x nx for Heaviside, as in the paper. The paper's text gives 16 cells
for 300 x 100, but 12 reproduces the printed values, so 12 is used. The reference port is `literature.top88`.

| Mesh | Filter | Radius (cells) | Published | Reference code | Difference |
|---|---|---|---|---|---|
| 60 x 20 | sensitivity | 2.4 | 216.81 | 216.81 | +0.002 % |
| 60 x 20 | density | 2.4 | 233.71 | 233.71 | +0.002 % |
| 60 x 20 | Heaviside | 1.8 | 189.14 | 189.14 | +0.000 % |
| 150 x 50 | sensitivity | 6 | 219.52 | 219.52 | -0.000 % |
| 150 x 50 | density | 6 | 235.73 | 235.73 | +0.002 % |
| 150 x 50 | Heaviside | 4.5 | 191.49 | 191.49 | -0.001 % |
| 300 x 100 | sensitivity | 12 | 222.29 | 222.29 | -0.002 % |
| 300 x 100 | density | 12 | 238.31 | 238.31 | +0.002 % |
| 300 x 100 | Heaviside | 9 | 193.24 | 193.24 | +0.000 % |

## 2. The module against the paper and the reference (`results/2d/module_tetrahedra.json`)

The beam is a slab of linear tetrahedra run through the module's `Optimiser`. The module raises any radius below 3 element sizes, so on the
60 x 20 slab a request for 3 cells became 3.37 cells; the reference code was rerun at that radius (`reference_60x20_at_module_radius.json`).
Rows marked "guard lifted" ran with `literature.guard_lifted()`, which sets the module's `simp.MIN_CELLS` to 1 for the run. Negative differences mean the
module is stiffer, as expected of linear tetrahedra.

| Case | Mesh | Radius used | Compared with | That value | Module | Difference | Iterations |
|---|---|---|---|---|---|---|---|
| density, radius 3 cells (default limit) | 60 x 20 | 3.37 | reference code at the same radius | 262.72 | 256.95 | -2.196 % | 48 |
| density, radius 150x50 6 cells | 150 x 50 | 6.00 | paper | 235.73 | 235.38 | -0.148 % | 45 |
| density, paper radius 2.4 (guard lifted) | 60 x 20 | 2.40 | paper | 233.71 | 229.17 | -1.942 % | 45 |
| projection to beta 512, 150x50 radius 4.5 | 150 x 50 | 4.50 | paper | 191.49 | 186.11 | -2.812 % | 79 |
| projection to beta 8 (module default), 150x50 radius 4.5 | 150 x 50 | 4.50 | paper | 191.49 | 197.76 | +3.275 % | 62 |
| projection to beta 512, 60x20 radius 1.8 (guard lifted) | 60 x 20 | 1.80 | paper | 189.14 | 181.31 | -4.142 % | 90 |

## 3. The 3D cantilever against the printed values (`results/3d/published_*`)

Hexahedron reference code (`literature.top3d`, a port of top3D125) with Heaviside projection. The paper does not state its penalty continuation, beta or
iteration count, so these runs used penalty 3, beta 2, eta 0.5 and 100 iterations. The module cannot run this mesh (92,000 unknowns, limit 60,000 without PARDISO).

| Filter edge | Published | Reference code | Difference | Iterations | Seconds |
|---|---|---|---|---|---|
| Neumann | 3993.7 | 4077.3 | +2.094 % | 100 | 4335 |
| Dirichlet | 4074.3 | 4088.2 | +0.342 % | 100 | 3679 |

## 4. The module against the hexahedron reference on meshes the module accepts (`results/3d/module_vs_hexahedra/`)

Density filter, volume fraction 0.12, penalty 3. The radius is the same in length units for both codes (at least 3 element sizes of the
tetrahedron mesh and within the module's 40 million filter-link cap). The sign of the difference changes between the two meshes, so the
module's 3D stiffness is not claimed to better than about 10 % on meshes it accepts.

| Mesh | Radius | Hexahedra | Tetrahedra (module) | Difference |
|---|---|---|---|---|
| 24 x 12 x 12 | 6.8 | 16841 | 14901 | -11.516 % |
| 32 x 16 x 16 | 5.1 | 8483 | 9002 | +6.110 % |

Stiffness of the solid beam (no optimisation), tetrahedra against hexahedra (`results/3d/solid_stiffness_tetrahedra_vs_hexahedra.json`):

| Cells along x | Tetrahedra | Hexahedra | Ratio |
|---|---|---|---|
| 16 | 368.5 | 396.6 | 0.929 |
| 24 | 394.7 | 413.6 | 0.954 |
| 32 | 408.0 | 423.1 | 0.964 |

## Limits found in the module

At most 60,000 unknowns without `pypardiso` (the 300 x 100 slab has 182,000); at most 40 million filter links (a radius of 8 on 32 x 16 x 16 needs
135 million); a radius floor of 3 element sizes, so the published 3D radius of sqrt(3) cells cannot be reproduced on any mesh.

## Notes on the files

* JSON files keep the full iteration history of each run where one exists (`history`); the 3D published runs list compliance, volume, change and time per iteration.
* Files were copied unchanged from the working folder of the run; only the names were made descriptive.
* The scripts that drove the individual runs are not stored here (data only). Their parameters are the ones given in the tables above and in each JSON file (`settings`).
