"""One CFD-coupled flutter run at one air speed: case preparation, the OpenFOAM runner, and the resumable, watchdog
protected run itself (`SpeedRun`).

Procedure (measured on the workbench's OpenFOAM v2606, see `lumped.py` for the coupling protocol):

  phase A  the flow develops with the wing held and NO coupling (a `staticFvMesh` dynamicMeshDict, so no waiting for the
           structure: about 0.2 s per step on 8 cores against 1.35 s per coupled step). The result is written once.
  phase B  the coupled dynamicMeshDict is switched on and OpenFOAM restarts from that written time. The structure is held
           for `t_ref` (the load of that interval is the reference), then released from rest with modal velocities and the
           decay or growth of the modal coordinates gives the damping (`CoupledRun`).

A coupled step costs about 1 s of wall time whatever the structure needs, and one hung exchange once froze a run for 40
minutes, so phase B is protected:

* Checkpoints. OpenFOAM writes a time directory every `checkpoint_every_s` of simulated time (adjustableRunTime). When such
  a directory is complete, the structure state at exactly that time is saved to `run_dir/checkpoint.json` (atomic) next to
  the name of the written time. Every exchange is appended to `run_dir/history.jsonl`.
* Watchdog. No new exchange for `max(stall_timeout_s, stall_factor x median exchange wall time)`: the mpiexec process tree
  is killed (by the PID of the process this run started) and the run RESUMES from the last checkpoint: structure state
  restored, history cut back to that time, later (half written) time directories deleted, OpenFOAM restarted with
  startFrom latestTime. After `max_retries` resumes the speed is marked "stalled". A crash of the solver counts the same way
  ("failed" after the retries).
* Time limits. `speed_time_limit_s` (wall seconds for this speed) and `deadline_ts` (absolute epoch time, the total limit):
  the run is stopped cleanly and marked "partial" (the data so far is kept and can still be analysed).
* A fresh start after phase A is the degenerate resume (no checkpoint yet: the base is the phase A result).

OpenFOAM and the process handling sit behind `FoamRunner` (run_phase_a, start_phase_b, stop); the tests give `SpeedRun` a
stand-in that speaks the real lock protocol. Everything here is independent of the wing: any lumped-node model works.
"""
from __future__ import annotations

import collections
import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import numpy as np

from . import flutter_cfd as FC
from . import lumped

DEFAULTS = {
    "patch": "wing", "rho": 1.204, "p_ref": 0.0, "rho_name": None,   # forces: incompressible (rhoRef) or compressible (rho field)
    "solver": "pimpleFoam", "n_procs": 8,
    "max_co": 6.0, "max_delta_t": 5e-4, "n_outer": 2,                  # measured: maxCo 6 with 2 outer correctors is stable
    "t_settle": 0.05, "t_ref": 0.004, "t_run": 0.3,                    # simulated seconds: phase A, held coupled, released
    "perturbation": None, "perturbation_amp": 1e-3, "n_excite": 4,     # release velocities (explicit list, or amplitude at the tip)
    "prescribed": None,                                                # {"mode", "amp", "f_hz", "ramp_s"}: forced-motion run
    "checkpoint_every_s": 0.01, "keep_checkpoints": 2,
    "stall_timeout_s": 120.0, "stall_factor": 20.0, "startup_timeout_s": 900.0, "max_retries": 2,
    "speed_time_limit_s": None, "deadline_ts": None,
    "turn_timeout": 7200, "poll": 0.002, "pre_steps": [],
}

_TIME_NAME = re.compile(r"^[0-9]+(\.[0-9]*)?([eE][-+]?[0-9]+)?$|^\.[0-9]+$")


class RunCancelled(Exception):
    pass


# ------------------------------------------------------------------------------------------------------ small helpers
def write_json_atomic(path, obj, indent=None):
    """Write JSON to a temp file next to `path` and replace it (a reader never sees half a file)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(obj, indent=indent), encoding="utf-8")
    for k in range(20):  # (Windows refuses the replace while a reader has the target open)
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if k == 19:
                raise
            time.sleep(0.02)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def kill_tree(proc, wait=10.0):
    """Kill the process this run started and everything below it (mpiexec and its solver processes)."""
    if proc is None or proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            proc.kill()
        proc.wait(timeout=wait)
    except (OSError, subprocess.TimeoutExpired):
        try:
            proc.kill()
        except OSError:
            pass


def release_velocity(model: FC.NodeModel, amp=1e-3, n_excite=4):
    """Modal velocities that give the lowest `n_excite` modes a tip displacement amplitude of `amp` [m] (as in the
    prototype runs): omega x amp / (tip motion of the mode)."""
    tip = np.linalg.norm(model.T[:, -1, :], axis=1)
    return model.omega * amp / np.maximum(tip, 1e-9) * (np.arange(model.m) < n_excite)


# ------------------------------------------------------------------------------------------------------ case files
def set_control(case, **kw):
    """Set entries of system/controlDict (add them after `application` when missing)."""
    cd = Path(case) / "system" / "controlDict"
    s = cd.read_text()
    for k, v in kw.items():
        val = v if isinstance(v, str) else (str(int(v)) if isinstance(v, (bool, int)) else "%.10g" % v)
        s, n = re.subn(rf"^{k}\s+[^;]+;", lambda m: f"{k:<16}{val};", s, count=1, flags=re.M)
        if n == 0:
            s = re.sub(r"^(application\s+\w+;)", lambda m: m.group(1) + f"\n{k:<16}{val};", s, count=1, flags=re.M)
    cd.write_text(s, newline="\n")


def use_dynamic_mesh(case, which):
    """Make constant/dynamicMeshDict the "static" (phase A: no coupling) or the "coupled" version (both are kept)."""
    c = Path(case) / "constant"
    src = c / f"dynamicMeshDict.{which}"
    if not src.exists():
        raise FileNotFoundError(f"{src} is missing (prepare_coupled_case was not run)")
    (c / "dynamicMeshDict").write_text(src.read_text(), newline="\n")


def prepare_coupled_case(case, node_model: FC.NodeModel, settings, det=None):
    """Make a written OpenFOAM transient case (pimpleFoam, displacementLaplacian dynamicMeshDict, pointDisplacement file)
    ready for the two phases: the lumped-point coupling (`lumped.write_setup`), maxCo / maxDeltaT, nOuterCorrectors,
    a scotch decomposeParDict for `n_procs`, and BOTH dynamicMeshDict versions (constant/dynamicMeshDict.static for phase
    A, .coupled for phase B; the static one is the active file). Safe to call again. Returns the key numbers."""
    case = Path(case)
    s = {**DEFAULTS, **(settings or {})}
    if not (case / "constant" / "dynamicMeshDict.coupled").exists():
        lumped.write_setup(case, node_model.x0, patch=s["patch"], rho=s["rho"], timeout=int(s["turn_timeout"]),
                           p_ref=s["p_ref"], rho_name=s["rho_name"])
        dm = case / "constant" / "dynamicMeshDict"
        (case / "constant" / "dynamicMeshDict.coupled").write_text(dm.read_text(), newline="\n")
        (case / "constant" / "dynamicMeshDict.static").write_text(
            lumped.HEADER % "dynamicMeshDict" + "dynamicFvMesh staticFvMesh;\n", newline="\n")
    use_dynamic_mesh(case, "static")
    set_control(case, maxCo=float(s["max_co"]), maxDeltaT=float(s["max_delta_t"]), adjustTimeStep="yes",
                writeControl="adjustableRunTime", writeInterval=float(s["t_settle"]), endTime=float(s["t_settle"]))
    fv = case / "system" / "fvSolution"
    txt, n = re.subn(r"nOuterCorrectors\s+\d+;", f"nOuterCorrectors {int(s['n_outer'])};", fv.read_text())
    if n == 0:
        raise ValueError("system/fvSolution has no nOuterCorrectors entry")
    fv.write_text(txt, newline="\n")
    (case / "system" / "decomposeParDict").write_text(
        lumped.HEADER % "decomposeParDict" + f"numberOfSubdomains {int(s['n_procs'])};\nmethod scotch;\n", newline="\n")
    t_release = float(s["t_settle"]) + float(s["t_ref"])
    return {"n_procs": int(s["n_procs"]), "t_settle": float(s["t_settle"]), "t_release": t_release,
            "t_end": t_release + float(s["t_run"]), "checkpoint_every_s": float(s["checkpoint_every_s"])}


def _proc_dirs(case):
    case = Path(case)
    procs = sorted((d for d in case.glob("processor*") if d.is_dir() and d.name[9:].isdigit()), key=lambda d: int(d.name[9:]))
    return procs or [case]


def _times_in(d: Path):
    out = []
    if d.is_dir():
        for x in d.iterdir():
            if x.is_dir() and _TIME_NAME.match(x.name):
                v = float(x.name)
                if v > 0:
                    out.append((x.name, v))
    return sorted(out, key=lambda p: p[1])


def written_times(case):
    """[(directory name, time)] of the written times (time > 0) of the case, oldest first: those of processor0 of a split
    case, else of the case folder."""
    return _times_in(_proc_dirs(case)[0])


def time_dir_everywhere(case, name):
    return all((d / name).is_dir() for d in _proc_dirs(case))


def remove_times(case, keep=(), before=None, after=None):
    """Delete time directories (time > 0) from every processor folder, except the names in `keep`: those earlier than
    `before` and those later than `after` (a time is deleted when it is in the range asked for)."""
    for d in _proc_dirs(case):
        for name, v in _times_in(d):
            if name in keep:
                continue
            if (before is not None and v < before) or (after is not None and v > after):
                shutil.rmtree(d / name, ignore_errors=True)


def finish_phase_a(case):
    """After phase A: the latest written time is the restart time. The static mesh did not write pointDisplacement, so
    copy it from 0 into that time. Returns {"t", "name"}."""
    dirs = _proc_dirs(case)
    times = _times_in(dirs[0])
    if not times:
        raise RuntimeError("Phase A wrote no time directory")
    name, t = times[-1]
    for d in dirs:
        ensure_point_displacement(d, name)
    return {"t": t, "name": name}


def ensure_point_displacement(proc_dir, name):
    proc_dir = Path(proc_dir)
    if proc_dir.name == "0" or not (proc_dir / "0").is_dir():
        return
    for f in (proc_dir / "0").glob("pointDisplacement*"):
        if not (proc_dir / name / f.name).exists():
            shutil.copy(f, proc_dir / name / f.name)


# ------------------------------------------------------------------------------------------------------ history
class HistoryLog:
    """run_dir/history.jsonl: one line per exchange {"t", "q", "Q", "rel"}, flushed every `flush_every` exchanges."""

    def __init__(self, path, flush_every=50):
        self.path = Path(path)
        self.flush_every = flush_every
        self._f = None
        self._n = 0

    def _open(self):
        if self._f is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._f = open(self.path, "a", encoding="utf-8")
        return self._f

    def append(self, row):
        t, q, Q, rel = row
        self._open().write(json.dumps({"t": float(t), "q": np.asarray(q, float).tolist(), "Q": np.asarray(Q, float).tolist(),
                                       "rel": int(bool(rel))}) + "\n")
        self._n += 1
        if self._n % self.flush_every == 0:
            self.flush()

    def flush(self):
        if self._f is not None:
            self._f.flush()

    def close(self):
        if self._f is not None:
            self._f.close()
            self._f = None

    def truncate(self, n):
        """Keep the first n rows (the file is rewritten atomically); returns the kept rows."""
        self.close()
        rows = read_history_rows(self.path)[:max(int(n), 0)]
        tmp = self.path.with_name(self.path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps({"t": r[0], "q": r[1].tolist(), "Q": r[2].tolist(), "rel": int(r[3])}) + "\n")
        os.replace(tmp, self.path)
        return rows


def read_history_rows(path):
    """[(t, q (m,), Q (m,), released)] of a history file (a half written last line is ignored)."""
    rows = []
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return rows
    for line in text.splitlines():
        try:
            d = json.loads(line)
            rows.append((d["t"], np.array(d["q"], float), np.array(d["Q"], float), bool(d["rel"])))
        except (ValueError, KeyError):
            break
    return rows


def read_series(path):
    """(t (nt,), q (nt, m)) of the released part of a history file."""
    rows = [r for r in read_history_rows(path) if r[3]]
    if not rows:
        return np.zeros(0), np.zeros((0, 0))
    return np.array([r[0] for r in rows]), np.array([r[1] for r in rows])


# ------------------------------------------------------------------------------------------------------ OpenFOAM
class FoamRunner:
    """The OpenFOAM side of a speed (native Windows build, parallel with MS-MPI as in the prototype): phase A to its end,
    and the start of phase B from a written time. `SpeedRun` takes any object with these three methods."""

    def __init__(self, case, settings, det, log=None):
        from . import runner as _runner  # (imported late: the tests of the rest need no OpenFOAM)
        self._rn = _runner
        self.case = Path(case)
        self.s = {**DEFAULTS, **(settings or {})}
        self.det = det
        self.log = log or (lambda line: None)
        self.env = _runner.windows_env(Path(det["project_dir"]), Path(det["bin_dir"]))
        self.bin = Path(det["bin_dir"])

    def _cmd(self, app):
        n = int(self.s["n_procs"])
        exe = str(self.bin / (app + ".exe"))
        if n > 1:
            return [str(Path(self.det["mpi_dir"]) / "mpiexec.exe"), "-n", str(n), exe, "-parallel", "-case", str(self.case)]
        return [exe, "-case", str(self.case)]

    def _popen(self, logname, append=False):
        lf = open(self.case / logname, "ab" if append else "wb")
        proc = subprocess.Popen(self._cmd(self.s["solver"]), cwd=self.case, env=self.env, stdout=lf,
                                stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        lf.close()  # (the child holds its own handle)
        return proc

    def run_phase_a(self, alive=lambda: True):
        """Uncoupled flow development to t_settle; returns {"t", "name"} of the restart time. Raises RunCancelled when
        alive() turns false, RuntimeError when a step fails."""
        case, s = self.case, self.s
        use_dynamic_mesh(case, "static")
        if s["pre_steps"] and not self._rn.run_steps_windows(case, s["pre_steps"], self.det, on_line=self.log):
            raise RuntimeError("Mesh preparation (checkMesh / renumberMesh) failed; see the case logs")
        if int(s["n_procs"]) > 1:
            r = subprocess.run([str(self.bin / "decomposePar.exe"), "-case", str(case), "-force"], env=self.env,
                               capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if r.returncode != 0:
                raise RuntimeError("decomposePar failed: " + r.stdout[-400:])
        shutil.rmtree(case / "comms", ignore_errors=True)
        set_control(case, startFrom="startTime", endTime=float(s["t_settle"]), writeInterval=float(s["t_settle"]),
                    writeControl="adjustableRunTime")
        proc = self._popen("log.phaseA")
        try:
            while proc.poll() is None:
                if not alive():
                    raise RunCancelled()
                time.sleep(0.5)
        finally:
            kill_tree(proc)
        if proc.returncode != 0:
            raise RuntimeError(f"Phase A (uncoupled flow) failed with exit code {proc.returncode}; see log.phaseA")
        return finish_phase_a(case)

    def start_phase_b(self, name, t_end):
        """Start OpenFOAM coupled from the written time `name` (later time directories must be gone: startFrom
        latestTime) to t_end, writing a time directory every checkpoint_every_s. Returns the Popen."""
        use_dynamic_mesh(self.case, "coupled")
        for d in _proc_dirs(self.case):
            ensure_point_displacement(d, name)
        set_control(self.case, startFrom="latestTime", endTime=float(t_end), writeControl="adjustableRunTime",
                    writeInterval=float(self.s["checkpoint_every_s"]))
        with open(self.case / "log.pimpleFoam", "ab") as lf:
            lf.write(f"\n##### start from {name} at {time.strftime('%H:%M:%S')}\n".encode())
        return self._popen("log.pimpleFoam", append=True)

    def stop(self, proc):
        kill_tree(proc)

    def log_tail(self, n=800):
        try:
            return (self.case / "log.pimpleFoam").read_text(errors="ignore")[-n:]
        except OSError:
            return ""


# ------------------------------------------------------------------------------------------------------ the run
class SpeedRun:
    """One air speed: phase A, then phase B (coupled) with checkpoints and the stall watchdog (see the module text).

    Files in run_dir: phaseA.json (restart time), checkpoint.json, history.jsonl, speed.json (final state).
    `progress` (a dict, read by the status writer of the controller) is updated as it goes. `clock` and `runner` are
    injectable for the tests. `state` is one of pending, phaseA, phaseB, done, partial, stalled, failed, cancelled."""

    def __init__(self, case, model: FC.NodeModel, settings, det, run_dir, runner=None, clock=time.time):
        self.case, self.model = Path(case), model
        self.s = {**DEFAULTS, **(settings or {})}
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.runner = runner or FoamRunner(self.case, self.s, det)
        self.clock = clock
        self.cancel_event = threading.Event()
        self.state, self.message, self.retries = "pending", "", 0
        self.run = None
        self._reason = None
        self._t0 = None
        prior = read_json(self.run_dir / "speed.json", {}) or {}
        self._wall_prior = float(prior.get("wall_s", 0.0))
        self.progress = {"state": "pending", "phase": None, "t_sim": 0.0, "t_end": None, "exchanges": 0, "wall_s": self._wall_prior,
                         "eta_s": None, "retries": 0, "last_exchange_ts": None, "checkpoint_t": None, "message": ""}

    # -- control
    def cancel(self):
        self.cancel_event.set()

    @property
    def comms(self):
        return self.case / "comms"

    def wall_s(self):
        return self._wall_prior + (0.0 if self._t0 is None else self.clock() - self._t0)

    def _limit_reason(self):
        lim, dl = self.s["speed_time_limit_s"], self.s["deadline_ts"]
        if lim is not None and self.wall_s() > float(lim):
            return "the time limit for this speed was reached"
        if dl is not None and self.clock() > float(dl):
            return "the total time limit was reached"
        return None

    def _set(self, state, message=""):
        self.state, self.message = state, message
        self.progress.update(state=state, message=message, wall_s=self.wall_s(), retries=self.retries)
        write_json_atomic(self.run_dir / "speed.json", {"state": state, "message": message, "wall_s": self.wall_s(),
                                                        "retries": self.retries, "t": self.progress["t_sim"]})

    # -- phase A
    def phase_a(self):
        """Uncoupled flow development (skipped when it is already done). Returns True when phase A is complete."""
        done = read_json(self.run_dir / "phaseA.json")
        if done:
            return True
        if self._t0 is None:
            self._t0 = self.clock()
        self.progress.update(phase="A", t_end=float(self.s["t_settle"]))
        self._set("phaseA")
        try:
            res = self.runner.run_phase_a(alive=lambda: not self.cancel_event.is_set() and self._limit_reason() is None)
        except RunCancelled:
            lim = self._limit_reason()
            self._set("partial" if lim and not self.cancel_event.is_set() else "cancelled",
                      lim or "cancelled during the flow development")
            return False
        except Exception as e:  # noqa: BLE001 - the message goes to the user
            self._set("failed", f"flow development failed: {e}")
            return False
        write_json_atomic(self.run_dir / "phaseA.json", res)
        self.progress["t_sim"] = res["t"]
        return True

    # -- phase B
    def _base(self):
        ph = read_json(self.run_dir / "phaseA.json")
        if not ph:
            raise RuntimeError("Phase A is not done")
        return ph

    def _load_checkpoint(self, base):
        ck = read_json(self.run_dir / "checkpoint.json")
        if ck and ck.get("name") and time_dir_everywhere(self.case, ck["name"]) and ck.get("state", {}).get("t_prev") is not None:
            return ck
        return None

    def _make_run(self, base, start, rows):
        s, tb = self.s, base["t"]
        t_release = tb + float(s["t_ref"])
        if s["prescribed"]:
            p = s["prescribed"]
            run = FC.PrescribedRun(self.model, self.comms, t_release, p["mode"], p["amp"], p["f_hz"], p.get("ramp_s"),
                                   poll=s["poll"], t_ref_from=tb, t_start=tb)
        else:
            v0 = (np.asarray(s["perturbation"], float) if s["perturbation"] is not None
                  else release_velocity(self.model, s["perturbation_amp"], s["n_excite"]))
            run = FC.CoupledRun(self.model, self.comms, t_release, v0, poll=s["poll"], t_ref_from=tb, t_start=tb)
        if start["state"] is not None:
            run.load_state(start["state"], rows)
        return run, t_release

    def phase_b(self):
        """The coupled run from the last checkpoint (or the end of phase A), with the watchdog and the retries. Returns
        the final state: done, partial, stalled, failed or cancelled."""
        if self._t0 is None:
            self._t0 = self.clock()
        base = self._base()
        self.retries = 0
        while True:
            try:
                out = self._attempt(base)
            except Exception as e:  # noqa: BLE001 - e.g. OpenFOAM could not be started: the message goes to the user
                self._set("failed", f"the coupled run could not be run: {e}")
                return self.state
            if out != "retry":
                return self.state
            self.retries += 1
            if self.retries > int(self.s["max_retries"]):
                if self._reason == "stall":
                    self._set("stalled", f"no answer from OpenFOAM after {self.retries - 1} restarts")
                else:
                    self._set("failed", f"OpenFOAM stopped {self.retries} times: {self._tail()}")
                return self.state

    resume = phase_b  # a resume is a phase B start: it begins at the last checkpoint

    def _tail(self):
        f = getattr(self.runner, "log_tail", None)
        return (f() if f else "").strip()[-300:]

    def run_all(self):
        """Phase A then phase B."""
        if self.phase_a():
            return self.phase_b()
        return self.state

    def _attempt(self, base):
        s = self.s
        ck = self._load_checkpoint(base)
        start = ck or {"name": base["name"], "t": base["t"], "state": None, "n_history": 0}
        t_start = float(start["name"])
        remove_times(self.case, after=t_start + max(1e-9, 1e-6 * t_start))
        shutil.rmtree(self.comms, ignore_errors=True)
        hlog = HistoryLog(self.run_dir / "history.jsonl")
        rows = hlog.truncate(start["n_history"])
        run, t_release = self._make_run(base, start, rows)
        self.run = run
        t_end = t_release + float(s["t_run"])
        t_end_eff = t_end * (1 - 1e-7)
        self.progress.update(phase="B", t_end=t_end, t_sim=t_start, exchanges=len(rows))
        self._set("phaseB", "resumed from the checkpoint at %.6g s" % t_start if ck else "")
        self._reason = None
        st = {"last_ex": self.clock(), "first": True, "gaps": collections.deque(maxlen=200), "next_poll": 0.0,
              "ring": collections.deque(maxlen=32), "ck_t": t_start, "ck_names": [] if not ck else [start["name"]],
              "pace": collections.deque(maxlen=60), "n_logged": len(rows)}
        proc = self.runner.start_phase_b(start["name"], t_end)

        def stall_limit():
            if st["first"]:
                return float(s["startup_timeout_s"])
            med = float(np.median(st["gaps"])) if st["gaps"] else 0.0
            return max(float(s["stall_timeout_s"]), float(s["stall_factor"]) * med)

        def alive():
            now = self.clock()
            if self.cancel_event.is_set():
                self._reason = "cancelled"
            elif now >= st["next_poll"]:
                st["next_poll"] = now + 0.05
                lim = self._limit_reason()
                if lim:
                    self._reason, self.message = "time_limit", lim
                elif proc.poll() is not None:
                    self._reason = "exited"
                elif now - st["last_ex"] > stall_limit():
                    self._reason = "stall"
            return self._reason is None

        def on_exchange(t, r):
            now = self.clock()
            if not st["first"]:
                st["gaps"].append(now - st["last_ex"])
            st["first"], st["last_ex"] = False, now
            if len(r.history) > st["n_logged"]:  # (a repeated handshake adds no row)
                hlog.append(r.history[-1])
                st["n_logged"] += 1
            st["ring"].append((t, r.to_state()))
            st["pace"].append((now, t))
            self._checkpoint(r, t, st, hlog)
            p = self.progress
            p.update(t_sim=t, exchanges=len(r.history), wall_s=self.wall_s(), last_exchange_ts=time.time())
            if len(st["pace"]) > 5 and st["pace"][-1][0] > st["pace"][0][0]:
                rate = (st["pace"][-1][1] - st["pace"][0][1]) / (st["pace"][-1][0] - st["pace"][0][0])
                p["eta_s"] = (t_end - t) / rate if rate > 0 else None

        try:
            run.run(alive, on_exchange=on_exchange, t_end=t_end_eff, turn_timeout=None)
        finally:
            rc = proc.poll()
            self.runner.stop(proc)
            hlog.flush()
            hlog.close()
        t = run.t_prev if run.t_prev is not None else t_start
        reason = self._reason
        if reason is None or (reason == "exited" and rc == 0 and t >= t_end - 2 * (run.h_prev or 0.0)):
            reason = "done" if t >= t_end_eff - 2 * (run.h_prev or 0.0) else "exited"
        self._reason = reason
        self.progress.update(t_sim=t, exchanges=len(run.history), wall_s=self.wall_s())
        if reason == "done":
            self._set("done", "")
            return "done"
        if reason == "cancelled":
            self._set("cancelled", "cancelled")
            return "cancelled"
        if reason == "time_limit":
            self._set("partial", self.message)
            return "partial"
        return "retry"  # stall, or the solver stopped on its own before the end time

    def _checkpoint(self, run, t, st, hlog):
        """Save a checkpoint when OpenFOAM has completely written a new time directory (one older than this exchange, so
        every processor is through with it) and the structure state at exactly that time is at hand."""
        h = run.h_prev
        if not h:
            return
        names = [(n, v) for n, v in written_times(self.case) if v > st["ck_t"] + 1e-12 and v < t - 0.25 * h]
        if not names:
            return
        name, T = names[-1]
        snap = min(st["ring"], key=lambda p: abs(p[0] - T), default=None)
        st["ck_t"] = T  # (older unmatched directories are not tried again)
        if snap is None or abs(snap[0] - T) > min(max(1e-5 * T, 1e-9) + 1e-12, 0.25 * h):
            return
        hlog.flush()
        state = dict(snap[1])
        state["t_prev"] = float(name)  # OpenFOAM restarts at the time written in the name
        ck = {"name": name, "t": float(name), "state": state, "n_history": state["n_history"], "wall_s": self.wall_s(),
              "time": time.strftime("%Y-%m-%d %H:%M:%S")}
        write_json_atomic(self.run_dir / "checkpoint.json", ck)
        st["ck_names"].append(name)
        keep = set(st["ck_names"][-int(self.s["keep_checkpoints"]):]) | {self._base()["name"]}
        remove_times(self.case, keep=keep, before=float(name))
        self.progress["checkpoint_t"] = float(name)

    # -- results
    def series(self):
        """(t, q) of the released motion so far (from history.jsonl)."""
        return read_series(self.run_dir / "history.jsonl")
