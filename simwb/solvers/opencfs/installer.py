"""openCFS for the workbench: find an installed ``cfs``, or unpack the workbench's own copy on request.

openCFS (https://opencfs.org, Verein zur Foerderung der Software openCFS, MIT licence) publishes a portable Windows
build: one zip with bin/cfs.exe, its DLLs (including the Visual C++ and Intel runtimes), the XML schemas and the
licences. Nothing to install: no admin rights, no WSL, no registry. The workbench downloads that zip once into
<workbench>/opencfs, checks its SHA-256 against the value pinned below, and unpacks it. Deleting the folder removes it.

VERIFIED (2026-11-20): the release page lists  https://gitlab.com/api/v4/projects/12930334/packages/generic/openCFS/
2026W/CFS-2026W-Windows.zip  (also reachable as .../-/releases/2026W/downloads/CFS-Windows.zip); the server answers
Content-Length 148511870; the SHA-256 below was computed on that download by us (GitLab publishes no checksum for it);
the zip unpacks to CFS-2026W-Windows/{bin,lib,license,share} with 338 files, 470 MB; bin/cfs.exe starts and runs a
harmonic analysis from a normal (non-admin) shell. Run directly:  python -m simwb.solvers.opencfs.installer
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

BUNDLED_DIR = Path(__file__).resolve().parents[3] / "opencfs"
JOB_PROJECT = "_opencfs"
VERSION_TAG = "2026W"
URL = f"https://gitlab.com/api/v4/projects/12930334/packages/generic/openCFS/{VERSION_TAG}/CFS-{VERSION_TAG}-Windows.zip"
SHA256 = "6582e08530827cba2fe6f68a9135a389aa0e93aa69625c02f19c061b1572be96"
SIZE_BYTES = 148511870
MAX_BYTES = int(SIZE_BYTES * 1.05)  # refuse a download that is larger than the one pinned
UNPACKED_MB = 470
MIN_FREE_GB = 1.2
_cache: dict = {}


def install_dir() -> Path:
    return Path(os.environ.get("SIMWB_OPENCFS_DIR") or BUNDLED_DIR)


def supported() -> bool:
    """The portable build is a Windows zip. (Linux has a tar.gz of the same release; not wired up: untested.)"""
    return sys.platform == "win32"


def _exe_name() -> str:
    return "cfs.exe" if sys.platform == "win32" else "cfs"


def _candidates():
    env = os.environ.get("SIMWB_OPENCFS")  # a cfs executable, or the folder of an unpacked release
    if env:
        p = Path(env)
        yield p if p.is_file() else p / "bin" / _exe_name()
    d = install_dir()
    if d.is_dir():
        for sub in sorted(d.iterdir(), reverse=True):  # <workbench>/opencfs/CFS-2026W-Windows/bin/cfs.exe
            yield sub / "bin" / _exe_name()
    w = shutil.which("cfs")
    if w:
        yield Path(w)


def find_cfs() -> Path | None:
    return next((c for c in _candidates() if c.is_file()), None)


def _run_env(exe: Path) -> dict:
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([str(exe.parent), env.get("PATH", "")])  # its DLLs are next to cfs.exe
    return env


def _version(exe: Path) -> str | None:
    """'26.09' from `cfs -v`; None when the program does not identify itself as openCFS (another `cfs` on the PATH)."""
    try:
        out = subprocess.run([str(exe), "-v"], capture_output=True, text=True, timeout=30, env=_run_env(exe),
                             cwd=tempfile.gettempdir(),
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    if "openCFS" not in out:
        return None
    m = re.search(r"CFS_VERSION:\s*([0-9][0-9.]*)", out) or re.search(r"v\.\s*([0-9][0-9.]*)", out)
    return m.group(1) if m else "unknown"


def detect(refresh: bool = False) -> dict:
    """{"available", "exe", "version", "source", "installable", "help"}."""
    if not refresh and _cache.get("info"):
        return _cache["info"]
    info = {"available": False, "exe": None, "version": None, "installable": supported(), "source": None,
            "help": f"Install it from Settings (one download of about {SIZE_BYTES // 2**20} MB, {UNPACKED_MB} MB "
                    "unpacked), or put cfs on the PATH / set SIMWB_OPENCFS."}
    for exe in _candidates():
        if not exe.is_file():
            continue
        v = _version(exe)
        if v is None:
            continue
        info.update(available=True, exe=str(exe), version=v, help="",
                    source="workbench copy" if install_dir() in exe.parents else "found on this computer")
        break
    _cache["info"] = info
    return info


def _download(url: str, dest: Path, sha256: str, job, jm):
    req = urllib.request.Request(url, headers={"User-Agent": "SimWorkbench"})
    h = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        if total > MAX_BYTES:
            raise RuntimeError("The openCFS download is larger than expected - download stopped.")
        done = 0
        while chunk := r.read(1 << 20):
            done += len(chunk)
            if done > MAX_BYTES:
                raise RuntimeError("The openCFS download is larger than expected - download stopped.")
            f.write(chunk)
            h.update(chunk)
            jm.update(job, 0.02 + 0.6 * (done / (total or SIZE_BYTES)), f"Downloading openCFS ({done / 2**20:.0f} MB)")
            job.check_cancel()
    if h.hexdigest() != sha256:
        raise RuntimeError("The downloaded openCFS file does not match its checksum - download stopped, nothing "
                           "was unpacked.")


def safe_extract(zip_path: Path, dest: Path, job=None, jm=None):
    """Unpack a zip below dest, refusing any entry that would land outside it (zip-slip)."""
    root = dest.resolve()
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        for i, n in enumerate(names):
            target = (root / n).resolve()
            if root != target and root not in target.parents:
                raise RuntimeError(f"The openCFS zip holds an unsafe path ({n}) - nothing was unpacked.")
        for i, n in enumerate(names):
            z.extract(n, root)
            if jm and i % 20 == 0:
                jm.update(job, 0.65 + 0.3 * i / max(len(names), 1), "Unpacking openCFS")
                job.check_cancel()


def install(job, jm) -> dict:
    """Download the pinned portable release into <workbench>/opencfs (SHA-256 checked) and unpack it. No admin."""
    if not supported():
        raise RuntimeError("The built-in openCFS setup is for Windows; elsewhere unpack the Linux release from "
                           "opencfs.org yourself and set SIMWB_OPENCFS to its cfs executable.")
    dest = install_dir()
    dest.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(dest).free / 2**30
    if free < MIN_FREE_GB:
        raise RuntimeError(f"Only {free:.1f} GB free on this drive; openCFS needs about {MIN_FREE_GB:.1f} GB.")
    tmp = Path(tempfile.mkdtemp(prefix="simwb_cfs_", dir=dest))  # own new empty folder next to the target
    try:
        jm.update(job, 0.02, f"Downloading openCFS {VERSION_TAG} (about {SIZE_BYTES // 2**20} MB)")
        zp = tmp / "cfs.zip"
        _download(URL, zp, SHA256, job, jm)
        stage = tmp / "unpacked"
        stage.mkdir()
        safe_extract(zp, stage, job, jm)
        top = [p for p in stage.iterdir() if p.is_dir()]
        if len(top) != 1 or not (top[0] / "bin" / _exe_name()).is_file():
            raise RuntimeError("The openCFS zip does not have the expected layout (bin/cfs.exe) - nothing installed.")
        target = dest / top[0].name
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(top[0]), str(target))
        info = detect(refresh=True)
        if not info["available"]:
            raise RuntimeError("openCFS was unpacked but cfs.exe did not start (blocked by security software?).")
        jm.update(job, 0.98, f"openCFS {info.get('version') or ''} ready", log=f"cfs: {info['exe']}")
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
    """Install from the command line: python -m simwb.solvers.opencfs.installer (progress is printed)."""
    class _Job:
        progress, proc, message = 0.0, None, ""

        def check_cancel(self):
            pass

    class _JM:
        def update(self, job, progress=None, message=None, log=None, **kw):
            if progress is not None:
                job.progress = progress
            if log:
                print("  " + log, flush=True)
            if message and message != job.message:
                job.message = message
                print(f"[{job.progress * 100:3.0f} %] {message}", flush=True)

    if not supported():
        print("The built-in openCFS setup is for Windows.")
        return 0
    try:
        info = install(_Job(), _JM())
    except Exception as e:  # noqa: BLE001
        print(f"failed: {e}")
        return 1
    print(f"done: openCFS {info.get('version')} at {info.get('exe')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
