"""A preview of the wall (inflation) layers in the 3D view: a translucent shell round the object, as thick as all the layers together, so that how
much of the air next to the walls the layers will take can be seen before meshing. The thickness follows the layer settings (number, growth ratio,
first layer). It is the object's own surface pushed out along its normals, so it is an approximation where the surface has sharp corners."""
from __future__ import annotations

import numpy as np
from vtkmodules.util.numpy_support import numpy_to_vtk, numpy_to_vtkIdTypeArray
from vtkmodules.vtkCommonCore import vtkPoints
from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkPolyData
from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper

COLOUR = (0.02, 0.71, 0.83)             # the layer shell
DIGITS = 7                              # vertices that are the same point to this many decimals (m) are one vertex: the shell stays in one piece


def shell_points(faces: list, thickness: float):
    """(points, triangles, base points) of the object's surface pushed out by `thickness` (m) along its vertex normals, or None. Each face is turned
    so that its triangles look away from the middle of the object (the tessellation of one face need not agree with the next about which way is
    out), then the faces share their edge vertices and one normal there."""
    parts = []
    for f in faces:
        m = f.get("mesh") or {}
        if not m.get("vertices") or not m.get("triangles"):
            continue
        parts.append((np.asarray(m["vertices"], float).reshape(-1, 3), np.asarray(m["triangles"], np.int64).reshape(-1, 3)))
    if not parts:
        return None
    middle = np.vstack([v for v, _t in parts]).mean(axis=0)
    pts, tris, off = [], [], 0
    for v, t in parts:
        fn = np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
        out = float(np.sum(fn * (v[t].mean(axis=1) - middle)))
        if out < 0:                                              # (this face looks inwards: reverse its triangles)
            t = t[:, [0, 2, 1]]
        pts.append(v)
        tris.append(t + off)
        off += len(v)
    p = np.vstack(pts)
    t = np.vstack(tris)
    _u, first, inv = np.unique(np.round(p, DIGITS), axis=0, return_index=True, return_inverse=True)
    p, t = p[first], inv.reshape(-1)[t]                          # (faces share their edge vertices: one normal there, no gaps)
    t = t[(t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 0] != t[:, 2])]
    a, b, c = p[t[:, 0]], p[t[:, 1]], p[t[:, 2]]
    fn = np.cross(b - a, c - a)                                  # (area weighted)
    n = np.zeros_like(p)
    for k in range(3):
        np.add.at(n, t[:, k], fn)
    ln = np.linalg.norm(n, axis=1)
    n = n / np.where(ln > 0, ln, 1.0)[:, None]
    return p + n * float(thickness), t, p


class LayerView:
    def __init__(self, view):
        self.v = view
        self.actors: list = []
        self._sig = None

    def clear(self):
        was = bool(self.actors)
        self.v._clear(self.actors)
        self._sig = None
        if was:
            self.v.render()

    def set(self, faces: list, thickness: float, key=None):
        if not faces or thickness <= 0:
            self.clear()
            return
        sig = (key, round(thickness, 9), len(faces))
        if sig == self._sig and self.actors:
            return
        self._sig = sig
        self.v._clear(self.actors)
        out = shell_points(faces, thickness)
        if out is None:
            return
        pts, tris, _base = out
        pd = vtkPolyData()
        p = vtkPoints()
        p.SetData(numpy_to_vtk(np.ascontiguousarray(pts, np.float64), deep=True))
        pd.SetPoints(p)
        cells = np.hstack([np.full((len(tris), 1), 3, np.int64), tris]).ravel()
        ca = vtkCellArray()
        ca.ImportLegacyFormat(numpy_to_vtkIdTypeArray(cells, deep=True))
        pd.SetPolys(ca)
        m = vtkPolyDataMapper()
        m.SetInputData(pd)
        act = vtkActor()
        act.SetMapper(m)
        pr = act.GetProperty()
        pr.SetColor(*COLOUR)
        pr.SetOpacity(0.28)
        pr.SetAmbient(1.0)
        pr.SetDiffuse(0.0)
        act.PickableOff()
        self.v.ren.AddActor(act)
        self.actors.append(act)
        self.v.render()
