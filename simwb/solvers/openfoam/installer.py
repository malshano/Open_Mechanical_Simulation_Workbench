"""Built-in OpenFOAM (Windows): the workbench sets up its own copy of the official ESI build.

The openfoam.com native Windows build (OpenFOAM-v2xxx-windows-mingw.exe) is installed silently into the
workbench folder (<workbench>/openfoam, or SIMWB_OPENFOAM_DIR) - automatically on the first start (the server
starts it in the background; run_workbench.bat does it during setup), so the user never installs anything.
No admin rights are needed. OpenFOAM is GPL software from OpenCFD Ltd; its licence files are kept in the tree.
The MS-MPI runtime for parallel runs (mpiexec.exe, smpd.exe, msmpi.dll; Microsoft's open-source MS-MPI, MIT
licence, from the conda-forge package) is unpacked into <workbench>/mpi - no system install, no admin rights.
Run directly:  python -m simwb.solvers.openfoam.installer
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import hashlib
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from . import runner

# The installer is pinned to one release and checked against its SHA-256 before it runs (the file is not code-signed,
# so the checksum is what proves it is the official build). A newer release: update both lines after checking it.
# SIMWB_OPENFOAM_URL (+ SIMWB_OPENFOAM_SHA256, required with it) installs another build, e.g. from a company mirror.
OPENFOAM_VERSION = "v2606"
OPENFOAM_URL = f"https://dl.openfoam.com/source/{OPENFOAM_VERSION}/OpenFOAM-{OPENFOAM_VERSION}-windows-mingw.exe"
OPENFOAM_SHA256 = "020321d58b42e1d2ad0b616cdde17f92503608edd902388fb8c73e94e9cb1e25"
MIN_FREE_GB = 4.0       # download (~0.2 GB) + installed tree (~2 GB) + headroom
MIN_SIZE = 50 * 2**20   # sanity check on the downloaded installer
JOB_PROJECT = "_openfoam"  # pseudo-project for the install job (one install at a time)
MPI_URL = "https://conda.anaconda.org/conda-forge/win-64/msmpi-10.1.1-h3502643_7.tar.bz2"
MPI_SHA256 = "1c69dc018dbb249e988b4c9ce246ff51ad090eef0138798727632b4952f754a9"


def install_dir() -> Path:
    return Path(os.environ.get("SIMWB_OPENFOAM_DIR") or runner.BUNDLED_DIR)


def supported() -> bool:
    return sys.platform == "win32"


def installer_source() -> tuple[str, str]:
    """(url, sha256) of the Windows installer to download: the pinned release, or SIMWB_OPENFOAM_URL with
    SIMWB_OPENFOAM_SHA256 (a URL without a checksum is refused: the file is run as a program)."""
    url = os.environ.get("SIMWB_OPENFOAM_URL", "").strip()
    if not url:
        return OPENFOAM_URL, OPENFOAM_SHA256
    sha = os.environ.get("SIMWB_OPENFOAM_SHA256", "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise RuntimeError("SIMWB_OPENFOAM_URL is set without SIMWB_OPENFOAM_SHA256 (the SHA-256 of that installer): "
                           "the downloaded file is run as a program, so it must be checked first.")
    return url, sha


MANUAL = f"install manually from https://dl.openfoam.com/source/{OPENFOAM_VERSION}/"


def _download(url: str, dest: Path, job, jm, what="OpenFOAM", span=(0.05, 0.45), min_size=MIN_SIZE):
    if not str(url).lower().startswith("https://"):
        raise ValueError(f"Refusing to download {what} over an unencrypted connection: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "SimWorkbench"})
    done = total = 0
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            last = -1
            while True:
                job.check_cancel()
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                mb = done >> 20
                if mb // 10 != last:  # update every 10 MB
                    last = mb // 10
                    frac = done / total if total else 0.0
                    jm.update(job, progress=span[0] + span[1] * frac,
                              message=f"Downloading {what}: {mb} / {total >> 20} MB" if total else
                              f"Downloading {what}: {mb} MB")
    except OSError as e:  # URLError/HTTPError/timeouts are OSErrors
        raise RuntimeError(f"Could not download {url}: {getattr(e, 'reason', None) or e}. Check the internet "
                           f"connection / proxy and try again, or {MANUAL}") from e
    if total and done != total:
        raise RuntimeError(f"Download incomplete ({done} of {total} bytes). Check the connection and try again.")
    if what != "OpenFOAM":
        return
    with open(dest, "rb") as f:
        head = f.read(2)
    if done < min_size or head != b"MZ":
        raise RuntimeError(f"The downloaded file is not a Windows installer. Try again later or {MANUAL}")


def verify_sha256(path: Path, sha256: str):
    """Refuse a download whose SHA-256 differs from the expected one (a changed or tampered file is never run)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest() != sha256.lower():
        raise RuntimeError(f"The downloaded OpenFOAM installer does not match its checksum (expected {sha256}, got "
                           f"{h.hexdigest()}) - it was not run. Try again later or {MANUAL}")


def _smoke_test(project_dir: Path, bin_dir: Path) -> str:
    """Run a solver binary once so a broken install fails here, not in the user's first simulation."""
    try:
        r = subprocess.run([str(Path(bin_dir) / "simpleFoam.exe"), "-help"],
                           env=runner.windows_env(Path(project_dir), Path(bin_dir)), capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f"OpenFOAM was installed but simpleFoam does not start: {e}") from e
    out = (r.stdout + r.stderr).decode("utf-8", "ignore")
    if r.returncode != 0 or "Usage" not in out:
        raise RuntimeError("OpenFOAM was installed but simpleFoam does not start:\n" + out[-1500:])
    return out


def _desktop_shortcuts() -> set:
    desk = Path(os.path.expanduser("~")) / "Desktop"
    return set(desk.glob("OpenFOAM*.lnk")) if desk.exists() else set()


def install(job, jm) -> dict:
    """Job body: download and silently install the native Windows build, then verify it runs.
    A cancelled or failed install (including a failed smoke test) leaves nothing behind."""
    if not supported():
        raise RuntimeError("Built-in installation is available on Windows only.\n" + runner.install_help())
    info = runner.detect(refresh=True)
    if info["available"]:
        jm.update(job, progress=1.0, message=f"OpenFOAM is already available ({info['backend']})")
        return info
    target = install_dir()
    target.parent.mkdir(parents=True, exist_ok=True)
    free_gb = shutil.disk_usage(target.parent).free / 2**30
    if free_gb < MIN_FREE_GB:
        raise RuntimeError(f"Not enough disk space on {target.anchor}: {free_gb:.1f} GB free, "
                           f"about {MIN_FREE_GB:.0f} GB needed.")
    if target.exists() and any(target.iterdir()):
        raise RuntimeError(f"{target} exists but holds no working OpenFOAM. Delete or rename the folder (or set "
                           "SIMWB_OPENFOAM_DIR to another folder) and try again.")

    url, sha = installer_source()
    jm.update(job, progress=0.02, message="Downloading OpenFOAM", log=f"Installer: {url}")
    tmp = Path(tempfile.mkdtemp(prefix="simwb_openfoam_"))
    shortcuts_before = _desktop_shortcuts()
    ok = False
    try:
        exe = tmp / url.rsplit("/", 1)[-1]
        _download(url, exe, job, jm)
        verify_sha256(exe, sha)
        jm.update(job, progress=0.5, message="Installing OpenFOAM (a few minutes, no admin rights needed)",
                  log=f"Installing into {target}")
        # detection ignores the tree while this marker exists (no half-installed "available")
        target.mkdir(parents=True, exist_ok=True)
        marker = target / runner.INSTALLING_MARKER
        marker.write_text("installing")
        # NSIS silent install; /D must be the last argument and must not be quoted
        job.proc = subprocess.Popen(f'"{exe}" /S /D={target}')
        while True:
            try:
                code = job.proc.wait(timeout=5)
                break
            except subprocess.TimeoutExpired:
                job.check_cancel()
                jm.update(job, progress=min(0.88, job.progress + 0.005))
        job.proc = None
        job.check_cancel()
        if code != 0:
            raise RuntimeError(f"The OpenFOAM installer exited with code {code}.")

        jm.update(job, progress=0.9, message="Checking the installation")
        found = runner.find_windows_install(roots=[target], include_installing=True)
        if not found:
            raise RuntimeError(f"Installation finished but no OpenFOAM solver was found in {target}.")
        pdir, bdir = found
        if not runner.ensure_pstream(bdir, False):
            raise RuntimeError("libPstream.dll could not be switched to the serial version.")
        _smoke_test(pdir, bdir)
        marker.unlink()
        ok = True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        if not ok:  # cancelled or failed: remove the partial tree and new shortcuts so a retry starts clean
            shutil.rmtree(target, ignore_errors=True)
            for lnk in _desktop_shortcuts() - shortcuts_before:
                lnk.unlink(missing_ok=True)
            runner.detect(refresh=True)

    try:
        install_mpi(job, jm)
    except RuntimeError as e:  # OpenFOAM still works on one core
        jm.update(job, None, log=f"Parallel runs not set up: {e}")
    info = runner.detect(refresh=True)
    if not info["available"]:
        raise RuntimeError("OpenFOAM was installed but is not detected: " + (info.get("detail") or ""))
    jm.update(job, progress=1.0, message=f"OpenFOAM installed: {info['detail']}", log="simpleFoam starts correctly")
    return info


def install_mpi(job, jm) -> dict:
    """Unpack the MS-MPI runtime into <workbench>/mpi so OpenFOAM can run on several cores (~12 MB download)."""
    if runner.find_mpi():
        return runner.detect(refresh=True)
    target = runner.BUNDLED_MPI_DIR
    tmp = Path(tempfile.mkdtemp(prefix="simwb_mpi_"))
    try:
        pkg = tmp / "msmpi.tar.bz2"
        jm.update(job, progress=0.92, message="Setting up parallel runs (MS-MPI)", log=f"MS-MPI: {MPI_URL}")
        _download(MPI_URL, pkg, job, jm, what="MS-MPI", span=(0.92, 0.05))
        if hashlib.sha256(pkg.read_bytes()).hexdigest() != MPI_SHA256:
            raise RuntimeError("The MS-MPI download is damaged (checksum mismatch). Try again later.")
        out = tmp / "mpi"
        out.mkdir()
        want = {f"Library/bin/{f}": f for f in runner.MPI_FILES} | {"info/licenses/LICENSE.txt": "LICENSE.txt"}
        with tarfile.open(pkg, "r:bz2") as tf:
            for m in tf.getmembers():
                if m.name in want and m.isfile():
                    (out / want[m.name]).write_bytes(tf.extractfile(m).read())
        if any(not (out / f).exists() for f in runner.MPI_FILES):
            raise RuntimeError("The MS-MPI package does not contain the expected files.")
        (out / "README.txt").write_text("Microsoft MPI runtime (MIT licence, https://github.com/microsoft/Microsoft-MPI),\n"
                                        "unpacked by the workbench for parallel OpenFOAM runs. Nothing is installed.\n")
        shutil.rmtree(target, ignore_errors=True)
        shutil.move(str(out), str(target))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    info = runner.detect(refresh=True)
    jm.update(job, progress=1.0, message=f"Parallel runs ready: {info.get('detail', '')}")
    return info


def running_job(jm):
    for j in jm.jobs.values():
        if j.project == JOB_PROJECT and j.status == "running":
            return j
    return None


def start(jm):
    """Start (or return the running) install job."""
    return running_job(jm) or jm.start("install", JOB_PROJECT, install, jm)


def auto_start(jm):
    """Server start: set up the built-in OpenFOAM in the background if no OpenFOAM is available
    (SIMWB_AUTO_OPENFOAM=0 turns this off)."""
    if not supported() or os.environ.get("SIMWB_AUTO_OPENFOAM", "1") == "0":
        return None
    info = runner.detect(refresh=True)
    if not info["available"]:
        return start(jm)
    if info["backend"] == "windows" and not info.get("parallel") and not runner.find_mpi():
        # OpenFOAM present (e.g. installed before) but no MPI runtime yet: add it for multi-core runs
        return running_job(jm) or jm.start("install", JOB_PROJECT, install_mpi, jm)
    return None


def wait_until_ready(jm, job, timeout=3600):
    """A CFD run requested while the built-in OpenFOAM is still being set up waits for it."""
    import time

    t0 = time.time()
    while (inst := running_job(jm)) is not None:
        job.check_cancel()
        jm.update(job, None, f"Waiting for the built-in OpenFOAM setup to finish ({inst.message})")
        if time.time() - t0 > timeout:
            raise RuntimeError("The OpenFOAM setup is taking too long; see the status bar.")
        time.sleep(3)


def main():
    """Install from the command line (used by run_workbench.bat on the first start)."""
    class _Job:
        progress, proc, message = 0.0, None, ""

        def check_cancel(self):
            pass

    class _JM:
        def update(self, job, progress=None, message=None, log=None, **kw):
            if progress is not None:
                job.progress = progress
            if message and message != job.message:
                job.message = message
                print(f"  [{job.progress * 100:3.0f} %] {message}", flush=True)

    if not supported():
        print("The built-in OpenFOAM is for Windows; see README for Linux/macOS.")
        return 0
    info = runner.detect(refresh=True)
    if info["available"]:
        if info["backend"] == "windows" and not runner.find_mpi():
            try:
                install_mpi(_Job(), _JM())
            except Exception as e:  # noqa: BLE001
                print(f"Parallel runs not set up: {e}")
        print("OpenFOAM is ready.")
        return 0
    print(f"Setting up the built-in OpenFOAM in {install_dir()} (one time, ~200 MB download)...")
    try:
        install(_Job(), _JM())
    except Exception as e:  # noqa: BLE001
        print(f"OpenFOAM setup failed: {e}\nThe workbench will retry when it starts.")
        return 1
    print("OpenFOAM is ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
