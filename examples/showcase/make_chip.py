"""Showcase geometry: a BGA processor package with a copper lid and a plate-fin heatsink on a PCB (millimetres).

    python examples/showcase/make_chip.py

Writes two STEP files with exactly the same parts (only the air volume differs):
    chip_passive.step  parts only        (natural convection: the workbench puts an automatic air box around them)
    chip_fan.step      parts + "Air duct" (forced convection: a fan pushes air through the duct, +x direction)

Parts (z up; PCB centred on the origin in x/y):
    PCB            100 x 70 x 1.6   FR4
    BGA substrate   35 x 35 x 1.2   organic package substrate (the solder-ball layer is not modelled)
    Silicon die     15 x 15 x 0.78  the heat source
    Copper lid      35 x 35 x 2.0   heat spreader on top of the die (0.78 mm air gap round the die)
    Heatsink        55 x 55 base 3 mm + 12 fins, 1.2 mm thick, 30 mm tall (extruded aluminium, fins along x)
The thermal interface materials (die/lid, lid/heatsink) are the study's contact resistance, not meshed layers.
"""
import re
import sys
from pathlib import Path

import gmsh

HERE = Path(__file__).parent

# geometry parameters (mm)
PCB = dict(x=100.0, y=70.0, t=1.6)
SUB = dict(a=35.0, t=1.2)
DIE = dict(a=15.0, t=0.78)
LID = dict(a=35.0, t=2.0)
HS = dict(a=55.0, base=3.0, n_fins=12, fin_t=1.2, fin_h=30.0)
DUCT = dict(x0=-80.0, x1=180.0, h=42.0)  # y spans the PCB width; floor = PCB underside


def parts():
    occ = gmsh.model.occ
    out = []  # (name, tag)
    z = 0.0
    pcb = occ.addBox(-PCB["x"] / 2, -PCB["y"] / 2, z, PCB["x"], PCB["y"], PCB["t"])
    out.append(("PCB", pcb))
    z += PCB["t"]
    sub = occ.addBox(-SUB["a"] / 2, -SUB["a"] / 2, z, SUB["a"], SUB["a"], SUB["t"])
    out.append(("BGA substrate", sub))
    z += SUB["t"]
    die = occ.addBox(-DIE["a"] / 2, -DIE["a"] / 2, z, DIE["a"], DIE["a"], DIE["t"])
    out.append(("Silicon die", die))
    z += DIE["t"]
    lid = occ.addBox(-LID["a"] / 2, -LID["a"] / 2, z, LID["a"], LID["a"], LID["t"])
    out.append(("Copper lid", lid))
    z += LID["t"]
    a = HS["a"]
    base = occ.addBox(-a / 2, -a / 2, z, a, a, HS["base"])
    n, t = HS["n_fins"], HS["fin_t"]
    pitch = (a - t) / (n - 1)
    fins = [(3, occ.addBox(-a / 2, -a / 2 + i * pitch, z + HS["base"], a, t, HS["fin_h"])) for i in range(n)]
    hs, _ = occ.fuse([(3, base)], fins)
    out.append(("Heatsink", hs[0][1]))
    return out, z + HS["base"] + HS["fin_h"]


def build(name, duct):
    gmsh.clear()
    gmsh.model.add(name)
    occ = gmsh.model.occ
    ps, ztop = parts()
    names = [n for n, _ in ps]
    tags = [t for _, t in ps]
    if duct:
        d = occ.addBox(DUCT["x0"], -PCB["y"] / 2, 0.0, DUCT["x1"] - DUCT["x0"], PCB["y"], DUCT["h"])
        names.append("Air duct")
        tags.append(d)
        assert DUCT["h"] > ztop, "duct lower than the heatsink"
    occ.synchronize()
    for n, t in zip(names, tags):
        gmsh.model.setEntityName(3, t, n)
    path = HERE / f"{name}.step"
    order = [t for _, t in gmsh.model.getEntities(3)]
    nm = [gmsh.model.getEntityName(3, t) for t in order]
    gmsh.write(str(path))
    text = path.read_text()
    text = re.sub(r"'Open CASCADE STEP translator [\d.]+ \d+\.(\d+)'", lambda m: "'" + nm[int(m.group(1)) - 1] + "'", text)
    path.write_text(text)
    vols = {gmsh.model.getEntityName(3, t): round(occ.getMass(3, t), 1) for t in order}
    print("wrote", path, vols)


if __name__ == "__main__":
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    build("chip_passive", duct=False)
    build("chip_fan", duct=True)
    gmsh.finalize()
