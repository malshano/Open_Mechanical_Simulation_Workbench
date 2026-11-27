"""A helper process that ends when the process that started it is gone.

A worker started by the workbench (the readers of the saved times) used to go on for minutes after the workbench was closed, holding
gigabytes: nothing told it that nobody was waiting for its result. The parent passes its id in SIMWB_PARENT_PID and the worker calls
`start()`; when the parent has ended, the worker ends too."""
from __future__ import annotations

import os
import sys
import threading
import time

ENV = "SIMWB_PARENT_PID"


def parent_alive(pid: int) -> bool:
    if pid <= 0:
        return True
    if sys.platform == "win32":
        import ctypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = ctypes.c_void_p
        k32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        k32.WaitForSingleObject.restype = ctypes.c_uint32
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        h = k32.OpenProcess(0x00100000, False, pid)      # SYNCHRONIZE
        if not h:
            return False                                  # (no such process any more)
        try:
            return k32.WaitForSingleObject(h, 0) == 0x102   # WAIT_TIMEOUT: still running
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def start(interval: float = 2.0, exit_fn=None) -> threading.Thread | None:
    """In a worker: end this process when the parent named in SIMWB_PARENT_PID is gone. None when it names none."""
    try:
        pid = int(os.environ.get(ENV, "0"))
    except ValueError:
        pid = 0
    if pid <= 0:
        return None

    def watch():
        while parent_alive(pid):
            time.sleep(interval)
        (exit_fn or os._exit)(1)

    t = threading.Thread(target=watch, daemon=True, name="simwb-parent-watch")
    t.start()
    return t
