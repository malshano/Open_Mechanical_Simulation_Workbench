"""Backend of the results tab: displacement of structural surfaces, the line plot, the residual history."""
import base64

import numpy as np
import pytest

from simwb import project as projmod
from simwb import resample
from simwb.results import ResultSet, boundary_from_tets


def _rs(fields):
    # two tets filling the unit cube corner region: a simple, valid mesh
    nodes = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 1, 1]], float)
    tets = np.array([[0, 1, 2, 3], [1, 2, 3, 4]])
    tris, cells = boundary_from_tets(tets)
    return ResultSet(nodes, tets, np.zeros(2, int), ["all"], fields, tris, cells, np.zeros(len(tris), int), ["wall"])


def _decode(p, key, dtype=np.float32):
    return np.frombuffer(base64.b64decode(p[key]), dtype)


def test_static_fields_all_move_with_the_deformation():
    u = np.tile([1.0, 0.0, 0.0], (5, 1)) * 2.0     # mm
    rs = _rs({"Von Mises stress": ("node", np.arange(5, dtype=np.float32), "MPa"), "Total deformation": ("node", u, "mm")})
    for f in ("Von Mises stress", "Total deformation"):
        p = rs.surface(f)
        assert p["disp_kind"] == "static"
        d = _decode(p, "disp").reshape(-1, 3)
        assert np.allclose(d[:, 0], 2e-3)            # metres, drawn larger by the viewer


def test_mode_shape_moves_with_its_own_field_only():
    shape = np.random.default_rng(1).normal(size=(5, 3)).astype(np.float32)
    rs = _rs({"Mode 1": ("node", shape, "relative"), "Temperature": ("node", np.zeros(5), "°C")})
    assert rs.surface("Mode 1")["disp_kind"] == "mode"
    assert "disp" not in rs.surface("Temperature")


def test_line_through_the_mesh_and_outside_it():
    rs = _rs({"T": ("node", np.array([0, 10, 20, 30, 40.0]), "°C")})
    r = resample.line_payload(rs, "T", [0.1, 0.1, 0.1], [0.1, 0.1, 5.0], n=50)
    assert len(r["s"]) == 50 and r["unit"] == "°C"
    assert r["values"][0] is not None and r["values"][-1] is None      # leaves the mesh
    assert 0 < r["n_inside"] < 50
    assert r["min"] <= r["mean"] <= r["max"]
    with pytest.raises(ValueError):
        resample.line_payload(rs, "T", [0, 0, 0], [0, 0, 0])
    with pytest.raises(ValueError):
        resample.line_payload(rs, "nope", [0, 0, 0], [1, 1, 1])


def test_residual_history_is_thinned_and_keeps_the_worst_equations():
    n = 1000
    h = {"iters": list(range(1, n + 1)),
         "residuals": {"air/p": [1.0 / i for i in range(1, n + 1)], "air/Ux": [1e-6] * n, "air/k": [None] * n}}
    out = projmod._residual_history(h, points=100, keep=1)
    assert len(out["iters"]) <= 100 and out["iters"][-1] == n
    assert list(out["series"]) == ["air/p"]                       # the highest at the end; empty series dropped
    assert projmod._residual_history({"iters": [1], "residuals": {}}) is None
