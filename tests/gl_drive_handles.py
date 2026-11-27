"""Drives the handles of refinement regions and rotating zones with REAL mouse events (QTest press / move / release on the 3D view's VTK widget) in a window with
a real OpenGL surface, then prints what happened as one JSON line. Run by tests/test_qt_handles_mouse.py in its own process (the other Qt tests use Qt's
offscreen platform without OpenGL, where VTK has no render window and gets no mouse events). Exit code 77: no OpenGL window could be made here.

    python tests/gl_drive_handles.py <folder for screenshots> [<workspace folder>]

Not a test by itself: the assertions are in tests/test_qt_handles_mouse.py."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SHOTS = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp())
SHOTS.mkdir(parents=True, exist_ok=True)
WS = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(tempfile.mkdtemp())
WS.mkdir(parents=True, exist_ok=True)
os.environ["SIMWB_HOME"] = str(WS)
os.environ.pop("SIMWB_NO_GL", None)
os.environ.pop("QT_QPA_PLATFORM", None)
os.environ["QT_QPA_PLATFORM"] = os.environ.get("SIMWB_DRIVE_PLATFORM", "windows" if sys.platform == "win32" else "xcb")

import numpy as np  # noqa: E402
from PySide6.QtCore import QPoint, QSettings, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

out: dict = {}


def pump(ms=60):
    QTest.qWait(ms)


def main():
    from simwb import agent
    agent.auth_status = lambda: {"loggedIn": True, "authMethod": "api_key"}
    import gmsh
    from simwb.qt import mode, theme
    from simwb.qt.assistant import ChatBridge
    from simwb.qt.control import Control
    from simwb.qt.engine import Engine
    from simwb.qt.main_window import MainWindow
    mode.set_simple(False)
    app = QApplication.instance() or QApplication([])
    theme.apply(app)
    step = WS / "box.step"
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.model.occ.addBox(0, 0, 0, 100, 20, 20)
    gmsh.model.occ.synchronize()
    gmsh.write(str(step))
    gmsh.finalize()
    eng = Engine(settings=QSettings(str(WS / "s.ini"), QSettings.IniFormat))
    bridge = ChatBridge()
    win = MainWindow(eng, bridge)
    win.resize(1500, 950)
    win.show()
    c = Control(win, eng, bridge, app, debug=True)
    win.control = c
    pump(300)
    view = win.view
    if view.rw is None:
        print(json.dumps({"skip": "no OpenGL window"}))
        return 77
    out["gl"] = True

    def settle():
        t0 = time.time()
        while time.time() - t0 < 60:
            pump(50)
            try:
                if c.wait_idle(1.0).get("idle"):
                    break
            except Exception:  # noqa: BLE001
                pass
        c._settle()

    c.do("study.new", {"name": "Flow", "study": "external_aero"})
    c.do("geometry.import", {"path": str(step)})
    settle()
    hs = view.handles
    iren = view.iren

    def dpr():
        return view.gizmo.dpr()

    def widget_pos(px):
        return QPoint(int(round(px[0] / dpr())), int(round(px[1] / dpr())))

    def mouse_drag(start_px, end_px, steps=8, shift=False, shot=None):
        """A real drag: press, moves, (a picture half way), release."""
        mod = Qt.ShiftModifier if shift else Qt.NoModifier
        a, b = np.asarray(start_px, float), np.asarray(end_px, float)
        QTest.mouseMove(iren, widget_pos(a))
        pump(30)
        out.setdefault("hover", []).append(hs.hover)
        QTest.mousePress(iren, Qt.LeftButton, mod, widget_pos(a))
        pump(30)
        took = hs.drag is not None
        for i in range(1, steps + 1):
            QTest.mouseMove(iren, widget_pos(a + (b - a) * i / steps))
            pump(15)
            if shot and i == steps // 2:
                view.save_png(str(SHOTS / shot))
        QTest.mouseRelease(iren, Qt.LeftButton, mod, widget_pos(b))
        pump(150)
        return took

    def look(name):
        pump(100)
        win.screenshot(str(SHOTS / name))

    # ---------------------------------------------------------------- a rotating zone, in Setup
    c.do("step.go", {"step": "setup"})
    c.widget_do("setup.zone.add", "click")
    settle()
    view.fit()
    pump(300)
    rot = win.panels["setup"].rot
    z0 = dict(eng.state()["moving_zones"][0])
    out["zone0"] = z0
    look("01_setup_zone_handles.png")

    def press_picks(label):
        """Press (no drag) on the place each handle says it is taken: what did the press take? Then let go without moving."""
        got = {}
        for key, spec in list(hs.specs.items()):
            part = "ring" if spec["kind"] == "ring" else "face" if spec["kind"] == "face" else "axis0"
            if key == "move":
                for part in ("axis0", "axis1", "axis2", "ball"):
                    got[f"{key}:{part}"] = _press_release(hs.grab_px(key, part))
                continue
            got[f"{key}:{part}"] = _press_release(hs.grab_px(key, part))
        out[label] = got

    def _press_release(px):
        QTest.mouseMove(iren, widget_pos(px))
        pump(30)
        QTest.mousePress(iren, Qt.LeftButton, Qt.NoModifier, widget_pos(px))
        pump(30)
        took = None if hs.drag is None else f"{hs.drag['key']}:{hs.drag['part']}"
        QTest.mouseRelease(iren, Qt.LeftButton, Qt.NoModifier, widget_pos(px))
        pump(80)
        return took

    iren.AddObserver("KeyPressEvent", lambda o, e: out.setdefault("keys", []).append(str(iren.GetKeySym())))
    press_picks("zone_picks_default")
    out["zone_draft_after_clicks"] = rot.draft is not None and rot.draft["zone"] != {k: z0[k] for k in rot.draft["zone"]}
    # the arrow along x: a draft, nothing stored; then Apply
    start = hs.grab_px("move", "axis0")
    tip = hs._arrows(hs.specs["move"])[0]
    end = hs._px((tip[1] + tip[2]) / 2 + [0.02, 0, 0])
    out["zone_move_took"] = mouse_drag(start, end, shot="02_setup_zone_moving.png")
    z1 = rot.current(0)
    out["zone_move_dx_mm"] = (z1["center"][0] - z0["center"][0]) * 1000
    out["zone_move_dyz_mm"] = [(z1["center"][k] - z0["center"][k]) * 1000 for k in (1, 2)]
    out["zone_stored_before_apply"] = eng.state()["moving_zones"][0] == z0
    # Escape in the middle of a drag of the length handle: the zone stays as it was
    spec = hs.specs["face.length.1"]
    a, b = np.asarray(hs.grab_px("face.length.1")), None
    end = hs._px(hs._taken(spec, "face") + np.asarray(spec["dir"]) * 0.01)
    QTest.mouseMove(iren, widget_pos(a))
    pump(30)
    QTest.mousePress(iren, Qt.LeftButton, Qt.NoModifier, widget_pos(a))
    pump(30)
    for i in range(1, 5):
        QTest.mouseMove(iren, widget_pos(a + (end - a) * i / 4))
        pump(20)
    out["zone_len_during_drag_mm"] = rot.current(0)["length"] * 1000
    QTest.keyClick(iren, Qt.Key_Escape)
    pump(100)
    QTest.mouseMove(iren, widget_pos(end + np.array([30.0, 30.0])))
    pump(30)
    QTest.mouseRelease(iren, Qt.LeftButton, Qt.NoModifier, widget_pos(end + np.array([30.0, 30.0])))
    pump(100)
    out["zone_len_after_escape_mm"] = rot.current(0)["length"] * 1000
    out["zone_dragging_after_escape"] = hs.drag is not None
    c.widget_do("setup.zone.0.apply", "click")
    settle()
    out["zone_applied_dx_mm"] = (eng.state()["moving_zones"][0]["center"][0] - z0["center"][0]) * 1000
    z1 = dict(eng.state()["moving_zones"][0])
    # a face handle (radius)
    start = hs.grab_px("face.radius", "face")
    end = hs._px(hs._taken(hs.specs["face.radius"], "face") + hs._dir(hs.specs["face.radius"]) * 0.01)
    out["zone_radius_took"] = mouse_drag(start, end)
    z2 = rot.current(0)
    out["zone_radius_dr_mm"] = (z2["radius"] - z1["radius"]) * 1000
    c.widget_do("setup.zone.0.apply", "click")
    settle()
    # the ring: a quarter turn about x (picked by pressing ON the ring, where the zone's other handles also are)
    s_ = hs.specs["tilt.x"]
    start = hs._px(hs._ring_point(s_, 20.0))
    end = hs._px(hs._ring_point(s_, 20.0 + 100.0))
    out["zone_tilt_took"] = mouse_drag(start, end, steps=12)
    out["zone_axis_draft"] = rot.current(0)["axis"]
    c.widget_do("setup.zone.0.apply", "click")
    settle()
    out["zone_axis_after"] = eng.state()["moving_zones"][0]["axis"]
    look("03_setup_zone_after_tilt.png")
    press_picks("zone_picks_tilted")                       # (the same on a zone along another axis)
    rot.cancel_draft()
    # a ring picked anywhere on it, not only where it crosses an arrow
    picks = []
    for k in ("tilt.x", "tilt.z"):
        if k in hs.specs:
            for deg in (20.0, 110.0, 200.0, 290.0):
                picks.append(hs.pick(*hs._px(hs._ring_point(hs.specs[k], deg))))
    out["zone_ring_picks"] = [list(p) if p else None for p in picks]

    # ---------------------------------------------------------------- a refinement region, in Mesh
    c.do("step.go", {"step": "mesh"})
    settle()
    c.do("mesh.region_add", {"kind": "box"})
    settle()
    c.widget_do("mesh.region.0.xform", "set", True)
    pump(300)
    view.fit()
    pump(300)
    xf = win.panels["mesh"].adv.xform
    r0 = dict(eng.state()["mesh"]["regions"][0])
    look("04_mesh_region_handles.png")
    kinds = {k: v["kind"] for k, v in hs.specs.items()}
    out["region_kinds"] = kinds
    # move: the arrow along y
    ar = hs._arrows(hs.specs["move"])[1]
    start = hs._px((ar[1] + ar[2]) / 2)
    end = hs._px((ar[1] + ar[2]) / 2 + [0, 0.01, 0])
    out["region_move_took"] = mouse_drag(start, end, shot="05_mesh_region_moving.png")
    out["region_shift_mm"] = (xf.shift * 1000).round(3).tolist()
    out["region_stored_before_apply"] = eng.state()["mesh"]["regions"][0]["min"] == r0["min"]
    # turn: the ring about z, 30 degrees
    s = hs.specs["turn.z"]
    start = hs._px(hs._ring_point(s, 20.0))
    end = hs._px(hs._ring_point(s, 50.0))
    out["region_turn_took"] = mouse_drag(start, end, steps=10)
    from simwb.qt.regionxform import euler_deg
    out["region_turn_deg"] = euler_deg(xf.R)[2]
    # resize: the +x face handle out by 15 mm
    sp = hs.specs["face.0.1"]
    a, b = hs._arrows(sp)[0][1:]
    start = hs._px((a + b) / 2)
    end = hs._px((a + b) / 2 + hs._dir(sp) * 0.015)
    out["region_face_took"] = mouse_drag(start, end, shot="06_mesh_region_resizing.png")
    out["region_grow_mm"] = (xf.grow * 1000).round(3).tolist()
    look("07_mesh_region_after_drags.png")
    c.widget_do("mesh.region.0.xf.apply", "click")
    settle()
    out["region_applied"] = eng.state()["mesh"]["regions"][0]
    pump(200)
    look("08_mesh_region_applied.png")
    c.close()
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception as e:  # noqa: BLE001
        import traceback
        out["error"] = f"{type(e).__name__}: {e}"
        out["trace"] = traceback.format_exc()
        code = 1
    print("RESULT " + json.dumps(out, default=str))
    sys.stdout.flush()
    os._exit(code)
