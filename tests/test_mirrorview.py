"""The whole model from the solved part (simwb/mirrorview.py): the numpy functions, without a window. Fixes #63."""
import base64

import numpy as np
import pytest

from simwb import mirrorview as mv
from simwb import symmetry as sym


def enc(a):
    return base64.b64encode(np.ascontiguousarray(a).tobytes()).decode()


def dec(s, dt, cols=1):
    a = np.frombuffer(base64.b64decode(s), dt)
    return a.reshape(-1, cols) if cols > 1 else a


def box_payload(lo=(0.2, 0.1, 0.3), hi=(1.0, 0.6, 0.9), per="vertex", disp=False):
    """A closed box as an outward-wound surface payload: the winding is what the tests judge by (signed volume > 0)."""
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    pts = np.array([[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0], [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]], np.float32)
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]       # (each seen from outside: counter-clockwise)
    tris = np.array([t for q in quads for t in ((q[0], q[1], q[2]), (q[0], q[2], q[3]))], np.uint32)
    vals = (np.arange(8) * 1.5 if per == "vertex" else np.arange(len(tris)) * 2.0).astype(np.float32)
    p = {"positions": enc(pts), "indices": enc(tris), "values": enc(vals), "per": per, "n_tris": len(tris), "min": float(vals.min()), "max": float(vals.max()), "unit": "Pa"}
    if disp:
        p["disp"] = enc(np.array([[1, 2, 3]] * 8, np.float32))
    return p


def volume_of(p):
    """Signed volume of a closed surface by the divergence theorem: positive when the triangles are wound outward."""
    pos, idx = dec(p["positions"], np.float32, 3).astype(float), dec(p["indices"], np.uint32, 3).astype(int)
    a, b, c = pos[idx[:, 0]], pos[idx[:, 1]], pos[idx[:, 2]]
    return float(np.sum(np.einsum("ij,ij->i", a, np.cross(b, c))) / 6.0)


def plane(point, normal, name="P"):
    return sym.make_plane(point, normal, name)


def test_the_box_helper_is_wound_outward():
    assert volume_of(box_payload()) == pytest.approx(0.8 * 0.5 * 0.6, rel=1e-5)


def test_one_reflection_moves_the_points_turns_the_winding_and_copies_the_scalars():
    p = box_payload()
    pl = plane([0, 0.5, 0], [0, 1, 0])
    R, t = mv.image_transforms([pl])[0]
    m = mv.mirror_surface(p, R, t)
    pos, mpos = dec(p["positions"], np.float32, 3), dec(m["positions"], np.float32, 3)
    assert np.allclose(mpos[:, 0], pos[:, 0]) and np.allclose(mpos[:, 2], pos[:, 2])
    assert np.allclose(mpos[:, 1], 1.0 - pos[:, 1])                                   # (y' = 2 * 0.5 - y)
    assert volume_of(p) > 0 and volume_of(m) > 0                                      # the normals of the copy still point out of the body
    assert volume_of(m) == pytest.approx(volume_of(p), rel=1e-5)
    assert not np.array_equal(dec(p["indices"], np.uint32), dec(m["indices"], np.uint32))   # (the corner order really was turned)
    assert np.array_equal(dec(m["values"], np.float32), dec(p["values"], np.float32))     # a scalar is the same on the other side
    f = mv.mirror_surface(box_payload(per="face"), R, t)
    assert np.array_equal(dec(f["values"], np.float32), np.arange(12) * 2.0) and f["per"] == "face"


def test_the_deformation_vectors_are_reflected():
    p = box_payload(disp=True)
    R, t = mv.image_transforms([plane([0, 0.5, 0], [0, 1, 0])])[0]
    d = dec(mv.mirror_surface(p, R, t)["disp"], np.float32, 3)
    assert np.allclose(d, [[1, -2, 3]] * 8)


def test_a_vector_loses_its_component_normal_to_the_plane_and_keeps_the_rest():
    n = np.array([1.0, 2.0, 2.0]) / 3.0
    pl = plane([0.3, 0.1, 0.2], n)
    R, _ = mv.image_transforms([pl])[0]
    v = np.array([[0.7, -1.2, 2.5], [3.0, 0.0, 0.0]])
    out = mv.reflect_vectors(v, R)
    assert np.allclose(out, v - 2 * np.outer(v @ n, n))                                # v - 2 (v . n) n
    assert np.allclose(out @ n, -(v @ n))                                              # the normal component changes sign
    tangent = v - np.outer(v @ n, n)
    assert np.allclose(out - np.outer(out @ n, n), tangent)                            # the tangential part is untouched
    assert np.allclose(np.linalg.norm(out, axis=1), np.linalg.norm(v, axis=1))         # (so a speed is the same)


def test_points_reflect_through_a_slanted_plane_with_a_shift():
    n = np.array([0.0, 3.0, 4.0]) / 5.0
    pl = plane([0, 1.0, 1.0], n)
    R, t = mv.image_transforms([pl])[0]
    x = np.array([[0.2, 0.4, 2.0]])
    y = mv.reflect_points(x, R, t)
    assert np.allclose(sym.signed_distance(pl, y), -sym.signed_distance(pl, x))        # the other side, as far from the plane
    assert np.allclose(mv.reflect_points(y, R, t), x)                                  # twice is the identity


def test_two_planes_give_three_images_the_quarter_model_whole():
    p = box_payload(lo=(0.1, 0.1, 0.0), hi=(0.9, 0.7, 1.0))
    pls = [plane([0, 0, 0], [1, 0, 0], "x"), plane([0, 0, 0], [0, 1, 0], "y")]
    w = mv.whole_surface(p, pls)
    assert w["n_tris"] == 4 * p["n_tris"] and w["mirror"]["copies"] == 4 and w["mirror"]["solved_tris"] == p["n_tris"]
    pos = dec(w["positions"], np.float32, 3)
    n = len(dec(p["positions"], np.float32, 3))
    quarters = {(bool(c[0] > 0), bool(c[1] > 0)) for c in pos.reshape(4, n, 3).mean(1)}
    assert quarters == {(True, True), (False, True), (True, False), (False, False)}   # one box in each quadrant
    idx = dec(w["indices"], np.uint32, 3).reshape(4, -1, 3)
    for k in range(4):                                                                 # every copy closed and outward, also the one that was reflected twice
        sub = {"positions": enc(pos.reshape(4, n, 3)[k]), "indices": enc(idx[k] - np.uint32(k * n))}
        assert volume_of(sub) > 0
    # "mirror about the first plane, then the result about the second" is the same four boxes
    first = mv.whole_surface(p, [pls[0]])
    second = mv.whole_surface(first, [pls[1]])
    assert np.allclose(np.sort(dec(second["positions"], np.float32), axis=None), np.sort(pos, axis=None), atol=1e-6)


def test_the_mirror_is_a_payload_of_the_same_kind_and_nothing_without_planes():
    p = box_payload()
    assert mv.whole_surface(p, []) is p
    w = mv.whole_surface(p, [plane([0, 0.5, 0], [0, 1, 0])])
    assert w["min"] == p["min"] and w["max"] == p["max"] and w["unit"] == "Pa" and w["per"] == "vertex"
    assert len(dec(w["values"], np.float32)) == 16 and dec(w["indices"], np.uint32).max() == 15
    assert mv.whole_surface({"n_tris": 0}, [plane([0, 0, 0], [0, 1, 0])]) == {"n_tris": 0}
    assert mv.side_of_triangle(w, 3) == 0 and mv.side_of_triangle(w, 12) == 1 and mv.side_of_triangle(w, 23) == 1


def test_a_point_of_a_copy_maps_back_to_the_solved_point():
    p = box_payload()
    pls = [plane([0, 0.4, 0], [0, 1, 0]), plane([0.1, 0, 0], [1, 0, 0])]
    w = mv.whole_surface(p, pls)
    pos = dec(w["positions"], np.float32, 3).astype(float)
    n = len(pos) // 4
    for side in (1, 2, 3):
        back = [mv.to_solved(pos[side * n + i], w["mirror"], side) for i in range(n)]
        assert np.allclose(back, pos[:n], atol=1e-5)


def test_arrows_and_particle_paths_are_reflected():
    pos = np.array([[0.5, 0.2, 0.1], [0.6, 0.4, -0.2]], np.float32)
    vec = np.array([[1.0, 2.0, 3.0], [4.0, -1.0, 0.5]], np.float32)
    a = {"n": 2, "h": 0.1, "positions": enc(pos), "vectors": enc(vec), "min": 1.0, "max": 5.0, "p95": 4.0, "unit": "m/s"}
    w = mv.mirror_vectors(a, [plane([0, 0, 0], [0, 1, 0])])
    assert w["n"] == 4
    assert np.allclose(dec(w["positions"], np.float32, 3)[2:], pos * [1, -1, 1])
    assert np.allclose(dec(w["vectors"], np.float32, 3)[2:], vec * [1, -1, 1])         # the y component (normal to the plane) flips
    P = np.full((2, 3, 3), np.nan, np.float32)
    P[0] = [[0, 1, 0], [1, 1, 0], [2, 2, 1]]
    P[1, :2] = [[0, 2, 0], [1, 3, 0]]
    S = np.arange(6, dtype=np.float32).reshape(2, 3)
    q = mv.mirror_paths({"n": 2, "steps": 3, "paths": enc(P), "speed": enc(S), "min": 0, "max": 5}, [plane([0, 0, 0], [0, 1, 0])])
    Q = dec(q["paths"], np.float32).reshape(4, 3, 3)
    assert q["n"] == 4 and np.allclose(Q[2][:, 1], -P[0][:, 1]) and np.isnan(Q[3, 2]).all()      # (a lost particle stays lost)
    assert np.array_equal(dec(q["speed"], np.float32).reshape(4, 3)[2], S[0])


def test_the_volume_grid_is_flipped_and_moved_for_an_axis_plane_and_skipped_for_a_slanted_one():
    nx, ny, nz = 4, 3, 2
    data = np.arange(nx * ny * nz, dtype=np.uint8).reshape(nz, ny, nx) + 1
    v = {"dims": [nx, ny, nz], "origin": [0.0, 0.2, 0.0], "h": 0.1, "log": False, "lo": 0.0, "hi": 1.0, "unit": "m/s", "data": enc(data),
         "box": [0.0, 0.2, 0.0, 0.3, 0.4, 0.1]}
    out, skipped = mv.mirror_volumes(v, [plane([0, 0, 0], [0, 1, 0])])
    assert skipped == 0 and len(out) == 1
    m = out[0]
    assert np.allclose(m["origin"], [0.0, -0.4, 0.0]) and np.allclose(m["box"], [0.0, -0.4, 0.0, 0.3, -0.2, 0.1])
    assert np.array_equal(dec(m["data"], np.uint8).reshape(nz, ny, nx), data[:, ::-1, :])     # (y is the middle index of the (z, y, x) array)
    out, skipped = mv.mirror_volumes(v, [plane([0, 0, 0], [0, 1, 1])])
    assert out == [] and skipped == 1


def test_the_camera_box_holds_the_solved_part_and_its_images():
    b = mv.mirror_box([0, 0, -1, 2, 1, 1], [plane([0, 0, 0], [0, 1, 0])])
    assert np.allclose(b, [0, -1, -1, 2, 1, 1])
    assert mv.mirror_box(None, [plane([0, 0, 0], [0, 1, 0])]) is None
    assert mv.mirror_box([0, 0, 0, 1, 1, 1], []) == [0, 0, 0, 1, 1, 1]


def zone(cx, cy, cz, axis="x", length=0.6, r=0.45, name="zone1"):
    a = np.array({"x": [1, 0, 0], "y": [0, 1, 0], "z": [0, 0, 1]}[axis], float)
    c = np.array([cx, cy, cz], float)
    return {"shape": "cylinder", "name": name, "start": (c - a * length / 2).tolist(), "end": (c + a * length / 2).tolist(), "radius": r, "size": 0.0}


def test_a_zone_on_the_plane_is_drawn_once_and_one_off_the_plane_twice():
    pl = [plane([0, 0, 0], [0, 1, 0])]
    assert len(mv.mirror_regions([zone(1.5, 0, 0)], pl)) == 1                          # (centred on the plane, axis in it)
    off = mv.mirror_regions([zone(1.5, 0.8, 0)], pl)
    assert len(off) == 2 and off[1]["name"] == "zone1 (mirrored)"
    assert np.allclose(off[1]["start"], [1.2, -0.8, 0]) and np.allclose(off[1]["end"], [1.8, -0.8, 0])
    assert len(mv.mirror_regions([zone(1.5, 0, 0, axis="y")], pl)) == 1                # (the axis across the plane, the middle on it: its own image)
    quarter = [plane([0, 0, 0], [0, 1, 0]), plane([0, 0, 0], [0, 0, 1])]
    assert len(mv.mirror_regions([zone(1.5, 0.8, 0.5)], quarter)) == 4                 # (a zone in the open: itself and three images)
    assert len(mv.mirror_regions([zone(1.5, 0.8, 0)], quarter)) == 2                   # (on one of the planes: two)


def test_a_box_region_is_mirrored_with_its_turn():
    pl = [plane([0, 0, 0], [0, 1, 0])]
    box = {"name": "wake", "min": [0, 0.2, 0], "max": [1, 0.6, 0.4], "size": 0.1}
    out = mv.mirror_regions([box], pl)
    assert len(out) == 2 and np.allclose(out[1]["min"], [0, -0.6, 0]) and np.allclose(out[1]["max"], [1, -0.2, 0.4])
    on = {"name": "mid", "min": [0, -0.2, 0], "max": [1, 0.2, 0.4], "size": 0.1}
    assert len(mv.mirror_regions([on], pl)) == 1                                       # (symmetric about the plane: once)
    c, s = np.cos(0.4), np.sin(0.4)
    turned = dict(box, rotation=[[1, 0, 0], [0, c, -s], [0, s, c]])
    out = mv.mirror_regions([turned], pl)
    Rn = np.asarray(out[1]["rotation"])
    assert np.allclose(np.linalg.det(Rn), 1.0) and np.allclose(Rn @ Rn.T, np.eye(3))  # (still a proper turn)


def test_the_air_box_faces_are_mirrored_and_the_cut_is_left_out():
    def face(i, verts, tris, **kw):
        return {"id": i, "kind": "farfield", "mesh": {"vertices": np.array(verts, float).ravel().tolist(), "triangles": [int(v) for v in np.array(tris).ravel()]}, **kw}
    far = face(1, [[0, 1, 0], [1, 1, 0], [0, 1, 1]], [[0, 1, 2]])
    cut = face(2, [[0, 0, 0], [1, 0, 0], [0, 0, 1]], [[0, 1, 2]], symmetry=0)
    out = mv.mirror_faces([far, cut], [plane([0, 0, 0], [0, 1, 0])])
    assert [f["id"] for f in out] == [1, 1_000_001]
    assert np.allclose(np.array(out[1]["mesh"]["vertices"]).reshape(-1, 3)[:, 1], -1.0)
    assert out[1]["mesh"]["triangles"] == [0, 2, 1]                                     # (the winding turned)
    assert mv.mirror_faces([far, cut], []) == [far, cut]
