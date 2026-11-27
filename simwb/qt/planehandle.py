"""The preview of a plane in the 3D view, with a handle to move it: a translucent amber sheet with an outline across the model, an arrow from its
middle into the side that is kept, and a ball in the middle. Dragging the arrow (or the ball) moves the plane along its normal - the axis it is
across, or at right angles to the face it follows - and reports the new position; holding Shift snaps the move to round steps.

It is the symmetry planes' draft (Setup step): nothing is rebuilt while it is placed, the panel only applies it when the person presses Apply.
Like the gizmo it works from pixel positions and the camera alone: `press(x, y)`, `drag_to(x, y, shift)`, `release(x, y)` (device pixels of the
3D view, origin top-left), which the Viewport's mouse handlers call."""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import Qt
from vtkmodules.util.numpy_support import numpy_to_vtk, numpy_to_vtkIdTypeArray
from vtkmodules.vtkCommonCore import vtkPoints
from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkPolyData
from vtkmodules.vtkFiltersCore import vtkTubeFilter
from vtkmodules.vtkFiltersSources import vtkConeSource, vtkSphereSource
from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper

COLOR = "#ffb703"                      # (the draft colour of the symmetry planes)
GRAB_PX = 12.0                         # how close (window pixels) the pointer must be to the arrow to take it
BALL_PX = 16.0


def _rgb(h: str):
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (1, 3, 5))


def _poly(pts, closed: bool) -> vtkPolyData:
    pts = np.asarray(pts, float)
    pd = vtkPolyData()
    p = vtkPoints()
    p.SetData(numpy_to_vtk(np.ascontiguousarray(pts), deep=True))
    pd.SetPoints(p)
    n = len(pts)
    idx = list(range(n)) + ([0] if closed else [])
    ca = vtkCellArray()
    ca.ImportLegacyFormat(numpy_to_vtkIdTypeArray(np.array([len(idx)] + idx, np.int64), deep=True))
    pd.SetLines(ca)
    return pd


def plane_axes(n: np.ndarray):
    """Two unit vectors that span the plane with unit normal n."""
    a = np.array([1.0, 0.0, 0.0]) if abs(n[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    u = np.cross(n, a)
    u /= np.linalg.norm(u)
    return u, np.cross(n, u)


class PlaneHandle:
    def __init__(self, view):
        self.v = view
        self.on = False
        self.point = np.zeros(3)
        self.normal = np.array([1.0, 0.0, 0.0])
        self.box = None                     # the parts' box (x0, y0, z0, x1, y1, z1): the sheet is sized to it
        self.callback = None                # callback(point (m), final)
        self.snap_m = 0.0                   # the round step of a Shift-drag (m)
        self.drag: dict | None = None
        self.hover = False
        self.main: list = []                # the sheet and its outline (in the model's layer: the model hides them)
        self.top: list = []                 # the arrow and the ball (the top layer: always grabbable)

    # ------------------------------------------------------------------ geometry
    @property
    def _g(self):
        return self.v.gizmo                  # (its projection helpers work from the camera alone)

    def _half(self) -> float:
        b = np.asarray(self.box, float) if self.box is not None else np.array([-1, -1, -1, 1, 1, 1.0])
        return 0.5 * float(np.linalg.norm(b[3:] - b[:3])) or 1.0

    def _middle(self) -> np.ndarray:
        """Where the arrow starts: the parts' middle, put on the plane."""
        b = np.asarray(self.box, float) if self.box is not None else np.zeros(6)
        c = (b[:3] + b[3:]) / 2
        return c - float((c - self.point) @ self.normal) * self.normal

    def _tip(self) -> np.ndarray:
        return self._middle() + self.normal * 0.35 * self._half()

    # ------------------------------------------------------------------ show / hide
    def set(self, point, normal, box, callback=None, snap_m: float = 0.0):
        n = np.asarray(normal, float)
        self.point = np.asarray(point, float)
        self.normal = n / (np.linalg.norm(n) or 1.0)
        self.box = box
        if callback is not None:
            self.callback = callback
        self.snap_m = float(snap_m)
        self.on = True
        self._draw()

    def clear(self):
        was = self.on or bool(self.main or self.top)
        self.on = False
        self.drag = None
        self.hover = False
        self.callback = None
        self.v._clear(self.main)
        self.v._clear(self.top)
        if was:
            self.v.render()

    def _tube(self, pts, radius, opacity, closed, layer, ren):
        tube = vtkTubeFilter()
        tube.SetInputData(_poly(pts, closed))
        tube.SetRadius(radius)
        tube.SetNumberOfSides(8)
        tube.Update()
        self._add(tube, opacity, layer, ren)

    def _add(self, src, opacity, layer, ren, color=COLOR, pos=None, mapper_input=None):
        m = vtkPolyDataMapper()
        if mapper_input is not None:
            m.SetInputData(mapper_input)
        else:
            m.SetInputConnection(src.GetOutputPort())
        act = vtkActor()
        act.SetMapper(m)
        pr = act.GetProperty()
        pr.SetColor(*_rgb(color))
        pr.SetOpacity(opacity)
        pr.SetAmbient(1.0)
        pr.SetDiffuse(0.0)
        act.PickableOff()
        if pos is not None:
            act.SetPosition(*pos)
        ren.AddActor(act)
        layer.append(act)
        return act

    def _draw(self):
        v = self.v
        v._clear(self.main)
        v._clear(self.top)
        if not self.on:
            return
        n = self.normal
        u, w = plane_axes(n)
        c = self._middle()
        h = self._half()
        corners = np.array([c + u * h + w * h, c - u * h + w * h, c - u * h - w * h, c + u * h - w * h])
        sheet = _poly(corners, True)                     # (the four corners as one filled quad, no lines)
        ca = vtkCellArray()
        ca.ImportLegacyFormat(numpy_to_vtkIdTypeArray(np.array([4, 0, 1, 2, 3], np.int64), deep=True))
        sheet.SetPolys(ca)
        sheet.SetLines(vtkCellArray())
        self._add(None, 0.20, self.main, v.ren, mapper_input=sheet)
        self._tube(corners, 0.004 * h, 1.0, True, self.main, v.ren)
        hot = self.hover or self.drag is not None
        tip = self._tip()
        self._tube(np.array([c, tip]), (0.012 if hot else 0.008) * h, 1.0, False, self.top, v.ren2)
        cone = vtkConeSource()
        cone.SetHeight(0.11 * h)
        cone.SetRadius((0.05 if hot else 0.04) * h)
        cone.SetResolution(16)
        cone.SetCenter(*(tip + n * 0.055 * h))
        cone.SetDirection(*n)
        self._add(cone, 1.0, self.top, v.ren2)
        ball = vtkSphereSource()
        ball.SetRadius((0.045 if hot else 0.035) * h)
        ball.SetThetaResolution(16)
        ball.SetPhiResolution(12)
        ball.SetCenter(*c)
        self._add(ball, 1.0, self.top, v.ren2)
        v.render()

    # ------------------------------------------------------------------ the mouse (device pixels, origin top-left)
    def _px(self, p):
        return np.array(self._g.project(p)[:2])

    def pick(self, x: float, y: float) -> bool:
        if not self.on:
            return False
        dpr = self._g.dpr()
        a, b, q = self._px(self._middle()), self._px(self._tip()), np.array([x, y], float)
        if np.linalg.norm(q - a) < BALL_PX * dpr:
            return True
        ab = b - a
        t = 0.0 if not ab @ ab else float(np.clip((q - a) @ ab / (ab @ ab), 0.0, 1.0))
        return float(np.linalg.norm(q - (a + t * ab))) < GRAB_PX * dpr

    def _along(self, x: float, y: float):
        """The distance along the normal (m) of the point of the plane's axis line nearest the pointer's line."""
        o, d = self._g.ray(x, y)
        n = self.normal
        w0 = o - self._middle()
        b = float(d @ n)
        den = 1 - b * b
        if den < 1e-6:
            return None                          # (looking straight along the normal: it cannot be dragged)
        return float((n @ w0 - b * (d @ w0)) / den)

    def hover_at(self, x: float, y: float) -> bool:
        over = self.drag is None and self.pick(x, y)
        if self.drag is None and over != self.hover:
            self.hover = over
            self._g._cursor(Qt.OpenHandCursor if over else None)
            self._draw()
        return over

    def press(self, x: float, y: float) -> bool:
        if not self.pick(x, y):
            return False
        t0 = self._along(x, y)
        if t0 is None:
            return False
        self.drag = {"t0": t0, "origin": self.point.copy()}
        self.hover = True
        self._g._cursor(Qt.ClosedHandCursor)
        self._draw()
        return True

    def drag_to(self, x: float, y: float, shift: bool = False):
        d = self.drag
        if d is None:
            return None
        t = self._along(x, y)
        if t is None:
            return None
        delta = t - d["t0"]
        if shift and self.snap_m > 0:
            delta = round(delta / self.snap_m) * self.snap_m
        self.point = d["origin"] + self.normal * delta
        self._draw()
        if self.callback:
            self.callback(self.point.copy(), False)
        return self.point

    def release(self, x: float, y: float, shift: bool = False):
        if self.drag is None:
            return
        self.drag_to(x, y, shift)
        self.drag = None
        self.hover = False
        self._g._cursor(None)
        self._draw()
        if self.callback:
            self.callback(self.point.copy(), True)

    def cancel(self) -> bool:
        """Escape during a drag: the plane goes back to where it was when the mouse went down, and the drag ends there (the control is told, so its number agrees)."""
        d = self.drag
        if d is None:
            return False
        self.point = d["origin"].copy()
        self.drag = None
        self.hover = False
        self._g._cursor(None)
        self._draw()
        if self.callback:
            self.callback(self.point.copy(), True)
        return True

    def describe(self) -> dict:
        return {"on": self.on, "point": [float(x) for x in self.point], "normal": [float(x) for x in self.normal], "dragging": self.drag is not None,
                "hover": self.hover}
