"""The drag handles of a rotating zone in the Setup step (the shared ones of handles.py: the same look and behaviour as the refinement regions of the Mesh
step and the controls of the Results step).

| What the zone does   | Handle                                                                          | Numbers                                    |
|----------------------|---------------------------------------------------------------------------------|--------------------------------------------|
| Move                 | three arrows (x red, y green, z blue) and a ball at its middle                  | "Centre x", "Centre y", "Centre z" (mm)    |
| Resize               | a square-ended arrow on each flat face (length) and one on its side (radius)    | "Length along the axis", "Radius" (mm)     |
| Turn the axis        | two rings round its middle (the axes it is not along): 90 degrees at a time     | "Axis" (+x ... -z)                         |

A zone has to lie along x, y or z (the mesh and the sliding interface are built on that), so its axis is turned in steps of 90 degrees: the ring jumps from
one axis to the next and the drawing shows the zone along the new one. For an axis that is not along x, y or z the model is turned instead (the
"Centre on a round edge" and "Turn the model" buttons of the zone).

While a handle is dragged only the drawing, the numbers in the card and a line in the corner of the view change; the zone is stored (and the mesh marked as
out of date) when the handle is let go."""
from __future__ import annotations

import numpy as np

from .. import regions as region_tools
from .. import zones as zone_tools
from .forms import fmt_num
from .gizmo import nice_step_mm

M = 1000.0
MOVE_COLOUR = "#f5f5f5"


def face_key(key) -> str:
    return f"face.{key[0]}.{key[1]}" if key[0] == "length" else "face.radius"


class ZoneHandles:
    def __init__(self, rot):
        self.rot = rot
        self.showing = False                  # True while the 3D view's handles are this one's
        self.base: dict | None = None         # the stored zone when the drag in progress began
        self.index: int | None = None

    # ------------------------------------------------------------------ what the zone asks for
    def wanted(self) -> int | None:
        """The index of the zone whose handles are shown now, or None: only in the Setup step, only while nothing else is being picked in the 3D view
        (a click near a handle would take it instead of the face under it) and only for the zone whose "Show handles" box is ticked."""
        p = self.rot.panel
        i = self.rot.handle_zone
        if p.win is None or p.win.step != "setup" or not p.engine.pid or i is None:
            return None
        if p.axis_pick or p.sym.picking or p.sym.draft or p.active:
            return None
        zs = p.engine.state().get("moving_zones") or []
        return i if 0 <= i < len(zs) else None

    def specs(self, i: int, z: dict) -> list:
        r0 = max(float(z["radius"]), float(z["length"]) / 2)
        c = [float(x) for x in z["center"]]
        snap = nice_step_mm(2 * r0) / M
        out = [{"key": "move", "kind": "point", "point": c, "size": 0.9 * r0, "fit": True, "color": MOVE_COLOUR, "snap_m": snap,
                "callback": lambda key, v, final, i=i: self.on_move(i, v, final)}]
        ax = int(np.argmax(np.abs(zone_tools.axis_of(z))))
        for a in range(3):
            if a != ax:                                        # (a turn about the zone's own axis changes nothing)
                out.append({"key": f"tilt.{'xyz'[a]}", "kind": "ring", "axis": a, "point": c, "radius": 1.15 * r0, "fit": True, "value": 0.0, "detent_deg": 90.0,
                            "callback": (lambda key, v, final, a=a, i=i: self.on_tilt(i, a, v, final))})
        for h in zone_tools.face_handles(z):
            k = h["key"]
            room = float(z["length"]) - region_tools.MIN_SIZE if k[0] == "length" else float(z["radius"]) - max(region_tools.MIN_SIZE, 1.01 * float(z.get("cell_size") or 0))
            out.append({"key": face_key(k), "kind": "face", "axis": 0 if k[0] == "length" else 1, "point": np.asarray(h["pos"], float).tolist(),
                        "dir": np.asarray(h["dir"], float).tolist(), "length": 0.2 * r0, "fit": True, "value": 0.0, "limits": [-max(room, 0.0), None], "snap_m": snap,
                        "callback": (lambda key, v, final, k=k, i=i: self.on_scale(i, k, v, final))})
        return out

    def sync(self):
        """Show the handles of the zone that has them (or none): called after every drawing of the Setup step."""
        view = self.rot.panel.view
        if view is None:
            return
        i = self.wanted()
        if i is None:
            if self.showing:
                view.handles.clear()
                view.handles.label(None)
                self.showing = False
            return
        z = self.rot.zones()[i]
        view.handles.set(self.specs(i, z))
        self.showing = True

    def hide(self):
        view = self.rot.panel.view
        if self.showing and view is not None:
            view.handles.clear()
            view.handles.label(None)
        self.showing = False

    # ------------------------------------------------------------------ what a handle does
    def _begin(self, i: int):
        self.index = i
        self.base = self.rot.zones()[i]

    def _show(self, i: int, z: dict, text: str):
        """The drawing, the numbers of the card and a line in the corner follow the drag; nothing is stored."""
        rot = self.rot
        rot.draw_zones(i, override={i: z})
        rot.show_numbers(i, z)
        rot.panel.view.handles.label(text)
        view = rot.panel.view
        view.handles.set(self.specs(i, z))

    def _store(self, i: int, z: dict):
        rot = self.rot
        view = rot.panel.view
        view.handles.label(None)
        zs = rot.zones()
        keep = {k: z[k] for k in ("center", "axis", "radius", "length")}
        zs[i].update(keep)
        self.base = None
        rot._save_zones(zs)
        now = rot.zones()[i]
        if any(now[k] != keep[k] for k in keep):                     # (the engine refused it - the zone would touch another: the zone, its card and its handles go back)
            rot.panel._sig = None
            rot.panel.redo()

    def on_move(self, i: int, point, final: bool):
        if self.base is None:
            self._begin(i)
        z = zone_tools.moved(self.base, point)
        d = (np.asarray(point, float) - np.asarray(self.base["center"], float)) * M
        self._show(i, z, f"Moving {self.base['name']}: ({d[0]:+.1f}, {d[1]:+.1f}, {d[2]:+.1f}) mm")
        if final:
            self._store(i, z)

    def on_scale(self, i: int, key, delta: float, final: bool):
        if not final and self.base is None:
            self._begin(i)
        if self.base is None:
            return
        g = np.zeros((3, 2))
        g[(0, key[1]) if key[0] == "length" else (1, 0)] = float(delta)
        z = zone_tools.grown(self.base, g)
        what = "radius" if key[0] == "radius" else "length"
        self._show(i, z, f"{self.base['name']} {what}: {fmt_num(round((z['radius'] if what == 'radius' else z['length']) * M, 2))} mm ({float(delta) * M:+.1f} mm)")
        if final:
            self._store(i, z)

    def on_tilt(self, i: int, about: int, angle: float, final: bool):
        if not final and self.base is None:
            self._begin(i)
        if self.base is None:
            return
        name = zone_tools.tilted_axis(self.base, about, float(np.degrees(angle)))
        z = {**self.base, "axis": name}
        self._show(i, z, f"{self.base['name']} axis: {name}" + ("" if name == self.base["axis"] else f" (was {self.base['axis']})"))
        if final:
            if name == self.base["axis"]:
                self.base = None
                self.rot.panel.view.handles.label(None)
                self.rot.draw_zones(i)
                self.sync()
            else:
                self._store(i, z)
