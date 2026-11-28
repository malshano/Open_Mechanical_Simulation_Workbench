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
    return [(m.get("layers") or {}).get("clamp"), (m.get("layers") or {}).get("method"), m.get("sizing_methods"), m.get("smart"), m.get("preset"), m.get("growth"),
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
