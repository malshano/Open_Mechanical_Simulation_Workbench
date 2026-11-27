"""Linear tetrahedron finite elements for the topology optimiser: element matrices for unit stiffness, a fast repeated assembly (the sparsity pattern
is worked out once; every iteration only sums the element values into it) and the element strain energy used for the sensitivities.

Linear (4-node) tetrahedra are used inside the loop because the optimiser solves hundreds of times; they are stiffer than the 10-node tetrahedra of
the static solver, so the final shape should be re-checked with the static study (the module reports the gap)."""
from __future__ import annotations

import numpy as np
import scipy.sparse as sp

D_LAM = np.zeros((6, 6))
D_LAM[:3, :3] = 1.0
D_MU = np.diag([2.0, 2.0, 2.0, 1.0, 1.0, 1.0])          # (Voigt order xx yy zz xy yz zx, engineering shear)
CHUNK = 40_000


def tet_gradients(p: np.ndarray):
    """p (N, 4, 3) corner coordinates -> (G (N, 4, 3) gradients of the four shape functions, V (N,) volumes)."""
    M = p[:, 1:, :] - p[:, :1, :]
    det = np.linalg.det(M)
    Minv = np.linalg.inv(M)
    G = np.empty((len(p), 4, 3))
    G[:, 1:, :] = np.transpose(Minv, (0, 2, 1))
    G[:, 0, :] = -G[:, 1:, :].sum(axis=1)
    return G, np.abs(det) / 6.0


def b_matrix(G: np.ndarray) -> np.ndarray:
    """Strain-displacement matrices (N, 6, 12)."""
    B = np.zeros((len(G), 6, 12))
    for a in range(4):
        gx, gy, gz = G[:, a, 0], G[:, a, 1], G[:, a, 2]
        c = 3 * a
        B[:, 0, c] = gx
        B[:, 1, c + 1] = gy
        B[:, 2, c + 2] = gz
        B[:, 3, c], B[:, 3, c + 1] = gy, gx
        B[:, 4, c + 1], B[:, 4, c + 2] = gz, gy
        B[:, 5, c], B[:, 5, c + 2] = gz, gx
    return B


def unit_stiffness(nodes: np.ndarray, tets: np.ndarray, nu: np.ndarray):
    """(Ke (N, 144) for Young's modulus 1 and each element's Poisson ratio, V (N,))."""
    N = len(tets)
    Ke = np.empty((N, 144))
    V = np.empty(N)
    for a in range(0, N, CHUNK):
        sl = slice(a, min(a + CHUNK, N))
        G, v = tet_gradients(nodes[tets[sl]])
        if (v <= 0).any():
            raise ValueError("The mesh has flat (zero-volume) tetrahedra: generate the mesh again")
        B = b_matrix(G)
        lam = nu[sl] / ((1 + nu[sl]) * (1 - 2 * nu[sl]))
        mu = 1.0 / (2 * (1 + nu[sl]))
        D = lam[:, None, None] * D_LAM + mu[:, None, None] * D_MU
        Ke[sl] = (v[:, None, None] * np.einsum("nia,nij,njb->nab", B, D, B)).reshape(-1, 144)
        V[sl] = v
    return Ke, V


class Assembler:
    """Sums element matrices (N, 144) into the global sparse matrix with a sparsity pattern found once."""

    def __init__(self, tets: np.ndarray, n_nodes: int):
        N = len(tets)
        self.ndof = 3 * n_nodes
        self.edof = (3 * tets[:, :, None] + np.arange(3)).reshape(N, 12)
        i = np.repeat(self.edof, 12, axis=1).ravel()
        j = np.tile(self.edof, (1, 12)).ravel()
        key = i.astype(np.int64) * self.ndof + j
        del i, j
        self.order = np.argsort(key, kind="stable")
        ks = key[self.order]
        del key
        new = np.empty(len(ks), bool)
        new[0] = True
        new[1:] = ks[1:] != ks[:-1]
        self.starts = np.flatnonzero(new)
        uk = ks[self.starts]
        del ks
        rows = uk // self.ndof
        idx_t = np.int32 if len(uk) < 2 ** 31 - 1 else np.int64
        self.indices = (uk % self.ndof).astype(idx_t)
        ptr = np.zeros(self.ndof + 1, np.int64)
        np.cumsum(np.bincount(rows, minlength=self.ndof), out=ptr[1:])
        self.indptr = ptr.astype(idx_t)

    def matrix(self, vals: np.ndarray) -> sp.csr_matrix:
        data = np.add.reduceat(np.ascontiguousarray(vals).ravel()[self.order], self.starts)
        return sp.csr_matrix((data, self.indices, self.indptr), shape=(self.ndof, self.ndof))


def element_energy(edof: np.ndarray, Ke: np.ndarray, u: np.ndarray) -> np.ndarray:
    """u_e^T Ke_unit u_e for every element (the strain energy of that element at unit Young's modulus, times two)."""
    out = np.empty(len(edof))
    for a in range(0, len(edof), CHUNK):
        sl = slice(a, min(a + CHUNK, len(edof)))
        ue = u[edof[sl]]
        out[sl] = np.einsum("na,nab,nb->n", ue, Ke[sl].reshape(-1, 12, 12), ue)
    return out
