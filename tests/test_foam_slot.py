"""Heavy-job slots (simwb.solvers.openfoam.foam_slot): capacity, stale reclaim, two real processes."""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from simwb.solvers.openfoam import foam_slot as S

ROOT_OF_REPO = Path(__file__).resolve().parents[1]


def test_pid_alive_knows_this_process_and_a_finished_one():
    assert S.pid_alive(os.getpid())
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    assert not S.pid_alive(p.pid) and not S.pid_alive(0) and not S.pid_alive(-5)


def test_heavy_jobs_exclude_each_other_and_coupled_ones_share(tmp_path):
    root = tmp_path / "slots"
    state = {"now": 0, "max": 0}
    lock = threading.Lock()

    def heavy():
        with S.acquire(S.HEAVY, root=root, poll=0.01, timeout=20):
            with lock:
                state["now"] += 1
                state["max"] = max(state["max"], state["now"])
            time.sleep(0.15)
            with lock:
                state["now"] -= 1

    ths = [threading.Thread(target=heavy) for _ in range(4)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    assert state["max"] == 1  # never two at once
    # three coupled runs fit together, a fourth and a heavy one wait
    with S.acquire(S.COUPLED, root=root) as a, S.acquire(S.COUPLED, root=root), S.acquire(S.COUPLED, root=root):
        assert S.status(root)["used"] == pytest.approx(3 * S.COUPLED)
        with pytest.raises(S.SlotTimeout):
            with S.acquire(S.COUPLED, root=root, timeout=0.2, poll=0.02):
                pass
        with pytest.raises(S.SlotTimeout):
            with S.acquire(S.HEAVY, root=root, timeout=0.2, poll=0.02):
                pass
    assert S.status(root)["used"] == 0 and not list(root.glob("slot-*"))  # released on exit
    # a heavy job blocks a coupled one, and an abort() ends the wait
    with S.acquire(S.HEAVY, root=root):
        with pytest.raises(S.SlotAborted):
            with S.acquire(S.COUPLED, root=root, poll=0.02, abort=lambda: True):
                pass


def test_a_slot_is_released_when_the_block_raises(tmp_path):
    with pytest.raises(RuntimeError):
        with S.acquire(S.HEAVY, root=tmp_path):
            raise RuntimeError("boom")
    assert S.try_acquire(S.HEAVY, tmp_path) is not None


def test_stale_slots_are_reclaimed(tmp_path):
    root = tmp_path / "slots"
    root.mkdir()
    old = root / "slot-111-aaaa"  # a live process but a heartbeat from long ago
    old.mkdir()
    (old / "slot.json").write_text(json.dumps({"pid": os.getpid(), "weight": 1.0, "heartbeat": time.time() - 500}))
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    dead = root / "slot-222-bbbb"  # a fresh heartbeat but the process is gone
    dead.mkdir()
    (dead / "slot.json").write_text(json.dumps({"pid": p.pid, "weight": 1.0, "heartbeat": time.time()}))
    empty = root / "slot-333-cccc"  # the holder died between mkdir and the first write
    empty.mkdir()
    os.utime(empty, (time.time() - 100, time.time() - 100))
    assert S.status(root)["used"] == 0  # (status does not count them, and does not delete them)
    assert old.exists()
    with S.acquire(S.HEAVY, root=root, timeout=2, poll=0.02):
        assert not old.exists() and not dead.exists() and not empty.exists()
    # a fresh slot of a live process is NOT reclaimed
    keep = root / "slot-444-dddd"
    keep.mkdir()
    (keep / "slot.json").write_text(json.dumps({"pid": os.getpid(), "weight": 1.0, "heartbeat": time.time()}))
    with pytest.raises(S.SlotTimeout):
        with S.acquire(S.HEAVY, root=root, timeout=0.2, poll=0.02):
            pass
    assert keep.exists()


def test_the_heartbeat_is_refreshed(tmp_path):
    with S.acquire(S.COUPLED, root=tmp_path, heartbeat_s=0.05) as slot:
        h0 = json.loads((slot.path / "slot.json").read_text())["heartbeat"]
        time.sleep(0.3)
        h1 = json.loads((slot.path / "slot.json").read_text())["heartbeat"]
        assert h1 > h0 + 0.1


def test_two_processes_cannot_hold_the_heavy_slot_together(tmp_path):
    root, log = tmp_path / "slots", tmp_path / "log"
    log.mkdir()
    code = (
        "import sys, time, json\n"
        "from simwb.solvers.openfoam import foam_slot as S\n"
        "root, name = sys.argv[1], sys.argv[2]\n"
        "with S.acquire(S.HEAVY, root=root, poll=0.02, timeout=60):\n"
        "    t0 = time.time(); time.sleep(0.6); t1 = time.time()\n"
        "open(sys.argv[3] + '/' + name, 'w').write(json.dumps([t0, t1]))\n")
    env = dict(os.environ, PYTHONPATH=str(ROOT_OF_REPO) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    procs = [subprocess.Popen([sys.executable, "-c", code, str(root), f"p{i}", str(log)], env=env, cwd=ROOT_OF_REPO)
             for i in range(3)]
    for p in procs:
        assert p.wait(60) == 0
    spans = sorted(json.loads((log / f"p{i}").read_text()) for i in range(3))
    for a, b in zip(spans, spans[1:]):
        assert b[0] >= a[1] - 0.01  # the next one starts after the previous one is done
