"""How fast each physical core is when they all work at once, for splitting a parallel run to suit the machine.

Hybrid processors (Intel P/E cores, AMD Zen 5 / Zen 5c, Arm big.LITTLE) have cores of different speed. An equal split
of the mesh makes every time step wait for the slowest core: the fast cores finish their part and idle (MPI's busy
wait still shows them at 100 % in the task manager). With the speed of every core known, each process gets a share
of the cells in proportion to its core's speed and is pinned to that core, so they all finish together.

The speeds are measured, not guessed from the clock: one process per physical core runs the same memory-heavy
numpy loop (like the solver, limited by memory traffic as much as by the clock) for a couple of seconds, all at
the same time, so the boost and power limits of a fully loaded chip are included. The result is kept per machine.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

CACHE = Path(os.environ.get("SIMWB_CPU_CACHE") or Path.home() / ".simwb" / "core_speeds.json")
UNEVEN = 0.08  # cores within 8 % of each other count as equal (an even split; no weights, no pinning)

_WORK = r"""
import ctypes, sys, time
import numpy as np
mask, start, secs = int(sys.argv[1]), float(sys.argv[2]), float(sys.argv[3])
if mask:
    k = ctypes.windll.kernel32
    k.SetProcessAffinityMask(k.GetCurrentProcess(), ctypes.c_size_t(mask))
a = np.random.rand(1 << 21); b = np.random.rand(1 << 21); c = np.empty_like(a)  # 3 x 16 MB: past the caches
while time.time() < start:
    time.sleep(0.005)
n, end = 0, start + secs
while time.time() < end:
    np.multiply(a, 1.0001, out=c); c += b; np.sqrt(c, out=c); a, c = c, a
    n += 1
print(n)
"""


def core_layout() -> list:
    """Physical cores in the operating system's order: [{"mask": logical-processor bit mask, "cls": efficiency
    class (higher = faster core type)}]. Windows only (that is where the MS-MPI ranks are pinned); [] elsewhere."""
    if sys.platform != "win32":
        return []
    try:
        import ctypes

        k32, n = ctypes.windll.kernel32, ctypes.c_ulong(0)
        k32.GetLogicalProcessorInformationEx(0, None, ctypes.byref(n))  # RelationProcessorCore
        buf = ctypes.create_string_buffer(n.value)
        if not n.value or not k32.GetLogicalProcessorInformationEx(0, buf, ctypes.byref(n)):
            return []
        raw, off, out = buf.raw, 0, []
        while off < n.value:
            size = int.from_bytes(raw[off + 4:off + 8], "little")
            # PROCESSOR_RELATIONSHIP: Flags @8, EfficiencyClass @9, GroupCount @30, GROUP_AFFINITY[0].Mask @32
            out.append({"mask": int.from_bytes(raw[off + 32:off + 40], "little"), "cls": raw[off + 9]})
            off += size or n.value
        return out
    except Exception:  # noqa: BLE001
        return []


def _key(cores: list) -> str:
    return "|".join(f"{c['mask']:x}:{c['cls']}" for c in cores) + f"|{os.environ.get('PROCESSOR_IDENTIFIER', '')}"


def measure(cores: list, secs: float = 2.5) -> list:
    """Relative speed of every core (the fastest = 1), all cores loaded at once."""
    start = time.time() + 1.5  # every worker loaded and waiting before the clock starts
    procs = [subprocess.Popen([sys.executable, "-I", "-c", _WORK, str(c["mask"]), repr(start), str(secs)],
                              stdout=subprocess.PIPE, text=True,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) for c in cores]
    counts = []
    for p in procs:
        out, _ = p.communicate(timeout=60)
        counts.append(float(out.strip() or 0))
    top = max(counts) or 1.0
    return [round(x / top, 3) for x in counts]


def core_speeds(refresh: bool = False, log=None) -> list:
    """[{"mask", "cls", "speed"}] for this machine, measured once and kept (measured again when the cores change)."""
    cores = core_layout()
    if len(cores) < 2:
        return []
    key = _key(cores)
    if not refresh:
        try:
            saved = json.loads(CACHE.read_text())
            if saved.get("key") == key:
                return saved["cores"]
        except (OSError, ValueError, KeyError):
            pass
    if log:
        log("Measuring the speed of every CPU core under full load (once for this computer)")
    speeds = measure(cores)
    # cores of one type share a speed (the mean of their type): one slow measurement does not skew a core
    by_cls: dict = {}
    for c, s in zip(cores, speeds):
        by_cls.setdefault(c["cls"], []).append(s)
    out = [dict(c, speed=round(sum(by_cls[c["cls"]]) / len(by_cls[c["cls"]]), 3), measured=s)
           for c, s in zip(cores, speeds)]
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps({"key": key, "cores": out}, indent=1))
    except OSError:
        pass
    if log:
        log("Core speeds: " + " ".join(f"{c['speed']:.2f}" for c in out))
    return out


def rank_weights(n_procs: int, speeds: list | None = None) -> list | None:
    """Share of the cells for ranks 0..n-1 when rank i runs on physical core i (MS-MPI -affinity_layout seq:P),
    scaled to a mean of 1. None when the cores are alike (an even split, no pinning needed) or unknown."""
    sp = speeds if speeds is not None else core_speeds()
    if not sp or n_procs < 2 or n_procs > len(sp):
        return None
    w = [c["speed"] for c in sp[:n_procs]]
    if min(w) <= 0 or max(w) / min(w) < 1 + UNEVEN:
        return None
    m = sum(w) / len(w)
    return [round(x / m, 4) for x in w]
