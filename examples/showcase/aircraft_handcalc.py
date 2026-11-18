"""First-principles numbers for the light aircraft of make_aircraft.py: wing area, aspect ratio, mean chord and a
lifting-line (Prandtl / Glauert Fourier series) estimate of the wing's lift and induced drag.

    python examples/showcase/aircraft_handcalc.py [alpha_deg] [speed_m_s] [rho]

alpha is the angle of attack of the fuselage axis (the wing sits at +incidence relative to it, see WING).
The section lift curve is thin-airfoil theory (2 pi per rad) with the zero-lift angle of the NACA 2412 section
(-2.07 deg) and the local geometric twist; the fuselage and the tail are not in the lifting-line model.
"""
import math
import sys

import numpy as np

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from make_aircraft import HTAIL, WING  # noqa: E402

W = WING


def planform(n=4001):
    """chord(y) [m] and local incidence (deg) over y in [-b/2, b/2] (the rounded tips shrink the chord)."""
    yb, ys, c0, c1, cap = W["y_break"], W["half_span"], W["c_root"], W["c_tip"], W["cap"]
    yc = ys - cap
    ymax = yc + 0.96 * cap        # the last section of the rounded tip (0.96 of the cap length)
    y = np.linspace(0, ymax, n)
    ch = np.where(y <= yb, c0, c0 + (c1 - c0) * (y - yb) / (ys - yb))
    u = np.clip((y - yc) / cap, 0, 1)
    ch = ch * np.sqrt(1 - u ** 2)
    inc = np.where(y <= yb, W["incid_root"], W["incid_root"] + (W["incid_tip"] - W["incid_root"]) * (y - yb) / (ys - yb))
    return y / 1000, ch / 1000, inc


def areas():
    y, c, inc = planform()
    S = 2 * np.trapezoid(c, y)
    b = 2 * (W["half_span"] - W["cap"] + 0.96 * W["cap"]) / 1000      # tip of the last section
    b = 2 * (W["half_span"]) / 1000
    mac = 2 * np.trapezoid(c ** 2, y) / S
    return S, b, mac, y, c, inc


def lifting_line(alpha_deg, N=60):
    S, b, mac, _, _, _ = areas()
    yh, ch, inc = planform()
    bhalf = W["half_span"] / 1000
    th = (np.arange(1, N + 1)) * math.pi / (2 * N)
    yy = bhalf * np.cos(th)                             # stations from the tip (th small) to the root
    c = np.interp(yy, yh, ch, right=ch[-1])
    i_loc = np.interp(yy, yh, inc)
    a0 = 2 * math.pi
    aL0 = math.radians(-2.07)
    alpha = math.radians(alpha_deg)
    n = np.arange(1, 2 * N, 2)                          # odd terms (symmetric loading)
    n = n[:N]
    A = np.zeros((N, N))
    for k, t in enumerate(th):
        for j, nn in enumerate(n):
            A[k, j] = math.sin(nn * t) * (4 * (2 * bhalf) / (a0 * c[k]) + nn / math.sin(t))
    rhs = alpha + np.radians(i_loc) - aL0
    An = np.linalg.solve(A, rhs)
    AR = b * b / S
    CL = An[0] * math.pi * AR
    delta = float(np.sum(n[1:] * (An[1:] / An[0]) ** 2))
    e = 1 / (1 + delta)
    CDi = CL ** 2 / (math.pi * AR * e)
    return dict(S=S, b=b, AR=AR, mac=mac, CL=CL, e=e, CDi=CDi)


def closed_form(alpha_deg, e=0.95):
    S, b, mac, y, c, inc = areas()
    AR = b * b / S
    a0 = 2 * math.pi
    aL0 = -2.07
    twist = float(2 * np.trapezoid(c * inc, y) / S)     # area-weighted incidence (deg)
    a = a0 / (1 + a0 / (math.pi * e * AR))
    CL = a * math.radians(alpha_deg + twist - aL0)
    return dict(AR=AR, mean_incidence_deg=twist, a_per_rad=a, CL=CL, CDi=CL ** 2 / (math.pi * e * AR))


if __name__ == "__main__":
    alpha = float(sys.argv[1]) if len(sys.argv) > 1 else 4.0
    V = float(sys.argv[2]) if len(sys.argv) > 2 else 50.0
    rho = float(sys.argv[3]) if len(sys.argv) > 3 else 1.225
    r = lifting_line(alpha)
    q = 0.5 * rho * V * V
    print(f"wing area S = {r['S']:.3f} m2   span b = {r['b']:.3f} m   AR = {r['AR']:.3f}   MAC = {r['mac']:.3f} m")
    print(f"dynamic pressure q = {q:.1f} Pa")
    print(f"lifting line (Glauert, 60 terms): CL = {r['CL']:.4f}  e = {r['e']:.4f}  CDi = {r['CDi']:.5f}")
    cf = closed_form(alpha)
    print(f"closed form (e = 0.95, area-weighted incidence {cf['mean_incidence_deg']:.2f} deg): "
          f"a = {cf['a_per_rad']:.3f} /rad, CL = {cf['CL']:.4f}, CDi = {cf['CDi']:.5f}")
    L = r["CL"] * q * r["S"]
    print(f"lift (wing, lifting line) = {L:.0f} N  -> supports {L / 9.81:.0f} kg;  induced drag = {r['CDi'] * q * r['S']:.0f} N")
    # horizontal tail download (rough): symmetric NACA 0012 at its own incidence in the wing's downwash, not computed here
