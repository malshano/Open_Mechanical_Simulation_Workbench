# Mechanical Simulation Workbench

https://github.com/user-attachments/assets/729b3f70-68c9-4c16-a749-821662b5661a

Import a STEP, STL or OBJ file → click faces to set boundary conditions → mesh → solve → inspect results, in the browser,
with a Claude assistant that can do the setup for you.

| Study | Solver | Typical use |
|---|---|---|
| **Heat transfer in solids** | built-in finite-element solver (no install) | heated plates, PCBs, enclosures with convection coefficients |
| **Static structural (stress, deformation)** | built-in linear static FEM, quadratic tetrahedra (no install) | brackets, shafts, housings: von Mises and principal stresses, deflection, safety factor, estimated stress error under forces, pressures, gravity |
| **Vibration (natural frequencies, shake test)** | built-in modal FEM, quadratic tetrahedra (no install) | brackets, mounts, PCBs: will a part resonate with a motor or rotor? Optional sine-sweep shake test: stress, movement and acceleration against frequency |
| **Heatsink / electronics cooling** | OpenFOAM `chtMultiRegionSimpleFoam` | chip + heatsink in a duct or enclosure with a fan |
| **Drone / external aerodynamics** | OpenFOAM `simpleFoam` (k-ω SST) + actuator-disk rotors | drag, rotor downwash, trim pitch in forward flight |

Built on open-source projects: [gmsh](https://gmsh.info) (CAD import + meshing, OpenCASCADE kernel),
[OpenFOAM](https://openfoam.com) (CFD), [three.js](https://threejs.org) (3D view), SciPy/PyAMG and PARDISO via
pypardiso (FEM solvers).

## Documentation

* [docs/USER_MANUAL.md](docs/USER_MANUAL.md) - for people using the workbench.
* [docs/TECHNICAL_MANUAL.md](docs/TECHNICAL_MANUAL.md) - for people (and LLM agents) changing it.
* [docs/INDEX.md](docs/INDEX.md) - which one to read.
* [CONTRIBUTING.md](CONTRIBUTING.md) - how to contribute; [SECURITY.md](SECURITY.md) - how to report a security problem.

## Install (Windows 10/11, one click)

Download **`SimWB-Setup.exe`** from the latest release and double-click it (or unzip `SimWB-v*-windows.zip` and
double-click **`Install SimWB.bat`**). It installs everything into one folder, with no admin rights: a private
Python, the libraries, OpenFOAM + Microsoft MPI for flow simulation and CalculiX as a second structural solver
(about 3 GB of downloads, 15-30 minutes), makes a desktop icon and opens the workbench. `SimWB-Setup.exe` first asks
**where to install** (default `C:\SimWB`, no admin rights needed; an existing install in
`%LOCALAPPDATA%\Programs\SimWB` is updated in place; a folder that already holds other files gets a `SimWB` folder
inside it). Outside your user folder it must sit directly on a drive (`C:\SimWB`, `D:\SimWB`) and is made private to
you, so other users of the computer cannot change the programs it runs and whether to put an **icon on the desktop**. The *Uninstall* shortcut in the Start menu removes
it all; your studies are saved in the folders you choose and are not touched. Windows
SmartScreen may warn about the unsigned setup file: choose *More info* > *Run anyway*.
Options of `installer\Install-SimWB.ps1`: `-InstallDir`, `-NoDesktopShortcut`, `-SkipSolvers`, `-DryRun` (check this
computer only), `-Quiet`.

## Start

Windows: the desktop icon made by the setup, or double-click **`run_workbench.bat`** in a source checkout (it
creates a Python environment with Python 3.10+, then the browser opens at <http://127.0.0.1:8765>).

Manual: `python -m venv .venv && .venv\Scripts\pip install -r requirements.txt && .venv\Scripts\python -m simwb.server`

## OpenFOAM (CFD solver) - built in

The flow studies use OpenFOAM, and on Windows the workbench brings its own copy: on the first start it downloads
the official openfoam.com Windows build (~200 MB download, ~1.2 GB installed, a few minutes, no admin rights) into
its own folder `openfoam\` and uses it from there. `run_workbench.bat` does this during the first-time setup; a
server started another way does it in the background (progress in the status bar), and a CFD run started meanwhile
waits for it. Nothing to install by hand.

CFD runs use several CPU cores: the workbench also unpacks the Microsoft MPI runtime (~12 MB download, three files,
MIT licence) into its folder `mpi\` - nothing is installed on the system. Solve → **CPU cores**: *Auto* uses all
physical cores for large meshes (about one core per 20k cells), or pick a number. The mesh is split
(`decomposePar`), solved with `mpiexec`, and the latest result joined (`reconstructPar`) automatically.

* An OpenFOAM that is already installed is used instead (`SIMWB_OPENFOAM_CMD`, Linux/macOS install, WSL,
  `%USERPROFILE%\OpenFOAM`). Docker is not needed: on Windows it is used only with `SIMWB_USE_DOCKER=1`
  (image `opencfd/openfoam-default:2512`); on Linux/macOS it is a fallback.
* `SIMWB_OPENFOAM_DIR` moves the built-in copy; `SIMWB_AUTO_OPENFOAM=0` turns the automatic setup off.
* Linux/macOS: install OpenFOAM from openfoam.com (e.g. `apt install openfoam2606-default`); it is found automatically.
* OpenFOAM is free software (GPL) by OpenCFD Ltd; its licence files are in `openfoam\licenses`.

Without OpenFOAM you can still set up, mesh and **export** the case (Solve → “Write OpenFOAM case only” / download .zip)
and run it elsewhere with `./Allrun`.

## Claude assistant

The chat panel uses the **Claude Agent SDK** with your existing Claude login (the same one as Claude Code — click
*Log in* in the panel if needed), or `ANTHROPIC_API_KEY` if set. Claude can only act through workbench tools
(find faces, set materials and boundary conditions, mesh, run, read results) — it has no shell or file access.
Model: `SIMWB_CLAUDE_MODEL` (default `claude-opus-5-5`).

Use the workbench from **Claude Code / Claude Desktop** via MCP:

```
claude mcp add simwb -- C:\path\to\Open_Mechanical_Simulation_Workbench\.venv\Scripts\python.exe -m simwb.mcp_server
```

## Workflow tips

* **Symmetry planes** — for a mirror-symmetric part (and symmetric loads or flow) add a plane in Setup: drag it, flip it,
  press *Set plane*: the other side is cut away, only one side is meshed and solved (half the cells), and the Results
  step can show the mirrored half. Forces, moments and heat flows in the results are those of the whole body.

* **Simple / Expert** (top right) — Simple shows the essential settings and a *Next step* hint; Expert shows every
  setting and tool. Switching never changes a value.
* **Results** — *Probe* pins the value at a clicked point, *Picture* saves the view as a PNG, *Export* writes a
  one-page report (HTML, prints to PDF), a CSV table of the node values, or a VTK file for ParaView.

* **Geometry** — STL/OBJ meshes must be closed (watertight); they are read in mm, split into faces at edges sharper than 40°, and each closed shell becomes a solid (flow studies get an automatic air box).
* **Geometry** — STEP units are honoured (converted to metres internally). Bodies touching each other are fused
  into a conformal mesh (perfect thermal contact).
* **Air volume** (flow studies, automatic air around the parts) — pick its shape: *Box*, *C-shape* (a half cylinder
  upstream joined to a box downstream: its curved face is the inlet, fewer cells than a box for the same distance
  to the inlet) or *Cylinder along the flow*. It starts fitted to the parts; drag the orange handles in the view or
  type the sizes, then *Apply*; *Fit to parts* goes back to the automatic size, *Show all* zooms out to it. On a
  curved inlet set the inlet's *Flow direction* to an axis (uniform flow, like wind) — *Auto-detect inlet & outlet*
  does this. Curved shapes need a STEP file (STL/OBJ: box).
* **What you see** — every condition is drawn on its faces with a label, in all studies: arrows for loads, flows
  and heat input, blocks for fixed supports, balls for sliding supports, plates for slip / symmetry and open sides,
  short outward arrows for convection, a ball for an added mass, glowing spheres for heat generated in a part,
  the gravity arrow, the free stream on the air volume, and (aero) the oncoming air in front of the body, which
  turns with speed, pitch and sideslip.
* **Setup** — choose a boundary condition, then click faces in the 3D view (click again to remove). Hover a face
  to see what it is. Unassigned faces get the study default (insulated / no-slip wall). The *Readiness* list
  tells you what is still missing.
  * Heatsink: *Auto-detect inlet & outlet* picks the two ends of the duct.
  * Drone: rotors are placed on the motor tops automatically; thrust defaults to carrying the weight at the set pitch.
* **No fan / still air** — heat in solids: the condition *Natural convection + radiation (still air)* works out h on
  every face (size, orientation to gravity, temperature) and adds radiation to the room, so you do not have to
  guess h. Electronics cooling: set *Air flow* to *natural* to simulate the air itself rising off the parts (no
  inlet; an automatic air box is open to the room and leaves room above for the plume; in an enclosure mark the
  vents as *Air outlet / opening*), and switch *Radiation between surfaces* on (often 30-50 % of the heat).
* **Reaching faces inside** — untick *Air box* to hide the air around the parts, or use *Section* (also in Setup):
  hidden and cut-away faces cannot be clicked, so you pick the face you see.
* **Projects** — the project menu (top left) opens and deletes projects (with a confirmation); the SimWorkbench
  logo goes to the start page; *Close* (top right) stops every running job and solver process and the server.
* **Layout** — like commercial tools: the *Outline* (model tree: geometry, materials, boundary conditions, moving
  zones, mesh, solution, results, each with a status tick or warning) sits above the details panel; click a node to
  open and highlight it. Under the 3D view, *Messages* lists what is ready or missing and *Solver log* follows
  the running job. The steps along the top and the *Next* buttons still guide you through in order.
* **Explanations** — every simulation type (*About this simulation* in Setup) and every model option (analysis,
  flow model, radiation, air flow, convection scheme, moving-zone method ...) shows a short plain-language
  explanation of the selected choice.
* **Advanced (OpenFOAM)** (Solve step, CFD studies) — convection scheme, non-orthogonal correctors, relaxation,
  PIMPLE correctors, Courant number, solver tolerances; turbulence models k-ω SST, k-ω, k-ε, realizable k-ε,
  Spalart-Allmaras or laminar. *Case files* lists every OpenFOAM dictionary of the case: open one to read or edit
  it; your version replaces the generated file on every run until you revert it, so any OpenFOAM feature can be
  used.
* **Moving zones** (external aerodynamics) — a cylinder of air turning with a propeller, fan or wheel: add it,
  set centre, axis, radius, length and rpm, or *Fit around part*. *MRF* (steady, rotating reference frame, fast)
  or *Sliding mesh* (transient: the zone's cells rotate, its boundary becomes a cyclicAMI pair). The zone boundary
  is meshed exactly; regenerate the mesh after changing a zone.
* **Mesh** — start with *Coarse* for a quick look, then *Medium/Fine*. Use *Section* to look inside.
* **Selecting many faces** — Shift+click selects every face of that part, Ctrl+click selects all similar faces
  (same surface type, parallel normal, similar area — e.g. all fin sides). Click a view button twice to look from the
  opposite side; hide parts with the eye icon.
* **Solve** — OpenFOAM runs stop automatically when the key results (hottest temperature / aerodynamic forces) no
  longer change; you can continue a run for more iterations.
* **Transient** — set *Analysis* to *transient* (heat in solids, electronics cooling, external aero) and give the end
  time; the time step is automatic (heat: end time / 200; flow: Courant number about 1) or fixed. Results get a
  time slider with play, and a plot over time (temperatures per part, or drag and lift). All loads switch on at
  t = 0 from the start temperature / free stream; transient flow runs take far more time than steady ones.
* **Results** — surface or section colour plots, plus an engineering summary (max temperatures, thermal resistance,
  total-pressure drop, mass-flux energy balance, drag, CdA, trim pitch and rotor thrust split, convergence status,
  model assumptions).
* **Trust the numbers** — use *Run mesh-sensitivity check* on the Results page: the case is re-meshed one level finer
  (in a copy of the project), re-solved, and the key outputs are compared.

## Physics notes

* Heat conduction: linear tetrahedral FEM, verified against 1-D analytical solutions (tests/test_thermal.py).
  Transient: lumped heat capacity, BDF2 time steps (first step backward Euler), energy balance including the heat
  stored; checked against the series solution of a heated slab and an adiabatic block (tests/test_transient_domain.py).
* Transient flow: pimpleFoam (URANS k-ω SST; forces averaged over the second half of the run) and
  chtMultiRegionFoam, adjustable time step at Courant number 1, every saved time kept for the time slider.
* Vibration: linear-elastic modal analysis with 10-node tetrahedra (edge nodes added to the mesher's linear tets),
  consistent mass, shift-invert Lanczos (PARDISO, SciPy SuperLU without it). Fixed supports and added masses on
  faces; effective modal mass per direction; modes within ±15 % of an excitation range are flagged. Verified
  against Euler-Bernoulli cantilever and free-free beams (≤ 1 %), the cantilever effective mass (61 % in mode 1)
  and a tip-mass Rayleigh estimate (tests/test_pipeline.py). No damping, pre-stress or contact.
* Heatsink: steady conjugate heat transfer, second-order convection, laminar or k-ω SST chosen from the fin-channel
  Reynolds number, narrow air gaps are detected and meshed with ≥4–10 cells across (by preset), optional contact
  resistance between touching parts, no radiation.
* No fan (electronics cooling, natural convection): buoyant ideal-gas air, openings at the room's hydrostatic
  pressure (p_rgh total pressure), optional fvDOM surface-to-surface radiation (24 directions, one emissivity,
  openings radiate like the room). Checked on a vertical 100 x 100 x 2 mm aluminium plate, 5 W, in open air:
  CFD 61.8 °C without radiation vs 63.6 °C from the Churchill-Chu correlation; with radiation (ε 0.85)
  46.3 vs 46.7 °C, 2.50 W convection + 2.47 W radiation; heat balance within 1.6 %.
* Still air on solids (built-in FEM): h from Churchill-Chu (vertical, Ra·cos tilt) and the horizontal-plate
  correlations (hot side up 0.54 Ra^¼ / 0.15 Ra^⅓, down 0.52 Ra^⅕), air properties at the film temperature,
  iterated with radiation; matches a hand calculation of the same correlations to 0.01 K.
* Drone: steady RANS (k-ω SST, Spalding wall function) on tetrahedra, rotors as uniform actuator disks (no swirl),
  slipstream refinement behind each rotor, far field ≥8 spans upstream / 16 downstream; near hover the far field
  switches to ambient total pressure.

## Layout

```
simwb/
  meshgeom.py         STL / OBJ import (faces from sharp edges, volumes from closed shells)
  gmsh_worker.py      STEP import, domain building (air box / enclosure), meshing  (runs in a subprocess)
  domain.py           automatic air volume: box / C-shape / cylinder, fitted sizes, inlet/outlet/side faces
  project.py          project model + all operations (used by GUI, Claude, MCP)
  studies.py          study catalogue: BC types, parameters, units, defaults
  solvers/thermal_fem.py         built-in conduction FEM (verified against analytical solutions)
  solvers/modal_fem.py           built-in modal (vibration) FEM, quadratic tetrahedra
  solvers/static_fem.py          built-in linear static stress FEM (shares the modal assembly)
  memlimit.py                    memory cap for mesher subprocesses (Windows job object / RLIMIT_AS)
  solvers/openfoam/cases.py      OpenFOAM case writers;  foamio.py polyMesh writer/reader
  solvers/openfoam/runner.py     OpenFOAM detection + execution;  results_loader.py
  agent.py / tools.py / mcp_server.py   Claude integration
  server.py + web/    FastAPI backend and the browser GUI
examples/             example STEP files (python examples/make_examples.py regenerates them)
tests/                pytest suite (python -m pytest tests)
```

Projects are stored in `projects/` (override with `SIMWB_HOME`).

## Licence

Copyright (C) 2026 Malshan Perera

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public
License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later
version (`SPDX-License-Identifier: GPL-3.0-or-later`). It is distributed in the hope that it will be useful, but
WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See
[LICENSE](LICENSE) for the full text.

Simulation results are only as good as the model and the mesh: check them before you rely on them for design decisions.

Third-party software, under its own licence: three.js and its controls in `simwb/web/vendor/` (MIT), gmsh (GPL-2.0-or-later),
OpenFOAM (GPL-3.0) and CalculiX (GPL), downloaded by the installer, and the Python packages in `requirements.txt`.
