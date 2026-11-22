"""The 3D view's orientation tools: ONE gizmo that turns and moves - three rings around the model (x red / y green / z blue, fixed in the
world) that turn it, three arrows (the same colours) that move it along an axis and a small centre ball that moves it freely in the view's
plane - the live preview of the model turning and moving (the parts only: the air box stays), and the arrow on the face being aligned.

A drag on a ring or an arrow changes a PREVIEW of the model; the panel (orient.py) keeps it and sends it to the engine on Apply. The preview
acts on the whole model or on one body (`target`: its volume id), the rest stays where it is.
Everything works from pixel positions and the camera alone (no render window), so a test can drive a real drag with synthetic positions:
`press(x, y)`, `drag_to(x, y, shift)`, `release(x, y, shift)` (pixels of the 3D view, origin top-left); the mouse handlers of the Viewport
call exactly these.

The Viewport holds one instance (`view.gizmo`) and calls it from its mouse handlers (a few lines there); the model's actors are turned
through `apply_preview()`."""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QTimer, Qt
from vtkmodules.util.numpy_support import numpy_to_vtk, numpy_to_vtkIdTypeArray
from vtkmodules.vtkCommonCore import vtkPoints
from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkPolyData
from vtkmodules.vtkCommonMath import vtkMatrix4x4
from vtkmodules.vtkFiltersCore import vtkTubeFilter
from vtkmodules.vtkFiltersSources import vtkArrowSource, vtkConeSource, vtkSphereSource
from vtkmodules.vtkCommonTransforms import vtkTransform
from vtkmodules.vtkFiltersGeneral import vtkTransformPolyDataFilter
from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper, vtkTextActor

from . import theme

SNAP_DEG = 5.0                          # Shift held: the angle snaps to steps of this many degrees (and a move to a round step in mm)
RING_COLORS = ("#e23b3b", "#22c55e", "#3b82f6")   # x, y, z
RING_SCALE = 0.6                        # the ring's radius in units of the bounding radius of what it acts on (half its diagonal) ...
SCREEN_FRACTION = 0.30                  # ... but never more than this fraction of the 3D view's smaller side, so it always stays inside the picture
ARROW_SCALE = 0.85                      # the move arrows' length in units of the ring's radius (they lie inside the rings)
GRAB_PX = 9.0                           # how close (pixels) the pointer must be to a ring to take it
HINT = "Drag a ring to turn · drag an arrow to move · drag the ball to move freely · hold Shift to snap (5°, round mm)"


def _rgb(h: str):
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (1, 3, 5))


def ring_axes(a: int):
    """(u, v) of the ring about axis a: u x v = the axis (a right-handed turn)."""
    u = np.zeros(3)
    v = np.zeros(3)
    u[(a + 1) % 3] = 1.0
    v[(a + 2) % 3] = 1.0
    return u, v


def rot_about(axis: int, rad: float) -> np.ndarray:
    """The right-handed turn by rad about x (0), y (1) or z (2)."""
    c, s = round(math.cos(rad), 12), round(math.sin(rad), 12)
    i, j = ((1, 2), (2, 0), (0, 1))[axis]
    m = np.eye(3)
    m[i, i], m[i, j], m[j, i], m[j, j] = c, -s, s, c
    return m


def _poly(pts: np.ndarray, closed: bool = True) -> vtkPolyData:
    pd = vtkPolyData()
    p = vtkPoints()
    p.SetData(numpy_to_vtk(np.ascontiguousarray(pts, np.float64), deep=True))
    pd.SetPoints(p)
    n = len(pts)
    ids = list(range(n)) + ([0] if closed else [])
    cells = np.array([len(ids)] + ids, np.int64)
    ca = vtkCellArray()
    ca.ImportLegacyFormat(numpy_to_vtkIdTypeArray(cells, deep=True))
    pd.SetLines(ca)
    return pd


def nice_step_mm(size_m: float) -> float:
    """A round step in mm (1, 2, 5, 10, 20, 50 ...) of about 1/40 of the model's size: what a Shift-drag of an arrow snaps to."""
    raw = max(size_m * 1000.0 / 40.0, 1e-6)
    e = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 5, 10):
        if raw <= m * e:
            return m * e
    return 10 * e


class RotateGizmo:
    """Rings (turn), arrows (move along an axis) and a ball (move in the view's plane) around the middle of what is acted on.
    callback(kind, axis, value, final): kind 'rot' (axis 0..2, value an angle in rad), 'move' (axis 0..2, value a distance in m) or
    'free' (axis 3, value a shift [dx, dy, dz] in m); called first with a zero value and final False when a drag starts."""

    def __init__(self, view):
        self.v = view
        self.on = False
        self.paused = False                     # True while a face is being picked: the handles are hidden and cannot take a click
        self.center = np.zeros(3)
        self.size = 1.0
        self.bound = 0.7                        # the bounding radius of what the gizmo acts on (half the diagonal of its box)
        self._drawn_r = 0.0
        self._redraw_queued = False
        self.callback = None
        self.actors: list = []
        self.hover: tuple | None = None         # ("rot" | "move" | "free", axis) under the pointer
        self.drag: dict | None = None
        self.R = np.eye(3)                      # the previewed turn, about self.pivot ...
        self.shift = np.zeros(3)                # ... then the previewed move (m)
        self.pivot = np.zeros(3)
        self.target: int | None = None          # the volume the preview acts on (None: every part)
        self.marker = None                      # (point, direction, length) of the arrow on the face being aligned
        self.marker_actors: list = []
        self.text = None
        self.text_actor: vtkTextActor | None = None
        try:
            view.ren.GetActiveCamera().AddObserver("ModifiedEvent", self._camera_moved)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ the camera, without a render window
    def _size(self):
        """The 3D view's size in DEVICE pixels (what the render window and the mouse events use: twice the widget's on a 200 % screen)."""
        if self.v.rw is not None:
            w, h = self.v.rw.GetSize()
            if w > 0 and h > 0:
                return int(w), int(h)
        return max(self.v.iren.width(), 1), max(self.v.iren.height(), 1)

    def dpr(self) -> float:
        return self._size()[0] / max(self.v.iren.width(), 1)

    def _matrix(self) -> np.ndarray:
        w, h = self._size()
        m = self.v.ren.GetActiveCamera().GetCompositeProjectionTransformMatrix(w / h, -1, 1)
        return np.array([[m.GetElement(i, j) for j in range(4)] for i in range(4)])

    def project(self, p) -> tuple[float, float, float]:
        """(x, y, depth) of a world point in pixels of the 3D view (origin top-left); depth in the camera's -1..1."""
        w, h = self._size()
        q = self._matrix() @ np.array([p[0], p[1], p[2], 1.0])
        if abs(q[3]) < 1e-12:
            return 0.0, 0.0, 9.0
        q = q / q[3]
        return float((q[0] + 1) / 2 * w), float((1 - q[1]) / 2 * h), float(q[2])

    def ray(self, x: float, y: float) -> tuple[np.ndarray, np.ndarray]:
        """The line under pixel (x, y): (origin, unit direction) in world coordinates."""
        w, h = self._size()
        inv = np.linalg.inv(self._matrix())
        out = []
        for z in (-1.0, 1.0):
            q = inv @ np.array([x / w * 2 - 1, 1 - y / h * 2, z, 1.0])
            out.append(q[:3] / q[3])
        d = out[1] - out[0]
        return out[0], d / (np.linalg.norm(d) or 1.0)

    # ------------------------------------------------------------------ the rings and arrows
    def _scale(self) -> float:
        """The ring's radius now (m): 0.6 x the bounding radius of what the gizmo acts on, cut down so that it takes at most 30 % of the
        picture's smaller side where it is seen (zoomed in on a small part, or a model that fills the view, it stays inside)."""
        base = RING_SCALE * self.bound
        try:
            cam = self.v.ren.GetActiveCamera()
            d = np.array(cam.GetFocalPoint()) - np.array(cam.GetPosition())
            right = np.cross(d, np.array(cam.GetViewUp()))
            n = float(np.linalg.norm(right))
            if n < 1e-12:
                return base
            right = right / n
            c = self.c
            x0, y0, _ = self.project(c)
            x1, y1, _ = self.project(c + right * base)
            px = math.hypot(x1 - x0, y1 - y0)                 # the ring's size on the screen if drawn at its natural size
            w, h = self._size()
            room = SCREEN_FRACTION * min(w, h)
            return base if px <= room or px < 1e-9 else base * room / px
        except Exception:  # noqa: BLE001
            return base

    @property
    def radius(self) -> float:
        return self._scale()

    @property
    def arrow_length(self) -> float:
        return ARROW_SCALE * self._scale()

    @property
    def c(self) -> np.ndarray:
        """Where the gizmo is now: the middle of what it acts on, plus the previewed move."""
        return self.center + self.shift

    def ring_points(self, a: int, n: int = 120) -> np.ndarray:
        u, v = ring_axes(a)
        t = np.linspace(0, 2 * np.pi, n, endpoint=False)
        return self.c + self._r * (np.cos(t)[:, None] * u + np.sin(t)[:, None] * v)

    def arrow_tip(self, a: int) -> np.ndarray:
        e = np.zeros(3)
        e[a] = 1.0
        return self.c + ARROW_SCALE * self._r * e

    def set(self, center, size: float, callback, target: int | None = None, bound: float | None = None):
        """Show the gizmo around `center` (the pivot of the turn), `size` the extent of what it acts on (m), `bound` its bounding radius."""
        self.center = np.asarray(center, float)
        self.size = max(float(size), 1e-9)
        self.bound = max(float(bound) if bound else 0.7 * self.size, 1e-9)
        self.callback = callback
        self.target = target
        self.on = True
        self._draw()

    def set_paused(self, on: bool):
        """Hide the handles (and let every click through to the model) while the user is picking a face: the rings run over the model, so a
        click on a face would otherwise grab a ring instead (the align steps: click the surface, click the plane)."""
        on = bool(on)
        if on == self.paused:
            return
        self.paused = on
        self.drag = None
        self.hover = None
        if self.on:
            self._draw()
            self.v.render()

    def clear(self):
        self.on = False
        self.drag = None
        self.hover = None
        self.callback = None
        self.v._clear(self.actors)
        self._hint(False)
        self.v.render()

    @property
    def _r(self) -> float:
        return self._drawn_r if self._drawn_r > 0 else self._scale()

    def _camera_moved(self, *_):
        """The camera moved (a zoom, the view turned, the first fit): draw the gizmo again if its size on the screen should change."""
        if not self.on or self.drag is not None or self._redraw_queued:
            return
        r = self._scale()
        if self._drawn_r > 0 and abs(r - self._drawn_r) > 0.04 * self._drawn_r:
            self._redraw_queued = True
            QTimer.singleShot(0, self._redraw)

    def _redraw(self):
        self._redraw_queued = False
        if self.on and self.drag is None:
            self._draw()

    def _tube(self, pts, radius, color, opacity, closed=True):
        tube = vtkTubeFilter()
        tube.SetInputData(_poly(pts, closed))
        tube.SetRadius(radius)
        tube.SetNumberOfSides(10)
        tube.Update()
        m = vtkPolyDataMapper()
        m.SetInputConnection(tube.GetOutputPort())
        act = vtkActor()
        act.SetMapper(m)
        pr = act.GetProperty()
        pr.SetColor(*_rgb(color))
        pr.SetOpacity(opacity)
        pr.SetAmbient(1.0)
        pr.SetDiffuse(0.0)
        act.PickableOff()
        self.v.ren2.AddActor(act)                      # (the top layer: the model never hides a handle)
        self.actors.append(act)

    def _solid(self, src, color, opacity, pos=None):
        m = vtkPolyDataMapper()
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
        self.v.ren2.AddActor(act)
        self.actors.append(act)
        return act

    def _state_of(self, kind: str, a: int):
        hot = self.hover == (kind, a) or (self.drag is not None and (self.drag["kind"], self.drag["axis"]) == (kind, a))
        busy = self.hover is not None or self.drag is not None
        return hot, busy and not hot

    def _draw(self):
        v = self.v
        v._clear(self.actors)
        if not self.on or self.paused:
            return
        self._drawn_r = self._scale()
        R = self._drawn_r
        for a in range(3):
            hot, dim = self._state_of("rot", a)
            self._tube(self.ring_points(a), R * (0.016 if hot else 0.009), RING_COLORS[a], 1.0 if hot else 0.35 if dim else 0.85)
        for a in range(3):                              # the move arrows: a shaft from the middle and a cone at its end
            hot, dim = self._state_of("move", a)
            op = 1.0 if hot else 0.35 if dim else 0.95
            tip = self.arrow_tip(a)
            self._tube(np.array([self.c, tip]), R * (0.014 if hot else 0.008), RING_COLORS[a], op, closed=False)
            cone = vtkConeSource()
            cone.SetHeight(R * 0.15)
            cone.SetRadius(R * (0.055 if hot else 0.043))
            cone.SetResolution(16)
            e = np.zeros(3)
            e[a] = 1.0
            cone.SetCenter(*(tip + e * R * 0.075))
            cone.SetDirection(*e)
            self._solid(cone, RING_COLORS[a], op)
        hot, dim = self._state_of("free", 3)            # the ball in the middle: a free move in the plane of the view
        ball = vtkSphereSource()
        ball.SetRadius(R * (0.05 if hot else 0.038))
        ball.SetThetaResolution(20)
        ball.SetPhiResolution(12)
        ball.SetCenter(*self.c)
        self._solid(ball, "#f5f5f5", 1.0 if hot else 0.35 if dim else 0.95)
        self._hint(True)
        v.render()

    def _hint(self, on: bool):
        """The gizmo's hint in the bar under the 3D view (it never covers the picture)."""
        bar = getattr(self.v, "banner", None)
        if bar is None:
            return
        bar.set("hint", HINT, 0) if on else bar.clear("hint")

    def show_angle(self, text: str | None):
        """What the drag in progress does (an angle, a distance), written in the corner of the 3D view (None: remove it)."""
        self.text = text
        v = self.v
        if v.rw is None:
            return
        if self.text_actor is None:
            self.text_actor = vtkTextActor()
            self.text_actor.GetTextProperty().SetFontSize(20)
            self.text_actor.GetTextProperty().SetBold(1)
            self.text_actor.SetPosition(12, 34)
            self.text_actor.PickableOff()
        if text:
            self.text_actor.GetTextProperty().SetColor(*_rgb(theme.tokens()["vp_text"]))
            self.text_actor.SetInput(text)
            if not v.ren2.HasViewProp(self.text_actor):
                v.ren2.AddViewProp(self.text_actor)
        else:
            v.ren2.RemoveViewProp(self.text_actor)
        v.render()

    # ------------------------------------------------------------------ the pointer
    @staticmethod
    def _seg_dist(pts: np.ndarray, p, closed: bool) -> float:
        nxt = np.roll(pts, -1, axis=0) if closed else np.vstack([pts[1:], pts[-1:]])
        e = nxt - pts
        L2 = np.maximum((e ** 2).sum(1), 1e-12)
        t = np.clip(((np.array(p) - pts) * e).sum(1) / L2, 0, 1)
        return float(np.min(np.linalg.norm(pts + t[:, None] * e - np.array(p), axis=1)))

    def pick(self, x: float, y: float) -> tuple | None:
        """The handle under pixel (x, y) (within a few pixels of its drawn line): ("free", 3) the ball, ("move", a) an arrow or ("rot", a)
        a ring; the nearest wins, the ball and the arrows before the rings where they meet."""
        if not self.on or self.paused:
            return None
        best, bd = None, GRAB_PX * self.dpr()
        c = self.project(self.c)
        d = math.hypot(c[0] - x, c[1] - y)
        if d < 11 * self.dpr():
            return ("free", 3)
        for a in range(3):
            pts = np.array([self.project(p)[:2] for p in (self.c, self.arrow_tip(a))])
            d = self._seg_dist(pts, (x, y), False) - 1.5         # (arrows first)
            if d < bd:
                best, bd = ("move", a), d
        for a in range(3):
            pts = np.array([self.project(p)[:2] for p in self.ring_points(a, 180)])
            d = self._seg_dist(pts, (x, y), True)
            if d < bd:
                best, bd = ("rot", a), d
        return best

    def angle_at(self, a: int, x: float, y: float) -> float | None:
        """The angle (rad) of the pointer on the plane of ring a, round the centre; None when the pointer's line runs along that plane."""
        o, d = self.ray(x, y)
        n = np.zeros(3)
        n[a] = 1.0
        den = float(d @ n)
        if abs(den) < 1e-6:
            return None
        p = o + d * (float((self.c - o) @ n) / den) - self.c
        u, v = ring_axes(a)
        return math.atan2(float(p @ v), float(p @ u))

    def along(self, a: int, x: float, y: float, origin=None) -> float | None:
        """The distance along axis a (from `origin`, default the gizmo's middle) of the point of that axis nearest to the pointer's line."""
        o, d = self.ray(x, y)
        u = np.zeros(3)
        u[a] = 1.0
        w0 = o - (self.c if origin is None else np.asarray(origin, float))
        b = float(d @ u)
        den = 1 - b * b
        if den < 1e-6:
            return None                                     # (looking straight along the arrow: it cannot be dragged)
        return float((u @ w0 - b * (d @ w0)) / den)

    def in_view_plane(self, x: float, y: float, origin) -> np.ndarray | None:
        """The point on the plane through `origin` facing the camera that is under the pointer."""
        cam = self.v.ren.GetActiveCamera()
        n = np.array(cam.GetPosition()) - np.array(cam.GetFocalPoint())
        n = n / (np.linalg.norm(n) or 1.0)
        o, d = self.ray(x, y)
        den = float(d @ n)
        if abs(den) < 1e-6:
            return None
        return o + d * (float((np.asarray(origin, float) - o) @ n) / den)

    def _cursor(self, shape):
        c = self.v.iren
        shape = shape if shape is not None else getattr(self.v, "pick_cursor", None)      # (while a face is picked the pointer stays a pointing hand)
        c.setCursor(shape) if shape is not None else c.unsetCursor()

    def hover_at(self, x: float, y: float):
        """The pointer moved over the view (no button): light the handle under it and say, with the cursor, that it can be dragged."""
        h = self.pick(x, y) if self.on and self.drag is None else self.hover
        if self.drag is None and h != self.hover:
            self.hover = h
            self._cursor((Qt.SizeAllCursor if h[0] == "free" else Qt.OpenHandCursor) if h is not None else None)
            self._draw()
        return h

    def press(self, x: float, y: float) -> bool:
        """A button went down at (x, y): True when it took a handle (the camera must not move then)."""
        h = self.pick(x, y)
        if h is None:
            return False
        kind, a = h
        if kind == "rot":
            a0 = self.angle_at(a, x, y)
            d = {"kind": kind, "axis": a, "last": a0, "total": 0.0}
        elif kind == "move":
            a0 = self.along(a, x, y)
            d = {"kind": kind, "axis": a, "t0": a0, "total": 0.0, "origin": self.c.copy()}
        else:
            a0 = self.in_view_plane(x, y, self.c)
            d = {"kind": kind, "axis": 3, "p0": a0, "origin": self.c.copy(), "total": np.zeros(3)}
        if a0 is None:
            return False
        self.drag = d
        self.hover = h
        self._cursor(Qt.ClosedHandCursor)
        self._draw()
        if self.callback:
            self.callback(kind, a, np.zeros(3) if kind == "free" else 0.0, False)
        return True

    def _snap_angle(self, total: float, shift: bool) -> float:
        if not shift:
            return total
        s = math.radians(SNAP_DEG)
        return round(total / s) * s

    def snap_mm(self) -> float:
        return nice_step_mm(self.size)

    def _snap_len(self, total, shift: bool):
        if not shift:
            return total
        s = self.snap_mm() / 1000.0
        return np.round(np.asarray(total) / s) * s if isinstance(total, np.ndarray) else round(total / s) * s

    def drag_to(self, x: float, y: float, shift: bool = False):
        """The pointer moved with the button down: the value of the drag now (an angle in rad, a distance in m, a shift in m)."""
        d = self.drag
        if d is None:
            return None
        kind, a = d["kind"], d["axis"]
        if kind == "rot":
            ang = self.angle_at(a, x, y)
            if ang is None:
                return None
            step = ang - d["last"]
            step -= 2 * math.pi * round(step / (2 * math.pi))           # (unwrapped: more than a whole turn is allowed)
            d["last"] = ang
            d["total"] += step
            val = self._snap_angle(d["total"], shift)
        elif kind == "move":
            t = self.along(a, x, y, d["origin"])
            if t is None:
                return None
            d["total"] = t - d["t0"]
            val = self._snap_len(d["total"], shift)
        else:
            p = self.in_view_plane(x, y, d["origin"])
            if p is None:
                return None
            d["total"] = p - d["p0"]
            val = self._snap_len(d["total"], shift)
        if self.callback:
            self.callback(kind, a, val, False)
        return val

    def release(self, x: float, y: float, shift: bool = False):
        d, self.drag = self.drag, None
        if d is None:
            return None
        kind, a = d["kind"], d["axis"]
        val = self._snap_angle(d["total"], shift) if kind == "rot" else self._snap_len(d["total"], shift)
        self.hover = self.pick(x, y)
        self._cursor(None if self.hover is None else Qt.OpenHandCursor)
        self._draw()
        if self.callback:
            self.callback(kind, a, val, True)
        return val

    # ------------------------------------------------------------------ the preview of the model turning and moving
    def _user_matrix(self) -> vtkMatrix4x4 | None:
        if np.allclose(self.R, np.eye(3)) and not np.abs(self.shift).max() > 1e-12:
            return None
        t = self.pivot - self.R @ self.pivot + self.shift
        m = vtkMatrix4x4()
        for i in range(3):
            for j in range(3):
                m.SetElement(i, j, float(self.R[i, j]))
            m.SetElement(i, 3, float(t[i]))
        return m

    def set_preview(self, R, pivot, shift=None, target: int | None = None):
        """Turn the drawn parts (not the air box) by R about `pivot`, then move them by `shift` (m); None puts them back. `target`: one
        body (its volume id): the others stay. The picking index is not turned."""
        self.R = np.eye(3) if R is None else np.asarray(R, float)
        self.pivot = np.asarray(pivot if pivot is not None else np.zeros(3), float)
        self.shift = np.zeros(3) if shift is None else np.asarray(shift, float)
        self.target = target
        self.apply_preview()
        if self.on:
            self._draw()                                  # (the handles go with the model)
        self.v.render()

    def apply_preview(self):
        """Give the model's actors the turn (also to the ones drawn later: highlights are made again whenever the selection changes)."""
        v = self.v
        m = self._user_matrix() or vtkMatrix4x4()
        ident = vtkMatrix4x4()
        keys = list(getattr(v, "geo_keys", []))
        for i, (a, e) in enumerate(zip(v.geo_actors, v.edge_actors)):
            mine = self.target is None or (i < len(keys) and keys[i] == self.target)
            a.SetUserMatrix(m if mine else ident)
            e.SetUserMatrix(m if mine else ident)
        for a in list(v.mark_actors) + list(self.marker_actors):
            a.SetUserMatrix(m if self.target is None else ident)

    @property
    def previewing(self) -> bool:
        return not np.allclose(self.R, np.eye(3)) or bool(np.abs(self.shift).max() > 1e-12)

    # ------------------------------------------------------------------ the arrow on the face being aligned
    def set_marker(self, point, direction, length: float):
        """An arrow from `point` (a point of the picked face) along `direction` (where it points now); None removes it. Both are
        in the model's stored orientation: the preview turn is applied here, as the model's own."""
        v = self.v
        v._clear(self.marker_actors)
        self.marker = None if point is None else (np.asarray(point, float), np.asarray(direction, float), float(length))
        if self.marker is None:
            v.render()
            return
        p, d, L = self.marker
        d = d / (np.linalg.norm(d) or 1.0)
        src = vtkArrowSource()
        src.SetTipResolution(16)
        src.SetShaftResolution(12)
        src.Update()
        # the arrow lies along +x; turn it onto d, scale it to L, put its tail on the point
        z = np.cross([1.0, 0, 0], d)
        s = float(np.linalg.norm(z))
        tr = vtkTransform()
        tr.Translate(*p)
        if s > 1e-9:
            tr.RotateWXYZ(math.degrees(math.atan2(s, d[0])), *(z / s))
        elif d[0] < 0:
            tr.RotateWXYZ(180, 0, 0, 1)
        tr.Scale(L, L, L)
        f = vtkTransformPolyDataFilter()
        f.SetInputConnection(src.GetOutputPort())
        f.SetTransform(tr)
        f.Update()
        m = vtkPolyDataMapper()
        m.SetInputConnection(f.GetOutputPort())
        act = vtkActor()
        act.SetMapper(m)
        act.GetProperty().SetColor(1.0, 0.83, 0.23)
        act.GetProperty().SetAmbient(0.9)
        act.PickableOff()
        v.ren2.AddActor(act)
        self.marker_actors.append(act)
        self.apply_preview()
        v.render()

    # ------------------------------------------------------------------ for the control API and the tests
    def describe(self) -> dict:
        h = self.hover
        d = self.drag
        return {"rings": self.on, "hover": None if h is None else (f"{h[0]}:{'xyz'[h[1]] if h[1] < 3 else 'free'}"),
                "dragging": None if d is None else f"{d['kind']}:{'xyz'[d['axis']] if d['axis'] < 3 else 'free'}",
                "previewing": self.previewing, "preview_matrix": self.R.tolist(), "preview_shift_mm": (self.shift * 1000).round(6).tolist(),
                "pivot": self.pivot.tolist(), "center": self.center.tolist(), "ring_radius": self._r, "bound": self.bound, "marker": self.marker is not None,
                "target_volume": self.target, "angle_text": self.text, "hint": HINT if self.on else "", "snap_mm": self.snap_mm()}

    def ring_point(self, a: int, deg: float) -> np.ndarray:
        """The world point of ring a at the given angle (0 along u)."""
        u, v = ring_axes(a)
        t = math.radians(deg)
        return self.c + self._r * (math.cos(t) * u + math.sin(t) * v)

    def simulate_drag(self, a: int, degrees: float, shift: bool = False, start_deg: float = 20.0, steps: int = 6) -> dict:
        """A mouse drag along ring a that turns it by `degrees`: the press, a few moves and the release at the pixels where those
        points of the ring are drawn, through the same code as the real mouse."""
        x, y, _ = self.project(self.ring_point(a, start_deg))
        if not self.press(x, y) or self.drag["kind"] != "rot" or self.drag["axis"] != a:
            if self.drag is not None:
                self.release(x, y)
            raise ValueError(f"The {'xyz'[a]} ring cannot be taken from the camera's point of view: turn the 3D view so that it is not edge-on")
        out = None
        steps = max(steps, int(abs(degrees) // 20) + 1)
        for k in range(1, steps + 1):
            x, y, _ = self.project(self.ring_point(a, start_deg + degrees * k / steps))
            out = self.drag_to(x, y, shift)
        self.release(x, y, shift)
        return {"kind": "rot", "axis": "xyz"[a], "degrees": math.degrees(out or 0.0)}

    def simulate_move(self, a: int, mm: float, shift: bool = False, steps: int = 6, along_frac: float = 0.6) -> dict:
        """A mouse drag of arrow a (a = 3: the ball, mm is then a drag of the pointer to the right by that many mm in the view's plane)
        by `mm` along it, through the same code as the real mouse."""
        if a == 3:
            c0 = self.c.copy()
            x, y, _ = self.project(c0)
            if not self.press(x, y) or self.drag["kind"] != "free":
                raise ValueError("The centre ball cannot be taken")
            cam = self.v.ren.GetActiveCamera()
            right = np.cross(np.array(cam.GetFocalPoint()) - np.array(cam.GetPosition()), cam.GetViewUp())
            right = right / np.linalg.norm(right)
            out = None
            for k in range(1, steps + 1):
                x, y, _ = self.project(c0 + right * mm / 1000.0 * k / steps)
                out = self.drag_to(x, y, shift)
            self.release(x, y, shift)
            return {"kind": "free", "shift_mm": (np.asarray(out) * 1000).round(4).tolist()}
        e = np.zeros(3)
        e[a] = 1.0
        base = self.c.copy()
        for frac in (along_frac, 0.8, 0.45, 0.95, 0.3):             # (somewhere along the arrow where it is the handle nearest to the pointer)
            along_frac = frac
            x, y, _ = self.project(base + e * ARROW_SCALE * self._r * frac)
            if self.press(x, y) and self.drag["kind"] == "move" and self.drag["axis"] == a:
                break
            if self.drag is not None:
                self.release(x, y)
        else:
            raise ValueError(f"The {'xyz'[a]} arrow cannot be taken from the camera's point of view: turn the 3D view so that it is not end-on")
        out = None
        for k in range(1, steps + 1):
            x, y, _ = self.project(base + e * (ARROW_SCALE * self._r * along_frac + mm / 1000.0 * k / steps))
            out = self.drag_to(x, y, shift)
        self.release(x, y, shift)
        return {"kind": "move", "axis": "xyz"[a], "mm": float(out) * 1000.0}
