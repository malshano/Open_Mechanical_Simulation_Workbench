"""Write the markdown reports of the first-principles checks from the raw numbers of validation_modal.py and
validation_heat.py (JSON files).

    python examples/showcase/validation_reports.py <report_dir> <modal_raw.json> <heat_raw.json>
"""
import json
import sys
from pathlib import Path

out_dir, modal_raw, heat_raw = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
sys.path.insert(0, str(Path(__file__).parents[2]))
from simwb import materials  # noqa: E402

M = materials.get_solid("aluminium_6061")
QV = "q'''"  # volumetric heat generation symbol


def pct(a, b):
    return 100 * (a - b) / b


def sg(x, p=4):
    return f"{x:.{p}g}"


def modal_report():
    d = json.load(open(modal_raw))
    h, ht = d["hand"], d["hand_timoshenko"]
    c = d["cantilever_conv"]
    fine = c[-1]
    fam = fine["fam"]
    cal = d["calculix"]
    fr = d["free"]
    L = []
    w = L.append
    w("# First-principles check: natural frequencies of a beam (vibration_modal study)\n")
    w("Compared with beam theory: Euler-Bernoulli (EB), and Timoshenko beam theory (shear deformation + rotary inertia, "
      "1-D finite-element solution, 600 elements) to separate what is a modelling difference from what is a mesh error.\n")
    w("## Inputs (as the workbench uses them)\n")
    w(f"- Material `aluminium_6061` from the library: E = {M['E'] / 1e9:g} GPa, rho = {M['rho']:g} kg/m3, nu = {M['nu']}"
      f"  (G = E / 2(1+nu) = {M['E'] / 2 / (1 + M['nu']) / 1e9:.2f} GPa, Timoshenko shear coefficient 10(1+nu)/(12+11nu) = "
      f"{10 * (1 + M['nu']) / (12 + 11 * M['nu']):.3f})")
    w("- Cantilever: 200 x 20 x 3 mm (L x b x t), the end face at x = 0 a *Fixed support*, nothing else. "
      "STEP made by `examples/showcase/make_validation.py` (`validation_cantilever.step`).")
    w("- A = b t = 60 mm2; I(thickness direction, weak axis) = b t^3/12 = 45 mm4; I(width direction) = t b^3/12 = 2000 mm4.")
    w(f"- Study (built-in solver): `{d['cantilever_pid']}`, in `~/SimWB-Showcase/validation-studies/`. "
      f"Mesh 1.0 mm (global), {fine['cells']:,} cells, {fine['unknowns']:,} unknowns, quadratic tetrahedra.\n")
    w("## 1. Cantilever bending modes\n")
    w("f_n = (beta_n L)^2 / (2 pi) * sqrt(E I / (rho A L^4)),  beta_n L = 1.8751, 4.6941, 7.8548 (the roots of "
      "cos(bL) cosh(bL) = -1 to 7 digits are used in the script)\n")
    w("| Mode | Formula result EB [Hz] | Timoshenko [Hz] | Workbench [Hz] (1.0 mm mesh) | error vs EB | error vs Timoshenko |")
    w("|---|---|---|---|---|---|")
    for key, name in (("z", "thickness direction (weak axis)"), ("y", "width direction (in plane)")):
        for i in range(3):
            wb = fam[key][i]
            w(f"| {name} bending {i + 1} | {sg(h[key][i])} | {sg(ht[key][i])} | {sg(wb)} | {pct(wb, h[key][i]):+.2f} % | "
              f"{pct(wb, ht[key][i]):+.2f} % |")
    w("\nThe workbench lists the modes by frequency (mode 1 = 61.6 Hz, mode 3 = 405.6 Hz ...); the families were "
      "separated with the effective-mass direction it reports (z-bending: mass in z, y-bending: mass in y).\n")
    w("Other modes found by the workbench against hand formulas (cantilever):\n")
    w("| Mode | Formula | Hand [Hz] | Workbench [Hz] | error |")
    w("|---|---|---|---|---|")
    w(f"| 1st axial | f = sqrt(E/rho) / (4 L) = {(M['E'] / M['rho']) ** .5:.1f} m/s / 0.8 m | {sg(h['axial'])} | {sg(fam['x'][0])} | "
      f"{pct(fam['x'][0], h['axial']):+.2f} % |")
    w(f"| 1st torsion | f = sqrt(G J / (rho I_p)) / (4 L), St Venant J of a 20 x 3 rectangle, free warping | {sg(h['torsion'])} | "
      f"{sg(fam['other'][0])} | {pct(fam['other'][0], h['torsion']):+.2f} % |")
    w("\n## 2. Mesh convergence (built-in solver)\n")
    w("| Global mesh size | cells | unknowns | f1 z-bend [Hz] | f2 z-bend | f3 z-bend | f1 y-bend | axial | solve time |")
    w("|---|---|---|---|---|---|---|---|---|")
    for r in c:
        f = r["fam"]
        w(f"| {r['size_mm']} mm | {r['cells']:,} | {r['unknowns']:,} | {f['z'][0]:.2f} | {f['z'][1]:.1f} | {f['z'][2]:.1f} | "
          f"{f['y'][0]:.1f} | {f['x'][0]:.0f} | {r['seconds']:.0f} s |")
    w("\nThe quadratic elements are converged to better than 0.1 % already at 1.5 mm (2 elements through the 3 mm "
      "thickness) and the 3 mm mesh (1 element through the thickness) is within 0.5 %. The residual difference to "
      "beam theory is therefore not a mesh error.\n")
    w("## 3. Cross-check with CalculiX (same mesh, independent solver code)\n")
    wbm = d.get("builtin_full") or [m["frequency_Hz"] for m in fine["modes"]]  # full precision (the tool output has 4 digits)
    if "freqs" in cal:
        w(f"Study `{cal['pid']}` (copy of the study, setting Solver = calculix, {cal['cells']:,} cells, "
          f"{cal['seconds']:.0f} s against {fine['seconds']:.0f} s for the built-in solver). Mode by mode:\n")
        w("| Mode | built-in [Hz] | CalculiX [Hz] | difference |")
        w("|---|---|---|---|")
        cf = d.get("calculix_full") or cal["freqs"]
        for i, (a, b) in enumerate(zip(wbm, cf)):
            w(f"| {i + 1} | {a:.3f} | {b:.3f} | {pct(b, a):+.4f} % |")
        w(f"\nThe two solvers agree to {max(abs(pct(b, a)) for a, b in zip(wbm, cf)):.4f} % or better on all {len(cf)} modes "
          "(full-precision values from the stored results; the assistant tools print only 4 digits).\n")
    else:
        w(f"CalculiX failed: {cal}\n")
    w("## 4. Free-free square bar (200 x 10 x 10 mm)\n")
    w("Bending: f_n = (beta_n L)^2 / (2 pi) sqrt(E I/(rho A L^4)), beta_n L = 4.7300, 7.8532, 10.9956, I = s^4/12 = 833.3 mm4, "
      "A = 100 mm2. Axial: f = sqrt(E/rho) / (2 L). Every bending frequency is a degenerate pair (the section is square), "
      "which the solver reproduces.\n")
    w("| Mode | EB [Hz] | Timoshenko [Hz] | Workbench solver [Hz] | error vs EB | error vs Timoshenko |")
    w("|---|---|---|---|---|---|")
    pairs = [fr["freqs"][0], fr["freqs"][2], fr["freqs"][4]]
    for i in range(3):
        w(f"| bending {i + 1} (pair) | {sg(fr['hand_eb'][i])} | {sg(fr['hand_timo'][i])} | {sg(pairs[i])} | "
          f"{pct(pairs[i], fr['hand_eb'][i]):+.2f} % | {pct(pairs[i], fr['hand_timo'][i]):+.2f} % |")
    ax = [f for f, u in zip(fr["freqs"], fr["ux_share"]) if u > 0.9]
    w(f"| 1st axial | {sg(fr['hand_axial'])} | - | {sg(ax[0])} | {pct(ax[0], fr['hand_axial']):+.2f} % | - |")
    w(f"\nThe rigid-body modes are separated ({fr['n_rigid']} found, as a free body must have). "
      f"The study `{fr['pid']}` ({fr['cells']:,} cells, 2.5 mm) exists in the folder but its **Solve step fails in the workbench** "
      "(see the problem below), so this table was computed by calling the workbench's own modal solver "
      "(`simwb.solvers.modal_fem.solve`) in a script on the study's mesh with the PARDISO factorisation switched off. "
      f"Its eigen-solution residual is {fr['residual']:.1e}. The study in the folder carries no results.\n")
    w("## Why the numbers differ from the formulas (honest explanation)\n")
    w("- **Shear deformation and rotary inertia** (not in Euler-Bernoulli) lower the frequencies, more for higher modes "
      "and for short or thick beams. For the in-plane (width) direction of the cantilever (b/L = 0.1) this is -0.6 / -4.9 / "
      "-10.6 %: the workbench matches the Timoshenko result to +0.2 / +0.25 / +0.3 %, which is the correct physics. "
      "For the free-free bar (s/L = 0.05) the effect is -0.9 / -2.5 / -4.6 % and the workbench matches Timoshenko to 0.01-0.08 %.")
    w("- **Weak-axis bending of the cantilever** (thickness 3 mm, width 20 mm): the workbench is 0.5-0.7 % *above* both "
      "EB and Timoshenko. The mesh is converged and CalculiX gives the same, so it is the 3-D continuum itself. A wide "
      "plate strip is slightly stiffer than a beam because the Poisson contraction across the width is restrained "
      "(the stiffness tends towards E/(1-nu^2), +2.9 % in frequency, for a very wide strip; b/t = 6.7 gives a part of it). "
      "I did not isolate this with a separate run, so it is the expected cause, not a proven one.")
    w("- **Axial (cantilever)**: +0.2 %. The clamped end restrains the lateral (Poisson) contraction, a small 3-D effect not in the "
      "1-D formula. Free-free axial (no clamp) agrees to -0.02 %.")
    w("- **Torsion** +2.5 %: the formula assumes free warping; the clamped end prevents warping and stiffens the bar.")
    w("- The differences are the 3-D model being right where the beam formulas are idealised, not a bad solve: "
      "the mesh study and the independent CalculiX result both support that.")
    w("\n## Problem found in the workbench\n")
    w("**Free-free modal studies fail the residual check with PARDISO** (`simwb/solvers/modal_fem.py`, `_shift_invert`): "
      "a free homogeneous aluminium bar (2.5 mm mesh, 44k unknowns, also a free 200 x 20 x 3 mm beam) stops with "
      "\"The eigen-solution is inaccurate (relative residual 1e-2...6e-2) ...\". The frequencies it had found were right. "
      "Switching the factorisation to SuperLU (`modal_fem._pardiso = lambda: None`) brings the relative residual to "
      "3e-7 on the same mesh, so the cause is the inaccurate PARDISO solve (mtype=2, no iterative refinement) of the nearly "
      "singular K - sigma M of a free body. Suggested fix: for a free part (no fixed supports) use SuperLU, or enable "
      "PARDISO's iterative refinement (`ps.set_iparm(8, 2)`) / retry with SuperLU when the residual check fails. "
      "Note `MAX_DOF_SUPERLU = 60000` then limits free-free models.")
    (out_dir / "modal_beam_validation.md").write_text("\n".join(L) + "\n", encoding="utf-8")


def heat_report():
    d = json.load(open(heat_raw))
    a, b, c = d["a"], d["b"], d["c"]
    L = []
    w = L.append
    w("# First-principles check: steady heat conduction in a slab (heat_conduction study)\n")
    w(f"Geometry: 100 x 20 x 20 mm bar, x = length (`validation_slab.step`, made by `examples/showcase/make_validation.py`). "
      f"Material `aluminium_6061`, k = {d['k']:g} W/mK from the library. Side faces are left unassigned (= insulated, the "
      f"default), so the problem is one-dimensional. L = 0.1 m, A = 4e-4 m2. Mesh: global 4 mm tetrahedra (about 3500 cells, 1000 nodes); "
      "built-in solver, steady.\n")
    w("Sign convention of the workbench: `heat_out_W` is the heat leaving the part through the faces; heat entering is negative.\n")
    w(f"## (a) Both end faces at fixed temperature (100 degC and 20 degC) - study `{a['pid']}`\n")
    w("| Quantity | Hand calculation | Workbench | error |")
    w("|---|---|---|---|")
    w(f"| Heat flow Q | Q = k A dT / L = {d['k']:g} x 4e-4 x 80 / 0.1 = {a['Q_hand']:.3f} W | {-a['Q_hot_leaving_body_W']:.3f} W in at hot end, "
      f"{a['Q_cold_leaving_body_W']:.3f} W out at cold end | {pct(a['Q_cold_leaving_body_W'], a['Q_hand']):+.4f} % |")
    w(f"| Mean temperature | 60 degC (linear profile) | volume mean {a['T_mean']:.2f} degC | {a['T_mean'] - 60:+.3f} K |")
    w(f"| Profile T(x) = 100 - 800 x [x in m], all {a['profile']['n']} mesh nodes | linear | max deviation {a['profile']['max_abs_K']:.1e} K, "
      f"rms {a['profile']['rms_K']:.1e} K | ~0 (the linear tetrahedron reproduces a linear field exactly) |")
    w(f"| Energy balance | in = out | imbalance {a['energy']['imbalance_percent']} % | - |\n")
    w(f"## (b) Fixed temperature (100 degC) at one end, convection (h = {b['h']:g} W/m2K, T_inf = 20 degC) at the other - study `{b['pid']}`\n")
    w("| Quantity | Hand calculation | Workbench | error |")
    w("|---|---|---|---|")
    w(f"| Series resistance | R = L/(kA) + 1/(hA) = {b['R_cond']:.4f} + {b['R_conv']:.4f} = {b['R_cond'] + b['R_conv']:.4f} K/W | - | - |")
    w(f"| Heat flow Q | Q = (100 - 20) / R = {b['Q_hand']:.3f} W | {b['Q_conv']:.3f} W (convective face), {-b['Q_hot']:.3f} W at the hot end | "
      f"{pct(b['Q_conv'], b['Q_hand']):+.3f} % |")
    w(f"| Temperature of the convective end face | T_s = T_inf + Q / (hA) = 20 + {b['Q_hand']:.3f} x {b['R_conv']:.1f} = {b['Ts_hand']:.2f} degC | "
      f"{b['Ts_wb']:.2f} degC (area-mean on the face) | {b['Ts_wb'] - b['Ts_hand']:+.3f} K ({pct(b['Ts_wb'], b['Ts_hand']):+.3f} %) |")
    w(f"| Profile: linear from 100 to {b['Ts_hand']:.2f} degC, all {b['profile']['n']} nodes | linear | max deviation {b['profile']['max_abs_K']:.1e} K | ~0 |")
    w("\nThe printed workbench values have 4 significant figures, which explains the last digits of the errors.\n")
    w(f"## (c) Extra: uniform heat generation, both ends at 20 degC - study `{c['pid']}`\n")
    w(f"Heat generated in the body: P = {c['P']:g} W (body setting heat_W), {QV} = P / (A L) = {c['qv']:.3g} W/m3.\n")
    w("| Quantity | Hand calculation | Workbench | error |")
    w("|---|---|---|---|")
    w(f"| Heat out of each end | P / 2 = 10 W | {c['Q_A']:.3f} W and {c['Q_B']:.3f} W | {pct(c['Q_A'], 10):+.3f} % |")
    w(f"| Maximum temperature (mid-length) | T0 + {QV} L^2 / (8 k) = 20 + {c['qv']:.3g} x 0.01 / (8 x {d['k']:g}) = {c['Tmax_hand']:.3f} degC | "
      f"{c['Tmax_wb']:.2f} degC | {c['Tmax_wb'] - c['Tmax_hand']:+.3f} K ({pct(c['Tmax_wb'] - 20, c['Tmax_hand'] - 20):+.2f} % of the rise) |")
    w(f"| Volume-mean temperature | T0 + {QV} L^2 / (12 k) = {c['Tmean_hand']:.3f} degC | {c['Tmean_wb']:.2f} degC | {c['Tmean_wb'] - c['Tmean_hand']:+.3f} K |")
    w(f"| Profile T(x) = 20 + {QV} x (L - x) / (2 k) | parabola | max deviation {c['profile']['max_abs_K']:.4f} K "
      f"(rms {c['profile']['rms_K']:.4f} K) of a {c['Tmax_hand'] - 20:.2f} K rise = {100 * c['profile']['max_abs_K'] / (c['Tmax_hand'] - 20):.2f} % | see below |")
    w(f"| Energy balance | in = out = 20 W | imbalance {c['energy']['imbalance_percent']:.1e} % | - |\n")
    w("The only visible error is the parabolic profile in (c): linear tetrahedra cannot represent a parabola exactly "
      "(the nodal values are interpolated from a piecewise-linear field), 0.2 % of the temperature rise at a 4 mm mesh; "
      "it goes down with the mesh size. In (a) and (b) the exact solution is linear and the solver reproduces it to "
      "round-off, with energy conserved to 1e-12 W.\n")
    (out_dir / "heat_slab_validation.md").write_text("\n".join(L) + "\n", encoding="utf-8")


modal_report()
heat_report()
print("reports written to", out_dir)
