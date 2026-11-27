"""Issue 13: closing the workbench ends the process in seconds, not minutes, and nothing is left running unseen.

Found by running the real window on a synthetic study: a tool call of the assistant that waits for a job ("wait for the run", up to
10 minutes) runs in a ThreadPoolExecutor thread, which Python joins when the interpreter ends; the closed window therefore kept the process
alive, invisible, until the wait ended, while the job (loading the results: one busy core, memory growing) went on in its daemon thread."""
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest
from qt_support import ctl  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


def _run(code: str, timeout: float = 60) -> tuple[float, str]:
    t0 = time.time()
    r = subprocess.run([sys.executable, "-c", textwrap.dedent(code)], cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    return time.time() - t0, r.stdout + r.stderr


WAITER = """
import asyncio, threading, time
{imp}
loop = asyncio.new_event_loop()
threading.Thread(target=loop.run_forever, daemon=True).start()
asyncio.run_coroutine_threadsafe({call}(time.sleep, 8), loop)   # (a tool call waiting for a job)
time.sleep(0.5)
print("main is done", flush=True)
"""


def test_a_waiting_tool_call_keeps_the_process_alive_with_the_standard_executor():
    """The cause, shown: with asyncio.to_thread the finished program waits for the call."""
    took, out = _run(WAITER.format(imp="", call="asyncio.to_thread"))
    assert "main is done" in out and took > 7


def test_a_waiting_tool_call_does_not_keep_the_process_alive_on_a_daemon_thread():
    took, out = _run(WAITER.format(imp="from simwb import daemonthread", call="daemonthread.to_thread"))
    assert "main is done" in out and took < 6


def test_the_daemon_thread_call_returns_results_and_errors():
    import asyncio

    from simwb import daemonthread

    async def go():
        assert await daemonthread.to_thread(lambda a, b=1: a + b, 2, b=3) == 5
        with pytest.raises(ZeroDivisionError):
            await daemonthread.to_thread(lambda: 1 / 0)
    asyncio.run(go())


def test_the_exit_watchdog_ends_a_process_that_does_not_end(tmp_path):
    from simwb.qt import app

    ended = threading.Event()
    got = {}
    app.ensure_exit(3, grace=0.2, exit_fn=lambda rc: (got.update(rc=rc), ended.set()), log_dir=tmp_path)
    assert ended.wait(3) and got["rc"] == 3
    text = (tmp_path / "close.log").read_text()          # (findable under pythonw, which has no console)
    assert "did not end by itself" in text and "still running" in text


def test_the_control_file_of_this_run_is_removed(tmp_path, monkeypatch):
    import importlib
    import json
    import os

    monkeypatch.setenv("SIMWB_HOME", str(tmp_path / "ws"))
    import simwb.project as P

    importlib.reload(P)
    from simwb.qt import app

    f = P.WORKSPACE / ".ui" / "control.json"
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps({"pid": os.getpid() + 1, "token": "x"}))   # (another run's: left alone)
    app._remove_control_file()
    assert f.exists()
    f.write_text(json.dumps({"pid": os.getpid(), "token": "x"}))
    app._remove_control_file()
    assert not f.exists()


# ------------------------------------------------------------------------------------------- the window
def _pump(cond, seconds=15):
    from PySide6.QtWidgets import QApplication

    t0 = time.time()
    while time.time() - t0 < seconds:
        QApplication.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def _study(ctl):
    ctl.do("study.new", {"name": "Plate", "study": "heat_conduction"})
    return ctl.engine.project()


def _job(pr, honours_cancel=True, child=False):
    from simwb import project as P

    state = {"children": [], "stop": threading.Event()}

    def work(job):
        if child:
            state["children"].append(subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"]))
            job.proc = state["children"][-1]
        while not state["stop"].is_set():
            time.sleep(0.02)
            if honours_cancel:
                job.check_cancel()
    j = P.JOBS.start("solve", pr.id, work)
    state["job"] = j
    return state


def test_closing_with_nothing_running_closes_at_once_and_says_nothing(ctl):
    pr = _study(ctl)
    pr._results = object()
    ctl.win.close()
    assert _pump(lambda: ctl.win._shutdown == "done" and not ctl.win.isVisible(), 5)
    assert ctl.win.toasts.history == [] or all("Closing" not in h["text"] for h in ctl.win.toasts.history)
    from simwb import project as P
    assert not P._OPEN and pr._results is None      # (the memory of the open studies is let go)


def test_closing_while_a_job_runs_asks_once_stops_it_and_closes(ctl, monkeypatch):
    pr = _study(ctl)
    asked = []
    monkeypatch.setattr(type(ctl.win), "_confirm_close", lambda self, n: asked.append(n) or True)
    st = _job(pr, child=True)
    assert _pump(lambda: st["children"], 5)
    ctl.win.close()
    assert _pump(lambda: ctl.win._shutdown == "done" and not ctl.win.isVisible(), 15)
    assert asked == [1]
    assert st["job"].status == "cancelled" and st["children"][0].poll() is not None   # the job and its child process are gone
    st["stop"].set()


def test_saying_no_keeps_the_window_and_the_job(ctl, monkeypatch):
    pr = _study(ctl)
    monkeypatch.setattr(type(ctl.win), "_confirm_close", lambda self, n: False)
    st = _job(pr)
    ctl.win.close()
    _pump(lambda: False, 0.5)
    assert getattr(ctl.win, "_shutdown", None) is None and st["job"].status == "running"
    monkeypatch.setattr(type(ctl.win), "_confirm_close", lambda self, n: True)
    ctl.win.close()                                   # (it can still be closed afterwards)
    assert _pump(lambda: ctl.win._shutdown == "done", 15)
    st["stop"].set()


def test_a_job_that_does_not_stop_cannot_keep_the_window_open_and_a_closing_notice_shows(ctl, monkeypatch):
    pr = _study(ctl)
    monkeypatch.setattr(type(ctl.win), "_confirm_close", lambda self, n: True)
    monkeypatch.setattr(type(ctl.win), "SHUTDOWN_GRACE_S", 1.5)
    st = _job(pr, honours_cancel=False)
    t0 = time.time()
    ctl.win.close()
    assert _pump(lambda: any("Closing" in h["text"] for h in ctl.win.toasts.history), 5)   # a small notice, not an invisible wait
    assert "Closing" in ctl.win.windowTitle()
    assert _pump(lambda: ctl.win._shutdown == "done" and not ctl.win.isVisible(), 15)
    assert time.time() - t0 < 6
    st["stop"].set()


# ---------------------------------------------------------------------------------- helper processes end with the app
def test_a_worker_ends_when_its_parent_is_gone(tmp_path):
    """The readers of the saved times (frame_worker) must not outlive the workbench: they ended minutes after it, holding gigabytes."""
    import os

    parent = tmp_path / "parent.py"
    parent.write_text(textwrap.dedent(f"""
        import os, subprocess, sys
        env = dict(os.environ, SIMWB_PARENT_PID=str(os.getpid()))
        code = "import sys; sys.path.insert(0, r'{ROOT}'); from simwb import parentwatch; parentwatch.start(0.2); import time; time.sleep(60)"
        p = subprocess.Popen([sys.executable, "-c", code], env=env)
        print(p.pid, flush=True)
    """))
    r = subprocess.run([sys.executable, str(parent)], capture_output=True, text=True, timeout=30)
    pid = int(r.stdout.split()[0])                    # (the parent has ended now)
    from simwb import parentwatch

    t0 = time.time()
    while time.time() - t0 < 10 and parentwatch.parent_alive(pid):
        time.sleep(0.1)
    assert not parentwatch.parent_alive(pid), "the worker is still running after its parent ended"
    assert parentwatch.parent_alive(os.getpid())


def test_no_parent_named_means_no_watch(monkeypatch):
    from simwb import parentwatch

    monkeypatch.delenv(parentwatch.ENV, raising=False)
    assert parentwatch.start() is None
