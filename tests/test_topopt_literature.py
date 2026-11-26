"""The published topology-optimisation benchmarks and their reference codes (simwb/topopt/literature.py). Only the quick cases run here; the 150 x 50 and 300 x 100 slabs and
the 48 x 24 x 24 cantilever (about 45 minutes) run with `python -m simwb.topopt.literature --full` and the 3D reference function top3d."""
import numpy as np
import pytest

from simwb.topopt import benchmarks, literature as lit, simp


@pytest.mark.parametrize("ft", [1, 2, 3])
def test_the_reference_code_reproduces_the_printed_values_of_the_88_line_paper(ft):
    rmin, pub = lit.PUBLISHED_MBB[(60, ft)]
    r = lit.top88(60, 20, 0.5, 3.0, rmin, ft)
    assert r["c"] == pytest.approx(pub, abs=0.006)                         # (measured: 216.81, 233.71, 189.14 = the paper's Fig. 3 and Fig. 8, to two decimals)


def test_the_fast_reference_is_the_line_by_line_port_with_a_quicker_filter():
    slow = benchmarks.top88_reference(60, 20, 0.5, 3.0, 3.0, 2)
    fast = lit.top88(60, 20, 0.5, 3.0, 3.0, 2)
    assert fast["c"] == pytest.approx(slow["c"], rel=1e-9) and fast["iterations"] == slow["iterations"]


def test_the_module_is_within_a_few_per_cent_of_the_reference_at_the_same_radius():
    r = lit.run_module_mbb(60, 20, 3.0)
    ref = lit.top88(60, 20, 0.5, 3.0, r["radius"], 2)["c"]
    assert r["mass"] == pytest.approx(0.5, abs=1e-6)
    assert abs(r["c"] - ref) / ref < 0.05 and r["c"] < ref                  # (measured: 3.37 cells, -2.2 %: linear tetrahedra are a little stiffer)


def test_the_radius_floor_can_be_lifted_for_a_paper_radius_and_comes_back():
    floor = simp.MIN_CELLS
    with lit.guard_lifted():
        r = lit.run_module_mbb(60, 20, 2.4)
        assert r["radius"] == pytest.approx(2.4)
    assert simp.MIN_CELLS == floor
    assert abs(r["c"] - 233.71) / 233.71 < 0.05                              # (measured: 229.17 against the published 233.71)


def test_the_hexahedron_stiffness_is_symmetric_with_six_rigid_body_modes():
    K = lit.ke_hex()
    assert np.abs(K - K.T).max() < 1e-12
    w = np.linalg.eigvalsh(K)
    assert (np.abs(w[:6]) < 1e-12).all() and w[6] > 1e-3


def test_the_3d_cantilever_has_the_published_line_load_and_tetrahedra_are_a_little_stiffer_than_hexahedra():
    pb = lit.cantilever3d_problem(16)
    ld = np.flatnonzero(pb.f)
    assert (ld % 3 == 2).all() and (pb.f[ld] <= 0).all()                     # (all forces are down)
    assert pb.f.sum() == pytest.approx(-2 * 24 / np.pi, rel=0.02)   # (the integral of sin over the edge: 2 x 24 / pi)
    assert (pb.nodes[pb.fixed // 3, 0] < 1e-9).all()                         # (held only at the fixed end)
    s = lit.solid_stiffness(16, radius=10.0)
    assert 0.85 < s["tets"] / s["hex"] < 1.0                                 # (measured 0.929 at 16 cells, 0.954 at 24, 0.964 at 32: the gap closes with the mesh)
