"""Which engine for which problem: the data behind the guide on the Multiphysics tab and the table in docs/ROADMAP.md.

Pure data and small functions, no GUI and no heavy imports (simwb.software is imported lazily and only by
`engine_status`). Every study in `studies.STUDIES` carries an optional "guide" dict

    {"problem": str, "engines": [engine id | {"id": engine id, "role": str}], "fidelity": str, "cost": str, "needs": str}

which `apply_guides(STUDIES)` fills from GUIDE_ENTRIES (one line in studies.py). Studies that do not exist yet (planned
engines and studies) are listed from PLANNED so the guide also says what is coming and where it will run.
"""
from __future__ import annotations

import sys

# engine id -> metadata. platforms: sys.platform families that can run it natively (no WSL, no admin rights).
ENGINES = {
    "builtin": {"name": "Built-in FEM", "licence": "own code; BSD libraries (SciPy, PyPardiso, PyAMG)",
                "platforms": ("win32", "linux", "darwin"), "planned": False,
                "install": "Part of the workbench: nothing to install"},
    "calculix": {"name": "CalculiX", "licence": "GPL-2.0+", "platforms": ("win32", "linux", "darwin"), "planned": False,
                 "install": "Optional program, installed from the Solve step (no admin rights)"},
    "openfoam": {"name": "OpenFOAM", "licence": "GPL-3.0 (OpenFOAM), MIT (MS-MPI)",
                 "platforms": ("win32", "linux", "darwin"), "planned": False,
                 "install": "Installed by the workbench on the first flow run (no Docker, no WSL)"},
    "opencfs": {"name": "openCFS", "licence": "MIT", "platforms": ("win32", "linux", "darwin"), "planned": True,
                "install": "Optional portable download, unpacked into the workbench folder (no admin rights)"},
    "moose": {"name": "MOOSE", "licence": "LGPL-2.1", "platforms": ("linux", "darwin"), "planned": True,
              "install": "Optional, Linux and macOS only"},
}

# why an engine is not offered on a platform
PLATFORM_REASON = {
    ("moose", "win32"): "MOOSE has no native Windows build; its official Windows route is WSL, which the workbench "
                        "does not use",
}
PLATFORM_NAMES = {"win32": "Windows", "linux": "Linux", "darwin": "macOS"}

GUIDE_ENTRIES = {
    "heat_conduction": {
        "problem": "Temperatures in solid parts from heat sources and cooling, steady or over time",
        "engines": ["builtin"],
        "fidelity": "Conduction in solids with convection and radiation to the surroundings (textbook coefficients, "
                    "about +-25 %); no flow field",
        "cost": "seconds to minutes",
        "needs": "nothing",
    },
    "vibration_modal": {
        "problem": "Natural frequencies, mode shapes and the response to a shake table",
        "engines": [{"id": "builtin", "role": "default solver"}, {"id": "calculix", "role": "independent cross-check"}],
        "fidelity": "Linear modes of the solid parts with added masses; shake response with one damping ratio",
        "cost": "seconds to minutes",
        "needs": "nothing (CalculiX optional)",
    },
    "static_structural": {
        "problem": "Stress, deformation and safety factor of parts under loads and supports",
        "engines": [{"id": "builtin", "role": "default solver"}, {"id": "calculix", "role": "independent cross-check"}],
        "fidelity": "Linear elastic statics, quadratic tetrahedra; no contact, plasticity or large deformation",
        "cost": "seconds to minutes",
        "needs": "nothing (CalculiX optional)",
    },
    "aeroelastic_flutter": {
        "problem": "Flutter or divergence of a wing, blade or fin: screening and design sweeps",
        "engines": [{"id": "builtin", "role": "modal FEM + strip theory (p-k method)"}],
        "fidelity": "Linear modes with Theodorsen strip aerodynamics; no stall, shocks or 3D flow",
        "cost": "seconds",
        "needs": "nothing",
    },
    "cht_enclosure": {
        "problem": "Cooling of heat-producing parts in an enclosure: air flow and temperatures together",
        "engines": [{"id": "openfoam", "role": "conjugate heat transfer, fans"}],
        "fidelity": "3D flow with solid conduction, buoyancy and radiation models",
        "cost": "minutes to hours",
        "needs": "OpenFOAM (installed by the workbench)",
    },
    "external_aero": {
        "problem": "Air flow around a body: forces, pressure and wake",
        "engines": [{"id": "openfoam", "role": "RANS flow solver"}],
        "fidelity": "3D steady or transient flow, turbulence models",
        "cost": "minutes to hours",
        "needs": "OpenFOAM (installed by the workbench)",
    },
    "aeroelastic_flutter_cfd": {
        "problem": "Flutter with 3D flow, stall or shocks",
        "engines": [{"id": "openfoam", "role": "3D flow, lumped-point coupling"},
                    {"id": "builtin", "role": "modal structure"}],
        "fidelity": "Transient flow coupled to the modal structure each time step; damping read from the decay",
        "cost": "hours per air speed",
        "needs": "OpenFOAM (installed by the workbench)",
    },
    "thermal_stress": {
        "problem": "Thermal stress and warping of a part from heating",
        "engines": [{"id": "builtin", "role": "heat FEM, then static FEM"},
                    {"id": "calculix", "role": "coupled temperature-displacement"}],
        "fidelity": "One-way coupling: the temperature field loads a linear elastic structure",
        "cost": "seconds to minutes",
        "needs": "nothing (CalculiX optional)",
    },
    "vibro_acoustics": {
        "problem": "A panel or housing radiating noise, fan or flow noise, piezo and electro-mechanical parts",
        "engines": [{"id": "opencfs", "role": "acoustics, piezo, electro-mechanics"}],
        "fidelity": "Finite-element acoustics coupled to the vibrating structure; sound propagation from a flow result",
        "cost": "minutes",
        "needs": "openCFS (portable download)",
    },
}

# studies that are not in studies.STUDIES yet (or not for every engine): label and the guide to show until they are
PLANNED = {
    "aeroelastic_flutter_cfd": {"label": "Flutter (CFD-coupled)"},
    "thermal_stress": {"label": "Thermal stress (heat and structure)"},
    "vibro_acoustics": {"label": "Vibro-acoustics (openCFS)"},
    "nonlinear_thermomechanics": {
        "label": "Nonlinear thermo-mechanics (MOOSE)",
        "guide": {
            "problem": "Nonlinear thermo-mechanics, contact, diffusion and phase change; problems that need full "
                       "implicit coupling",
            "engines": [{"id": "moose", "role": "fully coupled nonlinear finite elements"}],
            "fidelity": "Nonlinear, fully implicit multiphysics",
            "cost": "depends on the problem",
            "needs": "Linux or macOS (no native Windows build)",
        },
    },
    "aeroelastic_flutter_transonic": {
        "label": "Flutter, transonic (step 3)",
        "guide": {
            "problem": "Flutter with shocks: the transonic dip of a wing",
            "engines": [{"id": "openfoam", "role": "compressible flow (rhoPimpleFoam / rhoCentralFoam)"},
                        {"id": "builtin", "role": "modal structure"}],
            "fidelity": "Compressible moving-mesh flow coupled to the modal structure",
            "cost": "hours per air speed",
            "needs": "OpenFOAM (installed by the workbench)",
        },
    },
}


def apply_guides(studies: dict, entries: dict | None = None) -> dict:
    """Set studies[key]["guide"] from the entries (default GUIDE_ENTRIES) for every key that exists. Returns studies."""
    for key, g in (GUIDE_ENTRIES if entries is None else entries).items():
        if key in studies and isinstance(studies[key], dict):
            studies[key]["guide"] = {**g, "engines": list(g["engines"])}
    return studies


def platform_family(platform: str | None = None) -> str:
    """'win32', 'linux' or 'darwin' for a sys.platform string (default: this machine)."""
    p = (platform or sys.platform).lower()
    return "win32" if p.startswith(("win", "cygwin", "msys")) else "darwin" if p.startswith(("darwin", "mac")) else "linux"


def engine_status(ids=None) -> dict:
    """engine id -> installed (True/False) or None (unknown), asked from simwb.software (imported lazily; any failure
    gives None). The built-in solver is always installed. Opt-in because the OpenFOAM check touches the disk."""
    out = {}
    for e in ids or ENGINES:
        if e == "builtin":
            out[e] = True
            continue
        try:
            from . import software

            out[e] = bool(software._status(e)["installed"]) if e in software.SOLVERS else None
        except Exception:
            out[e] = None
    return out


def _engine(item, status, plat) -> dict:
    eid, role = (item.get("id"), item.get("role")) if isinstance(item, dict) else (item, None)
    meta = ENGINES.get(eid)
    if meta is None:
        raise KeyError(f"unknown engine {eid!r}")
    offered = plat in meta["platforms"]
    installed = (status or {}).get(eid)
    if eid == "builtin" and installed is None:
        installed = True
    if not offered:
        installed = False
    reason = None
    if not offered:
        reason = PLATFORM_REASON.get((eid, plat)) or f"{meta['name']} is not available on {PLATFORM_NAMES.get(plat, plat)}"
    elif meta["planned"]:
        reason = "Planned: not part of the workbench yet. " + meta["install"]
    elif installed is False:
        reason = "Not installed. " + meta["install"]
    return {"id": eid, "name": meta["name"], "installed": installed, "offered": offered, "reason": reason, "role": role}


def rows(studies: dict, status: dict | None = None, platform: str | None = None, include_planned: bool = True) -> list[dict]:
    """The guide rows: one per study with a "guide" (in the order of `studies`), then one per planned study that does not
    exist yet. A row: {"study", "label", "problem", "fidelity", "cost", "needs", "exists", "planned", "engines": [{"id",
    "name", "installed": bool|None, "offered": bool, "reason": str|None, "role": str|None}]}. status = {engine id:
    installed bool|None} (None/missing = unknown); platform = a sys.platform string (default this machine)."""
    plat = platform_family(platform)
    out = []

    def row(key, label, g, exists, planned):
        out.append({"study": key, "label": label, "problem": g.get("problem", ""), "fidelity": g.get("fidelity", ""),
                    "cost": g.get("cost", ""), "needs": g.get("needs", ""), "exists": exists, "planned": planned,
                    "engines": [_engine(e, status, plat) for e in g.get("engines", [])]})

    for key, spec in (studies or {}).items():
        if isinstance(spec, dict) and spec.get("guide"):
            row(key, spec.get("label", key), spec["guide"], True, False)
    if include_planned:
        for key, p in PLANNED.items():
            if key in (studies or {}) and (studies[key] or {}).get("guide"):
                continue
            g = p.get("guide") or GUIDE_ENTRIES.get(key)
            if g:
                exists = key in (studies or {})
                row(key, p["label"], g, exists, not exists)
    return out


def guide_markdown(rs: list[dict]) -> str:
    """The rows as a Markdown table (for docs/ROADMAP.md and the manuals)."""
    def cell(e):
        note = []
        if not e["offered"]:
            note.append("not available on this platform")
        elif ENGINES[e["id"]]["planned"]:
            note.append("planned")
        return e["name"] + (f" ({e['role']})" if e.get("role") else "") + (f" - {', '.join(note)}" if note else "")

    esc = lambda s: str(s).replace("|", "\\|").replace("\n", " ")  # noqa: E731
    lines = ["| Problem | Study | Engine | Fidelity | Cost | Needs |", "|---|---|---|---|---|---|"]
    for r in rs:
        study = f"`{r['study']}`" + (" (planned)" if r.get("planned") else "")
        lines.append(f"| {esc(r['problem'])} | {study} | {esc('; '.join(cell(e) for e in r['engines']))} | "
                     f"{esc(r['fidelity'])} | {esc(r['cost'])} | {esc(r['needs'])} |")
    return "\n".join(lines) + "\n"
