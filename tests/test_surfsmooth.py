"""Smoothing of vortex surfaces (issue #61): the surface is smoothed for display with a non-shrinking filter; the data is not touched.
Synthetic fields on an irregular tetrahedral mesh, no solver."""
import base64

import numpy as np
import pytest

from simwb import surfsmooth as S
from simwb.results import ResultSet, boundary_from_tets


@pytest.fixture(scope="module")
def mesh():
    from scipy.spatial import Delaunay
    n = 22
    g = np.linspace(-1, 1, n)
    rng = np.random.default_rng(0)
    nodes = np.array(np.meshgrid(g, g, g, indexing="ij")).reshape(3, -1).T
    nodes = nodes + rng.uniform(-0.3, 0.3, nodes.shape) * (g[1] - g[0])           # (an irregular mesh, cells of about 0.1)
    tets = Delaunay(nodes).simplices
    tris, cells = boundary_from_tets(tets)
    return nodes, tets, tris, cells, rng


def _iso(mesh, q, smooth=0):
    nodes, tets, tris, cells, _ = mesh
    rs = ResultSet(nodes, tets, np.zeros(len(tets)), ["air"], {"Q": ("node", q, "1/s2")}, tris, cells, np.zeros(len(tris)), ["farfield"])
    return rs.iso("Q", 0.5, "Q", smooth=smooth)


def _arrays(p):
    pos = np.frombuffer(base64.b64decode(p["positions"]), np.float32).reshape(-1, 3).astype(float)
    idx = np.frombuffer(base64.b64decode(p["indices"]), np.uint32).reshape(-1, 3).astype(int)
    return pos, idx


def _sphere(mesh, noise):
    nodes, *_, rng = mesh
    r = np.linalg.norm(nodes, axis=1)
    return 1.0 - (r / 0.6) ** 2 + noise * rng.standard_normal(len(nodes)), r


def _tube(mesh, noise):
    nodes, *_, rng = mesh
    rho = np.hypot(np.hypot(nodes[:, 0], nodes[:, 1]) - 0.55, nodes[:, 2])          # a thin torus, tube radius 0.14 = 1.5 cells
    return 1.0 - (rho / 0.2) ** 2 + noise * rng.standard_normal(len(nodes))


def test_the_surface_is_wound_one_way_so_it_has_a_volume_and_smooth_normals(mesh):
    """Marching tetrahedra used to wind the triangles both ways (half faced in, half out): no consistent normals, no volume."""
    q, _ = _sphere(mesh, 0.0)
    pos, idx = _arrays(_iso(mesh, q))
    a, b, c = pos[idx[:, 0]], pos[idx[:, 1]], pos[idx[:, 2]]
    tetvol = np.einsum("ij,ij->i", a, np.cross(b, c)) / 6                           # (the origin is inside: all positive when all face outwards)
    assert (tetvol > 0).all()
    m = S.measure(pos, idx)
    assert m["closed"] and abs(m["volume"] - 4 / 3 * np.pi * (0.6 * np.sqrt(0.5)) ** 3) / m["volume"] < 0.06


def test_surface_smoothing_removes_the_roughness_and_keeps_the_size(mesh):
    q, _ = _sphere(mesh, 0.04)
    p = _iso(mesh, q)
    pos0, idx = _arrays(p)
    rough = lambda pos: np.linalg.norm(pos, axis=1).std() / np.linalg.norm(pos, axis=1).mean()  # noqa: E731
    out, st = S.smooth_payload(p, 40)
    pos1, idx1 = _arrays(out)
    assert (idx1 == idx).all() and out["n_tris"] == p["n_tris"] and out["values"] == p["values"]     # (same triangles, same colours: only the positions move)
    assert rough(pos1) < 0.85 * rough(pos0)                                                          # rounder (the noise left is the wobble over several cells)
    assert rough(_arrays(S.smooth_payload(p, 100)[0])[0]) < 0.65 * rough(pos0)                      # and rounder still at the strongest setting
    assert st["closed"] and abs(st["volume_change"]) < 0.01 and abs(st["area_change"]) < 0.06        # but the same size
    m0, m1 = S.measure(pos0, idx), S.measure(pos1, idx)
    assert abs(m1["volume"] / m0["volume"] - 1) < 0.01


def test_a_thin_vortex_keeps_its_volume_at_every_strength(mesh):
    """The reason for a non-shrinking filter: a thin tube (a cell and a half across) must not get thinner."""
    p = _iso(mesh, _tube(mesh, 0.02))
    v0 = S.measure(*_arrays(p))["volume"]
    for strength, tolerance in ((10, 0.005), (25, 0.005), (40, 0.01), (50, 0.01), (75, 0.02), (100, 0.03)):      # (the default is 40)
        out, st = S.smooth_payload(p, strength)
        assert abs(S.measure(*_arrays(out))["volume"] / v0 - 1) < tolerance, (strength, st)


def test_smoothing_the_data_first_shrinks_a_thin_vortex_which_is_why_it_is_off_by_default(mesh):
    """The older 'smoothing' averages the FIELD before the surface is made: one pass takes a large share of a thin vortex away."""
    nodes, tets, tris, cells, rng = mesh
    q = _tube(mesh, 0.02)
    v = [S.measure(*_arrays(_iso(mesh, q, smooth=k)))["volume"] for k in (0, 1)]
    assert v[1] < 0.8 * v[0]
    out, _ = S.smooth_payload(_iso(mesh, q), 100)
    assert S.measure(*_arrays(out))["volume"] > 0.97 * v[0]


def test_no_smoothing_returns_the_payload_unchanged(mesh):
    q, _ = _sphere(mesh, 0.04)
    p = _iso(mesh, q)
    out, st = S.smooth_payload(p, 0)
    assert out is p and st["passes"] == 0
    assert S.smooth_payload({}, 50)[0] == {} and S.passes_for(0) == 0 and S.passes_for(100) == S.MAX_PASSES and S.passes_for(0.5) >= S.MIN_PASSES


def test_the_cut_edge_of_an_open_surface_stays_where_it_is(mesh):
    """A surface cut at the region box or at the wall gap is open: its border must not slide (it meets other surfaces there)."""
    q, _ = _sphere(mesh, 0.0)
    p = _iso(mesh, q)
    pos, idx = _arrays(p)
    keep = pos[idx].mean(1)[:, 2] < 0.1                                                           # the lower part of the sphere: open at the top
    used, inv = np.unique(idx[keep].ravel(), return_inverse=True)
    open_p = dict(p, positions=base64.b64encode(pos[used].astype(np.float32).tobytes()).decode(),
                  indices=base64.b64encode(inv.astype(np.uint32).tobytes()).decode(), n_tris=int(keep.sum()))
    out, st = S.smooth_payload(open_p, 60)
    a, b = _arrays(open_p)[0], _arrays(out)[0]
    e = np.sort(np.concatenate([inv.reshape(-1, 3)[:, [0, 1]], inv.reshape(-1, 3)[:, [1, 2]], inv.reshape(-1, 3)[:, [2, 0]]]), axis=1)
    u, cnt = np.unique(e[:, 0] * (len(used) + 1) + e[:, 1], return_counts=True)
    border = np.unique(np.concatenate([u[cnt == 1] // (len(used) + 1), u[cnt == 1] % (len(used) + 1)]))
    assert len(border) > 10 and not st["closed"] and np.abs(a[border] - b[border]).max() < 1e-5
