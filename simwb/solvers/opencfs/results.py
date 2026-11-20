"""Read openCFS results.

openCFS writes its own HDF5 file (results_hdf5/<job>.cfs), which needs h5py (not a workbench dependency). The
simulation XML therefore also asks for ASCII Gmsh output (``<gmsh binaryFormat="no"/>``): VERIFIED on the 2026W
release, results_gmsh/<job>.msh holds one $NodeData block per quantity, frequency and amplitude/phase:

    $NodeData
    1                                   one string tag: the name  "acouPressure (Pa)-amp" / "...-phase"
    "mechDisplacement (m)-amp"
    1                                   one real tag: the frequency (Hz) of the harmonic step
    500
    3                                   three integer tags: step index, components (1 scalar / 3 vector), node count
    0
    3
    1304
    1 8.6699e-12                        node id (the id in the mesh file we wrote) followed by the components;
     8.71582e-12                        a vector's components continue on the next lines
     2.58131e-06
    ...
    $EndNodeData

Amplitudes have six significant digits. Only nodes of the region carry data (pressure: the fluid nodes,
displacement: the solid nodes).
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np


def read_nodedata(path) -> list:
    """All $NodeData blocks of an ASCII Gmsh file: [{"name", "unit", "kind", "freq", "step", "ncomp", "ids", "data"}].

    kind is "amp" or "phase" (openCFS's default complex format; phase in degrees). ids are 1-based node ids of the
    file; data is (n, ncomp) float.
    """
    blocks = []
    cur = None
    with open(path, "r", errors="replace") as f:
        for line in f:
            s = line.strip()
            if s == "$NodeData":
                cur = []
            elif s == "$EndNodeData" and cur is not None:
                blocks.append(_block(cur))
                cur = None
            elif cur is not None:
                cur.append(s)
    return blocks


def _block(lines: list) -> dict:
    if len(lines) < 8:
        raise ValueError("A $NodeData block of the openCFS result file is too short.")
    name = lines[1].strip('"')
    freq = float(lines[3])
    step, ncomp, nn = int(lines[5]), int(lines[6]), int(lines[7])
    toks = np.array(" ".join(lines[8:]).split(), dtype=float)
    if toks.size != nn * (1 + ncomp):
        raise ValueError(f"$NodeData '{name}' holds {toks.size} numbers, expected {nn * (1 + ncomp)}.")
    toks = toks.reshape(nn, 1 + ncomp)
    m = re.match(r"^(\S+)\s*(?:\(([^)]*)\))?-(amp|phase|real|imag)$", name)
    q, unit, kind = (m.group(1), m.group(2) or "", m.group(3)) if m else (name, "", "")
    return {"name": q, "unit": unit, "kind": kind, "freq": freq, "step": step, "ncomp": ncomp,
            "ids": toks[:, 0].astype(np.int64), "data": toks[:, 1:]}


def amplitudes(blocks: list, quantity: str, n_nodes: int):
    """Amplitude per frequency and node of a quantity ("acouPressure", "mechDisplacement").

    Returns (freqs (nf,), amp (nf, n_nodes)) where a vector's amplitude is the length of the complex vector,
    sqrt(sum_i |u_i|^2) (each component's amplitude is what openCFS writes); nodes without data are 0.
    A frequency with no block of this quantity (the region is absent) gives an all-zero row.
    """
    sel = [b for b in blocks if b["name"] == quantity and b["kind"] == "amp"]
    freqs = sorted({b["freq"] for b in blocks})
    row = {f: i for i, f in enumerate(freqs)}
    out = np.zeros((len(freqs), n_nodes))
    for b in sel:
        ids = b["ids"] - 1
        ok = (ids >= 0) & (ids < n_nodes)
        out[row[b["freq"]], ids[ok]] = np.sqrt((b["data"][ok] ** 2).sum(1))
    return np.array(freqs), out
