---
title: Mechanical Simulation Workbench - User Manual
audience: engineers who use the workbench (not people who change its code; for that read TECHNICAL_MANUAL.md)
product_version: 0.4.0b5
document_version: 2
format: Markdown, one topic per section, UI labels in **bold**, file names and commands in `code`
how_to_use_this_file_with_an_llm: >
  Paste the whole file, or only the section whose heading matches the task. Every section is self-contained:
  it repeats the terms it needs. Section ids (the "## N." numbers) are stable references.
---

# Mechanical Simulation Workbench - User Manual

## 0. Summary (read this first)

| Item | Value |
|---|---|
| What it is | A desktop application: import CAD, mark faces, mesh, solve, look at results. Like Ansys Workbench, but small. |
| Runs where | On your own computer, in its own window. Nothing is uploaded. |
| Input | STEP (preferred), STL or OBJ files. |
| Studies | Heat transfer in solids, Static structural, Topology optimisation, Vibration (modal), Electronics cooling (CFD), External aerodynamics (CFD). |
| Solvers | Built-in finite-element solvers (no install) and OpenFOAM (installed automatically on Windows). |
| Workflow | **Geometry -> Setup -> Mesh -> Solve -> Results** (the five steps along the top bar). |
| Assistant | A Claude chat panel on the right can do the setup for you. It needs a Claude login. |
| Where studies are saved | In a folder you choose every time you create a study: the folder chooser opens before anything is created (cancel = nothing is made). |

Terms used in this manual:

| Term | Meaning |
|---|---|
| Study | One simulation (geometry + conditions + mesh + results). Stored as one folder with a `project.json`. The program calls it "project" internally. |
| Face | One surface patch of the CAD model. Boundary conditions are put on faces. |
| Boundary condition (BC) | A load or constraint on faces: a fixed temperature, a force, a velocity inlet, and so on. |
| Part / body | One solid of the model (or the air volume). |
| Air volume | The box (or other shape) of air that the program builds around the parts in the flow studies. |
| Mesh | The cells the solver works on. |
| Moving zone | A cylinder of air that turns with a propeller, fan or wheel. |
| Transient | A run that follows time. Its results have a time slider. |
| Steady | A run that looks for one final state. |

---

## 1. Install and start

1. Windows: run `SimWB-Setup.exe` (one file) or unzip the release and double-click `Install SimWB.bat`. The setup puts everything in one folder (default `C:\SimWB`), downloads OpenFOAM and CalculiX, and makes one icon, **Mechanical Simulation Workbench**, on the desktop and in the Start menu. No administrator rights are needed.
2. Double-click the icon: the window opens in front, in a few seconds.
3. From a source checkout: double-click `run_workbench.bat`. The first start creates a Python environment (Python 3.10 or newer is needed), installs the packages and, for the flow studies, downloads OpenFOAM (about 200 MB) and the Microsoft MPI runtime (about 12 MB) into the workbench folder.
4. To stop: click **Close** (top right). This stops every running solver.

Other ways to start:

```
.venv\Scripts\python -m simwb.qt
```

Settings are read from environment variables (all optional):

| Variable | Effect |
|---|---|
| `SIMWB_HOME` | Folder where studies are stored by default. Default: the `projects` folder of the workbench. |
| `SIMWB_PORT` | Port of the window's local API (REST, tools and window control: for Claude and scripts). Default 8765, or the next free one. |
| `SIMWB_OPENFOAM_DIR` | Move the built-in OpenFOAM copy. |
| `SIMWB_OPENFOAM_CMD` | Use an OpenFOAM that is already installed. |
| `SIMWB_AUTO_OPENFOAM=0` | Do not download OpenFOAM automatically. |
| `SIMWB_MEM_LIMIT_GB` | Memory limit for the helper programs (meshing). |
| `SIMWB_CLAUDE_MODEL` | Model used by the chat panel. |
| `ANTHROPIC_API_KEY` | Use this key for the chat panel instead of a Claude login. |

---

## 2. The screen

| Area | Where | What it does |
|---|---|---|
| Step bar | top | **Geometry, Setup, Mesh, Solve, Results**. A green tick means the step is done. Click a step to go there. |
| Study menu | top left | Open, create, rename or delete a study. The **SimWorkbench** logo goes to the start page. |
| Outline | left, upper | The model tree: Geometry, Boundary conditions, Rotors, Moving zones, Mesh, Solution, Results. Click a node to open and highlight it. Ticks and warnings show what is ready or missing. |
| Details panel | left, lower | The settings of the selected step or node. |
| 3D view | centre | Rotate with the left mouse button, pan with the right, zoom with the wheel. Buttons **Iso X Y Z Fit** set the view (click the same axis twice to look from the other side). **Edges** shows face edges. **Air box** shows the air volume. **Section** cuts the model by a plane. |
| Messages / Solver log | bottom | What is ready or missing; the live log of the running job. |
| Claude panel | right | One chat box for the assistant and for Claude commands that start with `/` (sections 10 and 11). |
| Status bar | bottom | Study name, mesh size, solution state, progress of the running job with **Cancel**. |
| **☀ Light mode / ☾ Dark mode** | top right, left of Simple / Expert | Switches between the dark and the light theme with one click; the button names the theme it switches to. The choice is remembered. **View > Theme** also has *Follow the system*. All text is at least 4.5:1 against its background in both themes (messages, boxes and dialogs 7:1). |
| **Simple / Expert** | top right | **Simple** shows the essential settings and a **Next step** hint on top of the details panel (what is still missing, with a button that takes you there). **Expert** shows every setting and tool: part sizing, refinement regions, wall layers, solver numerics, case files, the solver choice, extra checks. Switching changes only what is shown, never a value. A new installation starts in Simple. |
| **Probe**, **Picture** | above the 3D view | **Probe** (Results step): click a point of the coloured result to pin its value there; click **Probe** again to stop. The labels stay until the field, the time or the section changes. **Picture** saves the 3D view as a PNG with the colour scale. |

---

## 3. Create a study and choose where it is saved

1. Open the start page (**File > New study**, or the **＋ New** button in the top bar).
2. On the card of the kind of study you want, press **New study…** (or **Try example**).
3. A small window asks **where to save it**: a **Name** and a **Save in** folder (type it, or press **Browse…**). It shows the exact folder the
   study will be. Press **Create study**; **Cancel** makes nothing.
4. The study gets its own sub-folder, named after the study, inside the folder you chose (the folder is made if it does not exist). If that
   name is taken, `-2`, `-3`... is added. The program remembers the folder for the next study.
5. The study opens on the **Geometry** step. Now import your 3D model: press **Import CAD file (STEP, STL, OBJ)…**, or use **File > Import 3D
   model…** (Ctrl+I) at any time (with a model already there it replaces it). The model is copied into the study's folder.
6. **Duplicate** (⧉ next to the study name): a copy of the open study with its geometry, setup and mesh, but no results. Use it for variants, for example the same propeller at another speed, or a transient run of a steady setup. The mesh stays valid, so the copy can be solved at once.

Rules:

- The folder must be a full path (starts with a drive letter, or `/` on Linux/macOS). A relative path is refused; the window says so and stays open.
- The folder is created if it does not exist. It must be writable; otherwise you get the message "Cannot save in ...".
- The path of every study is shown in the list of recent studies on the start page, and in the output of `/status` and `/login` (they run in that folder).

Open a study you saved earlier (for example one that was moved or copied to another computer):

1. On the start page click **Open a saved study...**.
2. Pick the study folder (the one that contains `project.json`).
3. It is added to the list. If the folder is not a study you get "is not a project folder".

Delete a study: the trash icon in the study menu or in the recent list. This deletes the whole study folder, including meshes and results, after a confirmation.

---

## 4. Step 1 - Geometry

1. Click **Use my STEP file** or drag a file in. STEP units are honoured (converted to metres inside).
2. STL/OBJ files must be closed (watertight). They are read in millimetres and split into faces at edges sharper than 40 degrees. Each closed shell becomes a solid.
3. Touching bodies are fused into one conformal mesh (perfect thermal contact).
4. If the model is turned the wrong way (for a flow study: the nose is not along the flow axis), turn it in the Geometry step. **Drag one of the three rings** around the model in the 3D view (red = x, green = y, blue = z; hold **Shift** for 5° steps): the model turns live about its own middle and stays where it is, while the rings and the air-flow arrow stay fixed. The angle is shown while you drag. Press **Apply orientation** to rebuild the geometry (the parts turn exactly, faces keep their conditions, rotors, rotating zones and symmetry planes turn with them) or **Cancel**. The buttons X/Y/Z ±90° and the typed angle do the same without the mouse; **Hide rings** removes the rings; **Reset** returns to the file's own orientation. An orientation built with this view is exact: it is applied to the stored file, not to the mesh.
   **Align a face to a plane:** press **⬓ Align a face to a plane…** and make two clicks in the 3D view. **First click: the face to align** (a flat face of a part; it turns yellow and an arrow shows which way it points, out of the part). **Second click: the plane to align it to**: a wall of the tunnel (flow and cooling studies: the air volume is shown translucent and the wall under the pointer lights up; for example the floor) or another flat face of the model. For a tunnel wall choose **facing the wall** (the face lies against it) or **facing away**; for another face choose **same way** (parallel, pointing the same way) or **face to face** (pointing at each other). Instead of the second click you can pick a coordinate plane in the panel: **XY plane** (facing up +z or down −z), **YZ plane** (+x / −x), **XZ plane** (+y / −y); the nearest choice is marked ★. The model turns in the view at once (the shortest turn); try another choice if it faces the wrong way. The turn is only a preview until it is applied: press **Apply orientation**, or just go on to the next step - **it is applied automatically when you leave the Geometry step**. Faces of the air volume and contact faces between two parts cannot be the face to align.
5. Flow studies get an automatic **air volume**. Choose its shape in the details panel:
   - **Box** - a plain box.
   - **C-shape (curved inlet)** - half cylinder upstream, box downstream. Fewer cells for the same distance to the inlet. STEP only.
   - **Cylinder along the flow** - STEP only.
   You can drag the orange handles in the 3D view or type sizes, then click **Apply**. **Fit to parts** returns to the automatic size. **Show all** zooms out to the whole air volume.
6. A failed import keeps the previous geometry.
7. **CAD edits (ask the assistant).** Describe the change in the chat and the assistant edits the model: *"drill a 6 mm hole
   through the plate 20 mm from the end"*, *"round the four long edges with 2 mm"*, *"cut the model in half at x = 0"*, *"mirror
   the wing about the XZ plane and join the halves"*, *"add a 10 mm rib along the top"*, *"make a wing from these airfoil
   sections"*. It can add boxes, cylinders, spheres, cones, flat shapes pushed out (prisms), shapes turned about an axis
   (revolve) and lofts through cross-sections; cut, join or intersect bodies; move, turn, scale, mirror and pattern them (a row,
   a ring of blades); put fillets and chamfers on chosen edges and drill holes. It measures from the model first and checks the
   size and volume after each step.
   The edits are listed under **CAD edits** in the Geometry panel. They are replayed on your imported file whenever the geometry
   is built, so the file itself is never changed: press **✕** on an edit to remove it (the later ones are replayed without it),
   **↶ Undo last**, or **Remove all** to return to the file as imported. A refused edit (a fillet too large for the body, a
   body name that does not exist) says why and leaves the model as it was. After an edit the faces have new numbers: boundary
   conditions on faces that survive follow them, the assistant tells you which ones to check. The mesh and results become
   stale. STEP files only (not STL / OBJ). If you turn the model afterwards, the edits turn with it.

---

## 5. Step 2 - Setup

### 5.1 Common steps

1. Choose a boundary condition from the list in the details panel.
2. Click faces in the 3D view to assign them. Click a face again to remove it.
3. Faces without a condition get the study default (insulated / free / no-slip wall).
4. Selecting many faces:
   - **Shift+click**: all faces of that part.
   - **Ctrl+click**: all similar faces (same surface type, parallel normal, similar area).
5. To reach faces inside the model, untick **Air box** or use **Section**. Hidden or cut-away faces cannot be clicked.
6. Hover a face to see what it is. Each condition is drawn on its faces with a label (arrows for loads and flows, blocks for fixed supports, and so on).
7. The **Readiness** list (and the **Messages** tab) says what is still missing.
8. **About this simulation** and the small texts under each option explain every choice in plain language.

### 5.1a Materials and fluids

Every part has a **Material** drop-down in *Parts & materials*, and the fluid region has a **Fluid** drop-down in the same place. The last
entry of each drop-down, **＋ Add a material…** or **＋ Add a fluid…**, adds one of your own. The **Materials…** button next to the heading opens
the library: two tabs, *Solids (parts)* and *Fluids (liquids and gases)*, with the values of every material.

- **Solids** (steel, aluminium, copper, plastics, titanium ...): density, specific heat, conductivity, Young's modulus, Poisson's ratio,
  and a yield strength (empty for a brittle material).
- **Fluids**: gases (air, nitrogen, helium) and liquids (water, seawater, a 50 % glycol coolant, ethanol, transformer oil, engine oil,
  glycerin), all at about 20 °C. A **gas** follows the perfect-gas law in the compressible runs; a **liquid** has a constant density.
  External aerodynamics (a body moving through the fluid) and the cooling study take any of them. A liquid in the cooling study is a
  forced flow (Air flow: fan, that is a pump or an inlet): natural convection needs a gas, and the readiness list says so.
- **Add** opens a form. *Start from* copies the values of a material that is like yours; then change what differs. Viscosity is in mPa·s
  (the same number as centipoise: water 1.0). The Prandtl number is worked out from the other values when left empty. A wrong value is
  refused with the reason, and the form stays open.
- **Your own materials** are saved with your workspace and kept inside every study that uses them, so a study sent to someone else opens with
  its materials. In the library you can **Change…**, **Copy as new…** and **Remove** them; the built-in ones cannot be changed (copy one
  instead) and one that a study uses cannot be removed.
- Claude can do the same: the tools `add_material` and `remove_material`, and the window actions `setup.materials`, `setup.material_add`,
  `setup.material_remove`.

### 5.2 Boundary conditions per study

| Study | Condition ids (as labelled in the GUI) | Default for unassigned faces |
|---|---|---|
| Heat transfer in solids | Fixed temperature, Convection, Radiation + convection, Natural convection + radiation (still air), Heat flux, Heat load | Insulated |
| Static structural | Fixed support, Frictionless support, Displacement, Force, Pressure | Free |
| Topology optimisation | Fixed support, Frictionless support, Force, Pressure, Keep solid (and symmetry planes) | Free |
| Vibration (modal) | Fixed support, Added mass | Free |
| Electronics cooling (CFD) | Velocity inlet, Pressure outlet, Slip wall, Wall with fixed temperature, Wall with convection | Adiabatic wall (air box sides: slip) |
| External aerodynamics (CFD) | Wall, Slip wall, Symmetry, Moving wall | Wall (no-slip) |

Heat sources are set on parts (body settings), not on faces, in the heat and cooling studies.

### 5.3 Settings per study

| Study | Main settings |
|---|---|
| Heat transfer in solids | Analysis (steady/transient), End time, Time step, Start temperature, Number of saved frames |
| Static structural | Gravity |
| Topology optimisation | Mass to keep, Smallest feature size, Most iterations, Extra solid layers round supports and loads (and per part: In the design space / keep solid) |
| Vibration (modal) | Number of modes, Excitation frequency range (modes within 15 % of the range are flagged) |
| Electronics cooling | Air flow (forced/natural), Room temperature, Radiation between surfaces, Emissivity, Fluid, Flow model, Contact resistance, Gravity, Analysis, End time, Time step, Iterations, CPU cores |
| External aerodynamics | Fluid, Flow model, Flow speed (m/s, below about 100), Forward axis, Up axis, Pitch angle (nose down +), Sideslip angle, Vehicle mass, Rotor drag coefficient k, Auto trim, Moment point offsets, Reference area, Reference length, Turbulence intensity, Analysis, End time, Time step, Iterations, CPU cores |

### 5.4 Heat: no fan

- **Natural convection + radiation (still air)** works out the heat transfer coefficient h on every face (size, orientation, temperature) and adds radiation to the room. You do not have to guess h.
- Electronics cooling with **Air flow = natural** simulates the air rising off the parts: no inlet, an automatic air box open to the room, vents marked **Air outlet / opening**. Switch **Radiation between surfaces** on (often 30-50 % of the heat).

### 5.5 External aerodynamics: rotors and moving zones

- **Rotors** (actuator disks): found automatically on motor tops. Thrust defaults to carrying the weight at the set pitch. Moves air without modelling blades.
- **Moving zones** (a real turning propeller/fan/wheel):
  1. Open **Moving zones**, add a zone.
  2. Set Centre, Axis, Radius, Length, rpm, or click **Fit around part**.
     **The zone must turn about the real axis of the part.** **Fit around part** uses the part's area-weighted centroid, which for a multi-blade propeller is not the hub. Instead press **⌖ Centre on a cylinder or circular edge…** on the zone and click the real centre of rotation in the 3D view: a **cylindrical face** (the motor shaft or can, a hub bore) or a face near a **circular edge** (the rim of a hole, the edge of a boss or disc; the circular edge nearest to your click is used). The zone's centre and axis are set exactly on it (the axis keeps its sense of rotation); radius and length are kept. A yellow circle and line show what was found. The centre along the axis is the middle of the cylinder (or the plane of the edge): move it with **Centre x / y / z** if the zone should sit elsewhere along the axis.
     A rotating zone has to lie along x, y or z. If the feature you picked is tilted (for example 5° off), the workbench offers to **turn the whole model** so that its axis lies along the nearest coordinate axis; the zone stays (on that axis) and you pick again to put it exactly on the feature. The same button is on each **rotor** (actuator disc), where any axis direction is allowed.
  3. Choose the method:
     - **MRF**: steady, rotating reference frame, fast, shows one blade position.
     - **Sliding mesh**: transient, the zone's cells really rotate. Needs a transient analysis.
  3b. **Two or more propellers:** add one zone per propeller (each zone around its own propeller, with its own centre, axis and rpm; use a negative rpm for a propeller that turns the other way). All of them can be sliding-mesh zones in the same run. The zones must not overlap or touch each other (the air between them needs cells) and each must keep a gap to every part that is not inside it. Each sliding interface is kept on its own processor, so use at least as many CPU cores as sliding zones. If the zones are so close that their interfaces share cells, the workbench keeps all interfaces on one processor instead (the log says so); the run is then somewhat slower. Moving the zones apart gives a better balance.
  4. Regenerate the mesh after you move or resize a zone. A new speed (rpm) or a switch between MRF and sliding mesh keeps the mesh.
  5. The walls inside a zone (the propeller) get their own wall patch (`<zone>_walls`) and force monitor. The results then report the **propeller alone**: thrust, torque, power, and the propeller coefficients C_T = T/(ρn²D⁴), C_P = P/(ρn³D⁵), advance ratio J = V/(nD), efficiency and tip Mach number. D is taken from the blades, not from the zone.
  6. Tip speed: the solver is incompressible. Above a tip Mach number of about 0.7, thrust and torque lose accuracy, and above 1 they are not physical. Example: a 7-inch propeller reaches Mach 1 at about 37,000 rpm.
- Flow model **kOmegaSSTDDES** (DES, section 8.3) resolves vortices and needs a transient analysis.

---

## 6. Step 3 - Mesh

1. Choose a preset: **quick, coarse, medium, fine, very fine**. Start with coarse for a first look.
2. Click **Generate mesh**. The progress is shown in the status bar. The result is cell count and a quality summary (cells below quality 0.1 are "poor").
3. Look inside with **Section**. The **mesh metrics** view shows only the cells below a quality you choose, on the CAD, and zooms to the worst.
4. Local control:
   - **Face sizing**: cell size on chosen faces.
   - **Part sizing**: cell size on a whole part's surface, growing outwards.
   - **Refinement regions**: boxes and cylinders of fine cells (body of influence). A cylinder can have an **Inner radius**: greater than 0 makes a ring (tube).
   - **+ Wake behind rotating zones**: one-click cylinder of fine cells downstream of each rotating zone.
   - **+ Tip-vortex rings**: one-click ring of fine cells (0.65-1.0 zone radius, one radius long, cells of 1/50 zone radius) along the path of blade-tip vortices. Use with the DES flow model.
   Each region shows an estimate of its cell count; check it before you generate (millions of cells need hours).
5. Sizing is kept when you replace the geometry (part sizing follows parts of the same name; what cannot be placed is dropped with a note).
6. Warning "Max non-orthogonality ... > 75 degrees can make OpenFOAM unstable": refine the mesh near the sharp or thin features, or accept the risk for a quick look.
6a. **Mesh advisor** (external aerodynamics, tetrahedral mesh; under the resolution buttons, in Simple and Expert mode): what a good mesh of this body in this flow needs, and how yours measures up.
   Background, measurements and sources: `docs/MESHING_GUIDE.md`.
   - **Smart sizing (recommended)**: sizes the mesh from the length of the body **along the flow** (the old sizing used the largest side, which for a wing is the span, so a thin wing got
     coarse cells round it and none in its wake). It refines a near-field box and the wake behind the body, with a cell growth of 1.2 to 1.3, and never asks for more cells than half of the
     free memory holds (the text says when a preset was coarsened). A line under the switch gives the numbers: wall cells, near field, wake, growth, cells.
   - **Use the recommended setup**: smart sizing on and the wall layers that fit: the first layer for a y+ of about 40 at its centre (wall functions need 30 or more) and as many layers (at
     most 5) as the thinnest part leaves room for. Then press **Generate mesh**.
   - **Mesh score** (after meshing): the share of the checks that pass, and every check with its value and its target: wall cells (at most 1.5 % of the length), cells across the thinnest part
     (at least 4), the near field (at most 5 % of the length), the wake (at most 8 %), the change of cell size between neighbours (at most 1.6), the distance to the domain walls, the wall layers
     and their first-layer y+ (5 or less, or 30 or more), the non-orthogonality and skewness of the mesh the solver runs, and the poorest cell. ✓ is ok, ⚠ is to look at, ✗ failed; each
     ⚠ and ✗ says what to change. The limits are practical targets (the research grids ask for much more); the reasons are in `docs/MESHING_GUIDE.md`.
   - The assistant has the same tools (`mesh_advice`, `mesh_apply_recipe`).
7. **Mesh type** (external aerodynamics): **Tetrahedral** (the default, made by Gmsh) or **Hex-dominant** (made by OpenFOAM's snappyHexMesh). The hex-dominant mesh is mostly hexahedral cells that change size in octree steps, with a finer wake behind the body and wall layers made together with it. It usually has fewer, more orthogonal cells for the same wall resolution. It takes about a minute per 100,000 cells and needs OpenFOAM.
   - It is for **a body in the automatic box of air**, with its wall faces as the only boundary conditions. **Rotors, moving zones, symmetry planes and a custom air volume need the tetrahedral mesh**; the button explains which one stops it.
   - The preset, *Near-wall size*, *Face sizing* (thin faces get finer cells), *Refinement regions* and the *Wall layers* settings work as for the tetrahedral mesh.
   - **Thin parts need fine cells**: a wing 64 mm thick meshed with 50 mm cells cannot get layers (the statistics show how much of the wall did). Give thin faces a finer *Face sizing*, or choose a finer preset.
   - The statistics show the share of hexahedra, the maximum non-orthogonality, skewness and aspect ratio from `checkMesh`, and the share of wall faces that got layers. "Concave cells" are reported as a note: snappyHexMesh limits them and the solvers tolerate them.

---

## 7. Step 4 - Solve

1. Click **Run**. Built-in studies take seconds to minutes. OpenFOAM studies take minutes to many hours.
2. Steady OpenFOAM runs stop automatically when the key results (hottest temperature / forces) no longer change. You can continue a run for more iterations. A transient run that was stopped before its end time (Cancel, or the workbench or computer stopped) shows **Resume** on the Solve step: it carries on from the last time the solver saved, with the same split case, to the end time.
3. **CPU cores**: *Auto* uses all physical cores for large meshes (about one core per 20,000 cells), or choose a number. The mesh is split, solved in parallel, and joined again automatically. The join step now runs several processes at once for transient results.
4. Cancel a run with **Cancel** in the status bar. A cancelled run is marked interrupted. **Stop and keep results** ends a run cleanly and keeps what exists.
5. **Advanced (OpenFOAM)**: convection scheme, non-orthogonal correctors, relaxation, PIMPLE outer correctors, Courant number, solver tolerances. **Case files** lists every OpenFOAM file of the case; edit one and your version replaces the generated file on every run until you revert it. Entries that make OpenFOAM run code or programs (`#codeStream`, `#calc`, `coded` conditions and function objects, `systemCall`, libraries given by path, `#include` of files outside the case) are refused: a study you receive from someone else could otherwise run programs on your computer. To use them, run the case by hand outside the workbench.
6. **Start from** (external aerodynamics): *Uniform flow*, or the latest solution of another study of the same geometry. Use it to start a transient sliding-mesh run from a converged steady MRF run of the same propeller: the slipstream is already there, so one or two revolutions are enough instead of several. It also helps a steady run at a new speed started from a neighbouring speed. The cells are paired by position; on the same mesh the copy is exact.
7. **Write OpenFOAM case only** / download .zip exports the case so it can be run elsewhere (`./Allrun`).
8. Time needed for transient flow runs: the run-time estimate of the Solve panel is unreliable for sliding-mesh runs. Judge from the **Solver log**: time reached, Courant number, step count.

Transient settings: set **Analysis** to transient, give the **End time**. The time step is automatic (heat: end time / 200; flow: Courant number about 1) or fixed. All loads switch on at t = 0. Results get a time slider, a play button and a plot over time. With a **sliding-mesh** rotating zone, the Setup step shows a **Suggested end time** under the settings, with a **Use it** button: the longest of 5 revolutions of the fastest zone, 2 revolutions of the slowest zone and 3 flow-through times of the model (its length along the flow / the flow speed; none when the air is still), rounded up to two figures. It also tells you how many time steps that is (one step is 0.5° of the fastest zone, 720 per revolution) and warns when your own end time is more than three times longer. It is a starting point for a first transient run from uniform flow; a run started from a converged steady one needs fewer revolutions.

**Time limit (flow studies).** The setting **Time limit** (hours, 0 = none) stops a run that takes longer: the solver writes its current solution and the results are kept, and the convergence note says that the limit ended the run. The hours count from the start of the run, so they include the mesh split before the solver starts (25-40 minutes for 3.6 million cells). A run whose solution blows up (turbulence values above 1e9, or the continuity errors growing) is stopped at once with a hint, instead of running to the end on garbage. While it runs, the Solution step shows the newest residual of every field, the residual chart and the solver's last lines. Afterwards the saved times are joined and read by several processes at once, as many as the memory of the computer allows.

**When a saved time cannot be joined (flow studies, parallel runs).** The saved times of a parallel run are joined one by one, the newest first. If one of them is damaged (for example a file that was only half written when the run was stopped), the workbench leaves that time out, joins all the others and names it: a warning in the results ("The saved time 0.015 s could not be joined ... reconstructPar could not read 0.015/meshPhi (line 2068449)") and a line in the solver log. Nothing is deleted: the pieces of the damaged time are moved to `case/unjoined/<time>/processorN`, where the file can be repaired by hand. If no time at all can be joined, the run is shown as failed with the real cause (the file that could not be read), not as a divergence. A run is only called a divergence when the solver itself crashed with a floating point exception.

**Load results from case.** On the Solve step, **Load results from case** joins the saved times that are still in the case folder (`case/processor*`) and loads them as the results, without starting the solver. Use it after a run was stopped, failed or its results could not be loaded. It leaves the pieces in place, so the run can still be resumed. It never hands back an older time without saying so: if the newest saved time in the case folder could not be loaded, the results carry a warning that names it and the time they are from. Joining the saved times is done by OpenFOAM, so the button is greyed out (with the reason in its tooltip) while OpenFOAM is not installed and there are times left to join; the Solve step says how many. Before you run again, the Solve step also says how many saved times exist only as pieces, and how many were set aside in `case/unjoined/` after a failed join: a new run deletes the old case, including them.

**Changing a setting and changing it back.** A stopped or finished run can only be carried on while the setup still matches the case it was made from. The study remembers that setup when the case is written and compares against it: a setting that is changed and then set back to the same value leaves **Resume** and the results available. When something really differs, the Solve step shows one note at the top of the step, for example "The setup changed since the last run (Flow speed: 30.0 to 45.0 m/s; Advanced OpenFOAM settings). Change it back to carry on the old run, or run again (that replaces the old case)." (the same sentence is the refusal when you try to resume anyway). Below it, one short line says how many saved times are not loaded yet or were set aside, and how to load them. The note is ordered by what to do: the old run first, then the saved times, then the mesh and setup items. The end time, the save settings (**Saved time points**, **Saved frame every**), the iteration count, the number of cores and the time limit do not count as changes. A resumed transient run carries on to the end time that is set at that moment (it must be later than the last saved time) and keeps it exactly: the remaining time is split into a whole number of saves, so the last one falls on the end time. The save interval you ask for is used when it divides the remaining time; otherwise the nearest one that does is used, and the job message says which ("2 more saves every 0.025 s, fitted from 0.02 s so that the last one falls on the end time 0.2 s"). The **End time** setting is not changed by a resume.

**A study run by an older version of the workbench** kept no record of what its case was made from, so it cannot tell whether the setup changed. If it is locked, the Solve step says so and offers **Load results from case** (when OpenFOAM can join the saved times), running again, or the button **The setup matches the case folder: use it**. That button is for when you know nothing was changed. The workbench first compares the setup with what the case folder records about itself: steady or transient, kind of study, flow speed and direction, fluid, number of cells of the mesh, rotors, moving zones and their speeds, symmetry planes. If any of them differs it refuses and names it ("the case was made for a flow speed of 30 m/s but the setup says 60 m/s"). If nothing differs it still asks "Use the setup anyway?" (the answer is No by default) and says in plain words that results would be worked out with today's settings. It cannot check the other settings, so a wrong confirmation is possible: for that reason every result of an adopted study (reloaded, loaded from the case, or from a resume) carries a warning saying so, until the study is run again from the start. A resume still runs the case exactly as it is on disk, so confirming cannot damage it.

**Smooth videos (sliding-mesh runs).** The video can only be as smooth as the saved times are close in blade turn. The setting **Saved frame every** (degrees, default 8, expert) saves a time every that many degrees of turn of the fastest zone (a whole number of time steps; the end time is rounded to a saved time), so the blades, the vortex surfaces and the field of every picture are from the same instant. Set it to 0 to use **Saved time points** instead. Each saved time takes about 150 bytes per cell (3.6 million cells: 0.5 GB), and the setup checks show the number of saved times and the disk they take, with a warning (more than half the free space) or an error (more than 90 %). If the results of a finished run are saved too far apart (more than 12° of turn), the Results step says so and suggests the save interval; the Solve step then offers **Continue the run** to one more turn of the fastest zone, saving every 'Saved frame every' degrees, and keeps the times saved before. The MP4 button makes one picture per saved time whenever vortex surfaces or the volume are shown (they cannot turn with the blades); the in-between pictures (2× to 8×) are for views that follow the blades, such as the plain surface.

**Only open studies you trust.** A study folder is data the workbench reads, including its results file (`results/result.npz`, which uses Python's pickle format for a few lists). Open folders from people you do not know only after checking them.

**Claude Code in the study's folder.** Every study folder gets a `CLAUDE.md` (what the study is, where its files are, how to work in it) and a `.mcp.json` that registers the workbench's tools (`simwb`). The **Claude Code** button in the assistant's header opens Claude Code in a terminal window in that folder, so it knows the study and can drive it through the running workbench (approve the `simwb` server when it asks). The built-in assistant is told the folder too, and asks you where to save before it creates a study. Your own notes go in `CLAUDE.local.md` (the generated files are rewritten when the study is opened).

**Volume rendering, the Promo look and the orbit.** The volume (Results step, Volume rendering) now stops at the model in front of it: the cloud no longer buries the quadcopter and its blades. **Look** chooses *Smoke* (default: a pale cloud that thickens where the value is high, the thin outskirts show too) or *Colour map* (the colours of the scale). With the automatic scale the cloud shows only the flow faster than the free stream (the slipstream). **🎬 Promo look** (next to the MP4 button) sets a showcase video in one click: velocity on the model, vortex surfaces for the wake, smoke made from the vortex strength (Q-criterion, log scale: it has the structure of the flow, where a smoke of the speed is one smooth bubble), the whole model in 3D (no section; the mirrored half shown), no edge lines, the isometric view (no orbit) and 8 frames per second. For a steady result (no time slider) the Promo look and a **Turntable MP4** button appear instead: the camera turns once around the model (4 s). The orbit select turns the camera around the model while the video runs (0 to 360°; the view is restored afterwards). Press ● MP4 to record it: the video is one picture per saved time, so it is as long as the saved times allow (40 times at 8 fps: 5 s).

Using the computer fully: OpenFOAM is limited by memory bandwidth, not by the number of threads. On an 8-core / 16-thread laptop, 16 processes were no faster than 8 (38.8 s vs 37.7 s per time step on 2.74 million cells), so *Auto* uses the physical cores and the task manager shows about 50 % CPU. That is expected. What does save time:
- start a transient sliding-mesh run from a converged steady MRF run (**Start from**), so you need fewer revolutions;
- fewer PIMPLE outer correctors (Advanced): every one of them runs in each time step. 3 outer + 1 non-orthogonal corrector took 22.4 s per step instead of 37.7 s with 5 + 2 (default 5 + 2);
- fewer saved times (each one is written, joined and loaded).

Run-time guide measured on the author's machine (8 physical cores): a 2.74 million cell sliding-mesh DES run of 1.5 propeller revolutions took 11.6 hours of solver time. Plan accordingly; use fewer cells and shorter end times for first tries.

---

## 8. Step 5 - Results

### 8.0 The four tabs of the Results step

The panel is split into four tabs; the 3D view is the same in all of them.

| Tab | What it holds |
|---|---|
| **Summary** | The numbers of the study as tiles with a verdict (green / amber / red edge: safety factor, margin to an allowed temperature, a mode in the excitation range, heat balance) and a **show on model** link under the key values. Bars compare the parts (stress as a share of the yield strength, temperatures on one scale, forces and moments from a zero line); a stacked bar shows where the heat goes; the vibration study draws its natural frequencies as stems (height = share of the mass moving in the main direction, colour = direction, red band = excitation range, click a stem to show the mode); a flow study draws the solver residuals. Type an **Allowed temperature** to see the margin of every part (kept per study). Warnings are listed under the numbers. |
| **Display** | **Field**, **Colours** (colour map: turbo, viridis, plasma, inferno, cool-warm, rainbow, grayscale; reverse; flat colour bands; the range), the **distribution** of the field over the drawn surface (drag over it to set the colour range, double-click for automatic), **Model** (opacity, min / max labels, mirrored half), **Deformation** and the contour planes, vortex surfaces, volume, arrows and particles of a flow. |
| **Probe** | A **line plot**: the field along a straight line between two points you click on the model (or from the lowest to the highest value); move over the plot to see the point on the model; **Copy data** puts the table on the clipboard. The values pinned with **Probe** are listed here. |
| **Checks** | The mesh-sensitivity check, the flow-model comparison, the comparison with the previous run and the model assumptions. |

**Deformation.** A static study draws the part deformed under the load: the real deformation is far too small to see, so it is drawn larger (**Auto**: the largest movement is 8 % of the model size; **True size x1**, or a scale of your own); the note under the controls says by how much. The thin outline is the undeformed part. A mode shape swings at about 8 % of the model size times the amplitude. Every field of a static study (stress, safety factor, ...) is drawn on the deformed shape. The colour map is remembered in the browser.

### 8.1 Display

| Control | What it does |
|---|---|
| **Field** | Which result colours the surface (Pressure, Velocity, Temperature, y+, Q-criterion, ...). |
| Time bar | For transient results: slider, play. |
| **Min / Max / Automatic range** | The colour scale. Untick **Automatic range** to type your own. |
| **Opacity** | Makes the coloured surface and the **Section** see-through. |
| **Section** (above the 3D view) | Cut by an x, y or z plane and colour the cut. |
| Summary panels | Forces, drag, thrust, moments, temperatures, pressure drop, energy balance, convergence, assumptions. Numbers shown are those the solver produced. |
| **Export** (end of the panel) | **Report**: the study as one page (parts, boundary conditions, settings, mesh, key results, warnings, assumptions, the 3D view as it is now) - an HTML file that opens in any browser and prints to PDF. **CSV table**: one row per mesh node with its position and every result. **VTK (ParaView)** (Expert): the mesh with every field as a `.vtu` file for ParaView, PyVista or VisIt. For transient results the files hold the time shown. |

### 8.2 Vortex surfaces

Surfaces of equal Q-criterion wrap vortex cores (tip vortices, wakes).

| Control | Meaning |
|---|---|
| **Show** | On by default when the study has a moving zone. |
| **Colour by** | Any field painted on the vortex surface. |
| **Weaker vortices shown** (slider) | Move right to show weaker vortices too (more surfaces); left for only the strongest. The starting position comes from the propeller's rotation rate (or flow speed over reference length). |
| **Keep clear of walls [mm]** | Leaves out surface pieces this close to walls, where boundary-layer shear makes Q large. Default 2 % of the smallest rotating-zone radius. |
| **Smooth the surfaces** (tick box) and **Amount** (slider, 0-100, default 40) | Rounds off the roughness that follows the mesh cells, so the surfaces look like a liquid. It follows the slider live. **Untick the box** to see the surfaces exactly as the mesh made them. It is a display filter: the data is unchanged, and the line under the slider says so and gives the change of the surface area (and of the enclosed volume of a closed surface). The size is kept: a non-shrinking filter (windowed sinc, as in Taubin smoothing) moves the points, so a thin vortex does not get thinner (at the default, well under 1 % of its volume; at 100, a vortex only one and a half cells across loses about 2 %). The surface is drawn with smooth shading while it is smoothed. |
| **Smooth the data first** (0-3 passes, default 0) | Averages the data over the mesh cells before the surfaces are made. It removes single-cell lumps but also flattens thin vortices (one pass can take away more than a third of a thin vortex, three can remove it), so it is off by default; earlier versions had one pass on. Prefer the surface smoothing. While it is above 0 a line under the box says so, in the warning colour. |
| **Opacity** | Of the vortex surfaces. |

### 8.3 Contour planes

Cuts through the flow, coloured by a field, several at once, each with its own opacity.

1. Click **+ Add contour plane**.
2. Choose **Field** and **Plane** (x, y, z). Move **Position** with the slider (shown in mm).
3. Set **Opacity** (10-100 %). Use a low value to see vortex surfaces behind the plane.
4. **Remove** deletes a plane.

A plane does not clip the model: the whole model stays visible (see-through by default). To open the model at a plane, choose
**Model: Cut open at plane 1 (section view)** under the planes.

Contour planes are clean rectangles over the shared **Region** (below), with a hole where a body is, and they follow the time slider. Their colours run over the range that holds nearly all of the flow (1st to 99th percentile), so the extremes on the blade tips do not wash the picture out.

### 8.4 Volume rendering, vector arrows, animated particles

All three look at one shared box (the **Region**) resampled onto a regular grid. Set **Region** and **Grid** once.

| Control | Meaning |
|---|---|
| **Region** | Around the propeller (needs a moving zone) / Body and wake / Body only / Whole air box. |
| **Grid** | Grid points along the longest side: 48, 64, 96, 128, 160. More is finer and slower (a few seconds at 96 for a 2.7 million cell result). |

Every one of the three has a scale that is **auto** (the range of the data) or **custom** (type your own):

**Volume rendering** (the flow as a see-through cloud)

| Control | Meaning |
|---|---|
| **Show**, **Field** | Turn on, choose the field. Besides the result fields there is **Vorticity** (how strongly the air spins, worked out from the velocity). Q-criterion and vorticity are drawn on a log scale. |
| **Scale** auto / custom, **From - to** | Values mapped to the colours and the opacity ramp. Values below "From" are invisible, above "To" are the top colour/opacity. Automatic windows: velocity shows only air faster than 1.15 times the free stream (the slipstream); vorticity the strongest 1.3 decades; Q-criterion the strongest 3. Tip: vorticity shows the tip vortices best (try From 120, To 1500 1/s on a propeller at 8000 rpm). |
| **Density** | How opaque the cloud is. |

**Vector arrows** (arrows on a plane showing where the air goes)

| Control | Meaning |
|---|---|
| **Show**, **Plane**, **Position** | The plane the arrows sit on. |
| **Density** | Number of arrows across the region (4-80). |
| **Length** auto / custom [mm per m/s] | Automatic: a typical arrow is about one grid spacing long. Custom: millimetres of arrow per 1 m/s of speed. |
| **Scale** auto / custom | The speed range of the colours. |

**Animated particles** (dots carried by the flow, with tails)

| Control | Meaning |
|---|---|
| **Show** | Turn on. |
| **Start** | *In front of the propeller* (when there is a moving zone; the default): a disk a little wider than the propeller, just upstream, so the dots that pass through it show the slipstream. *Upstream sheet*: a sheet across the whole upstream end of the region. *Everywhere*: random points in the fluid. |
| **Tail** | Length of the fading tail behind each dot, in path steps (2-60). |
| **Particles** | Count (50-6000). |
| **Dot size** | In pixels. |
| **Speed** auto / custom [x playback] | Automatic: about 25 path steps per second. Custom: a multiplier, for example 0.2 for slow motion. |
| **Scale** auto / custom | The speed range of the colours. |

Particles follow the flow field at the time shown (a frozen snapshot): move the time slider to see the flow change. They show streamlines, not the true history of a transient flow. Particles stop where they leave the region or enter a body.

Limits: values inside bodies and outside the air volume are empty (grid points that no cell holds are left out; points in long thin far-field cells are searched among more candidates). Resampling uses the mesh; cells larger than the grid spacing are interpolated, finer ones are averaged.

### 8.5 Video

**Export MP4** (transient results) renders every saved time to a video with the colour scale and the time. The frames are drawn in the browser, so keep the tab visible while it renders.

Sliding-mesh results: the propeller turns in the view, when playing and in the video. This also applies to a section view, where the CAD parts in the rotating zone are turned to the angle of each time. The **1× / 2× / 4× / 8×** selector next to the frame rate adds in-between pictures per saved time: the propeller keeps turning smoothly between them, while the flow fields change at the saved times. Without them, a propeller that turns by close to a multiple of the blade spacing between saved times would look still, like a propeller filmed by a camera.

### 8.6 Trust the numbers

- **Run mesh-sensitivity check** (Results): the case is re-meshed one level finer in a copy of the study, re-solved and the key outputs compared. With three levels the program reports a grid convergence index (GCI).
- **Flow model comparison** (heatsinks): same mesh, other flow model; the conservative value is reported.
- The results summary states its assumptions. Read it.
- Coarse meshes are indicative only. A flow mesh gets prism layers on its walls (Mesh step, Wall layers; OpenFOAM must be installed); heat and structure meshes are tetrahedra only.

---

## 9. Typical recipes

### 9.1 Heat in a plate (built-in solver, minutes)

1. Start page -> **Heat transfer in solids** -> **Try example** (heated_plate.step).
2. Setup: put **Fixed temperature** or **Convection** on faces, set a **Heat load** on the part.
3. Mesh -> medium. Solve -> Run. Results -> Temperature.

### 9.2 Natural frequencies of a bracket

1. **Vibration (modal)** -> your STEP file.
2. Setup: **Fixed support** on the bolt-hole faces. Optional **Added mass** for a mounted part. Set the number of modes and the excitation range.
3. Mesh -> medium. Solve. Results: mode list (frequency, effective mass), mode shape animation, flagged modes.

### 9.3 Drag of a body in a stream

1. **External aerodynamics** -> your STEP file. Set speed, forward axis, up axis.
2. Mesh -> coarse. Solve (steady). Results: drag, CdA, pressure, velocity section.

### 9.4 A turning propeller with visible tip vortices

1. **External aerodynamics**, import the propeller (and frame).
2. Setup -> **Moving zones**: add a zone, **Fit around part**, set rpm, method **MRF**, Analysis steady, Flow model kOmegaSST. Mesh and solve. Check the propeller thrust against a bench value or an empirical estimate (section 14). This run is fast.
3. **Duplicate** the study. In the copy, set the method to **Sliding mesh**. Then Solve -> **Start from** = the steady study.
4. Settings: Analysis **transient**, Flow model **kOmegaSSTDDES** or kOmegaSST, End time 1.5-2 revolutions when started from the steady run, at least 3 from uniform flow (one revolution = 60 / rpm seconds). Small air box ("minimal" preset) for tests.
5. Mesh (before step 2): **+ Wake behind rotating zones** and **+ Tip-vortex rings**. Check the cell estimates. Generate.
6. Solve. Expect hours. Watch the Courant number in the log.
7. Results: **Vortex surfaces** (on by default), **Volume rendering** with Field = Velocity or Q-criterion, **Vector arrows**, **Animated particles**.

The wake in the results is only as long as the run: one revolution gives about one turn of tip vortex.

---

## 10. Claude assistant (chat)

- The **Assistant** tab answers and acts through workbench tools only: find faces, set materials and conditions, mesh, run, read results. It has no shell or file access.
- It needs a Claude login (same as Claude Code) or the `ANTHROPIC_API_KEY` variable. The panel header shows who is logged in.
- It costs money on your account for every message. Use it deliberately.
- **New** starts a new conversation.
- The model is set by `SIMWB_CLAUDE_MODEL`.
- It works in the folder the study is saved in.

Use the workbench from Claude Code or Claude Desktop (MCP):

```
claude mcp add simwb -- C:\path\to\Open_Mechanical_Simulation_Workbench\.venv\Scripts\python.exe -m simwb.mcp_server
```

---

## 11. Claude commands in the chat (log in, log out)

The chat box is also a small command line. A line that **starts with `/`** runs the Claude Code command line in the folder the study is saved in; its output appears in the conversation as a dark block. A line without `/` goes to the assistant (section 10).

| You type | What happens |
|---|---|
| `/login` (or the **Log in** button in the panel header) | Runs `claude auth login`. A URL is printed (clickable) and the browser opens. Follow it. If the program asks for a code, type the code in the chat box and press Enter: while a command runs, your next line goes to it. |
| `/logout` (or **Log out**) | Runs `claude auth logout`. |
| `/status` | Runs `claude auth status`. |
| `/version` | Runs `claude --version`. |
| `/ask question` | One plain question to Claude Code in the study folder (costs money). No study tools; shell and file-editing tools (Bash, Edit, Write, NotebookEdit, WebFetch) are switched off. For anything about the study, ask the assistant instead (no `/`). |
| `/help` | Lists these commands. |

Anything else after a `/` is answered with "Unknown command". The server also refuses any flag that is not on its list (for example `--dangerously-skip-permissions`, `--allowedTools`, `--mcp-config`, `--settings`, `--add-dir`): they would let a typed line (or a web page that tricked your browser) run commands on your computer.

Behaviour:

- Output appears line by line and is kept with the conversation. **Stop** ends a running command.
- After `/login` or `/logout` finishes, the assistant is reset and reconnects with the new login on your next message. The panel header shows who is logged in; **Log out** appears there while you are logged in.
- Only the `claude` command runs here, and only the commands above. It is not a general shell. A full interactive Claude Code session needs a normal terminal opened in the study folder.

---|---|
| `login` (or the **Log in** button) | Runs `claude auth login`. A URL is printed and the browser opens. Follow it. If the program asks for a code, type the code in the input line and press Enter. |
| `logout` (or **Log out**) | Runs `claude auth logout`. |
| `status` | Runs `claude auth status`. |
| `--help`, `--version`, `mcp list` | Passed to the `claude` command. A leading `claude` is allowed and ignored. |
| `-p "question"` | One question to Claude Code in the study folder (costs money). It runs without shell and file-editing tools (Bash, Edit, Write, NotebookEdit, WebFetch are switched off). |

Anything else is refused with a message. In particular flags such as `--dangerously-skip-permissions`, `--allowedTools`, `--mcp-config`, `--settings` and `--add-dir` are not accepted: they would let a typed line (or a web page that tricked your browser) run commands on your computer. For those, open a normal terminal in the study folder.

Behaviour:

- Output appears line by line. Web addresses in the output are clickable.
- While a command runs, anything you type goes to its input. **Stop** ends it.
- After `login` or `logout` finishes, the chat assistant is reset and reconnects with the new login on the next message. The header shows the new state. **Log out** also appears in the panel header while you are logged in.
- Only the `claude` command runs here, and only the commands in the table. It is not a general shell.
- A full interactive Claude Code session (typing `claude` alone) is not supported in this pane; open a normal terminal in the study folder for that. The path is shown at the top of the tab.

---

## 12. Files and folders

Inside a study folder:

| Path | Content |
|---|---|
| `project.json` | The whole study setup and state. Human-readable. |
| `geometry/` | Imported CAD and its tessellation. |
| `mesh/` | The mesh. |
| `case/` | The OpenFOAM case (logs: `log.pimpleFoam`, `log.simpleFoam`, `log.reconstructPar`, ...). |
| `results/result.npz` | The loaded results (can be over 1 GB for a transient run). |

Back up or move a study by copying its folder, then use **Open a saved study...** on the other computer.

---

## 13. Troubleshooting

| Symptom | Cause and action |
|---|---|
| "Please wait: a job is still running" | Cancel the running job in the status bar, or wait. |
| Mesh generation fails with "crossing surfaces" | The program locates the faces and retries with local refinement. If it still fails, check the CAD for overlapping parts. |
| Max non-orthogonality above 75 degrees | Refine near sharp edges or thin features. |
| Courant number above about 30 in a sliding-mesh run | Cells at blade roots are too small for the time step. Refine less there or use a smaller step. The run may still be stable (one 1.37 million cell run was at Courant 48 -> 38). |
| Run takes much longer than the estimate | The estimate is unreliable for sliding meshes. Read the **Solver log**. |
| The last step of a run (joining the results) is slow | It uses several processes now (at most 4, as each needs about 1.6 GB for a 2.7 million cell mesh). Free memory helps. |
| Vortex surfaces show nothing | Move **Weaker vortices shown** to the right. Reduce **Keep clear of walls**. Check the run was long enough for a wake to form. |
| Volume looks like a solid green block | The scale includes the free-stream value. Untick **auto** and set **From** above it. Lower **Density**. |
| Particles do not move | Check the **Speed** field is not 0. Particles that start in a body stop at once; use **Start = Everywhere** or a different **Region**. |
| Chat says you need to log in | Type `/login` in the chat box. |
| Disk full during results | A transient result needs several GB. Free space; the pieces from the parallel run are deleted after joining. |
| "Cannot save in ..." when creating a study | The folder is not writable or the drive is missing. Choose another. |

---

## 14. Limits (be honest about results)

- Flow meshes are tetrahedra with prism layers on the walls (when OpenFOAM is installed); heat and structure meshes are tetrahedra only. Wall-bounded results (drag, heat transfer) from coarse meshes are indicative.
- Actuator-disk rotors use an empirical in-plane drag model (k editable).
- Vibration: linear, no damping, no pre-stress, no contact.
- Radiation view factors are not in the built-in FEM.
- Incompressible flow only (below about 100 m/s).
- DES needs fine meshes and small time steps; a coarse mesh gives smooth, wrong-looking vortices.
- Volume, arrow and particle views are visualisations of the results; they do not add physics.


---

## 15. The desktop window in detail

The workbench is a desktop program. Sections 3 to 14 describe the five steps and their settings; this section describes the window around them: how to start it, its areas, saving and sharing a study, the results tools, the assistant, and what to do when something goes wrong.

### 15.1 Start it

| How | What happens |
|---|---|
| The icon **Mechanical Simulation Workbench** on the desktop or in the Start menu (made by the setup) | Opens the window; no console. |
| `run_workbench.bat` in the workbench folder (a source checkout) | The same. The first time it creates the Python environment and installs everything. |
| `.venv\Scripts\python -m simwb.qt` | The same, from a console. `--project <name>` opens a study at once; `--no-api` turns off the local control interface (see 15.8). |

To stop it click **Close** (top right): it stops every running solver.

### 15.2 The window

| Area | Where | What it does |
|---|---|---|
| Menus **File, View, Help** | top left | **File**: new, open, save, save as, export and import a study (15.3). **View**: dark, light or system theme (remembered), show or hide the docks. |
| **Simple / Expert** | top right | As in section 2. The choice is remembered. |
| **Jobs** | top right | The running jobs (meshing, solving ...), each with **Stop**. |
| **Claude** | top right | Shows or hides the assistant dock (15.6). |
| **Close** | top right | Stops all jobs and leaves. |
| Study switcher and **New** | below the menus | The open study; a list of the others; **New** goes back to the start screen. |
| Step bar | below that | **Geometry, Setup, Mesh, Solve, Results** with a tick for each finished step. |
| Outline | left, upper | The model tree. Click a node to select it. |
| Step panel | left, lower | The settings of the step. Results has the tabs **Summary, Display, Flow, Probe, Checks** (Flow only for flow and cooling studies). |
| 3D view | centre | Left button turns, the wheel zooms, the middle button (or Shift + left button) pans. Its bar: **Iso, X, Y, Z, Fit**, **Edges**, **Air box**, **Probe** and **Picture** (a PNG with the colour scale); a second bar, **Section**, on the Mesh and Results steps (15.8a). Click a face to select it; Ctrl-click adds. Resting the pointer on a face shows its number, kind, part, area and the condition it carries. The bar under the picture says what to click next. |
| Output | below the 3D view | **Messages** and **Solver log**. |
| Claude dock | right | The assistant. |
| Status bar | bottom | The study, its type, the mesh and solution state; while a job runs, what it is doing, a progress bar and **Cancel**. |
| Notices | top right, over the window | Small **toasts** ("Saved at 12:30", "Mesh ready", "Cannot do that: ...") that fade after a few seconds, stay while the pointer is on them and close with their x or Esc. They are also in the log. |

With no study open the window shows **What do you want to simulate?**: a card for every study type (what it does, which solver, **Use my
CAD file** or **Try example**), **Open a saved study...**, **Import a study file...** and the studies opened lately. Docks that are in the
way can be closed from the **View** menu and come back when a study opens.

Scrolling only scrolls. The mouse wheel or a touchpad over a **drop-down** never changes it: choose an entry by clicking it in the open list. A
**slider** or a **number field** takes the wheel only after you have clicked it. The window is sized to your screen and kept inside it.

### 15.3 Saving, opening and sharing a study

A study is written to its folder after every change, so nothing is lost when the window closes. The **File** menu:

| Entry | What it does |
|---|---|
| **New study** | Back to the start screen (the open study is saved first). |
| **Open study folder** | Any folder that holds a `project.json`. |
| **Open from the workspace**, **Open recent** | The studies of the workspace; the last ten opened. |
| **Save** | Writes the study now and says when. |
| **Save as** | A full copy in another folder (optionally with the solver case and results); the copy becomes the open study. |
| **Export study to one file** | One `.zip`, with or without the solver case and results (much smaller without). |
| **Import study from a file** | Unpacks an exported `.zip` into a new study. It refuses anything that is not a saved study, anything that would write outside its own folder, and anything unreasonably large; an AI agent's settings files inside the zip (`.claude`, `CLAUDE.md`, `.mcp.json`) are not unpacked. |

**Closing the workbench.** Closing the window (the X, Alt+F4, **File** > quit or the **Close** button) ends the program within a few seconds (up to about 10 seconds if a run does not stop at once). If a job is running you are asked once ("N job(s) are still running and will be stopped. Close anyway?"); a yes stops it the way **Cancel** does: a flow run keeps the times it saved and can be resumed when you open the study again. While the running work stops (and the assistant shuts down) a small notice "Closing: stopping the running work ..." shows, and the window title says "Closing"; a quick close shows nothing. The study is saved and the memory of the results is released before the program ends. If something still holds the program after the window is gone, it is ended 8 seconds later; what was in the way is written to the console and to the file `.ui/close.log` of the workspace. The file `.ui/control.json` of the workspace (port and token of the running window) is removed on closing, so a script that edits `project.json` can wait for that file to disappear (or for the process in it to end) before it touches the study.

To give a study to a colleague: **Export study to one file** (leave out the results to keep it small), send the `.zip`, and they use
**Import study from a file**.

### 15.4 Results

The tabs are the ones of section 8, with these differences and additions.

- **Display**: field, colour map (reverse, bands), range (auto or typed), deformation (animated, end state or off; scale; hidden when
  nothing deforms, as in flow and temperature results), edges, **Max and Min labels** (drag a label with the mouse to move it), and a
  **Section** for parts (heat and structural results): an axis and a position that cut the part open. A flow is cut with contour planes
  in the Flow tab instead.
- **Contour lines**: in Display, switch on **Contours**: a number of evenly spaced lines, extra values you type, and **Write the value on
  each line** (on by default). Lines are drawn over the colours: on the surface, on the section, and on every contour plane of a flow.
- **Flow tab** (flow and cooling studies): ways of looking at the flow, drawn together over the grey object. Choose the **Region** (around
  the object, or the whole air box) and the **Grid** (48, 64, 96 or 128 points across: finer shows more and takes longer).
  - **Contour planes**: cuts through the flow coloured by a field, as many as you like. A flow result opens with one (Velocity, across Y,
    in the middle). **+ Add contour plane** adds another; each has its **Field**, **Plane across** X, Y or Z, a **Position** slider that
    moves it along that axis (its place is written under it in mm; the plane is cut again when you let go), an **Opacity** slider (lower
    it to see the object and the other tools behind) and **Remove**. A plane never cuts the object by itself: the object stays whole.
    **Model** (under the planes) chooses how it is drawn: **Whole model, see-through** (the default, so the part of a plane behind the
    object still shows), **Whole model, solid**, or **Cut open at plane 1 (section view)**, which opens the object at the first plane and
    shows it straight on. The colours run over the range that holds nearly all of the flow (1st to 99th percentile).
  - **Vortex surfaces** (turbulence): surfaces of equal Q-criterion (rotation beats strain). The slider **Weaker vortices shown** works
    towards the right (the Q value under it falls): at the strong end the surfaces only wrap the wall layer on the object; towards the
    weak end the tip vortices, the leading-edge vortex and the wake appear, and also more noise. **Coloured by** picks the field painted on
    them; **Keep clear of walls** hides the surface near the object; **Smooth the surfaces** and its **Amount** round it (untick the box for the surface as the mesh made it; the legend says "(smoothed)"). For a wake use the whole air box, a weak
    setting and turn the view (**Z** for the top, **-X** from behind).
  - **Volume rendering**: the whole flow as a see-through cloud (field, density, colour map or smoke).
  - **Arrows** on a plane (axis, position, number across) and **Particle paths** (number, where they start - the upstream face, anywhere, or **a disk you place**, moved with its handle - steps). With **Animate the
    particles** (on by default) dots run along the paths, as the air moves; **Dot speed** sets how fast.
  - **Automatic colour range**: every plane, the cloud, the arrows and the paths have their own. Untick it and type **from** and **to**
    to fix the colours of that tool; while it is ticked the boxes show the range used.
  - A Region of the whole air box with a fine grid needs more memory; if the window is slow, go back to a coarser grid.
- **Handles in the 3D view** (Results step): everything that has a position has a handle you can drag, and the numbers follow it (and it follows
  the numbers). A **plane** (a contour plane, the plane of the arrows, the section of a part) is a coloured sheet with a ball and an arrow in its
  middle: drag the ball or the arrow along the plane's axis; **Shift** snaps to round steps; the sheet stops at the edge of the region. Looked at
  straight along its axis, a plane is dragged up and down the screen instead. A **point** is a ball: a pinned value or an end of the line plot
  slides over the model under the pointer and reads its value there; the disk the particles start on has arrows (red x, green y, blue z) as
  well as a ball. While you drag, only the handle, the numbers and the labels move; the flow is cut again, the paths drawn again and the line
  plotted again when you let go. The handles of a tab are shown while that tab is shown:

  | Control | Tab | Handle | Numbers |
  |---|---|---|---|
  | Contour plane (each) | Flow | sheet over the region | slider, **Position (mm)**, the label under it |
  | Plane of the vector arrows | Flow | sheet | slider, **Position (mm)** |
  | Start of the particle paths: **A disk you place** | Flow | disk with a ball and x, y, z arrows | **Disk centre X, Y, Z**, **Disk radius** (mm) |
  | Section of a part | Display (and the bar over the 3D view) | sheet over the part | slider, **Position (mm)**, the bar's value |
  | Pinned value (each) | Probe | ball on the model | **X, Y, Z** (mm) under each pin: type a point and it goes to the nearest place on the model |
  | Ends A and B of the line plot | Probe | ball on the model | **X, Y, Z** of each end |

  The refinement regions of the Mesh step and the rotating zones of the Setup step use the same handles, drawn the same way: **three arrows** (red x, green y,
  blue z) and a **ball** move something, **rings** turn it (Shift: 5 degree steps), and a **square-ended arrow** on a face resizes it. The pointer becomes a hand over a
  handle, the handle lights up, and a line in the corner of the view says what the drag does (for example *Turning about z: +30.0°*). In every step the numbers and
  the handles follow each other, and what is expensive waits until you let go. **Esc during a drag** puts the handle back where it was when you pressed the mouse
  (in every step: the shared handles, the Geometry step's rings and arrows and a symmetry plane's arrow); a second Esc presses the **Cancel** of the bar under the 3D view. Where two handles lie on one line, a move arrow is taken along its shaft and a
  resize arrow at its square end; a ring is taken anywhere on it.
- **Probe**: pin values by clicking the coloured result; line plot between two points or from the minimum to the maximum.
- **Time bar** (Display, under the field; transient results): the saved time shown, a slider over the saved times and **▶ Play** /
  **❚❚ Pause**. Pinned values are cleared when the time changes; the line plot follows the time. A note says when the field hardly
  changes over the saved times (the animation would look still).
- **Video**: **● MP4** (transient) saves every saved time as drawn now, with the colour legend and the time on each picture; on a steady
  result **● Turntable MP4** turns the camera around the model. Choose the **frames per second** and the camera **orbit** (degrees over
  the video) beside the button; the button counts the pictures while it records. **The current window is what is recorded** (its 3D view,
  picture by picture), so keep it open: a notice at the start says so and asks you **not to minimise it**; while it runs a bar under the 3D
  view reads "● Recording ... (picture 3 of 40)" with a **Stop recording** button (Esc does the same), and the window title starts with
  "● Recording". Stop keeps the pictures made so far as the video. If the window is minimised anyway, the recording **pauses** and a warning
  says so; it goes on from the same picture by itself when you restore the window (nothing is lost, the pictures are made one at a time).
  Only one video can be recorded at a time.
- **Distribution** (Display, under the range): how much of the surface has each value, in the colours of the map, with the mean. Drag
  across the bars to set the colour range to that part; double-click to go back to the automatic range. The line under it says how
  much of the surface is below and above the range.
- **Show in the view** (Display, under Model; external flow results): one tick box per geometry the view can draw, so that with a moving
  (sliding) mesh the part that moves can be looked at alone. The list has the **Object** (the airframe), every surface that turns with a
  moving zone (named after the zone, marked "(moving)", e.g. the propeller), any surface with a condition of its own, the **Air box**
  (the same switch as the Air box button of the 3D view; off by default), and the **outlines** of the **moving zones** and of the
  **refinement regions** (off by default). Everything that has a surface is ticked at first. **Only what moves** hides all but the
  moving parts and shows their zone outline in one click; **Show all** ticks every surface again. The ticks work on the time bar and
  while it plays or records: the mesh moves, the choice stays. They are remembered for each study while the program runs. The control
  API has `results.visibility`.
- **Model**: **Opacity** (see-through surfaces, to look behind them), **Hide the air** (cooling studies) and one tick box per part when
  the result has several (untick one to hide it). **Deformation** adds **Outline of the undeformed part** (on by default) and a line
  that says how much the deformation is exaggerated.
- **Summary charts**: natural frequencies as stems (click a stem or a mode row to show it; each row gives the effective mass x / y / z),
  the shake-test sweep (stress against frequency, both logarithmic, peaks dotted), flutter frequency and damping against the air speed
  (the critical speed dashed), solver residuals for a flow, and forces or temperatures over time for transient runs. **Animate** under
  the modes (and **Animate deformation** for a static result) is the same switch as the Display tab's.
- **Temperatures and forces**: type an **Allowed temperature**: every part's bar shows its margin (or how far it is over) and the
  Max temperature tile says over or under. **Where the heat goes** splits the heat in one bar. Flow results show the forces on the body
  and its moments as bars from a centre line; a static result shows the force at each support.
- **Checks**: the mesh-sensitivity table (each key result on the coarse and finer mesh, the change, and for **Three-level check (GCI)**
  the grid-convergence index), the verdict, **Run mesh-sensitivity check** / **Check again**, the flow-model comparison of cooling
  studies, the previous run, and the model assumptions. A button that cannot be used says why under it and in its tooltip.
- **Export**: report (HTML), table (CSV) and VTK for ParaView, in the Results step.
- The camera fits what you look at: switching a section on or off, or choosing another view, re-fits the view to the object or the cut.

Limits of the views: contour lines and their labels do not follow an animated deformation; labels can be hidden behind a surface; the
force coefficients of a flow study use the frontal area of the object, which is not the wing area of a wing (work from the forces).

### 15.4a The Geometry step

The Geometry step of the window has everything of section 4 and more.

**The gizmo (turn and move).** Round the model the 3D view shows one gizmo: three **rings** (x red, y green, z blue) that turn it, three
**arrows** that move it along an axis and a white **ball** that moves it freely in the plane of the view. The pointer becomes a hand over a
handle and the handle lights up. Drag a ring to turn, an arrow to move, the ball to move freely; the model follows live in the view, with
the angle or the distance written in the corner and in the panel. Hold **Shift** to snap to 5° steps and to round distances. Nothing is
rebuilt yet: press **Apply** to rebuild the geometry turned and moved like the preview, **Cancel** to throw the preview away, or **Reset
orientation** to go back to the place the file had. If you go to another step with a preview, it is applied. The **X/Y/Z -90° / +90°**
buttons, the **Turn by this angle** field and the **Move by** fields (dx, dy, dz in mm) change the same preview, and turns and moves add up.
**Hide gizmo** takes the handles out of the way.

**One body or all.** The gizmo acts on **the whole model** by default (**Move all**). Press **Move this body** on a body's card in the
list to turn and move just that body, leaving the others where they are; it is stored as a CAD edit (*rotate*, *move*), so it can be removed
again, and it needs a STEP file. Moving the whole model also moves its supports, loads and zones with it.

**Align a face to a plane.** Press **Align a face to a plane...**: a card appears over the top of the 3D view and guides you in three steps - **1 Click the surface you want to align**, **2 Click the side, plane or face to align it with** (any face of the model, or a wall of the air volume, the control volume; or press **Coordinate plane...**, **Air volume wall...** or **Another face - just click it** in the card), **3 Check the preview, then Apply**. The current step is big, finished steps carry a tick and what you chose, and **Back**, **Cancel** and **Apply** are in the card. Details: click a face of the model, flat or curved. The view is fitted to the
model, a bar under the 3D view says what to click, and the top of the panel shows the current choice in one line ("Face 7 → Air volume:
top (+z), facing the wall") with **Apply** and **Cancel**. For a curved face (a wing, a smooth body, an STL mesh) the **average
direction** of the face is used; press **Normal at the click** to use the direction of the surface at the point you clicked instead. A
yellow arrow shows where the face points now, the face is highlighted, and a message box with an icon says what was picked.
The next click chooses the plane to lay it against, click **a face of any part** or **a wall of the air volume**
(the air volume is shown translucent while you pick, the camera stays on the model, and the face under the pointer is named next to the
cursor; turn the view to reach another wall), and the model turns in the view at once. Or choose a coordinate plane (**facing +z** ...:
the nearest one is marked with a star), or choose by name in the panel: a **wall of the air volume** (upstream (inlet), downstream
(outlet), top, bottom, the sides, or a cylinder's ends and wall), or a **body** and then one of its faces. **Same direction / Face to
face** (for an air-volume wall **facing the wall / facing away**) say which way round. Press **Apply** to rebuild the geometry.

**CAD edits.** The list shows every edit made to the model (also by the assistant), each with a **✕** to remove it, **Undo last** and
**Remove all**. **Add an edit** opens a form for every kind: boxes, cylinders, spheres, cones, prisms, revolved shapes, lofts, cuts,
joins, holes, fillets, chamfers, moves, turns, scales, mirrors, linear and polar patterns, cuts at a plane, deleting and renaming.
Outlines are typed as lists of points. An edit that does not work is refused and the model stays as it was. STL and OBJ models cannot be
edited. Faces get new numbers after an edit: check the supports and loads that sit on edited faces.

**Air volume** (flow and cooling studies). Choose the **shape** (box, C-shape, cylinder along the flow), type the sizes in mm and press
**Apply**; **Fit to parts** sizes it automatically; **Show all** zooms out to it. A cooling study chooses whether a body of the file is the
air or an air box is made. A flow study has the **automatic size** in spans of the parts: **Minimal**, **Compact**, **Standard**, **Large**
or your own numbers and **Fit with these**. The air volume's orange drag handles are not in the window yet: sizes are typed. **Parts**
lists the parts of a flow study with **Hide / Show**.
### 15.4b Setup

The Setup step has the blocks of section 5 in the same order, and the conditions are drawn on the model (each in its colour, with its
name and value, arrows for loads, flows, pressures and heat; the free stream, rotors, rotating zones, heat sources, symmetry planes and
gravity too). A line at the top of the panel says what the last action did, or why it could not be done.

- **About this simulation** says which **Solver** runs the study. In a structural or vibration study, choose built-in or CalculiX under
  **Advanced settings** (Expert mode; the block is open).
- **Boundary conditions** says what the **Other faces** get. In a cooling study with a fan, **✨ Auto-detect inlet & outlet** puts the inlet and
  outlet on the two end faces of the air volume. The condition of a symmetry plane is listed greyed out: change it with its plane.
- **Symmetry planes**: choose which way the plane cuts (**YZ plane, across x**, **XZ plane, across y**, **XY plane, across z**, or **Through a flat face**,
  then click the face). The plane is drawn in the 3D view at once: an amber sheet with an arrow into the side that is kept. Move it by
  dragging the arrow (hold Shift for round steps) or by typing the **Position [mm]** (for a face: the **Offset from the face**, positive into
  the part). **⇄ Flip** keeps the other side. Nothing is rebuilt until you press **Apply**. Each plane that is set has its position,
  **⇄ Flip**, **✕** (remove: the removed side comes back) and, in an external flow, **Also delete the model beyond the plane**; each
  change rebuilds the geometry (a job), and the buttons wait while it runs.
- **Rotors** (drone): **✨ Detect on motor tops**, **＋ Add rotor**, and for each rotor its **Radius**, **Thrust** (empty = automatic: the rotors
  together carry the weight), **⌖ Centre on a cylinder or circular edge…** (press it, then click a motor can or a round edge) and **✕**.
  **Reset thrusts to automatic** empties every thrust. The weight and the total thrust are written under the flow condition.
- **Moving zones** (flow studies): **＋ Add rotating zone**; each zone has its name, **Method** (MRF steady, or sliding mesh in a transient external
  flow), speed, axis, centre, radius, length, **Cell size inside** (empty = automatic), **Fit around part** and **⌖ Centre on a cylinder or circular
  edge…**. If the picked axis is not along x, y or z, the panel offers **Turn the model** (or **Leave it**).
  **Each zone is drawn in the 3D view** (Setup and Mesh) as a translucent cylinder in **its own colour** (purple, pink, green, amber for the first four zones: the card's swatch, the tints and the Mesh colouring by the cells that turn use the same) with its name, speed and method on it; a sliding-mesh zone has a heavier
  outline, because its boundary is the sliding interface. A tick on the two flat faces shows how far it has turned. Under the zone's numbers a line says which parts
  are **inside** (they turn with it), which are **outside**, and warns in the warning colour about a part the zone's boundary cuts through (the mesh cannot
  match there: make the zone larger or smaller). **Preview the turn** (a slider over one revolution, and ▶) turns a purple ghost of what is inside the zone;
  with a transient analysis it says how many saved frames the angle is (the **Saved frame every** setting). Nothing is stored; leaving the step puts it away.
  **Parts in the 3D view**: the parts that are inside a zone are tinted in that zone's colour, and a part its boundary cuts through in the warning amber (the same
  words as the line on the card; a part with less than 1 % of its surface in the zone counts as outside and the card says *less than 1 %*). The tint is in Setup
  and in Mesh, under the colour of a boundary condition.
  **Drag a zone in the 3D view**: the zone whose card you used last shows its handles (its **Show handles in the 3D view** box hides them; a new zone shows them at
  once). The three arrows and the ball move it, a square-ended arrow on each flat face changes its **Length along the axis**, the one on its side its **Radius**, and the
  two rings turn its **Axis** a quarter turn at a time (a zone has to lie along x, y or z: the ring jumps from one axis to the next, the line in the corner says which
  axis it shows and when it will jump, and the card's **Axis** follows; for any other direction use **Centre on a round edge** and **Turn the model**). The **Centre**,
  **Radius**, **Length** and **Axis** in the card follow the handles and the handles follow the numbers. **Like a refinement region, a dragged zone is only previewed**:
  the card shows **Apply** and **Cancel** side by side (and the bar under the 3D view has them too), a number typed meanwhile changes the preview, and nothing is
  stored - and the mesh does not become out of date - until you press **Apply**. **Esc** cancels a drag; while the preview overlaps another zone the card, the bar and the line in the corner say which one ("Apply will be refused"), and if you press **Apply** anyway the
  status line keeps the reason and the preview stays for you to change. The bar under the 3D view is one short line (*Not stored: Apply / Cancel*) while something is pending and does not change
  height during a drag. **The smallest a zone can be**: a few cells of its **Cell size inside** (4 for the radius, 2 for the length) or, without one, 2 %
  (radius) and 1 % (length) of the model; a drag stops there, a typed smaller radius or length is raised to it and a line says so in plain words (only the number you typed is changed: a speed, a method or a cell size never changes the size of a zone, and a zone that
  is already smaller than this in an older study is never made larger). A **Cell size inside** that the zone is too small for is refused with *This cell size needs a zone of at least X mm* and the zone is left
  as it is. The engine refuses a zone smaller than half of the least. The handles are put away while you are choosing a centre of rotation or the faces of a condition, so that a click reaches the model.
- The settings are headed (**Flow condition**, **Loads**, **Modes and vibration source** ...). A transient external flow with
  sliding zones shows a **Suggested end time** with **Use it**.

### 15.5 The mesh

The Mesh step has the same settings as section 6, in the same order: **Mesh type** (tetrahedral or hex-dominant, flow studies),
**Resolution**, then the folded advanced options - **Advanced sizes and defeaturing**, **Part sizing**, **Refinement regions** and **Wall
layers** - and **Generate mesh**.

- **Part sizing**: choose the part in the drop-down (in a flow study that is the object: the parts of your STEP file by name) and press
  **＋ Part sizing**, or press **Pick a part in the 3D view** and click the part: all its faces get a cell size, which you then change in
  the list. The faces that have a part sizing are tinted in the 3D view. A whole part has one sizing; change its size instead of adding another.
  To size only some faces of a part, press **Pick faces in the 3D view** and click the faces one by one (a second click takes a face out again; the air volume cannot be picked),
  then press **＋ Size the selected faces**. The picking stays on: pick the next group and add it, as many sizings as you need, each with its own size. The list names each
  one by its part and faces; **◎** shows its faces in the 3D view and **✕** removes it. The same faces cannot have two sizings.
- **Edge refinement** (flow studies, tetrahedral mesh; Expert mode): finer cells along the sharp edges that cross the flow - the leading and trailing edge of a wing, the corner of a box -
  where the flow changes fastest. Edges along the flow are left alone. The cells at the edge are the wall cells divided by the number in **edge cell size = wall cell size /** (2 to 20, default 6) and grow
  back to the wall size away from the edge. **Refine sharp edges** switches it on; **refine edges sharper than** is the angle between the two surfaces at the edge (180° smooth, 90° the corner of a box,
  a few degrees a knife edge; edges below the angle are refined, default 140°); **most extra cells (cap)** limits the cost: the edge cells are made coarser until the estimate fits under it (default 150,000), so the option is kept within the cap (to the accuracy of the estimate, see below). Open the group and the program looks for the sharp edges in the geometry (a second or two, once per geometry); a line under the fields then says, live, about how many extra cells
  the settings add, how many sharp edges and how long they are, and the edge and wall cell sizes in mm - also while the option is off ("Off. Switched on it would: ..."). **Show the sharp edges in the 3D view** draws the edges that will be refined in orange.
  The estimate is rough: against real meshes (the mesh with the option minus the same mesh without it, five example geometries, 19 meshes) it was within 19 %, so the line says "within about 25 %". The cap limits the estimate, so a mesh can come out a few percent over it; the
  line says what the cap did ("edge cells 3.2x finer than the wall cells instead of 12x"). After meshing the group says what the mesher did ("Last mesh: 1 sharp edge refined, edge cells 4.17 mm = wall cells (25 mm) / 6"), then counts, in the mesh itself, how many extra cells the edges cost - in the warning colour when that is over the cap -
  and what the mesher had estimated. When nothing was refined it says why (no sharp edge across the flow, the cap too low, a smallest cell size set by hand). The size of the cells is no longer taken over from the edges onto the whole faces next to them (fine cells then covered whole faces), so with Smart sizing off the whole mesh can be smaller than
  without edge refinement (the old sizing spreads the sizes of curved edges); the estimate and the cap are for the edge zone.
  **Smart sizing** switches the refinement on by itself at 4×; the box shows it as on, and switching it off there keeps it off. With a **smallest cell** set in Advanced sizes and Smart sizing off the mesher keeps to the size you set and does not refine edges (the group says so).
  In other kinds of study and for the hex-dominant mesh the group is there but greyed, with the reason.
- **Wall layers** (flow studies; the block is open): the number of layers, the growth ratio, the first layer (0 = automatic) and the
  **target y+** of the first cell, **measured at its centre** as OpenFOAM does (0 = automatic: about 30 for the wall-function models, which want 30 or more; 5 to 30 is the buffer
  layer where they are least accurate; a target near 1 resolves the wall itself and needs many more layers). Beta 3 and earlier sized the first layer so that the target was
  reached at its outer face, so the cells came out at half the y+ (a mean of 15.8 on a wing run set up for 30). A line under the fields says what they come to - the first and the total thickness, the y+ it aims at and how many
  layers lie inside the boundary layer - and **Show the layers in the 3D view** draws a translucent shell round the object as thick as
  all the layers together (an approximation at sharp corners; it is the preview before the mesh exists).
  - **Tolerant layers**: where the cells are small and the wall is sharp (a leading edge), the layer step removes layers that would be badly shaped. With **Tolerant layers** on its limits are looser, so more of
    the layers stay there; they can be less smooth, so look at the mesh quality. Off is the default. After meshing, a line says how many layers were made, on what share of the wall faces, and with which limits; if you
    change the switch afterwards it says the layers are out of date (**Make the layers again**). Not for the hex-dominant mesh, which makes its layers together with the mesh.
  - **The layers are made with the mesh.** When you press **Generate mesh** in a flow study (tetrahedral mesh, OpenFOAM installed), the
    program adds the layers with OpenFOAM's snappyHexMesh in its layer-only mode - the same step the run would do - so **the mesh you see,
    count and check is the mesh the solver runs**. Without OpenFOAM, or when the setup is not complete, the card says why and the run adds
    the layers itself. A hex-dominant mesh makes its layers together with the mesh.
  - **Wall layers made** (under the statistics): how many layers were made, on what share of the wall faces, the mesh the solver runs (its
    cells = tetrahedra + layer cells) and a table per surface (layers made of the layers asked for - an average over the faces, so 4.6 of 5
    means that some faces lost one - the first and the total thickness, and how much of the planned thickness it got). When less than 98 % of
    the wall got layers the card says why. The usual cause is a thin part: **a layer grows from each side of it, so all the layers together
    must take less than about half its thickness**, or they run into each other and OpenFOAM removes them. The card (and the summary line under
    the layer fields, before you mesh) names how many layers fit. Example, measured on the example wing (thinnest part 6.3 mm, first layer
    0.85 mm, growth 1.2): 5 layers kept 70 % of the wall, 3 layers 99 %, 2 layers 100 %. A finer mesh does not help, it makes this worse
    (the layers stay as thick while the part is resolved more finely). A warning also appears when the non-orthogonality is above 70°.
  - **Colour the mesh by Wall layers** (see below) and **Zoom to the layers**: the mesh is cut through the middle of the object and the
    view goes to the edge of the object, where each layer is a band from warm (at the wall) to cool (the outermost layer) round the section.
  - **Make the layers again** repeats only the layer step on the mesh that is there, after you change the layer settings (the tetrahedra
    stay as they are). Until you do, the card says that the layer settings changed since the layers were made; the run always uses the
    settings as they are now.
- **Refinement regions are drawn in the 3D view** before you mesh: each box or cylinder (a ring too) as a translucent body with an outline, in
  the colour of its swatch in the list, with its name and cell size written on it; the region you edit is drawn stronger. **◎** zooms to a
  region, and **Show the regions in the 3D view** switches the drawing off. Edit the numbers and the drawing follows.
- **Move, turn and resize a region**: press **Move / turn…** on a region's card. The region gets the handles of the 3D view (the same as in the Results step and for
  the rotating zones): **three arrows** (red x, green y, blue z) and a white **ball** at its middle move it, three **rings** (about x, y, z) turn it, and a
  **square-ended arrow** on each face resizes it (a box has six, a cylinder one at each end and one for the radius): the face moves and the opposite one
  stays. Hold **Shift** for round steps (5 degrees, round millimetres); **Esc** cancels a drag. Or type **Move by** (mm), **Turn about** (degrees), **Size** (mm, in the
  region's own axes) or **Scale all** (a percentage of the stored region): the numbers and the handles follow each other, and a line in the corner of the view says what
  the drag does. The region is drawn as it would be; nothing is stored and nothing is meshed until you press **Apply**. **Apply**, **Reset** and **Cancel** are at the top
  of the session (before the numbers), and **Apply** and **Cancel** are also in the bar under the 3D view, so they can be pressed without scrolling. A box keeps its turn
  (a note on its card says so; its corner numbers are the box before the turn); a cylinder moves its end points.
  A turned box is meshed as the turned box. **Tetrahedral mesh**: a size field in the box's own axes, checked on a real (tiny) Gmsh mesh.
  **Hex-dominant mesh**: the turned box is written to snappyHexMesh as a closed surface of its own, so the fine cells fill the box and not the larger box round it;
  this has been checked as written files (the surface is closed, faces outward, the volume of the box, and the `snappyHexMeshDict` names it) but **not yet by a real
  snappyHexMesh run** (OpenFOAM was not available where it was built).
  **Regions follow the model**: turn or move the whole model in the Geometry step and the refinement regions turn and move with it, as the rotating zones do (moving
  one body only leaves them where they are).
  **Line up with a face…** (in the same session) turns the region so that the side of it that already points closest to a face is parallel to that face
  (a cylinder: its axis): click the button, then click a face of the model or a wall of the air volume (the handles are put away while you click, so that
  they never take the click). Tick **and put its middle on the face** to move the region there too. It is only previewed: the numbers show the turn, and
  **Apply** stores it.
- **Refinement regions**: **＋ Box** and **＋ Cylinder** as before; each region now shows roughly how many cells it alone makes (in the
  warning colour above three million), and a box has **Fit around part…** to put it round a part. In a flow study with rotating zones,
  **＋ Wake behind rotating zones** adds a cylinder of zone-size cells two diameters downstream of each zone, and **＋ Tip-vortex rings**
  adds a ring of fine cells along the path of the blade tips. Pressed again, they replace the earlier ones. Without a rotating zone the
  two buttons are greyed and their tooltip says to add one in Setup.
- **Statistics**: a table with the cells, nodes, near-wall and bulk size, minimum and mean quality, poor cells, the largest
  non-orthogonality and skewness (when known), the defeatured faces, and for a hex-dominant mesh the share of hexahedra, prisms and
  polyhedra, the largest aspect ratio and the share of the wall that got layers; with wall layers also the cells with the layers, the
  layers made, the first and all layers and the non-orthogonality and skewness of the mesh with them; then the time it took.
- **Colour the mesh by** quality, **Cell size**, **Region**, **Aspect ratio**, **Size jump** (the largest size ratio to a neighbour),
  **Cell volume**, **Rotating zones** (flow studies with a rotating zone in a mesh made after it was added: the cells that turn with each zone in its
  colour, the others grey; a cut through the zone shows where its boundary, the sliding interface, lies in the mesh). Under the colour buttons, **Show the zone
  boundaries** draws the meshed boundary of each zone as a pink surface (for a sliding-mesh zone the **sliding interface**, where the turning cells meet the fixed ones;
  it should be a clean cylinder) and says how many faces it has and its area, for example *prop: sliding interface, 4,120 faces, 28.3 cm²* or **Wall layers** (flow studies with layers: the mesh the solver runs, each layer in its own colour and the tetrahedra
  in grey; choosing it opens a cut through the object if none is open). A hex-dominant mesh is shown by quality and region only (the other buttons are greyed and say why). Under the buttons,
  the **cell budget** shows, for each band of cell sizes, the share of the cells and of the volume, with remarks when a size jump or a
  fine region uses too many cells.
- Under the quality check (**Show cells below**, **Zoom to worst**) the verdict lists, in words, what the checks cover: poorly
  shaped cells, how many cells lie across the thinnest part and the narrowest air gap, the estimated wall y+ of a flow (from the first
  layer when there are layers, else from the surface cells), and a coarse CFD mesh. **Next: solve →** goes on.
- **Mesh sensitivity** (once the study is solved and converged): **Run mesh-sensitivity check** re-meshes one preset finer, solves again
  and compares the key results; **Three-level check (GCI)** uses two finer meshes and gives the grid-convergence index of each result.
  The last check's table and verdict are shown here (and on the Results checks).

### 15.5a Solving

The Solve step:

- **Solver**: which solver runs and whether its program is found. If OpenFOAM (flow studies) or CalculiX (when chosen for a structural
  study) is missing, **Install OpenFOAM** / **Install CalculiX** download it into the workbench folder with a progress bar and **Cancel
  installation**; **Other ways to install** lists the alternatives and **Re-check** looks again. Without OpenFOAM the main button becomes
  **Write OpenFOAM case (OpenFOAM not installed)**.
- Flow studies: **Max iterations** (steady runs), **CPU cores** (**Auto (n)** or a number; one core where parallel runs are not set up),
  **Start from** (uniform flow, or the solution of another study of the same kind, e.g. a steady run before a transient propeller run),
  and the **Expected run time**.
- While a run is going: **Stop and keep results** (flow runs: the solver writes the current solution) and **Cancel**. The progress shows
  the latest residuals and a residual chart, as well as the solver's messages.
- After a run: **Continue the last run for N more iterations** (steady), **Continue the run to … s, saving every … s** (a finished transient
  run; the suggestion is one more turn of the fastest rotating zone) or **Resume** (a stopped transient run).
- **Write OpenFOAM case only** and **Save case (.zip)…** (the case folder as a zip file).
- **Advanced (OpenFOAM)** (Expert mode): the numerics (convection scheme, correctors, relaxation, Courant number, solver tolerance) with
  **Reset all to defaults**, and the **Case files**: **Show case files** writes the case as the next run would and lists its dictionaries;
  pressing one opens it in an editor under the list with **Save** (your version is used on every run, marked ✎), **Revert to generated**
  and **Close**.

### 15.6 The Claude assistant in the window

Same assistant as section 10: it sets up, meshes, runs and explains, with the same tools. It has no shell and no access to your files.
Whatever it writes or reads goes through checks: it saves files only inside the workspace or the temporary folder, never replaces an
existing file or one of the workbench's own files, never uses a network path or a link, and reads only CAD files and exported studies by
their full path (starting with a drive letter). If it says it may not use a path, give it a place inside the workspace, and move the file
yourself afterwards.

- **Stop** (next to **Send**) is always there. It is lit while Claude is answering or a command runs, and stops it; when nothing runs its
  tooltip says so. While Claude answers, **Send** waits.
- **Claude Code** opens Claude Code in a terminal window, in the study's folder (it finds a `CLAUDE.md` and the workbench tools there).
  It needs a study; without one the button says so. Only you use this button: the built-in assistant cannot press it.
- **The `/` commands.** A line in the message box that starts with `/` runs the Claude command line in the study's folder, and its output
  appears in the conversation as a block: `/login` (or **Log in**), `/logout` (or **Log out**), `/status`, `/ask question`, `/version`
  and `/help`. While a command waits for a code, the next line you type goes to it. Anything without `/` goes to Claude. Section 11
  lists the commands in detail.
- **The conversation is kept for each study** and is there again when you switch studies or restart the window. **New chat** forgets it.
  Claude's answer in one study stays in that study if you switch while it is working.
- **Ctrl+K** puts the cursor in the message box (and shows the dock if it was hidden).

### 15.7 Not in the desktop beta yet

What is still missing in the window is small:

- **Dragging in the 3D view**: the orange handles of the air volume and a rotor disc cannot be dragged yet. Type the values in the panel instead.
  (A symmetry plane, a refinement region, a rotating zone and everything in the Results step have handles.)
- **Symbols on the model**: block, roller and plate symbols for each kind of condition. The desktop window shows each
  condition by its tint, its label and arrows.
- **A per-part gizmo preview inside a flow study**: the model turns and moves as a whole there (a single part can be moved in heat,
  structural and vibration studies from a STEP file).
- **Results extras**: the promo look, the section-sweep video, the mirrored half of a symmetric model, the arrow length scale and the
  particle dot size and tail.
`docs/QT_PARITY.md` lists every control of the former browser version and where it is in the window.

### 15.8a The 3D view's bar, status bar and keys

- **Probe** (in the 3D view's bar, Results step): click it, then click the coloured result to pin the value there; it is the same switch as
  the **Probe** tab's. Before the simulation has run it is greyed and its tooltip says why. The hint bar under the picture says "Probe is
  on" with a **Stop probing** button.
- **Section** (second bar, on the Mesh step once the mesh exists and on the Results step once the simulation has run): **Off, X, Y, Z**
  choose the axis; the slider moves the plane and the label gives its place in mm; **play** (the triangle) sweeps the plane from one end
  to the other and stops there; **Hide air** (cooling studies) draws only the solid parts. These move the panel's own section controls.
- **The hint bar** under the picture says what to click next ("Click faces to add or remove them from ... (2 selected). Ctrl-click adds
  more faces", "Click a flat face: the symmetry plane goes through it") with a **Done** or **Cancel** button; **Esc** presses it.
- **The status bar** shows a running job's message and progress and a **Cancel** button that stops it. When a job finishes or fails a toast
  says so.
- **Keys**: Ctrl+1 Iso, Ctrl+2 / 3 / 4 look along X / Y / Z (again: from the other side), F fit, Ctrl+E edges, Ctrl+P picture,
  Ctrl+Shift+P probe, Ctrl+Shift+Space play the section, Alt+1 to Alt+5 the steps, Ctrl+K write to Claude, Esc answer the hint or close a
  toast. Each button's tooltip names its key. A control with the keyboard focus has a bright ring.

### 15.8 For people who script it

The window serves a local interface on `127.0.0.1` (port 8765 or the next free one; `--no-api` turns it off): the same operations as
the menus and controls, by name, so that Claude Code or a script can drive and inspect the running window. Running Python inside the
window needs `--debug-api` and a secret; do not use that on a shared computer. `docs/QT_APP.md` has the full list and the security notes.

### 15.9 If something goes wrong

| Problem | What to do |
|---|---|
| A box **The job failed** | Read its message, close it and run the step again. Meshing can fail to start when the computer is short of memory (close other programs). |
| The window is slow with a flow study | Use a coarser **Grid** in the Flow tab, or the region around the object. |
| Text appears as empty boxes in a picture taken by a script | That happens only in the test mode without fonts (`QT_QPA_PLATFORM=offscreen`); the real window is not affected. |
| The desktop icon says the libraries are missing | Run the setup again, or `run_workbench.bat`. |

## Choosing the solver: built-in FEM or CalculiX (structural studies)

Static structural and Vibration (modal) studies have a **Solver** setting (Setup, Settings):

- **built-in** - the workbench's own finite-element solver. Always available. Supports everything the study offers
  (free-free modes, added masses, frictionless supports on holes).
- **calculix** - the open-source CalculiX program (`ccx`), a separate download. Same mesh, materials and loads,
  independent solver code: use it to cross-check the built-in numbers. In this version it supports fixed supports,
  prescribed displacements, forces, pressures, gravity, and frictionless supports on flat faces normal to x, y or z;
  modal studies need a fixed support and no added masses (the Solve step says what to change otherwise).

Install CalculiX from the **Solve** step (button "Install CalculiX", about 150 MB from conda-forge, no admin rights,
one folder `calculix` next to the workbench; delete it to remove). If `ccx` is already on the PATH, or `SIMWB_CCX`
points to it, that copy is used.

**Software used for each step** (Solve step, below the solver) lists the program behind every element of the
workflow - geometry, meshing, setup, solver, results, video export, assistant - with its licence and whether it is
installed. CalculiX and OpenFOAM are GPL programs the workbench only runs; they are never part of the workbench.


### Even work on every core (parallel runs)

A sliding-mesh run keeps its rotating interface on one process, which would leave that process with about a third
more cells than the others (the others then wait for it every step). After splitting the mesh the workbench counts
the cells per process and, when one holds more than 10 % above the mean, splits again asking for fewer cells on
that process. Up to four values are tried and the most even split is kept, so it adapts to whatever mesh is being
run. The Solver log shows "Cells per process ... splitting again with weights ..." and the final spread (measured
on a 2.7 million cell propeller: 1.34 x the mean before, 1.08 x after). Each extra split takes about a minute for
2.7 million cells; a resumed run keeps its original split.

### Continue a finished transient run

A finished transient flow run can be carried on to a later end time: **Solve** step, "Continue the run to ... s,
saving every ... s". The saved times so far are kept; a longer save interval keeps the disk use down.


## Stress results (static structural)

| Field | Meaning |
|---|---|
| **Von Mises stress** | The equivalent stress compared with the yield strength (ductile metals). |
| **Max / Min principal stress** | The largest tension and the largest compression at each point. Judge brittle materials (cast iron, ceramics, many plastics) and composites by these, not by von Mises. |
| **Safety factor** | Yield strength / von Mises stress, shown when every part has a yield strength; capped at 15 so the colours stay readable. |
| **Stress error estimate** | How far the stress of the single elements is from the smoothed stress at each point [MPa]: large where the mesh is too coarse for the stress field. |
| **Total deformation** | Displacement [mm]. |

The summary gives the **estimated stress error** in percent (energy norm). Below about 10 % the mesh resolves the
stress field well; above 15 % the results panel warns and the field above shows where to refine (a finer preset,
or **Part sizing** on the highly stressed faces). A sharp inside corner always shows a high local value: the
stress there grows without limit as the mesh is refined, so add the real fillet radius to the CAD model if that
corner matters.

Stress is averaged at the nodes within each part only. Where two parts of different material touch, each part
shows its own stress (the stress jumps there in reality).


## Topology optimisation: the stiffest shape for the mass you choose

**What it does.** You say which faces are held and which carry loads, and how much of the part's mass to keep (30 % is a good first try). The optimiser takes material away where it carries little load and keeps it where it carries a lot. You get the stiffest shape that weighs that much (the same shape is also the lightest one for that stiffness). It is a starting point for a design, not a finished part.

**How to use it.**
1. Start a **Topology optimisation** study and import the part. **Try example** gives a bulky bracket: a 100 x 30 x 50 mm aluminium block with two 8 mm bolt holes at the left end and a 10 mm pin hole at the right end. Hold the two bolt holes (Fixed support) and put a force on the pin hole (for example 300 N down); on the Medium mesh (about 44,000 elements) the run takes about four minutes and gives a clean truss-like bracket that keeps 30 % of the mass.
2. In **Setup** put a **Fixed support** (or frictionless support) on the held faces and a **Force** or **Pressure** on the loaded faces. The cells that touch them always stay (one layer: a supported or loaded face is never hollowed out). A part that must not change (a bolt, a pad) is set to **In the design space: keep solid** in its card; a surface on a part that otherwise may change (a mounting pad, a sealing face) gets the condition **Keep solid** on its faces: no force, no support, the material next to it just stays.
3. Choose **Mass to keep**. **Smallest feature size** 0 means automatic: 4 % of the part's size and never less than four mesh cells. A larger value gives a simpler, sturdier shape; a smaller one needs a finer mesh.
4. **Mesh** as usual. Medium gives members a few cells wide; a finer mesh gives finer detail, not a different shape.
5. **Solve** (**Find the shape**). Before it starts the step says what it will need (elements, memory) and refuses, with the reason, a run that cannot work: no support, no load, a mesh too large for the free memory, a target below the material that is kept solid. While it runs the shape draws itself in the 3D view every second or so, a chart shows the stiffness kept, the mass and the sharpness, and **Stop and keep this shape** ends the run at any time with the shape found so far. Most runs settle in 50 to 120 iterations.
6. **Results** says in words what was kept: "A stiff shape for these loads: it keeps 35 % of the mass and, by the optimiser's estimate, 70 % of the stiffness of the solid part". Under it are **Check stress and stiffness** and **Save as STL…**, and a box **Read before you rely on this shape** that names what looks odd: walls the smallest feature size cannot thin, a support that is not joined to the loaded part, pieces that float, the linear-element gap, a single load case. The slider **Show material denser than** decides which material the picture shows (50 % is the usual choice); colours are the density, 0 empty and 1 solid. The sections and cuts of the Results step work on the shape.
7. **Check stress and stiffness** solves the shape shown (at the slider's threshold) again with the quadratic elements of the Static structural solver, with the same supports and loads: the maximum von Mises stress, the lowest safety factor, the largest movement against the solid part's, and the stiffness kept as the quadratic solver measures it, next to what the optimiser predicted. Pieces that do not carry the load to a support are left out and counted. Tick **also with CalculiX** (when CalculiX is installed) to solve it with a separate program as well and compare. If the slider is moved afterwards the card says the check belongs to another threshold.
8. **Save as STL…** writes the shape at the threshold as a binary STL in millimetres, for CAD or 3D printing. The faces the optimiser made are smoothed; the part's own faces are left exactly as they are. Pieces that float are included: look at them first.

**Reading the numbers.**

| Number | Meaning |
|---|---|
| Mass kept | The share of the part's mass in the shape. It equals your target; the sharp shape at 50 % is close to it. |
| Stiffness kept | How stiff the shape is against the solid part under the same loads. 100 % would be as stiff as solid. |
| Smallest feature | The thinnest member allowed, in mm and in mesh cells. Below four cells the stiffness figure can change by several per cent with the mesh. |
| Sharpness | The share of the changeable material that is solid or empty (not grey). Grey material is something the optimiser could not decide; the slider decides how to show it. |

**Floating pieces.** A piece of the shape that is joined to no support carries nothing and only uses up mass. At the end of the run the optimiser empties such pieces (two rounds at most) and lets the mass they held go to the load path; the notes say how much. Material kept solid by design (round supports, loads and Keep solid faces) is never emptied; if it ends up unattached the notes say so.

**How the optimiser was checked.**
- The gradient (sensitivity) matches finite differences, to about 1e-3.
- The solid part's stiffness agrees with beam theory and with the quadratic static solver.
- The stiffness kept is nearly the same on two meshes of a cantilever (0.459 and 0.456).
- **The published half MBB beam.** The 88-line code of Andreassen, Clausen, Schevenels, Lazarov and Sigmund (Struct Multidisc Optim 43, 2011) publishes its algorithm and problem in full but prints no compliance values, so its algorithm was ported line by line (bilinear quadrilaterals) and run on the same problem as this optimiser (60 x 20 cells, keep 50 %, penalty 3, density filter of radius 3 cells; the paper's own filter radius 1.5 is below this optimiser's smallest allowed size). The reference gives a compliance of 249.2, this optimiser 257.0 (3 % higher: linear tetrahedra are a little stiffer than quadrilaterals), and the two density fields have a correlation of 0.992 and show the same truss. The comparison is in the tests.
- **A second program.** After a run, **Check stress and stiffness** with the CalculiX box ticked solves the shape in CalculiX and in the built-in solver: the largest movement agreed to six digits on the bracket.

**What to keep in mind.**
- **It is a concept shape.** It is the stiffest for the one set of loads you gave; the optimiser does not check stress and a sharp corner can still be overstressed. **Check stress and stiffness** gives a first answer; rebuild or smooth the shape in CAD and check that with a **Static structural** study too.
- **The check can disagree with the prediction.** The optimiser's linear elements and its grey material make its stiffness figure only an estimate; on the bracket the quadratic solver gave 64 to 75 % where the optimiser said 70 %. The largest movement can differ much more than the stiffness, because stiffness is an average over where the load acts.
- **One load case.** The shape suits those loads only; the setup warns when only one load is set. Several load cases are not available yet.
- **Own weight is not included** (it changes with the shape).
- **Prescribed displacements are refused**: the stiffness objective is defined for forces. Use a force or a pressure.
- **Linear tetrahedra** keep each iteration fast; they read stiffer than the quadratic ones of the Static structural study, especially on thin parts. After the run the solid part is solved again with the quadratic elements and the difference is shown in the results.
- **Symmetry.** Use a symmetry plane (Setup) to optimise half of a symmetric part: the optimiser treats the plane as a mirror and the shape is smooth across it. A whole symmetric 3D model is not forced to stay symmetric.
- **Vibration is not optimised.** A stiffer shape at the same mass often has higher natural frequencies, but nothing here controls them: check the result with a Vibration (modal) study.


## Flutter (multiphysics: structure + air)

Start page, tab **Multiphysics** (it holds the studies that couple several kinds of physics; the *Studies* tab holds the single-physics ones).
The structure and the air are solved together: the natural modes of the part (built-in FEM) are put in a stream of air
whose forces follow the motion. The air speed is raised until the motion stops decaying and grows.

1. Import the wing, blade, fin or plate. Examples: `airplane_wing.step` (half wing of a small aircraft: 800 mm semi-span, NACA 2406 section, tapered 160 to 100 mm chord, leading edge swept 15 degrees, 3D-printed in PLA; flutters at about 75 m/s) and `flutter_plate.step` (80 x 300 x 1.2 mm aluminium plate). Model the wing as one solid half (root at the fuselage): the root is treated as a mirror, so the finite-wing correction uses the aspect ratio of the whole wing.
2. Mark the clamped root as **Fixed support** and the upper and lower skin as **Aerodynamic surface**. Added masses
   (pods, tip tanks) can be put on faces.
3. Setup: the direction the air flows towards (along the chord), the **span direction**, altitude, highest air speed
   (0 = up to Mach 0.6), the design speed (for the margin: flutter speed / design speed, aircraft rules ask for 1.15),
   damping (0 = lowest, conservative speed).
4. Mesh, Solve (seconds). Results: flutter speed, frequency, Mach, margin, divergence speed, frequency and damping
   against air speed (flutter where a damping line crosses zero), and the shapes: *Flutter mode*, *Divergence shape*
   and every natural mode, animated in the 3D view.

Limits: incompressible attached flow (indicative above Mach 0.6; transonic flutter is lower), strips across the span
without induction between them (a finite-wing factor AR/(AR+2) of the whole wing is applied by default), no sweep effect on the air forces (a swept wing is cut into strips along the flow), thin slender surfaces (aspect
ratio below about 2: estimate only), no sweep effect, no stall, linear small motion, no pre-stress. The method
reproduces the Goland wing (published 2-D strip value about 137 m/s) within 1 %. A CFD-coupled check (OpenFOAM with
preCICE) is the next multiphysics step.

## Shake test (sine sweep) in a vibration study

Simulates a vibration-table test. Set in **Setup**, Settings:

| Setting | Meaning |
|---|---|
| **Shake test (sine sweep)** | off, or the axis x / y / z along which the fixed supports are shaken. |
| **Shake amplitude** [g] | Acceleration amplitude of the sine at the supports. The response is linear: twice the amplitude, twice the stress. |
| **Damping** [% of critical] | Used for every mode; it sets the height of the peaks (amplification about 1 / (2 x damping): 2 % gives 25x). Typical 0.5-2 % for one-piece or welded metal parts, 2-5 % for bolted assemblies and plastics. Measure it if the peak stress matters. |
| **Excitation from / up to** [Hz] | The swept range. Both 0: from a third of the first natural frequency to the highest computed one. |

Results: the highest stress, movement and acceleration in the part at every frequency (curve on logarithmic
axes), a table of the resonance peaks, and the fields **Shake test: stress amplitude** and **Shake test:
displacement amplitude** at the worst frequency. Amplitudes are the largest value during one cycle. Needs a fixed
support and the built-in solver. Request enough modes to cover the swept range (the panel says when the sweep goes
above the highest computed mode). Vibration damage is fatigue: the stress allowed for many cycles is well below
the yield strength.


## Symmetry planes (mirror): solve half or a quarter of the model

When a part and what happens to it are mirror-symmetric (a car, an aircraft, a bracket loaded in its middle plane, a
heatsink in a duct), only one side has to be built and solved. The other side is the mirror image. For a flow study
the air volume is cut as well, so a plane through the middle halves the cells and about halves the run time.

**Add a plane** (Setup step, **Symmetry planes**):

| Way | What to do |
|---|---|
| Across an axis | Press **YZ plane, across x** (or the XZ / XY one). The plane appears at once through the middle of the parts, as an amber sheet with an arrow and a round handle. Drag the arrow in the 3D view (it slides along the plane's axis; hold Shift for round steps) or type the **Position** in mm. The arrow points into the side that is **kept**; **⇄ Flip** keeps the other side. When it is right, press **Apply**. |
| Through a flat face | Press **Through a flat face** and click a flat face of the model. The plane lies on the face and the side with the parts is kept (flip it for the other one). Move it parallel to the face by dragging the arrow or typing the **Offset from the face** (0 = on the face, positive = into the part), then **Apply**. Choosing **Symmetry plane (mirror)** in **＋ Add boundary condition…** and clicking a face does the same. |

Setting the plane rebuilds the geometry in the background: **everything on the other side is removed** (parts are cut,
parts wholly on that side disappear, the air volume is cut). Part names, materials and boundary conditions on faces
that the plane cuts are carried over. A plane you have set shows its position (edit it: the model is rebuilt), a
**Flip** button and **✕** (removes the plane and brings the removed side back). The plane's boundary condition
**Symmetry plane n** is also listed under Boundary conditions, **greyed out and marked 🔗 as connected** to the plane (it has no ✕ of its
own): the faces on the plane carry that condition, their selection follows the plane and cannot be edited by hand, and
the plane is moved, flipped or removed in the Symmetry planes card.

**Air volume only, or the model too (flow studies).** A plane ends the **air volume** (the control volume) at the plane: the box shown in the 3D view and in the Air volume panel follows it. The parts are **not** deleted: the model stays whole, and a part that crosses the plane is simply meshed on the kept side only (a part wholly beyond the plane is left out). Tick **Also delete the model beyond the plane** (on the new plane or on a plane already set) if you want the part of the model beyond the plane removed from the geometry as well; the mesh and the results are the same either way. Where a part crosses the plane, the 3D view draws the cut section as a solid grey cap, so the part looks whole (not a hollow shell) from the cut side; the cap is display only. Studies without air (stress, thermal) always cut the model.

**Put the plane in the middle of the model, or clear of it.** A plane that only just cuts the model (a few percent of its size, for example 1 mm off the lowest point) leaves tiny faces and cells of a few micrometres there; a transient run can diverge at that spot. If the plane only grazes the model and the cut leaves tiny faces just above it (under 1 mm tall), the workbench moves the plane a fraction of a millimetre into the model so that they are gone, and says so in the messages ("was moved ... mm into the model"). A plane through the middle of a model is never moved. The Setup step also warns about a grazing plane ("only just cuts the model").

Up to **three planes that are perpendicular to each other** (half, quarter, eighth). Two planes at another angle, or
parallel planes, are refused: the mirror images would not tile the model.

**In the Results step**, **Show mirrored half** (Display) draws the mirror image(s) of the model and of whatever result
is shown: the surface colours, sections, vortex surfaces, volume cloud, arrows, particles. Untick it to see only the
solved part. **Fit** includes the mirrored part. The images are redrawn from the planes: nothing is stored twice.

**What changes in the numbers** (the summary says so):

| Study | Boundary condition acts as | Results |
|---|---|---|
| External aerodynamics | OpenFOAM `symmetry` patch (no flow through the plane, no friction) | Forces, moments, rotor thrust, the reference area and coefficients are those of the **whole body** (the half's forces plus those of its mirror image: drag and lift add, side force across the plane cancels, the moments are transformed with the reflection). |
| Electronics cooling | `symmetry` in the air and in every part (no flow, no heat across) | Heat flows, the air flow rate and the thermal resistance are those of the whole device; temperatures and speeds are local values. |
| Static structural | A frictionless support: no movement through the plane | Stress, deformation and the safety factor are local values. **Loads you enter act on the kept part only:** enter half of a total that is shared by two halves. The summary lists the force each support carries (the mirror plane carries part of the load). |
| Heat in solids | Adiabatic (nothing to add) | Temperatures are local; a heat load given as a total acts on the kept part only. |
| Vibration | The movement through the plane is blocked (planes normal to x, y or z) | Only the modes that are **symmetric** across the plane are found; the others need the whole part. |

**Rules the workbench checks** (messages in Setup): the oncoming flow (and gravity, for natural convection and
own-weight loads) must run **along** the plane: a flow with side-slip is not symmetric. A plane may not cut through a
rotor or a rotating zone; rotors and zones on the removed side are dropped (their mirror images stand in for them: a
quadcopter with alternating propellers is symmetric, with all propellers turning the same way it is not). With
rotors, the weight is shared by all rotors including the mirrored ones.

**Be honest about the assumption.** A mirror forces a symmetric flow. Real wakes behind bluff bodies shed vortices
alternately and are not symmetric; a steady RANS run is fine, a transient run with vortex shedding is not.
Likewise a structure may buckle or vibrate asymmetrically. If in doubt, run the whole model once and compare.
