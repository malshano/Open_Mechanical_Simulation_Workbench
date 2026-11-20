"""CFD-coupled flutter: modal structure (built-in FEM modes) driven by the pressure forces of an OpenFOAM solution.

This is the structure side of the coupling that `lumped.py` describes. The structure is the same modal model the strip
theory study uses (`modal_fem` modes). Its modes are sampled on a stick model of lumped points along the structure; at
every exchange OpenFOAM hands over the force and moment of each point's share of the wing surface and gets the new
position and rotation of every point back.

    modal equation        M_i q_i'' + C_i q_i' + K_i q_i = Q_i(t) - Q_i,ref        (i = 1..m, diagonal in the modes)
    generalised force     Q_i = sum_j  F_j . T_ij + M_j . W_ij                      (virtual work of the nodal loads)
    node motion           u_j = sum_i q_i T_ij ,  rotation_j = sum_i q_i W_ij

T_ij, W_ij are the translation and rotation of mode i at node j, found by a rigid-body fit of the mode shape over the part
of the surface that belongs to node j. Q_ref is the aerodynamic load of the undisturbed, held structure: the motion is the
perturbation around the loaded equilibrium (a linearisation: the steady lift is carried by the pre-loaded structure).

Procedure (as in industrial CFD flutter work): the flow develops with the structure held; then it is released from rest with
a small modal velocity, and the decay or growth of the modal coordinates against time gives the damping at that air
speed (`fit_poles`): negative damping = flutter. Several speeds bracket the flutter speed.

The structure is advanced exactly (matrix exponential of every mode) with the force held constant over an exchange
interval, and the state handed to OpenFOAM is the one half an interval ahead (the mesh stays where it is put until the
next exchange). The coupling is explicit (staggered): fine for a light fluid like air, not for a fluid much denser than
the structure (added-mass instability).
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

import numpy as np
from scipy.linalg import expm

from . import lumped


# --------------------------------------------------------------------------------------------- structure model
def fit_rigid(points, disp, centre, weights=None):
    """Least-squares rigid motion u = t + w x (x - centre) of the displacement field `disp` (n,3) at `points` (n,3):
    returns (t (3,), w (3,)). Exact for a rigid motion."""
    P = np.asarray(points, float) - np.asarray(centre, float)
    D = np.asarray(disp, float)
    n = len(P)
    A = np.zeros((3 * n, 6))
    A[0::3, 0] = A[1::3, 1] = A[2::3, 2] = 1.0
    # w x r = (wy rz - wz ry, wz rx - wx rz, wx ry - wy rx)
    A[0::3, 4], A[0::3, 5] = P[:, 2], -P[:, 1]
    A[1::3, 3], A[1::3, 5] = -P[:, 2], P[:, 0]
    A[2::3, 3], A[2::3, 4] = P[:, 1], -P[:, 0]
    b = D.reshape(-1)
    if weights is not None:
        w = np.repeat(np.sqrt(np.asarray(weights, float)), 3)
        A, b = A * w[:, None], b * w
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    return sol[:3], sol[3:]


class NodeModel:
    """Modal structure sampled at lumped nodes: x0 (n,3) node positions, T and W (m,n,3) translation and rotation of
    each mode at each node, omega (m) [rad/s], mg (m) generalised masses [kg], zeta damping ratio."""

    def __init__(self, x0, T, W, omega, mg, zeta=0.0):
        self.x0, self.T, self.W = np.asarray(x0, float), np.asarray(T, float), np.asarray(W, float)
        self.omega, self.mg, self.zeta = np.asarray(omega, float), np.asarray(mg, float), float(zeta)
        self.m, self.n = self.T.shape[0], self.T.shape[1]

    def generalised_force(self, forces, moments):
        """Q_i = sum_j F_j . T_ij + M_j . W_ij"""
        return np.einsum("mnk,nk->m", self.T, np.asarray(forces, float)) + np.einsum("mnk,nk->m", self.W, np.asarray(moments, float))

    def node_state(self, q):
        """Absolute node positions (n,3) and rotation vectors (n,3) for modal coordinates q (m,)."""
        q = np.asarray(q, float)
        return self.x0 + np.einsum("m,mnk->nk", q, self.T), np.einsum("m,mnk->nk", q, self.W)


def build_node_model(nodes, aero_nodes, shapes, omega, mg, flow, span, n_nodes=8, zeta=0.0):
    """Sample modes on `n_nodes` lumped nodes along the span axis. nodes (N,3) mesh node coordinates, aero_nodes indices of
    the nodes on the aerodynamic surface, shapes (m,N,3) mode displacements. Each node sits at the centroid of the
    aerodynamic nodes in its span bin (so it is near the middle of the section) and its surface share is that bin."""
    X = np.asarray(nodes, float)
    s_hat = np.asarray(span, float) / np.linalg.norm(span)
    idx = np.unique(np.asarray(aero_nodes, int))
    ys = X[idx] @ s_hat
    edges = np.linspace(ys.min(), ys.max(), n_nodes + 1)
    which = np.minimum(np.searchsorted(edges, ys, side="right") - 1, n_nodes - 1)
    x0, T, W = [], [], []
    shapes = np.asarray(shapes, float)
    for j in range(n_nodes):
        sel = idx[which == j]
        if len(sel) < 6:
            continue
        c = X[sel].mean(0)
        x0.append(c)
        tj, wj = [], []
        for i in range(len(shapes)):
            t, w = fit_rigid(X[sel], shapes[i][sel], c)
            tj.append(t)
            wj.append(w)
        T.append(tj)
        W.append(wj)
    if len(x0) < 2:
        raise ValueError("The aerodynamic surface is too small for the lumped nodes")
    return NodeModel(np.array(x0), np.transpose(np.array(T), (1, 0, 2)), np.transpose(np.array(W), (1, 0, 2)), omega, mg, zeta)


# --------------------------------------------------------------------------------------------- time integration
def propagate(omega, zeta, mg, q, v, f, h):
    """Exact state of the modes after h with the force f (per mode, N) held constant: returns (q, v) arrays."""
    q2, v2 = np.empty_like(q), np.empty_like(v)
    for i in range(len(q)):
        w = omega[i]
        A = np.array([[0.0, 1.0], [-w * w, -2.0 * zeta * w]])
        Bm = np.array([0.0, 1.0 / mg[i]])
        M = np.zeros((3, 3))
        M[:2, :2], M[:2, 2] = A * h, Bm * h
        E = expm(M)
        s = E[:2, :2] @ np.array([q[i], v[i]]) + E[:2, 2] * f[i]
        q2[i], v2[i] = s
    return q2, v2


def fit_poles(t, y, order=None, tol=1e-9):
    """Poles s_k (complex, 1/s) of the sampled signals y (nt,) or (nt, m) on a uniform time grid t, by the matrix-pencil
    method. y ~ sum a_k exp(s_k t): Re s < 0 decays, Re s > 0 grows. Returns the poles with positive imaginary part."""
    y = np.asarray(y, float)
    y = y[:, None] if y.ndim == 1 else y
    nt = len(y)
    dt = (t[-1] - t[0]) / (nt - 1)
    L = nt // 2
    cols = []
    for sig in y.T:
        cols.append(np.array([sig[i:i + L + 1] for i in range(nt - L)]))
    Hk = np.hstack(cols)  # (nt-L, (L+1) * m) Hankel blocks side by side
    U, S, Vt = np.linalg.svd(Hk, full_matrices=False)
    r = order or int(np.sum(S > S[0] * 1e-4))
    r = max(2, r + (r % 2))
    Vr = Vt[:r]
    # per signal blocks: stack along rows to get the shift-invariance of the right singular vectors
    Vb = Vr.reshape(r, y.shape[1], L + 1)
    V1 = np.concatenate([Vb[:, k, :-1] for k in range(y.shape[1])], axis=1)
    V2 = np.concatenate([Vb[:, k, 1:] for k in range(y.shape[1])], axis=1)
    z = np.linalg.eigvals(V2 @ np.linalg.pinv(V1))  # shift invariance: V2 = P diag(z) P^-1 V1
    s = np.log(z.astype(complex)) / dt
    s = s[(s.imag > 1e-6) & np.isfinite(s)]
    return s[np.argsort(s.imag)]


def damping_ratio(s):
    """Damping ratio of poles s: -Re s / |s| (negative = growing)."""
    s = np.asarray(s, complex)
    return -s.real / np.abs(s)


# --------------------------------------------------------------------------------------------- the coupled run
class CoupledRun:
    """Structure side of one CFD-coupled run: `step()` answers one OpenFOAM exchange.

    t_start is the simulated time the flow starts from (the restart time after the uncoupled development). The run can be
    saved and restored (`to_state` / `from_state`) for a restart of OpenFOAM from a written time: the state is that of the
    structure right after the exchange at `t_prev`; `history` is kept apart (the caller logs it)."""

    kind = "coupled"

    def __init__(self, model: NodeModel, comms, t_release, perturbation, poll=0.002, t_ref_from=None, t_start=0.0):
        self.model = model
        self.slave = lumped.Slave(comms, poll)
        self.t_release = float(t_release)
        self.t_ref_from = 0.5 * self.t_release if t_ref_from is None else float(t_ref_from)  # the load before the release is averaged from here
        self.t_start = float(t_start)
        m = model.m
        self.q, self.v = np.zeros(m), np.zeros(m)
        self.v_release = np.zeros(m) if perturbation is None else np.asarray(perturbation, float)  # modal velocities given at the release [m/s per unit mode]
        self.t_prev = None
        self.h_prev = None
        self.q_ref = None
        self._f_prev = np.zeros(m)
        self._ref_sum, self._ref_n = np.zeros(m), 0
        self.released = False
        self._handshaken = False
        self.history = []  # (t, q (m,), Q (m,), released)

    # -- state for a restart
    def to_state(self) -> dict:
        """JSON-able state right after the last exchange (all that the structure needs to go on from there)."""
        lst = lambda a: None if a is None else np.asarray(a, float).tolist()  # noqa: E731
        return {"kind": self.kind, "q": lst(self.q), "v": lst(self.v), "q_ref": lst(self.q_ref), "ref_sum": lst(self._ref_sum),
                "ref_n": int(self._ref_n), "t_prev": self.t_prev, "h_prev": self.h_prev, "released": bool(self.released),
                "f_prev": lst(self._f_prev), "n_history": len(self.history), "t_release": self.t_release,
                "t_ref_from": self.t_ref_from, "t_start": self.t_start}

    def load_state(self, st: dict, history=None):
        arr = lambda a: None if a is None else np.array(a, float)  # noqa: E731
        self.q, self.v, self.q_ref = arr(st["q"]), arr(st["v"]), arr(st["q_ref"])
        self._ref_sum, self._ref_n = arr(st["ref_sum"]), int(st["ref_n"])
        self.t_prev, self.h_prev, self.released = st["t_prev"], st["h_prev"], bool(st["released"])
        self._f_prev = arr(st["f_prev"])
        self._handshaken = self.t_prev is not None
        self.history = [] if history is None else list(history)

    @classmethod
    def from_state(cls, state: dict, *args, history=None, **kw):
        """A run (constructed with `*args, **kw` like the class itself) put back into the saved `state`."""
        run = cls(*args, **kw)
        run.load_state(state, history)
        return run

    def _angles(self, rot):
        return np.array([lumped.euler_zxz(w) for w in rot])

    # -- the structure in time (what a PrescribedRun replaces)
    def _on_release(self, t):
        self.v = self.v_release.copy()

    def _advance(self, t, h):
        md = self.model
        self.q, self.v = propagate(md.omega, md.zeta, md.mg, self.q, self.v, self._f_prev, h)

    def _state_ahead(self, t, ahead):
        md = self.model
        if self.released and ahead > 0:
            return propagate(md.omega, md.zeta, md.mg, self.q, self.v, self._f_prev, ahead)[0]
        return self.q

    def _reply(self, t):
        """The state handed to OpenFOAM: half an interval ahead (the mesh stays put until the next exchange)."""
        ahead = 0.5 * (self.h_prev or 0.0)
        pos, rot = self.model.node_state(self._state_ahead(t, ahead))
        return t, pos, self._angles(rot)

    def step(self, fo):
        """One exchange: forces dict from OpenFOAM (or None at the very first) -> (t, positions, angles)."""
        md = self.model
        if fo is None and self._handshaken:  # no new data (a restored run, or OpenFOAM asking again): the same answer
            return self._reply(self.t_prev)
        t = self.t_start if fo is None else fo["time"]
        Q = np.zeros(md.m) if fo is None else md.generalised_force(fo["forces"], fo["moments"])
        h = 0.0 if self.t_prev is None else max(t - self.t_prev, 0.0)
        if t < self.t_release:  # the structure is held while the flow develops: the load of the second half is the reference
            if t >= self.t_ref_from:
                self._ref_sum += Q
                self._ref_n += 1
        else:
            if not self.released:
                self.released = True
                self.q_ref = self._ref_sum / max(self._ref_n, 1)
                self._on_release(t)
                h = 0.0
            if h > 0:
                self._advance(t, h)
            self._f_prev = Q - self.q_ref
        if h > 0:
            self.h_prev = h
        self.t_prev = t
        self._handshaken = True
        self.history.append((t, self.q.copy(), Q.copy(), self.released))
        return self._reply(t)

    def run(self, alive, on_exchange=None, t_end=None, turn_timeout=3600):
        """Answer OpenFOAM until it ends (alive() false) or t_end is reached. Returns the history."""
        while alive():
            if not self.slave.wait_turn(alive, timeout=turn_timeout):
                break
            fo = self.slave.read_forces()
            t, pos, ang = self.step(fo)
            self.slave.respond(t, pos, ang)
            if on_exchange:
                on_exchange(t, self)
            if t_end is not None and t >= t_end:
                break
        return self.history

    def series(self):
        """Time (nt,) and modal coordinates (nt, m) after the release."""
        rows = [(t, q) for t, q, _, rel in self.history if rel]
        if not rows:
            return np.zeros(0), np.zeros((0, self.model.m))
        return np.array([r[0] for r in rows]), np.array([r[1] for r in rows])


class PrescribedRun(CoupledRun):
    """Forced-motion cross-check: the modal coordinate `mode` is DRIVEN, q(t) = amp sin(w (t - t_release) + phase) behind a
    raised-cosine ramp of `ramp_s` seconds (default one period; the motion starts smoothly from rest), instead of being
    integrated from the forces. The forces are recorded as usual: `aero` (via `lockin`) then gives the aerodynamic
    stiffness and damping of that mode at that frequency, which the free run's damping can be checked against."""

    kind = "prescribed"

    def __init__(self, model, comms, t_release, mode, amp, f_hz, ramp_s=None, phase=0.0, poll=0.002, t_ref_from=None,
                 t_start=0.0):
        super().__init__(model, comms, t_release, None, poll, t_ref_from, t_start)
        self.mode, self.amp, self.f_hz, self.phase = int(mode), float(amp), float(f_hz), float(phase)
        self.omega_d = 2 * math.pi * self.f_hz
        self.ramp_s = float(ramp_s) if ramp_s else 1.0 / self.f_hz

    def drive(self, t):
        """Modal coordinates and velocities (m,), (m,) of the prescribed motion at time t."""
        q, v = np.zeros(self.model.m), np.zeros(self.model.m)
        tau = t - self.t_release
        if tau <= 0:
            return q, v
        if tau < self.ramp_s:
            r = 0.5 * (1 - math.cos(math.pi * tau / self.ramp_s))
            dr = 0.5 * math.pi / self.ramp_s * math.sin(math.pi * tau / self.ramp_s)
        else:
            r, dr = 1.0, 0.0
        s, c = math.sin(self.omega_d * tau + self.phase), math.cos(self.omega_d * tau + self.phase)
        q[self.mode] = self.amp * r * s
        v[self.mode] = self.amp * (dr * s + r * self.omega_d * c)
        return q, v

    def _on_release(self, t):
        self.q, self.v = self.drive(t)

    def _advance(self, t, h):
        self.q, self.v = self.drive(t)

    def _state_ahead(self, t, ahead):
        return self.drive(t + ahead)[0] if self.released else self.q

    def aero(self, n_skip_periods=1):
        """(aerodynamic stiffness, aerodynamic damping) of the driven mode [N/m, N s/m per unit modal coordinate]."""
        rows = [(t, Q[self.mode]) for t, _, Q, rel in self.history if rel]
        t, Q = np.array([r[0] for r in rows]), np.array([r[1] for r in rows])
        return aero_from_lockin(lockin(t, Q, self.f_hz, n_skip_periods, t_origin=self.t_release), self.amp, self.omega_d)


def lockin(t, Q, f_hz, n_skip_periods=1, t_origin=None):
    """Generalised-force coefficient at the frequency f_hz of a forced motion q = A sin(w (t - t_origin)): the sampled force
    Q(t) = a_s sin + a_c cos + mean + drift is fitted over a whole number of periods (the first `n_skip_periods` after
    t[0], the start-up, are skipped; the samples may be unevenly spaced). Returns a_s + 1j a_c: the real part is in phase
    with the displacement, the imaginary part with the velocity. t_origin defaults to t[0]."""
    t, Q = np.asarray(t, float), np.asarray(Q, float)
    if len(t) < 8 or f_hz <= 0:
        raise ValueError("lockin: too few samples")
    T = 1.0 / f_hz
    t0 = t[0] + n_skip_periods * T
    n = int(math.floor((t[-1] - t0) / T + 1e-9))
    if n < 1:
        raise ValueError("lockin: less than one period after the skipped start-up")
    sel = (t >= t0 - 1e-12) & (t <= t0 + n * T + 1e-12)
    ts = t[sel]
    th = 2 * math.pi * f_hz * (ts - (t[0] if t_origin is None else t_origin))
    A = np.column_stack([np.sin(th), np.cos(th), np.ones_like(ts), (ts - ts.mean()) / (n * T)])
    sol, *_ = np.linalg.lstsq(A, Q[sel], rcond=None)
    return complex(sol[0], sol[1])


def aero_from_lockin(Qc, amp, omega):
    """(aero_stiffness, aero_damping) per unit modal coordinate / modal velocity from `lockin`'s coefficient for the motion
    q = amp sin(omega t): Q = -k q - c q', so k = -Re(Qc)/amp and c = -Im(Qc)/(amp omega). Positive c = damping."""
    return -Qc.real / amp, -Qc.imag / (amp * omega)


def analyse_series(t, q):
    """Poles of modal coordinates q (nt, m) sampled at the times t (nt,) after the release (see `analyse`)."""
    t, q = np.asarray(t, float), np.asarray(q, float)
    if len(t) < 24:
        raise ValueError("Too few exchanges after the release to find the damping: run longer")
    tg = np.linspace(t[0], t[-1], len(t))
    Y = np.column_stack([np.interp(tg, t, q[:, i]) for i in range(q.shape[1])])
    sc = np.maximum(np.abs(Y).max(0), 1e-300)
    s = fit_poles(tg, Y / sc)
    f, z = s.imag / (2 * math.pi), damping_ratio(s)
    keep = (z > -0.5) & (z < 0.9)
    poles = sorted(zip(f[keep].tolist(), z[keep].tolist()))
    out = {"poles": poles, "n_samples": int(len(t)), "duration_s": float(t[-1] - t[0])}
    out["critical"] = min(poles, key=lambda p: p[1]) if poles else None
    return out


def analyse(run: CoupledRun, f_guess_hz=None):
    """Poles of the released motion. Returns {"poles": [(f_Hz, zeta)], "critical": (f_Hz, zeta) of the lowest damping}.
    The samples are put on a uniform time grid first (the exchanges are not exactly evenly spaced)."""
    t, q = run.series()
    return analyse_series(t, q)


def save_history(run: CoupledRun, path):
    t, q = run.series()
    Path(path).write_text(json.dumps({"t": t.tolist(), "q": q.tolist(), "omega": run.model.omega.tolist(),
                                      "created": time.strftime("%Y-%m-%d %H:%M:%S")}))


def save_model(model: NodeModel, path):
    """Write a NodeModel to a .npz file (a detached run reads it back)."""
    np.savez(path, x0=model.x0, T=model.T, W=model.W, omega=model.omega, mg=model.mg, zeta=np.array(model.zeta))


def load_model(path) -> NodeModel:
    with np.load(path) as d:
        return NodeModel(d["x0"], d["T"], d["W"], d["omega"], d["mg"], float(d["zeta"]))
