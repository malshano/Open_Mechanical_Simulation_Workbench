"""CAD edits: a short list of operations applied to the imported solids, replayed from the imported file every time the
geometry is built (so any edit can be removed again and the file itself is never changed).

An edit is a dict {"op": name, ...parameters}. Lengths are millimetres, angles degrees, coordinates those of the model
as the user sees it (the frame of the 3D view). Bodies are named; the names are the ones the workbench shows.

  new bodies      box, cylinder, sphere, cone, prism (a flat polygon pushed along a vector), revolve (a flat profile turned
                  about an axis), loft (through two or more cross-sections: wings, fuselages, blades)
  boolean         cut, union, intersect
  move / copy     move, rotate, scale, mirror, pattern_linear, pattern_polar
  features        fillet, chamfer (round / bevel the chosen edges), hole (a drilled hole)
  bodies          delete, rename, cut_plane (keep one side of a plane)

validate() checks and normalises one edit without any CAD kernel (so a wrong request is refused at once, with the
reason); apply() runs the list inside the gmsh worker process; turn() moves the edits with the parts when the model is
turned. Edges are picked with a selector (see SELECTOR), never by id: the ids change whenever an earlier edit does.
"""
from __future__ import annotations

import math

import numpy as np

MM = 1e-3
MAX_EDITS = 60


class EditError(ValueError):
    """A CAD edit that cannot be applied (the message says why; the geometry is left as it was)."""


# kind -> what the value is. pt: a position (mm), dv: a direction or displacement (mm / unit), num, pos (> 0), int,
# names: body names, pts: list of points, sections: list of point lists, sel: edge selector, enum:a|b, str, bool, poly
FIELDS = {
    "box": {"name": ("str", True), "min": ("pt", True), "size": ("size", True), "rot": ("rot", False)},
    "cylinder": {"name": ("str", True), "base": ("pt", True), "axis": ("dv", True), "radius_mm": ("pos", True)},
    "sphere": {"name": ("str", True), "center": ("pt", True), "radius_mm": ("pos", True)},
    "cone": {"name": ("str", True), "base": ("pt", True), "axis": ("dv", True), "radius1_mm": ("num0", True),
             "radius2_mm": ("num0", True)},
    "prism": {"name": ("str", True), "points": ("poly", True), "direction": ("dv", True)},
    "revolve": {"name": ("str", True), "points": ("poly", True), "axis_point": ("pt", True),
                "axis_direction": ("dv", True), "angle_deg": ("pos", False)},
    "loft": {"name": ("str", True), "sections": ("sections", True), "smooth": ("bool", False),
             "ruled": ("bool", False)},
    "cut": {"target": ("str", True), "tools": ("names", True)},
    "union": {"bodies": ("names", True), "name": ("str", False)},
    "intersect": {"target": ("str", True), "tools": ("names", True)},
    "move": {"bodies": ("names", False), "by": ("dv", True)},
    "rotate": {"bodies": ("names", False), "point": ("pt", True), "axis": ("dv", True), "angle_deg": ("num", True)},
    "scale": {"bodies": ("names", False), "factor": ("factor", True), "about": ("pt", False)},
    "mirror": {"bodies": ("names", False), "point": ("pt", True), "normal": ("dv", True), "copy": ("bool", False)},
    "pattern_linear": {"body": ("str", True), "count": ("int", True), "step": ("dv", True), "union": ("bool", False)},
    "pattern_polar": {"body": ("str", True), "count": ("int", True), "point": ("pt", True), "axis": ("dv", True),
                      "angle_deg": ("pos", False), "union": ("bool", False)},
    "fillet": {"body": ("str", True), "radius_mm": ("pos", True), "edges": ("sel", True)},
    "chamfer": {"body": ("str", True), "distance_mm": ("pos", True), "edges": ("sel", True)},
    "hole": {"body": ("str", True), "at": ("pt", True), "direction": ("dv", True), "diameter_mm": ("pos", True),
             "depth_mm": ("pos", False)},
    "delete": {"bodies": ("names", True)},
    "rename": {"body": ("str", True), "name": ("str", True)},
    "cut_plane": {"bodies": ("names", False), "point": ("pt", True), "normal": ("dv", True), "keep": ("enum:+|-", False)},
}

# the keys of an edge selector (SELECTOR in the tool text): points are positions (mm)
SELECTOR_KEYS = ("all", "near", "of_face_near", "parallel", "length_mm", "type", "in_box")


def _num(v, what, lo=None, hi=None):
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise EditError(f"{what} must be a number") from None
    if not math.isfinite(x):
        raise EditError(f"{what} must be a finite number")
    if lo is not None and x < lo:
        raise EditError(f"{what} must be at least {lo}")
    if hi is not None and x > hi:
        raise EditError(f"{what} must be at most {hi}")
    return x


def _vec(v, what, n=3):
    if not isinstance(v, (list, tuple)) or len(v) != n:
        raise EditError(f"{what} must be a list of {n} numbers [x, y, z] in mm")
    return [_num(x, what) for x in v]


def _names(v, what):
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, (list, tuple)) or not v or not all(isinstance(x, str) and x.strip() for x in v):
        raise EditError(f"{what} must be a list of body names")
    return [x.strip() for x in v]


def _selector(v):
    if not isinstance(v, dict) or not v:
        raise EditError("edges must be a selector, for example {\"all\": true} or {\"near\": [[x, y, z]]} "
                        f"(keys: {', '.join(SELECTOR_KEYS)})")
    bad = set(v) - set(SELECTOR_KEYS)
    if bad:
        raise EditError(f"Unknown edge selector key(s) {sorted(bad)}. Keys: {', '.join(SELECTOR_KEYS)}")
    out = {}
    for k, x in v.items():
        if k == "all":
            out[k] = bool(x)
        elif k in ("near", "of_face_near"):
            pts = x if isinstance(x, (list, tuple)) and x and isinstance(x[0], (list, tuple)) else [x]
            out[k] = [_vec(p, f"edges.{k} point") for p in pts]
        elif k == "parallel":
            if isinstance(x, str):
                if x.strip().lower() not in ("x", "y", "z"):
                    raise EditError("edges.parallel must be 'x', 'y', 'z' or a direction [dx, dy, dz]")
                x = [1.0 if c == x.strip().lower() else 0.0 for c in "xyz"]
            x = _vec(x, "edges.parallel")
            if not any(abs(c) > 0 for c in x):
                raise EditError("edges.parallel must not be a zero vector")
            out[k] = x
        elif k == "length_mm":
            if not (isinstance(x, (list, tuple)) and len(x) == 2):
                raise EditError("edges.length_mm must be [min, max] (either may be null)")
            out[k] = [0.0 if x[0] is None else _num(x[0], "edges.length_mm"),
                      None if x[1] is None else _num(x[1], "edges.length_mm")]
        elif k == "type":
            if str(x).lower() not in ("line", "circle", "ellipse", "spline"):
                raise EditError("edges.type must be line, circle, ellipse or spline")
            out[k] = str(x).lower()
        elif k == "in_box":
            b = _vec(x, "edges.in_box", 6)
            out[k] = [min(b[i], b[i + 3]) for i in range(3)] + [max(b[i], b[i + 3]) for i in range(3)]
    if out.get("all") is False and len(out) == 1:
        raise EditError("the edge selector selects nothing")
    return out


def validate(op: dict) -> dict:
    """The edit with its values checked and converted (floats, lists); raises EditError with the reason."""
    if not isinstance(op, dict) or "op" not in op:
        raise EditError("an edit is an object with an 'op' key. Operations: " + ", ".join(FIELDS))
    kind = op["op"]
    if kind not in FIELDS:
        raise EditError(f"Unknown operation '{kind}'. Operations: " + ", ".join(FIELDS))
    spec = FIELDS[kind]
    bad = set(op) - set(spec) - {"op"}
    if bad:
        raise EditError(f"{kind}: unknown parameter(s) {sorted(bad)}. Parameters: {', '.join(spec)}")
    out = {"op": kind}
    for key, (k, required) in spec.items():
        if key not in op or op[key] is None:
            if required:
                raise EditError(f"{kind}: '{key}' is required")
            continue
        v, what = op[key], f"{kind}.{key}"
        if k == "str":
            if not isinstance(v, str) or not v.strip():
                raise EditError(f"{what} must be a name")
            out[key] = v.strip()
        elif k == "num":
            out[key] = _num(v, what)
        elif k == "num0":
            out[key] = _num(v, what, 0.0)
        elif k == "pos":
            out[key] = _num(v, what, 1e-9)
        elif k == "int":
            n = int(_num(v, what, 2, 500))
            out[key] = n
        elif k == "pt":
            out[key] = _vec(v, what)
        elif k == "dv":
            d = _vec(v, what)
            if not any(abs(c) > 0 for c in d):
                raise EditError(f"{what} must not be a zero vector")
            out[key] = d
        elif k == "size":
            out[key] = [_num(c, what, 1e-9) for c in _vec(v, what)]
        elif k == "rot":
            R = np.asarray(v, float)
            if R.shape != (3, 3) or not np.allclose(R @ R.T, np.eye(3), atol=1e-6):
                raise EditError(f"{what} must be a 3x3 rotation matrix")
            out[key] = R.tolist()
        elif k == "names":
            out[key] = ["all"] if (isinstance(v, str) and v.lower() == "all") else _names(v, what)
        elif k == "bool":
            out[key] = bool(v)
        elif k == "factor":
            f = [_num(c, what, 1e-6, 1e6) for c in (v if isinstance(v, (list, tuple)) else [v])]
            if len(f) not in (1, 3):
                raise EditError(f"{what} must be one number or [sx, sy, sz]")
            out[key] = f
        elif k == "poly":
            pts = [_vec(p, what) for p in v] if isinstance(v, (list, tuple)) else None
            if not pts or len(pts) < 3:
                raise EditError(f"{what} must be at least 3 points [[x, y, z], ...] in one plane")
            out[key] = pts
        elif k == "sections":
            if not isinstance(v, (list, tuple)) or len(v) < 2:
                raise EditError(f"{what} must be a list of at least 2 cross-sections, each a list of points")
            secs = []
            for s in v:
                if not isinstance(s, (list, tuple)) or len(s) < 3:
                    raise EditError(f"every cross-section in {what} needs at least 3 points")
                secs.append([_vec(p, what) for p in s])
            out[key] = secs
        elif k == "sel":
            out[key] = _selector(v)
        elif k.startswith("enum:"):
            if str(v) not in k[5:].split("|"):
                raise EditError(f"{what} must be one of {k[5:].replace('|', ', ')}")
            out[key] = str(v)
    if kind == "cone" and out["radius1_mm"] == 0 and out["radius2_mm"] == 0:
        raise EditError("cone: at least one radius must be above zero")
    if kind == "pattern_linear" and out.get("count", 0) < 2:
        raise EditError("pattern_linear: count must be at least 2 (the original and copies)")
    if kind == "revolve":
        out.setdefault("angle_deg", 360.0)
        if out["angle_deg"] > 360.0:
            raise EditError("revolve.angle_deg must be at most 360")
    if kind == "pattern_polar":
        out.setdefault("angle_deg", 360.0)
        if out["angle_deg"] > 360.0:
            raise EditError("pattern_polar.angle_deg must be at most 360")
    return out


def describe(op: dict) -> str:
    """One line for the user (the geometry panel, the project summary)."""
    def short(v):
        if isinstance(v, float):
            return f"{v:.4g}"
        if isinstance(v, (list, tuple)):
            s = "[" + ", ".join(short(x) for x in v[:6]) + (", ..." if len(v) > 6 else "") + "]"
            return s
        if isinstance(v, dict):
            return "{" + ", ".join(f"{k}: {short(x)}" for k, x in v.items()) + "}"
        return str(v)
    rest = ", ".join(f"{k}={short(v)}" for k, v in op.items() if k not in ("op", "rot"))
    text = f"{op.get('op', '?')}  {rest}"
    return text if len(text) <= 150 else text[:147] + "..."


# ---------------------------------------------------------------------------- the edits move with the parts
def turn(edits: list, D, t) -> list:
    """The edits after the parts were turned: x' = D x + t (D a 3x3 rotation, t the shift in metres). Positions move,
    directions only turn; a box keeps its shape by carrying the turn in its 'rot'; an axis-aligned selection box becomes
    the box around the turned one."""
    D = np.asarray(D, float)
    t_mm = np.asarray(t, float) / MM
    pt = lambda p: (D @ np.asarray(p, float) + t_mm).tolist()  # noqa: E731
    dv = lambda v: (D @ np.asarray(v, float)).tolist()  # noqa: E731
    out = []
    for e in edits:
        n = dict(e)
        spec = FIELDS[e["op"]]
        for key, (k, _) in spec.items():
            if key not in n:
                continue
            if k == "pt":
                n[key] = pt(n[key])
            elif k == "dv":
                n[key] = dv(n[key])
            elif k == "poly":
                n[key] = [pt(p) for p in n[key]]
            elif k == "sections":
                n[key] = [[pt(p) for p in s] for s in n[key]]
            elif k == "sel":
                s = dict(n[key])
                for q in ("near", "of_face_near"):
                    if q in s:
                        s[q] = [pt(p) for p in s[q]]
                if "parallel" in s:
                    s["parallel"] = dv(s["parallel"])
                if "in_box" in s:
                    b = s["in_box"]
                    c = np.array([[b[i], b[j], b[k_]] for i in (0, 3) for j in (1, 4) for k_ in (2, 5)], float)
                    c = c @ D.T + t_mm
                    s["in_box"] = [*c.min(0).tolist(), *c.max(0).tolist()]
                n[key] = s
        if e["op"] == "box":
            n["rot"] = (D @ np.asarray(e.get("rot") or np.eye(3), float)).tolist()
        out.append(n)
    return out


# ---------------------------------------------------------------------------- applying the edits (gmsh worker)
def clean_name(raw: str, i: int) -> str:
    n = (raw or "").split("/")[-1].strip()
    if not n or n.lower().startswith(("open cascade", "solid", "body", "part", "none")):
        return f"Body {i + 1}"
    return n


def half_space(gmsh, point, normal, size: float) -> int:
    """A big box filling the side of the plane (point, normal) the normal points to (metres)."""
    occ = gmsh.model.occ
    box = occ.addBox(-size, -size, 0.0, 2 * size, 2 * size, size)  # z >= 0 in the plane's own frame
    n = np.asarray(normal, float)
    n = n / np.linalg.norm(n)
    ax = np.cross([0.0, 0.0, 1.0], n)
    sn = float(np.linalg.norm(ax))
    if sn > 1e-12:
        occ.rotate([(3, box)], 0, 0, 0, *(float(v) for v in ax / sn), math.atan2(sn, float(n[2])))
    elif n[2] < 0:
        occ.rotate([(3, box)], 0, 0, 0, 1, 0, 0, math.pi)
    occ.translate([(3, box)], *(float(v) for v in point))
    return box


class _Model:
    """The gmsh model seen through the edits: bodies by name."""

    def __init__(self, gmsh):
        self.g = gmsh
        self.occ = gmsh.model.occ
        self.reg: dict = {}

    def sync(self):
        self.occ.synchronize()

    def vols(self) -> list:
        return [t for _, t in self.g.model.getEntities(3)]

    # The names live here, not in gmsh: gmsh puts the STEP part names back on every synchronize(), so a body of the file
    # that was renamed would get its old name again. apply() ends by rebuilding the model without those labels.
    def name(self, tag) -> str:
        return self.reg.get(tag) or self.g.model.getEntityName(3, tag)

    def set_name(self, tag, name):
        self.reg[tag] = name
        self.g.model.setEntityName(3, tag, name)

    def name_all(self):
        """Every body gets its final name now (STEP names kept, unnamed ones 'Body n', duplicates numbered), so the
        edits that follow can refer to them."""
        used = set()
        for i, t in enumerate(self.vols()):
            n = clean_name(self.name(t), i)
            base, k = n, 2
            while n in used:
                n, k = f"{base} {k}", k + 1
            used.add(n)
            self.set_name(t, n)

    def names(self) -> dict:
        return {t: self.name(t) for t in self.vols()}

    def free_name(self, name: str) -> str:
        """The name for a new body: the one asked for, which must not be taken."""
        if name in self.names().values():
            raise EditError(f"A body named '{name}' exists already: choose another name")
        return name

    def unique(self, name: str) -> str:
        taken, n, k = set(self.names().values()), name, 2
        while n in taken:
            n, k = f"{name} {k}", k + 1
        return n

    def pick(self, refs, optional_all=True) -> list:
        names = self.names()
        if refs in (None, ["all"]):
            if not names:
                raise EditError("There is no body to work on")
            return list(names)
        out = []
        for r in refs:
            hits = [t for t, n in names.items() if n == r] or [t for t, n in names.items() if n.lower() == r.lower()]
            if len(hits) != 1:
                raise EditError(f"No body named '{r}'. Bodies: " + (", ".join(f"'{n}'" for n in names.values()) or "none"))
            if hits[0] not in out:
                out.append(hits[0])
        return out

    def one(self, ref: str) -> int:
        return self.pick([ref])[0]

    def bbox_mm(self, tag) -> list:
        b = self.g.model.getBoundingBox(3, tag)
        return [round(v / MM, 3) for v in b]

    def finish_new(self, dimtags, name, what):
        vols = [t for d, t in dimtags if d == 3]
        self.sync()
        if not vols:
            raise EditError(f"{what}: the kernel made no solid (check that the shape is closed and not degenerate)")
        for k, t in enumerate(vols):
            self.set_name(t, name if k == 0 else self.unique(name))
        return vols[0]

    # --- edges
    def edges_of(self, vol) -> dict:
        """{edge tag: [adjacent face tags of the body]}"""
        out = {}
        for _, f in self.g.model.getBoundary([(3, vol)], oriented=False):
            for _, e in self.g.model.getBoundary([(2, f)], oriented=False):
                out.setdefault(abs(e), []).append(f)
        return out

    def edge_info(self, e) -> dict:
        m = self.g.model
        typ = m.getType(1, e)
        com = self.occ.getCenterOfMass(1, e)
        length = self.occ.getMass(1, e)
        d = None
        pts = [abs(p) for _, p in m.getBoundary([(1, e)], oriented=False)]
        if typ == "Line" and len(pts) == 2:
            a = np.array(m.getValue(0, pts[0], []))
            b = np.array(m.getValue(0, pts[1], []))
            if np.linalg.norm(b - a) > 0:
                d = ((b - a) / np.linalg.norm(b - a)).tolist()
        return {"type": typ, "center": list(com), "length": length, "dir": d}


def _pick_edges(M: _Model, vol, sel: dict):
    """-> (sorted edge tags chosen by the selector, {edge: [adjacent faces of the body]})."""
    edges = M.edges_of(vol)
    if not edges:
        raise EditError("The body has no edges")
    diag = math.dist(M.g.model.getBoundingBox(3, vol)[:3], M.g.model.getBoundingBox(3, vol)[3:])
    chosen = set()
    start = False
    for p_mm in sel.get("near", []):
        p = [c * MM for c in p_mm]
        best, bd = None, 1e300
        for e in edges:
            cp, _ = M.g.model.getClosestPoint(1, e, p)
            d = math.dist(cp, p)
            if d < bd:
                best, bd = e, d
        if bd > max(5 * MM, 0.02 * diag):
            raise EditError(f"No edge of the body is near {p_mm} mm (the closest is {bd / MM:.1f} mm away): use "
                            "find_edges to get the coordinates")
        chosen.add(best)
        start = True
    for p_mm in sel.get("of_face_near", []):
        p = [c * MM for c in p_mm]
        faces = {f for fs in edges.values() for f in fs}
        best, bd = None, 1e300
        for f in faces:
            cp, _ = M.g.model.getClosestPoint(2, f, p)
            d = math.dist(cp, p)
            if d < bd:
                best, bd = f, d
        if bd > max(5 * MM, 0.02 * diag):
            raise EditError(f"No face of the body is near {p_mm} mm (the closest is {bd / MM:.1f} mm away)")
        chosen.update(e for e, fs in edges.items() if best in fs)
        start = True
    if not start:
        chosen = set(edges)
    info = {e: M.edge_info(e) for e in chosen}
    keep = []
    for e, ei in info.items():
        ok = True
        if "parallel" in sel:
            v = np.asarray(sel["parallel"], float)
            v = v / np.linalg.norm(v)
            ok = ei["dir"] is not None and abs(float(np.dot(ei["dir"], v))) > math.cos(math.radians(2.0))
        if ok and "length_mm" in sel:
            lo, hi = sel["length_mm"]
            L = ei["length"] / MM
            ok = L >= lo - 1e-6 and (hi is None or L <= hi + 1e-6)
        if ok and "type" in sel:
            t = ei["type"].lower()
            ok = (sel["type"] == "spline" and "spline" in t) or t == sel["type"] or (sel["type"] == "ellipse" and "ellipse" in t)
        if ok and "in_box" in sel:
            b = sel["in_box"]
            c = [v / MM for v in ei["center"]]
            ok = all(b[i] - 1e-6 <= c[i] <= b[i + 3] + 1e-6 for i in range(3))
        if ok:
            keep.append(e)
    if not keep:
        raise EditError("The edge selector matches no edge of the body (use find_edges to see the edges)")
    return sorted(keep), edges


# ---------------------------------------------------------------------------- the operations
def _polygon_surface(M: _Model, pts_mm, what):
    pts = [np.asarray(p, float) for p in pts_mm]
    if np.allclose(pts[0], pts[-1]):
        pts = pts[:-1]
    if len(pts) < 3:
        raise EditError(f"{what}: the polygon needs at least 3 different points")
    n = np.zeros(3)
    for i in range(len(pts)):  # (Newell's normal: the plane of the polygon and twice its area)
        a, b = pts[i], pts[(i + 1) % len(pts)]
        n += np.cross(a, b)
    area2 = float(np.linalg.norm(n))
    size = max(float(np.ptp(np.array(pts), axis=0).max()), 1e-9)
    if area2 < 1e-9 * size * size:
        raise EditError(f"{what}: the points are on a line (no area)")
    n /= area2
    off = max(abs(float(np.dot(p - pts[0], n))) for p in pts)
    if off > 1e-4 * size + 1e-6:
        raise EditError(f"{what}: the points are not in one plane (one is {off:.3g} mm off it)")
    ids = [M.occ.addPoint(*(p * MM)) for p in pts]
    lines = [M.occ.addLine(ids[i], ids[(i + 1) % len(ids)]) for i in range(len(ids))]
    loop = M.occ.addCurveLoop(lines)
    return M.occ.addPlaneSurface([loop]), n


def op_box(M, p):
    name = M.free_name(p["name"])
    t = M.occ.addBox(*(c * MM for c in p["min"]), *(c * MM for c in p["size"]))
    rot = p.get("rot")
    if rot is not None and not np.allclose(rot, np.eye(3)):
        from scipy.spatial.transform import Rotation
        rv = Rotation.from_matrix(np.asarray(rot, float)).as_rotvec()
        ang = float(np.linalg.norm(rv))
        if ang > 1e-12:
            M.occ.rotate([(3, t)], *(c * MM for c in p["min"]), *(rv / ang), ang)
    M.finish_new([(3, t)], name, "box")


def op_cylinder(M, p):
    name = M.free_name(p["name"])
    t = M.occ.addCylinder(*(c * MM for c in p["base"]), *(c * MM for c in p["axis"]), p["radius_mm"] * MM)
    M.finish_new([(3, t)], name, "cylinder")


def op_sphere(M, p):
    name = M.free_name(p["name"])
    t = M.occ.addSphere(*(c * MM for c in p["center"]), p["radius_mm"] * MM)
    M.finish_new([(3, t)], name, "sphere")


def op_cone(M, p):
    name = M.free_name(p["name"])
    t = M.occ.addCone(*(c * MM for c in p["base"]), *(c * MM for c in p["axis"]), p["radius1_mm"] * MM,
                      p["radius2_mm"] * MM)
    M.finish_new([(3, t)], name, "cone")


def op_prism(M, p):
    name = M.free_name(p["name"])
    s, n = _polygon_surface(M, p["points"], "prism")
    d = np.asarray(p["direction"], float)
    if abs(float(np.dot(d / np.linalg.norm(d), n))) < 1e-6:
        raise EditError("prism: the direction lies in the plane of the polygon (it must push the polygon out of its plane)")
    out = M.occ.extrude([(2, s)], *(c * MM for c in d))
    M.finish_new(out, name, "prism")


def op_revolve(M, p):
    name = M.free_name(p["name"])
    s, n = _polygon_surface(M, p["points"], "revolve")
    ax = np.asarray(p["axis_direction"], float)
    ax = ax / np.linalg.norm(ax)
    if abs(float(np.dot(ax, n))) > 1e-4:
        raise EditError("revolve: the axis must lie in the plane of the profile (a profile is turned about a line "
                        "in its own plane)")
    out = M.occ.revolve([(2, s)], *(c * MM for c in p["axis_point"]), *ax, math.radians(p["angle_deg"]))
    M.finish_new(out, name, "revolve")


def op_loft(M, p):
    name = M.free_name(p["name"])
    wires = []
    for si, sec in enumerate(p["sections"]):
        pts = [np.asarray(q, float) for q in sec]
        if np.allclose(pts[0], pts[-1]):
            pts = pts[:-1]
        ids = [M.occ.addPoint(*(q * MM)) for q in pts]
        n = len(ids)
        if p.get("smooth"):
            # a spline through the points, but one spline per smooth run: a sharp corner (the trailing edge of an airfoil)
            # ends a run. One closed spline through a corner overshoots it and gives a surface the mesher cannot handle.
            corners = []
            for i in range(n):
                a, b = pts[i] - pts[i - 1], pts[(i + 1) % n] - pts[i]
                na, nb = np.linalg.norm(a), np.linalg.norm(b)
                if na > 0 and nb > 0 and float(np.dot(a, b)) / (na * nb) < math.cos(math.radians(40)):
                    corners.append(i)
            if not corners:
                curve = [M.occ.addSpline(ids + [ids[0]])]
            else:
                curve = []
                for k, c0 in enumerate(corners):
                    c1 = corners[(k + 1) % len(corners)]
                    run = [(c0 + j) % n for j in range(((c1 - c0) % n or n) + 1)]
                    curve.append(M.occ.addSpline([ids[j] for j in run]) if len(run) >= 3
                                 else M.occ.addLine(ids[run[0]], ids[run[-1]]))
        else:
            curve = [M.occ.addLine(ids[i], ids[(i + 1) % n]) for i in range(n)]
        wires.append(M.occ.addCurveLoop(curve))
    try:
        out = M.occ.addThruSections(wires, makeSolid=True, makeRuled=bool(p.get("ruled")))
    except Exception as e:  # noqa: BLE001
        raise EditError(f"loft: the kernel could not join the cross-sections ({e}). Give every section the same "
                        "number of points in the same order, going round the same way") from None
    M.finish_new(out, name, "loft")


def _consume(M, kind, targets, tools, result_name=None):
    fn = {"cut": M.occ.cut, "union": M.occ.fuse, "intersect": M.occ.intersect}[kind]
    objs = [(3, t) for t in targets]
    tls = [(3, t) for t in tools]
    names = {t: M.name(t) for t in targets + tools}
    try:
        out, mp = fn(objs, tls, removeObject=True, removeTool=True)
    except Exception as e:  # noqa: BLE001
        raise EditError(f"{kind}: the kernel could not do it ({e})") from None
    M.sync()
    named = set()
    for i, t in enumerate(targets):
        for tag in [tag for d, tag in mp[i] if d == 3 and tag not in named]:
            base = result_name or names[t]
            M.set_name(tag, base if base not in {M.name(x) for x in named} else M.unique(base))
            named.add(tag)
    if not [t for d, t in out if d == 3]:
        raise EditError(f"{kind}: nothing is left (the bodies do not overlap" + (", or the cut removed everything)" if kind == "cut" else ")"))
    return [t for d, t in out if d == 3]


def op_cut(M, p):
    target = M.one(p["target"])
    tools = [t for t in M.pick(p["tools"]) if t != target]
    if not tools:
        raise EditError("cut: the tool bodies must be other bodies than the target")
    _consume(M, "cut", [target], tools)


def op_union(M, p):
    ids = M.pick(p["bodies"])
    if len(ids) < 2:
        raise EditError("union: give at least two bodies")
    res = _consume(M, "union", ids[:1], ids[1:], p.get("name"))
    if len(res) > 1:
        raise EditError("union: the bodies do not touch or overlap, so they stay separate. Move them together first")


def op_intersect(M, p):
    target = M.one(p["target"])
    tools = [t for t in M.pick(p["tools"]) if t != target]
    if not tools:
        raise EditError("intersect: the tool bodies must be other bodies than the target")
    _consume(M, "intersect", [target], tools)


def op_move(M, p):
    M.occ.translate([(3, t) for t in M.pick(p.get("bodies"))], *(c * MM for c in p["by"]))
    M.sync()


def op_rotate(M, p):
    ax = np.asarray(p["axis"], float)
    M.occ.rotate([(3, t) for t in M.pick(p.get("bodies"))], *(c * MM for c in p["point"]), *(ax / np.linalg.norm(ax)),
                 math.radians(p["angle_deg"]))
    M.sync()


def op_scale(M, p):
    ids = M.pick(p.get("bodies"))
    f = p["factor"] * 3 if len(p["factor"]) == 1 else p["factor"]
    if p.get("about") is not None:
        c = [v * MM for v in p["about"]]
    else:
        bbs = [M.g.model.getBoundingBox(3, t) for t in ids]
        c = [(min(b[i] for b in bbs) + max(b[i + 3] for b in bbs)) / 2 for i in range(3)]
    M.occ.dilate([(3, t) for t in ids], *c, *f)
    M.sync()


def op_mirror(M, p):
    ids = M.pick(p.get("bodies"))
    n = np.asarray(p["normal"], float)
    n = n / np.linalg.norm(n)
    d = -float(np.dot(n, [c * MM for c in p["point"]]))
    if p.get("copy", True):
        names = {t: M.name(t) for t in ids}
        new = M.occ.copy([(3, t) for t in ids])
        M.occ.mirror(new, *n, d)
        M.sync()
        for (_, nt), t in zip(new, ids):
            M.set_name(nt, M.unique(names[t] + " (mirror)"))
    else:
        M.occ.mirror([(3, t) for t in ids], *n, d)
        M.sync()


def _copies(M, tag, transforms, union):
    name = M.name(tag)
    made = []
    for k, tf in enumerate(transforms):
        new = M.occ.copy([(3, tag)])
        tf(new)
        M.sync()
        nt = new[0][1]
        M.set_name(nt, M.unique(f"{name} {k + 2}"))
        made.append(nt)
    if union:
        _consume(M, "union", [tag], made, name)


def op_pattern_linear(M, p):
    tag = M.one(p["body"])
    step = [c * MM for c in p["step"]]
    _copies(M, tag, [(lambda new, k=k: M.occ.translate(new, *(c * k for c in step))) for k in range(1, p["count"])],
            p.get("union"))


def op_pattern_polar(M, p):
    tag = M.one(p["body"])
    ax = np.asarray(p["axis"], float)
    ax = ax / np.linalg.norm(ax)
    full = abs(p["angle_deg"] - 360.0) < 1e-9
    step = p["angle_deg"] / (p["count"] if full else p["count"] - 1)
    pt = [c * MM for c in p["point"]]
    _copies(M, tag, [(lambda new, k=k: M.occ.rotate(new, *pt, *ax, math.radians(step * k))) for k in range(1, p["count"])],
            p.get("union"))


def _keep_name(M, name, new_vols, before, what):
    """A feature (fillet, chamfer) rebuilds the volume under a new tag: it keeps its name. The kernel does not always
    refuse an impossible one (a 12 mm fillet on a 20 mm bar came back as a shape three times the size): the result has to
    stay inside the box of the body it came from, with a volume that is a sensible change of it."""
    vols = [t for d, t in new_vols if d == 3]
    ok = len(vols) == 1
    if ok:
        bb0, v0 = before
        bb = M.g.model.getBoundingBox(3, vols[0])
        tol = 1e-4 * math.dist(bb0[:3], bb0[3:]) + 1e-9
        v1 = M.occ.getMass(3, vols[0])
        ok = (all(bb[i] >= bb0[i] - tol and bb[i + 3] <= bb0[i + 3] + tol for i in range(3))
              and 0.3 * v0 < v1 < 1.05 * v0)
    if not ok:
        raise EditError(f"{what} did not give a sound shape (the radius or distance is too large for the body there)")
    M.set_name(vols[0], name)


def op_fillet(M, p):
    vol = M.one(p["body"])
    name = M.name(vol)
    edges, _ = _pick_edges(M, vol, p["edges"])
    before = (M.g.model.getBoundingBox(3, vol), M.occ.getMass(3, vol))
    what = f"fillet: radius {p['radius_mm']:g} mm on {len(edges)} edge(s) of '{name}'"
    try:
        out = M.occ.fillet([vol], edges, [p["radius_mm"] * MM] * len(edges), removeVolume=True)
        M.sync()
        _keep_name(M, name, out, before, what)
    except EditError:
        raise EditError(what + " did not work. A smaller radius, or fewer edges, usually does") from None
    except Exception as e:  # noqa: BLE001
        raise EditError(f"{what} did not work ({e}). A smaller radius, or fewer edges, usually does") from None


def op_chamfer(M, p):
    vol = M.one(p["body"])
    name = M.name(vol)
    edges, adj = _pick_edges(M, vol, p["edges"])
    before = (M.g.model.getBoundingBox(3, vol), M.occ.getMass(3, vol))
    what = f"chamfer: distance {p['distance_mm']:g} mm on {len(edges)} edge(s) of '{name}'"
    try:
        out = M.occ.chamfer([vol], edges, [adj[e][0] for e in edges], [p["distance_mm"] * MM] * len(edges),
                            removeVolume=True)
        M.sync()
        _keep_name(M, name, out, before, what)
    except EditError:
        raise EditError(what + " did not work. A smaller distance, or fewer edges, usually does") from None
    except Exception as e:  # noqa: BLE001
        raise EditError(f"{what} did not work ({e}). A smaller distance, or fewer edges, usually does") from None


def op_hole(M, p):
    vol = M.one(p["body"])
    d = np.asarray(p["direction"], float)
    d = d / np.linalg.norm(d)
    bb = M.g.model.getBoundingBox(3, vol)
    diag = math.dist(bb[:3], bb[3:])
    over = max(1.0 * MM, 0.01 * diag)  # (starts a little outside the face so that the cut opens it cleanly)
    at = np.asarray([c * MM for c in p["at"]], float)
    depth = (p["depth_mm"] * MM if p.get("depth_mm") else 2.0 * diag + over)
    start = at - d * over
    tool = M.occ.addCylinder(*start, *(d * (depth + over)), p["diameter_mm"] * MM / 2)
    M.sync()
    M.set_name(tool, M.unique("hole tool"))
    try:
        _consume(M, "cut", [vol], [tool])
    except EditError as e:
        raise EditError(f"hole: {e}") from None


def op_delete(M, p):
    ids = M.pick(p["bodies"])
    if len(ids) >= len(M.vols()):
        raise EditError("delete: that would remove every body")
    M.occ.remove([(3, t) for t in ids], recursive=True)
    M.sync()


def op_rename(M, p):
    tag = M.one(p["body"])
    if p["name"] != M.name(tag):
        M.set_name(tag, M.free_name(p["name"]))


def op_cut_plane(M, p):
    ids = M.pick(p.get("bodies"))
    n = np.asarray(p["normal"], float)
    n = n / np.linalg.norm(n) * (1 if p.get("keep", "+") == "+" else -1)
    pt = [c * MM for c in p["point"]]
    for t in ids:
        bb = M.g.model.getBoundingBox(3, t)
        reach = math.dist(bb[:3], bb[3:]) + math.dist([(bb[k] + bb[k + 3]) / 2 for k in range(3)], pt)
        tool = half_space(M.g, pt, n, 2.0 * reach + 1e-3)
        M.sync()
        M.set_name(tool, M.unique("plane tool"))
        _consume(M, "intersect", [t], [tool])   # (the pieces keep the name of the body)


OPS = {
    "box": op_box, "cylinder": op_cylinder, "sphere": op_sphere, "cone": op_cone, "prism": op_prism,
    "revolve": op_revolve, "loft": op_loft, "cut": op_cut, "union": op_union, "intersect": op_intersect,
    "move": op_move, "rotate": op_rotate, "scale": op_scale, "mirror": op_mirror, "pattern_linear": op_pattern_linear,
    "pattern_polar": op_pattern_polar, "fillet": op_fillet, "chamfer": op_chamfer, "hole": op_hole,
    "delete": op_delete, "rename": op_rename, "cut_plane": op_cut_plane,
}


def _rebuild_without_labels(M: _Model, workdir=None):
    """The edited solids through a BREP file (exact, and it carries no STEP labels), named from the registry: the names
    then stay on the bodies whatever the model does next."""
    import tempfile
    from pathlib import Path

    from scipy.optimize import linear_sum_assignment

    M.sync()
    vols = M.vols()
    snap = [(np.array(M.occ.getCenterOfMass(3, t)), M.occ.getMass(3, t), M.name(t)) for t in vols]
    d = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="simwb_edit_"))
    d.mkdir(parents=True, exist_ok=True)
    path = d / "edited.brep"
    try:
        M.g.write(str(path))
        M.g.clear()
        M.g.model.add("edited")
        M.occ.importShapes(str(path), highestDimOnly=True)
        M.occ.synchronize()
        new = M.vols()
        if len(new) != len(snap):
            raise EditError(f"the edited model came back with {len(new)} bodies instead of {len(snap)}")
        cost = np.zeros((len(new), len(snap)))
        for i, t in enumerate(new):
            com, m = np.array(M.occ.getCenterOfMass(3, t)), M.occ.getMass(3, t)
            for j, (c0, m0, _) in enumerate(snap):
                cost[i, j] = np.linalg.norm(com - c0) / max(np.cbrt(m0), 1e-12) + abs(m - m0) / max(m0, 1e-30)
        M.reg = {}
        for i, j in zip(*linear_sum_assignment(cost)):
            M.set_name(new[i], snap[j][2])
    finally:
        path.unlink(missing_ok=True)
        if not workdir:
            d.rmdir()


def apply(gmsh, edits: list, progress=None, workdir=None) -> list:
    """Runs the edits on the model in gmsh (solids, metres). Returns a report: one line per edit, then the bodies."""
    if len(edits) > MAX_EDITS:
        raise EditError(f"At most {MAX_EDITS} edits: remove or merge some")
    M = _Model(gmsh)
    M.sync()
    M.name_all()
    report = []
    for i, raw in enumerate(edits):
        op = validate(raw)
        if progress:
            progress(i, len(edits), op["op"])
        before = len(M.vols())
        try:
            OPS[op["op"]](M, op)
        except EditError as e:
            raise EditError(f"CAD edit {i + 1} ({op['op']}) failed: {e} The edit was not applied; the geometry is as it was.") from None
        except Exception as e:  # noqa: BLE001 - the CAD kernel
            raise EditError(f"CAD edit {i + 1} ({op['op']}) failed in the CAD kernel: {e}. The edit was not applied; "
                            "the geometry is as it was.") from None
        report.append(f"{i + 1}. {describe(op)}  ->  {before} -> {len(M.vols())} bodies")
    _rebuild_without_labels(M, workdir)
    for t in M.vols():
        bb = M.bbox_mm(t)
        vol = M.occ.getMass(3, t) / MM ** 3 / 1000.0
        report.append(f"body '{M.name(t)}': {bb[3] - bb[0]:.4g} x {bb[4] - bb[1]:.4g} x {bb[5] - bb[2]:.4g} mm, "
                      f"{vol:.4g} cm3, x {bb[0]:.4g}..{bb[3]:.4g}, y {bb[1]:.4g}..{bb[4]:.4g}, z {bb[2]:.4g}..{bb[5]:.4g} mm")
    return report


def edge_records(gmsh, face_kinds: dict, limit: int = 6000) -> list:
    """The edges of the model's own faces, for find_edges: {id, type, center_mm, length_mm, direction, faces}. face_kinds:
    {face tag: kind}; the faces of the air volume (farfield / enclosure) are left out."""
    M = _Model(gmsh)
    adj = {}
    for f, kind in face_kinds.items():
        if kind in ("farfield", "enclosure"):
            continue
        for _, e in gmsh.model.getBoundary([(2, f)], oriented=False):
            adj.setdefault(abs(e), []).append(int(f))
    out = []
    for e in sorted(adj)[:limit]:
        try:
            ei = M.edge_info(e)
        except Exception:  # noqa: BLE001
            continue
        out.append({"id": int(e), "type": ei["type"], "center_mm": [round(c / MM, 3) for c in ei["center"]],
                    "length_mm": round(ei["length"] / MM, 3), "direction": None if ei["dir"] is None else [round(c, 5) for c in ei["dir"]],
                    "faces": adj[e]})
    return out
