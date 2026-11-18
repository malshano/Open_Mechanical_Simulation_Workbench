"""CalculiX for the workbench: find an installed ``ccx``, or set up the workbench's own copy on request.

The own copy is a small conda environment in <workbench>/calculix, made by micromamba (a single 4.6 MB program from
conda-forge, pinned and checksummed below) from the conda-forge ``calculix`` package: native Windows ccx.exe with its
libraries, no admin rights, no system changes, nothing else to install. Deleting the folder removes it.

CalculiX is GPL-2.0-or-later software (Guido Dhondt): downloaded by the user on request, never shipped in this
project. Run directly:  python -m simwb.solvers.calculix.installer
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

BUNDLED_DIR = Path(__file__).resolve().parents[3] / "calculix"
JOB_PROJECT = "_calculix"
MICROMAMBA_URL = "https://conda.anaconda.org/conda-forge/win-64/micromamba-2.10.0-0.tar.bz2"
MICROMAMBA_SHA256 = "6283f25f8142606d86e81a43c7103b5d4f1d5c2027e3f9d7326eff23b33930d8"
CCX_SPEC = "calculix=2.23"
MIN_FREE_GB = 2.0
_cache: dict = {}


def install_dir() -> Path:
    return Path(os.environ.get("SIMWB_CALCULIX_DIR") or BUNDLED_DIR)


def supported() -> bool:
    return sys.platform == "win32"


def _candidates():
    env = os.environ.get("SIMWB_CCX")
    if env:
        yield Path(env)
    d = install_dir() / "env"
    for sub in ("Library/bin", "bin", "Scripts", ""):
        yield d / sub / ("ccx.exe" if sys.platform == "win32" else "ccx")
    for name in ("ccx", "ccx.exe") + tuple(f"ccx_{v}" for v in ("2.23", "2.22", "2.21", "2.20")):
        w = shutil.which(name)
        if w:
            yield Path(w)


def find_ccx() -> Path | None:
    return next((c for c in _candidates() if c.is_file()), None)


def detect(refresh: bool = False) -> dict:
    """{"available", "exe", "version", "source", "installable", "help"}."""
    if not refresh and _cache.get("info"):
        return _cache["info"]
    exe = find_ccx()
    info = {"available": False, "exe": None, "version": None, "installable": supported(),
            "help": "Install it from Settings (one download, about 150 MB), or put ccx on the PATH / set SIMWB_CCX."}
    if exe:
        info.update(available=True, exe=str(exe), version=_version(exe),
                    source="workbench copy" if install_dir() in exe.parents else "found on this computer", help="")
    _cache["info"] = info
    return info


def _version(exe: Path) -> str | None:
    try:
        out = subprocess.run([str(exe), "-v"], capture_output=True, text=True, timeout=30, env=_env(exe),
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        m = re.search(r"[Vv]ersion\s+([0-9][0-9.]*)", out)
        return m.group(1) if m else None
    except (OSError, subprocess.SubprocessError):
        return None


def _env(exe: Path) -> dict:
    """The conda environment's DLLs are next to ccx.exe (Library/bin): put them first on the PATH."""
    env = dict(os.environ)
    root = exe.parent.parent  # <env>/Library: libarpack.dll is in mingw-w64/bin, the rest next to ccx.exe
    env["PATH"] = os.pathsep.join([str(exe.parent), str(root / "mingw-w64" / "bin"), str(root / "bin"),
                                   env.get("PATH", "")])
    return env


def _download(url: str, dest: Path, sha256: str, job, jm):
    req = urllib.request.Request(url, headers={"User-Agent": "SimWorkbench"})
    h = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            h.update(chunk)
            done += len(chunk)
            jm.update(job, 0.02 + 0.08 * (done / total if total else 0.5), f"Downloading the installer helper "
                      f"({done / 2**20:.1f} MB)")
            job.check_cancel()
    if h.hexdigest() != sha256:
        raise RuntimeError("The downloaded helper program does not match its checksum - download stopped.")


def install(job, jm) -> dict:
    """Set up <workbench>/calculix: micromamba (pinned, checksummed) creates a conda-forge environment with ccx."""
    if not supported():
        raise RuntimeError("The built-in CalculiX setup is for Windows; elsewhere install CalculiX with your package "
                           "manager (it is found on the PATH) or set SIMWB_CCX.")
    dest = install_dir()
    dest.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(dest).free / 2**30
    if free < MIN_FREE_GB:
        raise RuntimeError(f"Only {free:.1f} GB free on this drive; CalculiX needs about {MIN_FREE_GB:.0f} GB.")
    tmp = Path(tempfile.mkdtemp(prefix="simwb_ccx_"))
    try:
        jm.update(job, 0.02, "Downloading the installer helper (micromamba, conda-forge)")
        pkg = tmp / "micromamba.tar.bz2"
        _download(MICROMAMBA_URL, pkg, MICROMAMBA_SHA256, job, jm)
        with tarfile.open(pkg) as tf:
            tf.extractall(tmp / "mm", filter="data")
        mm = next((tmp / "mm").rglob("micromamba.exe"))
        shutil.copy2(mm, dest / "micromamba.exe")
        jm.update(job, 0.12, "Installing CalculiX from conda-forge (about 150 MB)")
        env_dir = dest / "env"
        cmd = [str(dest / "micromamba.exe"), "create", "-y", "-p", str(env_dir), "-c", "conda-forge",
               "--override-channels", CCX_SPEC]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                env={**os.environ, "MAMBA_ROOT_PREFIX": str(dest / "root")},
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        job.proc = proc
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                jm.update(job, None, None, log=line, quiet=True)
            job.check_cancel()
        if proc.wait() != 0:
            raise RuntimeError("micromamba could not install CalculiX - see the log.")
        job.proc = None
        exe = find_ccx()
        if exe is None:
            raise RuntimeError("CalculiX was installed but ccx.exe was not found in it.")
        info = detect(refresh=True)
        jm.update(job, 0.98, f"CalculiX {info.get('version') or ''} ready", log=f"ccx: {exe}")
        shutil.rmtree(dest / "root", ignore_errors=True)  # the package cache (the environment keeps what it needs)
        return info
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def running_job(jm):
    for j in jm.jobs.values():
        if j.project == JOB_PROJECT and j.status == "running":
            return j
    return None


def start(jm):
    return running_job(jm) or jm.start("install", JOB_PROJECT, install, jm)


def main() -> int:
    """Install from the command line: python -m simwb.solvers.calculix.installer (progress is printed)."""
    class _Job:
        progress, proc, message = 0.0, None, ""

        def check_cancel(self):
            pass

    class _JM:
        def update(self, job, progress=None, message=None, log=None, **kw):
            if progress is not None:
                job.progress = progress
            if log and not kw.get("quiet"):
                print("  " + log, flush=True)
            elif log:
                print("    " + log[:160], flush=True)
            if message and message != job.message:
                job.message = message
                print(f"[{job.progress * 100:3.0f} %] {message}", flush=True)

    if not supported():
        print("The built-in CalculiX setup is for Windows; elsewhere install CalculiX with your package manager.")
        return 0
    try:
        info = install(_Job(), _JM())
    except Exception as e:  # noqa: BLE001
        print(f"failed: {e}")
        return 1
    print(f"done: CalculiX {info.get('version')} at {info.get('exe')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
