"""The size-field preview (#42): the planned cell sizes before any mesh exists, against what the mesher really makes."""
import importlib

import numpy as np
import pytest
from qt_support import EXAMPLES  # noqa: F401


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMWB_HOME", str(tmp_path / "ws"))
    import simwb.project as P
    importlib.reload(P)
    yield P


def wing(P, smart: bool):
    pr = P.create_project("W", "external_aero")
    j = pr.import_step(EXAMPLES / "airplane_wing.step", original_name="w.step")
    assert j.wait(300) and j.status == "done", j.error
    pr.set_settings(speed=30)
    pr.set_mesh(preset="quick", smart=smart)
    return pr


def test_the_pieces_of_the_field_are_the_workers():
    from simwb import gmsh_worker as gw, sizefield as sf
    p = np.array([[0.0, 0, 0], [0.05, 0, 0], [0.2, 0, 0], [5.0, 0, 0]])
    # the wall field: the wall size within one cell, the growth ratio outside, the far-field size at the end of the growth
    s = sf._grown(np.array([0.0, 0.01, 0.3, 9.0]), 0.01, 1.0, 1.25)
    assert s[0] == pytest.approx(0.01) and s[1] == pytest.approx(0.01) and 0.01 < s[2] < 1.0 and s[3] == pytest.approx(1.0)
    # a box: the inside size, then a linear change over the thickness, as gmsh's Box field
    d = sf._box_dist(p, [-1, -1, -1], [1, 1, 1])
    assert d == pytest.approx([0, 0, 0, 4.0])
    # the same expression the worker gives to gmsh for a cylinder, evaluated by hand
    expr = gw.cylinder_size_expr([0, 0, 0], [1, 0, 0], 0.1, 0.01, 0.5, 0.2)
    x, y, z = 0.5, 0.3, 0.0
    ev = eval(expr.replace("max", "np.maximum").replace("min", "np.minimum").replace("sqrt", "np.sqrt").replace("^", "**"), {"np": np, "x": x, "y": y, "z": z})
    mine = 0.01 + (0.5 - 0.01) * np.minimum(1, sf._cyl_dist(np.array([[x, y, z]]), [0, 0, 0], [1, 0, 0], 0.1) / 0.2)
    assert float(ev) == pytest.approx(float(mine[0]))
    # a turned box agrees with the worker's expression too
    R = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]], float)
    e2 = gw.box_size_expr([-1, -1, -1], [1, 1, 1], R, 0.01, 0.5, 0.2)
    q = (0.3, 2.5, 0.0)
    ev2 = eval(e2.replace("max", "np.maximum").replace("min", "np.minimum").replace("sqrt", "np.sqrt").replace("^", "**"), {"np": np, "x": q[0], "y": q[1], "z": q[2]})
    mine2 = 0.01 + 0.49 * np.minimum(1, sf._box_dist(np.array([q]), [-1, -1, -1], [1, 1, 1], R) / 0.2)
    assert float(ev2) == pytest.approx(float(mine2[0]))


@pytest.mark.parametrize("smart", (True, False))
def test_the_preview_agrees_with_the_mesh_that_is_made(workspace, smart):
    from simwb import meshviz, sizefield
    pr = wing(workspace, smart)
    spec = sizefield.decisions(pr)                                                    # (before any mesh exists)
    j = pr.generate_mesh()
    assert j.wait(900) and j.status == "done", (j.error, j.log[-5:])
    st = pr.data["mesh"]["stats"]["sizes"]
    assert spec["h_wall"] == pytest.approx(st["near_wall"]) and spec["h_body"] == pytest.approx(st["body"]) and spec["size_far"] == pytest.approx(st["far"])
    m = pr.load_mesh()
    cen = m["nodes"][m["tets"]].mean(1)
    real = meshviz.cell_metrics(m["nodes"], m["tets"], ("size",))["size"] / 1000.0
    pred = sizefield.evaluate(spec, cen)
    assert np.corrcoef(np.log(real), np.log(pred))[0, 1] > 0.85                      # where the cells are small and large: the same places
    assert 0.7 < float(np.median(real / pred)) < 1.8                                  # and about the sizes (the cells come out a little larger than the target)
    far = np.linalg.norm(cen - cen.mean(0), axis=1) > 3.0
    assert np.median(real[far]) == pytest.approx(np.median(pred[far]), rel=0.3)


def test_the_preview_draws_the_walls_and_a_cut(workspace):
    from simwb import sizefield
    pr = wing(workspace, True)
    out = pr.size_preview()
    assert out["surface"]["n_tris"] > 100 and out["section"] is None and out["range"][0] <= out["h_wall"] * 1000 + 1e-6
    assert "Near field" in out["fields"] and "Wake" in out["fields"] and out["notes"]
    cut = pr.size_preview("y", 0.5)
    assert cut["section"]["n_tris"] > 1000 and cut["section"]["max"] <= cut["size_far"] * 1000 + 1e-6
    d = sizefield.combined(cut)
    assert d["n_tris"] == cut["surface"]["n_tris"] + cut["section"]["n_tris"] and d["range"][1] > d["range"][0] and d["scale"] == "log"
    with pytest.raises(ValueError, match="axis"):
        pr.size_preview("w")


def test_a_setting_changes_the_preview_before_any_mesh(workspace):
    from simwb import sizefield
    pr = wing(workspace, False)
    a = sizefield.decisions(pr)
    pr.set_mesh(preset="fine")
    b = sizefield.decisions(pr)
    assert b["h_wall"] < a["h_wall"]                                                  # a finer preset: smaller cells, without meshing
    pr.set_mesh(regions=[{"name": "Wake", "shape": "box", "min": [0.3, 0.0, -0.1], "max": [1.0, 0.8, 0.1], "size": 0.004}])
    c = sizefield.decisions(pr)
    assert "Wake" in [n for n, _ in c["terms"]]
    inside = np.array([[0.5, 0.4, 0.0]])
    assert sizefield.evaluate(c, inside)[0] == pytest.approx(0.004, rel=0.05) and sizefield.evaluate(b, inside)[0] > 0.004 * 1.5


def test_the_preview_of_a_solid_study(workspace):
    from simwb import sizefield
    pr = workspace.create_project("S", "static_structural")
    j = pr.import_step(EXAMPLES / "camera_mount.step", original_name="c.step")
    assert j.wait(300) and j.status == "done", j.error
    pr.set_mesh(preset="coarse")
    d = pr.size_preview("x", 0.5)
    assert d["surface"]["n_tris"] > 0 and d["h_wall"] <= d["size_far"] and d["fields"][0] == "Walls"
