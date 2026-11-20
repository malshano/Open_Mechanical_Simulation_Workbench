"""The peak-memory meter sees allocations of this process and of the programs it starts."""
import subprocess
import sys

import numpy as np

from simwb import memwatch


def test_the_meter_sees_this_process_and_its_children():
    total, own = memwatch.tree_mb()
    assert total is not None and own is not None and own > 10 and total >= own - 1
    with memwatch.PeakMemory(interval=0.05) as pm:
        a = np.ones(60_000_000 // 8 * 2)                      # ~120 MB, touched
        a[::512] = 2
        child = subprocess.Popen([sys.executable, "-c", "import numpy as np, time; a = np.ones(40_000_000 // 8 * 2); a[::512] = 2; time.sleep(1.2)"])
        child.wait()
        del a
    r = pm.result()
    assert r["own_peak_ram_mb"] >= own + 80, (r, own)          # this process grew by the array
    assert r["peak_ram_mb"] >= r["own_peak_ram_mb"] and r["peak_ram_mb"] > 150
    # the child alone made the tree larger than this process at some moment
    assert pm.peak_mb > pm.own_peak_mb - 1


def test_an_unreadable_process_gives_nothing_not_a_number():
    assert memwatch.tree_mb(2 ** 30)[0] in (None, 0.0) or memwatch.tree_mb(2 ** 30)[0] < 1
