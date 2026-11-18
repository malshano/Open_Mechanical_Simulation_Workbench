"""Symmetry planes: the mirror group, the force / moment expansion (checked against an explicit mirrored body)."""
import re
import numpy as np
import pytest

from simwb import symmetry as S


def _cloud(rng, n=40):
    """A 'half body' on the +y side of the plane y = 0.1: points and a force on each."""
    x = rng.normal(size=(n, 3))
    x[:, 1] = 0.1 + np.abs(x[:, 1]) + 0.05
    return x, rng.normal(size=(n, 3))


def _totals(x, f, c):
    return f.sum(0), np.cross(x - c, f).sum(0)


@pytest.mark.parametrize("planes", [
    [S.axis_plane("y", 0.1)],
    [S.axis_plane("y", 0.1), S.axis_plane("z", -0.2)],
    [S.axis_plane("y", 0.1), S.axis_plane("z", -0.2), S.axis_plane("x", 0.3, keep_positive=False)],
])
def test_expanded_force_and_moment_equal_the_explicit_mirror_images(planes):
    rng = np.random.default_rng(3)
    x, f = _cloud(rng)
    # keep the cloud on the kept side of every plane
    for pl in planes:
        d = S.signed_distance(pl, x)
        x = x + np.outer(np.maximum(0.05 - d, 0), pl["normal"])
    c = np.array([0.4, -0.3, 0.7])  # (a moment point that is NOT on any plane)
    F, M = _totals(x, f, c)
    totF, totM = np.zeros(3), np.zeros(3)
    for R, t in S.group(planes):  # every image: reflect the points and the force vectors explicitly
        xi, fi = x @ R.T + t, f @ R.T
        a, b = _totals(xi, fi, c)
        totF, totM = totF + a, totM + b
    gotF, gotM = S.expand_force_moment(planes, F, M, c)
    assert np.allclose(gotF, totF, atol=1e-10) and np.allclose(gotM, totM, atol=1e-10)


def test_one_plane_drag_doubles_and_side_force_cancels():
    pl = S.axis_plane("y", 0.0)
    F, M = np.array([2.0, 0.7, -1.0]), np.zeros(3)  # drag along x, side force along y (the plane normal), lift z
    totF, _ = S.expand_force_moment([pl], F, M, [0, 0, 0])
    assert np.allclose(totF, [4.0, 0.0, -2.0])


def test_moment_of_a_pitching_body_adds_and_the_roll_moment_cancels():
    pl = S.axis_plane("y", 0.0)
    M = np.array([0.5, 1.5, -0.25])  # roll (x), pitch (y), yaw (z) about a point on the plane
    _, totM = S.expand_force_moment([pl], np.zeros(3), M, [0.0, 0.0, 0.0])
    assert np.allclose(totM, [0.0, 3.0, 0.0])


def test_images_and_validation():
    a, b, c = S.axis_plane("x", 0), S.axis_plane("y", 0), S.axis_plane("z", 0)
    assert len(S.images([a])) == 1 and len(S.images([a, b])) == 3 and len(S.images([a, b, c])) == 7
    assert S.mass_factor([a, b]) == 4
    assert [p["name"] for p in S.validate([a, b])] == ["Symmetry plane 1", "Symmetry plane 2"]
    with pytest.raises(ValueError, match="perpendicular"):
        S.validate([a, S.make_plane([0, 0, 0], [1, 1, 0])])
    with pytest.raises(ValueError, match="perpendicular|same plane"):
        S.validate([a, S.axis_plane("x", 1.0)])  # parallel planes would repeat the model forever
    with pytest.raises(ValueError, match="At most"):
        S.validate([a, b, c, S.axis_plane("x", 2.0)])
    with pytest.raises(ValueError):
        S.make_plane([0, 0, 0], [0, 0, 0])
    with pytest.raises(ValueError):
        S.make_plane([0, 0, float("nan")], [1, 0, 0])


def test_reflection_flip_and_turn():
    pl = S.axis_plane("y", 0.2)
    R, t = S.reflection(pl)
    assert np.allclose(R @ [1, 2, 3] + t, [1, -1.6, 3])  # y -> 0.4 - y
    flipped = S.make_plane(pl["point"], -np.asarray(pl["normal"]))
    assert S.signed_distance(pl, [0, 1, 0]) > 0 > S.signed_distance(flipped, [0, 1, 0])
    D = np.array([[0.0, -1, 0], [1, 0, 0], [0, 0, 1]])  # 90 degrees about z
    q = S.turn(pl, D)
    assert np.allclose(q["normal"], [-1, 0, 0]) and np.allclose(q["point"], [-0.2, 0, 0])


def test_flow_and_gravity_must_lie_in_the_plane():
    pl = S.axis_plane("y", 0.0)
    assert not S.crossing_issues([pl], flow_dir=[1, 0, 0], gravity=[0, 0, -1])
    bad = S.crossing_issues([pl], flow_dir=[1, 0.2, 0], gravity=[0, 1, 0])
    assert len(bad) == 2 and "oncoming flow" in bad[0] and "Gravity" in bad[1]


def test_projected_area_factor_and_cylinder_tests():
    pl = S.axis_plane("y", 0.0)
    assert S.area_factor([pl], [1, 0, 0]) == 2.0 and S.area_factor([pl], [0, 1, 0]) == 1.0
    assert S.intersects_cylinder(pl, [0, 0.01, 0], [0, 0, 1], 0.05, 0.01)   # disk straddling the plane
    assert not S.intersects_cylinder(pl, [0, 0.2, 0], [0, 0, 1], 0.05, 0.01)
    assert S.intersects_cylinder(pl, [0, 0.2, 0], [0, 1, 0], 0.05, 0.5)     # long zone along the normal
    assert S.side_of(pl, [0, 0.2, 0]) == "kept" and S.side_of(pl, [0, -0.2, 0]) == "removed"


def test_plane_from_a_face_keeps_the_side_of_the_parts():
    face = {"normal": [0, 1, 0], "centroid": [0.0, 0.5, 0.0], "surface_type": "Plane"}
    assert S.plane_from_face(face, keep_toward=[0, 2, 0])["normal"] == pytest.approx([0, 1, 0])
    assert S.plane_from_face(face, keep_toward=[0, -2, 0])["normal"] == pytest.approx([0, -1, 0])
    pl = S.axis_plane("y", 0.5)
    assert S.on_plane(pl, face, 1e-6) and not S.on_plane(S.axis_plane("y", 0.6), face, 1e-6)
    assert not S.on_plane(pl, dict(face, surface_type="Cylinder"), 1e-6)


# ---------------------------------------------------------------- the project: planes cut the model
@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMWB_HOME", str(tmp_path / "ws"))
    import importlib

    import simwb.project as P

    importlib.reload(P)
    yield P


def _wait(job, timeout=300):
    assert job.wait(timeout), "job timed out"
    assert job.status == "done", (job.error, job.log[-10:])
    return job.result


def test_plane_cuts_the_part_and_makes_a_derived_boundary_condition(workspace, box_step):
    P = workspace
    pr = P.create_project("Half", "static_structural")
    _wait(pr.import_step(box_step))
    pr.set_settings(gravity="off")
    bid = str(pr.bodies()[0]["id"])
    pr.set_body(int(bid), material="steel_structural") if "steel_structural" in __import__("simwb.materials", fromlist=["x"]).SOLIDS else None
    vol0 = pr.bodies()[0]["volume"]
    name0 = pr.bodies()[0]["name"]
    pr.add_bc("Root", "fixed_support", [f["id"] for f in pr.faces() if f["normal"][0] < -0.9])

    _wait(pr.add_symmetry(axis="y", pos_mm=10))          # the middle of the 20 mm wide bar
    b = pr.bodies()[0]
    assert b["volume"] == pytest.approx(vol0 / 2, rel=1e-6) and b["name"] == name0
    assert len(pr.data["symmetry"]) == 1 and pr.data["symmetry"][0]["normal"] == pytest.approx([0, 1, 0])
    bcs = {x["name"]: x for x in pr.data["bcs"]}
    sym_bc = bcs["Symmetry plane 1"]
    assert sym_bc["type"] == "symmetry" and sym_bc["auto"] and len(sym_bc["faces"]) == 1
    cut = pr.face_map()[sym_bc["faces"][0]]
    assert cut["symmetry"] == 0 and cut["area"] == pytest.approx(0.1 * 0.02, rel=1e-6)  # 100 x 20 mm section
    assert bcs["Root"]["faces"], "the fixed support on the end face survives the cut (same area ratio is not needed)"
    assert pr.data["bodies"][str(pr.bodies()[0]["id"])]["material"]  # the material survived the rebuild

    # flip: the other half stays; the derived BC follows the new cut face, and a support on the end face moves to the
    # same face of the other half (the mirror image of the old one: the two halves of that face touch at the plane)
    _wait(pr.update_symmetry("Symmetry plane 1", flip=True))
    assert pr.data["symmetry"][0]["normal"] == pytest.approx([0, -1, 0])
    assert pr.bodies()[0]["volume"] == pytest.approx(vol0 / 2, rel=1e-6)
    root = next(x for x in pr.data["bcs"] if x["name"] == "Root")
    assert len(root["faces"]) == 1
    rf = pr.face_map()[root["faces"][0]]
    assert rf["normal"][0] < -0.9 and rf["centroid"][1] < 0.01 and rf["area"] == pytest.approx(0.02 * 0.01, rel=1e-6)
    # move to 5 mm: a quarter of the width stays on the kept (-y) side... 5 mm of 20 mm
    _wait(pr.update_symmetry("Symmetry plane 1", pos_mm=5))
    assert pr.bodies()[0]["volume"] == pytest.approx(vol0 * 5 / 20, rel=1e-6)
    # a rename needs no rebuild
    assert pr.update_symmetry("Symmetry plane 1", name="Mirror") is None
    assert {x["name"] for x in pr.data["bcs"] if x.get("plane_id")} == {"Mirror"}
    # a second plane at right angles: a quarter; a parallel one is refused
    _wait(pr.add_symmetry(axis="z", pos_mm=10, keep="-"))
    assert pr.bodies()[0]["volume"] == pytest.approx(vol0 * 5 / 20 / 2, rel=1e-6)
    assert len([x for x in pr.data["bcs"] if x.get("plane_id")]) == 2
    with pytest.raises(ValueError, match="perpendicular|same plane"):
        pr.add_symmetry(axis="y", pos_mm=15)
    # the faces of a plane cannot be edited by hand; deleting the condition removes the plane (a rebuild)
    with pytest.raises(ValueError, match="follow the plane"):
        pr.update_bc("Mirror", faces=[1])
    pr.delete_bc("Mirror")
    _wait(P.JOBS.latest(pr.id, "prepare"))
    assert len(pr.data["symmetry"]) == 1 and pr.bodies()[0]["volume"] == pytest.approx(vol0 / 2, rel=1e-6)
    _wait(pr.remove_symmetry("Symmetry plane 2"))
    assert pr.bodies()[0]["volume"] == pytest.approx(vol0, rel=1e-6) and not pr.data["symmetry"]
    assert not any(x.get("plane_id") for x in pr.data["bcs"])


def test_a_support_on_a_part_that_a_plane_removes_comes_back_with_it(workspace, box_step):
    """A plane through the middle of the bar's length leaves the root face on the removed side: the support has no
    faces then; removing the plane brings the face and the support back."""
    P = workspace
    pr = P.create_project("Back", "static_structural")
    _wait(pr.import_step(box_step))
    root = [f["id"] for f in pr.faces() if f["normal"][0] < -0.9]
    pr.add_bc("Root", "fixed_support", root)
    _wait(pr.add_symmetry(axis="x", pos_mm=50, keep="+"))                   # keeps x >= 50 mm: the root (x = 0) is gone
    bc = next(x for x in pr.data["bcs"] if x["name"] == "Root")
    assert bc["faces"] == [] and bc["lost"]
    _wait(pr.update_symmetry("Symmetry plane 1", flip=True))                  # keeps x <= 50 mm: the root is back
    bc = next(x for x in pr.data["bcs"] if x["name"] == "Root")
    assert len(bc["faces"]) == 1 and "lost" not in bc
    assert pr.face_map()[bc["faces"][0]]["normal"][0] < -0.9
    _wait(pr.add_symmetry(axis="y", pos_mm=10, keep="+"))                      # a second plane: the root is cut in half
    assert len(next(x for x in pr.data["bcs"] if x["name"] == "Root")["faces"]) == 1


def test_a_plane_from_a_face_keeps_the_side_with_the_part_and_a_failed_cut_changes_nothing(workspace, box_step):
    P = workspace
    pr = P.create_project("Face", "heat_conduction")
    _wait(pr.import_step(box_step))
    end = next(f for f in pr.faces() if f["normal"][0] < -0.9)          # the x = 0 end face
    _wait(pr.add_symmetry(faces=[end["id"]]))
    assert pr.data["symmetry"][0]["normal"][0] == pytest.approx(1.0)    # the part is on the +x side: it stays
    assert pr.bodies()[0]["volume"] == pytest.approx(0.1 * 0.02 * 0.02, rel=1e-6)
    sym_bc = next(x for x in pr.data["bcs"] if x.get("plane_id"))
    assert sym_bc["faces"] == [f["id"] for f in pr.faces() if f.get("symmetry") == 0]
    # a plane that would remove everything is refused by the geometry step; the study keeps its planes
    before = [dict(p) for p in pr.data["symmetry"]]
    with pytest.raises(AssertionError):
        _wait(pr.add_symmetry(axis="y", pos_mm=500))
    assert pr.data["symmetry"] == before and pr.data["geometry"]["status"] == "ready"
    assert pr.bodies()[0]["volume"] == pytest.approx(0.1 * 0.02 * 0.02, rel=1e-6)
    with pytest.raises(ValueError, match="does not exist"):
        pr.add_symmetry(faces=[9999])


def test_symmetric_flow_setup_is_checked(workspace, box_step):
    P = workspace
    pr = P.create_project("Flow", "external_aero")
    _wait(pr.import_step(box_step))
    air0 = pr.bodies()[0]["volume"]
    pr.set_settings(forward_axis="+x")
    _wait(pr.add_symmetry(axis="y", pos_mm=10))
    assert pr.bodies()[0]["volume"] == pytest.approx(air0 / 2, rel=0.02)   # half the air volume
    sym_faces = [f for f in pr.faces() if f.get("symmetry") == 0]
    assert sym_faces and all(f["kind"] == "farfield" for f in sym_faces)
    assert not [c for c in pr.check() if c["level"] == "error" and "crosses" in c["message"]]
    pr.set_settings(yaw_deg=10)                  # side-slip: the flow crosses the plane
    bad = [c for c in pr.check() if c["level"] == "error" and "crosses" in c["message"]]
    assert bad and "side-slip" in bad[0]["message"]


def test_whole_body_forces_rotors_and_area_from_the_half_model():
    """A half quadcopter (two rotors, y >= 0) in a +x flow: the whole body has four rotors, double the drag, no side
    force, the pitch moment doubled and the roll moment cancelled; the history is expanded row by row."""
    from simwb.solvers.openfoam import results_loader as rl

    pl = S.axis_plane("y", 0.0)
    rotors = [{"center": [0.2, 0.2, 0.05], "axis": [0, 0, 1], "thrust_N": 3.0, "H_N": [-0.1, 0.02, 0.0]},
              {"center": [-0.2, 0.2, 0.05], "axis": [0, 0, 1], "thrust_N": 3.0, "H_N": [-0.1, 0.02, 0.0]}]
    meta = {"U_inf": [-10.0, 0, 0], "CofR": [0, 0, 0], "ref_area": 0.01, "rotors": rotors}
    F = {"total": np.array([2.0, 0.7, -1.0]), "pressure": np.array([1.5, 0.6, -1.0]), "viscous": np.array([0.5, 0.1, 0.0]),
         "moment": np.array([0.2, 0.5, 0.3])}
    T = np.array([0.0, 1.0])
    Ft = np.array([[1.0, 0.1, 0.0], [2.0, 0.7, -1.0]])
    F["history"] = (T, Ft, Ft * 0.8, Ft * 0.2, np.array([[0.1, 0.4, 0.1], [0.2, 0.5, 0.3]]))
    Fw, mw, note = rl.whole_body([pl], F, meta, 10.0, [0, 0, 1])
    assert np.allclose(Fw["total"], [4.0, 0.0, -2.0]) and np.allclose(Fw["pressure"], [3.0, 0.0, -2.0])
    assert np.allclose(Fw["moment"], [0.0, 1.0, 0.0])        # roll and yaw cancel, pitch adds (about a point on the plane)
    assert np.allclose(Fw["history"][1], [[2.0, 0.0, 0.0], [4.0, 0.0, -2.0]]) and Fw["history"][4].shape == (2, 3)
    assert len(mw["rotors"]) == 4 and mw["rotors"][2]["mirrored"]
    assert mw["rotors"][2]["center"] == pytest.approx([0.2, -0.2, 0.05]) and mw["rotors"][2]["H_N"] == pytest.approx([-0.1, -0.02, 0.0])
    assert sum(r["thrust_N"] for r in mw["rotors"]) == 12.0
    assert mw["ref_area"] == pytest.approx(0.02) and note["copies"] == 2
    # the input is not changed; no force output (a run without one) still works
    assert meta["ref_area"] == 0.01 and len(meta["rotors"]) == 2
    assert rl.whole_body([pl], None, meta, 10.0, [0, 0, 1])[0] is None
    # automatic rotor thrust shares the weight over ALL rotors, not only the modelled ones
    from simwb.solvers.openfoam import cases

    assert cases.rotor_thrusts({"mass_kg": 1.0}, [{}, {}], 4) == pytest.approx([9.81 / 4, 9.81 / 4])
    assert cases.rotor_thrusts({"mass_kg": 1.0}, [{}, {}]) == pytest.approx([9.81 / 2, 9.81 / 2])


def test_symmetry_plane_becomes_an_openfoam_symmetry_patch(workspace, box_step):
    """The half air volume is meshed and exported: its cut face is a patch of type symmetry (no wall treatment) and
    the case remembers the planes for the whole-body results."""
    import json

    P = workspace
    pr = P.create_project("HalfAir", "external_aero")
    _wait(pr.import_step(box_step))
    _wait(pr.add_symmetry(axis="y", pos_mm=10))
    pr.set_mesh(preset="quick")
    _wait(pr.generate_mesh(), 600)
    assert pr.data["mesh"]["status"] == "ready"
    job = pr.solve(export_only=True)
    job.wait(120)
    meta = json.loads((pr.case_dir / "simwb_case.json").read_text())
    assert len(meta["symmetry"]) == 1
    types = {p["name"]: p["bc_type"] for p in meta["patches"]}
    assert types.get("symmetry_plane_1") == "symmetry"
    boundary = (pr.case_dir / "constant" / "polyMesh" / "boundary").read_text()
    block = boundary[boundary.index("symmetry_plane_1"):] if "symmetry_plane_1" in boundary else boundary
    assert "symmetry" in block.split("}")[0]
    u0 = (pr.case_dir / "0" / "U").read_text()
    assert re.search(r"symmetry_plane_1\s*\{\s*type\s+symmetry;", u0)


# ---------------------------------------------------------------- the solvers: half beam + mirror = full beam
def _half_beam(L, b, h, nx, ny, nz):
    """A cantilever of width b modelled as its half (y from 0 to b/2): face 1 = the root, 3 = the tip, 4 = the cut
    face at y = b/2 (the mirror plane), 2 = the rest."""
    import test_pipeline as tp

    m = tp._beam_mesh(L, b / 2, h, nx, ny, nz)
    cy = m["nodes"][m["tris"]].mean(1)[:, 1]
    m["tri_surf"] = np.where((cy > b / 2 - 1e-9) & (m["tri_surf"] == 2), 4, m["tri_surf"]).astype(np.int32)
    return m


def test_half_beam_with_a_mirror_plane_equals_the_full_beam_statically():
    import test_pipeline as tp

    from simwb.solvers import static_fem

    L, b, h, E, P = 0.2, 0.02, 0.01, 200e9, 100.0
    props = {1: {"E": E, "nu": 0.3, "rho": 7850.0, "yield": 250e6, "name": "beam"}}
    full = tp._beam_mesh(L, b, h, 16, 4, 3)
    root = {"name": "root", "type": "fixed_support", "faces": [1]}
    tip = lambda F: {"name": "tip", "type": "force", "faces": [3], "params": {"fx_N": 0, "fy_N": 0, "fz_N": -F}}  # noqa: E731
    rf = static_fem.solve(full, props, [root, tip(P)], log=lambda *a: None)["report"]
    half = _half_beam(L, b, h, 16, 2, 3)
    rh = static_fem.solve(half, props, [root, tip(P / 2), {"name": "mirror", "type": "symmetry", "faces": [4]}],
                          log=lambda *a: None)["report"]
    I = b * h ** 3 / 12
    assert abs(rh["max_displacement_mm"] / (P * L ** 3 / (3 * E * I) * 1e3) - 1) < 0.03     # beam theory
    assert abs(rh["max_displacement_mm"] / rf["max_displacement_mm"] - 1) < 0.03            # and the full model
    assert abs(rh["max_von_mises_MPa"] / rf["max_von_mises_MPa"] - 1) < 0.06
    assert abs(rh["reaction_force_N"][2] - P / 2) < 1e-6                                     # half the load
    # the mirror plane carries force too: it is counted, so the balance is a real check and every support reports its own
    assert rh["force_balance_error"] < 1e-6, rh["force_balance_error"]
    bcs = rh["boundary_conditions"]
    root_r, mirror_r = np.array(bcs["root"]["reaction_N"]), np.array(bcs["mirror"]["reaction_N"])
    assert np.allclose(root_r + mirror_r, rh["reaction_force_N"], atol=1e-6)
    assert abs(root_r[2] - P / 2) < 1e-3 and abs(mirror_r[2]) < 1e-3                          # the shear goes to the root
    assert abs(mirror_r[0]) < 1e-6 and abs(mirror_r[2]) < 1e-6                                # a mirror pushes only along its normal
    assert abs(rh["reaction_force_N"][1]) < 1e-6                                              # nothing is pushed sideways


def test_half_beam_with_a_mirror_plane_finds_the_symmetric_modes_only():
    import test_pipeline as tp

    from simwb.solvers import modal_fem

    L, b, h, E, nu, rho = 0.1, 0.01, 0.005, 69e9, 0.33, 2700.0
    mat = {1: {"E": E, "nu": nu, "rho": rho}}
    clamp = {"name": "clamp", "type": "fixed_support", "faces": [1], "params": {}}
    full = modal_fem.solve(tp._beam_mesh(L, b, h, 16, 4, 2), mat, [clamp], n_modes=4, log=lambda s: None)
    ff = [m["frequency_Hz"] for m in full["modes"]]       # 1: thin-direction bending, 2: wide-direction (antisymmetric
    #                                                       in y across the mid plane), 3: 2nd thin-direction bending
    mirror = {"name": "mirror", "type": "symmetry", "faces": [4], "params": {}}
    half = modal_fem.solve(_half_beam(L, b, h, 16, 2, 2), mat, [clamp, mirror], n_modes=3, log=lambda s: None)
    hf = [m["frequency_Hz"] for m in half["modes"]]
    assert abs(hf[0] / ff[0] - 1) < 0.02                  # the same first mode
    assert abs(hf[1] / ff[2] - 1) < 0.03                  # the wide-direction mode (ff[1]) is not symmetric: skipped
    assert any("symmetric across the plane" in w for w in half["report"]["warnings"])
    # a plane that is not normal to an axis is refused with the way out
    tilted = _half_beam(L, b, h, 8, 2, 2)
    tilted["nodes"] = tilted["nodes"] @ np.array([[1, 0, 0], [0, 0.9, 0.1], [0, -0.1, 0.9]]).T  # (shears it a little)
    with pytest.raises(ValueError, match="normal to the x, y or z axis"):
        modal_fem.solve(tilted, mat, [clamp, mirror], n_modes=2, log=lambda s: None)


def test_cooling_study_with_a_mirror_plane_writes_constraint_fields_in_every_region(workspace):
    """OpenFOAM stops when a 'symmetry' patch has an ordinary patch field: every field of the air and of each solid
    needs type symmetry there. The inlet and outlet are cut in half by the plane and must keep their conditions."""
    import json
    from pathlib import Path

    P = workspace
    root = Path(__file__).resolve().parents[1]
    pr = P.create_project("HeatsinkHalf", "cht_enclosure")
    _wait(pr.import_step(root / "examples" / "heatsink_duct.step"))
    full_vol = sum(b["volume"] for b in pr.bodies())
    io = pr.suggest_inlet_outlet()                       # inlet and outlet on the duct ends
    bb = pr.data["geometry"]["info"]["bbox"]
    _wait(pr.add_symmetry(axis="y", pos_mm=1e3 * (bb[1] + bb[4]) / 2))
    assert sum(b["volume"] for b in pr.bodies()) == pytest.approx(full_vol / 2, rel=0.02)
    types = {b["type"] for b in pr.data["bcs"] if b["faces"]}
    assert {"velocity_inlet", "pressure_outlet", "symmetry"} <= types, types   # the cut ends kept their conditions
    chip = min((b for b in pr.bodies() if b["role"] == "solid"), key=lambda b: b["volume"])
    pr.set_body(chip["id"], material="silicon", heat_W=7.5)
    for b in pr.bodies():
        if b["role"] == "solid" and b["id"] != chip["id"]:
            pr.set_body(b["id"], material="aluminium_6063")
    pr.set_mesh(preset="coarse")
    _wait(pr.generate_mesh(), 900)
    assert not [c for c in pr.check() if c["level"] == "error"], [c for c in pr.check() if c["level"] == "error"]
    _wait(pr.solve(export_only=True))
    case = pr.case_dir
    assert json.loads((case / "simwb_case.json").read_text())["symmetry"]
    for f in ("0.orig/air/U", "0.orig/air/T", "0.orig/air/p_rgh", "0.orig/air/k", "0.orig/air/nut"):
        assert re.search(r"symmetry_plane_1\s*\{\s*type\s+symmetry;", (case / f).read_text()), f
    solids = [d.name for d in (case / "0.orig").iterdir() if d.name != "air" and d.is_dir()]
    assert solids
    for r in solids:
        for fld in ("T", "p"):
            assert re.search(r"symmetry_plane_1\s*\{\s*type\s+symmetry;", (case / "0.orig" / r / fld).read_text()), (r, fld)


def test_cooling_totals_of_the_whole_device_from_the_kept_part():
    from simwb.solvers.openfoam import results_loader as rl

    pl = S.axis_plane("y", 0.0)
    s = {"heat_input_W": 7.5, "heat_out_W": 7.4, "flow_m3_per_h": 3.0, "thermal_resistance_K_per_W": 4.0,
         "T_max": 61.0, "velocity_max": 2.5, "assumptions": ["x"]}
    rl._cht_whole_device(s, [pl])
    assert s["heat_input_W"] == 15.0 and s["flow_m3_per_h"] == 6.0 and s["thermal_resistance_K_per_W"] == 2.0
    assert s["T_max"] == 61.0 and s["velocity_max"] == 2.5 and s["symmetry"]["copies"] == 2
    assert s["assumptions"][0] == "x" and "mirror" in s["assumptions"][1]


def test_the_assistant_can_set_move_flip_and_remove_a_plane(workspace, box_step):
    import json

    from simwb import tools

    P = workspace
    pr = P.create_project("Chat", "heat_conduction")
    _wait(pr.import_step(box_step))
    out = json.loads(tools.call("set_symmetry", pr, {"action": "add", "axis": "y", "position_mm": 10}))
    assert out["rebuild"]["status"] == "done" and len(out["planes"]) == 1
    assert "Symmetry plane 'Symmetry plane 1': normal to y at 10.0 mm, keeps the +y side" in out["state"]
    out = json.loads(tools.call("set_symmetry", pr, {"action": "update", "plane": "Symmetry plane 1", "flip": True}))
    assert out["planes"][0]["normal"] == pytest.approx([0, -1, 0]) and "keeps the -y side" in out["state"]
    # a boundary condition of type symmetry on a face goes the same way (the geometry is rebuilt first)
    end = next(f for f in pr.faces() if f["normal"][0] < -0.9)
    out = json.loads(tools.call("add_boundary_condition", pr, {"type": "symmetry", "faces": [end["id"]], "name": "End mirror"}))
    assert out["symmetry"]["status"] == "done" and len(pr.data["symmetry"]) == 2
    assert tools.call("set_symmetry", pr, {"action": "update", "plane": "nope"}).startswith("ERROR")
    assert tools.call("set_symmetry", pr, {"action": "dance"}).startswith("ERROR")
    out = json.loads(tools.call("set_symmetry", pr, {"action": "remove", "plane": "End mirror"}))
    assert len(out["planes"]) == 1
    assert any(t[0] == "set_symmetry" for t in tools.TOOLS)


def test_planes_turn_with_the_parts_about_their_middle(workspace, box_step):
    """Turning the geometry about the middle of the parts moves a symmetry plane with them: the same half stays and
    the plane's normal turns."""
    P = workspace
    pr = P.create_project("Turn", "heat_conduction")
    _wait(pr.import_step(box_step))
    _wait(pr.add_symmetry(axis="x", pos_mm=50, keep="-"))          # the bar is 100 mm long along x: half stays
    vol = pr.bodies()[0]["volume"]
    D = [[0.0, -1, 0], [1, 0, 0], [0, 0, 1]]                         # +90 about z: x -> y
    _wait(pr.rotate_geometry(matrix=D))
    pl = pr.data["symmetry"][0]
    assert pl["normal"] == pytest.approx([0, -1, 0], abs=1e-9)       # was -x, now -y
    assert pr.bodies()[0]["volume"] == pytest.approx(vol, rel=1e-6)  # still the same half of the bar
    assert len(next(x for x in pr.data["bcs"] if x.get("plane_id"))["faces"]) == 1
    # the plane passes through the (unchanged) middle of the bar: the kept half ends at it
    bb = pr.bodies()[0]["bbox"]
    assert pr.data["symmetry"][0]["point"][1] == pytest.approx(bb[4], abs=1e-6) or \
        pr.data["symmetry"][0]["point"][1] == pytest.approx(bb[1], abs=1e-6)
    # an STL / OBJ mesh cannot be cut: a clear message
    pr.data["geometry"]["format"] = "stl"
    with pytest.raises(ValueError, match="STEP file"):
        pr.add_symmetry(axis="y", pos_mm=0)



# ---------------------------------------------------------------- aligning a face with a coordinate plane
def test_align_matrix_and_nearest_axis():
    from simwb import orient

    rng = np.random.default_rng(1)
    for _ in range(20):
        n, t = rng.normal(size=3), np.eye(3)[rng.integers(3)] * rng.choice([-1, 1])
        R = orient.align_matrix(n, t)
        assert np.allclose(R @ R.T, np.eye(3), atol=1e-12) and np.linalg.det(R) == pytest.approx(1.0)
        assert np.allclose(R @ (n / np.linalg.norm(n)), t, atol=1e-12)
    assert np.allclose(orient.align_matrix([0, 0, 1], "+z"), np.eye(3))
    R = orient.align_matrix([0, 0, 1], "-z")                      # opposite: a half turn
    assert np.allclose(R @ [0, 0, 1], [0, 0, -1]) and np.linalg.det(R) == pytest.approx(1.0)
    # the shortest turn: a vector turned onto +z by a small angle moves only a little
    R = orient.align_matrix([0.1, 0, 1], "+z")
    assert np.allclose(R @ [0, 1, 0], [0, 1, 0], atol=1e-12)       # (the axis of the turn is y: y stays)
    assert orient.nearest_axis([0.1, -0.9, 0.2]) == "-y" and orient.nearest_axis([0, 0, 3]) == "+z"
    with pytest.raises(ValueError):
        orient.align_matrix([0, 0, 0], "+z")


def _cube_faces():
    """A unit cube as six flat faces (two triangles each) whose stored normals point the WRONG way on three faces."""
    v = np.array([[x, y, z] for x in (0, 1) for y in (0, 1) for z in (0, 1)], float)
    quads = {  # (corner indices of the quad), outward normal
        0: ([0, 1, 3, 2], (-1, 0, 0)), 1: ([4, 5, 7, 6], (1, 0, 0)), 2: ([0, 1, 5, 4], (0, -1, 0)),
        3: ([2, 3, 7, 6], (0, 1, 0)), 4: ([0, 2, 6, 4], (0, 0, -1)), 5: ([1, 3, 7, 5], (0, 0, 1))}
    faces = {}
    for i, (q, n) in quads.items():
        flip = -1 if i % 2 == 0 else 1
        faces[i + 1] = {"id": i + 1, "normal": [flip * c for c in n], "volumes": [1], "surface_type": "Plane",
                        "mesh": {"vertices": v[q].ravel().tolist(), "triangles": [0, 1, 2, 0, 2, 3]}}
    return faces, quads


def test_outward_normal_is_decided_geometrically_not_from_the_stored_sign():
    from simwb import orient

    faces, quads = _cube_faces()
    for fid, (_, n) in quads.items():
        out, pt = orient.outward_normal(faces[fid + 1], faces, solid=True)
        assert np.allclose(out, n), (fid, out)                        # out of the cube, whatever the stored sign
        assert np.all((pt >= -1e-9) & (pt <= 1 + 1e-9))
        inn, _ = orient.outward_normal(faces[fid + 1], faces, solid=False)  # a flow study: the cube is the empty part,
        assert np.allclose(inn, -np.asarray(n))                       # the volume is the air ... of the cube itself
    two = dict(faces[1], volumes=[1, 2])
    assert orient.outward_normal(two, faces, solid=True) is None      # a contact face between two parts: undecided


def test_a_flat_face_is_laid_on_a_coordinate_plane(workspace, box_step):
    P = workspace
    pr = P.create_project("Align", "static_structural")
    _wait(pr.import_step(box_step))                                    # a 100 x 20 x 20 mm bar along x
    end = next(f for f in pr.faces() if f["centroid"][0] > 0.09)       # the x = 100 mm end face
    info = pr.align_info(end["id"])
    assert info["normal"] == pytest.approx([1, 0, 0], abs=1e-9) and info["nearest"] == "+x"
    _wait(pr.align_face(end["id"], "-z"))                              # stand the bar on that end: it faces down
    bb = pr.bodies()[0]["bbox"]
    assert (bb[5] - bb[2]) == pytest.approx(0.1, abs=1e-6) and (bb[3] - bb[0]) == pytest.approx(0.02, abs=1e-6)
    bottom = [f for f in pr.faces() if f["normal"][2] < -0.99 or f["centroid"][2] < bb[2] + 1e-6]
    assert any(abs(f["centroid"][2] - bb[2]) < 1e-6 and f["area"] == pytest.approx(0.02 * 0.02, rel=1e-6) for f in bottom)
    assert np.allclose(pr._parts_center(), [(bb[0] + bb[3]) / 2, (bb[1] + bb[4]) / 2, (bb[2] + bb[5]) / 2], atol=1e-6)
    new = pr.align_info(next(f["id"] for f in pr.faces() if abs(f["centroid"][2] - bb[2]) < 1e-6))
    assert new["normal"] == pytest.approx([0, 0, -1], abs=1e-9)        # the face now faces down
    with pytest.raises(ValueError, match="already faces"):
        pr.align_face(next(f["id"] for f in pr.faces() if abs(f["centroid"][2] - bb[2]) < 1e-6), "-z")
    with pytest.raises(ValueError, match="direction"):
        pr.align_face(1, "up")
    with pytest.raises(ValueError, match="does not exist"):
        pr.align_info(9999)
    import json

    from simwb import tools

    face = next(f["id"] for f in pr.faces() if f["centroid"][0] > bb[3] - 1e-6)  # a face at the high x side
    out = json.loads(tools.call("align_face", pr, {"face": face, "direction": "+z"}))
    assert out["rebuild"]["status"] == "done"
    assert any(t[0] == "align_face" for t in tools.TOOLS)



def test_a_face_is_laid_against_a_wall_of_the_tunnel(workspace, box_step):
    """Flow study: pick the model's end face, then the floor of the tunnel: the bar stands on that end."""
    P = workspace
    pr = P.create_project("Wall", "external_aero")
    _wait(pr.import_step(box_step))                                    # a bar 100 x 20 x 20 mm along x
    walls = {f["id"]: pr.wall_info(f["id"]) for f in pr.faces() if f.get("kind") == "farfield"}
    floor = next(i for i, w in walls.items() if w["nearest"] == "-z")
    assert walls[floor]["normal"] == pytest.approx([0, 0, -1], abs=1e-9)   # out of the tunnel: down
    ceiling = next(i for i, w in walls.items() if w["nearest"] == "+z")
    with pytest.raises(ValueError, match="not a wall of the air volume"):
        pr.wall_info(next(f["id"] for f in pr.faces() if f.get("kind") == "body"))
    end = next(f for f in pr.faces() if f.get("kind") == "body" and f["centroid"][0] > 0.09)   # the x = 100 mm end
    assert pr.align_info(end["id"])["normal"] == pytest.approx([1, 0, 0], abs=1e-9)      # points out of the bar
    _wait(pr.align_face(end["id"], wall=floor))                         # the end face against the floor: it faces down
    bb = pr.data["geometry"]["info"]["parts_bbox"]
    assert (bb[5] - bb[2]) == pytest.approx(0.1, abs=1e-5) and (bb[3] - bb[0]) == pytest.approx(0.02, abs=1e-5)
    low = next(f for f in pr.faces() if f.get("kind") == "body" and f["centroid"][2] < bb[2] + 1e-6)
    assert pr.align_info(low["id"])["normal"] == pytest.approx([0, 0, -1], abs=1e-9)
    # the same face "facing away" from the floor, i.e. against the ceiling: it then looks up
    with pytest.raises(ValueError, match="already faces"):
        pr.align_face(low["id"], wall=floor)
    _wait(pr.align_face(low["id"], wall=floor, away=True))
    top = next(f for f in pr.faces() if f.get("kind") == "body" and pr.align_info(f["id"])["normal"][2] > 0.99
               and f["area"] == pytest.approx(0.02 * 0.02, rel=1e-3))
    assert top["centroid"][2] > (pr.data["geometry"]["info"]["parts_bbox"][2] + pr.data["geometry"]["info"]["parts_bbox"][5]) / 2
    assert ceiling != floor
    import json

    from simwb import tools

    out = json.loads(tools.call("align_face", pr, {"face": top["id"], "wall": floor}))
    assert out["rebuild"]["status"] == "done"



def test_a_face_is_aligned_to_the_plane_of_another_face(workspace, box_step):
    """Pick the face to align, then another flat face as the plane: same way, or face to face."""
    P = workspace
    pr = P.create_project("Two", "static_structural")
    _wait(pr.import_step(box_step))                                    # a bar 100 x 20 x 20 mm along x
    end = next(f for f in pr.faces() if f["centroid"][0] > 0.09)       # x = 100 mm end, points +x
    top = next(f for f in pr.faces() if f["centroid"][2] > 0.019)      # z = 20 mm face, points +z
    ref = pr.ref_info(top["id"])
    assert ref["kind"] == "face" and ref["normal"] == pytest.approx([0, 0, 1], abs=1e-9)
    with pytest.raises(ValueError, match="different face"):
        pr.align_face(end["id"], wall=end["id"])
    _wait(pr.align_face(end["id"], wall=top["id"]))                    # same way: the end now faces +z (bar stands up)
    bb = pr.bodies()[0]["bbox"]
    assert (bb[5] - bb[2]) == pytest.approx(0.1, abs=1e-6)
    now = next(f for f in pr.faces() if f["area"] == pytest.approx(0.02 * 0.02, rel=1e-6) and f["centroid"][2] > bb[5] - 1e-6)
    assert pr.align_info(now["id"])["normal"] == pytest.approx([0, 0, 1], abs=1e-9)
    side = next(f for f in pr.faces() if f["area"] > 0.0019 and abs(pr.align_info(f["id"])["normal"][0]) > 0.99)
    n_side = pr.align_info(side["id"])["normal"]
    _wait(pr.align_face(now["id"], wall=side["id"], away=True))        # face to face: it now points opposite to the side
    after = next(f for f in pr.faces() if f["area"] == pytest.approx(0.02 * 0.02, rel=1e-6)
                 and np.dot(pr.align_info(f["id"])["normal"], n_side) < -0.99)
    assert after is not None


# ---------------------------------------------------------------- centre and axis from a round feature
def _cyl_face(center, axis, r, length, a0=0.0, a1=2 * np.pi, n=48, m=3):
    """A cylindrical face (or an arc of one) as a display mesh with vertices on the surface."""
    axis = np.asarray(axis, float) / np.linalg.norm(axis)
    ref = np.array([1.0, 0, 0]) if abs(axis[0]) < 0.9 else np.array([0, 1.0, 0])
    u = np.cross(axis, ref)
    u /= np.linalg.norm(u)
    w = np.cross(axis, u)
    ang = np.linspace(a0, a1, n + 1)
    hs = np.linspace(-length / 2, length / 2, m)
    v = np.array([np.asarray(center) + r * (np.cos(a) * u + np.sin(a) * w) + h * axis for h in hs for a in ang])
    t = []
    for j in range(m - 1):
        for i in range(n):
            a, b = j * (n + 1) + i, j * (n + 1) + i + 1
            t += [a, b, b + n + 1, a, b + n + 1, a + n + 1]
    return {"surface_type": "Cylinder", "mesh": {"vertices": v.ravel().tolist(), "triangles": t}}


def _annulus_face(center, normal, r_out, r_in, n=64):
    """A flat ring (a disc with a round hole) as a display mesh."""
    normal = np.asarray(normal, float) / np.linalg.norm(normal)
    ref = np.array([1.0, 0, 0]) if abs(normal[0]) < 0.9 else np.array([0, 1.0, 0])
    u = np.cross(normal, ref)
    u /= np.linalg.norm(u)
    w = np.cross(normal, u)
    ang = np.linspace(0, 2 * np.pi, n, endpoint=False)
    ring = lambda r: np.array([np.asarray(center) + r * (np.cos(a) * u + np.sin(a) * w) for a in ang])  # noqa: E731
    v = np.vstack([ring(r_out), ring(r_in)])
    t = []
    for i in range(n):
        j = (i + 1) % n
        t += [i, j, n + i, j, n + j, n + i]
    return {"surface_type": "Plane", "mesh": {"vertices": v.ravel().tolist(), "triangles": t}}


def test_cylinder_axis_centre_radius_from_a_tilted_face_and_from_an_arc():
    from simwb import axisfit

    for ax, (a0, a1) in (([0, 0, 1], (0, 2 * np.pi)), ([1, 2, 3], (0, 2 * np.pi)), ([1, 2, 3], (0.3, 0.3 + 2 * np.pi / 3))):
        f = _cyl_face([0.01, -0.02, 0.03], ax, 0.004, 0.012, a0, a1)
        res = axisfit.fit_cylinder(f)
        u = np.asarray(ax, float) / np.linalg.norm(ax)
        assert abs(abs(np.dot(res["axis"], u)) - 1) < 1e-9
        assert res["radius"] == pytest.approx(0.004, rel=1e-6) and res["length"] == pytest.approx(0.012, rel=1e-6)
        # the centre is on the axis, half way along the face (for an arc the extent along the axis is the same)
        assert np.allclose(res["center"], [0.01, -0.02, 0.03], atol=1e-9)
    # a flat face is not a cylinder
    with pytest.raises(ValueError, match="not a cylinder"):
        axisfit.fit_cylinder(_annulus_face([0, 0, 0], [0, 0, 1], 0.01, 0.003))


def test_the_circular_edge_nearest_to_the_click_is_used():
    from simwb import axisfit

    face = _annulus_face([0.02, 0.0, 0.005], [0, 1, 1], 0.010, 0.003)
    n = np.array([0, 1, 1]) / np.sqrt(2)
    ref = np.cross(n, [1.0, 0, 0])
    ref /= np.linalg.norm(ref)
    near_hole = np.array([0.02, 0, 0.005]) + 0.0035 * ref
    near_rim = np.array([0.02, 0, 0.005]) + 0.0098 * ref
    inner = axisfit.pick_axis(face, near_hole)
    outer = axisfit.pick_axis(face, near_rim)
    assert inner["kind"] == "edge" and inner["radius"] == pytest.approx(0.003, rel=1e-6)
    assert outer["radius"] == pytest.approx(0.010, rel=1e-6)
    for res in (inner, outer):
        assert np.allclose(res["center"], [0.02, 0, 0.005], atol=1e-9) and abs(abs(np.dot(res["axis"], n)) - 1) < 1e-9
    # a square face has no circular edge: the message says what to click
    sq = {"surface_type": "Plane", "mesh": {"vertices": [0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0], "triangles": [0, 1, 2, 0, 2, 3]}}
    with pytest.raises(ValueError, match="no circular edge"):
        axisfit.pick_axis(sq, [0.5, 0.5, 0])


def test_axis_info_on_the_real_bracket_holes(workspace):
    """The bracket's two 4.4 mm holes through the 3 mm base: from the cylinder face and from the rim of the flat face."""
    from pathlib import Path

    P = workspace
    pr = P.create_project("Holes", "static_structural")
    _wait(pr.import_step(Path(__file__).resolve().parents[1] / "examples" / "camera_mount.step"))
    cyl = [f for f in pr.faces() if f["surface_type"] == "Cylinder"]
    assert len(cyl) == 2
    got = sorted((pr.axis_info(f["id"]) for f in cyl), key=lambda d: d["center"][0])
    for info, x in zip(got, (0.025, 0.050)):
        assert info["kind"] == "cylinder" and info["axis"] == pytest.approx([0, 0, 1], abs=1e-4) and info["nearest"] == "+z"
        assert info["center"] == pytest.approx([x, 0.020, 0.0015], abs=1e-6) and info["angle_deg"] < 0.01
        assert info["radius"] == pytest.approx(0.0022, abs=1e-4) and info["length"] == pytest.approx(0.003, abs=1e-6)
    # clicking the flat top face near the second hole's rim finds the same circle (centre at the top, z = 3 mm)
    top = next(f for f in pr.faces() if f["surface_type"] == "Plane" and f["normal"][2] > 0.99 and f["centroid"][2] > 0.0029
               and f["area"] > 1e-3)
    edge = pr.axis_info(top["id"], [0.0524, 0.020, 0.003])
    assert edge["kind"] == "edge" and edge["center"] == pytest.approx([0.050, 0.020, 0.003], abs=1e-6)
    assert edge["axis"] == pytest.approx([0, 0, 1], abs=1e-4) and edge["radius"] == pytest.approx(0.0022, abs=1e-4)
    with pytest.raises(ValueError, match="does not exist"):
        pr.axis_info(9999)



def test_a_tipped_zone_is_dropped_or_snapped_and_a_pick_puts_it_on_the_round_feature(workspace):
    """After a turn that tips a zone's axis off x/y/z the zone is dropped; with snap_zones (the pick-then-turn flow) it
    stays on the nearest axis, and picking the now straight hole puts its centre and axis exactly on the hole."""
    from pathlib import Path

    P = workspace
    pr = P.create_project("Snap", "external_aero")
    _wait(pr.import_step(Path(__file__).resolve().parents[1] / "examples" / "camera_mount.step"))
    zone = {"name": "z1", "center": [0.03, 0.02, 0.01], "axis": "+z", "radius": 0.01, "length": 0.005, "rpm": 3000,
            "method": "mrf"}
    c, s_ = np.cos(np.radians(5)), np.sin(np.radians(5))
    tilt = [[c, 0, s_], [0, 1, 0], [-s_, 0, c]]                        # 5 degrees about y: the holes' axes tip
    pr.set_moving_zones([zone])
    _wait(pr.rotate_geometry(matrix=tilt))                               # the default: an oblique turn drops the zone
    assert pr.data["moving_zones"] == [] and any("was removed" in n for n in pr.data["notes"])
    pr.set_moving_zones([zone])
    hole = next(f["id"] for f in pr.faces() if f["surface_type"] == "Cylinder")
    info = pr.axis_info(hole)
    assert info["angle_deg"] == pytest.approx(5.0, abs=0.05) and info["nearest"] == "+z"
    D = (np.array(tilt).T).tolist()                                      # turn it back: the pick-then-turn flow
    _wait(pr.rotate_geometry(matrix=D, snap_zones=True))
    z = pr.data["moving_zones"]
    assert len(z) == 1 and z[0]["axis"] == "+z" and any("kept on the z axis" in n for n in pr.data["notes"])
    straight = pr.axis_info(next(f["id"] for f in pr.faces() if f["surface_type"] == "Cylinder"))
    assert straight["angle_deg"] < 0.01                                  # the hole is along z again
    pr.set_moving_zones([dict(z[0], center=straight["center"], axis=straight["nearest"])])
    assert pr.data["moving_zones"][0]["center"] == pytest.approx(straight["center"], abs=1e-9)



def test_a_turn_and_its_inverse_bring_the_model_back_exactly(workspace):
    """Turns pivot on the centre of mass of the parts (which does not change when they turn), so tipping the model and
    tipping it back leaves every face where it was (the middle of a tilted part's bounding box would drift)."""
    from pathlib import Path

    P = workspace
    pr = P.create_project("Back", "static_structural")
    _wait(pr.import_step(Path(__file__).resolve().parents[1] / "examples" / "camera_mount.step"))
    c0 = np.asarray(pr._parts_center())
    k0 = sorted(map(tuple, np.round([f["centroid"] for f in pr.faces()], 6)))
    c, s_ = np.cos(np.radians(23)), np.sin(np.radians(23))
    tilt = np.array([[c, 0, s_], [0, 1, 0], [-s_, 0, c]])
    _wait(pr.rotate_geometry(matrix=tilt.tolist()))
    assert np.allclose(pr._parts_center(), c0, atol=1e-6)                        # the pivot did not move
    assert pr.state()["geometry"]["pivot"] == pytest.approx(c0.tolist(), abs=1e-6)  # (the view pivots on the same point)
    _wait(pr.rotate_geometry(matrix=tilt.T.tolist()))
    k1 = sorted(map(tuple, np.round([f["centroid"] for f in pr.faces()], 6)))
    assert np.allclose(np.array(k1), np.array(k0), atol=2e-6)


def test_circular_edges_of_the_bracket_and_of_an_open_arc(workspace):
    """The two holes of the bracket have four rims (top and bottom), each shared by a flat face and a bore and listed
    once; an arc that is not a full circle is found as one edge, not cut in two."""
    from pathlib import Path

    from simwb import axisfit

    P = workspace
    pr = P.create_project("Edges", "static_structural")
    _wait(pr.import_step(Path(__file__).resolve().parents[1] / "examples" / "camera_mount.step"))
    edges = pr.circular_edges()
    assert len(edges) == 4 and all(e["closed"] and e["radius"] == pytest.approx(0.0022, abs=1e-4) for e in edges)
    assert all(e["nearest"] == "+z" and e["angle_deg"] < 0.01 and len(e["faces"]) == 2 for e in edges)
    got = sorted((round(e["center"][0] * 1000), round(e["center"][2] * 1000, 1)) for e in edges)
    assert got == [(25, 0.0), (25, 3.0), (50, 0.0), (50, 3.0)]          # x of the hole, z of the rim
    assert all(len(e["points"]) % 3 == 0 and 18 <= len(e["points"]) <= 3 * 96 for e in edges)
    # a quarter of a ring face: its inner edge is an arc (open), found once with its true centre and radius
    ring = _annulus_face([0.0, 0.0, 0.0], [0, 0, 1], 0.010, 0.004, n=96)
    v = np.asarray(ring["mesh"]["vertices"]).reshape(-1, 3)
    t = np.asarray(ring["mesh"]["triangles"]).reshape(-1, 3)
    ang = np.arctan2(v[:, 1], v[:, 0])
    keep = np.all((ang[t] >= -1e-9) & (ang[t] <= np.pi / 2 + 1e-9), axis=1)    # triangles inside the first quadrant
    quarter = {"id": 7, "surface_type": "Plane", "mesh": {"vertices": ring["mesh"]["vertices"], "triangles": t[keep].ravel().tolist()}}
    arcs = [e for e in axisfit.circular_edges([quarter], 0.02) if not e["closed"]]
    radii = sorted(round(e["radius"], 6) for e in arcs)
    assert radii == [0.004, 0.01]
    for e in arcs:
        assert np.allclose(e["center"], [0, 0, 0], atol=1e-9) and abs(e["axis"][2]) == pytest.approx(1.0)


def test_a_plane_that_only_grazes_the_model_is_warned_about(workspace, box_step):
    """A plane slicing a thin sliver off the model leaves micro-edges and tiny cells (a drone run diverged there):
    a plane through the middle, or clear of the model, is fine."""
    import numpy as np

    pr = workspace.create_project("Graze", "external_aero")
    _wait(pr.import_step(box_step))
    lo, hi = (np.asarray(x, float) for x in (pr.data["geometry"]["info"]["parts_bbox"][:3],
                                              pr.data["geometry"]["info"]["parts_bbox"][3:]))
    mid, size = (lo + hi) / 2, hi - lo

    def plane(offset):
        return {"name": "P", "normal": [0.0, 1.0, 0.0], "point": [mid[0], lo[1] + offset, mid[2]]}

    assert pr._symmetry_grazing([plane(size[1] / 2)]) == []                       # the middle
    assert pr._symmetry_grazing([plane(-0.01 * size[1])]) == []                   # clear of the model
    msg = pr._symmetry_grazing([plane(0.005 * size[1])])                          # slices 0.5 % off
    assert len(msg) == 1 and "only just cuts" in msg[0]


def _face(kind, zlo, zhi, kind_name="body"):
    v = [0, 0, zlo, 1e-3, 0, zlo, 0, 1e-3, zhi]
    return {"kind": kind_name, "mesh": {"vertices": v, "triangles": [0, 1, 2]}}


def test_grazing_plane_is_nudged_past_the_tiny_faces_it_leaves():
    plane = {"id": "a", "name": "P", "point": [0, 0, 0.0], "normal": [0, 0, 1.0]}
    bbox = [0, 0, -0.9e-3, 0.1, 0.1, 0.1]                                  # 0.9 mm of 100.9 mm below the plane
    faces = [_face("body", 0, 0.1e-3), _face("body", 0, 0.11e-3), _face("body", 0, 30e-3),   # (the last: a big face)
             _face("body", 0, 0), _face("farfield", 0, 0.05e-3)]            # (on the plane; domain wall)
    (i, t), = S.grazing_nudges([plane], faces, bbox)
    assert i == 0 and 0.13e-3 < t < 0.16e-3
    assert S.grazing_nudges([plane], faces, [0, 0, -50e-3, 0.1, 0.1, 50e-3]) == []   # through the middle: untouched
    assert S.grazing_nudges([plane], [_face("body", 0, 30e-3)], bbox) == []          # nothing tiny left


def test_a_grazing_plane_is_moved_when_the_geometry_is_cut(workspace, tmp_path):
    """A 100 mm block with a 1 mm stud under it; the plane cuts the stud 0.1 mm below the block, so a 0.1 mm tall
    ring of the stud would be left above the plane. The plane is moved up past it and the user is told."""
    import gmsh

    step = tmp_path / "stud.step"
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    box = gmsh.model.occ.addBox(-50, -50, 0.1, 100, 100, 100)
    stud = gmsh.model.occ.addCylinder(0, 0, -0.9, 0, 0, 1.0, 3)
    gmsh.model.occ.fuse([(3, box)], [(3, stud)])
    gmsh.model.occ.synchronize()
    gmsh.write(str(step))
    gmsh.finalize()
    pr = workspace.create_project("Stud", "external_aero")
    _wait(pr.import_step(step))
    _wait(pr.add_symmetry(axis="z", pos_mm=0, keep="+"))
    z = pr.data["symmetry"][0]["point"][2]
    assert 0.13e-3 < z < 0.16e-3                                           # moved up by about 0.14 mm
    assert any("moved" in n and "tiny faces" in n for n in pr.data["notes"])
    left = [f["id"] for f in pr.faces() if f["kind"] == "body" and f["bbox"][2] >= z - 1e-7 and 1e-7 < f["bbox"][5] - z <= 1e-3]
    assert not left                                                        # no tiny face above the plane any more


def test_a_flow_plane_trims_the_air_volume_and_keeps_the_parts_unless_told_to_delete(workspace, tmp_path):
    """A plane that crosses a part ends the air volume there (the stored box follows the plane); the part stays whole
    in the model unless the plane is told to delete the model beyond it. The flag is kept through the saved planes."""
    import gmsh

    step = tmp_path / "blockp.step"
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.model.occ.addBox(-50, -50, -20, 100, 100, 100)
    gmsh.model.occ.synchronize()
    gmsh.write(str(step))
    gmsh.finalize()
    pr = workspace.create_project("Trim", "external_aero")
    _wait(pr.import_step(step))
    _wait(pr.add_symmetry(axis="z", pos_mm=0, keep="+"))
    assert pr.data["symmetry"][0]["cut_parts"] is False
    dom = pr.data["geometry"]["info"]["domain"]
    assert abs(dom["box_min"][2]) < 1e-9 and dom["box_max"][2] > 0.1            # the air volume ends at the plane
    assert dom["spec"]["bbox_min"][2] == dom["box_min"][2]
    n_off = pr.data["geometry"]["info"]["n_faces"]
    _wait(pr.update_symmetry(pr.data["symmetry"][0]["id"], cut_parts=True))
    assert pr.data["symmetry"][0]["cut_parts"] is True
    assert pr.data["geometry"]["info"]["n_faces"] == n_off                       # the air around it is the same


def test_the_cut_parts_flag_survives_validation():
    pl = S.validate([dict(S.axis_plane("z", 0.0), cut_parts=True)])
    assert pl[0]["cut_parts"] is True
    assert S.make_plane([0, 0, 0], [0, 0, 1])["cut_parts"] is False
