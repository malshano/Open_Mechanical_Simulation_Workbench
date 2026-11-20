"""Control layer of the desktop application: everything a person can see or do in the window, as calls.

* state        - one snapshot of what the window shows (step, study, every control's value, what the 3D view draws, the legend,
                 the camera, jobs, the assistant, error counts)
* actions      - every operation of the user interface by name (study.new, mesh.generate, results.show ...), with documented
                 parameters; the same widgets and handlers a mouse click would use are driven, so a bug in the UI shows up here
* widgets      - every control by its stable id (mesh.generate, results.colormap ...): get, click, set, select
* screenshot   - the window, the 3D view or one control, as a PNG file
* log          - Python logging, uncaught exceptions, Qt and VTK messages, anything printed: a ring buffer, readable by time
* wait         - until the running jobs are finished and the window has caught up
* eval         - run Python inside the application (debug mode only)
* coverage     - which controls have no id yet (a test keeps this empty: nothing in the window may be out of reach)

Every call may come from any thread (the web server's, the assistant's); it runs on the window's thread and waits for the answer.
Modal dialogs (file choosers) cannot be driven: their actions have an equivalent in `actions` that takes the path directly."""
from __future__ import annotations

import ast
import collections
import contextlib
import io
import logging
import os
import re
import secrets
import sys
import threading
import time
import traceback
from concurrent.futures import Future
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, QtMsgType, Signal, Slot, qInstallMessageHandler
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QAbstractButton, QAbstractItemView, QAbstractSpinBox, QApplication, QComboBox, QDockWidget, QLabel,
                               QLineEdit, QMenu, QPlainTextEdit, QProgressBar, QSlider, QTextEdit, QToolButton, QTreeWidget, QWidget)

from .. import project as P
from .. import studies, ui_bridge
from . import mode as app_mode
from . import theme
from .ids import of as wid_of

# controls that open a modal dialog: they cannot be driven (the dialog would wait for a person); use the named action instead
MODAL = {
    "file.open_folder": "study.open_folder", "file.save_as": "study.save_as", "file.export": "study.export",
    "file.import": "study.import", "geometry.import": "geometry.import", "help.about": None,
    "home.open_folder": "study.open_folder", "home.import": "study.import", "viewbar.picture": "view.picture",
    "chrome.close": None, "results.export.report": "results.export", "results.export.csv": "results.export", "results.export.vtk": "results.export",
}


def modal_alt(id: str):
    """(opens a dialog?, the action that replaces it) for a control id."""
    if id.startswith("home.new."):
        return True, "study.new"
    return (id in MODAL), MODAL.get(id)

from .panels.common import ClickLabel  # noqa: E402

INTERACTIVE = (QAbstractButton, QComboBox, QAbstractSpinBox, QSlider, QLineEdit, QPlainTextEdit, QTextEdit, QTreeWidget, ClickLabel)
INTERNAL_PARENTS = (QAbstractSpinBox, QComboBox, QAbstractItemView, QTextEdit, QPlainTextEdit)


# ------------------------------------------------------------------------------------------------------------- the log
class LogBuffer:
    """The last few thousand messages of the application, from every source, numbered so a reader can ask for what is new."""

    def __init__(self, size: int = 3000):
        self.items: collections.deque = collections.deque(maxlen=size)
        self.n = 0
        self.lock = threading.Lock()
        self.counts = {"error": 0, "warning": 0}
        self.vtk_file: Path | None = None
        self._vtk_pos = 0

    def add(self, level: str, source: str, text: str):
        text = str(text).rstrip()
        if not text:
            return
        with self.lock:
            self.n += 1
            self.items.append({"i": self.n, "time": time.strftime("%H:%M:%S"), "level": level, "source": source, "text": text[:6000]})
            if level in self.counts:
                self.counts[level] += 1

    def pull_vtk(self):
        """New lines of the VTK message file (VTK writes its warnings and errors from C++, outside Python)."""
        f = self.vtk_file
        if not f or not f.exists():
            return
        with open(f, "r", encoding="utf-8", errors="replace") as fh:
            fh.seek(self._vtk_pos)
            for line in fh.read().splitlines():
                lvl = "error" if "ERR|" in line else "warning" if "WARN|" in line else "info"
                self.add(lvl, "vtk", re.sub(r"^.*?\|\s*", "", line, count=1) if "|" in line else line)
            self._vtk_pos = fh.tell()

    def since(self, after: int = 0, level: str | None = None, source: str | None = None, contains: str | None = None,
              limit: int = 200) -> dict:
        self.pull_vtk()
        order = ["debug", "info", "warning", "error"]
        with self.lock:
            rows = [r for r in self.items if r["i"] > after]
            last = self.n
        if level:
            floor = order.index(level) if level in order else 0
            rows = [r for r in rows if order.index(r["level"]) >= floor]
        if source:
            rows = [r for r in rows if r["source"] == source]
        if contains:
            rows = [r for r in rows if contains.lower() in r["text"].lower()]
        return {"last": last, "errors": self.counts["error"], "warnings": self.counts["warning"], "entries": rows[-limit:]}


class _Tee(io.TextIOBase):
    """Passes writes on to the real stream and keeps them in the log."""

    def __init__(self, real, log: LogBuffer, level: str, source: str):
        self.real, self.log, self.level, self.source = real, log, level, source
        self.buf = ""

    def write(self, s):
        if self.real is not None:
            try:
                self.real.write(s)
            except Exception:  # noqa: BLE001
                pass
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            self.log.add(self.level, self.source, line)
        return len(s)

    def flush(self):
        if self.real is not None:
            with contextlib.suppress(Exception):
                self.real.flush()

    def isatty(self):
        return False

    def fileno(self):
        return self.real.fileno()


def install_log_hooks(log: LogBuffer):
    """Route the application's messages into the log. Returns a function that puts everything back as it was."""
    saved = (sys.excepthook, threading.excepthook, sys.stdout, sys.stderr)

    class H(logging.Handler):
        def emit(self, rec):
            lvl = {"CRITICAL": "error", "ERROR": "error", "WARNING": "warning", "INFO": "info"}.get(rec.levelname, "debug")
            log.add(lvl, rec.name, self.format(rec))
    h = H()
    h.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger()
    root.addHandler(h)
    level_before = root.level
    root.setLevel(min(root.level or logging.WARNING, logging.INFO))

    prev = sys.excepthook

    def hook(t, v, tb):
        log.add("error", "exception", "".join(traceback.format_exception(t, v, tb)))
        prev(t, v, tb)
    sys.excepthook = hook
    prev_t = threading.excepthook

    def thook(a):
        log.add("error", f"thread:{getattr(a.thread, 'name', '?')}", "".join(traceback.format_exception(a.exc_type, a.exc_value, a.exc_traceback)))
        prev_t(a)
    threading.excepthook = thook

    def qt_handler(mode, ctx, msg):
        lvl = {QtMsgType.QtDebugMsg: "debug", QtMsgType.QtInfoMsg: "info", QtMsgType.QtWarningMsg: "warning"}.get(mode, "error")
        log.add(lvl, "qt", msg)
        if sys.__stderr__:
            with contextlib.suppress(Exception):
                sys.__stderr__.write(msg + "\n")
    qInstallMessageHandler(qt_handler)
    sys.stderr = _Tee(sys.stderr, log, "warning", "stderr")
    sys.stdout = _Tee(sys.stdout, log, "info", "stdout")
    try:  # VTK's own messages
        from vtkmodules.vtkCommonCore import vtkFileOutputWindow, vtkOutputWindow
        d = P.WORKSPACE / ".ui"
        d.mkdir(parents=True, exist_ok=True)
        f = d / "vtk.log"
        f.write_text("")
        w = vtkFileOutputWindow()
        w.SetFileName(str(f))
        w.SetFlush(1)
        vtkOutputWindow.SetInstance(w)
        log.vtk_file = f
    except Exception as e:  # noqa: BLE001
        log.add("warning", "control", f"VTK messages are not captured: {e}")

    def restore():
        sys.excepthook, threading.excepthook, sys.stdout, sys.stderr = saved
        root.removeHandler(h)
        root.setLevel(level_before)
        qInstallMessageHandler(None)
    return restore


# ------------------------------------------------------------------------------------------------------- the controller
class Action:
    def __init__(self, name, doc, params, fn):
        self.name, self.doc, self.params, self.fn = name, doc, params or {}, fn

    def describe(self):
        return {"name": self.name, "doc": self.doc,
                "params": {k: {"type": t, "doc": d, "required": r} for k, (t, d, r) in self.params.items()}}


class Control(QObject):
    _req = Signal(object)

    def __init__(self, win, engine, bridge, app: QApplication, debug: bool = False):
        super().__init__(win)
        self.win, self.engine, self.bridge, self.app, self.debug = win, engine, bridge, app, debug
        # the secret that code execution (eval) needs, whichever way it is reached; api.start writes it to <workspace>/.ui/control.json
        self.token = secrets.token_urlsafe(24)
        self.log = LogBuffer()
        self._restore_hooks = install_log_hooks(self.log)
        self.actions: dict[str, Action] = {}
        self._name_menus()
        self._register()
        self._req.connect(self._exec, Qt.QueuedConnection)
        ui_bridge.HANDLER = self.handle
        self.log.add("info", "control", f"control layer ready ({len(self.actions)} actions, debug={debug})")

    def close(self):
        """Take the hooks out again (the window is closing, or a test is over)."""
        if ui_bridge.HANDLER == self.handle:
            ui_bridge.HANDLER = None
        self._restore_hooks()

    # ------------------------------------------------------------------ threads
    @Slot(object)
    def _exec(self, item):
        fn, fut = item
        try:
            fut.set_result(fn())
        except BaseException as e:  # noqa: BLE001
            fut.set_exception(e)

    def on_gui(self, fn, timeout: float = 120.0):
        """Run fn on the window's thread and return its result (or raise what it raised)."""
        if QThread.currentThread() is self.thread():
            return fn()
        fut: Future = Future()
        self._req.emit((fn, fut))
        try:
            return fut.result(timeout)
        except TimeoutError:
            raise RuntimeError("The window did not answer in time (a dialog may be open, or it is busy)") from None

    def handle(self, op: str, args: dict):
        """Entry point for the tools and the REST API (any thread)."""
        if op == "log":
            return self.log.since(**{k: v for k, v in args.items() if k in ("after", "level", "source", "contains", "limit")})
        if op == "wait":
            return self.wait_idle(float(args.get("timeout_s", 120)))
        if op == "eval":
            # Code execution is gated HERE, for every caller (REST route, tool endpoint, in-process): the debug switch and the secret
            # of this run. The secret is in <workspace>/.ui/control.json, which only this user's processes can read.
            if not self.debug:
                raise ValueError("eval is off. Start the window with --debug-api (or SIMWB_DEBUG_API=1) to turn it on.")
            if not secrets.compare_digest(str(args.get("token") or ""), self.token):
                raise ValueError("eval needs the control token of this run (<workspace>/.ui/control.json): it was missing or wrong")
            args = {"code": args.get("code", "")}
        table = {"state": self.state, "actions": self.list_actions, "do": self.do, "widgets": self.widgets, "widget": self.widget_do,
                 "screenshot": self.screenshot, "eval": self.eval_code, "coverage": self.coverage}
        if op not in table:
            raise ValueError(f"Unknown operation '{op}'. Operations: {', '.join(list(table) + ['log', 'wait'])}")
        return self.on_gui(lambda: table[op](**args))

    # ------------------------------------------------------------------ naming
    def _name_menus(self):
        for m in self.win.menuBar().findChildren(QMenu):
            if not m.objectName():
                m.setObjectName("menu." + re.sub(r"\W+", "_", m.title().replace("&", "").lower()).strip("_"))
            if not m.menuAction().objectName():
                m.menuAction().setObjectName(m.objectName() + ".open")
        for a in self.win.menuBar().actions():
            if a.menu() is not None and not a.objectName():
                a.setObjectName(a.menu().objectName() + ".open")

    # ------------------------------------------------------------------ widgets and actions of the window
    def _index(self) -> dict:
        """{id: widget or QAction} of everything addressable now."""
        idx: dict = {}
        for w in self.win.findChildren(QWidget):
            i = wid_of(w)
            if i:
                idx[i] = w
        for a in self.win.findChildren(QAction):
            if a.objectName() and not a.isSeparator():
                idx[a.objectName()] = a
        return idx

    def _is_internal(self, w: QWidget) -> bool:
        p = w.parentWidget()
        while p is not None:
            if isinstance(p, INTERNAL_PARENTS):
                return True
            p = p.parentWidget()
        # (Qt's own parts: the float/close buttons in a dock's title bar, the toolbar's overflow button)
        return isinstance(w.parentWidget(), QDockWidget) and isinstance(w, QAbstractButton) or             type(w).__name__.startswith(("QDockWidget", "QToolBarExtension", "QMenuBar"))

    def _owner_id(self, w: QWidget) -> str | None:
        p = w.parentWidget()
        while p is not None:
            if wid_of(p):
                return wid_of(p)
            p = p.parentWidget()
        return None

    @staticmethod
    def _value(o):
        if isinstance(o, QAction):
            return {"checked": o.isChecked(), "enabled": o.isEnabled()} if o.isCheckable() else {"enabled": o.isEnabled()}
        if isinstance(o, QComboBox):
            return {"text": o.currentText(), "index": o.currentIndex(), "items": [o.itemText(i) for i in range(o.count())]}
        if isinstance(o, QAbstractButton):
            return {"checked": o.isChecked()} if o.isCheckable() else None
        if isinstance(o, (QAbstractSpinBox, QSlider)):
            return o.value()
        if isinstance(o, QProgressBar):
            return {"value": o.value(), "maximum": o.maximum()}
        if isinstance(o, QLineEdit):
            return o.text()
        if isinstance(o, (QPlainTextEdit, QTextEdit)):
            return o.toPlainText()[-4000:]
        if isinstance(o, QLabel):
            return o.text()
        if isinstance(o, QTreeWidget):
            cur = o.currentItem()
            return {"selected": cur.text(0) if cur else None, "items": _tree_paths(o)}
        return None

    def _entry(self, i: str, o) -> dict:
        if isinstance(o, QAction):
            return {"id": i, "class": "QAction", "kind": "action", "text": o.text().replace("&", ""), "value": self._value(o),
                    "enabled": o.isEnabled(), "visible": o.isVisible(), "modal": modal_alt(i)[0]}
        text = o.text() if hasattr(o, "text") and callable(o.text) and not isinstance(o, (QPlainTextEdit, QTextEdit)) else None
        r = o.rect()
        tl = o.mapTo(self.win, r.topLeft())
        return {"id": i, "class": type(o).__name__, "kind": "widget", "text": text if text is not None else o.property("api_text"),
                "value": self._value(o), "enabled": o.isEnabled(), "visible": o.isVisible(), "tooltip": o.toolTip() or None,
                "rect": [tl.x(), tl.y(), r.width(), r.height()], "inside": self._owner_id(o), "modal": modal_alt(i)[0]}

    def widgets(self, filter: str | None = None, visible_only: bool = False) -> dict:
        """Every addressable control: {id, class, text, value, enabled, visible, rect (in the window), inside}."""
        rows = []
        for i, o in sorted(self._index().items()):
            if filter and filter.lower() not in i.lower():
                continue
            e = self._entry(i, o)
            if visible_only and not e["visible"]:
                continue
            rows.append(e)
        return {"count": len(rows), "widgets": rows}

    def coverage(self) -> dict:
        """Controls and menu entries that have no id: nothing may be out of reach of the API."""
        bad_w, total_w, unlabelled, no_pointer = [], 0, [], []
        for w in self.win.findChildren(QWidget):
            if isinstance(w, QToolButton) or not isinstance(w, INTERACTIVE) or self._is_internal(w):
                continue
            total_w += 1
            if not wid_of(w):
                bad_w.append({"class": type(w).__name__, "text": getattr(w, "text", lambda: "")() if hasattr(w, "text") else "",
                              "inside": self._owner_id(w)})
                continue
            # a signifier: the control says what it is
            if not (w.accessibleName() or w.toolTip() or (hasattr(w, "text") and not isinstance(w, (QPlainTextEdit, QTextEdit)) and w.text())):
                unlabelled.append(wid_of(w))
            # an affordance: what can be pressed or chosen looks and acts clickable
            if isinstance(w, (QAbstractButton, QComboBox, QSlider, ClickLabel)) and w.cursor().shape() != Qt.PointingHandCursor:
                no_pointer.append(wid_of(w))
        bad_a, total_a = [], 0
        for a in self.win.findChildren(QAction):
            if a.isSeparator():
                continue
            total_a += 1
            if not a.objectName():
                bad_a.append({"text": a.text()})
        return {"widgets": total_w, "actions": total_a, "unnamed_widgets": bad_w, "unnamed_actions": bad_a,
                "unlabelled": unlabelled, "no_pointer_cursor": no_pointer,
                "complete": not bad_w and not bad_a and not unlabelled and not no_pointer}

    def widget_do(self, id: str, op: str = "get", value=None) -> dict:
        """op: get | click | set | select (a tree item, by its path 'Study/Geometry') | text."""
        idx = self._index()
        o = idx.get(id)
        if o is None:
            near = [k for k in idx if id.split(".")[0] in k][:12]
            raise ValueError(f"No control with the id '{id}'. Similar ids: {near or 'none'} (list them all with widgets)")
        before = self.log.n
        if op == "get":
            pass
        elif modal_alt(id)[0] and op in ("click", "set", "select", "text"):
            alt = modal_alt(id)[1]
            raise ValueError(f"'{id}' opens a dialog that waits for a person." + (f" Use the action '{alt}' with the path instead." if alt else ""))
        elif op == "click":
            if isinstance(o, QAction):
                if not o.isEnabled():
                    raise ValueError(f"'{id}' is disabled")
                o.trigger()
            else:
                if not o.isEnabled() or not o.isVisible():
                    raise ValueError(f"'{id}' is {'disabled' if not o.isEnabled() else 'not visible'} now")
                o.click()
        elif op in ("set", "text"):
            self._set(o, id, value)
        elif op == "select":
            if not isinstance(o, QTreeWidget):
                raise ValueError(f"'{id}' is not a tree")
            it = _tree_find(o, str(value))
            if it is None:
                raise ValueError(f"No tree item '{value}'. Items: {_tree_paths(o)[:20]}")
            o.setCurrentItem(it)
            o.itemClicked.emit(it, 0)
        else:
            raise ValueError("op must be get, click, set, select or text")
        self._settle()
        e = self._entry(id, o)
        e["new_log"] = self._new_problems(before)
        return e

    def _set(self, o, id, value):
        if isinstance(o, QComboBox):
            i = o.findText(str(value)) if not isinstance(value, int) or isinstance(value, bool) else int(value)
            if i < 0:
                i = o.findData(value)
            if i < 0 or i >= o.count():
                raise ValueError(f"'{id}' has no item '{value}'. Items: {[o.itemText(k) for k in range(o.count())]}")
            o.setCurrentIndex(i)
            o.activated.emit(i)              # (a choice, as the user's click on the list would be)
        elif isinstance(o, QAbstractButton):
            if not o.isCheckable():
                raise ValueError(f"'{id}' is a push button: use click")
            o.setChecked(bool(value))
        elif isinstance(o, (QAbstractSpinBox, QSlider)):
            o.setValue(float(value) if hasattr(o, "decimals") else int(value))
        elif isinstance(o, QLineEdit):
            o.setText(str(value))
            o.editingFinished.emit()
        elif isinstance(o, QPlainTextEdit):
            o.setPlainText(str(value))
        else:
            raise ValueError(f"'{id}' ({type(o).__name__}) cannot be set")

    def _settle(self):
        """Let the window finish what a change started (its redraws are debounced), so the answer shows the result."""
        for _ in range(3):
            self.app.processEvents()
        t = getattr(self.win, "_refresh_timer", None)
        if t is not None and t.isActive():
            t.stop()
            self.win._refresh_all()
        self.app.processEvents()

    def _new_problems(self, after: int) -> list:
        self.log.pull_vtk()
        with self.log.lock:
            return [r for r in self.log.items if r["i"] > after and r["level"] in ("warning", "error")][-10:]

    # ------------------------------------------------------------------ state
    def state(self) -> dict:
        win, eng, view = self.win, self.engine, self.win.view
        panels = {}
        for k, p in win.panels.items():
            panels[k] = {wid_of(w): self._value(w) for w in p.findChildren(QWidget) if wid_of(w) and self._value(w) is not None}
            panels[k].update({wid_of(w): w.property("api_text") for w in p.findChildren(QWidget) if wid_of(w) and w.property("api_text")})
            panels[k].update({wid_of(w): {"button": w.text(), "enabled": w.isEnabled()} for w in p.findChildren(QAbstractButton)
                              if wid_of(w) and not w.isCheckable()})
        st = None
        if eng.pid:
            s = eng.state()
            st = {"id": s["id"], "name": s["name"], "study": s["study"], "path": s.get("path"),
                  "geometry": s["geometry"].get("status"), "bodies": [b["name"] for b in s["body_list"]],
                  "mesh": {"status": s["mesh"].get("status"), "preset": s["mesh"].get("preset"),
                           "cells": (s["mesh"].get("stats") or {}).get("cells")},
                  "solve": s["solve"].get("status"), "bcs": [b["name"] for b in s.get("bcs") or []],
                  "checks": s.get("checks")}
        a = win.assistant
        jobs = [j.to_dict(log_tail=0) for j in P.JOBS.jobs.values() if j.status == "running"]
        self.log.pull_vtk()
        return {
            "window": {"title": win.windowTitle(), "size": [win.width(), win.height()], "step": win.step,
                       "theme": {"mode": theme.mode(), "effective": theme.effective()}, "expert": not app_mode.simple(),
                       "screen": "home" if win.center.currentWidget() is win.home else "study",
                       "status_message": win.statusBar().currentMessage(), "visible": win.isVisible()},
            "study": st,
            "panels": panels,
            "view": {"overlay": view.overlay_info, "geometry": view.geometry_info, "camera": view.camera(),
                     "pick_mode": view.pick_mode, "selection": view.selection, "face_tints": view.face_tints,
                     "legend": getattr(view.legend, "spec", None) and {k: v for k, v in view.legend.spec.items() if k != "table"},
                     "gl": view.rw is not None},
            "docks": {d.objectName().removeprefix("dock."): d.isVisible() for d in win.findChildren(QDockWidget)},
            "jobs_running": jobs,
            "assistant": {"busy": a.busy, "login": a.who.text(), "status": a.status.text(),
                          "messages": len(a.log.get(a.pid or "", [])), "last": a.log.get(a.pid or "", [])[-3:]},
            "log": {"errors": self.log.counts["error"], "warnings": self.log.counts["warning"], "last_id": self.log.n},
            "debug": self.debug,
        }

    # ------------------------------------------------------------------ actions
    def list_actions(self) -> dict:
        return {"actions": [a.describe() for a in self.actions.values()],
                "note": "do(action, args). Every control also has an id: see widgets / widget."}

    def do(self, action: str, args: dict | None = None) -> dict:
        a = self.actions.get(action)
        if a is None:
            raise ValueError(f"Unknown action '{action}'. Actions: {', '.join(sorted(self.actions))}")
        args = dict(args or {})
        unknown = set(args) - set(a.params)
        if unknown:
            raise ValueError(f"Unknown argument(s) {sorted(unknown)} for {action}. Allowed: {sorted(a.params) or 'none'}")
        missing = [k for k, (_, _, req) in a.params.items() if req and k not in args]
        if missing:
            raise ValueError(f"Missing argument(s) {missing} for {action}")
        before = self.log.n
        result = a.fn(**args)
        self._settle()
        return {"action": action, "result": result, "step": self.win.step, "study": self.engine.pid,
                "overlay": self.win.view.overlay_info, "status_message": self.win.statusBar().currentMessage(),
                "new_log": self._new_problems(before)}

    def _reg(self, name, doc, params, fn):
        self.actions[name] = Action(name, doc, params, fn)

    def _w(self, id):
        o = self._index().get(id)
        if o is None:
            raise ValueError(f"The control '{id}' does not exist now")
        return o

    def _click(self, id):
        o = self._w(id)
        if not o.isEnabled() or not o.isVisible():
            raise ValueError(f"The control '{id}' is {'disabled' if not o.isEnabled() else 'not visible'} now (step: {self.win.step})")
        o.click()

    def _register(self):
        eng, win, R = self.engine, self.win, self._reg
        S, N, B = "string", "number", "boolean"

        def need_study():
            if not eng.pid:
                raise ValueError("No study is open (study.new, study.open_folder or study.open first)")

        # ---- cases
        R("study.new", "Create a study and open it.", {"name": (S, "name", True), "study": (S, "study type key, e.g. static_structural", True),
                                                        "location": (S, "folder to keep it in (default: the workspace)", False)},
          lambda name, study, location=None: {"id": eng.new_study(name, study, location).id})
        R("study.open_folder", "Open a study folder (one with project.json) from anywhere on the disk.", {"path": (S, "folder", True)},
          lambda path: {"id": eng.open_folder(path).id})
        R("study.open", "Open a study of the workspace by id.", {"id": (S, "study id (see study.list)", True)},
          lambda id: {"id": eng.open_id(id).id})
        R("study.close", "Close the open study.", {}, lambda: eng.close_study() or {"closed": True})
        R("study.save", "Write the open study now.", {}, lambda: (need_study(), {"saved_at": eng.save()})[1])
        R("study.save_as", "Save a full copy in another folder; the copy becomes the open study.",
          {"location": (S, "folder", True), "name": (S, "name of the copy", False), "keep_results": (B, "include case + results (default true)", False)},
          lambda location, name=None, keep_results=True: (need_study(), {"id": eng.save_as(location, name, keep_results).id})[1])
        R("study.export", "Write the open study to ONE .zip file.", {"path": (S, "full path of the .zip", True), "keep_results": (B, "default true", False)},
          lambda path, keep_results=True: (need_study(), {"file": str(eng.export_zip(path, keep_results))})[1])
        R("study.import", "Open a study from a file made by study.export.", {"path": (S, ".zip file", True), "location": (S, "folder (default: workspace)", False)},
          lambda path, location=None: {"id": eng.import_zip(path, location).id})
        R("study.example", "Start a study from a bundled example (as the 'Try example' button of the start screen does): creates it in the "
                           "folder last used and imports the example CAD file; a job (use wait).",
          {"study": (S, "study type key", True), "example": (S, "example file, e.g. heated_plate.step (default: the first of the type)", False)},
          lambda study, example=None: self._example(study, example))
        R("study.types", "The study types of the start screen, with their examples.", {},
          lambda: {"types": [{"key": k, "label": s["label"], "examples": s.get("examples"), "solver": s.get("solver"),
                              "multiphysics": s.get("category") == "multiphysics"} for k, s in studies.STUDIES.items()]})
        R("study.list", "The studies of the workspace.", {}, lambda: {"studies": eng.workspace_list()})
        R("study.recent", "The studies opened lately.", {}, lambda: {"recent": eng.recent()})
        R("study.state", "The whole project state (the same as the REST API's).", {}, lambda: (need_study(), eng.state())[1])
        # ---- steps
        def go(step):
            need_study()
            if step not in win.step_actions:
                raise ValueError(f"step must be one of {list(win.step_actions)}")
            win.go(step)
            return {"step": win.step}
        R("step.go", "Show a step: geometry, setup, mesh, solve or results.", {"step": (S, "step name", True)}, go)
        R("geometry.import", "Import a CAD file (STEP, STL, OBJ) into the open study; a job.", {"path": (S, "absolute path", True)},
          lambda path: (need_study(), {"job": eng.import_step(path).to_dict(log_tail=0)})[1])
        R("setup.material", "Set the material of a body (through the same combo box a person uses).",
          {"body_id": (N, "body id", True), "material": (S, "material key or its label", True)},
          lambda body_id, material: self._material(int(body_id), material))
        # ---- mesh
        R("mesh.preset", "Choose the mesh resolution: quick, coarse, medium, fine, very_fine.", {"preset": (S, "preset", True)},
          lambda preset: (self._click(f"mesh.preset.{preset}"), {"preset": preset})[1])
        R("mesh.generate", "Generate (or regenerate) the mesh; a job (use wait).", {}, lambda: (self._click("mesh.generate"), {"started": True})[1])
        R("mesh.view", "How the mesh is shown: colour by quality | size | region; cut through it along x, y, z or off.",
          {"color": (S, "quality|size|region", False), "cut": (S, "x|y|z|off", False), "position": (N, "cut position 0..1", False)},
          lambda color=None, cut=None, position=None: self._mesh_view(color, cut, position))
        # ---- solve and results
        def mesh_poor(on=True, threshold=None):
            if threshold is not None:
                self._set(self._w("mesh.poor.threshold"), "mesh.poor.threshold", str(threshold))
            self._set(self._w("mesh.poor.on"), "mesh.poor.on", bool(on))
            return {"poor": win.panels["mesh"].poor_on, "threshold": win.panels["mesh"].poor_thr, "info": win.panels["mesh"].poor_info.text()}
        R("mesh.poor", "Show only the cells below a quality threshold on the model (the mesher's weak spots), or switch that off.",
          {"on": (B, "default true", False), "threshold": (N, "quality 0.01-0.9 (default 0.1)", False)}, mesh_poor)
        R("mesh.zoom_worst", "Turn the 3D view to the poorest cell.", {}, lambda: {"worst": win.panels["mesh"].zoom_worst()})
        R("solve.run", "Run the simulation; a job (use wait).", {}, lambda: (self._click("solve.run"), {"started": True})[1])
        R("results.show", "Choose what the results step shows. Omitted arguments stay as they are.",
          {"field": (S, "field name (see state.panels.results)", False), "colormap": (S, "turbo|viridis|plasma|inferno|cool-warm|rainbow|grayscale", False),
           "reverse": (B, "reverse the colours", False), "bands": (N, "colour bands, 0 = smooth", False),
           "deform": (B, "show the deformation", False), "edges": (B, "show the mesh edges", False),
           "animate": (B, "animate the deformation (the load applied and released)", False),
           "scale": (N, "deformation exaggeration (0 or omitted: automatic; 1 = true size)", False),
           "range": ("array", "[lo, hi] colour range; [] for automatic", False), "minmax": (B, "label the highest and lowest values", False),
           "section": (S, "x|y|z: cut the part across that axis; '' (or off) for no section", False),
           "section_pos": (N, "0..1: where the plane cuts (from one end of the part to the other)", False)},
          lambda **kw: self._results(**kw))

        def rp():
            if not eng.pid:
                raise ValueError("No study is open")
            return win.panels["results"]
        R("results.tab", "Show a tab of the results step: summary, display, probe or checks.", {"name": (S, "tab", True)},
          lambda name: (self._click(f"results.tab.{name}"), {"tab": name})[1])
        R("results.pin", "Pin the value of the drawn result at a point (world coordinates, m): what a Probe click does.",
          {"x": (N, "x", True), "y": (N, "y", True), "z": (N, "z", True)}, lambda x, y, z: {"pin": rp().pin_at([x, y, z])})
        R("results.line", "Plot the field along the straight line between two points of the undeformed part (m).",
          {"p0": ("array", "[x,y,z]", True), "p1": ("array", "[x,y,z]", True)},
          lambda p0, p1: (setattr(rp(), "line_ends", [list(p0), list(p1)]), {"line": {k: v for k, v in (rp().fetch_line() or {}).items() if k != "values" and k != "s"}})[1])
        R("results.line_minmax", "Plot the field along the line from its lowest to its highest value.", {},
          lambda: (rp().line_min_max(), {"line": {k: v for k, v in (rp().line_data or {}).items() if k not in ("values", "s")}})[1])
        R("results.clear_probes", "Remove the pinned values and the line plot.", {}, lambda: (rp().clear_probes(), {"cleared": True})[1])
        R("results.export", "Write the results to a file: format report (HTML) | csv | vtk.",
          {"format": (S, "report|csv|vtk", True), "path": (S, "full path of the file", True)},
          lambda format, path: {"file": rp().export(format, path)})
        # ---- view
        R("view.set", "Turn the 3D view: iso, +x, -x, +y, -y, +z, -z.", {"name": (S, "direction", True)},
          lambda name: (win.view.set_view(name), {"camera": win.view.camera()})[1])
        R("view.fit", "Fit the 3D view to what is drawn.", {}, lambda: (win.view.fit(), {"camera": win.view.camera()})[1])
        R("view.camera", "Set the camera (any of position, focal_point, view_up as [x,y,z]; view_angle in degrees).",
          {"position": ("array", "[x,y,z]", False), "focal_point": ("array", "[x,y,z]", False), "view_up": ("array", "[x,y,z]", False),
           "view_angle": (N, "degrees", False)},
          lambda **kw: (win.view.set_camera(**kw), {"camera": win.view.camera()})[1])
        def air_box(visible=True):
            if not win.act_air.isVisible():
                raise ValueError("This study has no air box (flow and cooling studies have one)")
            win.chk_air.setChecked(bool(visible))
            return {"visible": win.chk_air.isChecked(), "geometry": win.view.geometry_info}
        R("view.air_box", "Show or hide the air box of a flow or cooling study (translucent).", {"visible": (B, "default true", False)}, air_box)
        def pick_face(id, add=False):
            win.view.click_face(int(id), bool(add))           # as a click on that face would (the contract in Viewport)
            return {"picked": int(id), "add": bool(add), "selection": win.view.selection}
        R("view.pick_face", "Click a CAD face in the 3D view (emits Viewport.facePicked): what a panel does with a picked face.",
          {"id": (N, "face id", True), "add": (B, "Ctrl/Shift held: add to the selection", False)}, pick_face)
        R("view.pick_point", "Click a point of the shown surface (emits Viewport.pointPicked), coordinates in metres.",
          {"x": (N, "x", True), "y": (N, "y", True), "z": (N, "z", True)},
          lambda x, y, z: (win.view.pointPicked.emit(float(x), float(y), float(z)), {"picked": [x, y, z]})[1])
        R("view.click", "Click at a pixel of the 3D view as the mouse would (needs the OpenGL window): picks the face or surface point there.",
          {"x": (N, "pixel x (origin top-left of the 3D view)", True), "y": (N, "pixel y", True), "add": (B, "Ctrl held", False)},
          lambda x, y, add=False: {"hit": win.view.click_at(float(x), float(y), bool(add)), "selection": win.view.selection})
        R("view.pick_ray", "What a segment p0 -> p1 (world coordinates, m) hits in the 3D view, without clicking: a CAD face (while the model "
                           "is shown) or a point of the drawn result.",
          {"p0": ("array", "[x,y,z]", True), "p1": ("array", "[x,y,z]", True)}, lambda p0, p1: {"hit": win.view.pick_ray(p0, p1)})
        R("view.project", "Where a world point is on the 3D view's pixels (to click it with view.click).", {"point": ("array", "[x,y,z] in m", True)},
          lambda point: {"pixel": win.view.world_to_screen(point)})
        R("view.selection", "Set the selected faces (as a panel would).", {"ids": ("array", "face ids", True)},
          lambda ids: (win.view.set_selection(ids), {"selection": win.view.selection})[1])
        R("view.pick_mode", "What a click in the 3D view does: face | point | none.", {"mode": (S, "face|point|none", True)},
          lambda mode: (win.view.set_pick_mode(None if mode in ("none", "") else mode), {"pick_mode": win.view.pick_mode})[1])
        R("view.edges", "Show or hide the edges and face outlines of the model.", {"visible": (B, "default true", False)},
          lambda visible=True: (win.view.viewbar.edges.setChecked(bool(visible)), {"edges": win.view.edges_on})[1])
        R("view.picture", "Save the 3D view with its colour scale as a PNG.", {"path": (S, "full path of the .png", True)},
          lambda path: (win.view.picture(path), {"file": path})[1])
        def set_theme(mode):
            win.set_theme(mode)
            self._settle()
            return {"mode": theme.mode(), "effective": theme.effective()}
        R("app.mode", "Simple (only the essential settings) or expert (every setting and tool).", {"mode": (S, "simple|expert", True)},
          lambda mode: self._set_mode(mode))
        R("theme.set", "Switch the look of the window: system, dark or light.", {"mode": (S, "system|dark|light", True)}, set_theme)
        def setup_panel():
            if not eng.pid:
                raise ValueError("No study is open")
            return win.panels["setup"]

        def bc_add(type, faces=None, name=None):
            win.go("setup")
            self._settle()
            bc = setup_panel().add_bc(type, faces, name)
            self._settle()
            return {"bc": bc and {k: bc.get(k) for k in ("id", "name", "type", "faces")}}

        def bc_faces(bc, faces):
            setup_panel().set_bc_faces(self._bc_id(bc), faces)
            return {"bc": bc, "faces": list(faces)}

        def bc_delete(bc):
            setup_panel().delete_bc(self._bc_id(bc))
            return {"deleted": bc}

        def bc_set(bc, name=None, params=None):
            cur = next(b for b in eng.state()["bcs"] if b["id"] == self._bc_id(bc))
            eng.project().update_bc(cur["id"], name=name, params={**(cur.get("params") or {}), **(params or {})} if params else None)
            eng.changed.emit()
            return {"bc": bc}

        def setting(key, value):
            eng.project().set_settings(**{key: value})
            eng.changed.emit()
            return {key: value}
        R("setup.bc_add", "Add a boundary condition of a type (study.types / the study's face_bc_types) on faces (default: the faces selected "
                          "in the view; none selected: it starts picking faces).",
          {"type": (S, "boundary-condition type key, e.g. fixed_support, force", True), "faces": ("array", "face ids", False), "name": (S, "name", False)},
          bc_add)
        R("setup.bc_faces", "Set the faces of a boundary condition (by id or name).", {"bc": (S, "id or name", True), "faces": ("array", "face ids", True)}, bc_faces)
        R("setup.bc_delete", "Delete a boundary condition (by id or name).", {"bc": (S, "id or name", True)}, bc_delete)
        R("setup.bc_set", "Rename a boundary condition and/or change parameters ({'fz_N': -50}).",
          {"bc": (S, "id or name", True), "name": (S, "new name", False), "params": ("object", "parameters to change", False)}, bc_set)
        R("setup.setting", "Set a study setting (see the 'Loads and settings' form), e.g. gravity: -z.",
          {"key": (S, "setting key", True), "value": ("any", "new value", True)}, setting)
        R("view.stats", "What the 3D view holds and whether its picture is blank (bounds, colours).", {}, lambda: win.view.stats())
        # ---- assistant
        R("assistant.ask", "Type a question into the assistant as the user would (it answers asynchronously: see assistant.transcript).",
          {"text": (S, "message", True)}, lambda text: (need_study(), win.assistant.ask(text), {"sent": True})[2])
        R("assistant.stop", "Stop the assistant's current answer.", {}, lambda: (win.assistant.bridge.stop(win.assistant.pid), {"stopped": True})[1])
        R("assistant.new_chat", "Start a fresh conversation.", {}, lambda: (win.assistant.new_chat(), {"ok": True})[1])
        R("assistant.transcript", "The conversation of the open study.", {"last": (N, "how many messages (default 20)", False)},
          lambda last=20: {"busy": win.assistant.busy, "messages": [{"role": r, "text": t} for r, t in win.assistant.log.get(win.assistant.pid or "", [])[-int(last):]]})
        # ---- window
        R("window.resize", "Resize the window.", {"width": (N, "pixels", True), "height": (N, "pixels", True)},
          lambda width, height: (win.resize(int(width), int(height)), {"size": [win.width(), win.height()]})[1])
        R("dock.show", "Show or hide a dock: outline, step, assistant, log.", {"name": (S, "dock", True), "visible": (B, "default true", False)},
          lambda name, visible=True: self._dock(name, visible))
        R("jobs.status", "Running jobs and the latest job of each kind for the open study.", {}, self._jobs)

    def _example(self, study, example):
        spec = studies.STUDIES.get(study)
        if spec is None:
            raise ValueError(f"Unknown study type '{study}'. Types: {', '.join(studies.STUDIES)}")
        ex = example or (spec.get("examples") or [None])[0]
        if ex is None or ex not in (spec.get("examples") or []):
            raise ValueError(f"'{example}' is not an example of {study}. Examples: {spec.get('examples')}")
        self.win.new_from_card(study, ex)
        return {"study": self.engine.pid, "example": ex}

    def _set_mode(self, mode):
        if mode not in ("simple", "expert"):
            raise ValueError("mode must be simple or expert")
        app_mode.set_simple(mode == "simple")
        self._settle()
        return {"expert": not app_mode.simple()}

    def _bc_id(self, ref):
        """A boundary condition's id from its id or its name."""
        for b in self.engine.state().get("bcs") or []:
            if ref in (b["id"], b["name"]):
                return b["id"]
        raise ValueError(f"No boundary condition '{ref}'. Have: {[b['name'] for b in self.engine.state().get('bcs') or []]}")

    def _material(self, body_id, material):
        need = self._index().get(f"setup.material.{body_id}")
        if need is None:
            raise ValueError(f"No material control for body {body_id}: is the study on a step with that body? (state.study.bodies)")
        self._set(need, f"setup.material.{body_id}", material)
        return {"body_id": body_id, "material": need.currentText()}

    def _mesh_view(self, color, cut, position):
        if color:
            self._click(f"mesh.color.{color}")
        if cut is not None:
            self._click(f"mesh.cut.{'off' if cut in ('', 'off') else cut}")
        if position is not None:
            self._set(self._w("mesh.cut_position"), "mesh.cut_position", int(float(position) * 1000))
        return {"color": self.win.panels["mesh"].color, "cut": self.win.panels["mesh"].axis or "off", "position": self.win.panels["mesh"].frac}

    def _results(self, field=None, colormap=None, reverse=None, bands=None, deform=None, edges=None, animate=None, scale=None, range=None,
                 minmax=None, section=None, section_pos=None):
        panel = self.win.panels["results"]
        if section_pos is not None:
            self._set(self._w("results.section.pos"), "results.section.pos", int(round(float(section_pos) * 1000)))
        if section is not None:
            key = "" if str(section).lower() in ("", "off", "none") else str(section).lower()
            if key not in ("", "x", "y", "z"):
                raise ValueError("section must be x, y, z or off")
            self._click(f"results.section.{key or 'off'}")
        for id, v in (("results.field", field), ("results.colormap", colormap), ("results.reverse", reverse), ("results.bands", bands),
                      ("results.deform", deform), ("results.edges", edges), ("results.minmax", minmax)):
            if v is not None:
                self._set(self._w(id), id, v)
        if animate is not None:
            self._set(self._w("results.deform_mode"), "results.deform_mode", "Animated" if animate else "End state")
        if scale is not None:
            self._set(self._w("results.deform_scale"), "results.deform_scale", "" if not scale else str(scale))
        if range is not None:
            if range:
                self._set(self._w("results.range.auto"), "results.range.auto", False)
                self._set(self._w("results.range.min"), "results.range.min", str(range[0]))
                self._set(self._w("results.range.max"), "results.range.max", str(range[1]))
            else:
                self._set(self._w("results.range.auto"), "results.range.auto", True)
        return {k: self._value(self._w(f"results.{k}")) for k in ("field", "colormap", "reverse", "bands", "deform", "edges", "minmax")}

    def _dock(self, name, visible):
        d = self.win.findChild(QDockWidget, f"dock.{name}")
        if d is None:
            raise ValueError("dock must be outline, step, assistant or log")
        d.setVisible(bool(visible))
        return {"name": name, "visible": d.isVisible()}

    def _jobs(self):
        out = {"running": [j.to_dict(log_tail=0) for j in P.JOBS.jobs.values() if j.status == "running"], "latest": {}}
        if self.engine.pid:
            for kind in ("prepare", "mesh", "solve", "check"):
                j = P.JOBS.latest(self.engine.pid, kind)
                if j:
                    out["latest"][kind] = j.to_dict(log_tail=5)
        return out

    # ------------------------------------------------------------------ pictures
    def screenshot(self, what: str = "window") -> dict:
        """what: window | viewport | a control id. A PNG file is written; its path is returned."""
        d = P.WORKSPACE / ".ui" / "shots"
        d.mkdir(parents=True, exist_ok=True)
        old = sorted(d.glob("*.png"), key=lambda p: p.stat().st_mtime)
        for p in old[:-30]:   # (keep the last thirty)
            with contextlib.suppress(OSError):
                p.unlink()
        name = re.sub(r"\W+", "_", what)
        path = d / f"{time.strftime('%H%M%S')}-{name}.png"
        self._settle()
        if what == "window":
            self.win.screenshot(str(path))
        elif what == "viewport":
            self.win.view.image().save(str(path), "PNG")
        else:
            o = self._index().get(what)
            if o is None or isinstance(o, QAction):
                raise ValueError(f"what must be window, viewport or a control id (not '{what}')")
            (self.win.view.image() if what == "view.canvas" else o.grab().toImage()).save(str(path), "PNG")
        from PySide6.QtGui import QImage
        im = QImage(str(path))
        return {"path": str(path), "width": im.width(), "height": im.height(), "what": what}

    # ------------------------------------------------------------------ waiting, eval
    def wait_idle(self, timeout_s: float = 120.0) -> dict:
        """Until no job of the open study is running and the window has caught up (not on the window's thread)."""
        t0 = time.time()
        pid = self.engine.pid
        while time.time() - t0 < timeout_s:
            running = [j for j in list(P.JOBS.jobs.values()) if j.status == "running" and (pid is None or j.project == pid)]
            if not running:
                break
            time.sleep(0.25)
        idle = not [j for j in list(P.JOBS.jobs.values()) if j.status == "running" and (pid is None or j.project == pid)]
        self.on_gui(self._settle)
        return {"idle": idle, "waited_s": round(time.time() - t0, 1), "jobs": self.on_gui(self._jobs)}

    def eval_code(self, code: str) -> dict:
        """Run Python inside the application (debug mode only): win, engine, view, control, app, P and np are in scope.

        SECURITY: this runs arbitrary code with the user's rights. That is the point of it - it is the debugging console that lets
        Claude inspect and change anything in the running window - so it is OFF unless the window was started with --debug-api (or
        SIMWB_DEBUG_API=1), the HTTP endpoint also needs the per-run token that only this user's files hold, and every use is
        written to the log. Never enable it on a machine that other people use over the network."""
        if not self.debug:
            raise ValueError("eval is off. Start the window with --debug-api (or SIMWB_DEBUG_API=1) to turn it on.")
        import numpy as np
        env = {"win": self.win, "engine": self.engine, "view": self.win.view, "control": self, "app": self.app, "P": P, "np": np,
               "assistant": self.win.assistant}
        out, before = io.StringIO(), self.log.n
        value = None
        self.log.add("warning", "control", "eval: " + code[:300].replace("\n", " | "))
        try:
            tree = ast.parse(code)
            last = tree.body.pop() if tree.body and isinstance(tree.body[-1], ast.Expr) else None
            with contextlib.redirect_stdout(out):
                exec(compile(tree, "<eval>", "exec"), env)
                if last is not None:
                    value = eval(compile(ast.Expression(last.value), "<eval>", "eval"), env)
        except Exception:  # noqa: BLE001
            return {"error": traceback.format_exc(), "stdout": out.getvalue()}
        self._settle()
        return {"stdout": out.getvalue(), "value": None if value is None else repr(value)[:20000], "new_log": self._new_problems(before)}


# ------------------------------------------------------------------------------------------------------------- helpers
def _tree_paths(t: QTreeWidget) -> list:
    out = []

    def walk(it, prefix):
        p = f"{prefix}/{it.text(0)}" if prefix else it.text(0)
        out.append(p)
        for k in range(it.childCount()):
            walk(it.child(k), p)
    for k in range(t.topLevelItemCount()):
        walk(t.topLevelItem(k), "")
    return out


def _tree_find(t: QTreeWidget, path: str):
    def walk(it, prefix):
        p = f"{prefix}/{it.text(0)}" if prefix else it.text(0)
        if p == path or it.text(0) == path:
            return it
        for k in range(it.childCount()):
            r = walk(it.child(k), p)
            if r is not None:
                return r
        return None
    for k in range(t.topLevelItemCount()):
        r = walk(t.topLevelItem(k), "")
        if r is not None:
            return r
    return None
