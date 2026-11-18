"""Material library (SI units).

Solids: k [W/m/K], rho [kg/m3], cp [J/kg/K], E [Pa] Young's modulus, nu [-] Poisson's ratio, Sy [Pa] typical
minimum yield strength (None for brittle materials and composites) - room temperature, isotropic; laminates and FR4
use in-plane quasi-isotropic values. Check strengths against your supplier's datasheet.
Fluids: additionally mu [Pa s], Pr [-], beta [1/K] (thermal expansion), M [kg/kmol]
"""

SOLIDS = {
    "aluminium_6061": {"label": "Aluminium 6061", "k": 167.0, "rho": 2700.0, "cp": 896.0, "E": 68.9e9, "nu": 0.33,
                       "Sy": 276e6},  # T6
    "aluminium_6063": {"label": "Aluminium 6063 (extruded heatsinks)", "k": 201.0, "rho": 2690.0, "cp": 900.0,
                       "E": 68.3e9, "nu": 0.33, "Sy": 145e6},  # T5
    "copper": {"label": "Copper", "k": 390.0, "rho": 8940.0, "cp": 385.0, "E": 117e9, "nu": 0.34, "Sy": 70e6},  # annealed
    "steel": {"label": "Steel (carbon)", "k": 45.0, "rho": 7850.0, "cp": 490.0, "E": 200e9, "nu": 0.29, "Sy": 250e6},  # S235 / 1018
    "stainless_304": {"label": "Stainless steel 304", "k": 16.2, "rho": 8000.0, "cp": 500.0, "E": 193e9, "nu": 0.29,
                      "Sy": 215e6},
    "fr4": {"label": "FR4 (PCB, in-plane avg.)", "k": 0.8, "rho": 1850.0, "cp": 1100.0, "E": 22e9, "nu": 0.15, "Sy": None},
    "silicon": {"label": "Silicon (die)", "k": 148.0, "rho": 2330.0, "cp": 712.0, "E": 130e9, "nu": 0.28, "Sy": None},
    "abs": {"label": "ABS plastic", "k": 0.17, "rho": 1050.0, "cp": 1400.0, "E": 2.2e9, "nu": 0.35, "Sy": 40e6},
    "pla": {"label": "PLA plastic", "k": 0.13, "rho": 1240.0, "cp": 1800.0, "E": 3.5e9, "nu": 0.36, "Sy": 50e6},
    "carbon_fibre": {"label": "Carbon fibre composite", "k": 5.0, "rho": 1600.0, "cp": 900.0, "E": 50e9, "nu": 0.30, "Sy": None},
    "thermal_pad": {"label": "Thermal pad / TIM", "k": 3.0, "rho": 2500.0, "cp": 1000.0, "E": 1e6, "nu": 0.45, "Sy": None},
    "ceramic_alumina": {"label": "Alumina ceramic", "k": 30.0, "rho": 3900.0, "cp": 880.0, "E": 370e9, "nu": 0.22,
                        "Sy": None},
    "titanium_6al4v": {"label": "Titanium Ti-6Al-4V", "k": 6.7, "rho": 4430.0, "cp": 526.0, "E": 113.8e9, "nu": 0.34,
                       "Sy": 880e6},
    "magnesium_az31b": {"label": "Magnesium AZ31B", "k": 96.0, "rho": 1770.0, "cp": 1000.0, "E": 45e9, "nu": 0.35,
                        "Sy": 200e6},
    "pom": {"label": "POM / acetal plastic", "k": 0.31, "rho": 1410.0, "cp": 1465.0, "E": 2.8e9, "nu": 0.35,
            "Sy": 65e6},
    "nylon_pa6": {"label": "Nylon PA6 (dry)", "k": 0.25, "rho": 1140.0, "cp": 1700.0, "E": 2.7e9, "nu": 0.39,
                  "Sy": 70e6},
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
