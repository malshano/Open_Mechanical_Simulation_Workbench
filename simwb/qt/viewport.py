"""The 3D view: VTK inside a Qt widget. Draws the CAD faces, a mesh or a result surface (from the payloads the engine makes:
base64 float arrays), with a colour bar, depth-peeled transparency (see-through surfaces are drawn in the right order from
every side) and the orientation triad."""
from __future__ import annotations

import base64
import os
import time

import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QVBoxLayout, QWidget
from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor
from vtkmodules.util.numpy_support import numpy_to_vtk, numpy_to_vtkIdTypeArray
from vtkmodules.vtkCommonCore import vtkLookupTable, vtkPoints
from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkPolyData
from vtkmodules.vtkFiltersCore import vtkCellDataToPointData, vtkContourFilter, vtkFeatureEdges, vtkPolyDataNormals, vtkStripper
from vtkmodules.vtkFiltersSources import vtkArrowSource, vtkCubeSource
from vtkmodules.vtkCommonTransforms import vtkTransform
from vtkmodules.vtkFiltersGeneral import vtkTransformFilter
from vtkmodules.vtkIOImage import vtkPNGWriter
from vtkmodules.vtkInteractionStyle import vtkInteractorStyleTrackballCamera
from vtkmodules.vtkInteractionWidgets import vtkOrientationMarkerWidget
from vtkmodules.vtkRenderingAnnotation import vtkAxesActor, vtkScalarBarActor
from vtkmodules.vtkRenderingCore import (vtkActor, vtkBillboardTextActor3D, vtkPolyDataMapper, vtkRenderer, vtkWindowToImageFilter)
from vtkmodules.vtkRenderingFreeType import vtkVectorText  # noqa: F401  (registers the text renderer)
import vtkmodules.vtkRenderingOpenGL2  # noqa: F401  (the OpenGL back end)
import vtkmodules.vtkRenderingFreeType  # noqa: F401

from . import colormaps, theme
from .banner import Banner
from .gizmo import RotateGizmo
from .legend import Legend
from .picking import FaceIndex, locator_for, long_segment
from .viewtools import ViewBar

# SIMWB_NO_GL=1: no OpenGL window (automated tests, machines without a display). The pipeline is built and described exactly the
# same; only the drawing and the pictures are left out.
def no_gl() -> bool:
    return bool(os.environ.get("SIMWB_NO_GL"))

BODY_COLORS = ["#9fb4cc", "#c9b88a", "#9bc2a0", "#c4a0cf", "#d0a09a", "#8fc9c9"]


def _b64(s: str, dtype) -> np.ndarray:
    return np.frombuffer(base64.b64decode(s), dtype)


def _rgb(h: str):
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (1, 3, 5))


class Viewport(QWidget):
    """One VTK render window. Methods return nothing; the engine's payloads go in, pictures come out.

    PICKING CONTRACT (used by the geometry, setup and results panels; the real picking is the "viewtools" worktree's job - until it
    lands the signals below can be emitted by hand, which is what the control API's view.pick_face does, so panels can be built and
    tested against it):
      facePicked(face_id, add)  - the user clicked a CAD face (add: Ctrl/Shift held = add to the selection)
      faceHovered(face_id)      - the face under the pointer, -1 for none
      pointPicked(x, y, z)      - a point on the shown surface was clicked (probe, line plot, align to a wall) [m]
      set_pick_mode(mode)       - None | "face" | "point": what a click does now
      set_selection(ids)        - faces drawn as selected
      set_face_tints({id: '#rrggbb'}) - faces tinted by what they carry (a boundary condition's colour)
    """
    facePicked = Signal(int, bool)
    faceHovered = Signal(int)
    pointPicked = Signal(float, float, float)
    selectionChanged = Signal(list)       # the selection after a click in face mode

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)               # (no gaps between the toolbar, the 3D view and the legend: they showed the plain backdrop)
        headless = no_gl()
        self.iren = QWidget(self) if headless else QVTKRenderWindowInteractor(self)
        lay.addWidget(self.iren, 1)
        self.banner = Banner()          # what to do now, in a bar of its own under the picture (the gizmo's hint, the align step's instruction)
        lay.addWidget(self.banner)
        self.legend = Legend()
        lay.addWidget(self.legend)
        self.viewbar = ViewBar(self)
        lay.insertWidget(0, self.viewbar)
        self.rw = None if headless else self.iren.GetRenderWindow()
        if self.rw is not None:
            self.rw.SetAlphaBitPlanes(1)  # (depth peeling needs an alpha channel and no multisampling)
            self.rw.SetMultiSamples(0)
        self.ren = vtkRenderer()
        self.ren.GradientBackgroundOn()
        self.ren.SetUseDepthPeeling(1)    # see-through surfaces (contours, glass) in the right order from every side
        self.ren.SetMaximumNumberOfPeels(12)
        self.ren.SetOcclusionRatio(0.0)
        # a second layer on top, sharing the camera, for what is hung on the model (labels): never cut by the model itself
        self.ren2 = vtkRenderer()
        self.ren2.SetLayer(1)
        self.ren2.InteractiveOff()
        self.ren2.SetActiveCamera(self.ren.GetActiveCamera())
        self._style = None                   # the camera's interactor style (put aside while a label is dragged, then given back)
        if self.rw is not None:
            self.rw.SetNumberOfLayers(2)
            self.rw.AddRenderer(self.ren)
            self.rw.AddRenderer(self.ren2)
            style = self._style = vtkInteractorStyleTrackballCamera()
            self.iren.SetInteractorStyle(style)
            style.AddObserver("StartInteractionEvent", lambda *_: self._user_moved())      # (the user took the camera: stop re-fitting it)
            # (VTK's camera style grabs the mouse on a press: the release then goes ONLY to it and never reaches _on_release, so a click on a
            # face - a press and release that hardly moved - was never seen when the press went to the camera. The style ends its interaction
            # on that release: take it as the release. _on_release forgets the press, so the other path cannot count the click twice.)
            style.AddObserver("EndInteractionEvent", lambda *_: self._on_release(None, None))
        self._focus = None      # (x0, x1, y0, y1, z0, z1): the camera is fitted to this region (a flow cut open: close to the object) instead of everything drawn
        self._autofit = False   # the camera was fitted by the program (not moved by the user): it is fitted again when the view is resized
        cam = self.ren.GetActiveCamera()
        cam.SetPosition(1, -1.4, 0.9)     # (an oblique start: a camera straight above the up axis warns)
        cam.SetViewUp(0, 0, 1)
        self.geo_actors: list = []
        self.air_actors: list = []          # the air box of a flow study: translucent, shown or hidden on request
        self.air_on = False
        self._geo_shown = True
        self.overlay_actors: list = []
        self.bar = None
        self.pick_mode: str | None = None
        self.selection: list = []
        self.face_tints: dict = {}
        self.index = FaceIndex()            # ray picking over the CAD faces (works without a window)
        self._overlay_index = None          # ... and over the drawn result surface
        self.edge_actors: list = []
        self.mark_actors: list = []         # selection, hover and tint highlights
        self.gizmo = RotateGizmo(self)      # the rings that turn the model, its preview and the align arrow (gizmo.py)
        self._gizmo_drag = False
        self.label_actors: list = []        # text hung on the model (what a boundary condition carries)
        self.arrow_actors: list = []        # loads and gravity as arrows
        self._label_items: list = []
        self._label_state: list = []        # per label: its point, size and place on the screen (to find the one under the mouse)
        self._label_user: dict = {}         # key -> (dx, dy) in pixels: where the user dragged a label to (kept while the same result is shown)
        self._drag = None                   # (label index, press position, offset at the press, the camera style put aside)
        self._shown_name = None
        self.edges_on = True
        self.geo_keys: list = []             # the volume of each drawn group (parallel to geo_actors and edge_actors)
        self.hidden_bodies: set = set()
        self._solid_parts: list = []
        self._air_parts: list = []
        self.hover_id = -1
        self.overlays: list = []             # widgets laid over the 3D picture; each has place()
        self.pick_cursor = None              # the cursor to show over the picture while a face is being picked
        self._pick_box = None               # the box round everything that can be picked, air volume included (rays are made long enough to reach it)
        self.last_hit = None
        self._ov = None
        self.hover_highlight = True
        self._press = None
        self._hover_t = 0.0
        self.overlay_info: dict = {"kind": "none"}     # what the overlay shows (for the control API and for checking)
        self.geometry_info: dict = {"faces": 0, "bodies": 0}
        self._anim = None      # a mode shape swinging: (points, base, disp, amp)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        if self.rw is not None:
            self._triad()
            self.iren.Initialize()
            self.iren.Start()
        self.apply_theme()
        theme.bus.changed.connect(lambda _: self.apply_theme())
        if self.rw is not None:
            self.iren.AddObserver("LeftButtonPressEvent", self._on_press, 100)
            self.iren.AddObserver("LeftButtonReleaseEvent", self._on_release, 100)
            self.iren.AddObserver("MouseMoveEvent", self._on_move, 100)

    # ------------------------------------------------------------------ small things
    def apply_theme(self):
        """Background gradient, axis labels and outlines in the colours of the theme (they must stay readable in both)."""
        t = theme.tokens()
        rgb = lambda h: tuple(int(h[i:i + 2], 16) / 255.0 for i in (1, 3, 5))  # noqa: E731
        self.ren.SetBackground(*rgb(t["vp_bottom"]))
        self.ren.SetBackground2(*rgb(t["vp_top"]))
        for a in self.edge_actors:
            a.GetProperty().SetColor(*rgb(t["edge"]))
        for a in self.air_actors[1::2]:            # (each air box is a body and its outline)
            a.GetProperty().SetColor(*rgb(t["air_edge"]))
        if getattr(self, "label_actors", None):
            self.set_labels(self._label_items)
        if getattr(self, "_axes", None) is not None:
            for cap in (self._axes.GetXAxisCaptionActor2D(), self._axes.GetYAxisCaptionActor2D(), self._axes.GetZAxisCaptionActor2D()):
                cap.GetCaptionTextProperty().SetColor(*rgb(t["vp_text"]))
        self.render()

    def _triad(self):
        axes = vtkAxesActor()
        axes.SetTotalLength(1, 1, 1)
        self._axes = axes
        w = vtkOrientationMarkerWidget()
        w.SetOrientationMarker(axes)
        w.SetInteractor(self.iren)
        w.SetViewport(0.0, 0.0, 0.16, 0.2)
        w.SetEnabled(1)
        w.InteractiveOff()
        self._triad_widget = w

    def render(self):
        if self.rw is not None:
            self.rw.Render()

    def _clear(self, actors: list):
        for a in actors:
            for r in (self.ren, self.ren2):
                r.RemoveActor(a)
                r.RemoveViewProp(a)
        actors.clear()

    def clear_overlay(self):
        self._timer.stop()
        self._anim = None
        self._clear(self.overlay_actors)
        self._overlay_index = None
        self._ov = None
        self.legend.clear()
        self.overlay_info = {"kind": "none"}

    # ------------------------------------------------------------------ picking
    def set_pick_mode(self, mode: str | None):
        """None: clicks do nothing (hover still reports faces) | 'face': a click selects a face | 'point': a click reports a surface point."""
        if mode not in (None, "face", "point"):
            raise ValueError("pick mode must be None, 'face' or 'point'")
        self.pick_mode = mode

    def set_selection(self, ids):
        self.selection = [int(i) for i in ids]
        self._rebuild_marks()
        self.render()

    def set_face_tints(self, tints: dict):
        self.face_tints = {int(k): str(v) for k, v in tints.items()}
        self._rebuild_marks()
        self.render()

    def set_hover_highlight(self, on: bool):
        self.hover_highlight = bool(on)
        self._rebuild_marks()
        self.render()

    def _rebuild_index(self):
        """The pickable faces: those of the parts that are not hidden (a hidden part cannot be clicked), and the air box."""
        solid = [f for f in self._solid_parts if self._face_volume.get(f[0]) not in self.hidden_bodies]
        self.index.build(solid, self._air_parts)
        allv = [np.asarray(f[1], float).reshape(-1, 3) for f in solid + list(self._air_parts) if len(f[1])]
        self._pick_box = (np.vstack(allv).min(0), np.vstack(allv).max(0)) if allv else None

    def _apply_visibility(self):
        for k, a, e in zip(self.geo_keys, self.geo_actors, self.edge_actors):
            on = self._geo_shown and k not in self.hidden_bodies
            a.SetVisibility(on)
            e.SetVisibility(on and self.edges_on)

    def set_hidden_bodies(self, ids):
        """Hide some parts (by volume id) in the view: they are not drawn and not pickable."""
        self.hidden_bodies = {int(i) for i in ids}
        self._rebuild_index()
        self._apply_visibility()
        self.render()

    def set_edges_visible(self, on: bool):
        self.edges_on = bool(on)
        self._apply_visibility()
        self.render()

    def _mark_actor(self, ids, color, opacity=0.85):
        pts, tris = self.index.triangles_of(ids)
        if not len(tris):
            return None
        pd = self._polydata(pts, tris)
        m = vtkPolyDataMapper()
        m.SetInputData(pd)
        m.SetResolveCoincidentTopologyToPolygonOffset()
        m.SetRelativeCoincidentTopologyPolygonOffsetParameters(-2, -2)
        a = vtkActor()
        a.SetMapper(m)
        pr = a.GetProperty()
        pr.SetColor(*_rgb(color))
        pr.SetOpacity(opacity)
        pr.SetAmbient(0.9)
        pr.SetDiffuse(0.2)
        a.PickableOff()
        a.SetVisibility(self._geo_shown)
        return a

    def _rebuild_marks(self):
        """The highlights drawn over the CAD faces: tints (what a face carries), the selection, the face under the pointer."""
        self._clear(self.mark_actors)
        by_colour: dict = {}
        for fid, col in self.face_tints.items():
            by_colour.setdefault(col, []).append(fid)
        items = [(ids, col, 0.5) for col, ids in by_colour.items()]      # (translucent: the part stays visible under it)
        items.append((self.selection, "#ff9f1c", 0.9))
        if self.hover_highlight and self.hover_id >= 0 and self.hover_id not in self.selection:
            air = self.hover_id in {p[0] for p in self._air_parts}
            items.append(([self.hover_id], "#4cc9f0" if air else "#ffd8a8", 0.3 if air else 0.8))   # (a wall of the air volume: a see-through tint)
        for ids, col, op in items:
            a = self._mark_actor(ids, col, op)
            if a is not None:
                self.ren.AddActor(a)
                self.mark_actors.append(a)
        self.gizmo.apply_preview()          # (a highlight is turned with the model while the orientation is previewed)

    # ------------------------------------------------------------------ values on the drawn result
    def extrema(self):
        """Where the drawn field is lowest and highest: {'min': {'value', 'point'}, 'max': {...}} (points on the surface as drawn), or None."""
        o = getattr(self, "_ov", None)
        if not o or self.overlay_info.get("kind") != "scalar":
            return None
        v = o["val"]
        ok = np.isfinite(v)
        if not ok.any():
            return None
        out = {}
        for key, fn in (("min", np.nanargmin), ("max", np.nanargmax)):
            i = int(fn(np.where(ok, v, np.nan)))
            if o["per"] == "face":
                pt, bp = o["pos"][o["idx"][i]].mean(axis=0), o["base"][o["idx"][i]].mean(axis=0)
            else:
                pt, bp = o["pos"][i], o["base"][i]
            out[key] = {"value": float(v[i]), "point": [float(x) for x in pt], "base": [float(x) for x in bp]}
        return out

    def base_point(self, cell: int, point):
        """The point of the undeformed part that a point of the drawn (deformed) surface came from."""
        o = getattr(self, "_ov", None)
        if not o or not (0 <= cell < len(o["idx"])):
            return list(point)
        tri = o["idx"][cell]
        a, b, c = (o["pos"][i] for i in tri)
        p = np.asarray(point, float)
        v0, v1, v2 = b - a, c - a, p - a
        d00, d01, d11, d20, d21 = v0 @ v0, v0 @ v1, v1 @ v1, v2 @ v0, v2 @ v1
        den = d00 * d11 - d01 * d01
        if abs(den) < 1e-30:
            return [float(x) for x in o["base"][tri].mean(axis=0)]
        w1, w2 = (d11 * d20 - d01 * d21) / den, (d00 * d21 - d01 * d20) / den
        w = np.array([1 - w1 - w2, w1, w2])
        return [float(x) for x in w @ o["base"][tri]]

    def value_at(self, cell: int, point):
        """The field's value at a point of the drawn surface (a face's value, or the corners' values weighted by where in the triangle)."""
        o = getattr(self, "_ov", None)
        if not o or not (0 <= cell < len(o["idx"])):
            return None
        tri = o["idx"][cell]
        if o["per"] == "face":
            return float(o["val"][cell])
        a, b, c = (o["pos"][i] for i in tri)
        p = np.asarray(point, float)
        v0, v1, v2 = b - a, c - a, p - a
        d00, d01, d11, d20, d21 = v0 @ v0, v0 @ v1, v1 @ v1, v2 @ v0, v2 @ v1
        den = d00 * d11 - d01 * d01
        if abs(den) < 1e-30:
            return float(o["val"][tri].mean())
        w1 = (d11 * d20 - d01 * d21) / den
        w2 = (d00 * d21 - d01 * d20) / den
        w = np.array([1 - w1 - w2, w1, w2])
        return float(o["val"][tri] @ w)

    def _labels_visible(self) -> bool:
        """Text hung on the model shows with the CAD model (what a condition carries) or with a drawn result (its highest and lowest values,
        pinned points): the result view hides the CAD model but its labels are the point of it."""
        return bool(self._geo_shown or self.overlay_info.get("kind") not in (None, "none"))

    def set_arrows(self, items: list):
        """Arrows hung on the model: [{'point': [x,y,z] (the tip), 'dir': [dx,dy,dz], 'color': '#rrggbb', 'length': m}]. Loads point INTO the part."""
        self._clear(self.arrow_actors)
        rgb = lambda h: tuple(int(h[i:i + 2], 16) / 255.0 for i in (1, 3, 5))  # noqa: E731
        for it in items:
            d = np.asarray(it["dir"], float)
            n = float(np.linalg.norm(d))
            if n < 1e-12:
                continue
            d = d / n
            L = float(it.get("length") or 0.05)
            tip = np.asarray(it["point"], float)
            start = tip - d * L
            axis = np.cross([1.0, 0.0, 0.0], d)
            ang = float(np.degrees(np.arccos(np.clip(d[0], -1, 1))))
            tr = vtkTransform()
            tr.Translate(*start)
            if np.linalg.norm(axis) > 1e-9:
                tr.RotateWXYZ(ang, *axis)
            elif d[0] < 0:
                tr.RotateWXYZ(180, 0, 0, 1)
            tr.Scale(L, L, L)
            src = vtkArrowSource()
            src.SetTipResolution(20)
            src.SetShaftResolution(20)
            src.SetShaftRadius(0.045)                  # (stout: it has to read at a glance against the model)
            src.SetTipRadius(0.11)
            src.SetTipLength(0.32)
            f = vtkTransformFilter()
            f.SetInputConnection(src.GetOutputPort())
            f.SetTransform(tr)
            f.Update()
            m = vtkPolyDataMapper()
            m.SetInputConnection(f.GetOutputPort())
            a = vtkActor()
            a.SetMapper(m)
            a.GetProperty().SetColor(*rgb(it.get("color") or "#f59e0b"))
            a.GetProperty().SetAmbient(0.6)
            a.GetProperty().SetDiffuse(0.5)
            a.PickableOff()
            a.SetVisibility(self._geo_shown)
            self.ren.AddActor(a)
            self.arrow_actors.append(a)
        self.render()

    def face_center(self, ids):
        """The middle of some CAD faces (mean of their vertices), where a label for them goes; None if none are known."""
        pts, _ = self.index.triangles_of(ids)
        return None if not len(pts) else [float(v) for v in pts.mean(axis=0)]

    def set_labels(self, items: list):
        """Text hung at points of the model: [{'point': [x,y,z], 'text': str, 'color': '#rrggbb' (optional)}]. Drawn on a panel-coloured
        plate with a frame so that it reads on any backdrop, in both themes."""
        self._label_items = [dict(i) for i in items]
        self._clear(self.label_actors)
        t = theme.tokens()
        rgb = lambda h: tuple(int(h[i:i + 2], 16) / 255.0 for i in (1, 3, 5))  # noqa: E731
        for it in items:
            a = vtkBillboardTextActor3D()
            a.SetInput(str(it["text"]))
            a.SetPosition(*[float(v) for v in it["point"]])
            below = bool(it.get("below"))           # (the lowest value hangs under its point, the highest above: they never cover each other)
            a.SetDisplayOffset(0, -16 if below else 16)
            a.PickableOff()
            tp = a.GetTextProperty()
            tp.SetFontSize(16)
            tp.SetBold(True)
            tp.SetColor(*rgb(t["ink"]))
            tp.SetBackgroundColor(*rgb(t["panel"]))
            tp.SetBackgroundOpacity(0.96)
            tp.SetFrame(True)
            tp.SetFrameColor(*rgb(it.get("color") or t["line_strong"]))
            tp.SetFrameWidth(3)
            tp.SetJustificationToCentered()
            tp.SetVerticalJustificationToTop() if below else tp.SetVerticalJustificationToBottom()
            a.SetVisibility(self._labels_visible())
            self.ren2.AddActor(a)
            self.label_actors.append(a)
        self._separate_labels(items)
        self.render()

    def _separate_labels(self, items: list):
        """Labels whose points are close on the screen are moved up (or down, for the ones that hang below) until no two cover each other;
        a label the user dragged keeps the place it was dragged to."""
        placed = []                                                   # (x, centre y, width) on the screen
        self._label_state = []
        for it, a in zip(items, self.label_actors):
            self.ren.SetWorldPoint(*[float(v) for v in it["point"]], 1.0)
            self.ren.WorldToDisplay()
            x, y, _ = self.ren.GetDisplayPoint()
            w, h = 11 * len(str(it["text"])) + 14, 32
            sign = -1 if it.get("below") else 1
            key = it.get("key") or str(it["text"])
            if key in self._label_user:
                ox, dy = self._label_user[key]
            else:
                ox, dy = 0, 16 * sign
                while any(abs(x - px) < (w + pw) / 2 and abs((y + dy + sign * h / 2) - py) < h for px, py, pw in placed) and abs(dy) < 400:
                    dy += 34 * sign
            a.SetDisplayOffset(int(ox), int(dy))
            placed.append((x + ox, y + dy + sign * h / 2, w))
            self._label_state.append({"actor": a, "point": [float(v) for v in it["point"]], "w": w, "h": h, "sign": sign, "key": key, "off": (int(ox), int(dy))})

    def _label_at(self, x, y):
        """Index of the label under the display position (x, y) (origin bottom-left), or None."""
        for i in range(len(self._label_state) - 1, -1, -1):
            st = self._label_state[i]
            if not st["actor"].GetVisibility():
                continue
            self.ren.SetWorldPoint(*st["point"], 1.0)
            self.ren.WorldToDisplay()
            px, py, _ = self.ren.GetDisplayPoint()
            cx, cy = px + st["off"][0], py + st["off"][1] + st["sign"] * st["h"] / 2
            if abs(x - cx) <= st["w"] / 2 and abs(y - cy) <= st["h"] / 2:
                return i
        return None

    def move_label(self, index: int, dx: int, dy: int):
        """Put label `index` at pixel offset (dx, dy) from its point (what dragging it does); the place is kept for redraws of this result."""
        st = self._label_state[index]
        st["off"] = (int(dx), int(dy))
        st["actor"].SetDisplayOffset(int(dx), int(dy))
        self._label_user[st["key"]] = (int(dx), int(dy))
        self.render()

    def reset_labels(self):
        """Labels back to the places the program chose."""
        self._label_user.clear()
        self._separate_labels(self._label_items)
        self.render()

    def pick_ray(self, p0, p1):
        """What the segment p0 -> p1 (world coordinates) hits: {'kind': 'face', 'face', 'point', 'air'} for a CAD face (while the CAD
        model is shown), {'kind': 'surface', 'cell', 'point'} for the drawn result, or None."""
        if self._geo_shown:
            h = self.index.pick(p0, p1, air_pickable=self.air_on)
            if h is not None:
                return {"kind": "face", **h}
        if self._overlay_index is not None and self.overlay_actors and self.overlay_actors[0].GetVisibility():
            h = self._overlay_index.hit(p0, p1)
            if h is not None:
                return {"kind": "surface", "cell": h[0], "point": h[1]}
        return None

    def screen_ray(self, x: float, y: float):
        """The world-space segment under the pixel (x, y) of the 3D view (origin top-left, as Qt reports it). Needs the window."""
        if self.rw is None:
            raise ValueError("The 3D view has no OpenGL window here (SIMWB_NO_GL): give a ray with pick_ray")
        h = self.iren.height()
        pts = []
        for z in (0.0, 1.0):
            self.ren.SetDisplayPoint(float(x), float(h - 1 - y), z)
            self.ren.DisplayToWorld()
            w = self.ren.GetWorldPoint()
            pts.append([w[0] / w[3], w[1] / w[3], w[2] / w[3]] if w[3] else list(w[:3]))
        if self._pick_box is not None:            # (not only to the far clipping plane: the walls of a big air volume are farther than that)
            return long_segment(pts[0], pts[1], *self._pick_box)
        return pts[0], pts[1]

    def world_to_screen(self, point):
        """Where a world point is on the 3D view's pixels (origin top-left); for clicking on it from the control API."""
        if self.rw is None:
            raise ValueError("The 3D view has no OpenGL window here (SIMWB_NO_GL)")
        self.ren.SetWorldPoint(float(point[0]), float(point[1]), float(point[2]), 1.0)
        self.ren.WorldToDisplay()
        d = self.ren.GetDisplayPoint()
        return [float(d[0]), float(self.iren.height() - 1 - d[1])]

    def click_face(self, fid: int, add: bool = False):
        """What a click on a face does: tell the panels (facePicked) and, in face mode, change the selection (Ctrl/Shift adds or removes
        one face; a plain click selects just that face; a click on nothing clears it)."""
        if self.pick_mode == "face":
            sel = list(self.selection)
            if fid < 0:
                sel = [] if not add else sel
            elif add:
                sel = [i for i in sel if i != fid] if fid in sel else sel + [int(fid)]
            else:
                sel = [int(fid)]
            self.set_selection(sel)
            self.selectionChanged.emit(list(self.selection))
        if fid >= 0:                               # (after the selection changed: a panel may then show another one, as the align step does)
            self.facePicked.emit(int(fid), bool(add))

    def click_at(self, x: float, y: float, add: bool = False):
        """A click at pixel (x, y) of the 3D view (as the mouse would): what is hit, or None."""
        h = self.pick_ray(*self.screen_ray(x, y))
        if h is None:
            self.click_face(-1, add)
            return None
        self.last_hit = h                          # (before the click is announced: the panel reads the point on the face from it)
        if h["kind"] == "face":
            self.click_face(h["face"], add)
        if self.pick_mode == "point":
            self.pointPicked.emit(*h["point"])
        return h

    def hover_at(self, x: float, y: float):
        h = self.pick_ray(*self.screen_ray(x, y))
        fid = h["face"] if h and h["kind"] == "face" else -1
        self._set_hover(fid)
        return h

    def _set_hover(self, fid: int):
        if fid != self.hover_id:
            self.hover_id = fid
            if self.hover_highlight:
                self._rebuild_marks()
                self.render()
            self.faceHovered.emit(int(fid))

    def _to_view(self, q):
        """A mouse position as VTK reports it (device pixels, origin bottom-left) -> the window pixels of click_at / hover_at (origin top-left,
        as Qt and the control API count). They differ by the display scaling: on a 200 % screen a real click used to be taken for a place
        twice as far out (and mirrored with the wrong height), so the face under the mouse was never found."""
        dpr = float(self.iren.devicePixelRatioF() or 1.0)
        h_dev = float(self.rw.GetSize()[1]) if self.rw is not None else self.iren.height() * dpr
        return q[0] / dpr, (h_dev - 1.0 - q[1]) / dpr

    # (the mouse: a press and release that hardly moved is a click, anything else is the camera being turned)
    def _on_press(self, obj, event):
        self._press = tuple(self.iren.GetEventPosition())
        if self.gizmo.press(self._press[0], self.gizmo._size()[1] - 1 - self._press[1]):    # a ring is taken: it turns the model, the camera stays
            self._gizmo_drag = True
            self.iren.SetInteractorStyle(None)
            return
        i = self._label_at(*self._press) if self._label_state and self._style is not None else None
        if i is not None:                                   # a label is taken: it moves with the mouse and the camera stays where it is
            self._drag = (i, self._press, self._label_state[i]["off"], self._style)
            self.iren.SetInteractorStyle(None)

    def _on_release(self, obj, event):
        p, self._press = self._press, None
        if self._gizmo_drag:
            self._gizmo_drag = False
            q = self.iren.GetEventPosition()
            self.gizmo.release(q[0], self.gizmo._size()[1] - 1 - q[1], bool(self.iren.GetShiftKey()))
            if self._style is not None:
                self.iren.SetInteractorStyle(self._style)
            return
        if self._drag is not None:
            if self._drag[3] is not None:
                self.iren.SetInteractorStyle(self._drag[3])
            self._drag = None
            return
        if p is None:
            return
        q = self.iren.GetEventPosition()
        if abs(q[0] - p[0]) + abs(q[1] - p[1]) > 4:
            return
        add = bool(self.iren.GetControlKey() or self.iren.GetShiftKey())
        self.click_at(*self._to_view(q), add)

    def _on_move(self, obj, event):
        q = self.iren.GetEventPosition()
        if self._gizmo_drag:
            self.gizmo.drag_to(q[0], self.gizmo._size()[1] - 1 - q[1], bool(self.iren.GetShiftKey()))
            return
        if self._drag is not None:
            i, p0, off0, _ = self._drag
            self.move_label(i, off0[0] + q[0] - p0[0], off0[1] + q[1] - p0[1])
            return
        if self._press is not None:
            return
        now = time.monotonic()
        if now - self._hover_t < 0.05:
            return
        self._hover_t = now
        if self.gizmo.on and self.gizmo.hover_at(q[0], self.gizmo._size()[1] - 1 - q[1]) is not None:
            return                                          # (over a ring: the cursor is a hand, no face is hovered)
        over = bool(self._label_state) and self._label_at(*q) is not None        # (the cursor says that a label can be dragged)
        self.iren.setCursor(Qt.SizeAllCursor) if over else self.iren.unsetCursor()
        self.hover_at(*self._to_view(q))

    def picture(self, path: str):
        """The 3D view with its colour legend as a PNG."""
        img = self.image()
        if self.legend.height() > 0 and self.legend.isVisible():
            leg = self.legend.grab().toImage().scaledToWidth(img.width()) if self.legend.width() else None
            if leg is not None:
                out = QImage(img.width(), img.height() + leg.height(), QImage.Format_RGB888)
                from PySide6.QtGui import QPainter
                p = QPainter(out)
                p.drawImage(0, 0, img)
                p.drawImage(0, img.height(), leg)
                p.end()
                img = out
        img.save(path, "PNG")

    def show_geometry(self, on: bool):
        self._geo_shown = bool(on)
        self._apply_visibility()
        for a in self.mark_actors + self.arrow_actors:
            a.SetVisibility(bool(on))
        for a in self.label_actors:
            a.SetVisibility(self._labels_visible())
        for a in self.air_actors:
            a.SetVisibility(bool(on) and self.air_on)
        self.render()

    def set_air_visible(self, on: bool):
        """Show or hide the air box (it never hides the part: it is translucent)."""
        self.air_on = bool(on)
        for a in self.air_actors:
            a.SetVisibility(self._geo_shown and self.air_on)
        if not self.air_on and self.hover_id in {p[0] for p in self._air_parts}:    # (a wall that was under the pointer must not stay lit once the box is hidden:
            self._set_hover(-1)                                                       #  its highlight is as big as the air volume and covers the model)
        self.geometry_info["air_visible"] = self.air_on and bool(self.air_actors)
        self.render()

    # ------------------------------------------------------------------ the CAD model
    def set_geometry(self, faces: list, air_kinds=(), air_visible: bool = False):
        """The CAD faces (each {'id', 'kind', 'mesh': {'vertices', 'triangles'}, 'volumes': [...]}) as one surface per body. Faces of
        the kinds in air_kinds (the far-field planes of a flow study, the enclosure of a cooling study) are the air box: one
        translucent surface with its edges, visible only when air_visible (see set_air_visible)."""
        self._clear(self.geo_actors)
        self._clear(self.air_actors)
        self.air_on = bool(air_visible)
        air = [f for f in faces if f.get("kind") in air_kinds]
        faces = [f for f in faces if f.get("kind") not in air_kinds]
        self.geometry_info = {"faces": len(faces) + len(air), "bodies": 0, "air_faces": len(air), "air_visible": False}
        self._clear(self.edge_actors)
        self._clear(self.mark_actors)

        def parts(fs):
            return [(f["id"], (f.get("mesh") or {}).get("vertices") or [], (f.get("mesh") or {}).get("triangles") or [], f.get("kind"))
                    for f in fs if (f.get("mesh") or {}).get("vertices")]
        self._solid_parts, self._air_parts = parts(faces), parts(air)
        self._face_volume = {f["id"]: (f.get("volumes") or [0])[0] for f in faces}
        self.geo_keys = []
        self._rebuild_index()
        if air:
            self._air_box(air)
            self.geometry_info["air_visible"] = self.air_on
        groups: dict = {}
        for f in faces:
            m = f.get("mesh") or {}
            if not m.get("vertices"):
                continue
            key = (f.get("volumes") or [0])[0]
            groups.setdefault(key, []).append((np.asarray(m["vertices"], float).reshape(-1, 3), np.asarray(m["triangles"], np.int64).reshape(-1, 3)))
        self.geometry_info["bodies"] = len(groups)
        for i, (key, parts) in enumerate(sorted(groups.items())):
            pts, tris, off = [], [], 0
            for v, t in parts:
                pts.append(v)
                tris.append(t + off)
                off += len(v)
            pd = self._polydata(np.vstack(pts), np.vstack(tris))
            nm = vtkPolyDataNormals()
            nm.SetInputData(pd)
            nm.SetFeatureAngle(35)
            nm.SplittingOn()
            nm.Update()
            act = self._actor(nm.GetOutput(), color=_rgb(BODY_COLORS[i % len(BODY_COLORS)]))
            self.ren.AddActor(act)
            self.geo_actors.append(act)
            self.geo_keys.append(key)
            fe = vtkFeatureEdges()          # the sharp edges and the outline of every face, thin and dark
            fe.SetInputData(pd)
            fe.BoundaryEdgesOn()
            fe.FeatureEdgesOn()
            fe.SetFeatureAngle(30)
            fe.ManifoldEdgesOff()
            fe.NonManifoldEdgesOn()
            fe.ColoringOff()
            fe.Update()
            em = vtkPolyDataMapper()
            em.SetInputConnection(fe.GetOutputPort())
            em.SetResolveCoincidentTopologyToPolygonOffset()
            em.SetRelativeCoincidentTopologyLineOffsetParameters(-1, -1)
            ea = vtkActor()
            ea.SetMapper(em)
            ea.GetProperty().SetColor(0.07, 0.08, 0.11)
            ea.GetProperty().SetLineWidth(1.3)
            ea.PickableOff()
            ea.SetVisibility(self.edges_on and self._geo_shown)
            self.ren.AddActor(ea)
            self.edge_actors.append(ea)
        self._rebuild_marks()
        self.gizmo.apply_preview()
        self.render()

    def _air_box(self, faces: list):
        pts, tris, off = [], [], 0
        for f in faces:
            m = f.get("mesh") or {}
            if not m.get("vertices"):
                continue
            v = np.asarray(m["vertices"], float).reshape(-1, 3)
            pts.append(v)
            tris.append(np.asarray(m["triangles"], np.int64).reshape(-1, 3) + off)
            off += len(v)
        if not pts:
            return
        pd = self._polydata(np.vstack(pts), np.vstack(tris))
        body = self._actor(pd, color=(0.55, 0.75, 1.0))
        pr = body.GetProperty()
        pr.SetOpacity(0.14)             # (see-through; depth peeling draws it in the right order from every side)
        pr.SetAmbient(0.9)
        pr.SetDiffuse(0.1)
        pr.SetSpecular(0.0)
        fe = vtkFeatureEdges()          # the outline of the box, so that it reads as a box
        fe.SetInputData(pd)
        fe.BoundaryEdgesOn()
        fe.FeatureEdgesOn()
        fe.SetFeatureAngle(30)
        fe.NonManifoldEdgesOff()
        fe.ManifoldEdgesOff()
        fe.ColoringOff()                # (else VTK colours the edges by type, red for the boundary ones)
        fe.Update()
        m = vtkPolyDataMapper()
        m.SetInputConnection(fe.GetOutputPort())
        edges = vtkActor()
        edges.SetMapper(m)
        edges.GetProperty().SetColor(0.75, 0.88, 1.0)
        edges.GetProperty().SetOpacity(0.7)
        edges.GetProperty().SetLineWidth(1.5)
        for a in (body, edges):
            a.PickableOff()
            a.SetVisibility(self.air_on and self._geo_shown)
            self.ren.AddActor(a)
            self.air_actors.append(a)

    def _add_contours(self, pd, per_face: bool, lo: float, hi: float, spec: dict, log: bool) -> int:
        """Lines of equal value on the drawn surface: spec['n'] evenly spaced levels between lo and hi (geometric on a log scale) and the extra
        spec['levels']. Returns how many levels were drawn."""
        n = max(int(spec.get("n", 10)), 1)
        even = np.geomspace(lo, hi, n + 2)[1:-1] if log and lo > 0 else np.linspace(lo, hi, n + 2)[1:-1]
        levels = sorted({float(v) for v in list(even) + [float(x) for x in (spec.get("levels") or []) if lo < float(x) < hi]})   # (an extra outside the range draws nothing)
        src = pd
        if per_face:                                                  # (the values sit on the faces: averaged to the corners for the lines)
            c2p = vtkCellDataToPointData()
            c2p.SetInputData(pd)
            c2p.Update()
            src = c2p.GetOutput()
        cf = vtkContourFilter()
        cf.SetInputData(src)
        cf.SetNumberOfContours(len(levels))
        for i, v in enumerate(levels):
            cf.SetValue(i, v)
        cf.ComputeScalarsOn()                                          # (each line point then carries its level: the labels read it)
        cf.Update()
        t = theme.tokens()
        # a light line under a dark one: the lines read on the dark end of a colour map as well as on the bright end
        for width, colour, opacity, offset in ((4.0, "#ffffff", 0.55, -1), (1.7, "#101521", 0.95, -2)):
            m = vtkPolyDataMapper()
            m.SetInputConnection(cf.GetOutputPort())
            m.ScalarVisibilityOff()
            m.SetResolveCoincidentTopologyToPolygonOffset()
            m.SetRelativeCoincidentTopologyLineOffsetParameters(offset, offset)
            a = vtkActor()
            a.SetMapper(m)
            a.GetProperty().SetColor(*_rgb(colour))
            a.GetProperty().SetLineWidth(width)
            a.GetProperty().SetLighting(False)
            a.GetProperty().SetOpacity(opacity)
            a.PickableOff()
            self.ren.AddActor(a)
            self.overlay_actors.append(a)
        self.overlay_info["contour_labels"] = self._label_contours(cf.GetOutput()) if spec.get("labels", True) else 0
        return len(levels)

    def _label_contours(self, lines):
        """The value of each contour line, written at the middle of its longest piece: a small plate on the line."""
        from vtkmodules.util.numpy_support import vtk_to_numpy
        st = vtkStripper()
        st.SetInputData(lines)
        st.JoinContiguousSegmentsOn()
        st.Update()
        out = st.GetOutput()
        sc = out.GetPointData().GetScalars()
        if out.GetNumberOfLines() == 0 or sc is None:
            return 0
        pts = vtk_to_numpy(out.GetPoints().GetData())
        vals = vtk_to_numpy(sc)
        conn = vtk_to_numpy(out.GetLines().GetConnectivityArray())
        offs = vtk_to_numpy(out.GetLines().GetOffsetsArray())
        best = {}                                                      # level -> (length, middle point)
        for i in range(len(offs) - 1):
            ids = conn[offs[i]:offs[i + 1]]
            if len(ids) < 2:
                continue
            q = pts[ids]
            length = float(np.linalg.norm(np.diff(q, axis=0), axis=1).sum())
            key = round(float(vals[ids[0]]), 9)
            if key not in best or length > best[key][0]:
                best[key] = (length, q[len(q) // 2])
        t = theme.tokens()
        for key, (_, pt) in best.items():
            a = vtkBillboardTextActor3D()
            a.SetInput(f"{key:.4g}")
            a.SetPosition(*[float(v) for v in pt])
            a.PickableOff()
            tp = a.GetTextProperty()
            tp.SetFontSize(14)
            tp.SetBold(True)
            tp.SetColor(*_rgb(t["ink"]))
            tp.SetBackgroundColor(*_rgb(t["panel"]))
            tp.SetBackgroundOpacity(0.85)
            tp.SetJustificationToCentered()
            tp.SetVerticalJustificationToCentered()
            self.ren.AddActor(a)
            self.overlay_actors.append(a)
        return len(best)

    def _polydata(self, pts: np.ndarray, tris: np.ndarray) -> vtkPolyData:
        pd = vtkPolyData()
        p = vtkPoints()
        p.SetData(numpy_to_vtk(np.ascontiguousarray(pts, np.float64), deep=True))
        pd.SetPoints(p)
        cells = np.hstack([np.full((len(tris), 1), 3, np.int64), tris.astype(np.int64)]).ravel()
        ca = vtkCellArray()
        ca.ImportLegacyFormat(numpy_to_vtkIdTypeArray(cells, deep=True))
        pd.SetPolys(ca)
        return pd

    def _actor(self, pd, color=None, mapper=None) -> vtkActor:
        m = mapper or vtkPolyDataMapper()
        m.SetInputData(pd)
        a = vtkActor()
        a.SetMapper(m)
        pr = a.GetProperty()
        pr.SetAmbient(0.55)      # (colours stay as they are in the colour bar; the light only models the shape)
        pr.SetDiffuse(0.55)
        pr.SetSpecular(0.08)
        pr.SetInterpolationToPhong()
        if color is not None:
            pr.SetColor(*color)
        pr.SetEdgeColor(0.07, 0.08, 0.11)
        return a

    # ------------------------------------------------------------------ a field on a surface
    def show_surface(self, p: dict, *, name: str = "", unit: str = "", cmap: str = "turbo", reverse: bool = False,
                     bands: int = 0, vrange=None, edges: bool = False, deform: float | None = None, fixed_quality: bool = False,
                     animate: bool = False, solid=None, contours=None, opacity: float = 1.0, outline: bool = False):
        """A surface payload {positions, indices, values, per, [categories, present, scale, disp, disp_kind]} coloured by
        its values. vrange: (lo, hi) or None for the payload's own; fixed_quality: the red-is-bad scale of cell quality. solid: (r, g, b) draws it
        in that colour instead (the object, when a flow tool shows its own field); no colour bar then. contours: {'on', 'n', 'levels'}: lines of
        equal value, n evenly spaced between the lowest and highest value and any extra levels. opacity: 0.1..1 (see-through surfaces);
        outline: with a deformation drawn, the thin outline of the part before it deformed."""
        self.clear_overlay()
        if name != self._shown_name:
            self._shown_name = name
            self._label_user.clear()
        if not p or not p.get("n_tris"):
            self.render()
            return {}
        pos = _b64(p["positions"], np.float32).reshape(-1, 3).astype(np.float64)
        idx = _b64(p["indices"], np.uint32).reshape(-1, 3).astype(np.int64)
        val = _b64(p["values"], np.float32).astype(np.float64)
        lo, hi = (vrange if vrange else (p["min"], p["max"]))
        if hi <= lo:
            hi = lo + 1.0
        base = pos.copy()
        disp = None
        info = {"range": (lo, hi)}
        if p.get("disp") and deform != 0:
            disp = _b64(p["disp"], np.float32).reshape(-1, 3).astype(np.float64)
            size = float(np.linalg.norm(pos.max(0) - pos.min(0))) or 1.0
            umax = float(np.linalg.norm(disp, axis=1).max()) or 1.0
            if p.get("disp_kind") == "static":
                auto = 0.08 * size / umax
                amp = auto if deform is None else deform
                info["deform"] = {"scale": amp, "auto": auto, "animated": bool(animate)}
                if animate:      # the load applied and released again, smoothly
                    self._anim = {"base": base, "disp": disp, "amp": amp, "t": 0.0, "kind": "static"}
                else:
                    pos = base + amp * disp
                disp = None
            else:       # a mode shape: swings by 8 % of the model size
                self._anim = {"base": base, "disp": disp, "amp": 0.08 * size, "t": 0.0}
        pd = self._polydata(pos, idx)
        arr = numpy_to_vtk(val, deep=True)
        arr.SetName(name or "value")
        cats = p.get("categories")
        lut = vtkLookupTable()
        if cats:
            lut.SetNumberOfTableValues(len(cats))
            for i, c in enumerate(cats):
                lut.SetTableValue(i, *_rgb(c["color"]), 1.0)
            lut.SetTableRange(0, len(cats) - 1)
            lut.Build()
        else:
            tab = colormaps.turbo(1 - np.linspace(0, 1, 256)) if fixed_quality else colormaps.table(cmap, 256, reverse, bands)
            lut.SetNumberOfTableValues(256)
            for i, c in enumerate(tab):
                lut.SetTableValue(i, *c, 1.0)
            if p.get("scale") == "log" and lo > 0:
                lut.SetScaleToLog10()
            lut.SetTableRange(lo, hi)
            lut.SetNanColor(0.6, 0.6, 0.6, 1.0)
            lut.Build()
        self._overlay_index = locator_for(pd)
        self._ov = {"pos": pos, "base": base, "idx": idx, "val": val, "per": p.get("per"), "unit": unit, "name": name}
        self.overlay_info = {"kind": "categories" if cats else "scalar", "name": name, "unit": unit, "range": [float(lo), float(hi)],
                             "triangles": int(len(idx)), "per": p.get("per"), "scale": p.get("scale") or "linear",
                             "colormap": None if cats else ("quality (fixed)" if fixed_quality else cmap), "reverse": bool(reverse),
                             "bands": int(bands), "edges": bool(edges), "deform": info.get("deform"),
                             "animated": bool(self._anim), "nan_values": int(np.isnan(val).sum()),
                             "categories": [c["name"] for c in (cats or [])]}
        if p.get("per") == "face":
            pd.GetCellData().SetScalars(arr)
        else:
            pd.GetPointData().SetScalars(arr)
        m = vtkPolyDataMapper()
        m.SetLookupTable(lut)
        m.SetScalarRange(*(0, len(cats) - 1) if cats else (lo, hi))
        m.UseLookupTableScalarRangeOn()
        m.SetColorModeToMapScalars()
        m.ScalarVisibilityOn()
        if p.get("per") == "face":
            m.SetScalarModeToUseCellData()
        else:
            m.SetScalarModeToUsePointData()
        if solid is not None:
            m.ScalarVisibilityOff()
        act = self._actor(pd, mapper=m, color=solid)
        act.GetProperty().SetEdgeVisibility(1 if edges else 0)
        act.GetProperty().SetLineWidth(1.0)
        op = min(max(float(opacity if opacity is not None else 1.0), 0.05), 1.0)
        act.GetProperty().SetOpacity(op)
        self.overlay_info["opacity"] = op
        self.overlay_info["outline"] = False
        if outline and (self._anim or info.get("deform")):          # the undeformed part as a thin outline (its sharp edges and border)
            fe = vtkFeatureEdges()
            fe.SetInputData(self._polydata(base, idx))
            fe.BoundaryEdgesOn()
            fe.FeatureEdgesOn()
            fe.SetFeatureAngle(35)
            fe.ManifoldEdgesOff()
            fe.NonManifoldEdgesOff()
            fe.ColoringOff()
            fe.Update()
            om = vtkPolyDataMapper()
            om.SetInputData(fe.GetOutput())
            oa = vtkActor()
            oa.SetMapper(om)
            oa.GetProperty().SetColor(*_rgb(theme.tokens()["vp_text"]))
            oa.GetProperty().SetOpacity(0.55)
            oa.GetProperty().SetLineWidth(1.0)
            oa.GetProperty().SetLighting(False)
            oa.PickableOff()
            self.ren.AddActor(oa)
            self.overlay_actors.append(oa)
            self.overlay_info["outline"] = True
            self.overlay_info["outline_segments"] = int(fe.GetOutput().GetNumberOfCells())
        # (flat per-face shading on the CAD facets, so the shape reads as a model)
        act.GetProperty().SetInterpolationToFlat()
        self.ren.AddActor(act)
        self.overlay_actors.append(act)
        self._anim_poly = pd
        self.overlay_info["contours"] = 0
        if contours and contours.get("on") and solid is None and not cats:
            self.overlay_info["contours"] = self._add_contours(pd, p.get("per") == "face", lo, hi, contours, p.get("scale") == "log")
        if solid is not None:
            self.legend.clear()
        elif cats:
            used = [cats[i] for i in (p.get("present") or range(len(cats))) if 0 <= i < len(cats)]
            self.legend.set_categories(name or "Region", used)
        else:
            tab = colormaps.turbo(1 - np.linspace(0, 1, 256)) if fixed_quality else colormaps.table(cmap, 256, reverse, bands)
            self.legend.set_scalar(name, unit, lo, hi, tab, log=p.get("scale") == "log")
        if self._anim:
            self._timer.start(30)
        self.render()
        return info

    def _scalar_bar(self, lut, name, unit, log=False):
        bar = vtkScalarBarActor()
        bar.SetLookupTable(lut)
        bar.SetTitle(f"{name} [{unit}]" if unit and unit != "-" else name)
        bar.SetNumberOfLabels(6)
        bar.SetLabelFormat("%-#6.3g")
        bar.SetOrientationToHorizontal()
        bar.SetWidth(0.46)
        bar.SetHeight(0.085)
        bar.SetPosition(0.27, 0.02)
        bar.SetBarRatio(0.4)
        for tp in (bar.GetTitleTextProperty(), bar.GetLabelTextProperty()):
            tp.SetColor(1, 1, 1)
            tp.ShadowOn()
        bar.GetTitleTextProperty().SetFontSize(15)
        bar.GetLabelTextProperty().SetFontSize(13)
        self.ren.AddViewProp(bar)
        self.bar = bar

    def _category_legend(self, p, cats):
        from vtkmodules.vtkRenderingAnnotation import vtkLegendBoxActor
        used = [cats[i] for i in (p.get("present") or range(len(cats))) if 0 <= i < len(cats)]
        leg = vtkLegendBoxActor()
        leg.SetNumberOfEntries(len(used))
        src = vtkCubeSource()
        src.Update()
        for i, c in enumerate(used):
            leg.SetEntry(i, src.GetOutput(), c["name"], _rgb(c["color"]))
        leg.GetPositionCoordinate().SetCoordinateSystemToNormalizedViewport()
        leg.GetPositionCoordinate().SetValue(0.01, 0.84 - 0.03 * len(used))
        leg.GetPosition2Coordinate().SetCoordinateSystemToNormalizedViewport()
        leg.GetPosition2Coordinate().SetValue(0.2, 0.03 * len(used) + 0.03)
        leg.UseBackgroundOn()
        leg.SetBackgroundColor(0.12, 0.13, 0.16)
        leg.SetBackgroundOpacity(0.8)
        leg.GetEntryTextProperty().SetColor(1, 1, 1)
        leg.BorderOff()
        self.ren.AddViewProp(leg)
        self.bar = leg

    def _tick(self):
        a = self._anim
        if not a:
            return
        a["t"] += 0.03
        if a.get("kind") == "static":
            k = 0.5 * (1 - np.cos(a["t"] * 2 * np.pi * 0.5))        # 0 -> 1 -> 0 over two seconds
        else:
            k = np.sin(a["t"] * 2 * np.pi * 0.8)
        pts = a["base"] + k * a["amp"] * a["disp"]
        self._anim_poly.GetPoints().SetData(numpy_to_vtk(np.ascontiguousarray(pts), deep=True))
        self._anim_poly.GetPoints().Modified()
        self.render()

    # ------------------------------------------------------------------ camera, pictures
    def fit(self, direction=None):
        cam = self.ren.GetActiveCamera()
        if direction is not None:
            d = np.asarray(direction, float)
            d = d / (np.linalg.norm(d) or 1.0)
            fp = np.array(cam.GetFocalPoint())
            cam.SetPosition(*(fp + d))
            cam.SetViewUp(0, 1, 0) if abs(d[2]) > 0.99 else cam.SetViewUp(0, 0, 1)
        self._reset_camera()
        self._autofit = True
        self.render()
        QTimer.singleShot(60, self._refit)      # (once the layout has settled: a view that has just been shown still has its old render size)

    def set_focus(self, box):
        """The region the camera fits to: box = [x0, y0, z0, x1, y1, z1] in world coordinates, or None for everything drawn."""
        self._focus = None if box is None else (box[0], box[3], box[1], box[4], box[2], box[5])

    def _reset_camera(self):
        if not self._focus:
            self.ren.ResetCamera()
            return
        self.ren.ResetCamera(*self._focus)
        self._fit_focus()

    def _fit_focus(self):
        """ResetCamera fits a sphere round the focus box, which is too far away when the box is thin along the viewing direction (a cut plane
        seen face-on): move the camera along its line of sight until the box's own outline, as the camera sees it, fills the picture."""
        cam = self.ren.GetActiveCamera()
        x0, x1, y0, y1, z0, z1 = self._focus
        c = np.array([(x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2])
        d = np.array(cam.GetPosition()) - np.array(cam.GetFocalPoint())
        if not np.linalg.norm(d):
            return
        d = d / np.linalg.norm(d)
        up = np.array(cam.GetViewUp())
        right = np.cross(up, d)
        if not np.linalg.norm(right):
            return
        right = right / np.linalg.norm(right)
        up = np.cross(d, right)
        corners = np.array([[x, y, z] for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]) - c
        w, h = np.abs(corners @ right).max(), np.abs(corners @ up).max()
        t = np.tan(np.radians(cam.GetViewAngle()) / 2)
        asp = max(self.ren.GetAspect()[0], 1e-6)
        dist = max(h / t, w / (t * asp)) * 1.1
        cam.SetFocalPoint(*c)
        cam.SetPosition(*(c + d * dist))
        self.ren.ResetCameraClippingRange()

    def set_view(self, name: str):
        self.fit({"iso": (1, -1.4, 0.9), "+x": (1, 0, 0), "-x": (-1, 0, 0), "+y": (0, 1, 0), "-y": (0, -1, 0),
                  "+z": (0, 0, 1), "-z": (0, 0, -1)}.get(name, (1, -1.4, 0.9)))

    def camera(self) -> dict:
        c = self.ren.GetActiveCamera()
        return {"position": list(c.GetPosition()), "focal_point": list(c.GetFocalPoint()), "view_up": list(c.GetViewUp()),
                "view_angle": c.GetViewAngle(), "parallel": bool(c.GetParallelProjection()), "distance": c.GetDistance()}

    def _user_moved(self):
        self._autofit = False

    def resizeEvent(self, e):  # noqa: N802
        """A view fitted before it had its final size (a study opened from the start screen) is fitted again to the size it got."""
        super().resizeEvent(e)
        QTimer.singleShot(0, self._place_overlays)
        if self._autofit:
            QTimer.singleShot(0, self._refit)

    def _place_overlays(self):
        for w in self.overlays:                                   # (cards laid over the picture, as the align guide)
            if w.isVisible():
                w.place()

    def _refit(self):
        if self._autofit:
            self._reset_camera()
            self.render()

    def set_camera(self, position=None, focal_point=None, view_up=None, view_angle=None, parallel=None):
        self._autofit = False
        c = self.ren.GetActiveCamera()
        if position is not None:
            c.SetPosition(*[float(v) for v in position])
        if focal_point is not None:
            c.SetFocalPoint(*[float(v) for v in focal_point])
        if view_up is not None:
            c.SetViewUp(*[float(v) for v in view_up])
        if view_angle is not None:
            c.SetViewAngle(float(view_angle))
        if parallel is not None:
            c.SetParallelProjection(bool(parallel))
        self.ren.ResetCameraClippingRange()
        self.render()

    def stats(self) -> dict:
        """What is in the scene and, when there is a picture, whether it is blank: the numbers a person reads off the screen."""
        b = self.ren.ComputeVisiblePropBounds()
        out = {"geometry": dict(self.geometry_info), "geometry_visible": any(a.GetVisibility() for a in self.geo_actors),
               "overlay": dict(self.overlay_info), "bounds": None if b[0] > b[1] else [float(v) for v in b], "gl": self.rw is not None}
        if self.rw is not None:
            im = self.image()
            a = np.frombuffer(im.constBits(), np.uint8).reshape(im.height(), im.bytesPerLine())[:, : im.width() * 3].reshape(im.height(), im.width(), 3)
            q = (a // 16).reshape(-1, 3)
            out["picture"] = {"size": [im.width(), im.height()], "mean_rgb": [round(float(v), 1) for v in a.reshape(-1, 3).mean(0)],
                              "distinct_colours": int(len({tuple(r) for r in q[:: max(1, len(q) // 20000)]}))}
        return out

    def image(self) -> QImage:
        """The 3D view as a picture (the GL surface is not part of a widget grab)."""
        if self.rw is None:
            im = QImage(max(self.iren.width(), 64), max(self.iren.height(), 64), QImage.Format_RGB888)
            im.fill(0x20252d)
            return im
        self.render()
        f = vtkWindowToImageFilter()
        f.SetInput(self.rw)
        f.SetInputBufferTypeToRGB()
        f.ReadFrontBufferOff()
        f.Update()
        im = f.GetOutput()
        w, h, _ = im.GetDimensions()
        a = np.frombuffer(memoryview(numpy_from_vtk(im.GetPointData().GetScalars())), np.uint8).reshape(h, w, 3)
        a = np.ascontiguousarray(a[::-1])
        return QImage(a.data, w, h, 3 * w, QImage.Format_RGB888).copy()

    def save_png(self, path: str):
        self.image().save(path, "PNG")

    def closeEvent(self, e):  # noqa: N802
        self._timer.stop()
        if self.rw is not None:
            self.rw.Finalize()
            self.iren.close()
        super().closeEvent(e)


def numpy_from_vtk(a):
    from vtkmodules.util.numpy_support import vtk_to_numpy
    return vtk_to_numpy(a)
