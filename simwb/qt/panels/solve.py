"""The solve step panel, laid out as the web version's Solve step: what stops a run, the solver and its software (found? install it,
re-check), the run options of a flow study (max iterations, CPU cores, start from another study), the expected run time, run / stop /
cancel, carrying a run on (resume, continue, extend a transient run), the OpenFOAM case (write it only, save it as a zip), progress
with the residuals, the advanced OpenFOAM numerics and case files, the solver's own messages, and the way on to the results."""
from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QDoubleValidator, QIntValidator
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QProgressBar, QPushButton,
                               QVBoxLayout, QWidget)

from ... import project as P
from ... import software, studies
from .. import jobs as J
from .. import mode, theme
from ..flow import FlowLayout
from ..forms import ParamForm, fmt_num
from ..ids import wid
from .common import FlowLayout, Section, _h, _muted, _Panel, clear_layout

PLACEHOLDER = "The solver's messages appear here while it runs."


def _item() -> QFrame:
    f = QFrame()
    f.setObjectName("item")
    f.setAttribute(Qt.WA_StyledBackground, True)
    return f


def _btn(text, id_, tip, primary=False):
    b = wid(QPushButton(text), id_)
    b.setObjectName("primary" if primary else "ghostBorder")
    b.setToolTip(tip)
    return b


def _row(*ws, stretch=True):
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(6)
    for x in ws:
        h.addWidget(x)
    if stretch:
        h.addStretch(1)
    return w


class SolvePanel(_Panel):
    def __init__(self, engine, parent=None):
        super().__init__(engine, parent)
        self._sig = self._adv_sig = None
        self.lines: list = []
        self.running = False
        self.flow = self.uses_ccx = False
        self.foam: dict | None = None
        self.ccx: dict | None = None
        self.init_from = ""
        self.case_list: dict = {}
        self.case_open: str | None = None
        self.case_text = None
        self.st: dict = {}
        self.v.addWidget(_h("Solve"))
        self.note = wid(QLabel(""), "solve.note")
        self.note.setWordWrap(True)
        self.v.addWidget(self.note)
        # ---- the solver and its software (added to the panel below the progress, see the end of __init__: Run comes first)
        self.solver_box = QVBoxLayout()
        self.solver_box.setSpacing(6)
        self.foam_status = wid(QLabel(""), "solve.foam.status")
        self.foam_status.setWordWrap(True)
        self.install_foam = _btn("Install OpenFOAM", "solve.install.openfoam",
                                 "Download the official openfoam.com Windows build (~200 MB) into the workbench folder; no admin rights", True)
        self.install_foam.clicked.connect(lambda: self._install("openfoam"))
        self.install_hint = _muted("~200 MB download, no admin rights")
        self.foam_help = Section("Other ways to install", "solve.foam.help", expanded=False)
        self.foam_help_text = wid(_muted(""), "solve.foam.help_text")
        self.foam_help_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.foam_help.body.addWidget(self.foam_help_text)
        self.ccx_status = wid(QLabel(""), "solve.ccx.status")
        self.ccx_status.setWordWrap(True)
        self.install_ccx = _btn("Install CalculiX", "solve.install.calculix",
                                "Download CalculiX (~150 MB from conda-forge) into the workbench folder; no admin rights", True)
        self.install_ccx.clicked.connect(lambda: self._install("calculix"))
        self.recheck_btn = _btn("Re-check", "solve.recheck", "Look for the solver programs again (after installing one yourself)")
        self.recheck_btn.clicked.connect(self.recheck)
        self.inst_bar = wid(QProgressBar(), "solve.install.progress")
        self.inst_bar.setRange(0, 1000)
        self.inst_bar.setToolTip("How far the installation has got")
        self.inst_msg = wid(_muted(""), "solve.install.message")
        self.inst_cancel = _btn("Cancel installation", "solve.install.cancel", "Stop the download and installation")
        self.inst_cancel.clicked.connect(self._install_cancel)
        sw = QWidget()
        swl = QVBoxLayout(sw)
        swl.setContentsMargins(0, 0, 0, 0)
        swl.setSpacing(4)
        self.install_row = _row(self.install_foam, self.install_hint)
        self.ccx_row = _row(self.install_ccx)
        for x in (self.foam_status, self.install_row, self.foam_help, self.ccx_status, self.ccx_row, self.inst_msg, self.inst_bar,
                  _row(self.inst_cancel, self.recheck_btn)):
            swl.addWidget(x)
        self._sw_block = sw
        self._poll = QTimer(self)
        self._poll.setInterval(1500)
        self._poll.timeout.connect(self._install_poll)
        # ---- run options (flow studies)
        self.opts = QWidget()
        ol = QVBoxLayout(self.opts)
        ol.setContentsMargins(0, 0, 0, 0)
        ol.setSpacing(4)
        self.iter_lab = QLabel("Max iterations")
        self.iters = wid(QLineEdit(), "solve.iterations")
        self.iters.setValidator(QIntValidator(50, 100000, self.iters))
        self.iters.setToolTip("Upper limit of solver iterations for a steady run. The run stops earlier once the key results stop changing. "
                              "Raise it, or use Continue, when a run ends not converged; lower it for a quick preview.")
        self.iters.editingFinished.connect(self._iters)
        self.iter_lab.setToolTip(self.iters.toolTip())
        self.iter_row = _row(self.iter_lab, self.iters)
        self.cores = wid(QComboBox(), "solve.cores")
        self.cores.setToolTip("How many processor cores the solver uses. Auto = about 20,000 cells per core, at most the physical cores. Leave it "
                              "on Auto; set fewer to keep the computer responsive. More than the physical cores gives no speed-up.")
        self.cores.activated.connect(self._cores)
        self.cores_lab = QLabel("CPU cores")
        self.cores_lab.setToolTip(self.cores.toolTip())
        self.serial = wid(_muted("CPU cores: 1 (parallel runs are not set up on this computer, so the run uses one core)"), "solve.cores.serial")
        self.cores_row = _row(self.cores_lab, self.cores)
        self.init_lab = QLabel("Start from")
        self.init = wid(QComboBox(), "solve.init_from")
        self.init.setToolTip("Initial flow: uniform, or the latest solution of another study of the same geometry. Choose a converged steady "
                             "(MRF) study when you run a transient sliding-mesh propeller: it then needs one or two revolutions instead of "
                             "several. Leave it on Uniform flow for a first run.")
        self.init_lab.setToolTip(self.init.toolTip())
        self.init.activated.connect(lambda i: setattr(self, "init_from", self.init.itemData(i) or ""))
        self.init_row = _row(self.init_lab, self.init)
        for x in (self.iter_row, self.cores_row, self.serial, self.init_row):
            ol.addWidget(x)
        self.v.addWidget(self.opts)
        self.eta = wid(_muted(""), "solve.eta")
        self.v.addWidget(self.eta)
        # ---- run, stop, cancel
        self.btn = wid(QPushButton("▶  Run the simulation"), "solve.run")
        self.btn.setObjectName("primary")
        self.btn.clicked.connect(self._solve)
        self.stop = _btn("Stop and keep results", "solve.stop", "Write the current solution and show it; the run can be continued later")
        self.stop.clicked.connect(self._stop)
        self.cancel = _btn("Cancel", "solve.cancel", "Kill the solver now; a flow run can be continued later from its last saved iteration")
        self.cancel.clicked.connect(self._cancel)
        row = QHBoxLayout()
        row.addWidget(self.btn, 1)
        row.addWidget(self.stop)
        row.addWidget(self.cancel)
        self.v.addLayout(row)
        # ---- carrying a run on
        self.resume = _btn("Resume", "solve.resume", "Carries on from the last time the solver saved (the steps after it are run again) to the end time")
        self.resume.clicked.connect(lambda: self._carry(resume=True))
        self.resume_row = _row(_muted("Resume the stopped run from its last saved time"), self.resume)
        self.ext_end = wid(QLineEdit(), "solve.extend.end")
        self.ext_end.setValidator(QDoubleValidator(0, 1e9, 9, self.ext_end))
        self.ext_end.setToolTip("The new end time of the run [s]")
        self.ext_wi = wid(QLineEdit(), "solve.extend.interval")
        self.ext_wi.setValidator(QDoubleValidator(0, 1e9, 9, self.ext_wi))
        self.ext_wi.setToolTip("Save the solution every this many seconds [s]: a longer interval keeps the disk use down")
        for e in (self.ext_end, self.ext_wi):
            e.setFixedWidth(90)
        self.extend = _btn("Continue", "solve.extend", "Carries the finished run on from its last saved time; the times saved so far are kept. "
                           "The suggestion is one more turn of the fastest rotating zone.")
        self.extend.clicked.connect(lambda: self._carry(extend=True))
        self.extend_row = QWidget()
        fl = FlowLayout(self.extend_row)
        for x in (QLabel("Continue the run to"), self.ext_end, QLabel("s, saving every"), self.ext_wi, QLabel("s"), self.extend):
            fl.addWidget(x)
        self.cont_n = wid(QLineEdit("1000"), "solve.continue.iterations")
        self.cont_n.setValidator(QIntValidator(1, 100000, self.cont_n))
        self.cont_n.setFixedWidth(80)
        self.cont_n.setToolTip("How many more iterations")
        self.cont = _btn("Continue", "solve.continue", "Carry the last run on from where it stopped for this many more iterations")
        self.cont.clicked.connect(lambda: self._carry())
        self.cont_row = QWidget()
        fl = FlowLayout(self.cont_row)
        for x in (QLabel("Continue the last run for"), self.cont_n, QLabel("more iterations"), self.cont):
            fl.addWidget(x)
        for x in (self.resume_row, self.extend_row, self.cont_row):
            self.v.addWidget(x)
        # ---- the OpenFOAM case
        self.case = _btn("Write OpenFOAM case only", "solve.case", "Write the solver's case files without running it (to run it yourself)")
        self.case.clicked.connect(self._case_only)
        self.zip = _btn("Save case (.zip)…", "solve.case_zip", "Save the OpenFOAM case folder of this study as a zip file")
        self.zip.clicked.connect(self._zip_dialog)
        self.case_row = QWidget()
        _fl = FlowLayout(self.case_row)
        _fl.addWidget(self.case)
        _fl.addWidget(self.zip)
        self.v.addWidget(self.case_row)
        # ---- progress
        self.bar = wid(QProgressBar(), "solve.progress")
        self.bar.setRange(0, 1000)
        self.bar.setToolTip("How far the run has got")
        self.v.addWidget(self.bar)
        self.state = wid(_muted(), "solve.state")
        self.v.addWidget(self.state)
        self.failed = wid(QLabel(""), "solve.failed")
        self.failed.setWordWrap(True)
        self.failed.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.v.addWidget(self.failed)
        self.resid = wid(_muted(""), "solve.residuals")
        self.v.addWidget(self.resid)
        self.chart = wid(J.ResidualChart(), "solve.residuals.chart")
        self.chart.setVisible(False)
        self.v.addWidget(self.chart)
        # ---- topology optimisation: what the run needs, how it is going
        self.topo = False
        self._live_t = 0.0
        self.topnote = wid(QLabel(""), "solve.topopt.preflight")
        self.topnote.setWordWrap(True)
        self.topnote.setTextFormat(Qt.RichText)
        self.v.addWidget(self.topnote)
        self.topinfo = wid(_muted(""), "solve.topopt.live")
        self.topinfo.setWordWrap(True)
        self.v.addWidget(self.topinfo)
        from ..charts import XYChart
        self.topchart = wid(XYChart(), "solve.topopt.chart")
        self.topchart.setMinimumHeight(170)
        self.topchart.setToolTip("How the stiffness kept and the mass change as the optimiser works. The mass settles at your target at once; the stiffness kept "
                                 "rises as the shape finds its load paths. 'Sharpness' is the share of the material that is solid or empty (not grey).")
        self.topchart.setVisible(False)
        self.v.addWidget(self.topchart)
        self.v.addWidget(_h("Solver"))                       # (below the run, its progress and its state: the run leads the step)
        self.v.addLayout(self.solver_box)
        self.v.addWidget(self._sw_block)
        # ---- advanced OpenFOAM: numerics and case files
        self.adv = Section("Advanced (OpenFOAM)", "solve.advanced", expanded=False)
        self.adv.body.addWidget(_muted("For experienced users. The defaults are chosen for each study; change these only when a run will not "
                                       "converge or you need a specific method. 0 / default = keep the study's value."))
        self.adv_box = QVBoxLayout()
        self.adv.body.addLayout(self.adv_box)
        self.v.addWidget(self.adv)
        # ---- the solver's messages and the way on
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
    def _no_foam(self) -> bool:
        return self.flow and self.foam is not None and not self.foam.get("available") and not self.foam.get("install_job")

    def _solve(self):
        try:
            if self._no_foam():
                return self._case_only()                 # (no OpenFOAM: the button writes the case for the user to run)
            if self.init_from:
                self.engine.project().solve(init_from=self.init_from)
            else:
                self.engine.solve()
            self.log.clear()
            self.state.setText("Running…")
            self.engine.changed.emit()
        except Exception as e:  # noqa: BLE001
            self._fail(e)

    def _case_only(self):
        try:
            self.engine.project().solve(export_only=True)
            self.state.setText("Writing the OpenFOAM case…")
            self.engine.changed.emit()
        except Exception as e:  # noqa: BLE001
            self._fail(e)

    def _carry(self, resume=False, extend=False):
        """Resume a stopped transient run, extend a finished one to a later end time, or continue a steady run by iterations."""
        try:
            pr = self.engine.project()
            if extend:
                pr.solve(end_time=float(self.ext_end.text()), write_interval=float(self.ext_wi.text()) if self.ext_wi.text().strip() else None)
            elif resume:
                pr.solve(continue_iterations=1)              # (a transient run resumes to its end time; the number is not used)
            else:
                pr.solve(continue_iterations=int(self.cont_n.text() or 1000))
            self.log.clear()
            self.state.setText("Running…")
            self.engine.changed.emit()
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

    def _iters(self):
        try:
            if not self.iters.text().strip():
                return
            n = int(self.iters.text())
            if n != int((self.engine.state()["settings"] or {}).get("iterations") or 0):
                self.engine.project().set_settings(iterations=n)
                self.engine.changed.emit()
        except Exception as e:  # noqa: BLE001
            self._fail(e)

    def _cores(self, i):
        try:
            self.engine.project().set_settings(n_procs=int(self.cores.itemData(i) or 0))
            self.engine.changed.emit()
        except Exception as e:  # noqa: BLE001
            self._fail(e)

    def save_case_zip(self, path: str) -> Path:
        out = J.case_zip(self.engine.project(), path)
        self.engine.message.emit(f"Case saved: {out}", False)
        return out

    def _zip_dialog(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save the OpenFOAM case", f"{self.engine.pid}-openfoam-case.zip", "Zip files (*.zip)")
        if path:
            try:
                self.save_case_zip(path)
            except Exception as e:  # noqa: BLE001
                self._fail(e)

    # ---- solver software
    def recheck(self):
        try:
            if self.flow:
                self.foam = J.foam_status(refresh=True)
            if self.uses_ccx:
                self.ccx = J.ccx_status(refresh=True)
        except Exception as e:  # noqa: BLE001
            self._fail(e)
        if self.engine.pid:
            self.refresh(self.engine.state())

    def _install(self, name):
        try:
            J.install(name)
        except Exception as e:  # noqa: BLE001
            self._fail(e)
            return
        self.engine.message.emit(f"Installing {'OpenFOAM' if name == 'openfoam' else 'CalculiX'}…", False)
        self._install_poll()
        if self._install_job()[1] is not None:
            self._poll.start()

    def _install_job(self):
        for j in P.JOBS.jobs.values():
            if j.project in ("_openfoam", "_calculix") and j.status == "running":
                return ("openfoam" if j.project == "_openfoam" else "calculix"), j
        return None, None

    def _install_poll(self):
        name, j = self._install_job()
        if j is None:
            if self._poll.isActive():                  # (it finished since the last look)
                self._poll.stop()
                self.engine.message.emit("Installation finished: the solver programs were looked for again", False)
                self.foam = self.ccx = None
                if self.engine.pid:
                    self.refresh(self.engine.state())
            return
        self.inst_bar.setValue(int(j.progress * 1000))
        self.inst_msg.setText(f"Installing {'OpenFOAM' if name == 'openfoam' else 'CalculiX'}… {j.message}")
        self._software()

    def _install_cancel(self):
        _n, j = self._install_job()
        if j is not None:
            P.JOBS.cancel(j.id)
            self.inst_msg.setText("Cancelling the installation…")

    # ---- advanced OpenFOAM
    def _adv_set(self, key, val):
        try:
            self.engine.project().set_foam_advanced(**{key: val})
        except Exception as e:  # noqa: BLE001
            self._fail(e)
            self._adv_sig = None
        self.engine.changed.emit()

    def _adv_reset(self):
        try:
            self.engine.project().set_foam_advanced(**{p["key"]: p["default"] for p in studies.FOAM_ADVANCED})
            self._adv_sig = None
            self.engine.changed.emit()
        except Exception as e:  # noqa: BLE001
            self._fail(e)

    def _case_files(self):
        try:
            self.case_list = self.engine.project().foam_preview()
            self.engine.message.emit(f"{len(self.case_list.get('files') or [])} case files written (a preview of the next run)", False)
        except Exception as e:  # noqa: BLE001
            self._fail(e)
        self._adv_sig = None
        self.refresh(self.engine.state())

    def open_case_file(self, path: str):
        """Show a case file in the editor under the list."""
        self.engine.project().case_file(path)           # (checks the path)
        self.case_open = path
        self._adv_sig = None
        self.refresh(self.engine.state())

    def _case_save(self):
        try:
            self.engine.project().set_case_file(self.case_open, self.case_text.toPlainText())
            self.engine.message.emit("Saved: used on the next run", False)
            self._adv_sig = None
            self.engine.changed.emit()
        except Exception as e:  # noqa: BLE001
            self._fail(e)

    def _case_revert(self):
        try:
            pr = self.engine.project()
            pr.set_case_file(self.case_open, None)
            try:
                self.case_list = pr.foam_preview()
            except Exception:  # noqa: BLE001
                pass
            self.engine.message.emit("Reverted to the generated file", False)
            self._adv_sig = None
            self.engine.changed.emit()
        except Exception as e:  # noqa: BLE001
            self._fail(e)

    def _case_close(self):
        self.case_open = None
        self._adv_sig = None
        self.refresh(self.engine.state())

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
        job = P.JOBS.jobs.get(j.get("id"))
        res = (job.extra or {}).get("residuals") if job is not None else None
        if res:
            self.resid.setText(J.latest_residuals(res))
            self.chart.set_data(res)
        self.running = status == "running"
        if self.topo:
            self._topo_live(job)
        self._buttons()

    # ------------------------------------------------------------------ what the panel shows
    def refresh(self, st):
        if not st:
            return
        spec, s = st["study_spec"], st["solve"].get("status", "none")
        self.flow = str(spec.get("solver", "")).startswith("openfoam")
        self.uses_ccx = J.uses_calculix(st)
        if self.engine.pid != getattr(self, "_pid", None):                 # another study: nothing of the previous one stays on show
            self._pid = self.engine.pid
            self.log.clear()
            self.bar.setValue(0)
            self.state.setText("")
            self.resid.setText("")
            self.chart.set_data(None)
            self.case_list, self.case_open, self.init_from, self._adv_sig = {}, None, "", None
        if self.flow and self.foam is None:
            try:
                self.foam = J.foam_status()
            except Exception:  # noqa: BLE001
                self.foam = {"available": False, "detail": "it could not be checked"}
        if self.uses_ccx and self.ccx is None:
            try:
                self.ccx = J.ccx_status()
            except Exception:  # noqa: BLE001
                self.ccx = {"available": False}
        if self.flow and not self.case_list:
            try:
                self.case_list = self.engine.project().case_files()
            except Exception:  # noqa: BLE001
                self.case_list = {}
        sig = repr([st["study"], st.get("settings"), theme.effective()])
        if sig != self._sig:
            self._sig = sig
            self._solver_card(st, spec)
        j = P.JOBS.latest(self.engine.pid, "solve")
        self.running = bool(j and j.status == "running")
        if j and not self.log.toPlainText() and j.log:                     # (the messages of the run that just finished)
            self.log.setPlainText("\n".join(str(x) for x in j.log[-300:]))
        if j and (j.extra or {}).get("residuals") and not self.chart.res:
            self.chart.set_data(j.extra["residuals"])
            self.resid.setText(J.latest_residuals(j.extra["residuals"]))
        if s == "done":
            self.state.setText("Done. The results are ready.")
            self.bar.setValue(1000)
        elif not self.running:                                             # (this study's own state, not a finished run's)
            self.bar.setValue(0)
            self.state.setText({"interrupted": "The last run was interrupted.", "failed": "The last run failed: see the output below.",
                                "stale": "The setup changed since the last run: run it again."}.get(s, ""))
        t = theme.tokens()
        self.failed.setVisible(s == "failed")
        self.failed.setText(("The last run failed:\n" + (st["solve"].get("error_tail") or "")) if s == "failed" else "")
        self.failed.setStyleSheet(f"color: {t['bad_text']};")
        self.mesh_ready = st["mesh"].get("status") in ("ready",)
        self.blockers = [c["message"] for c in (st.get("checks") or []) if c.get("level") == "error"]
        self.done = s == "done"
        self.st = st
        self.topo = st["study"] == "topology_optimization"
        if self.topo:
            self._topo_refresh(st)
        else:
            self.topchart.setVisible(False)
            self.topnote.setVisible(False)
            self.topinfo.setVisible(False)
        self._options(st)
        self._software()
        self._advanced(st)
        self._buttons()

    # ------------------------------------------------------------------ topology optimisation
    def _topo_series(self, hist, c_full):
        its = [h["iteration"] for h in hist]
        kept = [100.0 * c_full / h["compliance"] if c_full and h["compliance"] > 0 else float("nan") for h in hist]
        return [{"name": "Stiffness kept (%)", "x": its, "y": kept},
                {"name": "Mass (%)", "x": its, "y": [100.0 * h["mass_fraction"] for h in hist]},
                {"name": "Sharpness (%)", "x": its, "y": [100.0 * (1.0 - h["grey"]) for h in hist]}]

    def _topo_live(self, job):
        """A progress event of the optimisation: the line, the chart, and (about once a second) the shape in the 3D view."""
        import time
        live = (job.extra or {}).get("topopt") if job is not None else None
        if not live or not live.get("history"):
            return
        h = live["history"]
        kept = 100.0 * live["c_full"] / h[-1]["compliance"] if live.get("c_full") and h[-1]["compliance"] > 0 else None
        self.topinfo.setText(f"The shape is {100 * (1 - h[-1]['grey']):.0f} % sharp (solid or empty) and gets sharper as it settles. Stop keeps the shape as it is now.")
        self.topinfo.setVisible(True)
        self.topchart.set_data(self._topo_series(h, live.get("c_full")), xunit="iteration", yunit="%", ylim=(0, 105), legend=True)
        self.topchart.setVisible(True)
        now = time.monotonic()
        if self.win is not None and self.win.step == "solve" and now - self._live_t > 1.2:
            self._live_t = now
            self.win.update_view()

    def _topo_refresh(self, st):
        """What the optimisation will need (checked on the mesh before it starts) and the history of the last run."""
        pf = self.engine.project().topopt_preflight()
        t = theme.tokens()
        if pf.get("ok") is False:
            self.topnote.setText("<b>The optimisation cannot start:</b> " + pf["message"])
            self.topnote.setStyleSheet(f"color: {t['bad_text']};")
            self.blockers = list(getattr(self, "blockers", [])) + [pf["message"]]
        elif pf.get("ok"):
            e = pf["estimate"]
            lines = [f"Ready: {e['elements']:,} elements, smallest feature {pf['radius_mm']:.1f} mm ({pf['radius_mm'] / pf['element_mm']:.1f} element sizes), "
                     f"needs about {e['memory_bytes'] / 2 ** 30:.1f} GB of memory; {100 * pf['kept_solid_share']:.0f} % of the mass is kept solid round supports and loads."]
            lines += [n for n in pf["notes"]]
            self.topnote.setText("<br>".join(lines))
            self.topnote.setStyleSheet(f"color: {t['muted']};")
        else:
            self.topnote.setText(pf.get("message", ""))
            self.topnote.setStyleSheet(f"color: {t['muted']};")
        self.topnote.setVisible(bool(self.topnote.text()))
        if not self.running:
            top = (st["solve"].get("summary") or {}).get("topopt")
            live = self.engine.project().topopt_live()
            hist = (live or {}).get("history") or (top or {}).get("history")
            c_full = (live or {}).get("c_full") or (top or {}).get("compliance_solid")
            if hist:
                self.topchart.set_data(self._topo_series(hist, c_full), xunit="iteration", yunit="%", ylim=(0, 105), legend=True)
            self.topchart.setVisible(bool(hist))
            self.topinfo.setVisible(False)

    def _options(self, st):
        """Max iterations, cores and the study to start from (flow studies), and the expected run time."""
        se = st.get("settings") or {}
        self.opts.setVisible(self.flow)
        if not self.flow:
            self.eta.setText("")
            self.eta.setVisible(False)
            return
        self.iter_row.setVisible(se.get("analysis", "steady") != "transient")
        if not self.iters.hasFocus():
            self.iters.setText(str(int(se["iterations"])) if se.get("iterations") else "")
        serial = J.serial_only(self.foam)
        self.cores_row.setVisible(not serial)
        self.serial.setVisible(serial)
        self.cores.clear()
        self.cores.addItem(f"Auto ({J.cores_for(st, self.foam, 0)})", 0)
        for n in range(1, J.max_cores(st, self.foam) + 1):
            self.cores.addItem(str(n), n)
        self.cores.setCurrentIndex(max(0, self.cores.findData(int(se.get("n_procs") or 0))))
        srcs = [q for q in P.list_projects() if q["id"] != st["id"] and q.get("study") == st["study"]] if st["study"] == "external_aero" else []
        self.init_row.setVisible(bool(srcs) and not self.running)
        self.init.clear()
        self.init.addItem("Uniform flow", "")
        for q in srcs:
            self.init.addItem(q["name"], q["id"])
        self.init.setCurrentIndex(max(0, self.init.findData(self.init_from)))
        eta = J.run_time_estimate(st, self.foam) if self.mesh_ready else None
        self.eta.setVisible(bool(eta) and not self.running)
        self.eta.setText(f"Expected run time ≈ <b>{eta}</b>" if eta else "")

    def _software(self):
        t = theme.tokens()
        f = self.foam or {}
        name, ij = self._install_job()
        installing = ij is not None
        self.foam_status.setVisible(self.flow)
        if self.flow:
            if f.get("available"):
                self.foam_status.setText(f"✓ OpenFOAM found via <b>{f.get('backend')}</b> · {f.get('detail', '')}")
                self.foam_status.setStyleSheet(f"color: {t['ok_text']};")
            elif installing and name == "openfoam":
                self.foam_status.setText("Installing OpenFOAM…")
                self.foam_status.setStyleSheet(f"color: {t['warn_text']};")
            else:
                self.foam_status.setText("✖ OpenFOAM not found" + (f": {f['detail']}" if f.get("detail") else "") +
                                         ". Install it, or write the case and run it yourself.")
                self.foam_status.setStyleSheet(f"color: {t['bad_text']};")
        no_foam = self.flow and not f.get("available") and not installing
        self.install_row.setVisible(no_foam and bool(f.get("installable")))
        self.foam_help.setVisible(no_foam)
        self.foam_help_text.setText(f.get("help") or "")
        c = self.ccx or {}
        self.ccx_status.setVisible(self.uses_ccx)
        if self.uses_ccx:
            if c.get("available"):
                self.ccx_status.setText(f"✓ CalculiX {c.get('version') or ''} found ({c.get('source') or ''})")
                self.ccx_status.setStyleSheet(f"color: {t['ok_text']};")
            else:
                self.ccx_status.setText("✖ CalculiX is not installed. " + (c.get("help") or ""))
                self.ccx_status.setStyleSheet(f"color: {t['bad_text']};")
        self.ccx_row.setVisible(self.uses_ccx and not c.get("available") and bool(c.get("installable")) and not installing)
        self.recheck_btn.setVisible((no_foam or (self.uses_ccx and not c.get("available"))) and not installing)
        for w in (self.inst_bar, self.inst_msg, self.inst_cancel):
            w.setVisible(installing)
        if installing and not self._poll.isActive():
            self._poll.start()

    def _advanced(self, st):
        self.adv.setVisible(self.flow and not mode.simple())
        if not self.flow:
            return
        adv, ov = st.get("foam_advanced") or {}, sorted(st.get("foam_overrides") or {})
        sig = repr([adv, ov, self.case_list, self.case_open, st["mesh"].get("status"), self.running, theme.effective()])
        if sig == self._adv_sig:
            return
        self._adv_sig = sig
        n = len(adv)
        self.adv.title = "Advanced (OpenFOAM) · " + (f"{n} setting{'s' if n > 1 else ''} changed" if n else "defaults") + \
            (f" · {len(ov)} edited file{'s' if len(ov) > 1 else ''}" if ov else "")
        self.adv._toggled(self.adv.head.isChecked())
        clear_layout(self.adv_box)
        self.case_text = None
        vals = {p["key"]: adv.get(p["key"], p["default"]) for p in studies.FOAM_ADVANCED}
        form = ParamForm(studies.FOAM_ADVANCED, vals, "solve.adv")
        form.changed.connect(self._adv_set)
        self.adv_box.addWidget(form)
        reset = _btn("Reset all to defaults", "solve.adv.reset", "Put every advanced setting back to the study's default")
        reset.setEnabled(bool(n))
        if not n:
            reset.setToolTip("Every advanced setting is already the study's default")
        reset.clicked.connect(self._adv_reset)
        self.adv_box.addWidget(_row(reset))
        self.adv_box.addWidget(_h("Case files"))
        self.adv_box.addWidget(_muted("Every OpenFOAM dictionary of this case. Open one to read or edit it: your version replaces the generated "
                                      "file on every run (until you revert it), so any OpenFOAM feature can be used."))
        files = (self.case_list or {}).get("files") or []
        meshed = st["mesh"].get("status") in ("ready", "stale")
        show = _btn("↻ Refresh case files" if files else "Show case files", "solve.casefiles.show",
                    "Write the case files as the next run would (a preview; the real case is untouched) and list them" if meshed
                    else "Not yet: generate the mesh first (the case files are written for the mesh)")
        show.setEnabled(meshed and not self.running)
        if self.running:
            show.setToolTip("Not while a run is in progress")
        show.clicked.connect(self._case_files)
        self.adv_box.addWidget(_row(show))
        groups: dict = {}
        for f in files + [x for x in ov if x not in files]:
            groups.setdefault(f.split("/")[0], []).append(f)
        for g, fs in groups.items():
            self.adv_box.addWidget(_muted(f"{g}/"))
            wrap = QWidget()
            lay = FlowLayout(wrap)
            for f in fs:
                b = wid(QPushButton(f[len(g) + 1:] + (" ✎" if f in ov else "")), "solve.casefile." + re.sub(r"\W+", "_", f))
                b.setObjectName("ghostBorder")
                b.setToolTip(f"Open {f}" + (" (your edited version)" if f in ov else ""))
                b.clicked.connect(lambda _=False, f=f: self.open_case_file(f))
                lay.addWidget(b)
            self.adv_box.addWidget(wrap)
        if self.case_open:
            d = self.engine.project().case_file(self.case_open)
            card = _item()
            cv = QVBoxLayout(card)
            cv.setContentsMargins(8, 6, 8, 6)
            cv.addWidget(wid(QLabel(f"<b>{d['path']}</b>" + (" (edited)" if d["overridden"] else "")), "solve.casefile.path"))
            cv.addWidget(_muted("Saved edits replace the generated file before every run; settings in the panels that write this file then no "
                                "longer reach it until you revert."))
            self.case_text = wid(QPlainTextEdit(d["text"]), "solve.casefile.text")
            self.case_text.setMinimumHeight(220)
            self.case_text.setToolTip("The OpenFOAM dictionary: edit it here, then Save")
            cv.addWidget(self.case_text)
            save = _btn("Save", "solve.casefile.save", "Keep this version: it replaces the generated file on every run", True)
            save.clicked.connect(self._case_save)
            rev = _btn("Revert to generated", "solve.casefile.revert", "Forget your version: the generated file is used again")
            rev.setEnabled(d["overridden"])
            if not d["overridden"]:
                rev.setToolTip("This file is the generated one: there is nothing to revert")
            rev.clicked.connect(self._case_revert)
            close = _btn("Close", "solve.casefile.close", "Close the editor (unsaved changes are dropped)")
            close.clicked.connect(self._case_close)
            cv.addWidget(_row(save, rev, close))
            self.adv_box.addWidget(card)

    def _buttons(self):
        blockers = getattr(self, "blockers", [])
        meshed = getattr(self, "mesh_ready", False) or getattr(self, "done", False)
        no_foam = self._no_foam()
        can = meshed and not self.running and not blockers
        self.btn.setEnabled(can)
        if can:
            why = ("Write the OpenFOAM case files (OpenFOAM is not installed: run them yourself, or install it above)" if no_foam
                   else "Run the simulation")
        elif self.running:
            why = "A run is in progress"
        elif not meshed:
            why = "Not yet: generate the mesh first (step 3, Mesh)"
        else:
            why = "Not yet - the setup is not complete: " + "; ".join(blockers[:3]) + (" ..." if len(blockers) > 3 else "")
        self.btn.setToolTip(why)
        t = theme.tokens()
        st = self.st
        notes = []
        if st:
            mstat = st["mesh"].get("status")
            if mstat != "ready":
                notes.append("The mesh is out of date: regenerate it (step 3, Mesh)." if mstat == "stale" else "Generate a mesh first (step 3, Mesh).")
            if blockers:
                notes.append("Fix before solving:<br>" + "<br>".join("• " + b for b in blockers))
            if st["solve"].get("status") == "interrupted" and not self.running:
                notes.append("The last run was stopped before it finished. Continue it below, or run again.")
        if blockers and meshed and not self.running and not getattr(self, "done", False):
            self.state.setText("Before it can run: " + blockers[0] + (f" (and {len(blockers) - 1} more: see above)" if len(blockers) > 1 else ""))
        self.note.setText("<br>".join(notes))
        self.note.setVisible(bool(notes))
        self.note.setStyleSheet(f"color: {t['warn_text']};")
        again = getattr(self, "done", False) and not self.running
        self.btn.setText("Running…" if self.running else "Write OpenFOAM case" if no_foam
                         else "↻  Run the simulation again" if again else "▶  Run the simulation")
        self.btn.setObjectName("ghostBorder" if again else "primary")              # (View results is then the one main button)
        self.btn.style().unpolish(self.btn)
        self.btn.style().polish(self.btn)
        self.view_results.setObjectName("primary")
        self.stop.setVisible(self.running and (self.flow or self.topo))
        if self.topo:
            self.btn.setText("Working…" if self.running else "↻  Find the shape again" if again else "▶  Find the shape")
            self.stop.setText("Stop and keep this shape")
            self.stop.setToolTip("Stop now and keep the shape found so far. It is usable: the optimiser improves it step by step")
            self.cancel.setToolTip("Stop and throw this run away")
        self.cancel.setVisible(self.running)
        self.case_row.setVisible(self.flow)
        self.case.setEnabled(getattr(self, "mesh_ready", False) and not self.running and not blockers)
        self.case.setToolTip("Write the solver's case files without running it (to run it yourself)" if self.case.isEnabled()
                             else "A run is in progress" if self.running else "Not yet: generate the mesh and complete the setup first")
        case_dir = P.open_project(self.engine.pid).case_dir if (self.engine.pid and st and self.flow) else None
        has_case = bool(case_dir is not None and case_dir.exists())
        self.zip.setEnabled(has_case and not self.running)
        self.zip.setToolTip("Save the OpenFOAM case folder of this study as a zip file" if self.zip.isEnabled()
                            else "A run is in progress" if self.running else "Not yet: no OpenFOAM case is written (run, or write the case only)")
        co = J.carry_on(st, self.foam, self.running, case_dir) if st else {"kind": None}
        self.resume_row.setVisible(co["kind"] == "resume")
        self.extend_row.setVisible(co["kind"] == "extend")
        self.cont_row.setVisible(co["kind"] == "iterations")
        if co["kind"] == "extend" and getattr(self, "_ext_for", None) != (self.engine.pid, co["end"]):
            self._ext_for = (self.engine.pid, co["end"])
            self.ext_end.setText(fmt_num(co["end"]))
            self.ext_wi.setText(fmt_num(co["interval"]))
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
        label = ("CalculiX (separate open-source program), quadratic tetrahedra" if J.uses_calculix(st)
                 else spec.get("solver_label") or spec.get("solver", ""))
        lab = wid(QLabel(f"<b>Solver:</b> {label}"), "solve.solver")
        lab.setWordWrap(True)
        lab.setStyleSheet(f"color: {theme.tokens()['ink']}; background: {theme.tokens()['panel2']};")
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
            sec = Section("Software used for each step", "solve.software", expanded=False)
            for r in d["elements"]:
                row = QLabel(f"{r['element'].replace('_', ' ').capitalize()}: <b>{r['software']}</b> · {r['licence']}"
                             + ("" if r.get("installed", True) else " · not installed")
                             + (f" · ✓ {r['version']}" if r.get("kind") == "program" and r.get("installed") and r.get("version") else ""))
                row.setWordWrap(True)
                sec.body.addWidget(row)
            v.addWidget(sec)
        self.solver_box.addWidget(card)
