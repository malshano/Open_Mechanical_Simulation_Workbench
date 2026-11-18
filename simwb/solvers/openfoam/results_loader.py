"""Load OpenFOAM results (ASCII, latest time) into a ResultSet + engineering summary."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import numpy as np

from ...results import ResultSet
from ... import studies
from ... import symmetry as sym
from ...studies import AXES
from .foamio import exists as foam_exists, read_field, read_polymesh


def latest_time(case: Path, region: str | None = None) -> str | None:
    times = []
    for d in Path(case).iterdir():
        if d.is_dir():
            try:
                t = float(d.name)
            except ValueError:
                continue
            if t > 0 and (region is None or (d / region).is_dir()):
                times.append((t, d.name))
    return max(times)[1] if times else None


def display_mesh(pm: dict):
    """Tetrahedra for display. A tetrahedral mesh is used as it is (returns the tet array). Other cells (the
    prism layers snappyHexMesh adds on the walls) are split into tetrahedra round their centre: returns a dict
    {tets, tet_cell, first_tet, tris, tri_face, cp_cell, cp_vert} - every face fan-triangulated, each triangle joined
    to the centre of the owner (and neighbour) cell; the centres are extra points after pm["points"], the mean of
    the cell's face vertices (cp_cell/cp_vert pairs: recomputed when the mesh moves)."""
    if pm["faces"] is not None:
        try:
            return tets_from_polymesh(pm)
        except ValueError:  # triangles only, but not all cells are tetrahedra
            pass
    polys = pm["poly_faces"] if pm["faces"] is None else list(pm["faces"])
    own, nei, nc, npt = pm["owner"], pm["neighbour"], pm["n_cells"], len(pm["points"])
    k = np.array([len(f) for f in polys], np.int64)
    flat = np.concatenate(polys).astype(np.int64)
    starts = np.r_[0, np.cumsum(k)[:-1]]
    nt = k - 2
    tri_face = np.repeat(np.arange(len(polys)), nt)
    j = np.arange(nt.sum()) - np.repeat(np.cumsum(nt) - nt, nt)
    s = starts[tri_face]
    tris = np.stack([flat[s], flat[s + j + 1], flat[s + j + 2]], 1)
    nfi = len(nei)
    inner = tri_face < nfi
    tets = np.vstack([np.c_[tris, npt + own[tri_face]], np.c_[tris[inner], npt + nei[tri_face[inner]]]])
    tet_cell = np.r_[own[tri_face], nei[tri_face[inner]]]
    order = np.argsort(tet_cell, kind="stable")
    tets, tet_cell = tets[order], tet_cell[order]
    first_tet = np.searchsorted(tet_cell, np.arange(nc))
    cp_cell = np.r_[np.repeat(own, k), np.repeat(nei, k[:nfi])]
    cp_vert = np.r_[flat, flat[:k[:nfi].sum()]]
    dm = {"tets": tets, "tet_cell": tet_cell, "first_tet": first_tet, "tris": tris, "tri_face": tri_face,
          "cp_cell": cp_cell, "cp_vert": cp_vert, "n_cells": nc}
    P = display_points(pm["points"], dm)
    a, b, c, d = (P[tets[:, i]] for i in range(4))
    neg = np.einsum("ij,ij->i", np.cross(b - a, c - a), d - a) < 0
    tets[neg] = tets[neg][:, [0, 2, 1, 3]]
    return dm


def display_points(points, dm):
    """Mesh points (+ cell centres when the cells were split round their centre)."""
    if not isinstance(dm, dict):
        return points
    cen = np.zeros((dm["n_cells"], 3))
    np.add.at(cen, dm["cp_cell"], points[dm["cp_vert"]])
    cen /= np.bincount(dm["cp_cell"], minlength=dm["n_cells"])[:, None]
    return np.vstack([points, cen])


def tets_from_polymesh(pm: dict) -> np.ndarray:
    if pm["faces"] is None:
        raise ValueError("Mesh has non-triangular faces (not a tetrahedral mesh)")
    faces, own, nei = pm["faces"], pm["owner"], pm["neighbour"]
    nc = pm["n_cells"]
    cells = np.concatenate([own, nei])
    fidx = np.concatenate([np.arange(len(own)), np.arange(len(nei))])
    order = np.argsort(cells, kind="stable")
    counts = np.bincount(cells, minlength=nc)
    if not (counts == 4).all():
        raise ValueError("Mesh contains non-tetrahedral cells")
    nodes = faces[fidx[order]].reshape(nc, 12)
    nodes.sort(axis=1)
    tets = nodes[:, ::3].copy()
    # every node of a tet appears in exactly 3 of its faces
    p = pm["points"]
    v = np.einsum("ij,ij->i", np.cross(p[tets[:, 1]] - p[tets[:, 0]], p[tets[:, 2]] - p[tets[:, 0]]),
                  p[tets[:, 3]] - p[tets[:, 0]])
    neg = v < 0
    tets[neg] = tets[neg][:, [0, 2, 1, 3]]
    return tets


def _face_areas(pm, start, n):
    f = pm["faces"][start:start + n]
    p = pm["points"]
    sf = 0.5 * np.cross(p[f[:, 1]] - p[f[:, 0]], p[f[:, 2]] - p[f[:, 0]])
    return sf


_PM_CACHE: dict = {}  # polyMesh per region while one case is loaded (transient: many times, one mesh)


def _region_data(case: Path, region: str | None, time: str, fields: dict):
    mdir = case / "constant" / (region or "") / "polyMesh" if region else case / "constant" / "polyMesh"
    if str(mdir) not in _PM_CACHE:
        pm = read_polymesh(mdir)
        _PM_CACHE[str(mdir)] = (pm, display_mesh(pm))
    pm, tets = _PM_CACHE[str(mdir)]
    tdir = case / time / region if region else case / time
    moved = tdir / "polyMesh" / "points"
    if foam_exists(moved):  # moving mesh (sliding zone): the cells are where they were at this time
        from .foamio import read_points

        pm = {**pm, "points": read_points(moved), "moved": True}
    data = {}
    for fname in fields:
        fp = tdir / fname
        if foam_exists(fp):
            data[fname] = read_field(fp, pm["n_cells"], pm["patches"])
    return pm, tets, data


def _parse_moments(text: str):
    b = list(re.finditer(r"Sum of moments\s*\n\s*Total\s*:\s*\(([^)]*)\)", text))
    return np.array(b[-1].group(1).split(), float) if b else None


def force_history(text: str):
    """(times, total forces (n,3), pressure (n,3), viscous (n,3), moments (n,3) or None) from a solver log."""
    from .runner import RE_TIME

    tpos = [(m.start(), float(m.group(1))) for m in RE_TIME.finditer(text)]
    starts = [p for p, _ in tpos]
    import bisect

    T, Ft, Fp, Fv, Mt = [], [], [], [], []
    mom = {m.start(): np.array(m.group(1).split(), float)
           for m in re.finditer(r"Sum of moments\s*\n\s*Total\s*:\s*\(([^)]*)\)", text)}
    mkeys = sorted(mom)
    for m in re.finditer(r"Sum of forces\s*\n\s*Total\s*:\s*\(([^)]*)\)\s*\n\s*Pressure\s*:\s*\(([^)]*)\)\s*\n"
                         r"\s*Viscous\s*:\s*\(([^)]*)\)", text):
        k = bisect.bisect_right(starts, m.start()) - 1
        if k < 0:
            continue
        T.append(tpos[k][1])
        Ft.append(np.array(m.group(1).split(), float))
        Fp.append(np.array(m.group(2).split(), float))
        Fv.append(np.array(m.group(3).split(), float))
        j = bisect.bisect_right(mkeys, m.start())
        Mt.append(mom[mkeys[j]] if j < len(mkeys) else np.full(3, np.nan))
    if not T:
        return None
    M = np.array(Mt)
    return np.array(T), np.array(Ft), np.array(Fp), np.array(Fv), (None if np.isnan(M).all() else M)


def _dat_rows(path: Path) -> np.ndarray:
    """Numeric rows of a function-object .dat file (comment lines skipped; brackets of older formats ignored)."""
    rows = []
    for line in path.read_text(errors="ignore").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            rows.append([float(x) for x in line.replace("(", " ").replace(")", " ").split()])
        except ValueError:
            continue
    n = min((len(r) for r in rows), default=0)
    return np.array([r[:n] for r in rows]) if rows and n >= 4 else np.zeros((0, 4))


def zone_force_history(case: Path, name: str):
    """(times, total force (n,3), total moment about the zone centre (n,3)) of the walls inside a moving zone, from
    postProcessing/forces_<zone>/<start time>/{force,moment}.dat (a continued run adds a start-time folder)."""
    root = Path(case) / "postProcessing" / f"forces_{name}"
    if not root.is_dir():
        return None
    F, M = [], []
    for d in sorted((x for x in root.iterdir() if x.is_dir()), key=lambda x: float(x.name) if x.name.replace(".", "", 1).isdigit() else 0):
        f, m = d / "force.dat", d / "moment.dat"
        if f.exists() and m.exists():
            F.append(_dat_rows(f))
            M.append(_dat_rows(m))
    if not F:
        return None
    F, M = np.vstack(F), np.vstack(M)
    n = min(len(F), len(M))
    if n == 0:
        return None
    F, M = F[:n], M[:n]
    t, keep = F[:, 0], np.ones(n, bool)
    keep[:-1] = t[1:] > t[:-1]  # (a restarted run repeats times: the later rows win)
    return t[keep], F[keep, 1:4], M[keep, 1:4]


def propeller_summary(case: Path, z: dict, rho: float, V: float, U_dir, transient: dict | None) -> dict | None:
    """Thrust, torque, power and propeller coefficients of the walls in a moving zone. Averaged over the last full
    revolution of a transient run (the second half if it is shorter), the last 10 % of the iterations of a steady
    one."""
    h = zone_force_history(case, z["name"])
    if h is None or not len(h[0]):
        return None
    t, F, M = h
    a = np.asarray(z["axis"], float)
    w = abs(float(z["omega"]))
    if transient:
        rev = 2 * np.pi / w if w > 0 else 0.0
        t0 = t[-1] - rev if rev and t[-1] - t[0] >= rev else 0.5 * (t[0] + t[-1])
        sel = t >= t0
        window = (f"last revolution ({t0 * 1000:.4g}-{t[-1] * 1000:.4g} ms)" if rev and t[-1] - t[0] >= rev
                  else f"second half of the run (t >= {t0 * 1000:.4g} ms; less than one revolution was run)")
    else:
        sel = np.arange(len(t)) >= int(0.9 * len(t))
        window = f"last {int(sel.sum())} iterations"
    Fm, Mm = F[sel].mean(0), M[sel].mean(0)
    ax = F[sel] @ a
    T, Q = float(abs(Fm @ a)), float(Mm @ a)
    P = abs(Q) * w
    n = w / (2 * np.pi)
    D = 2 * float(z.get("blade_radius") or z.get("radius") or 0)
    out = {"name": z["name"], "thrust_N": T, "thrust_direction": (a if Fm @ a >= 0 else -a).tolist(),
           "force_N": Fm.tolist(), "torque_Nm": abs(Q), "power_W": P, "diameter_m": D, "rpm": z["rpm"],
           "average_over": window,
           "thrust_fluctuation_percent": float(100 * ax.std() / abs(ax.mean())) if transient and ax.mean() else None}
    if n > 0 and D > 0:
        e = np.asarray(out["thrust_direction"])
        Va = float(V * (np.asarray(U_dir) @ -e)) if V > 0 else 0.0  # flight speed along the thrust (inflow into the disc)
        CT, CP = T / (rho * n ** 2 * D ** 4), P / (rho * n ** 3 * D ** 5)
        J = Va / (n * D)
        out.update(CT=CT, CP=CP, J=J, axial_speed_m_s=Va, tip_speed_m_s=w * D / 2, tip_mach=w * D / 2 / 343.0,
                   efficiency=(J * CT / CP) if CP > 0 and J > 0 else None)
    return out


def _parse_forces(case: Path, app: str, avg_from: float | None = None):
    log = case / f"log.{app}"
    if not log.exists():
        return None
    text = log.read_text(errors="ignore")
    if avg_from is not None:  # transient: time average over t >= avg_from
        h = force_history(text)
        if h is not None:
            T, Ft, Fp, Fv, M = h
            sel = T >= avg_from
            if not sel.any():
                sel = T >= T[-1]
            return {"total": Ft[sel].mean(0), "pressure": Fp[sel].mean(0), "viscous": Fv[sel].mean(0),
                    "moment": None if M is None else np.nanmean(M[sel], 0), "history": h}
    blocks = list(re.finditer(
        r"Sum of forces\s*\n\s*Total\s*:\s*\(([^)]*)\)\s*\n\s*Pressure\s*:\s*\(([^)]*)\)\s*\n\s*Viscous\s*:\s*\(([^)]*)\)",
        text))
    if blocks:
        b = blocks[-1]
        f = [np.array(b.group(i).split(), float) for i in (1, 2, 3)]
        return {"total": f[0], "pressure": f[1], "viscous": f[2], "moment": _parse_moments(text)}
    # older format: "Pressure : (..)" "Viscous : (..)"
    pr = re.findall(r"Pressure\s*:\s*\(([^)]*)\)", text)
    vi = re.findall(r"Viscous\s*:\s*\(([^)]*)\)", text)
    if pr and vi:
        p, v = np.array(pr[-1].split(), float), np.array(vi[-1].split(), float)
        return {"total": p + v, "pressure": p, "viscous": v, "moment": _parse_moments(text)}
    return None


def load(case: Path, project: dict) -> tuple[ResultSet, dict]:
    case = Path(case)
    meta = json.loads((case / "simwb_case.json").read_text())
    _PM_CACHE.clear()
    try:
        if meta["study"] == "external_aero":
            rs, summary = _load_aero(case, project, meta)
        else:
            rs, summary = _load_cht(case, project, meta)
            if project.get("symmetry"):
                _cht_whole_device(summary, project["symmetry"])
        if meta.get("transient"):
            _add_frames(case, meta, rs, summary)
    finally:
        _PM_CACHE.clear()
    log = case / f"log.{meta['app']}"
    if log.exists() and not meta.get("transient"):
        its = re.findall(r"^Time = ([0-9.eE+-]+)\s*$", log.read_text(errors="ignore"), re.M)
        if its and float(its[-1]) > summary["time"] + 0.5:
            summary["warning_stale"] = (f"The saved fields are from iteration {summary['time']:g}, but the solver ran to "
                                        f"{float(its[-1]):g}; the displayed fields lag behind.")
    return rs, summary


# totals of a cooling run that scale with the size of the model (the kept part of a mirror-symmetric device)
CHT_EXTENSIVE = ("heat_input_W", "heat_out_W", "heat_carried_by_air_W", "heat_radiated_out_W", "heat_through_walls_W",
                 "heat_conducted_at_openings_W", "flow_m3_per_h")


def _cht_whole_device(summary: dict, planes: list):
    """Heat flows and the air flow rate of the whole device from the kept part; the thermal resistance (temperature
    rise per watt) of the whole device is that of the part divided by the number of copies. Temperatures and speeds
    are local and need no change."""
    k = sym.mass_factor(planes)
    for key in CHT_EXTENSIVE:
        if isinstance(summary.get(key), (int, float)):
            summary[key] = summary[key] * k
    if isinstance(summary.get("thermal_resistance_K_per_W"), (int, float)):
        summary["thermal_resistance_K_per_W"] = summary["thermal_resistance_K_per_W"] / k
    summary["symmetry"] = {"planes": len(planes), "copies": k,
                           "note": f"Heat flows, the air flow rate and the thermal resistance are those of the whole "
                                   f"device ({k} copies of the solved part); temperatures and speeds are local values. "
                                   "The heat of a part listed under Parts is that of its kept part."}
    summary["assumptions"] = list(summary.get("assumptions") or []) + [
        "Symmetry: the flow and the heat are mirror images across the plane: only the kept part was solved."]


def saved_times(case: Path, region: str | None = None) -> list:
    """Written time directories (t > 0), oldest first, as (float, name)."""
    out = []
    for d in Path(case).iterdir():
        try:
            t = float(d.name)
        except ValueError:
            continue
        if d.is_dir() and t > 0 and (region is None or (d / region).is_dir()):
            out.append((t, d.name))
    return sorted(out)


def _frame_fields(case, meta, t):
    """Display fields at one saved time: ({field: cell array}, {field: boundary-face values})."""
    if meta["study"] == "external_aero":
        pm, tets, data = _region_data(case, None, t, ["p", "U", "Q"])
        rho = meta["rho"]
        cf = {"p": data["p"]["internal"] * rho, "U": data["U"]["internal"]}
        if "Q" in data:
            cf["Q"] = data["Q"]["internal"]
        parts = [("air", pm, tets, cf,
                  {"p": {k: v * rho for k, v in data["p"]["patches"].items()}, "U": data["U"]["patches"]}, "")]
        out = _assemble(parts)
        f, sv = out[4], out[9]
        if isinstance(tets, dict):  # cells split for display: keep the frames per cell (expanded when shown)
            f = cf
            cells = {"__map": tets["tet_cell"]}
        else:
            cells = {}
        cells.update({"Pressure": f["p"], "Velocity": f["U"]})
        surf = {"Pressure": sv["p"], "Velocity": np.linalg.norm(sv["U"], axis=1)}
        if "Q" in f:
            cells["Q-criterion"], surf["Q-criterion"] = f["Q"], sv["Q"]
        if pm.get("moved"):  # moving mesh: where the points are at this time (the propeller turns on the slider)
            cells["__nodes"] = out[0]
        return cells, surf
    parts = []
    for r in meta["regions"]:
        flds = ["T", "U", "p"] if r == meta.get("fluid_region", "air") else ["T"]
        pm, tets, data = _region_data(case, r, t, flds)
        cf, pv = {}, {}
        for fname, fd in data.items():
            off = 273.15 if fname == "T" else 1e5 if fname == "p" else 0.0
            cf[fname] = fd["internal"] - off
            pv[fname] = {k: v - off for k, v in fd["patches"].items()}
        parts.append((r, pm, tets, cf, pv, f"{r}:"))
    out = _assemble(parts)
    f, sv = out[4], out[9]
    names = {"T": "Temperature", "U": "Velocity", "p": "Pressure"}
    cells = {names[k]: v for k, v in f.items() if k in names}
    surf = {names[k]: (np.linalg.norm(v, axis=1) if k == "U" and np.ndim(v) == 2 else v) for k, v in sv.items() if k in names}
    return cells, surf


def _frame_workers(case: Path, n_frames: int) -> int:
    """How many processes read the saved times at once: one per core while the memory allows it (each holds the
    mesh and a time of a few million cells: about 1 GB per million cells, 4 GB are left to the rest of the
    computer). 1 = read them here, one after the other."""
    import os

    from ... import memlimit
    from .runner import RE_PROC_CELLS

    if n_frames < 4:
        return 1
    try:
        cells = sum(int(c) for _, c in RE_PROC_CELLS.findall((Path(case) / "log.decomposePar").read_text(errors="ignore")))
    except OSError:
        cells = 0
    need = max(2.0, 1.0 * cells / 1e6) * 2 ** 30
    avail = memlimit.memory()[1]
    return max(1, min(os.cpu_count() or 1, 8, n_frames // 2, int((avail - 6 * 2 ** 30) // need)))


def _read_frames(case, meta, names):
    """The display fields (cells, surf) of the saved times `names`, in order. A transient run with many times is
    read by several worker processes (reading is the slow part of loading the results and is a single core job);
    a time a worker did not deliver is read here."""
    n = _frame_workers(case, len(names))
    if n < 2:
        for nm in names:
            yield _frame_fields(case, meta, nm)
        return
    import shutil
    import subprocess
    import sys
    import tempfile

    root = Path(__file__).resolve().parents[3]
    out = Path(tempfile.mkdtemp(prefix="frames_", dir=case))
    procs = []
    try:
        for i in range(n):
            share = names[i::n]  # (interleaved: every worker reads the mesh once and the same number of times)
            lf = open(out / f"worker{i}.log", "w")
            procs.append((subprocess.Popen([sys.executable, "-u", "-m", "simwb.solvers.openfoam.frame_worker", str(case),
                                            str(out), *share], cwd=root, stdout=lf, stderr=subprocess.STDOUT,
                                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)), lf))
        for pr, lf in procs:
            pr.wait()
            lf.close()
        for nm in names:
            f = out / f"{nm}.npz"
            if f.exists():
                with np.load(f) as z:  # (plain arrays: "c:<name>" cell fields, "s:<name>" surface values)
                    cells = {k[2:]: z[k] for k in z.files if k.startswith("c:")}
                    surf = {k[2:]: z[k] for k in z.files if k.startswith("s:")}
                f.unlink()
                yield cells, surf
            else:
                yield _frame_fields(case, meta, nm)
    finally:
        for pr, lf in procs:
            if pr.poll() is None:
                pr.kill()
        shutil.rmtree(out, ignore_errors=True)


def _add_frames(case, meta, rs, summary):
    """Transient run: the display fields at every saved time (time slider) and the history plots."""
    times = saved_times(case, "air" if meta["regions"] else None)
    frames = {}
    for (t, name), (cells, surf) in zip(times, _read_frames(case, meta, [n for _, n in times])):
        fmap = cells.pop("__map", None)
        if fmap is not None:
            rs.frame_map = fmap
        for k, v in cells.items():
            if k in rs.fields or k == "__nodes":
                frames.setdefault(k, []).append(np.asarray(v, np.float32))
        for k, v in surf.items():
            if k in rs.fields:
                frames.setdefault("s:" + k, []).append(np.asarray(v, np.float32))
    if frames:
        rs.times = [t for t, _ in times]
        rs.frames = frames
    tr = meta["transient"]
    summary["transient"] = {"end_time_s": tr["end_time"], "saved_times": len(times),
                            "time_reached_s": times[-1][0] if times else 0.0}


def _thin(t, series, n=400):
    """At most n points of a history (every k-th, always keeping the last)."""
    k = max(1, len(t) // n)
    idx = list(range(0, len(t), k))
    if idx[-1] != len(t) - 1:
        idx.append(len(t) - 1)
    return {"t": [float(t[i]) for i in idx], "series": {name: [float(v[i]) for i in idx] for name, v in series.items()}}


def _assemble(parts):
    """parts: list of (region_name, pm, tets or display mesh (display_mesh), cell_fields{name:(n,)|(n,3)},
    patch_values{field:{patch:arr}}, group_prefix). Fields are per display tetrahedron, boundary values per
    boundary triangle."""
    nodes, tets, treg, regions = [], [], [], []
    surf, scell, sgrp, groups = [], [], [], []
    fields = {}
    svals = {}
    off_n = off_c = 0
    ntet = lambda t: len(t["tets"]) if isinstance(t, dict) else len(t)  # noqa: E731
    total_cells = sum(ntet(p[2]) for p in parts)
    all_fields = {}
    for p in parts:
        for fname, arr in p[3].items():
            all_fields.setdefault(fname, arr.shape[1:] if arr.ndim == 2 else ())
    for ri, (rname, pm, t, cf, pv, prefix) in enumerate(parts):
        dm = t if isinstance(t, dict) else None
        tt = dm["tets"] if dm else t
        cell_of = dm["tet_cell"] if dm else slice(None)  # display tet -> mesh cell
        regions.append(rname)
        pts = display_points(pm["points"], dm)
        nodes.append(pts)
        tets.append(tt + off_n)
        treg.append(np.full(len(tt), ri))
        for fname, arr in cf.items():
            if fname not in fields:
                shape = (total_cells, 3) if arr.ndim == 2 else (total_cells,)
                fields[fname] = np.full(shape, np.nan)
            fields[fname][off_c:off_c + len(tt)] = arr[cell_of]
        for p in pm["patches"]:
            if p["nFaces"] == 0 or p.get("type", "").startswith("cyclic"):
                continue  # sliding / periodic interfaces are inside the flow, not surfaces to show
            gname = f"{prefix}{p['name']}"
            groups.append(gname)
            gi = len(groups) - 1
            s, n = p["start"], p["nFaces"]
            if dm:  # the patch's faces as fan triangles
                a, b = np.searchsorted(dm["tri_face"], [s, s + n])
                fidx = dm["tri_face"][a:b] - s  # face within the patch, per triangle
                surf.append(dm["tris"][a:b] + off_n)
                owner = pm["owner"][s:s + n][fidx]
                scell.append(dm["first_tet"][owner] + off_c)
            else:
                fidx = slice(None)
                surf.append(pm["faces"][s:s + n] + off_n)
                owner = pm["owner"][s:s + n]
                scell.append(owner + off_c)
            sgrp.append(np.full(len(owner), gi))
            for fname, shape in all_fields.items():
                if fname not in cf:  # field not defined in this region (e.g. velocity in a solid)
                    svals.setdefault(fname, []).append(np.full((len(owner), *shape), np.nan))
                    continue
                vals = cf[fname][owner].copy()
                pvv = pv.get(fname, {}).get(p["name"])
                if pvv is not None and len(pvv) == n:
                    vals = pvv[fidx]
                svals.setdefault(fname, []).append(vals)
        off_n += len(pts)
        off_c += len(tt)
    sv = {k: np.concatenate(v) for k, v in svals.items()}
    return (np.vstack(nodes), np.vstack(tets), np.concatenate(treg), regions, fields,
            np.vstack(surf), np.concatenate(scell), np.concatenate(sgrp), groups, sv)


def _load_cht(case, project, meta):
    t = latest_time(case, "air")
    if t is None:
        raise RuntimeError("No results found - the solver has not written any time step yet.")
    parts = []
    summary = {"time": float(t), "solids": {}, "regions": meta["regions"]}
    raw = {}
    for r in meta["regions"]:
        flds = ["T", "U", "p", "wallHeatFlux", "qr"] if r == meta.get("fluid_region", "air") else ["T"]
        pm, tets, data = _region_data(case, r, t, flds)
        cf, pv = {}, {}
        for fname, fd in data.items():
            arr = fd["internal"]
            if fname == "T":
                arr = arr - 273.15
                pv["T"] = {k: v - 273.15 for k, v in fd["patches"].items()}
            else:
                pv[fname] = fd["patches"]
            cf[fname] = arr
        parts.append((r, pm, tets, cf, pv, f"{r}:"))
        raw[r] = (pm, tets, cf, pv)
    (nodes, tets, treg, regions, fields, surf, scell, sgrp, groups, sv) = _assemble(parts)
    rs_fields = {}
    if "T" in fields:
        rs_fields["Temperature"] = ("cell", fields["T"], "°C")
    if "U" in fields:
        rs_fields["Velocity"] = ("cell", fields["U"], "m/s")
    if "p" in fields:
        pa = fields["p"] - 1e5
        rs_fields["Pressure"] = ("cell", pa, "Pa (gauge)")
    svals = {}
    if "T" in sv:
        svals["Temperature"] = sv["T"]
    if "U" in sv:
        svals["Velocity"] = np.linalg.norm(sv["U"], axis=1) if sv["U"].ndim == 2 else sv["U"]
    if "p" in sv:
        svals["Pressure"] = sv["p"] - 1e5
    rs = ResultSet(nodes, tets, treg, regions, rs_fields, surf, scell, sgrp, groups, svals)

    # ---- engineering summary
    for r in meta["solid_regions"]:
        pm, tt, cf, pv = raw[r]
        T = cf["T"]
        allT = np.concatenate([T] + [v for v in pv.get("T", {}).values()])
        summary["solids"][r] = {"T_max": float(np.nanmax(allT)), "T_mean": float(np.nanmean(T)),
                                "heat_W": meta["heat_W"].get(r, 0.0),
                                "label": meta.get("region_labels", {}).get(r, r)}
        # area-weighted temperature of each interface (e.g. heatsink_to_air, heatsink_to_chip)
        pT = {}
        for p in pm["patches"]:
            v = pv.get("T", {}).get(p["name"])
            if p["nFaces"] and v is not None and len(v) == p["nFaces"] and "_to_" in p["name"]:
                A = np.linalg.norm(_face_areas(pm, p["start"], p["nFaces"]), axis=1)
                pT[p["name"]] = {"T_mean": float((v * A).sum() / A.sum()), "area_m2": float(A.sum())}
        summary["solids"][r]["interfaces"] = pT
    pm, tt, cf, pv = raw["air"]

    def patch_avg(field, names):
        tot = a_tot = 0.0
        for p in pm["patches"]:
            if p["name"] in names and p["nFaces"]:
                A = np.linalg.norm(_face_areas(pm, p["start"], p["nFaces"]), axis=1)
                v = pv.get(field, {}).get(p["name"])
                if v is None:
                    v = cf[field][pm["owner"][p["start"]:p["start"] + p["nFaces"]]]
                if np.ndim(v) == 2:
                    v = np.linalg.norm(v, axis=1)
                tot += float((v * A).sum())
                a_tot += float(A.sum())
        return tot / a_tot if a_tot else float("nan")

    fl = meta["fluid"]
    Q = sum(meta["heat_W"].values())
    tdir = case / t / "air"
    phi = read_field(tdir / "phi", 0, pm["patches"]) if foam_exists(tdir / "phi") else None

    def flux_sums(names):
        """mass flow (kg/s, + = out), mass-weighted T, total and static pressure over patches, from phi."""
        m = mT = mP = mPs = 0.0
        for p in pm["patches"]:
            if p["name"] not in names or not p["nFaces"] or phi is None:
                continue
            f = phi["patches"].get(p["name"])
            if f is None:
                continue
            sl = slice(p["start"], p["start"] + p["nFaces"])
            own = pm["owner"][sl]
            Tf = pv.get("T", {}).get(p["name"])
            Tf = cf["T"][own] if Tf is None else Tf
            pf = pv.get("p", {}).get(p["name"])
            pf = cf["p"][own] if pf is None else pf
            Uf = pv.get("U", {}).get(p["name"])
            Uf = cf["U"][own] if Uf is None else Uf
            rho = pf / (8314.46 / fl["M"] * (Tf + 273.15)) if fl.get("M") else fl["rho"]
            p0 = pf + 0.5 * rho * (Uf ** 2).sum(1)
            w = np.abs(f)
            m += float(f.sum())
            mT += float((w * Tf).sum())
            mP += float((w * p0).sum())
            mPs += float((w * pf).sum())
        tot = sum(float(np.abs(phi["patches"].get(p["name"], np.zeros(0))).sum())
                  for p in pm["patches"] if phi is not None and p["name"] in names)
        nan = float("nan")
        return m, (mT / tot if tot else nan), (mP / tot if tot else nan), (mPs / tot if tot else nan)

    m_out, Tout, p0out, psout = flux_sums(meta["outlet_patches"])
    m_in, Tin, p0in, psin = flux_sums(meta["inlet_patches"])
    if meta.get("natural"):
        return _natural_summary(rs, summary, raw, meta, project, case, phi, pm, cf, pv, fl, Q)
    if phi is None or math.isnan(Tin):  # fall back to area averages
        Tin, Tout = patch_avg("T", meta["inlet_patches"]), patch_avg("T", meta["outlet_patches"])
        p0in, p0out = patch_avg("p", meta["inlet_patches"]), patch_avg("p", meta["outlet_patches"])
        psin, psout = p0in, p0out
        flow = sum(i["flow_m3s"] for i in meta["inlets"])
        mdot = flow * 1e5 / (8314.46 / fl["M"] * (Tin + 273.15))
    else:
        mdot = -m_in
    # enthalpy flux leaving minus entering (cp constant): heat carried away by the air
    Q_air = 0.0
    if phi is not None:
        for p in pm["patches"]:
            if p["name"] in meta["outlet_patches"] + meta["inlet_patches"] and p["name"] in phi["patches"]:
                sl = slice(p["start"], p["start"] + p["nFaces"])
                Tf = pv.get("T", {}).get(p["name"])
                Tf = cf["T"][pm["owner"][sl]] if Tf is None else Tf
                Q_air += float((phi["patches"][p["name"]] * (Tf - Tin)).sum()) * fl["cp"]
    else:
        Q_air = mdot * fl["cp"] * (Tout - Tin)
    umag = np.linalg.norm(cf["U"], axis=1) if "U" in cf else None
    imbalance = abs(Q_air - Q) / Q * 100 if Q else None
    summary.update({
        "inlet_T": Tin, "outlet_T_mixed": Tout,
        "pressure_drop_Pa": p0in - p0out,
        # same (mass-flux) weighting as the total drop, so the two are comparable
        "static_pressure_drop_Pa": psin - psout,
        "static_pressure_drop_area_avg_Pa": patch_avg("p", meta["inlet_patches"]) - patch_avg("p", meta["outlet_patches"]),
        "flow_m3_per_h": sum(i["flow_m3s"] for i in meta["inlets"]) * 3600, "mass_flow_kg_s": mdot,
        "heat_input_W": Q, "heat_carried_by_air_W": Q_air, "energy_imbalance_percent": imbalance,
        "energy_balance_note": "Heat carried by the air = mass-flux-weighted enthalpy change between inlet and outlet.",
        "pressure_drop_note": ("Total-pressure drop (static + dynamic) is what a fan must supply; the static drop is what "
                               "a manometer across the duct reads. Both are mass-flux weighted over inlet and outlet."),
        "velocity_p995": float(np.percentile(umag, 99.5)) if umag is not None else None,
        "flow_regime": meta.get("flow_regime"), "flow_model": meta.get("turbulence"),
        "contact_resistance_m2KW": meta.get("contact_resistance_m2KW", 0.0),
        "assumptions": ["Steady solver: the flow behind the fins may be slightly unsteady, so the pressure drop carries the "
                        "iteration-to-iteration band shown with the convergence", "No radiation",
                        "Chip underside adiabatic unless a boundary condition was set (no board heat path)",
                        "Perfect contact between parts" if not meta.get("contact_resistance_m2KW")
                        else f"Contact resistance {meta['contact_resistance_m2KW'] * 1e4:g} K·cm²/W between parts"],
    })
    if meta.get("transient"):
        summary["assumptions"][0] = ("Transient (chtMultiRegionFoam): values are at the end time; while parts are "
                                     "still heating up, the heat carried by the air is less than the heat input")
        summary["energy_imbalance_percent"] = None  # (heat is still being stored in the parts)
        from .runner import RE_FMM, RE_TIME
        import bisect

        log = case / f"log.{meta['app']}"
        txt = log.read_text(errors="ignore") if log.exists() else ""
        tpos = [(m.start(), float(m.group(1))) for m in RE_TIME.finditer(txt)]
        starts = [p for p, _ in tpos]
        ser = {}
        for m in RE_FMM.finditer(txt):
            k = bisect.bisect_right(starts, m.start()) - 1
            if k >= 0:
                ser.setdefault(m.group(1), []).append((tpos[k][1], float(m.group(3)) - 273.15))
        # fieldMinMax names T_<region>
        lab = meta.get("region_labels", {})
        ser = {f"{lab.get(n[2:], n[2:])} max": v for n, v in ser.items() if v}
        if ser:
            n = min(len(v) for v in ser.values())
            t0 = [t for t, _ in next(iter(ser.values()))[:n]]
            summary["history"] = _thin(t0, {k: [x for _, x in v[:n]] for k, v in ser.items()})
    hottest = max(summary["solids"].items(), key=lambda kv: kv[1]["T_max"]) if summary["solids"] else None
    if hottest:
        summary["hottest_part"] = {"region": hottest[0], "label": hottest[1]["label"], "T_max": hottest[1]["T_max"]}
        summary["thermal_resistance_K_per_W"] = (hottest[1]["T_max"] - Tin) / Q if Q else None
    cc = _correlation_check(project, meta, summary, raw.get("air"), case)
    if cc:
        summary["correlation_check"] = cc
    summary["default_hidden_regions"] = ["air"]
    return rs, summary


def teertstra_nu(re_b: float, b: float, L: float, pr: float) -> float:
    """Average Nusselt number (based on the gap b) for developing laminar flow between parallel isothermal plates
    (Teertstra, Yovanovich & Culham 1999, plate-fin heat sinks)."""
    rs = re_b * b / L
    return ((rs * pr / 2) ** -3 + (0.664 * math.sqrt(rs) * pr ** (1 / 3) * math.sqrt(1 + 3.65 / math.sqrt(rs))) ** -3) ** (-1 / 3)


def _channel_speed(air, fr):
    """Volume-mean through-flow speed of the air in the middle half of the fin channels (None if unknown)."""
    boxes, ax = fr.get("channel_boxes") or [], fr.get("flow_axis")
    if not air or not boxes or ax is None:
        return None
    pm, tets, cf, _ = air
    P = pm["points"][tets]
    cc = P.mean(1)
    vol = np.abs(np.einsum("ij,ij->i", P[:, 1] - P[:, 0], np.cross(P[:, 2] - P[:, 0], P[:, 3] - P[:, 0]))) / 6
    sel = np.zeros(len(cc), bool)
    for lo, hi in boxes:
        lo2, hi2 = np.array(lo, float), np.array(hi, float)
        q = 0.25 * (hi2[ax] - lo2[ax])
        lo2[ax] += q
        hi2[ax] -= q
        sel |= np.all((cc >= lo2) & (cc <= hi2), axis=1)
    if sel.sum() < 20:
        return None
    return float((np.abs(cf["U"][sel, ax]) * vol[sel]).sum() / vol[sel].sum())


def _fin_geometry(project, meta, case):
    """flow_regime with the fin-channel inputs; recomputed from the project's mesh/geometry for cases written
    before those inputs were stored."""
    fr = meta.get("flow_regime") or {}
    if fr.get("channel_boxes") or case is None:
        return fr
    try:
        from . import cases
        gdir = Path(case).parent / "geometry"
        faces = json.loads((gdir / "faces.json").read_text())
        bodies = json.loads((gdir / "bodies.json").read_text())
        new = cases.flow_regime(project, faces, [b["id"] for b in bodies if b["role"] == "fluid"])
        return dict(fr, **{k: new.get(k) for k in ("channel_gap", "channel_height", "channel_length", "fin_thickness",
                                                    "fin_thickness_from_pitch", "channel_boxes", "flow_axis")})
    except Exception:  # noqa: BLE001 - the cross-check is optional
        return fr


def _patch_integral(pm, pv, field, names):
    """Sum of a face field times the face areas over patches (W for a heat flux), or None if not written."""
    tot, found = 0.0, False
    for p in pm["patches"]:
        v = pv.get(field, {}).get(p["name"])
        if p["name"] in names and p["nFaces"] and v is not None and np.size(v) == p["nFaces"]:
            A = np.linalg.norm(_face_areas(pm, p["start"], p["nFaces"]), axis=1)
            tot += float((np.asarray(v, float) * A).sum())
            found = True
    return tot if found else None


def _natural_summary(rs, summary, raw, meta, project, case, phi, pm, cf, pv, fl, Q):
    """No fan: heat leaves with the warm air through the openings, as radiation through the openings and through
    cooled walls. Reference temperature = the room."""
    Tr = float(meta["T_room_C"])
    opens = meta["outlet_patches"]
    tets = raw["air"][1]
    pts = np.asarray(pm["points"])
    k_air = fl["mu"] * fl["cp"] / fl["Pr"]
    Q_air = Q_cond = m_out = 0.0
    for p in pm["patches"]:
        if p["name"] in opens and phi is not None and p["name"] in phi["patches"]:
            sl = slice(p["start"], p["start"] + p["nFaces"])
            own = pm["owner"][sl]
            Tc = cf["T"][own] - Tr
            f = phi["patches"][p["name"]]
            # with the final mass flux: air leaving carries its cell's temperature (the written patch values were
            # set from the flux before the last pressure correction, so faces that just turned to outflow would
            # still read the room temperature); air coming in is at the room temperature
            Q_air += float((np.where(f > 0, f, 0.0) * Tc).sum()) * fl["cp"]
            m_out += float(f[f > 0].sum())
            # conduction where room air comes in next to warm air (fixed room temperature there), laminar k
            Sf = _face_areas(pm, p["start"], p["nFaces"])
            A = np.linalg.norm(Sf, axis=1)
            fc = np.array([pts[q].mean(0) for q in pm["faces"][sl]])
            d = np.abs(np.einsum("ij,ij->i", fc - pts[np.asarray(tets)[own]].mean(1), Sf / A[:, None]))
            Q_cond += float(np.where(f < 0, k_air * Tc / np.maximum(d, 1e-9) * A, 0.0).sum())
    # radiation leaving through the openings (qr: net radiative flux on the boundary, + = into the boundary)
    Q_rad = _patch_integral(pm, pv, "qr", opens) if meta.get("radiation") else 0.0
    # cooled walls: heat into the wall from the air (wallHeatFlux: + = from the wall into the air)
    q_w = _patch_integral(pm, pv, "wallHeatFlux", meta.get("cooled_wall_patches") or [])
    Q_wall = -q_w if q_w is not None else 0.0
    Q_air += Q_cond  # (warm air leaving + heat conducted across the openings)
    Q_out = Q_air + (Q_rad or 0.0) + Q_wall
    umag = np.linalg.norm(cf["U"], axis=1) if "U" in cf else None
    summary.update({
        "natural": True, "radiation": bool(meta.get("radiation")), "emissivity": meta.get("emissivity"),
        "inlet_T": Tr, "room_T": Tr, "mass_flow_kg_s": m_out, "flow_m3_per_h": m_out / fl["rho"] * 3600,
        "heat_input_W": Q, "heat_carried_by_air_W": Q_air, "heat_conducted_at_openings_W": Q_cond,
        "heat_radiated_out_W": Q_rad,
        "heat_through_walls_W": Q_wall, "heat_out_W": Q_out,
        "energy_imbalance_percent": abs(Q_out - Q) / Q * 100 if Q else None,
        "energy_balance_note": "Heat out = warm air leaving through the openings (relative to the room) + radiation "
                               "out through the openings + heat through cooled walls.",
        "velocity_max": float(np.percentile(umag, 99.9)) if umag is not None else None,
        "flow_regime": meta.get("flow_regime"), "flow_model": meta.get("turbulence"),
        "contact_resistance_m2KW": meta.get("contact_resistance_m2KW", 0.0),
        "assumptions": ["Steady solver: a rising plume is often slightly unsteady - check the convergence band",
                        f"Radiation between surfaces (fvDOM, emissivity {meta['emissivity']:g}; openings radiate like "
                        "the room)" if meta.get("radiation") else "No radiation (often 30-50 % of the heat with no fan: "
                        "switch it on for a realistic answer)",
                        "Air: ideal gas with buoyancy (no Boussinesq approximation)",
                        "Perfect contact between parts" if not meta.get("contact_resistance_m2KW")
                        else f"Contact resistance {meta['contact_resistance_m2KW'] * 1e4:g} K·cm²/W between parts"],
    })
    if meta.get("transient"):
        summary["energy_imbalance_percent"] = None
        summary["assumptions"][0] = "Transient: values are at the end time (parts may still be heating up)"
    hottest = max(summary["solids"].items(), key=lambda kv: kv[1]["T_max"]) if summary["solids"] else None
    if hottest:
        summary["hottest_part"] = {"region": hottest[0], "label": hottest[1]["label"], "T_max": hottest[1]["T_max"]}
        summary["thermal_resistance_K_per_W"] = (hottest[1]["T_max"] - Tr) / Q if Q else None
    summary["default_hidden_regions"] = ["air"]
    return rs, summary


def _surface_heat(air, fluid, reg):
    """Heat crossing the air side of a part's surface from the solver's wallHeatFlux field (W, or None)."""
    if not air:
        return None
    pm, _, cf, pv = air
    q = pv.get("wallHeatFlux", {}).get(f"{fluid}_to_{reg}")
    for p in pm["patches"]:
        if p["name"] == f"{fluid}_to_{reg}" and q is not None and len(q) == p["nFaces"]:
            A = np.linalg.norm(_face_areas(pm, p["start"], p["nFaces"]), axis=1)
            return abs(float((np.asarray(q, float) * A).sum()))
    return None


def _correlation_check(project, meta, summary, air=None, case=None):
    """Independent estimate of the fin part's convective resistance from a plate-fin correlation, compared like for
    like with the CFD: (mean temperature of the part's air-side surface - inlet temperature) / heat against 1/(h A)
    (Teertstra's Nu is referenced to the inlet temperature). Only for laminar channels and one unheated finned part."""
    from ... import materials
    fr = _fin_geometry(project, meta, case)
    b, H, L, t = (fr.get(k) for k in ("channel_gap", "channel_height", "channel_length", "fin_thickness"))
    Q, Tin = summary.get("heat_input_W"), summary.get("inlet_T")
    if not (b and H and L and t and Q and Tin is not None) or fr.get("suggested") != "laminar":
        return None
    unheated = [(bid, reg) for bid, reg in (meta.get("region_of_body") or {}).items()
                if reg in summary["solids"] and not summary["solids"][reg].get("heat_W")]
    if len(unheated) != 1:
        return None
    bid, reg = unheated[0]
    part = summary["solids"][reg]
    itf = part.get("interfaces") or {}
    fluid = meta.get("fluid_region", "air")
    wet = itf.get(f"{reg}_to_{fluid}")
    if not wet:
        return None
    A = wet["area_m2"]
    fl = materials.get_fluid(project["settings"].get("fluid", "air"))
    k_s = materials.get_solid((project.get("bodies", {}).get(str(bid)) or {}).get("material") or "aluminium_6061")["k"]
    U_cfd = _channel_speed(air, fr)
    U = U_cfd or fr.get("channel_velocity") or fr.get("velocity")
    re_b = U * b * fl["rho"] / fl["mu"]
    h = teertstra_nu(re_b, b, L, fl["Pr"]) * fl["k"] / b
    t_known = fr.get("fin_thickness_from_pitch", False)  # (a mesh size is no fin thickness)
    m = math.sqrt(2 * h / (k_s * t))
    eta = math.tanh(m * H) / (m * H)
    # overall surface efficiency: only the fin faces lose effectiveness, not the base between the fins
    n_ch = len(fr.get("channel_boxes") or [])
    A_fin = min(A, (n_ch + 1) * (2 * H + t) * L) if n_ch else A
    eta_o = 1 - A_fin / A * (1 - eta)
    r_corr = 1 / (h * A)
    Q_s = _surface_heat(air, fluid, reg)  # heat that actually leaves through this surface
    Q_ref = Q_s if Q_s and 0.3 * Q < Q_s < 1.2 * Q else Q
    r_cfd = (wet["T_mean"] - Tin) / Q_ref
    diff = (r_cfd - r_corr) / r_corr * 100
    # base-referenced pair: interface to the heated part(s) against 1/(eta h A)
    base = [v for k, v in itf.items() if not k.endswith("_to_" + fluid)]
    r_base_cfd = ((sum(v["T_mean"] * v["area_m2"] for v in base) / sum(v["area_m2"] for v in base) - Tin) / Q_ref
                  if base and t_known and n_ch else None)  # needs the fin thickness and count
    # air passing outside the channels (over the fin tips, beside the part) widens the expected agreement
    flow = (summary.get("mass_flow_kg_s") or 0.0) / fl["rho"]
    bypass = max(0.0, 1 - n_ch * b * H * U_cfd / flow) if (U_cfd and n_ch and flow) else None
    band = min(30.0, 15.0 + 20.0 * (bypass or 0.0))  # the channel speed is from the CFD, so bypass matters less
    note = (f"Plate-fin correlation (Teertstra 1999, developing laminar flow at {U:.2f} m/s "
            f"{'(CFD mean in the channels)' if U_cfd else '(estimated channel speed)'}, h = {h:.0f} W/m²K over the "
            f"{A * 1e4:.0f} cm² air-side surface of {part['label']}): 1/(hA) = {r_corr:.2f} K/W; CFD (surface mean "
            f"temperature - inlet) / heat = {r_cfd:.2f} K/W ({diff:+.0f} %)."
            + (f" From the base: CFD {r_base_cfd:.2f} vs 1/(η₀hA) = {r_corr / eta_o:.2f} K/W (fin efficiency "
               f"{eta:.2f}, overall surface efficiency {eta_o:.2f})." if r_base_cfd else "")
            + (f" Heat through this surface {Q_ref:.2f} W (solver wall heat flux)." if Q_ref is not Q else "")
            + (f" {bypass * 100:.0f} % of the air bypasses the channels." if bypass is not None else "")
            + ("" if n_ch else " Fin count and thickness unknown for this mesh (regenerate it for the base-temperature "
                               "comparison).")
            + f" The correlation idealises the heat sink, so agreement within about ±{band:.0f} % is expected.")
    if abs(diff) > band:
        note += (" The difference is larger: check the mesh (mesh-sensitivity check), the flow regime and whether "
                 "the part has large non-fin surfaces.")
    return {"h_W_m2K": h, "fin_efficiency": eta, "wetted_area_m2": A, "channel_speed_m_s": U,
            "channel_speed_from_cfd": bool(U_cfd), "bypass_fraction": bypass, "expected_band_percent": band,
            "R_conv_correlation_K_per_W": r_corr, "R_conv_cfd_K_per_W": r_cfd, "difference_percent": diff,
            "R_base_cfd_K_per_W": r_base_cfd, "R_base_correlation_K_per_W": r_corr / eta_o if r_base_cfd else None,
            "surface_efficiency": eta_o, "surface_heat_W": Q_ref,
            "part": part["label"], "note": note}


def whole_body(planes: list, F, meta: dict, V: float, up) -> tuple:
    """The solved part's forces and moments and the rotors / reference area as those of the whole body, from the part
    and its mirror images. F: _parse_forces result (or None). Returns (F whole, meta whole, summary note)."""
    ref_dir = meta["U_inf"] if V > 0 else up
    meta = dict(meta, rotors=sym.mirror_rotors(planes, meta["rotors"]),
                ref_area=meta["ref_area"] * sym.area_factor(planes, ref_dir))
    if F is not None:
        cofr = np.asarray(meta["CofR"], float)
        half = F
        F = dict(half, total=sym.expand_force(planes, half["total"]), pressure=sym.expand_force(planes, half["pressure"]),
                 viscous=sym.expand_force(planes, half["viscous"]))
        if half.get("moment") is not None:
            F["moment"] = sym.expand_force_moment(planes, half["total"], half["moment"], cofr)[1]
        if half.get("history") is not None:
            T_h, Ft_h, Fp_h, Fv_h, M_h = half["history"]
            F["history"] = (T_h, sym.expand_force(planes, Ft_h), sym.expand_force(planes, Fp_h),
                            sym.expand_force(planes, Fv_h),
                            None if M_h is None else sym.expand_force_moment(planes, Ft_h, M_h, cofr)[1])
    note = {"planes": len(planes), "copies": sym.mass_factor(planes),
            "note": "Forces, moments, rotor thrust and the reference area are those of the whole body: the solved "
                    f"part and its {sym.mass_factor(planes) - 1} mirror image(s)."}
    return F, meta, note


def _load_aero(case, project, meta):
    t = latest_time(case)
    if t is None:
        raise RuntimeError("No results found - the solver has not written any time step yet.")
    pm, tets, data = _region_data(case, None, t, ["p", "U", "yPlus", "Q"])
    rho = meta["rho"]
    cf = {"p": data["p"]["internal"] * rho, "U": data["U"]["internal"]}
    pv = {"p": {k: v * rho for k, v in data["p"]["patches"].items()}, "U": data["U"]["patches"]}
    if "yPlus" in data:  # (wall values only)
        cf["yPlus"] = np.zeros(pm["n_cells"])
        pv["yPlus"] = data["yPlus"]["patches"]
    if "Q" in data:  # Q-criterion (vortex cores: Q > 0), written by the Q function object
        cf["Q"] = data["Q"]["internal"]
    parts = [("air", pm, tets, cf, pv, "")]
    (nodes, tt, treg, regions, fields, surf, scell, sgrp, groups, sv) = _assemble(parts)
    rs_fields = {"Pressure": ("cell", fields["p"], "Pa (gauge)"), "Velocity": ("cell", fields["U"], "m/s")}
    svals = {"Pressure": sv["p"], "Velocity": np.linalg.norm(sv["U"], axis=1)}
    if "yPlus" in data:
        rs_fields["y+"] = ("cell", np.full(len(tt), np.nan), "-")  # wall values only
        svals["y+"] = sv["yPlus"]
    if "Q" in data:
        rs_fields["Q-criterion"] = ("cell", fields["Q"], "1/s²")
        svals["Q-criterion"] = sv["Q"]
    rs = ResultSet(nodes, tt, treg, regions, rs_fields, surf, scell, sgrp, groups, svals)

    s = {**studies.default_settings("external_aero"), **project["settings"]}
    V = meta["speed"]
    summary = {"time": float(t), "speed_m_s": V, "U_inf": meta["U_inf"]}
    tr = meta.get("transient")
    F = _parse_forces(case, meta["app"], avg_from=tr["end_time"] / 2 if tr else None)
    # symmetry planes: the model that was solved is the kept part; forces, moments, rotors and the reference area are
    # those of the whole body (the part summed with its mirror images)
    planes = project.get("symmetry") or []
    F_half = F
    if planes:
        F, meta, summary["symmetry"] = whole_body(planes, F, meta, V, AXES[s.get("up_axis", "+z")])
    q = 0.5 * rho * V ** 2
    up = np.array(AXES[s.get("up_axis", "+z")], float)
    fwd = np.array(AXES[s.get("forward_axis", "+x")], float)
    th = math.radians(float(s.get("pitch_deg", 0.0)))
    world_up = math.cos(th) * up - math.sin(th) * fwd
    flight_dir = math.cos(th) * fwd + math.sin(th) * up
    thrust = sum((np.array(r["axis"]) * r["thrust_N"] for r in meta["rotors"]), np.zeros(3))
    weight = -float(s.get("mass_kg", 0.0)) * 9.81 * world_up
    # rotor in-plane drag (H-force) on the vehicle, modelled in the case writer (absent in older cases)
    Hvec = sum((np.array(r.get("H_N", [0, 0, 0]), float) for r in meta["rotors"]), np.zeros(3))
    k_drag = meta["rotors"][0].get("drag_coeff_s_per_m") if meta["rotors"] else None
    if F is not None:
        Ft = F["total"]
        D = float(Ft @ (np.array(meta["U_inf"]) / V)) if V > 0 else None
        D_rot = float(Hvec @ (np.array(meta["U_inf"]) / V)) if V > 0 else 0.0
        net = thrust + Ft + Hvec + weight
        summary.update({
            "aero_force_N": Ft.tolist(), "pressure_force_N": F["pressure"].tolist(),
            "viscous_force_N": F["viscous"].tolist(),
            "drag_N": D, "vertical_aero_force_N": float(Ft @ world_up),
            "rotor_drag_N": D_rot if k_drag is not None else None, "rotor_drag_coeff_s_per_m": k_drag,
            "total_drag_N": (D + D_rot) if D is not None else None,
            "CdA_m2": D / q if (V > 0 and D is not None) else None,
            "Cd_ref_projected_area": D / (q * meta["ref_area"]) if (V > 0 and D is not None and meta["ref_area"]) else None,
            "ref_area_m2": meta["ref_area"],
            "rotor_thrust_total_N": float(np.linalg.norm(thrust)),
            "net_force_N": net.tolist(),
            "net_vertical_N": float(net @ world_up), "net_forward_N": float(net @ flight_dir),
            "trim_note": _trim_note(net @ world_up, net @ flight_dir, s, meta["rotors"]),
        })
        # rotating zones: torque about each zone's axis (all walls; moved from the moment point to the zone centre)
        if meta.get("moving_zones") and F_half.get("moment") is not None:
            cofr = np.asarray(meta["CofR"], float)
            zs = []
            for z in meta["moving_zones"]:
                c, a = np.asarray(z["center"], float), np.asarray(z["axis"], float)
                Tq = float((F_half["moment"] + np.cross(cofr - c, F_half["total"])) @ a)
                zs.append({"name": z["name"], "method": z["method"], "rpm": z["rpm"], "torque_Nm": Tq,
                           "power_W": -Tq * z["omega"], "cells": z.get("cells")})
                if z.get("walls_patch"):  # the propeller alone (its own wall patch and force monitor)
                    pr = propeller_summary(case, z, meta["rho"], V,
                                           np.array(meta["U_inf"]) / V if V > 0 else np.zeros(3), meta.get("transient"))
                    if pr:
                        zs[-1]["propeller"] = pr
            summary["moving_zones"] = zs
        # force and moment coefficients in flow axes (drag along the flow, lift along the tilted up axis)
        A_ref = float(s.get("ref_area_m2") or 0.0) or meta["ref_area"]
        L_ref = float(s.get("ref_length_m") or 0.0) or meta["ref_length"]
        if V > 0 and A_ref:
            e_d = np.array(meta["U_inf"]) / V
            e_l = world_up - (world_up @ e_d) * e_d
            e_l /= np.linalg.norm(e_l) or 1.0
            e_s = np.cross(e_l, e_d)  # side force positive to the left when looking downstream
            co = {"CD": float(Ft @ e_d) / (q * A_ref), "CL": float(Ft @ e_l) / (q * A_ref),
                  "CS": float(Ft @ e_s) / (q * A_ref), "A_ref_m2": A_ref, "L_ref_m": L_ref,
                  "lift_N": float(Ft @ e_l), "side_force_N": float(Ft @ e_s),
                  "A_ref_source": "user" if s.get("ref_area_m2") else "projected frontal area"}
            M0 = F.get("moment")
            if M0 is not None and L_ref:
                side = np.cross(up, fwd)
                Mb = {"pitch": float(-(M0 @ side)), "roll": float(M0 @ fwd), "yaw": float(M0 @ up)}
                summary["aero_moment_Nm"] = Mb  # about the moment point (CG), body axes; pitch nose-up +
                co.update({"Cm": Mb["pitch"] / (q * A_ref * L_ref), "Cl": Mb["roll"] / (q * A_ref * L_ref),
                           "Cn": Mb["yaw"] / (q * A_ref * L_ref)})
            summary["coefficients"] = co
        W = float(s.get("mass_kg", 0.0)) * 9.81
        if V > 0 and W > 0 and meta["rotors"]:
            T_now = max(float(np.linalg.norm(thrust)), 1e-9)
            pitch, T_trim, _ = _trim(Ft, Hvec, T_now, W, world_up, flight_dir)
            summary["suggested_pitch_deg"] = pitch
            summary["trim_thrust_total_N"] = T_trim
            if k_drag:
                # the rotor-drag coefficient is empirical and sets the trim: show what +-40 % in k does
                summary["trim_sensitivity"] = []
                for f in (0.6, 1.0, 1.4):
                    pk, Tk, Dk = _trim(Ft, Hvec * f, T_now, W, world_up, flight_dir)
                    summary["trim_sensitivity"].append({"k_s_per_m": k_drag * f, "pitch_deg": pk, "thrust_N": Tk,
                                                        "total_drag_N": Dk})
            summary["trim_note_detail"] = (
                "Estimate from this run (frame forces held fixed, rotor drag scaled with thrust): re-run at the "
                f"suggested pitch with {T_trim:.2f} N total thrust to confirm."
                + (" Rotor drag is OFF (k = 0): the pitch is a lower bound." if not k_drag else ""))
        M = F.get("moment")
        if M is not None and meta["rotors"]:
            cofr = np.array(meta["CofR"])
            side = np.cross(up, fwd)

            def aero_moment(c):
                """Frame moment (about meta CofR, moved to c) + moment of the rotor drag forces at the hubs."""
                Mf = M - np.cross(c - cofr, Ft)
                Mh = sum((np.cross(np.array(r.get("center", c)) - c, np.array(r.get("H_N", [0, 0, 0]), float))
                          for r in meta["rotors"]), np.zeros(3))
                return Mf, Mh

            Mf, Mh = aero_moment(cofr)
            summary.update(moment_balance(Mf + Mh, meta["rotors"], cofr, up, fwd))
            summary["pitch_moment_parts_Nm"] = {"frame": float(-(Mf @ side)), "rotor_drag": float(-(Mh @ side))}
            # the rotor drag acts at the hubs, so its moment (and the thrust split) depends on the CG height
            summary["cg_sensitivity"] = []
            cg0 = float(s.get("cg_up_mm") or 0.0)
            for dz in (-20.0, 0.0, 20.0):
                c = cofr + dz * 1e-3 * up
                mf, mh = aero_moment(c)
                mb = moment_balance(mf + mh, meta["rotors"], c, up, fwd)
                summary["cg_sensitivity"].append({"cg_up_mm": cg0 + dz, "aero_pitch_moment_Nm": mb["aero_pitch_moment_Nm"],
                                                  "rotor_thrust_trim_N": mb["rotor_thrust_trim_N"]})
            summary["cg_set"] = bool(s.get("cg_up_mm") or s.get("cg_forward_mm"))
    else:
        summary["warning"] = "Force output not found in the solver log."
    if F is not None and F.get("history") is not None:
        T_h, Ft_h = F["history"][0], F["history"][1]
        e_d = np.array(meta["U_inf"]) / V if V > 0 else -world_up
        e_l = world_up - (world_up @ e_d) * e_d
        e_l /= np.linalg.norm(e_l) or 1.0
        summary["history"] = _thin(T_h, {"Drag [N]": Ft_h @ e_d, "Lift [N]": Ft_h @ e_l})
        summary["history"]["y_from_t"] = tr["end_time"] / 10  # (plot range without the start-up spike)
        summary["history_note"] = f"Forces above are averaged over t ≥ {tr['end_time'] / 2:.4g} s (the second half of the run)."
        if len(T_h) > 10:
            late = Ft_h[T_h >= tr["end_time"] / 2] @ e_d
            if len(late) > 2 and abs(late.mean()) > 0:
                summary["drag_fluctuation_percent"] = float(100 * late.std() / abs(late.mean()))
    summary["velocity_p995"] = float(np.percentile(np.linalg.norm(cf["U"], axis=1), 99.5))
    rotor_notes = [
        "Rotors are actuator disks with uniform thrust loading (no swirl, no blade geometry); blade-flapping hub "
        "moments are not modelled (they are usually nose-up and add to the pitch moment)",
        (f"Rotor in-plane drag (H-force) modelled as H = k·T·V_inplane with k = {k_drag:g} s/m, an empirical "
         "small-quadrotor value: set your own k for other propellers; it dominates the trim pitch at cruise "
         "(see the k sensitivity under the trim). H is taken proportional to thrust (blade-element theory "
         "suggests closer to sqrt(T), which matters when the thrust changes a lot from k's reference condition). "
         "Its reaction is spread uniformly over each disk and slows the air reaching the frame below the rotors, "
         "so the frame drag is lower than in a run with k = 0 (example quadcopter: 0.48 vs 0.76 N)"
         if k_drag else "Rotor in-plane drag is NOT included (k = 0 or an older case): drag and trim pitch are "
         "lower bounds"),
        "Rotor radius was estimated from the motor spacing unless you set it - enter your propeller radius",
    ]
    summary["assumptions"] = (rotor_notes if meta["rotors"] else []) + [
        (f"Transient {'laminar' if s.get('turbulence') == 'laminar' else 'DES' if s.get('turbulence') == 'kOmegaSSTDDES' else 'URANS'} (pimpleFoam, "
         f"{s.get('turbulence', 'kOmegaSST')}) with wall functions on a tetrahedral mesh: resolves large "
         "unsteady structures such as vortex shedding, not turbulence itself" if tr else
         f"Steady {'laminar flow' if s.get('turbulence') == 'laminar' else 'RANS'} ({s.get('turbulence', 'kOmegaSST')}) "
         "with wall functions on a tetrahedral mesh"),
        ("Moments are taken about the centre of the body bounding box: set the moment point (CG) in the settings "
         "if you need another one" if not (s.get("cg_up_mm") or s.get("cg_forward_mm")) else
         f"Moments are taken about the moment point: {float(s.get('cg_up_mm') or 0):g} mm above and "
         f"{float(s.get('cg_forward_mm') or 0):g} mm forward of the body bounding-box centre"),
    ]
    if "yPlus" in data:
        vals = [v for v in data["yPlus"]["patches"].values() if len(v)]
        if vals:
            allv = np.concatenate(vals)
            summary["yPlus"] = {"mean": float(allv.mean()), "max": float(allv.max())}
    summary["default_hidden_regions"] = []
    mirror_patches = {p_["name"] for p_ in meta.get("patches", []) if p_.get("bc_type") == "symmetry"}
    summary["default_groups"] = [g for g in groups if g != "farfield" and g not in mirror_patches]
    if planes:
        summary["assumptions"] = summary.get("assumptions", []) + [
            f"Symmetry: {len(planes)} mirror plane(s). Only the kept part of the model was solved; the flow beyond the "
            "plane is its mirror image (forces and moments above are the whole body's). Real wakes can be "
            "asymmetric (vortex shedding): a mirror forces a symmetric flow."]
    return rs, summary


def moment_balance(M_aero, rotors, cofr, up, fwd) -> dict:
    """Net moment about the CG (aerodynamic moment incl. rotor drag + the moment of the rotor thrusts set in this run)
    and the per-rotor thrusts that cancel the net pitch/roll moment at unchanged total thrust."""
    cofr = np.asarray(cofr, float)
    side = np.cross(up, fwd)  # points left; nose-up pitch is a negative moment about it
    M_thr = sum((np.cross(np.array(r.get("center", cofr)) - cofr, np.array(r["axis"]) * r["thrust_N"])
                 for r in rotors), np.zeros(3))
    M = M_aero + M_thr
    out = {"aero_pitch_moment_Nm": float(-(M_aero @ side)), "aero_roll_moment_Nm": float(M_aero @ fwd),
           "pitch_moment_Nm": float(-(M @ side)), "roll_moment_Nm": float(M @ fwd), "yaw_moment_Nm": float(M @ up)}
    A = []
    for r in rotors:
        mvec = np.cross(np.array(r.get("center", cofr)) - cofr, np.array(r["axis"]))  # moment per newton of thrust
        A.append([1.0, float(mvec @ side), float(mvec @ fwd)])
    rhs = np.array([0.0, out["pitch_moment_Nm"], -out["roll_moment_Nm"]])
    dT, *_ = np.linalg.lstsq(np.array(A).T, rhs, rcond=None)
    out["rotor_thrust_trim_N"] = [float(r["thrust_N"] + d) for r, d in zip(rotors, dT)]
    return out


def _trim(Ft, Hvec, T_now, W, world_up, flight_dir):
    """Level-flight trim estimate: the thrust vector balances weight, frame forces and rotor drag. Rotor drag
    scales with thrust (H = k T V), so the total thrust is iterated with the frame forces held fixed.
    Returns (nose-down pitch in deg, total thrust N, total drag N)."""
    T_trim = T_now
    for _ in range(30):
        Fa = Ft + Hvec * (T_trim / T_now)
        Dh = max(float(-Fa @ flight_dir), 0.0)
        Lv = W - float(Fa @ world_up)
        T_trim = math.hypot(Dh, max(Lv, 1e-9))
    return math.degrees(math.atan2(Dh, max(Lv, 1e-9))), T_trim, Dh


def _trim_note(fz, fx, s, rotors):
    if not rotors:
        return ""
    msgs = []
    if fz < -0.05 * float(s.get("mass_kg", 1)) * 9.81:
        msgs.append(f"Net vertical force {fz:.2f} N: the drone would descend - more thrust needed.")
    elif fz > 0.05 * float(s.get("mass_kg", 1)) * 9.81:
        msgs.append(f"Net vertical force +{fz:.2f} N: the drone would climb.")
    if fx < -0.05:
        msgs.append(f"Net forward force {fx:.2f} N: drag exceeds the forward thrust component - increase pitch angle.")
    elif fx > 0.05:
        msgs.append(f"Net forward force +{fx:.2f} N: the drone would accelerate - pitch angle can be reduced.")
    return " ".join(msgs) or "Forces are balanced: the drone is close to trimmed level flight at this pitch."
