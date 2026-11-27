"""Tables and a grid-convergence estimate from mesh_benchmark.py results.

    python benchmarks/mesh_analyze.py results.jsonl [more.jsonl ...]

A run is used only when it converged and its wall layers are there (mesh_benchmark.layers_ok: at least 80 % of the
target thickness by the layer table of the log, else at least 90 % of the wall faces extruded); the stored `valid` flag
is ignored and recomputed, so files written under older rules are judged by the same rule. Rows are grouped by the
complete method (method, layers, growth, flow mode, shape, edge refinement, tolerant layers, leading-edge size) and
ordered by the solver's final cell count; repeats of one spec are shown as a mean and a range. A group of three or more
meshes also gets the observed order of convergence, the Richardson-extrapolated Cf and Roache's grid-convergence index
(safety factor 1.25) of the finest pair, with r = (cells ratio)^(1/3); only when the three values converge monotonically.
"""
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mesh_benchmark import layers_ok  # noqa: E402


def load(paths):
    rows = []
    for p in paths:
        rows += [json.loads(ln) for ln in open(p, encoding="utf-8") if ln.strip()]
    return rows


def method_key(r):
    return (r["method"], r["layers"], r["growth"], r["mode"], r["shape"], r.get("edge_factor"),
            bool(r.get("tolerant_layers")), r.get("le_mm"))


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


def usable(r):
    """Converged by the app, or (stop rule off: the app never calls such a run converged) Cf steady by its own history:
    changed by less than 0.1 % over the last 100 iterations."""
    drift = r.get("cf_drift_last100_percent")
    steady_by_history = r.get("stop_tol_percent") == 0 and drift is not None and abs(drift) < 0.1
    return (bool(r.get("converged")) or steady_by_history) and layers_ok(r)


def stopping_table(rows):
    """Rows that carry stop_tol_percent: per mesh preset and stop rule, where the runs stopped, Cf and its spread
    between the repeats, and how much Cf still changed over the last 100 iterations. The spread of the runs with the
    rule off is the part that is not the stop rule (mesher / parallel-run noise)."""
    g = defaultdict(list)
    for r in rows:
        if "stop_tol_percent" in r and not r.get("error") and r.get("cf"):
            g[(r["method"], r["preset"], r["stop_tol_percent"])].append(r)
    if not g:
        return
    print("\nstop rule: spread of Cf between repeats (percent of the mean)")
    print(f"  {'method':<7}{'preset':<8}{'stop %':>7}{'n':>3}{'stopped at':>14}{'cf mean':>10}{'spread %':>10}{'drift last 100 it %':>21}{'cells':>9}")
    for (m, pr, tol), rs in sorted(g.items(), key=lambda kv: (kv[0][0], kv[0][1], -kv[0][2])):
        cf = [r["cf"] for r in rs]
        mean = sum(cf) / len(cf)
        its = sorted(r.get("stopped_at") or r.get("iterations") or 0 for r in rs)
        dr = [r.get("cf_drift_last100_percent") for r in rs if r.get("cf_drift_last100_percent") is not None]
        print(f"  {m:<7}{pr:<8}{tol:>7g}{len(rs):>3}{its[0]:>8}..{its[-1]:<4}{mean:>10.6f}{100 * (max(cf) - min(cf)) / mean:>10.3f}"
              f"{(max(dr, key=abs) if dr else float('nan')):>21.4f}{min(r['cells_final'] for r in rs):>9,}-{max(r['cells_final'] for r in rs):,}")


def main(paths):
    rows = load(paths)
    stopping_table(rows)
    groups = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if str(r.get("edge_field", "")).endswith("superseded"):      # made by code that no longer exists (see results/README.md)
            continue
        if r.get("error"):
            print(f"ERROR  {r.get('spec', r.get('method'))}: {r['error'][:120]}")
        elif not usable(r):
            why = "unconverged" if not r.get("converged") else (
                f"{r.get('layers_mean')} of {r['layers']} layers, {r.get('layers_thickness_percent')} % thickness"
                if r.get("layers_mean") is not None else f"{r.get('layers_kept_percent')} % of faces extruded")
            print(f"not used ({why}; err {r.get('cf_error_percent')} %)  {r.get('spec')}")
        else:
            groups[method_key(r)][r["preset"]].append(r)
    for key, presets in sorted(groups.items(), key=lambda kv: str(kv[0])):
        m, n, gr, mode, shape, edge, tol, le = key
        extra = (f" edge x{edge:g}" if edge else "") + (" tolerant layers" if tol else "") + (f" le {le:g} mm" if le else "")
        print(f"\n{m}  layers={n} growth={gr}{extra}  {mode} {shape} plate")
        print(f"  {'preset':<9}{'n':>2}{'cells(mesh)':>12}{'cells(final)':>13}{'layers':>8}{'mesh s':>8}{'solve s':>9}{'RAM MB':>8}{'Cf':>10}{'err %':>8}")
        table = []
        for preset, rs in presets.items():
            rs.sort(key=lambda r: r["cf"])
            mid = rs[len(rs) // 2]
            table.append((mid, rs))
        table.sort(key=lambda t: t[0]["cells_final"])
        for mid, rs in table:
            spread = f"  (range {rs[0].get('cf_error_percent')} .. {rs[-1].get('cf_error_percent')} %)" if len(rs) > 1 else ""
            lay = f"{mid['layers_mean']}" if mid.get("layers_mean") is not None else f"{(mid.get('layers_kept_percent') or 0):.0f}%"
            print(f"  {mid['preset']:<9}{len(rs):>2}{mid['cells']:>12,}{mid['cells_final']:>13,}{lay:>8}"
                  f"{mid['mesh_seconds']:>8.0f}{mid['solve_seconds']:>9.0f}{(mid.get('total_ram_mb') or 0):>8}"
                  f"{mid['cf']:>10.5f}{mid.get('cf_error_percent', float('nan')):>8.1f}{spread}")
        if len(table) >= 3:
            rc = richardson([t[0] for t in table])
            print("  grid convergence: " + (f"p = {rc[0]:.2f}, extrapolated Cf = {rc[1]:.5f}, GCI(fine) = {rc[2]:.1f} %" if rc else "not monotone / not enough information"))


if __name__ == "__main__":
    main(sys.argv[1:])
