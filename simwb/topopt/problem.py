"""The structural problem the optimiser solves, built from the project's tetrahedral mesh, its materials and its boundary conditions.

Supported conditions (the same ones the static study has): fixed support, frictionless support / symmetry plane, prescribed displacement, force and
pressure on faces. Own weight is NOT part of the optimisation (it changes with the shape); the study says so when gravity is on.

The elements next to a support or a loaded face are kept solid (`passive`): material is never removed where the load goes in. Everything else in the
chosen parts is the design space."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import scipy.sparse as sp

from .fe import Assembler, unit_stiffness

SUPPORTED = ("fixed_support", "frictionless_support", "symmetry", "force", "pressure", "free")


class ProblemError(ValueError):
    """A set-up the optimiser cannot start from; the message says what to change."""


@dataclass
class Problem:
    nodes: np.ndarray
    tets: np.ndarray
    volume: np.ndarray                 # (N,) element volumes, m3
    weight: np.ndarray                 # (N,) element masses at full density, kg (the volume target is a mass target)
    E0: np.ndarray                     # (N,) Young's modulus of the solid material, Pa
    Ke: np.ndarray                     # (N, 144) element stiffness for unit modulus
    asm: Assembler
    f: np.ndarray
    fixed: np.ndarray                  # constrained dofs
    fixed_val: np.ndarray
    pen: list                          # [(nodes, unit normals)] frictionless supports that are not along an axis (penalty)
    passive: np.ndarray                # (N,) kept solid
    design: np.ndarray                 # (N,) free to change
    centroid: np.ndarray               # (N, 3)
    h: float                           # typical element size, m
    applied: np.ndarray                # total applied force, N
    notes: list = field(default_factory=list)
    bc_report: dict = field(default_factory=dict)
    body_id: np.ndarray | None = None
    mirror_planes: list = field(default_factory=list)   # [(axis, position)] flat symmetry planes of a half model: the filter sees the mirrored part

    @property
    def n(self) -> int:
        return len(self.tets)

    @property
    def ndof(self) -> int:
        return self.asm.ndof

    def penalty_matrix(self, k: float):
        if not self.pen:
            return None
        rows, cols, vals = [], [], []
        for nodes, nn in self.pen:
            for i in range(3):
                for j in range(3):
                    rows.append(3 * nodes + i)
                    cols.append(3 * nodes + j)
                    vals.append(k * nn[:, i] * nn[:, j])
        return sp.coo_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))), shape=(self.ndof, self.ndof)).tocsr()


def tri_owners(mesh: dict) -> np.ndarray:
    """A tetrahedron owning each boundary triangle of the CAD faces."""
    if "tri_owner" in mesh:
        return np.asarray(mesh["tri_owner"], np.int64)
    loc = np.array([[1, 2, 3], [0, 3, 2], [0, 1, 3], [0, 2, 1]])
    t = mesh["tets"].astype(np.int64)
    F = np.sort(t[:, loc].reshape(-1, 3), axis=1)
    cells = np.repeat(np.arange(len(t)), 4)
    T = np.sort(mesh["tris"].astype(np.int64), axis=1)
    _, inv = np.unique(np.vstack([F, T]), axis=0, return_inverse=True)
    inv = inv.ravel()
    first = np.full(inv.max() + 1, -1)
    first[inv[: len(F)][::-1]] = cells[::-1]
    own = first[inv[len(F):]]
    own[own < 0] = 0
    return own


def _tri_geometry(p, t, tr, own):
    """(outward unit normals, areas) of boundary triangles; outward = away from the owning tetrahedron."""
    a, b, c = p[tr[:, 0]], p[tr[:, 1]], p[tr[:, 2]]
    nA = 0.5 * np.cross(b - a, c - a)
    out = (a + b + c) / 3 - p[t[own]].mean(axis=1)
    nA *= np.where(np.einsum("ij,ij->i", nA, out) < 0, -1.0, 1.0)[:, None]
    A = np.linalg.norm(nA, axis=1)
    return nA / np.maximum(A, 1e-300)[:, None], A


def build(mesh: dict, body_props: dict, bcs: list, design_bodies=None, keep_layers: int = 0, gravity=None, log=print) -> Problem:
    """mesh: nodes, tets, tet_vol, tris, tri_surf. body_props: {id: {E, nu, rho, name}}. bcs: the project's face conditions.
    design_bodies: ids of the parts that may change (None: all). keep_layers: extra layers of elements kept solid round the supports and loads."""
    p = np.asarray(mesh["nodes"], float)
    t = np.asarray(mesh["tets"], np.int64)
    tv = np.asarray(mesh["tet_vol"])
    tris = np.asarray(mesh["tris"], np.int64)
    ts = np.asarray(mesh["tri_surf"])
    N = len(t)
    E, nu, rho = np.zeros(N), np.zeros(N), np.zeros(N)
    for bid, bp in body_props.items():
        m = tv == int(bid)
        E[m], nu[m], rho[m] = float(bp["E"]), float(bp["nu"]), float(bp.get("rho") or 0.0)
    if (E <= 0).any():
        raise ProblemError("Some parts have no material (stiffness) yet: choose their materials in Setup.")
    if (rho <= 0).any():
        raise ProblemError("Some parts have no density: the volume target is a mass target, so every material needs a density.")
    Ke, V = unit_stiffness(p, t, nu)
    asm = Assembler(t, len(p))
    ndof = asm.ndof
    notes = []
    if gravity is not None and np.any(np.asarray(gravity, float)):
        notes.append("Own weight is left out of the optimisation (it changes with the shape): only the loads you set are used. Re-check the result with a static study that includes gravity.")

    own = tri_owners(mesh)
    f = np.zeros(ndof)
    fixed: dict = {}
    pen = []
    planes = []
    passive = np.zeros(N, bool)
    applied = np.zeros(3)
    rep = {}
    n_load = n_support = 0
    for bc in bcs:
        kind = bc.get("type")
        if kind == "displacement":
            vals = [bc.get("params", {}).get(k) for k in ("ux_mm", "uy_mm", "uz_mm")]
            if any(v not in (None, "") and float(v) != 0.0 for v in vals):
                raise ProblemError(f"The condition '{bc.get('name', kind)}' moves a face by a given amount. The optimiser makes the part as stiff as possible "
                                   "against FORCES, so a prescribed movement cannot be used: replace it with a force or a pressure, or use a fixed support (a movement of zero).")
        if kind not in SUPPORTED and kind != "displacement":
            raise ProblemError(f"The condition '{bc.get('name', kind)}' ({kind}) cannot be used in a topology optimisation: use fixed or frictionless supports, "
                               "displacements, forces and pressures on faces.")
        if kind == "free":
            continue
        sel = np.isin(ts, [int(x) for x in bc.get("faces", [])])
        tr = tris[sel]
        if not len(tr):
            log(f"warning: condition '{bc.get('name', kind)}' has no mesh faces")
            continue
        n_hat, A = _tri_geometry(p, t, tr, own[sel])
        nodes = np.unique(tr.ravel())
        prm = bc.get("params") or {}
        if kind != "symmetry":                                    # (material is kept where the part is held or loaded; a mirror plane is neither)
            passive[own[sel]] = True
        rep[bc.get("name", kind)] = {"type": kind, "area_m2": float(A.sum())}
        if kind == "fixed_support":
            n_support += 1
            for d in (3 * nodes[:, None] + np.arange(3)).ravel():
                fixed[int(d)] = 0.0
        elif kind == "displacement":
            n_support += 1
            for k, key in enumerate(("ux_mm", "uy_mm", "uz_mm")):
                v = prm.get(key)
                if v is None or v == "":
                    continue
                for nd in nodes:
                    fixed[int(3 * nd + k)] = float(v) * 1e-3
        elif kind in ("frictionless_support", "symmetry"):
            n_support += 1
            nn = np.zeros((len(p), 3))
            for col in range(3):
                np.add.at(nn, tr[:, col], n_hat * A[:, None])
            nn = nn[nodes]
            nn /= np.maximum(np.linalg.norm(nn, axis=1), 1e-300)[:, None]
            axis = np.argmax(np.abs(nn), axis=1)
            along = np.abs(nn[np.arange(len(nn)), axis]) > 0.999
            if kind == "symmetry" and along.all() and len(set(axis.tolist())) == 1:  # (the project's symmetry plane: the filter sees the mirror image; a roller is not a mirror)
                ax0 = int(axis[0])
                coord = p[nodes, ax0]
                if np.ptp(coord) < 1e-6 * max(float(np.ptp(p, axis=0).max()), 1e-30):
                    planes.append((ax0, float(coord.mean())))
            for nd, k in zip(nodes[along], axis[along]):          # (a plane across an axis: that one displacement is held, exactly)
                fixed[int(3 * nd + k)] = 0.0
            if (~along).any():                                     # (a tilted or curved face: held by a stiff penalty)
                pen.append((nodes[~along], nn[~along]))
        else:                                                      # force or pressure
            n_load += 1
            if kind == "force":
                F = np.array([float(prm.get(k) or 0.0) for k in ("fx_N", "fy_N", "fz_N")])
                tri_F = (A / A.sum())[:, None] * F[None, :]
            else:
                tri_F = -float(prm.get("pressure_Pa") or 0.0) * (A[:, None] * n_hat)
            for col in range(3):
                np.add.at(f, (3 * tr[:, col, None] + np.arange(3)).ravel(), (tri_F / 3.0).ravel())
            applied += tri_F.sum(axis=0)
            rep[bc.get("name", kind)]["force_N"] = tri_F.sum(axis=0).tolist()
    if not fixed and not pen:
        raise ProblemError("The part is not supported: add a fixed support (or supports that stop it moving freely) before optimising.")
    if n_load == 0:
        raise ProblemError("There is no load: add a force or a pressure on a face. Without a load there is nothing to carry and nothing to optimise.")
    if np.linalg.norm(f) == 0:
        raise ProblemError("The loads add up to zero force: check the values of the forces and pressures.")
    for _ in range(int(keep_layers)):
        near = np.isin(t, np.unique(t[passive])).any(axis=1)
        passive |= near
    design = np.isin(tv, [int(b) for b in design_bodies]) if design_bodies is not None else np.ones(N, bool)
    design &= ~passive
    passive = ~design                                                    # (kept solid: round the supports and loads, and every part marked 'keep solid')
    if not design.any():
        raise ProblemError("Nothing is left to optimise: the supports and loaded faces fill the whole design space. Choose a part that is bigger than its supports.")
    fd = np.array(sorted(fixed), np.int64)
    c = p[t].mean(axis=1)
    h = float(np.median((6 * np.sqrt(2) * V) ** (1.0 / 3.0)))
    return Problem(p, t, V, rho * V, E, Ke, asm, f, fd, np.array([fixed[int(d)] for d in fd]), pen, passive, design, c, h, applied, notes, rep, tv, planes)
