"""Flutter study on a meshed structure: built-in modal FEM + strip aerodynamics (simwb.solvers.flutter).

The study layer: turns the mesh, materials, boundary conditions and settings of a project into the modal model and
the strips, runs the speed sweep and writes the report the GUI shows (flutter speed, divergence speed, margin,
V-frequency and V-damping curves, warnings, assumptions) plus the displacement fields for the 3D view.
"""
from __future__ import annotations

import math
import time

import numpy as np

from . import flutter as F

AXES = {"+x": (1, 0, 0), "-x": (-1, 0, 0), "+y": (0, 1, 0), "-y": (0, -1, 0), "+z": (0, 0, 1), "-z": (0, 0, -1)}
MACH_LIMIT = 0.6  # the incompressible aerodynamics are used up to here
MARGIN_REQUIRED = 1.15  # flutter speed / design speed asked of aircraft structures (CS-25.629, FAR 25.629: 1.15 V_D)


def solve_study(mesh, body_props, bcs, settings, log=print):
    """mesh, body_props, bcs as for modal_fem.solve plus the BC type "aero_surface"; settings: the study settings.

    Returns {"report", "fields", "modes", "shapes"}; fields: name -> (nodes, 3) displacement relative to its maximum.
    """
    from . import modal_fem

    t0 = time.time()
    s = settings
    flow = np.array(AXES[str(s.get("flow_direction") or "+x")], float)
    span_name = str(s.get("span_axis") or "y")
    span = np.eye(3)["xyz".index(span_name)]
    if abs(float(flow @ span)) > 0.999:
        raise ValueError("The air flows along the span direction: choose a span axis across the flow "
                         f"(flow {s.get('flow_direction')}, span {span_name})")
    if not any(b["type"] == "fixed_support" and b["faces"] for b in bcs):
        raise ValueError("Add a fixed support at the root of the wing (the face that is clamped or bolted): flutter "
                         "of a free structure is not covered here")
    structural = [b for b in bcs if b["type"] != "aero_surface"]
    aero_faces = sorted({int(f) for b in bcs if b["type"] == "aero_surface" for f in b["faces"]})
    zeta = float(s.get("damping_percent") or 0.0) / 100.0
    n_modes = int(s.get("n_modes") or 8)

    log("Structure: natural modes")
    res = modal_fem.solve(mesh, body_props, structural, n_modes=n_modes, log=log)
    elastic = [(m, sh) for m, sh in zip(res["modes"], res["shapes"]) if not m["rigid_body"]]
    if len(elastic) < 2:
        raise ValueError("Fewer than two vibration modes were found: request more modes")
    modes = [m for m, _ in elastic]
    shapes = np.array([sh for _, sh in elastic])  # (modes, nodes, 3), largest displacement 1
    mg = np.array([m["generalized_mass_kg"] for m in modes])
    om = 2 * math.pi * np.array([m["frequency_Hz"] for m in modes])
    M, K = np.diag(mg), np.diag(mg * om ** 2)
    Cs = np.diag(2 * zeta * om * mg)

    nodes = np.asarray(mesh["nodes"], float)
    tris, ts = mesh["tris"].astype(np.int64), mesh["tri_surf"]
    sel = np.isin(ts, aero_faces) if aero_faces else np.ones(len(tris), bool)
    if not sel.any():
        raise ValueError("The aerodynamic surface has no mesh faces: mesh the study again")
    aero_nodes = np.unique(tris[sel])
    st = F.build_strips(nodes, aero_nodes, shapes, flow, span, int(s.get("n_strips") or 16),
                        finite_wing=str(s.get("finite_wing", "on")) != "off")
    log(f"Air: {len(st['b'])} strips, span {st['span_m'] * 1e3:.0f} mm, aspect ratio {st['aspect_ratio']:.2f}")

    atm = F.isa(float(s.get("altitude_m") or 0.0))
    rho, a_snd = atm["rho"], atm["a"]
    v_user = float(s.get("speed_max_ms") or 0.0)
    v_max = v_user if v_user > 0 else MACH_LIMIT * a_snd
    n_v = int(s.get("n_speeds") or 100)
    grid = np.linspace(v_max / n_v, v_max, n_v)
    log(f"Speed sweep up to {v_max:.0f} m/s (Mach {v_max / a_snd:.2f}), air density {rho:.3f} kg/m3")
    out = F.solve(M, K, st, rho, grid, Cs)
    P, ev, sysm = out["P"], out["events"], out["system"]

    fl, dv = ev["flutter"], ev["divergence"]
    inside = [e for e in (fl, dv) if e is not None and e["V"] <= v_max]
    crit = min(inside, key=lambda e: e["V"], default=None)
    kind = None if crit is None else ("flutter" if crit is fl else "divergence")
    warns = []
    report = {
        "modes": modes, "n_modes": len(modes), "first_frequency_Hz": modes[0]["frequency_Hz"],
        "mass_kg": res["report"]["mass_kg"], "structure_mass_kg": res["report"]["structure_mass_kg"],
        "unknowns": res["report"].get("unknowns"),
        "air": {"altitude_m": float(s.get("altitude_m") or 0.0), "density_kg_m3": rho, "speed_of_sound_m_s": a_snd,
                "temperature_K": atm["T_K"]},
        "strips": {"count": len(st["b"]), "span_m": st["span_m"], "area_m2": st["area_m2"],
                   "aspect_ratio": st["aspect_ratio"], "lift_factor": float(st["fac"]),
                   "mean_chord_m": float(2 * st["b"].mean()), "span_axis": span_name,
                   "flow_direction": str(s.get("flow_direction") or "+x")},
        "speed_range_m_s": [float(grid[0]), float(grid[-1])], "damping_percent": zeta * 100,
        "boundaries": res["report"].get("boundaries", {}),
    }
    if aero_faces:
        report["aero_faces"] = aero_faces
    else:
        warns.append("No aerodynamic surface was marked: every face of the structure carries air loads. Mark the "
                     "wing skin faces as 'Aerodynamic surface' (root blocks and pods are not wings).")

    # damping ratio of a root: -Re p / |p| (a real root: -1 decaying ... +1 growing)
    zr = -P.real / np.maximum(abs(P), 1e-300)
    dom = [int(np.argmax(abs(out["Q"][0, :, r]))) + 1 for r in range(len(modes))]  # the mode each root starts as
    report["sweep"] = {
        "speed_m_s": [float(x) for x in grid],
        "roots": [{"label": f"Mode {dom[r]}",
                   "frequency_Hz": [float(x) for x in P[:, r].imag.clip(min=0) / (2 * math.pi)],
                   "damping_percent": [float(x) for x in 100 * zr[:, r].clip(-1, 1)]} for r in range(len(modes))]}

    fields = {}
    report["flutter"] = None
    if fl is not None and fl["V"] <= v_max:
        i0 = max(int(np.searchsorted(grid, fl["V"])) - 1, 0)
        _, q_f = sysm.follow(fl["V"] * 1.0005, out["states"][i0][fl["root"]])
        q_f = q_f / q_f[int(np.argmax(abs(q_f)))]
        rel = np.abs(q_f)
        top = np.argsort(-rel)[:3]
        report["flutter"] = {
            "speed_m_s": fl["V"], "frequency_Hz": fl["omega"] / (2 * math.pi), "mach": fl["V"] / a_snd,
            "equivalent_airspeed_m_s": fl["V"] * math.sqrt(rho / 1.225),
            "mode_content": [{"mode": int(i) + 1, "share_percent": float(100 * rel[i] / rel.sum()),
                              "phase_deg": float(np.degrees(np.angle(q_f[i])))} for i in top]}
        u = np.einsum("m,mnk->nk", q_f, shapes.astype(complex))
        best = max(np.linspace(0, math.pi, 36, endpoint=False),
                   key=lambda t: float(np.abs((np.exp(1j * t) * u).real).max()))  # phase of the largest movement
        uf = (np.exp(1j * best) * u).real
        fields["Flutter mode"] = uf / max(float(np.linalg.norm(uf, axis=1).max()), 1e-30)
    report["divergence"] = None
    if dv is not None and dv["V"] <= v_max:
        report["divergence"] = {"speed_m_s": dv["V"], "mach": dv["V"] / a_snd}
        ud = np.einsum("m,mnk->nk", sysm.divergence()[1], shapes)
        fields["Divergence shape"] = ud / max(float(np.linalg.norm(ud, axis=1).max()), 1e-30)
    vd_any = sysm.divergence_speed()
    report["divergence_speed_any_m_s"] = vd_any  # (also above the examined range)
    report["critical"] = None if crit is None else {
        "type": kind, "speed_m_s": crit["V"], "mach": crit["V"] / a_snd,
        "equivalent_airspeed_m_s": crit["V"] * math.sqrt(rho / 1.225)}

    vdes = float(s.get("design_speed_ms") or 0.0)
    report["margin"] = None
    if crit is not None and vdes > 0:
        ratio = crit["V"] / vdes
        report["margin"] = {"design_speed_m_s": vdes, "ratio": ratio, "required": MARGIN_REQUIRED}
        if ratio < MARGIN_REQUIRED:
            warns.append(f"Flutter margin {ratio:.2f} is below {MARGIN_REQUIRED} (the usual requirement for aircraft: "
                         f"the {kind} speed at least 15 % above the design speed {vdes:g} m/s).")
    if crit is None:
        warns.append(f"No flutter or divergence up to {v_max:.0f} m/s (Mach {v_max / a_snd:.2f}). Raise 'Highest air "
                     "speed' to look further; above about Mach 0.6 the aerodynamics used here lose accuracy."
                     + (f" The structure diverges statically at {vd_any:.0f} m/s." if vd_any else ""))
    elif crit["V"] > MACH_LIMIT * a_snd:
        warns.append(f"The {kind} speed {crit['V']:.0f} m/s is above Mach {MACH_LIMIT}: compressibility is not "
                     "modelled, so the number is indicative only (transonic flutter is usually lower).")
    if fl is not None and dv is not None and dv["V"] < fl["V"]:
        warns.append(f"Divergence ({dv['V']:.1f} m/s) comes before flutter ({fl['V']:.1f} m/s): the flutter speed "
                     "above it is of no use.")
    if len(modes) < 4 or modes[-1]["frequency_Hz"] < 3 * modes[0]["frequency_Hz"]:
        warns.append("The modes computed cover a narrow frequency range: flutter needs both a bending and a torsion "
                     "mode - add modes if the first torsion mode is not among them.")
    if st["aspect_ratio"] < 2.0:
        warns.append(f"Aspect ratio {st['aspect_ratio']:.1f} (whole wing): a stubby plate is not slender enough for strip theory; "
                     "treat the result as an estimate.")
    if any(b["type"] == "symmetry" and b["faces"] for b in bcs):
        warns.append("Mirror plane: only symmetric flutter motions are found (both half wings moving alike).")
    report["warnings"] = warns
    report["assumptions"] = [
        "Structure: linear elastic modes of the built-in FEM (isotropic, rigid fixed supports, no pre-stress); only "
        "the computed modes take part, so too few modes make the structure artificially stiff",
        "Air: unsteady thin-airfoil theory of Theodorsen on strips across the span (incompressible, attached flow, no "
        "stall, no thickness or camber effects); each strip sees the heave and pitch fitted to the mode shapes over "
        "the aerodynamic faces; sweep is not modelled (chords are measured along the flow)",
        (f"Finite-wing correction: the lift of every strip is multiplied by AR/(AR+2) = {st['fac']:.2f}, AR = {st['aspect_ratio']:.1f} of the whole wing (the root is a mirror: fuselage or wall)"
         if st["fac"] != 1.0 else "No finite-wing correction (pure 2-D strip theory: conservative)"),
        "Flutter by the p-k method (Hassig 1971): damping and frequency of every mode against air speed; flutter is "
        "the lowest speed at which a mode with a frequency stops decaying, divergence the lowest speed at which the "
        "steady air forces overcome the stiffness",
        f"Standard atmosphere at {report['air']['altitude_m']:g} m: density {rho:.3f} kg/m³; structural damping "
        f"{zeta * 100:g} % of critical in every mode",
    ]
    report["seconds"] = round(time.time() - t0, 2)
    log(f"Solved in {report['seconds']} s: " + (f"{kind} at {crit['V']:.1f} m/s" if crit else "stable in the range"))
    return {"report": report, "fields": fields, "modes": res["modes"], "shapes": res["shapes"]}
