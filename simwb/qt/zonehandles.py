"""The drag handles of a rotating zone in the Setup step (the shared ones of handles.py: the same look and behaviour as the refinement regions of the Mesh
step and the controls of the Results step).

| What the zone does   | Handle                                                                          | Numbers                                    |
|----------------------|---------------------------------------------------------------------------------|--------------------------------------------|
| Move                 | three arrows (x red, y green, z blue) and a ball at its middle                  | "Centre x", "Centre y", "Centre z" (mm)    |
| Resize               | a square-ended arrow on each flat face (length) and one on its side (radius)    | "Length along the axis", "Radius" (mm)     |
| Turn the axis        | two rings round its middle (the axes it is not along): 90 degrees at a time     | "Axis" (+x ... -z)                         |

A zone has to lie along x, y or z (the mesh and the sliding interface are built on that), so its axis is turned in steps of 90 degrees: the ring jumps from
one axis to the next, the drawing and the on-screen line show the zone along the new one. For an axis that is not along x, y or z the model is turned instead
(the "Centre on a round edge" and "Turn the model" buttons of the zone).

Like a refinement region, a zone that is dragged is only previewed (a draft in rotors.py): the drawing, the numbers in the card and a line in the corner of the
view follow, the card shows Apply and Cancel (and the bar under the 3D view too), and nothing is stored until Apply. Escape cancels the drag in progress.

The handles are laid out so that each is taken where it is drawn: the move arrows reach 0.8 of the zone's size, the length handles' arrows start just beyond them
(a thin line leads to each from its face), the radius handle sticks out 0.15 of the size beyond the side, and the rings (taken anywhere on them) are 1.4 of the size."""
from __future__ import annotations

import numpy as np

from .. import zones as zone_tools
from .forms import fmt_num
from .gizmo import nice_step_mm

M = 1000.0
MOVE_COLOUR = "#f5f5f5"
MOVE_ARROW = 0.8                 # the move arrows' length, in units of the zone's size r0 = max(radius, half the length)
GAP = 0.05                       # a length handle's arrow starts this far beyond the move arrow (a thin line leads to it from the face) ...
BEYOND = 0.15                    # ... and is this long
RADIUS_ARROW = 0.15
RING = 1.4


def face_key(key) -> str:
    return f"face.{key[0]}.{key[1]}" if key[0] == "length" else "face.radius"


class ZoneHandles:
    def __init__(self, rot):
        self.rot = rot
        self.showing = False                  # True while the 3D view's handles are this one's
        self.base: dict | None = None         # the zone (stored, or with its draft) when the drag in progress began

    # ------------------------------------------------------------------ what the zone asks for
    def wanted(self) -> int | None:
        """The index of the zone whose handles are shown now, or None: only in the Setup step, only while nothing else is being picked in the 3D view
        (a click near a handle would take it instead of the face under it) and not when the zone's box was unticked."""
        p = self.rot.panel
        if p.win is None or p.win.step != "setup" or not p.engine.pid:
            return None
        if p.axis_pick or p.sym.picking or p.sym.draft or p.active:
            return None
        return self.rot.shown_zone()

    def specs(self, i: int, z: dict) -> list:
        r0 = max(float(z["radius"]), float(z["length"]) / 2)
        c = [float(x) for x in z["center"]]
        snap = nice_step_mm(2 * r0) / M
        rmin, lmin, _why = self.rot.min_for(z)
        out = [{"key": "move", "kind": "point", "point": c, "size": MOVE_ARROW * r0, "fit": True, "color": MOVE_COLOUR, "snap_m": snap,
                "callback": lambda key, v, final, i=i: self.on_move(i, v, final), "on_cancel": lambda key, i=i: self.on_cancel(i)}]
        ax = int(np.argmax(np.abs(zone_tools.axis_of(z))))
        for a in range(3):
            if a != ax:                                        # (a turn about the zone's own axis changes nothing)
                out.append({"key": f"tilt.{'xyz'[a]}", "kind": "ring", "axis": a, "point": c, "radius": RING * r0, "fit": True, "value": 0.0, "detent_deg": 90.0,
                            "callback": (lambda key, v, final, a=a, i=i: self.on_tilt(i, a, v, final))})
        for h in zone_tools.face_handles(z):
            k = h["key"]
            start = 0.0
            if k[0] == "length":
                room = float(z["length"]) - lmin
                start = max(MOVE_ARROW * r0 - float(z["length"]) / 2, 0.0) + GAP * r0          # (the arrow starts beyond the move arrow that lies on the same line)
                reach = BEYOND * r0
            else:
                room = float(z["radius"]) - rmin
                reach = RADIUS_ARROW * r0
            out.append({"key": face_key(k), "kind": "face", "axis": 0 if k[0] == "length" else 1, "point": np.asarray(h["pos"], float).tolist(),
                        "dir": np.asarray(h["dir"], float).tolist(), "length": reach, "start": start, "fit": True, "value": 0.0, "limits": [-max(room, 0.0), None], "snap_m": snap,
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
        view.handles.set(self.specs(i, self.rot.current(i)))
        self.showing = True

    def hide(self):
        view = self.rot.panel.view
        if self.showing and view is not None:
            view.handles.clear()
            view.handles.label(None)
        self.showing = False
        self.base = None

    def hide_label(self):
        view = self.rot.panel.view
        if view is not None:
            view.handles.label(None)

    # ------------------------------------------------------------------ what a handle does
    def _begin(self, i: int):
        self.base = self.rot.current(i)

    def _show(self, i: int, z: dict, text: str, final: bool):
        """The drawing, the numbers of the card and a line in the corner follow the drag; nothing is stored (the zone is a draft until Apply)."""
        rot = self.rot
        rot.set_draft(i, z)
        rot.draw_zones(i, live=True)
        rot.show_numbers(i, rot.current(i))
        view = rot.panel.view
        view.handles.label(None if final else text)
        view.handles.set(self.specs(i, rot.current(i)))
        if final:
            self.base = None
            rot.panel.redo()                              # (the card gets its Apply and Cancel, or loses them)
            if rot.panel.win is not None:
                rot.panel.win.shell.sync()

    def on_move(self, i: int, point, final: bool):
        if self.base is None:
            self._begin(i)
        z = {**self.base, "center": [float(x) for x in point]}
        d = (np.asarray(point, float) - np.asarray(self.base["center"], float)) * M
        self._show(i, z, f"Moving {self.base['name']}: ({d[0]:+.1f}, {d[1]:+.1f}, {d[2]:+.1f}) mm", final)

    def on_scale(self, i: int, key, delta: float, final: bool):
        if not final and self.base is None:
            self._begin(i)
        if self.base is None:
            return
        g = np.zeros((3, 2))
        g[(0, key[1]) if key[0] == "length" else (1, 0)] = float(delta)
        z = zone_tools.grown(self.base, g, self.rot.size())
        what = "radius" if key[0] == "radius" else "length"
        got = z["radius"] if what == "radius" else z["length"]
        self._show(i, z, f"{self.base['name']} {what}: {fmt_num(round(got * M, 2))} mm ({float(delta) * M:+.1f} mm)", final)

    def on_tilt(self, i: int, about: int, angle: float, final: bool):
        if not final and self.base is None:
            self._begin(i)
        if self.base is None:
            return
        name = zone_tools.tilted_axis(self.base, about, float(np.degrees(angle)))
        z = {**self.base, "axis": name}
        old = self.base["axis"]
        text = (f"{self.base['name']} axis: {name} (was {old}) - a zone lies along x, y or z, so its axis turns a quarter turn at a time" if name != old else
                f"{self.base['name']} axis: {old} - turn the ring past 45° for a quarter turn (a zone lies along x, y or z)")
        self._show(i, z, text, final)

    def on_cancel(self, i: int):
        """Escape during the drag of the ball or an arrow: the zone is as it was when the mouse went down."""
        self.on_move(i, (self.base or self.rot.current(i))["center"], True)
