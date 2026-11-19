"""The solve step panel."""
from __future__ import annotations

from PySide6.QtWidgets import QPlainTextEdit, QProgressBar, QPushButton
from ..ids import wid
from .common import _h, _muted, _Panel


# ---------------------------------------------------------------------------------------------------------- solve
class SolvePanel(_Panel):
    def __init__(self, engine, parent=None):
        super().__init__(engine, parent)
        self.v.addWidget(_h("Solve"))
        self.btn = wid(QPushButton("Run the simulation"), "solve.run")
        self.btn.setObjectName("primary")
        self.btn.clicked.connect(self._solve)
        self.v.addWidget(self.btn)
        self.bar = wid(QProgressBar(), "solve.progress")
        self.bar.setRange(0, 1000)
        self.v.addWidget(self.bar)
        self.state = wid(_muted(), "solve.state")
        self.v.addWidget(self.state)
        self.log = wid(QPlainTextEdit(), "solve.log")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)
        self.v.addWidget(self.log, 1)

    def _solve(self):
        try:
            self.engine.solve()
            self.state.setText("Running…")
        except Exception as e:  # noqa: BLE001
            self._fail(e)

    def on_job(self, ev: dict):
        j = ev.get("job") or {}
        if j.get("kind") != "solve":
            return
        self.bar.setValue(int(float(j.get("progress", 0)) * 1000))
        self.state.setText(f"{j.get('status', '')}: {j.get('message', '')}")
        for line in ev.get("log") or []:
            self.log.appendPlainText(str(line))

    def refresh(self, st):
        if not st:
            return
        s = st["solve"].get("status", "none")
        self.btn.setEnabled(st["mesh"].get("status") in ("ready",) or s == "done")
        if s == "done":
            self.state.setText("Done. Open the Results step.")
            self.bar.setValue(1000)
