"""Peak memory of a piece of work, including every program it starts (gmsh worker, snappyHexMesh, mpiexec ranks...).

    with PeakMemory() as pm:
        ...do the work...
    pm.peak_mb       # the largest total working set of this process and all its descendants, sampled every 0.25 s
    pm.own_peak_mb   # the largest working set of this process alone

No dependency: Windows reads the process list with the toolhelp snapshot and the working sets with psapi (ctypes);
Linux / macOS read /proc or `ps`. Where nothing can be read the values stay None (never an invented number).
"""
from __future__ import annotations

import os
import sys
import threading
import time


def _windows_tree_mb(root: int):
    import ctypes
    from ctypes import wintypes

    k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi

    class PE(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_char * 260)]

    class PMC(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t)]

    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)
    if snap in (None, wintypes.HANDLE(-1).value, -1):
        return None, None
    parent, pe = {}, PE()
    pe.dwSize = ctypes.sizeof(PE)
    ok = k32.Process32First(snap, ctypes.byref(pe))
    while ok:
        parent[pe.th32ProcessID] = pe.th32ParentProcessID
        ok = k32.Process32Next(snap, ctypes.byref(pe))
    k32.CloseHandle(snap)
    tree, grew = {root}, True
    while grew:
        grew = False
        for pid, ppid in parent.items():
            if ppid in tree and pid not in tree:
                tree.add(pid)
                grew = True
    total = own = 0
    k32.OpenProcess.restype = wintypes.HANDLE
    for pid in tree:
        h = k32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            continue
        c = PMC()
        c.cb = ctypes.sizeof(PMC)
        if psapi.GetProcessMemoryInfo(h, ctypes.byref(c), c.cb):
            total += c.WorkingSetSize
            if pid == root:
                own = c.WorkingSetSize
        k32.CloseHandle(h)
    return total / 2 ** 20, own / 2 ** 20


def _posix_tree_mb(root: int):
    import subprocess

    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,rss="], capture_output=True, text=True, timeout=5).stdout
    except Exception:  # noqa: BLE001
        return None, None
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
    return sum(rss.get(p, 0) for p in tree) / 1024, rss.get(root, 0) / 1024


def tree_mb(root: int | None = None):
    """(total MB of this process and its descendants, MB of this process alone) now; (None, None) when unknown."""
    root = root or os.getpid()
    try:
        return _windows_tree_mb(root) if sys.platform == "win32" else _posix_tree_mb(root)
    except Exception:  # noqa: BLE001
        return None, None


class PeakMemory:
    def __init__(self, interval: float = 0.25):
        self.interval, self.peak_mb, self.own_peak_mb = interval, None, None
        self._stop, self._t, self._root = threading.Event(), None, os.getpid()

    def _sample(self):
        total, own = tree_mb(self._root)
        if total is not None:
            self.peak_mb = total if self.peak_mb is None else max(self.peak_mb, total)
            self.own_peak_mb = own if self.own_peak_mb is None else max(self.own_peak_mb, own)

    def _run(self):
        while not self._stop.wait(self.interval):
            self._sample()

    def __enter__(self):
        self._sample()
        self.start_mb = self.peak_mb          # (what this process and its children already held)
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
        """peak_ram_mb: the largest total of the process tree; added_ram_mb: that minus what the tree held when the
        work started (the work's own footprint, without the server and the other jobs)."""
        r = lambda v: None if v is None else round(v)  # noqa: E731
        start = getattr(self, "start_mb", None)
        added = None if self.peak_mb is None or start is None else max(self.peak_mb - start, 0.0)
        return {"peak_ram_mb": r(self.peak_mb), "added_ram_mb": r(added), "own_peak_ram_mb": r(self.own_peak_mb)}
