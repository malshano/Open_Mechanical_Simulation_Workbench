"""Workbench mesh -> openCFS input: an ASCII Gmsh .msh (format 2.2) with one physical group per region.

openCFS reads Gmsh 2.2 (``<gmsh fileName=.../>`` in the simulation XML; VERIFIED: the official test suite example
Singlefield/Acoustics/Abc3d/Abc3d.msh is a 2.2 ASCII file with $PhysicalNames and `2 1 "abc"` / `3 2 "acoust3d"`
lines; the region names of the simulation XML are the physical-group names). Linear tetrahedra (type 4) and
triangles (type 2) only: a first-order mesh, the same corner mesh the built-in solvers start from.

What the workbench mesh gives (simwb.gmsh_worker): nodes (n, 3) m, tets (m, 4), tet_vol (m,) body id per tet,
tris (k, 3) boundary AND body-interface triangles, tri_surf (k,) CAD face id per triangle. Interface triangles
between two bodies are found here from the tets themselves (a face shared by tets of two bodies), so the coupling
surface needs no face information from the user.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

_FACES = ((1, 2, 3), (0, 3, 2), (0, 1, 3), (0, 2, 1))  # the four faces of a tet (outward when the tet is positive)


def _tet_faces(tets: np.ndarray):
    """(face nodes (4m, 3), owner tet (4m,)) of every tet face, in the tet's own node order."""
    f = np.stack([tets[:, list(c)] for c in _FACES], 1).reshape(-1, 3)
    return f, np.repeat(np.arange(len(tets)), 4)


def _key(tri: np.ndarray, n: int) -> np.ndarray:
    s = np.sort(tri, axis=1).astype(np.int64)
    return (s[:, 0] * n + s[:, 1]) * n + s[:, 2]


def face_sets(mesh: dict, is_fluid_body) -> dict:
    """Classify the faces of the tet mesh.

    is_fluid_body: callable(body_id) -> bool. Returns
      "interface": (k, 3) triangles shared by a solid tet and a fluid tet (the fluid-structure coupling surface)
      "solid_boundary": (triangles, cad face ids) of faces with one tet only, on a solid body
      "fluid_boundary": same on a fluid body
    Triangles keep the node order of the tet they were taken from.
    """
    nodes = np.asarray(mesh["nodes"], float)
    tets = np.asarray(mesh["tets"], np.int64)
    tv = np.asarray(mesh["tet_vol"])
    n = len(nodes)
    f, owner = _tet_faces(tets)
    key = _key(f, n)
    order = np.argsort(key, kind="stable")
    ks = key[order]
    first = np.r_[True, ks[1:] != ks[:-1]]
    grp = np.cumsum(first) - 1
    count = np.bincount(grp)
    fluid_t = np.array([bool(is_fluid_body(int(b))) for b in tv])
    idx_first = order[first]
    single = count == 1
    # a face with two tets: the second one is the next entry in the sorted order
    pair_second = np.full(len(count), -1, np.int64)
    pos = np.flatnonzero(first)
    two = count == 2
    pair_second[two] = order[pos[two] + 1]
    ia = idx_first[two]
    ib = pair_second[two]
    mixed = fluid_t[owner[ia]] != fluid_t[owner[ib]]
    iface = np.where(fluid_t[owner[ia]][mixed, None], f[ia][mixed], f[ib][mixed])  # (oriented from the fluid tet)
    # faces shared by two solids (or two fluids) are internal: nothing to write
    ib_idx = idx_first[single]
    bt = f[ib_idx]
    b_fluid = fluid_t[owner[ib_idx]]
    # CAD face id of the triangles (from the workbench's tris / tri_surf), -1 when the mesh has none
    has_tris = "tris" in mesh and len(mesh["tris"]) > 0
    if has_tris:
        tk = _key(np.asarray(mesh["tris"], np.int64), n)
        srt = np.argsort(tk)
        tks = tk[srt]
        tsurf = np.asarray(mesh["tri_surf"])

    def cad_face(tri):
        out = np.full(len(tri), -1, np.int64)
        if has_tris and len(tri):
            k = _key(tri, n)
            pos2 = np.minimum(np.searchsorted(tks, k), len(tks) - 1)
            hit = tks[pos2] == k
            out[hit] = tsurf[srt[pos2[hit]]]
        return out

    surf = cad_face(bt)
    isurf = cad_face(iface)
    return {"interface": iface, "interface_surf": isurf,
            "solid_boundary": (bt[~b_fluid], surf[~b_fluid]),
            "fluid_boundary": (bt[b_fluid], surf[b_fluid])}


def _cfs_orient(nodes: np.ndarray, tets: np.ndarray) -> np.ndarray:
    """openCFS wants tets with a NEGATIVE (b-a)x(c-a).(d-a) (VERIFIED: a mesh with positive tets from gmsh gives
    "3623 elements had a wrong orientation and were reoriented", swapping the 2nd and 3rd node makes it silent)."""
    p = nodes
    v = np.einsum("ij,ij->i", np.cross(p[tets[:, 1]] - p[tets[:, 0]], p[tets[:, 2]] - p[tets[:, 0]]),
                  p[tets[:, 3]] - p[tets[:, 0]])
    out = tets.copy()
    pos = v > 0
    out[pos] = tets[pos][:, [0, 2, 1, 3]]
    return out


def write_msh(path, nodes, volumes: dict, surfaces: dict | None = None, points: dict | None = None) -> dict:
    """Write an ASCII Gmsh 2.2 file.

    volumes:  {region name: (m, 4) tetrahedra (0-based node indices into nodes)}
    surfaces: {surface-region name: (k, 3) triangles}
    points:   {node-region name: array of node indices}
    Nodes used by no element are dropped. Returns {"used": array of the original node index of each written node
    (written id = position + 1)}.
    """
    surfaces = surfaces or {}
    points = points or {}
    nodes = np.asarray(nodes, float)
    all_idx = [np.asarray(v, np.int64).ravel() for v in list(volumes.values()) + list(surfaces.values())
               + list(points.values()) if len(v)]
    used = np.unique(np.concatenate(all_idx)) if all_idx else np.zeros(0, np.int64)
    remap = -np.ones(len(nodes), np.int64)
    remap[used] = np.arange(len(used))
    names = []  # (dim, id, name)
    for nm in points:
        names.append((0, len(names) + 1, nm))
    for nm in surfaces:
        names.append((2, len(names) + 1, nm))
    for nm in volumes:
        names.append((3, len(names) + 1, nm))
    pid = {nm: i for _, i, nm in names}
    out = ["$MeshFormat", "2.2 0 8", "$EndMeshFormat", "$PhysicalNames", str(len(names))]
    out += [f'{d} {i} "{nm}"' for d, i, nm in names]
    out.append("$EndPhysicalNames")
    out += ["$Nodes", str(len(used))]
    out += [f"{i + 1} {x:.12g} {y:.12g} {z:.12g}" for i, (x, y, z) in enumerate(nodes[used])]
    out.append("$EndNodes")
    elems = []
    eid = 1
    for nm, arr in points.items():
        for v in np.asarray(arr, np.int64).ravel():
            elems.append(f"{eid} 15 2 {pid[nm]} {pid[nm]} {remap[v] + 1}")
            eid += 1
    for nm, arr in surfaces.items():
        for a, b, c in remap[np.asarray(arr, np.int64).reshape(-1, 3)] + 1:
            elems.append(f"{eid} 2 2 {pid[nm]} {pid[nm]} {a} {b} {c}")
            eid += 1
    for nm, arr in volumes.items():
        for a, b, c, d in remap[_cfs_orient(nodes, np.asarray(arr, np.int64).reshape(-1, 4))] + 1:
            elems.append(f"{eid} 4 2 {pid[nm]} {pid[nm]} {a} {b} {c} {d}")
            eid += 1
    out += ["$Elements", str(len(elems))] + elems + ["$EndElements"]
    Path(path).write_text("\n".join(out) + "\n", newline="\n")
    return {"used": used}


def read_msh(path) -> dict:
    """Read back an ASCII Gmsh 2.2 file written by write_msh (tests, and a check that the file is as intended):
    {"nodes", "names": {id: (dim, name)}, "elements": [(type, physical id, node ids (1-based))]}."""
    lines = Path(path).read_text().splitlines()
    it = iter(lines)
    res = {"names": {}, "elements": []}
    for ln in it:
        ln = ln.strip()
        if ln == "$MeshFormat":
            res["format"] = next(it).split()[0]
        elif ln == "$PhysicalNames":
            for _ in range(int(next(it))):
                d, i, nm = next(it).split(None, 2)
                res["names"][int(i)] = (int(d), nm.strip().strip('"'))
        elif ln == "$Nodes":
            k = int(next(it))
            res["nodes"] = np.array([[float(x) for x in next(it).split()[1:4]] for _ in range(k)])
        elif ln == "$Elements":
            for _ in range(int(next(it))):
                v = [int(x) for x in next(it).split()]
                nt = v[2]
                res["elements"].append((v[1], v[3] if nt else 0, v[3 + nt:]))
    return res
