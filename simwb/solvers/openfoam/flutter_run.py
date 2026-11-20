"""The detached controller of a CFD-coupled flutter run (several air speeds, hours of wall time).

    python -m simwb.solvers.openfoam.flutter_run <run_dir>

The server starts it with `start_detached` (a process of its own that survives a restart of the server) and afterwards only
reads `run_dir/status.json` (`attach`) and creates `run_dir/cancel.flag` (`request_cancel`). Files in run_dir:

  manifest.json  {settings_hash, settings, model: "model.npz", speeds: [{U, state, dir, case?, result}], ...}; state is one
                 of pending, phaseA, phaseB, analysed, failed, stalled, cancelled. The controller keeps it up to date and
                 puts the analysis of every speed into "result".
  status.json    rewritten every <= 5 s: {state, pid, speed_index, speed_U, phase, t_sim, t_end, exchanges, wall_s, eta_s,
                 retries, heartbeat, message, speeds: [{U, state}], runs: [...]}. State: running, done, partial, cancelled,
                 stalled, failed. A heartbeat older than 30 s with the process gone means the controller died: start it
                 again, it resumes every speed from its checkpoint (see flutter_case).
  pid            process id of the controller (one controller per run_dir)
  cancel.flag    created by the server: every run is stopped cleanly (the solver process tree is killed)
  <speed dir>/   the case, checkpoint.json, history.jsonl, speed.json (see flutter_case.SpeedRun)

Manifest settings (all optional, see flutter_case.DEFAULTS for the rest): parallel_runs (speeds at the same time, each on
cores / parallel_runs processes), cores, total_time_limit_s, speed_time_limit_s, slot_root, prepare (false: the cases are
already prepared). Phase A of a speed holds a heavy slot (foam_slot), the coupled phase a coupled one.
"""
from __future__ import annotations

import math
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

from . import flutter_case as FCASE
from . import flutter_cfd as FC
from . import foam_slot

STATUS_EVERY_S = 2.0
ALIVE_S = 30.0
FINISHED = ("analysed", "failed", "stalled", "cancelled")
LOCK = threading.Lock()


# ------------------------------------------------------------------------------------------------------ the run folder
class RunDir:
    """The files of one run and the calls the server makes."""

    def __init__(self, path):
        self.path = Path(path)
        self.manifest_path = self.path / "manifest.json"
        self.status_path = self.path / "status.json"
        self.pid_path = self.path / "pid"
        self.cancel_path = self.path / "cancel.flag"

    @classmethod
    def create(cls, path, model, speeds, settings=None, settings_hash="", cases=None):
        """Make a run folder: the node model (model.npz) and a manifest of pending speeds (air speeds U [m/s], or dicts
        {U, case?, settings?}). `cases` maps U to an already written OpenFOAM case folder (default: <speed dir>/case)."""
        rd = cls(path)
        rd.path.mkdir(parents=True, exist_ok=True)
        FC.save_model(model, rd.path / "model.npz")
        entries = []
        for sp in speeds:
            e = dict(sp) if isinstance(sp, dict) else {"U": float(sp)}
            e.setdefault("dir", f"speed_{e['U']:g}")
            if cases and e["U"] in cases:
                e["case"] = str(cases[e["U"]])
            e.update(state="pending", result=None)
            entries.append(e)
        rd.write_manifest({"settings_hash": settings_hash, "settings": dict(settings or {}), "model": "model.npz",
                           "speeds": entries, "created": time.strftime("%Y-%m-%d %H:%M:%S")})
        return rd

    def read_manifest(self):
        return FCASE.read_json(self.manifest_path, None)

    def write_manifest(self, m):
        FCASE.write_json_atomic(self.manifest_path, m, indent=1)

    def update_speed(self, i, **kw):
        """Change fields of speeds[i] in the manifest (thread safe) and return the entry."""
        with LOCK:
            m = self.read_manifest()
            m["speeds"][i].update(kw)
            self.write_manifest(m)
            return m["speeds"][i]

    def request_cancel(self):
        self.path.mkdir(parents=True, exist_ok=True)
        self.cancel_path.write_text("cancel\n")

    def attach(self) -> dict:
        """The last status plus {"alive": bool, "age_s"}: alive = the heartbeat is younger than 30 s and the process runs."""
        st = FCASE.read_json(self.status_path)
        if not st:
            return {"state": "none", "alive": False, "age_s": None}
        age = time.time() - float(st.get("heartbeat", 0))
        try:
            pid = int(self.pid_path.read_text().strip())
        except (OSError, ValueError):
            pid = int(st.get("pid", 0) or 0)
        st = dict(st, age_s=age, alive=bool(st.get("state") == "running" and age < ALIVE_S and foam_slot.pid_alive(pid)))
        return st

    def start_detached(self, python=None) -> subprocess.Popen:
        """Start the controller as a process of its own (no console, own process group: it survives the server)."""
        self.cancel_path.unlink(missing_ok=True)
        root = Path(__file__).resolve().parents[3]
        env = dict(os.environ)
        env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
        flags = 0
        kw = {}
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200) | getattr(subprocess, "DETACHED_PROCESS", 0x8)
        else:
            kw["start_new_session"] = True
        log = open(self.path / "controller.log", "ab")
        try:
            return subprocess.Popen([python or sys.executable, "-m", "simwb.solvers.openfoam.flutter_run", str(self.path)],
                                    cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                    creationflags=flags, close_fds=True, **kw)
        finally:
            log.close()


def attach(run_dir) -> dict:
    return RunDir(run_dir).attach()


def start_detached(run_dir, python=None) -> subprocess.Popen:
    return RunDir(run_dir).start_detached(python)


def request_cancel(run_dir):
    RunDir(run_dir).request_cancel()


# ------------------------------------------------------------------------------------------------------ the analysis
def default_analyse(t, q, f_hints_hz):
    """Poles of the released modal coordinates: the damping estimator of flutter_damping (the robust one) when it is
    there, else the matrix-pencil fit of flutter_cfd. Returns {"poles": [{f_Hz, zeta, zeta_sigma, spread, n_periods, ok,
    reasons, hint_Hz}], "critical": pole or None, "method": str}."""
    try:
        from . import flutter_damping  # agent-written module; absent in older trees
        est = flutter_damping.estimate
    except Exception:  # noqa: BLE001
        est = None
    if est is not None:
        out = dict(est(t, q, list(f_hints_hz), n_periods_min=6, band=0.2))
        out.setdefault("method", "flutter_damping")
    else:
        raw = FC.analyse_series(t, q)
        dur = raw["duration_s"]
        hints = list(f_hints_hz)
        poles = []
        for f, z in raw["poles"]:
            hint = min(hints, key=lambda h: abs(h - f)) if hints else None
            n = dur * f
            poles.append({"f_Hz": f, "zeta": z, "zeta_sigma": None, "spread": None, "n_periods": n, "ok": bool(n >= 3),
                          "reasons": [] if n >= 3 else ["less than 3 periods in the record"],
                          "hint_Hz": hint if hint is not None and abs(hint - f) < 0.2 * hint else None})
        out = {"poles": poles, "method": "matrix_pencil", "duration_s": dur, "n_samples": raw["n_samples"]}
    good = [p for p in out.get("poles", []) if p.get("ok")]
    out["critical"] = min(good, key=lambda p: p["zeta"]) if good else None
    return out


# ------------------------------------------------------------------------------------------------------ the controller
class Controller:
    """Runs the speeds of a manifest. `runner_factory(case, settings, det)` (default `flutter_case.FoamRunner`), `analyse_fn`
    (t, q, f_hints_hz) -> dict, the model and `det` can be injected for the tests."""

    def __init__(self, run_dir, analyse_fn=None, runner_factory=None, model=None, det=None, clock=time.time,
                 status_every=STATUS_EVERY_S):
        self.rd = RunDir(run_dir)
        self.analyse_fn = analyse_fn or default_analyse
        self.runner_factory = runner_factory
        self.model, self.det, self.clock = model, det, clock
        self.status_every = status_every
        self.active = {}           # speed index -> SpeedRun
        self.cancelled = threading.Event()
        self.t0 = clock()
        self.wall_prior = 0.0
        self._deadline = None
        self.message = ""
        self.final = None
        self._stop = threading.Event()

    # -- status
    def _speeds_view(self):
        m = self.rd.read_manifest() or {"speeds": []}
        return [{"U": s.get("U"), "state": s.get("state")} for s in m["speeds"]]

    def _eta(self, runs, speeds):
        etas = [r["eta_s"] for r in runs if r.get("eta_s") is not None]
        cur = max(etas) if etas else None
        pending = sum(1 for s in speeds if s["state"] in ("pending",))
        done_wall = [s for s in (self.rd.read_manifest() or {"speeds": []})["speeds"] if s.get("state") == "analysed"
                     and (s.get("result") or {}).get("wall_s")]
        if cur is None:
            return None
        per = (sum(s["result"]["wall_s"] for s in done_wall) / len(done_wall)) if done_wall else None
        if per is None and runs:
            r = runs[0]
            per = (r.get("wall_s") or 0) + cur
        return cur + (pending * per if per else 0.0)

    def write_status(self, state="running"):
        speeds = self._speeds_view()
        runs = []
        for i, sr in sorted(self.active.items()):
            p = dict(sr.progress)
            p.update(speed_index=i, U=speeds[i]["U"] if i < len(speeds) else None, wall_s=sr.wall_s())
            runs.append(p)
        first = runs[0] if runs else {}
        st = {"state": state, "pid": os.getpid(), "speed_index": first.get("speed_index"), "speed_U": first.get("U"),
              "phase": first.get("phase"), "t_sim": first.get("t_sim"), "t_end": first.get("t_end"),
              "exchanges": first.get("exchanges"), "wall_s": self.wall_prior + self.clock() - self.t0,
              "eta_s": self._eta(runs, speeds), "retries": first.get("retries", 0), "heartbeat": time.time(),
              "message": first.get("message") or self.message, "speeds": speeds, "runs": runs}
        FCASE.write_json_atomic(self.rd.status_path, st)

    def _status_loop(self):
        while not self._stop.wait(self.status_every):
            if self.rd.cancel_path.exists() and not self.cancelled.is_set():
                self.cancel()
            try:
                self.write_status()
            except Exception:  # noqa: BLE001 - a status that cannot be written must not stop the run
                pass

    def cancel(self):
        self.cancelled.set()
        for sr in list(self.active.values()):
            sr.cancel()

    # -- one speed
    def _settings(self, m, entry, par):
        s = dict(m.get("settings") or {})
        s.update(entry.get("settings") or {})
        if s.get("cores") and par > 1:
            s["n_procs"] = max(1, int(s["cores"]) // par)
        elif s.get("cores"):
            s.setdefault("n_procs", int(s["cores"]))
        tl = s.get("total_time_limit_s")
        s["deadline_ts"] = self._deadline if tl else None
        return s

    def _make_runner(self, case, settings):
        if self.runner_factory is not None:
            return self.runner_factory(case, settings, self.det)
        return FCASE.FoamRunner(case, {**FCASE.DEFAULTS, **settings}, self.det)

    def _run_speed(self, i, m, par):
        entry = m["speeds"][i]
        if entry.get("state") in ("analysed", "failed"):
            return
        sdir = self.rd.path / entry.get("dir", f"speed_{i}")
        case = Path(entry["case"]) if entry.get("case") else sdir / "case"
        settings = self._settings(m, entry, par)
        slot_root = settings.get("slot_root")
        if self._deadline_passed(settings):
            return  # (left pending: the total time is used up)
        try:
            if settings.get("prepare", True) and not (sdir / "prepared.flag").exists():
                FCASE.prepare_coupled_case(case, self.model, settings, self.det)
                sdir.mkdir(parents=True, exist_ok=True)
                (sdir / "prepared.flag").write_text("1")
            sr = FCASE.SpeedRun(case, self.model, settings, self.det, sdir, runner=self._make_runner(case, settings))
        except Exception as e:  # noqa: BLE001
            self.rd.update_speed(i, state="failed", result={"ok": False, "error": f"could not set up the run: {e}"})
            return
        self.active[i] = sr
        if self.cancelled.is_set():
            sr.cancel()
        try:
            self.rd.update_speed(i, state="phaseA")
            if not (sdir / "phaseA.json").exists():
                try:
                    with foam_slot.acquire(foam_slot.HEAVY, root=slot_root, abort=self.cancelled.is_set, label=f"flutter A {i}"):
                        ok = sr.phase_a()
                except foam_slot.SlotAborted:
                    sr._set("cancelled", "cancelled while waiting for the computer")
                    ok = False
            else:
                ok = True
            state = sr.state
            if ok:
                self.rd.update_speed(i, state="phaseB")
                try:
                    with foam_slot.acquire(foam_slot.COUPLED, root=slot_root, abort=self.cancelled.is_set, label=f"flutter B {i}"):
                        state = sr.phase_b()
                except foam_slot.SlotAborted:
                    sr._set("cancelled", "cancelled while waiting for the computer")
                    state = "cancelled"
            self._finish(i, entry, sr, state, m)
        except Exception as e:  # noqa: BLE001
            self.rd.update_speed(i, state="failed", result={"ok": False, "error": str(e)})
        finally:
            self.active.pop(i, None)

    def _deadline_passed(self, settings):
        return bool(settings.get("deadline_ts")) and self.clock() > settings["deadline_ts"]

    def _finish(self, i, entry, sr, state, m):
        if state in ("done", "partial"):
            t, q = sr.series()
            hints = (self.model.omega / (2 * math.pi)).tolist()
            result = {"U": entry.get("U"), "partial": state == "partial", "wall_s": sr.wall_s(), "retries": sr.retries,
                      "n_exchanges": int(len(t)), "history": str(Path(entry.get("dir", f"speed_{i}")) / "history.jsonl"),
                      "message": sr.message}
            try:
                result.update(self.analyse_fn(t, q, hints))
                result["ok"] = True
                self.rd.update_speed(i, state="analysed", result=result)
            except Exception as e:  # noqa: BLE001
                result.update(ok=False, error=str(e))
                self.rd.update_speed(i, state="failed", result=result)
        else:
            self.rd.update_speed(i, state=state, result={"ok": False, "error": sr.message, "wall_s": sr.wall_s(),
                                                         "retries": sr.retries})

    # -- all speeds
    def run(self) -> int:
        """Run (or resume) every speed of the manifest. Returns the exit code (0 also when a speed failed: see the status)."""
        rd = self.rd
        rd.path.mkdir(parents=True, exist_ok=True)
        prev = rd.attach()
        if prev.get("alive") and int(prev.get("pid") or 0) != os.getpid():
            self.message = "another controller is running this folder"
            return 3
        rd.pid_path.write_text(str(os.getpid()))
        m = rd.read_manifest()
        status_thread = None
        try:
            if m is None:
                raise RuntimeError("manifest.json is missing or unreadable")
            self.wall_prior = float(m.get("wall_s", 0.0))
            tl = (m.get("settings") or {}).get("total_time_limit_s")
            self._deadline = self.t0 + max(float(tl) - self.wall_prior, 0.0) if tl else None
            if self.model is None:
                self.model = FC.load_model(rd.path / m.get("model", "model.npz"))
            if self.det is None and self.runner_factory is None:
                from . import runner
                self.det = runner.detect()
                if not self.det.get("available") or self.det.get("backend") != "windows":
                    raise RuntimeError("OpenFOAM (native Windows build) is not available")
            self.write_status()
            status_thread = threading.Thread(target=self._status_loop, daemon=True)
            status_thread.start()
            if rd.cancel_path.exists():
                self.cancel()
            par = max(1, int((m.get("settings") or {}).get("parallel_runs", 1)))
            todo = list(range(len(m["speeds"])))
            lock = threading.Lock()

            def worker():
                while not self.cancelled.is_set():
                    with lock:
                        if not todo:
                            return
                        i = todo.pop(0)
                    self._run_speed(i, rd.read_manifest(), par)

            threads = [threading.Thread(target=worker) for _ in range(min(par, len(todo)) or 0)]
            for th in threads:
                th.start()
            for th in threads:
                th.join()
            self.final = self._final_state()
        except Exception as e:  # noqa: BLE001
            self.message = str(e)
            self.final = "failed"
        finally:
            self._stop.set()
            if status_thread is not None:
                status_thread.join(2.0)
            try:
                m2 = rd.read_manifest()
                if m2 is not None:
                    m2["wall_s"] = self.wall_prior + self.clock() - self.t0
                    rd.write_manifest(m2)
                self.write_status(self.final or "failed")
            except Exception:  # noqa: BLE001
                pass
            rd.pid_path.unlink(missing_ok=True)
        return 0

    def _final_state(self):
        speeds = (self.rd.read_manifest() or {"speeds": []})["speeds"]
        states = [s.get("state") for s in speeds]
        if self.cancelled.is_set() or "cancelled" in states:
            return "cancelled"
        if "pending" in states or "phaseA" in states or "phaseB" in states:
            return "partial"  # (the total time ran out)
        if "stalled" in states:
            return "stalled"
        if "failed" in states:
            return "failed"
        if any((s.get("result") or {}).get("partial") for s in speeds):
            return "partial"
        return "done"


def main(argv=None) -> int:
    argv = sys.argv if argv is None else argv
    if len(argv) < 2:
        print("usage: python -m simwb.solvers.openfoam.flutter_run <run_dir>", file=sys.stderr)
        return 2
    return Controller(argv[1]).run()


if __name__ == "__main__":
    sys.exit(main())
