"""Slots for heavy OpenFOAM jobs shared between processes (several runs of the workbench, several detached controllers).

A heavy solve (all cores) takes weight 1.0, a coupled flutter run 0.34 (it spends most of its wall time waiting for the
exchange, one second per step), against a total capacity of 1.0: one heavy job, or up to three coupled runs, at a time.

    with foam_slot.acquire(foam_slot.COUPLED, timeout=3600) as slot:
        ...run...

Every holder is a folder `slot-<pid>-<id>` under the slot root (default <tempdir>/SimWB-Slots, or the folder named by the
environment variable SIMWB_SLOT_DIR) holding slot.json {pid, weight, heartbeat}; a thread of the holder refreshes the
heartbeat every few seconds. The check "does the new weight still fit" and the creation of the folder are done while holding
a lock folder made with mkdir (atomic on every file system), so two processes cannot both take the last place. A slot whose
process is gone, or whose heartbeat is older than `stale_s` (60 s), is reclaimed by the next one that looks.
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path

HEAVY = 1.0
COUPLED = 0.34
CAPACITY = 1.0
_EPS = 0.025  # three coupled runs (3 x 0.34) fit into 1.0


class SlotTimeout(TimeoutError):
    pass


class SlotAborted(Exception):
    pass


def default_root() -> Path:
    return Path(os.environ.get("SIMWB_SLOT_DIR") or Path(tempfile.gettempdir()) / "SimWB-Slots")


def pid_alive(pid) -> bool:
    """Is a process with this id running? (os.kill(pid, 0) would TERMINATE it on Windows, so ask the system instead.)"""
    pid = int(pid or 0)
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ctypes.get_last_error() == 5  # access denied: it exists
        try:
            code = wintypes.DWORD()
            return bool(k32.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def _write_slot(path: Path, data: dict):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data))
    for k in range(10):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if k == 9:
                raise
            time.sleep(0.01)


def _read_slot(d: Path):
    try:
        return json.loads((d / "slot.json").read_text())
    except (OSError, ValueError):
        return None


def _live_slots(root: Path, stale_s: float, reclaim=True):
    """[(folder, info)] of the slots in use; stale ones (dead process, old heartbeat) are removed when `reclaim`."""
    out = []
    if not root.is_dir():
        return out
    now = time.time()
    for d in sorted(root.glob("slot-*")):
        if not d.is_dir():
            continue
        info = _read_slot(d)
        if info is None:  # (being written, or the holder died between mkdir and the first write)
            try:
                young = now - d.stat().st_mtime < 10.0
            except OSError:
                continue
            if young:
                out.append((d, {"pid": 0, "weight": CAPACITY, "heartbeat": now}))
                continue
            stale = True
        else:
            stale = (now - float(info.get("heartbeat", 0)) > stale_s
                     or (int(info.get("pid", 0)) != os.getpid() and not pid_alive(info.get("pid"))))
        if stale:
            if reclaim:
                shutil.rmtree(d, ignore_errors=True)
            continue
        out.append((d, info))
    return out


@contextlib.contextmanager
def _admission(root: Path, poll=0.02, timeout=30.0):
    """The short critical section: a lock folder made with mkdir (a lock older than 10 s is a dead holder's)."""
    lock = root / "_admit"
    t0 = time.time()
    while True:
        try:
            lock.mkdir()
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > 10.0:
                    shutil.rmtree(lock, ignore_errors=True)
                    continue
            except OSError:
                continue
            if time.time() - t0 > timeout:
                raise SlotTimeout("the slot lock could not be taken")
            time.sleep(poll)
    try:
        yield
    finally:
        shutil.rmtree(lock, ignore_errors=True)


class Slot:
    """A held place; released by `release()` (or by leaving the `acquire` block)."""

    def __init__(self, path: Path, weight: float, heartbeat_s: float, label=""):
        self.path, self.weight, self.label = path, weight, label
        self._stop = threading.Event()
        self._hb = heartbeat_s
        self._thread = threading.Thread(target=self._beat, daemon=True)
        self._thread.start()

    def _data(self):
        return {"pid": os.getpid(), "weight": self.weight, "heartbeat": time.time(), "label": self.label}

    def _beat(self):
        while not self._stop.wait(self._hb):
            try:
                _write_slot(self.path / "slot.json", self._data())
            except OSError:
                pass

    def release(self):
        self._stop.set()
        self._thread.join(2.0)
        shutil.rmtree(self.path, ignore_errors=True)


def try_acquire(weight, root=None, stale_s=60.0, heartbeat_s=5.0, capacity=CAPACITY, label="") -> Slot | None:
    """One attempt: a Slot, or None when the weight does not fit now."""
    root = Path(root) if root else default_root()
    root.mkdir(parents=True, exist_ok=True)
    weight = min(float(weight), capacity)
    with _admission(root):
        used = sum(float(i.get("weight", CAPACITY)) for _, i in _live_slots(root, stale_s))
        if used + weight > capacity + _EPS:
            return None
        d = root / f"slot-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        d.mkdir()
        _write_slot(d / "slot.json", {"pid": os.getpid(), "weight": weight, "heartbeat": time.time(), "label": label})
    return Slot(d, weight, heartbeat_s, label)


@contextlib.contextmanager
def acquire(weight, root=None, timeout=None, poll=0.5, stale_s=60.0, heartbeat_s=5.0, capacity=CAPACITY, abort=None,
            label=""):
    """Wait for a place of `weight` and hold it inside the `with` block. timeout (s) raises SlotTimeout; `abort()` returning
    true while waiting raises SlotAborted (so a cancelled run does not stay in the queue)."""
    t0 = time.time()
    slot = None
    while slot is None:
        slot = try_acquire(weight, root, stale_s, heartbeat_s, capacity, label)
        if slot is not None:
            break
        if abort is not None and abort():
            raise SlotAborted()
        if timeout is not None and time.time() - t0 > timeout:
            raise SlotTimeout(f"no free place for a job of weight {weight} within {timeout:.0f} s")
        time.sleep(poll)
    try:
        yield slot
    finally:
        slot.release()


def status(root=None, stale_s=60.0) -> dict:
    """{"capacity", "used", "slots": [info]} of the live slots (stale ones are not counted, and not removed)."""
    root = Path(root) if root else default_root()
    live = [i for _, i in _live_slots(root, stale_s, reclaim=False)]
    return {"capacity": CAPACITY, "used": sum(float(i.get("weight", CAPACITY)) for i in live), "slots": live}
