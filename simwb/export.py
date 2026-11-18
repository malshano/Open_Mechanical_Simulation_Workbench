"""Results for use outside the workbench: a VTK file (ParaView, PyVista), a CSV table of the node values, and a
self-contained HTML report (setup, mesh, key results, warnings, assumptions) that prints to PDF from the browser.

All three are written from what the workbench already stores: the ``ResultSet`` and ``project.json``. Units are the
ones shown in the GUI (the field unit is part of the column / array name).
"""
from __future__ import annotations

import base64
import html
import re
from pathlib import Path

import numpy as np


def _name(field: str, unit: str) -> str:
    return f"{field} [{unit}]" if unit else field


# ---------------------------------------------------------------- VTK (.vtu)
def _b64_array(a: np.ndarray) -> str:
    """VTK inline binary: base64 of a uint32 byte count followed by the raw little-endian data."""
    raw = np.ascontiguousarray(a).tobytes()
    return base64.b64encode(np.uint32(len(raw)).tobytes() + raw).decode()


def _data_array(name: str, a: np.ndarray, kind: str | None = None) -> str:
    a = np.asarray(a)
    if a.dtype.kind == "f":
        a, vt = a.astype("<f4"), "Float32"
    elif a.dtype.itemsize == 1:
        a, vt = a.astype("u1"), "UInt8"
    else:
        a, vt = a.astype("<i4"), "Int32"
    nc = a.shape[1] if a.ndim == 2 else 1
    return (f'<DataArray type="{vt}" Name="{html.escape(name, quote=True)}" NumberOfComponents="{nc}" '
            f'format="binary">{_b64_array(a)}</DataArray>\n')


def write_vtu(rs, path: Path) -> Path:
    """The result set as a VTK unstructured grid of tetrahedra: node fields as point data, cell fields as cell
    data, the part of every cell as 'region'. Opens in ParaView / PyVista / VisIt."""
    n, m = len(rs.nodes), len(rs.tets)
    pd, cd = "", _data_array("region", rs.tet_region)
    for k, (loc, arr, unit) in rs.fields.items():
        arr = np.nan_to_num(np.asarray(arr, float), nan=0.0)
        if loc == "node" and len(arr) == n:
            pd += _data_array(_name(k, unit), arr)
        elif len(arr) == m:
            cd += _data_array(_name(k, unit), arr)
    xml = ('<?xml version="1.0"?>\n<VTKFile type="UnstructuredGrid" version="0.1" byte_order="LittleEndian" '
           'header_type="UInt32">\n<UnstructuredGrid>\n'
           f'<Piece NumberOfPoints="{n}" NumberOfCells="{m}">\n<Points>\n' + _data_array("Points", rs.nodes)
           + "</Points>\n<Cells>\n" + _data_array("connectivity", rs.tets.ravel())
           + _data_array("offsets", 4 * np.arange(1, m + 1)) + _data_array("types", np.full(m, 10, np.uint8))
           + f"</Cells>\n<PointData>\n{pd}</PointData>\n<CellData>\n{cd}</CellData>\n"
           "</Piece>\n</UnstructuredGrid>\n</VTKFile>\n")
    path = Path(path)
    path.write_text(xml, encoding="utf-8")
    return path


# ---------------------------------------------------------------- CSV
def write_csv(rs, path: Path) -> Path:
    """One row per mesh node: x, y, z [m] and every field (cell fields averaged to the nodes; vectors as three
    columns plus the magnitude)."""
    cols, names = [rs.nodes[:, 0], rs.nodes[:, 1], rs.nodes[:, 2]], ["x [m]", "y [m]", "z [m]"]
    for k, (loc, arr, unit) in rs.fields.items():
        arr = np.asarray(arr, float)
        if arr.ndim == 2:
            comps = arr if loc == "node" else np.column_stack([rs._to_nodes(arr[:, c]) for c in range(3)])
            for c, ax in enumerate("xyz"):
                cols.append(comps[:, c])
                names.append(_name(f"{k} {ax}", unit))
            cols.append(np.linalg.norm(comps, axis=1))
            names.append(_name(f"{k} magnitude", unit))
        else:
            cols.append(arr if loc == "node" else rs._to_nodes(arr))
            names.append(_name(k, unit))
    path = Path(path)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(",".join('"' + s.replace('"', "'") + '"' for s in names) + "\n")
        np.savetxt(f, np.column_stack(cols), delimiter=",", fmt="%.7g")
    return path


# ---------------------------------------------------------------- report
def _label(key: str) -> str:
    """'max_von_mises_MPa' -> 'Max von mises [MPa]' (the unit is the last part of the key by convention)."""
    parts = key.split("_")
    units = {"MPa", "Pa", "mm", "m", "kg", "N", "Hz", "W", "K", "C", "s", "g", "percent", "J", "m2", "m3"}
    unit = ""
    if len(parts) > 1 and parts[-1] in units:
        unit = {"percent": "%", "C": "°C", "m2": "m²", "m3": "m³"}.get(parts[-1], parts[-1])
        parts = parts[:-1]
    text = " ".join(parts)
    return text[:1].upper() + text[1:] + (f" [{unit}]" if unit else "")


def _num(v) -> str:
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (int, np.integer)):
        return f"{int(v):,}"
    if isinstance(v, (float, np.floating)):
        v = float(v)
        if v != v:
            return "-"
        if v == 0 or 1e-3 <= abs(v) < 1e6:
            return f"{v:.4g}"
        return f"{v:.3e}"
    return html.escape(str(v))


def _cell(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, (list, tuple)) and len(v) <= 6 and all(isinstance(x, (int, float)) for x in v):
        return " / ".join(_num(x) for x in v)
    if isinstance(v, dict) and len(v) <= 6 and all(isinstance(x, (int, float)) or x is None for x in v.values()):
        return ", ".join(f"{html.escape(str(k))} {_cell(x)}" for k, x in v.items())
    if isinstance(v, (dict, list, tuple)):
        return ""
    return _num(v)


def _table(rows: list, head: list | None = None) -> str:
    if not rows:
        return ""
    h = "<table>" + ("<tr>" + "".join(f"<th>{html.escape(str(c))}</th>" for c in head) + "</tr>" if head else "")
    for r in rows:
        h += "<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>"
    return h + "</table>"


def _summary_sections(s: dict) -> str:
    """Key numbers of a result summary without knowing the study: scalars as one table, every list of records
    (parts, modes, peaks ...) as its own table. Long series (histories, sweeps) are left to the plots."""
    skip = {"warnings", "assumptions", "history", "convergence"}
    scal, out = [], ""
    for k, v in s.items():
        if k in skip or v is None:
            continue
        if isinstance(v, (int, float, str, bool)) or (isinstance(v, (list, tuple)) and len(v) <= 3
                                                      and all(isinstance(x, (int, float)) for x in v)):
            if _cell(v) != "":  # (an empty list says nothing)
                scal.append([html.escape(_label(k)), _cell(v)])
    out += "<h2>Key results</h2>" + _table(scal)

    def records(title, recs):
        recs = [r for r in recs if isinstance(r, dict)]
        if not recs or len(recs) > 60:
            return ""
        keys = [k for k in recs[0] if any(_cell(r.get(k)) not in ("", "-") for r in recs)]
        if not keys:
            return ""
        return (f"<h3>{html.escape(_label(title))}</h3>"
                + _table([[_cell(r.get(k)) for k in keys] for r in recs], [_label(k) for k in keys]))

    for k, v in s.items():
        if k in skip:
            continue
        if isinstance(v, list) and v and isinstance(v[0], dict):
            out += records(k, v)
        elif isinstance(v, dict) and v and all(isinstance(x, dict) for x in v.values()):
            out += records(k, [dict({"name": name}, **rec) for name, rec in v.items()])
        elif isinstance(v, dict) and v:
            rows = [[html.escape(_label(kk)), _cell(x)] for kk, x in v.items() if _cell(x) not in ("",)]
            inner = [(kk, x) for kk, x in v.items() if isinstance(x, list) and x and isinstance(x[0], dict)]
            if rows:
                out += f"<h3>{html.escape(_label(k))}</h3>" + _table(rows)
            for kk, x in inner:
                out += records(f"{k} {kk}", x)
    return out


_CSS = """
body{font:14px/1.45 system-ui,'Segoe UI',sans-serif;color:#1c2330;max-width:900px;margin:28px auto;padding:0 20px}
h1{font-size:24px;margin:0 0 2px} h2{font-size:17px;margin:26px 0 8px;border-bottom:2px solid #d5dbe3;padding-bottom:3px}
h3{font-size:14px;margin:16px 0 6px} .sub{color:#5b6675;margin:0 0 14px}
table{border-collapse:collapse;width:100%;margin:4px 0 10px;font-size:13px}
td,th{border:1px solid #d5dbe3;padding:4px 8px;text-align:left;vertical-align:top} th{background:#eef1f5}
img{max-width:100%;border:1px solid #d5dbe3;border-radius:3px} .warn{background:#fff6e0;border-left:4px solid #d29922;
padding:6px 10px;margin:6px 0} ul{margin:4px 0 10px 20px;padding:0} .foot{color:#5b6675;font-size:12px;margin-top:30px}
@media print{body{margin:0;max-width:none} h2{break-after:avoid} table,img{break-inside:avoid}}
"""


def report_html(pr, image: str | None = None) -> str:
    """The study as one HTML page. image: a 'data:image/...;base64,' picture of the 3D view to include."""
    from . import materials

    d = pr.data
    spec = pr.spec
    solve = d.get("solve") or {}
    s = solve.get("summary") or {}
    e = lambda x: html.escape(str(x))  # noqa: E731
    h = (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>{e(d.get('name', pr.id))} - report"
         f"</title><style>{_CSS}</style></head><body>"
         f"<h1>{e(d.get('name', pr.id))}</h1><p class='sub'>{e(spec.get('label', d.get('study')))} · "
         f"{e(spec.get('solver_label', ''))} · solved {e(solve.get('finished') or 'not yet')}</p>")
    if image and re.match(r"^data:image/(png|jpeg);base64,[A-Za-z0-9+/=]+$", image):
        h += f"<img src='{image}' alt='3D view of the result'>"
    if solve.get("status") == "stale":
        h += "<div class='warn'>The setup changed after this result was computed: the numbers are out of date.</div>"

    # parts and materials
    rows = []
    for b in pr.bodies():
        cfg = d.get("bodies", {}).get(str(b["id"]), {})
        mat = cfg.get("material")
        label = ""
        if mat:
            try:
                label = materials.get_solid(mat).get("label", mat)
            except Exception:  # noqa: BLE001 - a fluid or an unknown key: show the key
                label = mat
        extra = ", ".join(f"{_label(k)} {_cell(v)}" for k, v in cfg.items() if k not in ("material", "name", "role")
                          and isinstance(v, (int, float)) and v)
        rows.append([e(cfg.get("name") or b.get("name", b["id"])), e(b.get("role", "solid")), e(label), e(extra)])
    h += "<h2>Parts</h2>" + _table(rows, ["Part", "Role", "Material", "Other"])

    # boundary conditions
    types = spec.get("face_bc_types", {})
    rows = [[e(bc.get("name", "")), e(types.get(bc["type"], {}).get("label", bc["type"])), str(len(bc.get("faces", []))),
             e(", ".join(f"{k} = {_cell(v)}" for k, v in (bc.get("params") or {}).items() if v not in (None, "")))]
            for bc in d.get("bcs", [])]
    h += "<h2>Boundary conditions</h2>" + (_table(rows, ["Name", "Type", "Faces", "Values"]) or "<p>None.</p>")
    h += f"<p>Other faces: {e(spec.get('default_face_bc', {}).get('label', '-'))}</p>"

    # settings
    vals = d.get("settings", {})
    rows = []
    for p in spec.get("settings", []):
        cond = p.get("show_if")
        if cond and not all(str(vals.get(k)) in ([str(x) for x in v] if isinstance(v, list) else [str(v)])
                            for k, v in cond.items()):
            continue
        rows.append([e(p["label"]), _cell(vals.get(p["key"], p.get("default"))) + (f" {e(p['unit'])}" if p.get("unit") else "")])
    h += "<h2>Settings</h2>" + _table(rows)

    # mesh
    ms = (d.get("mesh") or {}).get("stats") or {}
    if ms:
        q = ms.get("quality") or {}
        rows = [["Preset", e((d.get("mesh") or {}).get("preset", "-"))], ["Cells", _num(ms.get("cells", 0))],
                ["Nodes", _num(ms.get("nodes", 0))]]
        if q:
            rows += [["Cell quality, minimum / mean (1 = perfect)", f"{_num(q.get('min'))} / {_num(q.get('mean'))}"],
                     ["Poor cells (quality below 0.1)", _num(q.get("n_poor", 0))]]
        h += "<h2>Mesh</h2>" + _table(rows)

    # results
    if s:
        h += _summary_sections(s)
        if s.get("warnings"):
            h += "<h2>Warnings</h2>" + "".join(f"<div class='warn'>{e(w)}</div>" for w in s["warnings"])
        if s.get("assumptions"):
            h += "<h2>Model assumptions</h2><ul>" + "".join(f"<li>{e(a)}</li>" for a in s["assumptions"]) + "</ul>"
    else:
        h += "<h2>Results</h2><p>The study has not been solved yet.</p>"
    mc = d.get("mesh_check")
    if mc and mc.get("rows"):
        h += ("<h2>Mesh sensitivity</h2>"
              + _table([[e(r.get("name", "")), _cell(r.get("coarse")), _cell(r.get("fine")),
                         _cell(r.get("change_percent"))] for r in mc["rows"]],
                       ["Quantity", e(mc.get("from", "coarser")), e(mc.get("to", "finer")), "Change [%]"]))
        if mc.get("verdict"):
            h += f"<p>{e(mc['verdict'])}</p>"
    h += ("<p class='foot'>Made with the Mechanical Simulation Workbench. A simulation is a model: check the "
          "assumptions and the warnings above before using the numbers for a decision.</p></body></html>")
    return h
