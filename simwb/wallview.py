"""The wall-layer view after a solve: for every wall of a flow study the layers the mesh got (made of planned, the first layer and the total thickness)
and the y+ the solver found there, with how much of the wall lies in each y+ band and what that means for the wall treatment of the run.

The layers come from the layer step of the mesher (the statistics of the mesh: one row per OpenFOAM wall patch, as the log reports them); the y+ from the
result (the value of every wall face, in `ResultSet.surf_values["y+"]`, weighted by the face's area). A hex-dominant mesh reports only the share of wall
faces that got layers, not the layers per wall, so its rows carry the y+ only."""
from __future__ import annotations

import numpy as np

# the bands of y+ and what they mean for a wall-function model (the mesh advisor's words: the wall functions want 30 or more, 5 to 30 is the buffer layer
# where they are least accurate, below 5 the viscous sub-layer is resolved)
BANDS = [("resolved", 0.0, 5.0, "Below 5: the viscous sub-layer is resolved (right for a laminar or low-Reynolds run; finer than a wall function needs)"),
         ("buffer", 5.0, 30.0, "5 to 30: the buffer layer, where wall functions are least accurate"),
         ("log", 30.0, 300.0, "30 to 300: the range the wall functions expect"),
         ("high", 300.0, float("inf"), "Above 300: the first cell is too thick for a wall function")]


def band_shares(y: np.ndarray, area: np.ndarray) -> dict:
    tot = float(area.sum()) or 1.0
    return {k: round(100.0 * float(area[(y >= lo) & (y < hi)].sum()) / tot, 1) for k, lo, hi, _ in BANDS}


def patch_rows(rs, layer_patches: list | None) -> list:
    """One row per wall (OpenFOAM patch with a y+): name, area, y+ statistics and shares, and the layers the mesh got there (None when not known)."""
    yv = rs._scalar(rs.surf_values["y+"])
    t = rs.surf_tris
    p = rs.nodes
    area = 0.5 * np.linalg.norm(np.cross(p[t[:, 1]] - p[t[:, 0]], p[t[:, 2]] - p[t[:, 0]]), axis=1)
    lay = {r["name"]: r for r in (layer_patches or [])}
    rows = []
    for gi, name in enumerate(rs.groups):
        sel = (rs.surf_group == gi) & np.isfinite(yv) & (yv > 0)
        if not sel.any():
            continue
        y, a = yv[sel], area[sel]
        order = np.argsort(y)
        cum = np.cumsum(a[order]) / a.sum()
        row_l = lay.get(name)
        rows.append({"name": name, "area_m2": float(a.sum()), "faces": int(sel.sum()),
                     "yplus": {"min": float(y.min()), "mean": float((y * a).sum() / a.sum()), "median": float(y[order][min(np.searchsorted(cum, 0.5), len(y) - 1)]),
                               "p95": float(y[order][min(np.searchsorted(cum, 0.95), len(y) - 1)]), "max": float(y.max()), "shares": band_shares(y, a)},
                     "layers": None if row_l is None else {"made": row_l.get("made"), "target": row_l.get("target"), "first_mm": row_l.get("first_mm"),
                                                          "total_mm": row_l.get("total_mm"), "percent_of_plan": row_l.get("percent")}})
    return rows


def verdict(rows: list, laminar: bool = False) -> tuple:
    """(tone, words) about the y+ over the walls together, by area."""
    if not rows:
        return "", "No wall y+ in this result."
    tot = sum(r["area_m2"] for r in rows)
    sh = {k: sum(r["area_m2"] * r["yplus"]["shares"][k] for r in rows) / tot for k, *_ in BANDS}
    if laminar:
        ok = sh["resolved"] > 80
        return ("ok" if ok else "warn",
                f"{sh['resolved']:.0f} % of the wall has y+ below 5" + ("" if ok else ": a laminar run wants the first cell inside the viscous sub-layer"))
    if sh["log"] >= 70:
        return "ok", f"{sh['log']:.0f} % of the wall is in the 30 to 300 the wall functions expect"
    return "warn", (f"Only {sh['log']:.0f} % of the wall is in the 30 to 300 the wall functions expect: {sh['buffer']:.0f} % in the buffer layer (5 to 30), "
                    f"{sh['resolved']:.0f} % below 5, {sh['high']:.0f} % above 300")


def view(pr) -> dict:
    """{available, reason, method, layers_used, rows, verdict: {tone, words}, laminar, bands}: see the module text."""
    if pr.study != "external_aero":
        raise ValueError("The wall-layer view is for external aerodynamics studies")
    out = {"available": False, "reason": None, "rows": [], "verdict": None, "layers_used": False, "method": None, "laminar": False}
    st = (pr.data["mesh"].get("stats") or {})
    out["method"] = st.get("method") or "tet"
    try:
        rs = pr.results()
    except ValueError as e:
        out["reason"] = str(e)
        return out
    if "y+" not in rs.surf_values:
        out["reason"] = "This result has no wall y+ (the run did not write it)"
        return out
    lay = st.get("layers") or {}
    made = bool(lay.get("made")) and not lay.get("stale") and out["method"] != "hex"
    out["layers_used"] = made
    out["laminar"] = (pr.data.get("settings") or {}).get("turbulence") == "laminar"
    out["rows"] = patch_rows(rs, lay.get("patches") if made else None)
    out["available"] = bool(out["rows"])
    tone, words = verdict(out["rows"], out["laminar"])
    out["verdict"] = {"tone": tone, "words": words}
    if out["method"] == "hex" and (st.get("hex") or {}).get("layers_extruded_percent") is not None:
        out["hex_layers_percent"] = st["hex"]["layers_extruded_percent"]
    out["bands"] = [{"key": k, "from": lo, "to": None if hi == float("inf") else hi, "text": txt} for k, lo, hi, txt in BANDS]
    return out
