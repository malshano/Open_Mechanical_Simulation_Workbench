"""Light single-engine aircraft (Cessna-172 class) as ONE watertight solid, in millimetres.

    python examples/showcase/make_aircraft.py          -> examples/showcase/aircraft_light.step

Drawn with gmsh/OpenCASCADE, nothing is downloaded:
  * wing       low wing (meets the fuselage sides at about 90 deg), NACA 2412 sections, constant-chord centre panel + tapered, washed-out outer panels (2 deg dihedral),
               rounded plan-form tips (smooth loft of shrinking sections), blunt trailing edge (1 % chord, at least 16 mm)
  * fuselage   lofted from super-elliptical sections: rounded cowl/nose, cabin, tapering tail cone
  * h. tail    NACA 0012, -1 deg incidence;  fin: NACA 0010, swept
Nose along +x, up is +z, the span is along y, the symmetry plane is y = 0. All parts are fused into one solid.
"""
import math
import re
from pathlib import Path

import gmsh
import numpy as np
from scipy.interpolate import PchipInterpolator

HERE = Path(__file__).parent
NAME = "aircraft_light"

# ------------------------------------------------------------------ geometry parameters (mm)
WING = dict(xq=2800.0, z=-1100.0, c_root=1630.0, c_tip=1120.0, y_break=2400.0, half_span=5500.0,
            incid_root=1.5, incid_tip=-0.5, dihedral_deg=2.0, code="2412", cap=330.0)
HTAIL = dict(xq=6700.0, z=-480.0, c_root=1150.0, c_tip=750.0, half_span=1700.0, incid=-1.0, code="0012", cap=180.0)
FIN = dict(x_le_root=6300.0, z_root=-450.0, c_root=1500.0, c_tip=760.0, height=1500.0, sweep_deg=32.0, code="0010",
           cap=160.0)
TE_REL = 0.01   # blunt trailing edge: total thickness as a fraction of the local chord ... (0.5 % made slivers at the mid-plane cut of the fin)
TE_MIN = 16.0  # ... but at least this many mm: keeps the TE face about as wide as a surface cell, so it meshes cleanly

occ = gmsh.model.occ


# ------------------------------------------------------------------ aerofoil sections
def naca4(code, c, te_total, n=30):
    """Closed 2D outline (TE-upper -> LE -> TE-lower) of a NACA 4-digit section scaled to chord c, with the trailing
    edge opened to te_total (mm). Returns (upper, lower) as arrays of (x_over_c, z_over_c)."""
    m, p, t = int(code[0]) / 100.0, int(code[1]) / 10.0, int(code[2:]) / 100.0
    th = np.linspace(0, math.pi, n)
    x = 0.5 * (1 - np.cos(th))
    yt = 5 * t * (0.2969 * np.sqrt(x) - 0.1260 * x - 0.3516 * x ** 2 + 0.2843 * x ** 3 - 0.1036 * x ** 4)
    # (-0.1036: closed TE; the opening is then added explicitly and linearly, so it is the same at every station)
    yt = yt + x * (te_total / c) / 2
    if m > 0:
        yc = np.where(x < p, m / p ** 2 * (2 * p * x - x ** 2), m / (1 - p) ** 2 * ((1 - 2 * p) + 2 * p * x - x ** 2))
        dy = np.where(x < p, 2 * m / p ** 2 * (p - x), 2 * m / (1 - p) ** 2 * (p - x))
    else:
        yc, dy = np.zeros_like(x), np.zeros_like(x)
    a = np.arctan(dy)
    xu, zu = x - yt * np.sin(a), yc + yt * np.cos(a)
    xl, zl = x + yt * np.sin(a), yc - yt * np.cos(a)
    return np.c_[xu, zu], np.c_[xl, zl]


def section_wire(code, q, chord, u0, v0, incid_deg, span_dir=None):
    """Wire of one aerofoil section. q = quarter-chord point, u0 = chord direction, v0 = thickness direction,
    incid_deg = nose-up rotation about the span axis (u0 x v0 sense: LE moves along +v0)."""
    i = math.radians(incid_deg)
    u0, v0 = np.array(u0, float), np.array(v0, float)
    u = math.cos(i) * u0 - math.sin(i) * v0
    v = math.sin(i) * u0 + math.cos(i) * v0
    te = max(TE_REL * chord, TE_MIN)
    up, lo = naca4(code, chord, te)
    q = np.array(q, float)

    def P(xz):
        return q + chord * ((xz[0] - 0.25) * u + xz[1] * v)

    pts = [P(p) for p in up[::-1]] + [P(p) for p in lo[1:]]      # TE upper -> LE -> TE lower
    ptags = [occ.addPoint(*p) for p in pts]
    sp = occ.addSpline(ptags)
    ln = occ.addLine(ptags[-1], ptags[0])
    return occ.addWire([sp, ln])


# ------------------------------------------------------------------ lifting surfaces
def loft(wires, ruled):
    out = occ.addThruSections(wires, makeSolid=True, makeRuled=ruled)
    return [t for d, t in out if d == 3]


def wing_parts():
    w = WING
    yb, ys, c0, c1 = w["y_break"], w["half_span"], w["c_root"], w["c_tip"]
    tand = math.tan(math.radians(w["dihedral_deg"]))
    yc = ys - w["cap"]

    def chord(y):
        y = abs(y)
        return c0 if y <= yb else c0 + (c1 - c0) * (y - yb) / (ys - yb)

    def incid(y):
        y = abs(y)
        return w["incid_root"] if y <= yb else w["incid_root"] + (w["incid_tip"] - w["incid_root"]) * (y - yb) / (ys - yb)

    def zq(y):
        return w["z"] + max(abs(y) - yb, 0.0) * tand

    def sec(y, scale=1.0):
        return section_wire(w["code"], (w["xq"], y, zq(y)), chord(y) * scale, (1, 0, 0), (0, 0, 1), incid(y))

    solids = [loft([sec(-yb), sec(yb)], True)]      # centre panel, straight through the fuselage
    for s in (1, -1):
        outer = [sec(s * yb), sec(s * yc)]
        solids.append(loft(outer, True))
        # rounded tip: shrinking sections (ellipse in plan), smooth loft, starting with the panel's end section
        cap = [sec(s * yc)]
        for u in (0.40, 0.70, 0.88, 0.96):
            y = yc + u * w["cap"]
            cap.append(sec(s * y, math.sqrt(1 - u * u)))
        # (chord(y) keeps tapering linearly inside the cap, so the scale is relative to the local chord)
        solids.append(loft(cap, False))
    return [t for g in solids for t in g]


def htail_parts():
    h = HTAIL
    ys, yc = h["half_span"], h["half_span"] - h["cap"]

    def chord(y):
        return h["c_root"] + (h["c_tip"] - h["c_root"]) * abs(y) / ys

    def sec(y, scale=1.0):
        return section_wire(h["code"], (h["xq"], y, h["z"]), chord(y) * scale, (1, 0, 0), (0, 0, 1), h["incid"])

    solids = [loft([sec(-yc), sec(0.0), sec(yc)], True)]
    for s in (1, -1):
        cap = [sec(s * yc)] + [sec(s * (yc + u * h["cap"]), math.sqrt(1 - u * u)) for u in (0.40, 0.70, 0.88, 0.96)]
        solids.append(loft(cap, False))
    return [t for g in solids for t in g]


def fin_parts():
    f = FIN
    hh, zc = f["height"], f["height"] - f["cap"]
    tans = math.tan(math.radians(f["sweep_deg"]))

    def chord(z):
        return f["c_root"] + (f["c_tip"] - f["c_root"]) * z / hh

    def xq(z):                       # swept leading edge; quarter-chord point of the local section
        return f["x_le_root"] + z * tans + 0.25 * chord(z)

    def sec(z, scale=1.0):
        return section_wire(f["code"], (xq(z), 0.0, f["z_root"] + z), chord(z) * scale, (1, 0, 0), (0, 1, 0), 0.0)

    solids = [loft([sec(0.0), sec(zc)], True)]
    cap = [sec(zc)] + [sec(zc + u * f["cap"], math.sqrt(1 - u * u)) for u in (0.40, 0.70, 0.88, 0.96)]
    solids.append(loft(cap, False))
    return [t for g in solids for t in g]


# ------------------------------------------------------------------ fuselage
def fuselage():
    xs = [0, 1500, 2000, 2600, 3400, 4300, 5200, 6200, 7200, 8200]
    top = [-550, -550, -280, -20, -20, -150, -270, -300, -280, -290]      # roof line
    bot = [-1450, -1450, -1500, -1560, -1560, -1500, -1250, -900, -690, -560]  # belly line (tail cone sweeps up)
    a = PchipInterpolator(xs, [470, 470, 520, 550, 540, 430, 330, 260, 200, 100])    # half width
    b = PchipInterpolator(xs, [(t - u) / 2 for t, u in zip(top, bot)])                # half height
    zc = PchipInterpolator(xs, [(t + u) / 2 for t, u in zip(top, bot)])               # centre height
    xn = 1500.0
    stations = [5, 15, 40, 90, 170, 280, 420, 600, 800, 1050, 1300, 1500, 1750, 2000, 2300, 2600, 3000, 3400, 3850,
                4300, 4750, 5200, 5700, 6200, 6700, 7200, 7700, 8200]
    n, ex, M = 28, 2.6, 28
    wires = []
    for x in stations:
        s = math.sqrt(max(1 - (1 - x / xn) ** 2, 0.0)) if x < xn else 1.0   # elliptic nose
        A, B, Z = float(a(x)) * s, float(b(x)) * s, float(zc(x))
        th = np.linspace(0, 2 * math.pi, M, endpoint=False)
        pts = [(x, A * np.sign(math.cos(t)) * abs(math.cos(t)) ** (2 / ex),
                Z + B * np.sign(math.sin(t)) * abs(math.sin(t)) ** (2 / ex)) for t in th]
        tags = [occ.addPoint(*p) for p in pts]
        sp = occ.addSpline(tags + [tags[0]])          # closed (periodic) spline
        wires.append(occ.addWire([sp]))
    return loft(wires, False)


# ------------------------------------------------------------------ assemble
def build(parts_sel=("fuselage", "wing", "htail", "fin")):
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.model.add(NAME)
    fus = fuselage()
    parts = (fus + (wing_parts() if "wing" in parts_sel else []) + (htail_parts() if "htail" in parts_sel else [])
             + (fin_parts() if "fin" in parts_sel else []))
    occ.synchronize()
    # fuse everything to one solid
    obj = [(3, fus[0])]
    tools = [(3, t) for t in parts if t != fus[0]]
    if tools:
        occ.fuse(obj, tools, removeObject=True, removeTool=True)
    occ.removeAllDuplicates()
    occ.synchronize()
    vols = gmsh.model.getEntities(3)
    return vols


def write_step(path=None):
    vols = gmsh.model.getEntities(3)
    assert len(vols) == 1, f"expected one solid, got {len(vols)}"
    gmsh.model.setEntityName(3, vols[0][1], "Aircraft")
    gmsh.model.occ.synchronize()
    path = Path(path) if path else HERE / f"{NAME}.step"
    gmsh.write(str(path))
    text = path.read_text()
    text = re.sub(r"'Open CASCADE STEP translator [\d.]+ \d+'", "'Aircraft'", text)
    text = re.sub(r"'Open CASCADE STEP translator [\d.]+ \d+\.\d+'", "'Aircraft'", text)
    path.write_text(text)
    return path


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--te-rel", type=float, help="trailing-edge thickness as a fraction of the chord")
    ap.add_argument("--te-min", type=float, help="minimum trailing-edge thickness in mm")
    ap.add_argument("--out", help="output STEP path")
    ap.add_argument("--htail-x", type=float)
    ap.add_argument("--htail-z", type=float)
    ap.add_argument("--parts", default="fuselage,wing,htail,fin", help="comma list, for tests")
    args = ap.parse_args()
    if args.te_rel is not None:
        TE_REL = args.te_rel
    if args.te_min is not None:
        TE_MIN = args.te_min
    if args.htail_x is not None:
        HTAIL["xq"] = args.htail_x
    if args.htail_z is not None:
        HTAIL["z"] = args.htail_z
    vols = build(args.parts.split(','))
    print("volumes:", vols)
    for d, t in vols:
        print("volume mm3:", gmsh.model.occ.getMass(d, t), "bbox:", gmsh.model.getBoundingBox(d, t))
    print("faces:", len(gmsh.model.getEntities(2)), "edges:", len(gmsh.model.getEntities(1)))
    print("wrote", write_step(args.out))
    gmsh.finalize()
