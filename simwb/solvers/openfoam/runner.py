"""Find an OpenFOAM installation and run a case.

Supported backends (checked in this order):
  custom : SIMWB_OPENFOAM_CMD env var / settings, a template with {case} e.g.
           'wsl -d Ubuntu -e bash -lc "source /usr/lib/openfoam/openfoam2606/etc/bashrc && cd {case_posix} && bash ./Allrun"'
  native : OpenFOAM environment already loaded (Linux/macOS), or a bashrc in a standard location
  wsl    : Windows Subsystem for Linux with OpenFOAM installed in the default distro
  docker : Docker with the opencfd/openfoam-default image (Linux / macOS; on Windows only with SIMWB_USE_DOCKER=1)
  windows: native Windows build from openfoam.com (OpenFOAM-v2xxx-windows-mingw.exe, per-user install is fine);
           steps are run directly from Python; parallel runs use the MS-MPI runtime in <workbench>/mpi
           (set up by installer.py, no system install) or a system MS-MPI
"""
from __future__ import annotations

import glob
import json

import numpy as np
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# (Docker Hub has opencfd/openfoam-default tags up to 2512; there is no 2606 image, unlike the Windows installer)
DOCKER_IMAGE = os.environ.get("SIMWB_DOCKER_IMAGE", "opencfd/openfoam-default:2512")
_CACHE = {}

BASHRC_GLOBS = [
    "/usr/lib/openfoam/openfoam*/etc/bashrc",
    "/opt/openfoam*/etc/bashrc",
    "/opt/OpenFOAM*/OpenFOAM-*/etc/bashrc",
    os.path.expanduser("~/OpenFOAM/OpenFOAM-*/etc/bashrc"),
]


def _run(cmd, timeout=20):
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        out = r.stdout
        # wsl.exe prints UTF-16
        if b"\x00" in out[:200]:
            out = out.decode("utf-16-le", "ignore")
        else:
            out = out.decode("utf-8", "ignore")
        return r.returncode, out
    except Exception as e:  # noqa: BLE001
        return -1, str(e)


def to_wsl_path(p: Path) -> str:
    p = Path(p).resolve()
    drive = p.drive.rstrip(":").lower()
    rest = str(p)[len(p.drive):].replace("\\", "/")
    return f"/mnt/{drive}{rest}"


INSTALLING_MARKER = ".simwb_installing"  # written by installer.py while the silent install runs
BUNDLED_DIR = Path(__file__).resolve().parents[3] / "openfoam"  # the workbench's own copy (installer.py)
BUNDLED_MPI_DIR = Path(__file__).resolve().parents[3] / "mpi"  # MS-MPI runtime files (installer.py)
MPI_FILES = ("mpiexec.exe", "smpd.exe", "msmpi.dll")


def find_windows_install(roots=None, include_installing=False):
    """Locate a native Windows OpenFOAM (ESI mingw) installation; returns (project_dir, bin_dir) or None.
    Trees that the built-in installer is still writing are skipped unless include_installing."""
    if roots is None:
        roots = []
        if os.environ.get("SIMWB_OPENFOAM_DIR"):
            roots.append(Path(os.environ["SIMWB_OPENFOAM_DIR"]))
        roots.append(BUNDLED_DIR)
        home = Path(os.path.expanduser("~"))
        roots += [home / "OpenFOAM", Path("C:/Program Files/ESI-OpenCFD/OpenFOAM"), Path("C:/OpenFOAM")]
    for root in roots:
        root = Path(root)
        if not root.exists() or (not include_installing and (root / INSTALLING_MARKER).exists()):
            continue
        pats = ["msys64/home/ofuser/OpenFOAM/OpenFOAM-v*/platforms/*/bin/simpleFoam.exe",
                "*/msys64/home/ofuser/OpenFOAM/OpenFOAM-v*/platforms/*/bin/simpleFoam.exe",
                "OpenFOAM-v*/platforms/*/bin/simpleFoam.exe"]
        for pat in pats:
            hits = sorted(root.glob(pat))
            if hits:
                b = hits[-1].parent
                return b.parents[2], b
    return None


def windows_env(project_dir: Path, bin_dir: Path) -> dict:
    env = dict(os.environ)
    env.update(WM_PROJECT_DIR=str(project_dir), WM_PROJECT="OpenFOAM",
               WM_PROJECT_VERSION=project_dir.name.replace("OpenFOAM-", ""), FOAM_SIGFPE="1")
    mpi = find_mpi()
    env["PATH"] = os.pathsep.join([str(bin_dir)] + ([str(mpi)] if mpi else []) + [env.get("PATH", "")])
    return env


def find_mpi() -> Path | None:
    """Folder with the MS-MPI runtime (mpiexec.exe, msmpi.dll): SIMWB_MPI_DIR, the workbench's own copy, or a
    system MS-MPI install."""
    cands = [os.environ.get("SIMWB_MPI_DIR"), BUNDLED_MPI_DIR, os.environ.get("MSMPI_BIN"),
             "C:/Program Files/Microsoft MPI/Bin"]
    sys32 = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32"
    for c in cands:
        if c and (Path(c) / "mpiexec.exe").exists() and ((Path(c) / "msmpi.dll").exists()
                                                       or (sys32 / "msmpi.dll").exists()):
            return Path(c)
    return None


def physical_cores() -> int:
    """Physical CPU cores (not hardware threads: OpenFOAM is memory-bound, extra threads do not help)."""
    try:
        if sys.platform == "win32":
            import ctypes

            k32, n = ctypes.windll.kernel32, ctypes.c_ulong(0)
            k32.GetLogicalProcessorInformationEx(0, None, ctypes.byref(n))  # RelationProcessorCore: size query
            buf = ctypes.create_string_buffer(n.value)
            if n.value and k32.GetLogicalProcessorInformationEx(0, buf, ctypes.byref(n)):
                count = off = 0
                while off < n.value:  # variable-size records: DWORD Relationship, DWORD Size, ...
                    count += 1
                    off += int.from_bytes(buf.raw[off + 4:off + 8], "little") or n.value
                return max(count, 1)
        elif sys.platform.startswith("linux"):
            cores, phys = set(), "0"
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                key, _, val = line.partition(":")
                if key.strip() == "physical id":
                    phys = val.strip()
                elif key.strip() == "core id":
                    cores.add((phys, val.strip()))
            if cores:
                return len(cores)
    except Exception:  # noqa: BLE001 - fall back to the logical count
        pass
    return os.cpu_count() or 1


def auto_procs(n_cells: int, cores: int | None = None) -> int:
    """Cores for 'Auto': all physical cores for big meshes, fewer for small ones (~20k cells per core:
    below that the split and the exchange between the parts cost more than they save)."""
    return max(1, min(cores or physical_cores(), int(n_cells or 0) // 20000))


def ensure_pstream(bin_dir: Path, parallel: bool) -> bool:
    """Select the libPstream.dll variant of the ESI Windows build: MS-MPI (parallel; also runs serial steps)
    when an MPI runtime is available, else the serial (dummy) one. Returns whether the wanted variant is active
    (False e.g. without write access, or while an OpenFOAM program has the DLL open)."""
    b = Path(bin_dir)
    ps = b / "libPstream.dll"
    if parallel:
        src = next((q for q in (b / "libPstream.dll-msmpi", b / "libPstream.dll-msmpi.bak") if q.exists()), None)
    else:
        src = b / "libPstream.dll-dummy"
    if src is None or not src.exists():
        return not parallel and ps.exists()  # (a tree without variants: keep what is there)
    if ps.exists() and ps.read_bytes() == src.read_bytes():
        return True
    try:
        shutil.copy(src, ps)
        return True
    except OSError:
        return False


def detect(refresh=False) -> dict:
    """Returns {"available": bool, "backend": str, "detail": str, "help": str}."""
    if _CACHE and not refresh:
        return _CACHE["v"]
    res = {"available": False, "backend": None, "detail": "", "bashrc": None}
    custom = os.environ.get("SIMWB_OPENFOAM_CMD")
    if custom:
        res.update(available=True, backend="custom", detail=custom)
    elif sys.platform != "win32":
        if os.environ.get("WM_PROJECT_DIR"):
            res.update(available=True, backend="native", detail=os.environ["WM_PROJECT_DIR"])
        else:
            for g in BASHRC_GLOBS:
                hits = sorted(glob.glob(g))
                if hits:
                    res.update(available=True, backend="native", detail=hits[-1], bashrc=hits[-1])
                    break
    if not res["available"] and sys.platform == "win32" and shutil.which("wsl"):
        code, out = _run(["wsl", "-e", "bash", "-c", "ls -d " + " ".join(BASHRC_GLOBS[:3]) + " 2>/dev/null"])
        hits = [l.strip() for l in out.splitlines() if l.strip().endswith("bashrc")]
        if code == 0 and hits:
            res.update(available=True, backend="wsl", detail=hits[-1], bashrc=hits[-1])
        elif "no installed distributions" in out.lower() or "not installed" in out.lower():
            res["detail"] = "WSL is present but has no Linux distribution."
        else:
            res["detail"] = "WSL is present but OpenFOAM is not installed in it."
    if not res["available"] and sys.platform == "win32":
        w = find_windows_install()
        if w:
            pdir, bdir = w
            mpi = find_mpi()
            par = bool(mpi) and ensure_pstream(bdir, True)
            if par or ensure_pstream(bdir, False):
                cores = physical_cores()
                res.update(available=True, backend="windows", project_dir=str(pdir), bin_dir=str(bdir),
                           parallel=par, mpi_dir=str(mpi) if par else None, cores=cores,
                           detail=f"{pdir.name} (native Windows build, " + (f"up to {cores} cores)" if par else "1 core)"))
            else:
                res["detail"] = ("Native Windows OpenFOAM found, but libPstream.dll could not be switched to the "
                                 "serial version (no write access to the OpenFOAM folder).")
    # Docker is a fallback for Linux / macOS only. On Windows the workbench installs its own native OpenFOAM, and a Docker
    # Desktop that happens to be running must not stop that (it made the first start skip the built-in install and then
    # fail on the missing image): there it is used only when asked for (SIMWB_USE_DOCKER=1).
    if (not res["available"] and shutil.which("docker")
            and (sys.platform != "win32" or os.environ.get("SIMWB_USE_DOCKER") == "1")):
        code, out = _run(["docker", "info", "--format", "{{.ServerVersion}}"])
        if code == 0:
            # a running Docker is not enough: the image must be here or exist on the registry (an image name that
            # does not exist made every run fail with "not found" while the workbench said OpenFOAM was available)
            if _run(["docker", "image", "inspect", DOCKER_IMAGE])[0] == 0 or \
                    _run(["docker", "manifest", "inspect", DOCKER_IMAGE])[0] == 0:
                res.update(available=True, backend="docker", detail=DOCKER_IMAGE)
            else:
                res["detail"] = (f"Docker is running but the image {DOCKER_IMAGE} was not found (locally or on Docker "
                                 "Hub); set SIMWB_DOCKER_IMAGE to an OpenFOAM image that exists.")
    if not res["available"]:
        res["help"] = install_help()
        res["installable"] = sys.platform == "win32"  # built-in installer (installer.py)
    _CACHE["v"] = res
    return res


def install_help() -> str:
    if sys.platform == "win32":
        return (
            "OpenFOAM was not found. Easiest: press 'Install OpenFOAM' - the workbench downloads the official\n"
            "openfoam.com Windows build (~200 MB) and installs it into the workbench folder (no admin rights).\n"
            "Alternatives (then press 'Re-check'):\n"
            "  1. WSL: in an *administrator* PowerShell run  `wsl --install -d Ubuntu`, reboot, then in Ubuntu run:\n"
            "       curl https://dl.openfoam.com/add-debian-repo.sh | sudo bash\n"
            "       sudo apt-get install -y openfoam2606-default\n"
            "  2. Docker Desktop (advanced): set SIMWB_USE_DOCKER=1 and the workbench will use the image "
            + DOCKER_IMAGE + ".\n"
            "The heat-conduction study works without OpenFOAM."
        )
    return ("OpenFOAM was not found. Install OpenFOAM (openfoam.com, v2206 or newer) or Docker, "
            "or set SIMWB_OPENFOAM_CMD.")


def build_command(case: Path, script: str = "Allrun") -> list | str:
    info = detect()
    if not info["available"]:
        raise RuntimeError(install_help())
    case = Path(case).resolve()
    b = info["backend"]
    if b == "custom":
        return info["detail"].format(case=str(case), case_posix=to_wsl_path(case) if sys.platform == "win32"
                                     else str(case), script=script)
    if b == "native":
        src = f"source '{info['bashrc']}' && " if info.get("bashrc") else ""
        return ["bash", "-c", f"{src}cd '{case}' && bash ./{script}"]
    if b == "wsl":
        return ["wsl", "-e", "bash", "-c", f"source '{info['bashrc']}' >/dev/null 2>&1; cd '{to_wsl_path(case)}' && "
                                           f"tr -d '\\r' < {script} > .{script}.sh && bash ./.{script}.sh"]
    if b == "docker":
        return ["docker", "run", "--rm", "-v", f"{case}:/case", "-w", "/case", DOCKER_IMAGE,
                "bash", "-c", f"source /usr/lib/openfoam/openfoam*/etc/bashrc 2>/dev/null; bash ./{script}"]
    raise RuntimeError(f"unknown backend {b}")


# ----------------------------------------------------------------------------
# log parsing
# ----------------------------------------------------------------------------

RE_TIME = re.compile(r"^Time = ([0-9.eE+-]+)\s*$", re.M)
RE_RES = re.compile(r"Solving for (\w+), Initial residual = ([0-9.eE+-]+)")
RE_REGION = re.compile(r"Solving for (fluid|solid) region (\w+)")
RE_MAXT = re.compile(r"min/max\(T\) = ([0-9.eE+-]+) ([0-9.eE+-]+)")
RE_FMM = re.compile(r"^fieldMinMax (\w+) write:\s*\n\s*min/max\(T\) = ([0-9.eE+-]+) ([0-9.eE+-]+)", re.M)
RE_FORCE = re.compile(r"Sum of forces\s*\n\s*Total\s*:\s*\(([^)]*)\)")
RE_MOMENT = re.compile(r"Sum of moments\s*\n\s*Total\s*:\s*\(([^)]*)\)")


RE_WAVG = re.compile(r"^surfaceFieldValue flow_(\w+) write:\s*\n(?:\s+.*\n)*?\s+weightedAverage\(\w+\) of p = ([0-9.eE+-]+)",
                     re.M)


def _dp_stable(text, meta, it, window):
    """Pressure drop (inlet - outlet, mass weighted static p) changed < 1 % over the window?"""
    ins, outs = set(meta.get("inlet_patches", [])), set(meta.get("outlet_patches", []))
    pos_t = [(m.start(), float(m.group(1))) for m in RE_TIME.finditer(text)]
    if not pos_t or not ins or not outs:
        return True, "", None
    import bisect
    starts = [q for q, _ in pos_t]
    series = {}
    for m in RE_WAVG.finditer(text):
        k = bisect.bisect_right(starts, m.start()) - 1
        if k >= 0:
            series.setdefault(pos_t[k][1], {})[m.group(1)] = float(m.group(2))
    rows = sorted((t, v) for t, v in series.items() if ins <= set(v) and outs <= set(v))
    if len(rows) < 2:
        return True, "", None
    dp = [(t, sum(v[p] for p in ins) / len(ins) - sum(v[p] for p in outs) / len(outs)) for t, v in rows]
    if not any(t <= it - window for t, _ in dp):
        return False, "", None

    def change(at):
        """Relative change between the means of the two halves of the window ending at `at`."""
        first = [d for t, d in dp if at - window < t <= at - window / 2]
        second = [d for t, d in dp if at - window / 2 < t <= at]
        if not first or not second:
            return None
        a, b = sum(first) / len(first), sum(second) / len(second)
        return abs(b - a) / max(abs(b), 1e-9)

    ch = change(it)
    if ch is None:
        return False, "", None
    # unsteadiness band: the largest half-window change over the last 600 iterations (pseudo-steady iterates
    # behind fins wander; a mesh-check difference smaller than this is not resolved)
    # (only windows that start after the first half of the run: the start-up transient is not unsteadiness)
    band = max(c for c in (change(it - k) for k in range(0, 601, 50) if it - k - window >= it / 2)
               if c is not None) if it - window >= it / 2 else ch
    return ch < 0.01, f"pressure drop changed by {ch * 100:.2f} % (half-window means)", band * 100


def monitor_outputs(text: str, meta: dict, window: int = 300) -> dict:
    if meta.get("transient"):  # a transient run goes to its end time: no auto-stop
        times = [float(t) for t in RE_TIME.findall(text)]
        out = {"iteration": times[-1] if times else 0, "stable": False}
        if meta.get("study") == "cht_enclosure":
            hot = [float(m.group(3)) - 273.15 for m in RE_FMM.finditer(text)]
            if hot:
                out["max_T_C"] = max(hot[-len(meta.get("solid_regions") or [1]):])
        return out
    return _monitor_steady(text, meta, window)


def _monitor_steady(text: str, meta: dict, window: int = 300) -> dict:
    """Engineering convergence: have the quantities the user cares about stopped changing?

    CHT : hottest solid temperature changes < 0.1 % of its rise (min 0.01 K) over the last `window` iterations
    aero: after >= 500 iterations, the 100-iteration mean force and moment change by < auto_stop_tol_pct (default 0.2 %, 0 = off) of the total load
          (frame force + rotor drag; moments scaled by the reference length) over the last 200 iterations"""
    times = [float(t) for t in RE_TIME.findall(text)]
    it = times[-1] if times else 0
    out = {"iteration": it, "stable": False}
    if meta.get("study") == "cht_enclosure":
        series = {}
        # fieldMinMax writes every 10 iterations; pair each block with the latest preceding Time
        pos_t = [(m.start(), float(m.group(1))) for m in RE_TIME.finditer(text)]
        import bisect
        starts = [p for p, _ in pos_t]
        for m in RE_FMM.finditer(text):
            k = bisect.bisect_right(starts, m.start()) - 1
            if k >= 0:
                series.setdefault(m.group(1), []).append((pos_t[k][1], float(m.group(3)) - 273.15))
        if series:
            hot = max(series.values(), key=lambda v: v[-1][1])
            out["max_T_C"] = hot[-1][1]
            t0 = meta.get("T_initial_C", hot[0][1])
            rise = abs(hot[-1][1] - t0)  # temperature rise above the initial / inlet temperature
            tol = max(0.01, 1e-3 * rise)

            def judge(at):
                """Temperature test at iteration `at`: (settled, change over the window, remaining drift, T_inf)."""
                half = window / 2
                pts = [(t, v) for t, v in hot if t <= at]
                recent = [v for t, v in pts if t > at - half]
                before = [v for t, v in pts if at - window < t <= at - half]
                older = [v for t, v in pts if at - 1.5 * window < t <= at - window]
                if not (recent and before):
                    return False, None, None, None
                m2, m1 = sum(recent) / len(recent), sum(before) / len(before)
                dT, rem, t_inf = abs(m2 - m1), None, None
                if older:
                    d1, d2 = m1 - sum(older) / len(older), m2 - m1
                    if d1 * d2 > 0:
                        # geometric settling with ratio r: the drift still to come is r/(1-r) * d2 (Aitken);
                        # near-linear creep (r -> 1) is capped at r = 0.9, i.e. 9 * d2
                        r = min(d2 / d1, 0.9)
                        rem = d2 * r / (1 - r)
                        t_inf = m2 + rem
                    else:
                        rem = max(abs(d1), abs(d2))  # oscillation: its amplitude is the uncertainty
                ok = ((dT < tol and (rem is None or abs(rem) < 2.5 * tol))
                      or (rem is not None and dT < 2 * tol and abs(rem) < tol))
                return ok, dT, rem, t_inf

            if it >= max(600, window):
                ok, dT, rem, t_inf = judge(it)
                if dT is not None:
                    out["dT_window_K"] = dT
                    out["tolerance_K"] = tol
                    if rem is not None:
                        out["remaining_drift_K"] = abs(rem)
                        if t_inf is not None:
                            out["extrapolated_T_C"] = t_inf
                    # the test must also have passed 100 iterations earlier (wavering creep can pass once)
                    ok_prev = judge(it - 100)[0]
                    dp_ok, dp_txt, dp_band = _dp_stable(text, meta, it, window)
                    out["dp_check"] = dp_txt
                    if dp_band is not None:
                        out["dp_band_percent"] = dp_band
                    if ok and ok_prev and dp_ok:
                        out["stable"] = True
                        out["reason"] = (f"hottest part changed by {dT:.3f} K over the last {window} iterations"
                                         + (f" (about {abs(rem):.3f} K still to come)" if rem is not None else "")
                                         + (f", {dp_txt}" if dp_txt else ""))
    elif meta.get("study") == "external_aero":
        fs = [np.array(m.group(1).split(), float) for m in RE_FORCE.finditer(text)]
        ms = [np.array(m.group(1).split(), float) for m in RE_MOMENT.finditer(text)]
        win = 200
        if len(fs) > win and it >= 500:
            # mean of the last 100 iterations vs the 100 before: insensitive to iteration-to-iteration noise
            f1 = np.mean(fs[-win // 2:], axis=0)
            f0 = np.mean(fs[-win:-win // 2], axis=0)
            dF = float(np.linalg.norm(f1 - f0))
            # tolerance relative to the total load the trim has to balance (frame force + modelled rotor drag)
            H = float(np.linalg.norm(sum((np.array(r.get("H_N", [0, 0, 0]), float) for r in meta.get("rotors", [])),
                                         np.zeros(3))))
            frac = float(meta.get("auto_stop_tol_pct", 0.2)) / 100.0   # the setting "Stop when forces settle" (0 = never)
            tol = max(frac * (float(np.linalg.norm(f1)) + H), 1e-4) if frac > 0 else 0.0
            out.update(dF_window_N=dF, force_N=f1.tolist(), tolerance_N=tol)
            ok_m, txt_m = True, ""
            if len(ms) > win:
                m1 = np.mean(ms[-win // 2:], axis=0)
                dM = float(np.linalg.norm(m1 - np.mean(ms[-win:-win // 2], axis=0)))
                ok_m = dM < tol * float(meta.get("ref_length") or 1.0)
                txt_m = f", moment by {dM:.5f} N·m"
                out["dM_window_Nm"] = dM
            if frac > 0 and dF < tol and ok_m:
                out["stable"] = True
                out["reason"] = (f"aerodynamic force (100-iteration average) changed by {dF:.4f} N{txt_m} over the "
                                 f"last {win} iterations (< {frac * 100:g} % of the total load)")
    return out


def parse_log(text: str) -> dict:
    """Residual history: {"iters": [...], "residuals": {"air/Ux": [...]}}"""
    iters = []
    res = {}
    region = ""
    cur = None
    for line in text.splitlines():
        m = RE_TIME.match(line)
        if m:
            cur = float(m.group(1))
            iters.append(cur)
            continue
        m = RE_REGION.search(line)
        if m:
            region = m.group(2) + "/"
            continue
        m = RE_RES.search(line)
        if m and cur is not None:
            key = region + m.group(1)
            lst = res.setdefault(key, [])
            # first residual per iteration only
            if len(lst) < len(iters):
                lst.extend([None] * (len(iters) - 1 - len(lst)))
                lst.append(float(m.group(2)))
    return {"iters": iters, "residuals": res}


def parallel_steps(steps: list, app: str, n_procs: int, regions: bool, all_times: bool = False) -> list:
    """Windows build, n_procs > 1: split the mesh (decomposePar), run the solver under mpiexec, join the latest
    result (reconstructPar; all_times: every saved time, for transient runs). The solver step keeps its log name
    and decides success ("main")."""
    if n_procs <= 1:
        return steps
    ar = ["-allRegions"] if regions else []
    out = []
    for st in steps:
        if st.get("app") == app:
            kl = st.get("keep_logs")
            dec = {"app": "decomposePar", "args": [*ar, "-force", "-latestTime"], "log": "log.decomposePar", "keep_logs": kl}
            out += [dec,
                    # a constraint (sliding interface on one process) leaves that process with more cells than the
                    # others, and they wait for it every step: measure the split and split again with weights
                    {"action": "balance_split", "redo": dec},
                    dict(st, args=[*st.get("args", []), "-parallel"], mpi=int(n_procs), main=True),
                    {"app": "reconstructPar", "args": [*ar] if all_times else [*ar, "-latestTime"],
                     "log": "log.reconstructPar", "keep_logs": kl, **({"split_times": int(n_procs)} if all_times else {})},
                    # the pieces are joined: drop them (decomposePar -force splits again on the next run; a
                    # transient sliding run leaves several GB in them)
                    {"action": "rm_processors"}]
        else:
            out.append(st)
    return out


def processor_count(case: Path) -> int:
    """Number of processor* folders of a split (decomposed) case, 0 when it is not split."""
    return sum(1 for d in Path(case).glob("processor*") if d.is_dir() and d.name[9:].isdigit())


def unjoined_times(case: Path, aside: bool = True) -> int:
    """How many saved times (not counting the start state) exist only as pieces: in the processor folders, and
    (aside) set aside in case/unjoined/ after a failed join. A new run deletes them with the old case, so they are
    worth loading or repairing first. They are not part of the joined times of the case."""
    n = sum(1 for t in _piece_times(case) if not time_is_joined(case, t))
    if aside:
        n += set_aside_count(case)
    return n


def set_aside_count(case: Path) -> int:
    """How many saved times could not be joined and were set aside in case/unjoined/."""
    d = Path(case) / UNJOINED
    return sum(1 for x in d.iterdir() if x.is_dir()) if d.is_dir() else 0


def _time_dirs(d: Path) -> dict:
    return {x.name: float(x.name) for x in d.iterdir() if x.is_dir() and re.fullmatch(r"[0-9.eE+-]+", x.name)} \
        if d.is_dir() else {}


def saved_transient_time(case: Path, prune: bool = False) -> float | None:
    """Latest time an interrupted transient run can restart from: in a split case the newest time every processor
    folder holds completely (as many files as in its previous saved time); otherwise the newest time folder of the
    case. prune: delete later, half-written time folders, so that the solver's startFrom latestTime picks this one."""
    case = Path(case)
    procs = sorted(d for d in case.glob("processor*") if d.is_dir() and d.name[9:].isdigit())
    dirs = procs or [case]
    per = [_time_dirs(d) for d in dirs]
    common = sorted(set.intersection(*(set(t) for t in per)), key=float) if per else []

    def complete(t):
        for d in dirs:
            names = sorted(_time_dirs(d), key=float)
            i = names.index(t)
            if i > 0 and float(names[i - 1]) > 0:  # compare with the previous saved time (0 holds fewer fields)
                n_prev = len([f for f in (d / names[i - 1]).iterdir() if f.is_file()])
                if len([f for f in (d / t).iterdir() if f.is_file()]) < n_prev:
                    return False
        return True

    best = next((t for t in reversed(common) if complete(t)), None)
    if best is None:
        return None
    if prune:
        for d, times in zip(dirs, per):
            for name, v in times.items():
                if v > float(best):
                    shutil.rmtree(d / name, ignore_errors=True)
    return float(best)


RE_PROC_CELLS = re.compile(r"Processor\s+(\d+)\s+Number of cells\s*=\s*(\d+)")
BALANCE_TOLERANCE = 1.10  # accept the split when the largest process has at most 10 % more cells than the mean


def cells_per_process(decompose_log: Path) -> list:
    """Cells of every process from decomposePar's log (empty when it cannot be read)."""
    try:
        found = RE_PROC_CELLS.findall(Path(decompose_log).read_text(errors="ignore"))
    except OSError:
        return []
    return [int(c) for _, c in sorted(found, key=lambda t: int(t[0]))]


def new_weights(cells: list, old_weights: list | None = None) -> list:
    """Scotch processor weights that even out the cells after a constraint moved some: scotch assigns about
    w_i / sum(w) of the cells, then the constraint moves cells onto one process. With s_i the cells scotch gave
    process i and c_i what it ended with, asking for s_i + (mean - c_i) next time compensates the move (the sum stays
    the total). A floor keeps every process at 30 % of the mean at least. Weights are scaled to a mean of 1."""
    n, total = len(cells), float(sum(cells))
    mean = total / n
    w = list(old_weights) if old_weights and len(old_weights) == n else [1.0] * n
    s = [wi * total / sum(w) for wi in w]
    s_new = [max(0.3 * mean, si + (mean - ci)) for si, ci in zip(s, cells)]
    k = n / sum(s_new)
    return [round(x * k, 4) for x in s_new]


def _set_weights(dict_path: Path, weights: list | None):
    """Write (or remove) scotchCoeffs { processorWeights (...); } in a decomposeParDict."""
    txt = Path(dict_path).read_text()
    txt = re.sub(r"\nscotchCoeffs\s*\{[^}]*\}\s*", "\n", txt)
    if weights:
        # (scotch reads integer weights: the mean-1 weights scaled by 100)
        ints = [max(1, round(100 * x)) for x in weights]
        block = "scotchCoeffs\n{\n    processorWeights (" + " ".join(str(i) for i in ints) + ");\n}\n"
        txt = re.sub(r"(method\s+scotch;\s*)", lambda m: m.group(1) + block, txt, count=1)
    Path(dict_path).write_text(txt, newline="\n")


def _decompose(case: Path, redo: dict, info: dict, env: dict, on_proc=None) -> int:
    exe = Path(info["bin_dir"]) / "decomposePar.exe"
    with open(Path(case) / redo["log"], "w", encoding="utf-8", errors="ignore") as lf:
        proc = subprocess.Popen([str(exe), *redo.get("args", []), "-case", str(case)], cwd=case, env=env, stdout=lf,
                                stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if on_proc:
            on_proc(proc)
        return proc.wait()


def interface_weights(cells: list, a: float) -> list:
    """Scotch weights for a split where a constraint (a sliding interface kept on one process) piled cells onto
    one process: that process, the one with the most cells, asks scotch for fewer. Its excess over the mean is what
    the constraint moved onto it, and about a = 1.8 times that excess has to be taken off its own share (the moved
    cells come out of the other partitions, which then end up short, so more than the excess is needed). Only
    that one weight is changed: adjusting every weight (the earlier approach) moved the short partitions around
    from one split to the next and never settled."""
    n, mean = len(cells), sum(cells) / len(cells)
    # one process piled up (one sliding zone) or several (one interface kept on each of them: two propellers)
    heavy = [i for i in range(n) if cells[i] > 1.1 * mean] or [max(range(n), key=lambda i: cells[i])]
    w = [1.0] * n
    for k in heavy:
        w[k] = max(0.15, 1.0 - a * (cells[k] - mean) / mean)
    return w


BALANCE_SCAN = (1.8, 1.4, 2.2, 1.0)  # factors tried in turn until the split is within BALANCE_TOLERANCE


RE_AMI_PATCH = re.compile(r"\n\s+(\w+_AMI_\w+)\s*\{([^}]*)\}")


def split_interfaces(case: Path) -> list:
    """Sliding interfaces (cyclicAMI patches) that ended up on more than one process after decomposePar: a
    distributed AMI crashes MS-MPI ("MPI_Bcast: Message truncated"). Two sliding zones close together have air
    cells that touch both interfaces; they cannot sit on the two processes the interfaces are pinned to, so a few
    faces of one interface land on the other's process. Returns the names of the split patches."""
    where: dict = {}
    for d in sorted(Path(case).glob("processor*")):
        f = d / "constant" / "polyMesh" / "boundary"
        if not f.exists():
            continue
        for name, body in RE_AMI_PATCH.findall(f.read_text(errors="ignore")):
            m = re.search(r"nFaces\s+(\d+)", body)
            if m and int(m.group(1)) > 0:
                where.setdefault(name, set()).add(d.name)
    return sorted(n for n, procs in where.items() if len(procs) > 1)


def pin_interfaces_together(dpd: Path) -> bool:
    """Put every sliding interface of the decomposeParDict on process 0 (the cells that touch two interfaces are
    then on one process). Returns whether anything changed."""
    txt = Path(dpd).read_text()
    new = re.sub(r"\((\w+_AMIfaces) \d+\)", lambda m: f"({m.group(1)} 0)", txt)
    if new == txt:
        return False
    Path(dpd).write_text(new, newline="\n")
    return True


RE_BOUND = re.compile(r"bounding (k|omega|epsilon|nuTilda), min: [-0-9.eE+]+ max: ([0-9.eE+]+)")
RE_CONT = re.compile(r"time step continuity errors : sum local = ([0-9.eE+-]+)")


def divergence(text: str, tail: int = 60000) -> str | None:
    """A reason when the solution has blown up (None while it is sound): a turbulence quantity far beyond anything
    physical, or continuity errors of thousands. Read from the end of the solver log, so a run that diverges is
    stopped at once instead of running for hours on garbage."""
    t = text[-tail:]
    for m in RE_BOUND.finditer(t):
        try:
            v = float(m.group(2))
        except ValueError:
            continue
        if v > 1e9:
            return f"the solution diverged: {m.group(1)} reached {v:.3g} (a sound run stays below about 1e5)"
    vals = []
    for m in RE_CONT.finditer(t):
        try:
            vals.append(abs(float(m.group(1))))
        except ValueError:
            pass
    if len(vals) >= 3 and all(v > 1e3 for v in vals[-3:]):
        return f"the solution diverged: the continuity error reached {vals[-1]:.3g} (a sound run stays far below 1)"
    return None


# The start-up banner of every OpenFOAM program says that floating point exceptions are trapped ("trapFpe: Floating
# point exception trapping enabled (FOAM_SIGFPE)", "sigFpe : Enabling floating point exception trapping"): that is a
# setting, not a crash, and it must not count as one.
_FPE_BANNER = re.compile(r"FOAM_SIGFPE|exception trapping|trapping enabled", re.I)
_FPE_CRASH = re.compile(r"sigFpe::sigHandler|SIGFPE|Floating point exception", re.I)


def real_fpe(text: str) -> bool:
    """True when the text shows a floating point exception that really happened (the stack trace of sigFpe, or the
    system's "Floating point exception (core dumped)"), not the banner line every run starts with."""
    for line in (text or "").splitlines():
        if _FPE_CRASH.search(line) and not _FPE_BANNER.search(line):
            return True
    return False


RE_IO_FILE = re.compile(r"^file:\s*(?P<file>.*?)(?:\s+at line\s+(?P<line>\d+))?\.?\s*$")


def io_error(text: str) -> dict | None:
    """The last "FOAM FATAL IO ERROR" of a log as {why, file, line} (a file OpenFOAM could not read), None when there
    is none."""
    lines = (text or "").splitlines()
    found = None
    for i, ln in enumerate(lines):
        if "FOAM FATAL IO ERROR" not in ln:
            continue
        why = next((x.strip() for x in lines[i + 1:i + 4] if x.strip()), "")
        for x in lines[i + 1:i + 12]:
            m = RE_IO_FILE.match(x.strip())
            if m:
                found = {"why": why if not RE_IO_FILE.match(why) else "", "file": m.group("file").strip().strip("\"'"),
                         "line": int(m.group("line")) if m.group("line") else None}
                break
        else:
            found = {"why": why, "file": "", "line": None}
    return found


def failure_hint(case: Path, tail: str = "", step: str | None = None) -> str:
    """A plain-language likely cause for a failed parallel run, from the log tail and the decomposed mesh ("" when
    nothing is recognised). step: the program that failed ("reconstructPar", "decomposePar", the solver ...); a
    divergence is only named when the solver itself failed."""
    t = tail or ""
    solver_failed = step in (None, "") or step not in ("reconstructPar", "decomposePar", "mapFields", "snappyHexMesh")
    io = io_error(t)
    if io is not None:
        where = (io["file"] or "a saved file") + (f" (line {io['line']})" if io["line"] else "")
        return (f"{step + ' ' if step else 'OpenFOAM '}could not read {where}: {io['why'] or 'unreadable file'}. "
                "The file is damaged or was not written completely (the run was stopped while saving, or the disk "
                "was full)")
    if "Message truncated" in t or "MPI_Bcast" in t:
        bad = split_interfaces(case)
        if bad:
            return ("a sliding interface (" + ", ".join(bad[:3]) + ") is spread over several processes, which the "
                    "Windows MPI cannot run. Move the sliding zones further apart, or set CPU cores to 1 in Solve.")
        return ("the parallel communication broke down (a sliding-mesh interface split over processes is the usual "
                "cause). Try CPU cores = 1 in Solve.")
    if solver_failed and real_fpe(t):
        return ("the solution diverged (floating point exception). Use a smaller time step, a coarser rotation per "
                "step, or check the mesh quality near thin gaps and blade tips.")
    if "bad_alloc" in t or "out of memory" in t.lower():
        return "the computer ran out of memory. Use a coarser mesh or fewer CPU cores."
    return ""


def balance_split(case: Path, redo: dict, info: dict, env: dict, on_proc=None, on_line=None, scan=BALANCE_SCAN):
    """After decomposePar: if one process holds clearly more cells than the mean (a sliding interface kept on one
    process), split again with a lower scotch weight on that process; a few weights are tried, the best split is
    kept. Measured on a 2.7 M cell propeller (8 processes): 1.34 x the mean without, 1.08 x with. Returns the
    final cells per process."""
    case = Path(case)
    dpd = case / "system" / "decomposeParDict"
    cells = cells_per_process(case / redo["log"])
    if len(cells) < 2 or not dpd.exists():
        return cells
    bad = split_interfaces(case)
    if bad and pin_interfaces_together(dpd):
        if on_line:
            on_line("The sliding zones are so close that their interfaces share cells (" + ", ".join(bad[:3])
                    + (" ..." if len(bad) > 3 else "") + " were split over processes): splitting again with all "
                    "interfaces on one process. Moving the zones further apart gives a better balance.")
        if _decompose(case, redo, info, env, on_proc) == 0:
            cells = cells_per_process(case / redo["log"]) or cells
    bad = split_interfaces(case)
    if bad and on_line:
        on_line("WARNING: the sliding interface " + ", ".join(bad[:3]) + " is still spread over several processes; "
                "the run will probably stop with an MPI error. Set CPU cores to 1 in Solve, or move the zones apart.")

    def ratio(c):
        return max(c) / (sum(c) / len(c))

    start = cells
    best, best_txt, current_is_best = cells, dpd.read_text(), True
    for a in scan:
        if ratio(best) <= BALANCE_TOLERANCE:
            break
        weights = interface_weights(start, a)
        _set_weights(dpd, weights)
        if on_line:
            on_line(f"Cells per process {min(best):,}-{max(best):,} (mean {sum(best) // len(best):,}): splitting "
                    "again with weights " + " ".join(f"{x:g}" for x in weights))
        rc = _decompose(case, redo, info, env, on_proc)
        cells = cells_per_process(case / redo["log"])
        if rc != 0 or len(cells) < 2:
            current_is_best = False
            break
        if ratio(cells) < ratio(best):
            best, best_txt, current_is_best = cells, dpd.read_text(), True
        else:
            current_is_best = False
    if not current_is_best:  # the last split tried is not the best one: go back to it
        dpd.write_text(best_txt, newline="\n")
        _decompose(case, redo, info, env, on_proc)
    if on_line:
        on_line(f"Cells per process {min(best):,}-{max(best):,} (largest {ratio(best):.2f} x the mean)")
    return best


def reconstruct_windows(case: Path, info: dict, regions: bool) -> None:
    """Join the newest parallel result into the case (after a cancelled parallel run, before continuing it)."""
    case = Path(case)
    if (case / "processor0").exists():
        subprocess.run([str(Path(info["bin_dir"]) / "reconstructPar.exe"), *(["-allRegions"] if regions else []),
                        "-latestTime", "-case", str(case)], cwd=case,
                       env=windows_env(Path(info["project_dir"]), Path(info["bin_dir"])), capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


JOIN_SKIPPED = "join_skipped.json"  # written when some saved times could not be joined
JOIN_RAMP_S = 20  # seconds between two joins starting (the memory of the last one is still growing)


UNJOINED = "unjoined"  # case sub-folder that keeps the pieces of a time that could not be joined


def join_failure(log_text: str) -> str:
    """Why one time could not be joined, in a line, from its reconstructPar log."""
    io = io_error(log_text)
    if io is not None:
        return (f"could not read {io['file'] or 'a file'}" + (f" (line {io['line']})" if io["line"] else "")
                + (f": {io['why']}" if io["why"] else ""))
    last = [x.strip() for x in (log_text or "").splitlines() if x.strip()]
    return last[-1][:200] if last else "no message"


def set_unjoined_aside(case: Path, t: str) -> None:
    """Move the pieces of a time that could not be joined to case/unjoined/<t>/processorN: they stay (nothing is
    deleted, the damaged file can be repaired and the time joined by hand), and the processor folders then hold no
    half-usable time that a restart or "latest time" would pick up."""
    for d in Path(case).glob(f"processor*/{t}"):
        dest = Path(case) / UNJOINED / t / d.parent.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        try:
            shutil.move(str(d), str(dest))
        except OSError:
            pass
    shutil.rmtree(Path(case) / t, ignore_errors=True)  # a half-written join of that time


def _piece_times(case: Path) -> dict:
    """{time name: value} of every saved time (not the start state) that exists as pieces in ANY processor folder: a time written
    by only some of the processors (the run was stopped while saving) is still a time."""
    out: dict = {}
    for d in Path(case).glob("processor*"):
        if d.is_dir() and d.name[9:].isdigit():
            out.update({t: v for t, v in _time_dirs(d).items() if v > 0})
    return out


def time_is_joined(case: Path, t: str) -> bool:
    """True when the joined folder case/<t> holds every file the pieces of that time have (a half-written join does not count)."""
    d = Path(case) / t
    if not d.is_dir():
        return False
    try:
        names = {f.name for p in Path(case).glob(f"processor*/{t}") for f in p.iterdir() if f.is_file()}
        return all((d / n).exists() for n in names) and any(d.iterdir())
    except OSError:
        return False


def newest_piece_time(case: Path) -> float | None:
    """The newest saved time (not the start state) that exists as pieces in any processor folder, None when there are none."""
    times = _piece_times(case).values()
    return max(times) if times else None


def run_split_times(case: Path, st: dict, exe: Path, env: dict, on_proc=None, on_line=None, report: dict | None = None,
                    keep_pieces: bool = False) -> int | None:
    """reconstructPar is serial: with many saved times (a transient run) join them with several processes at once.
    The pieces of a joined time are deleted from the processor folders right away: the split and the joined copy of
    a whole run never sit on the disk together (a 2.7 M cell sliding run: about 0.6 GB per saved time compressed,
    twice that during a plain join). As many processes as the run had cores work at once, as long as the memory
    allows it: each holds a whole time of the mesh (about 0.6 GB per million cells), and the computer ran out of
    memory when too many did. A new one starts as soon as one finishes (no waiting for the slowest of a round).
    The newest time is joined first. A time that cannot be joined (a damaged file in one of the pieces) is skipped:
    the others are still joined, and the skipped one is named in report["skipped"] ({time, reason}); report["joined"]
    lists the times that were joined. keep_pieces: leave the pieces in the processor folders (loading the results of a
    case that may still be continued). Returns the worst exit code, None when there is nothing to split (the caller
    runs the step as usual)."""
    from ... import memlimit  # (simwb/memlimit.py)

    p0 = case / "processor0"
    # (time 0 is the start state, not a result: reconstructPar -time 0 finds nothing and stops with "No times selected",
    # which failed a finished run at its very last step)
    times = sorted(_piece_times(case), key=float) if p0.exists() else []   # (any processor's times: a half-saved one is named, not ignored)
    if keep_pieces:  # (loading the results of a case again: what is joined already is not joined twice)
        times = [t for t in times if not time_is_joined(case, t)]
    if len(times) < (1 if keep_pieces else 2):  # (a single time left to join is joined here too when loading: it may be the newest)
        return None
    times = [times[-1]] + times[:-1]  # the newest first: it is the one the results are read from
    cells = 0
    try:
        cells = sum(int(c) for _, c in RE_PROC_CELLS.findall((case / "log.decomposePar").read_text(errors="ignore")))
    except OSError:
        pass
    need = max(1.5, 0.65 * cells / 1e6) * 2 ** 30  # bytes one joining process holds
    reserve = 6 * 2 ** 30  # left to the rest of the computer (4 joins of 2 GB took the last 4 GB: the system stopped the workbench)
    avail = memlimit.memory()[1]
    cap = max(1, min(int(st["split_times"]), len(times), os.cpu_count() or 4))
    if on_line:
        on_line(f"$ reconstructPar on up to {cap} processes at once, as the memory allows ({len(times)} times, "
                f"about {need / 2 ** 30:.1f} GB each, {avail / 2 ** 30:.0f} GB free now)")
    import time as _time

    worst, k, pending, running, failed, last = 0, 0, list(times), [], False, 0.0
    rep = report if report is not None else {}
    rep.update(joined=[], skipped=[])
    logs = []  # (index, path), joined into the one log in time order at the end
    try:
        while pending or running:
            # (a new one only when the free memory covers it and after the last one has grown: its memory shows late)
            while pending and not failed and len(running) < cap and (
                    not running or (_time.time() - last > JOIN_RAMP_S and memlimit.memory()[1] > reserve + need)):
                last = _time.time()
                t = pending.pop(0)
                path = case / f"{st['log']}.{k}"
                lf = open(path, "w", encoding="utf-8", errors="ignore")
                logs.append((k, path))
                k += 1
                pr = subprocess.Popen([str(exe), *st.get("args", []), "-time", t, "-case", str(case)],
                                      cwd=case, env=env, stdout=lf, stderr=subprocess.STDOUT,
                                      creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                running.append((pr, t, lf, path))
                if on_proc:
                    on_proc(pr)
            _time.sleep(0.5)
            for item in [r for r in running if r[0].poll() is not None]:
                running.remove(item)
                pr, t, lf, path = item
                lf.close()
                worst = max(worst, pr.returncode)
                if pr.returncode != 0:
                    try:
                        why = join_failure(path.read_text(encoding="utf-8", errors="ignore"))
                    except OSError:
                        why = "no message"
                    rep["skipped"].append({"time": t, "reason": why})
                    shutil.rmtree(case / t, ignore_errors=True)  # (a half-written join of that time)
                    if on_line:
                        on_line(f"time {t} skipped: reconstructPar {why}")
                    # (nothing joined after several tries: the cause is not one damaged time but the setup, stop)
                    if not rep["joined"] and len(rep["skipped"]) >= 3:
                        failed = True
                    continue
                rep["joined"].append(t)
                if not keep_pieces:
                    for d in case.glob(f"processor*/{t}"):  # joined: the pieces are no longer needed
                        shutil.rmtree(d, ignore_errors=True)
                if on_line:
                    on_line(f"joined {len(rep['joined'])} of {len(times)} times")
            if failed and not running:
                break
        rep["not_tried"] = list(pending)
        if rep["joined"] and not keep_pieces:  # the rest is joined: the damaged times are put aside, nothing is lost
            for sk in rep["skipped"]:
                set_unjoined_aside(case, sk["time"])
    finally:
        for pr, _, lf, _p in running:  # an exception (Cancel): leave nothing running
            try:
                pr.kill()
            except OSError:
                pass
            lf.close()
        with open(case / st["log"], "w", encoding="utf-8", errors="ignore") as out:
            for _, path in sorted(logs):
                try:
                    out.write(path.read_text(encoding="utf-8", errors="ignore"))
                    path.unlink()
                except OSError:
                    pass
    return worst


def run_steps_windows(case: Path, steps: list, info: dict, on_proc=None, on_line=None) -> bool:
    """Execute case steps natively (Windows build). Step: {"app","args","log"} or {"action": "reset0"|"mkdir0"};
    "mpi": n runs the step under mpiexec on n processes (see parallel_steps)."""
    env = windows_env(Path(info["project_dir"]), Path(info["bin_dir"]))
    case = Path(case)
    if not any(st.get("keep_logs") for st in steps):
        for f in case.glob("log.*"):
            f.unlink()
    for name in ("case.done", "case.failed", JOIN_SKIPPED):
        (case / name).unlink(missing_ok=True)

    def failed(st):  # case.failed names the step that failed: the diagnosis reads that step's log, not the solver's
        (case / "case.failed").write_text(json.dumps({"step": st.get("app"), "log": st.get("log")}))

    for st in steps:
        if st.get("action") == "reset0":
            if (case / "0").exists():
                shutil.rmtree(case / "0")
            shutil.copytree(case / "0.orig", case / "0")
            continue
        if st.get("action") == "balance_split":
            balance_split(case, st["redo"], info, env, on_proc, on_line)
            continue
        if st.get("action") == "rm_processors":
            for d in case.glob("processor*"):
                shutil.rmtree(d, ignore_errors=True)
            continue
        if st.get("action") == "mkdir0":
            (case / "0").mkdir(exist_ok=True)
            continue
        exe = Path(info["bin_dir"]) / (st["app"] + ".exe")
        rep: dict = {}
        rc = run_split_times(case, st, exe, env, on_proc, on_line, report=rep) if st.get("split_times") else None
        if rc == 0:
            continue
        if rc is not None and rep.get("joined"):
            # some times could not be joined, the others were: carry on with those and say which are missing
            (case / JOIN_SKIPPED).write_text(json.dumps({"skipped": rep["skipped"], "not_tried": rep.get("not_tried", []),
                                                         "joined": len(rep["joined"])}))
            if on_line:
                for sk in rep["skipped"]:
                    on_line(f"WARNING: time {sk['time']} was left out: {sk['reason']}. Its pieces are kept in "
                            f"{UNJOINED}/{sk['time']}")
            continue
        if rc is not None and not st.get("allow_fail"):
            if on_line:
                on_line(f"{st['app']} failed (exit code {rc}) - see {st['log']}")
            failed(st)
            return False
        cmd = [str(exe), *st.get("args", []), "-case", str(case)]
        if st.get("mpi"):
            cmd = [str(Path(info["mpi_dir"]) / "mpiexec.exe"), "-n", str(st["mpi"]), *cmd]
        if on_line:
            on_line("$ " + " ".join((["mpiexec", "-n", str(st["mpi"])] if st.get("mpi") else [])
                                    + [st["app"], *st.get("args", [])]))
        with open(case / st["log"], "w", encoding="utf-8", errors="ignore") as lf:
            proc = subprocess.Popen(cmd, cwd=case, env=env, stdout=lf, stderr=subprocess.STDOUT,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if on_proc:
                on_proc(proc)
            rc = proc.wait()
        if rc != 0 and not st.get("allow_fail"):
            if on_line:
                on_line(f"{st['app']} failed (exit code {rc}) - see {st['log']}")
            failed(st)
            return False
    main = next((st for st in steps if st.get("main")), steps[-1])
    log = case / main["log"]
    ok = log.exists() and re.search(r"^End\s*$", log.read_text(errors="ignore"), re.M) is not None
    if ok:
        (case / "case.done").touch()
    else:
        failed(main)
    return ok
