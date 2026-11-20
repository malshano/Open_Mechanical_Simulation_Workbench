"""OpenFOAM lumped-point coupling helpers (simwb.solvers.openfoam.lumped): the parts that need no solver."""
import threading

import numpy as np
import pytest

from simwb.solvers.openfoam import lumped as L

FORCES = """////////
time            0.00050249962;
prevTime        0;

points
2
(
(0.064 0 0)
(0.087795 0.1 0)
);

forces
2
(
(-0.03 -0.018 0.3195)
(-0.068 0.0216 0.7938)
);

moments
2
(
(0.001 0.002 0.003)
(0.004 0.005 0.006)
);
"""


@pytest.mark.parametrize("w", [(0.0, 0.01, 0.0), (0.01, 0.0, 0.0), (0.0, 0.0, 0.02), (0.004, -0.007, 0.003), (0, 0, 0)])
def test_euler_zxz_is_a_small_rotation_by_the_vector(w):
    R = L.matrix_zxz(L.euler_zxz(w))
    skew = np.array([[0, -w[2], w[1]], [w[2], 0, -w[0]], [-w[1], w[0], 0.0]])
    assert np.allclose(R, np.eye(3) + skew, atol=3e-4)  # (the neglected terms are of second order)
    assert np.allclose(R.T @ R, np.eye(3), atol=1e-12) and np.linalg.det(R) == pytest.approx(1.0)


def test_positive_rotation_about_y_lifts_the_leading_edge():
    """Measured with OpenFOAM: +y rotation moves a point behind the node (x > 0) down and one in front of it up, so
    for flow along +x a positive rotation about +y is nose up."""
    R = L.matrix_zxz(L.euler_zxz((0.0, 0.01, 0.0)))
    assert (R @ [0.1, 0, 0])[2] < 0 and (R @ [-0.1, 0, 0])[2] > 0
    assert (R @ [0.1, 0, 0])[2] == pytest.approx(-0.001, rel=1e-3)


def test_euler_angles_are_continuous_through_zero_rotation():
    a = L.matrix_zxz(L.euler_zxz((1e-6, 2e-6, 0)))
    b = L.matrix_zxz(L.euler_zxz((-1e-6, -2e-6, 0)))
    assert np.allclose(a, np.eye(3), atol=1e-5) and np.allclose(b, np.eye(3), atol=1e-5)


def test_parse_forces_and_format_positions_round_trip():
    d = L.parse_forces(FORCES)
    assert d["time"] == pytest.approx(0.00050249962)
    assert d["points"].shape == d["forces"].shape == d["moments"].shape == (2, 3)
    assert d["forces"][1, 2] == pytest.approx(0.7938) and d["moments"][0, 2] == pytest.approx(0.003)
    txt = L.format_positions(d["time"], d["points"], np.zeros((2, 3)))
    assert txt.startswith("time 0.00050249962;\npoints\n(\n(0.064 0 0)\n") and "angles" in txt
    with pytest.raises(ValueError):
        L.parse_forces("time 1;\n")
    with pytest.raises(ValueError):
        L.format_positions(0.0, np.zeros((2, 3)), np.zeros((3, 3)))


def _fake_case(tmp_path):
    (tmp_path / "system").mkdir()
    (tmp_path / "0").mkdir()
    (tmp_path / "constant").mkdir()
    (tmp_path / "0" / "pointDisplacement").write_text(
        "boundaryField\n{\n    \".*\"\n    {\n        type            fixedValue;\n        value           uniform (0 0 0);\n    }\n"
        "    wing\n    {\n        type            oscillatingDisplacement;\n        amplitude       (0 0 0);\n"
        "        omega           62.8;\n        value           uniform (0 0 0);\n    }\n}\n")
    (tmp_path / "system" / "controlDict").write_text("application     pimpleFoam;\nstartFrom latestTime;\n")
    (tmp_path / "constant" / "dynamicMeshDict").write_text(
        "updateControl    timeStep;\nupdateInterval   1;\ndynamicFvMesh   dynamicMotionSolverFvMesh;\n")
    return tmp_path


def test_write_setup_patches_the_case(tmp_path):
    case = _fake_case(tmp_path)
    L.write_setup(case, [(0, 0, 0), (0.1, 0.5, 0), (0.2, 1.0, 0)], patch="wing", rho=1.204, interval=0.0005)
    pd = (case / "0" / "pointDisplacement").read_text()
    assert "lumpedPointDisplacement" in pd and "controllers     (wing)" in pd and "oscillatingDisplacement" not in pd
    assert pd.count('".*"') == 1  # the other patches are untouched
    assert "libs            (lumpedPointMotion);" in (case / "system" / "controlDict").read_text()
    dm = (case / "constant" / "dynamicMeshDict").read_text()
    assert dm.count("updateControl") == 1 and "runTime" in dm and "0.0005" in dm
    mv = (case / "system" / "lumpedPointMovement").read_text()
    assert "rhoRef 1.204" in mv and "waitInterval 1;" in mv
    assert "pointLabels (0 1 2)" in (case / "system" / "lumpedPointControllers").read_text()
    L.write_setup(case, [(0, 0, 0), (0.1, 0.5, 0)], patch="wing")  # again, now every step: nothing is duplicated
    dm = (case / "constant" / "dynamicMeshDict").read_text()
    assert dm.count("updateControl") == 1 and "timeStep" in dm and "runTime" not in dm
    assert (case / "system" / "controlDict").read_text().count("lumpedPointMotion") == 1
    with pytest.raises(ValueError):
        L.write_setup(case, [(0, 0, 0)], patch="nothere")


def test_slave_takes_turns_through_the_lock_file(tmp_path):
    s = L.Slave(tmp_path / "comms", poll=0.001)
    (tmp_path / "comms").mkdir()
    s.lock.write_text("status=slave\n")  # OpenFOAM's turn: the structure waits
    assert s.wait_turn(timeout=0.05) is False
    threading.Timer(0.05, s.lock.unlink).start()  # OpenFOAM removes the lock: the structure's turn
    assert s.wait_turn(timeout=2.0) is True
    (tmp_path / "comms" / "forces.out").write_text(FORCES)
    assert s.read_forces()["forces"].shape == (2, 3)
    s.respond(0.001, np.zeros((2, 3)), np.zeros((2, 3)))
    assert s.lock.exists() and (tmp_path / "comms" / "positions.in").read_text().startswith("time 0.001;")
    assert s.wait_turn(alive=lambda: False) is False


def test_write_setup_default_forces_block_is_unchanged_and_compressible_uses_the_density_field(tmp_path):
    (tmp_path / "inc").mkdir()
    case = _fake_case(tmp_path / "inc")
    L.write_setup(case, [(0, 0, 0), (0.1, 0.5, 0)], patch="wing", rho=1.204)
    mv = (case / "system" / "lumpedPointMovement").read_text()
    assert "forces\n{\n    p p;\n    pRef 0;\n    rhoRef 1.204;\n}\n" in mv and "rho rho" not in mv
    (tmp_path / "comp").mkdir()
    case2 = _fake_case(tmp_path / "comp")
    L.write_setup(case2, [(0, 0, 0), (0.1, 0.5, 0)], patch="wing", rho_name="rho", p_ref=101325.0)
    mv2 = (case2 / "system" / "lumpedPointMovement").read_text()
    assert "forces\n{\n    p p;\n    rho rho;\n    pRef 101325;\n}\n" in mv2 and "rhoRef" not in mv2
    # only the forces block differs
    assert mv.replace("forces\n{\n    p p;\n    pRef 0;\n    rhoRef 1.204;\n}\n", "") == \
        mv2.replace("forces\n{\n    p p;\n    rho rho;\n    pRef 101325;\n}\n", "")
