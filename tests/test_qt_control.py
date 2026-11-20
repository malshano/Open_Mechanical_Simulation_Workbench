"""The control layer of the desktop window: state, actions, widgets, log, pictures, eval, the tools and the REST routes.

The real window is built without an OpenGL surface (SIMWB_NO_GL=1, Qt's offscreen platform): everything else - the widgets, the
engine, the VTK pipeline that is described - is the real thing."""
import os
import threading
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.fixture
def ctl(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMWB_HOME", str(tmp_path / "ws"))
    monkeypatch.setenv("SIMWB_NO_GL", "1")
    import importlib

    import simwb.project as P

    importlib.reload(P)
    from simwb import agent

    monkeypatch.setattr(agent, "auth_status", lambda: {"loggedIn": True, "authMethod": "api_key"})   # (no call to the Claude CLI)
    from simwb.qt import theme
    from simwb.qt.assistant import ChatBridge
    from simwb.qt.control import Control
    from simwb.qt.engine import Engine
    from simwb.qt.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    theme.apply(app)
    eng = Engine(settings=QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat))
    bridge = ChatBridge()
    win = MainWindow(eng, bridge)
    win.show()
    c = Control(win, eng, bridge, app, debug=True)
    win.control = c
    yield c
    c.close()
    P.JOBS.listeners.remove(eng._on_event)
    bridge.shutdown()
    win.view.close()
    win.deleteLater()


def from_thread(app, fn, timeout=60):
    """Call fn on another thread (as the web server or the assistant would) while this thread keeps the window alive."""
    out = {}

    def run():
        try:
            out["v"] = fn()
        except BaseException as e:  # noqa: BLE001
            out["e"] = e
    t = threading.Thread(target=run)
    t.start()
    t0 = time.time()
    while t.is_alive() and time.time() - t0 < timeout:
        app.processEvents()
        time.sleep(0.005)
    t.join(1)
    if "e" in out:
        raise out["e"]
    return out["v"]


# ------------------------------------------------------------------------------------------------------------ coverage
def test_every_control_has_an_id_without_a_study(ctl):
    cov = ctl.coverage()
    assert cov["complete"], cov
    assert cov["widgets"] > 20 and cov["actions"] > 15


def test_every_control_has_an_id_with_a_study_open(ctl):
    ctl.engine.new_study("Plate", "heat_conduction")
    ctl._settle()
    for step in ("geometry", "setup", "mesh", "solve", "results"):
        ctl.do("step.go", {"step": step})
        assert ctl.coverage()["complete"], (step, ctl.coverage())


def test_ids_are_unique_and_listed(ctl):
    ids = [w["id"] for w in ctl.widgets()["widgets"]]
    assert len(ids) == len(set(ids)) and {"mesh.generate", "results.colormap", "assistant.send", "file.save", "step.mesh"} <= set(ids)
    assert ctl.widgets("mesh.preset")["count"] == 5


# ------------------------------------------------------------------------------------------------------------ actions
def test_actions_are_described_and_validated(ctl):
    names = {a["name"] for a in ctl.list_actions()["actions"]}
    assert {"study.new", "study.save_as", "step.go", "mesh.generate", "results.show", "assistant.ask", "view.camera"} <= names
    with pytest.raises(ValueError, match="Unknown action"):
        ctl.do("nope")
    with pytest.raises(ValueError, match="Missing argument"):
        ctl.do("study.new", {"name": "x"})
    with pytest.raises(ValueError, match="Unknown argument"):
        ctl.do("step.go", {"step": "mesh", "speed": 3})
    with pytest.raises(ValueError, match="No study is open"):
        ctl.do("step.go", {"step": "mesh"})


def test_case_actions_round_trip(ctl, tmp_path):
    r = ctl.do("study.new", {"name": "Bracket", "study": "static_structural", "location": str(tmp_path / "mine")})
    pid = r["result"]["id"]
    assert ctl.state()["study"]["name"] == "Bracket" and ctl.state()["window"]["title"].startswith("Bracket")
    assert ctl.do("study.save")["result"]["saved_at"]
    z = ctl.do("study.export", {"path": str(tmp_path / "b.zip")})["result"]["file"]
    back = ctl.do("study.import", {"path": z, "location": str(tmp_path / "other")})["result"]["id"]
    assert back != pid and ctl.state()["study"]["id"] == back
    cp = ctl.do("study.save_as", {"location": str(tmp_path / "copies"), "name": "Bracket 2"})["result"]["id"]
    assert ctl.state()["study"]["id"] == cp
    assert ctl.do("study.recent")["result"]["recent"]
    ctl.do("study.close")
    assert ctl.state()["study"] is None


def test_modal_controls_point_to_the_action_that_replaces_them(ctl):
    with pytest.raises(ValueError, match="study.open_folder"):
        ctl.widget_do("file.open_folder", "click")
    with pytest.raises(ValueError, match="geometry.import"):
        ctl.widget_do("geometry.import", "click")


# ------------------------------------------------------------------------------------------------------------ widgets
def test_widget_get_set_click(ctl):
    ctl.engine.new_study("Plate", "heat_conduction")
    ctl.do("step.go", {"step": "results"})
    assert ctl.widget_do("results.colormap")["value"]["text"] == "turbo"
    assert ctl.widget_do("results.colormap", "set", "viridis")["value"]["text"] == "viridis"
    assert ctl.widget_do("results.bands", "set", 8)["value"] == 8
    assert ctl.widget_do("results.reverse", "set", True)["value"] == {"checked": True}
    with pytest.raises(ValueError, match="no item"):
        ctl.widget_do("results.colormap", "set", "nonsense")
    with pytest.raises(ValueError, match="No control with the id"):
        ctl.widget_do("results.colour")
    ctl.do("step.go", {"step": "mesh"})
    e = ctl.widget_do("mesh.cut.y", "click")
    assert e["value"] == {"checked": True} and ctl.win.panels["mesh"].axis == "y"
    assert ctl.widget_do("step.results", "click")["id"] == "step.results" and ctl.win.step == "results"


def test_outline_tree_select(ctl):
    ctl.engine.new_study("Plate", "heat_conduction")
    ctl._settle()
    items = ctl.widget_do("outline.tree")["value"]["items"]
    assert any(i.endswith("Mesh — none") for i in items)
    ctl.widget_do("outline.tree", "select", [i for i in items if i.endswith("Mesh — none")][0])
    assert ctl.win.step == "mesh"


# ------------------------------------------------------------------------------------------------------------ state, log, eval, pictures
def test_state_describes_the_window(ctl):
    s = ctl.state()
    assert s["window"]["step"] == "geometry" and s["study"] is None and s["view"]["gl"] is False
    assert {"outline", "step", "assistant"} <= set(s["docks"]) and s["assistant"]["busy"] is False
    ctl.engine.new_study("Plate", "heat_conduction")
    ctl._settle()
    s = ctl.state()
    assert s["study"]["geometry"] != "ready" and "geometry.import" in s["panels"]["geometry"]


def test_log_collects_every_source(ctl):
    import logging

    import io

    from simwb.qt.control import _Tee

    before = ctl.log.n
    logging.getLogger("mything").error("boom from logging")
    tee = _Tee(io.StringIO(), ctl.log, "info", "stdout")      # (what replaces sys.stdout while the window runs)
    tee.write("hello from print\npartial")
    tee.write(" line\n")
    assert tee.real.getvalue() == "hello from print\npartial line\n"         # still reaches the real stream
    ctl.log.add("warning", "vtk", "pretend vtk warning")
    r = ctl.handle("log", {"after": before})
    texts = " | ".join(e["text"] for e in r["entries"])
    assert "boom from logging" in texts and "hello from print" in texts and "partial line" in texts and r["errors"] >= 1
    assert ctl.handle("log", {"after": before, "level": "error"})["entries"][0]["text"] == "boom from logging"
    assert ctl.handle("log", {"after": before, "contains": "pretend"})["entries"][0]["source"] == "vtk"


def test_uncaught_thread_errors_are_logged(ctl):
    def bad():
        raise RuntimeError("thread blew up")
    t = threading.Thread(target=bad, name="worker-x")
    t.start()
    t.join()
    r = ctl.handle("log", {"contains": "thread blew up"})
    assert r["entries"] and r["entries"][0]["level"] == "error" and r["entries"][0]["source"] == "thread:worker-x"


def test_an_action_reports_the_problems_it_caused(ctl):
    ctl.engine.new_study("Plate", "heat_conduction")
    import logging

    ctl.actions["boom"] = type(ctl.actions["study.save"])("boom", "x", {}, lambda: logging.getLogger("x").warning("careful") or {})
    r = ctl.do("boom")
    assert [e["text"] for e in r["new_log"]] == ["careful"]


def test_screenshot_files(ctl, tmp_path):
    d = ctl.screenshot("window")
    assert Path(d["path"]).exists() and d["width"] > 500
    assert ctl.screenshot("viewport")["width"] >= 64
    assert Path(ctl.screenshot("assistant.input")["path"]).exists()
    with pytest.raises(ValueError, match="what must be"):
        ctl.screenshot("no.such.thing")


def test_eval_is_gated_and_logged(ctl):
    r = ctl.eval_code("x = 2\nprint('hi')\nwin.step + str(x)")
    assert r["stdout"] == "hi\n" and r["value"] == "'geometry2'"
    assert "eval:" in ctl.handle("log", {"contains": "eval:"})["entries"][-1]["text"]
    assert "Traceback" in ctl.eval_code("1/0")["error"]
    ctl.debug = False
    with pytest.raises(ValueError, match="eval is off"):
        ctl.eval_code("1")


# ------------------------------------------------------------------------------------------------------------ other threads, tools, REST
def test_calls_from_other_threads_run_on_the_window_thread(ctl):
    from simwb import ui_bridge

    names = from_thread(ctl.app, lambda: ui_bridge.call("eval", {"code": "__import__('threading').current_thread().name", "token": ctl.token}))
    assert names["value"] == "'MainThread'"
    assert from_thread(ctl.app, lambda: ui_bridge.call("state", {}))["window"]["step"] == "geometry"


def test_the_tools_work_with_a_window_and_say_so_without_one(ctl):
    from simwb import tools as T

    out = from_thread(ctl.app, lambda: T.call("ui_state", None, {}))
    assert '"step": "geometry"' in out
    out = from_thread(ctl.app, lambda: T.call("ui_do", None, {"action": "nope"}))
    assert out.startswith("ERROR") and "Unknown action" in out
    shot = from_thread(ctl.app, lambda: T.call("ui_screenshot", None, {"what": "viewport"}))
    assert shot.startswith("SCREENSHOT: ") and shot.splitlines()[0].endswith(".png")
    assert "ERROR" in from_thread(ctl.app, lambda: T.call("ui_widget", None, {"id": "x.y"}))
    ctl.close()
    out = T.call("ui_state", None, {})
    assert out.startswith("ERROR") and "not running" in out


def test_the_assistant_tool_list_has_ui_tools_but_not_eval(ctl):
    from simwb import tools as T

    assert {"ui_state", "ui_do", "ui_screenshot", "ui_log", "ui_wait", "ui_widget", "ui_widgets", "ui_actions"} <= {t[0] for t in T.TOOLS}
    assert "ui_eval" not in {t[0] for t in T.TOOLS} and "ui_eval" in {t[0] for t in T.MCP_EXTRA}
    assert {t[0] for t in T.TOOLS + T.MCP_EXTRA if t[0].startswith("ui_")} <= T.PROJECT_FREE


def test_rest_routes(ctl):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from simwb.qt import api

    app = FastAPI()

    @app.exception_handler(ValueError)
    async def _(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=400, content={"error": str(exc)})
    app.include_router(api.make_router(ctl, ctl.token))
    c = TestClient(app)

    def get(path):
        return from_thread(ctl.app, lambda: c.get(path))

    def post(path, **kw):
        return from_thread(ctl.app, lambda: c.post(path, **kw))
    assert get("/api/ui/state").json()["window"]["step"] == "geometry"
    assert get("/api/ui/coverage").json()["complete"] is True
    assert len(get("/api/ui/actions").json()["actions"]) > 20
    assert get("/api/ui/widgets?filter=mesh.preset").json()["count"] == 5
    ctl.engine.new_study("Plate", "heat_conduction")
    r = post("/api/ui/do", json={"action": "step.go", "args": {"step": "mesh"}})
    assert r.status_code == 200 and r.json()["step"] == "mesh"
    r = post("/api/ui/widget", json={"id": "mesh.color.size", "op": "click"})
    assert r.json()["value"] == {"checked": True}
    assert post("/api/ui/do", json={"action": "bogus"}).status_code == 400
    shot = get("/api/ui/screenshot?what=window")
    assert shot.status_code == 200 and shot.headers["content-type"] == "image/png" and shot.content[:4] == b"\x89PNG"
    assert get("/api/ui/log?level=error").status_code == 200
    # eval needs the debug switch AND the token
    assert post("/api/ui/eval", json={"code": "1+1"}).status_code == 400
    bad = post("/api/ui/eval", json={"code": "1+1"}, headers={"Authorization": "Bearer wrong"})
    assert bad.status_code == 400 and "token" in bad.json()["error"]
    ok = post("/api/ui/eval", json={"code": "1+1"}, headers={"Authorization": f"Bearer {ctl.token}"})
    assert ok.json()["value"] == "2"
    ctl.debug = False
    assert "eval is off" in post("/api/ui/eval", json={"code": "1"}, headers={"Authorization": f"Bearer {ctl.token}"}).json()["error"]


# ------------------------------------------------------------------------------------------------------------ a real study through the window
def test_import_mesh_and_inspect_through_the_window(ctl):
    """CAD import -> mesh -> mesh views, all by the actions and controls an operator (or Claude) would use; the 3D view's overlay
    must describe what is drawn."""
    ctl.do("study.new", {"name": "Plate", "study": "heat_conduction"})
    ctl.do("geometry.import", {"path": str(EXAMPLES / "heated_plate.step")})
    assert from_thread(ctl.app, lambda: ctl.wait_idle(300), 320)["idle"]
    s = ctl.state()
    assert s["study"]["geometry"] == "ready" and len(s["study"]["bodies"]) == 2
    assert s["view"]["geometry"]["faces"] > 5 and s["view"]["geometry"]["bodies"] == 2
    mat_ids = [w["id"] for w in ctl.widgets("setup.material")["widgets"]]
    assert len(mat_ids) == 2
    ctl.do("step.go", {"step": "setup"})
    ctl.do("setup.material", {"body_id": int(mat_ids[0].rsplit(".", 1)[1]), "material": "Aluminium 6061"})
    ctl.do("step.go", {"step": "mesh"})
    ctl.do("mesh.preset", {"preset": "coarse"})
    ctl.do("mesh.generate")
    assert from_thread(ctl.app, lambda: ctl.wait_idle(300), 320)["idle"]
    ctl._settle()
    s = ctl.state()
    assert s["study"]["mesh"]["status"] == "ready" and s["study"]["mesh"]["cells"] > 500
    assert s["view"]["overlay"]["name"] == "Cell quality" and s["view"]["overlay"]["range"] == [0.0, 1.0]
    r = ctl.do("mesh.view", {"color": "size", "cut": "y", "position": 0.5})
    assert r["overlay"]["name"] == "Cell size" and r["overlay"]["scale"] == "log" and r["overlay"]["unit"] == "mm"
    assert r["overlay"]["range"][0] > 0 and r["new_log"] == []
    r = ctl.do("mesh.view", {"color": "region", "cut": "off"})
    assert r["overlay"]["kind"] == "categories" and r["overlay"]["categories"]
    assert ctl.do("view.stats")["result"]["overlay"]["kind"] == "categories"
    assert ctl.do("view.camera", {"position": [0, -1, 0.2]})["result"]["camera"]["position"] == [0.0, -1.0, 0.2]


# ------------------------------------------------------------------------------------------------------------ the start screen
def test_start_screen_has_a_card_per_study_type(ctl):
    s = ctl.state()
    assert s["window"]["screen"] == "home" and s["docks"] == {"outline": False, "step": False, "assistant": False, "log": False}
    assert ctl.widgets("home.new.")["count"] == 6 and ctl.widgets("home.example.")["count"] == 7
    assert {w["id"] for w in ctl.widgets("home.")["widgets"]} >= {"home.open_folder", "home.import", "home.new.static_structural"}
    types = ctl.do("study.types")["result"]["types"]
    assert len(types) == 6 and any(t["multiphysics"] for t in types)
    with pytest.raises(ValueError, match="study.new"):
        ctl.widget_do("home.new.static_structural", "click")        # opens the new-study dialog: not drivable
    with pytest.raises(ValueError, match="Unknown study type"):
        ctl.do("study.example", {"study": "nope"})
    with pytest.raises(ValueError, match="not an example"):
        ctl.do("study.example", {"study": "static_structural", "example": "heated_plate.step"})


def test_an_example_card_opens_a_study_and_the_views(ctl):
    ctl.widget_do("home.example.heat_conduction.0", "click")
    s = ctl.state()
    assert s["window"]["screen"] == "study" and s["study"]["study"] == "heat_conduction"
    assert from_thread(ctl.app, lambda: ctl.wait_idle(300), 320)["idle"]
    s = ctl.state()
    assert s["study"]["geometry"] == "ready" and s["docks"]["step"] is True
    ctl.do("study.close")
    assert ctl.state()["window"]["screen"] == "home" and ctl.state()["docks"]["step"] is False
    ctl.do("study.example", {"study": "vibration_modal"})                # the action does what the button does
    assert ctl.state()["study"]["study"] == "vibration_modal"
    assert from_thread(ctl.app, lambda: ctl.wait_idle(300), 320)["idle"]


def test_recent_studies_show_on_the_start_screen(ctl, tmp_path):
    ctl.do("study.new", {"name": "Bracket", "study": "static_structural", "location": str(tmp_path / "x")})
    ctl.do("study.close")
    ids = [w["id"] for w in ctl.widgets("home.recent")["widgets"]]
    assert ids == ["home.recent.0"] and ctl.widget_do("home.recent.0")["text"] == "Bracket"
    ctl.widget_do("home.recent.0", "click")
    assert ctl.state()["study"]["name"] == "Bracket" and ctl.state()["window"]["screen"] == "study"


def test_eval_needs_the_token_on_every_path(ctl, tmp_path):
    """Code execution is gated inside the control layer, so no route can skip the secret of the run."""
    from simwb import tools as T
    from simwb import ui_bridge

    for args in ({"code": "1"}, {"code": "1", "token": ""}, {"code": "1", "token": "nope"}):
        with pytest.raises(ValueError, match="control token"):
            ctl.handle("eval", args)
        with pytest.raises(ValueError, match="control token"):
            ui_bridge.call("eval", args)
    assert ctl.handle("eval", {"code": "1+1", "token": ctl.token})["value"] == "2"
    out = from_thread(ctl.app, lambda: T.call("ui_eval", None, {"code": "1"}))               # as the tool endpoint would run it
    assert out.startswith("ERROR") and "control token" in out
    out = from_thread(ctl.app, lambda: T.call("ui_eval", None, {"code": "1", "token": ctl.token}))
    assert '"value": "1"' in out
    ctl.debug = False                                                                          # debug off beats a right token
    with pytest.raises(ValueError, match="eval is off"):
        ctl.handle("eval", {"code": "1", "token": ctl.token})


def test_the_mcp_server_supplies_the_token_from_the_workspace_file(ctl):
    import json

    from simwb import mcp_server
    from simwb import project as P

    assert mcp_server._control_token() == ""                       # no window has written its file
    (P.WORKSPACE / ".ui").mkdir(parents=True, exist_ok=True)
    (P.WORKSPACE / ".ui" / "control.json").write_text(json.dumps({"token": "abc123"}))
    assert mcp_server._control_token() == "abc123"


def test_new_study_in_the_menu_goes_back_to_the_start_screen(ctl, tmp_path):
    ctl.do("study.new", {"name": "Bracket", "study": "static_structural", "location": str(tmp_path / "x")})
    assert ctl.state()["window"]["screen"] == "study"
    assert ctl.widget_do("file.new")["modal"] is False             # no dialog: it is a plain click now
    ctl.widget_do("file.new", "click")
    s = ctl.state()
    assert s["window"]["screen"] == "home" and s["study"] is None and s["docks"]["step"] is False
    assert (tmp_path / "x" / "Bracket" / "project.json").exists()   # the study was saved on the way out
    assert not [w for w in ctl.widgets()["widgets"] if "dialog" in w["class"].lower()]
    import simwb.qt.main_window as mw

    assert not hasattr(mw, "NewStudyDialog")                        # the model-selection window is gone


# ------------------------------------------------------------------------------------------------------------ the air box
def _block_step(path):
    import gmsh

    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.model.add("s")
    b = gmsh.model.occ.addBox(0, 0, 0, 60, 24, 10)
    c = gmsh.model.occ.addCylinder(15, 12, -1, 0, 0, 12, 4)
    gmsh.model.occ.cut([(3, b)], [(3, c)])
    gmsh.model.occ.synchronize()
    gmsh.write(str(path))
    gmsh.finalize()


def test_the_air_box_of_a_flow_study_is_translucent_and_togglable(ctl, tmp_path):
    """The six far-field faces used to be drawn opaque and hid the part: they are one translucent box, off at first, with a toggle."""
    _block_step(tmp_path / "block.step")
    ctl.do("study.new", {"name": "Flow", "study": "external_aero"})
    assert ctl.widget_do("view.air_box")["visible"] is True                # the toggle belongs to this study type
    ctl.do("geometry.import", {"path": str(tmp_path / "block.step")})
    assert from_thread(ctl.app, lambda: ctl.wait_idle(300), 320)["idle"]
    g = ctl.state()["view"]["geometry"]
    assert g["air_faces"] == 6 and g["faces"] == 13 and g["air_visible"] is False        # (flow study: off at first)
    v = ctl.win.view
    assert all(a.GetVisibility() for a in v.geo_actors) and v.geo_actors                    # the part is drawn
    assert v.air_actors and not any(a.GetVisibility() for a in v.air_actors)
    assert ctl.widget_do("view.air_box")["value"] == {"checked": False}
    ctl.widget_do("view.air_box", "click")
    assert ctl.state()["view"]["geometry"]["air_visible"] is True and all(a.GetVisibility() for a in v.air_actors)
    assert v.air_actors[0].GetProperty().GetOpacity() < 0.3                                  # translucent: the part shows through
    assert all(a.GetProperty().GetOpacity() == 1.0 for a in v.geo_actors)
    ctl.do("view.air_box", {"visible": False})
    assert ctl.state()["view"]["geometry"]["air_visible"] is False
    ctl.do("view.air_box", {"visible": True})
    ctl.do("step.go", {"step": "setup"})                                                       # the choice stays across the steps
    assert ctl.state()["view"]["geometry"]["air_visible"] is True
    ctl.do("study.close")
    ctl.do("study.new", {"name": "Plate", "study": "static_structural"})
    assert ctl.widget_do("view.air_box")["visible"] is False                               # a solid study has no air box
    with pytest.raises(ValueError, match="no air box"):
        ctl.do("view.air_box")


def test_a_cooling_study_shows_its_air_box_at_first(ctl):
    ctl.do("study.example", {"study": "cht_enclosure"})
    assert from_thread(ctl.app, lambda: ctl.wait_idle(300), 320)["idle"]
    g = ctl.state()["view"]["geometry"]
    assert g["air_faces"] > 0 and g["air_visible"] is True
    assert ctl.widget_do("view.air_box")["value"] == {"checked": True}


# ------------------------------------------------------------------------------------------------------------ the picking contract
def test_the_picking_contract_can_be_driven_and_observed(ctl):
    """Panels are built against Viewport's picking signals before the real picking exists: the control API emits them by hand."""
    got = []
    ctl.win.view.facePicked.connect(lambda i, add: got.append(("face", i, add)))
    ctl.win.view.pointPicked.connect(lambda x, y, z: got.append(("point", x, y, z)))
    r = ctl.do("view.pick_face", {"id": 7})
    assert got == [("face", 7, False)] and r["result"]["picked"] == 7
    ctl.do("view.pick_face", {"id": 9, "add": True})
    ctl.do("view.pick_point", {"x": 0.1, "y": 0.2, "z": 0.3})
    assert got[1:] == [("face", 9, True), ("point", 0.1, 0.2, 0.3)]
    v = ctl.win.view
    v.set_pick_mode("face")
    v.set_selection([3, 4])
    v.set_face_tints({5: "#e4572e"})
    s = ctl.state()["view"]
    assert s["pick_mode"] == "face" and s["selection"] == [3, 4] and s["face_tints"] == {5: "#e4572e"}
    with pytest.raises(ValueError, match="pick mode"):
        v.set_pick_mode("edge")
