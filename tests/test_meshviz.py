"""Mesh inspection metrics: cell size, aspect ratio, size jumps, the cell budget, the viewer and the tools."""
import importlib
import math

import numpy as np
import pytest

import simwb.project as P_
from simwb import meshviz


def _regular_tet(edge=1.0):
    a = edge
    p = np.array([[0, 0, 0], [a, 0, 0], [a / 2, a * math.sqrt(3) / 2, 0],
                  [a / 2, a * math.sqrt(3) / 6, a * math.sqrt(6) / 3]], float)
    return p, np.array([[0, 1, 2, 3]])


def test_a_regular_tetrahedron_has_the_size_of_its_edge_and_no_size_jump():
    p, t = _regular_tet(0.002)                                          # 2 mm edge, metres
    m = meshviz.cell_metrics(p, t)
    assert m["size"][0] == pytest.approx(2.0, rel=1e-4)                  # mm
    assert m["aspect"][0] == pytest.approx(math.sqrt(1.5), rel=1e-4)    # 1.2247: longest edge / smallest altitude
    assert m["growth"][0] == 1.0 and m["volume"][0] == pytest.approx(2.0 ** 3 / (6 * math.sqrt(2)), rel=1e-4)


def test_a_flat_sliver_has_a_large_aspect_ratio():
    p = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0.3, 0.3, 0.01]], float)
    assert meshviz.cell_metrics(p, np.array([[0, 1, 2, 3]]))["aspect"][0] > 50


def test_neighbours_and_size_jump_on_two_tetrahedra_that_share_a_face():
    p = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 1, 1]], float)
    t = np.array([[0, 1, 2, 3], [1, 2, 3, 4]])
    i, j = meshviz.neighbour_pairs(t)
    assert list(zip(i, j)) == [(0, 1)]
    g = meshviz.cell_metrics(p, t, ("growth",))["growth"]
    assert g[0] == g[1] and g[0] == pytest.approx((meshviz.cell_metrics(p, t, ("size",))["size"][1]
                                                   / meshviz.cell_metrics(p, t, ("size",))["size"][0]), rel=1e-4)


def _box_mesh(size_a, size_b):
    """A box meshed finely at one end and coarsely at the other (a mesh with a real size gradient)."""
    import gmsh

    gmsh.initialize(["-nopopup"])
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("b")
        gmsh.model.occ.addBox(0, 0, 0, 0.1, 0.04, 0.04)
        gmsh.model.occ.synchronize()
        f = gmsh.model.mesh.field.add("MathEval")
        gmsh.model.mesh.field.setString(f, "F", f"{size_a} + ({size_b} - {size_a}) * x / 0.1")
        gmsh.model.mesh.field.setAsBackgroundMesh(f)
        for k in (0, 1, 2):
            gmsh.option.setNumber(f"Mesh.MeshSizeExtendFromBoundary", 0)
        gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 0)
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 0)
        gmsh.model.mesh.generate(3)
        tags, xyz, _ = gmsh.model.mesh.getNodes()
        order = np.argsort(tags)
        xyz = xyz.reshape(-1, 3)[order]
        remap = np.zeros(int(tags.max()) + 1, int)
        remap[tags[order]] = np.arange(len(tags))
        _, _, nt = gmsh.model.mesh.getElements(3)
        return xyz, remap[nt[0].reshape(-1, 4).astype(int)]
    finally:
        gmsh.finalize()


def test_the_summary_of_a_graded_box_mesh_is_consistent():
    p, t = _box_mesh(0.004, 0.012)
    s = meshviz.summary(p, t)
    assert s["cells"] == len(t)
    vol = meshviz.cell_metrics(p, t, ("volume",))["volume"].astype(float).sum()
    assert vol == pytest.approx(0.1 * 0.04 * 0.04 * 1e9, rel=1e-3)      # the cells fill the box (mm^3)
    assert sum(b["cells"] for b in s["budget"]) == len(t)
    assert sum(b["volume_percent"] for b in s["budget"]) == pytest.approx(100.0, abs=0.2)
    assert sum(b["cells_percent"] for b in s["budget"]) == pytest.approx(100.0, abs=0.5 * len(s["budget"]))
    sz = s["metrics"]["size"]
    assert sz["max"] > 1.8 * sz["min"] and sz["unit"] == "mm"            # the size really varies along the box
    assert s["metrics"]["growth"]["min"] >= 1.0 and s["metrics"]["aspect"]["min"] >= 1.2
    assert "quality" not in s["metrics"]                                 # not given
    # fine cells are many but fill little of the volume: the budget shows it
    first, last = s["budget"][0], s["budget"][-1]
    assert first["cells_percent"] / max(first["volume_percent"], 1e-9) > last["cells_percent"] / max(last["volume_percent"], 1e-9)


def test_findings_name_abrupt_size_changes():
    n = 300
    f = meshviz.findings({"growth": np.r_[np.full(n, 3.0), np.ones(n)].astype(np.float32),
                          "aspect": np.full(2 * n, 2.0, np.float32), "size": np.full(2 * n, 1.0, np.float32)}, 2 * n)
    assert any("twice" in x for x in f)
    assert meshviz.findings({"growth": np.ones(n, np.float32), "aspect": np.full(n, 2.0, np.float32),
                             "size": np.ones(n, np.float32)}, n) == []


def test_display_ranges_ignore_a_few_extreme_cells():
    a = np.r_[np.full(1000, 2.0), [500.0]].astype(np.float32)
    lo, hi = meshviz.display_range("size", a)
    assert hi < 10 and meshviz.display_range("quality", a) == [0.0, 1.0] and meshviz.display_range("growth", a)[0] == 1.0


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMWB_HOME", str(tmp_path / "ws"))
    importlib.reload(P_)
    return P_


def test_the_project_views_and_metrics_reach_the_viewer_and_the_tools(workspace, tmp_path):
    import gmsh

    from simwb import tools

    gmsh.initialize(["-nopopup"])
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("b")
        gmsh.model.occ.addBox(0, 0, 0, 200, 80, 40)
        gmsh.model.occ.synchronize()
        gmsh.write(str(tmp_path / "box.step"))
    finally:
        gmsh.finalize()
    pr = workspace.create_project("Viz", "external_aero")
    j = pr.import_step(tmp_path / "box.step", original_name="box.step")
    assert j.wait(300) and j.status == "done", j.error
    pr.set_settings(speed=10)
    pr.set_mesh(preset="quick")
    j = pr.generate_mesh()
    assert j.wait(600) and j.status == "done", (j.error, j.log[-6:])

    q = pr.mesh_view()
    assert q["n_tris"] > 0 and q["range"] == [0.0, 1.0] and q["metric"] == "quality"
    for metric, worse in (("size", None), ("aspect", True), ("growth", True), ("volume", None)):
        d = pr.mesh_view(metric=metric)
        assert d["n_tris"] == q["n_tris"] and d["metric"] == metric and d["higher_is_worse"] == worse
        assert d["range"][1] > d["range"][0] and d["max"] >= d["min"] > 0
    sizes = pr.mesh_view(metric="size", clip_axis="x", clip_pos=0.0)     # also on a section through the air
    assert sizes["n_tris"] > 0
    with pytest.raises(ValueError, match="Unknown mesh metric"):
        pr.mesh_view(metric="smoothness")
    with pytest.raises(ValueError, match="quality"):
        pr.mesh_view(metric="size", poor=0.1)

    d = pr.mesh_metrics()
    st = pr.data["mesh"]["stats"]
    assert d["cells"] == st["cells"] and {"size", "aspect", "growth", "volume", "quality"} <= set(d["metrics"])
    assert d["metrics"]["quality"]["min"] == pytest.approx(st["quality"]["min"], abs=1e-6)
    tool = dict((n, f) for n, _, _, f in tools.TOOLS)["mesh_metrics"]
    assert tool(pr, {})["cells"] == d["cells"]
    # the REST endpoints of the viewer
    import simwb.server as S
    from fastapi.testclient import TestClient

    importlib.reload(S)
    c = TestClient(S.app, base_url="http://127.0.0.1:8765")
    h = {"X-SimWB": "1"}
    r = c.get(f"/api/projects/{pr.id}/mesh/view?metric=size", headers=h)
    assert r.status_code == 200 and r.json()["metric"] == "size" and r.json()["unit"] == "mm", r.text[:200]
    assert c.get(f"/api/projects/{pr.id}/mesh/view?metric=nonsense", headers=h).status_code in (400, 422)
    r = c.get(f"/api/projects/{pr.id}/mesh/metrics", headers=h)
    assert r.status_code == 200 and r.json()["cells"] == d["cells"] and r.json()["budget"]
    r = c.post(f"/api/projects/{pr.id}/mesh/settings", json={"edge_refinement": 4}, headers=h)
    assert r.status_code == 200 and r.json()["mesh"]["edge_refine"] == {"factor": 4.0}
    # the new mesh settings are reachable through the tool
    tools_set = dict((n, f) for n, _, _, f in tools.TOOLS)["set_mesh_settings"]
    tools_set(pr, {"edge_refinement": True, "layers": {"n": 3, "growth": 1.2, "tolerant": True}})
    assert pr.data["mesh"]["edge_refine"] == {"factor": 6.0} and pr.data["mesh"]["layers"]["tolerant"] is True
