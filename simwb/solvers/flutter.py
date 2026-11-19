"""Aeroelastic flutter and divergence of a structure in an air stream (fluid-structure coupling, built in).

The structure is the modal model of the built-in FEM (simwb.solvers.modal_fem): a few mode shapes with their
frequencies and generalised masses. The air is a strip-theory lifting-surface model: the aerodynamic faces are cut
into strips across the span, every strip is a flat plate (2-D thin airfoil) that heaves and pitches with the mode
shapes, and its unsteady lift and moment are Theodorsen's (1935) with the lift-curve slope optionally reduced for the
finite aspect ratio. Written in the modal coordinates q of the structure:

    [ (M - A2) p^2 + (Cs - A1) p + (K - A0) ] q = 0            p = s = the complex frequency of the motion q ~ e^(p t)

A2 (apparent mass), A1 (aerodynamic damping), A0 (aerodynamic stiffness) are assembled from the strips for the air
speed V and density rho. The Theodorsen function C(k) in them is evaluated at the reduced frequency k = omega b / V of
each strip (omega = Im p of the root under study, b = the strip's half chord): this is the p-k method of Hassig
(1971), solved root by root with the roots followed from one speed to the next. Re p < 0: the motion decays;
Re p > 0 with a frequency: flutter; Re p > 0 without a frequency (a real root): divergence (static instability).

Conventions: h = displacement of the section along the lift direction n (up), alpha = nose-up rotation, both at the
reference point a*b downstream of mid-chord. The result does not depend on the reference point (virtual work), which
the tests check.

Assumptions (the caller lists them to the user): linear, small motion; incompressible attached flow (use below about
Mach 0.5); each strip is independent (no spanwise induction beyond the aspect-ratio factor); sections are rigid in
their plane (camber changes are not seen by the air); no sweep effect; viscous and stall effects absent.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.special import hankel2

# ---------------------------------------------------------------------------------------------- air
R_AIR = 287.05
GAMMA = 1.4


def isa(altitude_m: float) -> dict:
    """International Standard Atmosphere (0-20 km): temperature [K], pressure [Pa], density [kg/m3], speed of sound."""
    h = float(altitude_m)
    if h < -500 or h > 20000:
        raise ValueError("Altitude must be between -500 m and 20 000 m (the standard atmosphere used here)")
    if h <= 11000:
        T = 288.15 - 0.0065 * h
        P = 101325.0 * (T / 288.15) ** 5.2559
    else:
        T = 216.65
        P = 22632.06 * math.exp(-9.80665 * (h - 11000.0) / (R_AIR * T))
    return {"T_K": T, "P_Pa": P, "rho": P / (R_AIR * T), "a": math.sqrt(GAMMA * R_AIR * T)}


def theodorsen(k):
    """Theodorsen's lift deficiency function C(k) = H1(2)(k) / (H1(2)(k) + i H0(2)(k)), k = omega b / V (array)."""
    k = np.maximum(np.asarray(k, float), 1e-6)  # C(0) = 1
    h1, h0 = hankel2(1, k), hankel2(0, k)
    return h1 / (h1 + 1j * h0)


# ---------------------------------------------------------------------------------------------- strips
def build_strips(nodes, aero_nodes, shapes, flow, span, n_strips=16, a_ref=0.0, finite_wing=True):
    """Cut the aerodynamic nodes into strips across the span and fit each mode shape to a rigid heave + pitch per strip.

    nodes (n,3) m; aero_nodes: indices of the nodes on the aerodynamic faces; shapes (m,n,3): displacement of every
    node in each mode (any scale: the same scale as the modal masses used with it); flow: direction the air moves
    (3,); span: spanwise direction (3,); a_ref: reference point as a fraction of the half chord downstream of
    mid-chord (any value gives the same result).
    Returns a dict with b, a, w, ph, pa, plus the geometry (lift direction, chord direction, span, area, aspect ratio).
    """
    X = np.asarray(nodes, float)
    s_hat = np.asarray(span, float)
    s_hat = s_hat / np.linalg.norm(s_hat)
    f = np.asarray(flow, float)
    c_hat = f - (f @ s_hat) * s_hat
    if np.linalg.norm(c_hat) < 1e-9 * max(np.linalg.norm(f), 1.0):
        raise ValueError("The flow direction is along the span axis: choose a span axis across the flow")
    c_hat = c_hat / np.linalg.norm(c_hat)
    n_hat = np.cross(c_hat, s_hat)
    idx = np.unique(np.asarray(aero_nodes, int))
    P = X[idx]
    xs, ys = P @ c_hat, P @ s_hat
    y0, y1 = float(ys.min()), float(ys.max())
    if y1 - y0 <= 0:
        raise ValueError("The aerodynamic faces have no extent along the span axis")
    ns = max(int(n_strips), 1)
    edges = np.linspace(y0, y1, ns + 1)
    which = np.minimum(np.searchsorted(edges, ys, side="right") - 1, ns - 1)
    U = np.einsum("mnk,k->mn", np.asarray(shapes, float)[:, idx, :], n_hat)  # (modes, aero nodes): along lift
    b, w, ph, pa, ym, chord = [], [], [], [], [], []
    for j in range(ns):
        sel = which == j
        if sel.sum() < 3:
            continue
        x = xs[sel]
        c = float(x.max() - x.min())
        if c <= 1e-9 * max(float(xs.max() - xs.min()), 1e-12):
            continue
        bj = 0.5 * c
        xr = 0.5 * (x.max() + x.min()) + a_ref * bj
        d = x - xr
        # least squares  u = h - alpha d  for every mode at once
        Amat = np.column_stack([np.ones_like(d), -d])
        coef, *_ = np.linalg.lstsq(Amat, U[:, sel].T, rcond=None)
        b.append(bj)
        w.append(float(edges[j + 1] - edges[j]))
        ph.append(coef[0])
        pa.append(coef[1])
        ym.append(0.5 * (edges[j] + edges[j + 1]))
        chord.append(c)
    if not b:
        raise ValueError("No usable strips: the aerodynamic faces are too small for the number of strips")
    b, w = np.array(b), np.array(w)
    area = float((2 * b * w).sum())
    span_len = y1 - y0
    # a wing clamped at its root is half of a symmetric wing (the fuselage or the wind-tunnel wall mirrors it): the
    # aspect ratio that sets the lift slope is that of the whole wing, 2 s^2 / S_half. (The half wing's own s^2 / S
    # would under-estimate the lift and so over-estimate the flutter speed.)
    ar = 2.0 * span_len ** 2 / area if area > 0 else 1.0
    return {"b": b, "a": np.full(len(b), float(a_ref)), "w": w, "ph": np.array(ph), "pa": np.array(pa),
            "y": np.array(ym), "chord": np.array(chord), "span_m": span_len, "area_m2": area, "aspect_ratio": ar,
            "fac": (ar / (ar + 2.0)) if finite_wing else 1.0,
            "c_hat": c_hat, "s_hat": s_hat, "n_hat": n_hat}


# ---------------------------------------------------------------------------------------------- the system
class Flutter:
    """Modal structure (M, K, damping Cs: (m,m)) in a stream of density rho, aerodynamics from strips (build_strips)."""

    def __init__(self, M, K, strips, rho, Cs=None):
        self.M = np.asarray(M, float)
        self.K = np.asarray(K, float)
        self.n = len(self.M)
        self.Cs = np.zeros_like(self.M) if Cs is None else np.asarray(Cs, float)
        self.s = strips
        self.rho = float(rho)
        w2 = np.linalg.eigvals(np.linalg.solve(self.M, self.K)).real
        self.omega_min = math.sqrt(max(float(w2.min()), 1e-12))
        self.omega_ref = self.omega_min

    # --- matrices
    def aero(self, V, C):
        """A2, A1, A0 (m,m) for speed V and the Theodorsen function C (one per strip)."""
        s, r = self.s, self.rho
        b, a, w, ph, pa = s["b"], s["a"], s["w"], s["ph"], s["pa"]
        fC = s["fac"] * C
        pr = math.pi * r
        a2 = [-pr * b ** 2, -pr * b ** 3 * a, -pr * b ** 3 * a, -pr * b ** 4 * (0.125 + a ** 2)]
        a1 = [-2 * pr * V * b * fC,
              pr * V * b ** 2 + 2 * pr * V * b ** 2 * (0.5 - a) * fC,
              -2 * pr * V * b ** 2 * (a + 0.5) * fC,
              -pr * V * b ** 3 * (0.5 - a) + 2 * pr * V * b ** 3 * (a + 0.5) * (0.5 - a) * fC]
        a0 = [0 * b, 2 * pr * V ** 2 * b * fC, 0 * b, 2 * pr * V ** 2 * b ** 2 * (a + 0.5) * fC]

        def assemble(c):  # c = (hh, ha, ah, aa) per strip
            return (np.einsum("s,si,sj->ij", w * c[0], ph, ph) + np.einsum("s,si,sj->ij", w * c[1], ph, pa)
                    + np.einsum("s,si,sj->ij", w * c[2], pa, ph) + np.einsum("s,si,sj->ij", w * c[3], pa, pa))

        return assemble(a2), assemble(a1), assemble(a0)

    def qep(self, V, omega):
        """Eigenvalues p and modal vectors q of the quadratic problem, the Theodorsen function taken at omega."""
        k = omega * self.s["b"] / max(V, 1e-12)
        A2, A1, A0 = self.aero(V, theodorsen(k))
        Mp = self.M - A2
        Cp = self.Cs - A1
        Kp = self.K - A0
        n = self.n
        Minv = np.linalg.inv(Mp)
        H = np.block([[np.zeros((n, n)), np.eye(n)], [-Minv @ Kp, -Minv @ Cp]])
        lam, Z = np.linalg.eig(H)
        return lam, Z[:n]

    # --- roots at V = 0 (structure in still air: the apparent mass only)
    def initial_roots(self):
        lam, Q = self.qep(0.0, 0.0)
        keep = np.where(lam.imag > 1e-9 * self.omega_ref)[0]
        keep = keep[np.argsort(lam[keep].imag)]
        if len(keep) < self.n:
            raise ValueError("The structure has overdamped or rigid-body modes at rest: give it a fixed support")
        return [(lam[i], Q[:, i] / np.linalg.norm(Q[:, i])) for i in keep[: self.n]]

    # --- one root at one speed (p-k iteration)
    def follow(self, V, prev, tol=1e-9, itmax=60):
        p0, q0 = prev
        om = abs(p0.imag)
        real_like = abs(p0.imag) < 1e-4 * self.omega_ref
        best = None
        for _ in range(itmax):
            lam, Q = self.qep(V, om)
            ok = np.where(lam.imag > -1e-9 * max(abs(lam).max(), 1e-12))[0]
            if not len(ok):
                ok = np.arange(len(lam))
            Qn = Q[:, ok] / np.linalg.norm(Q[:, ok], axis=0)
            mac = np.abs(q0.conj() @ Qn) ** 2 / max(float(np.vdot(q0, q0).real), 1e-300)
            dist = np.abs(lam[ok] - p0) / (abs(p0) + 0.1 * self.omega_ref)
            score = mac - 0.5 * np.minimum(dist, 4.0)
            if real_like:  # a static instability: follow the root that grows
                score = score + 0.3 * np.tanh(lam[ok].real / self.omega_ref)
            j = int(np.argmax(score))
            p, q = lam[ok][j], Qn[:, j]
            best = (p, q)
            om_new = max(p.imag, 0.0)
            if abs(om_new - om) <= tol * (om + self.omega_ref):
                break
            om = om_new
        return best

    def sweep(self, V_grid):
        """Roots at every speed: P (nV, n) complex, Q (nV, n, n) modal vectors, followed from V_grid[0] upward."""
        roots = self.initial_roots()
        P = np.zeros((len(V_grid), self.n), complex)
        Q = np.zeros((len(V_grid), self.n, self.n), complex)
        states = []
        for iv, V in enumerate(V_grid):
            new = []
            for r in range(self.n):
                new.append(self.follow(float(V), roots[r]))
            roots = new
            states.append(list(new))
            for r in range(self.n):
                P[iv, r], Q[iv, :, r] = new[r]
        return P, Q, states

    def divergence(self):
        """Lowest speed of static instability (divergence) and its modal vector: K q = V^2 A0hat q with the steady
        aerodynamic stiffness (k = 0, C = 1). None when no real positive eigenvalue exists (the aerodynamic
        stiffness never beats the structure's)."""
        _, _, A0 = self.aero(1.0, np.ones(len(self.s["b"])))
        mu, vec = np.linalg.eig(np.linalg.solve(self.K, A0.real))  # K^-1 A0 q = (1/V^2) q
        top = max(float(abs(mu).max()), 1e-300)
        ok = np.where((abs(mu.imag) < 1e-9 * top) & (mu.real > 1e-14 * top))[0]
        if not len(ok):
            return None
        j = ok[np.argmax(mu[ok].real)]
        return float(1.0 / math.sqrt(mu[j].real)), vec[:, j].real

    def divergence_speed(self):
        d = self.divergence()
        return None if d is None else d[0]

    def instabilities(self, V_grid, P, states):
        """First flutter as {"V", "omega", "root", "p"} or None, refined by bisection; and the divergence speed.

        Flutter: a root with a frequency whose real part turns positive (the p-k method is not valid for roots
        without a frequency: the divergence speed comes from the static problem instead)."""
        tol_im = 1e-4 * self.omega_ref
        flut = None
        for iv in range(1, len(V_grid)):
            for r in range(self.n):
                if not (P[iv, r].real > 1e-12 * self.omega_ref >= P[iv - 1, r].real) or abs(P[iv, r].imag) <= tol_im:
                    continue
                lo, hi = float(V_grid[iv - 1]), float(V_grid[iv])
                st, p_hi = states[iv - 1][r], P[iv, r]
                for _ in range(14):
                    mid = 0.5 * (lo + hi)
                    s_mid = self.follow(mid, st)
                    if s_mid[0].real > 1e-12 * self.omega_ref:
                        hi, p_hi = mid, s_mid[0]
                    else:
                        lo, st = mid, s_mid
                ev = {"V": 0.5 * (lo + hi), "omega": abs(p_hi.imag), "root": r, "p": p_hi}
                if flut is None or ev["V"] < flut["V"]:
                    flut = ev
        vd = self.divergence_speed()
        return {"flutter": flut, "divergence": None if vd is None else {"V": vd, "omega": 0.0, "root": None, "p": 0j}}


def solve(M, K, strips, rho, V_grid, Cs=None):
    """Run a speed sweep. Returns dict: V, P (complex roots, (nV, n)), Q (modal vectors), events, the system."""
    sysm = Flutter(M, K, strips, rho, Cs)
    V_grid = np.asarray(V_grid, float)
    P, Q, states = sysm.sweep(V_grid)
    return {"V": V_grid, "P": P, "Q": Q, "events": sysm.instabilities(V_grid, P, states), "system": sysm,
            "states": states}
