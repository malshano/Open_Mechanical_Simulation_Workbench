"""CalculiX (https://www.calculix.de) as an additional structural solver, next to the built-in FEM.

The workbench never links CalculiX: it writes an Abaqus-style input deck (inputdeck.py), runs the separate ``ccx``
program (runner.py) and reads the .frd / .dat it writes (results.py); installer.py fetches the program on request.
CalculiX is GPL software by Guido Dhondt: it stays a separate download, it is not part of this project's code.

Same mesh, materials, boundary conditions and result format as simwb.solvers.static_fem / modal_fem, so a study can
be solved by either and the two compared.
"""
from __future__ import annotations

from .api import solve_modal, solve_static  # noqa: F401
