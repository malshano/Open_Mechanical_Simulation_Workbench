"""A study is saved where the user chooses; the assistant and Claude Code start in that folder and know the study."""
import importlib
import json
from pathlib import Path

import pytest

H = {"X-SimWB": "1"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMWB_HOME", str(tmp_path / "ws"))
    monkeypatch.setenv("SIMWB_PORT", "8791")
    import simwb.project as P

    importlib.reload(P)
    import simwb.server as S

    importlib.reload(S)
    import simwb.tools as T
    from fastapi.testclient import TestClient

    return TestClient(S.app, base_url="http://127.0.0.1:8765"), P, S, T, tmp_path


def test_a_study_in_a_chosen_folder_knows_where_it_is(env):
    c, P, S, T, tmp = env
    pr = P.create_project("Frame modes", "vibration_modal", str(tmp / "mine"))
    assert pr.dir.parent == (tmp / "mine").resolve() and pr.dir != P.WORKSPACE
    md = (pr.dir / "CLAUDE.md").read_text(encoding="utf-8")
    assert str(pr.dir) in md and "Vibration" in md and "vibration_modal" in md and "8791" in md
    cfg = json.loads((pr.dir / ".mcp.json").read_text())
    sv = cfg["mcpServers"]["simwb"]
    assert sv["args"] == ["-m", "simwb.mcp_server"] and sv["env"]["SIMWB_PORT"] == "8791"
    assert Path(sv["env"]["PYTHONPATH"]) == Path(P.ROOT)


def test_other_mcp_servers_in_the_folder_are_kept(env):
    c, P, S, T, tmp = env
    pr = P.create_project("Keep", "heat_conduction", str(tmp / "k"))
    mcp = pr.dir / ".mcp.json"
    cfg = json.loads(mcp.read_text())
    cfg["mcpServers"]["mine"] = {"command": "x"}
    mcp.write_text(json.dumps(cfg))
    P.write_claude_files(pr.dir, 8800)
    cfg = json.loads(mcp.read_text())
    assert cfg["mcpServers"]["mine"] == {"command": "x"} and cfg["mcpServers"]["simwb"]["env"]["SIMWB_PORT"] == "8800"


def test_the_assistant_must_ask_for_the_folder(env):
    c, P, S, T, tmp = env
    out = T.call("create_project", None, {"name": "x", "study": "heat_conduction"})
    assert out.startswith("ERROR") and "where to save" in out
    assert not list(P.WORKSPACE.glob("*/project.json"))
    out = T.call("create_project", None, {"name": "x", "study": "heat_conduction", "location": str(tmp / "chosen")})
    assert not out.startswith("ERROR"), out
    assert (tmp / "chosen" / "x" / "project.json").exists()


def test_open_in_claude_code_starts_in_the_study_folder(env, monkeypatch):
    c, P, S, T, tmp = env
    pr = P.create_project("Open", "heat_conduction", str(tmp / "o"))
    seen = {}
    monkeypatch.setattr(S.agent, "open_in_claude_code", lambda folder: seen.update(folder=folder) or "opened")
    r = c.post(f"/api/projects/{pr.id}/open_claude", json={}, headers=H)
    assert r.status_code == 200 and r.json()["path"] == str(pr.dir) and seen["folder"] == pr.dir
    assert c.post(f"/api/projects/{pr.id}/open_claude", json={}).status_code == 403  # (not from outside the page)


def test_the_launcher_runs_the_claude_cli_only(env, monkeypatch, tmp_path):
    import simwb.agent as A

    calls = []
    # (only the agent module's view of subprocess: patching subprocess.Popen itself breaks libraries imported meanwhile)
    from types import SimpleNamespace
    monkeypatch.setattr(A, "subprocess", SimpleNamespace(Popen=lambda *a, **k: calls.append((a, k)),
                                                         CREATE_NEW_CONSOLE=0x10))
    evil = tmp_path / "a & calc ^ b"
    evil.mkdir()
    A.open_in_claude_code(evil)
    cmd, kw = calls[0][0][0], calls[0][1]
    assert len(cmd) == 1 and "claude" in cmd[0].lower()   # nothing but the CLI: no shell, no folder on the command line
    assert kw.get("cwd") == str(evil) and "cmd" not in cmd[0].lower().split("\\")[-1]


def test_a_section_on_the_nodes_of_the_mesh_has_no_holes():
    """A plane through mesh nodes (a symmetry plane, a block grid) did not cut the cells flat on it: block-shaped
    holes in the contour. Two stacked cubes of tets, the plane on the middle layer of nodes, and the plane at the
    boundary of the mesh: the cut must cover the whole square."""
    import numpy as np
    from simwb.results import ResultSet

    g = np.array([[x, y, z] for z in (0, 1, 2) for y in (0, 1) for x in (0, 1)], float)   # 12 nodes, 2 layers of cubes
    def cube(o):
        i = lambda x, y, z: o + x + 2 * y + 4 * z
        n = [i(x, y, z) for z in (0, 1) for y in (0, 1) for x in (0, 1)]
        return [[n[0], n[1], n[3], n[7]], [n[0], n[3], n[2], n[7]], [n[0], n[2], n[6], n[7]],
                [n[0], n[6], n[4], n[7]], [n[0], n[4], n[5], n[7]], [n[0], n[5], n[1], n[7]]]
    tets = np.array(cube(0) + cube(4), np.int64)
    rs = object.__new__(ResultSet)
    rs.nodes, rs.tets, rs.regions, rs.tet_region = g, tets, ["air"], np.zeros(len(tets), int)
    rs.fields = {"f": ("node", None, "u")}
    rs.nodal = lambda f: g[:, 2].copy()
    for pos in (1.0, 0.0, 2.0):   # on the middle nodes, and on the two ends of the mesh
        d = rs.slice("f", 2, pos)
        pts = np.frombuffer(__import__("base64").b64decode(d["positions"]), np.float32).reshape(-1, 3)
        tri = np.frombuffer(__import__("base64").b64decode(d["indices"]), np.uint32).reshape(-1, 3)
        a = 0.5 * np.linalg.norm(np.cross(pts[tri[:, 1]] - pts[tri[:, 0]], pts[tri[:, 2]] - pts[tri[:, 0]]), axis=1).sum()
        assert abs(a - 1.0) < 1e-3, (pos, a)   # the whole unit square


def test_points_outside_the_mesh_are_not_searched_for(monkeypatch):
    """The other half of a half model (the box of a volume or particle view reaches into it) is outside the mesh's
    bounding box: those points are found nowhere, at once; the points inside are found as before."""
    import numpy as np
    from types import SimpleNamespace
    import simwb.resample as R
    from simwb.resample import Locator

    monkeypatch.setattr(R, "K_NEAR", 4)   # (the toy mesh has six cells)
    g = np.array([[x, y, z] for z in (0, 1) for y in (0, 1) for x in (0, 1)], float)
    n = [0, 1, 3, 7, 0, 3, 2, 7, 0, 2, 6, 7, 0, 6, 4, 7, 0, 4, 5, 7, 0, 5, 1, 7]
    tets = np.array(n, np.int64).reshape(-1, 4)
    rs = SimpleNamespace(nodes=g, tets=tets, regions=["air"], tet_region=np.zeros(len(tets), int))
    Locator._last.clear()
    loc = Locator(rs)
    pts = np.array([[0.4, 0.5, 0.5], [0.5, -2.0, 0.5], [0.5, 0.5, 0.2], [3.0, 3.0, 3.0]])
    tet, lam = loc.locate(pts)
    assert tet[0] >= 0 and tet[2] >= 0 and tet[1] == -1 and tet[3] == -1
    assert abs(lam[0].sum() - 1) < 1e-9
