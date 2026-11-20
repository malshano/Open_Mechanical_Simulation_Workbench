"""The bottom dock: what the study says about itself (Messages: what is missing, what finished) and the solver's own output (Solver log).
Both are always one glance away, in every step, as in the web version. The two pages are chosen with a row of buttons (ids log.tab.*)."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDockWidget, QHBoxLayout, QPlainTextEdit, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from .. import project as P
from .ids import wid

KINDS = {"prepare": "Geometry", "mesh": "Mesh", "solve": "Solver", "check": "Check"}


class LogDock(QDockWidget):
    def __init__(self, parent=None):
        super().__init__("Output", parent)
        body = QWidget()
        v = QVBoxLayout(body)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.head = QWidget()
        self.head.setObjectName("tabhead")
        self.head.setAttribute(Qt.WA_StyledBackground, True)
        hl = QHBoxLayout(self.head)
        hl.setContentsMargins(6, 4, 6, 0)
        hl.setSpacing(2)
        self.stack = QStackedWidget()
        self.messages = wid(QPlainTextEdit(), "log.messages")
        self.messages.setReadOnly(True)
        self.messages.setPlaceholderText("Messages about the study appear here: what is missing, what has finished.")
        self.messages.setToolTip("Messages about the study")
        self.solver = wid(QPlainTextEdit(), "log.solver")
        self.solver.setReadOnly(True)
        self.solver.setMaximumBlockCount(5000)
        self.solver.setPlaceholderText("The solver's output appears here while it runs.")
        self.solver.setToolTip("The solver's own output")
        self.tab_btns = {}
        for i, (key, label, page) in enumerate((("messages", "Messages", self.messages), ("solver", "Solver log", self.solver))):
            b = wid(QPushButton(label), f"log.tab.{key}")
            b.setObjectName("dtab")
            b.setCheckable(True)
            b.setChecked(i == 0)
            b.setToolTip(f"Show the {label.lower()}")
            b.clicked.connect(lambda _=False, i=i: self.show_tab(i))
            hl.addWidget(b)
            self.tab_btns[key] = b
            self.stack.addWidget(page)
        hl.addStretch(1)
        v.addWidget(self.head)
        v.addWidget(self.stack, 1)
        self.setWidget(body)
        self.checks: list = []
        self.events: list = []

    def show_tab(self, i: int):
        self.stack.setCurrentIndex(i)
        for j, b in enumerate(self.tab_btns.values()):
            b.setChecked(j == i)

    def show_checks(self, checks):
        self.checks = list(checks or [])
        self._render()

    def _render(self):
        lines = []
        for c in self.checks:
            mark = {"error": "✖ Missing", "warning": "⚠ Check"}.get(c.get("level"), "ℹ")
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
