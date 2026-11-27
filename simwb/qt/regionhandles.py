"""The drag handles of a refinement region in the Mesh step (the shared ones of handles.py: the same look and the same behaviour as in the Results step
and for the rotating zones of the Setup step).

| What the region does   | Handle                                                                | Numbers                                  |
|------------------------|-----------------------------------------------------------------------|------------------------------------------|
| Move                   | three arrows (x red, y green, z blue) and a ball at its middle        | "Move by" x, y, z (mm)                   |
| Turn                   | three rings round its middle (about x, y, z; Shift: 5 degree steps)   | "Turn about" x, y, z (degrees)           |
| Resize                 | a square-ended arrow on each face (a box: six; a cylinder: its two ends and its side) | "Size", "Scale all"      |

The handles are for the region the session (regionxform.py) works on; they show where the region would be after Apply and follow the typed numbers. While
one is dragged only the drawing, the numbers and a line in the corner of the view change; nothing is stored until Apply."""
from __future__ import annotations

import numpy as np

from .. import regions as region_tools
from .gizmo import nice_step_mm

M = 1000.0
MOVE_COLOUR = "#f5f5f5"                    # the ball of the move handle: white, as the ball of the Geometry step's gizmo


def face_key(key) -> str:
    return f"face.{key[0]}.{key[1]}"


def parse_face_key(name: str):
    a, b = name.split(".")[1:]
    return (int(a) if a.isdigit() else a, int(b))


class RegionHandles:
    def __init__(self, xform):
        self.x = xform

    def specs(self, r: dict) -> list:
        """The handles of the region `r` (as it would be after Apply): move, three turns, the resize faces."""
        lo, hi = region_tools.bounds(r)
        ext = np.asarray(hi, float) - np.asarray(lo, float)
        size = float(ext.max()) or 0.1
        bound = 0.5 * float(np.linalg.norm(ext)) or size
        c = region_tools.centre(r)
        snap = nice_step_mm(size) / M
        x = self.x
        out = [{"key": "move", "kind": "point", "point": c.tolist(), "size": 0.5 * bound, "fit": True, "color": MOVE_COLOUR, "snap_m": snap,
                "callback": x.on_move}]
        for a in range(3):
            out.append({"key": f"turn.{'xyz'[a]}", "kind": "ring", "axis": a, "point": c.tolist(), "radius": 0.6 * bound, "fit": True, "value": 0.0,
                        "callback": (lambda key, v, final, a=a: x.on_turn(a, key, v, final))})
        sz = region_tools.size_of(r)
        for h in region_tools.face_handles(r):
            k = h["key"]
            ax = 0 if k[0] == "len" else 1 if k[0] == "rad" else int(k[0])
            if k[0] == "len":
                room = sz[0] - region_tools.MIN_SIZE
            elif k[0] == "rad":
                room = sz[1] - max(float(r.get("inner_radius") or 0.0) + region_tools.MIN_SIZE, region_tools.MIN_SIZE)
            else:
                room = sz[int(k[0])] - region_tools.MIN_SIZE
            out.append({"key": face_key(k), "kind": "face", "axis": ax, "point": np.asarray(h["pos"], float).tolist(), "dir": np.asarray(h["dir"], float).tolist(),
                        "length": 0.1 * size, "fit": True, "value": 0.0, "limits": [-max(room, 0.0), None], "snap_m": snap,
                        "callback": (lambda key, v, final, k=k: x.on_scale(k, v, final))})
        return out

    def show(self, r: dict):
        view = self.x.view
        if view is not None:
            view.handles.set(self.specs(r))
