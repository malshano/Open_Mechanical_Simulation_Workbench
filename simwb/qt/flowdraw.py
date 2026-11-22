"""The flow tools of the results step, drawn in the 3D view on top of the object: vector arrows, particle paths and volume rendering
(the vortex surfaces are an ordinary surface payload). Each takes the payload the engine made (base64 float arrays, the same ones
the web version draws) and adds its actors to the view's overlay, so that they go when the overlay is cleared."""
from __future__ import annotations

import base64

import numpy as np
from vtkmodules.util.numpy_support import numpy_to_vtk, numpy_to_vtkIdTypeArray
from vtkmodules.vtkCommonCore import vtkLookupTable, vtkPoints
from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkImageData, vtkPiecewiseFunction, vtkPolyData
from vtkmodules.vtkFiltersCore import vtkGlyph3D
from vtkmodules.vtkFiltersSources import vtkArrowSource
from vtkmodules.vtkRenderingCore import vtkActor, vtkColorTransferFunction, vtkPolyDataMapper, vtkVolume, vtkVolumeProperty
from vtkmodules.vtkRenderingVolumeOpenGL2 import vtkSmartVolumeMapper

from . import colormaps, theme


def _arr(s: str, dtype) -> np.ndarray:
    return np.frombuffer(base64.b64decode(s), dtype)


def _lut(tab, lo: float, hi: float) -> vtkLookupTable:
    lut = vtkLookupTable()
    lut.SetNumberOfTableValues(len(tab))
    for i, c in enumerate(tab):
        lut.SetTableValue(i, *c[:3], 1.0)
    lut.SetTableRange(lo, hi if hi > lo else lo + 1.0)
    lut.Build()
    return lut


def add_plane(view, p: dict, tab, lo: float, hi: float, opacity: float = 1.0, contours=None) -> int:
    """A contour plane: the field on a cut through the flow (a resample.plane_payload), coloured over lo..hi, with the contour lines of the Display
    tab when they are on. Returns the number of contour levels drawn."""
    from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper

    pts = _arr(p["positions"], np.float32).reshape(-1, 3).astype(np.float64)
    idx = _arr(p["indices"], np.uint32).reshape(-1, 3).astype(np.int64)
    val = _arr(p["values"], np.float32).astype(np.float64)
    if not len(idx):
        return 0
    pd = view._polydata(pts, idx)
    arr = numpy_to_vtk(np.ascontiguousarray(val), deep=True)
    arr.SetName("value")
    per_face = p.get("per") == "face"
    (pd.GetCellData() if per_face else pd.GetPointData()).SetScalars(arr)
    m = vtkPolyDataMapper()
    m.SetInputData(pd)
    m.SetLookupTable(_lut(tab, lo, hi))
    m.UseLookupTableScalarRangeOn()
    m.SetScalarRange(lo, hi)
    m.SetColorModeToMapScalars()
    m.SetScalarModeToUseCellData() if per_face else m.SetScalarModeToUsePointData()
    a = vtkActor()
    a.SetMapper(m)
    pr = a.GetProperty()
    pr.SetAmbient(0.7)
    pr.SetDiffuse(0.35)
    pr.SetOpacity(float(opacity))
    pr.BackfaceCullingOff()
    a.PickableOff()
    view.ren.AddActor(a)
    view.overlay_actors.append(a)
    if contours and contours.get("on"):
        return view._add_contours(pd, per_face, lo, hi, contours, p.get("scale") == "log")
    return 0


def add_arrows(view, p: dict, tab, lo: float, hi: float, length: float):
    """Arrows at p['positions'] along p['vectors'], coloured by their length (the speed); an arrow is `length` m per unit of speed."""
    pts = _arr(p["positions"], np.float32).reshape(-1, 3).astype(np.float64)
    vec = _arr(p["vectors"], np.float32).reshape(-1, 3).astype(np.float64)
    if not len(pts):
        return None
    pd = vtkPolyData()
    vp = vtkPoints()
    vp.SetData(numpy_to_vtk(np.ascontiguousarray(pts), deep=True))
    pd.SetPoints(vp)
    v = numpy_to_vtk(np.ascontiguousarray(vec), deep=True)
    v.SetName("flow")
    pd.GetPointData().SetVectors(v)
    s = numpy_to_vtk(np.linalg.norm(vec, axis=1), deep=True)
    s.SetName("speed")
    pd.GetPointData().SetScalars(s)
    src = vtkArrowSource()
    src.SetTipResolution(14)
    src.SetShaftResolution(14)
    src.SetShaftRadius(0.05)
    src.SetTipRadius(0.14)
    src.SetTipLength(0.34)
    g = vtkGlyph3D()
    g.SetSourceConnection(src.GetOutputPort())
    g.SetInputData(pd)
    g.SetVectorModeToUseVector()
    g.SetScaleModeToScaleByVector()
    g.SetScaleFactor(float(length))
    g.SetColorModeToColorByScalar()
    g.OrientOn()
    g.Update()
    m = vtkPolyDataMapper()
    m.SetInputConnection(g.GetOutputPort())
    m.SetLookupTable(_lut(tab, lo, hi))
    m.UseLookupTableScalarRangeOn()
    m.SetScalarRange(lo, hi)
    m.SetColorModeToMapScalars()
    m.SetScalarModeToUsePointData()
    a = vtkActor()
    a.SetMapper(m)
    a.GetProperty().SetAmbient(0.55)
    a.GetProperty().SetDiffuse(0.5)
    a.PickableOff()
    view.ren.AddActor(a)
    view.overlay_actors.append(a)
    return a


def add_paths(view, p: dict, tab, lo: float, hi: float, width: float = 2.2):
    """Particle paths: p['paths'] (n, steps, 3) float32 with NaN after a particle left the box or hit a body, p['speed'] (n, steps)."""
    n, steps = int(p["n"]), int(p["steps"])
    P = _arr(p["paths"], np.float32).reshape(n, steps, 3)
    S = _arr(p["speed"], np.float32).reshape(n, steps)
    ok = np.isfinite(P[:, :, 0])
    cnt = ok.sum(1)
    keep = cnt >= 2
    if not keep.any():
        return None
    use = ok & keep[:, None]
    pts = P[use].astype(np.float64)
    sp = S[use].astype(np.float64)
    counts = cnt[keep]
    offsets = np.concatenate([[0], np.cumsum(counts)]).astype(np.int64)
    pd = vtkPolyData()
    vp = vtkPoints()
    vp.SetData(numpy_to_vtk(np.ascontiguousarray(pts), deep=True))
    pd.SetPoints(vp)
    cells = vtkCellArray()
    cells.SetData(numpy_to_vtkIdTypeArray(offsets, deep=True), numpy_to_vtkIdTypeArray(np.arange(len(pts), dtype=np.int64), deep=True))
    pd.SetLines(cells)
    s = numpy_to_vtk(sp, deep=True)
    s.SetName("speed")
    pd.GetPointData().SetScalars(s)
    m = vtkPolyDataMapper()
    m.SetInputData(pd)
    m.SetLookupTable(_lut(tab, lo, hi))
    m.UseLookupTableScalarRangeOn()
    m.SetScalarRange(lo, hi)
    m.SetColorModeToMapScalars()
    m.SetScalarModeToUsePointData()
    a = vtkActor()
    a.SetMapper(m)
    a.GetProperty().SetLineWidth(float(width))
    a.GetProperty().SetLighting(False)
    a.PickableOff()
    view.ren.AddActor(a)
    view.overlay_actors.append(a)
    return a


def volume_window(p: dict, field: str, free_stream: float = 0.0):
    """The part of the value range the cloud shows (the strongest decades of a log field; the slipstream faster than the free stream),
    as (lo, hi) in the field's units: what the web version does."""
    lo, hi = float(p["lo"]), float(p["hi"])
    if p.get("log"):
        lo = max(lo, hi * 10 ** -(3 if field == "Q-criterion" else 1.3))
    elif field == "Velocity" and free_stream > 0:
        lo = min(max(lo, 1.15 * free_stream), hi * 0.9)
    return lo, hi


def add_volume(view, p: dict, tab, window, density: float = 5.0, style: str = "map"):
    """Volume rendering of p (a uint8 grid, 0 = no fluid, 1..255 along the value scale, log or linear as p['log']): colour and opacity
    follow the value inside `window` (lo, hi). density: higher = a thicker cloud. style: 'map' (the colours of the scale) or 'smoke'."""
    nx, ny, nz = (int(v) for v in p["dims"])
    data = _arr(p["data"], np.uint8).reshape(nz, ny, nx)
    img = vtkImageData()
    img.SetDimensions(nx, ny, nz)
    img.SetOrigin(*[float(v) for v in p["origin"]])
    h = float(p["h"])
    img.SetSpacing(h, h, h)
    img.GetPointData().SetScalars(numpy_to_vtk(np.ascontiguousarray(data.ravel()), deep=True))
    lo, hi = float(p["lo"]), float(p["hi"])
    if p.get("log"):
        nm = lambda v: np.log10(max(v, 1e-30) / lo) / np.log10(hi / lo)  # noqa: E731
    else:
        nm = lambda v: (v - lo) / ((hi - lo) or 1.0)  # noqa: E731
    a, b = (float(np.clip(nm(v), 0.0, 1.0)) for v in window)
    s0, s1 = 1.0 + a * 254.0, 1.0 + max(b, a + 1e-3) * 254.0
    ctf = vtkColorTransferFunction()
    if style == "smoke":
        t = theme.tokens()
        c = tuple(int(t["ink"][i:i + 2], 16) / 255.0 for i in (1, 3, 5))
        ctf.AddRGBPoint(0, *c)
        ctf.AddRGBPoint(255, *c)
    else:
        for i in range(1, 256):
            r, g, bl = tab[int((i - 1) / 254.0 * (len(tab) - 1))][:3]
            ctf.AddRGBPoint(i, r, g, bl)
    otf = vtkPiecewiseFunction()
    otf.AddPoint(0, 0.0)
    otf.AddPoint(max(s0 - 1e-3, 0.5), 0.0)
    otf.AddPoint(s0, 0.0)
    otf.AddPoint(s1, 1.0)
    otf.AddPoint(255, 1.0)
    prop = vtkVolumeProperty()
    prop.SetColor(ctf)
    prop.SetScalarOpacity(otf)
    prop.SetInterpolationTypeToLinear()
    prop.ShadeOff()
    diag = float(np.linalg.norm(np.array(p["box"][3:]) - np.array(p["box"][:3]))) or 1.0
    prop.SetScalarOpacityUnitDistance(diag / (max(float(density), 0.5) * 10.0))
    mapper = vtkSmartVolumeMapper()
    mapper.SetInputData(img)
    vol = vtkVolume()
    vol.SetMapper(mapper)
    vol.SetProperty(prop)
    vol.PickableOff()
    view.ren.AddVolume(vol)
    view.overlay_actors.append(vol)
    return vol


def table(cmap: str, reverse: bool, bands: int):
    return colormaps.table(cmap, 256, reverse, bands)


class ParticleDots:
    """Dots carried along the particle paths (the web version's animated particles): each dot starts at a random place on its path and steps
    along it about 25 path steps a second times `speed`, back to its start at the end. Driven by a Qt timer; it stops by itself when its
    actor is no longer drawn (the overlay was cleared)."""

    def __init__(self, view, p: dict, tab, lo: float, hi: float, speed: float = 1.0, size: float = 5.0):
        from PySide6.QtCore import QTimer

        n, steps = int(p["n"]), int(p["steps"])
        P = _arr(p["paths"], np.float32).reshape(n, steps, 3).astype(np.float64)
        S = _arr(p["speed"], np.float32).reshape(n, steps).astype(np.float64)
        cnt = np.isfinite(P[:, :, 0]).sum(1)
        keep = cnt >= 2
        self.P, self.S, self.cnt = P[keep], S[keep], cnt[keep]
        self.view, self.speed = view, max(float(speed), 0.05)
        self.phase = np.random.default_rng(1).uniform(0, 1, len(self.cnt)) * self.cnt
        self.t = 0.0
        self.pd = vtkPolyData()
        self.points = vtkPoints()
        self.pd.SetPoints(self.points)
        m = vtkPolyDataMapper()
        m.SetInputData(self.pd)
        m.SetLookupTable(_lut(tab, lo, hi))
        m.UseLookupTableScalarRangeOn()
        m.SetScalarRange(lo, hi)
        m.SetColorModeToMapScalars()
        m.SetScalarModeToUsePointData()
        self.actor = vtkActor()
        self.actor.SetMapper(m)
        pr = self.actor.GetProperty()
        pr.SetPointSize(float(size))
        pr.SetRenderPointsAsSpheres(True)
        pr.SetLighting(False)
        self.actor.PickableOff()
        view.ren.AddActor(self.actor)
        view.overlay_actors.append(self.actor)
        self.frames = 0
        self.step(0.0)
        self.timer = QTimer(view)
        self.timer.setInterval(40)
        self.timer.timeout.connect(lambda: self.step(0.04))
        self.timer.start()

    def positions(self):
        j = (np.floor(self.phase + self.t * 25.0 * self.speed).astype(np.int64)) % np.maximum(self.cnt, 1)
        k = np.arange(len(self.cnt))
        return self.P[k, j], self.S[k, j]

    def step(self, dt: float):
        if dt and self.actor not in self.view.overlay_actors:      # (no longer drawn: stop)
            self.timer.stop()
            return
        self.t += dt
        self.frames += 1
        pts, sp = self.positions()
        self.points.SetData(numpy_to_vtk(np.ascontiguousarray(pts), deep=True))
        verts = vtkCellArray()
        n = len(pts)
        verts.SetData(numpy_to_vtkIdTypeArray(np.arange(n + 1, dtype=np.int64), deep=True),
                      numpy_to_vtkIdTypeArray(np.arange(n, dtype=np.int64), deep=True))
        self.pd.SetVerts(verts)
        s = numpy_to_vtk(np.ascontiguousarray(sp), deep=True)
        s.SetName("speed")
        self.pd.GetPointData().SetScalars(s)
        self.pd.Modified()
        if dt:
            self.view.render()

    def stop(self):
        self.timer.stop()
