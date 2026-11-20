"""openCFS engine: XML and mesh writers, result reader, installer logic and the solve plumbing run without the program
(the program itself is replaced by a writer of its output file); the analytic benchmarks run it for real and are
skipped unless cfs is installed (Settings, or SIMWB_OPENCFS pointing at cfs.exe)."""
import hashlib
import io
import math
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import numpy as np
import pytest

from simwb.solvers.opencfs import api, installer, meshio, results, solve, xmlwriter

try:
    import gmsh
except ImportError:  # (a requirement of the workbench; only the benchmarks need it here)
    gmsh = None

NS = {"s": xmlwriter.NS_SIM, "m": xmlwriter.NS_MAT}


@pytest.fixture(autouse=True)
def _fresh_detection():
    installer._cache.clear()
    yield
    installer._cache.clear()


def quiet(_m):
    return None


def grid_mesh(nx=3, ny=3, nz=4, split=2, size=0.1):
    """A box of nx*ny*nz cubes (6 tets each), solid below layer `split`, air above; conformal at the interface.
    Faces: tri_surf 1 = bottom (z = 0), 2 = top, 3 = the four sides."""
    idx = lambda i, j, k: (k * (ny + 1) + j) * (nx + 1) + i  # noqa: E731
    nodes = np.array([[i * size, j * size, k * size] for k in range(nz + 1) for j in range(ny + 1)
                      for i in range(nx + 1)], float)
    kuhn = [(0, 1, 3, 7), (0, 1, 5, 7), (0, 2, 3, 7), (0, 2, 6, 7), (0, 4, 5, 7), (0, 4, 6, 7)]
    tets, vol = [], []
    for k in range(nz):
        for j in range(ny):
            for i in range(nx):
                c = [idx(i + a, j + b, k + d) for d in (0, 1) for b in (0, 1) for a in (0, 1)]
                for t in kuhn:
                    tets.append([c[q] for q in t])
                    vol.append(1 if k < split else 2)
    tets = np.array(tets)
    # boundary triangles from the faces seen once
    f, _ = meshio._tet_faces(tets)
    key = meshio._key(f, len(nodes))
    uniq, inv, cnt = np.unique(key, return_inverse=True, return_counts=True)
    once = cnt[inv] == 1
    tris = f[once]
    c = nodes[tris].mean(1)
    surf = np.where(c[:, 2] < 1e-9, 1, np.where(c[:, 2] > nz * size - 1e-9, 2, 3))
    return {"nodes": nodes, "tets": tets, "tet_vol": np.array(vol), "tris": tris, "tri_surf": surf}


def signed_vol(p, t):
    return np.einsum("ij,ij->i", np.cross(p[t[:, 1]] - p[t[:, 0]], p[t[:, 2]] - p[t[:, 0]]), p[t[:, 3]] - p[t[:, 0]])


PROPS = {1: {"E": 2.1e11, "nu": 0.3, "rho": 7850.0, "name": "plate"}, 2: {"fluid": True, "name": "air"}}
BCS = [{"name": "fix", "type": "fixed_support", "faces": [1], "params": {}},
       {"name": "push", "type": "force", "faces": [3], "params": {"fx_N": 2.0, "fy_N": 0.0, "fz_N": 0.0}}]
SET = {"f_min_hz": 100, "f_max_hz": 300, "n_freq": 3}


# ---------------------------------------------------------------- unit tests, no program

def test_msh_file_is_gmsh_22_with_named_groups_and_negative_tets(tmp_path):
    m = grid_mesh()
    fs = meshio.face_sets(m, lambda b: b == 2)
    vols = {"solid": m["tets"][m["tet_vol"] == 1], "air": m["tets"][m["tet_vol"] == 2]}
    wr = meshio.write_msh(tmp_path / "a.msh", m["nodes"], vols, {"couple": fs["interface"]},
                          {"probe": np.array([0, 5])})
    txt = (tmp_path / "a.msh").read_text()
    assert txt.startswith("$MeshFormat\n2.2 0 8\n$EndMeshFormat\n$PhysicalNames\n4\n")
    r = meshio.read_msh(tmp_path / "a.msh")
    assert r["format"] == "2.2"
    assert sorted(v for v in r["names"].values()) == [(0, "probe"), (2, "couple"), (3, "air"), (3, "solid")]
    kinds = [(e[0], r["names"][e[1]][1]) for e in r["elements"]]
    assert kinds.count((4, "solid")) == int((m["tet_vol"] == 1).sum())
    assert kinds.count((4, "air")) == int((m["tet_vol"] == 2).sum())
    assert kinds.count((2, "couple")) == len(fs["interface"]) == 18
    assert kinds.count((15, "probe")) == 2
    tt = np.array([e[2] for e in r["elements"] if e[0] == 4]) - 1
    assert (signed_vol(r["nodes"], tt) < 0).all()  # openCFS's orientation (verified: gmsh's positive gets "reoriented")
    assert len(wr["used"]) == len(m["nodes"])


def test_unused_nodes_are_dropped_and_mapped_back(tmp_path):
    m = grid_mesh()
    air = m["tets"][m["tet_vol"] == 2]
    wr = meshio.write_msh(tmp_path / "a.msh", m["nodes"], {"air": air})
    used = np.unique(air)
    assert (wr["used"] == used).all() and len(used) < len(m["nodes"])
    r = meshio.read_msh(tmp_path / "a.msh")
    assert np.allclose(r["nodes"], m["nodes"][used])


def test_face_sets_find_the_wet_surface_and_the_outer_faces():
    m = grid_mesh(3, 3, 4, split=2)
    fs = meshio.face_sets(m, lambda b: b == 2)
    assert len(fs["interface"]) == 3 * 3 * 2  # two triangles per cell face at the contact plane
    assert np.allclose(m["nodes"][fs["interface"]][:, :, 2], 0.2)
    assert (fs["interface_surf"] == -1).all()  # (the workbench mesh lists no triangle there in this synthetic mesh)
    st, ss = fs["solid_boundary"]
    ft, fsurf = fs["fluid_boundary"]
    assert set(ss) == {1, 3} and set(fsurf) == {2, 3}
    assert len(st) == 3 * 3 * 2 + 4 * 3 * 2 * 2 and len(ft) == 3 * 3 * 2 + 4 * 3 * 2 * 2


def test_simulation_xml_has_the_verified_elements():
    model = {"mesh": "m.msh", "material_file": "mat.xml", "solid_regions": [("solid_1", "mat_1")],
             "fluid_regions": [("air_2", "gas_2")], "surface_regions": ["couple", "load", "open"],
             "frequencies": [100.0, 200.0, 300.0], "coupling": "couple", "fixed": ["fix"],
             "tractions": [("load", (0, 0, 5.0), 0.0)], "pressures": [("load2", 3.0, 0.0)],
             "absorbing": [("open", "air_2")], "velocities": [("vel", 0.01, 0.0)]}
    root = ET.fromstring(xmlwriter.simulation_xml(model))
    assert root.tag == f"{{{xmlwriter.NS_SIM}}}cfsSimulation"
    assert root.find("s:fileFormats/s:input/s:gmsh", NS).get("fileName") == "m.msh"
    assert root.find("s:fileFormats/s:output/s:gmsh", NS).get("binaryFormat") == "no"
    assert root.find("s:fileFormats/s:materialData", NS).get("file") == "mat.xml"
    assert root.find("s:domain", NS).get("geometryType") == "3d"
    regs = {r.get("name"): r.get("material") for r in root.findall("s:domain/s:regionList/s:region", NS)}
    assert regs == {"solid_1": "mat_1", "air_2": "gas_2"}
    assert [e.get("name") for e in root.findall("s:domain/s:surfRegionList/s:surfRegion", NS)] == \
        ["couple", "load", "open"]
    h = root.find("s:sequenceStep/s:analysis/s:harmonic", NS)
    assert h.find("s:numFreq", NS).text == "3"
    assert [f.get("value") for f in h.findall("s:frequencyList/s:freq", NS)] == ["100", "200", "300"]
    mech = root.find("s:sequenceStep/s:pdeList/s:mechanic", NS)
    assert mech.get("subType") == "3d"  # (the default is planeStrain: "does not fit to problem geometry 3d")
    assert mech.find("s:bcsAndLoads/s:fix", NS).get("name") == "fix"
    assert len(mech.findall("s:bcsAndLoads/s:fix/s:comp", NS)) == 3
    tz = [c for c in mech.findall("s:bcsAndLoads/s:traction/s:comp", NS) if c.get("dof") == "z"][0]
    assert tz.get("value") == "5"
    assert mech.find("s:bcsAndLoads/s:pressure", NS).get("value") == "3"
    ac = root.find("s:sequenceStep/s:pdeList/s:acoustic", NS)
    assert ac.get("formulation") == "acouPressure"
    assert ac.find("s:bcsAndLoads/s:absorbingBCs", NS).get("volumeRegion") == "air_2"
    assert ac.find("s:bcsAndLoads/s:normalVelocity", NS).get("value") == "0.01"
    cp = root.find("s:sequenceStep/s:couplingList/s:direct/s:acouMechDirect/s:surfRegionList/s:surfRegion", NS)
    assert cp.get("name") == "couple"
    assert root.find("s:sequenceStep/s:linearSystems/s:system/s:solverList/s:directLU", NS) is not None


def test_no_coupling_block_without_an_interface_and_eigen_analysis():
    model = {"mesh": "m.msh", "material_file": "mat.xml", "solid_regions": [], "fluid_regions": [("a", "g")],
             "eigen": 8, "coupling": None}
    root = ET.fromstring(xmlwriter.simulation_xml(model))
    assert root.find("s:sequenceStep/s:couplingList", NS) is None
    assert root.find("s:sequenceStep/s:pdeList/s:mechanic", NS) is None
    e = root.find("s:sequenceStep/s:analysis/s:eigenFrequency", NS)
    assert e.find("s:numModes", NS).text == "8"
    assert root.find("s:sequenceStep/s:linearSystems/s:system/s:eigenSolverList/s:arpack", NS) is not None


def test_material_xml_has_bulk_modulus_and_loss_factor():
    x = xmlwriter.material_xml({"steel": {"E": 2.1e11, "nu": 0.3, "rho": 7850}}, {"air": {"rho": 1.2, "c": 343.0}},
                               0.04, 1000.0)
    root = ET.fromstring(x)
    st = root.find("m:material[@name='steel']/m:mechanical", NS)
    assert float(st.find("m:density/m:linear/m:real", NS).text) == 7850
    assert float(st.find("m:elasticity/m:linear/m:isotropic/m:elasticityModulus/m:real", NS).text) == 2.1e11
    d = st.find("m:damping/m:rayleigh/m:computeFromTanDeltaAtFrequency", NS)
    assert float(d.find("m:lossTangensDelta", NS).text) == 0.04 and float(d.find("m:centerFreq", NS).text) == 1000
    ac = root.find("m:material[@name='air']/m:acoustic", NS)
    assert float(ac.find("m:compressionModulus/m:linear/m:real", NS).text) == pytest.approx(1.2 * 343.0 ** 2)
    no_damp = ET.fromstring(xmlwriter.material_xml({"s": {"E": 1, "nu": 0.3, "rho": 1}}, {}, 0.0, 100.0))
    assert no_damp.find(".//m:damping", NS) is None


def test_frequencies():
    assert xmlwriter.frequencies(100, 200, 3) == [100.0, 150.0, 200.0]
    assert xmlwriter.frequencies(100, 400, 3, "log") == pytest.approx([100.0, 200.0, 400.0])
    assert xmlwriter.frequencies(250, 250, 5) == [250.0] and xmlwriter.frequencies(250, 400, 1) == [250.0]
    with pytest.raises(ValueError):
        xmlwriter.frequencies(0, 100, 5)
    with pytest.raises(ValueError):
        xmlwriter.frequencies(200, 100, 5)


GMSH_RESULT = """$MeshFormat
2.2 0 8
$EndMeshFormat
$NodeData
1
"acouPressure (Pa)-amp"
1
100
3
0
1
2
1 4.0
3 5.5
$EndNodeData
$NodeData
1
"acouPressure (Pa)-phase"
1
100
3
0
1
2
1 -82.0
3 -179.9
$EndNodeData
$NodeData
1
"mechDisplacement (m)-amp"
1
100
3
0
3
2
1 3e-06
 4e-06
 0
2 1e-06
 0
 0
$EndNodeData
$NodeData
1
"acouPressure (Pa)-amp"
1
200
3
1
1
2
1 8.0
3 11.0
$EndNodeData
"""


def test_result_reader_parses_scalar_and_wrapped_vector_blocks(tmp_path):
    f = tmp_path / "job.msh"
    f.write_text(GMSH_RESULT)
    bl = results.read_nodedata(f)
    assert len(bl) == 4
    b = bl[2]
    assert (b["name"], b["unit"], b["kind"], b["freq"], b["ncomp"]) == ("mechDisplacement", "m", "amp", 100.0, 3)
    assert b["ids"].tolist() == [1, 2] and b["data"][0].tolist() == [3e-6, 4e-6, 0.0]
    fr, p = results.amplitudes(bl, "acouPressure", 4)
    assert fr.tolist() == [100.0, 200.0]
    assert p.tolist() == [[4.0, 0.0, 5.5, 0.0], [8.0, 0.0, 11.0, 0.0]]  # nodes without data are 0; phase not mixed in
    fr, u = results.amplitudes(bl, "mechDisplacement", 3)
    assert u[0].tolist() == pytest.approx([5e-6, 1e-6, 0.0]) and u[1].tolist() == [0.0, 0.0, 0.0]
    with pytest.raises(ValueError, match="holds"):
        (tmp_path / "bad.msh").write_text(GMSH_RESULT.replace("1 4.0\n3 5.5", "1 4.0"))
        results.read_nodedata(tmp_path / "bad.msh")


def test_eigenfrequency_table_is_parsed_from_the_log():
    log = """++ Creating PDE 'acoustic'

     Frequency in Hz |           Errorbound
----------------------------------------
         1.77647e-05 |          0.000204176
             429.263 |          6.66155e-19
              573.03 |          4.34327e-19

>> Total wall-clock time"""
    assert api.parse_eigenfrequencies(log) == [1.77647e-05, 429.263, 573.03]


def test_check_setup_messages():
    m = grid_mesh()
    assert solve.check_setup(m, PROPS, BCS, SET) == []
    assert "air" in solve.check_setup(m, {1: PROPS[1], 2: {"E": 1, "nu": .3, "rho": 1}}, BCS, SET)[0]
    assert "excitation" in solve.check_setup(m, PROPS, BCS[:1], SET)[0]
    assert "frequency range" in solve.check_setup(m, PROPS, BCS, {"f_min_hz": 0, "f_max_hz": 10})[0]
    air_only = {"nodes": m["nodes"], "tets": m["tets"][m["tet_vol"] == 2], "tet_vol": m["tet_vol"][m["tet_vol"] == 2]}
    assert any("structural" in x for x in solve.check_setup(air_only, PROPS, [BCS[0]], SET))
    assert solve.spl_db(20e-6 * math.sqrt(2.0)) == pytest.approx(0.0)


def fake_cfs(monkeypatch, capture):
    """Replace the cfs program: write the result file openCFS would, with pressure = 3 Pa x frequency / 100 on
    every air node and 1e-6 m displacement on every solid node, and remember the files that were written."""
    def run(workdir, xml_name, mesh, on_proc=None, log=print, **kw):
        wd = Path(workdir)
        capture.update(xml=(wd / xml_name).read_text(), mat=(wd / "materials.xml").read_text(),
                       msh=meshio.read_msh(wd / mesh), kw=kw)
        msh = capture["msh"]
        names = msh["names"]
        air = sorted({n for e in msh["elements"] if e[0] == 4 and names[e[1]][1].startswith("air") for n in e[2]})
        sol = sorted({n for e in msh["elements"] if e[0] == 4 and names[e[1]][1].startswith("solid") for n in e[2]})
        fr = xmlwriter.frequencies(100, 300, 3)
        out = ["$MeshFormat", "2.2 0 8", "$EndMeshFormat"]
        for i, f in enumerate(fr):
            out += ["$NodeData", "1", '"acouPressure (Pa)-amp"', "1", f"{f:g}", "3", str(i), "1", str(len(air))]
            out += [f"{n} {3.0 * f / 100:g}" for n in air] + ["$EndNodeData"]
            out += ["$NodeData", "1", '"mechDisplacement (m)-amp"', "1", f"{f:g}", "3", str(i), "3", str(len(sol))]
            out += [f"{n} 0\n 0\n 1e-06" for n in sol] + ["$EndNodeData"]
        (wd / "results_gmsh").mkdir(exist_ok=True)
        (wd / "results_gmsh" / "job.msh").write_text("\n".join(out) + "\n")
        return {"results_gmsh": wd / "results_gmsh" / "job.msh", "log": "", "frequencies": fr, "eigenfrequencies": []}

    monkeypatch.setattr(api, "run_cfs", run)
    monkeypatch.setattr(installer, "detect", lambda refresh=False: {"available": True, "version": "26.09"})


def test_solve_writes_the_case_and_maps_results_back(monkeypatch, tmp_path):
    cap = {}
    fake_cfs(monkeypatch, cap)
    m = grid_mesh()
    bcs = BCS + [{"name": "mic", "type": "absorbing", "faces": [2], "params": {}}]
    res = solve.solve_vibroacoustics(m, PROPS, bcs, dict(SET, monitors=[{"name": "m1", "point": [0.15, 0.15, 0.35]}]),
                                     tmp_path, log=quiet)
    xml = ET.fromstring(cap["xml"])
    # two regions, coupling at the interface, structure fixed at the bottom, force over the +x... face 3 (sides)
    assert {r.get("name") for r in xml.findall("s:domain/s:regionList/s:region", NS)} == {"solid_1", "air_2"}
    names = {n for _, n in cap["msh"]["names"].values()}
    assert {"couple", "bc1_fixed_support", "bc2_force", "bc3_absorbing", "radiation"} <= names
    # open-air default: the air's side faces (3) have no condition -> radiation; the top (2) has its own absorbing
    tr = xml.find("s:sequenceStep/s:pdeList/s:mechanic/s:bcsAndLoads/s:traction", NS)
    area = 4 * 0.3 * 0.2  # the four side faces of the solid part: 4 x 0.3 m x 0.2 m
    fx = [c for c in tr.findall("s:comp", NS) if c.get("dof") == "x"][0]
    assert float(fx.get("value")) * area == pytest.approx(2.0, rel=1e-6)  # traction integrates to the 2 N force
    ab = [a.get("name") for a in xml.findall("s:sequenceStep/s:pdeList/s:acoustic/s:bcsAndLoads/s:absorbingBCs", NS)]
    assert ab == ["bc3_absorbing", "radiation"]
    # the material file carries the damping: ratio 2 % -> loss factor 0.04 at the band's geometric centre
    d = ET.fromstring(cap["mat"]).find(".//m:computeFromTanDeltaAtFrequency", NS)
    assert float(d.find("m:lossTangensDelta", NS).text) == pytest.approx(0.04)
    assert float(d.find("m:centerFreq", NS).text) == pytest.approx(math.sqrt(100 * 300))
    # results
    f = res["fields"]
    air = np.unique(m["tets"][m["tet_vol"] == 2])
    sol = np.unique(m["tets"][m["tet_vol"] == 1])
    assert f["Sound pressure amplitude"][1] == "Pa" and f["Displacement amplitude"][1] == "mm"
    p = f["Sound pressure amplitude"][0]
    assert np.allclose(p[air], 9.0)  # the strongest response is at 300 Hz
    assert np.allclose(p[np.setdiff1d(sol, air)], 0.0)  # (the wet surface's nodes belong to both)
    assert np.allclose(f["Displacement amplitude"][0][sol], 1e-3)  # 1e-6 m = 1e-3 mm
    rep = res["report"]
    assert rep["peak_frequency_Hz"] == 300.0 and rep["peak_pressure_Pa"] == pytest.approx(9.0)
    assert rep["peak_spl_dB"] == pytest.approx(solve.spl_db(9.0)) and len(rep["response"]) == 3
    mon = rep["monitors"][0]
    assert mon["pressure_Pa"] == pytest.approx([3.0, 6.0, 9.0]) and mon["displacement_mm"] == pytest.approx([1e-3] * 3)
    assert res["sweep"]["pressure_Pa"].shape == (3, len(m["nodes"]))
    assert "The strongest response is at the edge" in " ".join(rep["warnings"])


def test_open_air_can_be_switched_off_and_empty_faces_warn(monkeypatch, tmp_path):
    cap = {}
    fake_cfs(monkeypatch, cap)
    bcs = BCS + [{"name": "ghost", "type": "force", "faces": [99], "params": {"fx_N": 1}}]
    res = solve.solve_vibroacoustics(grid_mesh(), PROPS, bcs, dict(SET, absorbing_boundary=False), tmp_path, log=quiet)
    xml = ET.fromstring(cap["xml"])
    assert xml.find("s:sequenceStep/s:pdeList/s:acoustic/s:bcsAndLoads", NS) is None  # sound-hard walls: natural BC
    assert any("ghost" in w for w in res["report"]["warnings"])


def test_unsupported_condition_and_missing_material_are_refused(tmp_path):
    with pytest.raises(ValueError, match="not supported"):
        solve.solve_vibroacoustics(grid_mesh(), PROPS, BCS + [{"name": "x", "type": "heat_flux", "faces": [3]}],
                                   SET, tmp_path, log=quiet)
    with pytest.raises(ValueError, match="material"):
        solve.solve_vibroacoustics(grid_mesh(), {1: {"E": 0, "nu": 0.3, "rho": 1, "name": "bad"}, 2: PROPS[2]},
                                   BCS, SET, tmp_path, log=quiet)


def test_coarse_air_mesh_is_flagged(monkeypatch, tmp_path):
    fake_cfs(monkeypatch, {})
    res = solve.solve_vibroacoustics(grid_mesh(size=0.1), PROPS, BCS, dict(SET, f_max_hz=2000), tmp_path, log=quiet)
    assert any("too coarse" in w for w in res["report"]["warnings"])


# ---------------------------------------------------------------- installer logic with mocks

class _Job:
    progress, proc = 0.0, None

    def check_cancel(self):
        pass


class _JM:
    def update(self, *a, **k):
        pass


def test_pinned_download_is_a_checksummed_https_gitlab_asset():
    assert re.fullmatch(r"[0-9a-f]{64}", installer.SHA256)
    assert installer.URL.startswith("https://gitlab.com/api/v4/projects/12930334/packages/generic/openCFS/")
    assert installer.URL.endswith("Windows.zip") and installer.SIZE_BYTES < 1.5e9


class _Resp(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_download_refuses_a_wrong_checksum(monkeypatch, tmp_path):
    monkeypatch.setattr(installer.urllib.request, "urlopen", lambda req, timeout=0: _Resp(b"not the real zip"))
    with pytest.raises(RuntimeError, match="checksum"):
        installer._download("https://example.invalid/x.zip", tmp_path / "x.zip", installer.SHA256, _Job(), _JM())
    data = b"pinned payload"
    monkeypatch.setattr(installer.urllib.request, "urlopen", lambda req, timeout=0: _Resp(data))
    installer._download("https://example.invalid/u", tmp_path / "y.zip", hashlib.sha256(data).hexdigest(), _Job(), _JM())
    assert (tmp_path / "y.zip").read_bytes() == data


def test_download_refuses_an_oversized_file(monkeypatch, tmp_path):
    monkeypatch.setattr(installer, "MAX_BYTES", 10)
    monkeypatch.setattr(installer.urllib.request, "urlopen", lambda req, timeout=0: _Resp(b"x" * 100))
    with pytest.raises(RuntimeError, match="larger"):
        installer._download("https://example.invalid/u", tmp_path / "x.zip", "0" * 64, _Job(), _JM())


def _zip(path, names):
    with zipfile.ZipFile(path, "w") as z:
        for n in names:
            z.writestr(n, "x")


def test_unzip_refuses_paths_outside_the_folder(tmp_path):
    _zip(tmp_path / "evil.zip", ["CFS/bin/cfs.exe", "../outside.txt"])
    (tmp_path / "dest").mkdir()
    with pytest.raises(RuntimeError, match="unsafe"):
        installer.safe_extract(tmp_path / "evil.zip", tmp_path / "dest")
    assert not (tmp_path / "outside.txt").exists() and not any((tmp_path / "dest").iterdir())


def test_install_unpacks_into_the_workbench_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("SIMWB_OPENCFS_DIR", str(tmp_path / "opencfs"))
    monkeypatch.delenv("SIMWB_OPENCFS", raising=False)
    monkeypatch.setattr(installer, "supported", lambda: True)
    monkeypatch.setattr(installer, "_exe_name", lambda: "cfs.exe")
    monkeypatch.setattr(installer, "_download", lambda url, dest, sha, job, jm: _zip(
        dest, ["CFS-9-Windows/", "CFS-9-Windows/bin/cfs.exe", "CFS-9-Windows/license/LICENSE"]))
    monkeypatch.setattr(installer, "_version", lambda exe: "26.09")
    monkeypatch.setattr(installer.shutil, "which", lambda n: None)
    info = installer.install(_Job(), _JM())
    exe = tmp_path / "opencfs" / "CFS-9-Windows" / "bin" / "cfs.exe"
    assert info["available"] and Path(info["exe"]) == exe and info["source"] == "workbench copy"
    assert exe.is_file() and (exe.parents[1] / "license" / "LICENSE").is_file()
    assert not any(p.name.startswith("simwb_cfs_") for p in (tmp_path / "opencfs").iterdir())  # temp folder removed


def test_install_rejects_a_zip_with_another_layout(monkeypatch, tmp_path):
    monkeypatch.setenv("SIMWB_OPENCFS_DIR", str(tmp_path / "opencfs"))
    monkeypatch.setattr(installer, "supported", lambda: True)
    monkeypatch.setattr(installer, "_download", lambda url, dest, sha, job, jm: _zip(dest, ["thing/readme.txt"]))
    with pytest.raises(RuntimeError, match="layout"):
        installer.install(_Job(), _JM())


def test_detect_ignores_a_foreign_cfs_and_honours_the_env_override(monkeypatch, tmp_path):
    fake = tmp_path / "bin" / "cfs.exe"
    fake.parent.mkdir()
    fake.write_text("x")
    monkeypatch.setenv("SIMWB_OPENCFS", str(tmp_path))  # the folder of a release: bin/cfs.exe inside it
    monkeypatch.setenv("SIMWB_OPENCFS_DIR", str(tmp_path / "none"))
    monkeypatch.setattr(installer, "_exe_name", lambda: "cfs.exe")
    monkeypatch.setattr(installer.shutil, "which", lambda n: None)
    monkeypatch.setattr(installer, "_version", lambda exe: None)  # some other program called cfs
    assert installer.detect(refresh=True)["available"] is False
    monkeypatch.setattr(installer, "_version", lambda exe: "26.09")
    info = installer.detect(refresh=True)
    assert info["available"] and info["version"] == "26.09" and Path(info["exe"]) == fake


def test_run_cfs_without_the_program_says_how_to_get_it(monkeypatch, tmp_path):
    monkeypatch.setattr(installer, "detect", lambda refresh=False: {"available": False, "exe": None})
    with pytest.raises(RuntimeError, match="not installed"):
        api.run_cfs(tmp_path, "a.xml", "a.msh")


# ---------------------------------------------------------------- analytic benchmarks with the real program

REAL = pytest.mark.skipif(not installer.detect()["available"] or gmsh is None,
                          reason="openCFS (cfs) or gmsh is not installed")


def gmsh_mesh(size=0.0125):
    """After a gmsh geometry is synchronised: mesh it and return the workbench-style mesh dict (nodes, tets,
    tet_vol = volume tags, tris, tri_surf = surface tags). Caller finalizes gmsh."""
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.option.setNumber("Mesh.MeshSizeMax", size)
    gmsh.model.mesh.generate(3)
    tags, xyz, _ = gmsh.model.mesh.getNodes()
    o = np.argsort(tags)
    lut = -np.ones(int(tags.max()) + 1, np.int64)
    lut[np.asarray(tags)[o]] = np.arange(len(tags))
    tets, tv, tris, ts = [], [], [], []
    for _, v in gmsh.model.getEntities(3):
        et, _, en = gmsh.model.mesh.getElements(3, v)
        a = lut[np.asarray(en[list(et).index(4)], np.int64).reshape(-1, 4)]
        tets.append(a)
        tv.append(np.full(len(a), v))
    for _, s in gmsh.model.getEntities(2):
        et, _, en = gmsh.model.mesh.getElements(2, s)
        if 2 in list(et):
            a = lut[np.asarray(en[list(et).index(2)], np.int64).reshape(-1, 3)]
            tris.append(a)
            ts.append(np.full(len(a), s))
    return {"nodes": np.asarray(xyz).reshape(-1, 3)[o], "tets": np.vstack(tets), "tet_vol": np.concatenate(tv),
            "tris": np.vstack(tris), "tri_surf": np.concatenate(ts)}


def surf_tag_at(point_test):
    return [t for _, t in gmsh.model.getEntities(2)
            if point_test(np.array(gmsh.model.occ.getCenterOfMass(2, t)))]


@REAL
def test_benchmark_plate_driven_into_an_anechoic_duct(tmp_path):
    """A free steel plate (50 x 50 x 2 mm) pushed by a 1 N harmonic force radiates a plane wave into a duct that
    ends in an absorbing boundary: the plate moves with v = F / (i w m + rho c A) and the plane wave has
    p = rho c v. (Below the duct's cut-off, so only the plane wave propagates.)"""
    a, t, length = 0.05, 0.002, 0.5
    gmsh.initialize()
    try:
        gmsh.model.add("duct")
        plate = gmsh.model.occ.addBox(0, 0, -t, a, a, t)
        air = gmsh.model.occ.addBox(0, 0, 0, a, a, length)
        gmsh.model.occ.fragment([(3, plate)], [(3, air)])
        gmsh.model.occ.synchronize()
        for _, pt in gmsh.model.getBoundary(gmsh.model.getEntities(3), recursive=True):
            z = gmsh.model.getValue(0, pt, [])[2]
            gmsh.model.mesh.setSize([(0, pt)], 0.004 if abs(z) < 1e-9 or abs(z + t) < 1e-9 else 0.0125)
        back = surf_tag_at(lambda c: abs(c[2] + t) < 1e-9)
        outlet = surf_tag_at(lambda c: abs(c[2] - length) < 1e-9)
        m = gmsh_mesh()
        vols = {int(v): gmsh.model.occ.getCenterOfMass(3, int(v))[2] for v in np.unique(m["tet_vol"])}
    finally:
        gmsh.finalize()
    solid_id = [v for v, z in vols.items() if z < 0][0]
    air_id = [v for v, z in vols.items() if z > 0][0]
    props = {solid_id: {"E": 2.1e11, "nu": 0.3, "rho": 7850.0, "name": "plate"},
             air_id: {"fluid": True, "name": "air", "rho": 1.2, "c": 343.0}}
    bcs = [{"name": "push", "type": "force", "faces": back, "params": {"fz_N": 1.0}},
           {"name": "end", "type": "absorbing", "faces": outlet, "params": {}}]
    res = solve.solve_vibroacoustics(m, props, bcs, {"f_min_hz": 500, "f_max_hz": 1500, "n_freq": 3,
                                                     "absorbing_boundary": False, "damping_percent": 2},
                                     tmp_path, log=quiet)
    p = res["sweep"]["pressure_Pa"]
    air_nodes = np.unique(m["tets"][m["tet_vol"] == air_id])
    mid = air_nodes[(m["nodes"][air_nodes, 2] > 0.2) & (m["nodes"][air_nodes, 2] < 0.3)]
    worst = 0.0
    for i, f in enumerate(res["sweep"]["frequencies_Hz"]):
        w = 2 * math.pi * f
        expect = 1.2 * 343.0 / abs(1j * w * 7850.0 * t * a * a + 1.2 * 343.0 * a * a)
        worst = max(worst, abs(p[i, mid].mean() / expect - 1))
    print(f"duct benchmark: worst plane-wave pressure error {worst * 100:.3f} %")
    assert worst < 0.03, f"plane-wave pressure off by {worst * 100:.2f} %"


@REAL
def test_benchmark_rigid_cavity_eigenfrequencies(tmp_path):
    """f_lmn = c/2 sqrt((l/Lx)^2 + (m/Ly)^2 + (n/Lz)^2) of a rigid-walled air box, within 3 %."""
    lx, ly, lz, c = 0.4, 0.3, 0.2, 343.0
    gmsh.initialize()
    try:
        gmsh.model.add("cav")
        gmsh.model.occ.addBox(0, 0, 0, lx, ly, lz)
        gmsh.model.occ.synchronize()
        m = gmsh_mesh(size=0.02)
    finally:
        gmsh.finalize()
    meshio.write_msh(tmp_path / "m.msh", m["nodes"], {"air": m["tets"]})
    (tmp_path / "mat.xml").write_text(xmlwriter.material_xml({}, {"air": {"rho": 1.2, "c": c}}, 0.0, 100.0))
    (tmp_path / "sim.xml").write_text(xmlwriter.simulation_xml(
        {"mesh": "m.msh", "material_file": "mat.xml", "solid_regions": [], "fluid_regions": [("air", "air")],
         "eigen": 10}))
    out = api.run_cfs(tmp_path, "sim.xml", "m.msh", log=quiet)
    got = [f for f in out["eigenfrequencies"] if f > 1.0]  # (the zero-frequency constant-pressure mode is dropped)
    exact = sorted(c / 2 * math.sqrt((l / lx) ** 2 + (mm / ly) ** 2 + (n / lz) ** 2)
                   for l in range(4) for mm in range(4) for n in range(3) if (l, mm, n) != (0, 0, 0))
    assert len(got) >= 6
    print("cavity benchmark: errors %", [round(100 * (g / e - 1), 3) for g, e in zip(got[:6], exact[:6])])
    for g, e in zip(got[:6], exact[:6]):
        assert g == pytest.approx(e, rel=0.03)


@REAL
def test_benchmark_pulsating_sphere(tmp_path):
    """A rigid sphere (a = 50 mm) whose surface moves radially at v, in an air shell out to R = 400 mm closed by the
    absorbing boundary. The first-order absorbing condition is the impedance rho c on that sphere, so the exact
    answer is the sum of an outgoing and an ingoing spherical wave p = (A e^-ikr + B e^ikr) / r with u(a) = v and
    p(R) = rho c u(R): the pressure amplitude agrees within 5 % between 2a and 5a. (The sphere's facets make its
    area, and so the volume flow, about 3 % smaller than the exact sphere's: that is most of the difference.)"""
    a, big, f, v, rho, c = 0.05, 0.4, 500.0, 0.01, 1.2, 343.0
    gmsh.initialize()
    try:
        gmsh.model.add("sph")
        outer = gmsh.model.occ.addSphere(0, 0, 0, big)
        inner = gmsh.model.occ.addSphere(0, 0, 0, a)
        gmsh.model.occ.cut([(3, outer)], [(3, inner)])
        gmsh.model.occ.synchronize()
        inner_faces = [t for _, t in gmsh.model.getEntities(2) if gmsh.model.getBoundingBox(2, t)[3] < 2 * a]
        outer_faces = [t for _, t in gmsh.model.getEntities(2) if t not in inner_faces]
        gmsh.model.mesh.field.add("Distance", 1)
        gmsh.model.mesh.field.setNumbers(1, "SurfacesList", inner_faces)
        gmsh.model.mesh.field.add("Threshold", 2)
        for k, val in (("InField", 1), ("SizeMin", 0.01), ("SizeMax", 0.045), ("DistMin", 0.02), ("DistMax", 0.25)):
            gmsh.model.mesh.field.setNumber(2, k, val)
        gmsh.model.mesh.field.setAsBackgroundMesh(2)
        gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
        m = gmsh_mesh(size=0.045)
    finally:
        gmsh.finalize()
    props = {int(np.unique(m["tet_vol"])[0]): {"fluid": True, "name": "air", "rho": rho, "c": c}}
    bcs = [{"name": "wall", "type": "normal_velocity", "faces": inner_faces, "params": {"velocity_m_s": v}},
           {"name": "open", "type": "absorbing", "faces": outer_faces, "params": {}}]
    res = solve.solve_vibroacoustics(m, props, bcs, {"f_min_hz": f, "f_max_hz": f, "n_freq": 1}, tmp_path, log=quiet)
    k, w = 2 * math.pi * f / c, 2 * math.pi * f

    def wave(r, sign):  # outgoing (sign -1) / ingoing (+1) spherical wave, its radial velocity
        p = np.exp(sign * 1j * k * r) / r
        dp = np.exp(sign * 1j * k * r) * (sign * 1j * k * r - 1) / r ** 2
        return p, -dp / (1j * w * rho)

    (p_a0, u_a0), (p_a1, u_a1) = wave(a, -1), wave(a, +1)
    (p_r0, u_r0), (p_r1, u_r1) = wave(big, -1), wave(big, +1)
    A, B = np.linalg.solve(np.array([[u_a0, u_a1], [p_r0 - rho * c * u_r0, p_r1 - rho * c * u_r1]]), [v, 0])
    r = np.linalg.norm(m["nodes"], axis=1)
    sel = (r > 2 * a) & (r < 5 * a)
    expect = np.abs(A * wave(r[sel], -1)[0] + B * wave(r[sel], +1)[0])
    err = np.abs(res["sweep"]["pressure_Pa"][0][sel] / expect - 1)
    print(f"sphere benchmark: median error {np.median(err) * 100:.2f} %, max {err.max() * 100:.2f} %")
    assert np.median(err) < 0.05 and err.max() < 0.07, f"sphere pressure off by {np.median(err) * 100:.2f} % (median)"


@REAL
def test_written_xml_uses_only_names_of_the_schema_shipped_with_the_release():
    """(The release's own schema validation does not run, so check the names ourselves.)"""
    root = Path(installer.detect()["exe"]).parents[1] / "share" / "xml"
    known = set()
    for f in root.rglob("*.xsd"):
        known |= set(re.findall(r'name="([A-Za-z0-9_]+)"', f.read_text(errors="replace")))
    model = {"mesh": "m.msh", "material_file": "mat.xml", "solid_regions": [("s", "ms")],
             "fluid_regions": [("a", "ga")], "surface_regions": ["c", "l"], "node_regions": ["n"],
             "frequencies": [1.0, 2.0], "coupling": "c", "fixed": ["l"], "tractions": [("l", (1, 0, 0), 0)],
             "pressures": [("l", 1, 0)], "forces": [("n", (1, 0, 0))], "absorbing": [("c", "a")],
             "velocities": [("c", 1, 0)], "monitors": ["n"]}
    mat = xmlwriter.material_xml({"ms": {"E": 1, "nu": 0.3, "rho": 1}}, {"ga": {"rho": 1, "c": 1}}, 0.04, 100)
    for text in (xmlwriter.simulation_xml(model), xmlwriter.simulation_xml(dict(model, eigen=4)), mat):
        for el in ET.fromstring(text).iter():
            tag = el.tag.split("}")[1]
            assert tag in known, f"{tag} is not a name in the openCFS schema"
            for attr in el.attrib:
                assert attr in known, f"attribute {attr} is not a name in the openCFS schema"
