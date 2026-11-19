"""Stable ids for the parts of the window, so that the control API (and tests) can address every control by name.

A widget gets the dynamic property "wid" ("mesh.generate", "results.colormap"); a QAction uses its objectName. The ids are part of the
API: keep them when the look of the window changes."""
from __future__ import annotations


def wid(w, name: str):
    """Give a widget its API id; returns the widget (so it can be used inline)."""
    w.setProperty("wid", name)
    return w


def of(w) -> str | None:
    v = w.property("wid")
    return str(v) if v else None
