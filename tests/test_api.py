"""API smoke tests: input validation, security guards, robustness."""
import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
H = {"X-SimWB": "1"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMWB_HOME", str(tmp_path / "ws"))
    import simwb.project as P

    importlib.reload(P)
    import simwb.server as S

    importlib.reload(S)
    from fastapi.testclient import TestClient

    return TestClient(S.app, base_url="http://127.0.0.1:8765"), P


def _project(client, P, study="heat_conduction", example="heated_plate.step"):
    c, _ = client, P
    r = c.post("/api/projects", json={"name": "t", "study": study, "example": example}, headers=H)
    assert r.status_code == 200, r.text
    pid = r.json()["id"] if "id" in r.json() else "t"
    job = P.JOBS.latest(pid, "prepare")
    assert job.wait(120) and job.status == "done", job.error
    return pid


def test_mutations_need_header(client):
    c, P = client
    r = c.post("/api/projects", json={"name": "x", "study": "heat_conduction"})
    assert r.status_code == 403
    r = c.post("/api/projects", json={"name": "x", "study": "heat_conduction"},
               headers={**H, "Origin": "http://evil.example.com"})
    assert r.status_code == 403
    r = c.post("/api/projects", json={"name": "x", "study": "heat_conduction"},
               headers={**H, "Origin": "http://127.0.0.1:8765"})
    assert r.status_code == 200


def test_foreign_host_rejected(client):
    """DNS rebinding: a page on evil.example pointing its name at 127.0.0.1 sends Host: evil.example - refused,
    also for reads."""
    c, P = client
    assert c.get("/api/projects").status_code == 200
    assert c.get("/api/projects", headers={"host": "localhost:8765"}).status_code == 200
    assert c.get("/api/projects", headers={"host": "[::1]:8765"}).status_code == 200
    for host in ("evil.example:8765", "evil.example", "127.0.0.1.evil.example", ""):
        assert c.get("/api/projects", headers={"host": host}).status_code == 403, host
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/ws", headers={"host": "evil.example:8765"}) as ws:
            ws.receive_text()


def test_case_file_edit_cannot_run_programs():
    from simwb.solvers.openfoam import advanced as A
    for text in ('functions { f { type systemCall; executeCalls ("calc.exe"); } }',
                 'x #codeStream { code #{ system("calc"); #}; };', 't systemCall; f { type $t; }',
                 'libs ("C:/Users/x/evil.dll");', '#include "/etc/passwd"', '#include "../../x"',
                 # (a string that looks like a comment cannot hide an entry; nor can other keys or include forms)
                 'a "//"; #codeStream { };', 'x "/*"; f { type coded; } y "*/";', 'libs $myLibs;',
                 'functionObjectLibs ("/tmp/evil.so");', '#sinclude "/etc/passwd"', '#include "$HOME/x"',
                 # (a quoted key is a regular expression: it can stand for 'libs'; a library path under any key)
                 '"l.bs" ("C:/x/evil");', '".*"\n  ( evil );', 'foo ("C:/x/libevil.dll");',
                 # (a pattern key anywhere on a line, in any regex form, with any list form; a directive argument
                 # cannot swallow the next '#')
                 'a 1; "[lm]ibs" (forces);', 'x { } "lib(s)" $v;', '"Lib.*" 2{a};', '#include "a" #codeStream {}',
                 '#includeEtc"x"#calc "1"'):
        with pytest.raises(ValueError, match="not allowed"):
            A.check_safe("system/controlDict", text)
    A.check_safe("system/controlDict", 'libs (forces "libfieldFunctionObjects.so");\n#include "initialConditions"\n'
                                       '#includeEtc "caseDicts/setConstraintTypes"\n#include "<constant>/x"\n'
                                       '".*"\n{\n    type zeroGradient;\n}\n"(k|omega)" 0.7;\n')
    import time
    t0 = time.time()
    for text in ("libs (" + "a" * 30000 + ' ' + '"x' * 5000 + ")", "#" + "a" * 500000, "/*" * 200000, '"' * 400000,
                 "x" * 500000 + "Coded"):
        try:
            A.check_safe("system/controlDict", text)
        except ValueError:
            pass
    assert time.time() - t0 < 5, "the check must stay linear on hostile input"


def test_compressed_case_file_is_checked(tmp_path):
    import gzip
    from simwb.solvers.openfoam import advanced as A
    (tmp_path / "0").mkdir()
    with gzip.open(tmp_path / "0" / "U.gz", "wt") as f:
        f.write('internalField uniform (0 0 0);\nx #codeStream { };\n')
    with pytest.raises(ValueError, match="not allowed"):
        A.apply(tmp_path, {}, {})


def test_websocket_rejects_foreign_origin(client):
    c, P = client
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/ws", headers={"origin": "http://evil.example.com"}) as ws:
            ws.receive_text()


def test_path_traversal_rejected(client):
    c, P = client
    for pid in ["..%5Cws", "..", "a/../b", "..\\x"]:
        r = c.get(f"/api/projects/{pid}")
        assert r.status_code in (400, 404), (pid, r.status_code)
    r = c.delete("/api/projects/..%5C..%5Cx", headers=H)
    assert r.status_code in (400, 404)


def test_unknown_example_no_orphan(client):
    c, P = client
    r = c.post("/api/projects", json={"name": "y", "study": "heat_conduction", "example": "nope.step"}, headers=H)
    assert r.status_code == 400
    assert c.get("/api/projects").json() == []


def test_nan_and_bad_values_rejected(client):
    c, P = client
    pid = _project(c, P)
    st = c.get(f"/api/projects/{pid}").json()
    face = st["faces"][0]["id"] if "faces" in st else 1
    bad = [
        ("post", f"/api/projects/{pid}/bcs", {"type": "fixed_temperature", "faces": [face], "params": {"T": "nan"}}),
        ("post", f"/api/projects/{pid}/bcs", {"type": "convection", "faces": [face], "params": {"h": "Infinity"}}),
        ("post", f"/api/projects/{pid}/bcs", {"type": "fixed_temperature", "faces": [face], "params": {"T": -400}}),
        ("post", f"/api/projects/{pid}/bodies/1", {"heat_W": "nan"}),
        ("post", f"/api/projects/{pid}/mesh/settings", {"size_max_mm": -5}),
        ("post", f"/api/projects/{pid}/mesh/settings", {"refinements": [{"faces": [9999], "size_mm": 1}]}),
    ]
    for m, url, body in bad:
        r = getattr(c, m)(url, json=body, headers=H)
        assert r.status_code == 400, (url, body, r.text)
    # project still loads
    assert c.get(f"/api/projects/{pid}").status_code == 200


def test_settings_validation(client):
    c, P = client
    r = c.post("/api/projects", json={"name": "z", "study": "cht_enclosure"}, headers=H)
    pid = r.json()["id"]
    for body in ({"iterations": None}, {"iterations": 1e9}, {"n_procs": 1000}, {"T_initial": -300},
                 {"turbulence": "magic"}):
        r = c.post(f"/api/projects/{pid}/settings", json=body, headers=H)
        assert r.status_code == 400, (body, r.text)


def test_one_job_at_a_time(client):
    c, P = client
    pid = _project(c, P)
    pr = P.open_project(pid)
    j = pr.generate_mesh()
    with pytest.raises(RuntimeError, match="still running"):
        pr.prepare_geometry()
    j.wait(120)


def test_tool_arguments_validated(client):
    c, P = client
    from simwb import tools as T

    pid = _project(c, P)
    pr = P.open_project(pid)
    assert T.call("find_faces", pr, {"bogus": 1}).startswith("ERROR: unknown argument")
    assert T.call("rename_project", pr, {"name": "   "}).startswith("ERROR")
    assert T.call("add_boundary_condition", pr, {}).startswith("ERROR: missing argument")


def test_mesh_check_never_touches_user_projects(client):
    """A user project whose id looks like a mesh-check twin must survive a mesh check of its 'parent'."""
    c, P = client
    pid = _project(c, P)
    other = P.create_project(f"{pid} meshcheck", "heat_conduction")
    assert other.id == f"{pid}-meshcheck"
    pr = P.open_project(pid)
    bottom = pr.find_faces(normal="-z", at="min_z")[0]["id"]
    pr.add_bc("Cold", "fixed_temperature", [bottom], {"T": 20})
    pr.set_body(1, heat_W=5)
    pr.set_mesh(preset="coarse")
    j = pr.generate_mesh(); j.wait(120)
    j = pr.solve(); j.wait(120)
    j = pr.mesh_check(); j.wait(300)
    assert j.status == "done", j.error
    assert (P.WORKSPACE / other.id / "project.json").exists()
    assert other.id in [p["id"] for p in P.list_projects()]
    assert pr.data["mesh_check"]["rows"]


def test_project_saved_in_chosen_folder(client, tmp_path):
    """A study can be saved in a folder of the user's choice, listed, reopened from the folder and deleted."""
    c, P = client
    where = tmp_path / "my sims"
    r = c.post("/api/projects", json={"name": "Wing", "study": "heat_conduction", "location": str(where)}, headers=H)
    assert r.status_code == 200, r.text
    st = r.json()
    assert Path(st["path"]) == (where / "Wing").resolve() and (where / "Wing" / "project.json").exists()
    assert any(p["id"] == "Wing" and Path(p["path"]) == (where / "Wing").resolve() for p in c.get("/api/projects").json())
    # a second study of the same name gets its own folder; a relative folder is refused
    r2 = c.post("/api/projects", json={"name": "Wing", "study": "heat_conduction", "location": str(where)}, headers=H)
    assert r2.json()["id"] == "Wing-2" and (where / "Wing-2").is_dir()
    assert c.post("/api/projects", json={"name": "x", "study": "heat_conduction", "location": "rel/dir"},
                  headers=H).status_code == 400
    # forget the registry and open the folder again
    P._save_locations({})
    assert not any(p["id"] == "Wing" for p in c.get("/api/projects").json())
    r3 = c.post("/api/projects/open_folder", json={"path": str(where / "Wing")}, headers=H)
    assert r3.status_code == 200 and r3.json()["name"] == "Wing"
    assert c.post("/api/projects/open_folder", json={"path": str(tmp_path)}, headers=H).status_code == 400
    assert c.delete("/api/projects/Wing", headers=H).status_code == 200 and not (where / "Wing").exists()


def test_claude_terminal_allowlist():
    """The Claude terminal passes only fixed commands (or one question without shell/edit tools) to the claude CLI."""
    import simwb.server as S

    assert S.terminal_args(["auth", "logout"]) == ["auth", "logout"]
    q = S.terminal_args(["-p", "what is Q?"])
    assert q[:2] == ["-p", "what is Q?"] and "Bash" in q[-1] and "--dangerously-skip-permissions" not in q
    for bad in (["-p", "x", "--dangerously-skip-permissions"], ["--dangerously-skip-permissions"],
                ["-p", "--allowedTools", "Bash"], ["--settings", "x.json"], ["auth", "login", "--x"], ["mcp", "add", "a"], []):
        with pytest.raises(ValueError):
            S.terminal_args(bad)


def test_software_table_and_calculix_status_routes(client):
    c, P = client
    r = c.post("/api/projects", json={"name": "sw", "study": "static_structural"}, headers=H)
    assert r.status_code == 200, r.text
    d = c.get("/api/projects/sw/software").json()
    assert d["engine"] == "builtin" and {r["element"] for r in d["elements"]} >= {"geometry", "meshing", "solver"}
    assert [x["engine"] for x in d["choices"]] == ["builtin", "calculix"]
    s = c.get("/api/calculix").json()
    assert "available" in s and "installable" in s
    assert c.get("/api/meta").json()["studies"]["static_structural"]["settings"]


def test_results_export_and_report_routes(client):
    """Results leave the workbench as VTK (binary arrays that read back exactly), CSV (one row per node, cell
    fields at the nodes) and a self-contained report; text from the study is escaped, a picture must be an image."""
    import base64
    import xml.dom.minidom

    import numpy as np

    from simwb.results import ResultSet, boundary_from_tets

    c, P = client
    pid = _project(c, P)
    pr = P.open_project(pid)
    nodes = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 1, 1.0]])
    tets = np.array([[0, 1, 2, 3], [1, 2, 3, 4]])
    tris, owner = boundary_from_tets(tets)
    T = np.array([20.0, 30, 40, 50, 60], np.float32)
    fields = {"Temperature": ("node", T, "°C"),
              "Heat flux": ("cell", np.array([[1, 0, 0], [0, 2, 0]], np.float32), "W/m²")}
    rs = ResultSet(nodes, tets, [0, 0], ["part"], fields, tris, owner, np.zeros(len(tris), int), ["walls"])
    pr.results_dir.mkdir(exist_ok=True)
    rs.save(pr.results_dir / "result.npz")
    pr.data["solve"] = {"status": "done", "finished": "today", "summary": {
        "T_max": 60.0, "bodies": {"1": {"name": "plate", "T_max": 60.0}}, "warnings": ["check <this>"],
        "assumptions": ["steady state"]}}
    pr.save()

    r = c.get(f"/api/projects/{pid}/results/export?format=csv")
    assert r.status_code == 200
    rows = r.text.strip().splitlines()
    head = rows[0].split(",")
    assert head[:4] == ['"x [m]"', '"y [m]"', '"z [m]"', '"Temperature [°C]"'] and '"Heat flux magnitude [W/m²]"' in head
    assert len(rows) == 6 and [float(x.split(",")[3]) for x in rows[1:]] == [20, 30, 40, 50, 60]
    # node 0 belongs to the first cell only (flux 1 along x); node 4 to the second (2 along y)
    k = head.index('"Heat flux x [W/m²]"')
    assert float(rows[1].split(",")[k]) == 1 and float(rows[5].split(",")[k + 1]) == 2

    r = c.get(f"/api/projects/{pid}/results/export?format=vtu")
    assert r.status_code == 200
    doc = xml.dom.minidom.parseString(r.content)
    arrays = {a.getAttribute("Name"): a for a in doc.getElementsByTagName("DataArray")}
    raw = base64.b64decode(arrays["Temperature [°C]"].firstChild.data)
    assert int(np.frombuffer(raw[:4], "<u4")[0]) == 20 and np.frombuffer(raw[4:], "<f4").tolist() == T.tolist()
    conn = np.frombuffer(base64.b64decode(arrays["connectivity"].firstChild.data)[4:], "<i4")
    assert conn.tolist() == tets.ravel().tolist() and arrays["Heat flux [W/m²]"].getAttribute("NumberOfComponents") == "3"
    assert doc.getElementsByTagName("Piece")[0].getAttribute("NumberOfCells") == "2"
    assert c.get(f"/api/projects/{pid}/results/export?format=exe").status_code == 400

    img = "data:image/png;base64,iVBORw0KGgo="
    r = c.post(f"/api/projects/{pid}/report", json={"image": img}, headers=H)
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    assert "Key results" in r.text and "check &lt;this&gt;" in r.text and "<this>" not in r.text
    assert f"<img src='{img}'" in r.text and "steady state" in r.text and "Boundary conditions" in r.text
    bad = c.post(f"/api/projects/{pid}/report", json={"image": "data:image/png;base64,AA' onerror='x"}, headers=H)
    assert bad.status_code == 200 and "<img" not in bad.text
    assert c.post(f"/api/projects/{pid}/report", json={}).status_code == 403  # (mutating verbs need the header)


def test_continue_transient_run_is_never_a_fresh_run(client, monkeypatch):
    """'Continue to a later end time' must reach the solver as a continuation or be refused - a request that fell
    through to a plain run would clear the case folder with the saved times in it."""
    import json

    c, P = client
    assert c.post("/api/projects", json={"name": "prop", "study": "external_aero"}, headers=H).status_code == 200
    # no run yet: refused with a message, nothing started
    r = c.post("/api/projects/prop/solve", json={"end_time": 0.01}, headers=H)
    assert r.status_code == 400 and "continue" in r.json()["error"] and not P.JOBS.latest("prop", "solve")
    pr = P.open_project("prop")
    case = pr.case_dir
    for tm in ("0", "0.001", "0.002"):
        (case / tm).mkdir(parents=True)
    keep = case / "0.002" / "U.gz"
    keep.write_text("saved field")
    meta = {"app": "pimpleFoam", "regions": [], "steps": [],
            "transient": {"end_time": 0.002, "n_frames": 3, "write_interval": 0.001, "fixed_dt": True, "dt": 1e-5}}
    (case / "simwb_case.json").write_text(json.dumps(meta))
    pr.data["solve"] = {"status": "done", "summary": {}}
    pr.save()
    for body, word in (({"end_time": 0.002}, "later"), ({"end_time": 0.004, "write_interval": 0.01}, "interval"),
                       ({"end_time": 1.0, "write_interval": 0.001}, "400 more")):
        r = c.post("/api/projects/prop/solve", json=body, headers=H)
        assert r.status_code == 400 and word in r.json()["error"], r.text
    # a steady case has no end time to extend
    (case / "simwb_case.json").write_text(json.dumps(dict(meta, transient=None)))
    assert c.post("/api/projects/prop/solve", json={"end_time": 0.004}, headers=H).status_code == 400
    (case / "simwb_case.json").write_text(json.dumps(meta))
    started = {}

    def fake_start(kind, pid, fn, *args, **kw):
        started.update(kind=kind, fn=fn.__name__, args=args, kw=kw)
        raise ValueError("stop here: the test only looks at how the run would be started")

    monkeypatch.setattr(P.JOBS, "start", fake_start)
    c.post("/api/projects/prop/solve", json={"end_time": 0.004, "write_interval": 0.0005}, headers=H)
    assert started["fn"] == "_solve_openfoam" and started["kw"] == {"resume_transient": True, "extend": (0.004, 0.0005)}
    assert keep.read_text() == "saved field"  # the saved times are untouched
