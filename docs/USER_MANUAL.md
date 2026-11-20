---
title: Mechanical Simulation Workbench - User Manual
audience: engineers who use the workbench (not people who change its code; for that read TECHNICAL_MANUAL.md)
product_version: 0.1.0
document_version: 1
format: Markdown, one topic per section, UI labels in **bold**, file names and commands in `code`
how_to_use_this_file_with_an_llm: >
  Paste the whole file, or only the section whose heading matches the task. Every section is self-contained:
  it repeats the terms it needs. Section ids (the "## N." numbers) are stable references.
---

# Mechanical Simulation Workbench - User Manual

## 0. Summary (read this first)

| Item | Value |
|---|---|
| What it is | A local web application: import CAD, mark faces, mesh, solve, look at results. Like Ansys Workbench, but small. |
| Runs where | On your own computer. Address: <http://127.0.0.1:8765>. Nothing is uploaded. |
| Input | STEP (preferred), STL or OBJ files. |
| Studies | Heat transfer in solids, Static structural, Vibration (modal), Electronics cooling (CFD), External aerodynamics (CFD). |
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

1. Windows: double-click `run_workbench.bat` in the workbench folder.
2. The first start creates a Python environment (Python 3.10 or newer is needed), installs the packages and, for the flow studies, downloads OpenFOAM (about 200 MB) and the Microsoft MPI runtime (about 12 MB) into the workbench folder. No administrator rights are needed. It takes a few minutes.
3. The browser opens at <http://127.0.0.1:8765>. If it does not, open that address yourself.
4. To stop: click **Close** (top right). This stops the server and every running solver.

Other ways to start:

```
.venv\Scripts\python -m simwb.server
```

Settings are read from environment variables (all optional):

| Variable | Effect |
|---|---|
| `SIMWB_HOME` | Folder where studies are stored by default. Default: the `projects` folder of the workbench. |
| `SIMWB_PORT` | Port of the web page. Default 8765. |
| `SIMWB_NO_BROWSER=1` | Do not open a browser window at start. |
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
| **Simple / Expert** | top right | **Simple** shows the essential settings and a **Next step** hint on top of the details panel (what is still missing, with a button that takes you there). **Expert** shows every setting and tool: part sizing, refinement regions, wall layers, solver numerics, case files, the solver choice, extra checks. Switching changes only what is shown, never a value. A new installation starts in Simple. |
| **Probe**, **Picture** | above the 3D view | **Probe** (Results step): click a point of the coloured result to pin its value there; click **Probe** again to stop. The labels stay until the field, the time or the section changes. **Picture** saves the 3D view as a PNG with the colour scale. |

---

## 3. Create a study and choose where it is saved

1. Open the start page (click the **SimWorkbench** logo).
2. In the box **Save the study in**, either:
   - leave it empty: the study goes into the workbench's own folder (the grey placeholder shows which), or
   - type the full path of a folder, for example `D:\Simulations`, or
   - click **Browse...** and pick a folder.
3. Click a study card, then **Use my STEP file** (empty study) or **Try example**.
4. The study gets its own sub-folder, named after the study, inside the chosen folder. If that name is taken, `-2`, `-3`... is added.
5. The program remembers the last folder you used.
6. **Duplicate** (⧉ next to the study name): a copy of the open study with its geometry, setup and mesh, but no results. Use it for variants, for example the same propeller at another speed, or a transient run of a steady setup. The mesh stays valid, so the copy can be solved at once.

Rules:

- The path must be a full path (starts with a drive letter, or `/` on Linux/macOS). A relative path is refused.
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

### 5.2 Boundary conditions per study

| Study | Condition ids (as labelled in the GUI) | Default for unassigned faces |
|---|---|---|
| Heat transfer in solids | Fixed temperature, Convection, Radiation + convection, Natural convection + radiation (still air), Heat flux, Heat load | Insulated |
| Static structural | Fixed support, Frictionless support, Displacement, Force, Pressure | Free |
| Vibration (modal) | Fixed support, Added mass | Free |
| Electronics cooling (CFD) | Velocity inlet, Pressure outlet, Slip wall, Wall with fixed temperature, Wall with convection | Adiabatic wall (air box sides: slip) |
| External aerodynamics (CFD) | Wall, Slip wall, Symmetry, Moving wall | Wall (no-slip) |

Heat sources are set on parts (body settings), not on faces, in the heat and cooling studies.

### 5.3 Settings per study

| Study | Main settings |
|---|---|
| Heat transfer in solids | Analysis (steady/transient), End time, Time step, Start temperature, Number of saved frames |
| Static structural | Gravity |
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
| **Smoothing** | 0-3 passes of averaging before drawing. Default 1. |
| **Opacity** | Of the vortex surfaces. |

### 8.3 Contour planes

Cuts through the flow, coloured by a field, several at once, each with its own opacity.

1. Click **+ Add contour plane**.
2. Choose **Field** and **Plane** (x, y, z). Move **Position** with the slider (shown in mm).
3. Set **Opacity** (10-100 %). Use a low value to see vortex surfaces behind the plane.
4. **Remove** deletes a plane.

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
- Coarse meshes are indicative only. Tet-only meshes have no prism layers.

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
| Page stays empty (only the top bar) | The page script failed to load. Reload with Ctrl+F5. If it persists, restart the server and look at the browser console. |
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

- Meshes are tetrahedra only; no prism layers. Wall-bounded results (drag, heat transfer) from coarse meshes are indicative.
- Actuator-disk rotors use an empirical in-plane drag model (k editable).
- Vibration: linear, no damping, no pre-stress, no contact.
- Radiation view factors are not in the built-in FEM.
- Incompressible flow only (below about 100 m/s).
- DES needs fine meshes and small time steps; a coarse mesh gives smooth, wrong-looking vortices.
- Volume, arrow and particle views are visualisations of the results; they do not add physics.


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


## Multiphysics: which engine for which problem

The start page has a **Multiphysics** tab for studies that couple several kinds of physics. A table under the cards says which
engine suits which problem, what it costs and what it needs (the same table is in `docs/ROADMAP.md`).

| Problem | Study | Engine | Cost |
|---|---|---|---|
| Flutter or divergence of a wing, blade or fin, screening and design sweeps | Flutter (structure + air) | built-in modal FEM + strip theory | seconds |
| Flutter with 3D flow and stall | Flutter (CFD-coupled) | built-in modal FEM + OpenFOAM | hours per air speed |
| Stress and warping from heating | Thermal stress | built-in heat and static FEM, or CalculiX | seconds to minutes |
| A panel or housing radiating sound | Vibro-acoustics (harmonic) | openCFS (optional download) | minutes |
| Nonlinear thermo-mechanics, contact (planned, optional) | - | MOOSE: Linux and macOS only, hidden on Windows | - |

Every engine installs without administrator rights and without WSL, or it is not offered where it cannot run.

## Flutter (CFD-coupled)

The fast flutter study uses a simple air model. This study replaces it by a real 3D transient flow simulation (OpenFOAM) coupled
to the same modal structure, so it captures 3D flow and stall (shock waves come with a later compressible step). It takes
**hours per air speed**: choose the speeds with the fast study first.

1. Create the study from the Multiphysics tab with your STEP file (or the example `airplane_wing.step`). Mark the clamped root
   as **Fixed support**; the faces the air flows over are the **Aerodynamic surface**. Set neither and the larger flat end face
   across the span becomes the root and every other face aerodynamic when you mesh (check it in the results of **suggest faces**
   through the assistant, or in the Setup step).
2. Settings: flow direction and span axis, the number of modes (include the first torsion mode), altitude, the range and the
   number of air speeds (blank = automatic: around the flutter speed of the fast study), the flow model, the CFD mesh, and how
   long to follow the motion (periods of the critical mode; 8 or more for a reliable damping).
3. **Mesh** makes two meshes: the structure (solid parts) and the air around the **half** wing (the root face lies on a symmetry
   plane). **Solve** prints the plan (speeds, run lengths, an estimate of the hours) and starts the run.
4. The run is a separate program that keeps going if you close the workbench; open the study again and it reconnects. Each
   speed saves checkpoints, a hung solver is restarted from the last checkpoint, and every finished speed appears at once in
   the results. Cancel stops cleanly and keeps what is finished.
5. Results: for every air speed the damping ratio of the critical mode with its uncertainty, a verdict (decaying, neutral,
   growing, unreliable), the modal motion against time, the damping against speed with the fast study's curve for comparison,
   and the flutter speed where the damping crosses zero, with its uncertainty and whether it was interpolated or extrapolated.

How a speed is run: the flow develops with the wing held (uncoupled, fast), then the wing is held for a few milliseconds to
measure the steady load, then released from rest with a small disturbance of the critical modes. The damping is read from the
decay or growth of the motion. **A speed marked unreliable needs a longer run**: damping can only be read from several periods.

Limits: incompressible flow (valid to about Mach 0.3), linear modal structure, small motion, explicit coupling (fine for air).
The wing surface moves with a stick model of lumped nodes along the span; each time step costs about a second of waiting for the
exchange, which is why a run takes hours.

## Thermal stress

Heat first, then stress (one-way coupling). Mark the cooling and heating (fixed temperature, convection, heat sources, as in the
heat study) and the supports (as in the static study; **frictionless supports** let a part expand freely, fixed supports hold
it back). Set the **stress-free temperature** (the temperature at which the part carries no thermal stress) and the study solves
the heat conduction, steady or over time, then the stress caused by the temperature differences and the expansion of the
materials. For a part that simply sits at one temperature, leave the heat conditions out and enter a **uniform part temperature**.
Solver: the built-in FEM, or CalculiX for the structure (**Solver** setting, expert). Materials carry a thermal expansion
coefficient. Checked against the textbook: a bar held at both ends (stress = E x alpha x dT), free expansion (no stress),
a bimetal strip, and a bent plate.

## Vibro-acoustics (harmonic)

A part or panel is shaken by a harmonic force or pressure over a range of frequencies; its surface pushes the air and the sound
spreads into an air box with an absorbing outer boundary. Both are solved together by **openCFS** (a separate, optional program
of about 141 MB, installed from the Solve step without administrator rights). The part is meshed inside an automatic air box.
Put the **force** or **pressure** on a face of the part (the faces of the part touch the air: that is fine) and a support on
another; do not select the faces of the air box. Results: the sound pressure and the displacement at the frequency of the
strongest response. Checked against analytic cases: plane waves in a duct (0.5 %), the modes of a rigid cavity (0.7 %).
Needs about a sixth of a wavelength per element in the air at the highest frequency: keep the sweep range modest on a coarse mesh.

## Orthotropic materials

A body can have orthotropic properties (wood, fibre composites): `E1, E2, E3, G12, G23, G13, nu12, nu23, nu13` and the
direction of axis 1 (and 2). The structural solvers (static, modal, flutter) and CalculiX use them; the isotropic path is
unchanged. Checked with the first bending frequency of a cantilever along the stiff direction (0.4 %).

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
| Place and drag | Press **＋ Plane ⟂ x / y / z**. A plane appears through the middle of the parts with a round handle. Drag the handle in the 3D view (it slides along the plane's normal) or type the **Position** in mm. A green arrow points to the side that is **kept**; the red side will be removed. **⇄ Flip** keeps the other side. When it is right, press **Set plane: cut the model**. |
| From a flat face | Press **＋ From a face** and click a flat face of the model, or choose **Symmetry plane (mirror)** in **＋ Add boundary condition…** and click the face. The plane goes through the face; the side with the parts is kept (flip it if you want the other one). |

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
