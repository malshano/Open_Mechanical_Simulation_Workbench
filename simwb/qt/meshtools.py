"""The parts of the Mesh step that are not the advanced options: the statistics table, the mesh verdict in words (as the web version
judges a mesh: cell shape, cells across the thin parts and the gaps, the wall y+ of a flow, a coarse CFD mesh), the automatic refinement
regions of rotating zones (wake cylinders, tip-vortex rings), the cell budget (which cell sizes use the cells) and the mesh-sensitivity
check (one level finer, or the three-level grid-convergence index).

The functions at the top are plain Python (no Qt): they take the study's state and return words and numbers, so they can be tested
and reused without a window."""
from __future__ import annotations

import math

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .forms import fmt_num
from .ids import wid

SOLID_STUDIES = ("heat_conduction", "vibration_modal", "static_structural", "aeroelastic_flutter")
ZONE_CELLS = {"quick": 36, "coarse": 48, "medium": 64, "fine": 96, "very_fine": 128}   # (= gmsh_worker.ZONE_CELLS: cells round a zone)
RUN_COST = {"cht_enclosure": (5.1e-6, 1700), "external_aero": (6.2e-6, 500)}          # s per cell per iteration, iterations to auto-stop


def _f(v, d=3) -> str:
    """A number with d significant digits, as the web version writes it ('–' when there is none)."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "–"
    return fmt_num(float(f"{float(v):.{d}g}"))


def _f1(v) -> str:
    return "–" if v is None else f"{float(v):.1f}"


def _pct(v) -> str:
    """A share in percent with one decimal, never rounded up to 100 when it is not 100 (99.98 % reads 99.9 %)."""
    if v is None:
        return "–"
    v = float(v)
    return f"{math.floor(v * 10) / 10:.1f}" if v < 100 else "100"


def _nl(v) -> str:
    """A number of layers: whole when it is whole, else with one decimal (OpenFOAM reports the average over the faces: 4.6 of 5)."""
    if v is None:
        return "–"
    v = float(v)
    return str(int(round(v))) if abs(v - round(v)) < 0.05 else f"{v:.1f}"


# ---------------------------------------------------------------------------------------------------------------- statistics
def stats_rows(st: dict) -> list:
    """[(label, value)] of the mesh statistics table, in the web version's order."""
    m = st["mesh"]
    s = m.get("stats") or {}
    if not s:
        return []
    q, sz = s.get("quality") or {}, s.get("sizes") or {}
    rows = [("Cells", f"{s.get('cells', 0):,}"), ("Nodes", f"{s.get('nodes', 0):,}")]
    if sz.get("near_wall") is not None:
        rows += [("Near-wall size", f"{_f(sz['near_wall'] * 1000)} mm"), ("Bulk size", f"{_f(sz.get('body', 0) * 1000)} mm")]
    rows += [("Min / mean quality", f"{q.get('min', 0):.2f} / {q.get('mean', 0):.2f}"), ("Poor cells (<0.1)", f"{q.get('n_poor', 0):,}")]
    fq = s.get("foam_quality")
    if fq:
        rows += [("Max non-orthogonality", f"{_f1(fq.get('non_orthogonality_max'))}°"), ("Max skewness", f"{fq.get('skewness_max', 0):.2f}")]
    if s.get("defeatured_faces"):
        rows.append(("Defeatured faces", str(s["defeatured_faces"])))
    if s.get("method") == "hex" and s.get("cell_types"):
        ct, x = s["cell_types"], s.get("hex") or {}
        rows += [("Hexahedra", f"{100 * ct.get('hexahedra', 0) / max(s.get('cells', 1), 1):.0f} %"),
                 ("Prisms / polyhedra", f"{ct.get('prisms', 0):,} / {ct.get('polyhedra', 0):,}")]
        if x.get("aspect_ratio_max") is not None:
            rows.append(("Max aspect ratio", _f1(x["aspect_ratio_max"])))
        if x.get("layers_extruded_percent") is not None:
            rows.append(("Wall faces with layers", f"{_f1(x['layers_extruded_percent'])} %"))
    ly = s.get("layers") or {}
    if ly.get("made"):
        rows += [("Cells with layers", f"{ly['cells']:,} ({ly.get('layer_cells', 0):,} layer cells)"),
                 ("Layers made", f"{ly.get('layers_max', 0)} · {_pct(ly.get('extruded_percent'))} % of the wall faces"),
                 ("First / all layers", f"{_f(ly.get('first_mm'))} / {_f(ly.get('total_mm'))} mm")]
        if ly.get("non_orthogonality_max") is not None:
            rows += [("Max non-orthogonality (with layers)", f"{_f1(ly['non_orthogonality_max'])}°"),
                     ("Max skewness (with layers)", f"{(ly.get('skewness_max') or 0):.2f}")]
    if s.get("seconds") is not None:
        rows.append(("Time", f"{s['seconds']} s"))
    return rows


# ---------------------------------------------------------------------------------------------------------------- verdict
def _hex_notes(st: dict) -> tuple:
    s = st["mesh"]["stats"]
    f, x, layers = s.get("foam_quality") or {}, s["hex"], (st["mesh"].get("layers") or {})
    warn, info = [], []
    info.append(f"Surface cells {_f(x.get('surface_cell_mm'))} mm; background {_f(x.get('base_mm'))} mm ({x.get('levels', '?')} refinement levels).")
    if f.get("non_orthogonality_max", 0) > 70:
        warn.append(f"Max non-orthogonality {_f1(f['non_orthogonality_max'])}°: above 70° OpenFOAM may be unstable.")
    if f.get("skewness_max", 0) > 4:
        warn.append(f"Max skewness {f['skewness_max']:.2f} is above 4.")
    warn += [f"checkMesh: {t}" for t in x.get("problems") or []]
    pct = x.get("layers_extruded_percent")
    if pct is not None and layers.get("n") and pct < 50:
        warn.append(f"Only {_f1(pct)} % of the wall faces got layers: the parts are thin for these cells. Use a finer preset or refine the thin faces.")
    elif pct is not None and layers.get("n"):
        info.append(f"{_f1(pct)} % of the wall faces got layers.")
    real = [w for w in (x.get("warnings") or []) if "concave" not in w.lower()]
    info += [w for w in (x.get("warnings") or []) if "concave" in w.lower()]
    warn += real
    return ("warn" if warn else "ok"), warn, info


def mesh_notes(st: dict) -> tuple:
    """(tone 'ok'|'warn', [warnings], [information]) about the mesh of the study, as the web version's mesh verdict."""
    m, study = st["mesh"], st["study"]
    s = m.get("stats") or {}
    if not s:
        return "", [], []
    if s.get("method") == "hex" and s.get("hex"):
        return _hex_notes(st)
    q, f, sz = s.get("quality") or {}, s.get("foam_quality"), s.get("sizes") or {}
    warn, info = [], []
    if q.get("n_poor", 0) > 0:
        warn.append(f"{q['n_poor']} poorly shaped cells: try a finer preset (or show them with 'Show cells below').")
    if f and f.get("non_orthogonality_max", 0) > 75:
        warn.append("High non-orthogonality (above 75°): OpenFOAM may be unstable.")
    nw = sz.get("near_wall") or 0
    if sz.get("feature") and nw:
        across = sz["feature"] / nw
        info.append(f"Thinnest solid feature {_f(sz['feature'] * 1000)} mm ≈ {_f(across, 2)} cells across.")
        if study == "external_aero" and across < 2:
            warn.append("Thin parts have fewer than 2 cells across: use a finer preset.")
        if study == "heat_conduction" and across < 1.5:
            warn.append("Thin parts are about 1 cell thick: Medium or Fine is more accurate.")
        if study in ("vibration_modal", "static_structural", "aeroelastic_flutter") and across < 1.5:
            warn.append("Thin walls are about 1 element thick: check with Fine.")
    if sz.get("min_gap"):
        info.append(f"Narrowest air gap {_f(sz['min_gap'] * 1000)} mm ≈ {_f(sz.get('cells_across_gap'), 2)} cells across.")
        if study == "cht_enclosure" and sz.get("cells_across_gap") and sz["cells_across_gap"] < 6:
            warn.append(f"About {_f(sz['cells_across_gap'], 2)} cells across the air gaps: use at least 6 for final numbers.")
    ly = s.get("layers") or {}
    if study == "external_aero" and ly.get("made") and ly.get("first_mm"):
        se = st.get("settings") or {}
        V = max(float(se.get("speed") or 1), 1)
        L = float(((st.get("geometry") or {}).get("info") or {}).get("ref_length") or 0.1)
        from .. import materials
        fl = materials.FLUIDS.get(se.get("fluid") or "air") or materials.FLUIDS["air"]
        nu = fl["mu"] / fl["rho"]
        cf = 0.0576 * (V * L / nu) ** -0.2
        yp = 0.5 * ly["first_mm"] / 1000 * V * math.sqrt(cf / 2) / nu          # (the centre of a layer is half its thickness from the wall)
        info.append(f"Wall layers: {ly.get('layers_max', 0)} layers, the first {_f(ly['first_mm'])} mm thick: estimated wall y+ ≈ {_f(yp, 2)} "
                    f"(30–300 for wall functions, about 1 to resolve the wall).")
        warn += ly.get("warnings") or []
    elif study == "external_aero" and (ly.get("reason") and int((m.get("layers") or {}).get("n") or 0) > 0):
        info.append("Wall layers: " + ly["reason"])
    if study == "external_aero" and nw and not ly.get("made"):
        # a flat-plate estimate of the y+ of the first cell centre (tetrahedra: about 0.2 h from the wall)
        se = st.get("settings") or {}
        V = max(float(se.get("speed") or 1), 1)
        L = float(((st.get("geometry") or {}).get("info") or {}).get("ref_length") or 0.1)
        from .. import materials
        fl = materials.FLUIDS.get(se.get("fluid") or "air") or materials.FLUIDS["air"]
        nu = fl["mu"] / fl["rho"]
        Re = V * L / nu
        cf = 0.0576 * Re ** -0.2
        yp = 0.2 * nw * V * math.sqrt(cf / 2) / nu
        info.append(f"Estimated wall y+ ≈ {_f(yp, 2)} (30–300 ideal).")
        if yp > 500:
            warn.append("y+ is high: use a finer preset.")
    if study not in SOLID_STUDIES and s.get("cells", 0) < 60000:
        warn.append("Coarse for CFD: a first look only.")
    return ("warn" if warn else "ok"), warn, info


# ---------------------------------------------------------------------------------------------------------------- refinement regions
def region_cells(r: dict) -> tuple:
    """(estimated cells in the region, text, too_many) - tetrahedra of edge h take about 0.12 h^3."""
    if r.get("shape") == "cylinder":
        length = math.dist(r["start"], r["end"])
        vol = math.pi * (r["radius"] ** 2 - (r.get("inner_radius") or 0) ** 2) * length
        what = "ring" if r.get("inner_radius") else "cylinder"
    else:
        vol = math.prod(max(b - a, 0) for a, b in zip(r["min"], r["max"]))
        what = "box"
    n = vol / (0.12 * max(r["size"], 1e-9) ** 3)
    amount = _f(n / 1e6, 2) + " million" if n >= 1e6 else "{:,} thousand".format(round(n / 1e3))
    txt = f"≈ {amount} cells in this {what}"
    big = n > 3e6
    if big:
        txt += " - very slow to mesh and solve on a PC: use a larger cell size or a smaller region"
    return n, txt, big


def wake_regions(st: dict) -> list:
    """The regions with a wake cylinder behind every rotating zone (zone cell size, two diameters long; replaces earlier wakes)."""
    fd, zones = st.get("flow_dir"), st.get("moving_zones") or []
    if not zones or not fd:
        raise ValueError("Wake regions need rotating zones (Setup) and a flow direction")
    n = ZONE_CELLS.get(st["mesh"].get("preset"), 40)
    names = {f"{z['name']} wake" for z in zones}
    regs = [dict(r) for r in st["mesh"].get("regions") or [] if r.get("name") not in names]
    for z in zones:
        size = z.get("cell_size") or 2 * math.pi * z["radius"] / n
        regs.append({"name": f"{z['name']} wake", "shape": "cylinder", "start": list(z["center"]),
                     "end": [c + fd[k] * 4 * z["radius"] for k, c in enumerate(z["center"])], "radius": z["radius"], "size": size})
    return regs


def tip_rings(st: dict) -> list:
    """The regions with a ring of fine cells along the path of the blade-tip vortices of every rotating zone: 0.65 to 1.0 zone radius,
    from the zone's upstream face one radius downstream, cells of 1/50 of the radius (replaces earlier rings)."""
    fd, zones = st.get("flow_dir"), st.get("moving_zones") or []
    if not zones or not fd:
        raise ValueError("Tip-vortex rings need rotating zones (Setup) and a flow direction")
    names = {f"{z['name']} tip vortices" for z in zones}
    regs = [dict(r) for r in st["mesh"].get("regions") or [] if r.get("name") not in names]
    for z in zones:
        a = [c - fd[k] * (z.get("length") or 0) / 2 for k, c in enumerate(z["center"])]
        regs.append({"name": f"{z['name']} tip vortices", "shape": "cylinder", "start": a,
                     "end": [x + fd[k] * z["radius"] for k, x in enumerate(a)], "radius": z["radius"],
                     "inner_radius": 0.65 * z["radius"], "size": z["radius"] / 50})
    return regs


def layer_numbers(st: dict) -> dict | None:
    """What the wall-layer settings come to: {n, growth, first, total, delta, cells_across_delta, laminar} in metres, from the same plan the
    run uses (cases.layer_plan), or None when there are no layers or no flow to plan them for."""
    ly = (st["mesh"].get("layers") or {})
    n = int(ly.get("n") or 0)
    if st.get("study") != "external_aero" or n <= 0:
        return None
    se = st.get("settings") or {}
    V = float(se.get("speed") or 0)
    L = float(((st.get("geometry") or {}).get("info") or {}).get("ref_length") or 0)
    if V <= 0 or L <= 0:
        return None
    from .. import materials
    from ..solvers.openfoam import cases
    fl = materials.FLUIDS.get(se.get("fluid") or "air") or materials.FLUIDS["air"]
    try:
        return cases.layer_plan(V, L, fl["mu"] / fl["rho"], se.get("turbulence", "kOmegaSST"), n, float(ly.get("growth", 1.2)),
                                st.get("moving_zones") or [], ly.get("first"), ly.get("y_plus"))
    except Exception:  # noqa: BLE001 - the readout is a help: it never stops the panel
        return None


class AdvisorCard(QWidget):
    """The mesh advisor of a flow study: the smart-sizing switch, what the recipe asks for (and why), and, once there is a mesh, its scorecard."""
    smartToggled = Signal(bool)
    applyRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        self.smart = wid(QCheckBox("Smart sizing (recommended)"), "mesh.smart")
        self.smart.setToolTip("Size the mesh from the length of the body along the flow: wall cells, a refined near field and a refined wake that grow smoothly, "
                              "to what the free memory holds. The old sizing used the largest side of the body, which for a wing is the span.")
        self.smart.toggled.connect(self.smartToggled)
        self.why = wid(QLabel(""), "mesh.advisor.why")
        self.why.setWordWrap(True)
        self.why.setTextFormat(Qt.RichText)
        self.apply = wid(QPushButton("Use the recommended setup"), "mesh.advisor.apply")
        self.apply.setObjectName("ghostBorder")
        self.apply.setToolTip("Switch smart sizing on and set the wall layers that fit the thinnest part (then generate the mesh)")
        self.apply.clicked.connect(self.applyRequested)
        self.score = wid(QLabel(""), "mesh.advisor.score")
        self.score.setWordWrap(True)
        self.score.setTextFormat(Qt.RichText)
        self.table = wid(QLabel(""), "mesh.advisor.table")
        self.table.setWordWrap(True)
        self.table.setTextFormat(Qt.RichText)
        for w in (self.smart, self.why, self.apply, self.score, self.table):
            v.addWidget(w)

    def set_data(self, adv: dict, t: dict, smart: bool, mesh_ready: bool) -> None:
        mute, ok, warn, bad = t["muted"], t["ok_text"], t["warn_text"], t["bad_text"]
        self.smart.blockSignals(True)
        self.smart.setChecked(smart)
        self.smart.blockSignals(False)
        rec = adv["recipe"]
        a = adv["analysis"]
        mm = lambda v: _f(v * 1000, 3)  # noqa: E731
        txt = (f"Body {mm(a['length'])} mm long along the flow, {mm(a['span'])} mm across, thinnest part {mm(a['feature'])} mm. "
               f"The recipe: wall cells {mm(rec['h_surf'])} mm, near field {mm(rec['h_near'])} mm, wake {mm(rec['h_wake'])} mm, growth {_f(rec['growth'], 2)}"
               f" (about {rec['estimate']['cells_zones'] / 1000:,.0f}k cells in these zones).")
        lay = rec["layers"]
        if lay.get("n"):
            txt += f" Wall layers: {lay['n']} with the first {mm(lay['first_mm'] / 1000) if lay.get('first_mm') else '–'} mm (y+ about {_f(lay.get('y_plus'), 2)})."
        if rec["estimate"].get("reduced"):
            txt += f" <span style='color:{warn}'>Coarsened to fit the free memory.</span>"
        self.why.setText(txt if smart else f"<span style='color:{mute}'>Off: the old sizing is used.</span> " + txt)
        self.why.setToolTip("\n".join(rec["rationale"]))
        self.apply.setEnabled(True)
        sc = adv.get("scorecard")
        if not sc:
            self.score.setText("")
            self.table.setText("")
            self.score.setVisible(False)
            self.table.setVisible(False)
            return
        col = ok if sc["bad"] == 0 and sc["warn"] <= 2 else (warn if sc["bad"] == 0 else bad)
        self.score.setVisible(True)
        self.table.setVisible(True)
        self.score.setText(f"<span style='color:{col}'><b>Mesh score {sc['score']:.2f}</b></span> · {sc['ok']} checks ok, {sc['warn']} to look at, "
                           f"{sc['bad']} failed" + ("" if mesh_ready else " <span style='color:" + warn + "'>(the setup changed: generate the mesh again)</span>"))
        sym = {"ok": ("✓", ok), "warn": ("⚠", warn), "bad": ("✗", bad), "info": ("•", mute)}
        rows = []
        for c in sc["checks"]:
            s, k = sym[c["level"]]
            rows.append(f"<div style='margin-top:3px'><span style='color:{k}'><b>{s}</b></span> <b>{c['label']}</b>: {c['value']}"
                        + (f" <span style='color:{mute}'>(target: {c['target']})</span>" if c["target"] else "") + "</div>")
            if c["advice"] and c["level"] in ("warn", "bad"):
                rows.append(f"<div style='color:{k}; margin-left:14px'>{c['advice']}</div>")
        self.table.setText("".join(rows))


class LayerCard(QWidget):
    """What the layer step made on each surface (a table), whether it is out of date, and the buttons to make the layers again and to zoom to them."""
    makeRequested = Signal()
    zoomRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        self.state = wid(QLabel(""), "mesh.layers.state")
        self.state.setWordWrap(True)
        self.state.setTextFormat(Qt.RichText)
        self.table = wid(QLabel(""), "mesh.layers.table")
        self.table.setTextFormat(Qt.RichText)
        self.table.setWordWrap(True)
        self.notes = wid(QLabel(""), "mesh.layers.notes")
        self.notes.setWordWrap(True)
        self.notes.setTextFormat(Qt.RichText)
        row = QHBoxLayout()
        self.btn_make = wid(QPushButton("Make the layers"), "mesh.layers.make")
        self.btn_make.setObjectName("ghostBorder")
        self.btn_make.clicked.connect(self.makeRequested)
        self.btn_zoom = wid(QPushButton("Zoom to the layers"), "mesh.layers.zoom")
        self.btn_zoom.setObjectName("ghostBorder")
        self.btn_zoom.clicked.connect(self.zoomRequested)
        row.addWidget(self.btn_make)
        row.addWidget(self.btn_zoom)
        row.addStretch(1)
        for w in (self.state, self.table, self.notes):
            v.addWidget(w)
        v.addLayout(row)

    def set_data(self, st: dict, t: dict, busy: bool) -> None:
        s = (st["mesh"].get("stats") or {})
        ly = s.get("layers") or {}
        want = int((st["mesh"].get("layers") or {}).get("n") or 0) > 0
        made, stale = bool(ly.get("made")), bool(ly.get("stale"))
        warn, mute, ok = t["warn_text"], t["muted"], t["ok_text"]
        if made:
            tetra, lc = ly.get("cells_before") or ly["types"]["tetrahedra"], ly.get("layer_cells", 0)
            txt = (f"<span style='color:{ok}'><b>✓ {ly.get('layers_max', 0)} layers made</b></span> on {_pct(ly.get('extruded_percent'))} % of the wall faces. "
                   f"The solver runs this mesh: {ly['cells']:,} cells ({tetra:,} tetrahedra + {lc:,} layer cells).")
            if stale:
                txt = (f"<span style='color:{warn}'><b>⚠ The layer settings changed</b></span> since these layers were made: press "
                       f"<b>Make the layers again</b> to update what you see here and in the 3D view (the run uses the new settings either way).<br>" + txt)
        elif ly.get("reason"):
            txt = f"<span style='color:{warn}'><b>Layers not made here:</b></span> {ly['reason']}"
        elif want:
            txt = "The layers are added when the study runs. Press <b>Make the layers</b> to make them now and see them (OpenFOAM must be installed)."
        else:
            txt = "No wall layers are set (the number of layers is 0)."
        self.state.setText(txt)
        rows = ly.get("patches") or []
        if made and rows:
            self.table.setText("".join(
                f"<div><b>{r['name']}</b>: {_nl(r.get('made'))} of {r.get('target', '–')} layers · {_f(r.get('first_mm'))} → {_f(r.get('total_mm'))} mm · "
                f"<span style='color:{mute}'>{_f1(r.get('percent'))} % of the planned thickness</span></div>" for r in rows))
        else:
            self.table.setText("")
        self.table.setVisible(bool(self.table.text()))
        w = ly.get("warnings") or []
        self.notes.setText("".join(f"<div style='color:{warn}'>⚠ {x}</div>" for x in w))
        self.notes.setVisible(bool(w))
        ready = st["mesh"].get("status") in ("ready", "stale") and want
        self.btn_make.setVisible(ready or made)
        self.btn_make.setEnabled(bool(ready) and not busy)
        self.btn_make.setText("Make the layers again" if (made or ly.get("reason")) else "Make the layers")
        self.btn_make.setToolTip("Meshing is running" if busy else
                                 "Add the layers to the mesh again with the layer settings above (the mesh itself stays as it is)" if ready
                                 else "Set a number of layers first")
        self.btn_zoom.setVisible(made)
        self.btn_zoom.setToolTip("Turn the 3D view to a cut through the layers at the edge of the object and zoom in")


def fit_box(bbox: list) -> tuple:
    """(min, max) of a box round a part's bounding box [x0, y0, z0, x1, y1, z1], 15 % of its largest extent clear of it."""
    lo, hi = bbox[:3], bbox[3:]
    pad = 0.15 * max(h - l for l, h in zip(lo, hi))
    return [x - pad for x in lo], [x + pad for x in hi]


# ---------------------------------------------------------------------------------------------------------------- widgets
class StatsGrid(QWidget):
    """The statistics table: a muted label and a bold value per row, two pairs to a line (as the web version's grid)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.g = QGridLayout(self)
        self.g.setContentsMargins(0, 0, 0, 0)
        self.g.setHorizontalSpacing(10)
        self.g.setVerticalSpacing(2)
        self.rows: list = []
        self.g.setColumnStretch(1, 1)
        self.setToolTip("The numbers of the mesh: size, cell shape (quality, non-orthogonality, skewness) and, for hex-dominant meshes, "
                        "the cell types and the wall-layer coverage")

    def set_rows(self, rows: list, look: str = ""):
        if (rows, look) == (self.rows, getattr(self, "_look", "")):
            return
        self.rows, self._look = list(rows), look           # (rebuilt when the theme changes: the labels take its colours)
        while self.g.count():
            w = self.g.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        for i, (k, v) in enumerate(rows):
            lab = QLabel(k)
            lab.setObjectName("muted")
            lab.setWordWrap(True)                              # (a long label or value wraps: the table never makes the panel wider than its dock)
            val = wid(QLabel(f"<b>{v}</b>"), "mesh.stat." + "".join(c if c.isalnum() else "_" for c in k.lower()).strip("_"))
            val.setWordWrap(True)
            val.setProperty("api_text", f"{k}: {v}")
            from . import theme
            val.setStyleSheet(f"color: {theme.tokens()['ink']}; background: {theme.tokens()['panel']};")
            self.g.addWidget(lab, i, 0)
            self.g.addWidget(val, i, 1)
        self.setProperty("api_text", "; ".join(f"{k}: {v}" for k, v in rows))
        self.setVisible(bool(rows))


class Budget(QWidget):
    """The cell budget: for each size band the share of the cells and of the volume, as bars, and the findings in words."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(0, 0, 0, 0)
        self.v.setSpacing(2)
        self.data = None
        self.setToolTip("Which cell sizes use the cells (and so the memory and the run time): share of the cells / share of the volume")

    def set_data(self, d: dict | None, tokens: dict):
        self.data = d
        while self.v.count():
            w = self.v.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        if not d:
            self.setProperty("api_text", "")
            self.setVisible(False)
            return
        self.setVisible(True)
        head = QLabel("<b>Cell budget</b> (size band → share of cells / of the volume)")
        head.setWordWrap(True)                              # (a heading on one line made the whole panel wider than its dock)
        self.v.addWidget(head)
        mx = max([b["cells_percent"] for b in d.get("budget") or []] + [1])
        for b in d.get("budget") or []:
            row = QHBoxLayout()
            lab = QLabel(f"{_f(b['size_from'])}–{_f(b['size_to'])} mm")
            lab.setMinimumWidth(96)
            bar = QFrame()
            bar.setFixedHeight(8)
            bar.setFixedWidth(max(2, int(110 * b["cells_percent"] / mx)))
            bar.setStyleSheet(f"background: {tokens['accent']}; border-radius: 2px;")
            pct = QLabel(f"{b['cells_percent']:.1f} % / {b['volume_percent']:.1f} %")
            row.addWidget(lab)
            row.addWidget(bar)
            row.addStretch(1)
            row.addWidget(pct)
            w = QWidget()
            w.setLayout(row)
            row.setContentsMargins(0, 0, 0, 0)
            self.v.addWidget(w)
        for f in d.get("findings") or []:
            n = QLabel("⚠ " + f)
            n.setWordWrap(True)
            n.setStyleSheet(f"color: {tokens['warn_text']};")
            self.v.addWidget(n)
        self.setProperty("api_text", "; ".join(f"{b['size_from']}-{b['size_to']} mm: {b['cells_percent']} % cells" for b in d.get("budget") or [])
                         + (" | " + " | ".join(d.get("findings") or []) if d.get("findings") else ""))


def check_rows(mc: dict) -> list:
    """[(output, text)] of a mesh-sensitivity check, as the web version's table."""
    out = []
    for r in mc.get("rows") or []:
        t = f"{_f(r.get('coarse'))} → {_f(r.get('fine'))} {r.get('unit', '')} ({_f(r.get('change_percent'), 2)} %"
        if r.get("raw_change_percent") is not None and abs(r["raw_change_percent"] - (r.get("change_percent") or 0)) > 0.5:
            t += f"; {_f(r['raw_change_percent'], 2)} % of its own value"
        if r.get("resolved") is False:
            t += "; within the iteration error"
        t += ")"
        if r.get("fine2") is not None:
            t += f" → {_f(r['fine2'])} {r.get('unit', '')}"
            if r.get("convergence_type") == "oscillatory" and r.get("gci_percent") is not None:
                t += f" · oscillating: ±{_f(r['gci_percent'], 2)} % (half range)"
            elif r.get("gci_percent") is not None:
                t += f" · GCI {_f(r['gci_percent'], 2)} %, order {_f(r.get('order'), 2)}"
                if r.get("order_limited_to"):
                    t += f" (above the formal {r['order_limited_to']}: GCI uses {r['order_limited_to']})"
            else:
                t += f" · {r.get('convergence_type') or ''}"
        out.append((r.get("name", ""), t))
    return out


def check_state(st: dict, running: bool) -> dict:
    """What the mesh-sensitivity buttons can do now: {one: (enabled, why), three: (shown, enabled, why), note}."""
    sv = st["solve"]
    conv = ((sv.get("summary") or {}).get("convergence") or {})
    settled = sv.get("status") == "done" and conv.get("converged", True)
    preset = st["mesh"].get("preset")
    if running:
        why = "A check (or another job) is running for this study"
    elif sv.get("status") == "stale":
        why = "Not now: the setup changed since the results - run the simulation again first"
    elif sv.get("status") != "done":
        why = "Not yet: solve the study first (the check compares against its results)"
    elif not settled:
        why = "Not yet: the run has not converged - continue it first"
    else:
        why = ""
    ok = not why
    one_why = why or "Re-mesh one preset finer, solve again and compare the key results (how much they still change with the mesh)"
    three = preset in ("quick", "coarse", "medium")
    three_why = why or "Two finer meshes: the grid-convergence index (GCI) of each key result (about 3-4 times the time of one check)"
    note = ""
    cost, cells = RUN_COST.get(st["study"]), (st["mesh"].get("stats") or {}).get("cells")
    if ok and cost and cells:
        lo, hi = cells * 2 * cost[1] * 0.6 * cost[0] / 60, cells * 3 * cost[1] * cost[0] / 60

        def mn(m):
            return f"{max(1, round(m))} min" if m < 90 else f"{_f(m / 60, 2)} h"
        note = f"≈ {mn(lo)}–{mn(hi)}"
    return {"one": (preset != "very_fine", ok, one_why), "three": (three, ok, three_why), "note": note}


class MeshCheck(QWidget):
    """The mesh-sensitivity check: the last check's table and verdict, and the buttons that run one (one level finer, or three levels)."""

    def __init__(self, panel, parent=None):
        super().__init__(parent)
        self.panel = panel
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        self.table = wid(QLabel(""), "mesh.check.table")
        self.table.setWordWrap(True)
        self.table.setTextFormat(Qt.RichText)
        self.verdict = wid(QLabel(""), "mesh.check.verdict")
        self.verdict.setWordWrap(True)
        v.addWidget(self.table)
        v.addWidget(self.verdict)
        row = QHBoxLayout()
        self.one = wid(QPushButton("Run mesh-sensitivity check"), "mesh.check.run")
        self.one.setObjectName("ghostBorder")
        self.one.clicked.connect(lambda: self.panel.mesh_check(1))
        self.three = wid(QPushButton("Three-level check (GCI)"), "mesh.check.three")
        self.three.setObjectName("ghostBorder")
        self.three.clicked.connect(lambda: self.panel.mesh_check(2))
        self.note = wid(QLabel(""), "mesh.check.note")
        self.note.setObjectName("muted")
        self.note.setWordWrap(True)
        row.addWidget(self.one)
        row.addWidget(self.three)
        row.addStretch(1)
        v.addLayout(row)
        v.addWidget(self.note)

    def refresh(self, st: dict, running: bool, tokens: dict):
        mc = st.get("mesh_check")
        if mc and mc.get("rows"):
            head = f"{mc.get('from', '')} → {mc.get('to', '')}" + (f" → {mc['to2']}" if mc.get("to2") else "")
            rows = "".join(f"<tr><td style='padding-right:8px'>{n}</td><td>{t}</td></tr>" for n, t in check_rows(mc))
            self.table.setText(f"<table><tr><td><b>Output</b></td><td><b>{head}</b></td></tr>{rows}</table>")
            cells = f"{mc.get('cells_from', 0):,} → {mc.get('cells_to', 0):,}" + (f" → {mc['cells_to2']:,}" if mc.get("cells_to2") else "")
            warn = bool(mc.get("stale")) or (mc.get("max_change_percent") or 0) > 5
            txt = ("Outdated: the setup or results changed after this check - run it again. " if mc.get("stale") else "") + \
                  f"{mc.get('verdict', '')} ({cells} cells)" + (f" {mc['note_recomputed']}" if mc.get("note_recomputed") else "")
            self.verdict.setText(("⚠ " if warn else "✓ ") + txt)
            self.verdict.setStyleSheet(f"color: {tokens['warn_text'] if warn else tokens['ok_text']};")
            self.one.setText("Check again (one level finer)")
        else:
            self.table.setText("")
            self.verdict.setText("No check yet: it re-runs the study one preset finer and compares the key results.")
            self.verdict.setStyleSheet(f"color: {tokens['muted']};")
            self.one.setText("Run mesh-sensitivity check")
        if running:
            self.one.setText("Checking…")
        cs = check_state(st, running)
        shown, ok, why = cs["one"]
        self.one.setVisible(shown)
        self.one.setEnabled(ok)
        self.one.setToolTip(why)
        shown3, ok3, why3 = cs["three"]
        self.three.setVisible(shown3)
        self.three.setEnabled(ok3)
        self.three.setToolTip(why3)
        self.note.setText(cs["note"] or ("" if ok else why))
        if not shown and not shown3:
            self.note.setText("The mesh is already the finest preset: there is no finer level to compare with.")
