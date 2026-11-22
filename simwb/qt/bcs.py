"""What the Setup step hangs on the 3D model, for every kind of boundary condition (the web version's glyphs): the faces tinted in the
colour of their condition, a label with its name, type and value, and arrows - along a load or a flow, into or out of the faces for a
pressure, a heat flux or an outlet, outward for heat exchanged with the surroundings. Besides the conditions: the free stream and the far
field of an external flow, the open sides of a cooling study, the oncoming air, rotors and rotating zones (their axis), heat sources in
bodies, symmetry planes (the side kept) and gravity.

It is drawn through the 3D view's tint / label / arrow API only (Viewport.set_face_tints, set_labels, set_arrows); `marks()` computes the
lists from the project state, so it can be checked without a window."""
from __future__ import annotations

import math

from .forms import fmt_num

AXIS_VEC = {"+x": [1, 0, 0], "-x": [-1, 0, 0], "+y": [0, 1, 0], "-y": [0, -1, 0], "+z": [0, 0, 1], "-z": [0, 0, -1]}
ZONE_COLOUR, ROTOR_COLOUR, SYM_COLOUR, DRAFT_COLOUR, HEAT_COLOUR = "#9b6cff", "#ff8a00", "#4cc9f0", "#ffb703", "#fb5607"
EXCHANGE = ("convection", "radiation_convection", "wall_convection", "natural_convection")


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def bc_value(bc: dict, btype: dict) -> str:
    """The value a condition carries, short: '58.3 N', 'x 0, z -1 mm', '2 m/s, 25 °C', '60 °C'."""
    q = bc.get("params") or {}
    t = bc.get("type")
    unit = lambda v, u: f"{fmt_num(v)} {u}".strip()  # noqa: E731
    if t == "force" or any(k in q for k in ("fx_N", "fy_N", "fz_N")):
        return unit(math.sqrt(sum(_num(q.get(k)) ** 2 for k in ("fx_N", "fy_N", "fz_N"))), "N")
    if t == "displacement":
        parts = [f"{a} {fmt_num(q[f'u{a}_mm'])}" for a in "xyz" if q.get(f"u{a}_mm") not in (None, "")]
        return (", ".join(parts) + " mm") if parts else "held"
    if t == "velocity_inlet":
        v = unit(q["flow_rate"], "m³/h") if _num(q.get("flow_rate")) > 0 else unit(q.get("velocity", 0), "m/s")
        return v + (f", {unit(q['T'], '°C')}" if q.get("T") not in (None, "") else "")
    out = []
    for p in btype.get("params") or []:
        k = p["key"]
        if p.get("options") or q.get(k) in (None, "") or len(out) >= 2 or (out and not _num(q.get(k))):
            continue
        out.append(unit(q[k], p.get("unit", "")))
    return ", ".join(out)


def bc_label(bc: dict, btype: dict) -> str:
    """'Camera: 58.3 N', 'Bolts (Fixed support)': the name and the value; a condition without a value says its type instead (when its
    name does not)."""
    lab = btype.get("label", bc.get("type", ""))
    name = bc.get("name") or lab
    val = bc_value(bc, btype)
    if val:
        return f"{name}: {val}"
    return name if lab.lower() in name.lower() else f"{name} ({lab})"


def marks(st: dict, faces: list, center, size: float, active: str | None = None, draft: dict | None = None):
    """(tints {face: colour}, labels [{point, text, color}], arrows [{point (tip), dir, color, length}]) for the Setup step.
    center(ids) -> the middle of some faces or None (the 3D view's face_center); size: the parts' largest extent (m)."""
    spec = st["study_spec"]
    types = spec["face_bc_types"]
    fmap = {f["id"]: f for f in faces}
    tints, labels, arrows = {}, [], []
    L = size or 0.1

    def normal_arrows(ids, colour, sign):            # sign -1: into the faces (a load pushes in), +1: out of them
        for fid in list(ids)[:6]:
            f = fmap.get(fid)
            c = center([fid])
            if f and c is not None and f.get("normal"):
                n = [sign * x for x in f["normal"]]
                tip = c if sign < 0 else [c[k] + n[k] * 0.16 * L for k in range(3)]
                arrows.append({"point": tip, "dir": n, "color": colour, "length": 0.16 * L})

    for b in st.get("bcs") or []:
        bt = types.get(b["type"], {})
        col = bt.get("color", "#888888")
        ids = list(b.get("faces") or [])
        if b["id"] != active:
            for f in ids:
                tints[f] = col
        c = center(ids) if ids else None
        if c is None:
            continue
        labels.append({"point": c, "text": bc_label(b, bt), "color": col})
        q = b.get("params") or {}
        t = b["type"]
        d = None
        if t == "force":
            d = [_num(q.get(k)) for k in ("fx_N", "fy_N", "fz_N")]
        elif t == "displacement":
            d = [_num(q.get(f"u{a}_mm")) for a in "xyz"]
        elif t == "velocity_inlet" and q.get("direction") in AXIS_VEC:
            d = AXIS_VEC[q["direction"]]
        elif t == "moving_wall" and q.get("axis") in AXIS_VEC:
            d = AXIS_VEC[q["axis"]]
        if d and any(abs(x) > 0 for x in d):
            arrows.append({"point": c, "dir": d, "color": col, "length": 0.3 * L})
        elif t == "pressure" and _num(q.get("pressure_Pa")):
            normal_arrows(ids, col, -1 if _num(q.get("pressure_Pa")) > 0 else 1)
        elif t == "heat_flux" and _num(q.get("q")):
            normal_arrows(ids, col, -1 if _num(q.get("q")) > 0 else 1)
        elif t == "heat_load" and _num(q.get("Q")):
            normal_arrows(ids, col, -1 if _num(q.get("Q")) > 0 else 1)
        elif t == "velocity_inlet":
            normal_arrows(ids, col, -1)
        elif t == "pressure_outlet" or t in EXCHANGE:
            normal_arrows(ids, col, 1)

    info = st["geometry"].get("info") or {}
    pb = info.get("parts_bbox") or info.get("bbox")
    used = {f for b in st.get("bcs") or [] for f in b.get("faces") or []}
    free = [f for f in faces if f["id"] not in used and f.get("kind") in ("farfield", "enclosure")]
    s = st.get("settings") or {}
    if st["study"] == "external_aero":
        fd = st.get("flow_dir") or [1, 0, 0]
        bb = info.get("bbox") or pb
        if free and bb:
            ctr = [(bb[k] + bb[k + 3]) / 2 for k in range(3)]

            def role(f):
                if f.get("domain_role"):
                    return f["domain_role"]
                if abs(sum(f["normal"][k] * fd[k] for k in range(3))) < 0.7:
                    return "side"
                return "inlet" if sum((f["centroid"][k] - ctr[k]) * fd[k] for k in range(3)) < 0 else "outlet"
            for r, text, col in (("inlet", f"Free stream in: {fmt_num(s.get('speed'))} m/s", "#3a86ff"), ("outlet", "Free stream out", "#06d6a0"),
                                 ("side", "Far field (free stream)", "#9aa4b2")):
                ids = [f["id"] for f in free if role(f) == r]
                c = center(ids) if ids else None
                if c is not None:
                    labels.append({"point": c, "text": text, "color": col})
        if pb and math.sqrt(sum(x * x for x in fd)) > 1e-9:
            ctr = [(pb[k] + pb[k + 3]) / 2 for k in range(3)]
            r = 0.5 * math.sqrt(sum((pb[k + 3] - pb[k]) ** 2 for k in range(3)))
            tip = [ctr[k] - fd[k] * 1.05 * r for k in range(3)]
            arrows.append({"point": tip, "dir": fd, "color": "#3a86ff", "length": 0.5 * L})
            labels.append({"point": [tip[k] - fd[k] * 0.5 * L for k in range(3)],
                           "text": f"Air {fmt_num(s.get('speed'))} m/s · pitch {fmt_num(s.get('pitch_deg', 0))}° · sideslip {fmt_num(s.get('yaw_deg', 0))}°",
                           "color": "#3a86ff"})
    elif st["study"] == "cht_enclosure" and free:
        open_box = (st.get("domain") or {}).get("enclosure_mode") == "auto_box"
        nat = s.get("air_flow") == "natural"
        c = center([f["id"] for f in free])
        if c is not None:
            text = (f"Open to the room ({fmt_num(s.get('T_room'))} °C, air in / out)" if open_box and nat
                    else "Open sides (slip, adiabatic)" if open_box else "Walls (no-slip, adiabatic)")
            labels.append({"point": c, "text": text, "color": "#9aa4b2"})
    for z in st.get("moving_zones") or []:                  # a rotating zone: its axis at the centre, its name and speed at the rim
        a = AXIS_VEC.get(z["axis"], [0, 0, 1])
        perp = [0, 0, 1] if abs(a[2]) < 0.9 else [1, 0, 0]
        tip = [z["center"][k] + a[k] * (z["length"] / 2 + 0.5 * z["radius"]) for k in range(3)]
        arrows.append({"point": tip, "dir": a, "color": ZONE_COLOUR, "length": z["length"] / 2 + 0.5 * z["radius"]})
        labels.append({"point": [z["center"][k] + a[k] * z["length"] / 2 + perp[k] * z["radius"] for k in range(3)], "color": ZONE_COLOUR,
                       "text": f"{z['name']}: {fmt_num(z['rpm'])} rpm ({'sliding mesh' if z.get('method') == 'sliding' else 'MRF'})"})
    for i, r in enumerate(st.get("rotors") or []):          # a rotor disc: its thrust axis and its size
        a = r.get("axis") or [0, 0, 1]
        tip = [r["center"][k] + a[k] * r["radius"] for k in range(3)]
        arrows.append({"point": tip, "dir": a, "color": ROTOR_COLOUR, "length": r["radius"]})
        thr = "auto" if r.get("thrust_N") is None else f"{fmt_num(r['thrust_N'])} N"
        labels.append({"point": tip, "text": f"Rotor {i + 1}: Ø {fmt_num(2e3 * r['radius'])} mm, thrust {thr}", "color": ROTOR_COLOUR})
    for b in st.get("body_list") or []:
        if _num(b.get("heat_W")) > 0 and b.get("centroid"):
            labels.append({"point": b["centroid"], "text": f"{b['name']}: {fmt_num(b['heat_W'])} W", "color": HEAT_COLOUR})
    if pb:
        ctr = [(pb[k] + pb[k + 3]) / 2 for k in range(3)]
        planes = [dict(pl, draft=False) for pl in st.get("symmetry") or []]
        if draft:
            n = [0.0, 0.0, 0.0]
            a = "xyz".index(draft["axis"])
            n[a] = 1.0 if draft.get("keep", 1) > 0 else -1.0
            p = list(ctr)
            p[a] = draft["pos"]
            planes.append({"name": "New plane", "normal": n, "point": p, "draft": True})
        for pl in planes:                                    # a plane: an arrow from it into the side that is kept
            n = pl["normal"]
            dd = sum((ctr[k] - pl["point"][k]) * n[k] for k in range(3))
            on = [ctr[k] - dd * n[k] for k in range(3)]
            col = DRAFT_COLOUR if pl["draft"] else SYM_COLOUR
            arrows.append({"point": [on[k] + n[k] * 0.25 * L for k in range(3)], "dir": n, "color": col, "length": 0.25 * L})
            labels.append({"point": on, "text": f"{pl['name']}: keeps this side" if not pl["draft"] else "New plane (not set yet): keeps this side",
                           "color": col})
    g = s.get("gravity")
    if g in AXIS_VEC and pb:
        d = AXIS_VEC[g]
        ctr = [(pb[k] + pb[k + 3]) / 2 for k in range(3)]
        arrows.append({"point": ctr, "dir": d, "color": None, "length": 0.34 * L})
        labels.append({"point": [ctr[0], ctr[1], ctr[2] + (0.12 * L if g[1] != "z" else 0.0)], "text": "Gravity 9.81 m/s²", "color": None})
    return tints, labels, arrows
