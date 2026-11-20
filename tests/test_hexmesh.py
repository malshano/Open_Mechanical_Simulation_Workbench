"""The hex-dominant (snappyHexMesh) mesh: planning, the meshing case, reading the logs, and one real OpenFOAM run."""
import json
import re

import numpy as np
import pytest

import simwb.project as P_
from simwb.solvers.openfoam import cases, hexmesh, runner


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMWB_HOME", str(tmp_path / "ws"))
    import importlib

    importlib.reload(P_)
    yield P_


def _step(tmp_path, dims=(0.2, 0.08, 0.04)):
    """A box part as a STEP file (metres): a simple body for external flow."""
    import gmsh

    gmsh.initialize(["-nopopup"])
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("b")
        gmsh.model.occ.addBox(0, 0, 0, *[1000 * d for d in dims])   # (mm)
        gmsh.model.occ.synchronize()
        p = tmp_path / "box.step"
        gmsh.write(str(p))
    finally:
        gmsh.finalize()
    return p


def _project(P, tmp_path, **settings):
    pr = P.create_project("Hex", "external_aero")
    j = pr.import_step(_step(tmp_path), original_name="box.step")
    assert j.wait(300) and j.status == "done", j.error
    pr.set_settings(speed=10, **settings)
    return pr


def _plan(pr):
    faces, info, data = pr.faces(), pr.data["geometry"]["info"], pr.data
    s2p, patches = cases.build_patches("external_aero", faces, data.get("bcs", []))
    fl = P_.materials.get_fluid("air")
    plan = hexmesh.make_plan(data, faces, info, s2p, patches, cases.freestream_velocity(data["settings"]),
                             fl["mu"] / fl["rho"])
    return plan, faces, info, s2p, patches


def test_hex_mesh_is_offered_only_where_it_is_supported(workspace, tmp_path):
    pr = _project(workspace, tmp_path)
    assert pr.hex_unsupported() is None
    pr.set_mesh(method="hex")
    assert pr.data["mesh"]["method"] == "hex" and pr.data["mesh"]["status"] in ("none", "stale")
    with pytest.raises(ValueError, match="method must be"):
        pr.set_mesh(method="cartesian")
    # a rotor, a symmetry plane or a moving zone needs the tetrahedral mesh
    pr.data["rotors"] = [{"center": [0, 0, 0], "axis": [0, 0, 1], "radius": 0.05}]
    assert "Rotors" in pr.hex_unsupported()
    with pytest.raises(ValueError, match="Rotors"):
        pr.set_mesh(method="hex")
    pr.data["rotors"] = []
    pr.data["symmetry"] = [{"id": "x"}]
    assert "Symmetry" in pr.hex_unsupported()
    pr.data["symmetry"] = []
    pr.data["moving_zones"] = [{"name": "z"}]
    assert "Moving zones" in pr.hex_unsupported()
    # other studies do not have it
    pr2 = workspace.create_project("Heat", "heat_conduction")
    assert "external aerodynamics" in pr2.hex_unsupported().lower() or "Import geometry" in pr2.hex_unsupported()


def test_plan_sizes_levels_and_a_point_in_the_air(workspace, tmp_path):
    pr = _project(workspace, tmp_path)
    plan, faces, info, s2p, patches = _plan(pr)
    lo, hi = np.array(plan["box_min"]), np.array(plan["box_max"])
    base = plan["base"]
    assert plan["levels"] >= 4 and abs(base - plan["h_wall"] * 2 ** plan["levels"]) < 1e-12
    n = np.array(plan["cells_bg"])
    assert np.allclose(hi - lo, n * base)                      # cubic background cells
    assert (hi >= np.array(info["domain"]["box_max"]) - 1e-9).all()
    loc = np.array(plan["location"])
    assert ((loc > lo) & (loc < hi)).all()                      # inside the box...
    pb = np.array(info["parts_bbox"])
    assert not ((loc > pb[:3]) & (loc < pb[3:])).all()          # ...and not inside the part
    assert plan["regions"] and plan["regions"][0]["name"] == "wake" and plan["regions"][0]["level"] == plan["levels"] - 2
    assert [p["name"] for p in plan["wall_patches"]] == ["body"]
    assert sum(len(g["faces"]) for g in plan["wall_patches"][0]["groups"]) == 6
    assert plan["layers"]["n"] == 0 or plan["layers"]["first"] > 0


def test_refinement_groups_levels_and_the_user_regions(workspace, tmp_path):
    pr = _project(workspace, tmp_path)
    body = [f["id"] for f in pr.faces() if f.get("kind") != "farfield"]
    pr.set_mesh(refinements=[{"faces": body[:2], "size_mm": 2.0}],
                regions=[{"name": "my box", "min": [0, 0, 0], "max": [0.1, 0.05, 0.05], "size": 0.01}])
    plan = _plan(pr)[0]
    groups = plan["wall_patches"][0]["groups"]
    levels = sorted(g["level"] for g in groups)
    assert len(groups) == 2 and levels[0] == plan["levels"] and levels[1] > plan["levels"]
    fine = next(g for g in groups if g["level"] > plan["levels"])
    assert set(fine["faces"]) == set(body[:2])
    assert plan["base"] / 2 ** fine["level"] <= 0.002 * 1.0001            # the cell is at most the size asked for
    assert any(r["name"] == "my_box" for r in plan["regions"])


def test_the_meshing_case_is_written(workspace, tmp_path):
    pr = _project(workspace, tmp_path)
    pr.set_mesh(layers={"n": 3, "growth": 1.2})
    plan, faces, info, s2p, patches = _plan(pr)
    case = tmp_path / "hexcase"
    counts = hexmesh.write_case(case, plan, faces)
    assert counts and all(n > 0 for n in counts.values())
    for f in ("blockMeshDict", "snappyHexMeshDict", "surfaceFeatureExtractDict", "createPatchDict", "controlDict"):
        assert (case / "system" / f).is_file(), f
    g = plan["wall_patches"][0]["groups"][0]
    stl = (case / "constant" / "triSurface" / f"{g['name']}.stl").read_text()
    assert stl.startswith(f"solid {g['name']}") and stl.count("facet normal") == counts[g["name"]]
    snappy = (case / "system" / "snappyHexMeshDict").read_text()
    assert "castellatedMesh true;" in snappy and "addLayers       true;" in snappy
    assert f'file "{g["name"]}.stl"' in snappy and "explicitFeatureSnap true" in snappy
    assert "relativeSizes false;" in snappy and re.search(r"firstLayerThickness [0-9.e-]+;", snappy)
    assert "nRelaxedIter 3;" in snappy and "minDeterminant 1e-5;" in snappy   # (relaxed limits for the layer step)
    loc = re.search(r"locationInMesh \(([^)]*)\)", snappy).group(1).split()
    assert np.allclose([float(x) for x in loc], plan["location"], rtol=1e-6)
    # patches made by snappyHexMesh are merged back into the patches the setup knows
    cp = (case / "system" / "createPatchDict").read_text()
    assert "name body;" in cp and g["name"] in cp
    # (snappyHexMesh refuses to merge points when the case writes few digits)
    cd = (case / "system" / "controlDict").read_text()
    assert "writePrecision  10;" in cd and "writeFormat     ascii;" in cd
    bm = (case / "system" / "blockMeshDict").read_text()
    assert f"({plan['cells_bg'][0]} {plan['cells_bg'][1]} {plan['cells_bg'][2]})" in bm and "farfield" in bm
    # layers off: no layer step
    pr.set_mesh(layers={"n": 0, "growth": 1.2})
    plan0, faces0 = _plan(pr)[0], pr.faces()
    hexmesh.write_case(tmp_path / "c0", plan0, faces0)
    assert "addLayers       false;" in (tmp_path / "c0" / "system" / "snappyHexMeshDict").read_text()


CHECKMESH = """
Mesh stats
    points:           73130
    cells:            65864
    faces:            204656
Overall number of cells of each type:
    hexahedra:     60000
    prisms:        40
    polyhedra:     5824
    tetrahedra:    0
    Mesh non-orthogonality Max: 57.69867535 average: 6.172932724
    Max skewness = 1.699153444 OK.
    Max aspect ratio = 5.072085318 OK.
   *There are 317 faces with concave angles between consecutive edges. Max concave angle = 67.7 degrees.
 ***Concave cells (using face planes) found, number of cells: 2176
Failed 1 mesh checks.
"""
SNAPPY = """
Extruding 800 out of 1000 faces (80%). Removed extrusion at 5 faces.
patch faces    layers avg thickness[m]
                     near-wall overall
----- -----    ------ --------- -------
body 1000      3      0.000742  0.00213

patch   faces  layers overall thickness
        target mesh   [m]       [%]
-----   -----  -----  ----      ---
body 1000   3     2.48 0.0022   81.4

Layer mesh : cells:65864  faces:204656  points:73130
"""


def test_the_logs_are_read(tmp_path):
    (tmp_path / "log.checkMesh").write_text(CHECKMESH)
    (tmp_path / "log.snappyHexMesh").write_text(SNAPPY)
    r = hexmesh.parse_logs(tmp_path)
    assert r["cells"] == 65864 and r["types"]["hexahedra"] == 60000 and r["types"]["polyhedra"] == 5824
    assert r["non_orthogonality_max"] == pytest.approx(57.7, abs=0.1) and r["non_orthogonality_mean"] == pytest.approx(6.17, abs=0.01)
    assert r["skewness_max"] == pytest.approx(1.70, abs=0.01) and r["aspect_ratio_max"] == pytest.approx(5.07, abs=0.01)
    # concave cells are a warning, not a failure
    assert r["mesh_ok"] and r["problems"] == [] and r["concave_cells"] == 2176 and any("concave" in w for w in r["warnings"])
    assert r["layers_extruded_percent"] == 80.0     # (the last extrusion line, not the plan table before it)
    # any other failed check is a problem
    (tmp_path / "log.checkMesh").write_text(CHECKMESH.replace(
        "Failed 1 mesh checks.", " ***Cells with small determinant (< 0.001) found, number of cells: 176\nFailed 2 mesh checks."))
    r = hexmesh.parse_logs(tmp_path)
    assert not r["mesh_ok"] and r["problems"] and "determinant" in r["problems"][0]
    # no logs at all: nothing is invented
    assert hexmesh.parse_logs(tmp_path / "nothing")["cells"] == 0


def test_createpatch_result_is_moved_into_place(tmp_path):
    (tmp_path / "constant" / "polyMesh").mkdir(parents=True)
    (tmp_path / "constant" / "polyMesh" / "boundary").write_text("old")
    (tmp_path / "0" / "polyMesh").mkdir(parents=True)
    (tmp_path / "0" / "polyMesh" / "boundary").write_text("new")
    hexmesh.finalize(tmp_path)
    assert (tmp_path / "constant" / "polyMesh" / "boundary").read_text() == "new" and not (tmp_path / "0" / "polyMesh").exists()
    hexmesh.finalize(tmp_path)  # nothing to move: harmless


def test_the_case_writer_refuses_rotors_on_a_hex_mesh_and_uses_the_mesh_as_it_is(workspace, tmp_path):
    pr = _project(workspace, tmp_path)
    m = {"hex_dir": str(tmp_path / "nowhere"), "tets": np.zeros((0, 4), int), "nodes": np.zeros((0, 3))}
    data = dict(pr.data, rotors=[{"center": [0, 0, 0], "axis": [0, 0, 1], "radius": 0.05, "thrust_N": 1.0}])
    with pytest.raises(ValueError, match="does not support rotors"):
        cases.write_external_aero(tmp_path / "c", data, m, pr.faces(), pr.data["geometry"]["info"])


needs_foam = pytest.mark.skipif(not runner.detect().get("available"), reason="OpenFOAM is not installed")


@needs_foam
def test_a_real_hex_mesh_end_to_end(workspace, tmp_path):
    """The whole path with real OpenFOAM: mesh -> stored mesh -> viewer -> case (without a second snappyHexMesh)."""
    pr = _project(workspace, tmp_path)
    pr.set_mesh(preset="quick", layers={"n": 2, "growth": 1.2})
    pr.set_mesh(method="hex")
    j = pr.generate_mesh()
    assert j.wait(900) and j.status == "done", (j.error, j.log[-15:])
    st = pr.data["mesh"]["stats"]
    assert st["method"] == "hex" and st["cells"] > 1000
    assert st["cell_types"]["hexahedra"] > 0.5 * st["cells"]                 # mostly hexahedra
    assert st["foam_quality"]["non_orthogonality_max"] < 75
    m = pr.load_mesh()
    assert "hex_dir" in m and len(m["tri_surf"]) == len(m["tris"]) == len(m["tri_owner"])
    view = pr.mesh_view()
    assert view["n_tris"] > 0
    # the mesh patches are the ones the setup writes conditions for
    from simwb.solvers.openfoam.foamio import read_boundary

    names = {b["name"] for b in read_boundary(pr.mesh_dir / "polyMesh_hex" / "boundary")}
    assert names == {"farfield", "body"}
    j = pr.solve(export_only=True)
    assert j.wait(300) and j.status == "done", (j.error, j.log[-10:])
    meta = json.loads((pr.case_dir / "simwb_case.json").read_text())
    apps = [s["app"] for s in meta["steps"]]
    assert "snappyHexMesh" not in apps and "checkMesh" in apps and meta["n_cells"] == st["cells"]
    assert not (pr.case_dir / "system" / "snappyHexMeshDict").exists()
    assert "body" in (pr.case_dir / "0" / "U").read_text()
