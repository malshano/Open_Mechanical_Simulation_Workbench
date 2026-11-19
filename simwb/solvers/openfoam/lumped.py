"""Coupling of OpenFOAM to an external structure solver through OpenFOAM's own `lumpedPointMotion` interface.

No compiler and no extra software: OpenFOAM v2606 (the build the workbench installs) has the boundary condition
`lumpedPointDisplacement` (library `lumpedPointMotion`). The wing surface is tied to a few "lumped points" along a stick
model of the structure (for a wing: points along the elastic axis). Every exchange, OpenFOAM writes the pressure force
and moment of each point's share of the surface to `comms/forces.out`, and reads the new position and rotation of every
point from `comms/positions.in`; the surface is moved by them and the mesh follows (displacementLaplacian). The two
programs take turns through the lock file `comms/OpenFOAM.lock`: OpenFOAM removes it when it wants the structure to
answer, the structure creates it when it is done.

Facts measured with the workbench's OpenFOAM v2606 on Windows (tests/test_lumped.py checks the parts that need no solver):

* The returned forces are the *pressure* forces only (no viscous part), in N, on the points of the surface zone of each
  lumped point, and add up to the `forces` function object's pressure force exactly; moments are about the points.
* `angles` are intrinsic Euler angles z-x-z in radians, NOT a rotation vector: (a, eps, b) rotates about x by eps, a and
  b rotate about z. A small rotation vector w is therefore passed as (a, eps, -a + wz) with eps = |(wx, wy)|, a = atan2(wy, wx)
  (`euler_zxz`). Positive rotation about +y lifts the part with x < the point (nose up for flow along +x).
* A surface point moves with the displacement and rotation of its lumped points (interpolated along the chain), the new
  positions are absolute, so the structure sends point0 + displacement.
* `waitInterval` is an integer number of seconds and OpenFOAM only looks for the answer after that wait: one exchange costs
  about 1 s of wall time, whatever the structure needs.
* The mesh stays where it is put until the next exchange. Exchanging every ~0.5 ms of simulated time (many steps) makes the
  wall jump by a few micrometres inside one time step; an incompressible solver answers with a pressure impulse of about
  added mass x jump / dt^2: force spikes of +-1000 N for three steps after every update on a wing that carries 15 N, and the
  structure then responds to that noise (measured, run discarded). Exchange EVERY time step (`every_step=True`, the
  default): the wall then moves by velocity x dt per step and the forces are smooth. This costs about 1 s of wall time per
  step.
* A sudden large motion (10 degrees in one step) blows the solver up (mesh velocity = displacement / dt): the structure
  must start from rest and move smoothly.
"""
from __future__ import annotations

import math
import re
import time
from pathlib import Path

import numpy as np

LOCK = "OpenFOAM.lock"
HEADER = "FoamFile\n{\n    version 2.0;\n    format ascii;\n    class dictionary;\n    object %s;\n}\n"


# ------------------------------------------------------------------------------------------------------ rotations
def euler_zxz(w) -> tuple:
    """Intrinsic z-x-z Euler angles (radians) of a small rotation by the vector w = (wx, wy, wz) [rad]."""
    eps = math.hypot(w[0], w[1])
    a = math.atan2(w[1], w[0]) if eps > 0 else 0.0
    return (a, eps, -a + float(w[2]))


def matrix_zxz(angles) -> np.ndarray:
    """Rotation matrix Rz(a0) Rx(a1) Rz(a2) of intrinsic z-x-z Euler angles."""
    def rz(t):
        c, s = math.cos(t), math.sin(t)
        return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])

    def rx(t):
        c, s = math.cos(t), math.sin(t)
        return np.array([[1.0, 0, 0], [0, c, -s], [0, s, c]])
    return rz(angles[0]) @ rx(angles[1]) @ rz(angles[2])


# ------------------------------------------------------------------------------------------------------ case set-up
def write_setup(case, points, patch="wing", rho=1.204, interval=None, controller="wing", timeout=3600):
    """Add the coupling to a written, not yet renumbered OpenFOAM case (a pimpleFoam case with a displacementLaplacian
    dynamicMeshDict and a pointDisplacement file): the stick-model points (n,3) [m] in the mesh's coordinates, the wing
    patch name, the density [kg/m3] that turns the kinematic pressure into N, the time between exchanges [s] (None: every
    time step, which the physics needs: see the module text)."""
    case = Path(case)
    pts = np.asarray(points, float)
    n = len(pts)
    (case / "system" / "lumpedPointControllers").write_text(
        HEADER % "dictionary" + "points\n(\n" + "".join("    (%.9g %.9g %.9g)\n" % tuple(p) for p in pts) + ");\n"
        "controllers\n{\n    %s\n    {\n        pointLabels (%s);\n    }\n}\n" % (controller, " ".join(map(str, range(n)))),
        newline="\n")
    (case / "system" / "lumpedPointMovement").write_text(
        HEADER % "lumpedPointMovement" + '#include "lumpedPointControllers"\nrelax 1.0;\n'
        f"forces\n{{\n    p p;\n    pRef 0;\n    rhoRef {rho:.8g};\n}}\n"
        "communication\n{\n    commsDir \"comms\";\n    log on;\n    waitInterval 1;\n"
        f"    timeOut {int(timeout)};\n    initByExternal false;\n    inputName positions.in;\n    outputName forces.out;\n"
        "    logName movement.log;\n    inputFormat dictionary;\n    outputFormat dictionary;\n"
        "    scaleInput { length 1; }\n    scaleOutput { length 1; force 1; moment 1; }\n}\n", newline="\n")
    f = case / "0" / "pointDisplacement"
    txt = f.read_text()
    new, k = re.subn(r"    %s\n    \{.*?\n    \}\n" % re.escape(patch),
                     "    %s\n    {\n        type            lumpedPointDisplacement;\n"
                     "        value           uniform (0 0 0);\n        controllers     (%s);\n    }\n" % (patch, controller),
                     txt, flags=re.S)
    if k != 1:
        raise ValueError(f"The patch '{patch}' has no entry in 0/pointDisplacement")
    f.write_text(new, newline="\n")
    cd = case / "system" / "controlDict"
    s = cd.read_text()
    if "lumpedPointMotion" not in s:
        s, k = re.subn(r"^(application\s+\w+;)", r"\1\nlibs            (lumpedPointMotion);", s, count=1, flags=re.M)
        if k != 1:
            raise ValueError("system/controlDict has no 'application' entry")
        cd.write_text(s, newline="\n")
    dm = case / "constant" / "dynamicMeshDict"
    s = dm.read_text()
    s = re.sub(r"^updateControl\s+\w+;\n?|^updateInterval\s+[^;]+;\n?", "", s, flags=re.M)
    upd = ("updateControl    timeStep;\nupdateInterval   1;\n" if interval is None
           else f"updateControl    runTime;\nupdateInterval   {interval:.8g};\n")
    s = s.replace("dynamicFvMesh ", upd + "dynamicFvMesh ", 1)
    dm.write_text(s, newline="\n")


# ------------------------------------------------------------------------------------------------------ files
_VEC = r"\(\s*([-\d.eE+]+)\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s*\)"


def _block(text: str, name: str):
    m = re.search(r"^%s\s*\n\s*\d+\s*\n\(\s*(.*?)\n\s*\);" % name, text, re.S | re.M)
    if not m:
        raise ValueError(f"'{name}' not found in forces.out")
    return np.array(re.findall(_VEC, m.group(1)), float).reshape(-1, 3)


def parse_forces(text: str) -> dict:
    """Content of comms/forces.out: {"time", "points" (n,3), "forces" (n,3) [N], "moments" (n,3) [N m]}."""
    t = re.search(r"^time\s+([-\d.eE+]+);", text, re.M)
    if not t:
        raise ValueError("forces.out has no time")
    return {"time": float(t.group(1)), "points": _block(text, "points"), "forces": _block(text, "forces"),
            "moments": _block(text, "moments")}


def format_positions(t: float, points, angles) -> str:
    """Text of comms/positions.in: absolute positions (n,3) [m] and z-x-z Euler angles (n,3) [rad] of the points."""
    p, a = np.asarray(points, float), np.asarray(angles, float)
    if p.shape != a.shape or p.ndim != 2 or p.shape[1] != 3:
        raise ValueError("points and angles must both be (n, 3)")
    rows = lambda x: "".join("(%.12g %.12g %.12g)\n" % tuple(r) for r in x)  # noqa: E731
    return f"time {t:.12g};\npoints\n(\n{rows(p)});\nangles\n(\n{rows(a)});\n"


class Slave:
    """The structure side of the turn taking in `comms/` (see the module text)."""

    def __init__(self, comms, poll=0.002):
        self.comms = Path(comms)
        self.poll = poll

    @property
    def lock(self) -> Path:
        return self.comms / LOCK

    def wait_turn(self, alive=lambda: True, timeout=None):
        """Wait until OpenFOAM has handed over (the lock file is absent). False if `alive()` turned false or timed out."""
        t0 = time.time()
        while self.lock.exists():
            if not alive() or (timeout is not None and time.time() - t0 > timeout):
                return False
            time.sleep(self.poll)
        return alive()

    def read_forces(self):
        f = self.comms / "forces.out"
        return parse_forces(f.read_text()) if f.exists() else None

    def respond(self, t, points, angles):
        """Write the new state and hand control back to OpenFOAM."""
        self.comms.mkdir(parents=True, exist_ok=True)
        (self.comms / "positions.in").write_text(format_positions(t, points, angles))
        self.lock.write_text("status=slave\n")
