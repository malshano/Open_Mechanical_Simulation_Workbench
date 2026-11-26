"""The rotating zones (MRF or sliding mesh) drawn in the 3D view, in the Setup and Mesh steps: each zone's cylinder of air as a translucent body
with its outline in the zone colour; a sliding-mesh zone has a stronger outline (its boundary is the sliding interface, where the turning cells
meet the fixed ones). A radial tick on each flat face shows how far the zone has been turned by the turn preview. Drawn from the engine's
`moving_zones` (metres); the geometry is simwb.zones."""
from __future__ import annotations

import math

import numpy as np
from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper

from .. import zones as zone_tools
from .regionview import _pd, _rgb, cylinder_geometry

ZONE_COLOUR = "#9b6cff"


class ZoneView:
    def __init__(self, view):
        self.v = view
        self.actors: list = []
        self._sig = None

    def clear(self):
        was = bool(self.actors)
        self._sig = None
        self.v._clear(self.actors)
        if was:
            self.v.render()

    def set(self, zones: list, active: int | None = None, angles: dict | None = None, ghosts: dict | None = None):
        """Draw these zones (the engine's dictionaries) - or none. active: the zone being edited (stronger); angles: {index: degrees} turned by the
        preview (the tick on the flat faces)."""
        angles = angles or {}
        ghosts = ghosts or {}
        if not zones:
            self.clear()
            return
        sig = (repr(zones), active, tuple(sorted((int(k), round(float(a), 3)) for k, a in angles.items())), tuple(sorted(int(k) for k in ghosts)))
        if sig == self._sig and self.actors:
            return
        self._sig = sig
        self.v._clear(self.actors)
        width = 2.0 * max(self.v._px()[0], 1.0)
        for i, z in enumerate(zones):
            try:
                a, b = zone_tools.ends(z)
                pts, tris, lines = cylinder_geometry(a, b, float(z["radius"]))
            except (KeyError, TypeError, ValueError):
                continue                                          # (a zone that is half typed is not drawn)
            hot = active == i
            sliding = z.get("method") == "sliding"
            self._add(_pd(pts, polys=tris), 0.20 if hot else 0.09, False, 1.0)
            self._add(_pd(pts, lines=lines), 1.0 if hot or sliding else 0.75, True, width * (2.2 if sliding else 1.3) * (1.4 if hot else 1.0))
            tick = self._tick(z, a, b, float(angles.get(i, 0.0)))
            if tick is not None:
                self._add(_pd(*tick), 1.0, True, width * 2.0)
            g = ghosts.get(i)
            if g is not None and len(g[1]) and angles.get(i):
                pts_g = np.asarray(g[0], float)
                M = zone_tools.turn_matrix(z, float(angles[i]))
                pts_g = pts_g @ M[:3, :3].T + M[:3, 3]            # (what is inside, turned: a ghost in the zone colour beside the part itself)
                self._add(_pd(pts_g, polys=g[1]), 0.55, False, 1.0)
        self.v.render()

    @staticmethod
    def _tick(z: dict, a, b, deg: float):
        """A radial line on each flat face at `deg` degrees from a fixed direction: it turns with the zone."""
        ax = zone_tools.axis_of(z)
        h = np.array([1.0, 0.0, 0.0]) if abs(ax[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
        u = np.cross(ax, h)
        u /= np.linalg.norm(u)
        v = np.cross(ax, u)
        t = math.radians(deg)
        d = math.cos(t) * u + math.sin(t) * v
        r = float(z["radius"])
        pts = [a, a + d * r, b, b + d * r]
        return pts, (), [(0, 1), (2, 3)]

    def _add(self, pd, opacity: float, wire: bool, width: float):
        m = vtkPolyDataMapper()
        m.SetInputData(pd)
        act = vtkActor()
        act.SetMapper(m)
        pr = act.GetProperty()
        pr.SetColor(*_rgb(ZONE_COLOUR))
        pr.SetOpacity(opacity)
        pr.SetAmbient(1.0)
        pr.SetDiffuse(0.0)
        pr.SetSpecular(0.0)
        if wire:
            pr.SetLineWidth(width)
        act.PickableOff()
        self.v.ren.AddActor(act)
        self.actors.append(act)
