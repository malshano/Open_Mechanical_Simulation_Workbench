"""Repair of collapsed tetrahedra in a volume mesh.

A CAD model with micro-edges (1-2 micrometres, left by cuts and fuses) gives the mesher three nodes within a few
micrometres of each other, and the tetrahedron on them has a volume near zero (2e-17 m3 next to cells of 5e-4 m3).
OpenFOAM then sees a Courant number of thousands at the first step and the run diverges. The cure is the one a
mesher applies: collapse the short edge of the bad cell, moving one node onto the other, when that is safe.

``collapse_slivers`` does that on the arrays the mesher returns. A node on the surface may only move onto a node that
lies on all the same surfaces (so no surface loses its shape), no neighbouring cell may flip or become another
sliver, and no surface triangle may fold over; an edge that does not pass these checks is left alone.
"""
from __future__ import annotations

import math

import numpy as np


def tet_quality(p: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Normalised shape ratio 6*sqrt(2)*V / l_rms^3 (1 for a regular tetrahedron)."""
    a, b, c, d = (p[t[:, i]] for i in range(4))
    vol = np.abs(np.einsum("ij,ij->i", np.cross(b - a, c - a), d - a)) / 6.0
    l2 = (((b - a) ** 2).sum(1) + ((c - a) ** 2).sum(1) + ((d - a) ** 2).sum(1)
          + ((c - b) ** 2).sum(1) + ((d - b) ** 2).sum(1) + ((d - c) ** 2).sum(1)) / 6.0
    return np.clip(6 * math.sqrt(2) * vol / np.maximum(l2, 1e-300) ** 1.5, 0, 1)


def tet_altitude_ratio(p: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Smallest altitude of each tetrahedron over its rms edge length (0.82 for a regular one, near 0 for a flat
    sliver: four nodes almost in a plane, edges of ordinary length, the cell thin as paper). A flat cell has a
    Courant number inversely proportional to its thickness, which limits the time step of a moving-mesh run."""
    a, b, c, d = (p[t[:, i]] for i in range(4))
    vol = np.abs(np.einsum("ij,ij->i", np.cross(b - a, c - a), d - a)) / 6.0
    area = np.stack([np.linalg.norm(np.cross(q - r, s - r), axis=1) / 2 for q, r, s in
                     ((b, c, d), (a, c, d), (a, b, d), (a, b, c))], axis=1).max(1)
    l2 = (((b - a) ** 2).sum(1) + ((c - a) ** 2).sum(1) + ((d - a) ** 2).sum(1)
          + ((c - b) ** 2).sum(1) + ((d - b) ** 2).sum(1) + ((d - c) ** 2).sum(1)) / 6.0
    return 3.0 * vol / np.maximum(area, 1e-300) / np.sqrt(np.maximum(l2, 1e-300))


def _score(p, t, q_min, a_min):
    """Below 1 = a bad cell: the smaller of quality / q_min and altitude ratio / a_min."""
    return np.minimum(tet_quality(p, t) / q_min, tet_altitude_ratio(p, t) / a_min)


def _signed_vol(p, t):
    a, b, c, d = (p[t[:, i]] for i in range(4))
    return np.einsum("ij,ij->i", np.cross(b - a, c - a), d - a) / 6.0


def _tri_normals(p, t):
    return np.cross(p[t[:, 1]] - p[t[:, 0]], p[t[:, 2]] - p[t[:, 0]])


_EDGES = ((0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3))


def collapse_slivers(p, tets, tet_vol, tris, tri_surf, ztris, ztri_zone, q_min=0.02, edge_ratio=0.25, rounds=6,
                     tiny=None, any_edge=True, a_min=0.05):
    """Collapse an edge of every tetrahedron with shape quality below q_min or altitude ratio below a_min (a flat
    sliver whose quality number is still ordinary: 8 micrometres thick on 0.3 mm edges scores 0.03). With any_edge (the default) each of the
    six edges is tried, shortest first, until one passes the checks below (a flat cap sliver is cured by moving its
    free apex onto a base node, an edge that is not short at all); otherwise only the shortest edge, and only when it
    is under edge_ratio of the longest. An edge shorter than `tiny`
    (default 2e-5 of the size of the mesh: 40 micrometres on a 2 m model) may join nodes on different surfaces
    (the two faces at a CAD micro-edge), the merged node then lying on both. tets must be positively
    oriented. Returns (tets, tet_vol, tris, tri_surf, ztris, ztri_zone, n_collapsed); the node array is not changed
    (collapsed nodes are simply no longer used)."""
    p = np.asarray(p, float)
    tets, tet_vol = np.array(tets, np.int64), np.array(tet_vol)
    tris, tri_surf = np.array(tris, np.int64).reshape(-1, 3), np.array(tri_surf)
    ztris, ztri_zone = np.array(ztris, np.int64).reshape(-1, 3), np.array(ztri_zone)
    # the surfaces every node lies on (boundary surfaces by id, zone interfaces offset)
    on: dict = {}
    for arr, lab, off in ((tris, tri_surf, 0), (ztris, ztri_zone, 1 << 20)):
        for tri, s in zip(arr, lab):
            for n in tri:
                on.setdefault(int(n), set()).add(int(s) + off)
    if tiny is None:
        tiny = 2e-5 * float(np.linalg.norm(p.max(0) - p.min(0)))
    total = 0
    for _ in range(rounds):
        q = _score(p, tets, q_min, a_min)
        cand = np.flatnonzero(q < 1.0)
        if not len(cand):
            break
        cand = cand[np.argsort(q[cand])][:5000]
        alive_t = np.ones(len(tets), bool)
        alive_b = np.ones(len(tris), bool)
        alive_z = np.ones(len(ztris), bool)
        touched: set = set()
        done = 0
        def try_edge(u, v, length):
            """Collapse the edge (u, v) when it is safe; True when it was done."""
            su, sv = on.get(u, set()), on.get(v, set())
            if su <= sv:      # u moves onto v (u is on no surface v is not on)
                a, b = u, v
            elif sv <= su:
                a, b = v, u
            elif length <= tiny and not any(x >= 1 << 20 for x in su ^ sv):
                a, b = (u, v) if len(su) <= len(sv) else (v, u)   # (two walls meeting at a micro-edge)
            else:
                return False
            ok = True
            with_a = np.flatnonzero((tets == a).any(1) & alive_t)
            gone = [i for i in with_a if (tets[i] == b).any()]
            move = [i for i in with_a if not (tets[i] == b).any()]
            new_t = np.where(tets[move] == a, b, tets[move]) if move else np.zeros((0, 4), np.int64)
            if len(move):
                v_old, v_new = _signed_vol(p, tets[move]), _signed_vol(p, new_t)
                # the cells that remain must not flip, and the worst of the cells this touches must get better
                # (a cluster of slivers is repaired over several collapses, each a step up from the one before; nodes are
                # only removed, so this ends)
                q_new, q_old = _score(p, new_t, q_min, a_min).min(), _score(p, tets[with_a], q_min, a_min).min()
                ok = bool((v_new > 0).all() and (v_new > 1e-3 * np.abs(v_old)).all()
                          and (q_new >= 1.0 or q_new > 1.5 * q_old))
            surf = []
            for arr, alive in ((tris, alive_b), (ztris, alive_z)):
                wa = np.flatnonzero((arr == a).any(1) & alive)
                g = [i for i in wa if (arr[i] == b).any()]
                m = [i for i in wa if not (arr[i] == b).any()]
                nt = np.where(arr[m] == a, b, arr[m]) if m else np.zeros((0, 3), np.int64)
                if ok and len(m):
                    n_old, n_new = _tri_normals(p, arr[m]), _tri_normals(p, nt)
                    ok = bool((np.einsum("ij,ij->i", n_old, n_new) > 0).all())
                surf.append((g, m, nt))
            if not ok:
                return False
            for i in gone:
                alive_t[i] = False
            if len(move):
                tets[move] = new_t
            for (g, m, nt), (arr, alive) in zip(surf, ((tris, alive_b), (ztris, alive_z))):
                alive[g] = False
                if len(m):
                    arr[m] = nt
            on.setdefault(b, set()).update(on.get(a, set()))
            on.pop(a, None)
            touched.update(int(n) for i in with_a for n in tets[i])
            touched.update((a, b))
            return True

        for ti in cand:
            tn = tets[ti]
            if not alive_t[ti] or any(int(n) in touched for n in tn):
                continue
            ln = np.array([np.linalg.norm(p[tn[i]] - p[tn[j]]) for i, j in _EDGES])
            for k in np.argsort(ln)[: 6 if any_edge else 1]:
                if not any_edge and ln[k] > edge_ratio * ln.max():
                    break
                if try_edge(int(tn[_EDGES[k][0]]), int(tn[_EDGES[k][1]]), float(ln[k])):
                    done += 1
                    break
        tets, tet_vol = tets[alive_t], tet_vol[alive_t]
        tris, tri_surf = tris[alive_b], tri_surf[alive_b]
        ztris, ztri_zone = ztris[alive_z], ztri_zone[alive_z]
        total += done
        if not done:
            break
    return tets, tet_vol, tris, tri_surf, ztris, ztri_zone, total
