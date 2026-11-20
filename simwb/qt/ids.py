"""Stable ids for the parts of the window, so that the control API (and tests) can address every control by name, and the two things
every control needs to be usable:

* a SIGNIFIER - it says what it is: its text, else its tooltip, else a name made from its id (so a combo box or a spin box never
  stands there unlabelled);
* an AFFORDANCE - it looks and acts as something to use: buttons, check boxes, combo boxes and sliders get the pointing-hand cursor.

A widget gets the dynamic property "wid" ("mesh.generate", "results.colormap"); a QAction uses its objectName. The ids are part of the
API: keep them when the look of the window changes."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractButton, QAbstractSpinBox, QComboBox, QSlider


def _humanize(name: str) -> str:
    last = name.split(".")[-1].replace("_", " ").replace("-", " ").strip()
    return (last[:1].upper() + last[1:]) if last else name


def wid(w, name: str):
    """Give a widget its API id (and make it a labelled, clickable-looking control); returns the widget so it can be used inline."""
    w.setProperty("wid", name)
    if isinstance(w, (QAbstractButton, QComboBox, QSlider)):
        w.setCursor(Qt.PointingHandCursor)
    if isinstance(w, (QAbstractSpinBox, QComboBox, QSlider)) and not w.toolTip():
        w.setToolTip(_humanize(name))
    if not w.accessibleName():
        text = w.text() if isinstance(w, QAbstractButton) else ""
        w.setAccessibleName(text.replace("&", "") or w.toolTip() or _humanize(name))
    return w


def of(w) -> str | None:
    v = w.property("wid")
    return str(v) if v else None
