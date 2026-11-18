"""Result sets shared by all solvers: tet mesh + fields, with surface and clip extraction for display."""
from __future__ import annotations

import base64
from pathlib import Path

import numpy as np

LOC = ("node", "cell")


class ResultSet:
    """nodes (N,3), tets (M,4), tet_region (M), regions [names]
    fields: {name: (loc, data(N|M,) or (N|M,3), unit)}
    surf_tris (S,3), surf_cell (S) owning tet, surf_group (S) index into groups, groups [names]
    surf_values: optional {field: (S,)} boundary face values (cell-located fields)
    times, frames: transient results - frames {field: [array per time]}; fields hold the last time
    """

    def __init__(self, nodes, tets, tet_region, regions, fields, surf_tris, surf_cell, surf_group, groups,
                 surf_values=None, times=None, frames=None, frame_map=None):
        self.nodes = np.asarray(nodes, float)
        self.tets = np.asarray(tets, np.int64)
        self.tet_region = np.asarray(tet_region, np.int32)
        self.regions = list(regions)
        self.fields = fields
        self.surf_tris = np.asarray(surf_tris, np.int64)
        self.surf_cell = np.asarray(surf_cell, np.int64)
        self.surf_group = np.asarray(surf_group, np.int32)
        self.groups = list(groups)
        self.surf_values = surf_values or {}
        self.times = list(times or [])
        self.frames = frames or {}
        # frames of cell fields may be stored per mesh cell (prism layers: a cell is several display tets):
        # frame_map[tet] = cell, expanded only for the frame shown (37 frames x 1.5M tets used 1.5 GB)
        self.frame_map = None if frame_map is None else np.asarray(frame_map, np.int64)

    def at(self, i: int) -> "ResultSet":
        """The result set at saved time index i (transient results; -1 or steady: unchanged)."""
        if not self.times or i is None or not 0 <= i < len(self.times):
            return self
        f, sv = dict(self.fields), dict(self.surf_values)
        for k, arrs in self.frames.items():
            if k in f:
                a = arrs[i]
                if self.frame_map is not None and len(a) != len(self.tets):
                    a = a[self.frame_map]
                f[k] = (f[k][0], a, f[k][2])
            elif k.startswith("s:") and k[2:] in sv:  # boundary-face values of a cell field
                sv[k[2:]] = arrs[i]
        nodes = self.frames["__nodes"][i] if "__nodes" in self.frames else self.nodes  # (moving mesh)
        return ResultSet(nodes, self.tets, self.tet_region, self.regions, f, self.surf_tris, self.surf_cell,
                         self.surf_group, self.groups, sv)

    # --- persistence ---------------------------------------------------------
    def save(self, path: Path):
        d = dict(nodes=self.nodes, tets=self.tets, tet_region=self.tet_region, surf_tris=self.surf_tris,
                 surf_cell=self.surf_cell, surf_group=self.surf_group,
                 regions=np.array(self.regions, dtype=object), groups=np.array(self.groups, dtype=object))
        meta = {}
        for k, (loc, arr, unit) in self.fields.items():
            d["f_" + k] = arr
            meta[k] = (loc, unit)
        for k, arr in self.surf_values.items():
            d["s_" + k] = arr
        if self.times:
            d["times"] = np.asarray(self.times, float)
            d["frame_names"] = np.array(list(self.frames), dtype=object)
            if self.frame_map is not None:
                d["frame_map"] = self.frame_map.astype(np.int32)
            for j, (k, arrs) in enumerate(self.frames.items()):
                d[f"t{j}"] = np.stack(arrs).astype(np.float32)
        d["meta"] = np.array([repr(meta)], dtype=object)
        np.savez_compressed(path, **d)

    @classmethod
    def load(cls, path: Path):
        z = np.load(path, allow_pickle=True)
        import ast

        meta = ast.literal_eval(str(z["meta"][0]))
        fields = {k: (loc, z["f_" + k], unit) for k, (loc, unit) in meta.items()}
        sv = {k[2:]: z[k] for k in z.files if k.startswith("s_")}
        times, frames = None, None
        if "times" in z.files:
            times = z["times"].tolist()
            frames = {str(k): list(z[f"t{j}"]) for j, k in enumerate(z["frame_names"])}
        return cls(z["nodes"], z["tets"], z["tet_region"], list(z["regions"]), fields, z["surf_tris"],
                   z["surf_cell"], z["surf_group"], list(z["groups"]), sv, times, frames,
                   z["frame_map"] if "frame_map" in z.files else None)

    # --- extraction ----------------------------------------------------------
    def field_info(self):
        out = []
        for k, (loc, arr, unit) in self.fields.items():
            a = self._scalar(np.concatenate(self.frames[k]) if k in self.frames else arr)  # (over all times)
            fin = a[np.isfinite(a)]
            if not len(fin) and k in self.surf_values:  # wall-only quantity (y+): range from the wall values
                sv = self._scalar(self.surf_values[k])
                fin = sv[np.isfinite(sv)]
            out.append({"name": k, "loc": loc, "unit": unit, "min": float(fin.min()) if len(fin) else 0.0,
                        "max": float(fin.max()) if len(fin) else 0.0})
            now = self._scalar(arr)
            now = now[np.isfinite(now)]
            if len(now):  # the range that holds nearly all the flow (extremes sit on walls and blade tips)
                out[-1]["p01"], out[-1]["p99"] = (float(v) for v in np.percentile(now[:: max(1, len(now) // 400000)], [1, 99]))
            if k in self.frames and len(fin):
                # how much the values change over the saved times, as a fraction of the colour range: 99th
                # percentile over the points (a few cells in a wake should not hide a still picture)
                fr = np.array([self._scalar(f) for f in self.frames[k]], float)
                span = max(float(fin.max() - fin.min()), 1e-30)
                d = np.nan_to_num(np.abs(fr - fr[0]).max(0))
                out[-1]["change_over_time"] = float(np.percentile(d, 99)) / span
        return out

    @staticmethod
    def _scalar(a):
        a = np.asarray(a, float)
        return np.linalg.norm(a, axis=1) if a.ndim == 2 else a

    def _tri_payload(self, tris, cells, field, face_vals=None, hide_regions=(), smooth=False):
        if field not in self.fields:
            raise ValueError(f"Unknown field '{field}'. Available: {', '.join(self.fields)}")
        loc, arr, unit = self.fields[field]
        a = self._scalar(arr)
        if hide_regions:
            keep = ~np.isin(self.tet_region[cells], [self.regions.index(r) for r in hide_regions if r in self.regions])
            tris, cells = tris[keep], cells[keep]
            if face_vals is not None:
                face_vals = face_vals[keep]
        used, inv = np.unique(tris.ravel(), return_inverse=True)
        pos = self.nodes[used].astype(np.float32)
        idx = inv.reshape(-1, 3).astype(np.uint32)
        if loc == "node":
            vals = a[used].astype(np.float32)
            per = "vertex"
        elif smooth:
            # cell/face values averaged to the surface vertices (area weighted) - avoids a per-triangle checkerboard
            fv = (face_vals if face_vals is not None else a[cells]).astype(float)
            p_ = self.nodes
            ar = 0.5 * np.linalg.norm(np.cross(p_[tris[:, 1]] - p_[tris[:, 0]], p_[tris[:, 2]] - p_[tris[:, 0]]), axis=1)
            ok = np.isfinite(fv)
            sm = np.zeros(len(used))
            wt = np.zeros(len(used))
            ii = idx.astype(np.int64)
            np.add.at(sm, ii[ok].ravel(), np.repeat(fv[ok] * ar[ok], 3))
            np.add.at(wt, ii[ok].ravel(), np.repeat(ar[ok], 3))
            with np.errstate(invalid="ignore", divide="ignore"):
                vals = np.where(wt > 0, sm / wt, np.nan).astype(np.float32)
            per = "vertex"
        else:
            vals = (face_vals if face_vals is not None else a[cells]).astype(np.float32)
            per = "face"
        fin = vals[np.isfinite(vals)]
        out = {  # NaN values (field undefined in that region, e.g. velocity in a solid) are drawn grey
            "positions": _b64(pos), "indices": _b64(idx), "values": _b64(vals), "per": per,
            "n_tris": int(len(idx)), "min": float(fin.min()) if len(fin) else 0.0,
            "max": float(fin.max()) if len(fin) else 0.0, "unit": unit,
        }
        if loc == "node" and np.ndim(arr) == 2 and unit in ("relative", "mm"):  # mode shape / deformation
            out["disp"] = _b64(np.asarray(arr)[used].astype(np.float32))
        return out

    def surface(self, field, groups=None, hide_regions=()):
        m = np.ones(len(self.surf_tris), bool)
        if groups:
            m = np.isin(self.surf_group, [self.groups.index(g) for g in groups if g in self.groups])
        fv = self.surf_values.get(field)
        return self._tri_payload(self.surf_tris[m], self.surf_cell[m], field,
                                 None if fv is None else fv[m], hide_regions, smooth=True)

    def nodal(self, field):
        """Field values at the nodes (cell values averaged to the nodes, ignoring NaN regions)."""
        loc, arr, unit = self.fields[field]
        a = self._scalar(arr)
        if loc == "node":
            return a
        return self._to_nodes(a)

    def _to_nodes(self, a):
        """Cell values averaged to the nodes (NaN cells left out)."""
        ok = np.isfinite(a)
        t = self.tets[ok].ravel()
        s = np.bincount(t, np.repeat(a[ok], 4), minlength=len(self.nodes))
        n = np.bincount(t, minlength=len(self.nodes))
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(n > 0, s / n, np.nan)

    def slice(self, field, axis: int, pos: float, hide_regions=()):
        """True planar cut: every tet crossing the plane is cut exactly and values interpolated linearly."""
        if field not in self.fields:
            raise ValueError(f"Unknown field '{field}'. Available: {', '.join(self.fields)}")
        loc, _, unit = self.fields[field]
        val = self.nodal(field)
        t = self.tets
        if hide_regions:
            hid = [self.regions.index(r) for r in hide_regions if r in self.regions]
            t = t[~np.isin(self.tet_region, hid)]
        d = self.nodes[:, axis] - pos
        if (np.abs(d) < 1e-6).any():
            # the plane lies on nodes of the mesh (a symmetry plane, or a position on a refinement block's grid): the
            # cells flat on it would not be cut and the picture had block-shaped holes. Move it 0.2 mm into the mesh.
            d = d - 2e-4 if d.max() > 1e-6 else d + 2e-4   # (into the mesh: not past its last face)
        return self._contour(d, val, t, unit)

    def iso(self, iso_field, level: float, color_field=None, hide_regions=(), wall_groups=(), wall_gap=0.0,
            smooth=0):
        """Isosurface iso_field = level (e.g. Q-criterion > 0: vortex tubes), coloured by color_field. Pieces closer
        than wall_gap to the surfaces in wall_groups are left out: the shear in the boundary layer makes Q large
        there too and would wrap the walls in a skin that hides the vortices shed into the flow. smooth: passes of
        averaging over the neighbouring cells before contouring."""
        for f in (iso_field, color_field or iso_field):
            if f not in self.fields:
                raise ValueError(f"Unknown field '{f}'. Available: {', '.join(self.fields)}")
        color_field = color_field or iso_field
        t = self.tets
        if hide_regions:
            hid = [self.regions.index(r) for r in hide_regions if r in self.regions]
            t = t[~np.isin(self.tet_region, hid)]
        q = self.nodal(iso_field)
        for _ in range(smooth):  # node -> cell -> node averaging: rounds off single-cell lumps of a cell field
            q = self._to_nodes(q[self.tets].mean(1))
        d = q - float(level)
        walls = None
        if wall_gap > 0 and wall_groups:
            gi = [self.groups.index(g) for g in wall_groups if g in self.groups]
            wn = np.unique(self.surf_tris[np.isin(self.surf_group, gi)])
            if len(wn):
                walls = (self.nodes[wn], float(wall_gap))
        return self._contour(np.nan_to_num(d, nan=-1e30), self.nodal(color_field), t, self.fields[color_field][2],
                             walls=walls)

    def _contour(self, d, val, t, unit, walls=None):
        """Triangles where the node function d crosses zero inside the tets t (marching tetrahedra), with val
        interpolated to their corners. Corners on the same tet edge are shared (indexed mesh). walls: (points,
        gap) - triangles with their centre nearer than gap to a wall point are dropped."""
        dt = d[t]
        neg = dt < 0
        cnt = neg.sum(1)
        cross = (cnt > 0) & (cnt < 4)
        t, dt, neg, cnt = t[cross], dt[cross], neg[cross], cnt[cross]
        order = np.argsort(~neg, axis=1, kind="stable")  # negative-side nodes first
        ts = np.take_along_axis(t, order, 1)
        E = []  # per triangle: its three cut edges as (node a, node b), a on the negative side

        def tri(rows, e0, e1, e2):
            E.append(np.stack([np.stack([ts[rows, i], ts[rows, j]], 1) for i, j in (e0, e1, e2)], 1))

        r1, r2, r3 = np.where(cnt == 1)[0], np.where(cnt == 2)[0], np.where(cnt == 3)[0]
        tri(r1, (0, 1), (0, 2), (0, 3))
        tri(r3, (0, 3), (1, 3), (2, 3))
        tri(r2, (0, 2), (0, 3), (1, 3))  # quad 02-03-13-12 as two triangles
        tri(r2, (0, 2), (1, 3), (1, 2))
        E = np.concatenate(E).reshape(-1, 2) if len(t) else np.zeros((0, 2), np.int64)
        n = np.int64(len(self.nodes))
        keys, idx = np.unique(E[:, 0] * n + E[:, 1], return_inverse=True)  # one vertex per cut edge
        idx = idx.ravel()
        a, b = keys // n, keys % n
        w = (d[a] / (d[a] - d[b]))[:, None]
        pos_ = self.nodes[a] + (self.nodes[b] - self.nodes[a]) * w
        vals = val[a] + (val[b] - val[a]) * w[:, 0]
        tris = idx.reshape(-1, 3)
        if walls is not None and len(tris):
            from scipy.spatial import cKDTree
            near = cKDTree(walls[0]).query(pos_[tris].mean(1), distance_upper_bound=walls[1])[0] < walls[1]
            tris = tris[~near]
            used, inv = np.unique(tris.ravel(), return_inverse=True)
            pos_, vals, tris = pos_[used], vals[used], inv.reshape(-1, 3)
        pos_ = pos_.astype(np.float32)
        vals = np.asarray(vals, np.float32)
        fin = vals[np.isfinite(vals)]
        return {"positions": _b64(pos_), "indices": _b64(tris.ravel().astype(np.uint32)), "values": _b64(vals),
                "per": "vertex", "n_tris": int(len(tris)), "min": float(fin.min()) if len(fin) else 0.0,
                "max": float(fin.max()) if len(fin) else 0.0, "unit": unit}

    def clip(self, field, axis: int, pos: float, keep_below=True, hide_regions=()):
        """Crinkle clip: all tets whose centroid is on one side of the plane, shown by their outer faces."""
        cen = self.nodes[self.tets].mean(1)[:, axis]
        sel = np.where(cen <= pos if keep_below else cen >= pos)[0]
        if hide_regions:
            hid = [self.regions.index(r) for r in hide_regions if r in self.regions]
            sel = sel[~np.isin(self.tet_region[sel], hid)]
        if not len(sel):
            return self._tri_payload(np.zeros((0, 3), np.int64), np.zeros(0, np.int64), field)
        loc = np.array([[1, 2, 3], [0, 3, 2], [0, 1, 3], [0, 2, 1]])
        F = self.tets[sel][:, loc].reshape(-1, 3)
        cells = np.repeat(sel, 4)
        key = np.sort(F, axis=1)
        _, inv, cnt = np.unique(key, axis=0, return_inverse=True, return_counts=True)
        once = cnt[inv.ravel()] == 1
        return self._tri_payload(F[once], cells[once], field)

    def summary_ranges(self):
        return {k: v for k, v in ((f["name"], [f["min"], f["max"]]) for f in self.field_info())}


def _b64(a: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(a).tobytes()).decode()


def boundary_from_tets(tets):
    """Exterior + inter-region faces of a tet mesh -> (tris, owning cell)."""
    loc = np.array([[1, 2, 3], [0, 3, 2], [0, 1, 3], [0, 2, 1]])
    F = tets[:, loc].reshape(-1, 3)
    cells = np.repeat(np.arange(len(tets)), 4)
    key = np.sort(F, axis=1)
    _, inv, cnt = np.unique(key, axis=0, return_inverse=True, return_counts=True)
    once = cnt[inv.ravel()] == 1
    return F[once], cells[once]
