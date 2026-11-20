"""The first wall layer follows the flow model: y+ for the wall functions, a resolved boundary layer when laminar."""
import math

import pytest

from simwb.solvers.openfoam import cases

NU = 1.516e-5


def test_turbulent_keeps_the_y_plus_rule():
    p = cases.layer_plan(30.0, 1.0, NU, "kOmegaSST", 5, 1.2)
    assert p["first"] == pytest.approx(cases.layer_first_thickness(30.0, 1.0, NU))
    assert not p["laminar"] and p["delta"] == pytest.approx(0.37 * (30 / NU) ** -0.2, rel=1e-9)


def test_laminar_layers_span_the_boundary_layer():
    V, L = 3.0, 1.0
    delta = 5 * L / math.sqrt(V * L / NU)                       # Blasius thickness at the end of the plate
    p = cases.layer_plan(V, L, NU, "laminar", 14, 1.2)
    assert p["laminar"] and p["delta"] == pytest.approx(delta)
    assert p["total"] == pytest.approx(1.3 * delta, rel=1e-6)   # the layers cover 1.3 delta
    assert 8 < p["cells_across_delta"] <= 14                    # and put about ten cells across it
    # a y+ = 30 first layer (the old rule) would be a boundary layer in a handful of cells at best
    old = cases.layer_first_thickness(V, L, NU)
    assert old > p["first"] * 5


def test_few_layers_are_reported_not_hidden():
    p = cases.layer_plan(3.0, 1.0, NU, "laminar", 3, 1.2)
    assert p["cells_across_delta"] < 3.01                       # three layers cannot resolve it: the number says so


def test_the_first_layer_is_bounded_and_a_given_one_is_kept():
    p = cases.layer_plan(3.0, 1.0, NU, "laminar", 40, 1.3)
    assert p["first"] >= p["delta"] / 200 - 1e-12               # never absurdly thin
    q = cases.layer_plan(3.0, 1.0, NU, "laminar", 6, 1.2, first=0.002)
    assert q["first"] == 0.002 and q["total"] == pytest.approx(0.002 * (1.2 ** 6 - 1) / 0.2)


def test_no_flow_no_division_by_zero():
    p = cases.layer_plan(0.0, 1.0, NU, "laminar", 5, 1.2)
    assert p["delta"] == 0.0 and p["cells_across_delta"] == 0.0 and p["first"] > 0
