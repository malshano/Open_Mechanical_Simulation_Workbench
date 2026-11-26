"""Smoothing of an isosurface payload (vortex surfaces) for display.

A marching-tetrahedra surface follows the mesh cells: its triangles are as coarse as the cells, so it is faceted and bumpy where the mesh is
coarse or the field is noisy. This smooths the SURFACE (its vertex positions), not the field:

* **Windowed-sinc smoothing** (Taubin's idea, in VTK's vtkWindowedSincPolyDataFilter): a low-pass filter of the vertex positions. Plain
  Laplacian smoothing shrinks a closed surface a little more with every pass (a tube gets thinner and finally disappears); the windowed-sinc
  filter passes the low frequencies (the shape of the vortex) almost unchanged and removes the high ones (the roughness of the mesh), so the
  volume stays within about a percent at the strengths offered. The edge of an open surface (where it was cut at the region box, or at the
  wall gap) is held fixed, so the surface still meets its neighbours there.
* The field is not touched (smoothing the field before the surface is made flattens thin vortices and moves the level they are cut at), and
  the values the surface is coloured by stay on their vertices.

It is a display filter: the data is unchanged, and `stats` says how much the area (and for a closed surface the volume) changed, so that the
window can say so. Normals for smooth shading are made by the 3D view.
"""
from __future__ import annotations

import base64

import numpy as np

PASS_BAND = (0.1, 0.02)       # the filter's pass band at strength 0+ and at 100 (VTK's default is 0.1; lower is smoother), interpolated in the log
MIN_PASSES, MAX_PASSES = 6, 30


def passes_for(strength: float) -> int:
    """Smoothing passes for a strength of 0 (none) to 100 (the strongest offered)."""
    s = min(max(float(strength), 0.0), 100.0)
    return 0 if s <= 0 else int(round(MIN_PASSES + (MAX_PASSES - MIN_PASSES) * s / 100.0))


def pass_band_for(strength: float) -> float:
    s = min(max(float(strength), 0.0), 100.0) / 100.0
    return float(PASS_BAND[0] * (PASS_BAND[1] / PASS_BAND[0]) ** s)


def _dec(p: dict, key: str, dt):
    return np.frombuffer(base64.b64decode(p[key]), dt)


def _enc(a: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(a).tobytes()).decode()


def _polydata(pos: np.ndarray, tris: np.ndarray):
    from vtkmodules.util.numpy_support import numpy_to_vtk, numpy_to_vtkIdTypeArray
    from vtkmodules.vtkCommonCore import vtkPoints
    from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkPolyData

    pd = vtkPolyData()
    pts = vtkPoints()
    pts.SetData(numpy_to_vtk(np.ascontiguousarray(pos, np.float64), deep=True))
    pd.SetPoints(pts)
    ca = vtkCellArray()
    ca.ImportLegacyFormat(numpy_to_vtkIdTypeArray(np.hstack([np.full((len(tris), 1), 3, np.int64), tris.astype(np.int64)]).ravel(), deep=True))
    pd.SetPolys(ca)
    return pd


def measure(pos: np.ndarray, tris: np.ndarray) -> dict:
    """Area of a triangle surface, and the volume it encloses when it is closed (None for an open surface). The volume assumes the triangles are
    wound the same way throughout, as ResultSet.iso makes them (each faces the way the field falls)."""
    if not len(tris):
        return {"area": 0.0, "volume": None, "closed": False}
    a, b, c = (pos[tris[:, k]].astype(np.float64) for k in range(3))
    area = float(0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1).sum())
    e = np.sort(np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]]), axis=1).astype(np.int64)
    _u, counts = np.unique(e[:, 0] * (int(tris.max()) + 1) + e[:, 1], return_counts=True)
    closed = bool((counts == 2).all())
    vol = float(abs(np.einsum("ij,ij->i", a, np.cross(b, c)).sum()) / 6.0) if closed else None
    return {"area": area, "volume": vol, "closed": closed}


def smooth_payload(p: dict, strength: float):
    """(payload with its vertices smoothed, stats). strength 0 returns the payload itself. stats: {'passes', 'strength', 'area_change' and
    'volume_change' as fractions (volume only for a closed surface), 'closed'}."""
    passes = passes_for(strength)
    if not p or not p.get("n_tris") or not passes:
        return p, {"passes": 0, "strength": 0.0, "area_change": 0.0, "volume_change": None, "closed": False}
    from vtkmodules.util.numpy_support import vtk_to_numpy
    from vtkmodules.vtkFiltersCore import vtkWindowedSincPolyDataFilter

    pos = _dec(p, "positions", np.float32).reshape(-1, 3).astype(np.float64)
    idx = _dec(p, "indices", np.uint32).reshape(-1, 3).astype(np.int64)
    pd = _polydata(pos, idx)
    f = vtkWindowedSincPolyDataFilter()
    f.SetInputData(pd)
    f.SetNumberOfIterations(passes)
    f.SetPassBand(pass_band_for(strength))
    f.BoundarySmoothingOff()               # (the cut edges stay where they are)
    f.FeatureEdgeSmoothingOff()
    f.NonManifoldSmoothingOff()           # (a vertex where sheets touch stays too: it is a border of one of them)
    f.NormalizeCoordinatesOn()             # (scaled into a unit box first: the filter is then independent of the units and the size)
    f.Update()
    out = vtk_to_numpy(f.GetOutput().GetPoints().GetData()).astype(np.float64)
    m0, m1 = measure(pos, idx), measure(out, idx)
    q = dict(p)
    q["positions"] = _enc(out.astype(np.float32))
    stats = {"passes": passes, "pass_band": pass_band_for(strength), "strength": float(min(max(strength, 0.0), 100.0)), "closed": m0["closed"],
             "area_change": (m1["area"] - m0["area"]) / m0["area"] if m0["area"] else 0.0,
             "volume_change": (m1["volume"] - m0["volume"]) / m0["volume"] if m0["volume"] else None}
    return q, stats
