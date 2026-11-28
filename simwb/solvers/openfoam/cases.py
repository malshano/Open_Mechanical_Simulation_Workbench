"""OpenFOAM case generation (targets OpenFOAM ESI / openfoam.com v2206 and newer).

external_aero : simpleFoam, k-omega SST, freestream far field, rotors as actuator disks
                (vectorSemiImplicitSource in a cylindrical cellZone), forces on walls.
cht_enclosure : chtMultiRegionSimpleFoam, one air region + one region per solid, coupled
                interfaces (turbulentTemperatureCoupledBaffleMixed), heat sources via fvOptions.
"""
from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

import numpy as np

from ... import materials
from ...studies import AXES
from .foamio import header, polymesh_quality, read_polymesh, sanitize, tet_mesh_to_polymesh, write_polymesh

G = 9.81
CMU = 0.09


def _w(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, newline="\n")


def _vec(v):
    return "(" + " ".join(f"{float(x):.8g}" for x in v) + ")"


# ----------------------------------------------------------------------------
# patches
# ----------------------------------------------------------------------------

PATCH_TYPE = {
    "velocity_inlet": "patch", "pressure_outlet": "patch",
    "wall": "wall", "slip_wall": "wall", "symmetry": "symmetry",
    "wall_fixed_temperature": "wall", "wall_convection": "wall",
}


def build_patches(study: str, faces: list, bcs: list, open_box=False):
    """Map CAD faces to OpenFOAM patches.  Returns (surface->patch index, patches[]).
    open_box: unassigned enclosure faces of an automatic CHT air box are free-air sides (slip), not duct walls;
    a dict {"T": degC} instead: openings to the room (no fan: air flows in and out there)."""
    patches = []
    names = set()

    def add(name, ptype, bc):
        base = sanitize(name)
        n, i = base, 2
        while n in names:
            n, i = f"{base}_{i}", i + 1
        names.add(n)
        patches.append({"name": n, "type": ptype, "bc": bc})
        return len(patches) - 1

    surf2patch = {}
    for bc in bcs:
        idx = add(bc["name"], PATCH_TYPE.get(bc["type"], "wall"), bc)
        for f in bc["faces"]:
            surf2patch[int(f)] = idx
    defaults = {}
    for f in faces:
        if f["id"] in surf2patch or f.get("interface"):
            continue
        kind = f.get("kind", "boundary")
        if study == "external_aero":
            key = ("farfield", "patch") if kind == "farfield" else ("body", "wall")
        elif study == "cht_enclosure":
            key = (("open_sides" if open_box else "walls"), "wall") if kind == "enclosure" else ("solid_walls", "wall")
        else:
            key = ("walls", "wall")
        if key not in defaults:
            if key[0] == "open_sides" and isinstance(open_box, dict):
                defaults[key] = add(key[0], "patch", {"type": "pressure_outlet", "name": key[0], "kind": key[0],
                                                     "faces": [], "params": {"T_backflow": open_box["T"]}})
            else:
                defaults[key] = add(key[0], key[1], {"type": "slip_wall" if key[0] == "open_sides" else "default",
                                                     "name": key[0], "kind": key[0], "faces": []})
        surf2patch[f["id"]] = defaults[key]
    return surf2patch, patches


def split_zone_walls(zones: list, faces: list, surf2patch: dict, patches: list) -> dict:
    """Walls inside a moving zone (the propeller) get their own wall patch '<zone>_walls', so the solver reports
    the force and torque on the propeller alone (the airframe wall patch still carries the rest). Only faces with
    the default wall condition move; a face with its own boundary condition keeps it. Returns {zone name:
    {"patch", "faces", "blade_radius"}} (blade radius: largest distance of those faces from the axis)."""
    out = {}
    default = {i for i, p_ in enumerate(patches) if p_["type"] == "wall" and p_["bc"].get("type") == "default"}
    for z in zones:
        c, a = np.asarray(z["center"], float), np.asarray(AXES[z["axis"]] if isinstance(z["axis"], str) else z["axis"], float)
        a = a / np.linalg.norm(a)
        ids, rmax = [], 0.0
        for f in faces:
            if surf2patch.get(f["id"]) not in default or f.get("interface"):
                continue
            v = np.asarray((f.get("mesh") or {}).get("vertices") or [], float).reshape(-1, 3)
            if not len(v):
                continue
            rel = v - c
            h = rel @ a
            r = np.linalg.norm(rel - np.outer(h, a), axis=1)
            if (np.abs(h) <= 0.5 * z["length"] * 1.01).all() and (r <= z["radius"] * 1.01).all():
                ids.append(f["id"])
                rmax = max(rmax, float(r.max()))
        if not ids:
            continue
        name = sanitize(f"{z['name']}_walls")
        patches.append({"name": name, "type": "wall", "bc": {"type": "default", "name": name, "kind": "body", "faces": []}})
        for i in ids:
            surf2patch[i] = len(patches) - 1
        out[z["name"]] = {"patch": name, "faces": len(ids), "blade_radius": rmax}
    return out


def _write_mesh(case: Path, mesh: dict, surf2patch: dict, patches: list, zone_of_tet=None, zone_names=None,
                extra_zones=(), face_zone_specs=()):
    """face_zone_specs: [(name, triangles, inside-cell mask)] -> faceZones (moving-zone boundaries)."""
    from .foamio import face_zone

    tri_patch = np.array([surf2patch.get(int(s), -1) for s in mesh["tri_surf"]])
    pm = tet_mesh_to_polymesh(mesh["nodes"], mesh["tets"], mesh["tris"], tri_patch,
                              [p["name"] for p in patches], [p["type"] for p in patches],
                              zone_of_tet, zone_names)
    fzs = [(nm, *face_zone(pm, tr, inside)) for nm, tr, inside in face_zone_specs]
    write_polymesh(case / "constant" / "polyMesh", pm, extra_zones, fzs)
    q = polymesh_quality(pm)
    return pm, q


def _use_polymesh(case: Path, src: Path, patches: list):
    """A mesh made by OpenFOAM (snappyHexMesh) instead of one converted from our tetrahedra: copy it into the case.
    Its patches must be the ones the field files are written for (a patch missing from the mesh is harmless, a mesh
    patch with no condition is not). Returns (the mesh as read, a quality dict)."""
    import shutil

    dst = case / "constant" / "polyMesh"
    shutil.rmtree(dst, ignore_errors=True)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst)
    pm = read_polymesh(dst)
    have = {p["name"] for p in patches}
    missing = [b["name"] for b in pm["patches"] if b["name"] not in have]
    if missing:
        raise ValueError(f"The mesh has patches the setup does not know: {missing}. Mesh again.")
    return pm, {"non_orthogonality_max": None, "non_orthogonality_mean": None, "skewness_max": None}


def write_schedule(iterations: int):
    """Write every `wi` iterations and make endTime a multiple of it, so the final state is always written."""
    iterations = int(iterations)
    wi = 100 if iterations >= 200 else iterations
    end = int(math.ceil(iterations / wi) * wi)
    return wi, end


def transient_settings(s: dict, dt_auto: float, max_co: float = 1.0) -> dict | None:
    """Transient run settings from the study settings (None = steady). dt_auto: a stable first time step."""
    if s.get("analysis") != "transient":
        return None
    end = float(s["end_time"])
    n_frames = max(2, int(s.get("n_frames") or 21))
    dt = float(s.get("time_step") or 0.0)
    return {"end_time": end, "n_frames": n_frames, "write_interval": end / (n_frames - 1),
            "fixed_dt": dt > 0, "dt": dt if dt > 0 else min(dt_auto, end / 20), "max_co": max_co}


def frame_interval(tr: dict, s: dict, omega: float) -> dict:
    """Sliding-mesh runs: save a time every `frame_deg` degrees of turn of the fastest zone, a whole number of time
    steps (every saved time is a solved step), and end on a saved time. A smooth video needs the blades, the vortices
    and the field from the same instant, so the saved times must be close in rotation: the default 8 degrees (45 per
    turn) looks smooth, 30 degrees looks stepped. frame_deg = 0: the number of saved times as set (Saved time points)."""
    deg = float(s.get("frame_deg") or 0)
    if deg <= 0 or omega <= 0 or not tr.get("fixed_dt"):
        return tr
    steps = max(1, round(math.radians(deg) / omega / tr["dt"]))
    wi = steps * tr["dt"]
    n = max(1, round(tr["end_time"] / wi))
    tr.update(write_interval=wi, end_time=n * wi, n_frames=n + 1, frame_deg=math.degrees(omega * wi))
    return tr


def _common_system(case: Path, app: str, iterations: int, functions: str, n_procs: int, regions=None,
                   transient=None):
    if transient:
        tr = transient
        timing = f"""endTime         {tr['end_time']:.8g};
deltaT          {tr['dt']:.6g};
writeControl    {'runTime' if tr['fixed_dt'] else 'adjustableRunTime'};
writeInterval   {tr['write_interval']:.8g};
purgeWrite      0;
adjustTimeStep  {'no' if tr['fixed_dt'] else 'yes'};
maxCo           {tr['max_co']};
maxDi           10;
maxDeltaT       {tr['write_interval']:.8g};"""
    else:
        wi, iterations = write_schedule(iterations)
        timing = f"""endTime         {int(iterations)};
deltaT          1;
writeControl    timeStep;
writeInterval   {wi};
purgeWrite      2;"""
    _w(case / "system" / "controlDict", header("dictionary", "controlDict", "system") + f"""
application     {app};
startFrom       latestTime;
startTime       0;
stopAt          endTime;
{timing}
writeFormat     ascii;
writePrecision  8;
writeCompression {'on' if transient else 'off'};
timeFormat      general;
timePrecision   6;
runTimeModifiable true;

functions
{{
{functions}
}}
""")
    dp = header("dictionary", "decomposeParDict", "system") + f"""
numberOfSubdomains {int(n_procs)};
method          scotch;
"""
    _w(case / "system" / "decomposeParDict", dp)
    for r in regions or []:
        _w(case / "system" / r / "decomposeParDict", dp)


def _allrun(case: Path, body: str, app: str, n_procs: int, regions=False, all_times=False):
    ar = "-allRegions " if regions else ""
    if n_procs > 1:
        run = (f"runApplication decomposePar {ar}-force\n"
               f"runParallel {app}\n"
               f"runApplication reconstructPar {ar}{'' if all_times else '-latestTime'}\n")
    else:
        run = f"runApplication {app}\n"
    _w(case / "Allrun", f"""#!/bin/bash
cd "${{0%/*}}" || exit 1
. ${{WM_PROJECT_DIR:?}}/bin/tools/RunFunctions
# Generated by Mechanical Simulation Workbench
rm -f log.* case.done case.failed
{body}
{run}
if [ -f log.{app} ] && grep -q "^End" log.{app}; then touch case.done; else touch case.failed; fi
""")
    _w(case / "Allclean", """#!/bin/bash
cd "${0%/*}" || exit 1
rm -rf processor* postProcessing log.* case.done case.failed [1-9]*
""")


# ----------------------------------------------------------------------------
# external aerodynamics
# ----------------------------------------------------------------------------

def _enclosed_cells(tets, barrier_tris, guess):
    """Cells inside a closed surface meshed into the tet mesh (a moving zone's boundary): the tets are joined
    across shared faces except the barrier faces, and the connected pieces that lie mostly inside (guess: the
    centroid test against the ideal cylinder) are the zone. A centroid test alone takes a cell just outside a
    facet of the faceted cylinder for an inside cell; turned with the zone, it shears and inverts within a few
    degrees (a sliding run blew up after 2 degrees)."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    tets = np.asarray(tets, np.int64)
    loc = np.array([[1, 2, 3], [0, 3, 2], [0, 1, 3], [0, 2, 1]])
    F = np.sort(tets[:, loc].reshape(-1, 3), axis=1)
    B = np.sort(np.asarray(barrier_tris, np.int64), axis=1)
    _, inv = np.unique(np.vstack([F, B]), axis=0, return_inverse=True)
    inv = inv.ravel()
    fid, bid = inv[:len(F)], inv[len(F):]
    owner = np.repeat(np.arange(len(tets)), 4)
    keep = ~np.isin(fid, bid)
    fid, owner = fid[keep], owner[keep]
    o = np.argsort(fid, kind="stable")
    fid, owner = fid[o], owner[o]
    pair = np.where(fid[1:] == fid[:-1])[0]
    n = len(tets)
    g = coo_matrix((np.ones(len(pair)), (owner[pair], owner[pair + 1])), shape=(n, n))
    _, lab = connected_components(g, directed=False)
    frac = np.bincount(lab, weights=guess.astype(float)) / np.bincount(lab)
    return frac[lab] > 0.5


def layer_first_thickness(V, L, nu, zones=(), y_plus=30.0) -> float:
    """First wall-layer thickness for a y+ of about `y_plus` (30: the k-omega SST / k-epsilon wall functions) at the CENTRE of the first cell, which is
    where OpenFOAM measures it: half the thickness from the wall. (This used to size the layer so that y+ was reached at its outer face, and the cells
    came out at half the y+ asked for: a mean y+ of 15.8 on a wing run set up for 30, in the buffer layer where wall functions are least accurate.)
    From a turbulent flat plate (Cf = 0.058 Re^-0.2): the body at the flight speed over its length, and each rotating zone at 75 % of its
    tip speed over a chord of 15 % of its radius. The thinnest wins. (Layers sized relative to the local wall cell
    came out 0.08 mm on thin blades: time steps of 1e-7 s.)"""
    out = []
    for U, Lc in [(V, L)] + [(abs(z["rpm"]) * 2 * math.pi / 60 * 0.75 * z["radius"], 0.15 * z["radius"])
                             for z in zones]:
        if U > 0.1 and Lc > 0:
            cf = 0.058 * (U * Lc / nu) ** -0.2
            out.append(2.0 * y_plus * nu / (U * math.sqrt(cf / 2)))
    return min(out) if out else 0.01 * L


# snappyHexMesh layer-step limits (see write_external_aero): the strict ones are the long-standing default; the tolerant
# ones keep layers on sharp, finely meshed walls (measured on the flat plate: benchmarks/results)
LAYER_STRICT = {"mfr": 0.5, "mtm": 0.3, "iters": 50, "relaxed_iter": "", "relaxed": "maxNonOrtho 75;"}
LAYER_TOLERANT = {"mfr": 0.8, "mtm": 0.6, "iters": 100, "relaxed_iter": "\n    nRelaxedIter        3;",
                  "relaxed": "maxNonOrtho 75; minTwist 0.001; minDeterminant 1e-5; minFaceWeight 0.02; minVolRatio 0.01;"}

WALL_FUNCTION_MODELS = ("kOmegaSST", "kOmega", "kEpsilon", "realizableKE", "SpalartAllmaras", "kOmegaSSTDDES")


def boundary_layer_thickness(V, L, nu, laminar: bool) -> float:
    """99 % thickness of a flat-plate boundary layer at the end of a length L: Blasius 5 L / sqrt(Re) when laminar,
    0.37 L Re^-0.2 when turbulent."""
    re = max(V * L / nu, 1.0)
    return 5.0 * L / math.sqrt(re) if laminar else 0.37 * L * re ** -0.2


def layer_plan(V, L, nu, model: str, n: int, growth: float, zones=(), first=None, y_plus=None) -> dict:
    """First layer thickness (m) and what it resolves, for n wall layers growing by `growth`.

    Wall-function models (k-omega SST, k-epsilon ...): the first cell centre at y+ ~ 30 (`layer_first_thickness`).
    Laminar: there is no wall function, the boundary layer itself must be resolved. The n layers should span about
    1.3 times the layer thickness at the end of the body, so the first layer is sized from that (never thinner than
    1/200 of it); the cells across the thickness are reported, and few of them means: use more layers.
    A `first` given by the user is kept and only reported. `y_plus` is the user's target for the first cell centre (default 30; about 1 resolves the
    viscous sublayer and needs many more layers, see cells_across_delta). Returns {first, n, growth, delta, cells_across_delta, total, laminar, y_plus}."""
    laminar = model == "laminar"
    delta = boundary_layer_thickness(V, L, nu, laminar) if V > 0.1 else 0.0
    n = max(int(n), 1)
    g = max(float(growth), 1.0001)
    series = (g ** n - 1.0) / (g - 1.0)                    # sum of the layer thicknesses / the first one
    if first:
        t1 = float(first)
    elif laminar and delta > 0:
        t1 = max(1.3 * delta / series, delta / 200.0)
    else:
        t1 = layer_first_thickness(V, L, nu, zones, float(y_plus) if y_plus else 30.0)
    total = t1 * series
    across = 0.0
    if delta > 0 and t1 > 0:                               # layers inside delta: solve t1 (g^k - 1)/(g - 1) = delta
        across = min(float(n), math.log1p(delta * (g - 1.0) / t1) / math.log(g))
    return {"first": t1, "n": n, "growth": g, "delta": delta, "cells_across_delta": across, "total": total,
            "laminar": laminar, "y_plus": None if (laminar or first) else (float(y_plus) if y_plus else 30.0)}


def freestream_velocity(settings: dict) -> np.ndarray:
    V = float(settings.get("speed", 0.0))
    fwd = np.array(AXES[settings.get("forward_axis", "+x")], float)
    up = np.array(AXES[settings.get("up_axis", "+z")], float)
    if abs(fwd @ up) > 0.5:
        raise ValueError("Forward and up directions must be perpendicular")
    side = np.cross(up, fwd)
    th = math.radians(float(settings.get("pitch_deg", 0.0)))
    ps = math.radians(float(settings.get("yaw_deg", 0.0)))
    # vehicle velocity expressed in body axes (nose-down pitch th, sideslip ps);
    # the air moves the opposite way
    vel = math.cos(th) * (math.cos(ps) * fwd + math.sin(ps) * side) + math.sin(th) * up
    return -V * vel


ROTOR_DRAG_COEFF = 0.025  # s/m, small-quadrotor rotor drag (Faessler et al., RA-L 2018)


def rotor_thrusts(settings: dict, rotors: list, n_total: int | None = None) -> list:
    """Explicit thrust, or automatic: rotors carry the weight in the tilted attitude, T = W / (n cos(pitch)).
    n_total: all rotors of the vehicle when only part of it is modelled (symmetry planes)."""
    n = n_total or len(rotors)
    th = math.radians(float(settings.get("pitch_deg", 0.0)))
    W = float(settings.get("mass_kg", 0.0)) * G / max(math.cos(th), 0.2)
    out = []
    for r in rotors:
        t = r.get("thrust_N")
        out.append(float(t) if t not in (None, "") else (W / n if n else 0.0))
    return out


def inlet_area_along(faces: list, direction) -> float:
    """Area of (possibly curved) inlet faces seen along a direction (open surface: counted once)."""
    d = np.asarray(direction, float)
    d = d / (np.linalg.norm(d) + 1e-300)
    A = 0.0
    for f in faces:
        v = np.asarray(f["mesh"]["vertices"]).reshape(-1, 3)
        t = np.asarray(f["mesh"]["triangles"], int).reshape(-1, 3)
        if len(t):
            A += 0.5 * np.abs(np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]]) @ d).sum()
    return A


def projected_area(faces: list, direction) -> float:
    d = np.asarray(direction, float)
    d = d / (np.linalg.norm(d) + 1e-300)
    A = 0.0
    for f in faces:
        if f.get("kind") != "body":
            continue
        v = np.asarray(f["mesh"]["vertices"]).reshape(-1, 3)
        t = np.asarray(f["mesh"]["triangles"], int).reshape(-1, 3)
        if not len(t):
            continue
        n = 0.5 * np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
        A += 0.5 * np.abs(n @ d).sum()
    return A


def write_external_aero(case: Path, project: dict, mesh: dict, faces: list, info: dict) -> dict:
    s = project["settings"]
    fl = materials.get_fluid(s.get("fluid", "air"))
    rho, nu = fl["rho"], fl["mu"] / fl["rho"]
    Uinf = freestream_velocity(s)
    V = float(np.linalg.norm(Uinf))
    rotors = project.get("rotors", [])
    planes = project.get("symmetry") or []
    thrusts = rotor_thrusts(s, rotors, len(rotors) * 2 ** len(planes))
    k_drag = float(s.get("rotor_drag_coeff", ROTOR_DRAG_COEFF))
    if V < 1e-9 and not rotors:
        raise ValueError("Nothing drives the flow: set a flight speed > 0 or add at least one rotor.")

    surf2patch, patches = build_patches("external_aero", faces, project.get("bcs", []))
    zone_walls = split_zone_walls(project.get("moving_zones") or [], faces, surf2patch, patches)

    hexmesh = mesh.get("hex_dir")  # a snappyHexMesh mesh: used as it is (bodies only, no cell zones)
    if hexmesh and (rotors or project.get("moving_zones") or planes):
        raise ValueError("The hex-dominant mesh does not support rotors, moving zones or symmetry planes: "
                         "choose the tetrahedral mesh in the Mesh step.")

    # rotor cell zones
    cc = mesh["nodes"][mesh["tets"]].mean(1) if not hexmesh else np.zeros((0, 3))
    extra_zones, rotor_info = [], []
    for i, (r, T) in enumerate(zip(rotors, thrusts)):
        c = np.asarray(r["center"], float)
        a = np.asarray(r["axis"], float)
        a /= np.linalg.norm(a)
        rel = cc - c
        ax = rel @ a
        rad = np.linalg.norm(rel - np.outer(ax, a), axis=1)
        th = float(r.get("thickness") or 0.1 * float(r["radius"]))
        sel = np.where((np.abs(ax) <= th / 2) & (rad <= float(r["radius"])))[0]
        if len(sel) < 10:
            raise ValueError(
                f"Rotor {i + 1}: only {len(sel)} mesh cells inside the disk. Increase disk thickness or refine the mesh "
                f"(the mesher refines rotor disks automatically if rotors are defined before meshing).")
        zn = f"rotor{i + 1}"
        extra_zones.append((zn, sel))
        A_disk = math.pi * float(r["radius"]) ** 2
        v_ind = math.sqrt(abs(T) / (2 * rho * A_disk)) if T else 0.0
        # in-plane rotor drag (H-force): on the vehicle along the in-plane relative wind, H = k T V_inplane
        Vt = Uinf - float(Uinf @ a) * a
        H = k_drag * abs(T) * Vt  # vector, N
        rotor_info.append({"zone": zn, "thrust_N": T, "axis": a.tolist(), "cells": int(len(sel)), "center": c.tolist(),
                           "induced_velocity": v_ind, "disk_area": A_disk, "H_N": H.tolist(),
                           "drag_coeff_s_per_m": k_drag})

    # moving zones (rotating cylinders of air): cell zones; a sliding zone also gets its boundary as a face zone
    mzones, fz_specs = [], []
    ztris, ztz = mesh.get("zone_tris", np.zeros((0, 3))), mesh.get("zone_tri_zone", np.zeros(0))
    for zi, z in enumerate(project.get("moving_zones") or []):
        c = np.asarray(z["center"], float)
        a = np.asarray(AXES[z["axis"]], float)
        rel = cc - c
        ax = rel @ a
        rad = np.linalg.norm(rel - np.outer(ax, a), axis=1)
        inside = (np.abs(ax) <= z["length"] / 2) & (rad <= z["radius"])
        if (ztz == zi).any():  # the cells enclosed by the meshed (faceted) boundary, not the ideal cylinder
            inside = _enclosed_cells(mesh["tets"], ztris[ztz == zi], inside)
        if inside.sum() < 50:
            raise ValueError(f"Moving zone {z['name']}: only {int(inside.sum())} cells inside - regenerate the mesh "
                             "after adding or changing moving zones")
        extra_zones.append((z["name"], np.where(inside)[0]))
        omega = z["rpm"] * 2 * math.pi / 60
        mzones.append({"name": z["name"], "method": z["method"], "center": c.tolist(), "axis": a.tolist(),
                       "omega": omega, "rpm": z["rpm"], "cells": int(inside.sum()),
                       **({"walls_patch": zone_walls[z["name"]]["patch"],
                           "blade_radius": zone_walls[z["name"]]["blade_radius"]} if z["name"] in zone_walls else {})})
        if z["method"] == "sliding":
            tr_z = ztris[ztz == zi]
            if not len(tr_z):
                raise ValueError(f"Moving zone {z['name']}: its boundary is not in the mesh - regenerate the mesh")
            # three sliding pairs - the cylinder side and the two end caps: AMI projects faces onto each other,
            # and across the 90 degree rim that projection fails (a cell at the rim ran away after 0.8 degrees)
            X = np.asarray(mesh["nodes"], float)
            nrm = np.cross(X[tr_z[:, 1]] - X[tr_z[:, 0]], X[tr_z[:, 2]] - X[tr_z[:, 0]])
            cos = np.abs(nrm @ a) / np.maximum(np.linalg.norm(nrm, axis=1), 1e-300)
            axial = (X[tr_z].mean(1) - c) @ a
            parts = {"side": cos < 0.5, "capA": (cos >= 0.5) & (axial > 0), "capB": (cos >= 0.5) & (axial <= 0)}
            for part, sel in parts.items():
                if sel.any():
                    fz_specs.append((f"{z['name']}_{part}_faces", tr_z[sel], inside))
            mzones[-1]["ami_parts"] = [p for p, sel in parts.items() if sel.any()]
    sliding = [z for z in mzones if z["method"] == "sliding"]
    if sliding and s.get("analysis") != "transient":
        raise ValueError("A sliding-mesh zone needs a transient analysis (or use MRF for a steady run).")
    deforming = [p for p in patches if p["bc"]["type"] == "moving_wall"]
    if deforming and s.get("analysis") != "transient":
        raise ValueError("A moving part needs a transient analysis.")
    if deforming and sliding:
        raise ValueError("Use either a sliding-mesh zone or moving parts in one run, not both.")

    case.mkdir(parents=True, exist_ok=True)
    if hexmesh:
        pm, q = _use_polymesh(case, Path(hexmesh), patches)
    else:
        pm, q = _write_mesh(case, mesh, surf2patch, patches, extra_zones=extra_zones, face_zone_specs=fz_specs)

    # turbulence free-stream values
    L = info["ref_length"]
    U_ref = max([V] + [r["induced_velocity"] for r in rotor_info] + [0.5])
    I = float(s.get("turbulence_intensity", 1.0)) / 100.0
    k_inf = 1.5 * (I * U_ref) ** 2
    omega_inf = k_inf / (10.0 * nu)  # turbulent viscosity ratio 10

    wall_names = [p["name"] for p in patches if p["type"] == "wall" and p["bc"]["type"] in ("wall", "default", "moving_wall")
                  and p["bc"].get("kind") != "farfield"]
    slip_names = [p["name"] for p in patches if p["bc"]["type"] == "slip_wall"]
    sym_names = [p["name"] for p in patches if p["bc"]["type"] == "symmetry"]
    far_names = [p["name"] for p in patches if p["bc"].get("kind") == "farfield"]
    force_patches = wall_names

    ami = "".join(f'\n    "{z["name"]}_AMI.*"\n    {{\n        type cyclicAMI;\n    }}' for z in sliding)

    def bfield(far, wall, slip, extra=""):
        extra = extra + ami
        out = []
        for p in patches:
            n = p["name"]
            if n in sym_names:  # mirror plane: the same condition for every field
                out.append(f"    {n}\n    {{\n        type symmetry;\n    }}")
            elif n in far_names:
                out.append(f"    {n}\n    {{\n{far}    }}")
            elif n in slip_names:
                out.append(f"    {n}\n    {{\n{slip}    }}")
            else:
                out.append(f"    {n}\n    {{\n{wall}    }}")
        return "\n".join(out) + extra

    U = _vec(Uinf)
    hover = V < 1.0
    if hover:
        # near hover the freestream pair is ill-posed for entrainment: open boundary at ambient total pressure
        far_U = "        type pressureInletOutletVelocity;\n        value uniform (0 0 0);\n"
        far_p = "        type totalPressure;\n        p0 uniform 0;\n        value uniform 0;\n"
    else:
        far_U = f"        type freestreamVelocity;\n        freestreamValue uniform {U};\n        value uniform {U};\n"
        far_p = "        type freestreamPressure;\n        freestreamValue uniform 0;\n        value uniform 0;\n"
    _w(case / "0" / "U", header("volVectorField", "U", "0") + f"""
dimensions      [0 1 -1 0 0 0 0];
internalField   uniform {U};
boundaryField
{{
{bfield(far_U, "        type movingWallVelocity;\n        value uniform (0 0 0);\n" if sliding or deforming else "        type noSlip;\n",
        "        type slip;\n")}
}}
""")
    _w(case / "0" / "p", header("volScalarField", "p", "0") + f"""
dimensions      [0 2 -2 0 0 0 0];
internalField   uniform 0;
boundaryField
{{
{bfield(far_p, "        type zeroGradient;\n", "        type zeroGradient;\n")}
}}
""")
    _w(case / "0" / "k", header("volScalarField", "k", "0") + f"""
dimensions      [0 2 -2 0 0 0 0];
internalField   uniform {k_inf:.6g};
boundaryField
{{
{bfield(f"        type inletOutlet;\n        inletValue uniform {k_inf:.6g};\n        value uniform {k_inf:.6g};\n",
        f"        type kqRWallFunction;\n        value uniform {k_inf:.6g};\n", "        type zeroGradient;\n")}
}}
""")
    _w(case / "0" / "omega", header("volScalarField", "omega", "0") + f"""
dimensions      [0 0 -1 0 0 0 0];
internalField   uniform {omega_inf:.6g};
boundaryField
{{
{bfield(f"        type inletOutlet;\n        inletValue uniform {omega_inf:.6g};\n        value uniform {omega_inf:.6g};\n",
        f"        type omegaWallFunction;\n        value uniform {omega_inf:.6g};\n", "        type zeroGradient;\n")}
}}
""")
    eps_inf = CMU * k_inf * omega_inf  # (same turbulence level for every model)
    nut_inf = nu * 3.0
    _w(case / "0" / "epsilon", header("volScalarField", "epsilon", "0") + f"""
dimensions      [0 2 -3 0 0 0 0];
internalField   uniform {eps_inf:.6g};
boundaryField
{{
{bfield(f"        type inletOutlet;\n        inletValue uniform {eps_inf:.6g};\n        value uniform {eps_inf:.6g};\n",
        f"        type epsilonWallFunction;\n        value uniform {eps_inf:.6g};\n", "        type zeroGradient;\n")}
}}
""")
    _w(case / "0" / "nuTilda", header("volScalarField", "nuTilda", "0") + f"""
dimensions      [0 2 -1 0 0 0 0];
internalField   uniform {nut_inf:.6g};
boundaryField
{{
{bfield(f"        type inletOutlet;\n        inletValue uniform {nut_inf:.6g};\n        value uniform {nut_inf:.6g};\n",
        "        type fixedValue;\n        value uniform 0;\n", "        type zeroGradient;\n")}
}}
""")
    _w(case / "0" / "nut", header("volScalarField", "nut", "0") + f"""
dimensions      [0 2 -1 0 0 0 0];
internalField   uniform 0;
boundaryField
{{
{bfield("        type calculated;\n        value uniform 0;\n",
        "        type nutUSpaldingWallFunction;\n        value uniform 0;\n", "        type calculated;\n        value uniform 0;\n")}
}}
""")
    _w(case / "constant" / "transportProperties", header("dictionary", "transportProperties", "constant") + f"""
transportModel  Newtonian;
nu              {nu:.6g};
""")
    turb_model = s.get("turbulence", "kOmegaSST")
    des = turb_model == "kOmegaSSTDDES"
    if des and s.get("analysis") != "transient":
        raise ValueError("Vortex-resolving DES needs a transient analysis (it resolves the eddies in time).")
    # DDES: k-omega SST in the boundary layers, the eddies away from the walls resolved by the mesh (delayed DES
    # keeps the boundary layers in RANS mode); the settings of OpenFOAM's wallMountedHump tutorial
    _w(case / "constant" / "turbulenceProperties", header("dictionary", "turbulenceProperties", "constant")
       + ("simulationType  laminar;\n" if turb_model == "laminar" else """
simulationType  LES;
LES
{
    LESModel        kOmegaSSTDDES;
    turbulence      on;
    printCoeffs     on;
    kOmegaSSTDDESCoeffs { useSigma true; }
    delta           DeltaOmegaTilde;
    DeltaOmegaTildeCoeffs { }
}
""" if des else f"""
simulationType  RAS;
RAS
{{
    RASModel        {turb_model};
    turbulence      on;
    printCoeffs     on;
}}
"""))
    # rotating zones: MRF (the zone's air is solved in a rotating frame; walls inside it rotate) ...
    mrf = [z for z in mzones if z["method"] == "mrf"]
    if mrf:
        _w(case / "constant" / "MRFProperties", header("dictionary", "MRFProperties", "constant") + "".join(f"""
MRF_{z['name']}
{{
    cellZone        {z['name']};
    active          yes;
    nonRotatingPatches ();
    origin          {_vec(z['center'])};
    axis            {_vec(z['axis'])};
    omega           {z['omega']:.8g};
}}
""" for z in mrf))
    # ... or a sliding mesh: the zone's cells turn; its boundary becomes a pair of cyclicAMI patches
    ami_p = lambda z, part, side: f"""            {side}
            {{
                name            {z['name']}_AMI_{part}{1 if side == 'master' else 2};
                type            cyclicAMI;
                matchTolerance  0.0001;
                lowWeightCorrection 0.2;
                neighbourPatch  {z['name']}_AMI_{part}{2 if side == 'master' else 1};
                transform       noOrdering;
            }}"""  # noqa: E731
    if sliding:
        _w(case / "system" / "createBafflesDict", header("dictionary", "createBafflesDict", "system")
           + "\ninternalFacesOnly true;\nbaffles\n{\n" + "".join(f"""    {z['name']}_{part}
    {{
        type            faceZone;
        zoneName        {z['name']}_{part}_faces;
        patches
        {{
{ami_p(z, part, 'master')}
{ami_p(z, part, 'slave')}
        }}
    }}
""" for z in sliding for part in z["ami_parts"]) + "}\n")
    if len(sliding) == 1:
        z = sliding[0]
        _w(case / "constant" / "dynamicMeshDict", header("dictionary", "dynamicMeshDict", "constant") + f"""
dynamicFvMesh   dynamicMotionSolverFvMesh;
motionSolverLibs (fvMotionSolvers);
motionSolver    solidBody;
cellZone        {z['name']};
solidBodyMotionFunction rotatingMotion;
origin          {_vec(z['center'])};
axis            {_vec(z['axis'])};
omega           {z['omega']:.8g};
""")
    elif sliding:  # several turning zones (two propellers): each its own cell zone and its own motion
        _w(case / "constant" / "dynamicMeshDict", header("dictionary", "dynamicMeshDict", "constant") + """
dynamicFvMesh   dynamicMotionSolverFvMesh;
motionSolverLibs (fvMotionSolvers);
motionSolver    multiSolidBodyMotionSolver;
multiSolidBodyMotionSolverCoeffs
{
""" + "".join(f"""    {z['name']}
    {{
        cellZone        {z['name']};
        solidBodyMotionFunction rotatingMotion;
        rotatingMotionCoeffs
        {{
            origin          {_vec(z['center'])};
            axis            {_vec(z['axis'])};
            omega           {z['omega']:.8g};
        }}
    }}
""" for z in sliding) + "}\n")

    # moving parts: faces oscillate, the mesh deforms (Laplacian smoothing, stiffer close to the moving faces)
    if deforming:
        names = " ".join(p["name"] for p in deforming)
        _w(case / "constant" / "dynamicMeshDict", header("dictionary", "dynamicMeshDict", "constant") + f"""
dynamicFvMesh   dynamicMotionSolverFvMesh;
motionSolverLibs (fvMotionSolvers);
motionSolver    displacementLaplacian;
displacementLaplacianCoeffs
{{
    diffusivity     inverseDistance ({names});
}}
""")
        ents = ['    ".*"\n    {\n        type            fixedValue;\n        value           uniform (0 0 0);\n    }']
        ents += [f'    {n}\n    {{\n        type            symmetry;\n    }}' for n in sym_names]
        fa = {f["id"]: f for f in faces}
        for p in deforming:
            prm = p["bc"]["params"]
            ax = np.asarray(AXES[prm["axis"]], float)
            w = 2 * math.pi * float(prm["frequency"])
            if prm["motion"] == "rotate":
                fs_ = [fa[i] for i in p["bc"]["faces"] if i in fa]
                Asum = sum(f["area"] for f in fs_) or 1.0
                org = sum(np.asarray(f["centroid"], float) * f["area"] for f in fs_) / Asum
                ents.append(f"    {p['name']}\n    {{\n        type            angularOscillatingDisplacement;\n"
                            f"        axis            {_vec(ax)};\n        origin          {_vec(org)};\n"
                            f"        angle0          0;\n        amplitude       {math.radians(float(prm['amplitude'])):.8g};\n"
                            f"        omega           {w:.8g};\n        value           uniform (0 0 0);\n    }}")
            else:
                ents.append(f"    {p['name']}\n    {{\n        type            oscillatingDisplacement;\n"
                            f"        amplitude       {_vec(ax * float(prm['amplitude']) / 1000)};\n"
                            f"        omega           {w:.8g};\n        value           uniform (0 0 0);\n    }}")
        _w(case / "0" / "pointDisplacement", header("pointVectorField", "pointDisplacement", "0") + """
dimensions      [0 1 0 0 0 0 0];
internalField   uniform (0 0 0);
boundaryField
{
""" + "\n".join(ents) + "\n}\n")

    # actuator disks: momentum source = -(thrust + rotor drag) (force ON the air), kinematic units (divide by rho)
    fvo = []
    for r in rotor_info:
        F = -(np.asarray(r["axis"]) * r["thrust_N"] + np.asarray(r["H_N"])) / rho
        fvo.append(f"""{r['zone']}
{{
    type            vectorSemiImplicitSource;
    active          true;
    selectionMode   cellZone;
    cellZone        {r['zone']};
    volumeMode      absolute;
    injectionRateSuSp
    {{
        U           ({_vec(F)} 0);
    }}
}}
""")
    if fvo:
        _w(case / "constant" / "fvOptions", header("dictionary", "fvOptions", "constant") + "\n".join(fvo))

    # moments about the centre of gravity: airframe bounding-box centre + the user's offsets
    cofr = (np.mean(np.array(info["parts_bbox"]).reshape(2, 3), axis=0)
            + float(s.get("cg_up_mm") or 0.0) * 1e-3 * np.array(AXES[s.get("up_axis", "+z")], float)
            + float(s.get("cg_forward_mm") or 0.0) * 1e-3 * np.array(AXES[s.get("forward_axis", "+x")], float))
    zone_forces = "".join(f"""    forces_{z['name']}
    {{
        type            forces;
        libs            (forces);
        writeControl    timeStep;
        writeInterval   1;
        log             false;
        patches         ({zone_walls[z['name']]['patch']});
        rho             rhoInf;
        rhoInf          {rho};
        CofR            {_vec(z['center'])};
    }}
""" for z in mzones if z["name"] in zone_walls)
    funcs = f"""    forces
    {{
        type            forces;
        libs            (forces);
        writeControl    timeStep;
        writeInterval   1;
        log             true;
        patches         ({' '.join(force_patches)});
        rho             rhoInf;
        rhoInf          {rho};
        CofR            {_vec(cofr)};
    }}
{zone_forces}    yPlus
    {{
        type            yPlus;
        libs            (fieldFunctionObjects);
        writeControl    writeTime;
    }}
    Q
    {{
        type            Q;
        libs            (fieldFunctionObjects);
        writeControl    writeTime;
    }}
"""
    n_procs = int(s.get("n_procs", 1))
    it = int(s.get("iterations", 1500))
    # transient: first time step from the smallest cells (~ L/400 near the walls) and the fastest flow
    tr = transient_settings(s, 0.5 * (L / 400) / max(U_ref, 1e-3))
    if tr and sliding and not tr["fixed_dt"]:
        # sliding mesh: the time step follows the rotation (0.5 degree per step), not Courant 1 in the few tiny
        # cells at the blade edges (1e-6 s: 48 h for two turns); PIMPLE outer correctors keep it stable there
        w = max(abs(z["omega"]) for z in sliding)
        tr.update(fixed_dt=True, dt=min(math.radians(0.5) / w, tr["write_interval"]))
    if tr and sliding:
        frame_interval(tr, s, max(abs(z["omega"]) for z in sliding))
    app = "pimpleFoam" if tr else "simpleFoam"
    _common_system(case, app, it, funcs, n_procs, transient=tr)
    _w(case / "system" / "fvSchemes", header("dictionary", "fvSchemes", "system") + f"""
ddtSchemes      {{ default {'backward' if tr else 'steadyState'}; }}""" + """
gradSchemes
{
    default         Gauss linear;
    grad(U)         cellLimited Gauss linear 1;
    grad(k)         cellLimited Gauss linear 1;
    grad(omega)     cellLimited Gauss linear 1;
}
divSchemes
{
    default         none;
""" + f"""    div(phi,U)      {'Gauss LUST grad(U);  // DES: low dissipation, the eddies are not damped' if des else 'bounded Gauss linearUpwind grad(U);'}
""" + """    div(phi,k)      bounded Gauss upwind;  // robust at the actuator-disk edges (no k undershoots)
    div(phi,omega)  bounded Gauss upwind;
    div((nuEff*dev2(T(grad(U))))) Gauss linear;
}
laplacianSchemes { default Gauss linear limited corrected 0.5; }
interpolationSchemes { default linear; }
snGradSchemes   { default limited corrected 0.5; }
wallDist        { method meshWave; }
""")
    if tr:
        _w(case / "system" / "fvSolution", header("dictionary", "fvSolution", "system") + """
solvers
{
    p
    {
        solver          GAMG;
        smoother        GaussSeidel;
        tolerance       1e-6;
        relTol          0.01;
    }
    pFinal
    {
        solver          GAMG;
        smoother        GaussSeidel;
        tolerance       1e-6;
        relTol          0;
    }
    "(U|k|omega)"
    {
        solver          smoothSolver;
        smoother        symGaussSeidel;
        tolerance       1e-7;
        relTol          0.1;
    }
    "(U|k|omega)Final"
    {
        solver          smoothSolver;
        smoother        symGaussSeidel;
        tolerance       1e-7;
        relTol          0;
    }
}
PIMPLE
{
    nOuterCorrectors 1;
    nCorrectors     2;
    nNonOrthogonalCorrectors 1;
}
""")
        if sliding or deforming:  # moving mesh: flux correction after each mesh motion (+ the mesh-motion solve)
            fs = (case / "system" / "fvSolution").read_text()
            fs = fs.replace("solvers\n{", "solvers\n{\n    \"pcorr.*\"\n    {\n        solver          GAMG;\n"
                            "        smoother        GaussSeidel;\n        tolerance       1e-2;\n        relTol          0;\n    }"
                            "\n    \"cellDisplacement.*\"\n    {\n        solver          GAMG;\n        smoother        GaussSeidel;\n"
                            "        tolerance       1e-7;\n        relTol          0;\n    }", 1)
            fs = fs.replace("    nNonOrthogonalCorrectors 1;\n}", "    nNonOrthogonalCorrectors 1;\n    correctPhi      yes;\n}", 1)
            if sliding:
                # time step from the rotation: Courant > 1 in the small cells at a blade (up to ~100 at the blade
                # roots of a real propeller, which diverged within 3 steps with 3 plain outer correctors). Outer
                # correctors with under-relaxation inside the time step (none on the final one, so the step is
                # time-accurate) until it has converged - the usual large-time-step PIMPLE setup. 5, not 10: the
                # pressure residual control below is not reached on a tet mesh with AMI (each outer corrector starts
                # at 0.1-0.5), so every one of them runs; 5 kept a 2.7 M cell propeller stable, 3 (with 1
                # non-orthogonal corrector) was 41 % faster again from a developed flow (Advanced settings)
                fs = fs.replace("    nOuterCorrectors 1;", "    nOuterCorrectors 5;", 1)
                fs = fs.replace("    nNonOrthogonalCorrectors 1;\n    correctPhi      yes;\n}",
                                "    nNonOrthogonalCorrectors 2;\n    correctPhi      yes;\n"
                                "    turbOnFinalIterOnly true;\n"
                                "    outerCorrectorResidualControl\n    {\n"
                                "        U { tolerance 1e-4; relTol 0; }\n        p { tolerance 1e-3; relTol 0; }\n    }\n}\n"
                                "relaxationFactors\n{\n    fields { p 0.3; pFinal 1; }\n"
                                "    equations { \"(U|k|omega)\" 0.7; \"(U|k|omega)Final\" 1; }\n}", 1)
            _w(case / "system" / "fvSolution", fs)
    else:
        _w(case / "system" / "fvSolution", header("dictionary", "fvSolution", "system") + """
solvers
{
    p
    {
        solver          GAMG;
        smoother        GaussSeidel;
        tolerance       1e-7;
        relTol          0.05;
    }
    "(U|k|omega)"
    {
        solver          smoothSolver;
        smoother        symGaussSeidel;
        tolerance       1e-8;
        relTol          0.1;
    }
}
SIMPLE
{
    nNonOrthogonalCorrectors 1;
    consistent      no;
    residualControl
    {
        p               1e-4;
        U               1e-5;
        "(k|omega)"     1e-5;
    }
}
relaxationFactors
{
    fields          { p 0.3; }
    equations       { U 0.7; "(k|omega)" 0.6; }
}
""")
    # wall (inflation) layers: snappyHexMesh in layer-only mode pushes the tetrahedral mesh away from the walls
    # and fills the space with prism layers (a thin first cell for the boundary layer without tiny tetrahedra)
    lay = (project.get("mesh") or {}).get("layers") or {}
    # (a hex-dominant mesh already has its layers: they are made together with it, not added again at the run)
    n_lay = int(lay.get("n") or 0) if wall_names and not hexmesh else 0
    clamp = None
    if n_lay:
        g = float(lay.get("growth", 1.2))
        # automatic first layer: y+ ~ 30 for the wall-function models, a resolved boundary layer when laminar
        first = layer_plan(V, L, nu, s.get("turbulence", "kOmegaSST"), n_lay, g, project.get("moving_zones") or [],
                           lay.get("first"), lay.get("y_plus"))["first"]
        if lay.get("clamp"):
            # (#33) all the layers together fit inside a share (half) of the local thickness of the body on most of the wall: fewer layers, or a thinner one
            from . import layermesh
            ck = lay["clamp"]
            fr = float(ck.get("fraction", 0.5))
            th = layermesh.wall_thickness(faces, "external_aero", L, 1.5 * first * (g ** n_lay - 1.0) / (g - 1.0) / fr)
            clamp = layermesh.clamp_layers(first, g, n_lay, th, fr, float(ck.get("coverage", 0.95)))
            n_lay, first = clamp["n"], clamp["first"]
        size = (f"relativeSizes       false;\n    firstLayerThickness {float(first):.8g};\n"
                f"    minThickness        {float(first) * 0.1:.8g};")
        # tolerant layers (opt-in): with the strict limits snappyHexMesh removes layers wherever the cells are small
        # and the wall is sharp, which is exactly where a leading edge needs them
        tol = LAYER_TOLERANT if lay.get("tolerant") else LAYER_STRICT
        lays = "".join(f'        "{w}" {{ nSurfaceLayers {n_lay}; }}\n' for w in wall_names)
        _w(case / "system" / "snappyHexMeshDict", header("dictionary", "snappyHexMeshDict", "system") + f"""
castellatedMesh false;
snap            false;
addLayers       true;
geometry        {{}}
castellatedMeshControls
{{
    maxLocalCells 1000000; maxGlobalCells 100000000; minRefinementCells 0; nCellsBetweenLevels 1;
    features (); refinementSurfaces {{}}; refinementRegions {{}}; resolveFeatureAngle 30;
    locationInMesh (0 0 0); allowFreeStandingZoneFaces true;
}}
snapControls {{ nSmoothPatch 3; tolerance 2.0; nSolveIter 30; nRelaxIter 5; }}
addLayersControls
{{
    {size}
    layers
    {{
{lays}    }}
    expansionRatio      {g:.6g};
    nGrow               0;
    featureAngle        130;
    slipFeatureAngle    30;
    nRelaxIter          5;
    nSmoothSurfaceNormals 1;
    nSmoothNormals      3;
    nSmoothThickness    10;
    maxFaceThicknessRatio {tol['mfr']};
    maxThicknessToMedialRatio {tol['mtm']};
    minMedialAxisAngle  90;
    nBufferCellsNoExtrude 0;
    nLayerIter          {tol['iters']};{tol['relaxed_iter']}
}}
meshQualityControls
{{
    maxNonOrtho 70; maxBoundarySkewness 20; maxInternalSkewness 4; maxConcave 80; minVol 1e-15;
    minTetQuality -1e30; minArea -1; minTwist 0.02; minDeterminant 0.001; minFaceWeight 0.02;
    minVolRatio 0.01; minTriangleTwist -1; nSmoothScale 4; errorReduction 0.75;
    relaxed {{ {tol['relaxed']} }}
}}
mergeTolerance 1e-6;
""")

    # parallel sliding mesh: keep each interface (both sides) on one processor - a distributed AMI crashes MS-MPI
    # ("MPI_Bcast: Message truncated") in the Windows build
    ami_split = bool(sliding) and n_procs > 1
    if ami_split:
        _w(case / "system" / "topoSetDict", header("dictionary", "topoSetDict", "system") + "\nactions\n(\n"
           + "".join(f"    {{ name {z['name']}_AMIfaces; type faceSet; action new; source patchToFace; "
                     f"patch \"{z['name']}_AMI.*\"; }}\n" for z in sliding) + ");\n")
        dpf = case / "system" / "decomposeParDict"
        dpf.write_text(dpf.read_text() + "constraints\n{\n    ami\n    {\n        type    singleProcessorFaceSets;\n"
                       "        sets    (" + " ".join(f"({z['name']}_AMIfaces {k % n_procs})" for k, z in enumerate(sliding)) + ");\n    }\n}\n")
    _allrun(case, "runApplication checkMesh -allGeometry -allTopology\n"
            + ("runApplication snappyHexMesh -overwrite\n" if n_lay else "")
            + ("runApplication createBaffles -overwrite\nrunApplication mergeOrSplitBaffles -split -overwrite\n"
               if sliding else "")
            + "runApplication renumberMesh -overwrite\n"
            + ("runApplication topoSet\n" if ami_split else ""), app, n_procs, all_times=bool(tr))

    meta = {
        "study": "external_aero", "app": app, "regions": [], "rho": rho, "nu": nu, "transient": tr,
        "U_inf": Uinf.tolist(), "speed": V, "k_inf": k_inf, "omega_inf": omega_inf,
        "patches": [{"name": p["name"], "type": p["type"], "bc_type": p["bc"]["type"]} for p in patches],
        "force_patches": force_patches, "rotors": rotor_info, "moving_zones": mzones,
        "ref_area": projected_area(faces, Uinf if V > 0 else AXES[s.get("up_axis", "+z")]),
        "ref_length": L, "CofR": cofr.tolist(), "symmetry": planes,
        "mesh_quality": q, "n_cells": pm["n_cells"], "layer_clamp": clamp,
        "steps": [
            {"app": "checkMesh", "args": ["-allGeometry", "-allTopology"], "log": "log.checkMesh", "allow_fail": True},
            *([{"app": "snappyHexMesh", "args": ["-overwrite"], "log": "log.snappyHexMesh"}] if n_lay else []),
            *([{"app": "createBaffles", "args": ["-overwrite"], "log": "log.createBaffles"},
               # (the interface points are still shared: split them, or the zone drags the static cells with it)
               {"app": "mergeOrSplitBaffles", "args": ["-split", "-overwrite"], "log": "log.mergeOrSplitBaffles"}]
              if sliding else []),
            {"app": "renumberMesh", "args": ["-overwrite"], "log": "log.renumberMesh"},
            *([{"app": "topoSet", "args": [], "log": "log.topoSet"}] if ami_split else []),
            {"app": app, "args": [], "log": f"log.{app}"},
        ],
    }
    (case / "simwb_case.json").write_text(json.dumps(meta, indent=1))
    return meta


# ----------------------------------------------------------------------------
# conjugate heat transfer
# ----------------------------------------------------------------------------

def flow_regime(project: dict, faces: list, fluid_ids=()) -> dict:
    """Reynolds numbers of the inlet duct and of the narrowest fin channel -> laminar / turbulent suggestion."""
    fl = materials.get_fluid(project["settings"].get("fluid", "air"))
    nu = fl["mu"] / fl["rho"]
    inlets = [b for b in project.get("bcs", []) if b["type"] == "velocity_inlet" and b["faces"]]
    if not inlets:
        return {"suggested": "kOmegaSST", "note": "no inlet"}
    fa = {f["id"]: f for f in faces}
    bc = inlets[0]
    A = sum(fa[i]["area"] for i in bc["faces"] if i in fa)
    prm = bc["params"]
    along = prm.get("direction", "normal") != "normal"
    if along:  # uniform flow along an axis (curved inlet)
        A = inlet_area_along([fa[i] for i in bc["faces"] if i in fa], AXES[prm["direction"]])
    U = prm["flow_rate"] / 3600.0 / A if prm.get("flow_rate", 0) > 0 else prm["velocity"]
    Dh = math.sqrt(max(A, 1e-12))
    re_duct = U * Dh / nu
    sizes = project.get("mesh", {}).get("stats", {}).get("sizes", {}) or {}
    gap = sizes.get("min_gap")
    n = np.asarray(AXES[prm["direction"]] if along else fa[bc["faces"][0]]["normal"], float)
    flow_ax = int(np.argmax(np.abs(n)))
    # fin channels (between two part walls): hydraulic diameter of the s x H slot and its length along the flow
    chans = []
    for c in sizes.get("channels") or []:
        dims = np.asarray(c["box"], float)
        s_w = float(c["gap"])
        nax = int(np.argmin(np.abs(dims - s_w)))
        if nax == flow_ax:
            continue  # walls facing the flow, not a through-flow channel
        hax = ({0, 1, 2} - {nax, flow_ax}).pop()
        H, Lc = float(dims[hax]), float(dims[flow_ax])
        if H > 0 and Lc > 0:
            chans.append((s_w, 2 * s_w * H / (s_w + H), Lc, H, nax, c.get("lo"), c.get("hi")))
    re_gap = dh_ch = l_ch = s_ch = h_ch = t_fin = None
    boxes = []
    U_ch = U
    if chans or gap:
        # the parts block part of the duct: the air speeds up through the open area (continuity)
        wetted = [dict(f, kind="body") for f in faces
                  if f.get("kind") == "solid_outer" or (f.get("kind") == "interface" and set(f["volumes"]) & set(fluid_ids))]
        blocked = projected_area(wetted, n)
        U_ch = U * A / max(A - blocked, 0.2 * A)
        if chans:
            s_ch, dh_ch, l_ch, h_ch, n_ax, *_ = min(chans, key=lambda c: c[:4])  # narrowest channel
            same = [c for c in chans if abs(c[0] - s_ch) < 0.05 * s_ch and c[4] == n_ax and c[5]]
            boxes = [(c[5], c[6]) for c in same]
            mids = sorted(0.5 * (lo[n_ax] + hi[n_ax]) for lo, hi in boxes)
            if len(mids) >= 2:
                pitch = float(np.median(np.diff(mids)))
                if s_ch < pitch < 4 * s_ch:
                    t_fin = pitch - s_ch  # fin thickness from the channel pitch
        else:
            dh_ch = 2 * gap  # slot of unknown height ~ 2 * gap
        re_gap = U_ch * dh_ch / nu
    governing = re_gap if re_gap is not None else re_duct
    developing = l_ch is not None and l_ch / dh_ch < 10
    # short fin channels stay laminar well above 2300: the boundary layers never become fully developed
    laminar = governing < 2300 or (developing and governing < 4000)
    re_L = None
    if re_gap is None and project.get("domain", {}).get("enclosure_mode") == "auto_box":
        # parts in a box of free air: boundary layers on the part (flat plate, laminar below Re_L ~ 5e5)
        wet = [f for f in faces if f.get("kind") == "solid_outer" or f.get("interface")]
        pts = np.concatenate([np.asarray(f["mesh"]["vertices"], float).reshape(-1, 3) for f in wet
                              if f.get("mesh", {}).get("vertices")] or [np.zeros((1, 3))])
        L_part = float(np.ptp(pts[:, flow_ax]))
        re_L = U * L_part / nu
        laminar = re_L < 5e5
        governing = re_L
    sugg = "laminar" if laminar else "kOmegaSST"
    transitional = (3e5 <= re_L <= 5e5) if re_L is not None else 1800 <= governing <= 4000
    if re_L is not None:
        note = f"Part length Reynolds number ~{re_L:.3g} (free air around the part, length {L_part * 1e3:.0f} mm)"
    elif re_gap is not None:
        note = f"Fin-channel Reynolds number ~{re_gap:.0f} (D_h {dh_ch * 1e3:.1f} mm, channel speed ~{U_ch:.2f} m/s"
        note += f", length {l_ch / dh_ch:.1f} D_h)" if l_ch else ")"
    else:
        note = f"Duct Reynolds number ~{re_duct:.0f}"
    note += " -> laminar flow" if sugg == "laminar" else " -> turbulent flow (k-omega SST)"
    if laminar and governing >= 2300 and re_L is None:
        note += " (short, developing channel)"
    if transitional:
        note += ("; this is in the transitional range: laminar and turbulent models can differ by "
                 "~30 %, compare both for important decisions")
    return {"suggested": sugg, "Re_duct": float(re_duct), "Re_channel": None if re_gap is None else float(re_gap),
            "Re_part_length": re_L,
            "velocity": float(U), "channel_velocity": float(U_ch), "transitional": bool(transitional), "note": note,
            "channel_Dh": dh_ch, "channel_L_over_Dh": (l_ch / dh_ch) if l_ch else None,
            # for the correlation cross-check of the heat transfer (results_loader)
            "channel_gap": s_ch, "channel_height": h_ch, "channel_length": l_ch,
            "fin_thickness": t_fin or sizes.get("feature"), "fin_thickness_from_pitch": t_fin is not None,
            "channel_boxes": boxes, "flow_axis": flow_ax,
            "wetted_area_by_body": _wetted_areas(faces, fluid_ids)}


def natural_regime(project: dict, info: dict, g) -> dict:
    """No fan: laminar or turbulent from the Rayleigh number of the parts' height (air, an assumed 30 K rise)."""
    fl = materials.get_fluid(project["settings"].get("fluid", "air"))
    gv = np.asarray(g, float)
    up = -gv / (np.linalg.norm(gv) or 1.0)
    bb = np.asarray(info.get("parts_bbox") or info["bbox"], float)
    H = float(abs(np.dot(bb[3:] - bb[:3], up))) or float(np.max(bb[3:] - bb[:3]))
    nu = fl["mu"] / fl["rho"]
    alpha = nu / fl["Pr"]
    dT, T = 30.0, float(project["settings"].get("T_room", 25.0)) + 273.15 + 15.0
    ra = 9.81 / T * dT * H ** 3 / (nu * alpha)
    lam = ra < 1e9
    return {"suggested": "laminar" if lam else "kOmegaSST", "Rayleigh": float(ra), "height_m": H,
            "velocity_scale": float(max(0.05, math.sqrt(9.81 / T * dT * H))),
            "note": f"No fan: Rayleigh number ~{ra:.2g} (parts {H * 1e3:.0f} mm tall, ~30 K warmer than the room) -> "
                    + ("laminar natural convection" if lam else "turbulent natural convection (k-omega SST)"),
            "transitional": bool(3e8 <= ra <= 3e9)}


def _wetted_areas(faces: list, fluid_ids) -> dict:
    """Area of each solid body's faces that touch the air (body id as str -> m2)."""
    out = {}
    for f in faces:
        vols = set(f.get("volumes") or [])
        if len(vols) > 1 and vols & set(fluid_ids):
            for v in vols - set(fluid_ids):
                out[str(v)] = out.get(str(v), 0.0) + float(f["area"])
    return out


def write_cht(case: Path, project: dict, mesh: dict, faces: list, info: dict, bodies: list) -> dict:
    s = project["settings"]
    fl = materials.get_fluid(s.get("fluid", "air"))
    bcs = project.get("bcs", [])
    body_cfg = project.get("bodies", {})
    Tinit = float(s.get("T_initial", 25.0)) + 273.15

    fluid_bodies = [b for b in bodies if b["role"] == "fluid"]
    solid_bodies = [b for b in bodies if b["role"] == "solid"]
    if len(fluid_bodies) != 1:
        raise ValueError("Expected exactly one air region")
    inlets = [b for b in bcs if b["type"] == "velocity_inlet"]
    outlets = [b for b in bcs if b["type"] == "pressure_outlet"]
    natural = s.get("air_flow") == "natural"
    auto_box = project.get("domain", {}).get("enclosure_mode") == "auto_box"
    T_room = float(s.get("T_room", 25.0))
    radiation = s.get("radiation") == "on"
    eps = float(s.get("emissivity", 0.85))
    if natural:
        if s.get("gravity", "-z") == "off":
            raise ValueError("Natural convection (no fan) needs gravity: set the gravity direction.")
        if inlets:
            raise ValueError("No fan is selected but there is an air inlet: remove it, or set Air flow to 'fan'.")
        sinks = outlets or auto_box or [b for b in bcs if b["type"] in ("wall_fixed_temperature", "wall_convection")]
        if not sinks:
            raise ValueError("With no fan the heat needs a way out: mark openings (outlet / opening faces), use an "
                             "automatic air box (open to the room), or give walls a fixed temperature or heat loss.")
        Tinit = float(s.get("T_initial", T_room)) + 273.15
    elif not inlets or not outlets:
        raise ValueError("Conjugate heat transfer needs at least one air inlet and one air outlet.")

    # region names
    region_of_body = {}
    used = {"air"}
    region_of_body[fluid_bodies[0]["id"]] = "air"
    for b in solid_bodies:
        nm = sanitize(body_cfg.get(str(b["id"]), {}).get("name") or b["name"])
        base, i = nm, 2
        while nm in used:
            nm, i = f"{base}_{i}", i + 1
        used.add(nm)
        region_of_body[b["id"]] = nm
    for b in solid_bodies:
        if not body_cfg.get(str(b["id"]), {}).get("material"):
            raise ValueError(f"Body '{b['name']}' has no material assigned.")

    zone_names = [region_of_body[b["id"]] for b in bodies]
    zid = {b["id"]: i for i, b in enumerate(bodies)}
    zone_of_tet = np.array([zid[int(v)] for v in mesh["tet_vol"]]) if len(bodies) < 2 else \
        np.vectorize(zid.get)(mesh["tet_vol"])

    surf2patch, patches = build_patches("cht_enclosure", faces, bcs,
                                        open_box=({"T": T_room} if natural else True) if auto_box else False)
    case.mkdir(parents=True, exist_ok=True)
    pm, q = _write_mesh(case, mesh, surf2patch, patches, zone_of_tet, zone_names)

    solids = [region_of_body[b["id"]] for b in solid_bodies]
    _w(case / "constant" / "regionProperties", header("dictionary", "regionProperties", "constant") + f"""
regions
(
    fluid       (air)
    solid       ({' '.join(solids)})
);
""")
    gdir = s.get("gravity", "-z")
    g = [0, 0, 0] if gdir == "off" else [G * c for c in AXES[gdir]]
    regime = natural_regime(project, info, g) if natural else flow_regime(project, faces, [b["id"] for b in fluid_bodies])
    turb = s.get("turbulence", "auto")
    if turb == "auto":
        turb = regime["suggested"]

    # ---- fluid constant
    eos = "perfectGas" if materials.is_gas(fl) else "rhoConst"
    eos_coeffs = "" if eos == "perfectGas" else f"    equationOfState {{ rho {fl['rho']}; }}\n"
    _w(case / "constant" / "air" / "thermophysicalProperties",
       header("dictionary", "thermophysicalProperties", "constant/air") + f"""
thermoType
{{
    type            heRhoThermo;
    mixture         pureMixture;
    transport       const;
    thermo          hConst;
    equationOfState {eos};
    specie          specie;
    energy          sensibleEnthalpy;
}}
mixture
{{
    specie          {{ molWeight {fl['M']}; }}
    thermodynamics  {{ Cp {fl['cp']}; Hf 0; }}
    transport       {{ mu {fl['mu']}; Pr {fl['Pr']}; }}
{eos_coeffs}}}
""")
    for gloc in ("constant", "constant/air"):  # newer ESI versions read the top-level constant/g
        _w(case / gloc / "g", header("uniformDimensionedVectorField", "g", gloc) + f"""
dimensions      [0 1 -2 0 0 0 0];
value           {_vec(g)};
""")
    if turb == "laminar":
        tp = "simulationType  laminar;\n"
    else:
        tp = f"simulationType  RAS;\nRAS\n{{\n    RASModel        {turb};\n    turbulence      on;\n    printCoeffs     on;\n}}\n"
    _w(case / "constant" / "air" / "turbulenceProperties",
       header("dictionary", "turbulenceProperties", "constant/air") + tp)
    rad = "radiation       off;\nradiationModel  none;\n"
    if radiation:
        # surface-to-surface radiation through the (transparent) air: discrete ordinates, 4 x 2 x 3 = 24 directions
        _w(case / "constant" / "air" / "radiationProperties",
           header("dictionary", "radiationProperties", "constant/air") + """
radiation       on;
radiationModel  fvDOM;
fvDOMCoeffs
{
    nPhi        2;
    nTheta      3;
    tolerance   1e-3;
    maxIter     5;
}
solverFreq      10;
absorptionEmissionModel none;
scatterModel    none;
sootModel       none;
transmissivityModel none;
""")
        open_names = [p["name"] for p in patches if p["bc"]["type"] == "pressure_outlet"]
        ents = [f'".*"\n{{\n    type            lookup;\n    emissivity      {eps:g};\n    absorptivity    {eps:g};\n'
                f'    transmissivity  0;\n}}']
        ents += [f'{n}\n{{\n    type            lookup;\n    emissivity      1;\n    absorptivity    1;\n'
                 f'    transmissivity  0;\n}}' for n in open_names]  # (openings: the room, a black body)
        _w(case / "constant" / "air" / "boundaryRadiationProperties",
           header("dictionary", "boundaryRadiationProperties", "constant/air") + "\n".join(ents) + "\n")
        _w(case / "0.orig" / "air" / "IDefault", header("volScalarField", "IDefault", "0/air") + """
dimensions      [1 0 -3 0 0 0 0];
internalField   uniform 0;
boundaryField
{
    ".*"
    {
        type            greyDiffusiveRadiation;
        value           uniform 0;
    }
}
""")
    else:
        _w(case / "constant" / "air" / "radiationProperties",
           header("dictionary", "radiationProperties", "constant/air") + rad)

    # ---- solids
    heat_info = {}
    for b in solid_bodies:
        r = region_of_body[b["id"]]
        cfg = body_cfg.get(str(b["id"]), {})
        m = materials.get_solid(cfg["material"])
        _w(case / "constant" / r / "thermophysicalProperties",
           header("dictionary", "thermophysicalProperties", f"constant/{r}") + f"""
thermoType
{{
    type            heSolidThermo;
    mixture         pureMixture;
    transport       constIso;
    thermo          hConst;
    equationOfState rhoConst;
    specie          specie;
    energy          sensibleEnthalpy;
}}
mixture
{{
    specie          {{ molWeight 50; }}
    transport       {{ kappa {m['k']}; }}
    thermodynamics  {{ Hf 0; Cp {m['cp']}; }}
    equationOfState {{ rho {m['rho']}; }}
}}
""")
        _w(case / "constant" / r / "radiationProperties",
           header("dictionary", "radiationProperties", f"constant/{r}") + rad)
        Q = float(cfg.get("heat_W") or 0.0)
        heat_info[r] = Q
        fvo = case / "constant" / r / "fvOptions"
        if Q > 0:
            _w(fvo, header("dictionary", "fvOptions", f"constant/{r}") + f"""
heatSource
{{
    type            scalarSemiImplicitSource;
    active          true;
    selectionMode   all;
    volumeMode      absolute;
    injectionRateSuSp
    {{
        h           ({Q:.8g} 0);
    }}
}}
""")
    if sum(heat_info.values()) <= 0:
        raise ValueError("No solid generates heat - set 'Heat generated' on at least one body (e.g. the chip).")

    # ---- boundary conditions (fields in 0.orig/<region>)
    def inlet_turb(bc):
        A = sum(f["area"] for f in faces if f["id"] in bc["faces"])
        prm = bc["params"]
        if prm.get("direction", "normal") != "normal":  # uniform flow along an axis: the area seen along it
            A = inlet_area_along([f for f in faces if f["id"] in bc["faces"]], AXES[prm["direction"]])
        U = prm["flow_rate"] / 3600.0 / A if prm.get("flow_rate", 0) > 0 else prm["velocity"]
        Dh = math.sqrt(max(A, 1e-12))
        k = 1.5 * (0.05 * max(U, 0.05)) ** 2
        om = math.sqrt(k) / (CMU ** 0.25 * 0.07 * Dh)
        return U, k, om, A

    fl_entries = {f: [] for f in ("T", "U", "p", "p_rgh", "k", "omega", "epsilon", "nuTilda", "nut", "alphat")}
    nu_air = fl["mu"] / fl["rho"]
    k0 = 1.5 * (0.05 * 1.0) ** 2
    om0 = 1.0
    inlet_meta = []
    for p in patches:
        n, bc, t = p["name"], p["bc"], p["bc"]["type"]
        prm = bc.get("params", {})
        E = fl_entries
        if t == "velocity_inlet":
            U, k, om, A = inlet_turb(bc)
            inlet_meta.append({"patch": n, "velocity": U, "area": A, "flow_m3s": U * A})
            k0, om0 = k, om
            Tin = prm["T"] + 273.15
            E["T"].append((n, f"type fixedValue; value uniform {Tin};"))
            if prm.get("direction", "normal") != "normal":
                E["U"].append((n, f"type fixedValue; value uniform {_vec(U * np.asarray(AXES[prm['direction']], float))};"))
            elif prm.get("flow_rate", 0) > 0:
                E["U"].append((n, f"type flowRateInletVelocity; volumetricFlowRate {prm['flow_rate'] / 3600.0:.8g}; "
                                  f"value uniform (0 0 0);"))
            else:
                E["U"].append((n, f"type surfaceNormalFixedValue; refValue uniform {-U:.8g}; value uniform (0 0 0);"))
            E["p_rgh"].append((n, "type fixedFluxPressure; value $internalField;"))
            E["k"].append((n, f"type fixedValue; value uniform {k:.6g};"))
            E["omega"].append((n, f"type fixedValue; value uniform {om:.6g};"))
            E["epsilon"].append((n, f"type fixedValue; value uniform {CMU * k * om:.6g};"))
            E["nuTilda"].append((n, f"type fixedValue; value uniform {3 * nu_air:.6g};"))
            E["nut"].append((n, "type calculated; value uniform 0;"))
            E["alphat"].append((n, "type calculated; value uniform 0;"))
        elif t == "pressure_outlet":
            Tb = prm["T_backflow"] + 273.15
            E["T"].append((n, f"type inletOutlet; inletValue uniform {Tb}; value uniform {Tb};"))
            E["U"].append((n, "type pressureInletOutletVelocity; value uniform (0 0 0);"))
            # one outlet with a fan: static pressure fixed there. No fan, openings at different heights: the room
            # air's own hydrostatic pressure, i.e. p_rgh fixed (total pressure: air coming in starts from rest)
            E["p_rgh"].append((n, "type totalPressure; p0 uniform 1e5; value uniform 1e5;" if natural
                               else "type prghPressure; p uniform 1e5; value uniform 1e5;"))
            E["k"].append((n, "type inletOutlet; inletValue $internalField; value $internalField;"))
            E["omega"].append((n, "type inletOutlet; inletValue $internalField; value $internalField;"))
            E["epsilon"].append((n, "type inletOutlet; inletValue $internalField; value $internalField;"))
            E["nuTilda"].append((n, "type inletOutlet; inletValue $internalField; value $internalField;"))
            E["nut"].append((n, "type calculated; value uniform 0;"))
            E["alphat"].append((n, "type calculated; value uniform 0;"))
        elif t == "symmetry":  # a mirror plane: a constraint patch, every field needs the matching constraint type
            for fld in fl_entries:
                E[fld].append((n, "type symmetry;"))
        elif t == "slip_wall":  # free-air side / symmetry: frictionless and adiabatic
            E["U"].append((n, "type slip;"))
            for fld in ("k", "omega", "epsilon", "nuTilda", "nut", "alphat"):
                E[fld].append((n, "type zeroGradient;"))
        elif t == "wall_fixed_temperature":
            E["T"].append((n, f"type fixedValue; value uniform {prm['T'] + 273.15};"))
        elif t == "wall_convection":
            E["T"].append((n, f"type externalWallHeatFluxTemperature; mode coefficient; h uniform {prm['h']}; "
                              f"Ta constant {prm['T_inf'] + 273.15}; kappaMethod fluidThermo; "
                              f"{'qr qr; ' if radiation else ''}value $internalField;"))

    wall_defaults = {
        "T": "type zeroGradient;",
        "U": "type noSlip;",
        "p": "type calculated; value $internalField;",
        "p_rgh": "type fixedFluxPressure; value $internalField;",
        "k": "type kqRWallFunction; value $internalField;",
        "omega": "type omegaWallFunction; value $internalField;",
        "epsilon": "type epsilonWallFunction; value $internalField;",
        "nuTilda": "type fixedValue; value uniform 0;",
        "nut": "type nutUSpaldingWallFunction; value uniform 0;",
        "alphat": "type compressible::alphatJayatillekeWallFunction; Prt 0.85; value uniform 0;",
    }
    fluid_dims = {"T": ("volScalarField", "[0 0 0 1 0 0 0]", f"uniform {Tinit}"),
                  "U": ("volVectorField", "[0 1 -1 0 0 0 0]", "uniform (0 0 0)"),
                  "p": ("volScalarField", "[1 -1 -2 0 0 0 0]", "uniform 1e5"),
                  "p_rgh": ("volScalarField", "[1 -1 -2 0 0 0 0]", "uniform 1e5"),
                  "k": ("volScalarField", "[0 2 -2 0 0 0 0]", f"uniform {k0:.6g}"),
                  "omega": ("volScalarField", "[0 0 -1 0 0 0 0]", f"uniform {om0:.6g}"),
                  "epsilon": ("volScalarField", "[0 2 -3 0 0 0 0]", f"uniform {CMU * k0 * om0:.6g}"),
                  "nuTilda": ("volScalarField", "[0 2 -1 0 0 0 0]", f"uniform {3 * nu_air:.6g}"),
                  "nut": ("volScalarField", "[0 2 -1 0 0 0 0]", "uniform 0"),
                  "alphat": ("volScalarField", "[1 -1 -1 0 0 0 0]", "uniform 0")}
    interface_T = ("type compressible::turbulentTemperatureRadCoupledMixed; Tnbr T; "
                   "kappaMethod {km}; {qr}value $internalField;")
    # radiation: the air side knows the radiative flux qr at the wall; the solid side takes it from the air
    qr_f, qr_s = ("qr qr; qrNbr none; ", "qr none; qrNbr qr; ") if radiation else ("", "")
    # contact resistance between touching solids (thermal interface material / bolted joint), m2K/W.
    # OpenFOAM convention: the same layer is given on both sides of the interface.
    r_contact = float(s.get("contact_resistance", 0.0) or 0.0) * 1e-4  # input in K cm2/W
    for fld, (cls, dims, init) in fluid_dims.items():
        lines = [f'    ".*"\n    {{\n        {wall_defaults[fld]}\n    }}']
        if fld == "T":
            lines.append(f'    "air_to_.*"\n    {{\n        {interface_T.format(km="fluidThermo", qr=qr_f)}\n    }}')
        for n, e in fl_entries[fld]:
            lines.append(f"    {n}\n    {{\n        {e}\n    }}")
        _w(case / "0.orig" / "air" / fld, header(cls, fld, "0/air") + f"""
dimensions      {dims};
internalField   {init};
boundaryField
{{
{chr(10).join(lines)}
}}
""")
    for r in solids:
        lines = ['    ".*"\n    {\n        type zeroGradient;\n    }',
                 f'    "{r}_to_.*"\n    {{\n        {interface_T.format(km="solidThermo", qr="")}\n    }}',
                 f'    "{r}_to_air"\n    {{\n        {interface_T.format(km="solidThermo", qr=qr_s)}\n    }}']
        if r_contact > 0:
            for other in solids:
                if other != r:
                    lines.append(f"    {r}_to_{other}\n    {{\n        {interface_T.format(km='solidThermo', qr='')[:-len('value $internalField;')]}"
                                 f"thicknessLayers ({r_contact:.6g}); kappaLayers (1); value $internalField;\n    }}")
        for p in patches:
            bc, t = p["bc"], p["bc"]["type"]
            prm = bc.get("params", {})
            if t == "symmetry":
                lines.append(f"    {p['name']}\n    {{\n        type symmetry;\n    }}")
            elif t == "wall_fixed_temperature":
                lines.append(f"    {p['name']}\n    {{\n        type fixedValue; value uniform {prm['T'] + 273.15};\n    }}")
            elif t == "wall_convection":
                lines.append(f"    {p['name']}\n    {{\n        type externalWallHeatFluxTemperature; mode coefficient; "
                             f"h uniform {prm['h']}; Ta constant {prm['T_inf'] + 273.15}; kappaMethod solidThermo; "
                             f"value $internalField;\n    }}")
        _w(case / "0.orig" / r / "T", header("volScalarField", "T", f"0/{r}") + f"""
dimensions      [0 0 0 1 0 0 0];
internalField   uniform {Tinit};
boundaryField
{{
{chr(10).join(lines)}
}}
""")
        p_lines = ['    ".*"\n    {\n        type calculated;\n        value $internalField;\n    }']
        p_lines += [f"    {p['name']}\n    {{\n        type symmetry;\n    }}" for p in patches
                    if p["bc"]["type"] == "symmetry"]
        _w(case / "0.orig" / r / "p", header("volScalarField", "p", f"0/{r}") + f"""
dimensions      [1 -1 -2 0 0 0 0];
internalField   uniform 1e5;
boundaryField
{{
{chr(10).join(p_lines)}
}}
""")

    # ---- schemes / solution
    U_in = max([m["velocity"] for m in inlet_meta] + [regime.get("velocity_scale", 0.1)])
    L = info["ref_length"]
    tr = transient_settings(s, 0.5 * (L / 300) / U_in)
    ddt = "Euler" if tr else "steadyState"
    _w(case / "system" / "air" / "fvSchemes", header("dictionary", "fvSchemes", "system/air") + f"""
ddtSchemes      {{ default {ddt}; }}""" + """
gradSchemes
{
    default         Gauss linear;
    grad(U)         cellLimited Gauss linear 1;
    grad(h)         cellLimited Gauss linear 1;
    grad(k)         cellLimited Gauss linear 1;
    grad(omega)     cellLimited Gauss linear 1;
}
divSchemes
{
    default         none;
    div(phi,U)      bounded Gauss linearUpwind grad(U);
    div(phi,K)      bounded Gauss linear;
    div(phi,h)      bounded Gauss linearUpwind grad(h);
    div(phi,k)      bounded Gauss linearUpwind grad(k);
    div(phi,omega)  bounded Gauss linearUpwind grad(omega);
    div(phi,Ekp)    bounded Gauss linear;
    div(((rho*nuEff)*dev2(T(grad(U))))) Gauss linear;
    div(Ji,Ii_h)    bounded Gauss linearUpwind grad(Ii_h);
}
laplacianSchemes { default Gauss linear limited corrected 0.5; }
interpolationSchemes { default linear; }
snGradSchemes   { default limited corrected 0.5; }
wallDist        { method meshWave; }
""")
    _w(case / "system" / "air" / "fvSolution", header("dictionary", "fvSolution", "system/air") + """
solvers
{
    "rho.*"
    {
        solver          PCG;
        preconditioner  DIC;
        tolerance       1e-7;
        relTol          0;
    }
    p_rgh
    {
        solver          GAMG;
        smoother        GaussSeidel;
        tolerance       1e-7;
        relTol          0.01;
    }
    "(U|h|k|omega)"
    {
        solver          smoothSolver;
        smoother        symGaussSeidel;
        tolerance       1e-8;
        relTol          0.1;
    }
    Ii
    {
        solver          GAMG;
        smoother        symGaussSeidel;
        tolerance       1e-4;
        relTol          0;
        maxIter         10;
    }
}
SIMPLE
{
    momentumPredictor yes;
    nNonOrthogonalCorrectors 1;
    pRefCell        0;
    pRefValue       1e5;
    rhoMin          0.2;
    rhoMax          3;
    residualControl
    {
        p_rgh           1e-4;
        U               1e-4;
        h               1e-6;
        "(k|omega)"     1e-4;
    }
}
relaxationFactors
{
    fields          { rho 1; p_rgh 0.7; }
    equations       { U 0.3; h 0.7; "(k|omega)" 0.7; }
}
""")
    if tr:  # PIMPLE: final-iteration solvers, no under-relaxation
        _w(case / "system" / "air" / "fvSolution", header("dictionary", "fvSolution", "system/air") + """
solvers
{
    "rho.*"
    {
        solver          PCG;
        preconditioner  DIC;
        tolerance       1e-7;
        relTol          0;
    }
    p_rgh
    {
        solver          GAMG;
        smoother        GaussSeidel;
        tolerance       1e-7;
        relTol          0.01;
    }
    p_rghFinal
    {
        solver          GAMG;
        smoother        GaussSeidel;
        tolerance       1e-7;
        relTol          0;
    }
    "(U|h|k|omega)"
    {
        solver          smoothSolver;
        smoother        symGaussSeidel;
        tolerance       1e-8;
        relTol          0.1;
    }
    "(U|h|k|omega)Final"
    {
        solver          smoothSolver;
        smoother        symGaussSeidel;
        tolerance       1e-8;
        relTol          0;
    }
}
PIMPLE
{
    momentumPredictor yes;
    nCorrectors     2;
    nNonOrthogonalCorrectors 1;
    pRefCell        0;
    pRefValue       1e5;
    rhoMin          0.2;
    rhoMax          3;
}
""")
    for r in solids:
        _w(case / "system" / r / "fvSchemes", header("dictionary", "fvSchemes", f"system/{r}") + f"""
ddtSchemes      {{ default {ddt}; }}""" + """
gradSchemes     { default Gauss linear; }
divSchemes      { default none; }
laplacianSchemes { default Gauss linear limited corrected 0.5; }
interpolationSchemes { default linear; }
snGradSchemes   { default limited corrected 0.5; }
""")
        _w(case / "system" / r / "fvSolution", header("dictionary", "fvSolution", f"system/{r}") + """
solvers
{
    "(h|hFinal)"
    {
        solver          PCG;
        preconditioner  DIC;
        tolerance       1e-8;
        relTol          0.05;
    }
}
""" + ("""PIMPLE
{
    nNonOrthogonalCorrectors 1;
}
""" if tr else """SIMPLE
{
    nNonOrthogonalCorrectors 1;
    residualControl { h 1e-6; }
}
relaxationFactors
{
    equations       { h 1; }
}
"""))
    # top-level dictionaries (required by some versions even for multi-region cases)
    _w(case / "system" / "fvSchemes", header("dictionary", "fvSchemes", "system") + f"""
ddtSchemes {{ default {ddt}; }}""" + """
gradSchemes { default Gauss linear; }
divSchemes { default none; }
laplacianSchemes { default Gauss linear corrected; }
interpolationSchemes { default linear; }
snGradSchemes { default corrected; }
""")
    _w(case / "system" / "fvSolution", header("dictionary", "fvSolution", "system")
       + ("\nPIMPLE\n{\n    nOuterCorrectors 1;\n}\n" if tr else "\n"))

    out_patches = [p["name"] for p in patches if p["bc"]["type"] == "pressure_outlet"]
    in_patches = [p["name"] for p in patches if p["bc"]["type"] == "velocity_inlet"]
    funcs = []
    for r in solids:
        funcs.append(f"""    T_{r}
    {{
        type            fieldMinMax;
        libs            (fieldFunctionObjects);
        region          {r};
        fields          (T);
        location        false;
        writeControl    timeStep;
        writeInterval   10;
        log             true;
    }}""")
    for pn in out_patches + in_patches:
        funcs.append(f"""    flow_{pn}
    {{
        type            surfaceFieldValue;
        libs            (fieldFunctionObjects);
        region          air;
        regionType      patch;
        name            {pn};
        operation       weightedAverage;
        weightField     phi;
        fields          (T p);
        writeFields     false;
        writeControl    timeStep;
        writeInterval   10;
        log             true;
    }}""")
    cooled = [p["name"] for p in patches if p["bc"]["type"] in ("wall_fixed_temperature", "wall_convection")]
    funcs.append(f"""    wallHeatFlux
    {{
        type            wallHeatFlux;
        libs            (fieldFunctionObjects);
        region          air;
        patches         ("air_to_.*"{''.join(' ' + c for c in cooled)});""" + """
        writeControl    writeTime;
        log             true;
    }""")
    n_procs = int(s.get("n_procs", 1))
    it = int(s.get("iterations", 2000))
    app = "chtMultiRegionFoam" if tr else "chtMultiRegionSimpleFoam"
    _common_system(case, app, it, "\n".join(funcs), n_procs, ["air"] + solids, transient=tr)
    # fan zones (MRF in the air region): the cell zone is made after the regions are split (cells belong to one zone)
    fans = project.get("moving_zones") or []
    if fans:
        _w(case / "system" / "air" / "topoSetDict", header("dictionary", "topoSetDict", "system/air") + "\nactions\n(\n"
           + "".join(f"""    {{ name {z['name']}; type cellZoneSet; action new; source cylinderToCell;
      p1 {_vec(np.asarray(z['center']) - np.asarray(AXES[z['axis']]) * z['length'] / 2)};
      p2 {_vec(np.asarray(z['center']) + np.asarray(AXES[z['axis']]) * z['length'] / 2)}; radius {z['radius']:.8g}; }}
""" for z in fans) + ");\n")
        _w(case / "constant" / "air" / "MRFProperties", header("dictionary", "MRFProperties", "constant/air") + "".join(f"""
MRF_{z['name']}
{{
    cellZone        {z['name']};
    active          yes;
    nonRotatingPatches ();
    origin          {_vec(z['center'])};
    axis            {_vec(AXES[z['axis']])};
    omega           {z['rpm'] * 2 * math.pi / 60:.8g};
}}
""" for z in fans))
    body = ("runApplication checkMesh -allGeometry -allTopology\n"
            "mkdir -p 0\n"
            "runApplication splitMeshRegions -cellZonesOnly -overwrite\n"
            + ("runApplication topoSet -region air\n" if fans else "") +
            "rm -rf 0 && cp -r 0.orig 0\n"
            "for region in $(foamListRegions); do\n"
            "    runApplication -s $region checkMesh -region $region\n"
            "done\n")
    _allrun(case, body, app, n_procs, regions=True, all_times=bool(tr))

    meta = {
        "study": "cht_enclosure", "app": app, "transient": tr, "symmetry": project.get("symmetry") or [],
        "regions": ["air"] + solids, "fluid_region": "air", "solid_regions": solids,
        "region_of_body": {str(k): v for k, v in region_of_body.items()},
        "moving_zones": [dict(z, omega=z["rpm"] * 2 * math.pi / 60, axis=list(AXES[z["axis"]])) for z in fans],
        "region_labels": {region_of_body[b["id"]]: (body_cfg.get(str(b["id"]), {}).get("name") or b["name"])
                          for b in bodies},
        "heat_W": heat_info, "inlets": inlet_meta, "outlet_patches": out_patches, "inlet_patches": in_patches,
        "natural": natural, "T_room_C": T_room if natural else None, "radiation": radiation,
        "emissivity": eps if radiation else None, "cooled_wall_patches": cooled,
        "patches": [{"name": p["name"], "type": p["type"], "bc_type": p["bc"]["type"]} for p in patches],
        "fluid": fl, "turbulence": turb, "flow_regime": regime, "T_initial_C": Tinit - 273.15, "contact_resistance_m2KW": r_contact,
        "mesh_quality": q, "n_cells": pm["n_cells"],
        "steps": [
            {"app": "checkMesh", "args": ["-allGeometry", "-allTopology"], "log": "log.checkMesh", "allow_fail": True},
            {"action": "mkdir0"},
            {"app": "splitMeshRegions", "args": ["-cellZonesOnly", "-overwrite"], "log": "log.splitMeshRegions"},
            {"action": "reset0"},
            *([{"app": "topoSet", "args": ["-region", "air"], "log": "log.topoSet"}] if fans else []),
            *[{"app": "checkMesh", "args": ["-region", r], "log": f"log.checkMesh.{r}", "allow_fail": True}
              for r in ["air"] + solids],
            {"app": app, "args": [], "log": f"log.{app}"},
        ],
    }
    (case / "simwb_case.json").write_text(json.dumps(meta, indent=1))
    return meta


def write_case(case: Path, project: dict, mesh: dict, faces: list, info: dict, bodies: list) -> dict:
    if case.exists():
        shutil.rmtree(case)
    st = project["study"]
    if st == "external_aero":
        meta = write_external_aero(case, project, mesh, faces, info)
    elif st == "cht_enclosure":
        meta = write_cht(case, project, mesh, faces, info, bodies)
    else:
        raise ValueError(f"Study {st} does not use OpenFOAM")
    _all_turbulence_models(case)
    return meta


def _all_turbulence_models(case: Path):
    """Give epsilon and nuTilda the same schemes, solvers and relaxation as omega, so that any RAS model can be chosen
    (in the settings or by editing turbulenceProperties) without touching the other dictionaries."""
    for f in list(case.glob("system/**/fvSchemes")) + list(case.glob("system/**/fvSolution")):
        s = f.read_text()
        s2 = s.replace("(U|h|k|omega)", "(U|h|k|omega|epsilon|nuTilda)").replace("(U|k|omega)", "(U|k|omega|epsilon|nuTilda)")
        s2 = s2.replace('"(k|omega)"', '"(k|omega|epsilon|nuTilda)"')
        out = []
        for line in s2.splitlines():
            out.append(line)
            for key in ("div(phi,omega)", "grad(omega)"):
                if line.strip().startswith(key):
                    for new in ("epsilon", "nuTilda"):
                        out.append(line.replace("omega", new))
        s2 = "\n".join(out) + ("\n" if s2.endswith("\n") else "")
        if s2 != s:
            f.write_text(s2, newline="\n")
