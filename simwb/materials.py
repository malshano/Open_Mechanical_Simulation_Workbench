"""Material library (SI units).

Solids: k [W/m/K], rho [kg/m3], cp [J/kg/K], E [Pa] Young's modulus, nu [-] Poisson's ratio, Sy [Pa] typical
minimum yield strength (None for brittle materials and composites) - room temperature, isotropic; laminates and FR4
use in-plane quasi-isotropic values. Check strengths against your supplier's datasheet.
Fluids: additionally mu [Pa s], Pr [-], beta [1/K] (thermal expansion), M [kg/kmol], kind ("gas" or "liquid": a gas is a perfect gas in
the compressible cases, a liquid has a constant density) - all at about 20 C and 1 atm.

Your own materials: add_solid / add_fluid put them in the same two dictionaries (so every solver finds them like a built-in one), save them
in <workspace>/materials.json and a study that uses one keeps a copy of it in its own file (project.json), so a shared study opens
on a machine that does not have it.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

SOLIDS = {
    "aluminium_6061": {"label": "Aluminium 6061", "k": 167.0, "rho": 2700.0, "cp": 896.0, "E": 68.9e9, "nu": 0.33,
                       "Sy": 276e6},  # T6
    "aluminium_6063": {"label": "Aluminium 6063 (extruded heatsinks)", "k": 201.0, "rho": 2690.0, "cp": 900.0,
                       "E": 68.3e9, "nu": 0.33, "Sy": 145e6},  # T5
    "copper": {"label": "Copper", "k": 390.0, "rho": 8940.0, "cp": 385.0, "E": 117e9, "nu": 0.34, "Sy": 70e6},  # annealed
    "steel": {"label": "Steel (carbon)", "k": 45.0, "rho": 7850.0, "cp": 490.0, "E": 200e9, "nu": 0.29, "Sy": 250e6},  # S235 / 1018
    "stainless_304": {"label": "Stainless steel 304", "k": 16.2, "rho": 8000.0, "cp": 500.0, "E": 193e9, "nu": 0.29,
                      "Sy": 215e6},
    "fr4": {"label": "FR4 (PCB, in-plane avg.)", "k": 0.8, "rho": 1850.0, "cp": 1100.0, "E": 22e9, "nu": 0.15, "Sy": None},
    "silicon": {"label": "Silicon (die)", "k": 148.0, "rho": 2330.0, "cp": 712.0, "E": 130e9, "nu": 0.28, "Sy": None},
    "abs": {"label": "ABS plastic", "k": 0.17, "rho": 1050.0, "cp": 1400.0, "E": 2.2e9, "nu": 0.35, "Sy": 40e6},
    "pla": {"label": "PLA plastic", "k": 0.13, "rho": 1240.0, "cp": 1800.0, "E": 3.5e9, "nu": 0.36, "Sy": 50e6},
    "carbon_fibre": {"label": "Carbon fibre composite", "k": 5.0, "rho": 1600.0, "cp": 900.0, "E": 50e9, "nu": 0.30, "Sy": None},
    "thermal_pad": {"label": "Thermal pad / TIM", "k": 3.0, "rho": 2500.0, "cp": 1000.0, "E": 1e6, "nu": 0.45, "Sy": None},
    "ceramic_alumina": {"label": "Alumina ceramic", "k": 30.0, "rho": 3900.0, "cp": 880.0, "E": 370e9, "nu": 0.22,
                        "Sy": None},
    "titanium_6al4v": {"label": "Titanium Ti-6Al-4V", "k": 6.7, "rho": 4430.0, "cp": 526.0, "E": 113.8e9, "nu": 0.34,
                       "Sy": 880e6},
    "magnesium_az31b": {"label": "Magnesium AZ31B", "k": 96.0, "rho": 1770.0, "cp": 1000.0, "E": 45e9, "nu": 0.35,
                        "Sy": 200e6},
    "pom": {"label": "POM / acetal plastic", "k": 0.31, "rho": 1410.0, "cp": 1465.0, "E": 2.8e9, "nu": 0.35,
            "Sy": 65e6},
    "nylon_pa6": {"label": "Nylon PA6 (dry)", "k": 0.25, "rho": 1140.0, "cp": 1700.0, "E": 2.7e9, "nu": 0.39,
                  "Sy": 70e6},
}

FLUIDS = {
    "air": {
        "label": "Air (20 C, 1 atm)", "kind": "gas",
        "rho": 1.204, "mu": 1.825e-5, "cp": 1006.0, "k": 0.0257, "Pr": 0.713,
        "beta": 3.43e-3, "M": 28.96,
    },
    "nitrogen": {
        "label": "Nitrogen (20 C, 1 atm)", "kind": "gas",
        "rho": 1.165, "mu": 1.76e-5, "cp": 1040.0, "k": 0.0258, "Pr": 0.710,
        "beta": 3.43e-3, "M": 28.01,
    },
    "helium": {
        "label": "Helium (20 C, 1 atm)", "kind": "gas",
        "rho": 0.1664, "mu": 1.96e-5, "cp": 5193.0, "k": 0.152, "Pr": 0.670,
        "beta": 3.41e-3, "M": 4.003,
    },
    "water": {
        "label": "Water (20 C)", "kind": "liquid",
        "rho": 998.2, "mu": 1.002e-3, "cp": 4182.0, "k": 0.598, "Pr": 7.01,
        "beta": 2.07e-4, "M": 18.0,
    },
    "seawater": {
        "label": "Seawater (35 g/kg, 20 C)", "kind": "liquid",
        "rho": 1024.8, "mu": 1.08e-3, "cp": 3993.0, "k": 0.596, "Pr": 7.24,
        "beta": 2.5e-4, "M": 18.0,
    },
    "glycol_50": {
        "label": "Coolant: glycol / water 50 % (20 C)", "kind": "liquid",
        "rho": 1082.0, "mu": 3.9e-3, "cp": 3300.0, "k": 0.38, "Pr": 33.9,
        "beta": 4.5e-4, "M": 18.0,
    },
    "ethanol": {
        "label": "Ethanol (20 C)", "kind": "liquid",
        "rho": 789.0, "mu": 1.2e-3, "cp": 2440.0, "k": 0.171, "Pr": 17.1,
        "beta": 1.1e-3, "M": 46.07,
    },
    "transformer_oil": {
        "label": "Transformer oil (mineral, 20 C)", "kind": "liquid",
        "rho": 870.0, "mu": 0.022, "cp": 1860.0, "k": 0.12, "Pr": 341.0,
        "beta": 7.0e-4, "M": 18.0,
    },
    "engine_oil": {
        "label": "Engine oil (unused, 20 C)", "kind": "liquid",
        "rho": 888.1, "mu": 0.799, "cp": 1880.0, "k": 0.145, "Pr": 10360.0,
        "beta": 7.0e-4, "M": 18.0,
    },
    "glycerin": {
        "label": "Glycerin (20 C)", "kind": "liquid",
        "rho": 1260.0, "mu": 1.41, "cp": 2430.0, "k": 0.286, "Pr": 11980.0,
        "beta": 4.8e-4, "M": 92.09,
    },
}

BUILTIN_SOLIDS = frozenset(SOLIDS)          # (the library that comes with the program; the rest are the user's own)
BUILTIN_FLUIDS = frozenset(FLUIDS)
LOAD_WARNING = ""                           # (set when the user's library file could not be read)
on_change: list = []                        # callbacks (): the lists of materials changed (the studies refresh the fluid choices)


def get_solid(name: str) -> dict:
    if name not in SOLIDS:
        raise KeyError(f"Unknown solid material '{name}'. Options: {', '.join(SOLIDS)}")
    return SOLIDS[name]


def get_fluid(name: str) -> dict:
    if name not in FLUIDS:
        raise KeyError(f"Unknown fluid '{name}'. Options: {', '.join(FLUIDS)}")
    return FLUIDS[name]


def fluid_keys(kinds=None) -> list:
    """The fluids to choose from: all, or only those of the given kinds ("gas", "liquid")."""
    return [k for k, f in FLUIDS.items() if not kinds or f.get("kind", "gas") in kinds]


def is_gas(fluid: dict) -> bool:
    return fluid.get("kind", "gas") == "gas"


def is_custom(kind: str, key: str) -> bool:
    """kind: "solid" or "fluid"."""
    return key in (SOLIDS if kind == "solid" else FLUIDS) and key not in (BUILTIN_SOLIDS if kind == "solid" else BUILTIN_FLUIDS)


# ---------------------------------------------------------------------------- the user's own materials
def library_path() -> Path:
    """The user's library: materials.json in the workspace (project.py is not imported here: it imports this module)."""
    import os
    import sys
    pr = sys.modules.get("simwb.project")
    ws = getattr(pr, "WORKSPACE", None) or Path(os.environ.get("SIMWB_HOME", Path(__file__).resolve().parents[1] / "projects"))
    return Path(ws) / "materials.json"


def _num(v, name: str, lo: float, hi: float, unit: str = "") -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number") from None
    if not math.isfinite(x) or not (lo <= x <= hi):
        raise ValueError(f"{name} must be between {lo:g} and {hi:g}{(' ' + unit) if unit else ''}")
    return x


def _label(v, taken: set) -> str:
    t = re.sub(r"\s+", " ", str(v or "")).strip()
    if not t or len(t) > 60 or any(ord(c) < 32 for c in t):
        raise ValueError("Give the material a name of 1 to 60 characters")
    if any(x.lower() == t.lower() for x in taken):
        raise ValueError(f"There is already a material called '{t}'")
    return t


def _key_for(label: str, prefix: str, table: dict) -> str:
    base = prefix + (re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "material")[:40]
    key, n = base, 2
    while key in table:
        key, n = f"{base}_{n}", n + 1
    return key


def check_solid(f: dict, own: str | None = None) -> dict:
    """A solid's fields checked (SI units); raises ValueError saying which one is wrong. own: the key being edited (its name may stay)."""
    out = {"label": _label(f.get("label"), {m["label"] for k, m in SOLIDS.items() if k != own}),
           "k": _num(f.get("k"), "Conductivity", 0.001, 1000, "W/m/K"),
           "rho": _num(f.get("rho"), "Density", 1, 30000, "kg/m3"),
           "cp": _num(f.get("cp"), "Specific heat", 50, 10000, "J/kg/K"),
           "E": _num(f.get("E"), "Young's modulus", 1e4, 1.5e12, "Pa"),
           "nu": _num(f.get("nu"), "Poisson's ratio", -0.5, 0.499),
           "Sy": None}
    if f.get("Sy") not in (None, "", 0):
        out["Sy"] = _num(f.get("Sy"), "Yield strength", 1e4, 1e10, "Pa")
    return out


def check_fluid(f: dict, own: str | None = None) -> dict:
    kind = str(f.get("kind") or "liquid")
    if kind not in ("gas", "liquid"):
        raise ValueError("A fluid is a gas or a liquid")
    out = {"label": _label(f.get("label"), {m["label"] for k, m in FLUIDS.items() if k != own}), "kind": kind,
           "rho": _num(f.get("rho"), "Density", 0.01, 20000, "kg/m3"),
           "mu": _num(f.get("mu"), "Viscosity", 1e-7, 1e5, "Pa s"),
           "cp": _num(f.get("cp"), "Specific heat", 100, 20000, "J/kg/K"),
           "k": _num(f.get("k"), "Conductivity", 0.001, 1000, "W/m/K")}
    given = f.get("Pr")
    out["Pr"] = _num(given, "Prandtl number", 0.01, 1e6) if given not in (None, "") else out["mu"] * out["cp"] / out["k"]
    beta = f.get("beta")
    out["beta"] = _num(beta, "Thermal expansion", 0, 0.1, "1/K") if beta not in (None, "") else (3.4e-3 if kind == "gas" else 2.0e-4)
    if kind == "gas":
        out["M"] = _num(f.get("M"), "Molar mass", 1, 400, "kg/kmol") if f.get("M") not in (None, "") else 28.96
    else:
        out["M"] = 18.0            # (a liquid has a constant density; OpenFOAM only wants a number here)
    return out


def _save_library():
    path = library_path()
    blob = {"solids": {k: {**SOLIDS[k]} for k in SOLIDS if k not in BUILTIN_SOLIDS},
            "fluids": {k: {**FLUIDS[k]} for k in FLUIDS if k not in BUILTIN_FLUIDS}}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(blob, indent=1), encoding="utf-8")
    tmp.replace(path)


VERSION = 0                                 # (counts the changes: a panel that shows the library knows when to draw it again)


def _changed():
    global VERSION
    VERSION += 1
    for cb in list(on_change):
        try:
            cb()
        except Exception:  # noqa: BLE001 - a listener must never break the library
            pass


def add_solid(**f) -> str:
    """Add a solid to the library (and save it). Fields: label, k, rho, cp, E, nu, Sy (optional). Returns its key."""
    m = check_solid(f)
    key = _key_for(m["label"], "custom_", SOLIDS)
    SOLIDS[key] = {**m, "custom": True}
    _save_library()
    _changed()
    return key


def add_fluid(**f) -> str:
    """Add a fluid. Fields: label, kind (gas|liquid), rho, mu, cp, k, Pr and beta (optional), M (gas). Returns its key."""
    m = check_fluid(f)
    key = _key_for(m["label"], "custom_", FLUIDS)
    FLUIDS[key] = {**m, "custom": True}
    _save_library()
    _changed()
    return key


def update_custom(kind: str, key: str, **f) -> str:
    """Change one of the user's own materials (the built-in ones cannot be changed: add a copy instead)."""
    if not is_custom(kind, key):
        raise ValueError("Only materials you added can be changed; add a new one for a different value")
    table = SOLIDS if kind == "solid" else FLUIDS
    m = (check_solid if kind == "solid" else check_fluid)({**table[key], **f}, own=key)
    table[key] = {**m, "custom": True}
    _save_library()
    _changed()
    return key


def studies_using(kind: str, key: str) -> list:
    """The names of the studies (in the workspace and the folders studies were saved to) that use this material."""
    import sys
    pr = sys.modules.get("simwb.project")
    if pr is None:
        return []
    names = []
    for p in pr.list_projects(include_hidden=True):
        try:
            data = json.loads((Path(p["path"]) / "project.json").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if kind == "solid":
            used = any((c or {}).get("material") == key for c in (data.get("bodies") or {}).values())
        else:
            used = (data.get("settings") or {}).get("fluid") == key
        if used:
            names.append(p.get("name") or p["id"])
    return names


def remove_custom(kind: str, key: str):
    if not is_custom(kind, key):
        raise ValueError("Only materials you added can be removed")
    users = studies_using(kind, key)
    if users:
        label = (SOLIDS if kind == "solid" else FLUIDS)[key]["label"]
        raise ValueError(f"'{label}' is used by {', '.join(repr(u) for u in users[:3])}{' and others' if len(users) > 3 else ''}: "
                         f"give those another {'material' if kind == 'solid' else 'fluid'} first")
    (SOLIDS if kind == "solid" else FLUIDS).pop(key)
    _save_library()
    _changed()


def adopt(blob) -> int:
    """Register the custom materials that a study carries (project.json) or the library file holds and this machine does not have yet.
    Returns how many were added."""
    n = 0
    for kind, table, chk in (("solids", SOLIDS, check_solid), ("fluids", FLUIDS, check_fluid)):
        for key, m in ((blob or {}).get(kind) or {}).items():
            if key in table or not str(key).startswith("custom_") or not isinstance(m, dict):
                continue
            ok = None
            for label in (m.get("label"), f"{m.get('label')} (from a study)"):
                try:
                    ok = chk({**m, "label": label})
                    break
                except ValueError:                   # (the values are wrong, or the name is taken by another material here: second try)
                    continue
            if ok is not None:
                table[key] = {**ok, "custom": True}
                n += 1
    if n:
        _changed()
    return n


def used_by(data: dict) -> dict:
    """The custom materials a study's data refers to (its bodies' materials and its fluid), as they are now: kept in its own file."""
    solids = {m for m in (c.get("material") for c in (data.get("bodies") or {}).values()) if m and is_custom("solid", m)}
    fl = (data.get("settings") or {}).get("fluid")
    fluids = {fl} if fl and is_custom("fluid", fl) else set()
    return {"solids": {k: SOLIDS[k] for k in sorted(solids)}, "fluids": {k: FLUIDS[k] for k in sorted(fluids)}}


def load_user():
    """Read <workspace>/materials.json (the user's own materials) into the library."""
    global LOAD_WARNING
    LOAD_WARNING = ""
    try:
        path = library_path()
        if path.exists():
            blob = json.loads(path.read_text(encoding="utf-8"))
            adopt({"solids": blob.get("solids"), "fluids": blob.get("fluids")})
    except Exception as e:  # noqa: BLE001 - a damaged file must not stop the program
        LOAD_WARNING = f"Your materials file could not be read ({e}); the built-in materials are available."


load_user()
