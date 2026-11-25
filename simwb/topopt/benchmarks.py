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
