"""Run the openCFS program on a prepared case folder."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

from . import installer

JOB = "job"


def run_cfs(workdir, xml_name: str, mesh: str, on_proc=None, log=print, job: str = JOB, threads: int | None = None,
            timeout: float | None = None, on_step=None) -> dict:
    """Run ``cfs -m <mesh> -p <xml_name> <job>`` in workdir (the files written there by the caller).

    Returns {"results_gmsh": path of the ASCII Gmsh result, "info_xml": path, "log": text, "frequencies": [...],
    "eigenfrequencies": [...]}. Raises RuntimeError with openCFS's own message when it stops with an error.
    VERIFIED command line (cfs -h of the 2026W release): -m mesh file, -p XML parameter file, the last argument is the
    name of the simulation run; results go to <workdir>/results_gmsh/<job>.msh (and results_hdf5/<job>.cfs when the
    XML asks for it), the run log to <workdir>/<job>.info.xml.
    """
    info = installer.detect()
    if not info["available"]:
        raise RuntimeError("openCFS is not installed: install it from Settings (one download), or put cfs on the "
                           "PATH / set SIMWB_OPENCFS.")
    exe = Path(info["exe"])
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    for d in ("results_gmsh", "results_hdf5", "history"):
        shutil.rmtree(workdir / d, ignore_errors=True)
    for f in workdir.glob(job + ".info.xml"):
        f.unlink()
    env = installer._run_env(exe)
    n = int(threads or max(1, (os.cpu_count() or 2) // 2))
    env["CFS_NUM_THREADS"] = str(n)
    cmd = [str(exe), "--noColor", "-t", str(n), "-m", mesh, "-p", xml_name, job]
    log(f"$ cfs -m {mesh} -p {xml_name} {job}   (openCFS {info.get('version') or '?'}, {n} threads)")
    out = []
    proc = subprocess.Popen(cmd, cwd=workdir, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    timed_out = []
    timer = None
    if timeout:
        def _kill():
            timed_out.append(True)
            proc.kill()

        timer = threading.Timer(float(timeout), _kill)
        timer.daemon = True
        timer.start()
    if on_proc:
        on_proc(proc)
    try:
        for line in proc.stdout:
            line = line.rstrip()
            out.append(line)
            if not line.strip() or line.startswith("WARN parsing") or line.lstrip().startswith("-> 'unable to open"):
                continue  # (the release's schema check cannot load one schema file: a harmless warning every run)
            log(line)
            m = re.search(r"step (\d+) \((\d+)/(\d+)\)", line)
            if m and on_step:
                on_step(int(m.group(2)), int(m.group(3)))
        rc = proc.wait()
    finally:
        if timer:
            timer.cancel()
    text = "\n".join(out)
    res = workdir / "results_gmsh" / f"{job}.msh"
    if timed_out:
        raise RuntimeError(f"openCFS did not finish within {timeout / 60:.0f} minutes and was stopped.")
    err = re.search(r">> Error:?\s*\n?(.*?)(?:\n\s*\n|\nIn file|\Z)", text, re.S)
    if rc != 0 or err or not res.exists():
        why = (err.group(1).strip() if err else "") or f"exit code {rc}"
        raise RuntimeError(f"openCFS stopped: {why}")
    return {"results_gmsh": res, "info_xml": workdir / f"{job}.info.xml", "log": text,
            "frequencies": [float(x) for x in re.findall(r"Harmonic step \d+ \(\d+/\d+\) frequency\s+([0-9.eE+-]+)",
                                                         text)],
            "eigenfrequencies": parse_eigenfrequencies(text)}


def parse_eigenfrequencies(text: str) -> list:
    """The table 'Frequency in Hz | Errorbound' openCFS prints for an eigenfrequency analysis (VERIFIED format)."""
    out = []
    on = False
    for line in text.splitlines():
        if "Frequency in Hz" in line and "Errorbound" in line:
            on = True
            continue
        if on:
            m = re.match(r"^\s*([0-9.]+(?:[eE][+-]?\d+)?)\s*\|\s*([0-9.eE+-]+)\s*$", line)
            if m:
                out.append(float(m.group(1)))
            elif line.strip() and not set(line.strip()) <= {"-"}:
                break
    return out
