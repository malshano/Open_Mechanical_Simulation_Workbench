"""Built-in heat conduction solver (linear tetrahedral FEM), steady or transient.

    rho cp dT/dt = div(k grad T) + q'''      in each solid body (k, rho cp piecewise constant)
    steady: dT/dt = 0. Transient: lumped heat capacity, BDF2 time steps (first step backward Euler), all loads
    switched on at t = 0 from a uniform initial temperature.

Boundary conditions on CAD faces
    fixed_temperature : T = T0
    heat_flux         : -k dT/dn = -q          (q > 0 = heat INTO the part, W/m2)
    heat_load         : total power Q [W] spread uniformly over the selected faces
    convection        : -k dT/dn = h (T - T_inf)
    radiation_convection : convection + linearised radiation (eps, T_inf) - iterated
    natural_convection : still air: h from natural-convection correlations per surface triangle (orientation to
                        gravity, face size, surface temperature) + radiation to the room (eps) - iterated
    (unassigned)      : adiabatic

Bodies sharing a face (after CAD fragmenting) are in perfect thermal contact.
Temperatures are in degC throughout (the problem is linear except for radiation).
"""
from __future__ import annotations

import math
import time

import numpy as np
import scipy.sparse as sp

SIGMA = 5.670374419e-8

_TRI_MASS = np.array([[2, 1, 1], [1, 2, 1], [1, 1, 2]], float) / 12.0


def _grads(p, t):
    X = p[t]  # (M,4,3)
    D = X[:, 1:, :] - X[:, [0], :]  # (M,3,3) rows are edges
    det = np.linalg.det(D)
    V = np.abs(det) / 6.0
    G = np.linalg.inv(np.transpose(D, (0, 2, 1)))  # rows: grad xi_1..3
    B = np.concatenate([-G.sum(1, keepdims=True), G], axis=1)  # (M,4,3)
    return B, V


def _tri_area(p, tris):
    a, b, c = p[tris[:, 0]], p[tris[:, 1]], p[tris[:, 2]]
    return 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)


AXES = {"+x": (1, 0, 0), "-x": (-1, 0, 0), "+y": (0, 1, 0), "-y": (0, -1, 0), "+z": (0, 0, 1), "-z": (0, 0, -1)}


def _outward_normals(p, t, tr):
    """Unit normals of boundary triangles pointing out of the solid (via the tetrahedron that owns each)."""
    loc = np.array([[1, 2, 3], [0, 3, 2], [0, 1, 3], [0, 2, 1]])
    Fk = np.sort(t[:, loc].reshape(-1, 3), axis=1)
    _, inv = np.unique(np.vstack([Fk, np.sort(tr, axis=1)]), axis=0, return_inverse=True)
    inv = inv.ravel()
    owner = np.zeros(inv.max() + 1, np.int64)
    owner[inv[: len(Fk)]] = np.repeat(np.arange(len(t)), 4)
    own = owner[inv[len(Fk):]]
    a, b, c = p[tr[:, 0]], p[tr[:, 1]], p[tr[:, 2]]
    nrm = np.cross(b - a, c - a)
    nrm *= np.sign(np.einsum("ij,ij->i", nrm, (a + b + c) / 3 - p[t[own]].mean(1)))[:, None]
    return nrm / np.maximum(np.linalg.norm(nrm, axis=1), 1e-300)[:, None]


def _face_lengths(p, tr, tsurf, up):
    """Per triangle: height of its CAD face along gravity (vertical plates) and area / perimeter (horizontal)."""
    H = np.zeros(len(tr))
    Lh = np.zeros(len(tr))
    A = _tri_area(p, tr)
    for fid in np.unique(tsurf):
        m = tsurf == fid
        f = tr[m]
        z = p[np.unique(f)] @ up
        cnt = {}
        for a, b in np.sort(np.vstack([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]]), axis=1):
            cnt[(a, b)] = cnt.get((a, b), 0) + 1
        per = sum(np.linalg.norm(p[a] - p[b]) for (a, b), c in cnt.items() if c == 1)
        H[m] = max(z.max() - z.min(), 1e-4)
        Lh[m] = max(A[m].sum() / max(per, 1e-12), 1e-4)
    return H, Lh


def h_natural(Ts, Tinf, cos_up, H, Lh, g=9.81):
    """Natural-convection heat transfer coefficient [W/m2K] of air, per surface element (Incropera ch. 9):
    vertical/inclined up to 60 deg from vertical: Churchill-Chu with g cos(tilt); otherwise horizontal plates:
    hot facing up (or cold facing down) 0.54 Ra^1/4 / 0.15 Ra^1/3, hot facing down 0.52 Ra^1/5.
    Air properties at the film temperature (linear fits, about 250-450 K)."""
    Tf = 0.5 * (Ts + Tinf) + 273.15
    k = 0.0263 + 7.4e-5 * (Tf - 300.0)
    nu = 1.589e-5 + 1.0e-7 * (Tf - 300.0)
    Pr = 0.707
    dT = np.maximum(np.abs(Ts - Tinf), 0.05)
    gb = g / Tf * dT / (nu * nu / Pr)  # g beta dT / (nu alpha)
    vert = np.abs(cos_up) < np.cos(np.radians(30.0))  # (normal within 60 deg of horizontal)
    tilt = np.sqrt(np.clip(1.0 - cos_up ** 2, 0.0, 1.0))
    Ra_v = gb * np.maximum(tilt, 0.5) * H ** 3
    Nu_v = (0.825 + 0.387 * Ra_v ** (1 / 6) / (1 + (0.492 / Pr) ** (9 / 16)) ** (8 / 27)) ** 2
    Ra_h = gb * Lh ** 3
    up_hot = (cos_up > 0) == (Ts > Tinf)
    Nu_h = np.where(up_hot, np.where(Ra_h < 1e7, 0.54 * Ra_h ** 0.25, 0.15 * Ra_h ** (1 / 3)), 0.52 * Ra_h ** 0.2)
    return np.where(vert, Nu_v * k / H, Nu_h * k / Lh)


def solve(mesh: dict, body_props: dict, bcs: list, log=print, tol=1e-9, transient=None, progress=None) -> dict:
    """mesh: dict with nodes, tets, tet_vol, tris, tri_surf (as written by the mesher).
    body_props: {body_id: {"k": W/mK, "heat_W": W, "name": str, "rho_cp": J/m3K (transient only)}}
    bcs: [{"name","type","faces":[ids],"params":{...}}]
    transient: None (steady) or {"end_time": s, "dt": s (0 = end/200), "T0": degC, "n_frames": saved time points}
    """
    t0 = time.time()
    p = mesh["nodes"]
    t = mesh["tets"].astype(np.int64)
    tv = mesh["tet_vol"]
    tris = mesh["tris"].astype(np.int64)
    ts = mesh["tri_surf"]
    n = len(p)

    k_e = np.zeros(len(t))
    src_e = np.zeros(len(t))
    B, V = _grads(p, t)
    for bid, bp in body_props.items():
        m = tv == int(bid)
        if not m.any():
            continue
        k_e[m] = float(bp["k"])
        Q = float(bp.get("heat_W", 0.0) or 0.0)
        if Q:
            src_e[m] = Q / V[m].sum()
    if (k_e <= 0).any():
        missing = sorted(set(int(x) for x in tv[k_e <= 0]))
        raise ValueError(f"Bodies {missing} have no material (conductivity) assigned.")

    log(f"Assembling {len(t):,} elements, {n:,} nodes")
    Ke = k_e[:, None, None] * V[:, None, None] * np.einsum("eia,eja->eij", B, B)
    rows = np.repeat(t, 4, axis=1).ravel()
    cols = np.tile(t, (1, 4)).ravel()
    K = sp.coo_matrix((Ke.ravel(), (rows, cols)), shape=(n, n)).tocsr()
    f = np.bincount(t.ravel(), weights=np.repeat(src_e * V / 4.0, 4), minlength=n)

    fixed = {}
    conv_terms = []   # (tris, h, Tinf, name)
    rad_terms = []    # (tris, eps, Tinf, name)
    nat_terms = []    # (tris, A, Tinf, name, cos_up, H, Lh): natural convection, h from the surface temperature
    flux_in = 0.0
    group_tris = {}
    for bc in bcs:
        faces = [int(x) for x in bc.get("faces", [])]
        m = np.isin(ts, faces)
        tr = tris[m]
        group_tris[bc["name"]] = tr
        if len(tr) == 0:
            log(f"warning: boundary condition '{bc['name']}' has no mesh faces")
            continue
        A = _tri_area(p, tr)
        prm = bc.get("params", {})
        typ = bc["type"]
        if typ == "fixed_temperature":
            for nd in np.unique(tr):
                fixed[int(nd)] = float(prm["T"])
        elif typ in ("heat_flux", "heat_load"):
            q = float(prm["q"]) if typ == "heat_flux" else float(prm["Q"]) / A.sum()
            f += np.bincount(tr.ravel(), weights=np.repeat(q * A / 3.0, 3), minlength=n)
            flux_in += q * A.sum()
        elif typ == "convection":
            conv_terms.append((tr, A, float(prm["h"]), float(prm["T_inf"]), bc["name"]))
        elif typ == "radiation_convection":
            conv_terms.append((tr, A, float(prm["h"]), float(prm["T_inf"]), bc["name"]))
            rad_terms.append((tr, A, float(prm["emissivity"]), float(prm.get("T_surr", prm["T_inf"])), bc["name"]))
        elif typ == "natural_convection":
            up = np.array(AXES[prm.get("up", "+z")], float)
            cos_up = _outward_normals(p, t, tr) @ up
            H, Lh = _face_lengths(p, tr, ts[m], up)
            nat_terms.append((tr, A, float(prm["T_inf"]), bc["name"], cos_up, H, Lh))
            if float(prm.get("emissivity") or 0) > 0:
                rad_terms.append((tr, A, float(prm["emissivity"]), float(prm["T_inf"]), bc["name"]))
        elif typ in ("adiabatic", "wall", "symmetry"):  # (a mirror: no heat crosses, the natural condition)
            pass
        else:
            raise ValueError(f"Boundary condition type '{typ}' is not supported by the built-in thermal solver")

    def robin(tr, A, h, Tinf):
        Me = (h * A)[:, None, None] * _TRI_MASS[None]  # (h: one value, or one per triangle)
        r = np.repeat(tr, 3, axis=1).ravel()
        c = np.tile(tr, (1, 3)).ravel()
        M = sp.coo_matrix((Me.ravel(), (r, c)), shape=(n, n)).tocsr()
        fr = np.bincount(tr.ravel(), weights=np.repeat(h * Tinf * A / 3.0, 3), minlength=n)
        return M, fr

    if not fixed and not conv_terms and not nat_terms and not transient:
        raise ValueError(
            "The temperature level is undefined: add at least one 'fixed temperature' or 'convection' "
            "boundary condition (otherwise heat has nowhere to go)."
        )

    Kc = K.copy()
    fc = f.copy()
    for tr, A, h, Tinf, _ in conv_terms:
        M, fr = robin(tr, A, h, Tinf)
        Kc = Kc + M
        fc = fc + fr

    fixed_nodes = np.array(sorted(fixed), dtype=np.int64)
    fixed_vals = np.array([fixed[i] for i in fixed_nodes])
    free = np.ones(n, bool)
    free[fixed_nodes] = False

    nonlin = bool(rad_terms or nat_terms)  # (radiation, natural convection: iterate on the temperature)

    def nat_h(T, tr, Tinf, cos_up, H, Lh):
        return h_natural(T[tr].mean(1), Tinf, cos_up, H, Lh)

    def with_radiation(T):
        Kt, ft = Kc, fc
        for tr, A, Tinf, _, cu, H, Lh in nat_terms:
            M, fr = robin(tr, A, nat_h(T, tr, Tinf, cu, H, Lh), Tinf)
            Kt = Kt + M
            ft = ft + fr
        for tr, A, eps, Tsur, _ in rad_terms:
            # linearised radiation: h_r = eps sigma (Ts^2 + Tsur^2)(Ts + Tsur), Ts from last iterate (K)
            Ts = T[tr].mean(1) + 273.15
            Tk = Tsur + 273.15
            hr = eps * SIGMA * (Ts ** 2 + Tk ** 2) * (Ts + Tk)
            Me = (hr * A)[:, None, None] * _TRI_MASS[None]
            r = np.repeat(tr, 3, axis=1).ravel()
            c = np.tile(tr, (1, 3)).ravel()
            Kt = Kt + sp.coo_matrix((Me.ravel(), (r, c)), shape=(n, n)).tocsr()
            ft = ft + np.bincount(tr.ravel(), weights=np.repeat(hr * Tsur * A / 3.0, 3), minlength=n)
        return Kt, ft

    stored = np.zeros(n)  # heat stored per node [W] (transient: C dT/dt at the last step)
    frames = history = tinfo = None
    rad_converged = True
    if transient:
        cap_e = np.zeros(len(t))
        for bid, bp in body_props.items():
            cap_e[tv == int(bid)] = float(bp.get("rho_cp") or 0.0)
        if (cap_e <= 0).any():
            raise ValueError("Transient heat transfer needs the density and specific heat of every material.")
        Cn = np.bincount(t.ravel(), weights=np.repeat(cap_e * V / 4.0, 4), minlength=n)  # lumped capacity [J/K]
        t_end = float(transient["end_time"])
        dt = float(transient.get("dt") or 0.0) or t_end / 200.0
        n_steps = max(1, int(math.ceil(t_end / dt - 1e-9)))
        if n_steps > 20000:
            raise ValueError(f"{n_steps} time steps: use a larger time step (at most 20000 steps).")
        dt = t_end / n_steps
        n_frames = max(2, min(int(transient.get("n_frames") or 21), n_steps + 1))
        frame_at = {int(round(i * n_steps / (n_frames - 1))) for i in range(n_frames)}
        T = np.full(n, float(transient.get("T0", 25.0)))
        T[fixed_nodes] = fixed_vals
        body_nodes = {bid: np.unique(t[tv == int(bid)]) for bid in body_props if (tv == int(bid)).any()}
        body_w = {bid: Cn[nd] for bid, nd in body_nodes.items()}  # capacity-weighted mean temperature
        hist_every = max(1, n_steps // 400)
        history = {"t": [], "series": {}}

        def record(time_s):
            history["t"].append(time_s)
            for bid, nd in body_nodes.items():
                nm = body_props[bid].get("name", str(bid))
                history["series"].setdefault(f"{nm} max", []).append(float(T[nd].max()))
                history["series"].setdefault(f"{nm} mean", []).append(float((T[nd] * body_w[bid]).sum() / body_w[bid].sum()))

        frames = {"times": [0.0], "T": [T.copy()]}
        record(0.0)
        cache = {}
        T_prev = None
        log(f"Transient: {n_steps} steps of {dt:.4g} s up to {t_end:.4g} s, {n:,} nodes")
        for k in range(1, n_steps + 1):
            a = 1.0 if T_prev is None else 1.5  # backward Euler, then BDF2
            hist_T = T if T_prev is None else 2.0 * T - 0.5 * T_prev
            Kt, ft = with_radiation(T) if nonlin else (Kc, fc)
            if nonlin or a not in cache:
                At = (Kt + sp.diags(a * Cn / dt)).tocsr()
                cache[a] = (_make_solver(At[free][:, free], log), At[free][:, fixed_nodes])
            solve_ff, A_fd = cache[a]
            rhs = ft[free] + (Cn / dt * hist_T)[free] - A_fd @ fixed_vals
            T_new = T.copy()
            T_new[free] = solve_ff(rhs, T[free])
            stored = Cn * (a * T_new - hist_T) / dt
            T_prev, T = T, T_new
            if k % hist_every == 0 or k == n_steps:
                record(k * dt)
            if k in frame_at:
                frames["times"].append(k * dt)
                frames["T"].append(T.copy())
            if progress and (k % max(1, n_steps // 50) == 0 or k == n_steps):
                progress(k / n_steps, f"Time {k * dt:.4g} of {t_end:.4g} s")
        if not np.isfinite(T).all():
            raise RuntimeError("The transient solution diverged (non-finite temperatures).")
        tinfo = {"end_time_s": t_end, "time_step_s": dt, "steps": n_steps,
                 "T_max_any_time": float(max(np.max(x) for x in frames["T"]))}
    else:
        T = np.full(n, fixed_vals.mean() if len(fixed_vals) else
                    np.mean([c[3] for c in conv_terms] + [c[2] for c in nat_terms]))
        T[fixed_nodes] = fixed_vals
        if nat_terms:  # (start from a typical h ~ 7 W/m2K rather than the room temperature: fewer iterations)
            Kt = Kc
            ft = fc
            for tr, A, Tinf, *_ in nat_terms:
                M, fr = robin(tr, A, 7.0, Tinf)
                Kt, ft = Kt + M, ft + fr
            T[free] = _linsolve(Kt[free][:, free], ft[free] - Kt[free][:, fixed_nodes] @ fixed_vals, T[free], tol, log)

        n_rad_iter = 60 if nonlin else 1
        for it in range(n_rad_iter):
            Kt, ft = with_radiation(T)
            Kff = Kt[free][:, free]
            rhs = ft[free] - Kt[free][:, fixed_nodes] @ fixed_vals
            Tf = _linsolve(Kff, rhs, T[free], tol, log)
            dT = np.abs(Tf - T[free]).max() if it else np.inf
            # natural convection: h ~ dT^(1/4) -> damp the update a little so the iteration does not oscillate
            T[free] = Tf if not nat_terms or not it else 0.3 * T[free] + 0.7 * Tf
            if nonlin:
                log(f"{'radiation / natural convection' if nat_terms else 'radiation'} iteration {it + 1}: "
                    f"max change {dT:.3g} K")
                if dT < 1e-3:
                    break
        rad_converged = (not nonlin) or dT < 1e-3
        if nat_terms:
            Kt, ft = with_radiation(T)  # (h at the final temperatures, for the heat balance below)

    # ---- post-processing -----------------------------------------------------
    gradT = np.einsum("eia,ei->ea", B, T[t])
    q_e = -k_e[:, None] * gradT
    qmag = np.linalg.norm(q_e, axis=1)

    report = {"bodies": {}, "boundaries": {}, "seconds": 0.0}
    Te = T[t].mean(1)
    for bid, bp in body_props.items():
        m = tv == int(bid)
        if not m.any():
            continue
        nodes_b = np.unique(t[m])
        report["bodies"][str(bid)] = {
            "name": bp.get("name", str(bid)),
            "T_min": float(T[nodes_b].min()), "T_max": float(T[nodes_b].max()),
            "T_mean": float((Te[m] * V[m]).sum() / V[m].sum()),
            "heat_W": float(bp.get("heat_W", 0.0) or 0.0),
        }
    # heat leaving through each boundary group
    Q_src = float((src_e * V).sum())
    out_total = 0.0
    # heat leaving through Dirichlet nodes = -(residual of the full system) there (transient: incl. C dT/dt)
    R = Kt @ T - ft + stored
    for tr, A, h, Tinf, name in conv_terms:
        M, fr = robin(tr, A, h, Tinf)
        Q = float((M @ T - fr).sum())
        report["boundaries"].setdefault(name, {})["heat_out_W"] = Q
    for tr, A, Tinf, name, cu, H, Lh in nat_terms:
        h = nat_h(T, tr, Tinf, cu, H, Lh)
        M, fr = robin(tr, A, h, Tinf)
        d = report["boundaries"].setdefault(name, {})
        d["heat_out_W"] = float((M @ T - fr).sum())
        d["h_mean_W_m2K"] = float((h * A).sum() / A.sum())
        d["h_range_W_m2K"] = [float(h.min()), float(h.max())]
    for tr, A, eps, Tsur, name in rad_terms:
        Ts = T[tr].mean(1) + 273.15
        Qr = float((eps * SIGMA * A * (Ts ** 4 - (Tsur + 273.15) ** 4)).sum())
        d = report["boundaries"].setdefault(name, {})
        d["heat_out_convection_W"] = d.get("heat_out_W", 0.0)
        d["heat_out_radiation_W"] = Qr
        d["heat_out_W"] = d["heat_out_convection_W"] + Qr
    for bc in bcs:
        tr = group_tris.get(bc["name"])
        if tr is None or len(tr) == 0:
            continue
        A = _tri_area(p, tr)
        d = report["boundaries"].setdefault(bc["name"], {})
        d["T_mean"] = float((T[tr].mean(1) * A).sum() / A.sum())
        d["T_max"] = float(T[tr].max())
        d["area_m2"] = float(A.sum())
        if bc["type"] == "fixed_temperature":
            d["heat_out_W"] = float(-R[np.unique(tr)].sum())
        if bc["type"] in ("heat_flux", "heat_load"):
            prm = bc["params"]
            d["heat_in_W"] = float(prm["Q"]) if bc["type"] == "heat_load" else float(prm["q"]) * float(A.sum())
    for name, d in report["boundaries"].items():
        out_total += d.get("heat_out_W", 0.0)
    Q_in = Q_src + flux_in
    Q_st = float(stored[free].sum())  # (Dirichlet nodes are held: their storage is in their reaction)
    report["energy_balance"] = {
        "heat_in_W": Q_in, "heat_out_W": out_total,
        "imbalance_percent": 100.0 * abs(Q_in - out_total - Q_st) / max(abs(Q_in), 1e-12) if abs(Q_in) > 1e-12 else 0.0,
    }
    if transient:
        report["energy_balance"]["heat_stored_W"] = Q_st
    # independent discretisation indicator: heat leaving through convective/fixed-T faces computed from the
    # element temperature gradients (-k grad T . n), compared with the consistent (weak-form) value above
    try:
        loc = np.array([[1, 2, 3], [0, 3, 2], [0, 1, 3], [0, 2, 1]])
        Fk = np.sort(t[:, loc].reshape(-1, 3), axis=1)
        cells = np.repeat(np.arange(len(t)), 4)
        q_grad_out = 0.0
        q_cons_out = 0.0
        for bc in bcs:
            if bc["type"] not in ("fixed_temperature", "convection", "radiation_convection", "natural_convection"):
                continue
            tr = group_tris.get(bc["name"])
            if tr is None or not len(tr):
                continue
            key = np.sort(tr, axis=1)
            allk = np.vstack([Fk, key])
            _, inv = np.unique(allk, axis=0, return_inverse=True)
            inv = inv.ravel()
            owner = -np.ones(inv.max() + 1, np.int64)
            owner[inv[: len(Fk)]] = cells
            own = owner[inv[len(Fk):]]
            okm = own >= 0
            a_, b_, c_ = p[tr[okm, 0]], p[tr[okm, 1]], p[tr[okm, 2]]
            nA = 0.5 * np.cross(b_ - a_, c_ - a_)
            out_dir = (a_ + b_ + c_) / 3 - p[t[own[okm]]].mean(1)
            nA *= np.sign(np.einsum("ij,ij->i", nA, out_dir))[:, None]
            q_grad_out += float(np.einsum("ij,ij->i", q_e[own[okm]], nA).sum())
            q_cons_out += report["boundaries"].get(bc["name"], {}).get("heat_out_W", 0.0)
        if abs(q_cons_out) > 1e-12:
            # first-order (P1 gradient) estimate, kept for diagnostics only; use the mesh check to judge accuracy
            report["diagnostics"] = {"boundary_flux_from_gradients_W": q_grad_out,
                                     "boundary_flux_consistent_W": q_cons_out}
            report["discretisation_indicator_percent"] = 100 * abs(q_grad_out - q_cons_out) / abs(q_cons_out)
    except Exception:  # noqa: BLE001
        pass
    report["warnings"] = list(dict.fromkeys(_SOLVER_WARNINGS))
    _SOLVER_WARNINGS.clear()
    if not rad_converged:
        report["warnings"].append("Radiation / natural-convection iterations did not fully converge "
                                  "(max change > 1e-3 K).")
    if nat_terms:
        report["warnings"].append("Natural convection h from textbook correlations for isolated plates (typically "
                                  "+-20-30 %): fins close together, enclosures and neighbouring parts change it. "
                                  "Use Electronics cooling (no fan) to simulate the air itself.")
    if rad_terms:
        report["warnings"].append("Radiation is to the surroundings only (no view factors): finned or concave "
                                  "surfaces radiate less than computed.")
    if T.max() > 150:
        report["warnings"].append(f"Maximum temperature {T.max():.0f} °C is very high for electronics/structures - "
                                  "check the cooling assumptions (h values, heat load) and material limits.")
    report["assumptions"] = ["Steady state", "Perfect thermal contact between touching parts",
                             "Constant material properties"]
    if transient:
        report["assumptions"][0] = ("Transient from a uniform start temperature, all loads switched on at t = 0; "
                                    "BDF2 time steps, lumped heat capacity")
        report["transient"] = tinfo
        report["history"] = history
    report["T_min"] = float(T.min())
    report["T_max"] = float(T.max())
    report["seconds"] = round(time.time() - t0, 2)
    log(f"Solved in {report['seconds']} s: T = {T.min():.2f} .. {T.max():.2f} C")
    if frames:
        frames["q_mag"] = [np.linalg.norm(-k_e[:, None] * np.einsum("eia,ei->ea", B, Tf_[t]), axis=1)
                           for Tf_ in frames["T"]]
    return {"T": T, "q_mag": qmag, "report": report, "frames": frames}


_SOLVER_WARNINGS: list = []


def _make_solver(A, log):
    """Solver for repeated right-hand sides with one matrix: LU for small systems, AMG-preconditioned CG else."""
    A = A.tocsc()
    if A.shape[0] < 60000:
        from scipy.sparse.linalg import factorized

        f = factorized(A)
        return lambda b, x0: f(b)
    try:
        import pyamg
        from scipy.sparse.linalg import cg

        A = A.tocsr()
        M = pyamg.smoothed_aggregation_solver(A, symmetry="symmetric", max_coarse=500).aspreconditioner(cycle="V")

        def run(b, x0):
            x, info = cg(A, b, x0=x0, rtol=1e-9, maxiter=2000, M=M)
            if info != 0:
                _SOLVER_WARNINGS.append("The linear solver did not fully converge; results may be inaccurate.")
            return x
        return run
    except ImportError:
        from scipy.sparse.linalg import factorized

        f = factorized(A)
        return lambda b, x0: f(b)


def _linsolve(A, b, x0, tol, log):
    A = A.tocsr()
    if A.shape[0] < 30000:
        from scipy.sparse.linalg import spsolve

        return spsolve(A.tocsc(), b)
    try:
        import pyamg
        from scipy.sparse.linalg import cg

        ml = pyamg.smoothed_aggregation_solver(A, symmetry="symmetric", max_coarse=500)
        M = ml.aspreconditioner(cycle="V")
        it = [0]

        def cb(xk):
            it[0] += 1

        x, info = cg(A, b, x0=x0, rtol=tol, maxiter=2000, M=M, callback=cb)
        log(f"AMG-CG converged in {it[0]} iterations" if info == 0 else f"AMG-CG stopped (info={info})")
        if info != 0:
            _SOLVER_WARNINGS.append("The linear solver did not fully converge; results may be inaccurate.")
        return x
    except ImportError:
        from scipy.sparse.linalg import spsolve

        return spsolve(A.tocsc(), b)
