"""The automatic air volume around the parts (flow studies): its shape, size and the role of its faces.

Shapes (all sizes absolute, metres; "fit" sizes follow the parts when the user has not set any):
  box      : axis-aligned box, min/max corners
  c_domain : C-shaped volume - a half cylinder upstream (its curved face is the inlet) joined to a box downstream;
             fewer cells than a box for the same distance to the inlet (classic for wings and bluff bodies)
  cylinder : cylinder along the flow - inlet disk upstream, outlet disk downstream, curved side wall
The flow direction is the dominant axis of the free stream (external aero) or the chosen flow axis (cooling).
"""
from __future__ import annotations

import math

import numpy as np

SHAPES = ("box", "c_domain", "cylinder")
SIZE_KEYS = {"box": ("min", "max"), "c_domain": ("radius", "downstream", "span_min", "span_max"),
             "cylinder": ("radius", "upstream", "downstream")}
AXES = {"+x": (0, 1), "-x": (0, -1), "+y": (1, 1), "-y": (1, -1), "+z": (2, 1), "-z": (2, -1)}


def flow_axis(flow_dir) -> tuple[int, int]:
    f = np.asarray(flow_dir, float)
    if np.linalg.norm(f) < 1e-9:
        f = np.array([0, 0, -1.0])  # hover: downwash
    a = int(np.argmax(np.abs(f)))
    return a, (1 if f[a] > 0 else -1)


def resolve(dom: dict, study: str, parts_bb, L: float, span: float | None = None, flow_dir=(1, 0, 0),
            up_axis: int | None = None) -> dict:
    """The air volume as absolute numbers: the user's sizes where set, else fitted to the parts."""
    bb = np.asarray(parts_bb, float)
    lo, hi = bb[:3], bb[3:]
    c = (lo + hi) / 2
    half = (hi - lo) / 2
    shape = dom.get("shape", "box")
    if shape not in SHAPES:
        raise ValueError(f"Unknown air-volume shape '{shape}'. Options: {', '.join(SHAPES)}")
    if study == "external_aero":
        a, s = flow_axis(flow_dir)
    else:
        a, s = AXES[dom.get("flow_axis", "+x")]
    size = dict(dom.get("size") or {})
    aero = study == "external_aero"
    sp = max(L, float(span or 0.0))
    ff = dom.get("farfield", {})
    up_d, down_d, side_d = (float(ff.get(k, d)) * sp for k, d in (("upstream", 8.0), ("downstream", 16.0), ("side", 8.0)))
    spec = {"shape": shape, "flow_axis": a, "flow_sign": s, "center": c.tolist(), "fitted": not size}
    if shape == "box":
        if aero:
            blo, bhi = lo - side_d, hi + side_d
            if s > 0:
                blo[a], bhi[a] = lo[a] - up_d, hi[a] + down_d
            else:
                blo[a], bhi[a] = lo[a] - down_d, hi[a] + up_d
        else:
            pad = dom.get("padding")
            if pad is None and dom.get("natural_up"):
                # no fan: room for the rising plume above the parts (2 x), the air drawn in below and at the sides
                u = AXES[dom["natural_up"]]
                pad = [1.0] * 6
                pad[2 * u[0] + (1 if u[1] > 0 else 0)] = 2.0
                pad[2 * u[0] + (0 if u[1] > 0 else 1)] = 0.5
            pad = pad if pad is not None else [0.5] * 6
            pad = np.asarray(pad if isinstance(pad, list) else [float(pad)] * 6, float)
            blo, bhi = lo - pad[0::2] * L, hi + pad[1::2] * L
        spec["min"] = [float(x) for x in size.get("min", blo)]
        spec["max"] = [float(x) for x in size.get("max", bhi)]
        spec["bbox_min"], spec["bbox_max"] = spec["min"], spec["max"]
        return spec
    others = [i for i in range(3) if i != a]
    if shape == "c_domain":
        b = dom.get("span_axis")
        b = "xyz".index(b) if isinstance(b, str) else None
        if b is None or b == a:
            # the curve lies in the flow / up plane: the span axis is the remaining one
            b = next((i for i in others if i != up_axis), others[-1]) if up_axis is not None and up_axis != a \
                else max(others, key=lambda i: (hi[i] - lo[i], i))
        e = next(i for i in others if i != b)
        hd = math.hypot(half[a], half[e])  # parts' half diagonal in the plane of the curve
        R = size.get("radius", hd + (up_d if aero else 1.0 * L))
        D = size.get("downstream", half[a] + (down_d if aero else 2.0 * L))
        smin = size.get("span_min", lo[b] - (side_d if aero else 0.5 * L))
        smax = size.get("span_max", hi[b] + (side_d if aero else 0.5 * L))
        spec.update(span_axis=b, third_axis=e, radius=float(R), downstream=float(max(D, R)),
                    span_min=float(smin), span_max=float(smax))
        bmin, bmax = c.copy(), c.copy()
        bmin[e], bmax[e] = c[e] - R, c[e] + R
        bmin[b], bmax[b] = smin, smax
        if s > 0:
            bmin[a], bmax[a] = c[a] - R, c[a] + spec["downstream"]
        else:
            bmin[a], bmax[a] = c[a] - spec["downstream"], c[a] + R
    else:  # cylinder along the flow
        hd = math.hypot(half[others[0]], half[others[1]])
        R = size.get("radius", hd + (side_d if aero else 0.5 * L))
        U = size.get("upstream", half[a] + (up_d if aero else 0.5 * L))
        D = size.get("downstream", half[a] + (down_d if aero else 1.5 * L))
        spec.update(radius=float(R), upstream=float(U), downstream=float(D))
        bmin, bmax = c - R, c + R
        bmin[a], bmax[a] = (c[a] - U, c[a] + D) if s > 0 else (c[a] - D, c[a] + U)
    spec["bbox_min"], spec["bbox_max"] = bmin.tolist(), bmax.tolist()
    return spec


def check_encloses(spec: dict, parts_bb, L: float) -> str | None:
    """A message if the air volume does not contain the parts with some room around them, else None."""
    bb = np.asarray(parts_bb, float)
    lo, hi = bb[:3], bb[3:]
    gap = 0.02 * L
    c = np.asarray(spec["center"], float)
    a = spec["flow_axis"]
    if spec["shape"] == "box":
        if any(spec["min"][i] > lo[i] - gap or spec["max"][i] < hi[i] + gap for i in range(3)):
            return "The air box must enclose the parts with some room on every side."
        return None
    if spec["shape"] == "c_domain":
        b, e = spec["span_axis"], spec["third_axis"]
        corners = [(x, z) for x in (lo[a], hi[a]) for z in (lo[e], hi[e])]
        if max(math.hypot(x - c[a], z - c[e]) for x, z in corners) > spec["radius"] - gap:
            return "The radius of the C-shaped air volume must be larger than the parts (seen along the span)."
        if spec["span_min"] > lo[b] - gap or spec["span_max"] < hi[b] + gap:
            return "The span ends of the C-shaped air volume must be beyond the parts."
        return None
    o = [i for i in range(3) if i != a]
    corners = [(x, y) for x in (lo[o[0]], hi[o[0]]) for y in (lo[o[1]], hi[o[1]])]
    if max(math.hypot(x - c[o[0]], y - c[o[1]]) for x, y in corners) > spec["radius"] - gap:
        return "The radius of the cylindrical air volume must be larger than the parts."
    s = spec["flow_sign"]
    up_end, down_end = c[a] - s * spec["upstream"], c[a] + s * spec["downstream"]
    if min(up_end, down_end) > lo[a] - gap or max(up_end, down_end) < hi[a] + gap:
        return "The ends of the cylindrical air volume must be beyond the parts."
    return None


def build_occ(occ, spec: dict) -> int:
    """Create the air volume with the OpenCASCADE kernel; returns its volume tag."""
    c = np.asarray(spec["center"], float)
    a, s = spec["flow_axis"], spec["flow_sign"]
    unit = np.eye(3)
    if spec["shape"] == "box":
        lo, hi = np.asarray(spec["min"]), np.asarray(spec["max"])
        return occ.addBox(*lo, *(hi - lo))
    if spec["shape"] == "cylinder":
        p0 = c - s * spec["upstream"] * unit[a]
        d = s * (spec["upstream"] + spec["downstream"]) * unit[a]
        return occ.addCylinder(*p0, *d, spec["radius"])
    b, e, R = spec["span_axis"], spec["third_axis"], spec["radius"]
    p0 = c.copy()
    p0[b] = spec["span_min"]
    cyl = occ.addCylinder(*p0, *((spec["span_max"] - spec["span_min"]) * unit[b]), R)
    blo, bhi = c.copy(), c.copy()
    blo[e], bhi[e] = c[e] - R, c[e] + R
    blo[b], bhi[b] = spec["span_min"], spec["span_max"]
    if s > 0:
        bhi[a] = c[a] + spec["downstream"]
    else:
        blo[a] = c[a] - spec["downstream"]
    box = occ.addBox(*blo, *(bhi - blo))
    out, _ = occ.fuse([(3, cyl)], [(3, box)])
    occ.synchronize()
    if len(out) != 1:
        raise RuntimeError("Could not build the C-shaped air volume.")
    return out[0][1]


def face_role(face: dict, spec: dict, tol: float) -> str:
    """inlet / outlet / side for a face of the air volume (face record with bbox and surface_type)."""
    a, s = spec["flow_axis"], spec["flow_sign"]
    bb = face["bbox"]
    if spec["shape"] == "box":
        up_x = spec["min"][a] if s > 0 else spec["max"][a]
        down_x = spec["max"][a] if s > 0 else spec["min"][a]
    else:
        c = spec["center"][a]
        up_x = c - s * (spec["upstream"] if spec["shape"] == "cylinder" else spec["radius"])
        down_x = c + s * spec["downstream"]
    flat_at = (lambda x: abs(bb[a] - x) < tol and abs(bb[a + 3] - x) < tol)
    if flat_at(down_x):
        return "outlet"
    if spec["shape"] == "c_domain":
        return "inlet" if face.get("surface_type") not in ("Plane",) and not flat_at(spec["center"][a]) else "side"
    if flat_at(up_x):
        return "inlet"
    return "side"
