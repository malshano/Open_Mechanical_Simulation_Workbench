"""Generate example STEP files (in millimetres) for the reference studies.

    python examples/make_examples.py
"""
import math
from pathlib import Path

import gmsh

HERE = Path(__file__).parent


def _start(name):
    gmsh.clear()
    gmsh.model.add(name)


def _write(name):
    """Write STEP and give each solid a proper product name (gmsh writes generic translator names)."""
    import re

    gmsh.model.occ.synchronize()
    path = HERE / f"{name}.step"
    names = [gmsh.model.getEntityName(3, t) or f"Body {i + 1}" for i, (_, t) in enumerate(gmsh.model.getEntities(3))]
    gmsh.write(str(path))
    text = path.read_text()
    text = re.sub(r"'Open CASCADE STEP translator [\d.]+ \d+\.(\d+)'",
                  lambda m: "'" + names[int(m.group(1)) - 1] + "'", text)
    if len(names) == 1:  # a single solid is the root product itself (no ".N" suffix)
        text = re.sub(r"'Open CASCADE STEP translator [\d.]+ \d+'", "'" + names[0] + "'", text)
    path.write_text(text)
    print("wrote", path, names)


def heated_plate():
    """Aluminium plate with a 10 W power resistor block on top."""
    _start("heated_plate")
    occ = gmsh.model.occ
    plate = occ.addBox(0, 0, 0, 100, 60, 3)
    block = occ.addBox(40, 20, 3, 20, 20, 4)
    occ.synchronize()
    gmsh.model.setEntityName(3, plate, "Aluminium plate")
    gmsh.model.setEntityName(3, block, "Power resistor")
    _write("heated_plate")


def heatsink_in_duct():
    """Finned heatsink on a chip, inside a rectangular duct (the duct body is the air volume)."""
    _start("heatsink_duct")
    occ = gmsh.model.occ
    # outlet 255 mm (> 5 duct hydraulic diameters) behind the heatsink so the pressure drop is position-independent
    duct = occ.addBox(0, 0, 0, 360, 60, 40)
    chip = occ.addBox(65, 15, 0, 30, 30, 2)
    base = occ.addBox(55, 5, 2, 50, 50, 4)
    fins = []
    n, t, h = 8, 1.5, 24
    pitch = (50 - t) / (n - 1)
    for i in range(n):
        fins.append(occ.addBox(55, 5 + i * pitch, 6, 50, t, h))
    hs, _ = occ.fuse([(3, base)], [(3, f) for f in fins])
    occ.synchronize()
    gmsh.model.setEntityName(3, duct, "Air duct")
    gmsh.model.setEntityName(3, chip, "Chip")
    gmsh.model.setEntityName(3, hs[0][1], "Heatsink")
    _write("heatsink_duct")


def quadcopter():
    """Simple X-quadcopter: hub, four arms, four motor pods (props modelled as actuator disks)."""
    _start("quadcopter")
    occ = gmsh.model.occ
    hub = occ.addBox(-60, -45, -20, 120, 90, 40)
    ents = [hub]
    arm_len, arm_r = 190, 8
    motor_r, motor_h = 15, 22
    names = ["Frame"]
    for k in range(4):
        a = math.radians(45 + 90 * k)
        dx, dy = math.cos(a), math.sin(a)
        arm = occ.addCylinder(0, 0, 0, dx * arm_len, dy * arm_len, 0, arm_r)
        mx, my = dx * arm_len, dy * arm_len
        motor = occ.addCylinder(mx, my, -motor_h / 2, 0, 0, motor_h, motor_r)
        ents += [arm, motor]
        names += [f"Arm {k + 1}", f"Motor {k + 1}"]
    occ.synchronize()
    for e, n in zip(ents, names):
        gmsh.model.setEntityName(3, e, n)
    _write("quadcopter")


def camera_mount():
    """Aluminium L-bracket for a small camera, bolted down through two holes in its base (vibration example)."""
    _start("camera_mount")
    occ = gmsh.model.occ
    base = occ.addBox(0, 0, 0, 60, 40, 3)
    upright = occ.addBox(0, 0, 3, 3, 40, 50)
    gusset = occ.addWedge(3, 17.5, 3, 20, 5, 20)  # small rib in the middle of the corner
    holes = [occ.addCylinder(x, 20, -1, 0, 0, 5, 2.2) for x in (25, 50)]
    body, _ = occ.fuse([(3, base)], [(3, upright), (3, gusset)])
    body, _ = occ.cut(body, [(3, h) for h in holes])
    occ.synchronize()
    gmsh.model.setEntityName(3, body[0][1], "Camera bracket")
    _write("camera_mount")


if __name__ == "__main__":
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    heated_plate()
    heatsink_in_duct()
    quadcopter()
    camera_mount()
    gmsh.finalize()
