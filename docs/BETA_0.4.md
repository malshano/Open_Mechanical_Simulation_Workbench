# Version 0.4.0 beta 3

The third test version of the **desktop application**. The browser version is gone: the workbench is now one window (Qt and VTK) with one
icon, **Mechanical Simulation Workbench**. Your saved studies open as before. The Claude assistant and scripts still reach the program through
the local control interface; nothing needs a browser.

## What is new since beta 2
- **A new study asks where to save it** first (a name and a folder, with Browse...), then opens on the Geometry step. Import your 3D model there,
  or later with **File > Import 3D model** (Ctrl+I).
- **Align a face to a plane or to the air volume**, with the real mouse: a guided three-step popup, the air volume drawn with its edges and each
  wall in its own colour, and a menu when several walls lie on the same spot.
- **Symmetry planes**: choose the direction and a preview plane appears; drag its arrow or type the offset (also parallel to a picked face), then Apply.
- **Refinement regions**: move, turn and resize them by number or by dragging arrows, rings and face handles; a region is drawn as you work.
  **Part sizing** can be picked by name or by clicking the part, and the **wall layers** show as a translucent shell round the object.
- **Materials**: drop-downs for solids and for fluids (a short library of liquids and gases), and you can add your own.
- **Dark and light themes** with a visible switch in the top bar; every message box and window was checked for readable text in both.
- **Safer menus**: drop-downs never change when you scroll over them, and the window fits your screen.
- **The rest of the former browser version** is in the window: CAD edits, the air-volume editor, rotors and moving zones, continue
  runs, the transient time bar, contour planes, vortex surfaces and more. Every control of the old version and where it is now: `docs/QT_PARITY.md`.
- **Installer**: one icon, and the setup window asks for the folder (default `C:\SimWB`) and whether to put an icon on the desktop.

## Fixed
- **"Access is denied" when saving a study** (it could stop a solver run): saving now waits a moment when a virus scanner, the search indexer or another
  program has the study file open, and tries again.
- A layer shell that could point inwards, an align popup that could run off a small 3D view, and a few buttons without a hint text.
- Windows left open by the program's own tests made long test runs very slow; the automatic test run on GitHub now finishes in about 21 minutes.

## Known limits of this beta
- Windows only. The first install is about 1.1 GB (Python, solvers, Qt and VTK) plus OpenFOAM and CalculiX.
- **Not in the window yet** (type the values instead): dragging the orange handles of the air volume, and dragging a rotor disc or a rotating
  zone. Block, roller and plate symbols for conditions (each condition has its tint, label and arrows). A per-part gizmo preview inside a flow
  study. Results extras: the promo look, the section-sweep video, the mirrored half of a symmetric model, the arrow length scale and the
  particle dot size and tail.
- **Aligning a refinement region with a face of the model** is not built yet (move, turn and resize are).
- Particle paths are drawn still; contour lines do not follow an animated deformation.
- The lift and drag coefficients of a flow study use the frontal area, which is not the wing area of a wing.
- Please report problems with the saved study attached (File > Export study to one file).
