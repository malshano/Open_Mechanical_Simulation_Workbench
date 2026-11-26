"""A synthetic flow result (a box of 'air' with a block as the 'object' in it) written into a real external-flow study, so that the window's
flow tools can be driven without running OpenFOAM. Use in a test file:   from flow_support import flow_study   # noqa: F401"""
import numpy as np


def flow_resultset(n=7, noise=0.0, seed=1, moving=0):
    """Air = the unit-ish box [-1, 2] x [-1, 1] x [-1, 1]; the object is the block [0, 1] x [-0.25, 0.25] x [-0.25, 0.25] cut out of it.
    Fields: Velocity (vector, swirling), Pressure, Q-criterion (a smooth blob at the middle plus `noise`). moving: number of extra saved
    times whose nodes move (frames['__nodes'])."""
    from scipy.spatial import Delaunay

    from simwb.results import ResultSet, boundary_from_tets
    gx, gy, gz = np.linspace(-1, 2, 2 * n + 1), np.linspace(-1, 1, n + 1), np.linspace(-1, 1, n + 1)
    nodes = np.array(np.meshgrid(gx, gy, gz, indexing="ij")).reshape(3, -1).T
    tets = Delaunay(nodes).simplices
    c = nodes[tets].mean(1)
    in_body = (c[:, 0] > 0) & (c[:, 0] < 1) & (np.abs(c[:, 1]) < 0.3) & (np.abs(c[:, 2]) < 0.3)
    tets = tets[~in_body]
    used = np.unique(tets)
    remap = -np.ones(len(nodes), int)
    remap[used] = np.arange(len(used))
    nodes, tets = nodes[used], remap[tets]
    tris, cells = boundary_from_tets(tets)
    tc = nodes[tris].mean(1)
    body = (tc[:, 0] > -1e-9) & (tc[:, 0] < 1 + 1e-9) & (np.abs(tc[:, 1]) < 0.3 + 1e-9) & (np.abs(tc[:, 2]) < 0.3 + 1e-9)
    rng = np.random.default_rng(seed)
    r2 = (nodes[:, 1] ** 2 + nodes[:, 2] ** 2)
    vel = np.stack([10 + 0 * r2, -nodes[:, 2] * 2, nodes[:, 1] * 2], 1)
    q = np.exp(-r2 / 0.2) * np.exp(-((nodes[:, 0] - 1.0) ** 2) / 0.3) * 100.0 + noise * rng.standard_normal(len(nodes))
    fields = {"Velocity": ("node", vel, "m/s"), "Pressure": ("node", 5.0 - 0.5 * nodes[:, 0], "Pa"), "Q-criterion": ("node", q, "1/s²")}
    times, frames = None, None
    if moving:
        times = [float(i) for i in range(moving)]
        frames = {"__nodes": [nodes + np.array([0.1 * i, 0, 0]) for i in range(moving)], "Pressure": [fields["Pressure"][1] + i for i in range(moving)]}
    return ResultSet(nodes, tets, np.zeros(len(tets)), ["air"], fields, tris, cells, np.where(body, 1, 0), ["farfield", "body"],
                     times=times, frames=frames)


def make_flow_study(ctl, name="Flow test", **kw):
    """A new external-aero study whose result is the synthetic one; opened in the window at the Results step."""
    import simwb.project as P
    rs = flow_resultset(**kw)
    pr = P.create_project(name, "external_aero")
    pr.results_dir.mkdir(exist_ok=True)
    rs.save(pr.results_dir / "result.npz")
    pr._results = rs
    pr.data["solve"].update(status="done", summary={"default_groups": ["body"], "speed_m_s": 10.0, "U_inf": [10.0, 0, 0],
                                                     "coefficients": {"L_ref_m": 1.0}})
    pr.save()
    ctl.do("study.open", {"id": pr.id})
    ctl.do("step.go", {"step": "results"})
    return pr, rs
