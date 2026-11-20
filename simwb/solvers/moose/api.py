"""Run a MOOSE application on an input file: a plain subprocess in the working folder, log captured line by line.

The workbench writes the input file; MOOSE (a separate program, never linked) writes Exodus (.e) and CSV files next
to it. ``run_moose`` returns their names so the results reader can open them.
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path

EXODUS_EXT = (".e", ".exo", ".exodus")
CSV_EXT = (".csv",)


class MooseError(RuntimeError):
    """MOOSE failed; ``result`` carries the same dict run_moose returns (log, returncode, ...)."""

    def __init__(self, message: str, result: dict | None = None):
        super().__init__(message)
        self.result = result or {}


def _snapshot(workdir: Path) -> dict:
    out = {}
    for p in workdir.iterdir():
        if p.is_file() and p.suffix.lower() in EXODUS_EXT + CSV_EXT:
            try:
                st = p.stat()
                out[p.name] = (st.st_mtime_ns, st.st_size)
            except OSError:
                pass
    return out


def run_moose(workdir, input_file, exe=None, on_proc=None, log=print, timeout=None, extra_args=()) -> dict:
    """Run ``<exe> -i <input_file>`` in ``workdir``.

    exe: path of the MOOSE application (default: the detected one). on_proc(proc): called with the Popen object so a
    caller can cancel. log(line): called for every output line. Returns {"returncode", "log", "exodus", "csv",
    "command", "seconds"}, where exodus / csv list the result files written or changed by this run (names relative
    to workdir). Raises MooseError when MOOSE exits non-zero (result attached) and TimeoutError after ``timeout``
    seconds (the process is killed).
    """
    from .installer import find_moose, run_env
    workdir = Path(workdir)
    if not workdir.is_dir():
        raise FileNotFoundError(f"Working folder not found: {workdir}")
    inp = Path(input_file)
    if not inp.is_absolute() and not (workdir / inp).is_file():
        raise FileNotFoundError(f"MOOSE input file not found: {workdir / inp}")
    if inp.is_absolute() and not inp.is_file():
        raise FileNotFoundError(f"MOOSE input file not found: {inp}")
    exe = Path(exe) if exe else find_moose()
    if exe is None:
        raise MooseError("No MOOSE application found: install it from Settings (Linux/macOS), put a *-opt program on "
                         "the PATH or set SIMWB_MOOSE. MOOSE has no native Windows build.")
    cmd = [str(exe), "-i", str(inp), *map(str, extra_args)]
    before = _snapshot(workdir)
    t0 = time.time()
    lines: list[str] = []
    proc = subprocess.Popen(cmd, cwd=str(workdir), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            errors="replace", env=run_env(exe),
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if on_proc:
        on_proc(proc)
    timed_out = threading.Event()
    timer = None
    if timeout:
        def _kill():
            timed_out.set()
            try:
                proc.kill()
            except OSError:
                pass
        timer = threading.Timer(float(timeout), _kill)
        timer.daemon = True
        timer.start()
    try:
        for line in proc.stdout:
            line = line.rstrip("\r\n")
            lines.append(line)
            if log:
                log(line)
        rc = proc.wait()
    finally:
        if timer:
            timer.cancel()
        if proc.poll() is None:
            proc.kill()
    after = _snapshot(workdir)
    changed = sorted(n for n, v in after.items() if before.get(n) != v)
    result = {"returncode": rc, "log": lines, "command": cmd, "seconds": time.time() - t0,
              "exodus": [n for n in changed if n.lower().endswith(EXODUS_EXT)],
              "csv": [n for n in changed if n.lower().endswith(CSV_EXT)]}
    if timed_out.is_set():
        raise TimeoutError(f"MOOSE did not finish within {timeout} s and was stopped.")
    if rc != 0:
        tail = "\n".join(lines[-8:])
        raise MooseError(f"MOOSE stopped with exit code {rc}.\n{tail}", result)
    return result
