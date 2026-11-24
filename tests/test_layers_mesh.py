"""Wall layers made WITH the tetrahedral mesh (Mesh step) instead of just before the run: reading what OpenFOAM reports, numbering the layers,
the y+ target, the layered mesh in the viewer, and one real OpenFOAM run (skipped where OpenFOAM is not installed)."""
import importlib
import json
import math

import numpy as np
import pytest

import simwb.project as P_
from simwb.solvers.openfoam import cases, layermesh, runner


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMWB_HOME", str(tmp_path / "ws"))
    importlib.reload(P_)
    yield P_


# ------------------------------------------------------------------------------------------------------------------ the log
LOG = """\
Shrinking and layer addition phase
----------------------------------

Using mesh parameters

patch        faces    layers avg thickness[m]
                             near-wall overall
-----        -----    ------ --------- -------
wall_no_slip 26341    4      0.00107   0.00574
body         130      4      0.00107   0.00574


Outer iteration : 0
-------------------

Layer addition iteration 0
Extruding 26471 out of 26471 faces (100%). Removed extrusion at 0 faces.
Extruding 26400 out of 26471 faces (99.7317%). Removed extrusion at 71 faces.

Mesh with layers : cells:951514  faces:1973055  points:203933

patch        faces        layers        overall thickness
                      target   mesh     [m]       [%]
-----        -----        -----    ----     ---       ---
wall_no_slip 26341    4        4        0.00573   99.8
body         130      4        3.4      0.00555   96.7

Layer mesh : cells:951514  faces:1973055  points:203933
Cells per refinement level:
"""


def test_the_layer_tables_and_the_extruded_share_are_read_from_the_log(tmp_path):
    (tmp_path / "log.snappyHexMesh").write_text(LOG)
    d = layermesh.parse_log(tmp_path)
    wall, body = d["patches"]
    assert wall["name"] == "wall_no_slip" and wall["faces"] == 26341 and wall["target"] == 4 and wall["made"] == 4.0
    assert wall["first_mm"] == pytest.approx(1.07) and wall["total_mm"] == pytest.approx(5.73) and wall["percent"] == 99.8
    assert body["made"] == pytest.approx(3.4)                              # (OpenFOAM reports the average over the faces: it may be fractional)
    assert d["extruded_percent"] == pytest.approx(99.7317) and d["faces_extruded"] == 26400 and d["faces_total"] == 26471
    assert d["cells"] == 951514


def test_a_log_without_layers_reads_as_nothing(tmp_path):
    d = layermesh.parse_log(tmp_path)                                       # (no log at all)
    assert d["patches"] == [] and d["extruded_percent"] is None and d["cells"] is None
    (tmp_path / "log.snappyHexMesh").write_text("Finished meshing without any errors\n")
    assert layermesh.parse_log(tmp_path)["patches"] == []


def test_the_check_log_gives_the_cell_types_and_the_worst_numbers(tmp_path):
    (tmp_path / "log.checkMesh_layers").write_text(
        "    cells:            21463\nOverall number of cells of each type:\n    hexahedra:     0\n    prisms:        2856\n    wedges:        0\n"
        "    pyramids:      0\n    tet wedges:    0\n    tetrahedra:    18607\n    polyhedra:     0\n"
        "Mesh non-orthogonality Max: 48.9 average: 15.7\n Max skewness = 2.44 OK.\n Max aspect ratio = 10.19 OK.\nMesh OK.\n")
    c = layermesh.parse_check(tmp_path)
    assert c["types"]["prisms"] == 2856 and c["types"]["tetrahedra"] == 18607 and c["cells"] == 21463
    assert c["non_orthogonality_max"] == pytest.approx(48.9) and c["skewness_max"] == pytest.approx(2.44) and c["mesh_ok"]


# ------------------------------------------------------------------------------------------------------------------ the layered mesh
def stack_mesh():
    """Two prism layers on a triangle of wall with a tetrahedron on top (the shape snappyHexMesh makes), as a polyMesh dictionary."""
    A = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], float)
    P = np.vstack([A, A + [0, 0, 0.1], A + [0, 0, 0.2], [[0.3, 0.3, 0.5]]])
    faces = [[3, 4, 5], [6, 7, 8],                                           # internal: between layer 1 and 2, between layer 2 and the tetrahedron
             [0, 2, 1],                                                      # the wall
             [0, 1, 4, 3], [1, 2, 5, 4], [2, 0, 3, 5], [3, 4, 7, 6], [4, 5, 8, 7], [5, 3, 6, 8],     # the sides of the prisms
             [6, 7, 9], [7, 8, 9], [8, 6, 9]]                                # the top
    return {"points": P, "owner": np.array([0, 1, 0, 0, 0, 0, 1, 1, 1, 2, 2, 2]), "neighbour": np.array([1, 2]), "n_cells": 3, "faces": None,
            "poly_faces": [np.array(f) for f in faces],
            "patches": [{"name": "wall", "start": 2, "nFaces": 1}, {"name": "side", "start": 3, "nFaces": 6}, {"name": "top", "start": 9, "nFaces": 3}]}


def display(monkeypatch, tmp_path):
    pm = stack_mesh()
    monkeypatch.setattr(layermesh, "read_polymesh", lambda d: pm)
    patches = [{"name": "wall"}, {"name": "side"}, {"name": "top"}]
    faces = [{"id": 10, "kind": "body"}, {"id": 11, "kind": "body"}, {"id": 12, "kind": "farfield"}]
    return layermesh.display_arrays(tmp_path, {10: 0, 11: 1, 12: 2}, patches, faces, 7)[0]


def test_the_layers_are_counted_from_the_wall_outwards(monkeypatch, tmp_path):
    arr = display(monkeypatch, tmp_path)
    layer = arr["tet_layer"]
    assert {int(k): int(v) for k, v in zip(*np.unique(layer, return_counts=True))} == {0: 1, 1: 8, 2: 8}     # (a prism is 8 display tetrahedra)
    assert len(arr["tets"]) == 17 and len(arr["quality"]) == 17 and (arr["tet_vol"] == 7).all()


def test_the_display_tetrahedra_fill_the_layers_exactly(monkeypatch, tmp_path):
    arr = display(monkeypatch, tmp_path)
    P, T = arr["nodes"], arr["tets"].astype(int)
    a, b, c, d = (P[T[:, i]] for i in range(4))
    vol = np.einsum("ij,ij->i", np.cross(b - a, c - a), d - a) / 6.0
    assert (vol > 0).all()                                                    # (every one is turned the right way)
    assert vol.sum() == pytest.approx(0.05 + 0.05 + 0.05)                     # two prisms of 0.5 x 0.1 and a tetrahedron of 0.5 x 0.3 / 3
    assert vol[arr["tet_layer"] == 1].sum() == pytest.approx(0.05) and vol[arr["tet_layer"] == 2].sum() == pytest.approx(0.05)


def test_the_boundary_triangles_know_their_cad_face_and_a_cell(monkeypatch, tmp_path):
    arr = display(monkeypatch, tmp_path)
    assert len(arr["tris"]) == 1 + 12 + 3                                     # (the quadrilaterals of the sides are cut into two triangles)
    assert sorted(set(arr["tri_surf"].tolist())) == [10, 11, 12]
    wall = arr["tri_owner"][arr["tri_surf"] == 10][0]
    assert arr["tet_layer"][wall] == 1                                        # (the wall triangle belongs to a layer-1 cell)
    assert (arr["quality"] > 0).all() and (arr["quality"] <= 1).all()


def test_a_prism_whose_side_is_on_a_boundary_is_not_a_first_layer():
    """The second prism has three quadrilateral faces on a boundary (the far field, a symmetry plane) but its triangles are not on the wall:
    only the triangle on the wall starts the count (a real mesh has hundreds of such side faces along its symmetry plane)."""
    pm = stack_mesh()
    assert layermesh.layer_numbers(pm, np.array([True, True, False])).tolist() == [1, 2, 0]
    assert layermesh.layer_numbers(pm, np.array([False, False, False])).tolist() == [0, 0, 0]       # (no prisms, no layers)
    assert layermesh.layer_numbers(pm, np.array([False, True, False])).tolist() == [0, 0, 0]         # (a prism that is not on the wall nor on one that is)


# ------------------------------------------------------------------------------------------------------------------ y+ and the settings
def test_the_y_plus_target_sets_the_first_layer_thickness():
    args = (20.0, 1.6, 1.5e-5, "kOmegaSST", 5, 1.2)
    auto = cases.layer_plan(*args)
    low = cases.layer_plan(*args, y_plus=1.0)
    high = cases.layer_plan(*args, y_plus=100.0)
    assert auto["y_plus"] == 30.0 and low["y_plus"] == 1.0
    assert low["first"] == pytest.approx(auto["first"] / 30.0)               # (the first layer is proportional to the target y+)
    assert high["first"] == pytest.approx(auto["first"] * 100.0 / 30.0)
    assert low["total"] < auto["total"] < high["total"]                      # (the same number of layers: a thin first layer makes them thin all through)
    assert low["cells_across_delta"] >= auto["cells_across_delta"] >= high["cells_across_delta"]     # (and more of them lie inside the boundary layer)
    assert cases.layer_plan(*args, first=0.002, y_plus=5)["y_plus"] is None  # (a first layer given by the user wins: no target)
    assert cases.layer_plan(20.0, 1.6, 1.5e-5, "laminar", 5, 1.2, y_plus=5)["y_plus"] is None


def _flow_project(P, tmp_path, dims=(0.2, 0.08, 0.04)):
    import gmsh

    gmsh.initialize(["-nopopup"])
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("b")
        gmsh.model.occ.addBox(0, 0, 0, *[1000 * d for d in dims])
        gmsh.model.occ.synchronize()
        step = tmp_path / "box.step"
        gmsh.write(str(step))
    finally:
        gmsh.finalize()
    pr = P.create_project("Layers", "external_aero")
    j = pr.import_step(step, original_name="box.step")
    assert j.wait(300) and j.status == "done", j.error
    pr.set_settings(speed=10)
    return pr


def test_the_y_plus_target_is_stored_checked_and_kept(workspace, tmp_path):
    pr = _flow_project(workspace, tmp_path)
    pr.set_mesh(layers={"n": 4, "growth": 1.2, "y_plus": 5})
    assert pr.data["mesh"]["layers"]["y_plus"] == 5.0
    pr.set_mesh(layers={"n": 6, "growth": 1.2})                               # (an option not given keeps its value)
    assert pr.data["mesh"]["layers"]["y_plus"] == 5.0 and pr.data["mesh"]["layers"]["n"] == 6
    for bad in (0.1, 400):
        with pytest.raises(ValueError, match="y\\+ must be between"):
            pr.set_mesh(layers={"n": 4, "growth": 1.2, "y_plus": bad})
    pr.set_mesh(layers={"n": 4, "growth": 1.2, "y_plus": 0})                  # (0 or empty = automatic)
    assert pr.data["mesh"]["layers"]["y_plus"] is None


def test_changing_the_layers_marks_the_layered_mesh_out_of_date_but_keeps_the_mesh(workspace, tmp_path):
    pr = _flow_project(workspace, tmp_path)
    pr.set_mesh(preset="quick", layers={"n": 3, "growth": 1.2})
    pr.data["mesh"]["status"] = "ready"
    pr.data["mesh"]["stats"] = {"cells": 10, "layers": {"made": True, "stale": False}}
    pr.set_mesh(layers={"n": 3, "growth": 1.2})                               # (the same settings: nothing changes)
    assert pr.data["mesh"]["stats"]["layers"]["stale"] is False
    pr.set_mesh(layers={"n": 3, "growth": 1.3})
    assert pr.data["mesh"]["stats"]["layers"]["stale"] is True and pr.data["mesh"]["status"] == "ready"   # (the tetrahedra are as they were)


def test_the_layer_step_says_why_it_is_not_made(workspace, tmp_path, monkeypatch):
    pr = _flow_project(workspace, tmp_path)
    assert layermesh.why_not(pr.data, "static_structural", None)
    assert "itself" in layermesh.why_not(pr.data, "external_aero", "hex")
    pr.set_mesh(layers={"n": 0, "growth": 1.2})
    assert "0" in layermesh.why_not(pr.data, "external_aero", "tet")
    pr.set_mesh(layers={"n": 3, "growth": 1.2})
    assert layermesh.why_not(pr.data, "external_aero", "tet") is None
    monkeypatch.setattr(runner, "detect", lambda refresh=False: {"available": False, "backend": None})
    r = pr._make_layers(type("J", (), {})(), 0, 1)                            # (no OpenFOAM: a reason, not an error)
    assert r["made"] is False and "OpenFOAM" in r["reason"]


def test_the_layer_step_cannot_stop_the_mesh(workspace, tmp_path, monkeypatch):
    pr = _flow_project(workspace, tmp_path)
    pr.set_mesh(layers={"n": 3, "growth": 1.2})
    monkeypatch.setattr(pr, "_make_layers_inner", lambda *a, **k: (_ for _ in ()).throw(ValueError("the log was odd")))
    r = pr._make_layers(None, 0, 1)
    assert r["made"] is False and "the log was odd" in r["reason"] and not pr.layered_path.exists()


# ------------------------------------------------------------------------------------------------------------------ the view
def test_the_layer_view_needs_a_layered_mesh_and_says_so(workspace, tmp_path):
    pr = _flow_project(workspace, tmp_path)
    with pytest.raises(ValueError, match="no mesh with wall layers"):
        pr.mesh_view(metric="layers")
    with pytest.raises(ValueError, match="Available: .*layers"):
        pr.mesh_view(metric="nonsense")


def test_the_layer_colours_run_from_warm_at_the_wall_to_cool(workspace):
    c = workspace.Project.layer_colors(5)
    assert c[0] == "#f08a24" and c[-1] == "#2fb8c8" and len(set(c)) == 5
    assert workspace.Project.layer_colors(1) == ["#f08a24"] and workspace.Project.layer_colors(0) == []


def test_the_camera_goes_to_the_edge_of_the_object_in_the_cut(workspace, tmp_path, monkeypatch):
    pr = _flow_project(workspace, tmp_path)
    arr = display(monkeypatch, tmp_path)
    pr.mesh_dir.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(pr.layered_path, **arr)
    pr.data["mesh"]["stats"] = {"layers": {"made": True, "total_mm": 2.0}}
    f = pr.layer_focus("x", 0.3)                                              # (a cut through the stack at x = 0.3)
    assert f is not None and f["half"] == pytest.approx(0.02)                 # (ten times all the layers)
    lo, hi = np.array(f["box"][:3]), np.array(f["box"][3:])
    assert np.allclose(hi - lo, 0.04) and f["center"][0] == pytest.approx(0.3)
    assert f["center"][1] == pytest.approx(0.0)                               # (the end of the section that lies furthest back along its longer side)
    assert pr.layer_focus("x", 5.0) is None                                    # (a cut that meets no layer)


# ------------------------------------------------------------------------------------------------------------------ one real run
needs_foam = pytest.mark.skipif(not runner.detect().get("available"), reason="OpenFOAM is not installed")


@needs_foam
def test_a_real_flow_study_gets_its_layers_with_the_mesh(workspace, tmp_path):
    pr = _flow_project(workspace, tmp_path)
    pr.set_mesh(preset="quick", layers={"n": 3, "growth": 1.2})
    j = pr.generate_mesh()
    assert j.wait(900) and j.status == "done", (j.error, j.log[-12:])
    st = pr.data["mesh"]["stats"]
    L = st["layers"]
    assert L["made"] and L["layers_max"] == 3 and L["extruded_percent"] > 95
    assert L["cells"] > st["cells"] and L["cells"] == st["cells"] + L["layer_cells"]      # (prisms are added to the tetrahedra, nothing else changes)
    assert L["first_mm"] and L["total_mm"] > L["first_mm"] and L["patches"][0]["target"] == 3
    assert L["mesh_ok"] and L["non_orthogonality_max"] < 75
    assert pr.layered_path.exists() and not (pr.mesh_dir / "layercase").exists()           # (the working folder is removed)
    # the viewer: the outside shows the first layer at the wall, a cut shows all of them
    v = pr.mesh_view(metric="layers")
    assert v["n_tris"] > 0 and v["present"] == [1] and [c["name"] for c in v["categories"]][:2] == ["Tetrahedra", "Layer 1 (at the wall)"]
    ax, frac = pr.layers_default_cut()
    lo, hi = float(pr.load_mesh()["nodes"][:, "xyz".index(ax)].min()), float(pr.load_mesh()["nodes"][:, "xyz".index(ax)].max())
    cut = pr.mesh_view(ax, lo + (hi - lo) * frac, False, None, "layers")
    assert set(cut["present"]) >= {0, 1, 2, 3}
    assert pr.layer_focus(ax, lo + (hi - lo) * frac) is not None
    # the ordinary views are the tetrahedral mesh as before
    assert pr.mesh_view()["n_tris"] > 0 and pr.mesh_metrics()["method"] == "tet"
    # changing the layers makes them stale; making them again brings them up to date without a new mesh
    pr.set_mesh(layers={"n": 2, "growth": 1.2})
    assert pr.data["mesh"]["stats"]["layers"]["stale"] is True
    cells_before = st["cells"]
    j = pr.generate_layers()
    assert j.wait(600) and j.status == "done", (j.error, j.log[-8:])
    L2 = pr.data["mesh"]["stats"]["layers"]
    assert L2["layers_max"] == 2 and L2["stale"] is False and pr.data["mesh"]["stats"]["cells"] == cells_before
    # the case the run writes is unchanged (it adds the same layers itself)
    j = pr.solve(export_only=True)
    assert j.wait(300) and j.status == "done", (j.error, j.log[-8:])
    meta = json.loads((pr.case_dir / "simwb_case.json").read_text())
    assert "snappyHexMesh" in [s["app"] for s in meta["steps"]]


@needs_foam
def test_a_new_mesh_forgets_the_old_layers(workspace, tmp_path):
    pr = _flow_project(workspace, tmp_path)
    pr.set_mesh(preset="quick", layers={"n": 2, "growth": 1.2})
    assert pr.generate_mesh().wait(900) and pr.layered_path.exists()
    pr.set_mesh(layers={"n": 0, "growth": 1.2})
    j = pr.generate_mesh()
    assert j.wait(900) and j.status == "done"
    assert not pr.layered_path.exists() and pr.data["mesh"]["stats"]["layers"]["made"] is False
    with pytest.raises(ValueError, match="no mesh with wall layers"):
        pr.mesh_view(metric="layers")


# ------------------------------------------------------------------------------------------------------------------ how many layers fit
def test_the_layers_that_fit_on_a_thin_part_match_what_was_measured():
    """Measured on the example wing (thinnest part 6.34 mm, first layer 0.85 mm, growth 1.2): 5 layers kept 70 % of the wall, 3 kept 99 %, 2 kept 100 %."""
    assert layermesh.layers_that_fit(0.85, 1.2, 6.34) == 3
    assert layermesh.layers_that_fit(0.85, 1.1, 6.34) == 3                    # (3 layers of growth 1.1 kept 99.6 %)
    assert layermesh.layers_that_fit(0.85, 1.2, 40.0) >= 6                    # (a thick part takes many)
    assert layermesh.layers_that_fit(3.0, 1.2, 4.0) == 0                      # (not even one)
    assert layermesh.layers_that_fit(None, 1.2, 6.34) is None and layermesh.layers_that_fit(0.85, 1.2, 0) is None


def test_the_warning_names_the_cause_and_the_number_of_layers_that_fit():
    w, fit = layermesh.layer_warnings(70.2, 3.51, 0.85, 1.2, 5, 6.34)
    assert fit == 3 and len(w) == 1
    assert "70%" in w[0] and "6.3 mm" in w[0] and "run into each other" in w[0] and "3 layers of this growth fit" in w[0]
    assert "tolerant" not in w[0]                                              # (measured: the tolerant limits did not help here)
    w, fit = layermesh.layer_warnings(99.2, 2.97, 0.85, 1.2, 3, 6.34)         # (3 layers kept 99 %: nothing to say)
    assert w == [] and fit == 3
    w, _ = layermesh.layer_warnings(80.0, 1.0, 0.3, 1.2, 3, 40.0)              # (lost layers, but not on a thin part: the general advice)
    assert len(w) == 1 and "Sharp edges" in w[0] and "run into" not in w[0]
    w, _ = layermesh.layer_warnings(60.0, 5.0, 3.0, 1.2, 4, 4.0)               # (not even one fits)
    assert "Not even one layer fits" in w[0]
    w, _ = layermesh.layer_warnings(100.0, 1.0, 0.3, 1.2, 3, 40.0, non_orth=75)
    assert len(w) == 1 and "non-orthogonality is 75" in w[0]
    assert layermesh.layer_warnings(None, None, None, 1.2, 3, None) == ([], None)
