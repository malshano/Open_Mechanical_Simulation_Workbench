"""Small structured tetrahedral meshes and the classic test problems (a cantilever beam, an MBB-type beam) for checking the optimiser against theory and
against the static solver. Meshes are cut from a regular grid with the six-tetrahedra split of every cube, which is conforming."""
from __future__ import annotations

import itertools

import numpy as np


def box_mesh(L: float, W: float, H: float, nx: int, ny: int, nz: int, label=None, grade: float = 1.0) -> dict:
    """A box [0, L] x [0, W] x [0, H] in nx x ny x nz cubes cut into tetrahedra; the dict the project's mesh has (nodes, tets, tet_vol, tris, tri_surf).
    label(centroid (m, 3), normal (m, 3)) -> face id per boundary triangle (default 0). grade > 1 makes the cells finer towards x = 0 (a graded mesh)."""
    xs, ys, zs = L * np.linspace(0, 1, nx + 1) ** grade, np.linspace(0, W, ny + 1), np.linspace(0, H, nz + 1)
    X, Y, Z = np.meshgrid(xs, ys, zs, indexing="ij")
    nodes = np.stack([X.ravel(), Y.ravel(), Z.ravel()], axis=1)
    idx = np.arange(len(nodes)).reshape(nx + 1, ny + 1, nz + 1)
    base = idx[:nx, :ny, :nz].ravel()
    e = [(ny + 1) * (nz + 1), nz + 1, 1]
    tets = []
    for perm in itertools.permutations(range(3)):
        v0 = base
        v1 = v0 + e[perm[0]]
        v2 = v1 + e[perm[1]]
        v3 = v2 + e[perm[2]]
        tets.append(np.stack([v0, v1, v2, v3], axis=1))
    tets = np.vstack(tets).astype(np.int64)
    loc = np.array([[1, 2, 3], [0, 3, 2], [0, 1, 3], [0, 2, 1]])
    faces = tets[:, loc].reshape(-1, 3)
    key = np.sort(faces, axis=1)
    _, first, cnt = np.unique(key, axis=0, return_index=True, return_counts=True)
    tris = faces[first[cnt == 1]]
    cen = nodes[tris].mean(axis=1)
    a, b, c = nodes[tris[:, 0]], nodes[tris[:, 1]], nodes[tris[:, 2]]
    nrm = np.cross(b - a, c - a)
    nrm /= np.linalg.norm(nrm, axis=1)[:, None]
    surf = np.zeros(len(tris), int) if label is None else np.asarray(label(cen, nrm), int)
    return {"nodes": nodes, "tets": tets, "tet_vol": np.ones(len(tets), int), "tris": tris, "tri_surf": surf}


def cantilever(L=2.0, W=0.5, H=1.0, h=0.1, load="end", force=-1.0):
    """A beam fixed at x = 0 and loaded with a vertical force at its free end (face 2: the whole end face, or 'patch': its upper-middle part).
    E = 1, nu = 0.3, rho = 1 (unit values, so the numbers are the dimensionless ones of the literature). Returns (mesh, body_props, bcs)."""
    nx, ny, nz = max(int(round(L / h)), 1), max(int(round(W / h)), 1), max(int(round(H / h)), 1)

    def label(cen, nrm):
        out = np.zeros(len(cen), int)
        out[(cen[:, 0] < 1e-9)] = 1
        end = cen[:, 0] > L - 1e-9
        if load == "patch":
            end &= (np.abs(cen[:, 1] - W / 2) < 0.26 * W) & (cen[:, 2] > 0.55 * H)
        out[end] = 2
        return out
    mesh = box_mesh(L, W, H, nx, ny, nz, label)
    props = {1: {"E": 1.0, "nu": 0.3, "rho": 1.0, "name": "unit material"}}
    bcs = [{"name": "Fixed", "type": "fixed_support", "faces": [1], "params": {}},
           {"name": "Load", "type": "force", "faces": [2], "params": {"fx_N": 0.0, "fy_N": 0.0, "fz_N": force}}]
    return mesh, props, bcs


def mbb_half(L=3.0, W=0.1, H=1.0, h=0.1, force=-1.0):
    """The MBB beam (a simply supported beam with a load at mid-span) as its symmetric half: a frictionless plane at x = 0 (the mid-span), the load on a
    patch of the top face next to that plane, a roller under the far end (z = 0, x = L: the support is the bottom edge strip, held in z only through a
    frictionless plane would over-constrain, so it is held fully on a small patch). Returns (mesh, body_props, bcs)."""
    nx, ny, nz = max(int(round(L / h)), 1), max(int(round(W / h)), 1), max(int(round(H / h)), 1)

    def label(cen, nrm):
        out = np.zeros(len(cen), int)
        out[cen[:, 0] < 1e-9] = 1                                           # the mirror plane
        out[(cen[:, 2] > H - 1e-9) & (cen[:, 0] < 1.5 * h)] = 2             # load patch on top, at the plane
        out[(cen[:, 2] < 1e-9) & (cen[:, 0] > L - 1.5 * h)] = 3             # the support under the far end
        return out
    mesh = box_mesh(L, W, H, nx, ny, nz, label)
    props = {1: {"E": 1.0, "nu": 0.3, "rho": 1.0, "name": "unit material"}}
    bcs = [{"name": "Mirror plane", "type": "frictionless_support", "faces": [1], "params": {}},
           {"name": "Load", "type": "force", "faces": [2], "params": {"fx_N": 0.0, "fy_N": 0.0, "fz_N": force}},
           {"name": "Support", "type": "fixed_support", "faces": [3], "params": {}}]
    return mesh, props, bcs



# ---------------------------------------------------------------------------------------------------------------- the published MBB benchmark
def top88_reference(nelx: int = 60, nely: int = 20, volfrac: float = 0.5, penal: float = 3.0, rmin: float = 1.5, ft: int = 1, maxit: int = 1000) -> dict:
    """The 88-line topology optimisation code of Andreassen, Clausen, Schevenels, Lazarov and Sigmund (Struct Multidisc Optim 43, 2011, appendix) ported
    line by line: bilinear plane-stress quadrilaterals, E0 = 1, Emin = 1e-9, nu = 0.3, the half MBB beam (a unit force down at the top-left node, the left edge held
    in x, the bottom-right corner held in y), SIMP, optimality criteria with a move limit of 0.2, a sensitivity filter (ft = 1) or a density filter (ft = 2) with a
    cone kernel of radius rmin cells, stopping when no density changes by more than 0.01. Returns {c, x (nely x nelx physical densities), iterations, history}."""
    import scipy.sparse as sp
    from scipy.sparse.linalg import spsolve
    E0, Emin, nu = 1.0, 1e-9, 0.3
    A11 = np.array([[12, 3, -6, -3], [3, 12, 3, 0], [-6, 3, 12, -3], [-3, 0, -3, 12]], float)
    A12 = np.array([[-6, -3, 0, 3], [-3, -6, -3, -6], [0, -3, -6, 3], [3, -6, 3, -6]], float)
    B11 = np.array([[-4, 3, -2, 9], [3, -4, -9, 4], [-2, -9, -4, -3], [9, 4, -3, -4]], float)
    B12 = np.array([[2, -3, 4, -9], [-3, 2, 9, -2], [4, 9, 2, 3], [-9, -2, 3, 2]], float)
    KE = 1 / (1 - nu ** 2) / 24 * (np.block([[A11, A12], [A12.T, A11]]) + nu * np.block([[B11, B12], [B12.T, B11]]))
    node = lambda r, c: r + c * (nely + 1)                                        # noqa: E731  (nodes are numbered down the columns, row 0 on top)
    rr, cc = np.meshgrid(np.arange(nely), np.arange(nelx), indexing="ij")
    rr, cc = rr.ravel(order="F"), cc.ravel(order="F")                               # (elements are numbered down the columns too)
    nodes4 = np.stack([node(rr + 1, cc), node(rr + 1, cc + 1), node(rr, cc + 1), node(rr, cc)], axis=1)   # bottom-left, bottom-right, top-right, top-left
    edof = np.stack([2 * nodes4[:, 0], 2 * nodes4[:, 0] + 1, 2 * nodes4[:, 1], 2 * nodes4[:, 1] + 1, 2 * nodes4[:, 2], 2 * nodes4[:, 2] + 1,
                     2 * nodes4[:, 3], 2 * nodes4[:, 3] + 1], axis=1)
    ndof = 2 * (nelx + 1) * (nely + 1)
    iK, jK = np.repeat(edof, 8, axis=1).ravel(), np.tile(edof, (1, 8)).ravel()
    F = np.zeros(ndof)
    F[1] = -1.0
    fixed = np.union1d(np.arange(0, 2 * (nely + 1), 2), [ndof - 1])
    free = np.setdiff1d(np.arange(ndof), fixed)
    # the filter
    r = int(np.ceil(rmin)) - 1
    ii, jj, ss = [], [], []
    for i1 in range(nelx):
        for j1 in range(nely):
            e1 = j1 + i1 * nely
            for i2 in range(max(i1 - r, 0), min(i1 + r, nelx - 1) + 1):
                for j2 in range(max(j1 - r, 0), min(j1 + r, nely - 1) + 1):
                    w = max(0.0, rmin - np.hypot(i1 - i2, j1 - j2))
                    if w > 0:
                        ii.append(e1), jj.append(j2 + i2 * nely), ss.append(w)
    H = sp.csr_matrix((ss, (ii, jj)), shape=(nelx * nely, nelx * nely))
    Hs = np.asarray(H.sum(axis=1)).ravel()
    x = np.full(nelx * nely, volfrac)
    xPhys = x.copy()
    hist, loop, change = [], 0, 1.0
    while change > 0.01 and loop < maxit:
        loop += 1
        sK = (KE.ravel()[None, :] * (Emin + xPhys[:, None] ** penal * (E0 - Emin))).ravel()
        K = sp.csr_matrix((sK, (iK, jK)), shape=(ndof, ndof))
        K = (K + K.T) / 2
        U = np.zeros(ndof)
        U[free] = spsolve(K[free][:, free].tocsc(), F[free])
        ce = np.einsum("ij,jk,ik->i", U[edof], KE, U[edof])
        c = float(((Emin + xPhys ** penal * (E0 - Emin)) * ce).sum())
        dc = -penal * (E0 - Emin) * xPhys ** (penal - 1) * ce
        dv = np.ones(nelx * nely)
        if ft == 1:
            dc = (H @ (x * dc)) / Hs / np.maximum(1e-3, x)
        elif ft == 2:
            dc = H @ (dc / Hs)
            dv = H @ (dv / Hs)
        l1, l2, move = 0.0, 1e9, 0.2
        while (l2 - l1) / (l1 + l2) > 1e-3:
            lmid = 0.5 * (l2 + l1)
            xnew = np.maximum(0.0, np.maximum(x - move, np.minimum(1.0, np.minimum(x + move, x * np.sqrt(-dc / dv / lmid)))))
            xPhys = xnew if ft == 1 else (H @ xnew) / Hs
            if xPhys.sum() > volfrac * nelx * nely:
                l1 = lmid
            else:
                l2 = lmid
        change = float(np.abs(xnew - x).max())
        x = xnew
        hist.append({"iteration": loop, "c": c, "vol": float(xPhys.mean()), "change": change})
    return {"c": hist[-1]["c"], "x": xPhys.reshape(nely, nelx, order="F"), "iterations": loop, "history": hist}


def mbb_slab(nelx: int = 60, nely: int = 20):
    """The same half MBB beam for the tetrahedron optimiser: a slab one cell thick (unit cells, so plane stress with unit thickness), cut into tetrahedra.
    The unit force down at the top-left (shared between the two nodes through the thickness), the left face held in x, the bottom-right corner held in z.
    Returns (mesh, body_props, bcs, extra_fixed_dofs, load_vector_builder(problem))."""
    def label(cen, nrm):
        out = np.zeros(len(cen), int)
        out[cen[:, 0] < 1e-9] = 1                                                  # the left face
        out[(cen[:, 2] > nely - 1e-9) & (cen[:, 0] < 1.01)] = 2                   # (a load face: only so that the problem has a load; its force is replaced by the point load)
        return out
    mesh = box_mesh(float(nelx), 1.0, float(nely), nelx, 1, nely, label)
    props = {1: {"E": 1.0, "nu": 0.3, "rho": 1.0, "name": "unit material"}}
    bcs = [{"name": "Symmetry plane", "type": "frictionless_support", "faces": [1], "params": {}},
           {"name": "Load", "type": "force", "faces": [2], "params": {"fz_N": -1.0}}]
    return mesh, props, bcs


def mbb_slab_problem(nelx: int = 60, nely: int = 20):
    """mbb_slab as a Problem with the published point load and roller: the conditions of the face model are replaced by exactly those of the 88-line code."""
    from .problem import build
    mesh, props, bcs = mbb_slab(nelx, nely)
    pb = build(mesh, props, bcs, keep_conditions_solid=False)
    nodes = pb.nodes
    tl = np.flatnonzero((np.abs(nodes[:, 0]) < 1e-9) & (np.abs(nodes[:, 2] - nely) < 1e-9))          # the two nodes at the top-left, through the thickness
    br = np.flatnonzero((np.abs(nodes[:, 0] - nelx) < 1e-9) & (np.abs(nodes[:, 2]) < 1e-9))           # the two nodes at the bottom-right
    pb.f[:] = 0.0
    pb.f[3 * tl + 2] = -1.0 / len(tl)
    fixed = dict(zip(pb.fixed.tolist(), pb.fixed_val.tolist()))
    for nd in br:
        fixed[int(3 * nd + 2)] = 0.0
    pb.fixed = np.array(sorted(fixed), np.int64)
    pb.fixed_val = np.array([fixed[int(d)] for d in pb.fixed])
    pb.applied = np.array([0.0, 0.0, -1.0])
    return pb
