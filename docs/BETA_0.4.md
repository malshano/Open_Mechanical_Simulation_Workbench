# Version 0.4.0 beta 5

The fifth test version of the **desktop application** (Qt and VTK; the browser version is gone). One icon, **Mechanical Simulation Workbench**. Your saved studies open as before.
Beta 3 was the first without the browser version; this beta adds a much better mesh for a body in a flow (beta 4 was built but never released: its changes are in this one).

## What is new since beta 3
- **Part sizing on picked faces.** *Pick faces in the 3D view* collects faces one click at a time (a second click takes one out), and *Size the selected faces* gives them a cell size of their own; pick the next group and add another. Any number of sizings on the faces of one part, each listed by its faces with its own size, a button to show its faces, and one to remove it.
- **A mesh advisor for flow studies** (Mesh step, under the resolution buttons). It reads the body the way the flow sees it (its length along the flow, its width, its thinnest part), says
  what a good mesh needs, and after meshing gives a **mesh score** with one line per check (wall cells, cells across the thin part, the near field, the wake, how smoothly the cells grow,
  the distance to the domain walls, the wall layers and their y+, non-orthogonality, skewness, the poorest cell) and advice for each one that is not ok.
  Background, measurements and sources: `docs/MESHING_GUIDE.md`.
- **Smart sizing** (a switch, off by default) and **Use the recommended setup**: the old sizing started from the largest side of the body, so a thin wing got coarse cells round it and none
  in its wake. Smart sizing sizes from the length along the flow, refines a near-field box and the wake with a smooth growth, and never asks for more cells than half of your free memory holds.
  On the example wing the score went from 0.65 with two failed checks (156,000 cells) to 0.86 with none (1.03 million cells). It costs cells: about six times as many at the same preset.
  The assistant has the same tools (`mesh_advice`, `mesh_apply_recipe`).
- **Wall layers are made with the mesh.** The mesh you see and count is now the mesh the solver runs. A "Wall layers made" card (per surface: layers made, thickness, share of the wall),
  the colour **Wall layers** (a cut through the object, the camera on its edge), **Make the layers again**, **Zoom to the layers**, and a **target y+** for the first cell.
  The card says why layers are lost: a layer grows from each side of a thin part, so all the layers together must take less than about half its thickness.
- **y+ is now measured where OpenFOAM measures it**, at the centre of the first cell. The first layer used to be sized so that the target was reached at its outer face, so a run set up for
  y+ 30 came out at about 15 (the buffer layer, where wall functions are least accurate). The default first layer is now twice as thick; the card and the advisor tell you when that leaves room for fewer layers.
- The statistics table, the layer card and the advisor wrap their text instead of making the panel wider than its dock (the right edge of buttons and text was clipped).

## Known limits of this beta
- Windows only. The first install is about 1.1 GB (Python, solvers, Qt and VTK) plus OpenFOAM and CalculiX.
- **Smart sizing is off by default**, and it needs more cells and more time than the old sizing; the layers need OpenFOAM at meshing time (without it the run adds them as before).
- The mesh advisor and smart sizing are for external aerodynamics with the tetrahedral mesh; the size of the air volume is checked but not changed. A thin wing needs about a million tetrahedra.
- Only one layer fits on a thin wing with wall functions at 12 m/s; the card says so. Resolved layers (y+ about 1) would need many times more cells.
- **Aligning a refinement region with a face of the model** is not built yet (move, turn and resize are).
- **Not in the window yet** (type the values instead): dragging the orange handles of the air volume, and dragging a rotor disc or a rotating zone. Block, roller and plate symbols for conditions.
  A per-part gizmo preview inside a flow study. Results extras: the promo look, the section-sweep video, the mirrored half of a symmetric model, the arrow length scale and the particle dot size and tail.
- Particle paths are drawn still; contour lines do not follow an animated deformation.
- The lift and drag coefficients of a flow study use the frontal area, which is not the wing area of a wing.
- Please report problems with the saved study attached (File > Export study to one file).
