"""CalculiX input deck (.inp) from the workbench mesh, materials and face boundary conditions.

SI units throughout (m, Pa, kg, N). 10-node tetrahedra (C3D10) built from the same quadratic mesh as the built-in
solver, so both solve exactly the same discrete problem. Loads are written as nodal forces with the same consistent
distribution as the built-in solver (a 6-node triangle under uniform traction puts 1/3 of its load on each mid-edge
node), so differences between the solvers are the solvers', not the loading's.

Boundary conditions
    fixed_support        all three displacements zero on the face nodes
    displacement         prescribed components
    frictionless_support one displacement zero - only on flat faces normal to a coordinate axis (a curved face or a
                         tilted plane needs a rotated local system per node: not written yet, a clear error says so)
    force / pressure     nodal forces (*CLOAD)
    gravity              *DLOAD GRAV on every body
    thermal (optional)   *EXPANSION per material with the stress-free reference temperature (ZERO=), an *INITIAL
                         CONDITIONS TYPE=TEMPERATURE at that reference and a nodal *TEMPERATURE field in the step
                         (corner temperatures of the built-in heat solution, mid-edge nodes the mean of their corners
                         = the linear interpolation the built-in solver uses). A plain *STATIC step: one-way
                         coupling (CalculiX's own *COUPLED TEMPERATURE-DISPLACEMENT is not used).
    orthotropic bodies   *ELASTIC, TYPE=ENGINEERING CONSTANTS with an *ORIENTATION for the material axes
"""
from __future__ import annotations

import numpy as np

from ..modal_fem import EDGES, quadratic_mesh
from ..static_fem import _tri_normals

# CalculiX numbers the C3D10 mid-edge nodes 5..10 on edges 1-2, 2-3, 3-1, 1-4, 2-4, 3-4 - the same order as
# modal_fem.EDGES, so the quadratic mesh needs no renumbering. Swapping corners 2 and 3 (to fix a negative volume)
# moves the mid-edge nodes like this:
_FLIP = [0, 2, 1, 3, 6, 5, 4, 7, 9, 8]


def _ids(lines: list, ids, per_line: int = 12):
    ids = [int(i) for i in ids]
    for k in range(0, len(ids), per_line):
        lines.append(", ".join(str(i) for i in ids[k:k + per_line]))


def oriented_tets(p, t10):
    """Quadratic tets with positive volume (CalculiX stops on a negative Jacobian)."""
    a, b, c, d = (p[t10[:, i]] for i in range(4))
    vol = np.einsum("ij,ij->i", np.cross(b - a, c - a), d - a)
    out = t10.copy()
    neg = vol < 0
    out[neg] = t10[neg][:, _FLIP]
    return out, int(neg.sum())


class Deck:
    """The deck text plus what the readers need: node count of the corner mesh, the support node set."""

    def __init__(self):
        self.lines: list = []
        self.n_corner = 0
        self.n_nodes = 0
        self.n_elements = 0
        self.support_nodes: list = []  # 1-based ids of every constrained node (for the reaction totals)
        self.flipped = 0
        self.report_bcs: dict = {}
        self.applied = np.zeros(3)
        self.mass = 0.0
        self.direct_load = np.zeros(3)  # load applied on the support nodes themselves (ccx's RF leaves it out)

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


def _face_nodes(tr, edge_node):
    mids = np.stack([edge_node(tr[:, i], tr[:, j]) for i, j in ((0, 1), (1, 2), (0, 2))], 1)
    return mids, np.unique(np.concatenate([tr.ravel(), mids.ravel()]))


def _axis_of(n_hat, A):
    """Coordinate axis a flat face is normal to (None when the face is curved or tilted)."""
    nn = (n_hat * A[:, None]).sum(0)
    nn = nn / max(float(np.linalg.norm(nn)), 1e-300)
    k = int(np.argmax(np.abs(nn)))
    ok = abs(nn[k]) > 0.9999 and bool((np.abs(np.einsum("ij,j->i", n_hat, nn)) > 0.9999).all())
    return k if ok else None


def _material_lines(bid, bp, thermal):
    """*MATERIAL block (without the section): elastic constants, density and, for a thermal study, the expansion."""
    from ...materials import orthotropic_alpha, orthotropic_compliance, material_axes

    L = [f"*MATERIAL, NAME=M{int(bid)}"]
    o = bp.get("orthotropic")
    if o:
        material_axes(o)  # (validate the axes and the constants)
        orthotropic_compliance(o)
        L += ["*ELASTIC, TYPE=ENGINEERING CONSTANTS",
              ", ".join(f"{float(o[k]):.9e}" for k in ("E1", "E2", "E3")) + ", "
              + ", ".join(f"{float(o[k]):.6f}" for k in ("nu12", "nu13", "nu23")) + ", "
              + ", ".join(f"{float(o[k]):.9e}" for k in ("G12", "G13")),
              f"{float(o['G23']):.9e}, 0."]
    else:
        L += ["*ELASTIC", f"{float(bp['E']):.9e}, {float(bp['nu']):.6f}"]
    L += ["*DENSITY", f"{float(bp['rho']):.6e}"]
    if thermal is not None:
        amap = thermal.get("alpha") or {}
        a = next((amap[k] for k in (bid, int(bid), str(bid)) if k in amap), None)
        if a is None:
            a = bp.get("alpha")
        if a is None and o:
            a = orthotropic_alpha(o)
        if a is None:
            raise ValueError(f"{bp.get('name', bid)}: no thermal expansion coefficient (alpha) for this material.")
        zero = f", ZERO={float(thermal.get('T_ref_C', 20.0)):.9g}"
        a = np.atleast_1d(np.asarray(a, float))
        if a.size == 3 and o:
            L += [f"*EXPANSION, TYPE=ORTHO{zero}", f"{a[0]:.9e}, {a[1]:.9e}, {a[2]:.9e}, 0."]
        else:
            L += [f"*EXPANSION{zero}", f"{a[0]:.9e}"]
    return L


def build(mesh: dict, body_props: dict, bcs: list, mode: str, gravity=None, n_modes: int = 10, log=print,
          thermal=None) -> Deck:
    """mode: "static" or "modal". body_props: {id: {E, nu, rho, name}}; bcs as in the built-in solvers.
    thermal (static only): {"T_C": corner-node temperatures [degC], "T_ref_C": stress-free temperature,
    "alpha": {body id: [1/K]}} - see the module docstring."""
    if thermal is not None and mode != "static":
        raise ValueError("A thermal load is only written for static analyses.")
    p = np.asarray(mesh["nodes"], float)
    t = mesh["tets"].astype(np.int64)
    tv = mesh["tet_vol"]
    tris = mesh["tris"].astype(np.int64)
    ts = mesh["tri_surf"]
    d = Deck()
    d.n_corner = len(p)

    for bid, bp in body_props.items():
        if (tv == int(bid)).any() and float(bp.get("E") or 0) <= 0 and not bp.get("orthotropic"):
            raise ValueError(f"Body {bp.get('name', bid)} has no material (stiffness) assigned.")

    p10, t10, edge_node = quadratic_mesh(p, t)
    t10_raw = t10
    t10, d.flipped = oriented_tets(p10, t10)
    d.n_nodes, d.n_elements = len(p10), len(t10)
    L = d.lines
    L += ["** Mechanical Simulation Workbench - CalculiX input deck", "*NODE, NSET=NALL"]
    L += [f"{i + 1}, {x:.9e}, {y:.9e}, {z:.9e}" for i, (x, y, z) in enumerate(p10)]

    for bid, bp in body_props.items():
        sel = np.where(tv == int(bid))[0]
        if not len(sel):
            continue
        L.append(f"*ELEMENT, TYPE=C3D10, ELSET=B{int(bid)}")
        L += [f"{int(e) + 1}, " + ", ".join(str(int(n) + 1) for n in t10[e]) for e in sel]
        L += _material_lines(bid, bp, thermal)
        if bp.get("orthotropic"):  # (axes: a point on material axis 1, a point in the 1-2 plane)
            from ...materials import material_axes

            R = material_axes(bp["orthotropic"])
            L += [f"*ORIENTATION, NAME=OR{int(bid)}", ", ".join(f"{x:.9f}" for x in (*R[0], *R[1]))]
        L.append(f"*SOLID SECTION, ELSET=B{int(bid)}, MATERIAL=M{int(bid)}"
                 + (f", ORIENTATION=OR{int(bid)}" if bp.get("orthotropic") else ""))
        d.mass += float(bp["rho"]) * float(
            np.abs(np.einsum("ij,ij->i", np.cross(p[t[sel, 1]] - p[t[sel, 0]], p[t[sel, 2]] - p[t[sel, 0]]),
                             p[t[sel, 3]] - p[t[sel, 0]])).sum() / 6.0)

    loads = np.zeros((len(p10), 3))
    boundary: list = []  # (set name, first dof, last dof, value)
    support = set()
    sets: list = []
    for k, bc in enumerate(bcs):
        faces = [int(x) for x in bc.get("faces", [])]
        kind = bc["type"]
        if kind == "added_mass":
            if mode == "modal":
                raise ValueError("Added masses are not supported by the CalculiX modal solver yet - use the "
                                 "built-in FEM for this study.")
            continue
        sel = np.isin(ts, faces)
        tr = tris[sel]
        if not len(tr):
            log(f"warning: boundary condition '{bc['name']}' has no mesh faces")
            continue
        prm = bc.get("params", {})
        mids, nodes = _face_nodes(tr, edge_node)
        n_hat, A = _tri_normals(p, t, tr)
        d.report_bcs[bc["name"]] = {"type": kind, "area_m2": float(A.sum())}
        name = f"BC{k + 1}"
        if kind in ("fixed_support", "displacement", "frictionless_support", "symmetry"):
            sets.append((name, nodes))
        if kind == "fixed_support":
            boundary.append((name, 1, 3, None))
            support.update(int(n) + 1 for n in nodes)
        elif kind == "displacement":
            for c, key in enumerate(("ux_mm", "uy_mm", "uz_mm")):
                v = prm.get(key)
                if v is not None and v != "":
                    boundary.append((name, c + 1, c + 1, float(v) * 1e-3))
            support.update(int(n) + 1 for n in nodes)
        elif kind in ("frictionless_support", "symmetry"):
            ax = _axis_of(n_hat, A)
            if ax is None:
                raise ValueError(f"'{bc['name']}': CalculiX frictionless supports are written for flat faces normal "
                                 "to the x, y or z axis only (a hole or a tilted plane needs a rotated local system "
                                 "per node) - use the built-in FEM for this support, or turn the part to align the "
                                 "face with an axis.")
            boundary.append((name, ax + 1, ax + 1, None))
            support.update(int(n) + 1 for n in nodes)
        elif kind in ("force", "pressure"):
            if mode == "modal":
                continue
            if kind == "force":
                F = np.array([float(prm.get(q) or 0.0) for q in ("fx_N", "fy_N", "fz_N")])
                tri_F = (A / A.sum())[:, None] * F[None, :]
            else:
                tri_F = -float(prm.get("pressure_Pa") or 0.0) * (A[:, None] * n_hat)
            for col in range(3):
                np.add.at(loads, mids[:, col], tri_F / 3.0)
            d.applied += tri_F.sum(0)
            d.report_bcs[bc["name"]]["force_N"] = tri_F.sum(0).tolist()
        elif kind != "free":
            raise ValueError(f"Boundary condition type '{kind}' is not supported by the CalculiX solver")

    if not boundary and mode == "static":
        raise ValueError("The part is not supported: add a fixed support (or supports that stop it moving freely).")
    if not boundary and mode == "modal":
        raise ValueError("The CalculiX modal solver needs a fixed support (a free part has six rigid-body modes): "
                         "add one, or use the built-in FEM for free-free modes.")
    if thermal is not None:
        L += ["*INITIAL CONDITIONS, TYPE=TEMPERATURE", f"NALL, {float(thermal.get('T_ref_C', 20.0)):.9g}"]
    for name, nodes in sets:
        L.append(f"*NSET, NSET={name}")
        _ids(L, nodes + 1)
    d.support_nodes = sorted(support)
    if d.support_nodes:
        L.append("*NSET, NSET=SUPPORTS")
        _ids(L, d.support_nodes)
    L.append("*BOUNDARY")
    for name, a, b, v in boundary:
        L.append(f"{name}, {a}, {b}" + (f", {v:.9e}" if v is not None else ""))

    L.append("*STEP")
    if mode == "modal":
        L += ["*FREQUENCY", f"{int(n_modes)}", "*NODE FILE", "U"]
    else:
        L.append("*STATIC")
        if thermal is not None:
            Tc = np.asarray(thermal["T_C"], float)
            if Tc.shape != (len(p),):
                raise ValueError(f"The temperature field has {Tc.size} values but the mesh has {len(p)} corner nodes.")
            T10 = np.zeros(len(p10))
            T10[:len(p)] = Tc
            for a, (i, j) in enumerate(EDGES):
                T10[t10_raw[:, 4 + a]] = 0.5 * (Tc[t10_raw[:, i]] + Tc[t10_raw[:, j]])
            L.append("*TEMPERATURE")
            L += [f"{i + 1}, {T10[i]:.9g}" for i in range(len(p10))]
        nz = np.argwhere(loads != 0.0)
        if len(nz):
            L.append("*CLOAD")
            L += [f"{int(i) + 1}, {int(c) + 1}, {loads[i, c]:.9e}" for i, c in nz]
        g = np.asarray(gravity, float) if gravity is not None else np.zeros(3)
        if np.any(g):
            mag = float(np.linalg.norm(g))
            u = g / mag
            L.append("*DLOAD")
            L += [f"B{int(bid)}, GRAV, {mag:.6f}, {u[0]:.9f}, {u[1]:.9f}, {u[2]:.9f}"
                  for bid in body_props if (tv == int(bid)).any()]
        # CalculiX's RF is the internal force at a constrained node: a load applied directly on a support node goes
        # into the support without showing in RF. Add it back to the reported reaction (same consistent loads as the
        # built-in solver: gravity corner -1/20, mid-edge 1/5 of the element weight).
        if d.support_nodes:
            sup = np.zeros(len(p10), bool)
            sup[np.array(d.support_nodes) - 1] = True
            d.direct_load = loads[sup].sum(0)
            if np.any(g):
                rho_e = np.zeros(len(t))
                for bid, bp in body_props.items():
                    rho_e[tv == int(bid)] = float(bp["rho"])
                a_, b_, c_, e_ = (p[t[:, i]] for i in range(4))
                w = rho_e * np.abs(np.einsum("ij,ij->i", np.cross(b_ - a_, c_ - a_), e_ - a_)) / 6.0
                for a in range(10):
                    share = -0.05 if a < 4 else 0.2
                    on = sup[t10[:, a]]
                    d.direct_load = d.direct_load + (share * w[on, None] * g[None, :]).sum(0)
        L += ["*NODE FILE", "U", "*EL FILE", "S"]
        if d.support_nodes:
            L += ["*NODE PRINT, NSET=SUPPORTS, TOTALS=ONLY", "RF"]
    L.append("*END STEP")
    return d
