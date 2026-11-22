"""The parts of the Solve step that are not its layout: the solver software (OpenFOAM, CalculiX: found? install it, re-check), the CPU
cores and the expected run time, which way a finished or stopped run can be carried on (resume, continue by iterations, extend a
transient run to a later end time), the case zip, the residual chart, and the control-API actions of the step.

The functions at the top are plain Python (no Qt) and use the same engine calls as the web version's REST endpoints."""
from __future__ import annotations

import math
import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .. import project as P

RUN_COST = {"cht_enclosure": (5.1e-6, 1700), "external_aero": (6.2e-6, 500)}   # s per cell per iteration (serial), iterations to auto-stop


# ---------------------------------------------------------------------------------------------------------------- solver software
def foam_status(refresh: bool = False) -> dict:
    """OpenFOAM: {available, backend, detail, parallel, cores, installable, help, install_job} (as GET /api/openfoam)."""
    from ..solvers.openfoam import installer, runner

    j = installer.running_job(P.JOBS)
    if j:                                       # (do not probe a tree the installer is still writing)
        return {"available": False, "backend": None, "detail": "OpenFOAM is being installed", "installable": True,
                "install_job": j.to_dict(log_tail=0)}
    return runner.detect(refresh=refresh)


def ccx_status(refresh: bool = False) -> dict:
    """CalculiX: {available, exe, version, source, installable, help, install_job} (as GET /api/calculix)."""
    from ..solvers.calculix import installer

    info = dict(installer.detect(refresh=refresh))
    j = installer.running_job(P.JOBS)
    if j:
        info["install_job"] = j.to_dict(log_tail=0)
    return info


def install(name: str):
    """Start the built-in installation of 'openfoam' or 'calculix' (a job of its own, not of the study)."""
    if name == "openfoam":
        from ..solvers.openfoam import installer

        if not installer.supported():
            raise RuntimeError("The built-in OpenFOAM installation is available on Windows only.")
    elif name == "calculix":
        from ..solvers.calculix import installer

        if not installer.supported():
            raise RuntimeError("The built-in CalculiX setup is for Windows; install CalculiX with your package manager (it is found on "
                               "the PATH) or set SIMWB_CCX.")
    else:
        raise ValueError("Install 'openfoam' or 'calculix'")
    return installer.start(P.JOBS)


def uses_calculix(st: dict) -> bool:
    return st["study"] in ("vibration_modal", "static_structural") and str((st.get("settings") or {}).get("structural_solver") or "").startswith("calculix")


# ---------------------------------------------------------------------------------------------------------------- cores and run time
def serial_only(foam: dict | None) -> bool:
    return bool(foam) and foam.get("backend") == "windows" and not foam.get("parallel")


def cores_for(st: dict, foam: dict | None, n: int | None = None) -> int:
    """The cores a CFD run uses: n, or 'Auto' = about 20,000 cells per core, at most the physical cores (runner.auto_procs)."""
    if serial_only(foam):
        return 1
    n = int((st.get("settings") or {}).get("n_procs") or 0) if n is None else int(n)
    cells = (st["mesh"].get("stats") or {}).get("cells") or 0
    return n or max(1, min(int((foam or {}).get("cores") or 1), cells // 20000))


def max_cores(st: dict, foam: dict | None) -> int:
    return max(int((foam or {}).get("cores") or 8), int((st.get("settings") or {}).get("n_procs") or 0))


def _txt(m: float, fine=False) -> str:
    if fine:
        return f"{max(1, round(m))} min" if m < 90 else f"{m / 60:.2g} h"
    return f"{max(1, round(m))} min" if m < 10 else f"{round(m / 5) * 5} min" if m < 90 else f"{m / 60:.2g} h"


def run_time_estimate(st: dict, foam: dict | None) -> str | None:
    """The expected run time of a CFD run in words (from reference runs), or None when there is no estimate."""
    c, cells = RUN_COST.get(st["study"]), (st["mesh"].get("stats") or {}).get("cells")
    if not c or not cells:
        return None
    s = st.get("settings") or {}
    n = min(c[1], int(s.get("iterations") or c[1]))
    np_ = cores_for(st, foam)
    if s.get("analysis") == "transient":
        inl = next((b.get("params", {}).get("velocity") for b in st.get("bcs") or [] if b.get("type") == "velocity_inlet"), None)
        U = max(float((s.get("speed") or 5) if st["study"] == "external_aero" else (inl or 1)), 0.1)
        h = (st["mesh"].get("stats") or {}).get("sizes", {}).get("near_wall") or 1e-3
        sl = [z for z in st.get("moving_zones") or [] if z.get("method") == "sliding" and z.get("rpm")]
        w = max([abs(z["rpm"]) * math.pi / 30 for z in sl] + [0])
        end = float(s.get("end_time") or 0)
        steps = end / float(s["time_step"]) if float(s.get("time_step") or 0) > 0 else end * w / (0.5 * math.pi / 180) if w > 0 else end / (h / U)
        outer = float((st.get("foam_advanced") or {}).get("n_outer") or (5 if w > 0 else 1))
        if w > 0:
            m = steps * 2.2e-5 * cells * outer / max(np_, 1) / 60
            return (f"{_txt(m, True)} ({round(steps):,} time steps of 0.5° rotation, {outer:g} outer correctors each; plus splitting "
                    "and joining)")
        m = cells * steps * 2 * c[0] / 60 / (np_ * 0.6 if np_ > 1 else 1)
        return f"{_txt(m)} (about {round(steps):,} time steps - a rough guess)"
    m = cells * n * c[0] / 60 / (np_ * 0.6 if np_ > 1 else 1)          # (about 60 % parallel efficiency: memory-bound)
    if st["study"] == "external_aero" and (s.get("auto_trim") or "on") == "on" and st.get("rotors"):
        return f"{_txt(m)} (up to {_txt(2 * m)} if auto trim re-runs)"
    return _txt(m)


# ---------------------------------------------------------------------------------------------------------------- carrying a run on
def carry_on(st: dict, foam: dict | None, running: bool, case_dir: Path | None = None) -> dict:
    """How the last OpenFOAM run can be carried on now: {kind: None|'resume'|'extend'|'iterations', end, interval, why}."""
    sv, s = st["solve"], st.get("settings") or {}
    flow = str(st["study_spec"].get("solver", "")).startswith("openfoam")
    out = {"kind": None, "end": None, "interval": None}
    if not flow or running or not sv.get("case_dir") or sv.get("setup_changed") or not (foam or {}).get("available"):
        return out
    status = sv.get("status")
    if s.get("analysis") == "transient":
        if status in ("failed", "interrupted"):
            out["kind"] = "resume"
        elif status == "done":
            end = float(s.get("end_time") or 0)
            last = end
            if case_dir is not None:
                try:
                    from ..solvers.openfoam import runner

                    last = runner.saved_transient_time(case_dir) or end
                except Exception:  # noqa: BLE001
                    pass
            rpm = max([abs(z["rpm"]) for z in st.get("moving_zones") or [] if z.get("method") == "sliding" and z.get("rpm")] + [0])
            deg = float(s.get("frame_deg") or 0)
            out.update(kind="extend", end=float(f"{(last + 60 / rpm) if rpm else end * 2:.6g}"),
                       interval=float(f"{deg / (6 * rpm) if rpm and deg > 0 else end / max(int(s.get('n_frames') or 21) - 1, 1):.4g}"))
    elif status in ("done", "failed", "interrupted"):
        out["kind"] = "iterations"
    return out


def case_zip(pr, path: str) -> Path:
    """The OpenFOAM case folder of the study as a zip file at path (as GET /api/projects/{id}/case.zip)."""
    if not pr.case_dir.exists():
        raise ValueError("No OpenFOAM case written yet: run the simulation or write the case first")
    dest = Path(path)
    if dest.suffix.lower() != ".zip":
        dest = dest.with_suffix(".zip")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp())
    try:
        made = shutil.make_archive(str(tmp / "case"), "zip", pr.case_dir)
        shutil.move(made, dest)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return dest


def latest_residuals(res: dict | None) -> str:
    """The newest initial residual of every solved field: 'Latest residuals (step 213, ...): Ux 1.2e-04 · p 3.1e-02'."""
    if not res or not res.get("iters"):
        return ""
    parts = []
    for k, v in (res.get("residuals") or {}).items():
        x = next((q for q in reversed(v) if q is not None and q > 0), None)
        if x is not None:
            parts.append(f"{k} {x:.1e}")
    n = len(res["iters"])
    return f"Latest residuals (step {n}, time/iteration {res['iters'][-1]}): " + " · ".join(parts)


class ResidualChart(QWidget):
    """The residuals of the running solver against the iteration, on a logarithmic axis (1 to 1e-8), a line per field."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.res: dict | None = None
        self.setMinimumHeight(120)
        self.setToolTip("Solver residuals against the iteration (logarithmic): they fall as the solution settles")

    def set_data(self, res: dict | None):
        self.res = res if res and res.get("iters") else None
        self.setProperty("api_text", latest_residuals(self.res))
        self.setVisible(self.res is not None)
        self.update()

    def paintEvent(self, _e):  # noqa: N802
        if not self.res:
            return
        from . import colormaps, theme

        t = theme.tokens()
        g = QPainter(self)
        g.setRenderHint(QPainter.Antialiasing)
        W, H, pad = self.width(), self.height(), 34
        lo, hi = 1e-8, 1.0
        n = len(self.res["iters"])

        def X(i):
            return pad + (W - pad - 6) * i / max(n - 1, 1)

        def Y(v):
            return 6 + (H - 18) * (math.log10(hi) - math.log10(max(v, lo))) / (math.log10(hi) - math.log10(lo))
        g.setPen(QPen(QColor(t["line"]), 1))
        for e in range(0, -9, -2):
            y = Y(10 ** e)
            g.drawLine(QPointF(pad, y), QPointF(W, y))
            g.setPen(QColor(t["muted"]))
            g.drawText(QPointF(2, y + 4), f"1e{e}")
            g.setPen(QPen(QColor(t["line"]), 1))
        series = [(k, v) for k, v in (self.res.get("residuals") or {}).items() if any(x is not None and x > 0 for x in v)]
        for si, (k, v) in enumerate(series):
            r, gg, b = (float(x) for x in colormaps.turbo((si + 0.5) / max(len(series), 1)))
            col = QColor.fromRgbF(r, gg, b)
            g.setPen(QPen(col, 1.4))
            prev = None
            for i, x in enumerate(v):
                if x is None or x <= 0:
                    prev = None
                    continue
                p = QPointF(X(i), Y(x))
                if prev is not None:
                    g.drawLine(prev, p)
                prev = p
            g.drawText(QPointF(W - 110, 14 + 12 * si), k)
        g.end()


# ---------------------------------------------------------------------------------------------------------------- control-API actions
def register(ctl):
    """The actions of the Mesh and Solve steps added by this module (each drives the same widgets a person uses)."""
    from . import control

    win, eng, R = ctl.win, ctl.engine, ctl._reg
    S, N = "string", "number"
    control.MODAL.setdefault("solve.case_zip", "solve.case_zip")

    def sp():
        if not eng.pid:
            raise ValueError("No study is open")
        if win.step != "solve":
            win.go("solve")
            ctl._settle()
        return win.panels["solve"]

    def mp():
        if not eng.pid:
            raise ValueError("No study is open")
        if win.step != "mesh":
            win.go("mesh")
            ctl._settle()
        return win.panels["mesh"]

    def region_add(kind):
        mp()
        key = {"box": "box", "cylinder": "cyl", "cyl": "cyl", "wake": "wake", "tip_rings": "tips", "tips": "tips"}.get(kind)
        if key is None:
            raise ValueError("kind must be box, cylinder, wake or tip_rings")
        _open(ctl, "mesh.regions.toggle")
        ctl._click(f"mesh.region.add_{key}")
        return {"regions": [r["name"] for r in eng.state()["mesh"].get("regions") or []]}

    def region_fit(index, part):
        mp()
        _open(ctl, "mesh.regions.toggle")
        ctl._set(ctl._w(f"mesh.region.{int(index)}.fit"), f"mesh.region.{int(index)}.fit", part)
        r = eng.state()["mesh"]["regions"][int(index)]
        return {"min": r["min"], "max": r["max"]}

    def mesh_check(levels=1):
        mp()
        ctl._click("mesh.check.three" if int(levels) == 2 else "mesh.check.run")
        return {"started": True, "levels": int(levels)}

    def run_opts(cores=None, iterations=None, init_from=None):
        p = sp()
        if cores is not None:
            ctl._set(ctl._w("solve.cores"), "solve.cores", int(cores))
        if iterations is not None:
            ctl._set(ctl._w("solve.iterations"), "solve.iterations", str(iterations))
        if init_from is not None:
            c = ctl._w("solve.init_from")
            i = c.findData(init_from or "")
            if i < 0:
                i = c.findText(str(init_from))
            if i < 0:
                raise ValueError(f"No study '{init_from}' to start from. Items: {[c.itemText(k) for k in range(c.count())]}")
            ctl._set(c, "solve.init_from", i)
        s = eng.state()["settings"]
        return {"cores": s.get("n_procs"), "iterations": s.get("iterations"), "init_from": p.init_from}

    def carry(iterations=None, end_time=None, write_interval=None):
        sp()
        if end_time is not None:
            ctl._set(ctl._w("solve.extend.end"), "solve.extend.end", str(end_time))
            if write_interval is not None:
                ctl._set(ctl._w("solve.extend.interval"), "solve.extend.interval", str(write_interval))
            ctl._click("solve.extend")
        elif ctl._index().get("solve.resume") is not None and ctl._w("solve.resume").isVisible():
            ctl._click("solve.resume")
        else:
            if iterations is not None:
                ctl._set(ctl._w("solve.continue.iterations"), "solve.continue.iterations", str(int(iterations)))
            ctl._click("solve.continue")
        return {"started": True}

    def advanced(values=None, reset=False):
        p = sp()
        _open(ctl, "solve.advanced.toggle")
        if reset:
            ctl._click("solve.adv.reset")
        for k, v in (values or {}).items():
            ctl._set(ctl._w(f"solve.adv.{k}"), f"solve.adv.{k}", v)
        return {"foam_advanced": eng.state().get("foam_advanced") or {}, "panel": bool(p)}

    def case_files():
        sp()
        _open(ctl, "solve.advanced.toggle")
        ctl._click("solve.casefiles.show")
        return eng.project().case_files()

    def case_file(dictionary, text=None, revert=False):
        path = dictionary
        p = sp()
        _open(ctl, "solve.advanced.toggle")
        p.open_case_file(path)
        ctl._settle()
        if revert:
            ctl._click("solve.casefile.revert")
        elif text is not None:
            ctl._set(ctl._w("solve.casefile.text"), "solve.casefile.text", text)
            ctl._click("solve.casefile.save")
        return eng.project().case_file(path)

    def zip_case(path):
        need = sp()
        return {"path": str(need.save_case_zip(path))}

    def software(name=None, recheck=False):
        p = sp()
        if recheck:
            p.recheck()
        if name:
            ctl._click(f"solve.install.{name}")
        return {"openfoam": {k: v for k, v in (p.foam or {}).items() if k != "help"}, "calculix": {k: v for k, v in (p.ccx or {}).items() if k != "help"}}

    R("mesh.region_add", "Add a refinement region: box, cylinder, or (flow studies with rotating zones) the wake behind the zones or the tip-vortex rings.",
      {"kind": (S, "box|cylinder|wake|tip_rings", True)}, region_add)
    R("mesh.region_fit", "Fit a box refinement region round a part (15 % clear of it).",
      {"index": (N, "region number from 0", True), "part": (S, "part (body) name", True)}, region_fit)
    R("mesh.check", "Mesh-sensitivity check (after a converged run): re-mesh one level finer and compare (levels 1), or the three-level "
                    "grid-convergence index (levels 2); a job.", {"levels": (N, "1 or 2", False)}, mesh_check)
    R("solve.options", "Run options of a flow study: CPU cores (0 = Auto), max iterations (steady), and the study whose solution starts "
                       "the run ('' = uniform flow).",
      {"cores": (N, "0 = Auto, else 1..N", False), "iterations": (N, "max iterations of a steady run", False),
       "init_from": (S, "study id or name to start from; '' for uniform flow", False)}, run_opts)
    R("solve.continue", "Carry the last OpenFOAM run on: more iterations (steady), resume a stopped transient run, or extend a finished "
                        "transient run to a later end time; a job.",
      {"iterations": (N, "extra iterations (steady)", False), "end_time": (N, "new end time in s (transient)", False),
       "write_interval": (N, "save interval in s (transient)", False)}, carry)
    R("solve.stop", "Stop the running flow solver and keep the current solution.", {}, lambda: (sp(), ctl._click("solve.stop"), {"stopping": True})[2])
    R("solve.cancel", "Cancel the running job of the study (nothing new is kept).", {}, lambda: (sp(), ctl._click("solve.cancel"), {"cancelled": True})[2])
    R("solve.write_case", "Write the OpenFOAM case files without running the solver; a job.", {},
      lambda: (sp(), ctl._click("solve.case"), {"started": True})[2])
    R("solve.advanced", "Advanced OpenFOAM numerics: set some ({key: value}, keys as in the panel: convection, non_orth, relax_p ...) or reset "
                        "all to the defaults.", {"values": ("object", "{key: value}", False), "reset": ("boolean", "reset to defaults first", False)},
      advanced)
    R("solve.case_files", "Write the OpenFOAM case files as the next run would (a preview) and list them.", {}, case_files)
    R("solve.case_file", "Open an OpenFOAM case dictionary in the editor; with text, save your version (used on every run); with revert, "
                         "go back to the generated one.",
      {"dictionary": (S, "the dictionary inside the case, e.g. system/fvSchemes", True), "text": (S, "its new text", False),
       "revert": ("boolean", "back to the generated version", False)}, case_file)
    R("solve.case_zip", "Save the study's OpenFOAM case folder as a zip file.", {"path": (S, "where to write the .zip", True)}, zip_case)
    R("software.install", "Check for the solver programs (recheck) or install one: openfoam or calculix (a download; a job).",
      {"name": (S, "openfoam|calculix", False), "recheck": ("boolean", "look for the programs again", False)}, software)


def _open(ctl, toggle_id):
    o = ctl._index().get(toggle_id)
    if o is not None and o.isCheckable() and not o.isChecked():
        o.click()
        ctl._settle()
