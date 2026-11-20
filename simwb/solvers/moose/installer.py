"""Find a MOOSE application, or set up the workbench's own copy on request (Linux and macOS only).

MOOSE has no native Windows build: its official Windows route is WSL, which the workbench never uses, so on Windows
the engine is hidden (see ``supported``) unless the user points SIMWB_MOOSE at an executable they built themselves.

The own copy is a per-user conda environment in <workbench>/moose made by micromamba (pinned and checksummed below)
from INL's public conda channel (https://conda.software.inl.gov/public) with conda-forge for the rest: the pre-built
``moose`` package (LGPL-2.1, about 400 MB, the fully featured binary). No admin rights, no WSL, no Docker. Deleting
the folder removes it.

VERIFIED (2026-11-20, from the channel's repodata.json): the channel holds package ``moose`` for linux-64 and osx-arm64
(not osx-64, not linux-aarch64). The page https://mooseframework.inl.gov/getting_started/installation/conda.html was
not reachable here (HTTP 403); its source in the idaholab/moose repository gives the channel URL and the commands
``conda create -n moose moose`` and ``moose --help``.
UNVERIFIED: that a micromamba environment (without ``conda activate``) runs the binary, and the exact name of the
executable in the environment (``moose`` per the documentation; ``*-opt`` names are searched too). The installer
therefore ends with a search and fails with a clear message if nothing runnable is found. It has not been run end to
end (no Linux/macOS machine and a 400 MB download).

Run directly:  python -m simwb.solvers.moose.installer
"""
from __future__ import annotations

import hashlib
import os
import platform as _platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

BUNDLED_DIR = Path(__file__).resolve().parents[3] / "moose"
JOB_PROJECT = "_moose"
INL_CHANNEL = "https://conda.software.inl.gov/public"
MOOSE_SPEC = "moose"
MIN_FREE_GB = 4.0
WINDOWS_REASON = ("MOOSE has no native Windows build; its official Windows route is WSL, which the workbench does "
                  "not use")
# micromamba 2.10.0 (conda-forge), checksums computed from the downloaded packages. Only the subdirs for which the INL
# channel carries the `moose` package are listed.
MICROMAMBA = {
    "linux-64": ("https://conda.anaconda.org/conda-forge/linux-64/micromamba-2.10.0-0.tar.bz2",
                 "9a97976244929bbae28fb1fae504e74a3e7e6d1932870e3a03321f01b7fbd07b"),
    "osx-arm64": ("https://conda.anaconda.org/conda-forge/osx-arm64/micromamba-2.10.0-0.tar.bz2",
                  "acef4eaab19ec2174ad5a449e5aad981ca326270db819502ff096a92fc31caa6"),
}
_cache: dict = {}


def install_dir() -> Path:
    return Path(os.environ.get("SIMWB_MOOSE_DIR") or BUNDLED_DIR)


def supported(platform: str | None = None) -> tuple[bool, str]:
    """(offered, reason): offered natively on Linux and macOS only; the reason says why not."""
    p = platform or sys.platform
    if p.startswith("linux") or p == "darwin":
        return True, ""
    if p.startswith("win") or p == "cygwin":
        return False, WINDOWS_REASON + "."
    return False, f"MOOSE is offered on Linux and macOS only (this system: {p})."


def conda_subdir(system: str | None = None, machine: str | None = None) -> str | None:
    """The conda subdir with a `moose` package for this computer, or None."""
    s = (system or _platform.system()).lower()
    m = (machine or _platform.machine()).lower()
    if s == "linux" and m in ("x86_64", "amd64"):
        return "linux-64"
    if s == "darwin" and m in ("arm64", "aarch64"):
        return "osx-arm64"
    return None


def _is_exe(p: Path) -> bool:
    try:
        return p.is_file() and os.access(p, os.X_OK)
    except OSError:
        return False


def _opt_in(d: Path):
    """Executables named like MOOSE applications directly in a folder: moose-opt, <app>-opt, moose."""
    try:
        names = sorted(x for x in d.iterdir() if x.is_file())
    except OSError:
        return
    for x in names:
        if x.name.endswith("-opt") or x.name.endswith("-opt.exe"):
            yield x
    for n in ("moose", "moose.exe"):
        if (d / n).is_file():
            yield d / n


def _candidates():
    env = os.environ.get("SIMWB_MOOSE")
    if env:
        yield Path(env)
        return  # an explicit setting is the only choice, also on Windows (a user's own build)
    ok, _ = supported()
    if not ok:
        return
    d = install_dir()
    for sub in ("env/bin", "bin", ""):
        yield from _opt_in(d / sub)
    for part in os.environ.get("PATH", "").split(os.pathsep):
        if part:
            yield from (x for x in _opt_in(Path(part)) if x.name != "moose")  # a bare `moose` on PATH is too vague


def find_moose() -> Path | None:
    return next((c for c in _candidates() if _is_exe(c)), None)


def detect(refresh: bool = False) -> dict:
    """{"installed", "path", "version", "supported", "reason", "installable", "source", "help"}."""
    if not refresh and _cache.get("info"):
        return _cache["info"]
    ok, reason = supported()
    exe = find_moose()
    info = {"installed": False, "path": None, "version": None, "supported": ok, "reason": reason,
            "installable": ok and conda_subdir() is not None,
            "help": ("Install it from Settings (one download, about 400 MB), or put a MOOSE application "
                     "(*-opt) on the PATH / set SIMWB_MOOSE.") if ok else reason}
    if ok and not info["installable"]:
        info["help"] = ("The workbench's MOOSE setup covers Linux x86-64 and macOS Apple silicon; here put a MOOSE "
                        "application (*-opt) on the PATH or set SIMWB_MOOSE.")
    if exe:
        info.update(installed=True, path=str(exe), version=_version(exe), supported=True, reason="", help="",
                    source="workbench copy" if install_dir() in exe.parents else "found on this computer")
    _cache["info"] = info
    return info


def run_env(exe: Path | str) -> dict:
    """Environment for running `exe`: when it sits in a conda environment, that environment's bin first."""
    env = dict(os.environ)
    p = Path(exe)
    for parent in p.parents:
        if (parent / "conda-meta").is_dir():
            env["PATH"] = os.pathsep.join([str(parent / "bin"), env.get("PATH", "")])
            env["CONDA_PREFIX"] = str(parent)
            break
    return env


def _version(exe: Path) -> str | None:
    """Best effort: MOOSE applications print their version for --version; anything unexpected gives None."""
    try:
        out = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=30,
                             env=run_env(exe), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        text = (out.stdout or "") + "\n" + (out.stderr or "")
        m = re.search(r"[Vv]ersion[^0-9]{0,40}([0-9][0-9A-Za-z._+-]*)", text)
        return m.group(1) if m else None
    except (OSError, subprocess.SubprocessError):
        return None


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
    """Set up <workbench>/moose: micromamba (pinned, checksummed) creates an environment with INL's `moose`."""
    ok, reason = supported()
    if not ok:
        raise RuntimeError(reason + " MOOSE is not offered on this system.")
    sub = conda_subdir()
    if sub is None or sub not in MICROMAMBA:
        raise RuntimeError(f"INL's conda channel has the MOOSE package for Linux x86-64 and macOS Apple silicon only "
                           f"({_platform.system()} {_platform.machine()} is not covered). Install a MOOSE "
                           "application yourself and put it on the PATH or set SIMWB_MOOSE.")
    dest = install_dir()
    dest.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(dest).free / 2**30
    if free < MIN_FREE_GB:
        raise RuntimeError(f"Only {free:.1f} GB free on this drive; MOOSE needs about {MIN_FREE_GB:.0f} GB.")
    tmp = Path(tempfile.mkdtemp(prefix="simwb_moose_"))
    try:
        url, sha = MICROMAMBA[sub]
        jm.update(job, 0.02, "Downloading the installer helper (micromamba, conda-forge)")
        pkg = tmp / "micromamba.tar.bz2"
        _download(url, pkg, sha, job, jm)
        with tarfile.open(pkg) as tf:
            tf.extractall(tmp / "mm", filter="data")
        mm = next((tmp / "mm").rglob("micromamba"))
        shutil.copy2(mm, dest / "micromamba")
        os.chmod(dest / "micromamba", 0o755)
        jm.update(job, 0.12, "Installing MOOSE from INL's conda channel (about 400 MB, several minutes)")
        cmd = [str(dest / "micromamba"), "create", "-y", "-p", str(dest / "env"), "--override-channels",
               "-c", INL_CHANNEL, "-c", "conda-forge", MOOSE_SPEC]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                env={**os.environ, "MAMBA_ROOT_PREFIX": str(dest / "root")})
        job.proc = proc
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                jm.update(job, None, None, log=line, quiet=True)
            job.check_cancel()
        if proc.wait() != 0:
            raise RuntimeError("micromamba could not install MOOSE - see the log.")
        job.proc = None
        exe = find_moose()
        if exe is None:
            raise RuntimeError("MOOSE was installed but no runnable MOOSE application (moose, *-opt) was found in "
                               f"{dest / 'env' / 'bin'}. Set SIMWB_MOOSE to the executable.")
        info = detect(refresh=True)
        jm.update(job, 0.98, f"MOOSE {info.get('version') or ''} ready", log=f"moose: {exe}")
        shutil.rmtree(dest / "root", ignore_errors=True)  # the package cache
        return info
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def running_job(jm):
    for j in jm.jobs.values():
        if j.project == JOB_PROJECT and j.status == "running":
            return j
    return None


def start(jm):
    ok, reason = supported()
    if not ok:
        raise RuntimeError(reason + " MOOSE is not offered on this system.")
    return running_job(jm) or jm.start("install", JOB_PROJECT, install, jm)


def main() -> int:
    """Install from the command line: python -m simwb.solvers.moose.installer (progress is printed)."""
    class _Job:
        progress, proc, message = 0.0, None, ""

        def check_cancel(self):
            pass

    class _JM:
        def update(self, job, progress=None, message=None, log=None, **kw):
            if progress is not None:
                job.progress = progress
            if log:
                print(("    " + log[:160]) if kw.get("quiet") else "  " + log, flush=True)
            if message and message != job.message:
                job.message = message
                print(f"[{job.progress * 100:3.0f} %] {message}", flush=True)

    ok, reason = supported()
    if not ok:
        print(reason + " MOOSE is not offered on this system.")
        return 0
    try:
        info = install(_Job(), _JM())
    except Exception as e:  # noqa: BLE001
        print(f"failed: {e}")
        return 1
    print(f"done: MOOSE {info.get('version')} at {info.get('path')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
