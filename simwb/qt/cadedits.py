"""CAD edits in the Geometry step: the list of edits made to the model (each can be removed, the last undone, all removed) and a form to ADD
one - a shape, a cut, a hole, a fillet, a chamfer, a mirror, a pattern, a scale, a move, a loft ... The web version shows the list and leaves
adding to the assistant; here every kind of edit has a form too (the assistant uses the same engine call, project.edit_geometry).

The edits are replayed on the imported file every time the geometry is built, so any of them can be removed again; the file itself is never
changed. Faces get new numbers after an edit: check boundary conditions that sit on edited faces."""
from __future__ import annotations

import json

from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

from .. import cad_edit
from .ids import wid
from .orient import _h, _item, _muted, notice

# what each kind of edit is called, in the order a person looks for them
OPS = [("box", "Add a box"), ("cylinder", "Add a cylinder"), ("sphere", "Add a sphere"), ("cone", "Add a cone"), ("prism", "Add a prism (extruded outline)"),
       ("revolve", "Add a revolved shape"), ("loft", "Add a loft between outlines"), ("cut", "Cut one body with others"),
       ("union", "Join bodies"), ("intersect", "Keep what bodies share"), ("hole", "Drill a hole"), ("fillet", "Round edges (fillet)"),
       ("chamfer", "Bevel edges (chamfer)"), ("move", "Move bodies"), ("rotate", "Turn bodies"), ("scale", "Scale bodies"),
       ("mirror", "Mirror bodies"), ("pattern_linear", "Repeat in a line"), ("pattern_polar", "Repeat round an axis"),
       ("cut_plane", "Cut at a plane"), ("delete", "Delete bodies"), ("rename", "Rename a body")]

HELP = {
    "box": "A box from its lowest corner (min) with a size along x, y, z (mm); 'rot' is an optional 3x3 turn as JSON.",
    "cylinder": "A cylinder from the centre of its base along an axis direction (mm).",
    "sphere": "A sphere round a centre (mm).", "cone": "A cone or a truncated cone from its base along an axis; a radius may be 0 for a point.",
    "prism": "An outline (a list of [x, y, z] points in mm, as JSON) extruded along a direction.",
    "revolve": "An outline (points as JSON) turned round an axis, by default a full turn.",
    "loft": "Sections (lists of [x, y, z] points as JSON, one list per section) joined into a solid; smooth or ruled.",
    "cut": "Cut the target body with the tool bodies (names, comma separated).", "union": "Join the bodies (names, comma separated) into one.",
    "intersect": "Keep only what the target and the tool bodies share.",
    "hole": "A round hole in a body at a point, along a direction; depth empty = right through.",
    "fillet": "Round the chosen edges of a body with a radius.", "chamfer": "Bevel the chosen edges of a body.",
    "move": "Move bodies by a distance along x, y, z (mm); no names = every body.", "rotate": "Turn bodies about an axis through a point.",
    "scale": "Scale bodies by a factor about a point (default their middle).", "mirror": "Mirror bodies in a plane; with 'copy' the original stays.",
    "pattern_linear": "Copies of a body repeated by a step; count includes the original.", "pattern_polar": "Copies of a body repeated round an axis.",
    "cut_plane": "Cut bodies at a plane and keep one side.", "delete": "Delete bodies.", "rename": "Give a body another name.",
}
LABELS = {"min": "Lowest corner", "size": "Size", "rot": "Turn (3x3, JSON)", "name": "Name", "base": "Base centre", "axis": "Axis direction",
          "radius_mm": "Radius", "center": "Centre", "radius1_mm": "Base radius", "radius2_mm": "Top radius", "points": "Outline points (JSON)",
          "direction": "Direction", "axis_point": "Axis point", "axis_direction": "Axis direction", "angle_deg": "Angle", "sections": "Sections (JSON)",
          "smooth": "Smooth", "ruled": "Ruled", "target": "Target body", "tools": "Tool bodies", "bodies": "Bodies", "by": "Move by", "point": "Point",
          "factor": "Factor", "about": "About", "normal": "Plane normal", "copy": "Keep the original", "body": "Body", "count": "Number of copies",
          "step": "Step", "union": "Join the copies", "edges": "Edges", "distance_mm": "Distance", "at": "At point", "diameter_mm": "Diameter",
          "depth_mm": "Depth", "keep": "Keep side"}
UNITS = {"radius_mm": "mm", "radius1_mm": "mm", "radius2_mm": "mm", "distance_mm": "mm", "diameter_mm": "mm", "depth_mm": "mm", "angle_deg": "°"}
POINTS = ("pt", "dv", "size")


class CadEditsBox:
    """The CAD edits of the open study: a list and an add form. Lives in the Geometry panel; `host` is that panel."""

    def __init__(self, host):
        self.h = host
        self.op = "hole"
        self.fields: dict = {}            # key -> widget(s) of the form now shown
        self.notice: tuple | None = None
        self.open = False

    # ------------------------------------------------------------------ engine
    def _pr(self):
        return self.h.engine.project()

    def edit(self, action: str, op: dict | None = None, index: int | None = None):
        """Add, remove (index), undo the last (index None with action 'undo') or clear: a job; the geometry is rebuilt without or with the edit."""
        pr = self._pr()
        if action == "undo":
            n = len(pr.cad_edits())
            if not n:
                raise ValueError("There are no CAD edits to undo")
            action, index = "remove", n
        job = pr.edit_geometry(action, op=op, index=index)
        self.notice = ("info", {"add": "Adding the edit: the geometry is being rebuilt…", "remove": "Removing the edit: the geometry is being rebuilt…",
                                "clear": "Removing every edit: the geometry is being rebuilt…"}[action])
        self.h.rebuild()
        return job

    def _try(self, fn):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            self.notice = ("bad", f"<b>That edit did not work.</b> {e}")
            self.h.rebuild()

    # ------------------------------------------------------------------ the form's value
    def payload(self) -> dict:
        out = {"op": self.op}
        for key, (kind, req) in cad_edit.FIELDS[self.op].items():
            w = self.fields.get(key)
            if w is None:
                continue
            if kind in POINTS:
                vals = [x.text().strip() for x in w]
                if all(v == "" for v in vals):
                    if req:
                        raise ValueError(f"{LABELS.get(key, key)}: give x, y and z (mm)")
                    continue
                out[key] = [float(v or 0) for v in vals]
            elif kind in ("num", "num0", "pos", "factor", "int"):
                t = w.text().strip()
                if t == "":
                    if req:
                        raise ValueError(f"{LABELS.get(key, key)}: give a number")
                    continue
                out[key] = int(float(t)) if kind == "int" else float(t)
            elif kind == "bool":
                out[key] = bool(w.isChecked())
            elif kind.startswith("enum:"):
                out[key] = w.currentText()
            elif kind == "names":
                t = w.text().strip()
                if t == "":
                    if req:
                        raise ValueError(f"{LABELS.get(key, key)}: name at least one body")
                    continue
                out[key] = [s.strip() for s in t.split(",") if s.strip()]
            elif kind == "sel":
                mode, pts = w
                out[key] = {"all": True} if mode.currentIndex() == 0 else {"near": [[float(x.text() or 0) for x in pts]]}
            elif kind in ("poly", "sections", "rot"):
                t = w.text().strip()
                if t == "":
                    if req:
                        raise ValueError(f"{LABELS.get(key, key)}: give the points as JSON, for example [[0,0,0],[10,0,0],[10,10,0]]")
                    continue
                try:
                    out[key] = json.loads(t)
                except ValueError:
                    raise ValueError(f"{LABELS.get(key, key)} must be JSON, for example [[0,0,0],[10,0,0],[10,10,0]]") from None
            else:  # str
                t = w.text().strip()
                if t == "" and req:
                    raise ValueError(f"{LABELS.get(key, key)}: give a name")
                if t:
                    out[key] = t
        return out

    def add(self):
        op = self.payload()
        self.edit("add", op)

    # ------------------------------------------------------------------ widgets
    def build(self, v: QVBoxLayout, st: dict, busy: bool):
        g = st["geometry"]
        edits = self._pr().cad_edits()
        can = (g.get("info") or {}).get("source_format") not in ("stl", "obj")
        v.addWidget(_h("CAD edits"))
        v.addWidget(_muted("Changes to the model: shapes added or cut, holes, fillets, chamfers, mirrored or repeated copies, moves. They are replayed "
                           "on the imported file every time the geometry is built, so any of them can be removed again; the file is never changed. "
                           "Faces get new numbers after an edit: check boundary conditions that sit on edited faces."))
        if self.notice:
            v.addWidget(notice(*self.notice, name="geometry.cad.notice"))
        if not can:
            v.addWidget(notice("warn", "CAD edits work on STEP files: an STL or OBJ model is a surface mesh and cannot be edited.", "geometry.cad.nostep"))
            return
        if not edits:
            v.addWidget(_muted("No edits yet. Add one below (or ask the assistant)."))
        for e in edits:
            card = _item()
            row = QHBoxLayout(card)
            row.setContentsMargins(8, 4, 8, 4)
            num = QLabel(str(e["index"]))
            num.setObjectName("muted")
            row.addWidget(num)
            txt = QLabel(e["text"])
            txt.setWordWrap(True)
            txt.setStyleSheet("font-family: Consolas, monospace;")
            wid(txt, f"geometry.cad.edit.{e['index']}")
            row.addWidget(txt, 1)
            rm = wid(QPushButton("✕"), f"geometry.cad.remove.{e['index']}")
            rm.setObjectName("ghostBorder")
            rm.setEnabled(not busy)
            rm.setFixedWidth(34)
            rm.setToolTip("Remove this edit (the ones after it are replayed without it)" if not busy else "Wait: the geometry is being rebuilt")
            rm.clicked.connect(lambda _=False, i=e["index"]: self._try(lambda: self.edit("remove", index=i)))
            row.addWidget(rm)
            v.addWidget(card)
        row = QHBoxLayout()
        un = wid(QPushButton("↶ Undo last"), "geometry.cad.undo")
        un.setObjectName("ghostBorder")
        un.setEnabled(bool(edits) and not busy)
        un.setToolTip("Remove the last edit" if edits and not busy else "Wait: the geometry is being rebuilt" if busy else "There are no edits to undo")
        un.clicked.connect(lambda: self._try(lambda: self.edit("undo")))
        cl = wid(QPushButton("Remove all"), "geometry.cad.clear")
        cl.setObjectName("ghostBorder")
        cl.setEnabled(bool(edits) and not busy)
        cl.setToolTip("Remove every edit: back to the file as imported" if edits and not busy else "Wait: the geometry is being rebuilt" if busy
                      else "There are no edits to remove")
        cl.clicked.connect(lambda: self._try(lambda: self.edit("clear")))
        row.addWidget(un)
        row.addWidget(cl)
        v.addLayout(row)
        self._form(v, st, busy)

    def _form(self, v: QVBoxLayout, st: dict, busy: bool):
        tg = wid(QPushButton("▼ Add an edit" if self.open else "▶ Add an edit…"), "geometry.cad.toggle")
        tg.setObjectName("ghostBorder")
        tg.setToolTip("Show the form to add a shape, a cut, a hole, a fillet, a mirror, a pattern, a move ...")
        tg.clicked.connect(self._toggle)
        v.addWidget(tg)
        if not self.open:
            return
        box = _item()
        b = QVBoxLayout(box)
        b.setContentsMargins(8, 6, 8, 6)
        b.setSpacing(5)
        kind = wid(QComboBox(), "geometry.cad.op")
        for k, label in OPS:
            kind.addItem(label, k)
        kind.setCurrentIndex(max(0, kind.findData(self.op)))
        kind.setToolTip("What to do to the model")
        kind.activated.connect(lambda i: self._set_op(kind.itemData(i)))
        b.addWidget(kind)
        b.addWidget(_muted(HELP.get(self.op, "")))
        parts = [p["name"] for p in self._pr().parts()]
        if parts:
            b.addWidget(_muted("Bodies you can name: " + ", ".join(parts)))
        self.fields = {}
        for key, (k, req) in cad_edit.FIELDS[self.op].items():
            self._field(b, key, k, req, parts)
        add = wid(QPushButton("Add this edit"), "geometry.cad.add")
        add.setObjectName("primary")
        add.setEnabled(not busy)
        add.setToolTip("Rebuild the geometry with this edit (takes a moment); an edit that does not work is refused and the model stays as it was"
                       if not busy else "Wait: the geometry is being rebuilt")
        add.clicked.connect(lambda: self._try(self.add))
        b.addWidget(add)
        v.addWidget(box)

    def _field(self, b: QVBoxLayout, key: str, k: str, req: bool, parts: list):
        label = LABELS.get(key, key) + ("" if req else " (optional)") + (f" [{UNITS[key]}]" if key in UNITS else " [mm]" if k in POINTS else "")
        name = f"geometry.cad.f.{key}"
        row = QHBoxLayout()
        lab = QLabel(label)
        lab.setWordWrap(True)
        if k in POINTS:
            b.addWidget(lab)
            ws = []
            for ax in "xyz":
                f = wid(QLineEdit(), f"{name}.{ax}")
                f.setValidator(QDoubleValidator(-1e9, 1e9, 6, f))
                f.setPlaceholderText(ax)
                f.setToolTip(f"{label}: {ax} in mm")
                f.setMinimumWidth(40)
                row.addWidget(f)
                ws.append(f)
            self.fields[key] = ws
            b.addLayout(row)
        elif k == "bool":
            c = wid(QCheckBox(label), name)
            c.setToolTip(label)
            self.fields[key] = c
            b.addWidget(c)
        elif k.startswith("enum:"):
            b.addWidget(lab)
            c = wid(QComboBox(), name)
            c.addItems(k[5:].split("|"))
            c.setToolTip(label)
            self.fields[key] = c
            b.addWidget(c)
        elif k == "sel":
            b.addWidget(lab)
            mode = wid(QComboBox(), f"{name}.mode")
            mode.addItems(["All edges of the body", "Edges near a point (mm)"])
            mode.setToolTip("Which edges: every edge of the body, or those nearest to a point")
            b.addWidget(mode)
            ws = []
            for ax in "xyz":
                f = wid(QLineEdit(), f"{name}.{ax}")
                f.setValidator(QDoubleValidator(-1e9, 1e9, 6, f))
                f.setPlaceholderText(ax)
                f.setToolTip(f"Point near the edges: {ax} in mm (used with 'Edges near a point')")
                f.setMinimumWidth(40)
                row.addWidget(f)
                ws.append(f)
            b.addLayout(row)
            self.fields[key] = (mode, ws)
        else:
            f = wid(QLineEdit(), name)
            if k in ("num", "num0", "pos", "factor", "int"):
                f.setValidator(QDoubleValidator(-1e9, 1e9, 6, f))
                f.setPlaceholderText("number")
            elif k == "names":
                f.setPlaceholderText("body names, comma separated" + ("" if req else " (empty: every body)"))
                if req and parts:
                    f.setText(parts[0])
            elif k in ("poly", "sections", "rot"):
                f.setPlaceholderText("JSON, e.g. [[0,0,0],[10,0,0],[10,10,0]]")
            elif key in ("body", "target") and parts:
                f.setText(parts[0])
            f.setToolTip(label)
            b.addWidget(lab)
            b.addWidget(f)
            self.fields[key] = f

    def _set_op(self, op: str):
        self.op = op
        self.notice = None
        self.h.rebuild()

    def _toggle(self):
        self.open = not self.open
        self.h.rebuild()
