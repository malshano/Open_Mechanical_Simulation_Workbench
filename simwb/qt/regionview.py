"""The refinement regions of the Mesh step drawn in the 3D view, so that where the finer cells will be can be seen before the mesh is made: each
box or cylinder (a ring has an inner radius too) as a translucent body with an outline, in its own colour; the one being edited is stronger.
Drawn from the engine's region dictionaries (metres): boxes {min, max}, cylinders {start, end, radius, [inner_radius]}."""
from __future__ import annotations

import math

import numpy as np
from .. import regions as region_tools
from vtkmodules.util.numpy_support import numpy_to_vtk, numpy_to_vtkIdTypeArray
from vtkmodules.vtkCommonCore import vtkPoints
from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkPolyData
from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper

PALETTE = ("#14b8a6", "#e879f9", "#84cc16", "#fb923c", "#60a5fa", "#f43f5e")
SEGMENTS = 48


def colour_of(i: int) -> str:
    return PALETTE[i % len(PALETTE)]


def _rgb(h: str):
    return tuple(int(h[k:k + 2], 16) / 255.0 for k in (1, 3, 5))


def _pd(points, polys=(), lines=()) -> vtkPolyData:
    pd = vtkPolyData()
    p = vtkPoints()
    p.SetData(numpy_to_vtk(np.ascontiguousarray(np.asarray(points, float)), deep=True))
    pd.SetPoints(p)
    for attr, cells in ((pd.SetPolys, polys), (pd.SetLines, lines)):
        ca = vtkCellArray()
        if len(cells):
            flat = []
            for c in cells:
                flat += [len(c)] + [int(i) for i in c]
            ca.ImportLegacyFormat(numpy_to_vtkIdTypeArray(np.array(flat, np.int64), deep=True))
        attr(ca)
    return pd


def box_geometry(lo, hi):
    """(points, triangles, polylines) of a box."""
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    pts = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    quads = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    tris = [t for q in quads for t in ((q[0], q[1], q[2]), (q[0], q[2], q[3]))]
    lines = [(0, 1, 2, 3, 0), (4, 5, 6, 7, 4), (0, 4), (1, 5), (2, 6), (3, 7)]
    return pts, tris, lines


def cylinder_geometry(a, b, radius: float, inner: float = 0.0, n: int = SEGMENTS):
    """(points, triangles, polylines) of a cylinder from a to b; with an inner radius also the inner wall and its circles (a ring)."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    ax = b - a
    ax = ax / (np.linalg.norm(ax) or 1.0)
    h = np.array([1.0, 0.0, 0.0]) if abs(ax[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    u = np.cross(ax, h)
    u /= np.linalg.norm(u)
    v = np.cross(ax, u)
    t = np.linspace(0, 2 * math.pi, n, endpoint=False)
    circ = np.cos(t)[:, None] * u + np.sin(t)[:, None] * v
    pts, tris, lines = [], [], []

    def ring(radius_, base):
        i0 = len(pts)
        pts.extend((a + radius_ * c) for c in circ)
        pts.extend((b + radius_ * c) for c in circ)
        for i in range(n):
            j = (i + 1) % n
            tris.extend([(i0 + i, i0 + j, i0 + n + j), (i0 + i, i0 + n + j, i0 + n + i)])
        lines.append([i0 + i for i in range(n)] + [i0])
        lines.append([i0 + n + i for i in range(n)] + [i0 + n])
        for k in (0, n // 4, n // 2, 3 * n // 4):
            lines.append((i0 + k, i0 + n + k))
        return i0

    ring(radius, 0)
    if inner > 0:
        ring(inner, 1)
    else:                                                   # (a solid cylinder: its two end discs)
        for end in (a, b):
            c0 = len(pts)
            pts.append(end)
            for i in range(n):
                j = (i + 1) % n
                tris.append((c0, (0 if end is a else n) + i, (0 if end is a else n) + j))
    return pts, tris, lines


class RegionView:
    def __init__(self, view):
        self.v = view
        self.actors: list = []
        self.regions: list = []
        self.active: int | None = None

    def clear(self):
        was = bool(self.actors)
        self.regions = []
        self.v._clear(self.actors)
        if was:
            self.v.render()

    def set(self, regions: list, active: int | None = None):
        """Draw these regions (the engine's dictionaries) - or none."""
        if not regions:
            self.clear()
            return
        sig = (repr(regions), active)
        if sig == getattr(self, "_sig", None) and self.actors:
            return
        self._sig = sig
        self.regions, self.active = list(regions), active
        self.v._clear(self.actors)
        width = 2.0 * max(self.v._px()[0], 1.0)
        for i, r in enumerate(regions):
            try:
                if r.get("shape") == "cylinder":
                    pts, tris, lines = cylinder_geometry(r["start"], r["end"], float(r["radius"]), float(r.get("inner_radius") or 0))
                else:
                    pts, tris, lines = box_geometry(r["min"], r["max"])
                    if region_tools.is_turned(r):                      # (a box that is turned about its middle)
                        c = (np.asarray(r["min"], float) + np.asarray(r["max"], float)) / 2
                        pts = (c + (np.asarray(pts, float) - c) @ region_tools.rotation_of(r).T).tolist()
            except (KeyError, TypeError, ValueError):
                continue                                    # (a region that is half typed is not drawn)
            hot = active == i
            col = colour_of(i)
            self._add(_pd(pts, polys=tris), col, 0.22 if hot else 0.09, False, 1.0)
            self._add(_pd(pts, lines=lines), col, 1.0 if hot else 0.8, True, width * (1.7 if hot else 1.0))
        self.v.render()

    def _add(self, pd, colour: str, opacity: float, wire: bool, width: float):
        m = vtkPolyDataMapper()
        m.SetInputData(pd)
        act = vtkActor()
        act.SetMapper(m)
        pr = act.GetProperty()
        pr.SetColor(*_rgb(colour))
        pr.SetOpacity(opacity)
        pr.SetAmbient(1.0)
        pr.SetDiffuse(0.0)
        pr.SetSpecular(0.0)
        if wire:
            pr.SetLineWidth(width)
        act.PickableOff()
        self.v.ren.AddActor(act)
        self.actors.append(act)

    @staticmethod
    def label_point(r: dict) -> list:
        """Where a region's name hangs: the top of its box (the bounding box the engine stores for a cylinder too)."""
        lo, hi = r.get("min"), r.get("max")
        if lo and hi:
            return [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, hi[2]]
        return list(r.get("start") or [0, 0, 0])
