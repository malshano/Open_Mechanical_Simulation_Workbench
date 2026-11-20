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
