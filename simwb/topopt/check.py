"""Questions to ask of a finished shape, without solving anything: which pieces of it hang together, and which supports and loads are joined to the rest.
A floating piece carries nothing; a support that is not joined to the loaded part is a support the shape does not use. No Qt here.

Pieces are joined by shared FACES: two blocks that touch only along an edge or at a corner are two pieces (they could hinge), and a piece counts as held only if
it touches a support at three or more nodes."""
from __future__ import annotations

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

MIN_SUPPORT_NODES = 3


def face_nodes(tris, tri_surf, faces) -> np.ndarray:
    """The mesh nodes on these CAD faces."""
    sel = np.isin(tri_surf, [int(f) for f in faces])
    return np.unique(np.asarray(tris)[sel].ravel())


def tet_components(tets) -> np.ndarray:
    """A piece number for every tetrahedron: tetrahedra that share a face are in the same piece."""
    t = np.asarray(tets, np.int64)
    m = len(t)
    loc = np.array([[1, 2, 3], [0, 3, 2], [0, 1, 3], [0, 2, 1]])
    F = np.sort(t[:, loc].reshape(-1, 3), axis=1)
    N = np.int64(t.max() + 1)
    key = F[:, 0] * N * N + F[:, 1] * N + F[:, 2]
    order = np.argsort(key, kind="stable")
    ks = key[order]
    same = ks[1:] == ks[:-1]
    a, b = order[:-1][same] // 4, order[1:][same] // 4
    _, lab = connected_components(coo_matrix((np.ones(len(a)), (a, b)), shape=(m, m)), directed=False)
    return lab


def nodes_per_piece(tets, tlab, node_mask) -> np.ndarray:
    """How many distinct nodes of `node_mask` (a boolean per mesh node) each piece has."""
    t = np.asarray(tets, np.int64)
    rep = np.repeat(tlab, 4)
    nd = t.ravel()
    sel = node_mask[nd]
    pairs = np.unique(np.stack([rep[sel], nd[sel]], axis=1), axis=0) if sel.any() else np.zeros((0, 2), np.int64)
    return np.bincount(pairs[:, 0], minlength=int(tlab.max()) + 1) if len(pairs) else np.zeros(int(tlab.max()) + 1, int)


def held_pieces(tets, tlab, sup_mask, load_mask) -> np.ndarray:
    """The pieces that carry a load to a support: they have a loaded node and touch a support at three or more nodes."""
    ns = nodes_per_piece(tets, tlab, sup_mask)
    nl = nodes_per_piece(tets, tlab, load_mask)
    return np.flatnonzero((ns >= MIN_SUPPORT_NODES) & (nl >= 1))


def connectivity(nodes, tets, density, threshold: float, weight, supports: dict, loads: dict) -> dict:
    """The shape at a density threshold: how many pieces it has, how much of it floats, and which support faces are not joined to the loaded part.
    supports / loads: {condition name: [node array of each of its faces]}. weight: per element (the mass share of a piece)."""
    keep = np.asarray(density) >= threshold
    t = np.asarray(tets, np.int64)[keep]
    n = len(nodes)
    if not len(t):
        return {"pieces": 0, "floating_share": 0.0, "unattached": {}, "loads_joined": False, "threshold": threshold, "largest_floating_share": 0.0}
    tlab = tet_components(t)
    w = np.asarray(weight, float)[keep]
    load_mask = np.zeros(n, bool)
    for faces in loads.values():
        for nd in faces:
            load_mask[nd] = True
    nl = nodes_per_piece(t, tlab, load_mask)
    main = int(nl.argmax()) if nl.max() > 0 else -1                           # (the piece that holds most of the loaded nodes)
    share = np.bincount(tlab, weights=w, minlength=int(tlab.max()) + 1)
    pieces = int((share > 0).sum())
    floating = float(w[tlab != main].sum() / w.sum()) if main >= 0 else 1.0
    unattached = {}
    for name, faces in supports.items():
        bad = 0
        for nd in faces:
            mask = np.zeros(n, bool)
            mask[nd] = True
            cnt = nodes_per_piece(t, tlab, mask)
            if main < 0 or cnt[main] < MIN_SUPPORT_NODES:
                bad += 1
        if bad:
            unattached[name] = {"faces": len(faces), "unattached": bad}
    others = np.delete(share, main) if 0 <= main < len(share) else share
    return {"pieces": pieces, "floating_share": floating, "unattached": unattached, "loads_joined": main >= 0, "threshold": threshold,
            "largest_floating_share": float(others.max() / w.sum()) if pieces > 1 and main >= 0 and len(others) else 0.0}
