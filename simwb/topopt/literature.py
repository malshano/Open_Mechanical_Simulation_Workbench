"""Published benchmarks for the topology optimiser, and the reference codes they are checked with.

The numbers come from two papers. Each reference code is a port of that paper's own appendix code, so a match with the paper's printed values proves the port, and
the port then stands in for the paper wherever the paper prints no number (another mesh, another radius).

    Andreassen, Clausen, Schevenels, Lazarov, Sigmund, "Efficient topology optimization in MATLAB using 88 lines of code", Struct Multidisc Optim 43 (2011):
        the half MBB beam, 60 x 20 / 150 x 50 / 300 x 100 cells, volume fraction 0.5, penalty 3; Fig. 3 (sensitivity and density filter, radius 0.04 x width) and
        Fig. 8 (Heaviside filter, radius 0.03 x width).
    Ferrari and Sigmund, "A new generation 99 line Matlab code for compliance topology optimization and its extension to 3D", Struct Multidisc Optim (2020):
        Fig. 8, the 3D cantilever 48 x 24 x 24, volume fraction 0.12, penalty 3, radius sqrt(3) cells; code in its appendix C.

What is here: PUBLISHED_MBB / PUBLISHED_CANTILEVER (the printed values), top88 (2D reference, three filters), top3d (3D hexahedron reference), cantilever3d_problem (the 3D
cantilever as a Problem for the tetrahedron optimiser), run_module_mbb / run_module_cantilever (the optimiser on those problems), solid_stiffness (element error alone),
guard_lifted (switch off the 3-element-size radius floor so a radius from a paper can be tried) and main(), which prints the comparison.
Run:  python -m simwb.topopt.literature [--full]"""
from __future__ import annotations

import contextlib
import itertools
import time

import numpy as np
import scipy.sparse as sp
from scipy.ndimage import correlate
from scipy.sparse.linalg import spsolve, splu

from . import benchmarks, simp
from .problem import build
from .simp import Optimiser, Settings

# ---------------------------------------------------------------------------------------------------------------- the printed values
# (mesh cells along x, filter: 1 sensitivity, 2 density, 3 Heaviside) -> (radius in cells, compliance printed in the paper)
PUBLISHED_MBB = {
    (60, 1): (2.4, 216.81), (150, 1): (6.0, 219.52), (300, 1): (12.0, 222.29),
    (60, 2): (2.4, 233.71), (150, 2): (6.0, 235.73), (300, 2): (12.0, 238.31),
    (60, 3): (1.8, 189.14), (150, 3): (4.5, 191.49), (300, 3): (9.0, 193.24),
}
# 3D cantilever 48 x 24 x 24, volume fraction 0.12: filter edge 'N' (Neumann) or 'D' (Dirichlet) -> printed compliance. The paper does not print the iteration count,
# the penalty continuation or beta of the call, so the reference run's agreement is to be read with that in mind (penalty 3, beta 2, 100 iterations were used).
PUBLISHED_CANTILEVER = {"N": 3993.7, "D": 4074.3}


# ---------------------------------------------------------------------------------------------------------------- 2D reference (88-line code)
def top88(nelx, nely, volfrac=0.5, penal=3.0, rmin=1.5, ft=1, maxit=3000, log=None):
    """The 88-line code with its three filters (1 sensitivity, 2 density, 3 Heaviside with beta continuation 1 .. 512), the filter built without Python loops.
    Returns {c, iterations, history [(iteration, c, change, beta)], xPhys (nely x nelx), seconds}. benchmarks.top88_reference is the slower line-by-line version."""
    E0, Emin, nu = 1.0, 1e-9, 0.3
    A11 = np.array([[12, 3, -6, -3], [3, 12, 3, 0], [-6, 3, 12, -3], [-3, 0, -3, 12]], float)
    A12 = np.array([[-6, -3, 0, 3], [-3, -6, -3, -6], [0, -3, -6, 3], [3, -6, 3, -6]], float)
    B11 = np.array([[-4, 3, -2, 9], [3, -4, -9, 4], [-2, -9, -4, -3], [9, 4, -3, -4]], float)
    B12 = np.array([[2, -3, 4, -9], [-3, 2, 9, -2], [4, 9, 2, 3], [-9, -2, 3, 2]], float)
    KE = 1 / (1 - nu ** 2) / 24 * (np.block([[A11, A12], [A12.T, A11]]) + nu * np.block([[B11, B12], [B12.T, B11]]))
    node = lambda r, c: r + c * (nely + 1)                                                  # noqa: E731
    rr, cc = np.meshgrid(np.arange(nely), np.arange(nelx), indexing="ij")
    rr, cc = rr.ravel(order="F"), cc.ravel(order="F")
    nodes4 = np.stack([node(rr + 1, cc), node(rr + 1, cc + 1), node(rr, cc + 1), node(rr, cc)], axis=1)
    edof = np.stack([2 * nodes4[:, 0], 2 * nodes4[:, 0] + 1, 2 * nodes4[:, 1], 2 * nodes4[:, 1] + 1, 2 * nodes4[:, 2], 2 * nodes4[:, 2] + 1,
                     2 * nodes4[:, 3], 2 * nodes4[:, 3] + 1], axis=1)
    ndof = 2 * (nelx + 1) * (nely + 1)
    iK, jK = np.repeat(edof, 8, axis=1).ravel(), np.tile(edof, (1, 8)).ravel()
    F = np.zeros(ndof)
    F[1] = -1.0
    fixed = np.union1d(np.arange(0, 2 * (nely + 1), 2), [ndof - 1])
    free = np.setdiff1d(np.arange(ndof), fixed)
    nel = nelx * nely
    r = int(np.ceil(rmin)) - 1
    e1 = np.arange(nel)
    j1, i1 = e1 % nely, e1 // nely
    I, J, S = [], [], []
    for di in range(-r, r + 1):
        for dj in range(-r, r + 1):
            w = rmin - np.hypot(di, dj)
            if w <= 0:
                continue
            i2, j2 = i1 + di, j1 + dj
            ok = (i2 >= 0) & (i2 < nelx) & (j2 >= 0) & (j2 < nely)
            I.append(e1[ok]), J.append(j2[ok] + i2[ok] * nely), S.append(np.full(ok.sum(), w))
    H = sp.csr_matrix((np.concatenate(S), (np.concatenate(I), np.concatenate(J))), shape=(nel, nel))
    Hs = np.asarray(H.sum(axis=1)).ravel()
    x = np.full(nel, volfrac)
    beta = 1.0
    heav = lambda t, b: 1 - np.exp(-b * t) + t * np.exp(-b)                                  # noqa: E731
    xPhys = x.copy() if ft < 3 else heav(x, beta)
    loop = loopbeta = 0
    change = 1.0
    hist = []
    t0 = time.time()
    while change > 0.01 and loop < maxit:
        loop += 1
        loopbeta += 1
        sK = (KE.ravel()[None, :] * (Emin + xPhys[:, None] ** penal * (E0 - Emin))).ravel()
        K = sp.csr_matrix((sK, (iK, jK)), shape=(ndof, ndof))
        K = (K + K.T) / 2
        U = np.zeros(ndof)
        U[free] = spsolve(K[free][:, free].tocsc(), F[free])
        ce = np.einsum("ij,jk,ik->i", U[edof], KE, U[edof])
        c = float(((Emin + xPhys ** penal * (E0 - Emin)) * ce).sum())
        dc = -penal * (E0 - Emin) * xPhys ** (penal - 1) * ce
        dv = np.ones(nel)
        if ft == 1:
            dc = (H @ (x * dc)) / Hs / np.maximum(1e-3, x)
        elif ft == 2:
            dc = H @ (dc / Hs)
            dv = H @ (dv / Hs)
        else:
            xTilde = (H @ x) / Hs
            dx = beta * np.exp(-beta * xTilde) + np.exp(-beta)
            dc = H @ (dc * dx / Hs)
            dv = H @ (dv * dx / Hs)
        l1, l2, move = 0.0, 1e9, 0.2
        while (l2 - l1) / (l1 + l2) > 1e-3:
            lmid = 0.5 * (l2 + l1)
            xnew = np.maximum(0.0, np.maximum(x - move, np.minimum(1.0, np.minimum(x + move, x * np.sqrt(-dc / dv / lmid)))))
            if ft == 1:
                xPhys = xnew
            elif ft == 2:
                xPhys = (H @ xnew) / Hs
            else:
                xPhys = heav((H @ xnew) / Hs, beta)
            if xPhys.sum() > volfrac * nel:
                l1 = lmid
            else:
                l2 = lmid
        change = float(np.abs(xnew - x).max())
        x = xnew
        hist.append((loop, c, change, beta))
        if ft == 3 and beta < 512 and (loopbeta >= 50 or change <= 0.01):
            beta *= 2
            loopbeta = 0
            change = 1.0
        if log and loop % 20 == 0:
            log(f"it {loop} c {c:.2f} beta {beta:g} ch {change:.3f} {time.time() - t0:.0f}s")
    return {"c": hist[-1][1], "iterations": loop, "history": hist, "xPhys": xPhys.reshape(nely, nelx, order="F"), "seconds": time.time() - t0}


# ---------------------------------------------------------------------------------------------------------------- 3D reference (top3D125) and the 3D cantilever
def ke_hex(nu=0.3):
    """Stiffness of a unit cube of unit material, trilinear hexahedron, 2 x 2 x 2 Gauss points. Degrees of freedom ordered (ux, uy, uz) per node."""
    lam, mu = nu / ((1 + nu) * (1 - 2 * nu)), 1.0 / (2 * (1 + nu))
    D = np.zeros((6, 6))
    D[:3, :3] = lam
    D[np.arange(3), np.arange(3)] += 2 * mu
    D[3:, 3:] = np.eye(3) * mu
    sg = np.array([[-1, -1, -1], [1, -1, -1], [1, 1, -1], [-1, 1, -1], [-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1]], float)
    K = np.zeros((24, 24))
    g = 1 / np.sqrt(3)
    for gp in itertools.product([-g, g], repeat=3):
        gp = np.array(gp)
        dN = 0.125 * sg * (1 + sg[:, [1, 2, 0]] * gp[[1, 2, 0]]) * (1 + sg[:, [2, 0, 1]] * gp[[2, 0, 1]]) * 2.0
        B = np.zeros((6, 24))
        for a in range(8):
            bx, by, bz = dN[a]
            B[0, 3 * a] = bx
            B[1, 3 * a + 1] = by
            B[2, 3 * a + 2] = bz
            B[3, 3 * a], B[3, 3 * a + 1] = by, bx
            B[4, 3 * a + 1], B[4, 3 * a + 2] = bz, by
            B[5, 3 * a], B[5, 3 * a + 2] = bz, bx
        K += B.T @ D @ B * 0.125
    return K


def top3d(nelx=48, ft=3, volfrac=0.12, penal=3.0, rmin_cells=np.sqrt(3), bc="N", eta=0.5, beta=2.0, move=0.2, maxit=100, penal_cnt=None, beta_cnt=None, log=None, ch_tol=1e-6):
    """top3D125 on the 3D cantilever of its Fig. 8: domain 48 x 24 x 24 length units fixed at x = 0, a sine-shaped line load (-z) along the lower edge of the free end,
    cubic cells of side 48 / nelx, filter radius rmin_cells in cells (symmetric 'N' or zero 'D' edge), ft 2 = density filter, 3 = volume-preserving projection.
    Returns {c, iterations, history [(iteration, c, volume, change, seconds)], xPhys, seconds, nDof, nEl}."""
    nely = nelz = nelx // 2
    hc = 48.0 / nelx
    nEl = nelx * nely * nelz
    E0, Emin = 1.0, 1e-9
    Ke = ke_hex() * hc
    nid = np.arange((nelx + 1) * (nely + 1) * (nelz + 1)).reshape(nelx + 1, nely + 1, nelz + 1)
    c8 = [nid[:-1, :-1, :-1], nid[1:, :-1, :-1], nid[1:, 1:, :-1], nid[:-1, 1:, :-1], nid[:-1, :-1, 1:], nid[1:, :-1, 1:], nid[1:, 1:, 1:], nid[:-1, 1:, 1:]]
    conn = np.stack([c.ravel() for c in c8], axis=1)
    edof = (3 * conn[:, :, None] + np.arange(3)).reshape(nEl, 24)
    nDof = 3 * nid.size
    iK, jK = np.repeat(edof, 24, axis=1).ravel(), np.tile(edof, (1, 24)).ravel()
    fixed = (3 * nid[0].ravel()[:, None] + np.arange(3)).ravel()
    F = np.zeros(nDof)
    F[3 * nid[nelx, :, 0] + 2] = -hc * np.sin(np.pi * np.arange(nely + 1) * hc / 24.0)
    free = np.setdiff1d(np.arange(nDof), fixed)
    r = int(np.ceil(rmin_cells))
    ax = np.arange(-r + 1, r)
    dx, dy, dz = np.meshgrid(ax, ax, ax, indexing="ij")
    hk = np.maximum(0, rmin_cells - np.sqrt(dx ** 2 + dy ** 2 + dz ** 2))
    mode = "reflect" if bc == "N" else "constant"
    filt = lambda a: correlate(a, hk, mode=mode)                                       # noqa: E731
    shape = (nelx, nely, nelz)
    Hs = filt(np.ones(shape)).ravel()
    dHs = Hs.copy()
    prj = lambda v, e, b: (np.tanh(b * e) + np.tanh(b * (v - e))) / (np.tanh(b * e) + np.tanh(b * (1 - e)))                       # noqa: E731
    dprj = lambda v, e, b: b * (1 - np.tanh(b * (v - e)) ** 2) / (np.tanh(b * e) + np.tanh(b * (1 - e)))                           # noqa: E731
    deta = lambda v, e, b: -b / np.sinh(b) / np.cosh(b * (v - e)) ** 2 * np.sinh(v * b) * np.sinh((1 - v) * b)                      # noqa: E731
    cnt = lambda v, cc, l: v + (l >= cc[0]) * (v < cc[1]) * (l % cc[2] == 0) * cc[3]                                                # noqa: E731
    dV = np.full(nEl, 1.0 / nEl / volfrac)
    x = np.full(nEl, volfrac)
    xPhys, xOld, ch, loop = x.copy(), np.ones(nEl), 1.0, 0
    hist = []
    t0 = time.time()
    while ch > ch_tol and loop < maxit:
        loop += 1
        xPhys = filt((x / Hs).reshape(shape)).ravel()
        if ft > 1:
            if ft == 3:
                f = prj(xPhys, eta, beta).mean() - volfrac
                while abs(f) > 1e-6:
                    eta = eta - f / deta(xPhys, eta, beta).mean()
                    f = prj(xPhys, eta, beta).mean() - volfrac
                dHs = Hs / dprj(xPhys, eta, beta)
            xPhys = prj(xPhys, eta, beta)
        ch = np.linalg.norm(xPhys - xOld) / nEl
        xOld = xPhys
        sK = Emin + xPhys ** penal * (E0 - Emin)
        K = sp.csr_matrix(((Ke.ravel()[None, :] * sK[:, None]).ravel(), (iK, jK)), shape=(nDof, nDof))
        U = np.zeros(nDof)
        U[free] = splu(K[free][:, free].tocsc(), permc_spec="MMD_AT_PLUS_A", options={"SymmetricMode": True}).solve(F[free])
        ce = np.einsum("ij,jk,ik->i", U[edof], Ke, U[edof])
        c = float(F @ U)
        dsK = -penal * (E0 - Emin) * xPhys ** (penal - 1)
        dc = filt((dsK * ce).reshape(shape)).ravel() / dHs
        dV0 = filt(dV.reshape(shape)).ravel() / dHs
        xl, xu = x - move, x + move
        ocP = x * np.sqrt(np.maximum(-dc, 0) / dV0)
        l1, l2 = 0.0, ocP.mean() / volfrac
        while (l2 - l1) / (l2 + l1) > 1e-4:
            lm = 0.5 * (l1 + l2)
            xn = np.maximum(np.maximum(np.minimum(np.minimum(ocP / lm, xu), 1), xl), 0)
            if xn.mean() > volfrac:
                l1 = lm
            else:
                l2 = lm
        x = xn
        if penal_cnt:
            penal = cnt(penal, penal_cnt, loop)
        if beta_cnt:
            beta = cnt(beta, beta_cnt, loop)
        hist.append((loop, c, float(xPhys.mean()), ch, time.time() - t0))
        if log:
            log(f"it {loop} c {c:.2f} vol {xPhys.mean():.3f} ch {ch:.2e} t {time.time() - t0:.0f}s")
    return {"c": hist[-1][1], "iterations": loop, "history": hist, "xPhys": xPhys.reshape(shape), "seconds": time.time() - t0, "nDof": nDof, "nEl": nEl}


def cantilever3d_problem(nx: int):
    """The same cantilever for the tetrahedron optimiser: a box 48 x 24 x 24 of nx x nx/2 x nx/2 cubes cut into tetrahedra, unit material, fixed at x = 0, and the
    published sine line load (the face-load conditions are replaced by those nodal forces). Returns a Problem."""
    ny = nz = nx // 2
    hc = 48.0 / nx

    def label(cen, nrm):
        out = np.zeros(len(cen), int)
        out[cen[:, 0] < 1e-9] = 1
        out[(cen[:, 0] > 48 - 1e-9) & (cen[:, 2] < hc + 1e-9)] = 2
        return out
    mesh = benchmarks.box_mesh(48.0, 24.0, 24.0, nx, ny, nz, label)
    props = {1: {"E": 1.0, "nu": 0.3, "rho": 1.0, "name": "unit material"}}
    bcs = [{"name": "Fixed", "type": "fixed_support", "faces": [1], "params": {}},
           {"name": "Load", "type": "force", "faces": [2], "params": {"fx_N": 0.0, "fy_N": 0.0, "fz_N": -1.0}}]
    pb = build(mesh, props, bcs, keep_conditions_solid=False, log=lambda *a: None)
    nodes = pb.nodes
    ld = np.flatnonzero((np.abs(nodes[:, 0] - 48) < 1e-9) & (np.abs(nodes[:, 2]) < 1e-9))
    pb.f[:] = 0.0
    pb.f[3 * ld + 2] = -hc * np.sin(np.pi * nodes[ld, 1] / 24.0)
    pb.applied = np.array([0.0, 0.0, pb.f[3 * ld + 2].sum()])
    return pb


# ---------------------------------------------------------------------------------------------------------------- the module on those problems
@contextlib.contextmanager
def guard_lifted(min_cells: float = 1.0):
    """Switch the radius floor (3 element sizes) off for a moment, to try a filter radius taken from a paper. Not for users: below the floor the stiffness depends on the mesh."""
    old = simp.MIN_CELLS
    simp.MIN_CELLS = min_cells
    try:
        yield
    finally:
        simp.MIN_CELLS = old


def _run(pb, **kw) -> dict:
    t0 = time.time()
    res = Optimiser(pb, Settings(**kw), log=lambda *a, **k: None).run()
    return {"c": res["compliance"], "c_solid": res["compliance_solid"], "iterations": res["iterations"], "converged": res["converged"], "seconds": time.time() - t0,
            "mass": res["mass_fraction"], "grey": res["grey_fraction"], "radius": res["radius_m"], "n_elements": res["n_elements"], "n_unknowns": res["n_unknowns"],
            "notes": res["notes"], "density": res["density"]}


def run_module_mbb(nelx: int, nely: int, radius: float, projection: bool = False, beta_max: float = 8.0, max_iter: int = 450, **kw) -> dict:
    """The half MBB beam as a slab of tetrahedra through the module's optimiser (volume fraction 0.5, penalty 3, density filter, optional projection)."""
    return _run(benchmarks.mbb_slab_problem(nelx, nely), volume_fraction=0.5, penal=3.0, radius=radius, projection=projection, beta_max=beta_max, max_iter=max_iter,
                tol=2e-3, cleanup_islands=False, **kw)


def run_module_cantilever(nx: int, radius: float, projection: bool = False, max_iter: int = 250, **kw) -> dict:
    """The 3D cantilever through the module's optimiser (volume fraction 0.12, penalty 3, density filter)."""
    return _run(cantilever3d_problem(nx), volume_fraction=0.12, penal=3.0, radius=radius, projection=projection, max_iter=max_iter, tol=2e-3, cleanup_islands=False, **kw)


def solid_stiffness(nx: int, radius: float = 6.8) -> dict:
    """The 3D cantilever left solid: compliance by the module's tetrahedra and by the hexahedron reference (one solve each). Their ratio is the element error alone."""
    opt = Optimiser(cantilever3d_problem(nx), Settings(volume_fraction=0.12, radius=radius, projection=False), log=lambda *a, **k: None)
    return {"tets": opt.baseline()["compliance"], "hex": top3d(nx, ft=2, volfrac=0.999999, rmin_cells=1.01, maxit=1)["c"]}


def main(argv=None) -> None:
    import sys
    full = "--full" in (argv if argv is not None else sys.argv[1:])
    meshes = [(60, 20), (150, 50)] + ([(300, 100)] if full else [])
    print("2D MBB beam, reference code against the published values")
    for nx, ny in meshes:
        for ft in (1, 2, 3):
            rmin, pub = PUBLISHED_MBB[(nx, ft)]
            r = top88(nx, ny, 0.5, 3.0, rmin, ft)
            print(f"  {nx} x {ny}  filter {ft}  radius {rmin:g}  published {pub:.2f}  reference {r['c']:.2f}  ({100 * (r['c'] - pub) / pub:+.2f} %)  {r['iterations']} iterations")
    print("module (tetrahedra), 60 x 20, density filter, radius 3.37 cells (the smallest it accepts)")
    r = run_module_mbb(60, 20, 3.0)
    ref = top88(60, 20, 0.5, 3.0, r["radius"], 2)["c"]
    print(f"  reference at that radius {ref:.2f}  module {r['c']:.2f}  ({100 * (r['c'] - ref) / ref:+.2f} %)")


if __name__ == "__main__":
    main()
