"""Memory cap for worker processes (mesher), so one oversized mesh cannot exhaust the computer's memory.

Windows: the worker is started suspended, put in a Job Object with a job-wide memory limit (the venv launcher and
the real interpreter it starts are both inside), then resumed. The job is killed when its handle closes, so no
orphaned mesher survives its parent. Elsewhere: RLIMIT_AS in the child.
"""
from __future__ import annotations

import os
import subprocess
import sys


def limit_bytes() -> int:
    """Default cap: SIMWB_MEM_LIMIT_GB, else 80 % of the memory free now (at most 60 % of all, at least 1 GB)."""
    env = os.environ.get("SIMWB_MEM_LIMIT_GB")
    if env:
        return int(float(env) * 2 ** 30)
    total, free = memory()
    return int(max(min(0.6 * total, 0.8 * free), 2 ** 30))


def total_memory() -> int:
    return memory()[0]


def memory() -> tuple[int, int]:
    """(physical memory, available memory) in bytes."""
    if sys.platform == "win32":
        import ctypes

        class MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        m = MS()
        m.dwLength = ctypes.sizeof(MS)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return int(m.ullTotalPhys), int(m.ullAvailPhys)
    try:
        pg = os.sysconf("SC_PAGE_SIZE")
        return pg * os.sysconf("SC_PHYS_PAGES"), pg * os.sysconf("SC_AVPHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return 8 * 2 ** 30, 4 * 2 ** 30


class LimitedPopen:
    """Start a subprocess with a memory cap. Use .proc like a Popen; call .hit_limit() after it exits."""

    def __init__(self, args, limit: int | None = None, **kw):
        self.limit = limit or limit_bytes()
        self._job = None
        if sys.platform == "win32":
            self.proc = subprocess.Popen(args, creationflags=kw.pop("creationflags", 0) | 0x4, **kw)  # SUSPENDED
            try:
                self._job = _win_job(self.proc, self.limit)
            finally:
                import ctypes

                ctypes.windll.ntdll.NtResumeProcess(int(self.proc._handle))
        else:
            import resource

            def pre():
                resource.setrlimit(resource.RLIMIT_AS, (self.limit, self.limit))

            self.proc = subprocess.Popen(args, preexec_fn=pre, **kw)

    def hit_limit(self) -> bool:
        """True if the process tree used (nearly) all of its memory allowance."""
        if self._job is None:
            return False
        import ctypes

        info = _ExtLimit()
        ok = ctypes.windll.kernel32.QueryInformationJobObject(self._job, 9, ctypes.byref(info), ctypes.sizeof(info), None)
        return bool(ok) and info.PeakJobMemoryUsed >= 0.95 * self.limit

    def close(self):
        if self._job is not None:
            import ctypes

            ctypes.windll.kernel32.CloseHandle(self._job)  # kills anything still running in the job
            self._job = None


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount",
                                                      "OtherOperationCount", "ReadTransferCount",
                                                      "WriteTransferCount", "OtherTransferCount")]

    class _ExtLimit(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _Basic), ("IoInfo", _IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    def _win_job(proc, limit):
        k32 = ctypes.windll.kernel32
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        job = k32.CreateJobObjectW(None, None)
        info = _ExtLimit()
        # JOB_MEMORY | KILL_ON_JOB_CLOSE
        info.BasicLimitInformation.LimitFlags = 0x200 | 0x2000
        info.JobMemoryLimit = limit
        k32.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(info), ctypes.sizeof(info))
        if not k32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(int(proc._handle))):
            k32.CloseHandle(wintypes.HANDLE(job))
            return None
        return wintypes.HANDLE(job)
else:
    _ExtLimit = None
