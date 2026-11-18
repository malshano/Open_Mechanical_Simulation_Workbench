"""First-principles modal checks through the workbench HTTP API (server running on 127.0.0.1:8766).

 1. cantilever 200 x 20 x 3 mm aluminium, built-in solver, mesh 3.0 / 1.5 / 1.0 mm  vs Euler-Bernoulli (+ Timoshenko)
 2. the same, CalculiX solver (cross-check)
 3. free-free square bar 200 x 10 x 10 mm: bending and the first axial mode

    python examples/showcase/validation_modal.py [report_dir]
"""
import json
import math
import sys
from pathlib import Path

import numpy as np
import scipy.linalg as sla

sys.path.insert(0, str(Path(__file__).parent))
from validation_client import call, tool  # noqa: E402

sys.path.insert(0, str(Path(__file__).parents[2]))
from simwb import materials  # noqa: E402

HERE = Path(__file__).parent
LOC = str(Path.home() / "SimWB-Showcase" / "validation-studies")
REPORT = Path(sys.argv[1] if len(sys.argv) > 1 else Path.home() / "SimWB-Showcase" / "validation")
MAT = "aluminium_6061"
M = materials.get_solid(MAT)
E, RHO, NU = M["E"], M["rho"], M["nu"]
G = E / (2 * (1 + NU))
KAPPA = 10 * (1 + NU) / (12 + 11 * NU)  # Cowper shear coefficient, rectangular section

BL_CANT = [1.875104069, 4.694091133, 7.854757438]   # cos(bL) cosh(bL) = -1
BL_FREE = [4.730040745, 7.853204624, 10.99560784]   # cos(bL) cosh(bL) = +1


def eb(bl, I, A, L):
    return bl ** 2 / (2 * math.pi) * math.sqrt(E * I / (RHO * A * L ** 4))


def timoshenko(I, A, L, clamped, n=3, ne=600):
    """1-D Timoshenko beam FE (linear w, theta; reduced-integration shear; consistent mass), converged in ne."""
    h = L / ne
    nd = 2 * (ne + 1)
    K = np.zeros((nd, nd))
    Mm = np.zeros((nd, nd))
    kb = E * I / h * np.array([[1, -1], [-1, 1]])
    b = np.array([-1 / h, -0.5, 1 / h, -0.5])
    ks = KAPPA * G * A * h * np.outer(b, b)
    mt = RHO * A * h / 6 * np.array([[2, 1], [1, 2]])
    mr = RHO * I * h / 6 * np.array([[2, 1], [1, 2]])
    for e in range(ne):
        dofs = [2 * e, 2 * e + 1, 2 * e + 2, 2 * e + 3]  # w1 th1 w2 th2
        K[np.ix_(dofs, dofs)] += ks
        K[np.ix_(dofs[1::2], dofs[1::2])] += kb
        Mm[np.ix_(dofs[0::2], dofs[0::2])] += mt
        Mm[np.ix_(dofs[1::2], dofs[1::2])] += mr
    free = list(range(2, nd)) if clamped else list(range(nd))
    w = sla.eigh(K[np.ix_(free, free)], Mm[np.ix_(free, free)], eigvals_only=True)
    f = np.sqrt(np.abs(w)) / (2 * math.pi)
    f = f[f > 1.0] if not clamped else f
    return list(f[:n])


# ----------------------------------------------------------------------------- workbench
def make_project(name, step, bcs, settings, size_mm, study="vibration_modal"):
    p = call("POST", "/api/projects", {"name": name, "study": study, "location": LOC})
    pid = p["id"]
    r = json.loads(tool(pid, "import_step", step_path=str(HERE / step)))
    assert r["import"] == "done", r
    tool(pid, "set_body", body_id=1, material=MAT)
    for b in bcs:
        faces = json.loads(tool(pid, "find_faces", **b["find"]))["faces"]
        tool(pid, "add_boundary_condition", name=b["name"], type=b["type"], faces=[f["id"] for f in faces],
             params=b.get("params", {}))
    tool(pid, "set_settings", settings=settings)
    tool(pid, "set_mesh_settings", size_max_mm=size_mm, size_min_mm=size_mm)
    return pid


def solve(pid, size_mm=None):
    if size_mm:
        tool(pid, "set_mesh_settings", size_max_mm=size_mm, size_min_mm=size_mm)
    m = json.loads(tool(pid, "generate_mesh"))
    assert m["status"] == "done", m
    r = json.loads(tool(pid, "run_simulation"))
    assert r["status"] == "done", r
    return m, r["result"]


def families(modes):
    """Split the elastic modes into families by the direction with the effective mass."""
    fam = {"z": [], "y": [], "x": [], "other": []}
    for m in modes:
        em = m.get("effective_mass_fraction")
        if not em:
            continue
        d = max(em, key=em.get)
        key = d if em[d] > 0.02 else "other"
        fam[key].append(m["frequency_Hz"])
    return fam


def pct(a, b):
    return 100 * (a - b) / b


def main():
    out = {}
    w, t, L = 20e-3, 3e-3, 200e-3
    A = w * t
    Iz = w * t ** 3 / 12      # bending in z (thickness direction, weak axis)
    Iy = t * w ** 3 / 12      # bending in y (width direction)
    # --- hand values
    h = {
        "z": [eb(b, Iz, A, L) for b in BL_CANT],
        "y": [eb(b, Iy, A, L) for b in BL_CANT],
        "axial": 1 / (4 * L) * math.sqrt(E / RHO),
    }
    ht = {"z": timoshenko(Iz, A, L, True), "y": timoshenko(Iy, A, L, True)}
    # torsion (St Venant, free warping), cantilever first mode
    a_, b_ = w, t
    J = a_ * b_ ** 3 / 3 * (1 - 0.630 * b_ / a_ * (1 - b_ ** 4 / (12 * a_ ** 4)))
    Ip = RHO * (a_ * b_ ** 3 + b_ * a_ ** 3) / 12
    h["torsion"] = 1 / (4 * L) * math.sqrt(G * J / Ip)
    out["hand"], out["hand_timoshenko"] = h, ht

    # --- 1. cantilever, built-in solver, mesh convergence
    bcs = [{"name": "Clamped end", "type": "fixed_support", "find": {"normal": "-x"}}]
    pid = make_project("Validation - cantilever beam modal (built-in)", "validation_cantilever.step", bcs,
                       {"n_modes": 14}, 3.0)
    out["cantilever_pid"] = pid
    conv = []
    for size in (3.0, 1.5, 1.0):
        m, res = solve(pid, size)
        fam = families(res["modes"])
        conv.append({"size_mm": size, "cells": m["cells"], "nodes": m["nodes"], "unknowns": res["unknowns"],
                     "fam": fam, "seconds": res["seconds"], "modes": res["modes"]})
        print("built-in", size, m["cells"], {k: [round(x, 1) for x in v[:4]] for k, v in fam.items()})
    out["cantilever_conv"] = conv
    # --- 2. CalculiX cross-check on the finest mesh (copy of the study)
    calc = None
    try:
        p2 = call("POST", f"/api/projects/{pid}/duplicate", {"name": "Validation - cantilever beam modal (CalculiX)"})
        pid2 = p2["id"]
        tool(pid2, "set_settings", settings={"structural_solver": "calculix"})
        m2, res2 = solve(pid2)
        calc = {"pid": pid2, "cells": m2["cells"], "unknowns": res2.get("unknowns"),
                "freqs": [x["frequency_Hz"] for x in res2["modes"]], "seconds": res2.get("seconds")}
        print("calculix", [round(x, 1) for x in calc["freqs"]])
    except Exception as e:  # noqa: BLE001
        calc = {"error": str(e)}
        print("calculix failed", e)
    out["calculix"] = calc
    json.dump(out, open(REPORT / "_modal_raw.json", "w"), indent=1, default=str)

    # --- 3. free-free square bar. The workbench's own free-free solve fails its residual check with PARDISO (see
    # report), so the workbench solver is called in-process on the study's mesh with PARDISO switched off (SuperLU).
    s = 10e-3
    Ab, Ib = s * s, s ** 4 / 12
    pidf = make_project("Validation - free-free bar modal", "validation_bar.step", [], {"n_modes": 24}, 2.5)
    mf = json.loads(tool(pidf, "generate_mesh"))
    try:  # what the workbench itself does
        r = json.loads(tool(pidf, "run_simulation"))
        wb_status = r["status"] if r["status"] == "done" else r.get("error", "failed")[:200]
    except Exception as e:  # noqa: BLE001
        wb_status = str(e)[:200]
    folder = Path(call("GET", f"/api/projects/{pidf}")["path"])
    from simwb.solvers import modal_fem
    modal_fem._pardiso = lambda: None
    mesh = dict(np.load(folder / "mesh" / "mesh.npz"))
    resf = modal_fem.solve(mesh, {1: {"E": E, "nu": NU, "rho": RHO, "yield": None, "name": "Bar"}}, [], n_modes=24,
                           log=lambda x: None)
    modes, rep = resf["modes"], resf["report"]
    freqs, axial = [], []
    for mo, sh in zip(modes, resf["shapes"]):
        if mo["rigid_body"]:
            continue
        freqs.append(mo["frequency_Hz"])
        axial.append(float((sh[:, 0] ** 2).sum() / (sh ** 2).sum()))
    hb = [eb(b, Ib, Ab, L) for b in BL_FREE]
    hbt = timoshenko(Ib, Ab, L, False, n=3)
    ha = 1 / (2 * L) * math.sqrt(E / RHO)
    out["free"] = {"pid": pidf, "cells": mf["cells"], "unknowns": rep["unknowns"], "n_rigid": rep["n_rigid_body_modes"],
                   "freqs": freqs, "ux_share": axial, "hand_eb": hb, "hand_timo": hbt, "hand_axial": ha,
                   "workbench_status": wb_status, "residual": rep["max_relative_residual"]}
    print("free-free", [round(x, 1) for x in freqs], wb_status)
    json.dump(out, open(REPORT / "_modal_raw.json", "w"), indent=1, default=str)
    return out


if __name__ == "__main__":
    main()
