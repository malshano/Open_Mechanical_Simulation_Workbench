"""A synthetic flow result (a box of 'air' with a block as the 'object' in it) written into a real external-flow study, so that the window's
flow tools can be driven without running OpenFOAM. Use in a test file:   from flow_support import flow_study   # noqa: F401"""
import numpy as np


def flow_resultset(n=7, noise=0.0, seed=1, moving=0, prop=False, symmetry=False):
    """Air = the unit-ish box [-1, 2] x [-1, 1] x [-1, 1]; the object is the block [0, 1] x [-0.25, 0.25] x [-0.25, 0.25] cut out of it.
    prop: a second, small block [1.3, 1.7] x [-0.3, 0.3] x [-0.3, 0.3] (the 'propeller', surface group 'zone1_walls') whose nodes are the ones that move.
    Fields: Velocity (vector, swirling), Pressure, Q-criterion (a smooth blob at the middle plus `noise`). moving: number of extra saved
    times whose nodes move (frames['__nodes']). symmetry: only the half y >= 0 of all this (its cut at y = 0 is the surface group 'symmetry1'), as
    a run with a symmetry plane gives it."""
    from scipy.spatial import Delaunay

    from simwb.results import ResultSet, boundary_from_tets
    gx, gy, gz = np.linspace(-1, 2, 2 * n + 1), (np.linspace(0, 1, n // 2 + 2) if symmetry else np.linspace(-1, 1, n + 1)), np.linspace(-1, 1, n + 1)
    nodes = np.array(np.meshgrid(gx, gy, gz, indexing="ij")).reshape(3, -1).T
    tets = Delaunay(nodes).simplices
    c = nodes[tets].mean(1)
    in_body = (c[:, 0] > 0) & (c[:, 0] < 1) & (np.abs(c[:, 1]) < 0.3) & (np.abs(c[:, 2]) < 0.3)
    in_prop = (c[:, 0] > 1.3) & (c[:, 0] < 1.7) & (np.abs(c[:, 1]) < 0.3) & (np.abs(c[:, 2]) < 0.3) if prop else np.zeros(len(c), bool)
    in_body = in_body | in_prop
    tets = tets[~in_body]
    used = np.unique(tets)
    remap = -np.ones(len(nodes), int)
    remap[used] = np.arange(len(used))
    nodes, tets = nodes[used], remap[tets]
    tris, cells = boundary_from_tets(tets)
    tc = nodes[tris].mean(1)
    far = (np.abs(tc[:, 1]) > 0.99) | (np.abs(tc[:, 2]) > 0.99) | (tc[:, 0] < -0.99) | (tc[:, 0] > 1.99)          # (the faces of the air box)
    onsym = (np.abs(tc[:, 1]) < 1e-9) if symmetry else np.zeros(len(tc), bool)
    isprop = ~far & ~onsym & (tc[:, 0] > 1.2) if prop else np.zeros(len(tc), bool)
    body = ~far & ~isprop & ~onsym
    rng = np.random.default_rng(seed)
    r2 = (nodes[:, 1] ** 2 + nodes[:, 2] ** 2)
    vel = np.stack([10 + 0 * r2, -nodes[:, 2] * 2, nodes[:, 1] * 2], 1)
    q = np.exp(-r2 / 0.2) * np.exp(-((nodes[:, 0] - 1.0) ** 2) / 0.3) * 100.0 + noise * rng.standard_normal(len(nodes))
    fields = {"Velocity": ("node", vel, "m/s"), "Pressure": ("node", 5.0 - 0.5 * nodes[:, 0], "Pa"), "Q-criterion": ("node", q, "1/s²")}
    times, frames = None, None
    if moving:
        times = [float(i) for i in range(moving)]
        mover = np.zeros(len(nodes), bool)                              # (only the propeller's nodes move; without one, all do)
        mover[np.unique(tris[isprop])] = True
        if not prop:
            mover[:] = True
        frames = {"__nodes": [nodes + np.outer(mover, [0.0, 0.05 * i, 0.0]) for i in range(moving)], "Pressure": [fields["Pressure"][1] + i for i in range(moving)]}
    grp = np.where(body, 1, 0)
    names = ["farfield", "body"]
    if prop:
        grp = np.where(isprop, 2, grp)
        names.append("zone1_walls")
    if symmetry:
        grp = np.where(onsym, len(names), grp)
        names.append("symmetry1")
    return ResultSet(nodes, tets, np.zeros(len(tets)), ["air"], fields, tris, cells, grp, names, times=times, frames=frames)


def make_flow_study(ctl, name="Flow test", **kw):
    """A new external-aero study whose result is the synthetic one; opened in the window at the Results step. prop=True also gives it a sliding
    zone 'zone1' round the propeller block."""
    import simwb.project as P
    rs = flow_resultset(**kw)
    pr = P.create_project(name, "external_aero")
    if kw.get("prop"):
        pr.set_moving_zones([{"name": "zone1", "center": [1.5, 0, 0], "axis": "+x", "radius": 0.45, "length": 0.6, "rpm": 600, "method": "sliding"}])
    pr.results_dir.mkdir(exist_ok=True)
    rs.save(pr.results_dir / "result.npz")
    pr._results = rs
    summary = {"default_groups": [g for g in rs.groups if g not in ("farfield", "symmetry1")], "speed_m_s": 10.0, "U_inf": [10.0, 0, 0],
               "coefficients": {"L_ref_m": 1.0}}
    if kw.get("symmetry"):                   # (solved on the half y >= 0: the plane is the study's, as Setup keeps it, and the run's, as the summary keeps it)
        from simwb import symmetry as sym
        plane = sym.make_plane([0, 0, 0], [0, 1, 0], "Symmetry plane 1")
        pr.data["symmetry"] = [plane]
        summary["symmetry"] = {"planes": 1, "copies": 2, "definition": [plane]}
    pr.data["solve"].update(status="done", summary=summary)
    pr.save()
    ctl.do("study.open", {"id": pr.id})
    ctl.do("step.go", {"step": "results"})
    return pr, rs
