"""Reads the display fields of some saved times of a transient case in a process of its own.

Usage:  python -m simwb.solvers.openfoam.frame_worker <case> <out_dir> <time> [<time> ...]

Reading a saved time of a few million cells takes about half a minute on one core, and a 40-frame run took
about 20 minutes: results_loader._add_frames starts several of these workers, each reading its own share of the
times (the mesh is read once per worker), and collects what they wrote: one .npz per time, "c:<name>" cell fields and "s:<name>" surface values.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np


def main(argv: list[str]) -> int:
    from . import results_loader as rl

    case, out = Path(argv[0]), Path(argv[1])
    meta = json.loads((case / "simwb_case.json").read_text())
    for name in argv[2:]:
        cells, surf = rl._frame_fields(case, meta, name)
        arrays = {f"c:{k}": (np.asarray(v) if k == "__map" else np.asarray(v, np.float32)) for k, v in cells.items()}
        arrays.update({f"s:{k}": np.asarray(v, np.float32) for k, v in surf.items()})
        with open(out / f"{name}.part", "wb") as f:
            np.savez(f, **arrays)
        (out / f"{name}.part").replace(out / f"{name}.npz")  # (a finished file appears at once)
        print(f"frame {name}", flush=True)
    rl._PM_CACHE.clear()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
