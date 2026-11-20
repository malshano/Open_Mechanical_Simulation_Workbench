"""MOOSE (https://mooseframework.inl.gov, Idaho National Laboratory, LGPL-2.1) as an optional engine for nonlinear
thermo-mechanics, contact, diffusion / phase change and fully implicit coupling.

It is offered natively on Linux and macOS only. MOOSE has no native Windows build (its official Windows route is WSL,
which the workbench does not use), so on Windows the engine is hidden with a stated reason; docs/MOOSE_WINDOWS.md
describes the timeboxed investigation of an MSYS2 build.

The workbench never links MOOSE: it writes an input file, runs the separate application (api.run_moose) and reads
the Exodus / CSV files it writes. Nothing here has side effects at import, and everything imports without MOOSE.
"""
from __future__ import annotations

from .api import run_moose  # noqa: F401
from .installer import detect, supported  # noqa: F401
