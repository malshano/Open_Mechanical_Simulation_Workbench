"""Shared by the Qt window tests: the real window built without an OpenGL surface (SIMWB_NO_GL=1, Qt's offscreen platform), its control
layer (`ctl`), and a helper that calls it from another thread the way the web server or the assistant would.

Use in a test file:   from qt_support import ctl, from_thread   # noqa: F401"""
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

    from simwb.qt import mode
    mode.set_simple(False)                                   # (Simple / Expert is one switch for the whole process: a test that sets Simple must not leave it for the next)
    app = QApplication.instance() or QApplication([])
    theme.apply(app)
    eng = Engine(settings=QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat))
    bridge = ChatBridge()
    win = MainWindow(eng, bridge)
    win.show()
    c = Control(win, eng, bridge, app, debug=True)
    win.control = c
    yield c
    mode.set_simple(False)
    c.close()
    P.JOBS.listeners.remove(eng._on_event)
    bridge.shutdown()
    win.view.close()
    win.close()
    win.deleteLater()
    from PySide6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)      # (a test never runs the real event loop, so the delete would wait forever: every window of
    app.processEvents()                                                       # every test stayed open with its timers, and the later tests of a long run got slower and slower)


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
