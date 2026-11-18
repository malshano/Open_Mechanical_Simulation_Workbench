"""Built-in modal analysis (natural frequencies and mode shapes) with quadratic tetrahedra.

    K phi = omega^2 M phi          linear elasticity, isotropic material per body, no damping

The mesher writes linear (4-node) tetrahedra; this solver adds a node on every edge (straight edges) and solves with
10-node tetrahedra, which do not lock in bending the way linear tetrahedra do. Mode shapes are reported at the
corner nodes for display.

Boundary conditions on CAD faces
    fixed_support : all displacements zero on the face
    added_mass    : a mass [kg] spread over the faces (translational inertia only, no stiffness)
    (unassigned)  : free
Bodies sharing a face (after CAD fragmenting) are bonded.

Shake test (sine sweep, optional): the fixed supports are shaken with a(t) = A sin(2 pi f t) along one axis, as on
a vibration table. The steady response at every frequency is the sum of the modes (modal superposition) with the
same damping ratio zeta in every mode:

    q_i(f) = -Gamma_i A / (w_i^2 - w^2 + 2 i zeta w_i w)      Gamma_i = phi_i^T M d (participation along d)
    u_rel  = sum phi_i q_i          stress = sum S_i q_i       a_abs = A d - w^2 u_rel

The amplitudes reported are the largest values during the cycle (exact for a sum of out-of-phase modes).
"""
from __future__ import annotations

import math
import time

import numpy as np
import scipy.sparse as sp

from .thermal_fem import _grads, _tri_area

EDGES = [(0, 1), (1, 2), (0, 2), (0, 3), (1, 3), (2, 3)]  # local node a+4 sits on edge EDGES[a]
# degree-2 quadrature on the tetrahedron (exact for the quadratic integrand of the 10-node stiffness)
_QA, _QB = 0.5854101966249685, 0.1381966011250105
QPTS = np.array([[_QA, _QB, _QB, _QB], [_QB, _QA, _QB, _QB], [_QB, _QB, _QA, _QB], [_QB, _QB, _QB, _QA]])
MAX_DOF = 400_000      # with PARDISO (pypardiso): ~110k unknowns factorise in seconds
MAX_DOF_SUPERLU = 60_000  # scipy's SuperLU fills in heavily on 3-D quadratic meshes (110k unknowns: ~3 min, ~4 GB)


def _pardiso():
    try:
        import pypardiso

        return pypardiso
    except Exception:  # noqa: BLE001 - optional accelerator (MKL); SuperLU is the fallback
        return None


def _shift_invert(A, log, superlu=False):
    """Solver for (K - sigma M) x = b (symmetric positive definite) as a LinearOperator. superlu: not PARDISO (see
    solve(): for a free part A is nearly singular and PARDISO's factorisation can be too inaccurate)."""
    from scipy.sparse.linalg import LinearOperator, splu

    pd = None if superlu else _pardiso()
    if pd is not None:
        ps = pd.PyPardisoSolver(mtype=2)  # real symmetric positive definite
        Au = sp.triu(A, format="csr")
        ps.factorize(Au)
        log("Factorised with PARDISO")
        return LinearOperator(A.shape, matvec=lambda b: ps.solve(Au, np.ascontiguousarray(b)), dtype=float)
    lu = splu(A.tocsc(), permc_spec="MMD_AT_PLUS_A", options={"SymmetricMode": True})
    log("Factorised with SuperLU")
    return LinearOperator(A.shape, matvec=lu.solve, dtype=float)


def _shape_polys():
    """10-node shape functions as polynomials in the barycentric coordinates: {exponents(4): coefficient}."""
    polys = []
    for i in range(4):  # corner: L_i (2 L_i - 1)
        e2, e1 = [0] * 4, [0] * 4
        e2[i], e1[i] = 2, 1
        polys.append({tuple(e2): 2.0, tuple(e1): -1.0})
    for i, j in EDGES:  # edge: 4 L_i L_j
        e = [0] * 4
        e[i] += 1
        e[j] += 1
        polys.append({tuple(e): 4.0})
    return polys


def reference_mass() -> np.ndarray:
    """Consistent mass integrals int(N_a N_b) dV / V for the straight-sided 10-node tetrahedron (exact)."""
    polys = _shape_polys()
    f = math.factorial
    M = np.zeros((10, 10))
    for a in range(10):
        for b in range(10):
            s = 0.0
            for ea, ca in polys[a].items():
                for eb, cb in polys[b].items():
                    e = [x + y for x, y in zip(ea, eb)]
                    # int L1^a L2^b L3^c L4^d dV = 3! a! b! c! d! / (a+b+c+d+3)! * V
                    s += ca * cb * 6.0 * np.prod([f(k) for k in e]) / f(sum(e) + 3)
            M[a, b] = s
    return M


M_REF = reference_mass()


def quadratic_mesh(nodes, tets):
    """Add one node per edge: returns (nodes10, tets10 (M,10), edge lookup function)."""
    n = len(nodes)
    loc = np.array(EDGES)
    e = np.sort(tets[:, loc], axis=2).reshape(-1, 2)  # (M*6, 2)
    key = e[:, 0] * n + e[:, 1]
    ukey, inv = np.unique(key, return_inverse=True)
    a, b = ukey // n, ukey % n
    mids = 0.5 * (nodes[a] + nodes[b])
    tets10 = np.hstack([tets, n + inv.reshape(-1, 6)])

    def edge_node(i, j):
        i, j = np.minimum(i, j), np.maximum(i, j)
        pos = np.searchsorted(ukey, i * n + j)
        return n + pos

    return np.vstack([nodes, mids]), tets10, edge_node


def _assemble(p10, t10, B, V, lam_e, mu_e, rho_e, log, chunk=20000):
    """Global stiffness and consistent mass of 10-node tetrahedra (B: corner barycentric gradients (M,4,3))."""
    ndof = 3 * len(p10)
    K = sp.csr_matrix((ndof, ndof))
    M = sp.csr_matrix((ndof, ndof))
    dof = (3 * t10[:, :, None] + np.arange(3)[None, None, :]).reshape(len(t10), 30).astype(np.int32)
    I3 = np.eye(3)
    m_ref = np.einsum("ab,ij->aibj", M_REF, I3).reshape(30, 30)
    for s in range(0, len(t10), chunk):
        sl = slice(s, s + chunk)
        G, v, la, mu, rho = B[sl], V[sl], lam_e[sl], mu_e[sl], rho_e[sl]
        ne = len(G)
        # X[(a,i),(b,j)] = sum_q w_q dNa_i dNb_j (exact for the quadratic integrand), one batched product
        D = np.zeros((ne, len(QPTS), 10, 3))
        for k, q in enumerate(QPTS):
            D[:, k, :4] = (4 * q[:4] - 1)[None, :, None] * G
            for a, (i, j) in enumerate(EDGES):
                D[:, k, 4 + a] = 4 * (q[i] * G[:, j] + q[j] * G[:, i])
        D = D.reshape(ne, len(QPTS), 30)
        X = np.matmul(D.transpose(0, 2, 1), D) * (0.25 * v)[:, None, None]
        X5 = X.reshape(ne, 10, 3, 10, 3)
        S = np.einsum("eakbk->eab", X5)  # grad Na . grad Nb
        # lambda dNa_i dNb_j + mu dNa_j dNb_i + mu delta_ij grad Na . grad Nb
        Ke = la[:, None, None, None, None] * X5 + mu[:, None, None, None, None] * X5.transpose(0, 1, 4, 3, 2)
        Ke += mu[:, None, None, None, None] * S[:, :, None, :, None] * I3[None, None, :, None, :]
        d = dof[sl]
        R, C = np.repeat(d, 30, axis=1).ravel(), np.tile(d, (1, 30)).ravel()
        K = K + sp.coo_matrix((Ke.ravel(), (R, C)), shape=(ndof, ndof)).tocsr()
        Me = (rho * v)[:, None, None] * m_ref[None]
        M = M + sp.coo_matrix((Me.ravel(), (R, C)), shape=(ndof, ndof)).tocsr()
        log(f"Assembled {min(s + chunk, len(t10)):,} of {len(t10):,} elements")
    return K, M


def _outward_normal(p, t, tr, A):
    """Area-weighted outward unit normal of a set of boundary triangles (orientation from the owning tets)."""
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
    ok = own >= 0
    out_dir = np.zeros_like(nA)
    out_dir[ok] = (a + b + c)[ok] / 3 - p[t[own[ok]]].mean(1)
    nA *= np.where(np.einsum("ij,ij->i", nA, out_dir) < 0, -1.0, 1.0)[:, None]
    n = nA.sum(0)
    flat = float(np.linalg.norm(n) / (np.linalg.norm(nA, axis=1).sum() or 1.0))  # 1 = one flat face
    return n / (np.linalg.norm(n) or 1.0), flat


def remote_mass(p, t, tr, A, ndof, m_kg, height):
    """Mass m whose centre of gravity sits `height` above the face (outward normal), coupled to the face corner nodes
    like an RBE3 element: the reference point follows the area-weighted least-squares rigid motion of the face, so
    the mass adds inertia but no stiffness. Rotary inertia: a compact block of height 2*height on the face footprint.
    Returns U (ndof, 6) with the added mass matrix = U U^T, and the CG position."""
    nodes = np.unique(tr)
    w = np.zeros(len(p))
    np.add.at(w, tr.ravel(), np.repeat(A / 3.0, 3))
    w = w[nodes] / w[nodes].sum()
    x = p[nodes]
    c = w @ x
    r = x - c
    n, _ = _outward_normal(p, t, tr, A)
    e = height * n  # CG relative to the face centroid
    J = (w[:, None, None] * ((r ** 2).sum(1)[:, None, None] * np.eye(3) - r[:, :, None] * r[:, None, :])).sum(0)
    J += 1e-12 * np.trace(J) * np.eye(3)  # (a straight row of nodes cannot resist twisting about itself)
    Jinv = np.linalg.inv(J)

    def skew(v):
        return np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])

    # theta = J^-1 sum w_j r_j x u_j ; u_ref = ubar + theta x e = ubar - [e]x theta
    T = np.zeros((6, 3 * len(nodes)))
    Ex = skew(e)
    for k, (wj, rj) in enumerate(zip(w, r)):
        Tt = Jinv @ (wj * skew(rj))
        T[3:, 3 * k:3 * k + 3] = Tt
        T[:3, 3 * k:3 * k + 3] = wj * np.eye(3) - Ex @ Tt
    # footprint second moments about its centroid, exact per triangle: int r r^T dA = A/12 (sum ri ri^T + s s^T)
    v = p[tr] - c  # (n_tri, 3 vertices, 3)
    S = np.einsum("t,tij->ij", A / 12.0, np.einsum("tvi,tvj->tij", v, v) + np.einsum("ti,tj->tij", v.sum(1), v.sum(1)))
    J = (np.trace(S) * np.eye(3) - S) / A.sum()  # lamina inertia per unit mass
    I_rb = m_kg * (J + (2 * height) ** 2 / 12.0 * (np.eye(3) - np.outer(n, n)))
    Lm = np.zeros((6, 6))
    Lm[:3, :3] = math.sqrt(m_kg) * np.eye(3)
    Lm[3:, 3:] = np.linalg.cholesky(I_rb + 1e-12 * np.trace(I_rb) * np.eye(3))
    U = np.zeros((ndof, 6))
    dofs = (3 * nodes[:, None] + np.arange(3)).ravel()
    U[dofs] = T.T @ Lm
    return U, c + e


def band_check(elastic, band_hz, f_top, report, warns):
    """Resonance check of the elastic modes against the excitation band (shared by every modal solver): flags
    modes within +-15 % of it and within the 1.4x design margin, and writes the notes into report / warns."""
    res_risk = []
    if band_hz and elastic:
        lo, hi = band_hz
        for m in elastic:
            if 0.85 * lo <= m["frequency_Hz"] <= 1.15 * hi:
                m["in_excitation_band"] = True
                res_risk.append(m["elastic_index"])
        report["excitation_band_Hz"] = [lo, hi]
        report["modes_in_band"] = res_risk
        if res_risk:
            warns.append(f"Mode(s) {', '.join(map(str, res_risk))} lie within ±15 % of the excitation range "
                         f"{lo:g}-{hi:g} Hz: resonance risk. Stiffen, move supports or change the mass to shift them.")
        elif f_top < 0.85 * lo:
            warns.append(f"All computed modes are below the excitation range ({lo:g} Hz and up): request more modes to "
                         "check the range itself.")
        if f_top < 1.15 * hi and not (f_top < 0.85 * lo):
            warns.append(f"Modes above {f_top:.0f} Hz were not computed, but the check covers up to {1.15 * hi:.0f} Hz "
                         f"(excitation {hi:g} Hz + 15 %): request more modes to check the whole range.")
        near = []
        for m in elastic:  # outside +-15 % but inside the usual 1.4x design margin
            if not m.get("in_excitation_band") and lo / 1.4 <= m["frequency_Hz"] <= 1.4 * hi:
                m["near_excitation_band"] = True
                near.append(m["elastic_index"])
        report["modes_near_band"] = near
        f1 = elastic[0]["frequency_Hz"]
        if f1 > 1.15 * hi:
            caution = f1 < 1.4 * hi
            report["band_caution"] = caution
            report["band_note"] = (f"The first natural frequency ({f1:.0f} Hz) is {f1 / hi:.2f}x the highest excitation "
                                   f"frequency ({hi:g} Hz): no resonance in the range"
                                   + (", but below the recommended 1.4x margin - real mounts are softer than a fixed "
                                      "support, so the real frequency may move into the range." if caution else
                                      ". Real mounts are softer than a fixed support, so keep a margin of about 1.4x "
                                      "or more."))
        if near and not report.get("band_caution"):
            warns.append(f"Mode(s) {', '.join(map(str, near))} are outside ±15 % of the excitation range but within "
                         "1.4x of it: below the usual design margin.")


def cycle_peak(P, Q, R):
    """Largest value over a cycle of f(t) = P cos^2 + Q sin^2 + 2 R sin cos (a quadratic form of a harmonic
    vector a cos(wt) + b sin(wt): P = a.A.a, Q = b.A.b, R = a.A.b)."""
    return 0.5 * (P + Q) + np.sqrt((0.5 * (P - Q)) ** 2 + R ** 2)


def _vm_form(a, b):
    """Bilinear form of the von Mises stress squared for Voigt tensors (..., 6: xx yy zz yz xz xy)."""
    d = lambda v, i, j: v[..., i] - v[..., j]  # noqa: E731
    return (0.5 * (d(a, 0, 1) * d(b, 0, 1) + d(a, 1, 2) * d(b, 1, 2) + d(a, 2, 0) * d(b, 2, 0))
            + 3 * (a[..., 3] * b[..., 3] + a[..., 4] * b[..., 4] + a[..., 5] * b[..., 5]))


def sweep_frequencies(f_modes, f_lo, f_hi, zeta, n=120):
    """Frequencies of the sweep: evenly spaced on a log scale, plus points on and around every resonance in the
    range (a peak of damping zeta is about 2 zeta f wide: an even grid would step over it)."""
    grid = list(np.geomspace(f_lo, f_hi, n))
    for fn in f_modes:
        if f_lo <= fn <= f_hi:
            grid += [fn * (1 + k * zeta) for k in (-3, -2, -1.4, -1, -0.6, -0.3, 0, 0.3, 0.6, 1, 1.4, 2, 3)]
    return np.array(sorted({round(float(f), 9) for f in grid if f_lo <= f <= f_hi}))


def sine_sweep(f_modes, gamma_d, Phi, S, accel, d, zeta, f_lo, f_hi, log=print):
    """Response of a base-excited structure to a sine sweep by modal superposition.
    f_modes (k,) Hz; gamma_d (k,) participation of the mass-normalised modes along the shaking direction d;
    Phi (k, N, 3) mode shapes; S (k, K, 6) their stresses; accel: base acceleration amplitude [m/s2].
    Returns {"f", "stress" (max over the part [Pa]), "disp" [m], "accel" [m/s2] per frequency, "at": function
    f -> (stress amplitude (K,), displacement amplitude (N,))}."""
    wn = 2 * math.pi * np.asarray(f_modes, float)
    Phi = Phi.astype(np.float32)
    S = S.astype(np.float32)
    d = np.asarray(d, float)

    def q_of(f):
        w = 2 * math.pi * f
        return (-gamma_d * accel / (wn ** 2 - w ** 2 + 2j * zeta * wn * w)).astype(np.complex64)

    def fields(f):
        q = q_of(f)
        u = np.tensordot(q, Phi, axes=(0, 0))  # (N, 3) complex: relative displacement
        sig = np.tensordot(q, S, axes=(0, 0))  # (K, 6) complex
        a, b = sig.real, -sig.imag
        vm = np.sqrt(np.maximum(cycle_peak(_vm_form(a, a), _vm_form(b, b), _vm_form(a, b)), 0.0))
        ur, ui = u.real, -u.imag
        disp = np.sqrt(np.maximum(cycle_peak((ur * ur).sum(1), (ui * ui).sum(1), (ur * ui).sum(1)), 0.0))
        acc = (accel * d)[None, :] - (2 * math.pi * f) ** 2 * u  # absolute acceleration
        ar, ai = acc.real, -acc.imag
        acc_amp = np.sqrt(np.maximum(cycle_peak((ar * ar).sum(1), (ai * ai).sum(1), (ar * ai).sum(1)), 0.0))
        return vm, disp, acc_amp

    freqs = sweep_frequencies(f_modes, f_lo, f_hi, zeta)
    out = {"f": freqs, "stress": np.zeros(len(freqs)), "disp": np.zeros(len(freqs)), "accel": np.zeros(len(freqs))}
    for i, f in enumerate(freqs):
        vm, disp, acc = fields(float(f))
        out["stress"][i], out["disp"][i], out["accel"][i] = vm.max(), disp.max(), acc.max()
    log(f"Sine sweep: {len(freqs)} frequencies from {f_lo:.4g} to {f_hi:.4g} Hz")
    out["at"] = fields
    return out


def solve(mesh: dict, body_props: dict, bcs: list, n_modes: int = 10, band_hz=None, log=print, sine=None) -> dict:
    """mesh: nodes, tets, tet_vol, tris, tri_surf (as written by the mesher).
    body_props: {body_id: {"E": Pa, "nu": -, "rho": kg/m3, "name": str, "yield": Pa (optional)}}
    bcs: [{"name","type","faces":[ids],"params":{...}}]; band_hz: (f_min, f_max) excitation range or None.
    sine: {"axis": "x"|"y"|"z", "accel_g": amplitude, "damping_percent": % of critical} for a shake test of the
    fixed supports (sweep over band_hz, or around the computed modes), or None.
    """
    from scipy.sparse.linalg import eigsh

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
        E_e[m], nu_e[m], rho_e[m] = float(bp["E"]), float(bp["nu"]), float(bp["rho"])
    if (E_e <= 0).any() or (rho_e <= 0).any():
        missing = sorted(set(int(x) for x in tv[(E_e <= 0) | (rho_e <= 0)]))
        raise ValueError(f"Bodies {missing} have no material (stiffness and density) assigned.")
    lam_e = E_e * nu_e / ((1 + nu_e) * (1 - 2 * nu_e))
    mu_e = E_e / (2 * (1 + nu_e))

    p10, t10, edge_node = quadratic_mesh(p, t)
    ndof = 3 * len(p10)
    limit = MAX_DOF if _pardiso() is not None else MAX_DOF_SUPERLU
    if ndof > limit:
        raise ValueError(f"The mesh gives {ndof:,} unknowns with quadratic elements (limit {limit:,}"
                         + ("" if limit == MAX_DOF else ", or 400,000 with the optional 'pypardiso' package")
                         + "): use a coarser mesh preset - quadratic elements are accurate on coarser meshes.")
    log(f"Quadratic elements: {len(t10):,} tets, {len(p10):,} nodes, {ndof:,} unknowns")
    B, V = _grads(p, t)
    K, M = _assemble(p10, t10, B, V, lam_e, mu_e, rho_e, log)
    mass_structure = float((rho_e * V).sum())

    # ---- boundary conditions ---------------------------------------------------
    fixed_nodes = set()
    added = np.zeros(len(p10))
    added_total = 0.0
    U_parts = []  # offset masses: M_add = U U^T (rank 6 each)
    nonflat = []
    report_bcs = {}
    fixed_dofs = set()  # single movements held at zero (mirror planes)
    for bc in bcs:
        faces = [int(x) for x in bc.get("faces", [])]
        tr = tris[np.isin(ts, faces)]
        if not len(tr):
            log(f"warning: boundary condition '{bc['name']}' has no mesh faces")
            continue
        A = _tri_area(p, tr)
        prm = bc.get("params", {})
        if bc["type"] == "symmetry":
            from .static_fem import _tri_normals

            n_hat, A_t = _tri_normals(p, t, tr)
            nn = (n_hat * A_t[:, None]).sum(0)
            nn = nn / max(float(np.linalg.norm(nn)), 1e-300)
            ax = int(np.argmax(np.abs(nn)))
            if abs(nn[ax]) < 0.9999 or not (np.abs(n_hat @ nn) > 0.9999).all():
                raise ValueError(f"'{bc['name']}': the modal solver takes mirror planes normal to the x, y or z axis "
                                 "only: turn the part in Geometry so that the plane is aligned with an axis.")
            mids = [edge_node(tr[:, i], tr[:, j]) for i, j in ((0, 1), (1, 2), (0, 2))]
            for nd in np.unique(np.concatenate([tr.ravel()] + mids)):
                fixed_dofs.add(int(3 * nd + ax))
            report_bcs[bc["name"]] = {"type": "symmetry", "area_m2": float(A.sum()), "axis": "xyz"[ax]}
        elif bc["type"] == "fixed_support":
            mids = [edge_node(tr[:, i], tr[:, j]) for i, j in ((0, 1), (1, 2), (0, 2))]
            nodes_bc = np.unique(np.concatenate([tr.ravel()] + mids))
            fixed_nodes.update(int(x) for x in nodes_bc)
            report_bcs[bc["name"]] = {"type": "fixed_support", "area_m2": float(A.sum())}
        elif bc["type"] == "added_mass":
            m_kg = float(prm["mass_kg"])
            h = float(prm.get("cg_height_mm") or 0.0) / 1000
            added_total += m_kg
            report_bcs[bc["name"]] = {"type": "added_mass", "mass_kg": m_kg, "area_m2": float(A.sum()),
                                      "cg_height_mm": h * 1000}
            flat = _outward_normal(p, t, tr, A)[1] if m_kg > 0 else 1.0
            if m_kg > 0 and flat >= 0.95:  # one flat face: the component acts at its CG (CG height 0 = on the face)
                U_k, cg = remote_mass(p, t, tr, A, ndof, m_kg, h)
                U_parts.append(U_k)
                report_bcs[bc["name"]]["cg_m"] = cg.tolist()
            else:
                if m_kg > 0:  # several faces facing different ways: no CG direction, smeared over the faces
                    report_bcs[bc["name"]]["smeared"] = True
                    if h > 0:
                        report_bcs[bc["name"]]["cg_ignored"] = True
                        nonflat.append(bc["name"])  # spread over the face corner nodes by area (a thin layer: translational inertia only)
                added += np.bincount(tr.ravel(), weights=np.repeat(m_kg * A / A.sum() / 3.0, 3), minlength=len(p10))
        elif bc["type"] in ("free",):
            pass
        else:
            raise ValueError(f"Boundary condition type '{bc['type']}' is not supported by the modal solver")
    if added_total:
        M = M + sp.diags(np.repeat(added, 3))

    free = np.ones(ndof, bool)
    if fixed_nodes:
        fn = np.array(sorted(fixed_nodes))
        free[(3 * fn[:, None] + np.arange(3)).ravel()] = False
    if fixed_dofs:
        free[np.array(sorted(fixed_dofs))] = False
    from scipy.sparse.linalg import LinearOperator

    Kf, Mf_s = K[free][:, free].tocsr(), M[free][:, free].tocsr()
    U = np.hstack(U_parts) if U_parts else np.zeros((ndof, 0))
    Uf = U[free]
    nf = int(free.sum())

    def mass_mv(x):  # full mass (sparse + offset masses) on the free dofs
        return Mf_s @ x + (Uf @ (Uf.T @ x) if Uf.shape[1] else 0.0)

    def mass_full(x):
        return M @ x + (U @ (U.T @ x) if U.shape[1] else 0.0)

    Mf = LinearOperator((nf, nf), matvec=mass_mv, dtype=float)
    n_rigid_max = 0 if fixed_nodes else 6
    k = min(int(n_modes) + n_rigid_max, nf - 2)
    # shift below zero: K - sigma M is then positive definite even with free (rigid-body) motion. Scaled to the
    # stiffest material (~1e-3 of its wave-speed frequency c/L): a soft pad must not make the shift tiny, which
    # leaves K - sigma M nearly singular for free parts (the round-10 physics review measured useless modes then)
    c = math.sqrt(float(np.max(E_e / rho_e)))
    size = float(np.linalg.norm(p.max(0) - p.min(0)))
    sigma = -(2 * math.pi * 1e-3 * c / size) ** 2
    log(f"Solving for {k} modes (shift-invert Lanczos)")

    def eig(superlu):
        op0 = _shift_invert((Kf - sigma * Mf_s).tocsr(), log, superlu)
        if Uf.shape[1]:  # Woodbury: (A0 - sigma Uf Uf^T)^-1 with the factorisation of A0 = K - sigma M_sparse
            Z = np.column_stack([op0.matvec(Uf[:, j]) for j in range(Uf.shape[1])])
            S_inv = np.linalg.inv(np.eye(Uf.shape[1]) / (-sigma) + Uf.T @ Z)
            op = LinearOperator((nf, nf), matvec=lambda b: (lambda y: y - Z @ (S_inv @ (Uf.T @ y)))(op0.matvec(b)),
                                dtype=float)
        else:
            op = op0
        w2, vec = eigsh(Kf, k=k, M=Mf, sigma=sigma, which="LM", OPinv=op)
        order = np.argsort(w2)
        w2, vec = w2[order], vec[:, order]
        # rigid-body motion: far below the shift (|sigma| ~ (1e-3 c/L)^2, so this is f < 1e-5 c/L), not relative to the
        # highest mode found (which would depend on how many modes were requested)
        rigid = w2 < 1e-4 * abs(sigma)
        # accuracy of each elastic mode: relative residual of K phi = w^2 M phi
        resid = []
        for i in np.where(~rigid)[0]:
            Mphi = mass_mv(vec[:, i])
            resid.append(float(np.linalg.norm(Kf @ vec[:, i] - w2[i] * Mphi) / (w2[i] * np.linalg.norm(Mphi))))
        return w2, vec, rigid, max(resid, default=0.0)

    w2, vec, rigid, worst_resid = eig(False)
    if worst_resid > 1e-3 and not fixed_nodes and _pardiso() is not None and nf <= MAX_DOF_SUPERLU:
        # a free part: K - sigma M is nearly singular (its rigid-body modes are 1e6 times larger than the elastic
        # ones in the inverse), and PARDISO's factorisation of it is not accurate enough for them (residuals of 1e-2
        # on a free bar, from run to run): SuperLU's is, and a model this size factorises in seconds
        log(f"Residual {worst_resid:.0e} with PARDISO: solving again with SuperLU")
        w2, vec, rigid, worst_resid = eig(True)
    if rigid.any() and fixed_nodes:
        raise ValueError(f"{int(rigid.sum())} rigid-body modes: some parts are not connected to the fixed supports "
                         "(check that touching parts share faces, or add supports).")
    if rigid.sum() > 6:
        raise ValueError(f"{int(rigid.sum())} rigid-body modes (a free part has 6): some parts are not connected to "
                         "each other - check that touching parts share faces.")
    if worst_resid > 1e-3:
        raise RuntimeError(f"The eigen-solution is inaccurate (relative residual {worst_resid:.1e}): the stiffness "
                           "of the parts differs too much (very soft and very stiff materials in a free part?). "
                           "Add a fixed support or check the materials.")

    # ---- post-processing ---------------------------------------------------------
    mass_total = mass_structure + added_total
    r = np.zeros((ndof, 3))
    for d in range(3):
        r[d::3, d] = 1.0
    Mr = np.column_stack([mass_full(r[:, d]) for d in range(3)])
    m_dir = np.array([float(r[:, d] @ Mr[:, d]) for d in range(3)])  # total mass seen in each direction
    # participation of the mass-normalised modes; (M r) on the free dofs includes the coupling to supported nodes
    gamma = vec.T @ Mr[free]
    m_free = np.array([float(r[free, d] @ mass_mv(r[free, d])) for d in range(3)])
    supported = 1.0 - m_free / m_dir  # share of the mass on (or rigidly tied to) fixed nodes: never participates
    m_eff = gamma ** 2
    modes, shapes = [], []
    cum = np.zeros(3)
    for i in range(len(w2)):
        phi = np.zeros(ndof)
        phi[free] = vec[:, i]
        u = phi.reshape(-1, 3)[:n1]
        umax = np.linalg.norm(u, axis=1).max() or 1.0
        f_hz = math.sqrt(max(w2[i], 0.0)) / (2 * math.pi)
        frac = m_eff[i] / m_dir
        if not rigid[i]:
            cum += frac
        based = bool(fixed_nodes) and not rigid[i]  # effective mass needs a base to shake: not for free parts
        modes.append({"mode": i + 1, "frequency_Hz": f_hz, "rigid_body": bool(rigid[i]),
                      "effective_mass_fraction": {"x": float(frac[0]), "y": float(frac[1]), "z": float(frac[2])}
                      if based else None,
                      "dominant_direction": "xyz"[int(np.argmax(frac))] if based else None})
        shapes.append(u / umax)
    elastic = [m for m in modes if not m["rigid_body"]]
    for j, m in enumerate(elastic):
        m["elastic_index"] = m["mode"] = j + 1

    report = {"modes": elastic, "n_rigid_body_modes": int(rigid.sum()), "mass_kg": mass_total,
              "structure_mass_kg": mass_structure, "added_mass_kg": added_total, "boundaries": report_bcs,
              "unknowns": int(free.sum()), "element": "10-node tetrahedron (quadratic, straight edges)",
              "max_relative_residual": worst_resid,
              "effective_mass_basis": "fraction of the total mass, including the mass at the fixed supports",
              "supported_mass_fraction": float(supported.max()) if fixed_nodes else 0.0}
    report["first_frequency_Hz"] = elastic[0]["frequency_Hz"] if elastic else None
    report["cumulative_mass_fraction"] = {"x": float(cum[0]), "y": float(cum[1]), "z": float(cum[2])} \
        if fixed_nodes else None
    warns = []
    f_top = elastic[-1]["frequency_Hz"] if elastic else 0.0
    covered = bool(band_hz) and f_top >= 1.5 * max(band_hz)  # every mode up to 1.5x the excitation is included
    target = 0.8 * (1.0 - supported)  # 80 % of the mass that can move at all
    if fixed_nodes and (cum < target).any() and elastic and not covered:
        low = ", ".join(f"{a} {100 * c:.0f} %" for a, c, tg in zip("xyz", cum, target) if c < tg)
        warns.append(f"The computed modes capture only part of the moving mass ({low} of the total; about 80 % of "
                     "the movable mass is the usual target): request more modes if the vibration source reaches above "
                     f"{f_top:.0f} Hz, the highest frequency found.")
    if 1e-6 < worst_resid <= 1e-3:
        warns.append(f"The eigen-solution is less accurate than usual (relative residual {worst_resid:.0e}); the "
                     "frequencies are still good to a fraction of a percent. Adding a fixed support (if the part is "
                     "mounted) or checking very soft materials improves it.")
    fixed_area = sum(d["area_m2"] for d in report_bcs.values() if d["type"] == "fixed_support")
    total_area = float(_tri_area(p, tris).sum())
    if fixed_area > 0.5 * total_area:
        warns.append(f"{100 * fixed_area / total_area:.0f} % of the surface is fixed: the part can hardly move, so the "
                     "frequencies are unrealistically high. Fix only the faces that are bolted or clamped.")
    if nonflat:
        warns.append(f"Added mass {', '.join(nonflat)}: a CG height needs one flat mounting face (the selected faces "
                     "point in different directions), so it was treated as a thin layer on the faces instead.")
    plain = [b for b, d in report_bcs.items() if d["type"] == "added_mass" and not d.get("cg_height_mm")
             and not d.get("smeared")]
    if plain:
        warns.append(f"No CG height set for {', '.join(repr(x) for x in plain)}: the mass is assumed to sit on the face. "
                     "Enter the real height of the component's centre of gravity - depending on the mode it can move "
                     "the frequencies up or down by tens of percent.")
    if fixed_dofs:
        warns.append("Mirror plane: only the modes that are symmetric across the plane are found (the part moves "
                     "the same on both sides). Modes that are antisymmetric across it are not in this list: run the "
                     "whole part to see every mode.")
    if not fixed_nodes:
        warns.append("No fixed support: free-free modes (the part floating in space). The first six modes are "
                     "rigid-body motion and are not listed as vibration modes.")
    band_check(elastic, band_hz, f_top, report, warns)
    report["warnings"] = warns
    report["assumptions"] = [
        "Linear elastic, isotropic materials; small vibrations; no damping (natural frequencies only)",
        "Touching parts are bonded (no sliding or gaps); fixed supports are perfectly rigid - real mounts and bolted "
        "joints are softer (a fully fixed bolt bore is stiffer than a washer clamp), so the real frequencies are "
        "usually somewhat lower",
        "No pre-stress (bolt preload, spin stiffening or thermal stress are not included)",
        "Added masses carry inertia only, no stiffness: with a CG height they act at their CG through the face's "
        "average rigid motion (like an RBE3 element), with the rotary inertia of a compact block of twice that "
        "height on the face; without it they are a thin layer on the face",
        "Quadratic elements with straight edges: curved surfaces are approximated by the mesh facets",
    ]
    fields = {}
    if sine and not fixed_nodes:
        warns.append("Shake test skipped: it shakes the fixed supports, and this part has none (free-free).")
    elif sine and elastic:
        from .static_fem import _corner_stress, average_stress, von_mises

        ax = "xyz".index(sine["axis"])
        d = np.eye(3)[ax]
        accel = float(sine.get("accel_g") or 1.0) * 9.81
        zeta = float(sine.get("damping_percent") or 2.0) / 100
        f_modes = np.sqrt(np.maximum(w2, 0.0)) / (2 * math.pi)
        f_lo, f_hi = (band_hz if band_hz and band_hz[1] > band_hz[0] else (0.0, 0.0))
        if f_hi <= 0:  # no range given: around the computed modes
            f_lo, f_hi = f_modes[0] / 3, f_modes[-1]
        f_lo, f_hi = float(max(f_lo, f_modes[0] / 20)), float(f_hi)  # (a log sweep cannot start at zero; far below the first mode nothing moves)
        Phi, S, keys = [], [], None
        for i in range(len(w2)):  # mass-normalised shapes and their stresses (averaged per body, as in a static run)
            phi = np.zeros(ndof)
            phi[free] = vec[:, i]
            avg, key, key_node, key_body = average_stress(t, tv, _corner_stress(p10, t10, B, phi, lam_e, mu_e), n1)
            Phi.append(phi.reshape(-1, 3)[:n1])
            S.append(avg)
        sw = sine_sweep(f_modes, gamma[:, ax], np.array(Phi), np.array(S), accel, d, zeta, f_lo, f_hi, log)
        peaks = []
        for m in elastic:
            fn = m["frequency_Hz"]
            if f_lo <= fn <= f_hi:
                vm, disp, acc = sw["at"](fn)
                peaks.append({"mode": m["elastic_index"], "frequency_Hz": fn, "max_stress_MPa": float(vm.max() / 1e6),
                              "max_displacement_mm": float(disp.max() * 1e3),
                              "max_acceleration_g": float(acc.max() / 9.81)})
        i_w = int(np.argmax(sw["stress"]))
        f_w = float(sw["f"][i_w])
        vm, disp, acc = sw["at"](f_w)
        order = np.argsort(vm)  # one stress per node for display: the body with the higher stress at an interface
        pick = np.zeros(n1, np.int64)
        pick[key_node[order]] = order
        fields = {"Shake test: stress amplitude": (vm[pick], "Pa"),
                  "Shake test: displacement amplitude": (disp, "m")}
        yields = {int(b): float(bp.get("yield") or 0) for b, bp in body_props.items()}
        sy = np.array([yields.get(int(b), 0.0) for b in key_body])
        sf = float((sy[sy > 0] / np.maximum(vm[sy > 0], 1e-30)).min()) if (sy > 0).any() else None
        report["sine"] = {
            "axis": sine["axis"], "accel_g": accel / 9.81, "damping_percent": zeta * 100, "range_Hz": [f_lo, f_hi],
            "frequency_Hz": [float(x) for x in sw["f"]], "max_stress_MPa": [float(x / 1e6) for x in sw["stress"]],
            "max_displacement_mm": [float(x * 1e3) for x in sw["disp"]],
            "max_acceleration_g": [float(x / 9.81) for x in sw["accel"]],
            "peaks": peaks, "amplification_at_resonance": 1 / (2 * zeta),
            "worst": {"frequency_Hz": f_w, "max_stress_MPa": float(vm.max() / 1e6),
                      "max_displacement_mm": float(disp.max() * 1e3), "max_acceleration_g": float(acc.max() / 9.81),
                      "safety_factor_yield": sf},
        }
        if sf is not None and sf < 1:
            warns.append(f"Shake test: the stress amplitude at {f_w:.0f} Hz ({vm.max() / 1e6:.0f} MPa) exceeds the "
                         "yield strength. Vibration damage is fatigue: the limit for many cycles is well below yield.")
        if not peaks:
            warns.append(f"Shake test: no natural frequency lies in the swept range {f_lo:.4g}-{f_hi:.4g} Hz, so "
                         "there is no resonance peak in it.")
        if f_hi > 1.05 * f_modes[-1]:
            warns.append(f"Shake test: the sweep goes up to {f_hi:.0f} Hz but modes above {f_modes[-1]:.0f} Hz were "
                         "not computed - request more modes for the upper part of the range.")
        report["assumptions"].append(
            f"Shake test: steady response to a sine of constant acceleration at the fixed supports, modal "
            f"superposition of the computed modes with {zeta * 100:g} % damping in every mode (measure it if you "
            "can: the peaks scale with 1 / damping); amplitudes are the largest value during a cycle")
    report["seconds"] = round(time.time() - t0, 2)
    log(f"Solved in {report['seconds']} s: first mode "
        + (f"{report['first_frequency_Hz']:.1f} Hz" if report["first_frequency_Hz"] else "rigid only"))
    return {"modes": modes, "shapes": shapes, "report": report, "fields": fields}

