# Meshing guide: what a good mesh of a body in a flow needs

This is the background of the **mesh advisor** and **smart sizing** (Mesh step, external aerodynamics): what published gridding guidelines ask for, what we
measured on the example wing, and the limits we chose. The numbers marked *ours* are practical targets for a RANS study on a workstation; the research
targets in the sources are stricter and cost far more cells.

## 1. What was wrong with the old sizing (measured)

On `examples/airplane_wing.step` (chord 314 mm along the flow, span 800 mm, 10 mm thick, thinnest part 6.3 mm), preset *fine*:

| | Old sizing |
|---|---|
| Cells round the body (near field) | 31 mm = 10 % of the chord. The sizing started from `ref_length`, the **largest** side of the body (the span), and a fixed box of 1.5 x that size. |
| Cells in the wake | up to 24 % of the chord (47 % at *coarse*): nothing refined the wake, so it sat in cells as big as the far field. |
| Growth of the cell size | the refinement boxes went from their size to the far-field size over 8 of their own cells: abrupt steps (the ragged look of the cut). |
| First layer y+ | asked for 30, measured **15.8** on a wing run: y+ is measured at the cell **centre** but the layer was sized so that 30 was reached at its outer face. |
| Layers | 5 layers on the thin wing kept **70 %** of the wall (see 4). |

## 2. What a good mesh needs, and the limits we use

| Need | Published guidance | Our check (Mesh advisor) |
|---|---|---|
| Wall cells | Leading and trailing edge spacing about 0.1 % of the chord for the medium DPW grids [1][2] | **Wall cells at most 1.5 % of the length along the flow** (3 % warns). Research grids use 15 x finer and millions of cells. |
| Thin parts | resolve thickness with several cells | **at least 4 cells across the thinnest part** |
| Growth | below 1.25 in the viscous layers [2]; 1.1 to 1.3 in general [3] | cell size grows by 1.2 to 1.3 from the walls (1.2 from *medium*); **at most 1.6 between neighbours for 99 % of the cells** |
| Wake and near field | local refinement of walls, wakes and features beats global refinement [3] | **near field (0.3 body lengths round the body) at most 5 % of the length; wake (0.2 to 2 lengths behind it) at most 8 %** |
| Wall treatment | first cell y+ of about 1 (resolved) for the DPW grids [1]; wall functions need 30 or more; 5 to 30 is the buffer layer | **y+ of the first cell centre at most 5 or at least 30**; recommended 40 for wall functions |
| Layers | at least 2 layers of constant spacing normal to the wall [1]; growth below 1.25 [2]; layers collapse at trailing edges and tips in snappyHexMesh [4] | **at least 95 % of the wall with layers**; as many layers as the thin part leaves room for (section 4) |
| Domain | about 100 chords for the DPW compressible cases [1] | at least 8 body lengths upstream and to the sides and 15 downstream (low speed); a domain over 60 lengths is only noted |
| Mesh quality | | non-orthogonality at most 65° (above 75° bad), skewness at most 3 (above 4 bad), no cell below 0.1 shape quality |
| Cell count | about 3 x more cells for each finer grid level [1] | the cells are capped to half of the free memory (4,200 bytes a cell, measured: 3.4 GB for 845,000 cells) |

## 3. What the tools do

**Smart sizing** (`mesh.smart`, off by default) sizes an external-flow mesh from the shape and the flow (`simwb/meshadvisor.py`):

1. The length of the body **along the flow** (the chord of a wing), its width and its thinnest part.
2. Wall cells of that length divided by 40 (*quick*) to 190 (*very fine*); a **near-field box** (0.3 lengths round the body, 0.5 behind it) with cells 3 x the wall cell
   and a **wake box** (from the rear of the body, 3 to 5 lengths downstream, the width of the body and 0.4 lengths each side) with cells 4 to 5 x the wall cell.
3. Both boxes grow into the far field with a real growth (size = inside + (growth - 1) x distance), not over 8 cells.
4. Sharp edges across the flow (leading and trailing edges) are refined with a field that does **not** spread over the surface (see 5).
5. The cells are capped to what half of the free memory holds; the recommendation says when a preset was coarsened to fit.

**Recommended setup** also sets the wall layers: the first layer for y+ about 40 at its centre and as many layers (at most 5) as the thinnest part leaves room for.

**Scorecard**: after meshing, the mesh is measured in zones that do not depend on how it was sized and every check of section 2 is marked ok, to look at, or failed, with advice.
The same for the assistant: `mesh_advice`, `mesh_apply_recipe`.

## 4. What we measured on the example wing (coarse preset unless noted)

| | cells | wall | near | wake | score |
|---|---|---|---|---|---|
| old sizing | 73,500 | 1.6 % | 10.4 % | 47 % | 0.55 |
| old sizing, fine | 295,000 | 0.7 % | 5.5 % | 24 % | 0.70 |
| smart, everything on | **1,541,000** | 0.4 % | 2.7 % | 12 % | 0.80 |
| smart, no edge field | 661,000 | 0.6 % | 6.1 % | 12 % | 0.75 |
| smart, boundary sizes not spread | **373,000** | 1.1 % | 7.1 % | 12 % | 0.70 |
| smart medium, boundary sizes not spread | 1,031,000 | 1.0 % | 4.9 % | 8 % | **0.85** |

- **Spreading the boundary size** (`Mesh.MeshSizeExtendFromBoundary`) turned the fine edge cells of the leading and trailing edges into 478,000 one-millimetre triangles over the
  whole wing: 64 % of all the cells within 6 mm of it. Switched off, the same mesh has a quarter of the cells.
- **Equal cost is not a miracle**: at about 300,000 cells the smart sizing trades a coarser near field for a much better wake (12 % against 24 %); at about 1 million cells everything passes.
  A thin wing 0.8 m long costs about a million tetrahedra to resolve; cells stretched along the wall (prism layers) are the cheap way to resolve the wall and are made by OpenFOAM.
- **Why layers are lost**: a layer grows from each side of a thin part, so all layers together must take less than about half its thickness. 5 layers (6.3 mm in all) kept 70 % of a wing
  whose thinnest part is 6.3 mm, 3 layers 99 %, 2 layers 100 %. The tolerant layer limits changed nothing (69.2 % against 69.0 %). A finer mesh does not help.
- **The hex-dominant mesher is not better for a thin wing**: with 12 to 25 mm octree cells on a 6 mm wing it got layers on 53 to 91 % of the wall and skewness up to 10.
- Five other bodies (a quadcopter, an 8 m aircraft, a camera mount, a duct and the wing) all mesh with smart sizing; the score rose on each. Four of the five end with no failed check (the camera mount, the duct and the wing from 1, 3 and 3; the quadcopter had none); the 8 m aircraft went from 5 failed to 2.

## 5. Limits

- The domain size is not changed by the recipe; the scorecard says when it is too small or very large (the example wing's is 62 body lengths).
- With y+ near 1 (resolved boundary layer) a thin wing would need 10 to 20 layers and about a million extra prisms; the recipe uses wall functions.
- Gmsh can extrude prism layers itself (`extrudeBoundaryLayer`); we use OpenFOAM's layer step because it is the one the run uses and it reports its result.
- The memory cap uses the free memory at the time: close other programs to get a finer mesh.

## 6. The memory planner: what the estimate rests on (#20, #21)

`simwb/meshplan.py` estimates the tetrahedral mesh before it is made. **cells = (0.45 x (the integral of regular tetrahedra over the size field) + 8 x (wall area / wall size squared)) x 0.75 with smart sizing** (6 x the wall term for solid studies), plus the
edge-refinement term. **Memory of the mesh job = 800 MB + 0.5 kB x cells** (`added_ram_mb` of `memwatch` around the gmsh worker: 22 meshes from 55,000 to 1.38 million cells fit 794 MB + 0.46 kB a cell, largest miss 39 MB);
the run is 4.2 kB a cell (the earlier measurement, not re-measured). The fixed part is what #19 asked to see apart: 0.8 GB is the same for 55,000 cells and for 300,000.
The mesher's cells are about 1.4 times the edge of a regular tetrahedron of the field size (so the integral over-counts by about 2.5) except at the walls, where it makes more and smaller cells than the field says
(one in five cells of a smart-sized wing lies within half a wall cell of the wall): that is the second term.

Measured (gmsh worker alone, quick to fine presets, `benchmarks/results/mesh_plan_2026-11-28.jsonl`; estimate made before the mesh, with the constants above):

| geometry | study | preset | smart | edge | estimate | actual | estimate / actual | mesh job MB |
|---|---|---|---|---|---|---|---|---|
| airplane_wing | external_aero | quick | no | - | 47,121 | 54,891 | 0.86 | 793 |
| airplane_wing | external_aero | coarse | no | - | 59,864 | 74,341 | 0.81 | 813 |
| airplane_wing | external_aero | medium | no | - | 102,261 | 155,545 | 0.66 | 866 |
| airplane_wing | external_aero | fine | no | - | 206,119 | 297,954 | 0.69 | 937 |
| airplane_wing | external_aero | quick | yes | - | 197,562 | 153,964 | 1.28 | 855 |
| airplane_wing | external_aero | coarse | yes | - | 385,782 | 374,125 | 1.03 | 964 |
| airplane_wing | external_aero | coarse | no | 6 | 134,864 | 178,616 | 0.76 | 874 |
| quadcopter | external_aero | quick | no | - | 128,926 | 132,542 | 0.97 | 884 |
| quadcopter | external_aero | coarse | no | - | 138,696 | 150,195 | 0.92 | 887 |
| quadcopter | external_aero | medium | no | - | 302,639 | 371,584 | 0.81 | 961 |
| quadcopter | external_aero | quick | yes | - | 191,685 | 156,494 | 1.22 | 904 |
| quadcopter | external_aero | coarse | yes | - | 272,540 | 261,669 | 1.04 | 940 |
| camera_mount | external_aero | quick | no | - | 103,115 | 123,769 | 0.83 | 842 |
| camera_mount | external_aero | coarse | no | - | 136,820 | 155,259 | 0.88 | 849 |
| camera_mount | external_aero | medium | no | - | 305,992 | 411,453 | 0.74 | 942 |
| camera_mount | external_aero | fine | no | - | 669,667 | 1,375,953 | 0.49 | 1403 |
| camera_mount | external_aero | quick | yes | - | 190,508 | 164,154 | 1.16 | 864 |
| camera_mount | external_aero | coarse | yes | - | 369,023 | 345,679 | 1.07 | 953 |
| camera_mount | external_aero | medium | yes | - | 953,690 | 996,119 | 0.96 | 1245 |
| airplane_wing | external_aero | quick | no | - | 49,672 | 57,674 | 0.86 | 801 |
| airplane_wing | external_aero | quick | no | - | 64,040 | 55,498 | 1.15 | 793 |
| quadcopter | external_aero | quick | no | 6 | 203,926 | 365,506 | 0.56 | 984 |
| heated_plate | heat_conduction | quick | no | - | 33,620 | 29,538 | 1.14 | 795 |
| heated_plate | heat_conduction | medium | no | - | 36,480 | 32,987 | 1.11 | 799 |
| topology_bracket | static_structural | quick | no | - | 15,993 | 19,479 | 0.82 | 806 |
| topology_bracket | static_structural | medium | no | - | 27,608 | 44,062 | 0.63 | 821 |
| heatsink_duct | cht_enclosure | quick | no | - | 248,736 | 221,287 | 1.12 | 895 |

27 meshes: median -0.12, mean absolute 0.19, worst -0.51 (estimate / actual - 1). The edge-refinement rows use the cap bound (half of 150,000 extra cells): the quadcopter row shows how far off that is (the real edge cells were 233,000), which is why the calibrated edge estimate (#50) is the plug.

The estimate is within about 50 % of the real count on these meshes and 19 % on average (the table has the misses; the range shown is plus or minus 50 %); finer presets of thin or small-featured bodies are the worst (curved surfaces are sized by the
mesher's curvature rule, which is only estimated from the display triangulation). The sharp-edge refinement is bounded by its cap until its calibrated estimate (#50) is joined. Not measured: meshes above 1.4 million cells,
the hex-dominant mesher, and the run's memory.


## Sources

1. 4th AIAA CFD Drag Prediction Workshop, gridding guidelines: https://aiaa-dpw.larc.nasa.gov/Workshop4/gridding_guidelines_4.html (y+ about 1 for the coarse grid, 2/3, 4/9, 8/27 for finer; at least 2 constant layers; leading and trailing edge spacing about 0.1 % of the chord; cell count about 3 x between levels; farfield about 100 chords)
2. NASA High Lift Prediction Workshop 1, gridding guidelines: https://hiliftpw.larc.nasa.gov/Workshop1/GriddingGuidelinesHiLiftPW1-11JUN09.pdf (growth below 1.25 in the viscous layer)
3. "Best Practices and Mesh Convergence on the Extruded Imperial Front Wing With CharLES Solver", AIAA 2024-4259: https://doi.org/10.2514/6.2024-4259 (y+ about 2.5 and low aspect ratio cells for a laminar separation bubble; targeted refinement of walls and wakes); and "The Complete CFD Meshing Guide": https://turbulentthoughts.com/meshing-mistakes-that-derail-cfd-simulations-and-how-to-fix-them/ (growth below 1.2 in high-gradient regions)
4. CFD Online, snappyHexMesh wing threads: https://cfd-online.com/Forums/openfoam-meshing/198981-snappyhexmesh-3d-wing-meshing-setup-trials.html and https://www.cfd-online.com/Forums/openfoam-meshing/254604-airfoil-trailing-edge-boundary-layer-refinement-snappyhexmesh.html (layers collapse before the trailing edge; featureAngle 160 for leading and trailing edges; keep refinement levels graded); and the OpenFOAM user guide on snappyHexMesh: https://doc.cfd.direct/openfoam/user-guide-v8/snappyhexmesh (nCellsBetweenLevels, typically 3)
5. Gmsh: the `naca_boundary_layer_3d` example and `extrudeBoundaryLayer` (https://gitlab.onelab.info/gmsh/gmsh), and the reference manual https://www.gmsh.info/doc/texinfo/gmsh.pdf (curvature sizing `Mesh.MeshSizeFromCurvature`, `Mesh.MeshSizeExtendFromBoundary`)

The measurements in section 4 are ours: `benchmarks`-style scripts on `examples/airplane_wing.step` with OpenFOAM v2606 on Windows; the thresholds of section 2 marked *ours* are not taken from a source.
