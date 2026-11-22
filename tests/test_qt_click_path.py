"""The click path of the 3D view: what a ray crosses in the see-through air volume, and the pixel scaling (no OpenGL window needed)."""
import numpy as np

from simwb.qt.picking import FaceIndex


def _box_walls():
    """The six walls of a box from -5 to 5 m as faces 1..6 (two triangles each)."""
    walls = []
    corners = {1: (0, -5), 2: (0, 5), 3: (1, -5), 4: (1, 5), 5: (2, -5), 6: (2, 5)}
    for fid, (ax, val) in corners.items():
        a, b = [k for k in range(3) if k != ax]
        v = []
        for p, q in ((-5, -5), (5, -5), (5, 5), (-5, 5)):
            pt = [0.0, 0.0, 0.0]
            pt[ax], pt[a], pt[b] = val, p, q
            v.append(pt)
        walls.append((fid, v, [[0, 1, 2], [0, 2, 3]], "farfield"))
    return walls


def test_a_ray_through_the_box_reports_every_wall_nearest_first():
    idx = FaceIndex()
    idx.build([], _box_walls())
    hits = idx.air.hits([-20.0, 0.5, 0.5], [20.0, 0.5, 0.5])      # straight through the -x and +x walls
    assert [h[0] for h in hits] == [1, 2]
    assert hits[0][2] < hits[1][2]
    assert abs(hits[0][1][0] + 5.0) < 1e-6 and abs(hits[1][1][0] - 5.0) < 1e-6


def test_a_ray_that_crosses_one_wall_gives_one_entry_and_a_miss_gives_none():
    idx = FaceIndex()
    idx.build([], _box_walls())
    assert [h[0] for h in idx.air.hits([-20.0, 0.5, 0.5], [0.0, 0.5, 0.5])] == [1]
    assert idx.air.hits([-20.0, 9.0, 9.0], [20.0, 9.0, 9.0]) == []


def test_each_hit_is_the_nearest_crossing_of_its_face():
    idx = FaceIndex()
    idx.build([], _box_walls())
    hits = idx.air.hits([1.0, 1.0, 1.0], [30.0, 3.0, -2.0])
    assert len({h[0] for h in hits}) == len(hits)
    assert np.all(np.diff([h[2] for h in hits]) >= 0)
