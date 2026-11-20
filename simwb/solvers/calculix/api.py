"""CalculiX solves with the same call and result shape as simwb.solvers.static_fem.solve / modal_fem.solve."""
from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

import numpy as np

from . import inputdeck, installer, results
from ..modal_fem import band_check
from ..static_fem import body_safety

JOB = "job"


def run_ccx(workdir: Path, deck_text: str, log=print, on_proc=None, threads: int | None = None) -> str:
    """Write <workdir>/job.inp and run ccx on it; returns the solver output. Raises with CalculiX's own message on
    failure (singular system, negative Jacobian, ...)."""
    info = installer.detect()
    if not info["available"]:
        raise RuntimeError("CalculiX is not installed: install it from Settings, or choose the built-in FEM solver "
                           "for this study.")
    exe = Path(info["exe"])
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    for f in workdir.glob(JOB + ".*"):
        f.unlink()
    (workdir / f"{JOB}.inp").write_text(deck_text, newline="\n")
    env = installer._env(exe)
    n = threads or max(1, (os.cpu_count() or 2) // 2)  # physical-core count: the solver is memory-bandwidth bound
    env["OMP_NUM_THREADS"] = str(n)
    env["CCX_NPROC_EQUATION_SOLVER"] = str(n)
    log(f"$ ccx {JOB}   (CalculiX {info.get('version') or '?'}, {n} threads)")
    out = []
    proc = subprocess.Popen([str(exe), JOB], cwd=workdir, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if on_proc:
        on_proc(proc)
    for line in proc.stdout:
        line = line.rstrip()
        out.append(line)
        if line.strip():
            log(line)
    rc = proc.wait()
    text = "\n".join(out)
    errs = [x.strip() for x in out if "*ERROR" in x]
    if rc != 0 or errs or not (workdir / f"{JOB}.frd").exists():
        why = "; ".join(errs[:3]) or f"exit code {rc}"
        if re.search(r"singular|zero pivot|numerical singularity", text, re.I):
            why = ("the supports do not hold the part in place (it could move or rotate freely) - add a fixed "
                   "support or more supports. (" + why + ")")
        raise RuntimeError(f"CalculiX stopped: {why}")
    return text


def solve_static(mesh: dict, body_props: dict, bcs: list, gravity=None, log=print, workdir=None, on_proc=None,
                 thermal=None) -> dict:
    """thermal: None, or {"T_C": corner-node temperatures [degC], "T_ref_C": stress-free temperature, "alpha":
    {body id: 1/K}} for a one-way thermal stress analysis (see inputdeck)."""
    t0 = time.time()
    deck = inputdeck.build(mesh, body_props, bcs, "static", gravity=gravity, log=log, thermal=thermal)
    log(f"CalculiX deck: {deck.n_elements:,} quadratic tetrahedra, {deck.n_nodes:,} nodes")
    run_ccx(workdir, deck.text(), log, on_proc)
    wd = Path(workdir)
    blocks = results.read_frd(wd / f"{JOB}.frd")
    n1 = deck.n_corner
    d = next((b for b in blocks if b["name"] == "DISP"), None)
    s = next((b for b in blocks if b["name"] == "STRESS"), None)
    if d is None or s is None:
        raise RuntimeError("CalculiX wrote no displacements or stresses.")
    disp = np.zeros((n1, 3))
    m = min(len(d["data"]) - 1, n1)
    disp[:m] = d["data"][1:m + 1, :3]
    sg = np.zeros((n1, 6))
    m = min(len(s["data"]) - 1, n1)
    sg[:m] = s["data"][1:m + 1, :6]
    vm_n = results.von_mises(sg)
    dmag = np.linalg.norm(disp, axis=1)
    p = np.asarray(mesh["nodes"], float)
    t = mesh["tets"].astype(np.int64)
    bodies, warns = body_safety(body_props, mesh["tet_vol"], t, vm_n, dmag)
    i_d, i_s = int(np.argmax(dmag)), int(np.argmax(vm_n))
    reaction = results.read_total_force(wd / f"{JOB}.dat") if deck.support_nodes else None
    if reaction is not None:  # RF is the internal force at the supports: add the load applied on them directly
        reaction = (np.asarray(reaction) - deck.direct_load).tolist()
    g = np.asarray(gravity, float) if gravity is not None else np.zeros(3)
    total_applied = deck.applied + deck.mass * g
    balance = None
    if reaction is not None:
        resid = float(np.linalg.norm(np.asarray(reaction) + total_applied))
        if thermal is not None and np.linalg.norm(total_applied) < 1e-9:
            balance = None  # (a thermal load has no net force: the residual is reported as a force instead)
            if resid > 1e-6 * float(np.abs(np.asarray(reaction)).max() + 1.0) and resid > 1e-3:
                warns.append(f"Net support force {resid:.3g} N with no mechanical load (should be zero).")
        else:
            balance = resid / max(np.linalg.norm(total_applied), 1e-30)
            if balance > 1e-3:
                warns.append(f"Force balance error {balance * 100:.2g} % (reactions vs applied loads).")
    size = float(np.ptp(p, axis=0).max())
    if dmag.max() > 0.02 * size:
        warns.append(f"The largest deflection ({dmag.max() * 1e3:.3g} mm) is more than 2 % of the part size: "
                     "small-deflection (linear) theory becomes inaccurate.")
    if deck.flipped:
        log(f"{deck.flipped:,} elements were re-ordered to positive volume")
    report = {
        "solver": "CalculiX " + (installer.detect().get("version") or ""),
        "max_displacement_mm": float(dmag.max() * 1e3), "max_displacement_at_m": p[i_d].tolist(),
        "max_von_mises_MPa": float(vm_n.max() / 1e6), "max_von_mises_at_m": p[i_s].tolist(),
        "peak_von_mises_unaveraged_MPa": float(vm_n.max() / 1e6),
        "min_safety_factor": min((b["safety_factor"] for b in bodies if b["safety_factor"]), default=None),
        "bodies": bodies, "boundary_conditions": deck.report_bcs,
        "applied_force_N": total_applied.tolist(), "reaction_force_N": reaction,
        "force_balance_error": balance, "mass_kg": deck.mass, "gravity": g.tolist() if np.any(g) else None,
        "n_dof": 3 * deck.n_nodes, "n_elements": deck.n_elements, "solve_time_s": time.time() - t0,
        "warnings": warns,
        "assumptions": ["Linear elastic, small deflections; bonded contacts between bodies",
                        "Solved by CalculiX (separate program) with quadratic tetrahedra (C3D10), straight edges; "
                        "stresses are CalculiX's own nodal extrapolation"],
    }
    if thermal is not None:
        Tc = np.asarray(thermal["T_C"], float)
        report["thermal"] = {"T_ref_C": float(thermal.get("T_ref_C", 20.0)), "T_min_C": float(Tc.min()),
                             "T_max_C": float(Tc.max()),
                             "max_abs_dT_K": float(np.abs(Tc - float(thermal.get("T_ref_C", 20.0))).max())}
        report["assumptions"].append(
            "Thermal stress: one-way (temperature from the built-in heat solver written as a nodal *TEMPERATURE "
            "field, *STATIC step), stress-free at the reference temperature, constant material properties")
    return {"displacement": disp, "von_mises": vm_n, "report": report}


def solve_modal(mesh: dict, body_props: dict, bcs: list, n_modes: int = 10, band_hz=None, log=print, workdir=None,
                on_proc=None) -> dict:
    t0 = time.time()
    deck = inputdeck.build(mesh, body_props, bcs, "modal", n_modes=n_modes, log=log)
    log(f"CalculiX deck: {deck.n_elements:,} quadratic tetrahedra, {deck.n_nodes:,} nodes, {n_modes} modes")
    run_ccx(workdir, deck.text(), log, on_proc)
    wd = Path(workdir)
    freqs = results.read_frequencies(wd / f"{JOB}.dat")
    blocks = [b for b in results.read_frd(wd / f"{JOB}.frd") if b["name"] == "DISP"]
    if not freqs or len(blocks) < len(freqs):
        raise RuntimeError("CalculiX returned fewer mode shapes than frequencies.")
    n1 = deck.n_corner
    modes, shapes = [], []
    for i, (f, b) in enumerate(zip(freqs, blocks)):
        u = np.zeros((n1, 3))
        m = min(len(b["data"]) - 1, n1)
        u[:m] = b["data"][1:m + 1, :3]
        umax = float(np.linalg.norm(u, axis=1).max()) or 1.0
        modes.append({"mode": i + 1, "elastic_index": i + 1, "frequency_Hz": float(f), "rigid_body": False,
                      "effective_mass_fraction": None, "dominant_direction": None})
        shapes.append(u / umax)
    p = np.asarray(mesh["nodes"], float)
    warns = []
    fixed_area = sum(v["area_m2"] for v in deck.report_bcs.values() if v["type"] == "fixed_support")
    total_area = float(sum(v["area_m2"] for v in deck.report_bcs.values())) or 1.0
    report = {"modes": modes, "n_rigid_body_modes": 0, "mass_kg": deck.mass, "structure_mass_kg": deck.mass,
              "added_mass_kg": 0.0, "boundaries": deck.report_bcs, "unknowns": 3 * deck.n_nodes,
              "element": "10-node tetrahedron (quadratic, straight edges), solved by CalculiX",
              "solver": "CalculiX " + (installer.detect().get("version") or ""),
              "first_frequency_Hz": modes[0]["frequency_Hz"], "cumulative_mass_fraction": None,
              "supported_mass_fraction": 0.0}
    f_top = modes[-1]["frequency_Hz"]
    band_check(modes, band_hz, f_top, report, warns)
    report["warnings"] = warns
    report["assumptions"] = [
        "Linear elastic, isotropic materials; small vibrations; no damping (natural frequencies only)",
        "Touching parts are bonded; fixed supports are perfectly rigid - real mounts are softer, so the real "
        "frequencies are usually somewhat lower",
        "Solved by CalculiX (separate program, ARPACK eigen-solver); effective modal mass is not reported by this "
        "backend",
    ]
    report["seconds"] = round(time.time() - t0, 2)
    return {"modes": modes, "shapes": shapes, "report": report}
