"""Topology optimisation by SIMP: the stiffest shape for a given mass.

Minimise the compliance c = f . u (how much the loads bend the part: small is stiff) with the mass held at the target. Each element has a density
x between 0 (empty) and 1 (solid) and a stiffness E0 (x^p) with p = 3, which makes grey elements poor value so the design turns black and white.

    density filter   x~ = P x       P averages the densities within a radius (metres) with a cone weight: no checkerboards, and members no thinner
                                    than the radius, whatever the mesh
    projection       xb = H(x~)     a smooth step (tanh) that grows sharper in stages (beta 1, 2, 4, 8): a crisp black-and-white shape
    sensitivity      dc/dxb = -p xb^(p-1) E0 u_e^T Ke u_e   (u_e: element displacements); the chain rule through projection and filter follows
    update           optimality criteria: x <- x sqrt(-dc/(lambda dm)) with limits on the move, lambda found by bisection so the mass is exact

Solid elements next to supports and loads stay solid. Linear tetrahedra (see fe.py); the result is a concept shape to re-check with a static study.
The density filter, projection and OC update follow the standard formulation (Bendsoe-Sigmund 2003; Wang, Lazarov, Sigmund 2011; Andreassen et al. 2011)."""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass

import numpy as np
import scipy.sparse as sp
from scipy.spatial import cKDTree

from .. import memlimit
from ..solvers.modal_fem import MAX_DOF, MAX_DOF_SUPERLU, _pardiso
from ..solvers.static_fem import _factor_solve
from .fe import element_energy
from .problem import Problem, ProblemError

MAX_PAIRS = 40_000_000


@dataclass
class Settings:
    volume_fraction: float = 0.3         # the mass to keep, as a share of the whole part
    radius: float | None = None          # minimum member size / filter radius, m (None: chosen from the part and the mesh)
    penal: float = 3.0
    max_iter: int = 200
    stage_iters: int = 30                # at most this many iterations at each sharpness (beta) before it is doubled
    move: float = 0.2
    projection: bool = True
    beta_max: float = 8.0
    tol: float = 2e-3                    # the largest change of the filtered density between two iterations that counts as settled
    xmin: float = 1e-3
    emin: float = 1e-9                   # stiffness of empty material, relative to solid


MIN_CELLS = 3.0          # the smallest member size, in element sizes: below this the shape and its stiffness depend on the mesh (14 % at 2, 6 % at 2.5)
GOOD_CELLS = 4.0


def default_radius(problem: Problem) -> float:
    """4 % of the part's largest size, but never less than four element sizes (a member must be several cells wide to be described, and the result
    then no longer depends on the mesh)."""
    size = float(np.ptp(problem.nodes, axis=0).max())
    return max(0.04 * size, GOOD_CELLS * problem.h)


def estimate(problem: Problem, radius: float) -> dict:
    """What a run needs: memory (bytes), filter links, unknowns, and whether the machine can do it. The links are counted on a sample of elements
    (a thin part has far fewer neighbours than a ball of the same radius would)."""
    N = problem.n
    rng = np.random.default_rng(0)
    pick = rng.choice(N, size=min(N, 300), replace=False)
    tree = cKDTree(problem.centroid)
    per = float(np.mean(tree.query_ball_point(problem.centroid[pick], radius, return_length=True)))
    pairs = N * per
    mem = N * 144 * (8 + 8 + 8) + pairs * 20 + problem.ndof * 400
    free = memlimit.memory()[1]
    limit = MAX_DOF if _pardiso() is not None else MAX_DOF_SUPERLU
    return {"elements": N, "unknowns": problem.ndof, "filter_pairs": int(pairs), "memory_bytes": int(mem), "free_bytes": int(free),
            "dof_limit": limit, "fits": bool(problem.ndof <= limit and pairs <= MAX_PAIRS and mem < 0.6 * free)}


def _proj(x, beta, eta=0.5):
    a, b = np.tanh(beta * eta), np.tanh(beta * (1 - eta))
    t = np.tanh(beta * (x - eta))
    return (a + t) / (a + b), beta * (1 - t * t) / (a + b)


class Optimiser:
    def __init__(self, problem: Problem, settings: Settings | None = None, log=print):
        self.pb, self.s, self.log = problem, settings or Settings(), log
        s, pb = self.s, problem
        if not 0.02 <= s.volume_fraction < 1.0:
            raise ProblemError("The mass to keep must be between 2 % and 99 % of the part.")
        self.notes = list(pb.notes)
        r = s.radius if s.radius else default_radius(pb)
        if r < MIN_CELLS * pb.h:
            self.notes.append(f"The smallest feature size ({r * 1e3:.2g} mm) is below {MIN_CELLS:g} element sizes, so it was raised to {MIN_CELLS * pb.h * 1e3:.2g} mm: a member "
                              "must be several cells wide, or the shape and its stiffness depend on the mesh. Use a finer mesh for finer members.")
            r = MIN_CELLS * pb.h
        elif r < GOOD_CELLS * pb.h:
            self.notes.append(f"The smallest feature size is {r / pb.h:.1f} element sizes. It works, but the stiffness figure can still change by several per cent with the mesh; "
                              f"{GOOD_CELLS:g} or more element sizes ({GOOD_CELLS * pb.h * 1e3:.2g} mm) makes it mesh-independent.")
        self.radius = r
        est = estimate(pb, r)
        self.estimate = est
        if est["unknowns"] > est["dof_limit"]:
            raise ProblemError(f"The mesh gives {est['unknowns']:,} unknowns (limit {est['dof_limit']:,}"
                               + ("" if est["dof_limit"] == MAX_DOF else "; 400,000 with the optional 'pypardiso' package") + "). Use a coarser mesh for the optimisation.")
        if est["filter_pairs"] > MAX_PAIRS:
            raise ProblemError(f"The minimum member size ({r * 1e3:.3g} mm) is large for a mesh with {pb.n:,} elements ({est['filter_pairs'] / 1e6:.0f} million filter links). "
                               "Reduce the member size or use a coarser mesh.")
        if est["memory_bytes"] > 0.6 * est["free_bytes"]:
            raise ProblemError(f"This run needs about {est['memory_bytes'] / 2 ** 30:.1f} GB and only {est['free_bytes'] / 2 ** 30:.1f} GB is free: use a coarser mesh, "
                               "a larger minimum member size, or close other programs.")
        self.P = self._filter(r)
        self.PT = self.P.T.tocsr()
        w = pb.weight
        self.m_total = float(w.sum())
        self.m_target = s.volume_fraction * self.m_total
        self.m_passive = float(w[pb.passive].sum())
        if self.m_passive >= self.m_target:
            raise ProblemError(f"The elements kept solid round the supports and loads already weigh {100 * self.m_passive / self.m_total:.0f} % of the part, which is "
                               f"more than the {100 * s.volume_fraction:.0f} % to keep. Ask for more, or load and support smaller faces.")
        self.free = np.ones(pb.ndof, bool)
        self.free[pb.fixed] = False
        K1 = pb.asm.matrix(pb.Ke * pb.E0[:, None])
        self.kpen = 1e4 * float(K1.diagonal().max())
        self.Kpen = pb.penalty_matrix(self.kpen)
        self._solid = K1
        self.idx = np.flatnonzero(pb.design)
        self.log(f"Topology optimisation: {pb.n:,} elements ({len(self.idx):,} free to change), {pb.ndof:,} unknowns, member size {r * 1e3:.3g} mm, "
                 f"keep {100 * s.volume_fraction:.0f} % of the mass")

    def _filter(self, r: float) -> sp.csr_matrix:
        """Density filter: each element's density is the average of its neighbours within r (cone weight, weighted by their volume, so a refined or graded
        mesh gives the same shape). Next to a symmetry plane of a half model the mirror image of the part counts as neighbours too."""
        pb = self.pb
        n, c = pb.n, pb.centroid
        tree = cKDTree(c)
        W = tree.sparse_distance_matrix(tree, r, output_type="coo_matrix")
        W.data = r - W.data                                   # (cone weight; the distance of an element to itself is 0, but sparse output drops zeros)
        W = (W + sp.identity(n) * r).tocsr()
        for axis, pos in pb.mirror_planes:
            near = np.flatnonzero(np.abs(c[:, axis] - pos) < r)
            if not len(near):
                continue
            g = c[near].copy()
            g[:, axis] = 2 * pos - g[:, axis]
            Wg = tree.sparse_distance_matrix(cKDTree(g), r, output_type="coo_matrix").tocoo()
            Wg = sp.coo_matrix((r - Wg.data, (Wg.row, near[Wg.col])), shape=(n, n))
            W = W + Wg.tocsr()
        W = (W @ sp.diags(pb.volume)).tocsr()
        hs = np.asarray(W.sum(axis=1)).ravel()
        return (sp.diags(1.0 / hs) @ W).tocsr()

    # ------------------------------------------------------------------ the analysis
    def solve(self, xb: np.ndarray, K=None):
        pb = self.pb
        if K is None:
            E = pb.E0 * (self.s.emin + (1 - self.s.emin) * xb ** self.s.penal)
            K = pb.asm.matrix(pb.Ke * E[:, None])
        if self.Kpen is not None:
            K = K + self.Kpen
        u = np.zeros(pb.ndof)
        u[pb.fixed] = pb.fixed_val
        fr = self.free
        Kc = K.tocsr()
        Kr = Kc[fr]
        Kff = Kr[:, fr]
        rhs = pb.f[fr] - Kr[:, ~fr] @ u[~fr]
        loose = ("The supports do not hold the part in place (it could move or turn freely): add a fixed support, or supports that stop it moving and turning "
                 "in every direction.")
        try:
            uf = _factor_solve(Kff, rhs, lambda *_: None)
        except Exception as e:  # noqa: BLE001
            raise ProblemError(loose) from e
        if not np.all(np.isfinite(uf)):
            raise ProblemError(loose)
        rel = float(np.linalg.norm(Kff @ uf - rhs) / max(np.linalg.norm(rhs), 1e-300))
        if rel > 1e-5:
            raise ProblemError(loose + f" (the equations could only be solved to {rel:.0e})")
        u[fr] = uf
        return u, K

    def baseline(self) -> dict:
        """The part left solid: its compliance, and how well the equations are solved (the residual at the free unknowns, relative to the load).
        It says how stiff the design could be at best, which is what "stiffness kept" is measured against."""
        pb = self.pb
        u, K = self.solve(np.ones(pb.n), self._solid)
        c = float(pb.f @ u)
        res = self._solid @ u - pb.f
        if self.Kpen is not None:
            res = res + self.Kpen @ u
        rel = float(np.linalg.norm(res[self.free]) / max(np.linalg.norm(pb.f), 1e-30))
        if rel > 1e-6:
            self.notes.append(f"The equations of the solid part are solved only to {rel:.1e} (relative): the supports may leave the part free to move.")
        self.balance = rel
        return {"compliance": c, "balance": rel, "u": u}

    # ------------------------------------------------------------------ one evaluation: compliance, mass and their gradients
    def evaluate(self, xd: np.ndarray, beta: float, penal: float | None = None):
        pb, s = self.pb, self.s
        p = penal if penal is not None else s.penal
        x = np.ones(pb.n)
        x[self.idx] = xd
        xt = self.P @ x
        xt[pb.passive] = 1.0
        xb, dxb = _proj(xt, beta) if s.projection else (xt, np.ones_like(xt))
        E = pb.E0 * (s.emin + (1 - s.emin) * xb ** p)
        u, _ = self.solve(xb, pb.asm.matrix(pb.Ke * E[:, None]))
        ce = element_energy(pb.asm.edof, pb.Ke, u)
        c = float(pb.f @ u)
        dc_dxb = -p * (1 - s.emin) * xb ** (p - 1) * pb.E0 * ce
        dxt = dxb.copy()
        dxt[pb.passive] = 0.0
        dc = (self.PT @ (dc_dxb * dxt))[self.idx]
        dm = (self.PT @ (pb.weight * dxt))[self.idx]
        m = float((pb.weight * xb).sum())
        return {"c": c, "dc": dc, "m": m, "dm": dm, "xb": xb, "u": u, "ce": ce}

    def mass_of(self, xd: np.ndarray, beta: float) -> float:
        x = np.ones(self.pb.n)
        x[self.idx] = xd
        xt = self.P @ x
        xt[self.pb.passive] = 1.0
        xb = _proj(xt, beta)[0] if self.s.projection else xt
        return float((self.pb.weight * xb).sum())

    def oc(self, xd, dc, dm, beta):
        """Optimality-criteria step; lambda by bisection so that the mass after filtering and projection is the target."""
        s = self.s
        B = np.maximum(-dc / np.maximum(dm, 1e-30), 1e-30)
        mv = s.move if beta <= 2.0 else s.move * 2.0 / beta       # (smaller steps as the shape sharpens: the designs stops flipping between its limits)
        lo = np.maximum(s.xmin, xd - mv)
        hi = np.minimum(1.0, xd + mv)
        l1, l2 = np.log(B.min() * 1e-6), np.log(B.max() * 1e6)
        xn = xd
        for _ in range(60):
            lm = 0.5 * (l1 + l2)
            xn = np.clip(xd * np.sqrt(B / np.exp(lm)), lo, hi)
            if self.mass_of(xn, beta) > self.m_target:
                l1 = lm
            else:
                l2 = lm
            if l2 - l1 < 1e-9:
                break
        return xn

    # ------------------------------------------------------------------ the loop
    def run(self, on_iter=None, should_stop=None) -> dict:
        pb, s = self.pb, self.s
        t0 = time.time()
        base = self.baseline()
        c_full = base["compliance"]
        self.c_full = c_full
        self.log(f"Solid part: compliance {c_full:.4g} J x 2; equation residual {base['balance']:.1e}")
        xd = np.full(len(self.idx), min(max(s.volume_fraction, 0.05), 0.95))
        beta = 1.0 if s.projection else 1.0
        hist, since = [], 0
        stopped = converged = False
        xb = None
        wd = pb.weight[self.idx]
        for it in range(1, s.max_iter + 1):
            ev = self.evaluate(xd, beta)
            xn = self.oc(xd, ev["dc"], ev["dm"], beta)
            df = np.zeros(pb.n)
            df[self.idx] = xn - xd
            change = float(np.abs(self.P @ df)[self.idx].max())      # (how much the filtered density, the shape itself, changes)
            xb = ev["xb"]
            des = xb[self.idx]
            grey = float((wd * ((des > 0.1) & (des < 0.9))).sum() / wd.sum())
            rec = {"iteration": it, "compliance": ev["c"], "mass_fraction": ev["m"] / self.m_total, "change": change, "beta": beta, "grey": grey,
                   "seconds": time.time() - t0}
            hist.append(rec)
            self.log(f"iteration {it}: compliance {ev['c']:.4g}, mass {100 * rec['mass_fraction']:.1f} %, change {change:.3f}, beta {beta:g}, grey {100 * grey:.0f} %")
            if on_iter is not None:
                on_iter(rec, xb)
            xd = xn
            since += 1
            last = [h["compliance"] for h in hist[-10:] if h["beta"] == beta]
            stalled = len(last) >= 10 and (max(last) - min(last)) < 4e-3 * float(np.mean(last))
            if s.projection and beta < s.beta_max and (change < 0.01 or stalled or since >= s.stage_iters):
                beta, since = beta * 2.0, 0
                continue
            if (change < s.tol or stalled) and (not s.projection or beta >= s.beta_max):
                converged = True
                break
            if should_stop is not None and should_stop():
                stopped = True
                break
        # the shape that goes with the last design variables: one more evaluation, so density and numbers agree
        fin = self.evaluate(xd, beta)
        xb = fin["xb"]
        wt = pb.weight
        mass_sharp = float((wt * (xb >= 0.5)).sum() / self.m_total)
        out = {
            "density": xb, "design": xd, "compliance": fin["c"], "compliance_solid": c_full,
            "stiffness_kept": float(c_full / fin["c"]) if fin["c"] > 0 else None,
            "mass_fraction": float((wt * xb).sum() / self.m_total), "mass_fraction_threshold": mass_sharp,
            "grey_fraction": float((wd * ((xb[self.idx] > 0.1) & (xb[self.idx] < 0.9))).sum() / wd.sum()),
            "iterations": len(hist), "converged": converged, "stopped": stopped, "history": hist, "seconds": time.time() - t0,
            "radius_m": self.radius, "settings": asdict(s), "notes": self.notes, "force_balance_error": getattr(self, "balance", None),
            "n_elements": pb.n, "n_design": int(len(self.idx)), "n_unknowns": pb.ndof, "kept_solid_fraction": self.m_passive / self.m_total,
            "u": fin["u"], "element_energy": fin["ce"],
        }
        if not converged and not stopped:
            self.notes.append(f"The iteration limit ({s.max_iter}) was reached before the shape settled, so it may still change: run more iterations before relying on it.")
        if out["grey_fraction"] > 0.25:
            self.notes.append(f"{100 * out['grey_fraction']:.0f} % of the material is still grey (between solid and empty), so the threshold you pick changes the shape and its mass. "
                              "A larger smallest feature size, a finer mesh or more iterations make it sharper.")
        return out
