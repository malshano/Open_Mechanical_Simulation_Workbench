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
from vtkmodules.vtkFiltersCore import vtkFeatureEdges, vtkPolyDataNormals
from vtkmodules.vtkFiltersSources import vtkCubeSource
from vtkmodules.vtkIOImage import vtkPNGWriter
from vtkmodules.vtkInteractionStyle import vtkInteractorStyleTrackballCamera
from vtkmodules.vtkInteractionWidgets import vtkOrientationMarkerWidget
from vtkmodules.vtkRenderingAnnotation import vtkAxesActor, vtkScalarBarActor
from vtkmodules.vtkRenderingCore import (vtkActor, vtkPolyDataMapper, vtkRenderer, vtkWindowToImageFilter)
from vtkmodules.vtkRenderingFreeType import vtkVectorText  # noqa: F401  (registers the text renderer)
import vtkmodules.vtkRenderingOpenGL2  # noqa: F401  (the OpenGL back end)
import vtkmodules.vtkRenderingFreeType  # noqa: F401

from . import colormaps, theme
from .legend import Legend
from .picking import FaceIndex, locator_for
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
        if self.rw is not None:
            self.rw.AddRenderer(self.ren)
            self.iren.SetInteractorStyle(vtkInteractorStyleTrackballCamera())
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
        self.edges_on = True
        self.hover_id = -1
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
            self.ren.RemoveActor(a)
            self.ren.RemoveViewProp(a)
        actors.clear()

    def clear_overlay(self):
        self._timer.stop()
        self._anim = None
        self._clear(self.overlay_actors)
        self._overlay_index = None
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

    def set_edges_visible(self, on: bool):
        self.edges_on = bool(on)
        for a in self.edge_actors:
            a.SetVisibility(self.edges_on and self._geo_shown)
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
        items = [(ids, col, 0.7) for col, ids in by_colour.items()]
        items.append((self.selection, "#ff9f1c", 0.9))
        if self.hover_highlight and self.hover_id >= 0 and self.hover_id not in self.selection:
            items.append(([self.hover_id], "#ffd8a8", 0.8))
        for ids, col, op in items:
            a = self._mark_actor(ids, col, op)
            if a is not None:
                self.ren.AddActor(a)
                self.mark_actors.append(a)

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
        if fid >= 0:
            self.facePicked.emit(int(fid), bool(add))
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

    def click_at(self, x: float, y: float, add: bool = False):
        """A click at pixel (x, y) of the 3D view (as the mouse would): what is hit, or None."""
        h = self.pick_ray(*self.screen_ray(x, y))
        if h is None:
            self.click_face(-1, add)
            return None
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

    # (the mouse: a press and release that hardly moved is a click, anything else is the camera being turned)
    def _on_press(self, obj, event):
        self._press = tuple(self.iren.GetEventPosition())

    def _on_release(self, obj, event):
        p, self._press = self._press, None
        if p is None:
            return
        q = self.iren.GetEventPosition()
        if abs(q[0] - p[0]) + abs(q[1] - p[1]) > 4:
            return
        add = bool(self.iren.GetControlKey() or self.iren.GetShiftKey())
        self.click_at(q[0], self.iren.height() - 1 - q[1], add)

    def _on_move(self, obj, event):
        if self._press is not None:
            return
        now = time.monotonic()
        if now - self._hover_t < 0.05:
            return
        self._hover_t = now
        q = self.iren.GetEventPosition()
        self.hover_at(q[0], self.iren.height() - 1 - q[1])

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
        for a in self.geo_actors + self.edge_actors:
            a.SetVisibility(bool(on) and (a not in self.edge_actors or self.edges_on))
        for a in self.mark_actors:
            a.SetVisibility(bool(on))
        for a in self.air_actors:
            a.SetVisibility(bool(on) and self.air_on)
        self.render()

    def set_air_visible(self, on: bool):
        """Show or hide the air box (it never hides the part: it is translucent)."""
        self.air_on = bool(on)
        for a in self.air_actors:
            a.SetVisibility(self._geo_shown and self.air_on)
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
        self.index.build(parts(faces), parts(air))
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
                     bands: int = 0, vrange=None, edges: bool = False, deform: float | None = None, fixed_quality: bool = False):
        """A surface payload {positions, indices, values, per, [categories, present, scale, disp, disp_kind]} coloured by
        its values. vrange: (lo, hi) or None for the payload's own; fixed_quality: the red-is-bad scale of cell quality."""
        self.clear_overlay()
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
                pos = base + amp * disp
                info["deform"] = {"scale": amp, "auto": auto}
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
        act = self._actor(pd, mapper=m)
        act.GetProperty().SetEdgeVisibility(1 if edges else 0)
        act.GetProperty().SetLineWidth(1.0)
        # (flat per-face shading on the CAD facets, so the shape reads as a model)
        act.GetProperty().SetInterpolationToFlat()
        self.ren.AddActor(act)
        self.overlay_actors.append(act)
        self._anim_poly = pd
        if cats:
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
        pts = a["base"] + np.sin(a["t"] * 2 * np.pi * 0.8) * a["amp"] * a["disp"]
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
        self.ren.ResetCamera()
        self.render()

    def set_view(self, name: str):
        self.fit({"iso": (1, -1.4, 0.9), "+x": (1, 0, 0), "-x": (-1, 0, 0), "+y": (0, 1, 0), "-y": (0, -1, 0),
                  "+z": (0, 0, 1), "-z": (0, 0, -1)}.get(name, (1, -1.4, 0.9)))

    def camera(self) -> dict:
        c = self.ren.GetActiveCamera()
        return {"position": list(c.GetPosition()), "focal_point": list(c.GetFocalPoint()), "view_up": list(c.GetViewUp()),
                "view_angle": c.GetViewAngle(), "parallel": bool(c.GetParallelProjection()), "distance": c.GetDistance()}

    def set_camera(self, position=None, focal_point=None, view_up=None, view_angle=None, parallel=None):
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
