"""The topology optimiser's engine: element stiffness against the static solver and beam theory, the analytic gradient against finite differences, the
mass target met exactly, supports and loads kept solid, a result that does not depend on the mesh, and the refusals that say what to change."""
import numpy as np
import pytest

from simwb.solvers import static_fem
from simwb.topopt import Optimiser, ProblemError, Settings, benchmarks, build, estimate

QUIET = lambda *a: None  # noqa: E731


def _cantilever(h=0.2, **kw):
    mesh, props, bcs = benchmarks.cantilever(L=2.0, W=0.4, H=1.0, h=h, **kw)
    return mesh, props, bcs


def test_the_solid_part_is_as_stiff_as_the_static_solver_and_beam_theory_say():
    mesh, props, bcs = benchmarks.cantilever(L=2.0, W=0.5, H=1.0, h=0.1)
    pb = build(mesh, props, bcs)
    opt = Optimiser(pb, Settings(volume_fraction=0.4, radius=0.25), log=QUIET)
    base = opt.baseline()
    quad = 2 * static_fem.solve(mesh, props, bcs, log=QUIET)["report"]["strain_energy_J"]
    E, nu, L, W, H = 1.0, 0.3, 2.0, 0.5, 1.0
    theory = L ** 3 / (3 * E * W * H ** 3 / 12) + L / ((5 / 6) * E / (2 * (1 + nu)) * W * H)
    assert base["balance"] < 1e-9
    assert quad == pytest.approx(theory, rel=0.05)                         # (the quadratic solver is within 5 % of beam theory with shear)
    assert base["compliance"] == pytest.approx(quad, rel=0.08)             # (linear tetrahedra are a little stiffer: compliance a few % lower)
    assert base["compliance"] < quad


def test_the_gradients_match_finite_differences():
    mesh, props, bcs = _cantilever()
    pb = build(mesh, props, bcs)
    opt = Optimiser(pb, Settings(volume_fraction=0.4, radius=0.3), log=QUIET)
    rng = np.random.default_rng(3)
    xd = rng.uniform(0.3, 0.9, len(opt.idx))
    for beta in (1.0, 4.0):
        ev = opt.evaluate(xd, beta)
        for k in rng.choice(len(xd), 5, replace=False):
            h = 1e-6
            xp, xm = xd.copy(), xd.copy()
            xp[k] += h
            xm[k] -= h
            fp, fm = opt.evaluate(xp, beta), opt.evaluate(xm, beta)
            assert (fp["c"] - fm["c"]) / (2 * h) == pytest.approx(ev["dc"][k], rel=2e-3)
            assert (fp["m"] - fm["m"]) / (2 * h) == pytest.approx(ev["dm"][k], rel=1e-5)


def test_the_mass_target_is_met_and_the_supports_and_loads_stay_solid():
    mesh, props, bcs = benchmarks.cantilever(L=2.0, W=0.4, H=1.0, h=0.1, load="patch")
    pb = build(mesh, props, bcs)
    res = Optimiser(pb, Settings(volume_fraction=0.4, radius=0.3, max_iter=100), log=QUIET).run()
    assert res["mass_fraction"] == pytest.approx(0.4, abs=1e-6)             # (the filtered, projected density: exactly the target)
    assert res["mass_fraction_threshold"] == pytest.approx(0.4, abs=0.08)   # (the sharp shape at 0.5: close to it; grey material makes it differ)
    assert (res["density"][pb.passive] == 1.0).all() and pb.passive.sum() > 0
    assert res["stiffness_kept"] < 1.0 and res["stiffness_kept"] > 0.15
    assert res["compliance"] > res["compliance_solid"]                       # (taking material away never makes it stiffer)
    assert res["history"][-1]["beta"] >= 4 and res["grey_fraction"] < 0.5


def test_the_result_does_not_depend_on_the_mesh_when_the_member_size_is_set_in_metres():
    kept = []
    for h in (0.05, 0.04):                                                   # (the coarser mesh of 0.1 is 14 % off: too few cells to a member)
        mesh, props, bcs = benchmarks.cantilever(L=2.0, W=2 * h, H=1.0, h=h, load="patch")
        res = Optimiser(build(mesh, props, bcs), Settings(volume_fraction=0.4, radius=0.2, max_iter=150), log=QUIET).run()
        assert res["converged"]
        kept.append(res["stiffness_kept"])
    assert kept[0] == pytest.approx(kept[1], rel=0.03)


def test_a_part_with_no_support_no_load_or_an_impossible_target_is_refused_with_a_reason():
    mesh, props, bcs = _cantilever()
    with pytest.raises(ProblemError, match="not supported"):
        build(mesh, props, [b for b in bcs if b["type"] != "fixed_support"])
    with pytest.raises(ProblemError, match="no load"):
        build(mesh, props, [b for b in bcs if b["type"] != "force"])
    with pytest.raises(ProblemError, match="cannot be used"):
        build(mesh, props, bcs + [{"name": "Heat", "type": "heat_flux", "faces": [1], "params": {}}])
    pb = build(mesh, props, bcs, keep_layers=0)
    with pytest.raises(ProblemError, match="kept solid"):
        Optimiser(pb, Settings(volume_fraction=0.03), log=QUIET)
    with pytest.raises(ProblemError, match="between 2 %"):
        Optimiser(pb, Settings(volume_fraction=1.5), log=QUIET)


def test_a_member_size_below_the_cells_is_raised_and_said_and_gravity_is_left_out_and_said():
    mesh, props, bcs = _cantilever()
    pb = build(mesh, props, bcs, gravity=[0, 0, -9.81])
    opt = Optimiser(pb, Settings(volume_fraction=0.4, radius=0.01), log=QUIET)
    assert opt.radius == pytest.approx(3.0 * pb.h)
    assert any("raised" in n for n in opt.notes) and any("weight" in n for n in opt.notes)


def test_a_run_that_does_not_fit_is_refused_before_it_starts_with_the_numbers(monkeypatch):
    mesh, props, bcs = _cantilever()
    pb = build(mesh, props, bcs)
    from simwb.topopt import simp
    monkeypatch.setattr(simp.memlimit, "memory", lambda: (8 * 2 ** 30, 2 ** 20))
    with pytest.raises(ProblemError, match="GB"):
        Optimiser(pb, Settings(volume_fraction=0.4, radius=0.3), log=QUIET)
    monkeypatch.undo()
    est = estimate(pb, 0.3)
    assert est["elements"] == pb.n and est["unknowns"] == pb.ndof and est["fits"]


def test_stop_returns_the_shape_so_far():
    mesh, props, bcs = _cantilever()
    pb = build(mesh, props, bcs)
    seen = []
    res = Optimiser(pb, Settings(volume_fraction=0.4, radius=0.3), log=QUIET).run(on_iter=lambda rec, xb: seen.append(rec["iteration"]),
                                                                                  should_stop=lambda: len(seen) >= 5)
    assert res["stopped"] and res["iterations"] == 5 and res["density"].shape == (pb.n,)
    assert res["mass_fraction"] == pytest.approx(0.4, abs=1e-6)


def test_a_prescribed_displacement_is_refused_but_a_zero_one_is_a_support():
    mesh, props, bcs = _cantilever()
    moved = [dict(b) for b in bcs] + [{"name": "Press", "type": "displacement", "faces": [2], "params": {"uz_mm": 1.0}}]
    with pytest.raises(ProblemError, match="moves a face"):
        build(mesh, props, moved)
    held = [dict(b) for b in bcs[1:]] + [{"name": "Clamp", "type": "displacement", "faces": [1], "params": {"ux_mm": 0, "uy_mm": 0, "uz_mm": 0}}]
    assert build(mesh, props, held).fixed.size > 0


def test_a_part_that_is_not_held_is_refused_with_the_reason():
    mesh, props, bcs = benchmarks.cantilever(L=2.0, W=0.4, H=1.0, h=0.2)
    loose = [{"name": "Slide", "type": "frictionless_support", "faces": [1], "params": {}}, bcs[1]]     # (a plane holds one direction only: the part can still slide and turn)
    opt = Optimiser(build(mesh, props, loose), Settings(volume_fraction=0.4, radius=0.3), log=QUIET)
    with pytest.raises(ProblemError, match="do not hold"):
        opt.run()


def test_the_filter_gives_the_same_average_on_a_graded_mesh_and_sees_across_a_symmetry_plane():
    L, W, H = 2.0, 0.4, 1.0
    mesh = benchmarks.box_mesh(L, W, H, 20, 2, 5, lambda c, n: np.where(c[:, 0] < 1e-9, 1, np.where(c[:, 0] > L - 1e-9, 2, 0)), grade=1.6)
    props = {1: {"E": 1.0, "nu": 0.3, "rho": 1.0}}
    bcs = [{"name": "Fixed", "type": "fixed_support", "faces": [1], "params": {}},
           {"name": "Load", "type": "force", "faces": [2], "params": {"fz_N": -1.0}}]
    pb = build(mesh, props, bcs)
    assert pb.volume.max() / pb.volume.min() > 5                                     # (the cells differ a lot in size)
    opt = Optimiser(pb, Settings(volume_fraction=0.4, radius=0.3), log=QUIET)
    cx = pb.centroid[:, 0]
    inner = (cx > 0.5) & (cx < L - 0.5) & (pb.centroid[:, 1] > 0.15) & (pb.centroid[:, 1] < W - 0.15)
    err = np.abs(opt.P @ cx - cx)[inner]
    assert err.max() < 0.03 * opt.radius                                             # (a linear field is reproduced: the average is by volume, not by element count)
    assert np.allclose(np.asarray(opt.P.sum(axis=1)).ravel(), 1.0)
    # a half model with a symmetry plane at y = 0 must filter like the whole model it stands for: the mirror image counts as neighbours
    from scipy.spatial import cKDTree
    lab = lambda c, n: np.where(c[:, 0] < 1e-9, 1, np.where(c[:, 0] > L - 1e-9, 2, 0))  # noqa: E731
    half = benchmarks.box_mesh(L, W, H, 20, 4, 10, lambda c, n: np.where(c[:, 1] < 1e-9, 3, lab(c, n)))
    full = benchmarks.box_mesh(L, 2 * W, H, 20, 8, 10, lab)
    full["nodes"][:, 1] -= W                                                         # (the upper half of this mesh IS the half model)
    pbf = build(full, props, bcs)
    pbh = build(half, props, bcs + [{"name": "Mirror", "type": "symmetry", "faces": [3], "params": {}}])
    assert pbh.mirror_planes == [(1, pytest.approx(0.0, abs=1e-12))]
    r = 0.3
    P_full = Optimiser(pbf, Settings(volume_fraction=0.4, radius=r), log=QUIET).P
    P_with = Optimiser(pbh, Settings(volume_fraction=0.4, radius=r), log=QUIET).P
    planes, pbh.mirror_planes = pbh.mirror_planes, []
    P_without = Optimiser(pbh, Settings(volume_fraction=0.4, radius=r), log=QUIET).P
    d, j = cKDTree(pbf.centroid).query(pbh.centroid)
    assert d.max() < 1e-9                                                            # (every element of the half model is an element of the whole)
    ref = (P_full @ np.abs(pbf.centroid[:, 1]))[j]                                   # (the whole model filtering the even field |y|)
    y = pbh.centroid[:, 1]
    near = (y < 0.1) & (pbh.centroid[:, 0] > 0.6) & (pbh.centroid[:, 0] < L - 0.6)
    assert np.abs((P_with @ y) - ref)[near].max() < 0.01 * r
    assert np.abs((P_without @ y) - ref)[near].max() > 0.05 * r                      # (without the plane the filter is biased there)
    assert np.allclose(np.asarray(P_with.sum(axis=1)).ravel(), 1.0)


def test_a_small_feature_size_is_raised_to_three_element_sizes_and_a_shape_still_grey_is_said():
    mesh, props, bcs = _cantilever()
    pb = build(mesh, props, bcs)
    opt = Optimiser(pb, Settings(volume_fraction=0.4, radius=2.0 * pb.h), log=QUIET)
    assert opt.radius == pytest.approx(3.0 * pb.h) and any("raised" in n for n in opt.notes)
    opt = Optimiser(pb, Settings(volume_fraction=0.4, radius=3.4 * pb.h), log=QUIET)
    assert opt.radius == pytest.approx(3.4 * pb.h) and any("several per cent" in n for n in opt.notes)
    from simwb.topopt import default_radius
    assert default_radius(pb) >= 4.0 * pb.h


def test_a_symmetry_plane_is_a_mirror_not_a_support_and_a_roller_is_not_a_mirror():
    L, W, H = 2.0, 0.4, 1.0
    lab = lambda c, n: np.where(c[:, 1] < 1e-9, 3, np.where(c[:, 0] < 1e-9, 1, np.where(c[:, 0] > L - 1e-9, 2, 0)))  # noqa: E731
    mesh = benchmarks.box_mesh(L, W, H, 20, 4, 10, lab)
    props = {1: {"E": 1.0, "nu": 0.3, "rho": 1.0}}
    base = [{"name": "Fixed", "type": "fixed_support", "faces": [1], "params": {}}, {"name": "Load", "type": "force", "faces": [2], "params": {"fz_N": -1.0}}]
    sym = build(mesh, props, base + [{"name": "Mirror", "type": "symmetry", "faces": [3], "params": {}}])
    roll = build(mesh, props, base + [{"name": "Roller", "type": "frictionless_support", "faces": [3], "params": {}}])
    assert len(sym.mirror_planes) == 1 and roll.mirror_planes == []                 # (only the symmetry plane is seen across by the filter)
    on_plane = sym.centroid[:, 1] < 0.1
    assert not sym.passive[on_plane & (sym.centroid[:, 0] > 0.3) & (sym.centroid[:, 0] < L - 0.3)].any()   # (no solid layer along the mirror: the shape can open there)
    assert roll.passive[roll.centroid[:, 1] < 0.1].any()                               # (a roller holds the part: material stays where it holds)


def test_a_part_marked_keep_solid_stays_solid_and_counts_in_the_mass():
    mesh, props, bcs = benchmarks.cantilever(L=2.0, W=0.4, H=1.0, h=0.1, load="patch")
    mesh["tet_vol"] = np.where(mesh["nodes"][mesh["tets"]].mean(axis=1)[:, 0] > 1.6, 2, 1)       # a second part at the free end (a load pad)
    props[2] = dict(props[1])
    pb = build(mesh, props, bcs, design_bodies=[1])
    pad = mesh["tet_vol"] == 2
    assert pb.passive[pad].all() and not pb.design[pad].any()
    res = Optimiser(pb, Settings(volume_fraction=0.5, radius=0.3, max_iter=60), log=QUIET).run()
    assert (res["density"][pad] == 1.0).all()
    assert res["mass_fraction"] == pytest.approx(0.5, abs=1e-6)                                   # (the pad's mass is part of the 50 %)
    with pytest.raises(ProblemError, match="kept solid"):
        Optimiser(build(mesh, props, bcs, design_bodies=[1]), Settings(volume_fraction=0.1, radius=0.3), log=QUIET)


def test_floating_pieces_and_supports_that_are_not_joined_are_named():
    from simwb.topopt import check
    mesh, props, bcs = benchmarks.cantilever(L=2.0, W=0.4, H=1.0, h=0.2, load="patch")
    pb = build(mesh, props, bcs)
    c = pb.centroid
    d = np.ones(pb.n)
    d[c[:, 0] > 0.9] = 0.0                                                          # a gap right across the beam: the load end is cut from the support
    sup = {"Fixed": [check.face_nodes(mesh["tris"], mesh["tri_surf"], [1])]}
    load = {"Load": [check.face_nodes(mesh["tris"], mesh["tri_surf"], [2])]}
    d[c[:, 0] > 1.3] = 1.0                                                           # (the load end is back, with a gap between it and the support side)
    r = check.connectivity(pb.nodes, pb.tets, d, 0.5, pb.weight, sup, load)
    assert r["pieces"] == 2 and r["loads_joined"] and 0.3 < r["floating_share"] < 0.7     # (the support side now floats against the loaded part)
    assert r["unattached"] == {"Fixed": {"faces": 1, "unattached": 1}}
    d[:] = 1.0
    r = check.connectivity(pb.nodes, pb.tets, d, 0.5, pb.weight, sup, load)
    assert r["pieces"] == 1 and r["floating_share"] == 0.0 and r["unattached"] == {} and r["largest_floating_share"] == 0.0


def test_a_feature_size_thicker_than_the_walls_is_said_and_the_thickness_is_estimated():
    L, W, H = 2.0, 0.1, 1.0                                                          # a thin plate: 0.1 thick
    mesh = benchmarks.box_mesh(L, W, H, 20, 1, 10, lambda c, n: np.where(c[:, 0] < 1e-9, 1, np.where(c[:, 0] > L - 1e-9, 2, 0)))
    props = {1: {"E": 1.0, "nu": 0.3, "rho": 1.0}}
    bcs = [{"name": "Fixed", "type": "fixed_support", "faces": [1], "params": {}}, {"name": "Load", "type": "force", "faces": [2], "params": {"fz_N": -1.0}}]
    pb = build(mesh, props, bcs)
    assert 0.05 < pb.thickness < 0.2                                                 # (twice the volume over the surface area: about the wall)
    opt = Optimiser(pb, Settings(volume_fraction=0.5, radius=0.4), log=QUIET)
    assert any("cannot thin them" in n for n in opt.notes)
    assert pb.owners.keys() == {"Fixed", "Load"}


def test_the_shape_is_smoothed_without_moving_the_parts_own_faces_and_written_as_a_valid_stl(tmp_path):
    from simwb.results import ResultSet, boundary_from_tets
    from simwb.topopt import view as tview
    mesh, props, bcs = benchmarks.cantilever(L=2.0, W=0.4, H=1.0, h=0.2, load="patch")
    pb = build(mesh, props, bcs)
    tris, cells = boundary_from_tets(pb.tets)
    dens = np.where(pb.centroid[:, 2] > 0.4, 1.0, 0.0)                                # (the upper 60 % of the beam)
    rs = ResultSet(pb.nodes, pb.tets, np.zeros(pb.n, int), ["part"], {"Density": ("cell", dens, "-")}, tris, cells, np.zeros(len(tris), np.int32), ["part"])
    sub = tview.subset(rs, rs.fields["Density"][1] >= 0.5)
    v0, t0 = tview.shape_surface(sub, rs, smooth=False)
    v1, t1 = tview.shape_surface(sub, rs, smooth=True)
    top = np.abs(v0[:, 2] - 1.0) < 1e-9
    assert (t0 == t1).all() and np.allclose(v0[top], v1[top])                          # (the top is the part's own face: it did not move)
    n = tview.write_stl(tmp_path / "s.stl", v1, t1)
    assert (tmp_path / "s.stl").stat().st_size == 84 + 50 * n and n == len(t1)
    a, b, c = v1[t1[:, 0]], v1[t1[:, 1]], v1[t1[:, 2]]
    vol = float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)                # (the signed volume of a closed outward surface is the volume)
    assert vol > 0 and vol == pytest.approx(0.4 * 1.0 * 2.0 * 0.6, rel=0.35)


def test_blocks_that_touch_only_along_an_edge_are_two_pieces_and_a_piece_needs_three_support_nodes():
    from simwb.topopt import check
    mesh, props, bcs = benchmarks.cantilever(L=2.0, W=0.4, H=1.0, h=0.2, load="patch")
    pb = build(mesh, props, bcs)
    c = pb.centroid
    a = (c[:, 0] < 0.2) & (c[:, 1] < 0.2)                                           # one cube column ...
    b = (c[:, 0] > 0.2) & (c[:, 0] < 0.4) & (c[:, 1] > 0.2) & (c[:, 1] < 0.4)         # ... and the one that touches it only along a vertical edge
    tlab = check.tet_components(pb.tets[a | b])
    assert len(np.unique(tlab)) == 2                                                 # (they could hinge about the edge: not one solid)
    face = (c[:, 0] < 0.2)
    assert len(np.unique(check.tet_components(pb.tets[face]))) == 1                    # (a block is one piece)
    sup = np.zeros(len(pb.nodes), bool)
    sup[check.face_nodes(mesh["tris"], mesh["tri_surf"], [1])] = True
    load = np.zeros(len(pb.nodes), bool)
    load[check.face_nodes(mesh["tris"], mesh["tri_surf"], [2])] = True
    t = pb.tets[np.ones(pb.n, bool)]
    lab = check.tet_components(t)
    assert list(check.held_pieces(t, lab, sup, load)) == [0]                           # the whole beam: touches the support on a face, carries the load
    assert len(check.held_pieces(t, lab, sup, np.zeros_like(load))) == 0                # no load on it: nothing to carry


def test_the_published_mbb_benchmark_is_reproduced_by_the_tetrahedron_optimiser():
    """The half MBB beam of the 88-line paper (Andreassen et al. 2011): 60 x 20, keep 50 %, penalty 3, a density filter of radius 3 cells. The reference is that
    paper's algorithm ported line by line (quadrilaterals); the optimiser under test uses tetrahedra and its own code. Same problem, two codes."""
    ref = benchmarks.top88_reference(60, 20, 0.5, 3.0, 3.0, 2)
    pb = benchmarks.mbb_slab_problem(60, 20)
    res = Optimiser(pb, Settings(volume_fraction=0.5, radius=3.0, projection=False, max_iter=300, tol=2e-3, cleanup_islands=False), log=QUIET).run()
    assert res["mass_fraction"] == pytest.approx(0.5, abs=1e-6)
    assert res["compliance"] == pytest.approx(ref["c"], rel=0.08)                     # (measured: +3 %; linear tetrahedra are a little stiffer than bilinear quadrilaterals)
    c = pb.centroid
    ci = np.floor(c[:, [0, 2]] + 1e-9).astype(int)
    cell, cnt = np.zeros((20, 60)), np.zeros((20, 60))
    np.add.at(cell, (ci[:, 1], ci[:, 0]), res["density"])
    np.add.at(cnt, (ci[:, 1], ci[:, 0]), 1)
    cell /= cnt
    assert np.corrcoef(cell.ravel(), ref["x"][::-1].ravel())[0, 1] > 0.97              # (measured: 0.992: the same truss)


def test_the_reference_port_meets_its_volume_and_lowers_the_compliance():
    ref = benchmarks.top88_reference(30, 10, 0.5, 3.0, 1.5, 1)
    assert ref["x"].mean() == pytest.approx(0.5, abs=1e-3) and ref["iterations"] > 20
    assert ref["history"][-1]["c"] < ref["history"][0]["c"]


def test_elements_emptied_for_good_stay_empty_and_a_keep_solid_face_only_keeps_material():
    mesh, props, bcs = benchmarks.cantilever(L=2.0, W=0.4, H=1.0, h=0.2, load="patch")
    top = np.zeros(len(mesh["tris"]), int)
    cen = mesh["nodes"][mesh["tris"]].mean(axis=1)
    top[(cen[:, 2] > 1.0 - 1e-9) & (cen[:, 0] > 0.9) & (cen[:, 0] < 1.3)] = 7
    mesh["tri_surf"] = np.where(top > 0, 7, mesh["tri_surf"])
    plain = build(mesh, props, bcs)
    kept = build(mesh, props, bcs + [{"name": "Pad", "type": "keep_solid", "faces": [7], "params": {}}])
    assert kept.passive.sum() > plain.passive.sum() and "Pad" in kept.owners
    assert kept.passive[kept.owners["Pad"]].all() and np.allclose(kept.f, plain.f) and np.allclose(kept.applied, plain.applied)   # (no force, no support: only material that stays)
    opt = Optimiser(kept, Settings(volume_fraction=0.8, radius=0.6), log=QUIET)      # (the material kept round supports, loads and the pad is thick now: ask for more)
    opt.void[:6] = True
    xn = opt.oc(np.full(len(opt.idx), 0.4), -np.ones(len(opt.idx)), np.ones(len(opt.idx)), 1.0)
    assert (xn[:6] == opt.s.xmin).all() and (xn[6:] > opt.s.xmin).any()


def test_a_run_reports_the_floating_pieces_it_emptied_or_none():
    mesh, props, bcs = _cantilever(load="patch")
    pb = build(mesh, props, bcs)
    res = Optimiser(pb, Settings(volume_fraction=0.4, radius=0.6, max_iter=80), log=QUIET).run()
    assert res["removed_floating_share"] >= 0.0 and res["mass_fraction"] == pytest.approx(0.4, abs=1e-6)
    off = Optimiser(pb, Settings(volume_fraction=0.4, radius=0.6, max_iter=80, cleanup_islands=False), log=QUIET).run()
    assert off["removed_floating_share"] == 0.0


def test_a_keep_solid_face_keeps_a_pad_of_real_thickness_but_supports_and_loads_keep_the_single_layer():
    mesh, props, bcs = benchmarks.cantilever(L=2.0, W=0.4, H=1.0, h=0.1, load="patch")
    cen = mesh["nodes"][mesh["tris"]].mean(axis=1)
    mesh["tri_surf"] = np.where((cen[:, 2] < 1e-9) & (cen[:, 0] > 0.9) & (cen[:, 0] < 1.3), 7, mesh["tri_surf"])     # a pad on the underside
    pb = build(mesh, props, bcs + [{"name": "Pad", "type": "keep_solid", "faces": [7], "params": {}}])
    n0 = int(pb.passive.sum())
    pad_pts = pb.keep_points["Pad"]
    assert set(pb.keep_points) == {"Pad"}                                                          # (only the user's face is thickened)
    pb.grow_kept(0.4)                                                                             # a smallest feature of 0.4: keep the material within 0.2 of the pad
    n_big = int(pb.passive.sum())
    assert n_big > n0 and (pb.passive & pb.design).sum() == 0
    near = np.linalg.norm(pb.centroid[:, None, :] - pad_pts[None, ::25, :], axis=2).min(axis=1) < 0.15
    assert pb.passive[near].all()                                                                  # (a pad, not a skin)
    pb.grow_kept(0.2)                                                                             # idempotent and reversible: it is the radius that decides
    assert n0 < int(pb.passive.sum()) < n_big
    assert "Pad" in pb.owners and len(pb.owners["Pad"]) > 1
