"""#34 wake box, #35 curvature and proximity sizing (and the coarsening of #38 in the plan): the settings, the plans on synthetic geometry with known answers, the
memory planner's estimate and real small meshes that show the methods change the mesh as intended."""
import importlib
import math

import numpy as np
import pytest

import simwb.project as P_
from simwb import meshadvisor, meshplan, sizingmethods
from test_layer_clamp import _box


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMWB_HOME", str(tmp_path / "ws"))
    importlib.reload(P_)
    return P_


def _cylinder_faces(R=0.005, h=0.03, n=48, fid=1):
    """The side of a cylinder along z as display triangles, out of the body."""
    a = np.linspace(0, 2 * math.pi, n, endpoint=False)
    bot = np.stack([R * np.cos(a), R * np.sin(a), np.zeros(n)], 1)
    V = np.vstack([bot, bot + [0, 0, h]])
    T = []
    for i in range(n):
        j = (i + 1) % n
        T += [[i, j, n + j], [i, n + j, n + i]]
    return [{"id": fid, "kind": "body", "mesh": {"vertices": V.tolist(), "triangles": T}}]


# ---------------------------------------------------------------------------------------------------------------------------------- the settings
def test_the_settings_are_checked_merged_and_kept():
    ck = sizingmethods.check
    out = ck({"wake": True, "curvature": {"elements": 24}})
    assert out == {"wake": {"on": True}, "curvature": {"on": True, "elements": 24.0}}
    out = ck({"curvature": {"max_cells": 50000}, "wake": False}, out)
    assert out["curvature"] == {"on": True, "elements": 24.0, "max_cells": 50000.0} and out["wake"] == {"on": False}
    assert ck({"curvature": {"elements": ""}}, out)["curvature"] == {"on": True, "max_cells": 50000.0}            # emptied: automatic again
    for bad, msg in (({"nonsense": True}, "Unknown sizing method"), ({"curvature": {"elements": 2}}, "between 6 and 90"), ({"proximity": {"foo": 1}}, "Unknown option"),
                     ({"wake": {"length": 99}}, "between 0.5 and 20"), ({"coarsen": {"growth": 3}}, "between 1.25 and 1.6"), ("yes", "dictionary")):
        with pytest.raises(ValueError, match=msg):
            ck(bad)
    with pytest.raises(ValueError, match="external aerodynamics"):
        ck({"wake": True}, study="heat_conduction")
    assert sizingmethods.opts({"sizing_methods": out}, "curvature")["elements"] == 24.0 and sizingmethods.opts({"sizing_methods": out}, "wake") is None
    assert sizingmethods.opts({"sizing_methods": {"proximity": {"on": True}}}, "proximity")["cells_across"] == 3.0       # (the defaults are filled in)


def test_set_mesh_round_trip_and_save(workspace, tmp_path):
    from test_edge_refine import _plate_project
    pr = _plate_project(workspace, tmp_path)
    pr.set_mesh(sizing_methods={"proximity": {"cells_across": 4}, "curvature": True})
    sm = pr.data["mesh"]["sizing_methods"]
    assert sm["proximity"] == {"on": True, "cells_across": 4.0} and sm["curvature"] == {"on": True} and pr.data["mesh"]["status"] in ("stale", "none")
    pr.set_mesh(sizing_methods={"curvature": False})
    assert pr.data["mesh"]["sizing_methods"]["curvature"]["on"] is False and pr.data["mesh"]["sizing_methods"]["proximity"]["on"]
    with pytest.raises(ValueError):
        pr.set_mesh(sizing_methods={"proximity": {"cells_across": 99}})
    pr.save()
    assert workspace.open_project(pr.id).data["mesh"]["sizing_methods"]["proximity"]["cells_across"] == 4.0


# ---------------------------------------------------------------------------------------------------------------------------------- the plans
def test_the_cells_a_refined_face_adds_grow_as_the_size_falls_and_the_cap_holds():
    e = sizingmethods.bin_extra_cells
    assert e(0.01, 0.004, 0.003, 1.2) == 0.0 and e(0.0, 0.001, 0.003, 1.2) == 0.0
    assert e(0.01, 0.001, 0.003, 1.2) > e(0.01, 0.002, 0.003, 1.2) > 0 and e(0.02, 0.001, 0.003, 1.2) == pytest.approx(2 * e(0.01, 0.001, 0.003, 1.2))
    sizes, area = {1: 0.0008, 2: 0.0012}, {1: 0.02, 2: 0.01}
    free, e0, f0 = sizingmethods._fit_cap(sizes, area, 0.003, 1.2, 0.0005, 10 ** 9, False)
    assert f0 == 1.0 and free == sizes
    fit, e1, f1 = sizingmethods._fit_cap(sizes, area, 0.003, 1.2, 0.0005, 0.5 * e0, False)
    assert f1 > 1.0 and e1 <= 0.5 * e0 * 1.001 and all(fit[i] > sizes[i] for i in sizes)                              # coarser, under the cap


def test_the_curvature_method_refines_the_tight_curve_and_not_the_flat_face():
    faces = _cylinder_faces(0.005) + _box([0.1, 0, 0], [0.2, 0.05, 0.004], fid0=2)
    ms = {"sizing_methods": {"curvature": {"on": True}}}
    p = sizingmethods.plan("cht_enclosure", faces, ms, 0.003, 0.05, 1.2, 0.2, 16.0)
    assert p["n_curv"] == 16 and len(p["bins"]) == 1 and p["bins"][0][1] == [1]                                       # only the cylinder
    assert p["bins"][0][0] == pytest.approx(2 * math.pi * 0.005 / 16, rel=0.45)                                       # 2 pi R / 16 (a radius from the display triangles is good to about 30 %)
    fine = sizingmethods.plan("cht_enclosure", faces, {"sizing_methods": {"curvature": {"on": True, "elements": 40}}}, 0.003, 0.05, 1.2, 0.2, 16.0)
    assert fine["bins"][0][0] < p["bins"][0][0] and fine["n_curv"] == 40
    capped = sizingmethods.plan("cht_enclosure", faces, {"sizing_methods": {"curvature": {"on": True, "elements": 40, "max_cells": 1000}}}, 0.003, 0.05, 1.2, 0.2, 16.0)
    assert capped["info"]["curvature"]["capped"] and capped["info"]["curvature"]["extra_cells"] <= 1001 and capped["bins"][0][0] > fine["bins"][0][0]
    assert not sizingmethods.plan("cht_enclosure", faces, {"sizing_methods": {"curvature": {"on": True}}}, 0.0012, 0.05, 1.2, 0.2, 16.0)["bins"]       # the wall cells are finer already


def test_the_proximity_method_sizes_the_faces_that_face_a_narrow_gap():
    faces = _box([0, 0, 0], [0.1, 0.05, 0.004]) + _box([0, 0, 0.0055], [0.1, 0.05, 0.0095], fid0=7)               # a 1.5 mm gap
    ms = {"sizing_methods": {"proximity": {"on": True, "min_size_mm": 0.1, "max_cells": 10000000}}}
    p = sizingmethods.plan("cht_enclosure", faces, ms, 0.004, 0.05, 1.2, 0.1, 16.0)
    ids = sorted(i for _s, g in p["bins"] for i in g)
    assert len(ids) == 2 and p["info"]["proximity"]["side"] == "out" and p["info"]["proximity"]["narrowest"] == pytest.approx(0.0015, abs=1e-5)
    assert p["bins"][0][0] == pytest.approx(0.0015 / 3, rel=0.2)                                                      # 3 cells across
    wide = sizingmethods.plan("cht_enclosure", faces, {"sizing_methods": {"proximity": {"on": True, "cells_across": 6, "min_size_mm": 0.1, "max_cells": 10000000}}}, 0.004, 0.05, 1.2, 0.1, 16.0)
    assert wide["bins"][0][0] < p["bins"][0][0]
    none = sizingmethods.plan("cht_enclosure", _box([0, 0, 0], [0.1, 0.05, 0.004]), ms, 0.004, 0.05, 1.2, 0.1, 16.0)
    assert none["bins"] == [] and none["info"]["proximity"]["reason"] == "no_gaps"
    solid = sizingmethods.plan("heat_conduction", _box([0, 0, 0], [0.1, 0.05, 0.002]), ms, 0.004, 0.05, 1.2, 0.1, 16.0)    # a thin wall in a solid study
    assert solid["info"]["proximity"]["side"] == "in" and solid["bins"] and solid["bins"][0][0] == pytest.approx(0.002 / 3, rel=0.2)


def _analysis(sign=1.0):
    return {"lo": [0.0, -0.1, -0.05], "hi": [0.4, 0.1, 0.05], "axis": 0, "sign": sign, "length": 0.4, "lateral_axes": [1, 2]}


def test_the_wake_box_sits_behind_the_body_along_the_flow_and_the_cap_coarsens_it():
    lo, hi = meshadvisor.wake_box(_analysis(1.0), 4.0, 0.4)
    assert lo[0] == pytest.approx(0.4) and hi[0] == pytest.approx(0.4 + 4 * 0.4) and lo[1] == pytest.approx(-0.1 - 0.16) and hi[2] == pytest.approx(0.05 + 0.16)
    lo2, hi2 = meshadvisor.wake_box(_analysis(-1.0), 4.0, 0.4)                                                          # the flow the other way: the box is behind the other end
    assert hi2[0] == pytest.approx(0.0) and lo2[0] == pytest.approx(0.0 - 1.6)
    o = sizingmethods.opts({"sizing_methods": {"wake": {"on": True}}}, "wake")
    w = sizingmethods.wake_plan(_analysis(), o, 0.004)
    assert w["size"] == pytest.approx(5 * 0.004) and not w["capped"] and w["cells"] < w["cap"]
    big = sizingmethods.wake_plan(_analysis(), dict(o, size_factor=1.5, max_cells=20000), 0.004)
    assert big["capped"] and big["size"] > 1.5 * 0.004 and big["cells"] == pytest.approx(20000, rel=0.01)
    assert sizingmethods.wake_plan(_analysis(), dict(o, size_mm=9.0, max_cells=10 ** 8), 0.004)["size"] == pytest.approx(0.009)
    assert meshadvisor.recipe(dict(_an_full()), "quick", wake={"min": lo, "max": hi, "size": 0.01})["boxes"][1]["size"] == 0.01


def _an_full():
    an = _analysis()
    an.update(speed=10.0, nu=1.5e-5, turbulence="kOmegaSST", feature=0.02, reynolds=1e5, boundary_layer=0.001, ref_length=0.4, span=0.2, depth=0.1, thin=0.02)
    return an


# ---------------------------------------------------------------------------------------------------------------------------------- real meshes
def _step(tmp_path, build):
    import gmsh
    gmsh.initialize(["-nopopup"])
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.model.add("t")
    build(gmsh.model.occ)
    gmsh.model.occ.synchronize()
    p = tmp_path / "m.step"
    gmsh.write(str(p))
    gmsh.finalize()
    return p


def _flow_project(P, step, name="m"):
    pr = P.create_project(name, "external_aero")
    j = pr.import_step(step, original_name="m.step")
    assert j.wait(300) and j.status == "done", j.error
    pr.set_settings(speed=10.0)
    pr.set_mesh(preset="quick", smart=True)
    return pr


def _mesh(pr):
    for attempt in range(2):
        j = pr.generate_mesh()
        assert j.wait(600)
        if j.status == "failed" and "Non-manifold" in str(j.error) + str(j.log[-3:]) and attempt == 0:
            continue                                    # (Gmsh's threaded 3D mesher now and then leaves a face of three cells in a 1 mm gap: a second try of the same mesh is fine)
        break
    assert j.status == "done", (j.error, j.log[-6:])
    return pr.data["mesh"]["stats"]


def _edge_len_at(pr, sel):
    m = pr.load_mesh()
    X, T = m["nodes"], m["tris"]
    c = X[T].mean(1)
    s = sel(c)
    return float(np.linalg.norm(X[T[s, 0]] - X[T[s, 1]], axis=1).mean()), int(s.sum())


def test_proximity_sizing_makes_the_gap_faces_finer_and_the_estimate_follows(workspace, tmp_path):
    step = _step(tmp_path, lambda o: (o.addBox(0, 0, 0, 100, 60, 10), o.addBox(0, 0, 11.5, 100, 60, 10)))
    pr = _flow_project(workspace, step)
    off = _mesh(pr)
    e_off, n_off = _edge_len_at(pr, lambda c: np.abs(c[:, 2] - 0.010) < 1e-6)
    pr.set_mesh(sizing_methods={"proximity": {"max_cells": 100000}})
    ms, rc = meshplan.base_inputs(pr)
    est = meshplan.evaluate(pr, ms, rc)
    st = _mesh(pr)
    e_on, n_on = _edge_len_at(pr, lambda c: np.abs(c[:, 2] - 0.010) < 1e-6)
    info = st["sizing_methods"]["proximity"]
    assert info["applied"] and info["faces"] == 2 and info["side"] == "out" and info["narrowest"] == pytest.approx(0.0015, abs=2e-5)
    assert e_on < 0.65 * e_off and n_on > 2 * n_off                                                                    # the cells on the gap faces are much finer
    extra = st["cells"] - off["cells"]
    assert extra > 10_000 and extra <= 1.5 * info["extra_cells"]                                                      # an upper bound: the cap holds
    assert est["cells"] > 1.2 * meshplan.evaluate(pr, *(lambda a: (dict(a[0], sizing_methods={}), a[1]))(meshplan.base_inputs(pr)))["cells"]   # the planner sees it
    assert 0.5 < st["cells"] / est["cells"] < 1.6                                                                     # the whole estimate (measured: 0.5 to 1.2)


def test_curvature_sizing_refines_the_curved_faces_and_the_wake_box_the_air_behind(workspace, tmp_path):
    step = _step(tmp_path, lambda o: (o.addCylinder(0, 0, 0, 0, 0, 30, 20), o.addCylinder(60, 0, 0, 0, 0, 30, 4), o.addBox(-30, -30, -6, 130, 60, 6)))
    pr = _flow_project(workspace, step)
    base = _mesh(pr)
    small = lambda c: (np.hypot(c[:, 0] - 0.06, c[:, 1]) < 0.0045) & (c[:, 2] > 0.001)                             # the side of the small cylinder
    e_off, _n = _edge_len_at(pr, small)
    pr.set_mesh(sizing_methods={"curvature": {"elements": 40}})
    st = _mesh(pr)
    e_on, _n = _edge_len_at(pr, small)
    info = st["sizing_methods"]["curvature"]
    assert info["applied"] and info["cells_per_circle"] == 40 and e_on < 0.8 * e_off and st["cells"] > base["cells"]
    assert st["cells"] - base["cells"] < 2.0 * info["extra_cells"] + 2000                                             # (measured 0.55 to 0.74 of the estimate)
    # the wake box: cells behind the body as fine as asked; the stats and the planner both have the box
    pr.set_mesh(sizing_methods={"curvature": False, "wake": {"size_factor": 2.0, "length": 3.0}})
    ms, rc = meshplan.base_inputs(pr)
    box = [b for b in rc["boxes"] if b["name"] == "Wake"][0]
    w = (ms["sizing_plan"]["wake"])
    assert box["min"] == w["min"] and box["size"] == pytest.approx(w["size"]) and w["size"] == pytest.approx(2.0 * rc["h_surf"])
    st2 = _mesh(pr)
    assert st2["sizing_methods"]["wake"]["length"] == 3.0 and st2["cells"] > base["cells"]
    pr.set_mesh(smart=False)                                                                                          # (without smart sizing the box is a refinement region)
    ms, rc = meshplan.base_inputs(pr)
    assert rc is None and [r for r in ms["regions"] if r.get("auto") == "wake"] and not pr.data["mesh"].get("regions")  # (nothing is added to the regions you drew)
