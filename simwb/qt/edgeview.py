"""The sharp edges that the edge refinement will make finer, drawn in the 3D view of the Mesh step: each edge as a thick orange line over the model. Built on
the same preview machinery as the other mesh marks (a few actors that the view clears and redraws); the permanent marking of edges in the viewer is a
separate piece of work."""
from __future__ import annotations

import numpy as np

from .regionview import _pd, _rgb
from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper

COLOUR = "#f97316"


class EdgeView:
    def __init__(self, view):
        self.v = view
        self.actors: list = []
        self._sig = None
        self.count = 0                       # (edges drawn: the control API's state says what the view shows)

    def clear(self):
        was = bool(self.actors)
        self.v._clear(self.actors)
        self._sig = None
        self.count = 0
        if was:
            self.v.render()

    def set(self, lines: list):
        """Draw these polylines (lists of [x, y, z] in metres) - or none."""
        if not lines:
            self.clear()
            return
        sig = (len(lines), round(float(np.asarray(lines[0], float).sum()), 7), round(float(np.asarray(lines[-1], float).sum()), 7))
        if sig == self._sig and self.actors:
            return
        self._sig = sig
        self.v._clear(self.actors)
        pts, cells = [], []
        for ln in lines:
            p = np.asarray(ln, float).reshape(-1, 3)
            cells.append(tuple(range(len(pts), len(pts) + len(p))))
            pts.extend(p.tolist())
        m = vtkPolyDataMapper()
        m.SetInputData(_pd(pts, lines=cells))
        m.SetResolveCoincidentTopologyToPolygonOffset()
        act = vtkActor()
        act.SetMapper(m)
        pr = act.GetProperty()
        pr.SetColor(*_rgb(COLOUR))
        pr.SetLineWidth(4.0 * max(self.v._px()[0], 1.0))
        pr.SetAmbient(1.0)
        pr.SetDiffuse(0.0)
        act.PickableOff()
        self.v.ren.AddActor(act)
        self.actors.append(act)
        self.count = len(lines)
        self.v.render()
