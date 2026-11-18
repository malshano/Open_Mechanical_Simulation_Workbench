"""Readers for CalculiX output: the .frd result file (nodal displacements, stresses) and the .dat print file
(natural frequencies, total support reaction)."""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np


def read_frd(path: Path) -> list:
    """All result blocks of an ASCII .frd: [{"name": "DISP"|"STRESS"|..., "value": step value, "data": (max node id + 1,
    ncomp) array (row = node id)}]. Fixed-width records: ' -1' + node (I10) + values (E12.5 each)."""
    blocks, cur, ncomp, comp_names = [], None, 0, []
    for line in Path(path).read_text(errors="ignore").splitlines():
        if line.startswith("  100C"):
            m = re.search(r"([-+]?\d\.\d+(?:E[-+]\d+)?)", line[12:])
            cur = {"value": float(m.group(1)) if m else 0.0, "name": None, "rows": []}
            continue
        if cur is None:
            continue
        if line.startswith(" -4"):
            f = line.split()
            cur["name"] = f[1]
            ncomp = int(f[2])
            comp_names = []
        elif line.startswith(" -5"):
            comp_names.append(line.split()[1])
        elif line.startswith(" -1"):
            n = int(line[3:13])
            # (the header counts derived components such as ALL that the rows do not store: count by row width)
            k_vals = (len(line.rstrip()) - 13) // 12
            vals = [float(line[13 + 12 * k: 25 + 12 * k]) for k in range(min(k_vals, ncomp))]
            cur["rows"].append((n, vals))
        elif line.startswith(" -3") and cur["name"]:
            nmax = max((n for n, _ in cur["rows"]), default=0)
            arr = np.zeros((nmax + 1, max(ncomp, 1)))
            for n, v in cur["rows"]:
                arr[n, :len(v)] = v
            blocks.append({"name": cur["name"], "value": cur["value"], "data": arr, "components": comp_names})
            cur = None
    return blocks


_NUM = r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?"


def read_frequencies(dat: Path) -> list:
    """Natural frequencies [Hz] of the *FREQUENCY step from the .dat file's eigenvalue table (checked against the
    eigenvalue: f = sqrt(lambda) / 2 pi)."""
    out, on = [], False
    for line in Path(dat).read_text(errors="ignore").splitlines():
        if "E I G E N V A L U E" in line:
            on = True
            continue
        if not on:
            continue
        f = line.split()
        if len(f) == 5 and f[0].isdigit() and all(re.fullmatch(_NUM, x) for x in f[1:]):
            lam, cyc = float(f[1]), float(f[3])
            if lam > 0 and abs(cyc - (lam ** 0.5) / (2 * np.pi)) > 1e-3 * max(cyc, 1e-9):
                raise ValueError("Unexpected eigenvalue table in the CalculiX output (frequency and eigenvalue "
                                 "disagree).")
            out.append(cyc)
    return out


def read_total_force(dat: Path):
    """Total reaction force of the support node set (fx, fy, fz) from the .dat file, or None."""
    lines = Path(dat).read_text(errors="ignore").splitlines()
    for i, line in enumerate(lines):
        if "total force" in line.lower():
            for nxt in lines[i + 1:i + 4]:
                v = re.findall(_NUM, nxt)
                if len(v) >= 3:
                    return [float(x) for x in v[:3]]
    return None


def von_mises(s: np.ndarray) -> np.ndarray:
    """Von Mises stress of ccx's (SXX, SYY, SZZ, SXY, SYZ, SZX) rows."""
    xx, yy, zz, xy, yz, zx = (s[:, i] for i in range(6))
    return np.sqrt(0.5 * ((xx - yy) ** 2 + (yy - zz) ** 2 + (zz - xx) ** 2) + 3 * (xy ** 2 + yz ** 2 + zx ** 2))
