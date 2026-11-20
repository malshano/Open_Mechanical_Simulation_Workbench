"""The solve step panel: which solver runs, run / stop / cancel, progress, the solver's own messages, and the way on to the results."""
from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout
from PySide6.QtCore import Qt

from ... import project as P
from ... import software
from .. import theme
from ..ids import wid
from .common import Section, _h, _muted, _Panel, clear_layout

PLACEHOLDER = "The solver's messages appear here while it runs."


def _item() -> QFrame:
    f = QFrame()
    f.setObjectName("item")
    f.setAttribute(Qt.WA_StyledBackground, True)
    return f


class SolvePanel(_Panel):
    def __init__(self, engine, parent=None):
        super().__init__(engine, parent)
        self._sig = None
        self.lines: list = []
        self.running = False
        self.v.addWidget(_h("Solve"))
        # the solver and the software behind each step (rebuilt when the study changes)
        self.solver_box = QVBoxLayout()
        self.solver_box.setSpacing(6)
        self.v.addLayout(self.solver_box)
        row = QHBoxLayout()
        self.btn = wid(QPushButton("▶  Run the simulation"), "solve.run")
        self.btn.setObjectName("primary")
        self.btn.clicked.connect(self._solve)
        row.addWidget(self.btn, 1)
        self.stop = wid(QPushButton("Stop, keep results"), "solve.stop")
        self.stop.setObjectName("ghostBorder")
        self.stop.setToolTip("Stop the run now and keep the current solution (flow runs)")
        self.stop.clicked.connect(self._stop)
        self.cancel = wid(QPushButton("Cancel"), "solve.cancel")
        self.cancel.setObjectName("ghostBorder")
        self.cancel.setToolTip("Cancel the run (nothing is kept)")
        self.cancel.clicked.connect(self._cancel)
        row.addWidget(self.stop)
        row.addWidget(self.cancel)
        self.v.addLayout(row)
        self.case = wid(QPushButton("Write the OpenFOAM case only"), "solve.case")
        self.case.setObjectName("ghostBorder")
        self.case.setToolTip("Write the solver's case files without running it (to run it yourself)")
        self.case.clicked.connect(self._case_only)
        self.v.addWidget(self.case)
        self.bar = wid(QProgressBar(), "solve.progress")
        self.bar.setRange(0, 1000)
        self.bar.setToolTip("How far the run has got")
        self.v.addWidget(self.bar)
        self.state = wid(_muted(), "solve.state")
        self.v.addWidget(self.state)
        self.v.addWidget(_h("Solver output (latest lines)"))
        self.log = wid(QPlainTextEdit(), "solve.log")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)
        self.log.setPlaceholderText(PLACEHOLDER)
        self.log.setMinimumHeight(120)
        self.log.setToolTip("The solver's own messages")
        self.v.addWidget(self.log, 1)
        self.view_results = wid(QPushButton("View results →"), "solve.results")
        self.view_results.setObjectName("primary")
        self.view_results.clicked.connect(lambda: self.win.go("results"))
        self.v.addWidget(self.view_results)

    # ------------------------------------------------------------------ actions
    def _solve(self):
        try:
            self.engine.solve()
            self.log.clear()
            self.state.setText("Running…")
        except Exception as e:  # noqa: BLE001
            self._fail(e)

    def _case_only(self):
        try:
            self.engine.project().solve(export_only=True)
            self.state.setText("Writing the case files…")
        except Exception as e:  # noqa: BLE001
            self._fail(e)

    def _stop(self):
        try:
            self.engine.project().stop_solve()
            self.state.setText("Stopping: the solver writes its current solution…")
        except Exception as e:  # noqa: BLE001
            self._fail(e)

    def _cancel(self):
        j = P.JOBS.latest(self.engine.pid)
        if j and j.status == "running":
            P.JOBS.cancel(j.id)
            self.state.setText("Cancelling…")

    def on_job(self, ev: dict):
        j = ev.get("job") or {}
        if j.get("kind") != "solve":
            return
        self.bar.setValue(int(float(j.get("progress", 0)) * 1000))
        status = j.get("status", "")
        self.state.setText(f"{status.capitalize()}: {j.get('message', '')}".strip(": "))
        line = ev.get("log")
        if line:
            self.log.appendPlainText(str(line))
        self.running = status == "running"
        self._buttons()

    # ------------------------------------------------------------------ what the panel shows
    def refresh(self, st):
        if not st:
            return
        spec, s = st["study_spec"], st["solve"].get("status", "none")
        sig = repr([st["study"], st.get("settings"), theme.effective()])
        if sig != self._sig:
            self._sig = sig
            self._solver_card(st, spec)
        j = P.JOBS.latest(self.engine.pid, "solve")
        self.running = bool(j and j.status == "running")
        if j and not self.log.toPlainText() and j.log:                     # (the messages of the run that just finished)
            self.log.setPlainText("\n".join(str(x) for x in j.log[-300:]))
        if s == "done":
            self.state.setText("Done. The results are ready.")
            self.bar.setValue(1000)
        self.mesh_ready = st["mesh"].get("status") in ("ready",)
        self.done = s == "done"
        self.flow = str(spec.get("solver", "")).startswith("openfoam")
        self._buttons()

    def _buttons(self):
        can = (getattr(self, "mesh_ready", False) or getattr(self, "done", False)) and not self.running
        self.btn.setEnabled(can)
        self.btn.setToolTip("Run the simulation" if can else
                            ("A run is in progress" if self.running else "Not yet: generate the mesh first (step 3, Mesh)"))
        self.stop.setVisible(self.running and getattr(self, "flow", False))
        self.cancel.setVisible(self.running)
        self.case.setVisible(getattr(self, "flow", False))
        self.case.setEnabled(getattr(self, "mesh_ready", False) and not self.running)
        done = getattr(self, "done", False)
        self.view_results.setEnabled(done)
        self.view_results.setToolTip("Look at the results" if done else "Not yet: run the simulation first")

    def _solver_card(self, st, spec):
        clear_layout(self.solver_box)
        card = _item()
        v = QVBoxLayout(card)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(4)
        try:
            d = software.describe(st["study"], st.get("settings") or {})
        except Exception:  # noqa: BLE001
            d = None
        label = spec.get("solver_label") or spec.get("solver", "")
        lab = QLabel(f"<b>Solver:</b> {label}")
        lab.setWordWrap(True)
        v.addWidget(lab)
        if d:
            solver = next((r for r in d["elements"] if r["element"] == "solver"), None)
            if solver:
                ok = solver.get("installed")
                t = theme.tokens()
                st_lab = QLabel(f"{'✓ Ready' if ok else '✖ Not installed'}: {solver['software']} ({solver['licence']})"
                                + ("" if ok else f" - {solver.get('detail', '')}"))
                st_lab.setWordWrap(True)
                st_lab.setStyleSheet(f"color: {t['ok_text'] if ok else t['bad_text']};")
                v.addWidget(st_lab)
            sec = Section("Software used for each step", "solve.software", expanded=True)
            for r in d["elements"]:
                row = QLabel(f"{r['element'].replace('_', ' ').capitalize()}: <b>{r['software']}</b> · {r['licence']}"
                             + ("" if r.get("installed", True) else " · not installed"))
                row.setWordWrap(True)
                sec.body.addWidget(row)
            v.addWidget(sec)
        self.solver_box.addWidget(card)
