"""Peak memory of a piece of work, including every program it starts (gmsh worker, snappyHexMesh, mpiexec ranks...).

    with PeakMemory() as pm:
        ...do the work...
    pm.result()   # {"peak_ram_mb", "added_ram_mb", "own_peak_ram_mb", "peak_working_set_mb"}

The figure is the *private* memory (Windows: PrivateUsage, the memory only that process owns) summed over this process
and all its descendants, sampled every 0.25 s. A working set would count the shared DLLs once per process, which
overstates a parallel run by hundreds of MB. added_ram_mb is the peak minus what the tree held when the work began.
Linux / macOS: resident set size (it includes shared pages: an upper bound). Nothing can be read: the values stay
None (never an invented number). Only the programs of this machine are seen: a WSL or Docker backend runs elsewhere,
so callers must not report the figure for those (see `measurable_backend`).
"""
from __future__ import annotations

import os
import sys
import threading

MB = 2 ** 20
NATIVE_BACKENDS = ("windows", "native", "custom")


def measurable_backend(backend) -> bool:
    """True when the solver runs as a process of this machine (a WSL or Docker one is invisible to this meter)."""
    return backend in NATIVE_BACKENDS


def _windows_tree(root: int):
    import ctypes
    from ctypes import wintypes

    k32, psapi = ctypes.WinDLL("kernel32", use_last_error=True), ctypes.WinDLL("psapi", use_last_error=True)

    class PE(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_char * 260)]

    class PMC(ctypes.Structure):          # PROCESS_MEMORY_COUNTERS_EX
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t), ("PrivateUsage", ctypes.c_size_t)]

    k32.CreateToolhelp32Snapshot.argtypes, k32.CreateToolhelp32Snapshot.restype = [wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE
    k32.Process32First.argtypes = k32.Process32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(PE)]
    k32.Process32First.restype = k32.Process32Next.restype = wintypes.BOOL
    k32.OpenProcess.argtypes, k32.OpenProcess.restype = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE
    k32.CloseHandle.argtypes, k32.CloseHandle.restype = [wintypes.HANDLE], wintypes.BOOL
    k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    k32.GetProcessTimes.restype = wintypes.BOOL
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

    snap = k32.CreateToolhelp32Snapshot(0x2, 0)
    if not snap or snap == wintypes.HANDLE(-1).value:
        return None
    parent, pe = {}, PE()
    pe.dwSize = ctypes.sizeof(PE)
    ok = k32.Process32First(snap, ctypes.byref(pe))
    while ok:
        parent[pe.th32ProcessID] = pe.th32ParentProcessID
        ok = k32.Process32Next(snap, ctypes.byref(pe))
    k32.CloseHandle(snap)

    def created(pid):                      # creation time (100 ns ticks) or None
        h = k32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return None
        c, e, k, u = (wintypes.FILETIME() for _ in range(4))
        ok_ = k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(k), ctypes.byref(u))
        k32.CloseHandle(h)
        return (c.dwHighDateTime << 32 | c.dwLowDateTime) if ok_ else None

    # a child counts only when it started after its parent: a parent id can be reused by an unrelated process
    tree, born = {root}, {root: created(root)}
    grew = True
    while grew:
        grew = False
        for pid, ppid in parent.items():
            if ppid in tree and pid not in tree:
                t, tp = created(pid), born.get(ppid)
                if t is not None and tp is not None and t < tp:
                    continue
                tree.add(pid)
                born[pid] = t
                grew = True
    private = ws = own = 0
    for pid in tree:
        h = k32.OpenProcess(0x1000, False, pid)
        if not h:
            continue
        c = PMC()
        c.cb = ctypes.sizeof(PMC)
        if psapi.GetProcessMemoryInfo(h, ctypes.byref(c), c.cb):
            private += c.PrivateUsage
            ws += c.WorkingSetSize
            if pid == root:
                own = c.PrivateUsage
        k32.CloseHandle(h)
    return {"private": private / MB, "ws": ws / MB, "own": own / MB}


def _posix_tree(root: int):
    import subprocess

    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,rss="], capture_output=True, text=True, timeout=5).stdout
    except Exception:  # noqa: BLE001
        return None
    rows = [tuple(int(x) for x in ln.split()) for ln in out.splitlines() if len(ln.split()) == 3]
    parent = {p: pp for p, pp, _ in rows}
    rss = {p: r for p, _, r in rows}
    tree, grew = {root}, True
    while grew:
        grew = False
        for p, pp in parent.items():
            if pp in tree and p not in tree:
                tree.add(p)
                grew = True
    total = sum(rss.get(p, 0) for p in tree) / 1024
    return {"private": total, "ws": total, "own": rss.get(root, 0) / 1024}


def tree_mb(root: int | None = None):
    """{"private", "ws", "own"} in MB for this process (or `root`) and its descendants now; None when unknown."""
    root = root or os.getpid()
    try:
        return _windows_tree(root) if sys.platform == "win32" else _posix_tree(root)
    except Exception:  # noqa: BLE001
        return None


class PeakMemory:
    def __init__(self, interval: float = 0.25):
        self.interval, self.peak_mb, self.own_peak_mb, self.peak_ws_mb, self.start_mb = interval, None, None, None, None
        self._stop, self._t, self._root = threading.Event(), None, os.getpid()

    def _sample(self):
        s = tree_mb(self._root)
        if s is None:
            return None
        self.peak_mb = s["private"] if self.peak_mb is None else max(self.peak_mb, s["private"])
        self.own_peak_mb = s["own"] if self.own_peak_mb is None else max(self.own_peak_mb, s["own"])
        self.peak_ws_mb = s["ws"] if self.peak_ws_mb is None else max(self.peak_ws_mb, s["ws"])
        return s["private"]

    def _run(self):
        while not self._stop.wait(self.interval):
            self._sample()

    def __enter__(self):
        self.start_mb = self._sample()          # (what this process and its children already held)
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._t:
            self._t.join(timeout=2)
        self._sample()
        return False

    def result(self) -> dict:
        """peak_ram_mb: the largest private total of the process tree; added_ram_mb: that minus what the tree held when
        the work started (the work's own footprint, without the server and the other jobs)."""
        r = lambda v: None if v is None else round(v)  # noqa: E731
        added = None if self.peak_mb is None or self.start_mb is None else max(self.peak_mb - self.start_mb, 0.0)
        return {"peak_ram_mb": r(self.peak_mb), "added_ram_mb": r(added), "own_peak_ram_mb": r(self.own_peak_mb),
                "peak_working_set_mb": r(self.peak_ws_mb)}
