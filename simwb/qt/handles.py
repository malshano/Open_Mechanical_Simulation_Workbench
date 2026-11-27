"""Drag handles in the 3D view: ONE behaviour for every positioning control, so each control does not get a one-off handle.

A control describes what it wants as a spec; the view's HandleSet draws it, takes the mouse and reports back:

    {"key": "plane1", "kind": "plane", "axis": 1, "value": 0.12, "box": [x0, y0, z0, x1, y1, z1], "color": "#14b8a6",
     "callback": fn(key, value, final)}                      # a plane across axis 1 (y), at 0.12 m: a sheet over `box`, dragged along its axis
    {"key": "pin1", "kind": "point", "point": [x, y, z], "size": 0.1, "resolver": fn(x, y) -> point | None, "callback": fn(key, point, final)}
                                                             # a point: a ball (dragged in the view, or along the surface under the pointer with a
                                                             # resolver) and, without a resolver, arrows that move it along x, y or z
    {"key": "turn.x", "kind": "ring", "axis": 0, "point": [x, y, z], "radius": 0.2, "snap_deg": 5.0, "callback": fn(key, angle_rad, final)}
                                                             # a ring round an axis through `point`: dragged round it, it turns something by the angle (relative to
                                                             # where the drag began); `detent_deg` makes the angle jump in whole steps of that many degrees
    {"key": "face.0.1", "kind": "face", "point": [x, y, z], "dir": [dx, dy, dz], "length": 0.05, "limits": [lo, hi], "callback": fn(key, delta_m, final)}
                                                             # a face handle: an arrow with a square end on a face, pointing out of it; dragged along its arrow it
                                                             # resizes (delta = how far the face was moved outwards, relative to where the drag began)

Rings and faces report a change RELATIVE to where the drag began: the callback is called with 0 and final=False when the drag starts (the control
remembers what it was), then with the angle (rad) or the distance (m), and once with final=True on release. A plane or a point reports the new position.

`callback(key, value, final)` is called while dragging with final=False (cheap: the control updates its numbers and label, the view has moved the
handle itself; nothing expensive should run) and once on release with final=True (the control does the real work: cut the plane again, ...).
`HandleSet.move(key, value)` is the other way round: the control's numbers changed, the handle follows. Handles work from pixel positions and the camera
alone (press / drag_to / release in device pixels, origin top-left, like gizmo.py), so tests and the control API drive them without a window:
`simulate_move(key, value)` does a whole press-drag-release.

A plane that is looked at straight along its axis cannot be dragged along the line of sight: then the drag follows the pointer up and down the screen
(up = along the axis) instead of doing nothing. Shift snaps to round steps (`snap_m`).

Used by the Results step (contour planes, the arrows' plane, the section, pinned points, the line's ends, the seeds of the particle paths), by the
refinement regions of the Mesh step (regionhandles.py: arrows and a ball to move, rings to turn, face handles to resize) and by the rotating zones of
the Setup step (zonehandles.py). A spec with "fit": True is drawn smaller when the view is zoomed in so that its largest part (a ring's radius, an
arrow's length) never takes more than 30 % of the picture's smaller side."""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QTimer, Qt
from vtkmodules.util.numpy_support import numpy_to_vtk, numpy_to_vtkIdTypeArray
from vtkmodules.vtkCommonCore import vtkPoints
from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkPolyData
from vtkmodules.vtkFiltersCore import vtkTubeFilter
from vtkmodules.vtkFiltersSources import vtkConeSource, vtkCubeSource, vtkSphereSource
from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper

from .gizmo import RING_COLORS, SNAP_DEG, SCREEN_FRACTION, _rgb, ring_axes

PALETTE = ("#14b8a6", "#e879f9", "#84cc16", "#fb923c", "#60a5fa", "#f43f5e")
RING_PX = 16.0                          # how close (window pixels) the pointer must be to a ring to take it
GRAB_PX = 12.0                          # how close (window pixels) the pointer must be to a handle's arrow to take it
BALL_PX = 16.0
CAP_PX = 14.0                           # the square end of a face handle counts as part of it: this far (window pixels) from its centre
STRAIGHT_ON = 0.97                      # |view direction . axis| above this: the axis points at the camera
AXES = np.eye(3)
RELATIVE = ("ring", "face")             # kinds that report the change since the drag began (the others report a position)


def colour_of(i: int) -> str:
    return PALETTE[i % len(PALETTE)]


def _poly_lines(pts, closed: bool) -> vtkPolyData:
    pts = np.asarray(pts, float)
    pd = vtkPolyData()
    p = vtkPoints()
    p.SetData(numpy_to_vtk(np.ascontiguousarray(pts), deep=True))
    pd.SetPoints(p)
    idx = list(range(len(pts))) + ([0] if closed else [])
    ca = vtkCellArray()
    ca.ImportLegacyFormat(numpy_to_vtkIdTypeArray(np.array([len(idx)] + idx, np.int64), deep=True))
    pd.SetLines(ca)
    return pd


def _quad(corners) -> vtkPolyData:
    pd = _poly_lines(corners, True)
    ca = vtkCellArray()
    ca.ImportLegacyFormat(numpy_to_vtkIdTypeArray(np.array([4, 0, 1, 2, 3], np.int64), deep=True))
    pd.SetPolys(ca)
    pd.SetLines(vtkCellArray())
    return pd


HOOKS = ("callback", "resolver", "on_press", "on_cancel")      # (what a spec carries that is not a number: never a reason to draw again)


def _sig_of(s: dict):
    """What decides whether a handle must be drawn again: every number of its spec, but not its callbacks."""
    def norm(x):
        if isinstance(x, (list, tuple, np.ndarray)):
            return tuple(norm(i) for i in x)
        if isinstance(x, (float, np.floating)):
            return round(float(x), 9)
        if isinstance(x, dict):
            return tuple(sorted((k, norm(v)) for k, v in x.items()))
        return x
    return norm({k: v for k, v in s.items() if k not in HOOKS}), bool(s.get("resolver"))


class HandleSet:
    def __init__(self, view):
        self.v = view
        self.specs: dict = {}                 # key -> spec (the live values: a drag changes them)
        self.order: list = []
        self.actors: dict = {}                # key -> {"main": [...], "top": [...]}
        self.drag: dict | None = None
        self.hover: tuple | None = None       # (key, part)
        self.on = False
        self._k_drawn: dict = {}              # key -> the "fit" factor it was drawn with
        self._redraw_queued = False
        try:
            view.ren.GetActiveCamera().AddObserver("ModifiedEvent", self._camera_moved)
        except Exception:  # noqa: BLE001
            pass

    @property
    def _g(self):
        return self.v.gizmo                    # (its projection helpers work from the camera alone)

    # ------------------------------------------------------------------ the specs
    def set(self, specs: list):
        """Show exactly these handles. An unchanged list is not redrawn. While one handle is being dragged it keeps its place (it follows the pointer
        itself) and the others are updated: a region's rings and faces follow the arrow that moves it."""
        specs = [dict(s) for s in specs]
        if self.drag is not None:
            dk = self.drag["key"]
            if dk in self.specs and any(s["key"] == dk for s in specs) and [s["key"] for s in specs] == self.order:
                for s in specs:
                    if s["key"] == dk:
                        for h in HOOKS:
                            self.specs[dk][h] = s.get(h)
                    elif _sig_of(s) != _sig_of(self.specs[s["key"]]):
                        self.specs[s["key"]] = s
                        self._draw(s["key"])
                        self._sig = None                       # (what is drawn is no longer the list the last set() was given: the next one draws again)
                self.v.render()
            return
        sig = [_sig_of(s) for s in specs]
        if sig == getattr(self, "_sig", None) and self.on:
            for s in specs:                    # (the callbacks may have been rebuilt)
                for h in HOOKS:
                    self.specs[s["key"]][h] = s.get(h)
            return
        self._sig = sig
        for k in list(self.actors):
            self._clear(k)
        self.specs = {s["key"]: s for s in specs}
        self.order = [s["key"] for s in specs]
        self.on = bool(specs)
        self.hover = None
        for k in self.order:
            self._draw(k)
        self.v.render()

    def clear(self):
        was = self.on or bool(self.actors)
        for k in list(self.actors):
            self._clear(k)
        self.specs, self.order, self.drag, self.hover, self.on, self._sig = {}, [], None, None, False, None
        self._k_drawn = {}
        if was:
            self.v.render()

    def label(self, text: str | None):
        """What the drag in progress does ('Turning about x: +12.0°'), in the corner of the 3D view, as the gizmo of the Geometry step shows it."""
        self._g.show_angle(text)

    def move(self, key: str, value):
        """The control's numbers changed: the handle follows (no callback)."""
        s = self.specs.get(key)
        if s is None:
            return
        if s["kind"] == "point":
            s["point"] = [float(x) for x in value]
        else:
            s["value"] = float(value)
        self._sig = None                       # (the next set() draws what the controls say)
        self._draw(key)
        self.v.render()

    def value(self, key: str):
        s = self.specs[key]
        return list(s["point"]) if s["kind"] == "point" else float(s.get("value", 0.0))

    # ------------------------------------------------------------------ keeping the size on the screen
    def _scale(self, s) -> float:
        """1, or for a spec with "fit": the factor (< 1) that keeps its largest part inside 30 % of the picture's smaller side where it is seen."""
        if not s.get("fit"):
            return 1.0
        reach = float(s["radius"]) if s["kind"] == "ring" else float(s.get("size") or s.get("length") or 0.1)
        try:
            cam = self.v.ren.GetActiveCamera()
            d = np.array(cam.GetFocalPoint()) - np.array(cam.GetPosition())
            right = np.cross(d, np.array(cam.GetViewUp()))
            n = float(np.linalg.norm(right))
            if n < 1e-12:
                return 1.0
            c = np.asarray(s["point"], float)
            x0, y0, _ = self._g.project(c)
            x1, y1, _ = self._g.project(c + right / n * reach)
            px = math.hypot(x1 - x0, y1 - y0)
            w, h = self._g._size()
            room = SCREEN_FRACTION * min(w, h)
            return 1.0 if px <= room or px < 1e-9 else room / px
        except Exception:  # noqa: BLE001
            return 1.0

    def _camera_moved(self, *_):
        if not self.on or self.drag is not None or self._redraw_queued:
            return
        if any(s.get("fit") and abs(self._scale(s) - self._k_drawn.get(k, 1.0)) > 0.04 * self._k_drawn.get(k, 1.0) for k, s in self.specs.items()):
            self._redraw_queued = True
            QTimer.singleShot(0, self._redraw)

    def _redraw(self):
        self._redraw_queued = False
        if self.on and self.drag is None:
            for k in self.order:
                if self.specs[k].get("fit"):
                    self._draw(k)
            self.v.render()

    # ------------------------------------------------------------------ geometry of one handle
    def _normal(self, s) -> np.ndarray:
        if s.get("normal") is not None:
            n = np.asarray(s["normal"], float)
            return n / (np.linalg.norm(n) or 1.0)
        return AXES[int(s["axis"])]

    def _rect(self, s):
        """(centre, u, w, half-width u, half-width w) of a plane's sheet: the box cut across, 3 % smaller."""
        n = self._normal(s)
        b = np.asarray(s.get("box") or [-1, -1, -1, 1, 1, 1], float)
        ax = int(np.argmax(np.abs(n)))
        c = (b[:3] + b[3:]) / 2
        c = c + n * (float(s["value"]) - float(c @ n))
        others = [i for i in range(3) if i != ax]
        hu, hw = 0.485 * (b[3 + others[0]] - b[others[0]]), 0.485 * (b[3 + others[1]] - b[others[1]])
        return c, AXES[others[0]], AXES[others[1]], hu, hw

    def _k(self, s) -> float:
        return self._k_drawn.get(s["key"], 1.0)

    def _dir(self, s) -> np.ndarray:
        d = np.asarray(s["dir"], float)
        return d / (np.linalg.norm(d) or 1.0)

    def _grab(self, s) -> np.ndarray:
        """Where a handle is taken: a plane's ball (the middle of its sheet), a point's ball, a ring's centre, a face handle's foot."""
        if s["kind"] == "plane":
            return self._rect(s)[0]
        if s["kind"] == "face":
            return np.asarray(s["point"], float) + self._dir(s) * float(s.get("value") or 0.0)
        return np.asarray(s["point"], float)

    def _size(self, s) -> float:
        if s["kind"] == "plane":
            c, u, w, hu, hw = self._rect(s)
            return float(np.hypot(hu, hw)) or 1.0
        if s["kind"] == "ring":
            return float(s["radius"]) * self._k(s)
        if s["kind"] == "face":
            return float(s.get("length") or 0.1) * self._k(s)
        return float(s.get("size") or 0.1) * self._k(s)

    def _arrows(self, s):
        """[(part, start, tip)]: the handle's arrows. A plane has one, along its normal; a point without a resolver has x, y, z; a face handle has one
        out of its face."""
        g = self._grab(s)
        h = self._size(s)
        if s["kind"] == "plane":
            return [("axis", g, g + self._normal(s) * 0.3 * h)]
        if s["kind"] == "face":
            st = float(s.get("start") or 0.0) * self._k(s)               # (the arrow may start away from the face: beyond what lies on the same line)
            return [("face", g + self._dir(s) * st, g + self._dir(s) * (st + h))]
        if s["kind"] == "ring" or s.get("resolver"):
            return []
        return [(f"axis{a}", g, g + AXES[a] * h) for a in range(3)]

    def _ring_basis(self, s):
        if s.get("normal") is not None:
            n = self._normal(s)
            u = np.cross(n, AXES[int(np.argmin(np.abs(n)))])
            u /= np.linalg.norm(u)
            return u, np.cross(n, u)
        return ring_axes(int(s["axis"]))

    def _ring_points(self, s, n: int = 96) -> np.ndarray:
        u, v = self._ring_basis(s)
        t = np.linspace(0, 2 * np.pi, n, endpoint=False)
        return np.asarray(s["point"], float) + self._size(s) * (np.cos(t)[:, None] * u + np.sin(t)[:, None] * v)

    def _ring_point(self, s, deg: float) -> np.ndarray:
        u, v = self._ring_basis(s)
        t = math.radians(deg)
        return np.asarray(s["point"], float) + self._size(s) * (math.cos(t) * u + math.sin(t) * v)

    def _axis_colour(self, s, part: str = "") -> str:
        if s["kind"] == "plane":
            return s.get("color") or colour_of(self.order.index(s["key"]) if s["key"] in self.order else 0)
        if s["kind"] in ("ring", "face"):
            return s.get("color") or RING_COLORS[int(s.get("axis") or 0) % 3]
        return RING_COLORS[int(part[-1])]

    # ------------------------------------------------------------------ drawing
    def _clear(self, key):
        a = self.actors.pop(key, None)
        if a:
            self.v._clear(a["main"])
            self.v._clear(a["top"])

    def _add(self, layer, ren, src=None, colour="#ffffff", opacity=1.0, mapper_input=None):
        m = vtkPolyDataMapper()
        if mapper_input is not None:
            m.SetInputData(mapper_input)
        else:
            m.SetInputConnection(src.GetOutputPort())
        act = vtkActor()
        act.SetMapper(m)
        pr = act.GetProperty()
        pr.SetColor(*_rgb(colour))
        pr.SetOpacity(opacity)
        pr.SetAmbient(1.0)
        pr.SetDiffuse(0.0)
        act.PickableOff()
        ren.AddActor(act)
        layer.append(act)

    def _tube(self, layer, ren, pts, radius, colour, opacity=1.0, closed=False):
        t = vtkTubeFilter()
        t.SetInputData(_poly_lines(pts, closed))
        t.SetRadius(radius)
        t.SetNumberOfSides(8)
        t.Update()
        self._add(layer, ren, t, colour, opacity)

    def _draw(self, key):
        self._clear(key)
        s = self.specs.get(key)
        if s is None:
            return
        v = self.v
        main, top = [], []
        self.actors[key] = {"main": main, "top": top}
        self._k_drawn[key] = self._scale(s)
        hot = (self.hover is not None and self.hover[0] == key) or (self.drag is not None and self.drag["key"] == key)
        col = s.get("color") or colour_of(self.order.index(key) if key in self.order else 0)
        h = self._size(s)
        g = self._grab(s)
        if s["kind"] == "ring":
            self._tube(top, v.ren2, self._ring_points(s), h * (0.016 if hot else 0.009), self._axis_colour(s), 1.0 if hot else 0.85, True)
            return
        if s["kind"] == "face":                                 # (an arrow out of the face with a square end: the round tips are the move arrows)
            a, b = self._arrows(s)[0][1:]
            c = self._axis_colour(s)
            if float(s.get("start") or 0.0) > 0:                        # (a thin line from the face to where the arrow starts: it is the face's handle)
                self._tube(top, v.ren2, np.array([g, a]), h * 0.012, c, 0.45)
            self._tube(top, v.ren2, np.array([a, b]), h * (0.05 if hot else 0.035), c, 1.0 if hot else 0.9)
            cube = vtkCubeSource()
            side = h * (0.34 if hot else 0.28)
            cube.SetXLength(side)
            cube.SetYLength(side)
            cube.SetZLength(side)
            cube.SetCenter(*b)
            self._add(top, v.ren2, cube, c)
            return
        if s["kind"] == "plane":
            c, u, w, hu, hw = self._rect(s)
            corners = np.array([c + u * hu + w * hw, c - u * hu + w * hw, c - u * hu - w * hw, c + u * hu - w * hw])
            self._add(main, v.ren, colour=col, opacity=0.16 if not hot else 0.26, mapper_input=_quad(corners))
            self._tube(main, v.ren, corners, 0.004 * h * (1.6 if hot else 1.0), col, 0.9, True)
        if s["kind"] == "point" and s.get("disc"):             # (the disk the control stands for: its radius, across an axis)
            r, ax = float(s["disc"]["radius"]), int(s["disc"]["axis"])
            o = [i for i in range(3) if i != ax]
            t = np.linspace(0, 2 * np.pi, 48, endpoint=False)
            ring = g + r * (np.cos(t)[:, None] * AXES[o[0]] + np.sin(t)[:, None] * AXES[o[1]])
            fan = _poly_lines(ring, True)
            ca = vtkCellArray()
            ca.ImportLegacyFormat(numpy_to_vtkIdTypeArray(np.array([len(ring)] + list(range(len(ring))), np.int64), deep=True))
            fan.SetPolys(ca)
            fan.SetLines(vtkCellArray())
            self._add(main, v.ren, colour=col, opacity=0.2 if not hot else 0.3, mapper_input=fan)
            self._tube(main, v.ren, ring, 0.02 * r * (1.6 if hot else 1.0), col, 0.9, True)
        for part, a, b in self._arrows(s):
            axis_col = self._axis_colour(s, part)
            on = hot and self.hover is not None and self.hover[1] == part or (self.drag is not None and self.drag["key"] == key and self.drag["part"] == part)
            self._tube(top, v.ren2, np.array([a, b]), (0.014 if on else 0.009) * h, axis_col)
            cone = vtkConeSource()
            cone.SetHeight(0.12 * h)
            cone.SetRadius((0.05 if on else 0.035) * h)
            cone.SetResolution(16)
            d = (b - a) / (np.linalg.norm(b - a) or 1.0)
            cone.SetCenter(*(b + d * 0.06 * h))
            cone.SetDirection(*d)
            self._add(top, v.ren2, cone, axis_col)
        ball = vtkSphereSource()
        ball.SetRadius((0.05 if hot else 0.038) * h if s["kind"] == "point" else (0.045 if hot else 0.035) * h * 0.4)
        ball.SetThetaResolution(18)
        ball.SetPhiResolution(12)
        ball.SetCenter(*g)
        self._add(top, v.ren2, ball, col)

    # ------------------------------------------------------------------ the mouse (device pixels, origin top-left)
    def _px(self, p):
        return np.array(self._g.project(p)[:2])

    @staticmethod
    def _near_segment(q, pa, pb) -> float:
        ab = pb - pa
        t = 0.0 if not ab @ ab else float(np.clip((q - pa) @ ab / (ab @ ab), 0.0, 1.0))
        return float(np.linalg.norm(q - (pa + t * ab)))

    def pick(self, x: float, y: float):
        """(key, part) of the handle under (x, y), or None. What is taken first where handles overlap: a ball, then the square end of a face handle, then an
        arrow, then a ring (a ring is also the largest thing, so it comes last). Between arrows on the same line (a zone's move arrow and its length handle) the one
        whose own tip is nearer wins: each is taken at its end."""
        if not self.on:
            return None
        dpr = self._g.dpr()
        q = np.array([x, y], float)
        found = []
        for n, k in enumerate(self.order):
            s = self.specs[k]
            if s["kind"] == "ring":
                pts = np.array([self._px(p) for p in self._ring_points(s, 120)])
                d = min(self._near_segment(q, pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts)))
                if d < RING_PX * dpr:
                    found.append(((3, d, 0.0, n), (k, "ring")))
                continue
            if s["kind"] != "face":
                d = float(np.linalg.norm(q - self._px(self._grab(s))))
                if d < BALL_PX * dpr:
                    found.append(((0, d, 0.0, n), (k, "ball")))
            for part, a, b in self._arrows(s):
                pa, pb = self._px(a), self._px(b)
                tip = float(np.linalg.norm(q - pb))
                if s["kind"] == "face" and tip < CAP_PX * dpr:
                    found.append(((1, tip, 0.0, n), (k, part)))
                    continue
                d2 = self._near_segment(q, pa, pb if s["kind"] == "face" else pb + (pb - pa) * 0.12)         # (an arrow's cone is part of it)
                if d2 < GRAB_PX * dpr:                                   # (on a line two arrows share, a move arrow wins over the shaft of a face handle: that is taken at its square end)
                    found.append(((2, round(d2 / (4 * dpr)) + (1 if s["kind"] == "face" else 0), tip, n), (k, part)))
        if any(f[0][0] == 2 and not self.specs[f[1][0]]["kind"] == "face" for f in found):
            found = [f for f in found if not (f[0][0] == 2 and self.specs[f[1][0]]["kind"] == "face")]       # (where a move arrow is under the pointer, the shaft of a resize arrow is not: it is taken at its square end)
        return min(found, key=lambda f: f[0])[1] if found else None

    def _taken(self, s, part: str) -> np.ndarray:
        """The world point where a handle is taken: its ball; the square end of a face handle; the middle of an arrow; a ring where it is drawn at 20 degrees."""
        if s["kind"] == "ring":
            return self._ring_point(s, 20.0)
        if s["kind"] == "face":
            return self._arrows(s)[0][2]
        if part == "ball":
            return self._grab(s)
        a, b = next((a, b) for p, a, b in self._arrows(s) if p == part)
        return (a + b) / 2

    def grab_px(self, key: str, part: str = "ball") -> np.ndarray:
        """Where to press to take this handle (see _taken): for tests and the control API."""
        return self._px(self._taken(self.specs[key], part))

    def _line_param(self, origin, direction, x, y):
        o, d = self._g.ray(x, y)
        n = np.asarray(direction, float)
        w0 = o - np.asarray(origin, float)
        b = float(d @ n)
        den = 1 - b * b
        if den < 1e-6:
            return None
        return float((n @ w0 - b * (d @ w0)) / den)

    def _ring_angle(self, s, x, y):
        """The angle (rad) of the pointer on the plane of a ring, round its centre; None when the pointer's line runs along that plane."""
        o, d = self._g.ray(x, y)
        n = self._normal(s)
        den = float(d @ n)
        if abs(den) < 1e-6:
            return None
        c = np.asarray(s["point"], float)
        p = o + d * (float((c - o) @ n) / den) - c
        u, v = self._ring_basis(s)
        return math.atan2(float(p @ v), float(p @ u))

    def _straight_on(self, n) -> bool:
        cam = self.v.ren.GetActiveCamera()
        d = np.array(cam.GetPosition()) - np.array(cam.GetFocalPoint())
        d = d / (np.linalg.norm(d) or 1.0)
        return abs(float(d @ n)) > STRAIGHT_ON

    def hover_at(self, x: float, y: float):
        h = self.pick(x, y) if self.drag is None else self.hover
        if self.drag is None and h != self.hover:
            old, self.hover = self.hover, h
            self._g._cursor(Qt.OpenHandCursor if h is not None else None)
            for k in {old[0] if old else None, h[0] if h else None} - {None}:
                self._draw(k)
            self.v.render()
        return h

    def press(self, x: float, y: float, force=None) -> bool:
        """The button went down at (x, y): True when it took a handle. force=(key, part): take that one whatever is under the pointer (the arrows
        of a point overlap when it is looked at along a diagonal: a test or the control API must still be able to take the one it means)."""
        h = force if force is not None else self.pick(x, y)
        if h is None:
            return False
        key, part = h
        s = self.specs[key]
        d = {"key": key, "part": part, "px0": (x, y), "snap": float(s.get("snap_m") or 0.0)}
        if s["kind"] == "plane":
            n = self._normal(s)
            d.update(v0=float(s["value"]), n=n)
            if self._straight_on(n):
                d["mode"] = "updown"
            else:
                t0 = self._line_param(self._grab(s), n, x, y)
                if t0 is None:
                    return False
                d.update(mode="axis", t0=t0, origin=self._grab(s).copy())
        elif s["kind"] == "ring":
            a0 = self._ring_angle(s, x, y)
            if a0 is None:
                return False
            d.update(mode="ring", last=a0, total=0.0)
        elif s["kind"] == "face":
            t0 = self._line_param(np.asarray(s["point"], float), self._dir(s), x, y)
            if t0 is None:
                return False
            d.update(mode="face", t0=t0, v0=float(s.get("value") or 0.0))
        elif part == "ball":
            p0 = np.asarray(s["point"], float)
            if s.get("resolver"):
                d.update(mode="surface")
            else:
                q = self._g.in_view_plane(x, y, p0)
                if q is None:
                    return False
                d.update(mode="view", p0=p0, offset=p0 - q)
        else:
            a = int(part[-1])
            p0 = np.asarray(s["point"], float)
            t0 = self._line_param(p0, AXES[a], x, y)
            if t0 is None:
                return False
            d.update(mode="axis", a=a, p0=p0, t0=t0)
        d["restore"] = list(s["point"]) if s["kind"] == "point" else float(s.get("value") or 0.0)
        self.drag = d
        self.hover = (key, part)
        self._g._cursor(Qt.ClosedHandCursor)
        self._draw(key)
        if s.get("on_press"):
            s["on_press"](key)
        if s["kind"] in RELATIVE:
            s["value"] = 0.0
            self._draw(key)
            cb = s.get("callback")
            if cb:
                cb(key, 0.0, False)                              # (the drag begins: the control remembers what it was)
        return True

    def _clamp(self, s, val: float) -> float:
        lo, hi = s.get("limits") or (None, None)
        if lo is not None:
            val = max(val, float(lo))
        if hi is not None:
            val = min(val, float(hi))
        return val

    def drag_to(self, x: float, y: float, shift: bool = False):
        d = self.drag
        if d is None:
            return None
        s = self.specs[d["key"]]
        snap = d["snap"] if shift else 0.0
        if s["kind"] == "ring":
            ang = self._ring_angle(s, x, y)
            if ang is None:
                return None
            step = ang - d["last"]
            step -= 2 * math.pi * round(step / (2 * math.pi))        # (unwrapped: more than a whole turn is allowed)
            d["last"] = ang
            d["total"] += step
            val = d["total"]
            detent = float(s.get("detent_deg") or 0.0)
            if detent > 0:
                val = math.radians(round(math.degrees(val) / detent) * detent)
            elif shift:
                st = float(s.get("snap_deg") or SNAP_DEG)
                val = math.radians(round(math.degrees(val) / st) * st)
            s["value"] = val
        elif s["kind"] == "face":
            t = self._line_param(np.asarray(s["point"], float), self._dir(s), x, y)
            if t is None:
                return None
            delta = t - d["t0"] + d["v0"]
            if snap > 0:
                delta = round(delta / snap) * snap
            s["value"] = val = self._clamp(s, delta)
        elif s["kind"] == "plane":
            n = d["n"]
            if d["mode"] == "updown":                         # (looking straight along the axis: up the screen is along it)
                if d.get("mpp") is None:
                    d["mpp"] = self._mpp_for(s)
                delta = (d["px0"][1] - y) * d["mpp"] * (1.0 if self._facing(n) > 0 else -1.0)
            else:
                t = self._line_param(d["origin"], n, x, y)
                if t is None:
                    return None
                delta = t - d["t0"]
            if snap > 0:
                delta = round(delta / snap) * snap
            s["value"] = self._clamp(s, d["v0"] + delta)
            val = s["value"]
        else:
            p0 = d.get("p0")
            if d["mode"] == "surface":
                q = s["resolver"](x, y)
                if q is None:
                    return None
                new = np.asarray(q, float)
            elif d["mode"] == "view":
                q = self._g.in_view_plane(x, y, d["p0"])
                if q is None:
                    return None
                new = q + d["offset"]
            else:
                t = self._line_param(p0, AXES[d["a"]], x, y)
                if t is None:
                    return None
                delta = t - d["t0"]
                if snap > 0:
                    delta = round(delta / snap) * snap
                new = p0 + AXES[d["a"]] * delta
            s["point"] = new.tolist()
            val = list(s["point"])
        if s["kind"] == "ring":
            val = s["value"]
        self._draw(d["key"])
        self.v.render()
        cb = s.get("callback")
        if cb:
            cb(d["key"], val, False)
        return val

    def _facing(self, n) -> float:
        """Positive when the axis points towards the camera, and 'up the screen' then means further along it."""
        cam = self.v.ren.GetActiveCamera()
        d = np.array(cam.GetPosition()) - np.array(cam.GetFocalPoint())
        return float(d @ n)

    def _mpp_for(self, s) -> float:
        """Metres of travel along the axis for one pixel of up-and-down drag: the plane's size over the picture's height, so that the sheet crosses
        its whole range in about the height of the view."""
        _w, hpx = self._g._size()
        return 2.0 * self._size(s) / max(hpx * 0.5, 1.0)

    def release(self, x: float, y: float, shift: bool = False):
        d = self.drag
        if d is None:
            return
        self.drag_to(x, y, shift)
        s = self.specs[d["key"]]
        val = list(s["point"]) if s["kind"] == "point" else float(s.get("value", 0.0))
        self.drag = None
        self.hover = None
        self._g._cursor(None)
        self._draw(d["key"])
        self.v.render()
        cb = s.get("callback")
        if cb:
            cb(d["key"], val, True)

    def cancel(self) -> bool:
        """Escape during a drag: the handle goes back to where it was when the mouse went down and the drag ends there (the control is told it ended at the old
        value, so its numbers, its drawing and the handle agree again). True when a drag was cancelled."""
        d = self.drag
        if d is None:
            return False
        s = self.specs[d["key"]]
        old = d["restore"]
        if s["kind"] == "point":
            s["point"] = list(old)
        else:
            s["value"] = old
        self.drag = None
        self.hover = None
        self._g._cursor(None)
        self._draw(d["key"])
        self.v.render()
        if s.get("on_cancel"):
            s["on_cancel"](d["key"])
        elif s.get("callback"):
            s["callback"](d["key"], list(old) if s["kind"] == "point" else (0.0 if s["kind"] in RELATIVE else float(old)), True)
        return True

    # ------------------------------------------------------------------ driving it without a mouse (tests, the control API)
    def simulate_move(self, key: str, value, steps: int = 5, part: str = "ball", shift: bool = False):
        """Press the handle, drag it to `value` in a few mouse steps and release it. A plane: its position in m; a point: [x, y, z] in m; a ring: the angle
        to turn by, in degrees; a face handle: how far to move the face outwards, in m. Returns the value the handle ended at (it stops at its limits, and a
        plane seen straight on is dragged up the screen; a ring: the angle in degrees)."""
        s = self.specs[key]
        if s["kind"] == "ring":
            start = self.grab_px(key)
            if not self.press(*start, force=(key, "ring")):
                raise ValueError(f"The ring {key!r} cannot be taken here: turn the view so that it is not looked at edge-on")
            out = None
            steps = max(steps, int(abs(float(value)) // 20) + 1)
            q = start
            for i in range(1, steps + 1):
                q = self._px(self._ring_point(s, 20.0 + float(value) * i / steps))
                out = self.drag_to(*q, shift)
            self.release(*q, shift)
            return math.degrees(out or 0.0)
        if s["kind"] == "face":
            part = "face"
        start = self.grab_px(key, part)
        taken = self._taken(s, part)                                   # (where on the handle it is taken)
        if s["kind"] == "face":
            end = self._px(taken + self._dir(s) * (float(value) - float(s.get("value") or 0.0)))
        elif s["kind"] == "plane":
            n = self._normal(s)
            target = taken + n * (float(value) - float(s["value"]))
            if self._straight_on(n):                       # (the pixel that makes the drag move that far: up the screen)
                mpp = self._mpp_for(s)
                dy = (float(value) - float(s["value"])) / mpp * (1.0 if self._facing(n) > 0 else -1.0)
                end = start + np.array([0.0, -dy])
            else:
                end = self._px(target)
        else:
            end = self._px(taken + (np.asarray(value, float) - np.asarray(s["point"], float)))
        if not self.press(*start, force=(key, part)):
            raise ValueError(f"The handle {key!r} cannot be taken here: turn the view so that it is not looked at straight along its axis")
        for i in range(1, steps + 1):
            q = start + (end - start) * i / steps
            self.drag_to(*q, shift)
        self.release(*end, shift)
        return self.value(key)

    def describe(self) -> dict:
        def one(k, s):
            if s["kind"] == "point":
                return {"kind": "point", "point": self.value(k)}
            if s["kind"] == "ring":
                return {"kind": "ring", "axis": "xyz"[int(s["axis"])] if s.get("axis") is not None else None, "centre": [float(x) for x in s["point"]]}
            if s["kind"] == "face":
                return {"kind": "face", "moved_by": float(s.get("value") or 0.0), "direction": [round(float(x), 6) for x in self._dir(s)]}
            return {"kind": "plane", "value": self.value(k), **({"axis": "xyz"[int(s["axis"])]} if s.get("axis") is not None else {})}
        return {"on": self.on, "dragging": self.drag["key"] if self.drag else None, "hover": list(self.hover) if self.hover else None,
                "handles": {k: one(k, s) for k, s in self.specs.items()}}
