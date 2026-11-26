"""The drag handles of the Results step: every control that has a position in space gets a handle in the 3D view (handles.py is the one behaviour),
and its numbers follow the handle while it is dragged and the handle follows the numbers.

| Control (Results step)                     | Handle                                                 | Numbers                                      |
|--------------------------------------------|--------------------------------------------------------|----------------------------------------------|
| Contour planes (Flow tab), each            | a sheet over the region, dragged along its axis        | slider, "Position (mm)", the label           |
| Arrows' plane (Flow tab)                   | the same                                               | slider, "Position (mm)"                      |
| Seed disk of the particle paths (Flow tab) | a disk with arrows (x, y, z) and a ball                | "Disk centre X, Y, Z" and "Disk radius"      |
| Section of a part (Display tab, bar)       | a sheet over the part                                  | slider, "Position (mm)", the bar's value     |
| Pinned points (Probe tab), each            | a ball that follows the surface under the pointer      | X, Y, Z of the pin; its value is read there  |
| Ends A and B of the line plot (Probe tab)  | the same                                               | X, Y, Z of each end; the plot is made again  |

While a handle is dragged only cheap things happen (the handle, the numbers, a label); the flow is cut again, the paths drawn again and the line plotted
again when it is let go. The handles of a tab show while that tab is shown."""
from __future__ import annotations

from functools import partial

import numpy as np
from PySide6.QtCore import QTimer

from .handles import colour_of

SIZE_FRACTION = 0.045                       # a point handle's arrows are this share of the picture's diagonal


class ResultHandles:
    def __init__(self, panel):
        self.p = panel
        self._hit: dict = {}                # key -> the (cell, point) of the drawn result the handle was last dragged to
        self._seeding = False

    @property
    def view(self):
        return self.p.view

    def tab(self) -> str:
        return next((k for k, b in self.p.tab_btns.items() if b.isChecked()), "")

    def clear(self):
        if self.p.view is not None:
            self.p.view.handles.clear()

    # ------------------------------------------------------------------ what the controls ask for
    def specs(self) -> list:
        p, f = self.p, self.p.flow
        specs = []
        tab = self.tab()
        flow_tab = tab == "flow" and p.tab_btns["flow"].isVisibleTo(p)
        if flow_tab and f._box:
            box = list(f._box)
            for i, pl in enumerate(f.planes):
                pos = f.plane_position(i)
                if pos is not None:
                    specs.append({"key": f"plane{i + 1}", "kind": "plane", "axis": "xyz".index(pl["axis"]), "value": pos[0], "box": box,
                                  "limits": pos[1], "color": colour_of(i), "callback": partial(self._plane, i), "snap_m": self._snap(box)})
            if f.vec_on.isChecked() and f.vector_position() is not None:
                pos = f.vector_position()
                specs.append({"key": "vectors", "kind": "plane", "axis": "xyz".index(f.vec_axis_now()), "value": pos[0], "box": box, "limits": pos[1],
                              "color": "#fbbf24", "callback": self._vectors, "snap_m": self._snap(box)})
            if f.par_on.isChecked() and f.par_seed.currentData() == "disk":
                d = f.seed_disk()
                if d is None:
                    self._default_seed()
                else:
                    ax = self._flow_axis()
                    specs.append({"key": "seeds", "kind": "point", "point": d["center"], "size": SIZE_FRACTION * self._diag(box),
                                  "disc": {"radius": d["radius"], "axis": ax}, "color": "#38bdf8", "callback": self._seeds, "snap_m": self._snap(box)})
        if tab == "probe" and self.view._overlay_index is not None:
            size = SIZE_FRACTION * self._scene_diag()
            for i, pin in enumerate(p.pins):
                specs.append({"key": f"pin{i + 1}", "kind": "point", "point": pin["point"], "size": size, "resolver": partial(self._resolve, f"pin{i + 1}"),
                              "color": "#f59e0b", "callback": partial(self._pin, i)})
            if len(p.line_ends) >= 2:
                for k, nm in enumerate("AB"):
                    specs.append({"key": f"line{nm}", "kind": "point", "point": p.line_ends[k], "size": size, "resolver": partial(self._resolve, f"line{nm}"),
                                  "color": "#e5e7eb", "callback": partial(self._line, k)})
        ax = p.section_axis() if not p.tab_btns["flow"].isVisibleTo(p) else ""
        if ax and p.section_box.isVisibleTo(p):
            ext = p.section_extent()
            box = self._result_box()
            if ext and box:
                specs.append({"key": "section", "kind": "plane", "axis": "xyz".index(ax), "value": ext[0] + p.section_pos.value() / 1000.0 * (ext[1] - ext[0]),
                              "box": box, "limits": ext, "color": "#ffb703", "callback": self._section, "snap_m": self._snap(box)})
        return specs

    def sync(self):
        """Show the handles the controls ask for (called after every drawing, and when the tab changes)."""
        p = self.p
        if p.win is None or p.win.step != "results" or not p.engine.pid or p.view is None:
            return self.clear()
        self._hook_tabs()
        s = self.specs()
        if s:
            p.view.handles.set(s)
        else:
            p.view.handles.clear()

    def _hook_tabs(self):
        if getattr(self, "_hooked", False):
            return
        self._hooked = True
        for b in self.p.tab_btns.values():
            b.toggled.connect(lambda *_: self.sync())

    # ------------------------------------------------------------------ sizes
    @staticmethod
    def _diag(box) -> float:
        b = np.asarray(box, float)
        return float(np.linalg.norm(b[3:] - b[:3])) or 1.0

    @staticmethod
    def _snap(box) -> float:
        """A round step for a Shift-drag: 1/100 of the box's diagonal, as a 1-2-5 number."""
        d = ResultHandles._diag(box) / 100.0
        e = 10 ** np.floor(np.log10(d))
        return float(e * (1 if d / e < 1.5 else 2 if d / e < 3.5 else 5 if d / e < 7.5 else 10))

    def _scene_diag(self) -> float:
        o = getattr(self.view, "_ov", None)
        if not o:
            return 1.0
        pos = o["pos"]
        return float(np.linalg.norm(pos.max(0) - pos.min(0))) or 1.0

    def _result_box(self):
        """The result's extent as a box (m), from the same cached extents as the section bar."""
        try:
            ext = [self.p.win.shell._extent("results", a) for a in "xyz"]
        except Exception:  # noqa: BLE001
            return None
        if any(e is None for e in ext):
            return None
        return [e[0] for e in ext] + [e[1] for e in ext]

    def _flow_axis(self) -> int:
        try:
            u = np.asarray(self.p.engine._flow_summary().get("U_inf") or [1.0, 0.0, 0.0], float)
        except Exception:  # noqa: BLE001
            u = np.array([1.0, 0.0, 0.0])
        return int(np.argmax(np.abs(u)))

    # ------------------------------------------------------------------ what a handle does to its control
    def _plane(self, i, key, value, final):
        if i < len(self.p.flow.planes):
            self.p.flow.set_plane_position(i, value, final)

    def _vectors(self, key, value, final):
        self.p.flow.set_vector_position(value, final)

    def _section(self, key, value, final):
        self.p.set_section_position(value, final)

    def _seeds(self, key, point, final):
        self.p.flow.set_seed_disk(center=point, final=final)

    def _default_seed(self):
        """The disk was chosen for the first time: put it just upstream of the object (once the drawing is done: this runs inside it)."""
        if self._seeding:
            return
        d = self.p.engine.seed_disk_default()
        if not d:
            return
        self._seeding = True

        def go():
            self._seeding = False
            if self.p.flow.par_seed.currentData() == "disk" and not self.p.flow.seed_set:
                self.p.flow.set_seed_disk(d["center"], d["radius"], final=True)
        QTimer.singleShot(0, go)

    def _resolve(self, key, x, y):
        """The point of the drawn result under the pointer (a pinned point or an end of the line follows the surface)."""
        h = self.view.surface_point_under(x, y)
        if h is None:
            return None
        self._hit[key] = h
        return h[1]

    def _pin(self, i, key, point, final):
        h = self._hit.get(key)
        if h is not None and i < len(self.p.pins):
            self.p.set_pin_hit(i, h[0], point, final)

    def _line(self, k, key, point, final):
        h = self._hit.get(key)
        if h is not None and len(self.p.line_ends) >= 2:
            self.p.set_line_end(k, self.view.base_point(h[0], h[1]), final)
