"""STEP files (millimetres) for the first-principles validation studies of the showcase.

    python examples/showcase/make_validation.py

validation_cantilever.step  200 x 20 x 3 mm bar (x = length), for the cantilever / free-free beam modal checks
validation_slab.step        100 x 20 x 20 mm bar (x = length), for the 1-D heat conduction checks
validation_bar.step         200 x 10 x 10 mm square bar (x = length), for the free-free bending / axial modal checks
"""
import re
from pathlib import Path

import gmsh

HERE = Path(__file__).parent


def write(name, solid_name):
    """Write STEP and give the solid a proper product name (gmsh writes a generic translator name)."""
    gmsh.model.occ.synchronize()
    path = HERE / f"{name}.step"
    gmsh.write(str(path))
    text = path.read_text()
    text = re.sub(r"'Open CASCADE STEP translator [\d.]+ \d+'", "'" + solid_name + "'", text)
    path.write_text(text)
    print("wrote", path)


def box(name, solid_name, lx, ly, lz):
    gmsh.clear()
    gmsh.model.add(name)
    b = gmsh.model.occ.addBox(0, 0, 0, lx, ly, lz)
    gmsh.model.occ.synchronize()
    gmsh.model.setEntityName(3, b, solid_name)
    write(name, solid_name)


if __name__ == "__main__":
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    box("validation_cantilever", "Beam", 200, 20, 3)
    box("validation_slab", "Slab", 100, 20, 20)
    box("validation_bar", "Bar", 200, 10, 10)
    gmsh.finalize()
