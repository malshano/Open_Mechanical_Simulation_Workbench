"""The sizing methods and the layer methods of the Mesh step (Expert mode): wake box, curvature and proximity sizing, growth-limited coarsening (the 'Sizing
methods' group) and the layer clamp and layer method (in the Wall layers group). The engine side is simwb/sizingmethods.py (the plans, the same functions the
mesher and the memory planner use), simwb/localgeom.py and Project.set_mesh (sizing_methods, layers.clamp, layers.method); this file only builds the widgets and
writes the words. MeshAdvanced (mesh_adv.py) calls `signature`, `section` and `layer_widgets`."""
from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QLabel

from .. import theme
from ..forms import ParamForm, fmt_num
from ..ids import wid
from .common import _muted

N = lambda v: f"{int(round(v)):,}"            # noqa: E731  (a count with thousands)


def _msg(text: str, tone: str = "muted", name: str | None = None) -> QLabel:
    t = theme.tokens()
    lab = _muted(text)
    if tone != "muted":
        lab.setStyleSheet(f"color: {t[tone + '_text']};")
    if name:
        wid(lab, name)
    return lab


def _fail(adv, e):
    adv.panel._fail(e)
    adv.engine.changed.emit()


def signature(adv, st) -> list:
    """Everything the controls here depend on, for MeshAdvanced's rebuild test."""
    m = st["mesh"]
    return [(m.get("layers") or {}).get("clamp"), (m.get("layers") or {}).get("method"), m.get("sizing_methods"), adv.show_wake, m.get("smart"), m.get("preset"), m.get("growth"),
            m.get("status"), (m.get("stats") or {}).get("seconds")]


# ---------------------------------------------------------------------------------------------------------------------------------- the layer clamp
def layer_widgets(adv, st, m) -> list:
    """The layer clamp of the Wall layers group: fit the layers to the local thickness of the body (#33)."""
    ly = m.get("layers") or {}
    ck = ly.get("clamp")
    n = int(ly.get("n") or 0)
    hexm = m.get("method") == "hex"
    out = []
    chk = wid(QCheckBox("Fit the layers to the local thickness"), "mesh.layers.clamp")
    chk.setChecked(bool(ck))
    chk.setEnabled(not hexm)
    chk.setToolTip("Not for a hex-dominant mesh: it makes its layers together with the mesh (snappyHexMesh) and does not use this setting (switch to the tetrahedral mesh)"
                   if hexm else
                   "Measure how thick the body is behind its walls and cut the layers so that all of them together take at most half of that on most of the wall. "
                   "Thin plates and the tips of trailing edges are where layers from the two sides run into each other and collapse. Off is the default.")
    chk.toggled.connect(lambda v: _clamp_switch(adv, v))
    out.append(chk)
    f = ParamForm([{"key": "fraction", "label": "all the layers together take at most", "unit": "× local thickness", "default": 0.5, "min": 0.1, "max": 0.5,
                    "help": "0.5 = half the thickness: a layer grows from each side of a thin part. Lower leaves more room."},
                   {"key": "coverage", "label": "of the wall that must keep all its layers", "unit": "%", "default": 95.0, "min": 50.0, "max": 99.9,
                    "help": "The thinnest rest of the wall (the tip of a trailing edge, where the thickness goes to zero) is left to the layer step, which removes layers "
                            "there. 100 would size the layers for the very tip and leave one thin layer everywhere."}],
                  {"fraction": (ck or {}).get("fraction", 0.5), "coverage": 100.0 * (ck or {}).get("coverage", 0.95)}, "mesh.layers.clamp")
    f.setEnabled(bool(ck) and not hexm)
    f.changed.connect(lambda k, v, f=f: _clamp_set(adv, f.values()))
    out.append(f)
    note = ("The layers are cut to the thickness of the body behind the wall (measured on the faces of the geometry, along the wall normal)."
            if ck else "Off: the layers keep the number and thickness you set, and the layer step removes them where they do not fit.")
    pr = adv.engine.project() if adv.engine.pid else None
    rep = pr.layer_clamp_report() if (pr and ck and n > 0) else None
    if rep and rep.get("error"):
        out.append(wid(_msg("The thickness could not be measured here (" + rep["error"] + "); the run still adds the layers you set.", "warn"), "mesh.layers.clamp.note"))
    elif rep:
        txt = f"{n} layers asked for"
        if rep["thickness_ref"] is None:
            txt = f"All {n} layers fit: the body is thick enough behind the whole wall."
        else:
            thin = fmt_num(round(rep["thickness_ref"] * 1000, 3))
            if rep["changed"]:
                txt = (f"The body is {thin} mm thick or less behind {100 - 100 * rep['coverage']:.0f} % of the wall. The layers are cut from {n} to {rep['n']}"
                       + ("" if rep["y_plus_kept"] else f" and the first one thinned to {fmt_num(round(rep['first'] * 1000, 4))} mm (the y+ target is no longer met)")
                       + f"; all together {fmt_num(round(rep['total'] * 1000, 3))} mm. About {100 * rep['share_short']:.0f} % of the wall is still thinner than "
                       f"that allows (it was {100 * rep['share_short_before']:.0f} % with {n}); the layer step removes them there.")
            else:
                txt = f"All {n} layers fit: the body is at least {thin} mm thick behind {100 * rep['coverage']:.0f} % of the wall, and the layers take {fmt_num(round(rep['total'] * 1000, 3))} mm."
        out.append(wid(_msg(txt, "warn" if rep["changed"] and not rep["y_plus_kept"] else "muted"), "mesh.layers.clamp.note"))
    else:
        out.append(wid(_muted(note), "mesh.layers.clamp.note"))
    return out


def _clamp_switch(adv, on: bool):
    try:
        ly = dict(adv.engine.state()["mesh"].get("layers") or {})
        adv.engine.project().set_mesh(layers={"n": int(ly.get("n") or 0), "growth": ly.get("growth", 1.2), "first_mm": (ly.get("first") or 0) * 1000 or None,
                                              "y_plus": ly.get("y_plus"), "clamp": bool(on)})
        adv.engine.changed.emit()
    except Exception as e:  # noqa: BLE001
        _fail(adv, e)


def _clamp_set(adv, v: dict):
    try:
        ly = dict(adv.engine.state()["mesh"].get("layers") or {})
        adv.engine.project().set_mesh(layers={"n": int(ly.get("n") or 0), "growth": ly.get("growth", 1.2), "first_mm": (ly.get("first") or 0) * 1000 or None,
                                              "y_plus": ly.get("y_plus"), "clamp": {"fraction": v["fraction"], "coverage": v["coverage"] / 100.0}})
        adv.engine.changed.emit()
    except Exception as e:  # noqa: BLE001
        _fail(adv, e)


# ---------------------------------------------------------------------------------------------------------------------------------- the sizing methods
from ... import sizingmethods                      # noqa: E402

SHOWN = ("wake", "curvature", "proximity")
TITLES = {"wake": "Wake box", "curvature": "Curvature sizing", "proximity": "Proximity sizing (gaps and thin walls)", "coarsen": "Coarsen away from the walls"}
WHEN = {
    "wake": "Use it when the drag or the wake matters: the cells behind the body along the flow are made as fine as you ask, sized from the wall cells and the body, "
            "instead of growing to the far-field size right behind it. With Smart sizing on, this replaces its wake box.",
    "curvature": "Use it for parts with tight curves (fillets, holes, small cylinders): the faces are meshed with this many cells round a full circle, and the "
                 "fine cells grow back to the wall size with the mesh growth. A radius is measured on the display triangulation of the faces, so it is a rough guide; "
                 "the whole face gets the size (not only the tight part of it).",
    "proximity": "Use it for small gaps between walls (a slot, the gap between a wing and a flap, fins) and, in a solid study, for thin walls: at least this many cells "
                 "across. The faces that face each other get cells of the gap width divided by the number; a face with only a sliver of gap on it is left alone.",
}
CAP = {"key": "max_cells", "label": "most extra cells (cap)", "unit": "cells", "min": 1000.0, "max": 20000000.0, "default": 300000.0, "thousands": True,
       "help": "The cells are made coarser until the estimate fits under this number."}
PARAMS = {
    "wake": [{"key": "length", "label": "wake length", "unit": "body lengths", "min": 0.5, "max": 20.0, "default": 4.0,
              "help": "How far behind the body the box reaches, in lengths of the body along the flow."},
             {"key": "side", "label": "margin at each side", "unit": "body lengths", "min": 0.0, "max": 3.0, "default": 0.4,
              "help": "How much wider than the body the box is on each side, in body lengths."},
             {"key": "size_factor", "label": "cell size = wall cell size ×", "unit": "", "min": 1.5, "max": 40.0, "default": 5.0,
              "help": "The cells in the box, as a multiple of the wall cell size (Smart sizing uses 4 to 5)."},
             {"key": "size_mm", "label": "or a cell size of", "unit": "mm", "min": 0.001, "default": None, "blank": True, "placeholder": "from the factor",
              "help": "A cell size of your own (empty = use the factor); it replaces the factor."},
             dict(CAP, label="most cells in the box (cap)")],
    "curvature": [{"key": "elements", "label": "cells round a full circle", "unit": "", "min": 6.0, "max": 90.0, "default": None, "blank": True,
                   "placeholder": "from the preset", "help": "6 is crude, 24 is fine; a tighter radius gets smaller cells (empty = the preset's)."},
                  {"key": "min_size_mm", "label": "smallest cell", "unit": "mm", "min": 0.001, "default": None, "blank": True, "placeholder": "automatic",
                   "help": "No cell is made finer than this, however tight the curve (empty = the wall cell ÷ 5)."},
                  CAP],
    "proximity": [{"key": "cells_across", "label": "cells across a gap", "unit": "", "min": 2.0, "max": 12.0, "default": 3.0, "help": "At least this many cells across the gap."},
                  {"key": "max_gap_mm", "label": "look for gaps up to", "unit": "mm", "min": 0.001, "default": None, "blank": True,
                   "placeholder": "automatic", "help": "Wider gaps are not looked for: the wall cells already fit several across (empty = that many wall cells)."},
                  {"key": "min_size_mm", "label": "smallest cell", "unit": "mm", "min": 0.001, "default": None, "blank": True, "placeholder": "automatic",
                   "help": "No cell is made finer than this, however narrow the gap (empty = the wall cell ÷ 4)."},
                  CAP],
}
SPREAD_TIP = ("How exact the numbers are. Measured on real meshes (quick preset): the cells added by curvature sizing came out 0.55 to 0.74 of the estimate; for the two faces "
              "of a narrow gap 0.21 to 0.38 (the estimate is an upper bound there, so the cap holds); the wake box was within 15 %. Other geometries were not measured.")


def sizing_section(adv, st, m):
    """The 'Sizing methods' group of the advanced options."""
    pr = adv.engine.project()
    sm = m.get("sizing_methods") or {}
    anyon = any(v.get("on") for v in sm.values())
    s = adv._sec("Sizing methods", "mesh.methods", anyon)
    s.body.addWidget(_muted("Choose how the mesh is sized round what matters in this model. Each method is explained, estimates the cells it adds before the mesh is made, and "
                            "keeps them under its cap (the cell sizes are made coarser until they fit)."))
    pv = sizingmethods.preview(pr) if (anyon and adv.engine.pid) else {"info": {}}
    adv.methods_pv = pv
    for name in SHOWN:
        if name == "wake" and st["study"] != "external_aero":
            continue                                                    # (not a thing in this study: not shown)
        why = sizingmethods.unavailable(pr, name)
        rec = sm.get(name) or {}
        on = bool(rec.get("on")) and why is None
        chk = wid(QCheckBox(TITLES[name]), f"mesh.methods.{name}.on")
        chk.setChecked(on)
        chk.setEnabled(why is None)
        chk.setToolTip(why or WHEN[name])
        chk.toggled.connect(lambda v, n=name: _method_switch(adv, n, v))
        s.body.addWidget(chk)
        s.body.addWidget(wid(_muted(WHEN[name]), f"mesh.methods.{name}.about"))
        if why:
            s.body.addWidget(_msg(why + ".", name=f"mesh.methods.{name}.note"))
            continue
        vals = {p["key"]: rec.get(p["key"], p.get("default")) for p in PARAMS[name]}
        f = ParamForm(PARAMS[name], vals, f"mesh.methods.{name}")
        f.setEnabled(on)
        f.changed.connect(lambda k, v, n=name, f=f: _method_options(adv, n, f.values()))
        s.body.addWidget(f)
        est = _msg(_words(name, pv["info"].get(name), on, pv), name=f"mesh.methods.{name}.note")
        est.setToolTip(SPREAD_TIP)
        s.body.addWidget(est)
    if st["study"] == "external_aero":
        sw = wid(QCheckBox("Show the wake box in the 3D view"), "mesh.methods.wake.show")
        sw.setChecked(adv.show_wake and bool(pv.get("wake_region")))
        sw.setEnabled(bool(pv.get("wake_region")))
        sw.setToolTip("Draw the wake box as a translucent box behind the body" if sw.isEnabled() else "Switch the wake box on first")
        sw.toggled.connect(lambda v: _show_wake(adv, v))
        s.body.addWidget(sw)
    return s


def _words(name: str, info: dict | None, on: bool, pv: dict) -> str:
    if pv.get("error"):
        return "The estimate could not be made here (" + pv["error"] + "); the mesher still applies the method."
    if not on:
        return "Off."
    if not info:
        return "Looking at the geometry…"
    mm = lambda v: fmt_num(float(f"{v * 1000:.3g}"))                    # noqa: E731
    cap = N(info.get("cap", 0))
    if name == "wake":
        txt = (f"The box reaches {fmt_num(info['length'])} body lengths behind the body and {fmt_num(info['side'])} either side; cells of {mm(info['size'])} mm, about "
               f"{N(info['cells'])} cells (rough: within about 15 %; cap {cap}).")
        if info["capped"]:
            txt += f" ⚠ The cap made the cells coarser than the {mm(info['size_asked'])} mm asked for."
        return txt
    if not info.get("applied"):
        return {"no_curved_faces": "No face is curved tightly enough to need finer cells than the wall cells.", "no_gaps": "No gap or thin wall was found within reach.",
                "gaps_resolved": "The gaps found are wide enough for the wall cells."}.get(info.get("reason"), "Nothing to refine.")
    if name == "curvature":
        txt = f"{info['faces']} curved face{'s' if info['faces'] != 1 else ''} refined, down to {mm(info['smallest'])} mm"
    else:
        what = "gaps" if info["side"] == "out" else "thin walls"
        txt = (f"{info['faces']} face{'s' if info['faces'] != 1 else ''} next to {what} refined, down to {mm(info['smallest'])} mm "
               f"(narrowest {mm(info['narrowest'])} mm)")
    txt += f"; about {N(info['extra_cells'])} extra cells (rough upper estimate; cap {cap})."
    if info["capped"]:
        txt += " ⚠ The cap made the cells coarser than the gaps and curves call for."
    return txt


def _method_switch(adv, name: str, on: bool):
    try:
        adv.engine.project().set_mesh(sizing_methods={name: bool(on)})
        adv.engine.changed.emit()
    except Exception as e:  # noqa: BLE001
        _fail(adv, e)


def _method_options(adv, name: str, v: dict):
    try:
        adv.engine.project().set_mesh(sizing_methods={name: {k: ("" if x is None else x) for k, x in v.items()}})
        adv.engine.changed.emit()
    except Exception as e:  # noqa: BLE001
        _fail(adv, e)


def _show_wake(adv, on: bool):
    adv.show_wake = bool(on)
    adv._sig = None
    adv.panel.draw_marks()
    adv.engine.changed.emit()


def draw(adv):
    """The wake box in the 3D view when 'Show the wake box' is on (Expert mode, the Mesh step); called by the panel with the other marks."""
    from .. import mode

    v = adv.panel.view
    pv = adv.methods_pv
    if v is None or adv.xform.active:
        return
    if not (adv.show_wake and pv and pv.get("wake_region") and not mode.simple() and adv.panel.win is not None and adv.panel.win.step == "mesh"):
        return
    regs = (adv.engine.state()["mesh"].get("regions") or []) if adv.show_regions else []
    v.region_view.set(list(regs) + [pv["wake_region"]], adv.active_region)
