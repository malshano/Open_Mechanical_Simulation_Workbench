"""gmsh operations, executed in a separate process (gmsh holds global state and
is not thread safe; a crash in the CAD kernel must not take the server down).

Usage:  python -m simwb.gmsh_worker <task> <job.json>

Tasks
  prepare : STEP -> computational domain (domain.brep) + faces.json + bodies.json
  mesh    : domain.brep -> mesh.npz + mesh_stats.json

Progress is reported on stdout as lines:  PROGRESS <0..1> <message>
"""
from __future__ import annotations

import itertools
import json
import math
import os
import sys
import time
from pathlib import Path

import numpy as np

from . import cad_edit, domain, symmetry


def progress(frac: float, msg: str) -> None:
    print(f"PROGRESS {frac:.3f} {msg}", flush=True)


def _gmsh():
    import gmsh

    if not gmsh.isInitialized():
        gmsh.initialize()
    verbose = "SIMWB_GMSH_DEBUG" in os.environ  # (gmsh's own log on the terminal, for diagnosing a mesh)
    gmsh.option.setNumber("General.Terminal", int(verbose))
    gmsh.option.setNumber("General.Verbosity", 5 if verbose else 2)
    return gmsh


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------

def _bbox(gmsh, dim, tag):
    b = gmsh.model.getBoundingBox(dim, tag)
    return [b[0], b[1], b[2], b[3], b[4], b[5]]


def _load_brep(gmsh, path: Path):
    gmsh.clear()
    gmsh.model.add("domain")
    gmsh.model.occ.importShapes(str(path), highestDimOnly=False)
    gmsh.model.occ.synchronize()


def _face_record(gmsh, tag: int) -> dict:
    occ = gmsh.model.occ
    area = occ.getMass(2, tag)
    com = occ.getCenterOfMass(2, tag)
    bb = _bbox(gmsh, 2, tag)
    # normal at the parametric centre of the face
    try:
        lo, hi = gmsh.model.getParametrizationBounds(2, tag)
        uv = [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2]
        n = gmsh.model.getNormal(tag, uv)
        normal = [float(n[0]), float(n[1]), float(n[2])]
    except Exception:
        normal = [0.0, 0.0, 0.0]
    stype = gmsh.model.getType(2, tag)
    up, _ = gmsh.model.getAdjacencies(2, tag)
    return {
        "id": int(tag),
        "area": float(area),
        "centroid": [float(c) for c in com],
        "normal": normal,
        "bbox": bb,
        "surface_type": stype,
        "volumes": [int(v) for v in up],
    }


def _tessellate_faces(gmsh, diag: float, coarse_faces=(), coarse_size=None) -> dict:
    """Coarse surface triangulation of every face, used for display and picking."""
    gmsh.option.setNumber("Mesh.MeshSizeMax", coarse_size or diag / 60)
    # floor: curvature sizing goes to the minimum at a cone apex or sphere pole (diag/2000 took 16 s on a cone)
    gmsh.option.setNumber("Mesh.MeshSizeMin", diag / 400)
    gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 16)
    gmsh.option.setNumber("Mesh.Algorithm", 6)
    if coarse_faces:
        # body faces fine, big far-field faces coarse: size by point
        gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 1)
        gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 1)
        fine_pts = set()
        for _, t in gmsh.model.getEntities(2):
            if t in coarse_faces:
                continue
            for d, pt in gmsh.model.getBoundary([(2, t)], recursive=True):
                fine_pts.add(pt)
        allpts = [t for _, t in gmsh.model.getEntities(0)]
        gmsh.model.mesh.setSize([(0, t) for t in allpts if t not in fine_pts], coarse_size)
        gmsh.model.mesh.setSize([(0, t) for t in fine_pts], diag / 60)
    try:
        gmsh.model.mesh.generate(2)
    except Exception:
        gmsh.option.setNumber("Mesh.Algorithm", 1)
        gmsh.model.mesh.clear()
        gmsh.model.mesh.generate(2)
    tess = {}
    for _, tag in gmsh.model.getEntities(2):
        node_tags, coords, _ = gmsh.model.mesh.getNodes(2, tag, includeBoundary=True)
        if len(node_tags) == 0:
            continue
        coords = np.asarray(coords).reshape(-1, 3)
        idx = {int(t): i for i, t in enumerate(node_tags)}
        et, _, en = gmsh.model.mesh.getElements(2, tag)
        tris = []
        for etype, enodes in zip(et, en):
            name, _, _, nn, _, _ = gmsh.model.mesh.getElementProperties(etype)
            enodes = np.asarray(enodes).reshape(-1, nn)[:, :3]
            tris.append(np.vectorize(idx.get)(enodes))
        if not tris:
            continue
        tris = np.vstack(tris)
        tess[tag] = {
            "vertices": np.round(coords, 7).ravel().tolist(),
            "triangles": tris.astype(int).ravel().tolist(),
        }
    gmsh.model.mesh.clear()
    return tess


# ----------------------------------------------------------------------------
# prepare
# ----------------------------------------------------------------------------

def _half_space(gmsh, plane: dict, size: float) -> int:
    """A big box filling the kept side of a symmetry plane (n . (x - point) >= 0)."""
    return cad_edit.half_space(gmsh, plane["point"], plane["normal"], size)


def _cut_to_kept_side(gmsh, tags: list, planes: list, keep_names: bool = True) -> list:
    """Keep only the part of the volumes `tags` on the kept side of every symmetry plane. Volumes that end up empty
    are dropped; the names of the pieces are those of the volumes they come from. Returns the new volume tags."""
    occ = gmsh.model.occ
    cur = list(tags)
    for plane in planes:
        nxt = []
        for t in cur:
            name = gmsh.model.getEntityName(3, t) if keep_names else ""
            bb = _bbox(gmsh, 3, t)
            reach = math.dist(bb[:3], bb[3:]) + math.dist([(bb[k] + bb[k + 3]) / 2 for k in range(3)], plane["point"])
            tool = _half_space(gmsh, plane, 2.0 * reach + 1e-3)  # (reaches past every point of the volume)
            out, _ = occ.intersect([(3, t)], [(3, tool)], removeObject=True, removeTool=True)
            occ.synchronize()
            for d, tt in out:
                if d == 3:
                    nxt.append(tt)
                    if name:
                        gmsh.model.setEntityName(3, tt, name)
        cur = nxt
    return cur


def _wholly_beyond(bb, plane: dict, tol: float = 1e-9) -> bool:
    """True when the box bb = (xmin, ymin, zmin, xmax, ymax, zmax) lies wholly on the removed side of the plane."""
    n = np.asarray(plane["normal"], float)
    n = n / np.linalg.norm(n)
    pt = np.asarray(plane["point"], float)
    return all(float(np.dot([bb[3 * i[0]], bb[1 + 3 * i[1]], bb[2 + 3 * i[2]]] - pt, n)) < -tol
               for i in itertools.product((0, 1), repeat=3))


def task_prepare(job: dict) -> dict:
    """Import STEP and build the computational domain for the chosen study.

    The file is read as it is first: OCC healing (fix degenerated edges, small edges/faces) breaks clean
    spheres and cone apexes. Healing is only used if the plain import cannot be built or tessellated."""
    if job.get("format") in ("stl", "obj"):
        return _prepare_triangle_mesh(job)
    try:
        return _prepare(job, heal=False)
    except cad_edit.EditError:
        raise  # (a CAD edit that cannot be applied: healing the file would not help)
    except Exception as e:  # noqa: BLE001 - any OCC/gmsh failure: retry with shape healing
        progress(0.05, f"Plain import failed ({e}) - retrying with shape healing")
        try:
            return _prepare(job, heal=True)
        except cad_edit.EditError:
            raise
        except Exception:
            raise e from None


def _prepare(job: dict, heal: bool) -> dict:
    gmsh = _gmsh()
    out = Path(job["out_dir"])
    out.mkdir(parents=True, exist_ok=True)
    study = job["study"]
    dom = job.get("domain", {})

    progress(0.05, "Reading STEP file")
    gmsh.clear()
    gmsh.model.add("import")
    gmsh.option.setString("Geometry.OCCTargetUnit", "M")
    for opt in ("OCCFixDegenerated", "OCCFixSmallEdges", "OCCFixSmallFaces"):
        gmsh.option.setNumber(f"Geometry.{opt}", int(heal))
    occ = gmsh.model.occ
    occ.importShapes(job["step_file"], highestDimOnly=True)
    occ.synchronize()
    if not gmsh.model.getEntities(3) and gmsh.model.getEntities(2):
        # surface-only export: try to sew the shells into solids
        progress(0.1, "No solids found - trying to sew surfaces into solids")
        gmsh.clear()
        gmsh.option.setNumber("Geometry.OCCSewFaces", 1)
        gmsh.option.setNumber("Geometry.OCCMakeSolids", 1)
        occ.importShapes(job["step_file"], highestDimOnly=True)
        occ.synchronize()
        gmsh.option.setNumber("Geometry.OCCSewFaces", 0)
        gmsh.option.setNumber("Geometry.OCCMakeSolids", 0)
    scale = float(job.get("scale", 1.0))
    if scale != 1.0:
        occ.dilate(gmsh.model.getEntities(), 0, 0, 0, scale, scale, scale)
        occ.synchronize()
    rot = job.get("rotation")  # user orientation: 3x3 rotation about the origin (after scaling)
    if rot is not None and not np.allclose(rot, np.eye(3)):
        R = np.asarray(rot, float)
        ents = gmsh.model.getEntities(3) or gmsh.model.getEntities()
        if np.linalg.det(R) > 0:
            # a proper rotation is applied exactly: affineTransform rebuilds every face as a BSpline
            # approximation (a hub cylinder lost a third of its area and could not be meshed any more)
            from scipy.spatial.transform import Rotation
            rv = Rotation.from_matrix(R).as_rotvec()
            ang = float(np.linalg.norm(rv))
            if ang > 1e-12:
                occ.rotate(ents, 0, 0, 0, *(rv / ang), ang)
        else:
            occ.affineTransform(ents, [*R[0], 0.0, *R[1], 0.0, *R[2], 0.0])
        occ.synchronize()
    shift = job.get("translation")  # (a turn about the middle of the parts moves them back to where they were)
    if shift is not None and float(np.linalg.norm(shift)) > 0:
        occ.translate(gmsh.model.getEntities(3) or gmsh.model.getEntities(), *(float(v) for v in shift))
        occ.synchronize()

    edits = job.get("edits") or []
    edit_report: list = []
    if edits:  # the user's / the assistant's CAD edits, replayed on the imported file (in the frame it is shown in)
        progress(0.1, f"Applying {len(edits)} CAD edit(s)")
        edit_report = cad_edit.apply(gmsh, edits, lambda i, n, op: progress(0.1 + 0.02 * i / n, f"CAD edit {i + 1} of {n}: {op}"),
                                    workdir=out)
        occ = gmsh.model.occ   # (the model was rebuilt without the STEP labels, which would rename the bodies again)

    vols = [t for d, t in gmsh.model.getEntities(3)]
    if not vols:
        raise RuntimeError(
            f"{job['step_file']} contains no solid bodies (empty, or surfaces only). Export solids from your CAD tool."
        )

    # symmetry planes: only the kept side of the parts stays. The air volume is sized from the WHOLE model (as
    # without the planes) and cut afterwards, so the half model sees the same far field as the full one.
    planes = [dict(p_) for p_ in (job.get("symmetry") or [])]
    full_bb, n_removed = None, 0
    if planes:
        full_bb = _union_bbox([_bbox(gmsh, 3, t) for t in vols])
        progress(0.12, f"Cutting the parts at {len(planes)} symmetry plane(s)")
        # a flow study: the plane trims the air volume; the parts are cut too only where the plane says so (the
        # part of the model beyond it is deleted), and a part wholly beyond a plane is left out in any case
        part_planes = [p_ for p_ in planes if p_.get("cut_parts")] if study == "external_aero" else planes
        kept = _cut_to_kept_side(gmsh, vols, part_planes)
        if study == "external_aero":
            kept = [t for t in kept if not any(_wholly_beyond(_bbox(gmsh, 3, t), p_) for p_ in planes)]
        n_removed = len(vols) - len(kept)
        vols = kept
        if not vols:
            raise RuntimeError("No part lies on the kept side of the symmetry plane(s): flip a plane or move it "
                               "so that the parts are on the side that stays.")

    progress(0.15, f"Found {len(vols)} solid bodies")
    bodies = []
    for i, v in enumerate(vols):
        name = (gmsh.model.getEntityName(3, v) or "").split("/")[-1].strip()
        # (after CAD edits every body has its final name already: 'Body 2' must stay the body the edits called that)
        if not name or (not edits and name.lower().startswith(("open cascade", "solid", "body", "part", "none"))):
            name = f"Body {i + 1}"
        bodies.append({
            "src_tag": v,
            "name": name,
            "volume": float(occ.getMass(3, v)),
            "centroid": [float(c) for c in occ.getCenterOfMass(3, v)],
            "bbox": _bbox(gmsh, 3, v),
        })
    # make names unique
    seen = {}
    for b in bodies:
        n = b["name"]
        if n in seen:
            seen[n] += 1
            b["name"] = f"{n} {seen[n]}"
        else:
            seen[n] = 1

    allbb = _union_bbox([b["bbox"] for b in bodies])
    if full_bb is not None:
        allbb = full_bb  # (the air volume, the reference length and the span are those of the whole model)
    L = max(allbb[3] - allbb[0], allbb[4] - allbb[1], allbb[5] - allbb[2])

    progress(0.25, "Building computational domain")
    parts_bb = allbb
    final = []  # list of (tag, role, name, src_index)
    domain_info = {}
    if study in SOLID_STUDIES:
        dimtags = [(3, b["src_tag"]) for b in bodies]
        if len(dimtags) > 1:
            outdt, outmap = occ.fragment(dimtags, [])
            occ.synchronize()
            for i, m in enumerate(outmap):
                for d, t in m:
                    final.append((t, "solid", bodies[i]["name"], i))
        else:
            final.append((bodies[0]["src_tag"], "solid", bodies[0]["name"], 0))

    elif study == "external_aero":
        span = max(L, float(job.get("span") or 0.0))  # rotors may extend beyond the airframe
        spec = domain.resolve(dom, study, allbb, L, span, job.get("flow_dir", [1, 0, 0]), job.get("up_axis"))
        msg = domain.check_encloses(spec, allbb, L)
        if msg:
            raise RuntimeError(msg + " Use 'Fit to parts' or enlarge it.")
        lo, hi = np.array(spec["bbox_min"]), np.array(spec["bbox_max"])
        box = domain.build_occ(occ, spec)
        if planes:
            occ.synchronize()
            cut_box = _cut_to_kept_side(gmsh, [box], planes, keep_names=False)
            if len(cut_box) != 1:
                raise RuntimeError("The air volume lies entirely on the removed side of a symmetry plane: flip the "
                                   "plane or enlarge the air volume.")
            box = cut_box[0]
        body_dt = [(3, b["src_tag"]) for b in bodies]
        # all parts cut out of the box at once (the cut handles overlapping parts). Fusing them first lost a
        # propeller sitting on its motor: the fuse of two touching solids dropped 19 of the propeller's 20 faces
        box_copy = occ.copy([(3, box)]) if len(body_dt) > 1 else None
        tools = occ.copy(body_dt) if box_copy else None
        uncut = [p_ for p_ in planes if not p_.get("cut_parts")]
        if tools and uncut:  # (the parts were not cut: the face count to expect is that of the parts inside the box)
            occ.synchronize()
            tools = [(3, t) for t in _cut_to_kept_side(gmsh, [t for _, t in tools], uncut, keep_names=False)]
        cut, _ = occ.cut([(3, box)], body_dt, removeObject=True, removeTool=True)
        occ.synchronize()
        n_faces = sum(len(gmsh.model.getBoundary([c], oriented=False)) for c in cut)
        expect = 6 + sum(len(gmsh.model.getBoundary([t], oriented=False)) for t in tools) if tools else 0
        if box_copy and (len(cut) != 1 or n_faces < 0.9 * expect):  # (the old way: fuse first, then cut)
            occ.remove(cut, recursive=True)
            fused, _ = occ.fuse([tools[0]], tools[1:])
            cut, _ = occ.cut(box_copy, fused, removeObject=True, removeTool=True)
            occ.synchronize()
        else:
            if box_copy:
                occ.remove(box_copy + tools, recursive=True)
                occ.synchronize()
        if len(cut) > 1:
            # parts that touch (a propeller hub sitting on a motor) can leave a sliver "region" of ~zero volume
            # between them: drop such pieces; a real pocket of trapped air is still an error
            tiny = 1e-4 * min(b["volume"] for b in bodies)
            drop = [dt for dt in cut if abs(occ.getMass(3, dt[1])) < tiny]
            if drop and len(drop) < len(cut):
                # (not recursive: its faces are also the hub / motor faces of the air that stays; then only the
                # faces left bounding no volume go)
                occ.remove(drop)
                occ.synchronize()
                loose = [(2, s) for _, s in gmsh.model.getEntities(2) if not len(gmsh.model.getAdjacencies(2, s)[0])]
                if loose:
                    occ.remove(loose)
                    occ.synchronize()
                cut = [dt for dt in cut if dt not in drop]
                progress(0.3, f"Dropped {len(drop)} sliver(s) of air with no volume where parts touch")
        if len(cut) != 1:
            raise RuntimeError(f"Building the flow domain gave {len(cut)} regions; expected 1.")
        final.append((cut[0][1], "fluid", "Air", -1))
        # the air volume ends at a symmetry plane that is normal to an axis (what the 3D view and the checks use)
        for p_ in planes:
            n_ = np.asarray(p_["normal"], float)
            a_ = int(np.argmax(np.abs(n_)))
            if abs(n_[a_]) > 0.9999:
                if n_[a_] > 0:
                    lo[a_] = max(lo[a_], float(p_["point"][a_]))
                else:
                    hi[a_] = min(hi[a_], float(p_["point"][a_]))
        if "min" in spec and "max" in spec:  # (a box)
            spec["min"], spec["max"] = lo.tolist(), hi.tolist()
        spec["bbox_min"], spec["bbox_max"] = lo.tolist(), hi.tolist()
        domain_info = {"box_min": lo.tolist(), "box_max": hi.tolist(), "ref_length": L, "span": span, "spec": spec}

    elif study == "cht_enclosure":
        mode = dom.get("enclosure_mode", "auto_box")
        enc_idx = dom.get("enclosure_body")
        if mode == "body" and enc_idx is None:
            # default: the largest body, but only if it really encloses the others (a heatsink with a chip on it
            # is not an enclosure); otherwise build an air box around the parts
            big = int(np.argmax([b["volume"] for b in bodies]))
            bb = bodies[big]["bbox"]
            inside = len(bodies) > 1 and all(
                all(bb[k] - 1e-9 <= o["bbox"][k] for k in range(3)) and all(o["bbox"][k] <= bb[k] + 1e-9 for k in range(3, 6))
                for i, o in enumerate(bodies) if i != big)
            if inside:
                enc_idx = big
            else:
                mode = "auto_box"
                progress(0.2, "No body encloses the others: an air box is built around the parts")
        if mode == "body":
            enc_idx = int(enc_idx)
            enc_tag = bodies[enc_idx]["src_tag"]
            solids = [i for i in range(len(bodies)) if i != enc_idx]
            if not solids:
                raise RuntimeError("Enclosure mode 'body' needs at least one solid inside the enclosure body.")
            pbb = _union_bbox([bodies[i]["bbox"] for i in solids])
            L = max(pbb[3] - pbb[0], pbb[4] - pbb[1], pbb[5] - pbb[2])
            allbb = pbb
        else:
            spec = domain.resolve(dom, study, allbb, L)
            msg = domain.check_encloses(spec, allbb, L)
            if msg:
                raise RuntimeError(msg + " Use 'Fit to parts' or enlarge it.")
            enc_tag = domain.build_occ(occ, spec)
            if planes:
                occ.synchronize()
                cut_box = _cut_to_kept_side(gmsh, [enc_tag], planes, keep_names=False)
                if len(cut_box) != 1:
                    raise RuntimeError("The air box lies entirely on the removed side of a symmetry plane: flip the "
                                       "plane or enlarge the air box.")
                enc_tag = cut_box[0]
            enc_idx = -1
            solids = list(range(len(bodies)))
        tools = [(3, bodies[i]["src_tag"]) for i in solids]
        outdt, outmap = occ.fragment([(3, enc_tag)], tools)
        occ.synchronize()
        solid_out = set()
        for k, i in enumerate(solids):
            for d, t in outmap[k + 1]:
                final.append((t, "solid", bodies[i]["name"], i))
                solid_out.add(t)
        fluid = [t for d, t in outmap[0] if t not in solid_out]
        if not fluid:
            raise RuntimeError("No air volume left inside the enclosure.")
        # keep the largest fluid piece; others are enclosed pockets (treated as solid-filled -> drop)
        fluid.sort(key=lambda t: -occ.getMass(3, t))
        final.append((fluid[0], "fluid", "Air", enc_idx))
        if len(fluid) > 1:
            occ.remove([(3, t) for t in fluid[1:]], recursive=True)
            occ.synchronize()
        domain_info = {"enclosure_mode": mode, "enclosure_body": enc_idx, "ref_length": L}
        if mode != "body":
            domain_info.update(spec=spec, box_min=spec["bbox_min"], box_max=spec["bbox_max"])
        parts_bb = allbb
    else:
        raise ValueError(f"Unknown study type {study}")

    # remove anything not part of the final domain
    keep = {t for t, *_ in final}
    extra = [(3, t) for d, t in gmsh.model.getEntities(3) if t not in keep]
    if extra:
        occ.remove(extra, recursive=True)
        occ.synchronize()

    records = []
    for t, role, name, src in final:
        records.append({
            "role": role, "name": name, "src": src,
            "volume": float(occ.getMass(3, t)),
            "centroid": [float(c) for c in occ.getCenterOfMass(3, t)],
        })

    progress(0.45, "Writing domain geometry")
    brep = out / "domain.brep"
    gmsh.write(str(brep))

    # reload so that every later step sees identical entity tags
    _load_brep(gmsh, brep)
    occ = gmsh.model.occ
    vol_tags = [t for d, t in gmsh.model.getEntities(3)]
    out_bodies = []
    used = set()
    for t in vol_tags:
        c = np.array(occ.getCenterOfMass(3, t))
        vv = occ.getMass(3, t)
        best, bestd = None, 1e300
        for k, r in enumerate(records):
            if k in used:
                continue
            d = np.linalg.norm(c - r["centroid"]) + abs(vv - r["volume"]) ** (1 / 3)
            if d < bestd:
                best, bestd = k, d
        used.add(best)
        r = records[best]
        out_bodies.append({
            "id": int(t), "name": r["name"], "role": r["role"], "source_index": r["src"],
            "volume": float(vv), "centroid": c.tolist(), "bbox": _bbox(gmsh, 3, t),
        })

    progress(0.55, "Analysing faces")
    faces = [_face_record(gmsh, t) for d, t in gmsh.model.getEntities(2)]
    fluid_ids = {b["id"] for b in out_bodies if b["role"] == "fluid"}
    sym_tol = max(1e-4 * L, 1e-6)
    for f in faces:
        f["interface"] = len(f["volumes"]) > 1
        f["kind"] = "boundary"
        if study == "external_aero":
            bb = f["bbox"]
            lo, hi = domain_info["box_min"], domain_info["box_max"]
            # OCC bounding boxes are padded by the shape tolerance (~1e-7 m, more on unhealed imports): a tolerance
            # relative to the part size only (1e-6 L) misses the box faces, which then get wall refinement
            tol = max(1e-4 * L, 1e-6)
            if domain_info["spec"]["shape"] == "box":
                on_box = any(
                    abs(bb[a] - lo[a]) < tol and abs(bb[a + 3] - lo[a]) < tol
                    or abs(bb[a] - hi[a]) < tol and abs(bb[a + 3] - hi[a]) < tol
                    for a in range(3)
                )
            else:  # curved air volume: its faces reach beyond the parts (which it encloses with room around)
                on_box = any(bb[a] < parts_bb[a] - 10 * tol or bb[a + 3] > parts_bb[a + 3] + 10 * tol for a in range(3))
            f["kind"] = "farfield" if on_box else "body"
            if not on_box:
                f["part"] = _source_part(f, bodies, L)
        elif study == "cht_enclosure":
            if f["interface"]:
                f["kind"] = "interface"
            elif set(f["volumes"]) & fluid_ids:
                f["kind"] = "enclosure"
            else:
                f["kind"] = "solid_outer"  # solid face touching the outside of the enclosure
        elif study in SOLID_STUDIES and f["interface"]:
            f["kind"] = "interface"
        if f["kind"] in ("farfield", "enclosure") and domain_info.get("spec"):
            f["domain_role"] = domain.face_role(f, domain_info["spec"], max(1e-4 * L, 1e-6))
        # a face lying in a symmetry plane (the cut surface of the parts, or of the air volume)
        for k_, pl_ in enumerate(planes):
            if symmetry.on_plane(pl_, f, sym_tol) and not f["interface"]:
                f["symmetry"] = k_
                if study == "external_aero" and f["kind"] == "body" and set(f["volumes"]) & fluid_ids:
                    f["kind"] = "farfield"  # an air face: coarse mesh, no wall treatment
                if f.get("domain_role"):
                    f["domain_role"] = "symmetry"
                break

    progress(0.65, "Tessellating faces for display")
    allbb = _union_bbox([b["bbox"] for b in out_bodies])
    diag = math.dist(allbb[:3], allbb[3:])
    # for far-field domains tessellate relative to the body, the box faces are trivial planes
    if study == "external_aero":
        far = {f["id"] for f in faces if f["kind"] == "farfield"}
        tess = _tessellate_faces(gmsh, L * math.sqrt(3), far, diag / 8)
    else:
        tess = _tessellate_faces(gmsh, diag)
    for f in faces:
        f["mesh"] = tess.get(f["id"], {"vertices": [], "triangles": []})

    (out / "faces.json").write_text(json.dumps(faces))
    (out / "bodies.json").write_text(json.dumps(out_bodies, indent=1))
    try:  # the edges of the model, for find_edges (picking what a fillet or chamfer goes on)
        (out / "edges.json").write_text(json.dumps(cad_edit.edge_records(gmsh, {f["id"]: f["kind"] for f in faces})))
    except Exception:  # noqa: BLE001 - a convenience: never a reason to fail the import
        pass
    info = {
        "bodies": out_bodies,
        "source_bodies": [{k: v for k, v in b.items() if k != "src_tag"} for b in bodies],
        "bbox": allbb,
        "ref_length": L,
        "parts_bbox": parts_bb,
        "domain": domain_info,
        "n_faces": len(faces),
    }
    if edit_report:
        info["edit_report"] = edit_report
    if planes:
        info["symmetry"] = {"planes": [{"id": pl_.get("id"), "faces": [f["id"] for f in faces if f.get("symmetry") == k_]}
                                       for k_, pl_ in enumerate(planes)], "parts_removed": n_removed}
    (out / "domain_info.json").write_text(json.dumps(info, indent=1))
    progress(1.0, "Geometry ready")
    return info


def _prepare_triangle_mesh(job: dict) -> dict:
    """STL / OBJ: faces from sharp edges, volumes from the closed shells (simwb.meshgeom)."""
    from . import meshgeom

    if job.get("domain", {}).get("shape", "box") != "box" and job["study"] in ("external_aero", "cht_enclosure") \
            and (job["study"] == "external_aero" or job.get("domain", {}).get("enclosure_mode") != "body"):
        raise RuntimeError("A curved air volume needs a STEP file; with STL/OBJ use the box.")

    gmsh = _gmsh()
    out = Path(job["out_dir"])
    out.mkdir(parents=True, exist_ok=True)
    study = job["study"]
    progress(0.05, f"Reading {job['format'].upper()} mesh")
    args = {"fmt": job["format"], "scale": float(job.get("scale", 1.0)), "study": study,
            "domain": job.get("domain", {}), "flow_dir": job.get("flow_dir", [1, 0, 0]), "span": job.get("span"),
            "up_axis": job.get("up_axis"), "rotation": job.get("rotation"),
            "translation": job.get("translation")}
    bodies, faces, domain_info, allbb, src, L = meshgeom.build(gmsh, job["step_file"], **args)
    progress(0.6, f"Found {len(src)} closed shells and {len(faces)} faces")
    fluid_ids = {b["id"] for b in bodies if b["role"] == "fluid"}
    for f in faces:
        f["interface"] = len(f["volumes"]) > 1
        if study == "external_aero":
            f["kind"] = "farfield" if f.get("box_face") else "body"
        elif study == "cht_enclosure":
            f["kind"] = "interface" if f["interface"] else ("enclosure" if set(f["volumes"]) & fluid_ids else "solid_outer")
        else:
            f["kind"] = "interface" if f["interface"] else "boundary"
        if f["kind"] in ("farfield", "enclosure") and f.get("box_face") and domain_info.get("spec"):
            f["domain_role"] = domain.face_role(f, domain_info["spec"], max(1e-4 * L, 1e-6))
    (out / "faces.json").write_text(json.dumps(faces))
    (out / "bodies.json").write_text(json.dumps(bodies, indent=1))
    (out / "discrete.json").write_text(json.dumps(args))  # the mesher rebuilds the same model from the source
    info = {"bodies": bodies, "source_bodies": src, "bbox": _union_bbox([b["bbox"] for b in bodies]),
            "ref_length": L, "parts_bbox": allbb, "domain": domain_info, "n_faces": len(faces),
            "source_format": job["format"]}
    (out / "domain_info.json").write_text(json.dumps(info, indent=1))
    progress(1.0, "Geometry ready")
    return info


def _source_part(face, bodies, L):
    """Name of the original STEP body a face of the (fused, cut) flow domain came from: the smallest source body
    whose bounding box contains the face."""
    tol = 1e-3 * L
    fb = np.asarray(face["bbox"])
    best, best_vol = None, None
    for b in bodies:
        bb = np.asarray(b["bbox"])
        if np.all(fb[:3] >= bb[:3] - tol) and np.all(fb[3:] <= bb[3:] + tol):
            vol = float(np.prod(bb[3:] - bb[:3]))
            if best is None or vol < best_vol:
                best, best_vol = b["name"], vol
    if best is None:  # face spans several parts (e.g. a fillet across a joint): nearest body centroid
        c = np.asarray(face["centroid"])
        best = min(bodies, key=lambda b: np.linalg.norm(np.asarray(b["centroid"]) - c))["name"]
    return best


def _union_bbox(bbs):
    a = np.array(bbs)
    return [*a[:, :3].min(0).tolist(), *a[:, 3:].max(0).tolist()]


# ----------------------------------------------------------------------------
# mesh
# ----------------------------------------------------------------------------

ZONE_CELLS = {"quick": 36, "coarse": 48, "medium": 64, "fine": 96, "very_fine": 128}  # facets round a moving zone
# cells across narrow fluid gaps (fin channels) and across a rotor radius in the wake, per preset
SOLID_STUDIES = ("heat_conduction", "vibration_modal", "static_structural")  # solid-only domains (no air)
GAP_CELLS = {"quick": 4, "coarse": 6, "medium": 8, "fine": 10, "very_fine": 12}
WAKE_CELLS = {"quick": 5, "coarse": 5, "medium": 8, "fine": 11, "very_fine": 15}
# cells across the smallest solid feature (arm / boom diameter) for external aerodynamics
FEATURE_CELLS = {"quick": 3, "coarse": 3, "medium": 4, "fine": 6, "very_fine": 8}

# preset -> (cells across the part size L, curvature elements per 2*pi)
def face_extents(f) -> list:
    """Principal extents of a face's display triangulation, smallest first (rotation invariant)."""
    v = np.asarray(f.get("mesh", {}).get("vertices", []), float).reshape(-1, 3)
    if len(v) < 3:
        bb = f["bbox"]
        return sorted([bb[3] - bb[0], bb[4] - bb[1], bb[5] - bb[2]])
    c = v - v.mean(0)
    _, _, axes = np.linalg.svd(c, full_matrices=False)
    proj = c @ axes.T
    return sorted((proj.max(0) - proj.min(0)).tolist())


def sharp_edges(gmsh, face_ids, min_length: float, max_face_angle: float = 140.0, limit: int = 200):
    """The edges (gmsh curves) shared by exactly two of the faces `face_ids` where the faces meet sharply, longest first:
    [(curve tag, face angle in degrees, length)].

    The face angle is the angle between the two faces seen from the edge: 180 = smooth (a fillet seam), 90 = the
    corner of a box, a few degrees = a knife edge such as the leading edge of a wing. At the middle of the edge it is
    measured between the directions that lie in each face's tangent plane, perpendicular to the edge and pointing into
    the face (the side of the face's centre), so it does not depend on which way the CAD normals point and it is right
    for curved faces too. Edges shorter than min_length are left out."""
    ids = set(int(i) for i in face_ids)
    found = []
    for _, tag in gmsh.model.getEntities(1):
        up, _down = gmsh.model.getAdjacencies(1, tag)
        surfs = sorted({int(s) for s in up if int(s) in ids})
        if len(surfs) != 2:
            continue
        length = float(gmsh.model.occ.getMass(1, tag))
        if length < min_length:
            continue
        lo, hi = gmsh.model.getParametrizationBounds(1, tag)
        t = [(lo[0] + hi[0]) / 2]
        p = np.array(gmsh.model.getValue(1, tag, t), float)
        e = np.array(gmsh.model.getDerivative(1, tag, t), float)
        if np.linalg.norm(e) < 1e-30:
            continue
        e /= np.linalg.norm(e)
        dirs = []
        for s in surfs:
            uv = gmsh.model.getParametrization(2, s, [float(x) for x in p])
            n = np.array(gmsh.model.getNormal(s, list(uv)), float)[:3]
            d = np.cross(n, e)                                   # in the tangent plane, perpendicular to the edge
            if np.linalg.norm(d) < 1e-12:
                continue
            d /= np.linalg.norm(d)
            if d @ (np.array(gmsh.model.occ.getCenterOfMass(2, s), float) - p) < 0:
                d = -d                                           # ... pointing to the side the face lies on
            dirs.append(d)
        if len(dirs) != 2:
            continue
        angle = math.degrees(math.acos(float(np.clip(dirs[0] @ dirs[1], -1.0, 1.0))))
        if angle < max_face_angle:
            found.append((int(tag), angle, length))
    found.sort(key=lambda x: -x[2])
    return found[:limit]


def face_width(f) -> float:
    """Width of a face: middle principal extent (for a cylinder this is its diameter, for a fin top its
    thickness)."""
    return face_extents(f)[1]


def find_fluid_gaps(gmsh, faces, fluid_ids, L, max_pairs=4000):
    """Narrow fluid gaps between parallel planar walls (fin channels, slots).

    Returns [(gap_width, face_a, face_b)] for wall pairs that face each other across fluid."""
    cand = [f for f in faces if "Plane" in f.get("surface_type", "") and set(f["volumes"]) & fluid_ids
            and f.get("kind") in ("interface", "body", "solid_outer", "enclosure")]
    if len(cand) < 2:
        return []
    n = np.array([f["normal"] for f in cand], float)
    c = np.array([f["centroid"] for f in cand], float)
    bb = np.array([f["bbox"] for f in cand], float)
    dot = n @ n.T
    par = np.abs(dot) > 0.995
    d = np.abs(np.einsum("ijk,ik->ij", c[None, :, :] - c[:, None, :], n))
    ok = par & (d > 1e-6) & (d < L / 2)
    # lateral overlap of the bounding boxes (in the axes other than the normal)
    ax = np.argmax(np.abs(n), axis=1)
    lo, hi = bb[:, :3], bb[:, 3:]
    ov = np.minimum(hi[:, None, :], hi[None, :, :]) - np.maximum(lo[:, None, :], lo[None, :, :])
    for k in range(3):
        lat = (ax[:, None] != k)
        ok &= ~lat | (ov[:, :, k] > 1e-6)
    ii, jj = np.where(np.triu(ok, 1))
    order = np.argsort(d[ii, jj])[:max_pairs]
    out = []
    for i, j in zip(ii[order], jj[order]):
        # point in the middle of the overlap region, half way between the two planes
        mid_lat = (np.maximum(lo[i], lo[j]) + np.minimum(hi[i], hi[j])) / 2
        a = ax[i]
        mid = mid_lat.copy()
        mid[a] = (c[i][a] + c[j][a]) / 2
        if cand[i].get("kind") == "enclosure" and cand[j].get("kind") == "enclosure":
            continue  # the duct itself is not a narrow gap
        inside = any(gmsh.model.isInside(3, v, mid.tolist()) for v in fluid_ids)
        if inside:
            box_lo = np.maximum(lo[i], lo[j])
            box_hi = np.minimum(hi[i], hi[j])
            box_lo[a] = min(c[i][a], c[j][a])
            box_hi[a] = max(c[i][a], c[j][a])
            out.append((float(d[i, j]), cand[i]["id"], cand[j]["id"], box_lo.tolist(), box_hi.tolist()))
    # keep only the nearest partner of each face (the channel it borders), drops "across two fins" pairs
    best = {}
    for g in out:
        for fid in (g[1], g[2]):
            if fid not in best or g[0] < best[fid][0]:
                best[fid] = g
    return sorted({(g[1], g[2]): g for g in best.values()}.values())


def cylinder_size_expr(start, end, radius, v_in, v_out, thickness, inner=0.0) -> str:
    """Size-field expression (gmsh MathEval) for a capped cylinder from start to end: v_in inside, growing
    linearly to v_out over `thickness` outside it (like the Box field of the refinement boxes). With an inner
    radius it is a ring (tube): the cells grow towards the axis too - fine cells along the path of the tip
    vortices without filling the whole wake."""
    s = np.asarray(start, float)
    a = np.asarray(end, float) - s
    length = float(np.linalg.norm(a))
    u = a / length
    p = [f"(x-({s[0]:.9g}))", f"(y-({s[1]:.9g}))", f"(z-({s[2]:.9g}))"]
    t = "(" + "+".join(f"{p[k]}*({u[k]:.9g})" for k in range(3)) + ")"
    rho = "sqrt(" + "+".join(f"({p[k]}-{t}*({u[k]:.9g}))^2" for k in range(3)) + ")"
    dr = (f"max(0,max({rho}-({radius:.9g}),({inner:.9g})-{rho}))" if inner > 0
          else f"max(0,{rho}-({radius:.9g}))")
    da = f"max(0,max(-{t},{t}-({length:.9g})))"
    d = f"sqrt({dr}^2+{da}^2)"
    return f"({v_in:.9g})+({v_out - v_in:.9g})*min(1,{d}/({thickness:.9g}))"


def merge_small_faces(gmsh, faces, max_area, skip=(), min_cluster=3, only=None):
    """Virtual topology (as in Ansys): faces smaller than max_area (embossed text, logos, slivers of a converted
    model) are meshed together with the face they sit on, as one gmsh compound surface. The mesher then ignores
    the edges between them - the text is smoothed over - instead of following hundreds of tiny faces whose facets
    cross ("PLC Error: a segment and a facet intersect") or give near-flat cells. Each cluster of touching small
    faces joins the one large face it shares the most edge length with, so real edges (a trailing edge between two
    large faces) stay. Clusters of fewer than min_cluster faces (a lone sliver) are left to the mesher. `only`:
    these faces are the small ones (instead of the area test). Returns the compound groups [[host, small...], ...]."""
    walls = {f["id"] for f in faces if f["kind"] not in ("farfield", "enclosure")} - set(skip)
    area = {f["id"]: f["area"] for f in faces}
    small = ({int(i) for i in only} & walls) if only is not None else {i for i in walls if area.get(i, 1e30) < max_area}
    if not small:
        return []
    edges = {f: [abs(int(e)) for _, e in gmsh.model.getBoundary([(2, f)], oriented=False)] for f in small}
    par = {f: f for f in small}

    def find(a):
        while par[a] != a:
            par[a] = par[par[a]]
            a = par[a]
        return a

    host_len = {}  # cluster member -> {large face: shared edge length}
    for f, es in edges.items():
        for e in es:
            for g in gmsh.model.getAdjacencies(1, e)[0]:
                g = int(g)
                if g == f:
                    continue
                if g in small:
                    par[find(g)] = find(f)
                elif g in walls:
                    d = host_len.setdefault(f, {})
                    d[g] = d.get(g, 0.0) + gmsh.model.occ.getMass(1, e)
    clusters = {}
    for f in small:
        clusters.setdefault(find(f), []).append(f)
    groups = {}
    for members in clusters.values():
        if len(members) < min_cluster:  # (a lone sliver: the mesher copes, and its compound can hang MeshAdapt)
            continue
        cand = {}
        for f in members:
            for g, ln in host_len.get(f, {}).items():
                cand[g] = cand.get(g, 0.0) + ln
        if cand:  # (a cluster touching no large face - a loose bit - is left as it is)
            groups.setdefault(max(cand, key=cand.get), []).extend(members)
    out = [[h, *sorted(m)] for h, m in groups.items()]
    for g in out:
        gmsh.model.mesh.setCompound(2, g)
    return out


PRESETS = {"quick": (8, 6), "coarse": (10, 8), "medium": (16, 12), "fine": (26, 16), "very_fine": (40, 24)}
# solid FEM studies are cheap per element but straight-sided facets on curved faces bias them low (an inscribed
# 12-gon shaft is ~2 % too soft): resolve curvature more finely there
SOLID_CURVATURE = {"quick": 12, "coarse": 16, "medium": 24, "fine": 32, "very_fine": 40}


def task_mesh(job: dict) -> dict:
    gmsh = _gmsh()
    out = Path(job["out_dir"])
    out.mkdir(parents=True, exist_ok=True)
    study = job["study"]
    ms = job.get("mesh", {})
    t0 = time.time()

    progress(0.02, "Loading domain")
    disc = Path(job["brep"]).with_name("discrete.json")
    if disc.exists():  # STL / OBJ source: rebuild the same model (same entity tags) from the triangles
        from . import meshgeom

        meshgeom.build(gmsh, Path(job["brep"]).with_name("source.step"), **json.loads(disc.read_text()))
    else:
        _load_brep(gmsh, Path(job["brep"]))
    occ = gmsh.model.occ
    bodies = json.loads(Path(job["bodies_json"]).read_text())
    faces = json.loads(Path(job["faces_json"]).read_text())
    info = json.loads(Path(job["domain_info"]).read_text())
    L = info["ref_length"]  # size of the parts (excludes far-field / auto enclosure padding)

    # moving zones: their cylinder surface is embedded in the air volume, so the mesh follows it exactly
    zone_surfs = []  # (zone index, [surface tags])
    for zi, z in enumerate(job.get("moving_zones", [])):
        c = np.asarray(z["center"], float)
        a = np.asarray(z["axis"], float) * float(z["length"])
        cyl = occ.addCylinder(*(c - a / 2), *a, float(z["radius"]))
        occ.synchronize()
        surfs = [int(s) for _, s in gmsh.model.getBoundary([(3, cyl)], oriented=False)]
        occ.remove([(3, cyl)])
        occ.synchronize()
        host = None
        for _, vt in gmsh.model.getEntities(3):
            if any(b["id"] == vt and b["role"] == "fluid" for b in bodies):
                bb = gmsh.model.getBoundingBox(3, vt)
                if all(bb[k] <= c[k] <= bb[k + 3] for k in range(3)):
                    host = vt
        if host is None:
            raise RuntimeError(f"Moving zone {z['name']} is not inside the air volume")
        gmsh.model.mesh.embed(2, surfs, 3, host)
        zone_surfs.append((zi, surfs))

    n_per, n_curv = PRESETS.get(ms.get("preset", "medium"), PRESETS["medium"])
    if study in SOLID_STUDIES:
        n_curv = SOLID_CURVATURE.get(ms.get("preset", "medium"), n_curv)
    h_body = float(ms.get("size_max") or L / n_per)

    # smallest geometric feature (fin thickness, gap) from face extents
    feat = []
    for f in faces:
        if f["kind"] in ("farfield", "enclosure"):
            continue
        ext = face_width(f)
        if ext > L * 2e-3:
            feat.append(ext)
    feature = min(feat) if feat else L / 20
    # near-wall size: resolve thin features (fins) but never finer than L/(3n)
    thin_faces = []  # (face ids, size): local refinement of thin parts (aero)
    if study == "external_aero":
        # the near-wall size follows the body size; thin parts (a tail, an arm) are refined locally, not on
        # every wall of the body (a thin tail used to set 2.7 mm on all walls of a 1.2 m airframe)
        n_feat = FEATURE_CELLS.get(ms.get("preset", "medium"), 4)
        h_wall = float(ms.get("size_min") or min(max(feature / n_feat, L / (4 * n_per)), h_body / 2))
        if not ms.get("size_min"):
            h_thin_min = L / (12 * n_per)  # coarse 1.2 m UAV: 10 mm (L/25n gave 0.9M cells)
            by_size = {}
            for f in faces:
                if f["kind"] in ("farfield", "enclosure"):
                    continue
                w = face_width(f)
                if L * 2e-3 < w and w / n_feat < 0.9 * h_wall:
                    sz = max(w / n_feat, h_thin_min)
                    by_size.setdefault(round(sz, 6), []).append(f["id"])
            thin_faces = sorted(by_size.items())
    else:
        # resolve thin walls/plates (feature/2) but never finer than L/(8n) to bound the element count
        floor = L / ((8 if study in SOLID_STUDIES else 3) * n_per)  # CFD: cells are expensive (prism layers TODO)
        h_wall = float(ms.get("size_min") or min(max(feature / 2.0, floor), h_body / 2))
    growth = float(ms.get("growth", 1.25))

    # small faces (a blade-tip face, a tiny chamfer): a face smaller than the cells round it is crossed by flat
    # (sliver) cells - 0.3 mm tip faces in 0.8 mm cells gave a sliding run a Courant number of 80 at the first
    # step and it diverged. They get cells about as wide as the face, growing back out quickly (as Ansys sizes
    # small features), down to L/5000.
    small_faces, subcell = [], []
    if study not in SOLID_STUDIES and not ms.get("size_min"):
        local = {}
        for sz, ids in thin_faces:
            for i in ids:
                local[i] = min(local.get(i, 1e30), sz)
        for r in ms.get("refinements", []):
            for i in r["faces"]:
                local[int(i)] = min(local.get(int(i), 1e30), float(r["size"]))
        by_size = {}
        for f in faces:
            if f["kind"] in ("farfield", "enclosure"):
                continue
            _, w, long = face_extents(f)
            h = min(local.get(f["id"], h_wall), h_wall)
            # compact faces only: the cells added round a face grow outwards from it, about 2700 x length/width
            # of them - a 0.15 x 20 mm strip on a frame would add 350k (and the meshing filled the memory)
            if long < 2 * h:  # the face fits in about one cell: meshed with its neighbour (below)
                subcell.append(f["id"])
            elif w < 0.5 * h and long < 3 * h and long < 8 * max(w, L * 2e-4):
                by_size.setdefault(round(max(w, L * 2e-4), 7), []).append(f["id"])
        small_faces = sorted(by_size.items())
        thin_faces = sorted(thin_faces + small_faces)

    # narrow fluid gaps (fin channels) get their own refinement: >= GAP_CELLS across
    fluid_ids = {b["id"] for b in bodies if b["role"] == "fluid"}
    gaps = (find_fluid_gaps(gmsh, faces, fluid_ids, L)
            if fluid_ids and study not in SOLID_STUDIES and study != "external_aero" else [])  # open air: no channels
    n_gap = GAP_CELLS.get(ms.get("preset", "medium"), 6)
    gap_sizes = {}  # face -> size
    gap_boxes = []
    for w, fa, fb, blo, bhi in gaps:
        sz = w / n_gap
        if sz < h_wall:
            for fid in (fa, fb):
                gap_sizes[fid] = min(gap_sizes.get(fid, 1e9), sz)
            gap_boxes.append((sz, blo, bhi))
    min_gap = min((g[0] for g in gaps), default=None)
    # channels between two part walls (fin channels; not the part-to-duct-wall bypass) for the flow regime
    kind_of = {f["id"]: f.get("kind") for f in faces}
    channels = sorted({(round(w, 7), tuple(round(b - a, 7) for a, b in zip(blo, bhi)),
                        tuple(round(a, 7) for a in blo), tuple(round(b, 7) for b in bhi))
                       for w, fa, fb, blo, bhi in gaps
                       if kind_of.get(fa) != "enclosure" and kind_of.get(fb) != "enclosure"})[:50]
    if gaps:
        progress(0.06, f"Found {len(gaps)} narrow air gaps (smallest {min_gap * 1e3:.2f} mm)")

    progress(0.08, f"Sizing: near-wall {h_wall * 1e3:.2f} mm, body {h_body * 1e3:.2f} mm")
    gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 1)
    gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 0)
    gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", int(ms.get("curvature_elements", n_curv)))
    smallest = ([h_wall * 0.5] + [w / GAP_CELLS.get(ms.get("preset", "medium"), 6) * 0.9 for w, *_ in gaps]
                + [t[0] * 0.5 for t in thin_faces if t not in small_faces])
    # (the small-face sizes do not lower the global minimum: that freed the curvature sizing along every blade
    # edge - 1.5 million cells under 0.5 mm on a propeller - a small face gets at most the minimum set by the rest)
    for rz in job.get("rotors", []):
        smallest.append(float(rz.get("thickness", 0.1 * rz["radius"])) / 2 * 0.9)
    for z in job.get("moving_zones", []):
        smallest.append(float(z["radius"]) / 40)
        if z.get("cell_size"):
            smallest.append(float(z["cell_size"]) * 0.5)
    for r in ms.get("regions", []):
        smallest.append(float(r["size"]) * 0.5)
    for r in ms.get("refinements", []):  # (part / face sizing: without this the global minimum clamped it)
        smallest.append(float(r["size"]) * 0.5)
    gmsh.option.setNumber("Mesh.MeshSizeMin", min(smallest))
    # defeaturing (opt-in): clusters of small faces (text, logos) meshed together with the face they sit on.
    # (Not on by default: with fine sizes on the host face gmsh's reparametrisation of the merged face can fall
    # back to MeshAdapt and take hours - a propeller with 24 embossed letters at 0.8 mm did not finish in 45 min)
    dfs = float(ms.get("defeature") or 0)
    merged = []
    if dfs > 0 and study not in SOLID_STUDIES:
        zone_ids = {s_ for _, ss in zone_surfs for s_ in ss}
        merged = merge_small_faces(gmsh, faces, dfs ** 2, skip=zone_ids)
        if merged:
            progress(0.1, f"Defeaturing: {sum(len(g) - 1 for g in merged)} small faces (under {dfs * 1e3:.2g} mm "
                          f"across) merged into {len(merged)} faces - the mesh smooths over them")
    # faces about one cell small (a 0.4 x 1.5 mm blade-tip face in 0.8 mm cells) are always meshed with their
    # neighbour: on their own they are crossed by flat cells - on a blade tip those limited a sliding run to a
    # Courant number of 80 at the first step and it diverged
    done = {i for g in merged for i in g}
    tiny = [i for i in subcell if i not in done]
    if tiny and study not in SOLID_STUDIES:
        zone_ids = {s_ for _, ss in zone_surfs for s_ in ss}
        auto = merge_small_faces(gmsh, faces, 0, skip=zone_ids | done, min_cluster=1, only=tiny)
        if auto:
            merged = merged + auto
            progress(0.1, f"{sum(len(g) - 1 for g in auto)} faces of about one cell meshed with the face next "
                          "to them")
    gmsh.option.setNumber("Mesh.Algorithm", 6)  # Frontal-Delaunay 2D
    # faces with a degenerate edge (cone apex, sphere pole) get the plain Delaunay surface mesher: frontal meshing
    # with size fields leaves a duplicated facet there that stops the volume mesher
    for _, c in gmsh.model.getEntities(1) if not disc.exists() else []:
        if occ.getMass(1, c) < 1e-9 * L:
            for sf in gmsh.model.getAdjacencies(1, c)[0]:
                gmsh.model.mesh.setAlgorithm(2, int(sf), 5)
    gmsh.option.setNumber("Mesh.Algorithm3D", int(ms.get("algorithm3d", 10)))  # HXT
    gmsh.option.setNumber("Mesh.Optimize", 1)
    gmsh.option.setNumber("Mesh.OptimizeNetgen", 1)
    gmsh.option.setNumber("General.NumThreads", 0)

    fields = gmsh.model.mesh.field
    wall_faces = [f["id"] for f in faces if f["kind"] not in ("farfield", "enclosure")]
    if study in SOLID_STUDIES:
        wall_faces = [f["id"] for f in faces]
    size_far = h_body
    if study == "external_aero":
        bmin, bmax = info["domain"]["box_min"], info["domain"]["box_max"]
        size_far = max(bmax[i] - bmin[i] for i in range(3)) / 12
    elif study == "cht_enclosure":
        size_far = h_body

    dist = fields.add("Distance")
    fields.setNumbers(dist, "SurfacesList", wall_faces)
    fields.setNumber(dist, "Sampling", 40)
    thr = fields.add("Threshold")
    fields.setNumber(thr, "InField", dist)
    fields.setNumber(thr, "SizeMin", h_wall)
    fields.setNumber(thr, "SizeMax", size_far)
    fields.setNumber(thr, "DistMin", h_wall)
    # geometric growth from h_wall to size_far
    n_layers = math.log(max(size_far / h_wall, 1.0001)) / math.log(growth)
    fields.setNumber(thr, "DistMax", h_wall * (growth ** n_layers - 1) / (growth - 1))
    use = [thr]
    in_compound = {i for g in merged for i in g}  # (defeatured faces are smoothed over, not resolved)
    for sz, ids in thin_faces:
        ids = [i for i in ids if i not in in_compound]
        if not ids:
            continue
        fd = fields.add("Distance")
        fields.setNumbers(fd, "SurfacesList", ids)
        ft = fields.add("Threshold")
        fields.setNumber(ft, "InField", fd)
        fields.setNumber(ft, "SizeMin", sz)
        fields.setNumber(ft, "SizeMax", size_far)
        fields.setNumber(ft, "DistMin", sz)
        nl = math.log(max(size_far / sz, 1.0001)) / math.log(growth)
        fields.setNumber(ft, "DistMax", sz * (growth ** nl - 1) / (growth - 1))
        use.append(ft)
    thin_only = [t for t in thin_faces if t not in small_faces]
    if thin_only:
        progress(0.09, f"Thin parts refined locally: {sum(len(i) for _, i in thin_only)} faces down to "
                       f"{thin_only[0][0] * 1e3:.2f} mm")
    if small_faces:
        progress(0.09, f"Small faces sized to their width: {sum(len(i) for _, i in small_faces)} faces down to "
                       f"{small_faces[0][0] * 1e3:.2f} mm")

    if study not in SOLID_STUDIES and study != "external_aero":
        # in an enclosure, keep the bulk air at h_body
        cst = fields.add("MathEval")
        fields.setString(cst, "F", f"{h_body}")
        use.append(cst)
    if study == "external_aero":
        # wake / body box refinement
        n_wake = WAKE_CELLS.get(ms.get("preset", "medium"), 8)
        for rz in job.get("rotors", []):
            c = np.asarray(rz["center"], float)
            a = np.asarray(rz["axis"], float)
            a /= np.linalg.norm(a)
            R = float(rz["radius"])
            th = float(rz.get("thickness", 0.1 * R))

            def cylinder(center, axis_half, radius, size):
                cyl = fields.add("Cylinder")
                for k, nm in enumerate("XYZ"):
                    fields.setNumber(cyl, f"{nm}Center", float(center[k]))
                    fields.setNumber(cyl, f"{nm}Axis", float(axis_half[k]))
                fields.setNumber(cyl, "Radius", radius)
                fields.setNumber(cyl, "VIn", size)
                fields.setNumber(cyl, "VOut", size_far)
                use.append(cyl)

            # the disk itself: at least 2 cells through its thickness
            h_disk = min(h_body, th / 2, R / n_wake)
            cylinder(c, a * th, R * 1.1, h_disk)
            # the slipstream: ~3 diameters downstream along the local flow direction
            w = np.asarray(rz.get("wake_dir") or (-a).tolist(), float)
            w /= np.linalg.norm(w)
            Lw = 6.0 * R
            cylinder(c + w * Lw / 2, w * Lw / 2, R * 1.15, R / n_wake)
            # grade from the disk cells to the wake cells over the first 0.5 R (no sudden size jump below the disk)
            h_mid = math.sqrt(h_disk * R / n_wake)
            if h_mid < 0.9 * R / n_wake:
                cylinder(c + w * 0.25 * R, w * 0.25 * R, R * 1.12, h_mid)
        src = info["parts_bbox"]
        box = fields.add("Box")
        pad = 0.5 * L
        fields.setNumber(box, "XMin", src[0] - pad); fields.setNumber(box, "XMax", src[3] + pad)
        fields.setNumber(box, "YMin", src[1] - pad); fields.setNumber(box, "YMax", src[4] + pad)
        fields.setNumber(box, "ZMin", src[2] - pad); fields.setNumber(box, "ZMax", src[5] + pad)
        fields.setNumber(box, "VIn", h_body * 1.5)
        fields.setNumber(box, "VOut", size_far)
        fields.setNumber(box, "Thickness", L)
        use.append(box)

    # gap refinement only inside each channel (box between the two walls)
    for sz, blo, bhi in gap_boxes:
        bx = fields.add("Box")
        for k, nm in enumerate("XYZ"):
            fields.setNumber(bx, f"{nm}Min", blo[k])
            fields.setNumber(bx, f"{nm}Max", bhi[k])
        fields.setNumber(bx, "VIn", sz)
        fields.setNumber(bx, "VOut", size_far)
        fields.setNumber(bx, "Thickness", sz * 2)
        use.append(bx)

    # the zone boundary (sliding interface): about 30 cells round the circumference, similar sizes on both sides
    for zi, surfs in zone_surfs:
        z = job["moving_zones"][zi]
        hz = min(h_body, 2 * math.pi * float(z["radius"]) / ZONE_CELLS.get(ms.get("preset", "medium"), 36),
                 float(z.get("cell_size") or 1e9))  # (facets of a rotated faceted cylinder miss the static ones by
        # up to the sagitta h^2/8R: 0.3 mm with 15 mm facets broke a sliding run after half a facet of rotation)
        fd = fields.add("Distance")
        fields.setNumbers(fd, "SurfacesList", surfs)
        ft = fields.add("Threshold")
        fields.setNumber(ft, "InField", fd)
        fields.setNumber(ft, "SizeMin", hz)
        fields.setNumber(ft, "SizeMax", size_far)
        fields.setNumber(ft, "DistMin", hz)
        fields.setNumber(ft, "DistMax", hz * 12)
        use.append(ft)

    # user cell size inside a moving zone (e.g. finer round a propeller): a cylinder of that size
    for z in job.get("moving_zones", []):
        if not z.get("cell_size"):
            continue
        ax = np.asarray(z["axis"], float) * float(z["length"]) / 2  # (gmsh: the axis vector is the half length)
        fc = fields.add("Cylinder")
        for k, v in zip(("XCenter", "YCenter", "ZCenter"), z["center"]):
            fields.setNumber(fc, k, float(v))
        for k, v in zip(("XAxis", "YAxis", "ZAxis"), ax):
            fields.setNumber(fc, k, float(v))
        fields.setNumber(fc, "Radius", float(z["radius"]))
        fields.setNumber(fc, "VIn", float(z["cell_size"]))
        fields.setNumber(fc, "VOut", size_far)
        use.append(fc)
    # refinement boxes (the region's cell size inside, growing smoothly outside)
    for r in ms.get("regions", []):
        if r.get("shape") == "cylinder":
            expr = cylinder_size_expr(r["start"], r["end"], r["radius"], r["size"], size_far, r["size"] * 8,
                                      r.get("inner_radius", 0.0))
            fb = fields.add("MathEval")
            fields.setString(fb, "F", expr)
            use.append(fb)
            continue
        fb = fields.add("Box")
        for k, v in zip(("XMin", "YMin", "ZMin"), r["min"]):
            fields.setNumber(fb, k, float(v))
        for k, v in zip(("XMax", "YMax", "ZMax"), r["max"]):
            fields.setNumber(fb, k, float(v))
        fields.setNumber(fb, "VIn", float(r["size"]))
        fields.setNumber(fb, "VOut", size_far)
        fields.setNumber(fb, "Thickness", float(r["size"]) * 8)
        use.append(fb)

    for r in ms.get("refinements", []):
        fl = fields.add("Distance")
        fields.setNumbers(fl, "SurfacesList", [int(x) for x in r["faces"]])
        ft = fields.add("Threshold")
        fields.setNumber(ft, "InField", fl)
        fields.setNumber(ft, "SizeMin", float(r["size"]))
        fields.setNumber(ft, "SizeMax", size_far)
        fields.setNumber(ft, "DistMin", float(r["size"]))
        fields.setNumber(ft, "DistMax", float(r["size"]) * 10)
        use.append(ft)

    # sharp edges (leading and trailing edges, corners): cells of wall size / factor along them, back to the wall size
    # within 12 edge cells. Flow quantities change fastest there (a laminar plate's first 5 % of length carries about
    # 22 % of its friction), and it costs few cells because only a thin tube round each edge is refined.
    edge_info = None
    if study == "external_aero" and ms.get("edge_refine") and not ms.get("size_min"):
        factor = float(ms["edge_refine"].get("factor", 6.0))
        h_edge = max(h_wall / factor, L / 3000.0)
        if h_edge < 0.9 * h_wall:
            edges = sharp_edges(gmsh, wall_faces, max(0.03 * L, 8 * h_edge))
            if edges:
                fe = fields.add("Distance")
                fields.setNumbers(fe, "CurvesList", [e[0] for e in edges])
                fields.setNumber(fe, "Sampling", int(min(2000, max(50, 2 * max(e[2] for e in edges) / h_edge))))
                fte = fields.add("Threshold")
                fields.setNumber(fte, "InField", fe)
                fields.setNumber(fte, "SizeMin", h_edge)
                fields.setNumber(fte, "SizeMax", size_far)
                fields.setNumber(fte, "DistMin", h_edge)
                fields.setNumber(fte, "DistMax", h_edge * 12)
                use.append(fte)
                edge_info = {"edges": len(edges), "size": h_edge, "factor": factor,
                             "min_face_angle": min(e[1] for e in edges), "total_length": sum(e[2] for e in edges)}
                progress(0.09, f"Sharp edges refined: {len(edges)} edge(s), {edge_info['total_length']:.3g} m long, "
                               f"cells of {h_edge * 1e3:.2f} mm")

    mn = fields.add("Min")
    fields.setNumbers(mn, "FieldsList", use)
    fields.setAsBackgroundMesh(mn)
    gmsh.option.setNumber("Mesh.MeshSizeMax", size_far)


    def surfaces():  # (compound faces are reparametrised with MUMPS, which is not thread safe: "PB allocation in
        # DMUMPS_LOAD_INIT" and a heap crash when two compounds were meshed at once - one thread for the surfaces)
        if merged:
            gmsh.option.setNumber("General.NumThreads", 1)
        gmsh.model.mesh.generate(2)
        gmsh.option.setNumber("General.NumThreads", 0)

    progress(0.15, "Meshing surfaces")
    surfaces()
    progress(0.4, "Meshing volumes (this can take a while)")
    def n_tets():
        return sum(len(t) for t in gmsh.model.mesh.getElements(3)[1])

    # cone apexes and sphere poles can leave a degenerate surface triangle that stops the volume mesher
    # ("duplicated facets"): retry with other surface/volume algorithm pairs before giving up
    attempts = [(6, int(ms.get("algorithm3d", 10))), (6, 1), (5, 10), (1, 10)]
    if zone_surfs or merged:  # embedded surfaces (moving zones), compound faces: Delaunay (HXT fails on them)
        attempts = [(6, 1), (5, 1), (1, 1)]
        gmsh.option.setNumber("Mesh.Algorithm3D", 1)
        gmsh.option.setNumber("Mesh.OptimizeNetgen", 0)  # (Netgen's optimiser crashes on embedded surfaces)
    err = first_err = None
    repairs, balls = 0, []
    k = 0
    while k < len(attempts):
        a2, a3 = attempts[k]
        try:
            gmsh.model.mesh.generate(3)
            if n_tets():
                break
            err = "no volume elements"
        except Exception as e:  # noqa: BLE001
            err = str(e).splitlines()[0] if str(e) else type(e).__name__
            if "intersect" in err:
                # surface mesh repair (as Ansys does): where triangles of two faces cross - a sharp corner, a knife
                # edge, two faces nearly touching - refine a small ball round each crossing and mesh again
                cross, n = _crossings(gmsh)
                if first_err is None:
                    first_err = err + _crossing_text(cross, n)
                if cross and repairs < 3:
                    repairs += 1
                    for x, h in cross[:200]:
                        ball = fields.add("Ball")
                        for c_, nm in zip(x, "XYZ"):
                            fields.setNumber(ball, f"{nm}Center", float(c_))
                        fields.setNumber(ball, "Radius", 3 * h)
                        fields.setNumber(ball, "Thickness", 3 * h)
                        fields.setNumber(ball, "VIn", h / 4)
                        fields.setNumber(ball, "VOut", size_far)
                        balls.append(ball)
                    mn = fields.add("Min")
                    fields.setNumbers(mn, "FieldsList", use + balls)
                    fields.setAsBackgroundMesh(mn)
                    gmsh.option.setNumber("Mesh.MeshSizeMin", min(gmsh.option.getNumber("Mesh.MeshSizeMin"),
                                                                  min(h for _, h in cross) / 8))
                    progress(0.42, f"Surface mesh crossed itself in {n} place(s): refining there and meshing "
                                   f"again (repair {repairs}/3)")
                    gmsh.model.mesh.clear()
                    surfaces()
                    continue
        k += 1
        if k < len(attempts):
            a2, a3 = attempts[k]
            progress(0.45, f"Volume meshing failed ({err}); retrying with other algorithms ({a2}/{a3})")
            gmsh.option.setNumber("Mesh.Algorithm", a2)
            gmsh.option.setNumber("Mesh.Algorithm3D", a3)
            gmsh.model.mesh.clear()
            surfaces()
    else:
        raise RuntimeError(f"Volume meshing failed: {first_err or err}. Try another mesh preset or a finer size "
                           "there (Part sizing), or check the geometry for very small faces or gaps.")
    if repairs:
        progress(0.78, f"Surface mesh repaired in {repairs} round(s) ({len(balls)} local refinements)")
    progress(0.8, "Optimising mesh")
    try:
        gmsh.model.mesh.optimize("" if zone_surfs or merged else "Netgen")
    except Exception:
        pass
    if zone_surfs:
        # (Netgen crashes with embedded surfaces) a sliver on the zone boundary moves with the zone: one 15 um
        # thin cell on the rim limited a sliding run to 5e-7 s time steps - relocate nodes and optimise again
        for meth in ("Relocate3D", "", "Relocate3D", ""):
            try:
                gmsh.model.mesh.optimize(meth)
            except Exception:
                pass

    progress(0.88, "Collecting mesh data")
    node_tags, coords, _ = gmsh.model.mesh.getNodes()
    coords = np.asarray(coords).reshape(-1, 3)
    order = np.argsort(node_tags)
    node_tags = np.asarray(node_tags)[order]
    coords = coords[order]
    max_tag = int(node_tags.max())
    lut = -np.ones(max_tag + 1, dtype=np.int64)
    lut[node_tags] = np.arange(len(node_tags))

    tets, tet_vol = [], []
    for _, vt in gmsh.model.getEntities(3):
        et, _, en = gmsh.model.mesh.getElements(3, vt)
        for etype, enodes in zip(et, en):
            if etype != 4:
                continue
            arr = lut[np.asarray(enodes, dtype=np.int64).reshape(-1, 4)]
            tets.append(arr)
            tet_vol.append(np.full(len(arr), vt, dtype=np.int32))
    tris, tri_surf = [], []
    zone_of_surf = {s: zi for zi, ss in zone_surfs for s in ss}
    ztris, ztri_zone = [], []
    for _, st in gmsh.model.getEntities(2):
        if st in zone_of_surf:  # (internal faces: the moving-zone boundary, not a boundary patch)
            for etype, enodes in zip(*gmsh.model.mesh.getElements(2, st)[::2]):
                if etype == 2:
                    arr = lut[np.asarray(enodes, dtype=np.int64).reshape(-1, 3)]
                    ztris.append(arr)
                    ztri_zone.append(np.full(len(arr), zone_of_surf[st], dtype=np.int32))
            continue
        et, _, en = gmsh.model.mesh.getElements(2, st)
        for etype, enodes in zip(et, en):
            if etype != 2:
                continue
            arr = lut[np.asarray(enodes, dtype=np.int64).reshape(-1, 3)]
            tris.append(arr)
            tri_surf.append(np.full(len(arr), st, dtype=np.int32))
    tets = np.vstack(tets)
    tet_vol = np.concatenate(tet_vol)
    tris = np.vstack(tris)
    tri_surf = np.concatenate(tri_surf)

    # fix orientation: positive volume
    p = coords
    v = np.einsum("ij,ij->i", np.cross(p[tets[:, 1]] - p[tets[:, 0]], p[tets[:, 2]] - p[tets[:, 0]]),
                  p[tets[:, 3]] - p[tets[:, 0]])
    neg = v < 0
    tets[neg] = tets[neg][:, [0, 2, 1, 3]]

    # collapsed cells (three nodes within micrometres of each other at a CAD micro-edge): a volume near zero gives a
    # Courant number of thousands at the first step. Collapse their short edge when that is safe.
    from . import slivers

    progress(0.9, "Repairing collapsed cells")
    zt_a = np.vstack(ztris) if ztris else np.zeros((0, 3), np.int64)
    zz_a = np.concatenate(ztri_zone) if ztri_zone else np.zeros(0, np.int32)
    tets, tet_vol, tris, tri_surf, zt_a, zz_a, n_collapsed = slivers.collapse_slivers(
        coords, tets, tet_vol, tris, tri_surf, zt_a, zz_a)
    ztris, ztri_zone = ([zt_a], [zz_a]) if len(zt_a) else ([], [])
    if n_collapsed:
        progress(0.91, f"Repaired {n_collapsed} collapsed cell(s) (edges of a few micrometres in the CAD model)")

    # drop unused nodes (CAD vertices on removed entities)
    used = np.zeros(len(coords), bool)
    used[tets.ravel()] = True
    remap = -np.ones(len(coords), np.int64)
    remap[used] = np.arange(used.sum())
    coords = coords[used]
    tets = remap[tets]
    tris = remap[tris]
    good = (tris >= 0).all(1)
    tris, tri_surf = tris[good], tri_surf[good]
    ztris = remap[np.vstack(ztris)] if ztris else np.zeros((0, 3), np.int64)
    ztri_zone = np.concatenate(ztri_zone) if ztri_zone else np.zeros(0, np.int32)

    progress(0.93, "Computing mesh quality")
    q = _tet_quality(coords, tets)
    np.savez_compressed(out / "mesh.npz", nodes=coords, tets=tets.astype(np.int32),
                        tet_vol=tet_vol, tris=tris.astype(np.int32), tri_surf=tri_surf,
                        quality=q.astype(np.float32), zone_tris=ztris.astype(np.int32), zone_tri_zone=ztri_zone)
    hist, edges = np.histogram(q, bins=20, range=(0, 1))
    stats = {
        "nodes": int(len(coords)),
        "cells": int(len(tets)),
        "boundary_triangles": int(len(tris)),
        "cells_per_body": {str(b["id"]): int((tet_vol == b["id"]).sum()) for b in bodies},
        "quality": {
            "metric": "normalised shape ratio (1 = perfect tet, <0.1 poor)",
            "min": float(q.min()), "mean": float(q.mean()),
            "p01": float(np.percentile(q, 1)),
            "n_poor": int((q < 0.1).sum()),
            "histogram": hist.tolist(), "edges": edges.tolist(),
        },
        "defeatured_faces": sum(len(g) - 1 for g in merged),
        "edge_refine": edge_info,
        "collapsed_cells_repaired": int(n_collapsed),
        "sizes": {"near_wall": h_wall, "body": h_body, "far": size_far, "feature": feature,
                  "min_gap": min_gap,
                  "channels": [{"gap": w, "box": list(dims), "lo": list(lo), "hi": list(hi)}
                               for w, dims, lo, hi in channels],
                  "gap_cell": min(gap_sizes.values()) if gap_sizes else None,
                  "cells_across_gap": (min_gap / min(gap_sizes.values())) if gap_sizes
                  else (min_gap / h_wall if min_gap else None)},
        "seconds": round(time.time() - t0, 1),
    }
    (out / "mesh_stats.json").write_text(json.dumps(stats, indent=1))
    progress(1.0, f"Mesh ready: {stats['cells']:,} cells")
    return stats


def surface_intersections(P, T, limit=50, max_cells=512):
    """Pairs of surface triangles that cross (an edge of one passes through the other), not counting triangles
    that share a corner. Uniform grid broad phase, segment-triangle narrow phase. Returns [(tri a, tri b, point)]."""
    P = np.asarray(P, float)
    T = np.asarray(T, np.int64)
    X = P[T]
    lo, hi = X.min(1), X.max(1)
    ext = (hi - lo).max(1)
    h = max(float(np.median(ext)) * 2, 1e-12)
    o = lo.min(0)
    a = np.floor((lo - o) / h).astype(np.int64)
    b = np.floor((hi - o) / h).astype(np.int64)
    n = (b - a + 1).prod(1)
    keep = np.where(n <= max_cells)[0]  # (huge triangles - far-field planes - left out)
    dims = b.max(0) + 2
    nk = n[keep]
    rep = np.repeat(keep, nk)
    off = np.arange(nk.sum()) - np.repeat(np.cumsum(nk) - nk, nk)
    sz = (b[rep] - a[rep] + 1)
    ix = a[rep, 0] + off % sz[:, 0]
    iy = a[rep, 1] + (off // sz[:, 0]) % sz[:, 1]
    iz = a[rep, 2] + off // (sz[:, 0] * sz[:, 1])
    key = (ix * dims[1] + iy) * dims[2] + iz
    order = np.argsort(key, kind="stable")
    key, rep = key[order], rep[order]
    starts = np.flatnonzero(np.r_[True, key[1:] != key[:-1]])
    ends = np.r_[starts[1:], len(key)]
    pa, pb = [], []
    for s, e in zip(starts, ends):
        m = e - s
        if m < 2:
            continue
        g = rep[s:e]
        i, j = np.triu_indices(m, 1)
        pa.append(g[i])
        pb.append(g[j])
    if not pa:
        return [], 0
    pa, pb = np.concatenate(pa), np.concatenate(pb)
    pr = np.unique(np.sort(np.stack([pa, pb], 1), 1), axis=0)
    pa, pb = pr[:, 0], pr[:, 1]
    share = (T[pa][:, :, None] == T[pb][:, None, :]).any((1, 2))
    ov = np.all((lo[pa] <= hi[pb]) & (lo[pb] <= hi[pa]), 1)
    sel = ~share & ov
    pa, pb = pa[sel], pb[sel]

    def seg_tri(A, B, tri):  # segments A->B against triangles (Moller-Trumbore), interior hits only
        v0, v1, v2 = tri[:, 0], tri[:, 1], tri[:, 2]
        d, e1, e2 = B - A, v1 - v0, v2 - v0
        p = np.cross(d, e2)
        det = (e1 * p).sum(1)
        ok = np.abs(det) > 1e-30
        inv = np.where(ok, 1 / np.where(ok, det, 1), 0)
        s = A - v0
        u = (s * p).sum(1) * inv
        q = np.cross(s, e1)
        v, t = (d * q).sum(1) * inv, (e2 * q).sum(1) * inv
        eps = 1e-9
        hit = ok & (u > eps) & (v > eps) & (u + v < 1 - eps) & (t > eps) & (t < 1 - eps)
        return hit, A + d * t[:, None]
    hits, pts = np.zeros(len(pa), bool), np.zeros((len(pa), 3))
    for (x, y) in ((pa, pb), (pb, pa)):
        for i0, i1 in ((0, 1), (1, 2), (2, 0)):
            hh, pp = seg_tri(P[T[x, i0]], P[T[x, i1]], P[T[y]])
            new = hh & ~hits
            pts[new] = pp[new]
            hits |= hh
    w = np.flatnonzero(hits)[:limit]
    return [(int(pa[i]), int(pb[i]), pts[i]) for i in w], int(hits.sum())


def _crossings(gmsh):
    """Where the current surface mesh crosses itself: [(point, local triangle size)], number of crossing pairs."""
    try:
        P, T, _ = _surface_mesh(gmsh)
        hits, n = surface_intersections(P, T, limit=1000)
    except Exception:  # noqa: BLE001  (a diagnosis, never a second failure)
        return [], 0
    out = []
    for a, b, x in hits:
        e = P[np.r_[T[a], T[b]]]
        out.append((x, float(max(np.ptp(e[:3], 0).max(), np.ptp(e[3:], 0).max()))))
    return out, n


def _crossing_text(cross, n):
    if not n:
        return ""
    pts = "; ".join(f"({', '.join(f'{v * 1e3:.1f}' for v in x)}) mm" for x, _ in cross[:4])
    return f" The surface mesh crosses itself in {n} place(s), e.g. at {pts}"


def _surface_mesh(gmsh):
    tags, xyz, _ = gmsh.model.mesh.getNodes()
    lut = np.zeros(int(tags.max()) + 1, np.int64)
    lut[tags] = np.arange(len(tags))
    T, S = [], []
    for _, st in gmsh.model.getEntities(2):
        for et, en in zip(*gmsh.model.mesh.getElements(2, st)[::2]):
            if et == 2:
                t = lut[np.asarray(en, np.int64).reshape(-1, 3)]
                T.append(t)
                S.append(np.full(len(t), st))
    return np.asarray(xyz).reshape(-1, 3), np.vstack(T), np.concatenate(S)


def _tet_quality(p, t):
    """Normalised radius ratio-like shape quality: 6*sqrt(2)*V / l_rms^3 (1 for regular tet)."""
    a, b, c, d = (p[t[:, i]] for i in range(4))
    vol = np.abs(np.einsum("ij,ij->i", np.cross(b - a, c - a), d - a)) / 6.0
    l2 = (((b - a) ** 2).sum(1) + ((c - a) ** 2).sum(1) + ((d - a) ** 2).sum(1)
          + ((c - b) ** 2).sum(1) + ((d - b) ** 2).sum(1) + ((d - c) ** 2).sum(1)) / 6.0
    return np.clip(6 * math.sqrt(2) * vol / np.maximum(l2, 1e-300) ** 1.5, 0, 1)


TASKS = {"prepare": task_prepare, "mesh": task_mesh}


def main(argv):
    task, job_path = argv[1], argv[2]
    job = json.loads(Path(job_path).read_text())
    try:
        res = TASKS[task](job)
        print("RESULT " + json.dumps(res), flush=True)
        return 0
    except Exception as e:  # report cleanly to the parent
        import traceback

        traceback.print_exc()
        print("ERROR " + str(e).replace("\n", " "), flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
