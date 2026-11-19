---
title: Multiphysics roadmap (to-do list)
purpose: What is built, what is next, and which engine to use for which coupled problem. Update it when an item moves.
---

# Multiphysics roadmap

Multiphysics studies are listed on the **Multiphysics** tab of the start page (`category: "multiphysics"` in
`studies.STUDIES`). Each study names its engine(s). Rule for every engine: it must install without admin rights and
**without WSL** (not everyone has it), or it stays an optional extra that is not offered where it cannot run.

## Done

- [x] Flutter, fast: built-in modal FEM + Theodorsen strip aerodynamics, p-k method (`aeroelastic_flutter`).
- [x] Step 1 of CFD-coupled flutter: OpenFOAM `lumpedPointMotion` coupled to a Python structure solver
      (`solvers/openfoam/lumped.py`, protocol measured and tested).
- [x] Structure side of CFD-coupled flutter: modal model sampled at lumped nodes, exact time integration, damping from the
      decay (`solvers/openfoam/flutter_cfd.py`, `tests/test_flutter_cfd.py`).

## To do, in order

1. **CFD-coupled flutter, step 2 (subsonic, stall; OpenFOAM `pimpleFoam`/`rhoPimpleFoam`, transient SST or DES).**
   - [ ] First full coupled run on the wing (running: 0.3 s at 40 m/s on 8 cores, about 30 min). Known issue: the impulsive
         start leaves the lift still building after the structure is released; release later and remove the slow drift
         before fitting the damping.
   - [ ] Robust damping extraction (detrend, choose the model order, report the confidence).
   - [ ] Symmetry plane at the wing root (half model, mirror at y = 0).
   - [ ] Study "Flutter (CFD-coupled)" on the Multiphysics tab: settings, readiness checks, results (modal coordinates
         against time, damping against speed), a loop over several speeds to bracket the flutter speed, job progress,
         time limit and stall watchdog (a hung exchange must end the run, not wait for ever).
   - [ ] Validation against the strip theory (low speed) and a published case.
2. **CFD-coupled flutter, step 3 (transonic shocks):** `rhoPimpleFoam` (transonic) or `rhoCentralFoam`, compressible
   moving-mesh checks, validation on the AGARD 445.6 wing.
3. **Thermal stress (heat -> structure):** built-in heat FEM + static FEM, or CalculiX (`*COUPLED TEMPERATURE-DISPLACEMENT`,
   already installed as the second structural solver). No new software.
4. **A guide on the Multiphysics tab: which engine for which problem** (table below, shown in the GUI).
5. **openCFS engine (optional, portable Windows release, MIT licence):** vibro-acoustics (a vibrating panel radiating
   noise), aeroacoustics (CFD result -> sound propagation, Lighthill analogy), piezo and electro-mechanical parts,
   magneto-mechanics. Installer step like CalculiX (download, unpack into the workbench folder, no admin). Check first:
   the current Windows build, the mesh/field exchange format (gmsh meshes, `pyCFS` API), licence of the data tools.
6. **MOOSE engine (optional, last).** Only where it runs natively. The official routes are WSL, Docker or conda inside
   WSL; there is no supported native Windows build (an experimental MSYS2/MinGW route exists in its documentation).
   Plan: offer it on Linux and macOS; on Windows investigate the MSYS2 build and ship it only if it installs without
   admin rights and without WSL, otherwise hide it there. Use cases: nonlinear thermo-mechanics, contact, diffusion
   and phase change, problems that need full implicit coupling.

## Which engine for which problem

| Problem | Engine | Fidelity / cost | Needs |
|---|---|---|---|
| Flutter or divergence of a wing, blade, fin (screening, design sweeps) | built-in modal FEM + strip theory | seconds; no stall, shocks or 3D flow | nothing |
| Flutter with 3D flow, stall or shocks | OpenFOAM + modal structure (lumped-point coupling) | hours per air speed | OpenFOAM (installed by the workbench) |
| Thermal stress, warping from heating | built-in FEM or CalculiX | seconds to minutes | nothing (CalculiX optional) |
| Panel or housing radiating noise, fan or flow noise, piezo, electro-mechanical | openCFS (planned) | minutes | openCFS (portable download) |
| Nonlinear thermo-mechanics, contact, phase change (planned, optional) | MOOSE | depends | Linux/macOS, or a native Windows build if one proves practical |
