"""First-principles heat-conduction checks through the workbench HTTP API (server on 127.0.0.1:8766).

 slab 100 x 20 x 20 mm aluminium 6061 (k from the material library), x = length, side faces insulated (the default)
 (a) both end faces at fixed temperature      -> Q = k A dT / L, linear profile
 (b) one end fixed, other end convection      -> series resistance L/(kA) + 1/(hA)
 (c) both ends fixed + uniform heat generation-> T_max = T0 + q L^2 / (8 k), Q = P / 2 per end

    python examples/showcase/validation_heat.py
"""
import csv
import io
import json
import sys
import urllib.request
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from validation_client import BASE, HDR, call, tool  # noqa: E402

sys.path.insert(0, str(Path(__file__).parents[2]))
from simwb import materials  # noqa: E402

HERE = Path(__file__).parent
LOC = str(Path.home() / "SimWB-Showcase" / "validation-studies")
MAT = "aluminium_6061"
K = materials.get_solid(MAT)["k"]
L, W, H = 0.100, 0.020, 0.020
A = W * H


def build(name, bcs, heat_W=0.0, size_mm=4.0):
    pid = call("POST", "/api/projects", {"name": name, "study": "heat_conduction", "location": LOC})["id"]
    r = json.loads(tool(pid, "import_step", step_path=str(HERE / "validation_slab.step")))
    assert r["import"] == "done", r
    tool(pid, "set_body", body_id=1, material=MAT, heat_W=heat_W)
    for b in bcs:
        faces = json.loads(tool(pid, "find_faces", normal=b["normal"]))["faces"]
        tool(pid, "add_boundary_condition", name=b["name"], type=b["type"], faces=[f["id"] for f in faces],
             params=b["params"])
    tool(pid, "set_settings", settings={"analysis": "steady"})
    tool(pid, "set_mesh_settings", size_max_mm=size_mm, size_min_mm=size_mm)
    return pid


def run(pid):
    m = json.loads(tool(pid, "generate_mesh"))
    assert m["status"] == "done", m
    r = json.loads(tool(pid, "run_simulation"))
    assert r["status"] == "done", r
    return m, r["result"]


def nodes_csv(pid):
    req = urllib.request.Request(f"{BASE}/api/projects/{pid}/results/export?format=csv", headers=HDR)
    txt = urllib.request.urlopen(req, timeout=120).read().decode()
    rows = list(csv.reader(io.StringIO(txt)))
    hdr = rows[0]
    arr = np.array(rows[1:], float)
    return hdr, arr


def profile_error(pid, T_of_x):
    hdr, arr = nodes_csv(pid)
    ix = [i for i, h in enumerate(hdr) if h.lower().startswith("x")][0]
    it = [i for i, h in enumerate(hdr) if "temperature" in h.lower()][0]
    x = arr[:, ix] / (1000.0 if arr[:, ix].max() > 1.0 else 1.0)  # mm -> m if needed
    T = arr[:, it]
    ref = T_of_x(x)
    d = T - ref
    return {"hdr": hdr, "n": len(x), "max_abs_K": float(np.abs(d).max()), "rms_K": float(np.sqrt((d ** 2).mean()))}


def pct(a, b):
    return 100 * (a - b) / b


def main():
    out = {"k": K, "A": A, "L": L}
    # (a)
    T0, T1 = 100.0, 20.0
    pid = build("Validation - slab conduction between two temperatures",
                [{"name": "Hot end", "type": "fixed_temperature", "normal": "-x", "params": {"T": T0}},
                 {"name": "Cold end", "type": "fixed_temperature", "normal": "+x", "params": {"T": T1}}])
    m, res = run(pid)
    b = res["boundaries"]
    Qh, Qc = b["Hot end"]["heat_out_W"], b["Cold end"]["heat_out_W"]
    out["a"] = {"pid": pid, "cells": m["cells"], "nodes": m["nodes"], "Q_hand": K * A * (T0 - T1) / L,
                "Q_hot_leaving_body_W": Qh, "Q_cold_leaving_body_W": Qc, "energy": res["energy_balance"],
                "T_min": res["bodies"]["1"]["T_min"], "T_max": res["bodies"]["1"]["T_max"],
                "T_mean": res["bodies"]["1"]["T_mean"],
                "profile": profile_error(pid, lambda x: T0 + (T1 - T0) * x / L), "seconds": res.get("seconds")}
    print("a", out["a"])
    # (b)
    h, Tinf = 500.0, 20.0
    pid = build("Validation - slab conduction with end convection",
                [{"name": "Hot end", "type": "fixed_temperature", "normal": "-x", "params": {"T": T0}},
                 {"name": "Convective end", "type": "convection", "normal": "+x", "params": {"h": h, "T_inf": Tinf}}])
    m, res = run(pid)
    b = res["boundaries"]
    Rc, Rv = L / (K * A), 1 / (h * A)
    Q = (T0 - Tinf) / (Rc + Rv)
    Ts = Tinf + Q * Rv
    out["b"] = {"pid": pid, "cells": m["cells"], "nodes": m["nodes"], "h": h, "R_cond": Rc, "R_conv": Rv, "Q_hand": Q,
                "Ts_hand": Ts, "Q_hot": b["Hot end"]["heat_out_W"], "Q_conv": b["Convective end"]["heat_out_W"],
                "Ts_wb": b["Convective end"]["T_mean"], "energy": res["energy_balance"],
                "profile": profile_error(pid, lambda x: T0 + (Ts - T0) * x / L), "seconds": res.get("seconds")}
    print("b", out["b"])
    # (c)
    P = 20.0
    T0c = 20.0
    pid = build("Validation - slab with heat generation",
                [{"name": "End A", "type": "fixed_temperature", "normal": "-x", "params": {"T": T0c}},
                 {"name": "End B", "type": "fixed_temperature", "normal": "+x", "params": {"T": T0c}}], heat_W=P)
    m, res = run(pid)
    b = res["boundaries"]
    qv = P / (A * L)
    out["c"] = {"pid": pid, "cells": m["cells"], "nodes": m["nodes"], "P": P, "qv": qv,
                "Tmax_hand": T0c + qv * L ** 2 / (8 * K), "Tmax_wb": res["bodies"]["1"]["T_max"],
                "Tmean_hand": T0c + qv * L ** 2 / (12 * K), "Tmean_wb": res["bodies"]["1"]["T_mean"],
                "Q_A": b["End A"]["heat_out_W"], "Q_B": b["End B"]["heat_out_W"], "energy": res["energy_balance"],
                "profile": profile_error(pid, lambda x: T0c + qv / (2 * K) * x * (L - x)), "seconds": res.get("seconds")}
    print("c", out["c"])
    json.dump(out, open(sys.argv[1] if len(sys.argv) > 1 else "heat_raw.json", "w"), indent=1, default=str)
    return out


if __name__ == "__main__":
    main()
