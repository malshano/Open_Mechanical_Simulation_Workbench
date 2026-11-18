"""Background jobs with progress, logs and cancellation."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Cancelled(Exception):
    pass


class Job:
    def __init__(self, kind: str, project: str):
        self.id = uuid.uuid4().hex[:10]
        self.kind = kind
        self.project = project
        self.status = "running"
        self.progress = 0.0
        self.message = "Starting"
        self.log: list[str] = []
        self.result = None
        self.error = None
        self.phase = None  # (progress from, progress to, label) of a step in a composite job
        self.started = time.time()
        self.finished = None
        self.proc: subprocess.Popen | None = None
        self.cancel_requested = False
        self.extra = {}  # e.g. residual history
        self._done = threading.Event()

    def to_dict(self, log_tail=200):
        return {
            "id": self.id, "kind": self.kind, "project": self.project, "status": self.status,
            "progress": round(self.progress, 3), "message": self.message, "error": self.error,
            "log": self.log[-log_tail:], "elapsed": round((self.finished or time.time()) - self.started, 1),
            "extra": copy.deepcopy(self.extra),  # callers may reshape it; the job keeps its history
            **({"result": copy.deepcopy(self.result)} if self.status == "done" and self.kind == "check" else {}),
        }

    def wait(self, timeout=None) -> bool:
        return self._done.wait(timeout)

    def check_cancel(self):
        if self.cancel_requested:
            raise Cancelled()


class JobManager:
    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self.listeners = []
        self._lock = threading.Lock()

    def emit(self, event: dict):
        for fn in list(self.listeners):
            try:
                fn(event)
            except Exception:  # noqa: BLE001
                pass

    def update(self, job: Job, progress=None, message=None, log=None, quiet=False):
        ph = getattr(job, "phase", None)
        if progress is not None:
            job.progress = float(progress) if not ph else ph[0] + (ph[1] - ph[0]) * float(progress)
        if message is not None:
            job.message = f"{ph[2]}: {message}" if ph and ph[2] else message
        if log:
            job.log.append(log)
            if len(job.log) > 5000:
                del job.log[:1000]
        if not quiet:
            self.emit({"type": "job", "job": job.to_dict(log_tail=0), "log": log})

    def start(self, kind: str, project: str, fn, *args, **kw) -> Job:
        with self._lock:
            for j in self.jobs.values():
                if j.project == project and j.status == "running":
                    what = {"prepare": "geometry import", "mesh": "mesh generation", "solve": "solver run",
                            "check": "mesh check"}.get(j.kind, j.kind)
                    raise RuntimeError(f"Please wait: a {what} is still running for this project (or cancel it).")
        job = Job(kind, project)
        self.jobs[job.id] = job

        def runner():
            try:
                job.result = fn(job, *args, **kw)
                job.status = "done"
                job.progress = 1.0
            except Cancelled:
                job.status = "cancelled"
                job.message = "Cancelled" + (f" - {job.extra['final_message']}" if job.extra.get("final_message") else "")
            except Exception as e:  # noqa: BLE001
                job.status = "failed"
                job.error = str(e)
                job.message = f"Failed: {e}"
                job.log.append(traceback.format_exc())
            finally:
                job.finished = time.time()
                job._done.set()
                self.emit({"type": "job", "job": job.to_dict(log_tail=0), "final": True})
                self.emit({"type": "project_changed", "project": project})

        threading.Thread(target=runner, daemon=True).start()
        self.emit({"type": "job", "job": job.to_dict(log_tail=0)})
        return job

    def cancel(self, job_id: str):
        j = self.jobs.get(job_id)
        if not j:
            return False
        j.cancel_requested = True
        if j.proc and j.proc.poll() is None:
            try:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(j.proc.pid)], capture_output=True)
                else:
                    j.proc.kill()
            except Exception:  # noqa: BLE001
                pass
        return True

    def latest(self, project: str, kind: str | None = None):
        js = [j for j in self.jobs.values() if j.project == project and (kind is None or j.kind == kind)]
        return max(js, key=lambda j: j.started) if js else None


def run_gmsh_task(jm: JobManager, job: Job, task: str, payload: dict, workdir: Path, p0=0.0, p1=1.0):
    """Run a gmsh_worker task in a subprocess, forwarding progress."""
    workdir.mkdir(parents=True, exist_ok=True)
    jp = workdir / f"_{task}_job.json"
    jp.write_text(json.dumps(payload))
    from .memlimit import LimitedPopen

    lp = LimitedPopen([sys.executable, "-u", "-m", "simwb.gmsh_worker", task, str(jp)], cwd=ROOT,
                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    try:
        return _follow(jm, job, task, lp, p0, p1)
    finally:
        lp.close()  # nothing of the worker survives (Windows job object)


def _follow(jm, job, task, lp, p0, p1):
    proc = lp.proc
    job.proc = proc
    result, error = None, None
    for line in proc.stdout:
        line = line.rstrip()
        if line.startswith("PROGRESS "):
            _, frac, msg = line.split(" ", 2)
            jm.update(job, p0 + (p1 - p0) * float(frac), msg, log=msg)
        elif line.startswith("RESULT "):
            result = json.loads(line[7:])
        elif line.startswith("ERROR "):
            error = line[6:]
        elif line.strip() and not line.startswith(("Info", "Warning : ", "\x1b")):
            job.log.append(line)
    proc.wait()
    job.proc = None
    job.check_cancel()
    if proc.returncode != 0 and lp.hit_limit():
        raise RuntimeError(
            f"The {'mesh' if task == 'mesh' else task} needed more than {lp.limit / 2 ** 30:.1f} GB of memory and was "
            "stopped to protect the computer. Use a coarser preset or a larger minimum element size "
            "(or set SIMWB_MEM_LIMIT_GB if this machine can afford more).")
    if proc.returncode != 0 or error:
        raise RuntimeError(error or f"{task} failed (exit code {proc.returncode})")
    return result
