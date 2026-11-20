"""Tables and a grid-convergence estimate from mesh_benchmark.py results.

    python benchmarks/mesh_analyze.py results.jsonl [more.jsonl ...]

Only converged runs (`valid`) are used. Runs are grouped by method, layers, growth, mode and shape and ordered by the
solver's final cell count. For a group with three or more meshes the observed order of convergence p, the
Richardson-extrapolated Cf and Roache's grid-convergence index (safety factor 1.25) of the finest pair are printed;
the refinement ratio is r = (cells ratio)^(1/3).
"""
import json
import math
import sys
from collections import defaultdict


def load(paths):
    rows = []
    for p in paths:
        rows += [json.loads(ln) for ln in open(p, encoding="utf-8") if ln.strip()]
    return rows


def richardson(rows):
    """rows: three valid rows coarse -> fine with cf and cells_final. Returns (p, cf_extrapolated, gci_fine_percent) or None."""
    a, b, c = rows[-3:]
    r21, r32 = (b["cells_final"] / a["cells_final"]) ** (1 / 3), (c["cells_final"] / b["cells_final"]) ** (1 / 3)
    e21, e32 = b["cf"] - a["cf"], c["cf"] - b["cf"]
    if e21 == 0 or e32 == 0 or e21 * e32 < 0 or abs(r21 - r32) > 0.3 * r21:   # oscillating or very unequal ratios
        return None
    p = math.log(abs(e21 / e32)) / math.log((r21 + r32) / 2) if abs(e21 / e32) > 1 else None
    if not p:
        return None
    r = r32
    ext = c["cf"] + (c["cf"] - b["cf"]) / (r ** p - 1)
    gci = 1.25 * abs((c["cf"] - b["cf"]) / c["cf"]) / (r ** p - 1) * 100
    return p, ext, gci


def main(paths):
    rows = load(paths)
    groups = defaultdict(list)
    for r in rows:
        if r.get("error"):
            print(f"ERROR  {r.get('spec', r.get('method'))}: {r['error'][:120]}")
        elif not (r.get("converged") and (r.get("layers", 0) == 0 or r.get("layers_kept_percent") is None
                                          or r["layers_kept_percent"] >= 90.0)):
            why = ("unconverged" if not r.get("converged") else f"only {r['layers_kept_percent']:.0f} % of the layers kept")
            print(f"not used ({why}, err {r.get('cf_error_percent')} %)  {r.get('spec')}")
        else:
            groups[(r["method"], r["layers"], r["growth"], r["mode"], r["shape"])].append(r)
    for key, g in sorted(groups.items()):
        g.sort(key=lambda r: r["cells_final"])
        m, n, gr, mode, shape = key
        print(f"\n{m}  layers={n} growth={gr}  {mode} {shape} plate")
        print(f"  {'preset':<9}{'cells(mesh)':>12}{'cells(final)':>13}{'layers kept':>12}{'mesh s':>8}{'solve s':>9}{'RAM MB':>8}{'KB/cell':>9}{'Cf':>10}{'err %':>8}")
        for r in g:
            print(f"  {r['preset']:<9}{r['cells']:>12,}{r['cells_final']:>13,}{(r.get('layers_kept_percent') or 0):>11.0f}%"
                  f"{r['mesh_seconds']:>8.0f}{r['solve_seconds']:>9.0f}{(r.get('total_ram_mb') or 0):>8}{(r.get('ram_kb_per_final_cell') or 0):>9.1f}"
                  f"{r['cf']:>10.5f}{r.get('cf_error_percent', float('nan')):>8.1f}")
        if len(g) >= 3:
            rc = richardson(g)
            print("  grid convergence: " + (f"p = {rc[0]:.2f}, extrapolated Cf = {rc[1]:.5f}, GCI(fine) = {rc[2]:.1f} %" if rc else "not monotone / not enough information"))


if __name__ == "__main__":
    main(sys.argv[1:])
