"""The estimate of the extra cells of the edge refinement, the cap that is built on it, and the settings rules of #50 (no Qt): the estimate over all the edges
together (not edge by edge), its calibration against real meshes, the cap holding on a real mesh, a setting that is only part of a dictionary, an 'off' that
stays off, and the edge search (a file of its own, stopped when the geometry changes)."""
import threading
import time

import numpy as np
import pytest

import simwb.project as P_
from simwb import edgepreview, gmsh_worker as G

EXAMPLES = __import__("pathlib").Path(__file__).resolve().parents[1] / "examples"


def line(p0, p1, n=20):
    return np.linspace(p0, p1, n).tolist()


# ---------------------------------------------------------------------------------------------------------------------------------- the estimator
def test_the_estimate_counts_close_edges_once():
    """Two edges on top of each other (the two rims of a thin plate) share their tube: the estimate over all the edges is about that of one, not twice it."""
    one = [line([0, 0, 0], [1, 0, 0])]
    two = [line([0, 0, 0], [1, 0, 0]), line([0, 0, 1e-4], [1, 0, 1e-4])]
    far = [line([0, 0, 0], [1, 0, 0]), line([0, 5, 0], [1, 5, 0])]
    e1 = G.edge_cells_union(one, [90], 0.002, 0.012, 1.25)
    e2 = G.edge_cells_union(two, [90, 90], 0.002, 0.012, 1.25)
    e3 = G.edge_cells_union(far, [90, 90], 0.002, 0.012, 1.25)
    assert e2 == pytest.approx(e1, rel=0.08)
    assert e3 == pytest.approx(2 * e1, rel=0.08)                                 # (edges far apart add up)
    assert G.edge_cells_union(one, [90], 0.002, 0.012, 1.25) == e1               # (the same answer every time)


def test_the_estimate_of_one_edge_is_the_closed_form_times_the_air_share_and_the_calibration():
    h_e, h_w, g = 0.002, 0.012, 1.25
    est = G.edge_cells_union([line([0, 0, 0], [2, 0, 0], 40)], [90.0], h_e, h_w, g)
    closed = G.edge_cells_estimate(2.0, h_e, h_w, g) * (1 - 90 / 360) * G.EDGE_CELLS_CALIBRATION
    assert est == pytest.approx(closed, rel=0.06)
    knife = G.edge_cells_union([line([0, 0, 0], [2, 0, 0], 40)], [5.0], h_e, h_w, g)
    assert knife > est                                                          # (a knife edge has air nearly all round it)
    fine = G.edge_cells_union([line([0, 0, 0], [2, 0, 0], 40)], [90.0], 0.001, h_w, g)
    assert fine > est > G.edge_cells_union([line([0, 0, 0], [2, 0, 0], 40)], [90.0], 0.004, h_w, g) > 0


def test_the_plan_coarsens_the_edge_cells_until_the_estimate_fits_the_cap():
    edges = [(1, 20.0, 1.0, line([0, 0, 0], [1, 0, 0]))]
    free = G.edge_refine_plan(edges, 0.01, 1.0, 1.2, 12.0, cap=5e6)
    assert free["applied"] and not free["capped"] and free["factor_used"] == pytest.approx(12.0) and free["estimated_extra_cells"] > 20000
    capped = G.edge_refine_plan(edges, 0.01, 1.0, 1.2, 12.0, cap=20000)
    assert capped["applied"] and capped["capped"] and 2 < capped["factor_used"] < 12
    assert 15000 < capped["estimated_extra_cells"] <= 20000                     # (it lands just under the cap, not far under it)
    none = G.edge_refine_plan(edges, 0.01, 1.0, 1.2, 12.0, cap=10)
    assert not none["applied"] and none["reason"] == "cap_too_low"


# ---------------------------------------------------------------------------------------------------------------------------------- settings rules
@pytest.fixture
def aero(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMWB_HOME", str(tmp_path / "ws"))
    import importlib

    importlib.reload(P_)
    pr = P_.create_project("Wing", "external_aero")
    pr.import_step(EXAMPLES / "airplane_wing.step").wait(300)
    pr.set_settings(speed=30)
    pr.set_mesh(preset="quick")
    return pr


def test_a_partial_dictionary_keeps_the_other_values(aero):
    aero.set_mesh(edge_refinement={"factor": 12, "max_cells": 90000, "max_angle": 100})
    aero.set_mesh(edge_refinement={"max_cells": 5000})
    assert aero.data["mesh"]["edge_refine"] == {"factor": 12.0, "max_cells": 5000.0, "max_angle": 100.0}
    aero.set_mesh(edge_refinement={"max_angle": 60})
    assert aero.data["mesh"]["edge_refine"] == {"factor": 12.0, "max_cells": 5000.0, "max_angle": 60.0}
    aero.set_mesh(edge_refinement=True)                                          # (on again: nothing is reset)
    assert aero.data["mesh"]["edge_refine"]["factor"] == 12.0
    with pytest.raises(ValueError):
        aero.set_mesh(edge_refinement={"max_cells": 10})
    assert aero.data["mesh"]["edge_refine"]["max_cells"] == 5000.0               # (a refused call changes nothing)
    aero.set_mesh(edge_refinement=False)
    aero.set_mesh(smart=True)
    aero.set_mesh(edge_refinement={"max_cells": 8000})                           # (a first dictionary without a factor: the default, 6)
    assert aero.data["mesh"]["edge_refine"] == {"factor": 6.0, "max_cells": 8000.0}


def test_off_stays_off_whatever_smart_sizing_does(aero):
    aero.set_mesh(edge_refinement=False)                                         # off with smart sizing OFF ...
    aero.set_mesh(smart=True)                                                    # ... then smart sizing on ...
    assert not edgepreview.settings(aero)["on"]                                  # ... does not switch it on
    aero.set_mesh(smart=False)
    aero.set_mesh(smart=True)
    assert not edgepreview.settings(aero)["on"]
    aero.set_mesh(edge_refinement=True)
    assert edgepreview.settings(aero)["on"] and "no_edge_refine" not in aero.data["mesh"]
    aero.set_mesh(edge_refinement=False)
    assert "edge_refine" not in aero.data["mesh"] and aero.data["mesh"]["no_edge_refine"] is True


# ---------------------------------------------------------------------------------------------------------------------------------- the edge search
class _Sleeper:
    """Stands in for the worker program: a process that lives for a while."""

    made: list = []

    def __init__(self, args, **kw):
        import subprocess
        import sys

        self.args = args
        self.proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], stdout=subprocess.PIPE, text=True)
        _Sleeper.made.append(self)

    def close(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()


def test_every_search_has_its_own_job_file_and_a_new_geometry_stops_the_old_search(aero, monkeypatch):
    from simwb import memlimit

    monkeypatch.setattr(memlimit, "LimitedPopen", _Sleeper)
    _Sleeper.made.clear()
    edgepreview.ensure_scan(aero)
    t0 = time.time()
    while not _Sleeper.made and time.time() - t0 < 10:
        time.sleep(0.05)
    first = _Sleeper.made[0]
    assert first.proc.poll() is None and edgepreview.scan_state(aero)["state"] == "running"
    aero.data["geometry"]["updated"] = "a new geometry"                           # (the geometry was imported again under the search)
    edgepreview.ensure_scan(aero)
    t0 = time.time()
    while len(_Sleeper.made) < 2 and time.time() - t0 < 10:
        time.sleep(0.05)
    assert len(_Sleeper.made) == 2 and first.proc.poll() is not None             # the old search was stopped, the new one runs
    assert _Sleeper.made[1].proc.poll() is None
    assert first.args[-1] != _Sleeper.made[1].args[-1]                           # (each has a job file of its own: one finishing cannot delete the other's)
    edgepreview.cancel_all_scans()                                               # (what the program does when it closes)
    t0 = time.time()
    while _Sleeper.made[1].proc.poll() is None and time.time() - t0 < 10:
        time.sleep(0.05)
    assert _Sleeper.made[1].proc.poll() is not None
    edgepreview.cancel_all_scans()


# ---------------------------------------------------------------------------------------------------------------------------------- against real meshes
def test_the_estimate_and_the_cap_hold_on_real_meshes(aero):
    """The example wing with smart sizing (so that the mesh without the option is the same mesh but for the edges), quick preset, gmsh only: the estimate before
    meshing is within 30 % of the cells the option really adds (mesh with it less mesh without it), the cap lands the real extra cells at or under 1.25 x the cap,
    and the count made in the mesh itself agrees with the real number."""
    aero.set_mesh(smart=True, edge_refinement=False)
    aero.generate_mesh().wait(600)
    off = aero.data["mesh"]["stats"]["cells"]
    assert aero.data["mesh"]["stats"]["edge_refine"] is None

    aero.set_mesh(edge_refinement={"factor": 6, "max_cells": 5_000_000})
    pv = edgepreview.preview(aero, scan=True, wait=True)
    aero.generate_mesh().wait(600)
    st = aero.data["mesh"]["stats"]
    real = st["cells"] - off
    est = pv["plan"]["estimated_extra_cells"]
    assert st["edge_refine"]["estimated_extra_cells"] == pytest.approx(est)       # (the preview is the mesher's own plan)
    assert abs(est - real) <= 0.30 * real, (est, real)
    meas = edgepreview.measure(aero, wait=True)["result"]
    assert abs(meas["extra_cells"] - real) <= 0.30 * real, (meas, real)           # (the number the panel shows after meshing)

    cap = 30_000
    aero.set_mesh(edge_refinement={"factor": 12, "max_cells": cap})
    pv = edgepreview.preview(aero, scan=True, wait=True)
    assert pv["plan"]["capped"] and pv["plan"]["estimated_extra_cells"] <= cap and pv["plan"]["factor_used"] < 12
    aero.generate_mesh().wait(600)
    st = aero.data["mesh"]["stats"]
    real = st["cells"] - off
    assert st["edge_refine"]["capped"] and st["edge_refine"]["factor_used"] == pytest.approx(pv["plan"]["factor_used"])
    assert 0.5 * cap <= real <= 1.25 * cap, real
