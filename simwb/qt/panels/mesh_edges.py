"""Edge refinement and tolerant layers in the Mesh step (Expert mode, with the other advanced options): the controls, the estimate of the cells the edge
refinement adds before the mesh is made, what the mesher did after it, and the switch for the tolerant layer limits. The engine side is
simwb/edgepreview.py (the estimate, the same plan the mesher uses) and Project.set_mesh (edge_refinement, layers.tolerant); this file only builds the
widgets and writes the words. MeshAdvanced (mesh_adv.py) calls `signature`, `section` and `tolerant_widgets`."""
from __future__ import annotations

import re

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QCheckBox, QLabel

from ... import edgepreview, gmsh_worker
from .. import mode, theme
from ..forms import ParamForm, fmt_num
from ..ids import wid
from .common import _muted

N = lambda v: f"{int(round(v)):,}"            # noqa: E731  (a count with thousands)


def ROUGH(v) -> str:
    """An estimated count as the estimate deserves: two or three digits, not the last cell (48,263 would claim more than it knows)."""
    v = float(v)
    if v < 1000:
        return f"{int(round(v, -1)):,}"
    return f"{int(round(v, -2 if v < 100_000 else -3)):,}"
MM = lambda v: fmt_num(float(f"{v * 1000:.3g}"))  # noqa: E731  (metres as mm with three digits)


def _stats(m: dict) -> dict:
    return m.get("stats") or {}


def signature(adv, st) -> list:
    """Everything the controls below depend on, for MeshAdvanced's rebuild test."""
    m = st["mesh"]
    pr = adv.engine.project() if adv.engine.pid else None
    s = _stats(m)
    return [m.get("edge_refine"), m.get("no_edge_refine"), m.get("smart"), m.get("preset"), m.get("growth"), s.get("seconds"), s.get("edge_refine"), s.get("edge_refine_skipped"),
            (s.get("layers") or {}).get("seconds"), m.get("status"), adv.show_edges, adv._open.get("mesh.edges"),
            edgepreview.scan_state(pr)["state"] if pr else None, (getattr(pr, "_edge_measure", None) or {}).get("state") if pr else None]


def _msg(text: str, tone: str = "muted", name: str | None = None) -> QLabel:
    t = theme.tokens()
    lab = _muted(text)
    if tone != "muted":
        lab.setStyleSheet(f"color: {t[tone + '_text']};")
    if name:
        wid(lab, name)
    return lab


# ---------------------------------------------------------------------------------------------------------------------------------- the group
def section(adv, st, m):
    """The 'Edge refinement' group of the advanced options."""
    pr = adv.engine.project()
    e = m.get("edge_refine")
    s = adv._sec("Edge refinement", "mesh.edges", bool(e))
    s.head.toggled.connect(lambda on: QTimer.singleShot(0, lambda: adv.rebuild()))               # (opened: the edges are looked for, if that has not been done)
    s.body.addWidget(_muted("Finer cells along the sharp edges that cross the flow (the leading and trailing edge of a wing, the corner of a box), where the flow "
                            "changes fastest; the cells grow back to the wall size away from the edge. The extra cells are estimated before the mesh is made and "
                            "the edge cells are made coarser until the estimate fits under the cap. The estimate is within about 25 % in our measurements, so the "
                            "mesh can come out a little over the cap."))
    want = bool(e) or adv.show_edges or adv._open.get("mesh.edges", False) or bool(_stats(m).get("edge_refine"))
    pv = edgepreview.preview(pr, scan=want)
    adv.edge_pv = pv
    on = bool(pv["on"])
    chk = wid(QCheckBox("Refine sharp edges"), "mesh.edges.on")
    chk.setChecked(on and pv["available"])
    chk.setEnabled(pv["available"])
    chk.setToolTip("Make the cells finer along the sharp edges across the flow (a leading or trailing edge carries much of the friction of a thin body)"
                   if pv["available"] else pv["reason"])
    chk.toggled.connect(lambda v, pv=pv: _switch(adv, v, pv))
    s.body.addWidget(chk)
    if not pv["available"]:
        s.body.addWidget(_msg(pv["reason"] + ".", name="mesh.edges.note"))
        return s
    f = ParamForm([{"key": "factor", "label": "edge cell size = wall cell size ÷", "unit": "", "default": pv["factor"], "min": 2.0, "max": 20.0,
                    "help": "The cells at the edge are the wall cell size divided by this (2 to 20). The wall cell size and the result are shown below."},
                   {"key": "angle", "label": "refine edges sharper than", "unit": "°", "default": pv["max_angle"], "min": 20.0, "max": 175.0,
                    "help": "The angle between the two surfaces at the edge: 180° is smooth, 90° is the corner of a box, a few degrees is a knife edge. Edges "
                            "with a smaller angle than this are refined (140° leaves out rounded and lightly bent edges)."},
                   {"key": "cap", "label": "most extra cells (cap)", "unit": "cells", "default": pv["max_cells"], "min": 1000.0, "max": 5000000.0, "thousands": True,
                    "help": "The edge cells are made coarser until the estimated extra cells fit under this number (the estimate is within about 25 % in our measurements, so the mesh can come out a little over it)."}],
                  {"factor": pv["factor"], "angle": pv["max_angle"], "cap": pv["max_cells"]}, "mesh.edges")
    f.setEnabled(on)
    f.setToolTip("" if on else "Switch on 'Refine sharp edges' first")
    f.changed.connect(lambda k, v, f=f: _options(adv, f.values()))
    s.body.addWidget(f)
    s.body.addWidget(_estimate(pv, on))
    sw = wid(QCheckBox("Show the sharp edges in the 3D view"), "mesh.edges.show")
    ok = bool(pv["lines"])
    sw.setChecked(adv.show_edges and ok)
    sw.setEnabled(ok or pv["scan"] == "none" or adv.show_edges)
    sw.setToolTip("Draw the edges that get finer cells as orange lines over the model" if sw.isEnabled() else
                  ("Looking for the sharp edges…" if pv["scan"] == "running" else "There are no sharp edges to show with these settings"))
    sw.toggled.connect(lambda v: _show(adv, v))
    s.body.addWidget(sw)
    last = _last_mesh(m, pv)
    if last:
        s.body.addWidget(last)
    return s


def _estimate(pv: dict, on: bool) -> QLabel:
    """The live line: the wall cell size, the edge cell size, and about how many extra cells that adds (before there is a mesh)."""
    pl = pv["plan"]
    tone, name = "muted", "mesh.edges.estimate"
    if pv["blocked"] == "size_min":
        return _msg("⚠ Not used while a smallest cell size is set in Advanced sizes and Smart sizing is off: the mesher keeps to the size you set and leaves the "
                    "edges alone. Clear the smallest cell size, or switch Smart sizing on.", "warn", name)
    if pv["scan"] in ("none", "running"):
        return _msg("Looking for the sharp edges in the geometry…" if pv["scan"] == "running" else "Open this group to look for the sharp edges.", name=name)
    if pv["scan"] == "failed":
        return _msg(f"The sharp edges could not be looked for here ({pv.get('scan_error') or 'unknown reason'}); the mesher still does it when the mesh is made.",
                    "warn", name)
    if pl is None:
        return _msg("", name=name)
    lead = "" if on else "Off. Switched on it would: "
    if not pl["applied"]:
        why = {"no_edges": f"no sharp edge across the flow was found (edges where the surfaces meet at less than {fmt_num(pv['max_angle'])}° and are at least "
                           f"3 % of the body long), so nothing is refined",
               "finer_than_floor": "the wall cells are already about as fine as edge cells can be (the body length / 3000), so nothing is refined",
               "cap_too_low": f"even the coarsest edge cells would add more than the cap of {N(pl['cap_cells'])} cells, so nothing is refined: raise the cap"}[pl["reason"]]
        return _msg((lead + why + ".") if on else f"Off. {why[0].upper() + why[1:]}.", "muted" if on else "muted", name)
    length, used = fmt_num(float(f"{pl['total_length']:.3g}")), fmt_num(round(pl["factor_used"], 2))
    txt = (f"{lead}{'About' if on else 'about'} {ROUGH(pl['estimated_extra_cells'])} extra cells (a rough estimate, within about {int(gmsh_worker.EDGE_ESTIMATE_SPREAD * 100)} % in our "
           f"measurements; cap {N(pl['cap_cells'])}): {pl['edges']} sharp edge{'s' if pl['edges'] != 1 else ''} across the flow, {length} m long; "
           f"edge cells {MM(pl['h_edge'])} mm where the wall cells are {MM(pv['h_wall'])} mm.")
    if pl["capped"]:
        txt += f" ⚠ The cap changed the setting: edge cells {used}× finer than the wall cells instead of {fmt_num(pv['factor'])}×."
        tone = "warn"
    if pv["source"] == "smart":
        txt += " Smart sizing switches this on by itself (4×); change a value or switch it off here to use your own."
    return _msg(txt, tone, name)


def _last_mesh(m: dict, pv: dict) -> QLabel | None:
    """What the mesher did with the edges (its own numbers) and what the mesh holds at the edges, counted in the mesh itself: the count comes first, in the warning
    colour when it is over the cap."""
    s = _stats(m)
    if not s:
        return None
    er, why = s.get("edge_refine"), s.get("edge_refine_skipped")
    stale = " (the mesh is out of date: the settings changed since)" if m.get("status") == "stale" else ""
    t = theme.tokens()
    if er:
        used = fmt_num(round(er["factor_used"], 2))
        head = f"Last mesh: {er['edges']} sharp edge{'s' if er['edges'] != 1 else ''} refined, edge cells {MM(er['size'])} mm = wall cells ({MM(er['size_far_of_edge_field'])} mm) ÷ {used}"
        head += f" (the cap lowered it from ÷ {fmt_num(er['factor'])})." if er.get("capped") else "."
        mm = pv.get("measured")
        lines = []
        if mm and mm["state"] == "ready":
            r = mm["result"]
            over = r["extra_cells"] > er["cap_cells"]
            col = t["warn_text"] if over else t["ok_text"]
            lines.append(f"<div style='color:{col}'><b>{'⚠ ' if over else '✓ '}Counted in the mesh: about {ROUGH(r['extra_cells'])} extra cells</b> "
                         f"({N(r['zone_cells'])} cells within {MM(r['distance'])} mm of the edges, {r['zone_share'] * 100:.0f} % of the mesh)"
                         f"{' - over the cap of ' + N(er['cap_cells']) if over else ', cap ' + N(er['cap_cells'])}.</div>")
        elif mm and mm["state"] == "running":
            lines.append("<div>Counting the cells at the edges…</div>")
        lines.append(f"<div>The mesher estimated about {ROUGH(er['estimated_extra_cells'])} before meshing.{stale}</div>")
        txt = f"<div>{head}</div>" + "".join(lines)
    elif why:
        txt = ("<div>Last mesh: edge refinement was on but nothing was refined: "
               + {"size_min": "a smallest cell size was set in Advanced sizes and Smart sizing was off",
                  "no_edges": "no sharp edge across the flow was found",
                  "finer_than_floor": "the wall cells were already about as fine as edge cells can be",
                  "cap_too_low": "the cap on the extra cells was too low for even the coarsest edge cells"}.get(why, why) + "." + stale + "</div>")
    else:
        return None
    lab = wid(QLabel(txt), "mesh.edges.made")
    lab.setWordWrap(True)
    lab.setTextFormat(Qt.RichText)
    lab.setObjectName("muted")
    lab.setProperty("api_text", re.sub(r"<[^>]+>", " ", txt))
    return lab


# ---------------------------------------------------------------------------------------------------------------------------------- what the controls do
def _switch(adv, on: bool, pv: dict):
    try:
        if on:
            adv.engine.project().set_mesh(edge_refinement={"factor": pv["factor"], "max_cells": pv["max_cells"], "max_angle": pv["max_angle"]})
        else:
            adv.engine.project().set_mesh(edge_refinement=False)
        adv.engine.changed.emit()
    except Exception as e:  # noqa: BLE001
        adv.panel._fail(e)
        adv.engine.changed.emit()


def _options(adv, v: dict):
    try:
        adv.engine.project().set_mesh(edge_refinement={"factor": v["factor"], "max_angle": v["angle"], "max_cells": v["cap"]})
        adv.engine.changed.emit()
    except Exception as e:  # noqa: BLE001
        adv.panel._fail(e)
        adv.engine.changed.emit()


def _show(adv, on: bool):
    adv.show_edges = bool(on)
    adv._sig = None
    adv.panel.draw_marks()
    adv.engine.changed.emit()


def draw(adv):
    """The sharp edges in the 3D view when 'Show the sharp edges' is on (Expert mode, the Mesh step); called by the panel with the other marks."""
    pv = getattr(adv, "edge_pv", None)
    v = adv.panel.view
    if v is None:
        return
    lines = pv["lines"] if (pv and adv.show_edges and not mode.simple() and adv.panel.win is not None and adv.panel.win.step == "mesh") else []
    v.sharp_view.set(lines) if lines else v.sharp_view.clear()


# ---------------------------------------------------------------------------------------------------------------------------------- tolerant layers
def tolerant_widgets(adv, st, m) -> list:
    """The 'Tolerant layers' switch of the Wall layers group, and (after meshing) what the layer step kept."""
    ly = m.get("layers") or {}
    hexm = m.get("method") == "hex"
    out = []
    chk = wid(QCheckBox("Tolerant layers"), "mesh.layers.tolerant")
    chk.setChecked(bool(ly.get("tolerant")))
    chk.setEnabled(not hexm)
    chk.setToolTip("Not for a hex-dominant mesh: it makes its layers together with the mesh (snappyHexMesh) and does not use this setting (switch to the tetrahedral mesh)"
                   if hexm else
                   "Where the cells are small and the wall is sharp, the layer step removes layers that would be badly shaped. With this on its limits are looser, so "
                   "more of the layers stay on sharp edges and thin parts; they can be less smooth there, so look at the mesh quality. Off is the default.")
    chk.toggled.connect(lambda v: _tolerant(adv, v))
    out.append(chk)
    n = int(ly.get("n") or 0)
    note = ("Looser limits for the layer step: more of the layers stay on sharp edges and thin parts instead of being removed there (they can be less smooth)."
            if n > 0 else "Looser limits for the layer step so that more layers stay on sharp edges and thin parts. It matters once the number of layers is above 0.")
    out.append(wid(_muted(note), "mesh.layers.tolerant.note"))
    made = (_stats(m).get("layers") or {})
    if made.get("made"):
        was = (made.get("settings") or {}).get("tolerant")
        out.append(wid(_muted(f"Last mesh: {made.get('layers_max', 0)} layers made on {_pct(made.get('extruded_percent'))} % of the wall faces, with "
                              f"{'tolerant' if was else 'strict'} limits" + (" (the setting changed since: make the layers again)" if bool(was) != bool(ly.get("tolerant")) else "") + "."),
                       "mesh.layers.tolerant.made"))
    return out


def _pct(v) -> str:
    from ..meshtools import _pct as pct
    return pct(v)


def _tolerant(adv, on: bool):
    try:
        ly = dict(adv.engine.state()["mesh"].get("layers") or {})
        adv.engine.project().set_mesh(layers={"n": int(ly.get("n") or 0), "growth": ly.get("growth", 1.2), "first_mm": (ly.get("first") or 0) * 1000 or None,
                                              "y_plus": ly.get("y_plus"), "tolerant": bool(on)})
        adv.engine.changed.emit()
    except Exception as e:  # noqa: BLE001
        adv.panel._fail(e)
        adv.engine.changed.emit()
