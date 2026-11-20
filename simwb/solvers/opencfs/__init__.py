"""openCFS (https://opencfs.org) as an optional engine: coupled-field finite elements (acoustics, mechanics, piezo,
heat, electromagnetics), MIT licence, a separate download.

The workbench never links it: it writes a Gmsh mesh with named regions (meshio.py), the simulation and material XML
(xmlwriter.py), runs the separate ``cfs`` program (api.py) and reads the ASCII Gmsh result it writes (results.py);
installer.py fetches the portable Windows build on request (no admin rights, no WSL). First study: harmonic
vibro-acoustics (solve.py).
"""
from __future__ import annotations

from .solve import check_setup, solve_vibroacoustics, spl_db  # noqa: F401
