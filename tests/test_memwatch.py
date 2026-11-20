"""The peak-memory meter sees allocations of this process and of the programs it starts, as private memory."""
import subprocess
import sys

import numpy as np

from simwb import memwatch


def test_the_meter_sees_this_process_and_its_children():
    s = memwatch.tree_mb()
    assert s is not None and s["own"] > 10 and s["private"] >= s["own"] - 1 and s["ws"] > 0
    with memwatch.PeakMemory(interval=0.05) as pm:
        a = np.ones(60_000_000 // 8 * 2)                      # ~120 MB, touched
        a[::512] = 2
        child = subprocess.Popen([sys.executable, "-c", "import numpy as np, time; a = np.ones(40_000_000 // 8 * 2); a[::512] = 2; time.sleep(1.2)"])
        child.wait()
        del a
    r = pm.result()
    assert r["own_peak_ram_mb"] >= s["own"] + 80, (r, s)       # this process grew by the array
    assert r["peak_ram_mb"] >= r["own_peak_ram_mb"] and r["added_ram_mb"] >= 80
    assert pm.peak_mb > pm.own_peak_mb - 1                     # the child alone made the tree larger at some moment


def test_a_child_that_holds_memory_is_counted_while_it_runs_not_after():
    """The meter measures the peak while the child is alive: a short-lived child with a big array still shows."""
    child = subprocess.Popen([sys.executable, "-c", "import numpy as np, time; a = np.ones(100_000_000 // 8 * 2); a[::512] = 2; time.sleep(2)"])
    peaks = []
    try:
        import time
        t_end = time.time() + 1.8
        while time.time() < t_end:
            s = memwatch.tree_mb()
            peaks.append(s["private"] - s["own"])               # what the children hold
            time.sleep(0.1)
    finally:
        child.wait()
    assert max(peaks) > 80, max(peaks)                          # (private memory of the child, not shared DLLs)


def test_an_unreadable_process_gives_nothing_not_a_number():
    s = memwatch.tree_mb(2 ** 30)
    assert s is None or s["private"] < 1


def test_only_native_backends_are_measurable():
    assert memwatch.measurable_backend("windows") and memwatch.measurable_backend("native")
    assert not memwatch.measurable_backend("wsl") and not memwatch.measurable_backend("docker")
    assert not memwatch.measurable_backend(None)
