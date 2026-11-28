"""The mesh views of the Mesh step in the Qt window (#47, #49): the colour-by selector with the metrics, the legend, the histogram of the metric,
the cell budget (a click on a band shows those cells), the findings; and the same for a hex-dominant mesh (#41), made without OpenFOAM."""
import numpy as np
import pytest
from qt_support import ctl, from_thread  # noqa: F401
from test_qt_mesh import mesh, open_mesh  # noqa: F401
from test_qt_results import solved  # noqa: F401


def test_every_metric_can_colour_the_mesh_and_has_its_legend(ctl, solved):
    c = open_mesh(ctl, solved)
    ids = {w["id"] for w in c.widgets("mesh.color.")["widgets"]}
    assert {f"mesh.color.{k}" for k in ("quality", "size", "aspect", "growth", "volume", "region")} <= ids
    for k, name in (("size", "Cell size"), ("aspect", "Aspect ratio"), ("growth", "Size jump to neighbours"), ("volume", "Cell volume")):
        c.do("mesh.view", {"color": k})
        ov = c.state()["view"]["overlay"]
        assert ov["name"] == name and c.widget_do(f"mesh.color.{k}")["value"]["checked"]
        assert c.state()["view"]["legend"]["kind"] == "scalar"                  # the legend of the colours is there
    c.do("mesh.view", {"color": "region"})
    assert c.state()["view"]["legend"]["kind"] == "cats"


def test_the_histogram_follows_the_colouring_and_its_bars_have_the_legend_colours(ctl, solved):
    c = open_mesh(ctl, solved)
    pn = c.win.panels["mesh"]
    assert not pn.mv.isVisibleTo(pn) and pn.hist.isVisibleTo(pn)                # quality: the quality histogram
    c.do("mesh.view", {"color": "size"})
    assert pn.mv.isVisibleTo(pn) and not pn.hist.isVisibleTo(pn)
    h = pn.mv.hist
    assert sum(h.d["histogram"]) == mesh(c)["stats"]["cells"] and h.key == "size"
    assert "Cell size" in c.widget_do("mesh.hist")["text"]
    from simwb.qt.metrichist import bin_color
    lo, hi = h.d["range"]
    assert bin_color("size", lo, h.d["range"]) != bin_color("size", hi, h.d["range"])
    c.do("mesh.view", {"color": "growth"})
    assert h.key == "growth" and "Dashed line" in pn.mv.marks.text()
    c.do("mesh.view", {"color": "quality"})
    assert not pn.mv.isVisibleTo(pn)


def test_a_bar_of_the_histogram_shows_only_its_cells(ctl, solved):
    c = open_mesh(ctl, solved)
    pn = c.win.panels["mesh"]
    c.do("mesh.view", {"color": "size"})
    full = c.state()["view"]["overlay"]
    h = pn.mv.hist.d["histogram"]
    i = int(np.argmax(h))
    r = c.do("mesh.highlight", {"bar": i})["result"]
    assert r["band"][0] == "size" and r["cells"] == h[i]                       # the view drew exactly the cells of the bar
    assert "cells with cell size from" in r["text"] and c.widget_do("mesh.band.clear")["visible"]
    assert c.state()["view"]["overlay"]["triangles"] != full["triangles"]
    c.do("mesh.highlight", {"bar": i})                                         # the same bar again: all cells
    assert pn.mv.band is None and c.state()["view"]["overlay"]["triangles"] == full["triangles"]
    c.do("mesh.highlight", {"bar": i})
    c.widget_do("mesh.band.clear", "click")
    assert pn.mv.band is None
    c.do("mesh.highlight", {"bar": i})
    c.do("mesh.view", {"color": "aspect"})                                     # another metric: the choice does not carry over
    assert pn.mv.band is None
    with pytest.raises(ValueError, match="Give off"):
        c.do("mesh.highlight", {})


def test_a_band_of_the_cell_budget_is_clickable(ctl, solved):
    c = open_mesh(ctl, solved)
    pn = c.win.panels["mesh"]
    bud = mesh(c) and pn.budget.data["budget"]
    k = len(bud) // 2
    r = c.do("mesh.highlight", {"band": k})["result"]
    assert r["band"][0] == "size" and pn.color == "size"                       # the view is coloured by size, as the band is
    assert r["cells"] == bud[k]["cells"]
    assert pn.budget.rows[k][0].property("chosen") is True
    c.do("mesh.highlight", {"off": True})
    assert pn.mv.band is None and pn.budget.rows[k][0].property("chosen") is not True
    with pytest.raises(ValueError, match="bands"):
        c.do("mesh.highlight", {"band": 999})


def test_the_budget_and_findings_are_in_the_panel(ctl, solved):
    c = open_mesh(ctl, solved)
    pn = c.win.panels["mesh"]
    assert pn.budget.isVisibleTo(pn) and "% cells" in c.widget_do("mesh.budget")["text"]
    assert pn.budget.data["findings"] is not None


def test_poor_cells_and_a_band_are_not_shown_together(ctl, solved):
    c = open_mesh(ctl, solved)
    pn = c.win.panels["mesh"]
    c.do("mesh.view", {"color": "size"})
    c.do("mesh.highlight", {"bar": int(np.argmax(pn.mv.hist.d["histogram"]))})
    c.do("mesh.poor", {"on": True, "threshold": 0.5})
    assert pn.mv.band is None
    c.do("mesh.poor", {"on": False})


def test_the_histogram_follows_a_new_mesh(ctl, solved):
    c = open_mesh(ctl, solved)
    pn = c.win.panels["mesh"]
    c.do("mesh.view", {"color": "size"})
    n0 = sum(pn.mv.hist.d["histogram"])
    c.do("mesh.highlight", {"bar": 3})
    assert pn.mv.band
    c.do("mesh.preset", {"preset": "quick"})
    c.do("mesh.generate")
    assert from_thread(c.app, lambda: c.wait_idle(300), 320)["idle"]
    assert pn.mv.band is None and pn.mv.which == "size"                       # a new mesh: the old choice of cells is gone, the metric stays
    n1 = sum(pn.mv.hist.d["histogram"])
    assert n1 == mesh(c)["stats"]["cells"] and n1 != n0


def fake_hex_study(ctl, tmp_path, monkeypatch):
    """A flow study whose stored mesh is a small hex-dominant one (made here, no OpenFOAM): the panel as it is with a real snappyHexMesh mesh."""
    import simwb.solvers.openfoam.foamio as foamio
    from simwb.solvers.openfoam.results_loader import display_mesh, display_points
    from test_hexmesh import _step
    from test_meshviews import grid_polymesh

    pm = grid_polymesh([0, 1e-3, 2e-3, 4e-3, 8e-3, 12e-3], [0, 2e-3, 4e-3, 6e-3], [0, 2e-3, 4e-3])
    dm = display_mesh(pm)
    ctl.do("study.new", {"name": "Hex", "study": "external_aero"})
    ctl.do("geometry.import", {"path": str(_step(tmp_path))})
    assert from_thread(ctl.app, lambda: ctl.wait_idle(300), 320)["idle"]
    pr = ctl.engine.project()
    fluid = next(b["id"] for b in pr.bodies() if b["role"] == "fluid")
    q = np.linspace(0.3, 1.0, len(dm["tets"])).astype(np.float32)
    pr.mesh_dir.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(pr.mesh_dir / "mesh.npz", nodes=display_points(pm["points"], dm), tets=dm["tets"].astype(np.int32),
                        tet_vol=np.full(len(dm["tets"]), fluid), tris=np.zeros((0, 3), np.int32), tri_surf=np.zeros(0, np.int64),
                        tri_owner=np.zeros(0, np.int64), quality=q, zone_tris=np.zeros((0, 3), np.int32), zone_tri_zone=np.zeros(0, np.int32),
                        hex=np.array([1]))
    monkeypatch.setattr(foamio, "read_polymesh", lambda _d: pm)
    h, e = np.histogram(q, bins=20, range=(0, 1))
    n = pm["n_cells"]
    pr.data["mesh"].update(method="hex", status="ready", stats={
        "method": "hex", "cells": n, "nodes": len(pm["points"]), "cell_types": {"hexahedra": n}, "seconds": 1.0,
        "quality": {"min": 0.3, "mean": 0.65, "p01": 0.3, "n_poor": 0, "histogram": h.tolist(), "edges": e.tolist()},
        "foam_quality": {"non_orthogonality_max": 5.0, "non_orthogonality_mean": 1.0, "skewness_max": 0.5},
        "hex": {"levels": [1, 2], "base_mm": 4.0, "background_cells": 10, "surface_cell_mm": 1.0, "aspect_ratio_max": 3.0, "mesh_ok": True,
                "failed_checks": 0, "problems": [], "concave_cells": 0, "layers": {}, "layers_extruded_percent": None, "warnings": [], "wall_patches": []},
        "sizes": {"near_wall": 0.001, "body": 0.001, "far": 0.004, "feature": 0.001, "min_gap": None, "channels": [], "gap_cell": None, "cells_across_gap": None}})
    pr.save()
    ctl.engine.changed.emit()
    return pm


def test_a_hex_dominant_mesh_has_the_same_views(ctl, tmp_path, monkeypatch):
    pm = fake_hex_study(ctl, tmp_path, monkeypatch)
    ctl.do("step.go", {"step": "mesh"})
    pn = ctl.win.panels["mesh"]
    assert pn.hex and pn.metrics_ok
    for k in ("size", "aspect", "growth", "volume"):
        assert ctl.widget_do(f"mesh.color.{k}")["enabled"], k                    # (they were greyed out for a hex-dominant mesh)
    assert not pn.color_note.isVisibleTo(pn)
    ctl.do("mesh.view", {"color": "size", "cut": "x", "position": 0.5})            # (the fake mesh has no boundary triangles: a cut shows its cells)
    assert ctl.state()["view"]["overlay"]["name"] == "Cell size" and ctl.state()["view"]["legend"]["kind"] == "scalar"
    assert sum(pn.mv.hist.d["histogram"]) == pm["n_cells"]                      # the histogram counts cells, not the tetrahedra they are drawn with
    assert pn.budget.isVisibleTo(pn) and pn.budget.rows
    r = ctl.do("mesh.highlight", {"band": 0})["result"]
    assert r["cells"] == pn.budget.data["budget"][0]["cells"] and ctl.state()["view"]["overlay"]["triangles"] > 0
    ctl.do("mesh.highlight", {"off": True})
    ctl.do("mesh.view", {"color": "growth", "cut": "x", "position": 0.5})
    assert ctl.state()["view"]["overlay"]["name"] == "Size jump to neighbours" and ctl.state()["view"]["overlay"]["triangles"] > 0
