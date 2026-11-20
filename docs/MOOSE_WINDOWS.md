# MOOSE on Windows: timeboxed investigation (native MSYS2 build)

Status: not started. Nothing here is a promise. Facts are marked **verified** (read in a source named in the text, on
2026-11-20) or **unverified** (to be tested during the investigation).

## Why this document exists

MOOSE (Idaho National Laboratory, LGPL-2.1) is the optional multiphysics engine of the roadmap (item 6). The
workbench must work for people without WSL, Docker or conda-in-WSL, so MOOSE is only useful on Windows if there is a
native build that installs without those. Until that is shown, the engine is **hidden on Windows** with the reason
"MOOSE has no native Windows build; its official Windows route is WSL, which the workbench does not use"
(`simwb.solvers.moose.installer.supported`). On Linux (x86-64) and macOS (Apple silicon) it is offered: the installer
creates a per-user micromamba environment from INL's public conda channel (package `moose`, see
`simwb/solvers/moose/installer.py`).

## What the documentation says

Sources: the MOOSE documentation pages are served at mooseframework.inl.gov (they answered HTTP 403 to the tools used
here); the same pages were read as Markdown from the repository `idaholab/moose`, folder
`modules/doc/content/getting_started/installation/`.

- **verified** The installation index offers Windows only through WSL (`windows.md`), and that page states: "Using
  MOOSE on Windows 10 and 11 is experimental and not fully supported." Its WSL steps need an administrator shell.
- **verified** `msys2.md` exists but the installation index does not link to it. Its last substantive edit is from
  2020-06 (later commits only touch formatting, latest 2024-02). The helper scripts it names,
  `scripts/petsc_msys2_config.py` and `scripts/petsc_msys2_fixup.py`, exist in the repository (touched 2026-02 by a
  repository-wide formatting commit only). So the route is a leftover, not a maintained, tested platform.
- **verified** The conda route (`conda create -n moose moose`, channel `https://conda.software.inl.gov/public`) is
  Linux and macOS only: the channel's `repodata.json` lists package `moose` for linux-64 and osx-arm64, and none for
  win-64, osx-64 or linux-aarch64. There is no Windows conda package to reuse.

### What the MSYS2 page requires (verified, as written there)

1. MSYS2, 64-bit, "keep the suggested install location" (C:\msys64); use the **MINGW64** terminal (not UCRT64, which
   is the current MSYS2 default; whether the build works under UCRT64 is unverified).
2. `pacman`: `base-devel binutils git mingw-w64-x86_64-toolchain mingw-w64-x86_64-msmpi`, then **Microsoft MPI**
   `msmpisetup.exe` (not the SDK msi), version matching the msys2 package (MS MPI 10), default install path, and
   `C:\Program Files\Microsoft MPI\Bin` on the PATH.
3. `pacman`: `mingw-w64-x86_64-metis mingw-w64-x86_64-parmetis mingw-w64-x86_64-openblas
   mingw-w64-x86_64-netcdf mingw-w64-x86_64-pcre`.
4. `git clone` of moose with `git submodule update --init --recursive`; `MOOSE_DIR` in `~/.bashrc`.
5. PETSc built from the submodule with `petsc_msys2_config.py` (no `make install`); `PETSC_DIR` must be set with a
   hard-coded `C:/msys64/home/<user>/...` prefix; then `petsc_msys2_fixup.py` repairs broken paths in
   `petscvariables`.
6. libMesh built static (`--enable-static --disable-shared`) against MSYS2's netcdf. The page notes that libMesh needs
   "a patch to enable using an external netcdf installation" that "is not in the master branch or submodule yet".
   `contrib/bin/fix_windows_symlinks.sh` restores missing symlinks.
7. `make -j N` in `test/` builds `moose_test-opt`. To run it from a native Windows shell, DLLs have to be copied next
   to it. "The final moose executable also needs to be linked by hand" with the pcrecpp flags, removing the contrib
   pcre. The page ends there: no test run, no statement that the result passes the test suite.

### What blocks "no admin rights, relocatable per-user folder" (assessment)

- **verified in the page**: Microsoft MPI is a separate installer into `C:\Program Files` (a system installer; whether
  it can be installed or extracted without admin rights is **unverified**). The page assumes `C:\msys64`; absolute
  `PETSC_DIR` paths are written into PETSc's generated files (the fixup script exists because of broken paths), so a
  moved folder will probably break (**unverified** whether a prefix rewrite is enough).
- **verified in the page**: a full source build of PETSc + libMesh + MOOSE inside MSYS2 plus a hand-link step. This
  is a developer setup, not an installer: MSYS2's own installer, a compiler toolchain and a multi-hour build per
  version. Disk use and build time are **unverified** (expect several GB and hours).
- **unverified**: whether current moose (2026) still builds this way: the libMesh/PETSc/TIMPI versions, C++17/20
  requirements, Fortran/MPI assumptions and the removal of `pcre` from MSYS2 packages (MOOSE now vendors/uses other
  libraries) may all have moved on since the page was written.
- **unverified**: the MSYS2 installer can run per-user into a chosen folder without admin rights (the installer
  offers a folder choice; the pacman runs and MSYS2 itself need no system service, but this has not been tried here).
- **unverified**: whether the resulting executable can be packaged: collect `moose_test-opt.exe` (or an app built from
  a small module set) plus the mingw DLLs into one folder and run it from a plain Windows process without MSYS2 on
  the PATH. The page says DLLs must be copied, which suggests yes in principle.

## The investigation (3 working days, then stop)

Day 1: environment. Fresh Windows 11 user account without admin rights (or a VM). Install MSYS2 into a chosen per-user
folder; follow the page under both MINGW64 and UCRT64; record which steps ask for elevation (MS MPI is the first
suspect: try the MS-MPI redistributable extracted without the installer, or an MPI-less / serial build if MOOSE and
PETSc configure that way). Record every step that fails against the page as written.
Day 2: build. PETSc, libMesh, `moose_test-opt` using the newest MOOSE release tag, then a reduced module set
(heat conduction, solid mechanics, contact if it builds). Keep a log of the exact commands and versions.
Day 3: test and package. Run the tests of the built modules (`run_tests` needs Python and the build tree: record
what passes). Copy the result to a different folder and a different drive and run it from a plain `cmd.exe`
without MSYS2 on the PATH. Write the outcome here.

## Success criteria (all required)

1. `moose_test-opt` (or the heat-conduction/solid-mechanics application) **passes at least one test** from the MOOSE
   test suite on native Windows, run from a plain Windows process.
2. The result is a **relocatable, per-user folder** (move or copy it, it still runs); built or installed **without
   administrator rights**.
3. **No WSL, no Docker, no conda-in-WSL** at run time. A build-time toolchain (MSYS2) is acceptable only if the
   end user does not need it: the workbench would have to download a ready-made folder, which requires a place to
   host it and the LGPL source offer to be met (a separate download on request, never part of this project).
4. The setup is **repeatable by a script** that the workbench could run (or a pinned download with a checksum),
   not a manual developer procedure.

## Decision rule

- All four criteria met within the three days: write a Windows branch in `installer.py` (download a pinned,
  checksummed archive; `supported("win32")` becomes True) and record the build recipe here.
- Anything short of that: **MOOSE stays hidden on Windows**, this document records what failed, and the
  investigation is not reopened until MOOSE publishes a Windows conda package or a supported native build. Users who
  built their own can already point `SIMWB_MOOSE` at an executable; that works without the workbench offering it.

## Why MOOSE would be worth it (use cases) and what the other engines already cover

| Use case | Built-in FEM / CalculiX / openCFS | Where MOOSE would add something |
|---|---|---|
| Linear thermal stress, warping from heating | built-in FEM and CalculiX (sequential coupling, minutes) | little |
| Nonlinear thermo-mechanics (temperature-dependent properties, large deformation, plasticity with heat) | the CalculiX program supports nonlinear material, geometry and thermo-mechanics, but the workbench's deck writer only does static and modal studies today (unverified what is exposed); built-in FEM is linear | fully coupled, in one Newton solve |
| Contact (bolted joints, press fits, sliding) | the CalculiX program has contact, not exposed by the workbench today (unverified); built-in FEM none | mortar / node-to-face contact with the other physics in the same implicit system |
| Diffusion, phase change, species transport (hydrogen, moisture, solidification, battery-like) | none built in | phase-field, diffusion and reaction kernels are MOOSE's core; user-defined PDEs through input files |
| Fully implicit multiphysics coupling (thermal, mechanics, diffusion, electromagnetics in one system) | sequential coupling by exchanging fields between engines; openCFS for acoustics / piezo / magneto-mechanics | the one case where operator splitting is the limitation |

MOOSE's input is a free-form physics description (kernels, variables, materials) rather than a fixed study type, so
the integration cost is a per-study input writer; that is the reason the engine is optional and last on the roadmap.
Everything above beyond the first two rows is only worth building if users ask for it.
