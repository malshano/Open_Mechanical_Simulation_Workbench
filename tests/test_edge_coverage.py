"""#40: the edge refinement outside a body in a flow, with a smallest cell set by hand, and without a known flow axis."""
import importlib

import pytest

import simwb.project as P_
from simwb import edgepreview, gmsh_worker
from test_edge_refine import _gmsh, _wedge_plate

EXAMPLES = __import__("pathlib").Path(__file__).resolve().parents[1] / "examples"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMWB_HOME", str(tmp_path / "ws"))
    importlib.reload(P_)
    return P_


# ---------------------------------------------------------------------------------------------------------------------------------- pure
def test_the_share_of_the_tube_that_the_mesh_fills_depends_on_the_study():
    sh = gmsh_worker.edge_share
    assert sh(90.0, "air") == pytest.approx(0.75) and sh(90.0, "solid") == pytest.approx(0.25) and sh(90.0, "both") == 1.0
    assert sh(5.0, "air") > 0.98 and sh(5.0, "solid") < 0.02                       # a knife edge: air all round it, hardly any material
    assert [gmsh_worker.edge_study_share(s) for s in ("external_aero", "cht_enclosure", "heat_conduction", "vibration_modal")] == ["air", "both", "solid", "solid"]
    assert gmsh_worker.edge_spread("external_aero") == gmsh_worker.EDGE_ESTIMATE_SPREAD < gmsh_worker.edge_spread("heat_conduction")


def test_the_estimate_follows_the_side_of_the_edge_that_is_meshed():
    line = [[[0.0, 0.0, 0.0], [0.5, 0.0, 0.0], [1.0, 0.0, 0.0]]]
    air = gmsh_worker.edge_cells_union(line, [90.0], 0.002, 0.01, 1.25, share="air")
    solid = gmsh_worker.edge_cells_union(line, [90.0], 0.002, 0.01, 1.25, share="solid")
    both = gmsh_worker.edge_cells_union(line, [90.0], 0.002, 0.01, 1.25, share="both")
    assert both == pytest.approx(air / 0.75, rel=0.02) and both == pytest.approx(solid / 0.25, rel=0.02)
    plan = gmsh_worker.edge_refine_plan([(1, 90.0, 1.0, line[0])], 0.01, 1.0, 1.25, 5.0, 10 ** 9, 140.0, share="solid")
    assert plan["applied"] and plan["estimated_extra_cells"] == pytest.approx(solid, rel=0.02)


def test_the_flow_axis_comes_from_the_domain_then_from_the_hint_and_may_be_missing():
    f = gmsh_worker.edge_flow_axis
    assert f({"domain": {"spec": {"flow_axis": 1}}}, "external_aero", 0) == 1             # the domain wins
    assert f({"domain": {"spec": {}}}, "external_aero", 2) == 2                           # the settings' direction when the domain does not say
    assert f({}, "external_aero") is None and f({"domain": None}, "external_aero", None) is None
    assert f({"domain": {"spec": {"flow_axis": 1}}}, "heat_conduction", 1) is None        # no flow, no direction


def test_without_a_flow_axis_the_edges_along_the_flow_are_kept():
    gmsh = _gmsh()
    try:
        gmsh.model.occ.addBox(0, 0, 0, 100, 60, 30)
        gmsh.model.occ.synchronize()
        ids = [t for _, t in gmsh.model.getEntities(2)]
        assert len(gmsh_worker.sharp_edges(gmsh, ids, 1.0, flow_axis=gmsh_worker.edge_flow_axis({}, "external_aero"))) == 12
        assert len(gmsh_worker.sharp_edges(gmsh, ids, 1.0, flow_axis=gmsh_worker.edge_flow_axis({"domain": {"spec": {"flow_axis": 0}}}, "external_aero"))) == 8
    finally:
        gmsh.finalize()


def test_the_walls_whose_edges_are_refined():
    faces = [{"id": 1, "kind": "body"}, {"id": 2, "kind": "farfield"}, {"id": 3, "kind": "enclosure"}, {"id": 4, "kind": "solid"}]
    assert gmsh_worker.edge_wall_faces(faces, "external_aero") == [1, 4] and gmsh_worker.edge_wall_faces(faces, "cht_enclosure") == [1, 4]
    assert gmsh_worker.edge_wall_faces(faces, "heat_conduction") == [1, 2, 3, 4]


# ---------------------------------------------------------------------------------------------------------------------------------- real meshes
def _example_project(P, name, study, tmp_path):
    pr = P.create_project("E", study)
    j = pr.import_step(EXAMPLES / name, original_name=name)
    assert j.wait(300) and j.status == "done", j.error
    return pr


def _mesh(pr):
    j = pr.generate_mesh()
    assert j.wait(600) and j.status == "done", (j.error, j.log[-6:])
    return pr.data["mesh"]["stats"]


def test_a_solid_study_gets_finer_cells_along_its_edges_and_the_estimate_is_near(workspace, tmp_path):
    pr = _example_project(workspace, "flutter_plate.step", "vibration_modal", tmp_path)
    assert edgepreview.unavailable(pr) is None
    pr.set_mesh(preset="quick")
    base = _mesh(pr)
    assert base["edge_refine"] is None
    pr.set_mesh(edge_refinement=6)
    pv = edgepreview.preview(pr, scan=True, wait=True)
    assert pv["plan"]["applied"] and pv["share"] == "solid" and pv["flow_axis"] is None and pv["study"] == "vibration_modal"
    st = _mesh(pr)
    er = st["edge_refine"]
    assert er["edges"] >= 4 and er["share"] == "solid" and er["flow_filter"] is False
    assert er["size"] == pytest.approx(er["size_far_of_edge_field"] / 6, rel=0.01)       # 6 times finer than the wall cells
    extra = st["cells"] - base["cells"]
    assert extra > 5000 and 0.4 < extra / er["estimated_extra_cells"] < 1.8             # (measured on 12 meshes: 0.52 to 1.47)
    from simwb import meshplan
    est = meshplan.evaluate(pr, *meshplan.base_inputs(pr))
    assert est["edge_cells"] == pytest.approx(er["estimated_extra_cells"], rel=0.05)    # the memory planner takes the same plan


def test_a_smallest_cell_set_by_hand_is_the_wall_size_of_the_edge_refinement(workspace, tmp_path):
    """The old gate left the edges alone when size_min was set and Smart sizing was off: now the edge cells are size_min / factor."""
    pr = _example_project(workspace, "flutter_plate.step", "vibration_modal", tmp_path)
    pr.set_mesh(preset="quick", size_min_mm=4.0, edge_refinement=4)
    st = _mesh(pr)
    er = st["edge_refine"]
    assert er and st.get("edge_refine_skipped") is None
    assert er["size_far_of_edge_field"] == pytest.approx(0.004) and er["size"] == pytest.approx(0.001, rel=0.02)


def test_a_flow_study_without_a_flow_axis_in_its_domain_uses_the_free_stream(workspace, tmp_path):
    gmsh = _gmsh()
    try:
        gmsh.model.occ.addBox(0, 0, 0, 100, 60, 30)
        gmsh.model.occ.synchronize()
        step = tmp_path / "box.step"
        gmsh.write(str(step))
    finally:
        gmsh.finalize()
    pr = workspace.create_project("B", "external_aero")
    j = pr.import_step(step, original_name="box.step")
    assert j.wait(300) and j.status == "done", j.error
    pr.set_settings(speed=10)
    assert edgepreview.flow_axis_hint(pr) == 0                                            # forward +x: the free stream runs along x
    pr._edge_flow_hint() == 0
    assert pr._mesh_payload({})["flow_axis"] == 0
