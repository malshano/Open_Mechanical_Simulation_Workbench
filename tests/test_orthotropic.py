"""Orthotropic materials (fibre / grain direction per body) in the built-in static and modal solvers: the isotropic
limit, beam and plate formulas with the stiff direction along and across the part, rotated material axes, thermal
expansion per axis and the CalculiX deck."""
import numpy as np
import pytest

from simwb import materials
from simwb.solvers import calculix, modal_fem, static_fem
from simwb.solvers.calculix import inputdeck

from test_calculix import quiet
from test_thermal_stress import bc, box

E, NU = 70e9, 0.33
G = E / (2 * (1 + NU))
RHO = 1600.0


def ortho(E1=100e9, E2=5e9, E3=5e9, G12=4e9, G23=2e9, G13=4e9, nu12=0.3, nu23=0.4, nu13=0.3, **extra):
    d = dict(E1=E1, E2=E2, E3=E3, G12=G12, G23=G23, G13=G13, nu12=nu12, nu23=nu23, nu13=nu13)
    d.update(extra)
    return d


def body(orth=None, **kw):
    d = {"E": E, "nu": NU, "rho": RHO, "yield": None, "name": "o"}
    if orth is not None:
        d = {"rho": RHO, "yield": None, "name": "o", "orthotropic": orth}
    d.update(kw)
    return d


def iso_as_ortho(**extra):
    return ortho(E, E, E, G, G, G, NU, NU, NU, **extra)


# ---- the material --------------------------------------------------------------------------------------------------

def test_isotropic_constants_give_lame_stiffness_in_any_axes():
    lam, mu = E * NU / ((1 + NU) * (1 - 2 * NU)), G
    D = materials.orthotropic_stiffness(iso_as_ortho(axis1=[1, 2, 3], axis2=[0, 1, -1]))
    ref = np.zeros((6, 6))
    ref[:3, :3] = lam
    ref[:3, :3] += 2 * mu * np.eye(3)
    ref[3:, 3:] = mu * np.eye(3)
    assert D == pytest.approx(ref, rel=1e-9, abs=1e-3)


def test_rotating_the_material_axes_swaps_the_stiffness():
    o = ortho()
    D0 = materials.orthotropic_stiffness(o)
    D90 = materials.orthotropic_stiffness(dict(o, axis1=[0, 1, 0], axis2=[-1, 0, 0]))  # 90 degrees about z
    assert D90[0, 0] == pytest.approx(D0[1, 1]) and D90[1, 1] == pytest.approx(D0[0, 0])
    assert D90[0, 2] == pytest.approx(D0[1, 2]) and D90[1, 2] == pytest.approx(D0[0, 2])
    assert D90[4, 4] == pytest.approx(D0[3, 3]) and D90[3, 3] == pytest.approx(D0[4, 4])  # G13 <-> G23
    assert D90[5, 5] == pytest.approx(D0[5, 5])
    assert D0 == pytest.approx(D0.T) and np.linalg.eigvalsh(D0).min() > 0


def test_impossible_constants_and_axes_are_refused():
    with pytest.raises(ValueError, match="not physically possible"):
        materials.orthotropic_stiffness(ortho(nu12=0.9, E1=10e9, E2=100e9))  # nu12^2 < E1/E2 violated
    with pytest.raises(ValueError, match="parallel"):
        materials.orthotropic_stiffness(ortho(axis1=[1, 0, 0], axis2=[2, 0, 0]))
    with pytest.raises(ValueError, match="missing"):
        materials.orthotropic_stiffness({"E1": 1e9})


# ---- isotropic limit in the solvers ---------------------------------------------------------------------------------

def test_isotropic_limit_equals_the_isotropic_solution_static_and_modal():
    m = box(8, 2, 2, 0.2, 0.03, 0.03)
    bcs = [bc("w", "fixed_support", [1]), bc("l", "force", [2], fz_N=-300.0)]
    a = static_fem.solve(m, {1: body()}, bcs, gravity=[0, 0, -9.81], log=quiet)
    b = static_fem.solve(m, {1: body(iso_as_ortho(axis1=[1, 1, 0], axis2=[0, 0, 1]))}, bcs, gravity=[0, 0, -9.81],
                         log=quiet)
    assert b["displacement"] == pytest.approx(a["displacement"], rel=1e-8, abs=1e-14)
    assert b["von_mises"] == pytest.approx(a["von_mises"], rel=1e-7)
    assert b["report"]["stress_error_percent"] == pytest.approx(a["report"]["stress_error_percent"], rel=1e-6)
    ma = modal_fem.solve(m, {1: body()}, bcs[:1], n_modes=4, band_hz=(0, 1e9), log=quiet)
    mb = modal_fem.solve(m, {1: body(iso_as_ortho())}, bcs[:1], n_modes=4, band_hz=(0, 1e9), log=quiet)
    assert [x["frequency_Hz"] for x in mb["modes"]] == pytest.approx([x["frequency_Hz"] for x in ma["modes"]], rel=1e-9)


def test_mixed_isotropic_and_orthotropic_bodies():
    m = box(8, 2, 2, 0.2, 0.03, 0.03, body=lambda c: 1 if c[0] < 0.1 else 2)
    bcs = [bc("w", "fixed_support", [1]), bc("l", "force", [2], fz_N=-300.0)]
    a = static_fem.solve(m, {1: body(), 2: body()}, bcs, log=quiet)
    b = static_fem.solve(m, {1: body(), 2: body(iso_as_ortho())}, bcs, log=quiet)
    assert b["displacement"] == pytest.approx(a["displacement"], rel=1e-8, abs=1e-14)


# ---- beam theory ---------------------------------------------------------------------------------------------------

def timoshenko_tip(P, L, Ex, Gs, b, t):
    """Tip deflection of a cantilever (bending in the t direction): bending + shear (kappa = 5/6 rectangle)."""
    I, A = b * t ** 3 / 12, b * t
    return P * L ** 3 / (3 * Ex * I) + P * L / ((5 / 6) * Gs * A)


def test_cantilever_first_bending_frequency_with_E1_along_the_beam():
    L, h = 0.4, 0.02
    m = box(24, 2, 2, L, h, h)
    o = ortho(E1=120e9, E2=8e9, E3=8e9, G12=4e9, G13=4e9, G23=2.5e9)
    r = modal_fem.solve(m, {1: body(o)}, [bc("w", "fixed_support", [1])], n_modes=4, band_hz=(0, 1e9), log=quiet)
    I, A = h ** 4 / 12, h * h
    lam = 1.8751041
    f_eb = lam ** 2 / (2 * np.pi) * np.sqrt(o["E1"] * I / (RHO * A * L ** 4))
    # Timoshenko correction (shear + rotary inertia) of the lowest mode, first order: 1 / sqrt(1 + lam^2 r^2/L^2 (1 + E/(kG)))
    corr = 1 / np.sqrt(1 + lam ** 2 * (I / A) / L ** 2 * (1 + o["E1"] / ((5 / 6) * o["G13"])))
    f = r["report"]["first_frequency_Hz"]
    assert f == pytest.approx(f_eb * corr, rel=0.02)
    assert f < f_eb  # (shear flexibility lowers it)
    # the two lateral bending modes are the same (E2 = E3, G12 = G13): a doublet
    f2 = r["modes"][1]["frequency_Hz"]
    assert f2 == pytest.approx(f, rel=0.02)


def test_stiff_direction_across_the_beam_is_far_more_flexible():
    """Material axis 1 along y instead of x: the beam's own stiffness is E2. Same mesh, frequency ratio ~ sqrt(E2/E1)."""
    L, h = 0.4, 0.02
    m = box(24, 2, 2, L, h, h)
    o = ortho(E1=120e9, E2=8e9, E3=8e9, G12=4e9, G13=4e9, G23=2.5e9)
    fx = modal_fem.solve(m, {1: body(o)}, [bc("w", "fixed_support", [1])], n_modes=2, band_hz=(0, 1e9),
                         log=quiet)["report"]["first_frequency_Hz"]
    fy = modal_fem.solve(m, {1: body(dict(o, axis1=[0, 1, 0], axis2=[1, 0, 0]))}, [bc("w", "fixed_support", [1])],
                         n_modes=2, band_hz=(0, 1e9), log=quiet)["report"]["first_frequency_Hz"]
    assert fy / fx == pytest.approx(np.sqrt(o["E2"] / o["E1"]), rel=0.06)  # (shear flexibility moves the ratio a little)


def test_orthotropic_plate_strip_deflection_follows_the_orthotropic_beam_formula():
    """A thin strip clamped at one end, tip load; E1 along the length (stiff) and, with the axes turned, E2 along
    it: Euler-Bernoulli bending with the modulus of that direction plus Timoshenko shear."""
    L, b, t, P = 0.2, 0.02, 0.004, 20.0
    m = box(20, 2, 2, L, b, t)
    o = ortho(E1=140e9, E2=7e9, E3=7e9, G12=4.5e9, G13=4.5e9, G23=2.5e9, nu12=0.3, nu13=0.3, nu23=0.4)
    bcs = [bc("w", "fixed_support", [1]), bc("l", "force", [2], fz_N=-P)]
    stiff = static_fem.solve(m, {1: body(o)}, bcs, log=quiet)["report"]["max_displacement_mm"] * 1e-3
    assert stiff == pytest.approx(timoshenko_tip(P, L, o["E1"], o["G13"], b, t), rel=0.04)
    # axis 1 along y (across the width), axis 2 along x: stiffness along the strip is E2, shear in x-z is G23
    flex = static_fem.solve(m, {1: body(dict(o, axis1=[0, 1, 0], axis2=[1, 0, 0]))}, bcs,
                            log=quiet)["report"]["max_displacement_mm"] * 1e-3
    assert flex == pytest.approx(timoshenko_tip(P, L, o["E2"], o["G23"], b, t), rel=0.06)
    assert flex / stiff > 10  # (about E1/E2 = 20, less the shear share)


def test_stress_of_an_orthotropic_bar_in_tension_is_the_applied_one():
    L, b = 0.2, 0.02
    m = box(8, 2, 2, L, b, b)
    o = ortho()
    # a frictionless-supported bar pulled along x: sigma = F / A whatever the stiffness
    bcs = [bc("x", "frictionless_support", [1]), bc("y", "frictionless_support", [3]), bc("z", "frictionless_support", [5]),
           bc("pull", "force", [2], fx_N=4000.0)]
    r = static_fem.solve(m, {1: body(o)}, bcs, log=quiet)
    assert r["report"]["max_von_mises_MPa"] == pytest.approx(4000.0 / b ** 2 / 1e6, rel=0.01)
    # strain along x is sigma / E1
    assert r["displacement"][:, 0].max() == pytest.approx(4000.0 / b ** 2 / o["E1"] * L, rel=0.01)
    assert r["report"]["force_balance_error"] < 1e-6


# ---- thermal expansion per axis ------------------------------------------------------------------------------------

def test_orthotropic_free_expansion_uses_the_expansion_along_each_material_axis():
    m = box(4, 2, 2, 0.2, 0.05, 0.05)
    dT = 100.0
    T = np.full(len(m["nodes"]), 20.0 + dT)
    free = [bc(f"f{f}", "frictionless_support", [f]) for f in (1, 3, 5)]
    a1, a2, a3 = 2e-6, 30e-6, 40e-6
    for axes, exp in (({}, (a1, a2, a3)), ({"axis1": [0, 1, 0], "axis2": [1, 0, 0]}, (a2, a1, a3))):
        o = ortho(**axes, alpha1=a1, alpha2=a2, alpha3=a3)
        r = static_fem.solve(m, {1: body(o)}, free, log=quiet, thermal={"T_C": T, "T_ref_C": 20.0})
        u = r["displacement"]
        for k in range(3):
            assert u[:, k] == pytest.approx(exp[k] * dT * m["nodes"][:, k], rel=1e-6, abs=1e-12)
        assert r["report"]["max_von_mises_MPa"] < 1e-3  # a free orthotropic body expands without stress


def test_orthotropic_thermal_restraint_stress_along_the_fibre():
    """Restrained along x only (frictionless ends, free sides): sigma_x = -E1 alpha1 dT."""
    m = box(6, 2, 2, 0.2, 0.04, 0.04)
    o = ortho(alpha1=3e-6, alpha2=30e-6, alpha3=30e-6)
    bcs = [bc("e", "frictionless_support", [1, 2]), bc("y", "frictionless_support", [3]),
           bc("z", "frictionless_support", [5])]
    dT = 80.0
    r = static_fem.solve(m, {1: body(o)}, bcs, log=quiet,
                         thermal={"T_C": np.full(len(m["nodes"]), 20 + dT), "T_ref_C": 20.0})
    assert r["report"]["min_principal_MPa"] == pytest.approx(-o["E1"] * 3e-6 * dT / 1e6, rel=0.02)


# ---- CalculiX deck -------------------------------------------------------------------------------------------------

def test_calculix_deck_for_an_orthotropic_body():
    m = box(2, 1, 1, 0.1, 0.02, 0.02)
    o = ortho(axis1=[0, 1, 0], axis2=[1, 0, 0], alpha1=2e-6, alpha2=30e-6, alpha3=40e-6)
    sup = [bc("w", "fixed_support", [1])]
    d = inputdeck.build(m, {1: body(o)}, sup, "static", log=quiet,
                        thermal={"T_C": np.full(len(m["nodes"]), 50.0), "T_ref_C": 20.0, "alpha": {}})
    txt = d.text()
    assert "*ELASTIC, TYPE=ENGINEERING CONSTANTS\n1.000000000e+11, 5.000000000e+09, 5.000000000e+09, 0.300000, 0.300000, " \
           "0.400000, 4.000000000e+09, 4.000000000e+09\n2.000000000e+09, 0." in txt
    assert "*ORIENTATION, NAME=OR1\n0.000000000, 1.000000000, 0.000000000, 1.000000000, 0.000000000, 0.000000000" in txt
    assert "*SOLID SECTION, ELSET=B1, MATERIAL=M1, ORIENTATION=OR1" in txt
    assert "*EXPANSION, TYPE=ORTHO, ZERO=20\n2.000000000e-06, 3.000000000e-05, 4.000000000e-05, 0." in txt
    assert "*ELASTIC\n" not in txt
    assert "ENGINEERING CONSTANTS" in inputdeck.build(m, {1: body(o)}, sup, "modal", log=quiet).text()
    assert calculix  # (package import)
