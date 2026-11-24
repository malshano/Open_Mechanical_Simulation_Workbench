"""The Mesh step of the window shows the wall layers: the card (what was made on each surface), the 'Wall layers' colouring in a cut through the
object, the y+ target, the layers that are out of date. The layer step itself is replaced by a stand-in here (it needs OpenFOAM: see
test_layers_mesh.py for the real one), so these tests run anywhere."""
import numpy as np
import pytest
from qt_support import ctl, from_thread  # noqa: F401

import simwb.project as P
from test_qt_theme import best_contrast, visible_controls  # noqa: F401


def wait(c, t=300):
    assert from_thread(c.app, lambda: c.wait_idle(t), t + 20)["idle"]
    c._settle()


def fake_layers(self, job, p0=0.0, p1=1.0, cells_before=None, sizes=None):
    """What the layer step makes, without OpenFOAM: the tetrahedra nearest to the object are layers 1, 2 and 3 (by their distance to the object)."""
    m = self.load_mesh()
    bb = np.asarray(self.data["geometry"]["info"]["parts_bbox"], float)
    c = m["nodes"][m["tets"]].mean(1)
    d = np.linalg.norm(np.maximum(np.maximum(bb[:3] - c, c - bb[3:]), 0.0), axis=1)
    order = np.argsort(d)
    layer = np.zeros(len(c), np.int8)
    n = max(len(c) // 30, 3)
    for i in range(3):
        layer[order[i * n:(i + 1) * n]] = i + 1
    arr = dict(m)
    arr["tet_layer"] = layer
    self.mesh_dir.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(self.layered_path, **arr)
    k = int((layer > 0).sum())
    return {"made": True, "patches": [{"name": "body", "faces": 10000, "target": 3, "made": 2.6, "first_mm": 1.0, "total_mm": 3.64, "percent": 95.5}],
            "extruded_percent": 99.975, "faces_extruded": 9998, "faces_total": 10000, "cells": len(c) + k, "cells_before": cells_before, "layer_cells": k,
            "layers_max": 3, "first_mm": 1.0, "total_mm": 3.64, "types": {"tetrahedra": len(c), "prisms": k}, "non_orthogonality_max": 50.0,
            "non_orthogonality_mean": 15.0, "skewness_max": 2.0, "aspect_ratio_max": 9.0, "mesh_ok": True, "failed_checks": 0, "warnings": [],
            "y_plus": (self.data["mesh"].get("layers") or {}).get("y_plus"), "settings": dict(self.data["mesh"].get("layers") or {}),
            "stale": False, "seconds": 1.0}


@pytest.fixture
def flow(ctl, box_step, monkeypatch):
    monkeypatch.setattr(P.Project, "_make_layers_inner", fake_layers)
    ctl.do("study.new", {"name": "Flow", "study": "external_aero"})
    ctl.do("geometry.import", {"path": str(box_step)})
    wait(ctl)
    ctl.engine.project().set_settings(speed=10)
    ctl.engine.project().set_mesh(preset="quick", layers={"n": 3, "growth": 1.2})
    ctl.do("step.go", {"step": "mesh"})
    ctl.do("app.mode", {"mode": "expert"})
    ctl.do("mesh.generate")
    wait(ctl)
    ctl._settle()
    return ctl


def lay(c):
    return c.engine.state()["mesh"]["stats"]["layers"]


def text(c, i):
    import re
    return re.sub("<[^>]+>", "", c.widget_do(i)["value"] or "")


def test_the_mesh_job_makes_the_layers_and_the_card_says_what_was_made(flow):
    c = flow
    assert lay(c)["made"] and lay(c)["layers_max"] == 3
    assert c.widget_do("mesh.layers.card")["visible"] is True
    s = text(c, "mesh.layers.state")
    assert "3 layers made" in s and "99.9 % of the wall faces" in s             # (99.975 reads 99.9, not 100)
    assert f"{lay(c)['cells']:,} cells" in s and f"{lay(c)['layer_cells']:,} layer cells" in s       # (the mesh the solver runs)
    t = text(c, "mesh.layers.table")
    assert "body" in t and "2.6 of 3" in t and "1 → 3.64 mm" in t and "95.5 %" in t
    from simwb.qt.meshtools import stats_rows
    labels = [a for a, _b in stats_rows(c.engine.state())]
    assert {"Cells with layers", "Layers made", "First / all layers", "Max non-orthogonality (with layers)"} <= set(labels)


def test_the_colour_choice_wall_layers_opens_a_cut_and_names_the_layers(flow):
    c = flow
    b = c.widget_do("mesh.color.layers")
    assert b["enabled"] is True and "Show the mesh the solver runs" in b["tooltip"]
    assert c.win.panels["mesh"].axis == ""                                       # (no cut yet)
    r = c.do("mesh.view", {"color": "layers"})["result"]
    assert r["color"] == "layers" and r["cut"] in ("x", "y", "z") and 0 < r["position"] < 1      # (a cut through the object is opened by itself)
    ov = c.win.view.overlay_info
    assert ov["name"] == "Wall layers" and ov["categories"][:3] == ["Tetrahedra", "Layer 1 (at the wall)", "Layer 2"]
    assert ov["triangles"] > 0 and len(ov["categories"]) == 4
    assert c.win.view._focus is not None                                          # (the camera is on the edge of the object, where the layers are)


def test_the_cut_the_user_chose_is_kept_when_the_layers_are_shown(flow):
    c = flow
    c.do("mesh.view", {"cut": "z", "position": 0.4})
    r = c.do("mesh.view", {"color": "layers"})["result"]
    assert r["cut"] == "z" and r["position"] == pytest.approx(0.4)


def test_the_preview_shell_is_not_drawn_beside_the_real_layers(flow):
    c = flow
    assert c.win.view.layer_view.actors                                           # (the shell of the layers is the preview before they exist)
    c.do("mesh.view", {"color": "layers"})
    assert not c.win.view.layer_view.actors
    c.do("mesh.view", {"color": "quality"})
    assert c.win.view.layer_view.actors


def test_zoom_to_the_layers_turns_the_view_to_the_cut(flow):
    c = flow
    c.do("mesh.view", {"color": "quality", "cut": "off"})
    assert c.widget_do("mesh.layers.zoom")["visible"] is True
    c.widget_do("mesh.layers.zoom", "click")                                      # (it switches the colour and opens the cut as well)
    p = c.win.panels["mesh"]
    assert p.color == "layers" and p.axis and c.win.view._focus is not None


def test_changed_layer_settings_say_so_and_make_again_brings_them_up_to_date(flow):
    c = flow
    c.widget_do("mesh.layers.n", "set", "2")
    c._settle()
    assert lay(c)["stale"] is True
    s = text(c, "mesh.layers.state")
    assert "layer settings changed" in s and "Make the layers again" in s
    assert c.widget_do("mesh.layers.make")["text"] == "Make the layers again" and c.widget_do("mesh.layers.make")["enabled"] is True
    c.do("mesh.layers")
    wait(c)
    assert lay(c)["stale"] is False and "layer settings changed" not in text(c, "mesh.layers.state")


def test_the_y_plus_field_is_stored_and_the_summary_names_the_target(flow):
    c = flow
    assert c.widget_do("mesh.layers.y_plus")["visible"] is True
    c.widget_do("mesh.layers.y_plus", "set", "5")
    assert c.engine.state()["mesh"]["layers"]["y_plus"] == 5.0
    assert "y+ ≈ 5" in text(c, "mesh.layers.summary")
    c.widget_do("mesh.layers.y_plus", "set", "0")
    assert c.engine.state()["mesh"]["layers"]["y_plus"] is None and "automatic, for y+ ≈ 30" in text(c, "mesh.layers.summary")
    with pytest.raises(Exception, match="y\\+ must be between"):
        c.engine.project().set_mesh(layers={"n": 3, "growth": 1.2, "y_plus": 0.2})


def test_without_layers_the_colour_is_greyed_and_says_why(ctl, box_step, monkeypatch):
    monkeypatch.setattr(P.Project, "_make_layers_inner", lambda self, *a, **k: {"made": False, "reason": "OpenFOAM is not installed here"})
    ctl.do("study.new", {"name": "Flow", "study": "external_aero"})
    ctl.do("geometry.import", {"path": str(box_step)})
    wait(ctl)
    ctl.engine.project().set_settings(speed=10)
    ctl.engine.project().set_mesh(preset="quick", layers={"n": 3, "growth": 1.2})
    ctl.do("step.go", {"step": "mesh"})
    ctl.do("mesh.generate")
    wait(ctl)
    ctl._settle()
    b = ctl.widget_do("mesh.color.layers")
    assert b["enabled"] is False and "No mesh with layers yet" in b["tooltip"]
    assert "Layers not made here" in text(ctl, "mesh.layers.state") and "OpenFOAM is not installed" in text(ctl, "mesh.layers.state")
    assert ctl.widget_do("mesh.layers.make")["text"] == "Make the layers again"
    ctl.win.panels["mesh"].color = "layers"                                        # (asked for, but there is nothing to show)
    ctl.win.panels["mesh"].layers_ok = False
    assert ctl.win.panels["mesh"].request()[2] == "quality"
    assert not ctl.engine.project().layered_path.exists()


def test_a_solid_study_has_no_layer_card_and_no_layer_colour(ctl):
    ctl.do("study.new", {"name": "Solid", "study": "static_structural"})
    ctl.do("step.go", {"step": "mesh"})
    p = ctl.win.panels["mesh"]
    assert not p.layer_card.isVisibleTo(p) and not p.layers_h.isVisibleTo(p) and not p.color_rows[2].isVisibleTo(p)


def test_the_verdict_uses_the_first_layer_for_the_y_plus(flow):
    c = flow
    from simwb.qt.meshtools import mesh_notes
    _tone, warn, info = mesh_notes(c.engine.state())
    layer_line = next(x for x in info if x.startswith("Wall layers:"))
    assert "3 layers" in layer_line and "estimated wall y+" in layer_line
    assert not any("Estimated wall y+" in x for x in info)                        # (the old estimate from the surface cells is not shown beside it)


def test_regenerating_the_mesh_forgets_the_old_layered_mesh(flow, monkeypatch):
    c = flow
    pr = c.engine.project()
    assert pr.layered_path.exists()
    monkeypatch.setattr(P.Project, "_make_layers_inner", lambda self, *a, **k: {"made": False, "reason": "none this time"})
    c.do("mesh.generate")
    wait(c)
    c._settle()
    assert not pr.layered_path.exists() and c.widget_do("mesh.color.layers")["enabled"] is False
    assert c.win.panels["mesh"].request()[2] != "layers"


@pytest.mark.parametrize("name", ("dark", "light"))
def test_the_layer_card_and_the_new_colour_are_legible_in_both_themes(flow, name):
    c = flow
    c.do("theme.set", {"mode": name})
    c.do("mesh.view", {"color": "layers"})
    from PySide6.QtWidgets import QScrollArea
    p = c.win.panels["mesh"].layer_card.parent()                                   # (the panel is in a scroll area: bring the card into view)
    while p is not None and not isinstance(p, QScrollArea):
        p = p.parent()
    p.ensureWidgetVisible(c.win.panels["mesh"].layer_card, 0, 200)
    c._settle()
    weak, seen = [], 0
    for i, o in visible_controls(c):
        if not i.startswith(("mesh.layers.", "mesh.color.layers")):
            continue
        seen += 1
        bg, fg, r = best_contrast(o)
        if fg is not None and fg != "off screen" and r < 3.0:
            weak.append((i, round(r, 2)))
    assert seen >= 3 and not weak, weak
