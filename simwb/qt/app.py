"""Start the desktop application:  python -m simwb.qt [--project ID|FOLDER] [--step STEP] [--shot FILE.png]"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from . import theme
from .assistant import ChatBridge
from .engine import Engine
from .main_window import MainWindow


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="simwb.qt")
    ap.add_argument("--project", help="a study id of the workspace, or a study folder, to open")
    ap.add_argument("--step", choices=["geometry", "setup", "mesh", "solve", "results"], help="the step to show")
    ap.add_argument("--shot", help="save a picture of the window to this PNG file and exit (for checking the look)")
    ap.add_argument("--shot-delay", type=int, default=3000, help="milliseconds to wait before the picture")
    ap.add_argument("--mesh-color", choices=["quality", "size", "region"])
    ap.add_argument("--cut", choices=["x", "y", "z"])
    ap.add_argument("--field", help="the result field to show")
    ap.add_argument("--no-api", action="store_true", help="do not serve the REST API and the control API (default: served on 127.0.0.1)")
    ap.add_argument("--api-port", type=int, help="port of the API (default SIMWB_PORT or 8765; the next free one if busy)")
    ap.add_argument("--debug-api", action="store_true",
                    help="turn on the Python console of the control API (ui_eval): arbitrary code in this process, for debugging only")
    args, qt_args = ap.parse_known_args(argv)
    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName("Simulation Workbench")
    theme.apply(app)
    engine, bridge = Engine(), ChatBridge()
    win = MainWindow(engine, bridge)
    win.resize(1500, 920)
    win.show()
    debug = args.debug_api or os.environ.get("SIMWB_DEBUG_API") == "1"
    from .control import Control

    control = Control(win, engine, bridge, app, debug=debug)
    win.control = control
    if not args.no_api:
        from . import api
        try:
            info = api.start(control, args.api_port, debug)
            print(f"API: {info['url']}  (REST: /docs, window control: /api/ui/state){'  DEBUG CONSOLE ON' if debug else ''}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"The API could not be started: {e}", flush=True)
    if args.project:
        p = Path(args.project)
        if (p / "project.json").exists():
            engine.open_folder(str(p))
        else:
            engine.open_id(args.project)
    if args.mesh_color:
        win.panels["mesh"].color_btns[args.mesh_color].setChecked(True)
        win.panels["mesh"].color = args.mesh_color
    if args.cut:
        win.panels["mesh"].cut_btns[args.cut].setChecked(True)
        win.panels["mesh"].axis = args.cut
    if args.step:
        win.go(args.step)
    if args.field:
        win.panels["results"].field.setCurrentText(args.field)
    if args.shot:
        def shoot():
            win.screenshot(args.shot)
            print("saved", args.shot, flush=True)
            app.quit()
        QTimer.singleShot(args.shot_delay, shoot)
    return app.exec()
