# Version 0.4.0 beta 1

A first test version of the **desktop application**: the same workbench in its own window (Qt and VTK) instead of the browser.
The web version is still installed and has its own icon; nothing in your saved studies changes.

## What is new
- **A desktop window** with an outline of the study, the five steps (geometry, setup, mesh, solve, results), a 3D view you can turn,
  zoom and pick in, dark and light themes, simple and expert modes, and the Claude assistant in a dock.
- **Save and open cases**: save as a full copy, export a study to one `.zip` file, import it again.
- **Results**: colour maps, cuts, contour lines with their values written on them, labels of the highest and lowest value that you can
  move, animation, probes. For flow and cooling studies: vortex surfaces, a volume cloud, vector arrows and particle paths over the object.
- **Mesh views**: colour the mesh by quality, cell size or region, cut through it, show the cells below a quality threshold (the web version
  also colours by aspect ratio, size jump and volume and shows a cell budget).
- **Safer assistant**: the assistant (and an MCP client) can only write files inside the workspace or the temp folder, never replace
  a file, and cannot reach network or device paths; a saved study that is opened is unpacked with its entry names checked.

## Start it
Use the new icon **Mechanical Simulation Workbench (beta desktop)** (desktop and Start menu), or run `run_workbench_qt.bat`.

## Known limits of this beta
- Windows only, like the rest. The first install is about 1.1 GB (Python, solvers, Qt and VTK) plus OpenFOAM and CalculiX.
- A very rare failure of the mesher to start on a machine short of memory shows a "job failed" box; run it again.
- Particle paths are drawn still (not animated); contour lines do not follow an animated deformation.
- The lift and drag coefficients of a flow study use the frontal area of the object, which is not the wing area of a wing: for a
  wing, work from the forces themselves.
- Please report problems with the saved study attached (File > Export).
