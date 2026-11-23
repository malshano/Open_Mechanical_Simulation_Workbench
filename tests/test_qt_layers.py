"""Wall (inflation) layers made visible (issue #8): a summary of what the settings come to, and a translucent shell of their total thickness
round the object in the 3D view."""
import numpy as np
import pytest
from qt_support import ctl, from_thread  # noqa: F401

from simwb.qt import layerview
from simwb.qt.meshtools import layer_numbers


def cube_faces(h=1.0):
    """The six faces of a cube from -h to h as {mesh: {vertices, triangles}} (two triangles each)."""
    faces = []
    for ax in range(3):
        for sgn in (-1, 1):
            a, b = [k for k in range(3) if k != ax]
            v = []
            for p, q in ((-h, -h), (h, -h), (h, h), (-h, h)):
                pt = [0.0, 0.0, 0.0]
                pt[ax], pt[a], pt[b] = sgn * h, p, q
                v.append(pt)
            faces.append({"kind": "body", "mesh": {"vertices": v, "triangles": [[0, 1, 2], [0, 2, 3]]}})
    return faces


def test_the_shell_is_the_surface_pushed_out_by_the_thickness():
    pts, tris, base = layerview.shell_points(cube_faces(), 0.1)
    assert len(base) == 8                                                  # (the corners are shared between faces: no gaps)
    assert np.all(np.abs(pts).max(axis=1) > np.abs(base).max(axis=1))      # every vertex moved outwards
    assert 1.05 < np.abs(pts).max() < 1.11
    assert np.linalg.norm(pts - base, axis=1) == pytest.approx(0.1)


def test_the_numbers_follow_the_settings_and_vanish_without_layers_or_flow():
    st = {"study": "external_aero", "settings": {"speed": 10.0, "turbulence": "kOmegaSST", "fluid": "air"}, "moving_zones": [],
          "geometry": {"info": {"ref_length": 0.3}}, "mesh": {"layers": {"n": 6, "growth": 1.2}}}
    ln = layer_numbers(st)
    series = (1.2 ** 6 - 1) / 0.2
    assert ln["n"] == 6 and ln["total"] == pytest.approx(ln["first"] * series)
    st["mesh"]["layers"]["first"] = 0.0005
    assert layer_numbers(st)["first"] == pytest.approx(0.0005)
    st["mesh"]["layers"]["n"] = 0
    assert layer_numbers(st) is None
    st["mesh"]["layers"]["n"] = 6
    st["settings"]["speed"] = 0
    assert layer_numbers(st) is None                                       # (no flow: nothing to plan the layers for)
    st["study"] = "cht_enclosure"
    st["settings"]["speed"] = 10
    assert layer_numbers(st) is None


def test_a_liquid_needs_a_different_first_layer_than_air():
    st = {"study": "external_aero", "settings": {"speed": 10.0, "turbulence": "kOmegaSST", "fluid": "air"}, "moving_zones": [],
          "geometry": {"info": {"ref_length": 0.3}}, "mesh": {"layers": {"n": 6, "growth": 1.2}}}
    air = layer_numbers(st)["first"]
    st["settings"]["fluid"] = "water"
    assert layer_numbers(st)["first"] < air                                # (water: a thinner boundary layer for the same y+)
