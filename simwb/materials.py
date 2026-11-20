"""Material library (SI units).

Solids: k [W/m/K], rho [kg/m3], cp [J/kg/K], E [Pa] Young's modulus, nu [-] Poisson's ratio, alpha [1/K] linear
thermal expansion coefficient (mean value near room temperature), Sy [Pa] typical minimum yield strength (None for
brittle materials and composites) - room temperature, isotropic; laminates and FR4 use in-plane quasi-isotropic
values, and carbon fibre alpha (2e-6) is a quasi-isotropic laminate value (along the fibres it is about -0.5e-6,
across them 30e-6). Check strengths and expansion against your supplier's datasheet.
Fluids: additionally mu [Pa s], Pr [-], beta [1/K] (thermal expansion), M [kg/kmol]
"""

SOLIDS = {
    "aluminium_6061": {"label": "Aluminium 6061", "k": 167.0, "rho": 2700.0, "cp": 896.0, "E": 68.9e9, "nu": 0.33,
                       "alpha": 2.36e-05, "Sy": 276e6},  # T6
    "aluminium_6063": {"label": "Aluminium 6063 (extruded heatsinks)", "k": 201.0, "rho": 2690.0, "cp": 900.0,
                       "E": 68.3e9, "nu": 0.33, "alpha": 2.34e-05, "Sy": 145e6},  # T5
    "copper": {"label": "Copper", "k": 390.0, "rho": 8940.0, "cp": 385.0, "E": 117e9, "nu": 0.34, "alpha": 1.65e-05, "Sy": 70e6},  # annealed
    "steel": {"label": "Steel (carbon)", "k": 45.0, "rho": 7850.0, "cp": 490.0, "E": 200e9, "nu": 0.29, "alpha": 1.2e-05, "Sy": 250e6},  # S235 / 1018
    "stainless_304": {"label": "Stainless steel 304", "k": 16.2, "rho": 8000.0, "cp": 500.0, "E": 193e9, "nu": 0.29,
                      "alpha": 1.73e-05, "Sy": 215e6},
    "fr4": {"label": "FR4 (PCB, in-plane avg.)", "k": 0.8, "rho": 1850.0, "cp": 1100.0, "E": 22e9, "nu": 0.15, "alpha": 1.4e-05, "Sy": None},
    "silicon": {"label": "Silicon (die)", "k": 148.0, "rho": 2330.0, "cp": 712.0, "E": 130e9, "nu": 0.28, "alpha": 2.6e-06, "Sy": None},
    "abs": {"label": "ABS plastic", "k": 0.17, "rho": 1050.0, "cp": 1400.0, "E": 2.2e9, "nu": 0.35, "alpha": 9e-05, "Sy": 40e6},
    "pla": {"label": "PLA plastic", "k": 0.13, "rho": 1240.0, "cp": 1800.0, "E": 3.5e9, "nu": 0.36, "alpha": 6.8e-05, "Sy": 50e6},
    "carbon_fibre": {"label": "Carbon fibre composite", "k": 5.0, "rho": 1600.0, "cp": 900.0, "E": 50e9, "nu": 0.30, "alpha": 2e-06, "Sy": None},
    "thermal_pad": {"label": "Thermal pad / TIM", "k": 3.0, "rho": 2500.0, "cp": 1000.0, "E": 1e6, "nu": 0.45, "alpha": 0.00015, "Sy": None},
    "ceramic_alumina": {"label": "Alumina ceramic", "k": 30.0, "rho": 3900.0, "cp": 880.0, "E": 370e9, "nu": 0.22,
                        "alpha": 7.2e-06, "Sy": None},
    "titanium_6al4v": {"label": "Titanium Ti-6Al-4V", "k": 6.7, "rho": 4430.0, "cp": 526.0, "E": 113.8e9, "nu": 0.34,
                       "alpha": 8.6e-06, "Sy": 880e6},
    "magnesium_az31b": {"label": "Magnesium AZ31B", "k": 96.0, "rho": 1770.0, "cp": 1000.0, "E": 45e9, "nu": 0.35,
                        "alpha": 2.6e-05, "Sy": 200e6},
    "pom": {"label": "POM / acetal plastic", "k": 0.31, "rho": 1410.0, "cp": 1465.0, "E": 2.8e9, "nu": 0.35,
            "alpha": 0.00011, "Sy": 65e6},
    "nylon_pa6": {"label": "Nylon PA6 (dry)", "k": 0.25, "rho": 1140.0, "cp": 1700.0, "E": 2.7e9, "nu": 0.39,
                  "alpha": 8e-05, "Sy": 70e6},
    # orthotropic: E, nu, rho are the values along axis 1; "orthotropic" carries all nine constants (axis 1 = global x unless
    # the body sets its own axes). Use these for wood and composites; check against the supplier's data.
    "mahogany_agard445": {"label": "Mahogany, AGARD 445.6 weakened wing (orthotropic, axis 1 along the grain)",
                          "k": 0.15, "rho": 381.98, "cp": 1700.0, "E": 3.1511e9, "nu": 0.31, "Sy": None, "alpha": 5e-6,
                          "orthotropic": {"E1": 3.1511e9, "E2": 0.4162e9, "E3": 0.4162e9, "G12": 0.4392e9, "G23": 0.1e9,
                                          "G13": 0.4392e9, "nu12": 0.31, "nu23": 0.4, "nu13": 0.31}},
    "cfrp_unidirectional": {"label": "Carbon fibre / epoxy, unidirectional ply (orthotropic, axis 1 along the fibres)",
                            "k": 5.0, "rho": 1550.0, "cp": 900.0, "E": 135e9, "nu": 0.30, "Sy": None, "alpha": 0.5e-6,
                            "orthotropic": {"E1": 135e9, "E2": 10e9, "E3": 10e9, "G12": 5e9, "G23": 3.5e9, "G13": 5e9,
                                            "nu12": 0.30, "nu23": 0.45, "nu13": 0.30}},
}

FLUIDS = {
    "air": {
        "label": "Air (20 C, 1 atm)",
        "rho": 1.204, "mu": 1.825e-5, "cp": 1006.0, "k": 0.0257, "Pr": 0.713,
        "beta": 3.43e-3, "M": 28.96,
    },
    "water": {
        "label": "Water (20 C) - incompressible flow only",
        "rho": 998.2, "mu": 1.002e-3, "cp": 4182.0, "k": 0.598, "Pr": 7.01,
        "beta": 2.07e-4, "M": 18.0,
    },
}


def get_solid(name: str) -> dict:
    if name not in SOLIDS:
        raise KeyError(f"Unknown solid material '{name}'. Options: {', '.join(SOLIDS)}")
    return SOLIDS[name]


def get_fluid(name: str) -> dict:
    if name not in FLUIDS:
        raise KeyError(f"Unknown fluid '{name}'. Options: {', '.join(FLUIDS)}")
    return FLUIDS[name]


# ---- orthotropic elasticity (composites, wood, rolled or printed parts) ------------------------------------------
#
# A material may carry   "orthotropic": {"E1","E2","E3" [Pa], "G12","G23","G13" [Pa], "nu12","nu23","nu13" [-],
#                                        "axis1": [x,y,z], "axis2": [x,y,z]  (optional material axes),
#                                        "alpha1","alpha2","alpha3" [1/K] (optional thermal expansion per axis)}
# Material axis 1 is the stiff (fibre / grain / print) direction, 2 the in-plane transverse one, 3 the thickness.
# nu_ij is the strain in direction j caused by a stress in direction i (so nu12/E1 = nu21/E2, the usual composite
# convention). The axes default to the global x, y, z; axis2 is made perpendicular to axis1 (Gram-Schmidt) and axis3
# = axis1 x axis2. The same constants apply to the whole body (one fibre direction per body: a laminate with plies
# at different angles is not represented, use effective laminate constants).

_VOIGT = [(0, 0), (1, 1), (2, 2), (1, 2), (0, 2), (0, 1)]  # xx yy zz yz xz xy (the solvers' stress order)


def material_axes(orth: dict):
    """Rotation matrix R (rows = the material axes 1, 2, 3 in global components) of an orthotropic material."""
    import numpy as np

    e1 = np.asarray(orth.get("axis1") if orth.get("axis1") is not None else (1.0, 0.0, 0.0), float)
    e2 = np.asarray(orth.get("axis2") if orth.get("axis2") is not None else (0.0, 1.0, 0.0), float)
    if np.linalg.norm(e1) < 1e-12:
        raise ValueError("The orthotropic material axis 1 is the zero vector.")
    e1 = e1 / np.linalg.norm(e1)
    e2 = e2 - (e2 @ e1) * e1
    if np.linalg.norm(e2) < 1e-9:
        raise ValueError("The orthotropic material axes 1 and 2 are parallel: give two different directions.")
    e2 = e2 / np.linalg.norm(e2)
    return np.array([e1, e2, np.cross(e1, e2)])


def orthotropic_compliance(orth: dict):
    """6x6 compliance in the material axes (Voigt 1 2 3 23 13 12, engineering shear strains)."""
    import numpy as np

    try:
        E1, E2, E3 = (float(orth[k]) for k in ("E1", "E2", "E3"))
        G12, G23, G13 = (float(orth[k]) for k in ("G12", "G23", "G13"))
        n12, n23, n13 = (float(orth[k]) for k in ("nu12", "nu23", "nu13"))
    except KeyError as e:
        raise ValueError(f"Orthotropic material: constant {e} is missing "
                         "(E1 E2 E3 G12 G23 G13 nu12 nu23 nu13 are needed).") from e
    if min(E1, E2, E3, G12, G23, G13) <= 0:
        raise ValueError("Orthotropic material: moduli must be positive.")
    S = np.zeros((6, 6))
    S[0, 0], S[1, 1], S[2, 2] = 1 / E1, 1 / E2, 1 / E3
    S[0, 1] = S[1, 0] = -n12 / E1
    S[0, 2] = S[2, 0] = -n13 / E1
    S[1, 2] = S[2, 1] = -n23 / E2
    S[3, 3], S[4, 4], S[5, 5] = 1 / G23, 1 / G13, 1 / G12
    if np.linalg.eigvalsh(S).min() <= 0:
        raise ValueError("Orthotropic material: the constants are not physically possible (the stiffness would not "
                         "be positive definite - check the Poisson's ratios against E1/E2/E3: nu12^2 < E1/E2 and so "
                         "on).")
    return S


def _to_global_tensor(Cv, R):
    """Rotate a 6x6 stiffness (Voigt, material axes) to the global axes: C'_ijkl = R_pi R_qj R_rk R_sl C_pqrs."""
    import numpy as np

    C = np.zeros((3, 3, 3, 3))
    for I, (p, q) in enumerate(_VOIGT):
        for J, (r, s) in enumerate(_VOIGT):
            for a, b in ((p, q), (q, p)):
                for c, d in ((r, s), (s, r)):
                    C[a, b, c, d] = Cv[I, J]
    Cg = np.einsum("pi,qj,rk,sl,pqrs->ijkl", R, R, R, R, C, optimize=True)
    return np.array([[Cg[i, j, k, l] for (k, l) in _VOIGT] for (i, j) in _VOIGT])


def orthotropic_stiffness(orth: dict):
    """6x6 stiffness D (Pa) in the GLOBAL axes: stress (xx yy zz yz xz xy) = D engineering strain (same order)."""
    import numpy as np

    C = np.linalg.inv(orthotropic_compliance(orth))
    D = _to_global_tensor(C, material_axes(orth))
    return 0.5 * (D + D.T)


def expansion_voigt(alpha, orth=None):
    """Thermal strain per kelvin as a Voigt vector (xx yy zz yz xz xy, engineering shear) in the global axes.
    alpha: a number (isotropic) or, for an orthotropic material, three values along its material axes."""
    import numpy as np

    a = np.atleast_1d(np.asarray(alpha, float))
    if a.size == 1:
        return np.array([a[0], a[0], a[0], 0.0, 0.0, 0.0])
    R = material_axes(orth or {})
    T = R.T @ np.diag(a[:3]) @ R
    return np.array([T[0, 0], T[1, 1], T[2, 2], 2 * T[1, 2], 2 * T[0, 2], 2 * T[0, 1]])


def orthotropic_alpha(orth: dict):
    """The three expansion coefficients of an orthotropic material (None if it gives none)."""
    k = [orth.get(f"alpha{i}") for i in (1, 2, 3)]
    return None if any(v is None for v in k) else [float(v) for v in k]
