"""Picking CAD faces by ray, with no render window: a static cell locator over the faces' triangles. The 3D view turns a mouse click into a
ray (camera position -> point under the pointer) and asks this for the face it hits; tests and the control API give rays directly.

Solid faces win over the faces of the (translucent) air box: the air never blocks a click on the part."""
from __future__ import annotations

import numpy as np
from vtkmodules.util.numpy_support import numpy_to_vtk, numpy_to_vtkIdTypeArray
from vtkmodules.vtkCommonCore import vtkIdList, vtkPoints
from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkPolyData, vtkStaticCellLocator


def polydata(pts: np.ndarray, tris: np.ndarray) -> vtkPolyData:
    pd = vtkPolyData()
    p = vtkPoints()
    p.SetData(numpy_to_vtk(np.ascontiguousarray(pts, np.float64), deep=True))
    pd.SetPoints(p)
    cells = np.hstack([np.full((len(tris), 1), 3, np.int64), np.asarray(tris, np.int64)]).ravel()
    ca = vtkCellArray()
    ca.ImportLegacyFormat(numpy_to_vtkIdTypeArray(cells, deep=True))
    pd.SetPolys(ca)
    return pd


class _Set:
    """The triangles of a group of faces and a locator over them."""

    def __init__(self):
        self.ids = np.zeros(0, np.int64)        # face id of each triangle
        self.pd: vtkPolyData | None = None
        self.loc: vtkStaticCellLocator | None = None

    def build(self, faces: list):
        """faces: [(face_id, vertices (n,3), triangles (m,3))]"""
        pts, tris, ids, off = [], [], [], 0
        for fid, v, t in faces:
            v = np.asarray(v, float).reshape(-1, 3)
            t = np.asarray(t, np.int64).reshape(-1, 3)
            if not len(v) or not len(t):
                continue
            pts.append(v)
            tris.append(t + off)
            ids.append(np.full(len(t), fid, np.int64))
            off += len(v)
        if not pts:
            self.ids, self.pd, self.loc = np.zeros(0, np.int64), None, None
            return
        self.ids = np.concatenate(ids)
        self.pd = polydata(np.vstack(pts), np.vstack(tris))
        self.loc = vtkStaticCellLocator()
        self.loc.SetDataSet(self.pd)
        self.loc.BuildLocator()

    def hit(self, p0, p1):
        """(face id, point, distance from p0) of the first triangle on the segment p0 -> p1, or None."""
        if self.loc is None:
            return None
        pts, cells = vtkPoints(), vtkIdList()
        if not self.loc.IntersectWithLine(list(map(float, p0)), list(map(float, p1)), 1e-9, pts, cells) or not pts.GetNumberOfPoints():
            return None
        a = np.asarray(p0, float)
        best = None
        for k in range(pts.GetNumberOfPoints()):
            x = np.array(pts.GetPoint(k))
            d = float(np.linalg.norm(x - a))
            if best is None or d < best[2]:
                best = (int(self.ids[cells.GetId(k)]), [float(v) for v in x], d)
        return best


def locator_for(pd: vtkPolyData) -> _Set:
    """A ray-pick index over any surface (the drawn result overlay): the 'face id' of a hit is the triangle's index."""
    s = _Set()
    s.pd = pd
    s.ids = np.arange(pd.GetNumberOfCells(), dtype=np.int64)
    s.loc = vtkStaticCellLocator()
    s.loc.SetDataSet(pd)
    s.loc.BuildLocator()
    return s


class FaceIndex:
    def __init__(self):
        self.solid = _Set()
        self.air = _Set()
        self.geom: dict = {}     # face id -> (vertices, triangles, kind)

    def build(self, solid: list, air: list):
        """solid / air: [(face_id, vertices, triangles, kind)]"""
        self.geom = {f[0]: (np.asarray(f[1], float).reshape(-1, 3), np.asarray(f[2], np.int64).reshape(-1, 3), f[3]) for f in solid + air}
        self.solid.build([(f[0], f[1], f[2]) for f in solid])
        self.air.build([(f[0], f[1], f[2]) for f in air])

    def pick(self, p0, p1, air_pickable: bool = False):
        """The face a ray from p0 to p1 hits: {'face', 'point', 't', 'air'} or None. A solid face is preferred to an air face even when
        the air face is nearer (the air box is see-through); air faces only count when air_pickable."""
        h = self.solid.hit(p0, p1)
        if h is not None:
            return {"face": h[0], "point": h[1], "t": h[2], "air": False}
        if air_pickable:
            h = self.air.hit(p0, p1)
            if h is not None:
                return {"face": h[0], "point": h[1], "t": h[2], "air": True}
        return None

    def triangles_of(self, ids) -> tuple:
        """(points, triangles) of the given faces, to draw them highlighted."""
        pts, tris, off = [], [], 0
        for i in ids:
            g = self.geom.get(int(i))
            if g is None:
                continue
            pts.append(g[0])
            tris.append(g[1] + off)
            off += len(g[0])
        if not pts:
            return np.zeros((0, 3)), np.zeros((0, 3), np.int64)
        return np.vstack(pts), np.vstack(tris)


def long_segment(p0, p1, lo, hi):
    """The ray from p0 through p1 (the near and far clipping planes under a pixel) made long enough to reach everything pickable: the camera's clipping
    planes are fitted to the model, and a camera inside an air volume 50 times its size would otherwise never touch the far walls. lo, hi: the
    corners of the box that holds everything that can be picked (the air volume included)."""
    a = np.asarray(p0, float)
    d = np.asarray(p1, float) - a
    n = float(np.linalg.norm(d))
    if n < 1e-15:
        return list(a), list(np.asarray(p1, float))
    d = d / n
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    reach = float(np.linalg.norm(hi - lo)) + float(np.linalg.norm((lo + hi) / 2 - a))
    return list(a), list(a + d * max(n, 1.5 * reach))
