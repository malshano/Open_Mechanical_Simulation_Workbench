"""#33: the layers are cut to the local thickness of the body (half of it), in the planner and in the case the run writes."""
import importlib
import re

import numpy as np
import pytest

import simwb.project as P_
from simwb import localgeom, meshplan
from simwb.solvers.openfoam import layermesh


def _box(lo, hi, fid0=1):
    """The six faces of a box as display triangles, out of the body."""
    lo, hi = np.array(lo, float), np.array(hi, float)
    c = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    faces = []
    for k, q in enumerate(([0, 1, 3, 2], [4, 6, 7, 5], [0, 4, 5, 1], [2, 3, 7, 6], [0, 2, 6, 4], [1, 5, 7, 3])):
        v = c[q]
        tri = [[0, 1, 2], [0, 2, 3]]
        if np.cross(v[1] - v[0], v[2] - v[0]) @ (v.mean(0) - (lo + hi) / 2) < 0:
            tri = [[0, 2, 1], [0, 3, 2]]
        faces.append({"id": fid0 + k, "kind": "body", "mesh": {"vertices": v.tolist(), "triangles": tri}})
    return faces


def _wedge(length=0.1, width=0.05, t=0.004, tip=0.02):
    """A plate t thick whose last `tip` of length is a wedge that goes to zero thickness (extruded along y): triangles out of the body."""
    x0, x1, x2 = 0.0, length - tip, length
    ring = np.array([[x0, 0, t / 2], [x1, 0, t / 2], [x2, 0, 0], [x1, 0, -t / 2], [x0, 0, -t / 2]], float)
    P0, P1 = ring, ring + [0, width, 0]
    faces, fid = [], 1
    mid = np.array([length / 2, width / 2, 0.0])
    for i in range(5):
        j = (i + 1) % 5
        v = np.array([P0[i], P0[j], P1[j], P1[i]])
        tri = [[0, 1, 2], [0, 2, 3]]
        if np.cross(v[1] - v[0], v[2] - v[0]) @ (v.mean(0) - mid) < 0:
            tri = [[0, 2, 1], [0, 3, 2]]
        faces.append({"id": fid, "kind": "body", "mesh": {"vertices": v.tolist(), "triangles": tri}})
        fid += 1
    for P, sgn in ((P0, -1), (P1, 1)):                                  # (the two ends of the extrusion)
        tri = [[0, 1, 2], [0, 2, 4], [2, 3, 4]]
        n = np.cross(P[tri[0][1]] - P[tri[0][0]], P[tri[0][2]] - P[tri[0][0]])
        if n[1] * sgn < 0:
            tri = [[a, c, b] for a, b, c in tri]
        faces.append({"id": fid, "kind": "body", "mesh": {"vertices": P.tolist(), "triangles": tri}})
        fid += 1
    return faces


# ---------------------------------------------------------------------------------------------------------------------------------- the thickness
def test_the_thickness_behind_a_plate_is_its_thickness():
    surf = meshplan.Surface(_box([0, 0, 0], [0.1, 0.05, 0.004]), "heat_conduction")
    r = localgeom.wall_distances(surf, 0.002, 0.03, "in")
    big = np.abs(r["normals"][:, 2]) > 0.9                                                         # the two large faces
    assert big.any() and np.allclose(r["dist"][big], 0.004, atol=1e-5)                            # 4 mm behind the large faces
    assert np.isinf(r["dist"][np.abs(r["normals"][:, 0]) > 0.9]).all()                            # 100 mm behind the end faces: beyond the reach of 30 mm
    out = localgeom.wall_distances(surf, 0.002, 0.03, "out")
    assert np.isinf(out["dist"]).all()                                                            # nothing in front of a lone plate


def test_the_gap_between_two_plates_is_seen_from_both_sides():
    faces = _box([0, 0, 0], [0.1, 0.05, 0.004]) + _box([0, 0, 0.006], [0.1, 0.05, 0.010], fid0=7)
    r = localgeom.wall_distances(meshplan.Surface(faces, "heat_conduction"), 0.002, 0.03, "out")
    gap = np.isfinite(r["dist"])
    assert gap.any() and np.allclose(r["dist"][gap], 0.002, atol=1e-5)                            # the 2 mm slot between the plates
    assert len(set(np.unique(r["faces"][gap]))) == 2                                              # the top of the lower one and the bottom of the upper one


def test_the_thickness_of_a_wedge_goes_to_zero_at_its_tip():
    surf = meshplan.Surface(_wedge(), "heat_conduction")
    r = localgeom.wall_distances(surf, 0.001, 0.02, "in")
    d, a = r["dist"], r["areas"]
    fin = np.isfinite(d)
    assert fin.sum() > 100 and d[fin].min() < 0.0003 and abs(d[fin].max() - 0.004) < 5e-4
    full = np.where(fin, d, 1.0)
    assert 0.0002 < localgeom.weighted_quantile(full, a, 0.05) < 0.0035                           # the thinnest 5 % of the wall is the wedge, not the whole plate
    assert localgeom.weighted_quantile(full, a, 0.5) > 0.0035


# ---------------------------------------------------------------------------------------------------------------------------------- the planner
def _thick(dist, areas=None):
    dist = np.asarray(dist, float)
    return {"dist": dist, "areas": np.ones(len(dist)) if areas is None else np.asarray(areas, float)}


def test_fewer_layers_fit_inside_half_the_thickness():
    out = layermesh.clamp_layers(0.0005, 1.2, 5, _thick([0.004] * 100))        # 5 layers of 0.5 mm, growth 1.2: 3.72 mm; half of 4 mm is 2 mm: 3 layers (1.82 mm)
    assert out["n"] == 3 and out["first"] == 0.0005 and out["y_plus_kept"] and out["changed"]
    assert out["total"] == pytest.approx(0.0005 * (1.2 ** 3 - 1) / 0.2) and out["total"] <= 0.5 * 0.004
    assert out["share_short_before"] == 1.0 and out["share_short"] == 0.0
    assert not layermesh.clamp_layers(0.0005, 1.2, 3, _thick([0.004] * 100))["changed"]          # they fit: nothing changes
    assert not layermesh.clamp_layers(0.0005, 1.2, 5, _thick([float("inf")] * 10))["changed"]    # nothing near: thick enough


def test_a_single_thin_layer_when_not_even_one_fits_and_the_tip_is_left_out():
    out = layermesh.clamp_layers(0.0005, 1.2, 4, _thick([0.0005] * 100))
    assert out["n"] == 1 and out["first"] == pytest.approx(0.00025) and not out["y_plus_kept"]
    # the thinnest 4 % of the area is a tip: with a coverage of 95 % it does not set the layers, with 99.9 % it does
    d = [0.004] * 96 + [0.0001] * 4
    base = layermesh.clamp_layers(0.0005, 1.2, 3, _thick(d), coverage=0.95)
    assert base["n"] == 3 and base["share_short"] == pytest.approx(0.04)                         # the tip is what is still too thin
    tight = layermesh.clamp_layers(0.0005, 1.2, 3, _thick(d), coverage=0.999)
    assert tight["n"] == 1 and tight["first"] == pytest.approx(0.00005)


def test_the_clamp_setting_is_checked_and_kept_when_not_named():
    ck = layermesh.check_clamp
    assert ck(True) == {"fraction": 0.5, "coverage": 0.95} and ck(False) is None and ck({"on": False}) is None
    assert ck({"fraction": 0.3}, {"fraction": 0.5, "coverage": 0.9}) == {"fraction": 0.3, "coverage": 0.9}
    with pytest.raises(ValueError, match="0.1 to 0.5"):
        ck(0.8)
    with pytest.raises(ValueError, match="0.5 to 0.999"):
        ck({"coverage": 1.0})


# ---------------------------------------------------------------------------------------------------------------------------------- the project and the case
@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMWB_HOME", str(tmp_path / "ws"))
    importlib.reload(P_)
    return P_


def test_the_clamp_round_trips_through_set_mesh_and_the_file(workspace, tmp_path):
    from test_edge_refine import _plate_project
    pr = _plate_project(workspace, tmp_path)
    pr.set_mesh(layers={"n": 6, "growth": 1.2, "clamp": {"fraction": 0.4}})
    assert pr.data["mesh"]["layers"]["clamp"] == {"fraction": 0.4, "coverage": 0.95}
    pr.set_mesh(layers={"n": 4, "growth": 1.2})                                                    # (not named: kept)
    assert pr.data["mesh"]["layers"]["clamp"]["fraction"] == 0.4 and pr.data["mesh"]["layers"]["n"] == 4
    rep = pr.layer_clamp_report()
    assert rep["changed"] and rep["n"] <= 4 and rep["total"] <= 0.4 * rep["thickness_ref"] + 1e-12       # (the 4 mm plate with its knife-edge wedges)
    assert 0.0 <= rep["share_short"] <= rep["share_short_before"]
    pr.set_mesh(layers={"n": 4, "clamp": False})
    assert "clamp" not in pr.data["mesh"]["layers"] and pr.layer_clamp_report() is None
    with pytest.raises(ValueError):
        pr.set_mesh(layers={"n": 4, "clamp": {"fraction": 2}})
    pr.set_mesh(layers={"n": 4, "clamp": True})
    pr.save()
    again = workspace.open_project(pr.id)
    assert again.data["mesh"]["layers"]["clamp"] == {"fraction": 0.5, "coverage": 0.95}


def test_the_case_the_run_writes_gets_the_clamped_layers(workspace, tmp_path):
    from test_edge_refine import _plate_project
    pr = _plate_project(workspace, tmp_path)
    pr.set_mesh(preset="quick", layers={"n": 8, "growth": 1.2})
    j = pr.generate_mesh()
    assert j.wait(600) and j.status == "done", j.error
    out = {}
    for clamp in (False, True):
        pr.set_mesh(layers={"n": 8, "growth": 1.2, "clamp": clamp})
        j = pr.solve(export_only=True)
        assert j.wait(300) and j.status == "done", (j.error, j.log[-6:])
        txt = (pr.case_dir / "system" / "snappyHexMeshDict").read_text()
        out[clamp] = (int(re.search(r"nSurfaceLayers (\d+);", txt).group(1)), float(re.search(r"firstLayerThickness\s+([\d.e+-]+);", txt).group(1)))
    assert out[False][0] == 8
    assert out[True][0] < 8 and out[True][1] == pytest.approx(out[False][1])                       # fewer layers, the first one as thick as it was (y+ kept)
