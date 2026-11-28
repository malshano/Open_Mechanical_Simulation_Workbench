"""What tells the person that the Results step is drawing the whole model made of the solved part and its mirror images (simwb/mirrorview.py): a
"mirrored copy" entry in the legend, and a faint outline of every symmetry plane, sized to what is drawn. Added to the view's overlay, so that they go
with the drawing they belong to."""
from __future__ import annotations

import numpy as np
from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper

from .bcs import SYM_COLOUR
from .regionview import _pd, _rgb


def plane_rectangle(plane: dict, lo, hi, pad: float = 1.04):
    """(4 corners, as a closed loop) of a rectangle in `plane` that covers the box lo..hi seen along the plane's normal."""
    n = np.asarray(plane["normal"], float)
    n = n / (np.linalg.norm(n) or 1.0)
    p0 = np.asarray(plane["point"], float)
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    seed = np.array([0.0, 0.0, 1.0]) if abs(n[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    u = np.cross(n, seed)
    u /= np.linalg.norm(u)
    v = np.cross(n, u)
    c = (lo + hi) / 2
    c = c - float((c - p0) @ n) * n                          # (the middle of the drawing, put on the plane)
    d = corners - c
    a, b = d @ u, d @ v
    ua, ub = (a.min() + a.max()) / 2, (b.min() + b.max()) / 2
    ha, hb = pad * (a.max() - a.min()) / 2, pad * (b.max() - b.min()) / 2
    c = c + ua * u + ub * v
    return [c + s * ha * u + t * hb * v for s, t in ((-1, -1), (1, -1), (1, 1), (-1, 1))]


def mark(view, planes: list):
    """The legend entry and the plane outlines for `planes` (none: both are taken away)."""
    view.legend.set_extra([{"color": SYM_COLOUR, "name": "mirrored copy"}] if planes else [])
    ov = getattr(view, "_ov", None)
    if not planes or not ov or not len(ov["pos"]):
        return
    pts = ov["pos"]
    lo, hi = pts.min(0), pts.max(0)
    width = 1.6 * max(view._px()[0], 1.0)
    for pl in planes:
        c = plane_rectangle(pl, lo, hi)
        for polys, lines, opacity, wire in (([(0, 1, 2), (0, 2, 3)], None, 0.05, False), (None, [(0, 1), (1, 2), (2, 3), (3, 0)], 0.6, True)):
            pd = _pd([p.tolist() for p in c], polys=polys or (), lines=lines or ())
            m = vtkPolyDataMapper()
            m.SetInputData(pd)
            a = vtkActor()
            a.SetMapper(m)
            pr = a.GetProperty()
            pr.SetColor(*_rgb(SYM_COLOUR))
            pr.SetOpacity(opacity)
            pr.SetAmbient(1.0)
            pr.SetDiffuse(0.0)
            pr.SetSpecular(0.0)
            pr.SetLighting(False)
            if wire:
                pr.SetLineWidth(width)
            a.PickableOff()
            view.ren.AddActor(a)
            view.overlay_actors.append(a)
