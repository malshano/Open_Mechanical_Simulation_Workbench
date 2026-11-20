"""Sharp-edge refinement (leading / trailing edges) and the tolerant-layer switch."""
import importlib
import re

import pytest

import simwb.project as P_
from simwb import gmsh_worker


def _gmsh():
    import gmsh

    gmsh.initialize(["-nopopup"])
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.model.add("t")
    return gmsh


def _wedge_plate(gmsh):
    """1000 x 500 mm plate, 4 mm thick, with 50 mm wedges at both ends (a knife edge of ~4.6 degrees)."""
    occ = gmsh.model.occ
    xz = [(0, 0), (50, 2), (950, 2), (1000, 0), (950, -2), (50, -2)]
    pts = [occ.addPoint(x, -250, z) for x, z in xz]
    lines = [occ.addLine(pts[i], pts[(i + 1) % 6]) for i in range(6)]
    occ.extrude([(2, occ.addPlaneSurface([occ.addCurveLoop(lines)]))], 0, 500, 0)
    occ.synchronize()


def test_a_box_has_twelve_ninety_degree_edges():
    gmsh = _gmsh()
    try:
        gmsh.model.occ.addBox(0, 0, 0, 100, 60, 30)
        gmsh.model.occ.synchronize()
        ids = [t for _, t in gmsh.model.getEntities(2)]
        e = gmsh_worker.sharp_edges(gmsh, ids, min_length=1.0)
        assert len(e) == 12 and all(abs(a - 90.0) < 1.0 for _, a, _ in e)
        assert [round(x[2]) for x in e][:4] == [100] * 4                       # longest first
        # only some faces are 'wall': an edge needs both of its faces in the list
        two = gmsh_worker.sharp_edges(gmsh, ids[:2], min_length=1.0)
        assert len(two) <= 1
    finally:
        gmsh.finalize()


def test_a_knife_edge_is_found_and_the_flat_transitions_are_not():
    gmsh = _gmsh()
    try:
        _wedge_plate(gmsh)
        ids = [t for _, t in gmsh.model.getEntities(2)]
        e = gmsh_worker.sharp_edges(gmsh, ids, min_length=100.0)
        knife = [x for x in e if x[1] < 15.0]
        assert len(knife) == 2 and all(abs(x[2] - 500.0) < 1.0 for x in knife)       # leading and trailing edge
        assert all(3.0 < x[1] < 7.0 for x in knife)                                   # ~4.6 degrees
        # the wedge-to-flat transitions (175 degrees) are smooth enough to be left alone; the long 90 degree edges stay
        assert not [x for x in e if 100.0 < x[1] < 140.0]
        assert all(x[1] < 140.0 for x in e)
    finally:
        gmsh.finalize()


def test_short_edges_and_seams_are_left_out():
    gmsh = _gmsh()
    try:
        gmsh.model.occ.addCylinder(0, 0, 0, 0, 0, 50, 20)                              # two circles of 90 degrees and a seam
        gmsh.model.occ.synchronize()
        ids = [t for _, t in gmsh.model.getEntities(2)]
        e = gmsh_worker.sharp_edges(gmsh, ids, min_length=10.0)
        assert len(e) == 2 and all(abs(a - 90.0) < 2.0 for _, a, _ in e)               # the seam has one face on both sides
        assert gmsh_worker.sharp_edges(gmsh, ids, min_length=1000.0) == []
    finally:
        gmsh.finalize()


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMWB_HOME", str(tmp_path / "ws"))
    importlib.reload(P_)
    return P_


def _plate_project(P, tmp_path):
    gmsh = _gmsh()
    try:
        _wedge_plate(gmsh)
        step = tmp_path / "plate.step"
        gmsh.write(str(step))
    finally:
        gmsh.finalize()
    pr = P.create_project("Plate", "external_aero")
    j = pr.import_step(step, original_name="plate.step")
    assert j.wait(300) and j.status == "done", j.error
    pr.set_settings(speed=3.0, turbulence="laminar")
    return pr


def test_edge_refinement_setting_and_its_effect_on_the_mesh(workspace, tmp_path):
    pr = _plate_project(workspace, tmp_path)
    pr.set_mesh(preset="quick")
    j = pr.generate_mesh()
    assert j.wait(600) and j.status == "done", (j.error, j.log[-8:])
    base = pr.data["mesh"]["stats"]
    assert base["edge_refine"] is None
    with pytest.raises(ValueError, match="factor"):
        pr.set_mesh(edge_refinement=1.0)
    pr.set_mesh(edge_refinement=True)
    assert pr.data["mesh"]["edge_refine"] == {"factor": 6.0} and pr.data["mesh"]["status"] == "stale"
    j = pr.generate_mesh()
    assert j.wait(600) and j.status == "done", (j.error, j.log[-8:])
    st = pr.data["mesh"]["stats"]
    er = st["edge_refine"]
    assert er and er["edges"] >= 2 and er["min_face_angle"] < 10.0 and er["size"] < st["sizes"]["near_wall"] * 0.9
    assert st["cells"] > base["cells"] * 1.05                                          # more cells along the edges
    assert st["cells"] < base["cells"] * 3.0                                           # (measured: about 2x on this plate)
    pr.set_mesh(edge_refinement=False)
    assert "edge_refine" not in pr.data["mesh"]


def test_tolerant_layer_limits_reach_the_case_only_when_asked(workspace, tmp_path):
    pr = _plate_project(workspace, tmp_path)
    pr.set_mesh(preset="quick", layers={"n": 4, "growth": 1.2})
    j = pr.generate_mesh()
    assert j.wait(600) and j.status == "done", j.error
    out = {}
    for tolerant in (False, True):
        pr.set_mesh(layers={"n": 4, "growth": 1.2, "tolerant": tolerant})
        j = pr.solve(export_only=True)
        assert j.wait(300) and j.status == "done", (j.error, j.log[-6:])
        out[tolerant] = (pr.case_dir / "system" / "snappyHexMeshDict").read_text()
    assert re.search(r"maxFaceThicknessRatio 0\.5;", out[False]) and "nRelaxedIter" not in out[False]
    assert re.search(r"maxFaceThicknessRatio 0\.8;", out[True]) and re.search(r"nRelaxedIter\s+3;", out[True])
    assert "minDeterminant 1e-5" in out[True] and "minDeterminant 0.001" in out[False]
