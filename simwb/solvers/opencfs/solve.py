"""Harmonic vibro-acoustics: a structure driven by a harmonic load, radiating into an air volume, solved by openCFS.

Inputs are the workbench's own: the tetrahedral mesh of the solids AND of the surrounding air (conformal at the wet
surface: the workbench's enclosure meshing, as in the heat-transfer study), the body materials, and face boundary
conditions. Result: the sound pressure in the air and the structure's displacement amplitude at the frequency of the
strongest response, plus the whole frequency-response table and the response at monitor points.

Body roles (body_props[body id]):
    solid: {"E", "nu", "rho", "name"}                       (as for the structural solvers)
    fluid: {"fluid": True, "rho": 1.2, "c": 343.0, "name"}  (rho and c default to the settings air_rho / air_c)
Boundary conditions (bcs: list of {"name", "type", "faces": [CAD face ids], "params": {...}}):
    fixed_support    solid face: all displacements zero
    force            solid face: harmonic force, amplitude params fx_N, fy_N, fz_N (total, spread over the face)
    pressure         solid face: harmonic pressure params pressure_Pa, pushing into the face
    normal_velocity  fluid face: harmonic normal velocity params velocity_m_s (a vibrating wall / a duct inlet)
    absorbing        fluid face: radiation (first-order absorbing) boundary: sound leaves without reflection
    (a fluid boundary face with no condition is a rigid wall - or, with settings absorbing_boundary on, which is the
    default, absorbing, so that the air volume models open air)
Settings: f_min_hz, f_max_hz, n_freq, sampling ("linear"/"log"), damping_percent (structure damping ratio, % of
critical, 2 % default), air_c, air_rho, absorbing_boundary (True), monitors [{"name", "point": [x, y, z] m}],
timeout_min, threads.
"""
from __future__ import annotations

import math
import time
from pathlib import Path

import numpy as np

from . import api, installer, meshio, results, xmlwriter

P_REF = 20e-6  # Pa, the reference of the sound pressure level in air


def spl_db(amplitude_pa):
    """Sound pressure level (dB re 20 uPa) of a harmonic pressure of the given amplitude (rms = amplitude / sqrt 2)."""
    a = np.maximum(np.asarray(amplitude_pa, float), 1e-30)
    return 20.0 * np.log10(a / math.sqrt(2.0) / P_REF)


def _tri_areas(p, tri):
    return 0.5 * np.linalg.norm(np.cross(p[tri[:, 1]] - p[tri[:, 0]], p[tri[:, 2]] - p[tri[:, 0]]), axis=1)


def _mean_edge(p, tets):
    e = np.concatenate([np.linalg.norm(p[tets[:, a]] - p[tets[:, b]], axis=1)
                        for a, b in ((0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3))])
    return float(e.mean()), float(e.max())


def check_setup(mesh: dict, body_props: dict, bcs: list, settings: dict) -> list:
    """Problems that stop the study before any solving, as readable sentences (empty list = fine)."""
    out = []
    tv = np.asarray(mesh["tet_vol"])
    present = [int(b) for b in np.unique(tv)]
    fluid = [b for b in present if (body_props.get(b) or body_props.get(str(b)) or {}).get("fluid")]
    solid = [b for b in present if b not in fluid]
    if not fluid:
        out.append("The study needs an air (fluid) body around the structure: add an air enclosure body.")
    types = {b.get("type") for b in bcs}
    if not solid and "normal_velocity" not in types:
        out.append("The study needs at least one structural (solid) body that is driven.")
    if not types & {"force", "pressure", "normal_velocity"}:
        out.append("Add an excitation: a harmonic force or pressure on a face of the structure (or a normal "
                   "velocity on a face of the air).")
    try:
        fmin, fmax = float(settings.get("f_min_hz") or 0), float(settings.get("f_max_hz") or 0)
        if fmin <= 0 or fmax < fmin:
            out.append("Set the frequency range: lowest frequency above 0 and highest not below it.")
    except (TypeError, ValueError):
        out.append("The frequency range must be numbers.")
    return out


def solve_vibroacoustics(mesh: dict, body_props: dict, bcs: list, settings: dict, workdir, log=print,
                         on_proc=None, on_step=None) -> dict:
    t0 = time.time()
    s = settings or {}
    problems = check_setup(mesh, body_props, bcs, s)
    if problems:
        raise ValueError(problems[0])
    p = np.asarray(mesh["nodes"], float)
    tets = np.asarray(mesh["tets"], np.int64)
    tv = np.asarray(mesh["tet_vol"])
    air_rho = float(s.get("air_rho") or 1.2)
    air_c = float(s.get("air_c") or 343.0)

    def props(b):
        return body_props.get(b) or body_props.get(str(b)) or {}

    is_fluid = lambda b: bool(props(b).get("fluid"))  # noqa: E731
    bodies = [int(b) for b in np.unique(tv)]
    solid_ids = [b for b in bodies if not is_fluid(b)]
    fluid_ids = [b for b in bodies if is_fluid(b)]
    for b in solid_ids:
        if float(props(b).get("E") or 0) <= 0 or float(props(b).get("rho") or 0) <= 0:
            raise ValueError(f"Body {props(b).get('name', b)} has no material (stiffness, density) assigned.")
    warns: list = []

    if len(fluid_ids) > 1:
        raise ValueError("One air (fluid) body is supported; merge the air volumes into one body.")
    fs = meshio.face_sets(mesh, is_fluid)
    iface = fs["interface"]
    sb_tri, sb_surf = fs["solid_boundary"]
    fb_tri, fb_surf = fs["fluid_boundary"]
    log(f"mesh: {len(tets):,} tetrahedra ({int(sum((tv == b).sum() for b in fluid_ids)):,} in the air), "
        f"{len(iface):,} wet-surface triangles")
    if not len(iface) and solid_ids and fluid_ids:
        warns.append("The structure and the air share no surface in the mesh: the sound is not coupled to the "
                     "structure. Mesh the air body around (touching) the structure.")

    surfaces: dict = {}
    model_loads = {"fixed": [], "tractions": [], "pressures": [], "velocities": [], "absorbing": []}
    used_fluid = np.zeros(len(fb_tri), bool)
    wet_taken = np.zeros(len(iface), bool)  # wet-surface triangles that belong to a condition's own group
    wet_groups: list = []
    report_bcs = {}
    first_fluid = f"air_{fluid_ids[0]}"
    for k, bc in enumerate(bcs):
        kind = bc.get("type")
        faces = [int(x) for x in bc.get("faces", [])]
        prm = bc.get("params", {}) or {}
        nm = f"bc{k + 1}_{kind}"
        if kind in ("fixed_support", "force", "pressure"):
            sel = np.isin(sb_surf, faces)
            tri = sb_tri[sel]
            # faces that touch the air (every face of a part inside an air box does) are wet-surface faces: the
            # condition is applied to them too. They leave the plain coupling surface and are coupled as the group of
            # this condition (the coupling lists every group); the triangles are turned to point out of the solid, as
            # those of a free face do (the wet surface is oriented out of the air)
            wsel = np.isin(fs["interface_surf"], faces) & ~wet_taken
            if wsel.any():
                wet_taken |= wsel
                tri = np.vstack([tri, iface[wsel][:, [0, 2, 1]]]) if len(tri) else iface[wsel][:, [0, 2, 1]]
                wet_groups.append(nm)
            if not len(tri):
                warns.append(f"'{bc.get('name', nm)}' has no structure faces in the mesh (is it on the air box?): ignored.")
                continue
            area = float(_tri_areas(p, tri).sum())
            surfaces[nm] = tri
            report_bcs[bc.get("name", nm)] = {"type": kind, "area_m2": area}
            if kind == "fixed_support":
                model_loads["fixed"].append(nm)
            elif kind == "force":
                F = np.array([float(prm.get(q) or 0.0) for q in ("fx_N", "fy_N", "fz_N")])
                model_loads["tractions"].append((nm, tuple(F / area), 0.0))  # N/m^2: integrates to F over the face
                report_bcs[bc.get("name", nm)]["force_N"] = F.tolist()
            else:
                pr = float(prm.get("pressure_Pa") or 0.0)
                model_loads["pressures"].append((nm, pr, 0.0))
                report_bcs[bc.get("name", nm)]["pressure_Pa"] = pr
        elif kind in ("normal_velocity", "absorbing"):
            sel = np.isin(fb_surf, faces)
            tri = fb_tri[sel]
            if not len(tri):
                warns.append(f"'{bc.get('name', nm)}' has no air-side boundary faces in the mesh: ignored.")
                continue
            used_fluid |= sel
            surfaces[nm] = tri
            report_bcs[bc.get("name", nm)] = {"type": kind, "area_m2": float(_tri_areas(p, tri).sum())}
            if kind == "absorbing":
                model_loads["absorbing"].append((nm, first_fluid))
            else:
                v = float(prm.get("velocity_m_s") or 0.0)
                model_loads["velocities"].append((nm, v, 0.0))
                report_bcs[bc.get("name", nm)]["velocity_m_s"] = v
        elif kind in ("free", None):
            continue
        else:
            raise ValueError(f"Boundary condition type '{kind}' is not supported by the vibro-acoustics study.")
    if not (model_loads["tractions"] or model_loads["pressures"] or model_loads["velocities"]):
        # every excitation was dropped (e.g. selected on the faces of the air box instead of the part): the answer would
        # be silence, which must not look like a result
        raise ValueError("No excitation reaches the model: " + (" ".join(warns) if warns else "no harmonic force, pressure "
                         "or surface velocity is set") + " Select the force or pressure on a free face of the part "
                         "(not on the air box or on the surface the part shares with the air).")
    if s.get("absorbing_boundary", True) in (True, 1, "1", "true", "yes", "on"):
        rest = fb_tri[~used_fluid]
        if len(rest):
            surfaces["radiation"] = rest
            model_loads["absorbing"].append(("radiation", first_fluid))
            report_bcs["open air boundary"] = {"type": "absorbing", "area_m2": float(_tri_areas(p, rest).sum())}
    couple = []  # the surface groups that couple the structure to the air: the plain wet surface and the groups of conditions
    if (~wet_taken).any():
        surfaces["couple"] = iface[~wet_taken]
        couple.append("couple")
    couple += wet_groups

    # ----- files
    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)
    volumes = {f"solid_{b}": tets[tv == b] for b in solid_ids}
    volumes.update({f"air_{b}": tets[tv == b] for b in fluid_ids})
    wr = meshio.write_msh(work / "model.msh", p, volumes, surfaces)
    used = wr["used"]
    solids = {f"mat_{b}": {"E": float(props(b)["E"]), "nu": float(props(b)["nu"]), "rho": float(props(b)["rho"])}
              for b in solid_ids}
    fluids = {f"gas_{b}": {"rho": float(props(b).get("rho") or air_rho), "c": float(props(b).get("c") or air_c)}
              for b in fluid_ids}
    f_min, f_max = float(s["f_min_hz"]), float(s["f_max_hz"])
    freqs = xmlwriter.frequencies(f_min, f_max, int(s.get("n_freq") or 20), str(s.get("sampling") or "linear"))
    zeta = float(s.get("damping_percent") if s.get("damping_percent") not in (None, "") else 2.0) / 100.0
    f_c = math.sqrt(f_min * f_max)
    (work / "materials.xml").write_text(xmlwriter.material_xml(solids, fluids, 2.0 * zeta, f_c), newline="\n")
    model = {"mesh": "model.msh", "material_file": "materials.xml",
             "solid_regions": [(f"solid_{b}", f"mat_{b}") for b in solid_ids],
             "fluid_regions": [(f"air_{b}", f"gas_{b}") for b in fluid_ids],
             "surface_regions": list(surfaces), "frequencies": freqs,
             "coupling": couple if len(iface) and solid_ids and fluid_ids else None,
             "fixed": model_loads["fixed"], "tractions": model_loads["tractions"],
             "pressures": model_loads["pressures"], "absorbing": model_loads["absorbing"],
             "velocities": model_loads["velocities"]}
    (work / "model.xml").write_text(xmlwriter.simulation_xml(model), newline="\n")

    # ----- mesh resolution for the sound
    fl_tets = np.vstack([tets[tv == b] for b in fluid_ids])
    h_mean, h_max = _mean_edge(p, fl_tets)
    lam_min = air_c / f_max
    n_per_wave = lam_min / h_mean
    if n_per_wave < 6:
        warns.append(f"The air mesh is too coarse for {f_max:g} Hz: about {n_per_wave:.1f} elements per wavelength "
                     "(at least 6-10 are needed with linear elements). Refine the mesh or lower the highest "
                     "frequency.")
    n_dof = int(len(used) + 3 * len(np.unique(np.concatenate([tets[tv == b].ravel() for b in solid_ids])))
                if solid_ids else len(used))
    if n_dof > 400_000:
        warns.append(f"About {n_dof:,} unknowns: openCFS' direct solver may need a lot of memory and time.")

    # ----- run
    out = api.run_cfs(work, "model.xml", "model.msh", on_proc=on_proc, log=log, on_step=on_step,
                      threads=s.get("threads"), timeout=float(s.get("timeout_min") or 60) * 60)
    blocks = results.read_nodedata(out["results_gmsh"])
    nw = len(used)
    fr, pw = results.amplitudes(blocks, "acouPressure", nw)
    fr2, uw = results.amplitudes(blocks, "mechDisplacement", nw)
    if not len(fr) or not pw.any() and not uw.any():
        raise RuntimeError("openCFS wrote no results.")
    nf = len(fr)
    pa = np.zeros((nf, len(p)))
    ua = np.zeros((nf, len(p)))
    pa[:, used] = pw
    ua[:, used] = uw
    fluid_nodes = np.unique(fl_tets.ravel())
    solid_nodes = np.unique(np.concatenate([tets[tv == b].ravel() for b in solid_ids])) if solid_ids else \
        np.zeros(0, np.int64)
    pmax = pa[:, fluid_nodes].max(1)
    prms = np.sqrt((pa[:, fluid_nodes] ** 2).mean(1) / 2.0)
    umax = ua[:, solid_nodes].max(1) if len(solid_nodes) else np.zeros(nf)
    ipk = int(np.argmax(pmax))
    table = [{"frequency_Hz": float(fr[i]), "max_pressure_Pa": float(pmax[i]), "max_spl_dB": float(spl_db(pmax[i])),
              "mean_spl_dB": float(spl_db(prms[i] * math.sqrt(2.0))),
              "max_displacement_mm": float(umax[i] * 1e3)} for i in range(nf)]
    monitors = []
    for m in s.get("monitors") or []:
        q = np.asarray(m["point"], float)
        jf = int(fluid_nodes[np.argmin(np.linalg.norm(p[fluid_nodes] - q, axis=1))])
        mon = {"name": m.get("name") or "monitor", "point_m": q.tolist(), "air_node_m": p[jf].tolist(),
               "pressure_Pa": pa[:, jf].tolist(), "spl_dB": spl_db(pa[:, jf]).tolist()}
        if len(solid_nodes):
            js = int(solid_nodes[np.argmin(np.linalg.norm(p[solid_nodes] - q, axis=1))])
            mon.update(structure_node_m=p[js].tolist(), displacement_mm=(ua[:, js] * 1e3).tolist())
        monitors.append(mon)
    if nf > 1 and (ipk == 0 or ipk == nf - 1):
        warns.append("The strongest response is at the edge of the frequency range: widen the range to see the "
                     "whole peak.")
    peak_wave = air_c / max(fr[ipk], 1e-9)
    info = installer.detect()
    report = {
        "solver": "openCFS " + (info.get("version") or ""),
        "frequencies_Hz": fr.tolist(), "peak_frequency_Hz": float(fr[ipk]),
        "peak_pressure_Pa": float(pmax[ipk]), "peak_spl_dB": float(spl_db(pmax[ipk])),
        "peak_displacement_mm": float(umax[ipk] * 1e3), "response": table, "monitors": monitors,
        "boundary_conditions": report_bcs, "n_frequencies": nf, "n_elements": int(len(tets)), "n_dof": n_dof,
        "elements_per_wavelength": float(n_per_wave), "air_mesh_edge_mm": h_mean * 1e3,
        "wavelength_at_peak_m": peak_wave, "damping_percent": zeta * 100.0,
        "damping_centre_Hz": f_c, "air": {"rho": air_rho, "c": air_c},
        "solve_time_s": time.time() - t0, "warnings": warns,
        "assumptions": [
            "Linear elastic structure and linear (small-signal) acoustics in still air; harmonic (steady-state) "
            "response, amplitudes of the pressure and displacement at each frequency",
            f"Structural damping: Rayleigh damping giving a loss factor {2 * zeta:.3g} (damping ratio "
            f"{zeta * 100:.3g} %) at {f_c:.0f} Hz, about the same across the band; the air itself is lossless",
            "Fluid-structure coupling at the shared surface (pressure acts on the structure, the structure's motion "
            "drives the air); first-order tetrahedra",
            "The open boundary of the air volume is a first-order absorbing (radiation) condition: it is accurate "
            "when the boundary is a wavelength or more from the structure, and reflects a little otherwise",
            "Solved by openCFS (separate program, MIT licence) with a direct solver",
        ],
    }
    fields = {"Sound pressure amplitude": (pa[ipk].astype(np.float32), "Pa"),
              "Displacement amplitude": ((ua[ipk] * 1e3).astype(np.float32), "mm")}
    return {"fields": fields, "report": report,
            "sweep": {"frequencies_Hz": fr, "pressure_Pa": pa.astype(np.float32),
                      "displacement_mm": (ua * 1e3).astype(np.float32)}}
