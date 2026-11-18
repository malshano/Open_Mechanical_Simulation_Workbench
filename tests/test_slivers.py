import numpy as np
import pytest

from simwb import slivers


def _cube_with_cluster(seed=1):
    """Delaunay mesh of random points in a unit cube plus three corner points within 2 micrometres of each other
    (a collapsed cell like the ones a CAD micro-edge gives), boundary triangles from the convex hull."""
    scipy_spatial = pytest.importorskip("scipy.spatial")
    rng = np.random.default_rng(seed)
    pts = [np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 1, 0], [1, 0, 1], [0, 1, 1], [1, 1, 1.0]]),
           rng.uniform(0.05, 0.95, size=(120, 3))]
    base = np.array([1.0, 0.5, 0.5])
    pts.append(base + np.array([[0, 0, 0], [2e-6, 0, 0], [0, 2e-6, 0]]))
    p = np.vstack(pts)
    d = scipy_spatial.Delaunay(p)
    tets = d.simplices.astype(np.int64)
    v = slivers._signed_vol(p, tets)
    tets[v < 0] = tets[v < 0][:, [0, 2, 1, 3]]
    keep = np.abs(slivers._signed_vol(p, tets)) > 0
    tets = tets[keep]
    hull = scipy_spatial.ConvexHull(p)
    return p, tets, hull.simplices.astype(np.int64)


def test_a_collapsed_cell_is_repaired_and_the_mesh_stays_valid():
    p, tets, tris = _cube_with_cluster()
    q0 = slivers.tet_quality(p, tets)
    assert q0.min() < 1e-4                                           # a real sliver to start with
    vol0 = np.abs(slivers._signed_vol(p, tets)).sum()
    out = slivers.collapse_slivers(p, tets, np.zeros(len(tets), np.int32), tris, np.zeros(len(tris), np.int32),
                                   np.zeros((0, 3), np.int64), np.zeros(0, np.int32))
    t2, tv2, tr2, ts2, zt2, zz2, n = out
    assert n >= 1 and len(tv2) == len(t2) and len(ts2) == len(tr2)
    q1 = slivers.tet_quality(p, t2)
    assert q1.min() > q0.min() * 100                                 # the worst cell is gone
    assert (slivers._signed_vol(p, t2) > 0).all()                    # nothing flipped
    assert abs(np.abs(slivers._signed_vol(p, t2)).sum() - vol0) < 1e-5 * vol0   # same volume
    # the boundary is still a closed surface: every edge of the boundary triangles is shared by two of them
    e = np.sort(np.vstack([tr2[:, [0, 1]], tr2[:, [1, 2]], tr2[:, [2, 0]]]), axis=1)
    _, cnt = np.unique(e, axis=0, return_counts=True)
    assert (cnt == 2).all()


def test_a_good_mesh_is_left_alone():
    p, tets, tris = _cube_with_cluster()
    p = p[:-3]
    keep = (tets < len(p)).all(1)
    tets = tets[keep]
    out = slivers.collapse_slivers(p, tets, np.zeros(len(tets), np.int32), tris[(tris < len(p)).all(1)],
                                   np.zeros(int((tris < len(p)).all(1).sum()), np.int32),
                                   np.zeros((0, 3), np.int64), np.zeros(0, np.int32), q_min=1e-9, a_min=1e-9)
    assert out[-1] == 0 and len(out[0]) == len(tets)


def test_a_flat_cap_sliver_with_regular_edges_is_repaired():
    """A node 8 micrometres above the face of a tetrahedron (edges all about 0.3, height 8e-6) is a flat sliver that
    no short edge shows; collapsing its apex onto a base node removes it without flipping a neighbour."""
    scipy_spatial = pytest.importorskip("scipy.spatial")
    rng = np.random.default_rng(3)
    corners = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 1, 0], [1, 0, 1], [0, 1, 1], [1, 1, 1.0]])
    pts = np.vstack([corners, rng.uniform(0.05, 0.95, size=(150, 3))])
    d0 = scipy_spatial.Delaunay(pts)
    tet = d0.simplices[np.argmax(slivers.tet_quality(pts, d0.simplices) * (d0.simplices.max(1) > 7))]
    a, b, c = pts[tet[0]], pts[tet[1]], pts[tet[2]]
    n = np.cross(b - a, c - a)
    n /= np.linalg.norm(n)
    apex = (a + b + c) / 3 + 8e-6 * n                         # 8 micrometres above the middle of a face
    p = np.vstack([pts, apex])
    d = scipy_spatial.Delaunay(p)
    tets = d.simplices.astype(np.int64)
    v = slivers._signed_vol(p, tets)
    tets[v < 0] = tets[v < 0][:, [0, 2, 1, 3]]
    tets = tets[np.abs(slivers._signed_vol(p, tets)) > 0]
    hull = scipy_spatial.ConvexHull(p)
    tris = hull.simplices.astype(np.int64)
    q0 = slivers.tet_quality(p, tets)
    assert q0.min() < 1e-2
    out = slivers.collapse_slivers(p, tets, np.zeros(len(tets), np.int32), tris, np.zeros(len(tris), np.int32),
                                   np.zeros((0, 3), np.int64), np.zeros(0, np.int32))
    t2, n_done = out[0], out[-1]
    assert n_done >= 1
    assert slivers.tet_quality(p, t2).min() >= 0.02 > q0.min() * 2          # the flat sliver is gone (q_min default 0.02)
    assert (slivers._signed_vol(p, t2) > 0).all()
