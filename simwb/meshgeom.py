"""STL / OBJ (triangle mesh) geometry for the gmsh worker.

A triangle mesh has no CAD faces. The triangles are split into faces by gmsh at sharp edges
(classifySurfaces + createGeometry: each face is reparametrized so it can be remeshed), and the volumes are
rebuilt with the built-in kernel: one solid per closed shell, and for flow studies a box of air with the
shells as holes. No booleans are needed, so this works for any closed (watertight) mesh.

Units: triangle meshes carry none; coordinates are read as millimetres (times the user's scale factor).
The same input always gives the same entity tags, so the mesher rebuilds the model from the source file.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np

FEATURE_ANGLE_DEG = 40.0  # edges sharper than this split faces


def read_triangles(path, fmt: str):
    """(points (N,3), triangles (M,3)) with duplicate vertices merged and degenerate triangles removed."""
    data = Path(path).read_bytes()
    if fmt == "obj":
        pts, tris = _read_obj(data.decode("utf-8", errors="replace"))
    elif data[:5].lower() == b"solid" and b"facet" in data[:2000] and b"vertex" in data[:4000]:
        v = np.array(re.findall(rb"vertex\s+(\S+)\s+(\S+)\s+(\S+)", data), float)
        pts, tris = v, np.arange(len(v)).reshape(-1, 3)
    else:
        if len(data) < 84:
            raise RuntimeError("The STL file is empty or truncated.")
        n = int(np.frombuffer(data[80:84], np.uint32)[0])
        if len(data) < 84 + 50 * n:
            raise RuntimeError("The STL file is truncated (fewer triangles than its header says).")
        rec = np.frombuffer(data[84:84 + 50 * n], np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]))
        v = rec["v"].reshape(-1, 3).astype(float)
        pts, tris = v, np.arange(len(v)).reshape(-1, 3)
    if not len(tris):
        raise RuntimeError("The file contains no triangles.")
    if not np.all(np.isfinite(pts)):
        raise RuntimeError("The file contains invalid coordinates.")
    # merge coincident vertices (tolerance relative to the model size)
    tol = 1e-7 * max(float(np.ptp(pts, axis=0).max()), 1e-12)
    key = np.round(pts / tol).astype(np.int64)
    _, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    pts, tris = pts[first], inv.ravel()[tris]
    ok = (tris[:, 0] != tris[:, 1]) & (tris[:, 1] != tris[:, 2]) & (tris[:, 0] != tris[:, 2])
    tris = tris[ok]
    _, keep = np.unique(np.sort(tris, axis=1), axis=0, return_index=True)  # duplicate triangles
    tris = tris[np.sort(keep)]
    return pts, tris


def _read_obj(text):
    vs, faces = [], []
    for line in text.splitlines():
        if line.startswith("v "):
            vs.append([float(x) for x in line.split()[1:4]])
        elif line.startswith("f "):
            idx = []
            for tok in line.split()[1:]:
                i = int(tok.split("/")[0])
                idx.append(i - 1 if i > 0 else len(vs) + i)
            for k in range(1, len(idx) - 1):  # fan-triangulate polygons
                faces.append([idx[0], idx[k], idx[k + 1]])
    if not vs:
        raise RuntimeError("The OBJ file contains no vertices.")
    return np.array(vs, float), np.array(faces, np.int64).reshape(-1, 3)


def shells(tris, n_pts):
    """Connected components of the triangle mesh; raises if a shell is not closed."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    e = np.sort(np.vstack([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]]), axis=1)
    _, cnt = np.unique(e, axis=0, return_counts=True)
    n_open, n_multi = int((cnt == 1).sum()), int((cnt > 2).sum())
    if n_open or n_multi:
        what = (f"{n_open} open edges" if n_open else "") + (", " if n_open and n_multi else "") + \
               (f"{n_multi} edges shared by more than two triangles" if n_multi else "")
        raise RuntimeError(f"The mesh is not a closed solid ({what}). Repair it (e.g. Meshmixer, Blender 3D-Print "
                           "toolbox, PrusaSlicer 'repair') or export a STEP file from your CAD tool.")
    m = len(tris)
    rows = np.repeat(np.arange(m), 3)
    g = coo_matrix((np.ones(3 * m), (rows, tris.ravel())), shape=(m, n_pts)).tocsr()
    adj = g @ g.T
    n, lab = connected_components(adj, directed=False)
    return [np.where(lab == k)[0] for k in range(n)]


def volume_centroid(pts, tris):
    a, b, c = pts[tris[:, 0]], pts[tris[:, 1]], pts[tris[:, 2]]
    v6 = np.einsum("ij,ij->i", a, np.cross(b, c))
    V = v6.sum() / 6.0
    cen = ((a + b + c) * v6[:, None]).sum(0) / (24.0 * V) if V else pts.mean(0)
    return abs(float(V)), cen


def build(gmsh, path, fmt, scale, study, domain, flow_dir=(1, 0, 0), span=None, up_axis=None, rotation=None,
          translation=None):
    """Create the gmsh model. Returns (bodies, faces, domain_info, parts_bbox) like the STEP path."""
    pts, tris = read_triangles(path, fmt)
    pts = pts * 1e-3 * float(scale)
    if rotation is not None:  # user orientation: 3x3 rotation about the origin
        pts = pts @ np.asarray(rotation, float).T
    if translation is not None:  # (then shifted: a turn about the middle of the parts)
        pts = pts + np.asarray(translation, float)
    comps = shells(tris, len(pts))
    gmsh.clear()
    gmsh.model.add("mesh_import")
    node_comp = np.full(len(pts), -1)
    for k, ids in enumerate(comps):
        node_comp[np.unique(tris[ids])] = k
    tag_off = 1
    for k, ids in enumerate(comps):
        t = tris[ids]
        nodes = np.unique(t)
        s = gmsh.model.addDiscreteEntity(2)
        gmsh.model.mesh.addNodes(2, s, (nodes + 1).tolist(), pts[nodes].ravel().tolist())
        gmsh.model.mesh.addElementsByType(s, 2, list(range(tag_off, tag_off + len(t))), (t + 1).ravel().tolist())
        tag_off += len(t)
    gmsh.model.mesh.classifySurfaces(np.radians(FEATURE_ANGLE_DEG), True, True, np.pi)
    gmsh.model.mesh.createGeometry()
    geo = gmsh.model.geo

    # which shell each new face belongs to (the classification keeps the original nodes)
    surf_comp, surf_data = {}, {}
    for _, s in gmsh.model.getEntities(2):
        nt, _, _ = gmsh.model.mesh.getNodes(2, s, includeBoundary=True)
        et, _, en = gmsh.model.mesh.getElements(2, s)
        tri = np.asarray(en[0], np.int64).reshape(-1, 3) - 1 if len(en) else np.zeros((0, 3), np.int64)
        surf_comp[s] = int(np.bincount(node_comp[np.asarray(nt, np.int64) - 1]).argmax())
        surf_data[s] = tri
    comp_surfs = [[s for s, c in surf_comp.items() if c == k] for k in range(len(comps))]

    bodies, solid_tags = [], []
    names = [f"Body {k + 1}" for k in range(len(comps))]
    comp_info = [volume_centroid(pts, tris[ids]) for ids in comps]
    bb_of = [np.r_[pts[np.unique(tris[ids])].min(0), pts[np.unique(tris[ids])].max(0)] for ids in comps]
    loops = [geo.addSurfaceLoop(ss) for ss in comp_surfs]
    allbb = np.r_[np.min([b[:3] for b in bb_of], 0), np.max([b[3:] for b in bb_of], 0)]
    L = float(np.max(allbb[3:] - allbb[:3]))
    domain_info = {}
    if study in ("heat_conduction", "vibration_modal", "static_structural", "cht_enclosure"):
        for k, lp in enumerate(loops):
            solid_tags.append(geo.addVolume([lp]))
    box = None
    if study in ("external_aero", "cht_enclosure"):
        from . import domain as air

        sp = max(L, float(span or 0.0))
        spec = air.resolve({**domain, "shape": "box"}, study, allbb, L, sp, flow_dir, up_axis)
        msg = air.check_encloses(spec, allbb, L)
        if msg:
            raise RuntimeError(msg + " Use 'Fit to parts' or enlarge it.")
        lo, hi = np.array(spec["min"]), np.array(spec["max"])
        box = (lo, hi)
        domain_info = {"box_min": lo.tolist(), "box_max": hi.tolist(), "ref_length": L, "spec": spec}
        if study == "external_aero":
            domain_info["span"] = sp
        else:
            domain_info.update(enclosure_mode="auto_box", enclosure_body=-1)
    box_faces = []
    fluid_tag = None
    if box is not None:
        lo, hi = box
        P = {}
        for i in range(2):
            for j in range(2):
                for k in range(2):
                    P[i, j, k] = geo.addPoint(lo[0] if i == 0 else hi[0], lo[1] if j == 0 else hi[1],
                                              lo[2] if k == 0 else hi[2])
        lines = {}

        def line(a, b):
            if (b, a) in lines:
                return -lines[b, a]
            if (a, b) not in lines:
                lines[a, b] = geo.addLine(P[a], P[b])
            return lines[a, b]

        quads = [((0, 0, 0), (0, 1, 0), (0, 1, 1), (0, 0, 1)), ((1, 0, 0), (1, 0, 1), (1, 1, 1), (1, 1, 0)),
                 ((0, 0, 0), (0, 0, 1), (1, 0, 1), (1, 0, 0)), ((0, 1, 0), (1, 1, 0), (1, 1, 1), (0, 1, 1)),
                 ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)), ((0, 0, 1), (0, 1, 1), (1, 1, 1), (1, 0, 1))]
        for q in quads:
            cl = geo.addCurveLoop([line(q[i], q[(i + 1) % 4]) for i in range(4)])
            box_faces.append((geo.addPlaneSurface([cl]), q))
        outer = geo.addSurfaceLoop([s for s, _ in box_faces])
        fluid_tag = geo.addVolume([outer] + loops)
    geo.synchronize()

    # bodies
    for k, tag in enumerate(solid_tags):
        V, c = comp_info[k]
        bodies.append({"id": int(tag), "name": names[k], "role": "solid", "source_index": k, "volume": V,
                       "centroid": np.asarray(c).tolist(), "bbox": bb_of[k].tolist()})
    if fluid_tag is not None:
        lo, hi = box
        Vb = float(np.prod(hi - lo)) - sum(v for v, _ in comp_info)
        bodies.append({"id": int(fluid_tag), "name": "Air", "role": "fluid", "source_index": -1, "volume": Vb,
                       "centroid": ((lo + hi) / 2).tolist(), "bbox": [*lo.tolist(), *hi.tolist()]})

    # faces (with their display triangulation)
    faces = []
    for s, tri in surf_data.items():
        a, b, c = pts[tri[:, 0]], pts[tri[:, 1]], pts[tri[:, 2]]
        nA = 0.5 * np.cross(b - a, c - a)
        A = np.linalg.norm(nA, axis=1)
        n = nA.sum(0)
        planar = bool(len(A) and np.all(np.einsum("ij,j->i", nA, n / (np.linalg.norm(n) or 1)) > 0.9998 * A))
        used, loc = np.unique(tri, return_inverse=True)
        faces.append({
            "id": int(s), "area": float(A.sum()), "centroid": ((((a + b + c) / 3) * A[:, None]).sum(0) / (A.sum() or 1)).tolist(),
            "normal": (n / (np.linalg.norm(n) or 1)).tolist() if planar else [0.0, 0.0, 0.0],
            "bbox": [*pts[used].min(0).tolist(), *pts[used].max(0).tolist()],
            "surface_type": "Plane" if planar else "Mesh",
            "volumes": [int(v) for v in gmsh.model.getAdjacencies(2, s)[0]],
            "part": names[surf_comp[s]],
            "mesh": {"vertices": np.round(pts[used], 7).ravel().tolist(), "triangles": loc.reshape(-1, 3).ravel().tolist()},
        })
    for s, q in box_faces:
        lo, hi = box
        cs = np.array([[lo[0] if i == 0 else hi[0], lo[1] if j == 0 else hi[1], lo[2] if k == 0 else hi[2]] for i, j, k in q])
        n = np.cross(cs[1] - cs[0], cs[3] - cs[0])
        faces.append({
            "id": int(s), "area": float(np.linalg.norm(n)), "centroid": cs.mean(0).tolist(),
            "normal": (-n / np.linalg.norm(n)).tolist(), "bbox": [*cs.min(0).tolist(), *cs.max(0).tolist()],
            "surface_type": "Plane", "volumes": [int(v) for v in gmsh.model.getAdjacencies(2, s)[0]],
            "mesh": {"vertices": np.round(cs, 7).ravel().tolist(), "triangles": [0, 1, 2, 0, 2, 3]},
            "box_face": True,
        })
    src_bodies = [{"name": names[k], "volume": comp_info[k][0], "centroid": np.asarray(comp_info[k][1]).tolist(),
                   "bbox": bb_of[k].tolist()} for k in range(len(comps))]
    return bodies, faces, domain_info, allbb.tolist(), src_bodies, L
