"""The mesh metrics of a hex-dominant (polyhedral) mesh and the choice of cells by a metric range (#41, #47), on a small structured hex mesh made here,
so that no OpenFOAM is needed: the volumes, sizes, aspect ratios and size jumps are known exactly."""
import numpy as np
import pytest

from simwb import meshviz
from test_hexmesh import _project, workspace  # noqa: F401


def grid_polymesh(xs, ys, zs):
    """A hex mesh with the given node coordinates along x, y, z (metres) as foamio.read_polymesh returns it: quad faces, internal ones first."""
    nx, ny, nz = len(xs) - 1, len(ys) - 1, len(zs) - 1
    pid = lambda i, j, k: (i * (ny + 1) + j) * (nz + 1) + k  # noqa: E731
    pts = np.array([[x, y, z] for x in xs for y in ys for z in zs], float)
    cid = lambda i, j, k: (i * ny + j) * nz + k  # noqa: E731
    inner, outer = [], []                                      # (quad, owner, neighbour)
    for d in range(3):
        n = (nx, ny, nz)
        for idx in np.ndindex(*[m + (1 if a == d else 0) for a, m in enumerate(n)]):
            i, j, k = idx
            u, v = [a for a in range(3) if a != d]               # (u, v, d) is right-handed in the order (y, z, x), (z, x, y), (x, y, z)
            if d == 0:
                u, v = 1, 2
            elif d == 1:
                u, v = 2, 0
            else:
                u, v = 0, 1

            def corner(du, dv):
                c = list(idx)
                c[u] += du
                c[v] += dv
                return pid(*c)
            quad = [corner(0, 0), corner(1, 0), corner(1, 1), corner(0, 1)]
            lo, hi = list(idx), list(idx)
            lo[d] -= 1
            has_lo, has_hi = idx[d] > 0, idx[d] < n[d]
            if has_lo and has_hi:
                inner.append((quad, cid(*lo), cid(*hi)))
            elif has_hi:
                outer.append((quad[::-1], cid(*hi), -1))         # the low side: outward is -d
            else:
                outer.append((quad, cid(*lo), -1))
    faces = [q for q, _, _ in inner + outer]
    own = np.array([o for _, o, _ in inner + outer])
    nei = np.array([n for _, _, n in inner])
    return {"points": pts, "faces": None, "poly_faces": [np.array(f) for f in faces], "owner": own, "neighbour": nei, "n_cells": nx * ny * nz}


def test_the_metrics_of_hex_cells_are_the_cells_own():
    xs, ys, zs = [0, 1e-3, 2e-3, 4e-3], [0, 1e-3, 2e-3], [0, 1e-3, 3e-3]
    pm = grid_polymesh(xs, ys, zs)
    m = meshviz.polymesh_cell_metrics(pm)
    dx, dy, dz = np.diff(xs), np.diff(ys), np.diff(zs)
    vol = np.array([a * b * c for a in dx for b in dy for c in dz])                      # (cell order of grid_polymesh: x slowest)
    assert m["volume"] == pytest.approx(vol * 1e9, rel=1e-5)
    assert m["size"] == pytest.approx(np.cbrt(vol) * 1000, rel=1e-5)                     # the edge of the cube of the same volume
    asp = np.array([max(a, b, c) / min(a, b, c) for a in dx for b in dy for c in dz])
    assert m["aspect"] == pytest.approx(asp, rel=1e-4)
    assert m["aspect"].min() == pytest.approx(1.0) and m["aspect"].max() == pytest.approx(2.0)
    cube = np.cbrt(vol)
    assert m["growth"][0] == pytest.approx(max(cube[1] / cube[0], cube[0] / cube[1], cube[4] / cube[0], cube[2] / cube[0]), rel=1e-4)
    assert m["growth"].min() >= 1.0


def test_uniform_cells_have_no_size_jump_and_a_unit_aspect_ratio():
    pm = grid_polymesh(*([np.linspace(0, 3e-3, 4)] * 3))
    m = meshviz.polymesh_cell_metrics(pm)
    assert m["growth"] == pytest.approx(1.0) and m["aspect"] == pytest.approx(1.0) and m["size"] == pytest.approx(1.0)


def test_the_summary_counts_cells_not_display_tetrahedra():
    pm = grid_polymesh([0, 1e-3, 2e-3, 4e-3, 8e-3], [0, 1e-3, 2e-3], [0, 1e-3, 2e-3])
    m = meshviz.polymesh_cell_metrics(pm)
    d = meshviz.summarise(m)
    assert d["cells"] == 16 and sum(d["metrics"]["size"]["histogram"]) == 16
    assert sum(b["cells"] for b in d["budget"]) == 16 and all(b["hi"] > b["lo"] for b in d["budget"])
    assert d["metrics"]["growth"]["range"][0] == 1.0 and "range" in d["metrics"]["size"]
    b = d["budget"][0]
    assert len(meshviz.cells_between(m["size"], b["lo"], b["hi"])) == b["cells"]         # a click on a budget band selects its cells


def test_a_hex_project_shows_size_aspect_growth_volume_and_a_band(workspace, tmp_path, monkeypatch):
    import simwb.solvers.openfoam.foamio as foamio
    from simwb.solvers.openfoam.results_loader import display_mesh, display_points

    pm = grid_polymesh([0, 1e-3, 2e-3, 4e-3, 8e-3], [0, 2e-3, 4e-3], [0, 2e-3, 4e-3])
    dm = display_mesh(pm)
    pts = display_points(pm["points"], dm)
    pr = _project(workspace, tmp_path)
    fluid = next(b["id"] for b in pr.bodies() if b["role"] == "fluid")
    q = np.linspace(0.2, 1.0, len(dm["tets"])).astype(np.float32)
    pr.mesh_dir.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(pr.mesh_dir / "mesh.npz", nodes=pts, tets=dm["tets"].astype(np.int32), tet_vol=np.full(len(dm["tets"]), fluid),
                        tris=np.zeros((0, 3), np.int32), tri_surf=np.zeros(0, np.int64), tri_owner=np.zeros(0, np.int64), quality=q,
                        zone_tris=np.zeros((0, 3), np.int32), zone_tri_zone=np.zeros(0, np.int32), hex=np.array([1]))
    monkeypatch.setattr(foamio, "read_polymesh", lambda _d: pm)
    d = pr.mesh_metrics()
    assert d["method"] == "hex" and d["cells"] == 16 and {"size", "aspect", "growth", "volume", "quality"} <= set(d["metrics"])
    assert sum(d["metrics"]["quality"]["histogram"]) == 16
    for metric in ("size", "aspect", "growth", "volume"):
        v = pr.mesh_view(metric=metric, clip_axis="x", clip_pos=0.0015)               # (a cut through the cells)
        assert v["n_tris"] > 0 and v["metric"] == metric
    b = d["budget"][0]
    v = pr.mesh_view(metric="size", band=(b["lo"], b["hi"]))
    assert v["n_cells_in_band"] == b["cells"] and v["n_tris"] > 0 and v["band"] == [b["lo"], b["hi"]]
    assert pr.mesh_view(metric="size", band=(1e9, 2e9))["n_cells_in_band"] == 0
    with pytest.raises(ValueError, match="quality"):
        pr.mesh_view(metric="size", poor=0.5)
    with pytest.raises(ValueError, match="select cells"):
        pr.mesh_view(metric="region", band=(0, 1))


from simwb.solvers.openfoam import runner  # noqa: E402

needs_foam = pytest.mark.skipif(not runner.detect().get("available"), reason="OpenFOAM is not installed")


@needs_foam
def test_a_real_snappyhexmesh_mesh_is_measured_cell_by_cell(workspace, tmp_path):
    """One real hex-dominant mesh (quick preset on a box): the volumes of the cells add up to the volume of the display tetrahedra that fill the same
    domain, the histogram counts cells (not the tetrahedra they are drawn with), and every metric can be drawn and cut."""
    pr = _project(workspace, tmp_path)
    pr.set_mesh(preset="quick", layers={"n": 0})
    pr.set_mesh(method="hex")
    j = pr.generate_mesh()
    assert j.wait(900) and j.status == "done", (j.error, j.log[-15:])
    cells = pr.data["mesh"]["stats"]["cells"]
    d = pr.mesh_metrics()
    assert d["method"] == "hex" and d["cells"] == cells and sum(d["metrics"]["size"]["histogram"]) == cells
    m = pr.load_mesh()
    t, p = m["tets"].astype(np.int64), m["nodes"]
    tet_vol = np.abs(np.einsum("ij,ij->i", np.cross(p[t[:, 1]] - p[t[:, 0]], p[t[:, 2]] - p[t[:, 0]]), p[t[:, 3]] - p[t[:, 0]])).sum() / 6
    cell_vol = pr.hex_cell_data()["metrics"]["volume"].astype(np.float64).sum() * 1e-9
    assert cell_vol == pytest.approx(tet_vol, rel=0.02)
    assert len(pr.hex_cell_data()["tet_cell"]) == len(t) and len(t) > cells
    assert d["metrics"]["aspect"]["min"] >= 0.99 and d["metrics"]["growth"]["min"] >= 1.0
    for metric in ("size", "aspect", "growth", "volume"):
        assert pr.mesh_view(metric=metric)["n_tris"] > 0
        assert pr.mesh_view(metric=metric, clip_axis="y", clip_pos=0.0)["n_tris"] > 0
    b = d["budget"][-1]
    assert pr.mesh_view(metric="size", band=(b["lo"], b["hi"]))["n_cells_in_band"] == b["cells"]
