"""Which software does each element of a study.

One table the app shows (Solve step, "Software used"), the API serves (/api/software) and the manuals quote: every
element of the workflow - geometry, meshing, setup, solving, results, export, chat - with the program that does it,
its licence, whether it is part of the workbench or a separate program, and whether it is installed. A study picks
its solver here, so a second solver (CalculiX) is a new row and a setting, not a change to the other elements.
"""
from __future__ import annotations

# element -> (software, licence, kind). kind: "workbench" = our code; "library" = a Python library the workbench
# imports; "program" = a separate program the workbench runs (downloaded on request, GPL stays separate)
ELEMENTS = {
    "geometry": ("gmsh with OpenCASCADE (STEP) / own STL-OBJ reader", "GPL-2.0+ (gmsh), own code", "library"),
    "meshing": ("gmsh (surface + volume mesh)", "GPL-2.0+", "library"),
    "mesh_layers": ("OpenFOAM snappyHexMesh (wall layers, flow studies)", "GPL-3.0", "program"),
    "setup": ("Workbench (faces, boundary conditions, materials, zones)", "own code", "workbench"),
    "post": ("Workbench viewer (Three.js) + NumPy / SciPy", "MIT, BSD, own code", "workbench"),
    "video": ("imageio-ffmpeg (H.264 MP4)", "BSD, GPL (ffmpeg)", "library"),
    "assistant": ("Claude Agent SDK + MCP", "MIT", "library"),
}

SOLVERS = {
    "builtin": {"name": "Built-in FEM", "software": "Workbench FEM (SciPy, PyPardiso, PyAMG; quadratic tetrahedra)",
                "licence": "own code; BSD libraries", "kind": "library"},
    "calculix": {"name": "CalculiX", "software": "CalculiX ccx (separate program)", "licence": "GPL-2.0+",
                 "kind": "program"},
    "openfoam": {"name": "OpenFOAM", "software": "OpenFOAM v2606 + MS-MPI (separate programs)",
                 "licence": "GPL-3.0 (OpenFOAM), MIT (MS-MPI)", "kind": "program"},
}

# study -> engines that can solve it, first = default. A study's setting "structural_solver" picks among them.
STUDY_SOLVERS = {
    "heat_conduction": ["builtin"],
    "vibration_modal": ["builtin", "calculix"],
    "static_structural": ["builtin", "calculix"],
    "topology_optimization": ["builtin"],
    "aeroelastic_flutter": ["builtin"],
    "cht_enclosure": ["openfoam"],
    "external_aero": ["openfoam"],
}
SETTING = "structural_solver"
LABELS = {"builtin": "built-in", "calculix": "CalculiX"}  # the setting's option labels


def engine_for(study: str, settings: dict) -> str:
    """The engine solving this study with these settings (the study's default when the setting is absent)."""
    options = STUDY_SOLVERS.get(study) or ["builtin"]
    chosen = str((settings or {}).get(SETTING, "")).lower()
    return "calculix" if chosen.startswith("calculix") and "calculix" in options else options[0]


def solver_label(study: str, settings: dict, default: str) -> str:
    e = engine_for(study, settings)
    if e == "calculix":
        what = "modal analysis" if study == "vibration_modal" else "linear static"
        return f"CalculiX ({what}, quadratic tetrahedra) - separate program"
    return default


def _status(engine: str) -> dict:
    if engine == "calculix":
        from .solvers.calculix import installer

        i = installer.detect()
        return {"installed": i["available"], "version": i.get("version"), "installable": i["installable"],
                "detail": "" if i["available"] else i["help"]}
    if engine == "openfoam":
        from .solvers.openfoam import runner

        i = runner.detect()
        return {"installed": bool(i.get("available")), "version": None, "installable": bool(i.get("installable")),
                "detail": i.get("detail") or i.get("help") or ""}
    return {"installed": True, "version": None, "installable": False, "detail": ""}


def describe(study: str, settings: dict | None = None, studies: dict | None = None) -> dict:
    """Software used by every element of a study: {"engine", "choices", "elements": [{element, software, licence,
    kind, installed ...}]}."""
    from .studies import STUDIES

    spec = (studies or STUDIES).get(study) or {}
    settings = settings or {}
    engine = engine_for(study, settings)
    rows = []
    flow = engine == "openfoam"
    for key, (sw, lic, kind) in ELEMENTS.items():
        if key == "mesh_layers" and not flow:
            continue
        rows.append({"element": key, "software": sw, "licence": lic, "kind": kind, "installed": True})
    s = SOLVERS[engine]
    rows.insert(4 if flow else 3, {"element": "solver", "software": s["software"], "licence": s["licence"],
                                   "kind": s["kind"], **_status(engine), "engine": engine})
    return {"study": study, "label": spec.get("label"), "engine": engine,
            "choices": [{"engine": e, "name": SOLVERS[e]["name"], **_status(e)}
                        for e in (STUDY_SOLVERS.get(study) or ["builtin"])],
            "elements": rows}
