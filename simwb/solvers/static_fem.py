"""Built-in linear static stress analysis with quadratic tetrahedra.

    K u = f          small displacements, linear elastic material per body (isotropic, or orthotropic with its
                     material axes: see simwb.materials)

Same 10-node elements as the modal solver (simwb.solvers.modal_fem): exact degree-2 stiffness, consistent loads.

Boundary conditions on CAD faces
    fixed_support        : all displacements zero
    frictionless_support : no motion normal to the face (sliding allowed; on a hole: a bearing/pin that lets the
                           part rotate and slide along the axis)
    displacement         : prescribed displacement (x, y, z in mm; blank component = free)
    force                : total force vector [N] spread uniformly over the face area
    pressure             : pressure [Pa] normal to the face (positive pushes into the part)
    (unassigned)         : free
Gravity (optional) acts on all bodies. Bodies sharing a face are bonded.
Thermal load (optional, `thermal=`): the body is stress-free at the reference temperature T_ref and every point wants
to expand by eps_th = alpha (T - T_ref); the part is loaded by f = integral of B^T D eps_th dV and the stress is
sigma = D (eps - eps_th). T is the corner-node temperature interpolated linearly inside each element (a quadratic
tetrahedron represents the resulting displacement exactly for a linear temperature field).

Stresses are evaluated at the corner nodes of every element and the stress tensors are averaged at the nodes
within each body (as most codes display them; stress jumps across a material interface, so it is never averaged
across two bodies). The von Mises and principal stresses are those of the averaged tensor. The report also gives
the unaveraged maximum, so stress concentrations at sharp corners are visible, and an error estimate in the energy
norm (Zienkiewicz-Zhu: averaged minus element stresses) that says how well the mesh resolves the stress field.
"""
from __future__ import annotations

import time

import numpy as np
import scipy.sparse as sp

from .modal_fem import (EDGES, MAX_DOF, MAX_DOF_SUPERLU, QPTS, _assemble, _pardiso, body_moduli, element_stiffness,
                        quadratic_mesh, shape_gradients, strain_matrix)
from .thermal_fem import _grads


def _factor_solve(A, b, log):
    return _factorize(A, log)(b)


def _factorize(A, log):
    """Factorise A once; returns solve(b) for any number of right-hand sides (thermal stress over time)."""
    pd = _pardiso()
    if pd is not None:
        Ac = A.tocsr()
        ps = pd.PyPardisoSolver()
        ps.factorize(Ac)
        log("Solved with PARDISO")
        return lambda b: ps.solve(Ac, np.ascontiguousarray(b))
    from scipy.sparse.linalg import splu

    lu = splu(A.tocsc(), permc_spec="MMD_AT_PLUS_A", options={"SymmetricMode": True})
    log("Solved with SuperLU")
    return lu.solve


def _tri_normals(p, t, tr):
    """Outward unit normal and area of every boundary triangle."""
    loc = np.array([[1, 2, 3], [0, 3, 2], [0, 1, 3], [0, 2, 1]])
    F = np.sort(t[:, loc].reshape(-1, 3), axis=1)
    key = np.sort(tr, axis=1)
    _, inv = np.unique(np.vstack([F, key]), axis=0, return_inverse=True)
    inv = inv.ravel()
    owner = -np.ones(inv.max() + 1, np.int64)
    owner[inv[: len(F)]] = np.repeat(np.arange(len(t)), 4)
    own = owner[inv[len(F):]]
    a, b, c = p[tr[:, 0]], p[tr[:, 1]], p[tr[:, 2]]
    nA = 0.5 * np.cross(b - a, c - a)
    out = (a + b + c) / 3 - p[t[np.maximum(own, 0)]].mean(1)
    nA *= np.where(np.einsum("ij,ij->i", nA, out) < 0, -1.0, 1.0)[:, None]
    A = np.linalg.norm(nA, axis=1)
    return nA / np.maximum(A, 1e-300)[:, None], A


def _corner_stress(p10, t10, B, u, lam_e, mu_e, Dm=None, eps_th=None):
    """Stress tensor (Voigt xx yy zz yz xz xy) at the 4 corner nodes of every element: (M, 4, 6).
    Dm: per-element 6x6 stiffness (orthotropic material) instead of lam, mu. eps_th: (M, 4, 6) thermal strain at the
    corners (engineering shear), subtracted from the strain before the stress is formed."""
    ue = u.reshape(-1, 3)[t10]  # (M, 10, 3)
    out = np.zeros((len(t10), 4, 6))
    for c in range(4):
        q = np.zeros(4)
        q[c] = 1.0
        D = np.zeros((len(t10), 10, 3))
        D[:, :4] = (4 * q[:4] - 1)[None, :, None] * B
        for a, (i, j) in enumerate(EDGES):
            D[:, 4 + a] = 4 * (q[i] * B[:, j] + q[j] * B[:, i])
        Gu = np.einsum("eai,eaj->eij", ue, D)  # du_i/dx_j
        eps = 0.5 * (Gu + Gu.transpose(0, 2, 1))
        if Dm is None and eps_th is None:
            tr = np.trace(eps, axis1=1, axis2=2)
            sig = 2 * mu_e[:, None, None] * eps + (lam_e * tr)[:, None, None] * np.eye(3)
            out[:, c] = np.stack([sig[:, 0, 0], sig[:, 1, 1], sig[:, 2, 2], sig[:, 1, 2], sig[:, 0, 2], sig[:, 0, 1]], 1)
            continue
        ev = np.stack([eps[:, 0, 0], eps[:, 1, 1], eps[:, 2, 2], 2 * eps[:, 1, 2], 2 * eps[:, 0, 2],
                       2 * eps[:, 0, 1]], 1)  # engineering strain
        if eps_th is not None:
            ev = ev - eps_th[:, c]  # mechanical strain = total - thermal
        out[:, c] = _stress_of_strain(ev, lam_e, mu_e, Dm)
    return out


def _stress_of_strain(ev, lam, mu, Dm=None):
    """Stress (Voigt) of engineering strains ev (n, 6): isotropic (lam, mu) or the 6x6 matrices Dm (n, 6, 6)."""
    if Dm is not None:
        return np.einsum("eij,ej->ei", Dm, ev)
    tr = ev[:, 0] + ev[:, 1] + ev[:, 2]
    s = np.empty_like(ev)
    s[:, :3] = 2 * mu[:, None] * ev[:, :3] + (lam * tr)[:, None]
    s[:, 3:] = mu[:, None] * ev[:, 3:]
    return s


def thermal_load(t10, B, V, lam_e, mu_e, Dm, eps_c, ndof, chunk=20000):
    """Load vector f = sum_e integral(B^T D eps_th dV) of the thermal strain eps_c (M, 4, 6): eps_th at the 4 corners
    of every element (engineering shear), linear inside. 4-point rule, exact (degree 2). Also returns
    W0 = 1/2 integral(eps_th . D eps_th dV) so the elastic strain energy of the result can be formed. The net
    force of f is zero (it is a self-equilibrated load): only its distribution matters."""
    f = np.zeros(ndof)
    W0 = 0.0
    for s0 in range(0, len(t10), chunk):
        sl = slice(s0, s0 + chunk)
        G, v = B[sl], V[sl]
        Dc = None if Dm is None else Dm[sl]
        fe = np.zeros((len(G), 30))
        for q in QPTS:
            e_q = np.einsum("c,ecj->ej", q, eps_c[sl])  # thermal strain at the point
            sig = _stress_of_strain(e_q, lam_e[sl], mu_e[sl], Dc)
            fe += np.einsum("eia,ei->ea", strain_matrix(shape_gradients(G, q)), sig) * (0.25 * v)[:, None]
            W0 += 0.5 * float((np.einsum("ei,ei->e", e_q, sig) * 0.25 * v).sum())
        dof = (3 * t10[sl][:, :, None] + np.arange(3)[None, None, :]).reshape(len(G), 30)
        f += np.bincount(dof.ravel(), weights=fe.ravel(), minlength=ndof)
    return f, W0


def von_mises(s):
    xx, yy, zz, yz, xz, xy = (s[..., k] for k in range(6))
    return np.sqrt(0.5 * ((xx - yy) ** 2 + (yy - zz) ** 2 + (zz - xx) ** 2) + 3 * (yz ** 2 + xz ** 2 + xy ** 2))


def average_stress(t, tv, sc, n_nodes):
    """Corner stress tensors (M, 4, 6) averaged at the nodes of each body. A node shared by two bodies gets one
    value per body: the stress is discontinuous across a material interface, averaging over it is wrong on both
    sides. Returns (avg (K, 6), key of every element corner (M, 4), node of each key, body id of each key)."""
    ids, b_idx = np.unique(tv, return_inverse=True)
    key = (t + n_nodes * b_idx.reshape(-1)[:, None]).ravel()
    uk, inv = np.unique(key, return_inverse=True)
    inv = inv.ravel()
    cnt = np.bincount(inv, minlength=len(uk))
    flat = sc.reshape(-1, 6)
    avg = np.stack([np.bincount(inv, weights=flat[:, k], minlength=len(uk)) for k in range(6)], 1) / cnt[:, None]
    return avg, inv.reshape(-1, 4), uk % n_nodes, ids[uk // n_nodes]


def principal(s):
    """Largest and smallest principal stress of Voigt tensors (..., 6: xx yy zz yz xz xy)."""
    m = np.zeros(s.shape[:-1] + (3, 3))
    for (i, j), k in {(0, 0): 0, (1, 1): 1, (2, 2): 2, (1, 2): 3, (0, 2): 4, (0, 1): 5}.items():
        m[..., i, j] = m[..., j, i] = s[..., k]
    w = np.linalg.eigvalsh(m)
    return w[..., 2], w[..., 0]


def error_energy(d, E, nu):
    """Strain energy density of a stress difference d (..., 6) in an isotropic material: d : C^-1 : d / 2."""
    tr = d[..., 0] + d[..., 1] + d[..., 2]
    dd = (d[..., :3] ** 2).sum(-1) + 2 * (d[..., 3:] ** 2).sum(-1)
    return ((1 + nu) * dd - nu * tr ** 2) / (2 * E)


def error_energy_aniso(d, Dm):
    """Strain energy density of stress differences d (n, 4, 6) for materials with stiffness matrices Dm (n, 6, 6):
    d . D^-1 d / 2 (Voigt stress, engineering strain)."""
    S = np.linalg.inv(Dm)
    return 0.5 * np.einsum("eci,eij,ecj->ec", d, S, d)


def body_safety(body_props, tv, t, vm_n, dmag, vm_body=None):
    """Per-body maximum stress, deflection and safety factor against yield, with the warnings (shared by every
    static solver). vm_body: {body id: maximum stress of that body} when the stress was averaged per body (a node
    on a material interface then counts with its own body's value). Returns (bodies, warns)."""
    bodies = []
    warns = []
    for bid, bp in body_props.items():
        m = tv == int(bid)
        if not m.any():
            continue
        nodes_b = np.unique(t[m])
        vmax = float(vm_body[int(bid)]) if vm_body and int(bid) in vm_body else float(vm_n[nodes_b].max())
        sy = bp.get("yield")
        sf = (float(sy) / vmax) if (sy and vmax > 0) else None
        bodies.append({"id": int(bid), "name": bp.get("name", str(bid)), "max_von_mises_MPa": vmax / 1e6,
                       "yield_MPa": float(sy) / 1e6 if sy else None, "safety_factor": sf,
                       "max_displacement_mm": float(dmag[nodes_b].max() * 1e3)})
        if sf is not None and sf < 1:
            warns.append(f"{bp.get('name', bid)}: the stress ({vmax / 1e6:.0f} MPa) exceeds the yield strength "
                         f"({float(sy) / 1e6:.0f} MPa) - the part would deform permanently (linear analysis: beyond "
                         "yield the real stress is lower and the strain higher).")
        elif sf is not None and sf < 1.5:
            warns.append(f"{bp.get('name', bid)}: safety factor against yield {sf:.2f} (below 1.5).")
        if not sy:
            warns.append(f"{bp.get('name', bid)}: no yield strength for this material (brittle or composite): judge "
                         "the stress against its datasheet strength.")
    return bodies, warns


def _lookup(d, bid):
    """d[bid] where the keys may be ints or strings (JSON); None when absent."""
    for k in (bid, int(bid), str(bid)):
        if k in d:
            return d[k]
    return None


def thermal_strains(thermal, t, tv, body_props, n1):
    """Corner thermal strains (M, 4, 6) [engineering shear] for the `thermal` setting of solve(): alpha (T - T_ref)
    per body (isotropic number, or the three coefficients along the material axes of an orthotropic body)."""
    from ..materials import expansion_voigt, orthotropic_alpha

    Tc = np.asarray(thermal["T_C"], float)
    if Tc.shape != (n1,):
        raise ValueError(f"The temperature field has {Tc.size} values but the mesh has {n1} corner nodes.")
    T_ref = float(thermal.get("T_ref_C", 20.0))
    unit = np.zeros((len(t), 6))
    amap = thermal.get("alpha") or {}
    for bid, bp in body_props.items():
        m = tv == int(bid)
        if not m.any():
            continue
        orth = bp.get("orthotropic")
        a = _lookup(amap, bid)
        if a is None:
            a = bp.get("alpha")
        if a is None and orth:
            a = orthotropic_alpha(orth)
        if a is None:
            raise ValueError(f"{bp.get('name', bid)}: no thermal expansion coefficient (alpha) for this material.")
        unit[m] = expansion_voigt(a, orth)
    return (Tc[t] - T_ref)[:, :, None] * unit[:, None, :], T_ref


def solve(mesh: dict, body_props: dict, bcs: list, gravity=None, log=print, thermal=None) -> dict:
    """mesh: nodes, tets, tet_vol, tris, tri_surf. body_props: {id: {E, nu, rho, yield (Pa or None), name}}; an
    orthotropic body gives "orthotropic": {E1 E2 E3 G12 G23 G13 nu12 nu23 nu13, axis1, axis2} (simwb.materials).
    gravity: acceleration vector [m/s2] or None.
    thermal: None, or {"T_C": temperature at the mesh corner nodes [degC], "T_ref_C": stress-free temperature,
    "alpha": {body id: expansion coefficient [1/K]}} (alpha may also come from body_props[id]["alpha"]).
    Returns {"displacement" (N,3) m, "von_mises" (N,) Pa, "report", "fields"}; with thermal=None the result is the
    plain mechanical one, unchanged."""
    t0 = time.time()
    p = np.asarray(mesh["nodes"], float)
    t = mesh["tets"].astype(np.int64)
    tv = mesh["tet_vol"]
    tris = mesh["tris"].astype(np.int64)
    ts = mesh["tri_surf"]
    n1 = len(p)

    E_e, nu_e, rho_e = np.zeros(len(t)), np.zeros(len(t)), np.zeros(len(t))
    for bid, bp in body_props.items():
        m = tv == int(bid)
        E_e[m], nu_e[m] = body_moduli(bp)
        rho_e[m] = float(bp["rho"])
    if (E_e <= 0).any():
        missing = sorted(set(int(x) for x in tv[E_e <= 0]))
        raise ValueError(f"Bodies {missing} have no material (stiffness) assigned.")
    lam_e = E_e * nu_e / ((1 + nu_e) * (1 - 2 * nu_e))
    mu_e = E_e / (2 * (1 + nu_e))

    p10, t10, edge_node = quadratic_mesh(p, t)
    ndof = 3 * len(p10)
    limit = MAX_DOF if _pardiso() is not None else MAX_DOF_SUPERLU
    if ndof > limit:
        raise ValueError(f"The mesh gives {ndof:,} unknowns with quadratic elements (limit {limit:,}): use a coarser "
                         "mesh preset - quadratic elements are accurate on coarser meshes.")
    log(f"Quadratic elements: {len(t10):,} tets, {len(p10):,} nodes, {ndof:,} unknowns")
    B, V = _grads(p, t)
    Dm = element_stiffness(tv, body_props, lam_e, mu_e)  # (orthotropic bodies only; None = isotropic fast path)
    K, _ = _assemble(p10, t10, B, V, lam_e, mu_e, np.zeros(len(t)), log, Dm=Dm)
    K0 = K.tocsr()  # (without the support penalties: the strain energy of the result)
    f = np.zeros(ndof)
    eps_th = f_th = None
    W0 = 0.0
    if thermal is not None:
        eps_th, T_ref = thermal_strains(thermal, t, tv, body_props, n1)
        f_th, W0 = thermal_load(t10, B, V, lam_e, mu_e, Dm, eps_th, ndof)
        f += f_th
        log(f"Thermal load: T = {np.min(thermal['T_C']):.4g} .. {np.max(thermal['T_C']):.4g} C, reference {T_ref:.4g} C")
    mass = float((rho_e * V).sum())

    # body force: consistent loads of the 10-node tet under uniform load (corners -1/20, edges 1/5 of rho g V)
    g = np.asarray(gravity, float) if gravity is not None else np.zeros(3)
    if np.any(g):
        w = rho_e * V
        for a in range(10):
            share = -0.05 if a < 4 else 0.2
            np.add.at(f, (3 * t10[:, a, None] + np.arange(3)).ravel(), (share * w[:, None] * g[None, :]).ravel())

    fixed = {}  # dof -> prescribed value
    bc_dofs = {}  # condition name -> the dofs it holds (for the reaction of each support)
    pen_rows, pen_n = [], []  # frictionless: (node, normal) constraints by penalty
    applied = np.zeros(3)
    rep_bcs = {}
    for bc in bcs:
        faces = [int(x) for x in bc.get("faces", [])]
        sel = np.isin(ts, faces)
        tr = tris[sel]
        if not len(tr):
            log(f"warning: boundary condition '{bc['name']}' has no mesh faces")
            continue
        prm = bc.get("params", {})
        mids = np.stack([edge_node(tr[:, i], tr[:, j]) for i, j in ((0, 1), (1, 2), (0, 2))], 1)  # (n, 3)
        nodes6 = np.unique(np.concatenate([tr.ravel(), mids.ravel()]))
        n_hat, A = _tri_normals(p, t, tr)
        kind = bc["type"]
        rep_bcs[bc["name"]] = {"type": kind, "area_m2": float(A.sum())}
        if kind == "fixed_support":
            for d in (3 * nodes6[:, None] + np.arange(3)).ravel():
                fixed[int(d)] = 0.0
            bc_dofs[bc["name"]] = (3 * nodes6[:, None] + np.arange(3)).ravel()
        elif kind == "displacement":
            ds = []
            for k, key in enumerate(("ux_mm", "uy_mm", "uz_mm")):
                v = prm.get(key)
                if v is None or v == "":
                    continue
                for nd in nodes6:
                    fixed[int(3 * nd + k)] = float(v) * 1e-3
                    ds.append(int(3 * nd + k))
            bc_dofs[bc["name"]] = np.array(ds, np.int64)
        elif kind in ("frictionless_support", "symmetry"):  # (a mirror plane: no movement through it)
            # area-weighted normal at every face node (corners and mid-edges)
            nn = np.zeros((len(p10), 3))
            for col in range(3):
                np.add.at(nn, tr[:, col], n_hat * A[:, None])
                np.add.at(nn, mids[:, col], n_hat * A[:, None])
            nn = nn[nodes6]
            nn /= np.maximum(np.linalg.norm(nn, axis=1), 1e-300)[:, None]
            pen_rows.append(nodes6)
            pen_n.append(nn)
            bc_dofs[bc["name"]] = (3 * nodes6[:, None] + np.arange(3)).ravel()
        elif kind in ("force", "pressure"):
            if kind == "force":
                F = np.array([float(prm.get(k) or 0.0) for k in ("fx_N", "fy_N", "fz_N")])
                tri_F = (A / A.sum())[:, None] * F[None, :]
            else:
                tri_F = -float(prm.get("pressure_Pa") or 0.0) * (A[:, None] * n_hat)  # pushes into the part
            # 6-node triangle under uniform traction: corners 0, each mid-edge node 1/3 of the triangle's load
            for col in range(3):
                np.add.at(f, (3 * mids[:, col, None] + np.arange(3)).ravel(), (tri_F / 3.0).ravel())
            applied += tri_F.sum(0)
            rep_bcs[bc["name"]]["force_N"] = tri_F.sum(0).tolist()
        elif kind == "free":
            pass
        else:
            raise ValueError(f"Boundary condition type '{kind}' is not supported by the static solver")

    if not fixed and not pen_rows:
        raise ValueError("The part is not supported: add a fixed support (or supports that stop it moving freely).")

    # frictionless supports: penalty stiffness k n n^T (k ~ 1e4 x the largest diagonal term: constraint error ~1e-4)
    K = K.tocsr()
    if pen_rows:
        kpen = 1e4 * float(K.diagonal().max())
        rows, cols, vals = [], [], []
        for nodes6, nn in zip(pen_rows, pen_n):
            for i in range(3):
                for j in range(3):
                    rows.append(3 * nodes6 + i)
                    cols.append(3 * nodes6 + j)
                    vals.append(kpen * nn[:, i] * nn[:, j])
        K = K + sp.coo_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))),
                              shape=K.shape).tocsr()

    u = np.zeros(ndof)
    fd = np.array(sorted(fixed), np.int64)
    u[fd] = [fixed[int(d)] for d in fd]
    free = np.ones(ndof, bool)
    free[fd] = False
    Kff = K[free][:, free].tocsr()
    rhs = f[free] - K[free][:, ~free] @ u[~free]
    try:
        solve_ff = _factorize(Kff, log)
        uf = solve_ff(rhs)
    except Exception as e:  # noqa: BLE001 - singular: the supports leave a rigid-body motion
        raise ValueError("The supports do not hold the part in place (it could move or rotate freely): add a fixed "
                         "support or more supports.") from e
    if not np.all(np.isfinite(uf)):
        raise ValueError("The supports do not hold the part in place (it could move or rotate freely): add a fixed "
                         "support or more supports.")
    u[free] = uf
    res = Kff @ uf - rhs
    rel_res = float(np.linalg.norm(res) / max(np.linalg.norm(rhs), 1e-300))
    if rel_res > 1e-6:
        raise ValueError(f"The equation system could not be solved accurately (residual {rel_res:.1e}): the part is "
                         "probably not held in all directions - add supports.")

    # reactions: (K u - f) without the penalty terms is zero at the free dofs and, at the constrained ones (fixed
    # dofs and the nodes of frictionless / mirror planes), the force the support exerts. Counting both kinds makes
    # the balance a real check (a mirror plane carries load: its force is not zero).
    rho = K0 @ u - f
    constrained = np.zeros(ndof, bool)
    constrained[fd] = True
    for nodes6, _ in zip(pen_rows, pen_n):
        constrained[(3 * nodes6[:, None] + np.arange(3)).ravel()] = True
    reaction = np.where(constrained, rho, 0.0).reshape(-1, 3).sum(0)
    claimed = np.zeros(ndof, bool)  # (a node shared by two conditions counts for the first one)
    for name, ds in sorted(bc_dofs.items(), key=lambda kv: rep_bcs[kv[0]]["type"] in ("frictionless_support", "symmetry")):
        ds = ds[~claimed[ds]]  # (fixed and prescribed supports first: they own their dofs, a mirror only what is left)
        claimed[ds] = True
        rep_bcs[name]["reaction_N"] = [float(rho[ds[ds % 3 == k]].sum()) for k in range(3)]  # (x, y, z of each dof)
    body_force = mass * g
    total_applied = applied + body_force
    scale = float(np.linalg.norm(total_applied))
    if thermal is not None:  # (a thermal load has no net force: the scale of the check is the support forces, and
        # a millionth of the thermal nodal forces so that the round-off of a stress-free expansion is not a "error")
        scale = max(scale, 0.5 * float(np.abs(np.where(constrained, rho, 0.0)).sum()), 1e-6 * float(np.abs(f_th).sum()))
    balance = float(np.linalg.norm(reaction + total_applied) / max(scale, 1e-30))

    # stress tensors at the corner nodes, averaged at the nodes of each body; von Mises / principal of the average
    sc = _corner_stress(p10, t10, B, u, lam_e, mu_e, Dm=Dm, eps_th=eps_th)  # (M, 4, 6)
    vm_c = von_mises(sc)  # (M, 4)
    avg, key, key_node, key_body = average_stress(t, tv, sc, n1)
    vm_k = von_mises(avg)
    s1_k, s3_k = principal(avg)
    # one value per node for display: on a material interface the body with the higher stress
    order = np.argsort(vm_k)
    pick = np.zeros(n1, np.int64)
    pick[key_node[order]] = order
    vm_n, s1_n, s3_n = vm_k[pick], s1_k[pick], s3_k[pick]
    vm_body = {int(b): float(vm_k[key_body == b].max()) for b in np.unique(key_body)}
    # how far the element stresses are from the averaged ones: per node [Pa], and in the energy norm [%]
    jump = np.zeros(n1)
    np.maximum.at(jump, t.ravel(), np.abs(vm_c - vm_k[key]).ravel())
    en = error_energy(sc - avg[key], E_e[:, None], nu_e[:, None])
    if Dm is not None:  # (orthotropic elements: the energy norm of their own compliance)
        oe = np.isin(tv, [int(b) for b, bp in body_props.items() if bp.get("orthotropic")])
        en[oe] = error_energy_aniso(sc[oe] - avg[key][oe], Dm[oe])
    e_err = float((V[:, None] / 4 * en).sum())
    U = 0.5 * float(u @ (K0 @ u))
    if thermal is not None:  # elastic energy of (eps - eps_th): 1/2 u K u - u f_th + 1/2 int eps_th D eps_th
        U = U - float(u @ f_th) + W0
    err_pct = 100 * float(np.sqrt(e_err / max(U + e_err, 1e-300)))
    if thermal is not None and U + e_err < 1e-9 * max(W0, 1e-300):
        err_pct = 0.0  # (a free thermal expansion carries no stress: nothing to estimate an error of)
    frames = None
    if thermal is not None and thermal.get("extra_T_C"):
        # further temperature fields (time frames of a transient run): same matrix, one back-substitution each
        frames = {"displacement": [], "von_mises": []}
        K_fc = K[free][:, ~free]
        for Tk in thermal["extra_T_C"]:
            eps_k, _ = thermal_strains(dict(thermal, T_C=Tk), t, tv, body_props, n1)
            f_k, _ = thermal_load(t10, B, V, lam_e, mu_e, Dm, eps_k, ndof)
            f_k = f_k + (f - f_th)  # (mechanical loads and gravity are the same in every frame)
            u_k = np.zeros(ndof)
            u_k[fd] = u[fd]
            u_k[free] = solve_ff(f_k[free] - K_fc @ u_k[~free])
            sc_k = _corner_stress(p10, t10, B, u_k, lam_e, mu_e, Dm=Dm, eps_th=eps_k)
            avg_k, _, key_node_k, _ = average_stress(t, tv, sc_k, n1)
            vm_kk = von_mises(avg_k)
            pick_k = np.zeros(n1, np.int64)
            pick_k[key_node_k[np.argsort(vm_kk)]] = np.argsort(vm_kk)
            frames["displacement"].append(u_k.reshape(-1, 3)[:n1].copy())
            frames["von_mises"].append(vm_kk[pick_k])
    yields = {int(b): float(bp.get("yield") or 0) for b, bp in body_props.items()}
    sy_k = np.array([yields.get(int(b), 0.0) for b in key_body])
    disp = u.reshape(-1, 3)[:n1]
    dmag = np.linalg.norm(disp, axis=1)
    i_d, i_s = int(np.argmax(dmag)), int(np.argmax(vm_n))

    bodies, warns = body_safety(body_props, tv, t, vm_n, dmag, vm_body)
    size = float(np.ptp(p, axis=0).max())
    if dmag.max() > 0.02 * size:
        warns.append(f"The largest deflection ({dmag.max() * 1e3:.3g} mm) is more than 2 % of the part size: "
                     "small-deflection (linear) theory becomes inaccurate.")
    if balance > 1e-3:
        warns.append(f"Force balance error {balance * 100:.2g} % (reactions vs applied loads).")
    peak = float(vm_c.max())
    if peak > 1.5 * vm_n.max():
        warns.append(f"Unaveraged peak stress {peak / 1e6:.3g} MPa vs {vm_n.max() / 1e6:.3g} MPa averaged: a sharp "
                     "corner or a point load concentrates stress there - refine the mesh locally (or add a fillet) "
                     "before trusting the peak.")
    if err_pct > 15:
        warns.append(f"Estimated stress error {err_pct:.0f} % (energy norm): the mesh is too coarse for the stress "
                     "field - use a finer preset, or part sizing on the highly stressed faces. The field 'Stress "
                     "error estimate' shows where.")
    fields = {"Max principal stress": (s1_n, "Pa"), "Min principal stress": (s3_n, "Pa"),
              "Stress error estimate": (jump, "Pa")}
    if (sy_k > 0).all():  # (every body has a yield strength; capped like most codes so the colours stay readable)
        fields["Safety factor"] = (np.minimum(sy_k[pick] / np.maximum(vm_n, 1e-30), 15.0), "")
    report = {
        "max_displacement_mm": float(dmag.max() * 1e3), "max_displacement_at_m": p[i_d].tolist(),
        "max_von_mises_MPa": float(vm_n.max() / 1e6), "max_von_mises_at_m": p[i_s].tolist(),
        "peak_von_mises_unaveraged_MPa": peak / 1e6,
        "max_principal_MPa": float(s1_n.max() / 1e6), "min_principal_MPa": float(s3_n.min() / 1e6),
        "stress_error_percent": err_pct, "strain_energy_J": U,
        "min_safety_factor": min((b["safety_factor"] for b in bodies if b["safety_factor"]), default=None),
        "bodies": bodies, "boundary_conditions": rep_bcs,
        "applied_force_N": total_applied.tolist(), "reaction_force_N": reaction.tolist(),
        "force_balance_error": balance, "mass_kg": mass, "gravity": g.tolist() if np.any(g) else None,
        "relative_residual": rel_res, "n_dof": ndof, "n_elements": len(t10), "solve_time_s": time.time() - t0,
        "warnings": warns,
        "assumptions": ["Linear elastic, small deflections; bonded contacts between bodies",
                        "Stress tensors averaged at the nodes within each part; quadratic tetrahedra with straight "
                        "edges"],
    }
    if Dm is not None:
        report["assumptions"].append("Orthotropic bodies: constant material axes per body, elastic constants "
                                     "independent of temperature")
    if thermal is not None:
        Tc = np.asarray(thermal["T_C"], float)
        report["thermal"] = {"T_ref_C": T_ref, "T_min_C": float(Tc.min()), "T_max_C": float(Tc.max()),
                             "max_abs_dT_K": float(np.abs(Tc - T_ref).max()),
                             "thermal_load_net_N": f_th.reshape(-1, 3).sum(0).tolist()}
        report["assumptions"].append(
            "Thermal stress: stress-free at the reference temperature, strain alpha (T - T_ref) with constant "
            "alpha and material constants, temperature interpolated linearly between the mesh corner nodes")
    out = {"displacement": disp, "von_mises": vm_n, "report": report, "fields": fields}
    if frames is not None:
        out["frames"] = frames
    return out
