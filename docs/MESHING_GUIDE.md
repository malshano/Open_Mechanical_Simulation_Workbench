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

### Edge refinement and tolerant layers (Mesh step, Expert)

The leading and trailing edges of a thin body carry much of its friction, and the cells there are the most expensive to get right. **Edge refinement** makes the cells along the sharp edges across the flow
(not the ones along it) the wall cell / factor (default 6) and grows them back to the wall size with the mesh growth ratio. The cells it adds are estimated before meshing and capped (default 150,000):
the edge cells are made coarser, in steps of 8 %, until the estimate fits under the cap.

**Why the first estimate was far off, and what was changed.** Measured as the mesh with the option minus the same mesh without it (gmsh only, quick preset, no layers), the first estimate (a regular tetrahedron
of the field size, edge by edge) was 0.3 to 8 times the real number, and the real number could be several times over the cap. Three causes:

1. The mesher's global smallest cell size (half the wall size, or the thin-part size) cut every edge cell finer than it back to it: a factor of 6 gave the cells of a factor of 3 or so. The edge cells now have their own floor.
2. The size at the edge curves was extended from the boundary over the whole faces next to it (the Gmsh option `MeshSizeExtendFromBoundary`): fine triangles, and the tetrahedra under them, covered whole faces - 154,000 extra cells for a factor of 6 on the example wing
   where the field alone makes about 40,000, and different from face to face. Where an edge field is used the size is no longer extended from the boundary (the smart sizing recipe already did this), so the size field alone decides and the estimate can count it.
3. The sum over the edges counted the tubes of close edges twice (the two rims of a thin plate). `edge_cells_union` integrates the cells of the size field over the union of the tubes (Monte Carlo, fixed seed, about 1 % noise; each edge counts for the
   air share of the tube, (360 - face angle) / 360), times a calibration constant of 0.52: the mesher's cells are about 1.5 times the edge of a regular tetrahedron of the field size.

**What it comes to** (smart sizing, so that the mesh without the option is the same mesh but for the edges; quick preset; real = cells with less cells without; estimate = what the panel shows before meshing):

| geometry | factor, cap | estimate | real | real / estimate | real / cap |
|---|---|---|---|---|---|
| wing | 3 | 13,900 | 16,400 | 1.18 | |
| wing | 6 | 54,400 | 60,800 | 1.12 | |
| wing | 12 | 144,200 | 150,300 | 1.04 | |
| flutter plate | 3 | 42,800 | 37,200 | 0.87 | |
| flutter plate | 6 | 174,800 | 168,600 | 0.96 | |
| flutter plate | 12 | 508,600 | 469,600 | 0.92 | |
| camera mount | 3 | 36,300 | 37,700 | 1.04 | |
| camera mount | 6 | 141,600 | 131,700 | 0.93 | |
| camera mount | 12 | 388,100 | 330,800 | 0.85 | |
| wing | 12, cap 20,000 (factor lowered to 3.2) | 16,800 | 20,100 | 1.19 | 1.01 |
| flutter plate | 12, cap 20,000 (to 2.2) | 17,500 | 14,700 | 0.84 | 0.74 |
| camera mount | 12, cap 20,000 (to 2.4) | 19,200 | 20,800 | 1.08 | 1.04 |
| wing | 12, cap 100,000 (to 8.8) | 96,200 | 105,900 | 1.10 | 1.06 |
| flutter plate | 12, cap 100,000 (to 4.4) | 99,900 | 92,800 | 0.93 | 0.93 |
| camera mount | 12, cap 100,000 (to 4.8) | 95,500 | 91,800 | 0.96 | 0.92 |
| topology bracket (not used for the calibration) | 6 | 50,700 | 53,400 | 1.05 | |
| quadcopter (not used) | 6 | 77,500 | 73,600 | 0.95 | |
| topology bracket (not used) | 12, cap 50,000 (to 5.6) | 44,800 | 47,700 | 1.07 | 0.95 |
| quadcopter (not used) | 12, cap 50,000 (to 4.4) | 45,100 | 45,100 | 1.00 | 0.90 |
| topology bracket, small cap | 12, cap 5,000 (to 2.0) | 3,900 | 6,700 | 1.70 | 1.34 |
| camera mount, small cap | 12, cap 5,000 (to 1.6) | 3,900 | 5,700 | 1.45 | 1.13 |
| wing, small cap | 12, cap 5,000 (to 2.0) | 4,200 | 6,700 | 1.58 | 1.34 |
| topology bracket, small cap | 12, cap 10,000 (to 2.6) | 8,600 | 11,900 | 1.39 | 1.19 |
| camera mount, small cap | 12, cap 10,000 (to 1.9) | 8,400 | 8,900 | 1.07 | 0.89 |
| wing, small cap | 12, cap 10,000 (to 2.6) | 9,200 | 11,000 | 1.19 | 1.10 |

For caps of 20,000 and up (the first 19 meshes) the real number is 0.84 to 1.19 times the estimate (the calibration constant was fitted on the first nine; the other ten, among them two geometries it never saw, are within 0.84 to 1.10), and with the cap the real extra cells
came out at 0.74 to 1.06 times the cap. **Below 20,000 the estimate is too low**: with caps of 5,000 and 10,000 the mesh came out 1.07 to 1.70 times the estimate and 0.89 to 1.34 times the cap. The edge cells are then only about 1.6 to 2.6 times finer than the wall cells, where the
extra cells of the curve discretisation and the transition are a larger share than the field's integral; the shortfall is roughly 2,000 to 3,000 cells whatever the geometry, but adding that constant makes the 20,000 rows worse (the plate would go from 0.84 to 0.73), so no correction was applied and the panel says so. The panel
claims "typically within 25 % with Smart sizing" only for caps of 20,000 and up, and "usually 1.1 to 1.7 times larger" below. Not measured: other presets than quick, other flow models, very large models, and a mesh with wall layers (the layers are added to the mesh afterwards and are not in these numbers).

With **Smart sizing off** the comparison is not clean: the old sizing spreads the sizes of curved edges over whole faces, which the edge size field does not, so the mesh with edge refinement can be smaller than without (example wing, factor 6: 2,100 more cells in total, though the edge zone holds
about 15,000 more than it would at the wall size). The cap and the estimate are for the cells of the edge zone; the real total difference is lower in that mode: measured: 0.04 to 0.7 times the estimate (wing factor 6: estimate 25,700, whole mesh +900; camera mount factor 12: estimate 430,000, whole mesh +307,000). The panel says so at the head of the estimate and offers to turn Smart sizing on.

After meshing the panel counts the cells in the mesh itself: the tetrahedra within the reach of the edge field (`dist_max`) of the refined edges, less what the same volume holds at the wall size, taken from the mesh's own cells just outside that reach. In the smart sizing
meshes it agreed with the real difference within 21 % in eleven of twelve cases (a small count, 33,000 against 14,700, was 2.2 times the real one), and it is shown in the warning colour when it is over the cap.

**Tolerant layers** loosen the layer step's quality limits (maxFaceThicknessRatio 0.5 to 0.8, maxThicknessToMedialRatio 0.3 to 0.6, 100 instead of 50 iterations, relaxed mesh-quality limits) so that more layers survive on sharp edges; the price is less smooth layer cells there.

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

## Sources

1. 4th AIAA CFD Drag Prediction Workshop, gridding guidelines: https://aiaa-dpw.larc.nasa.gov/Workshop4/gridding_guidelines_4.html (y+ about 1 for the coarse grid, 2/3, 4/9, 8/27 for finer; at least 2 constant layers; leading and trailing edge spacing about 0.1 % of the chord; cell count about 3 x between levels; farfield about 100 chords)
2. NASA High Lift Prediction Workshop 1, gridding guidelines: https://hiliftpw.larc.nasa.gov/Workshop1/GriddingGuidelinesHiLiftPW1-11JUN09.pdf (growth below 1.25 in the viscous layer)
3. "Best Practices and Mesh Convergence on the Extruded Imperial Front Wing With CharLES Solver", AIAA 2024-4259: https://doi.org/10.2514/6.2024-4259 (y+ about 2.5 and low aspect ratio cells for a laminar separation bubble; targeted refinement of walls and wakes); and "The Complete CFD Meshing Guide": https://turbulentthoughts.com/meshing-mistakes-that-derail-cfd-simulations-and-how-to-fix-them/ (growth below 1.2 in high-gradient regions)
4. CFD Online, snappyHexMesh wing threads: https://cfd-online.com/Forums/openfoam-meshing/198981-snappyhexmesh-3d-wing-meshing-setup-trials.html and https://www.cfd-online.com/Forums/openfoam-meshing/254604-airfoil-trailing-edge-boundary-layer-refinement-snappyhexmesh.html (layers collapse before the trailing edge; featureAngle 160 for leading and trailing edges; keep refinement levels graded); and the OpenFOAM user guide on snappyHexMesh: https://doc.cfd.direct/openfoam/user-guide-v8/snappyhexmesh (nCellsBetweenLevels, typically 3)
5. Gmsh: the `naca_boundary_layer_3d` example and `extrudeBoundaryLayer` (https://gitlab.onelab.info/gmsh/gmsh), and the reference manual https://www.gmsh.info/doc/texinfo/gmsh.pdf (curvature sizing `Mesh.MeshSizeFromCurvature`, `Mesh.MeshSizeExtendFromBoundary`)

The measurements in section 4 are ours: `benchmarks`-style scripts on `examples/airplane_wing.step` with OpenFOAM v2606 on Windows; the thresholds of section 2 marked *ours* are not taken from a source.
