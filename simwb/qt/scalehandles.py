"""Resize handles of a refinement region in the 3D view: an arrow with a square end on each face of a box (a cylinder: one at each end and one on its
side for the radius), pointing out of the face. Dragging one moves that face along its arrow (the opposite face stays); Shift snaps to round steps.
The square ends tell them from the move arrows of the gizmo (round tips) that are drawn at the region's middle.

Like the gizmo and the plane's handle it works from pixel positions and the camera alone: press / drag_to / release (device pixels of the 3D view,
origin top-left), which the Viewport's mouse handlers call. `callback(key, delta, final)`: delta (m) is how far the face was moved outwards."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt
from vtkmodules.util.numpy_support import numpy_to_vtk, numpy_to_vtkIdTypeArray
from vtkmodules.vtkCommonCore import vtkPoints
from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkPolyData
from vtkmodules.vtkFiltersCore import vtkTubeFilter
from vtkmodules.vtkFiltersSources import vtkCubeSource
from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper

from .gizmo import RING_COLORS, _rgb

GRAB_PX = 12.0
CAP_PX = 14.0                             # the square end counts as part of the handle: this far (window pixels) from its centre


class ScaleHandles:
    def __init__(self, view):
        self.v = view
        self.on = False
        self.handles: list = []             # [{key, pos, dir}] (world, m)
        self.length = 0.1                   # how far a handle's arrow reaches out of its face (m)
        self.callback = None
        self.snap_m = 0.0
        self.drag: dict | None = None
        self.hover: int | None = None
        self.actors: list = []

    @property
    def _g(self):
        return self.v.gizmo                  # (its projection helpers work from the camera alone)

    # ------------------------------------------------------------------ show / hide
    def set(self, handles: list, length: float, callback=None, snap_m: float = 0.0):
        self.handles = [{"key": h["key"], "pos": np.asarray(h["pos"], float), "dir": np.asarray(h["dir"], float)} for h in handles]
        self.length = max(float(length), 1e-6)
        if callback is not None:
            self.callback = callback
        self.snap_m = float(snap_m)
        self.on = bool(self.handles)
        self._draw()

    def clear(self):
        was = self.on or bool(self.actors)
        self.on = False
        self.drag = None
        self.hover = None
        self.handles = []
        self.callback = None
        self.v._clear(self.actors)
        if was:
            self.v.render()

    def _colour(self, key) -> str:
        return RING_COLORS[int(key[0]) % 3] if isinstance(key[0], int) else RING_COLORS[0 if key[0] == "len" else 1]

    def _draw(self):
        v = self.v
        v._clear(self.actors)
        if not self.on:
            return
        for i, h in enumerate(self.handles):
            hot = self.hover == i or (self.drag is not None and self.drag["i"] == i)
            p, d = h["pos"], h["dir"]
            tip = p + d * self.length
            col = _rgb(self._colour(h["key"]))
            pd = vtkPolyData()
            pts = vtkPoints()
            pts.SetData(numpy_to_vtk(np.ascontiguousarray([p, tip], np.float64), deep=True))
            pd.SetPoints(pts)
            ca = vtkCellArray()
            ca.ImportLegacyFormat(numpy_to_vtkIdTypeArray(np.array([2, 0, 1], np.int64), deep=True))
            pd.SetLines(ca)
            tube = vtkTubeFilter()
            tube.SetInputData(pd)
            tube.SetRadius(self.length * (0.05 if hot else 0.035))
            tube.SetNumberOfSides(8)
            tube.Update()
            self._actor(tube.GetOutputPort(), col, 1.0 if hot else 0.9)
            cube = vtkCubeSource()                              # (the square end)
            s = self.length * (0.34 if hot else 0.28)
            cube.SetXLength(s)
            cube.SetYLength(s)
            cube.SetZLength(s)
            cube.SetCenter(*tip)
            self._actor(cube.GetOutputPort(), col, 1.0)
        v.render()

    def _actor(self, port, col, opacity):
        m = vtkPolyDataMapper()
        m.SetInputConnection(port)
        a = vtkActor()
        a.SetMapper(m)
        pr = a.GetProperty()
        pr.SetColor(*col)
        pr.SetOpacity(opacity)
        pr.SetAmbient(1.0)
        pr.SetDiffuse(0.0)
        a.PickableOff()
        self.v.ren2.AddActor(a)                                  # (the top layer: the model never hides a handle)
        self.actors.append(a)

    # ------------------------------------------------------------------ the mouse (device pixels, origin top-left)
    def _px(self, p):
        return np.array(self._g.project(p)[:2])

    def pick(self, x: float, y: float) -> int | None:
        if not self.on:
            return None
        dpr = self._g.dpr()
        q = np.array([x, y], float)
        best, bd = None, GRAB_PX * dpr
        for i, h in enumerate(self.handles):
            a, b = self._px(h["pos"]), self._px(h["pos"] + h["dir"] * self.length)
            ab = b - a
            t = 0.0 if not ab @ ab else float(np.clip((q - a) @ ab / (ab @ ab), 0.0, 1.0))
            d = min(float(np.linalg.norm(q - (a + t * ab))), float(np.linalg.norm(q - b)) - (CAP_PX - GRAB_PX) * dpr)
            if d < bd:
                best, bd = i, d
        return best

    def _along(self, origin, direction, x: float, y: float):
        """The distance along the arrow's line (m) of the point nearest the pointer's line."""
        o, d = self._g.ray(x, y)
        n = np.asarray(direction, float)
        w0 = o - np.asarray(origin, float)
        b = float(d @ n)
        den = 1 - b * b
        if den < 1e-6:
            return None                                          # (looking straight along the arrow: it cannot be dragged)
        return float((n @ w0 - b * (d @ w0)) / den)

    def hover_at(self, x: float, y: float):
        i = self.pick(x, y) if self.drag is None else self.hover
        if self.drag is None and i != self.hover:
            self.hover = i
            self._g._cursor(Qt.OpenHandCursor if i is not None else None)
            self._draw()
        return None if i is None else ("scale", i)

    def press(self, x: float, y: float) -> bool:
        i = self.pick(x, y)
        if i is None:
            return False
        h = self.handles[i]
        t0 = self._along(h["pos"], h["dir"], x, y)
        if t0 is None:
            return False
        self.drag = {"i": i, "key": h["key"], "origin": h["pos"].copy(), "dir": h["dir"].copy(), "t0": t0, "delta": 0.0}
        self.hover = i
        self._g._cursor(Qt.ClosedHandCursor)
        if self.callback:
            self.callback(h["key"], 0.0, False)
        return True

    def drag_to(self, x: float, y: float, shift: bool = False):
        d = self.drag
        if d is None:
            return None
        t = self._along(d["origin"], d["dir"], x, y)
        if t is None:
            return None
        delta = t - d["t0"]
        if shift and self.snap_m > 0:
            delta = round(delta / self.snap_m) * self.snap_m
        d["delta"] = delta
        if self.callback:
            self.callback(d["key"], delta, False)
        return delta

    def release(self, x: float, y: float, shift: bool = False):
        d = self.drag
        if d is None:
            return
        self.drag_to(x, y, shift)
        self.drag = None
        self.hover = None
        self._g._cursor(None)
        if self.callback:
            self.callback(d["key"], d["delta"], True)
        self._draw()
