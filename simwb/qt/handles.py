"""Drag handles in the 3D view: ONE behaviour for every positioning control, so each control does not get a one-off handle.

A control describes what it wants as a spec; the view's HandleSet draws it, takes the mouse and reports back:

    {"key": "plane1", "kind": "plane", "axis": 1, "value": 0.12, "box": [x0, y0, z0, x1, y1, z1], "color": "#14b8a6",
     "callback": fn(key, value, final)}                      # a plane across axis 1 (y), at 0.12 m: a sheet over `box`, dragged along its axis
    {"key": "pin1", "kind": "point", "point": [x, y, z], "size": 0.1, "resolver": fn(x, y) -> point | None, "callback": fn(key, point, final)}
                                                             # a point: a ball (dragged in the view, or along the surface under the pointer with a
                                                             # resolver) and, without a resolver, arrows that move it along x, y or z

`callback(key, value, final)` is called while dragging with final=False (cheap: the control updates its numbers and label, the view has moved the
handle itself; nothing expensive should run) and once on release with final=True (the control does the real work: cut the plane again, ...).
`HandleSet.move(key, value)` is the other way round: the control's numbers changed, the handle follows. Handles work from pixel positions and the camera
alone (press / drag_to / release in device pixels, origin top-left, like gizmo.py), so tests and the control API drive them without a window:
`simulate_move(key, value)` does a whole press-drag-release.

A plane that is looked at straight along its axis cannot be dragged along the line of sight: then the drag follows the pointer up and down the screen
(up = along the axis) instead of doing nothing. Shift snaps to round steps (`snap_m`).

Used by the Results step (contour planes, the arrows' plane, the section, pinned points, the line's ends, the seeds of the particle paths). The
refinement regions (#7) and the preview of rotating zones (#10) can use the same two kinds."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt
from vtkmodules.util.numpy_support import numpy_to_vtk, numpy_to_vtkIdTypeArray
from vtkmodules.vtkCommonCore import vtkPoints
from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkPolyData
from vtkmodules.vtkFiltersCore import vtkTubeFilter
from vtkmodules.vtkFiltersSources import vtkConeSource, vtkSphereSource
from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper

from .gizmo import RING_COLORS, _rgb

PALETTE = ("#14b8a6", "#e879f9", "#84cc16", "#fb923c", "#60a5fa", "#f43f5e")
GRAB_PX = 12.0                          # how close (window pixels) the pointer must be to a handle's arrow to take it
BALL_PX = 16.0
STRAIGHT_ON = 0.97                      # |view direction . axis| above this: the axis points at the camera
AXES = np.eye(3)


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


class HandleSet:
    def __init__(self, view):
        self.v = view
        self.specs: dict = {}                 # key -> spec (the live values: a drag changes them)
        self.order: list = []
        self.actors: dict = {}                # key -> {"main": [...], "top": [...]}
        self.drag: dict | None = None
        self.hover: tuple | None = None       # (key, part)
        self.on = False

    @property
    def _g(self):
        return self.v.gizmo                    # (its projection helpers work from the camera alone)

    # ------------------------------------------------------------------ the specs
    def set(self, specs: list):
        """Show exactly these handles (a handle that is being dragged keeps its place). An unchanged list is not redrawn."""
        specs = [dict(s) for s in specs]
        if self.drag is not None:
            return
        sig = [(s["key"], s["kind"], s.get("axis"), round(float(s.get("value", 0.0)), 9) if s["kind"] == "plane" else tuple(np.round(s["point"], 9)),
                tuple(np.round(s.get("box") or (), 6)), s.get("color"), round(float(s.get("size", 0) or 0), 9), bool(s.get("resolver")),
                repr(s.get("disc"))) for s in specs]
        if sig == getattr(self, "_sig", None) and self.on:
            for s in specs:                    # (the callbacks may have been rebuilt)
                self.specs[s["key"]]["callback"] = s.get("callback")
                self.specs[s["key"]]["resolver"] = s.get("resolver")
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
        if was:
            self.v.render()

    def move(self, key: str, value):
        """The control's numbers changed: the handle follows (no callback)."""
        s = self.specs.get(key)
        if s is None:
            return
        if s["kind"] == "plane":
            s["value"] = float(value)
        else:
            s["point"] = [float(x) for x in value]
        self._sig = None                       # (the next set() draws what the controls say)
        self._draw(key)
        self.v.render()

    def value(self, key: str):
        s = self.specs[key]
        return float(s["value"]) if s["kind"] == "plane" else list(s["point"])

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

    def _grab(self, s) -> np.ndarray:
        """Where a handle is taken: a plane's ball (the middle of its sheet), a point's ball."""
        return self._rect(s)[0] if s["kind"] == "plane" else np.asarray(s["point"], float)

    def _size(self, s) -> float:
        if s["kind"] == "plane":
            c, u, w, hu, hw = self._rect(s)
            return float(np.hypot(hu, hw)) or 1.0
        return float(s.get("size") or 0.1)

    def _arrows(self, s):
        """[(part, start, tip)]: the handle's arrows. A plane has one, along its normal; a point without a resolver has x, y, z."""
        g = self._grab(s)
        h = self._size(s)
        if s["kind"] == "plane":
            return [("axis", g, g + self._normal(s) * 0.3 * h)]
        if s.get("resolver"):
            return []
        return [(f"axis{a}", g, g + AXES[a] * h) for a in range(3)]

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
        hot = (self.hover is not None and self.hover[0] == key) or (self.drag is not None and self.drag["key"] == key)
        col = s.get("color") or colour_of(self.order.index(key) if key in self.order else 0)
        h = self._size(s)
        g = self._grab(s)
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
            axis_col = col if s["kind"] == "plane" else RING_COLORS[int(part[-1])]
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

    def pick(self, x: float, y: float):
        """(key, part) of the handle under (x, y), or None. A ball beats an arrow; a nearer handle beats a farther one."""
        if not self.on:
            return None
        dpr = self._g.dpr()
        q = np.array([x, y], float)
        best, bd = None, 1e18
        for k in self.order:
            s = self.specs[k]
            c = self._px(self._grab(s))
            d = float(np.linalg.norm(q - c))
            if d < BALL_PX * dpr and d < bd:
                best, bd = (k, "ball"), d
            for part, a, b in self._arrows(s):
                pa, pb = self._px(a), self._px(b)
                ab = pb - pa
                t = 0.0 if not ab @ ab else float(np.clip((q - pa) @ ab / (ab @ ab), 0.0, 1.0))
                d2 = float(np.linalg.norm(q - (pa + t * ab)))
                if d2 < GRAB_PX * dpr and d2 + BALL_PX * dpr < bd:           # (a ball wins when both are close)
                    best, bd = (k, part), d2 + BALL_PX * dpr
        return best

    def grab_px(self, key: str, part: str = "ball") -> np.ndarray:
        """Where to press to take this handle (the ball, or the middle of an arrow): for tests and the control API."""
        s = self.specs[key]
        if part == "ball":
            return self._px(self._grab(s))
        a, b = next((a, b) for p, a, b in self._arrows(s) if p == part)
        return self._px((a + b) / 2)

    def _line_param(self, origin, direction, x, y):
        o, d = self._g.ray(x, y)
        n = np.asarray(direction, float)
        w0 = o - np.asarray(origin, float)
        b = float(d @ n)
        den = 1 - b * b
        if den < 1e-6:
            return None
        return float((n @ w0 - b * (d @ w0)) / den)

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
        self.drag = d
        self.hover = (key, part)
        self._g._cursor(Qt.ClosedHandCursor)
        self._draw(key)
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
        if s["kind"] == "plane":
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
        val = float(s["value"]) if s["kind"] == "plane" else list(s["point"])
        self.drag = None
        self.hover = None
        self._g._cursor(None)
        self._draw(d["key"])
        self.v.render()
        cb = s.get("callback")
        if cb:
            cb(d["key"], val, True)

    # ------------------------------------------------------------------ driving it without a mouse (tests, the control API)
    def simulate_move(self, key: str, value, steps: int = 5, part: str = "ball", shift: bool = False):
        """Press the handle, drag it to `value` (a plane: its position in m; a point: [x, y, z] in m) in a few mouse steps and release it. Returns the
        value the handle ended at (it stops at its limits, and a plane seen straight on is dragged up the screen)."""
        s = self.specs[key]
        start = self.grab_px(key, part)
        taken = self._grab(s) if part == "ball" else next((a + b) / 2 for p, a, b in self._arrows(s) if p == part)        # (where on the handle it is taken)
        if s["kind"] == "plane":
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
        return {"on": self.on, "dragging": self.drag["key"] if self.drag else None, "hover": list(self.hover) if self.hover else None,
                "handles": {k: {"kind": s["kind"], ("value" if s["kind"] == "plane" else "point"): self.value(k),
                                **({"axis": "xyz"[int(s["axis"])]} if s["kind"] == "plane" and s.get("axis") is not None else {})}
                            for k, s in self.specs.items()}}
