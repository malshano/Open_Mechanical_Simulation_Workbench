"""What the Qt window talks to: the workbench engine (simwb.project) used in this process - no web server in between.
Long jobs (CAD import, meshing, solving) already run on their own threads in the engine; their progress comes back here as
Qt signals. Cases (a study = a folder with a project.json) are created, opened, saved, copied, exported and imported here."""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QSettings, Signal

from .. import project as P
from .. import resample
from ..results import merge_payloads

MAX_RECENT = 10
FLOW_STUDIES = ("external_aero", "cht_enclosure")


class Engine(QObject):
    opened = Signal(str)            # a study was opened (its id), or "" when closed
    changed = Signal()              # the study's data changed (a job finished, the assistant used a tool)
    job = Signal(dict)              # progress of a job of the open study
    message = Signal(str, bool)     # text for the status bar, is-an-error
    saved = Signal(str)             # time the study was last written

    def __init__(self, parent=None, settings: QSettings | None = None):
        super().__init__(parent)
        self.pid: str | None = None
        self.settings = settings or QSettings("SimWorkbench", "Workbench")   # (tests pass a file of their own)
        P.JOBS.listeners.append(self._on_event)   # (called from job threads: Qt queues the signals to the window's thread)

    # ------------------------------------------------------------------ events from job threads
    def _on_event(self, ev: dict):
        t = ev.get("type")
        if t == "job":
            j = ev.get("job") or {}
            if j.get("project") != self.pid:
                return
            self.job.emit(ev)
            if ev.get("final") or j.get("status") in ("done", "failed", "cancelled"):
                self.changed.emit()
        elif t == "project_changed" and ev.get("project") == self.pid:
            self.changed.emit()

    # ------------------------------------------------------------------ the open study
    def project(self) -> P.Project:
        if self.pid is None:
            raise RuntimeError("No study is open")
        return P.open_project(self.pid)

    def state(self) -> dict | None:
        return self.project().state() if self.pid else None

    def _open(self, pr: P.Project):
        self.pid = pr.id
        self._remember(pr.dir)
        self.opened.emit(pr.id)
        self.changed.emit()
        self.saved.emit(time.strftime("%H:%M:%S"))
        return pr

    def close_study(self):
        self.pid = None
        self.opened.emit("")

    # ------------------------------------------------------------------ cases: new, open, recent
    def new_study(self, name: str, study: str, location: str | None = None) -> P.Project:
        return self._open(P.create_project(name.strip() or "Study", study, location or None))

    def open_folder(self, path: str) -> P.Project:
        """Open a study folder from anywhere on the disk (one with a project.json)."""
        return self._open(P.open_project_folder(path))

    def open_id(self, pid: str) -> P.Project:
        return self._open(P.open_project(pid))

    def workspace_list(self) -> list:
        return P.list_projects()

    def recent(self) -> list:
        """[{name, path}] of the studies opened lately that are still on the disk, newest first."""
        out = []
        for p in self.settings.value("recent", [], type=list) or []:
            pj = Path(p) / "project.json"
            if pj.exists():
                try:
                    import json
                    out.append({"name": json.loads(pj.read_text()).get("name", Path(p).name), "path": str(p)})
                except (OSError, ValueError):
                    pass
        return out

    def _remember(self, folder: Path):
        cur = [str(folder)] + [p for p in (self.settings.value("recent", [], type=list) or []) if p != str(folder)]
        self.settings.setValue("recent", cur[:MAX_RECENT])

    def clear_recent(self):
        self.settings.setValue("recent", [])

    # ------------------------------------------------------------------ cases: save, save as, export, import
    def save(self) -> str:
        """Write the study to its folder now (it is also written after every change; this makes it explicit and says when)."""
        self.project().save(quiet=True)
        t = time.strftime("%H:%M:%S")
        self.saved.emit(t)
        return t

    def save_as(self, location: str, name: str | None = None, keep_results: bool = True) -> P.Project:
        """A full copy in another folder, which becomes the open study."""
        return self._open(P.save_project_as(self.pid, location, name, keep_results))

    def export_zip(self, path: str, keep_results: bool = True) -> Path:
        return P.export_project(self.pid, path, keep_results)

    def import_zip(self, path: str, location: str | None = None) -> P.Project:
        return self._open(P.import_project(path, location or None))

    def delete_open(self):
        pid = self.pid
        self.close_study()
        P.delete_project(pid)

    # ------------------------------------------------------------------ the steps (each returns the engine's job)
    def import_step(self, path: str, scale: float = 1.0):
        return self.project().import_step(path, scale=scale)

    def generate_mesh(self):
        return self.project().generate_mesh()

    def solve(self):
        return self.project().solve()

    def set_mesh_preset(self, preset: str):
        self.project().set_mesh(preset=preset)
        self.changed.emit()

    # ------------------------------------------------------------------ what the views draw
    def mesh_payload(self, axis: str | None, frac: float, color: str, poor: float | None = None):
        pr = self.project()
        if poor is not None:                  # only the cells below this quality, on the CAD model
            return pr.mesh_view(None, None, False, float(poor), color)
        if axis:
            m = pr.load_mesh()
            ax = "xyz".index(axis)
            lo, hi = float(m["nodes"][:, ax].min()), float(m["nodes"][:, ax].max())
            return pr.mesh_view(axis, lo + (hi - lo) * frac, False, None, color)
        return pr.mesh_view(None, None, False, None, color)

    def result_fields(self) -> list:
        return self.project().results().field_info()

    # ------------------------------------------------------------------ topology optimisation: the shape above a density threshold
    def topopt_threshold(self) -> float:
        return float(self.__dict__.setdefault("_topo_thr", {}).get(self.pid, 0.5))

    def set_topopt_threshold(self, v: float):
        self.__dict__.setdefault("_topo_thr", {})[self.pid] = min(max(float(v), 0.05), 0.95)

    def _shape(self, rs):
        """The result set reduced to the elements above the density threshold (cached for the threshold), so that surface, cut and colours draw the shape."""
        from ..topopt import view as tview
        key = (id(rs), round(self.topopt_threshold(), 3))
        c = self.__dict__.get("_shape_cache")
        if c and c[0] == key:
            return c[1]
        keep = rs.fields["Density"][1] >= self.topopt_threshold()
        out = tview.subset(rs, keep) if keep.any() else rs
        self._shape_cache = (key, out)
        return out

    def topopt_shown(self) -> dict:
        """What the threshold shows: the share of the part's mass above it (and in kg), from the saved density."""
        from ..topopt import view as tview
        pr = self.project()
        rs = pr.results()
        key = (id(rs), "vol")
        c = self.__dict__.get("_topo_vol")
        if not c or c[0] != key:
            c = self.__dict__["_topo_vol"] = (key, tview.volumes(rs.nodes, rs.tets))
        v, d = c[1], rs.fields["Density"][1]
        share = float(v[d >= self.topopt_threshold()].sum() / v.sum())
        kg = ((self._flow_summary().get("topopt") or {}).get("mass_kg_solid") or 0.0) * share
        return {"share": share, "kg": kg, "threshold": self.topopt_threshold()}

    def topopt_export_stl(self, path: str, smooth: bool = True) -> dict:
        """Write the shape at the threshold as a binary STL (in millimetres); the faces the optimiser made are smoothed, the part's own faces are left exactly as they are."""
        from ..topopt import view as tview
        rs = self.project().results()
        sub = self._shape(rs)
        v, t = tview.shape_surface(sub, rs, smooth=smooth)
        n = tview.write_stl(path, v, t, name=self.project().data.get("name") or "shape")
        return {"path": str(path), "triangles": int(n), "threshold": self.topopt_threshold(), "smoothed": bool(smooth)}

    def topopt_live_payload(self):
        """The shape the running (or last) optimisation has now, as a surface payload; None when there is none."""
        from ..topopt import view as tview
        pr = self.project()
        live = pr.topopt_live()
        if not live or live.get("density") is None:
            return None
        c = self.__dict__.get("_topo_mesh")
        if not c or c[0] != self.pid:
            m = pr.load_mesh()
            c = self.__dict__["_topo_mesh"] = (self.pid, m["nodes"], m["tets"].astype(np.int64))
        d = live["density"]
        if len(d) != len(c[2]):
            return None
        base = tview.live_base(c[1], c[2], d)
        keep = d >= self.topopt_threshold()
        return tview.subset(base, keep).surface("Density") if keep.any() else None

    def result_surface(self, field: str, time_index: int = -1, section=None, hide=None, groups=None):
        """The result field on the part's surface; with section=(axis 'x'|'y'|'z', fraction 0..1 of the part's extent) the part is cut there
        and the cells on the low side are shown with the cut faces open (the inside of the part). hide: result regions (parts) not drawn
        (None: the study's own choice - the air of a cooling study is hidden). groups: the surface groups of a flow result to draw (None: the study's own
        choice; [] none)."""
        rs = self.project().results().at(time_index)
        if self.project().study == "topology_optimization":
            rs = self._shape(rs)
        flow = self.project().study in FLOW_STUDIES
        box = self._flow_box(rs) if flow else None            # (a flow result is looked at close to the object, not across the whole far field)
        pos = ax = None
        if section:
            ax, pos = self._section_pos(rs, section, box)
        if flow:
            return self._flow_surface(rs, field, ax, pos, box, hide, groups)
        hide = tuple(hide or ())
        return rs.surface(field, hide_regions=hide) if ax is None else rs.clip(field, ax, pos, hide_regions=hide)

    @staticmethod
    def _section_pos(rs, section, box):
        """(axis index, position) of a section given as (axis 'x'|'y'|'z', fraction 0..1 of the box, or of the whole result without one)."""
        axis, frac = section
        ax = "xyz".index(axis)
        lo, hi = (float(box[ax]), float(box[3 + ax])) if box else (float(rs.nodes[:, ax].min()), float(rs.nodes[:, ax].max()))
        return ax, lo + min(max(float(frac), 0.0), 1.0) * (hi - lo)

    def _flow_summary(self) -> dict:
        return ((self.state() or {}).get("solve") or {}).get("summary") or {}

    def _flow_box(self, rs, around=9.0, downstream=12.0, axis=None, at=None):
        """[x0, y0, z0, x1, y1, z1] around the object of an external flow (9 body sizes each way, 21 downstream; the camera looks closer): where the flow is worth
        looking at. None when the study has no object cut out of the air (a cooling duct is looked at whole)."""
        s = self._flow_summary()
        groups = [g for g in (s.get("default_groups") or []) if g in rs.groups]
        if not groups:
            return None
        m = np.isin(rs.surf_group, [rs.groups.index(g) for g in groups])
        pts = rs.nodes[np.unique(rs.surf_tris[m].ravel())]
        if not len(pts):
            return None
        lo, hi = pts.min(0), pts.max(0)
        if axis is not None and at is not None:
            # the part of the object near the cut (a swept or tapered wing is much longer fore and aft over its whole span than its chord
            # at the cut): the points in a thin slab round the section, when there are enough of them
            ax = "xyz".index(axis)
            slab = max(0.02 * float(hi[ax] - lo[ax]), 1e-9)
            near = pts[np.abs(pts[:, ax] - at) <= slab]
            if len(near) >= 3:                                # (enough to measure the section in its plane)
                keep_lo, keep_hi = lo[ax], hi[ax]
                lo, hi = near.min(0), near.max(0)
                lo[ax], hi[ax] = keep_lo, keep_hi
        ext = hi - lo
        # the size the margins are measured in: the object's largest extent; for a cut, its largest extent IN the cut plane (a slender wing cut
        # across its span is one chord long there, not one span: sized by the span, the section would be a speck in the picture)
        size = float((ext if axis is None else np.delete(ext, "xyz".index(axis))).max()) or float(ext.max()) or 1.0
        lo, hi = lo - around * size, hi + around * size
        u = np.asarray(s.get("U_inf") or [0, 0, 0], float)
        if np.linalg.norm(u):
            d = int(np.argmax(np.abs(u)))
            if u[d] > 0:
                hi[d] += downstream * size
            else:
                lo[d] -= downstream * size
        dom_lo, dom_hi = rs.nodes.min(0), rs.nodes.max(0)
        return np.concatenate([np.maximum(lo, dom_lo), np.minimum(hi, dom_hi)]).tolist()

    def flow_focus(self, section=None):
        """The region the camera looks at when an external flow is cut open along axis: close around the object, measured in its size within
        the cut plane (the plane drawn reaches further, so that the large cells at its edge fall outside the picture)."""
        if self.project().study not in FLOW_STUDIES:
            return None
        rs = self.project().results()
        if not section:
            return self._flow_box(rs, 0.3, 0.8)
        ax, pos = self._section_pos(rs, section, self._flow_box(rs))
        return self._flow_box(rs, 0.3, 0.8, section[0], pos)

    def flow_focus_at(self, axis: str, pos: float):
        """The camera's box for a contour plane across axis at pos (metres): close round the object, in its size at the plane."""
        if self.project().study not in FLOW_STUDIES:
            return None
        return self._flow_box(self.project().results(), 0.3, 0.8, axis, pos)

    def _flow_surface(self, rs, field: str, ax, pos, box, hide=None, groups=None):
        """A flow or cooling result: the outside of the air box would hide everything, so draw the object (what the solver says to show:
        the body of an aircraft, the solid parts of a cooled assembly); with a section, the field on the plane through the air as well,
        and only the half of the object on the low side, cut open at the plane."""
        s = self._flow_summary()
        groups = (s.get("default_groups") or None) if groups is None else list(groups)
        hide = tuple(s.get("default_hidden_regions") or ()) if hide is None else tuple(hide)
        obj = rs.surface(field, groups=groups, hide_regions=hide, below=None if ax is None else (ax, pos))
        if ax is None:
            return obj
        plane = resample.plane_payload(rs, field, ax, pos, box, 0) if box else rs.slice(field, ax, pos)
        return merge_payloads(plane, obj)

    # ------------------------------------------------------------------ flow tools: vortex surfaces, volume, arrows, particle paths
    def flow_object(self, field: str, time_index: int = -1, cut=None, groups=None):
        """The object (or the solid parts) of a flow result coloured by a field: whole, or with cut=(axis index, position) only the part on the
        low side of that plane, cut open there. groups: the surface groups to draw (None: the study's own choice)."""
        rs = self.project().results().at(time_index)
        s = self._flow_summary()
        return rs.surface(field, groups=(s.get("default_groups") or None) if groups is None else list(groups),
                          hide_regions=tuple(s.get("default_hidden_regions") or ()), below=cut)

    def seed_disk_default(self) -> dict | None:
        """Where a seed disk for the particle paths starts: across the flow, just upstream of the object (its centre in m, its radius in m)."""
        if self.project().study not in FLOW_STUDIES:
            return None
        rs = self.project().results()
        box = self._flow_box(rs, 0.3, 0.8)
        if not box:
            return None
        b = np.asarray(box, float)
        u = np.asarray(self._flow_summary().get("U_inf") or [1.0, 0.0, 0.0], float)
        ax = int(np.argmax(np.abs(u)))
        c = (b[:3] + b[3:]) / 2
        c[ax] = b[ax] if u[ax] > 0 else b[3 + ax]
        across = [b[3 + i] - b[i] for i in range(3) if i != ax]
        return {"center": c.tolist(), "radius": 0.2 * min(across)}

    def smooth_iso(self, payload: dict, strength: float):
        """(the vortex-surface payload with its vertices smoothed for display, stats): a display filter on the surface that keeps its size
        (simwb.surfsmooth), kept for the same payload and strength so that moving the slider back and forth is quick."""
        from ..surfsmooth import smooth_payload
        cache = self.__dict__.setdefault("_smooth_cache", {})
        key = (id(payload), round(float(strength)))
        hit = cache.get(key)
        if hit and hit[0] is payload:
            return hit[1], hit[2]
        out, stats = smooth_payload(payload, strength)
        if len(cache) > 8:
            cache.clear()
        cache[key] = (payload, out, stats)
        return out, stats

    def flow_defaults(self) -> dict:
        """What the Flow tab starts from: the keep-clear-of-walls distance of the vortex surfaces (mm, 2 % of the reference length) and
        the free-stream speed."""
        s = self._flow_summary()
        L = (s.get("coefficients") or {}).get("L_ref_m")
        return {"gap_mm": float(f"{L * 20:.2g}") if L else 0.0, "free_stream": float(s.get("speed_m_s") or 0.0)}

    def flow_layers(self, tools: dict, time_index: int = -1) -> dict:
        """The payloads of the flow tools that are on. tools: {region 'object'|'all', res, iso {on, k, color, gap (mm), smooth}, volume {on, field},
        vectors {on, axis, frac, n}, streams {on, n, seed 'upstream'|'everywhere', steps}}. A region is a box: around the object, or the
        whole air. Results are kept for the same request."""
        pr = self.project()
        rs = pr.results().at(time_index)
        s = self._flow_summary()
        hide = ()          # (the regions hidden for the surface picture are the air of a cooling study: the samplers need it)
        walls = list(s.get("default_groups") or [])
        domain = np.concatenate([rs.nodes.min(0), rs.nodes.max(0)]).tolist()
        box = (self._flow_box(rs, 5.0, 12.0) if tools.get("region", "object") == "object" else None) or domain
        res = int(tools.get("res", 96))
        base = (self.pid, id(rs), tuple(np.round(box, 6)), res)
        cache = self.__dict__.setdefault("_flow_cache", {})

        def memo(key, fn):
            k = base + key
            if k not in cache:
                if len(cache) > 10:
                    cache.clear()
                cache[k] = fn()
            return cache[k]
        out = {"box": box, "free_stream": float(s.get("speed_m_s") or 0.0)}
        iso = tools.get("iso") or {}
        if iso.get("on") and "Q-criterion" in rs.fields:
            qmax = memo(("qmax",), lambda: max(f["max"] for f in rs.field_info() if f["name"] == "Q-criterion"))
            level = qmax * 10 ** (-float(iso.get("k", 48)) / 10.0)
            cf = iso.get("color") if iso.get("color") in rs.fields else None
            gap, sm = float(iso.get("gap", 0.0)), int(iso.get("smooth", 0))
            out["iso"] = memo(("iso", round(level, 12), cf, gap, sm), lambda: rs.iso(
                "Q-criterion", level, cf, hide_regions=hide, wall_groups=walls, wall_gap=max(0.0, gap) / 1000.0, smooth=min(max(sm, 0), 3)))
            out["iso_level"] = level
        vol = tools.get("volume") or {}
        if vol.get("on") and vol.get("field") in rs.fields:
            out["volume"] = memo(("vol", vol["field"]), lambda: resample.volume_payload(rs, vol["field"], box, res, hide))
        vec = tools.get("vectors") or {}
        if vec.get("on") and "Velocity" in rs.fields:
            ax = "xyz".index(vec.get("axis", "y"))
            pos = box[ax] + min(max(float(vec.get("frac", 0.5)), 0.0), 1.0) * (box[3 + ax] - box[ax])
            n = int(vec.get("n", 24))
            out["vectors"] = memo(("vec", ax, round(pos, 6), n), lambda: resample.vector_payload(rs, ax, pos, box, n, "Velocity", hide))
        planes = []
        for pl in tools.get("planes") or []:
            f = pl.get("field")
            if f not in rs.fields or pl.get("axis", "y") not in ("x", "y", "z"):
                continue
            ax = "xyz".index(pl.get("axis", "y"))
            pos = box[ax] + min(max(float(pl.get("frac", 0.5)), 0.0), 1.0) * (box[3 + ax] - box[ax])
            pay = memo(("plane", f, ax, round(pos, 9)), lambda f=f, ax=ax, pos=pos: resample.plane_payload(rs, f, ax, pos, box, 0))
            planes.append({"payload": pay, "field": f, "axis": pl.get("axis", "y"), "pos": pos, "range": pl.get("range"),
                           "opacity": min(max(float(pl.get("opacity", 1.0)), 0.1), 1.0)})
        if planes:
            out["planes"] = planes
        st = tools.get("streams") or {}
        if st.get("on") and "Velocity" in rs.fields:
            u = np.asarray(s.get("U_inf") or [1.0, 0.0, 0.0], float)
            seed = st.get("seed") if st.get("seed") in ("everywhere", "disk") else "upstream"
            disk = st.get("disk") if seed == "disk" else None
            seed_box = None
            if disk:                                    # a disk you placed: a box across the flow, thin along it (the paths start on its inscribed disk)
                c, r = np.asarray(disk["center"], float), float(disk["radius"])
                ax = int(np.argmax(np.abs(u)))
                half = np.full(3, r)
                half[ax] = 1e-6
                seed_box = np.concatenate([c - half, c + half]).tolist()
            elif seed == "disk":
                seed = "upstream"                       # (no disk placed yet: as before)
            out["paths"] = memo(("par", int(st.get("n", 800)), int(st.get("steps", 160)), seed, tuple(np.round(u, 6)),
                                 None if seed_box is None else tuple(np.round(seed_box, 6))),
                                lambda: resample.particle_paths(rs, box, min(res, 96), int(st.get("n", 800)), int(st.get("steps", 160)),
                                                                "box" if seed_box else seed, (u / (np.linalg.norm(u) or 1.0)).tolist(), hide_regions=hide,
                                                                seed_box=seed_box))
        return out

    def summary_text(self) -> str:
        return self.project().summary_text()
