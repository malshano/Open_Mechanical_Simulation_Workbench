"""The bottom dock: what the study says about itself (Messages: what is missing, what finished) and the solver's own output (Solver log).
Both are always one glance away, in every step, as in the web version."""
from __future__ import annotations

import time

from PySide6.QtWidgets import QDockWidget, QPlainTextEdit, QTabWidget

from .. import project as P
from .ids import wid

KINDS = {"prepare": "Geometry", "mesh": "Mesh", "solve": "Solver", "check": "Check"}


class LogDock(QDockWidget):
    def __init__(self, parent=None):
        super().__init__("Messages", parent)
        self.tabs = QTabWidget()
        self.messages = wid(QPlainTextEdit(), "log.messages")
        self.messages.setReadOnly(True)
        self.messages.setPlaceholderText("Messages about the study appear here: what is missing, what has finished.")
        self.messages.setToolTip("Messages about the study")
        self.solver = wid(QPlainTextEdit(), "log.solver")
        self.solver.setReadOnly(True)
        self.solver.setMaximumBlockCount(5000)
        self.solver.setPlaceholderText("The solver's output appears here while it runs.")
        self.solver.setToolTip("The solver's own output")
        self.tabs.addTab(self.messages, "Messages")
        self.tabs.addTab(self.solver, "Solver log")
        self.setWidget(self.tabs)
        self.checks: list = []
        self.events: list = []

    def show_checks(self, checks):
        self.checks = list(checks or [])
        self._render()

    def _render(self):
        lines = []
        for c in self.checks:
            mark = {"error": "✖ Missing", "warning": "⚠ Check"}.get(c.get("level"), "ℹ Note")
            lines.append(f"{mark}  [{str(c.get('step', '')).capitalize()}] {c.get('message', '')}")
        lines += self.events[-40:]
        text = "\n".join(lines)
        if text != self.messages.toPlainText():
            self.messages.setPlainText(text)

    def on_job(self, ev: dict):
        j = ev.get("job") or {}
        if j.get("kind") == "solve" and ev.get("log"):
            self.solver.appendPlainText(str(ev["log"]))
        if ev.get("final"):
            what = KINDS.get(j.get("kind"), j.get("kind", ""))
            self.events.append(f"{time.strftime('%H:%M:%S')}  {what}: {j.get('message') or j.get('status')}")
            self._render()

    def load(self, pid):
        """The output of the latest run of the study that was opened (if this session ran one)."""
        self.solver.clear()
        self.events = []
        j = P.JOBS.latest(pid, "solve") if pid else None
        if j and j.log:
            self.solver.setPlainText("\n".join(str(x) for x in j.log[-1000:]))
