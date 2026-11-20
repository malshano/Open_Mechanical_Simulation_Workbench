"""The setup step panel: parts and materials, boundary conditions (picked on the model), study settings, readiness.

Everything here is built from the engine's own definitions of the study (simwb.studies: boundary-condition types with their colours,
help and parameters; settings; what the study needs), so every study type gets a complete panel."""
from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

from ... import materials
from .. import mode, theme
from ..forms import ParamForm, fmt_num
from ..ids import wid
from .common import ClickLabel, Section, _h, _muted, _Panel, clear_layout

ADD_TEXT = "＋ Add boundary condition…"


def bc_summary(btype: dict, params: dict) -> str:
    """The value a boundary condition carries, short enough for a label on the model: '20 N', '60 °C', '5 W/m²K'."""
    p = params or {}
    comps = [k for k in p if k.startswith("f") and k.endswith("_N") and len(k) == 4]
    if comps:                                        # a force given by components: its size
        mag = sum(float(p[k]) ** 2 for k in comps) ** 0.5
        return f"{fmt_num(mag)} N"
    out = []
    units = {q["key"]: q.get("unit", "") for q in btype.get("params", [])}
    for q in btype.get("params", []):
        k = q["key"]
        if k in p and not q.get("options") and len(out) < 2:
            out.append(f"{fmt_num(p[k])} {units.get(k, '')}".strip())
    return ", ".join(out)


def _item() -> QFrame:
    f = QFrame()
    f.setObjectName("item")
    f.setAttribute(Qt.WA_StyledBackground, True)
    return f


class SetupPanel(_Panel):
    def __init__(self, engine, parent=None):
        super().__init__(engine, parent)
        self._sig = None
        self.active: str | None = None          # the boundary condition whose faces are being picked
        self.last_selection: list = []
        self.holder = QVBoxLayout()
        self.holder.setSpacing(10)
        self.v.addLayout(self.holder)
        self.v.addStretch(1)

    # ------------------------------------------------------------------ the 3D view
    def bind(self, win):
        super().bind(win)
        win.view.selectionChanged.connect(self._selection_changed)

    def shown(self):
        self.view.set_pick_mode("face")
        self.draw_marks()

    def left(self):
        self.view.set_pick_mode(None)
        self.view.set_selection([])
        self.view.set_face_tints({})
        self.view.set_labels([])
        self.view.set_arrows([])
        self.active = None

    def _selection_changed(self, ids):
        self.last_selection = list(ids)
        if self.win is not None and self.win.step == "setup" and self.active:
            try:
                self.engine.project().update_bc(self.active, faces=list(ids))
                self.engine.changed.emit()
            except Exception as e:  # noqa: BLE001
                self._fail(e)

    def draw_marks(self):
        """Tint the faces that carry a condition (the colour of its type) and hang its name and value on it."""
        if self.win is None or self.win.step != "setup" or not self.engine.pid:
            return
        st = self.engine.state()
        types = st["study_spec"]["face_bc_types"]
        tints, labels, arrows = {}, [], []
        info = (st["geometry"].get("info") or {})
        bb = info.get("parts_bbox") or info.get("bbox")
        size = max((bb[k + 3] - bb[k]) for k in range(3)) if bb else 0.1
        fmap = {f["id"]: f for f in self.engine.project().faces()}
        for b in st.get("bcs") or []:
            bt = types.get(b["type"], {})
            if b["id"] != self.active:
                for f in b.get("faces", []):
                    tints[f] = bt.get("color", "#888888")
            c = self.view.face_center(b.get("faces", []))
            if c is not None:
                val = bc_summary(bt, b.get("params") or {})
                labels.append({"point": c, "text": f"{b['name']}: {val}" if val else b["name"], "color": bt.get("color")})
                p = b.get("params") or {}
                comps = [p.get(f"f{a}_N") for a in "xyz"]
                if all(v is not None for v in comps) and any(abs(v) > 0 for v in comps):          # a force: an arrow along it, its tip on the faces
                    arrows.append({"point": c, "dir": comps, "color": bt.get("color"), "length": 0.3 * size})
                elif b["type"] == "pressure" and b.get("faces"):                                    # a pressure pushes on the faces: inward
                    for fid in b["faces"][:6]:
                        f = fmap.get(fid)
                        fc = self.view.face_center([fid])
                        if f and fc is not None:
                            arrows.append({"point": fc, "dir": [-x for x in f["normal"]], "color": bt.get("color"), "length": 0.18 * size})
        g = (st.get("settings") or {}).get("gravity")
        if g and g != "off" and bb:                                                                 # gravity: an arrow from above the part
            ax = "xyz".index(g[1])
            sgn = 1.0 if g[0] == "+" else -1.0
            d = [0.0, 0.0, 0.0]
            d[ax] = sgn
            ctr = [(bb[k] + bb[k + 3]) / 2 for k in range(3)]
            arrows.append({"point": [ctr[0], ctr[1], ctr[2]], "dir": d, "color": theme.tokens()["ink"], "length": 0.34 * size})
            labels.append({"point": [ctr[0], ctr[1], ctr[2] + (0.12 * size if ax != 2 else 0.0)], "text": "Gravity 9.81 m/s²", "color": theme.tokens()["muted"]})
        self.view.set_face_tints(tints)
        self.view.set_labels(labels)
        self.view.set_arrows(arrows)
        if self.active:
            bc = next((b for b in st.get("bcs") or [] if b["id"] == self.active), None)
            self.view.set_selection(bc.get("faces", []) if bc else [])

    # ------------------------------------------------------------------ building the panel
    def refresh(self, st):
        if not st:
            return
        hidden = sorted(self.win.hidden_bodies) if self.win is not None else []
        sig = json.dumps([st["study"], st.get("bcs"), [(b["id"], b["name"], b.get("material"), b.get("material_is_default"), b.get("heat_W"))
                          for b in st["body_list"]], st.get("settings"), st.get("checks"), self.active, theme.effective(), hidden],
                         default=str, sort_keys=True) + str(mode.simple())
        if sig == self._sig:
            self.draw_marks()
            return
        self._sig = sig
        clear_layout(self.holder)
        spec = st["study_spec"]
        self._about(spec)
        self._parts(st, spec)
        self._bcs(st, spec)
        self._settings(st, spec)
        self._checks(st)
        nxt = wid(QPushButton("Next: mesh →"), "setup.next")
        nxt.setObjectName("primary")
        nxt.clicked.connect(lambda: self.win.go("mesh"))
        self.holder.addWidget(nxt)
        self.draw_marks()

    def _about(self, spec):
        s = Section("About this simulation", "setup.about", expanded=True)
        s.body.addWidget(_muted(spec.get("explain", "")))
        needs = spec.get("needs") or []
        if needs:
            s.body.addWidget(_muted("What it needs: " + "; ".join(needs) + "."))
        self.holder.addWidget(s)

    # ---- parts & materials
    def _parts(self, st, spec):
        self.holder.addWidget(_h("Parts & materials"))
        for b in st["body_list"]:
            card = _item()
            v = QVBoxLayout(card)
            v.setContentsMargins(10, 8, 10, 8)
            v.setSpacing(6)
            head = QHBoxLayout()
            name = wid(QLineEdit(b["name"]), f"setup.body.{b['id']}.name")
            name.setToolTip("Rename this part")
            name.editingFinished.connect(lambda bid=b["id"], w=name: self._rename(bid, w.text()))
            head.addWidget(name, 1)
            if b["role"] == "solid":
                eye = wid(QPushButton("Show" if b["id"] in self.win.hidden_bodies else "Hide"), f"setup.body.{b['id']}.eye")
                eye.setObjectName("ghostBorder")
                eye.setToolTip("Hide or show this part in the 3D view")
                eye.clicked.connect(lambda _=False, bid=b["id"]: self.win.toggle_body(bid))
                head.addWidget(eye)
                hl = wid(QPushButton("Select"), f"setup.body.{b['id']}.select")
                hl.setObjectName("ghostBorder")
                hl.setToolTip("Select all the faces of this part in the 3D view")
                hl.clicked.connect(lambda _=False, bid=b["id"]: self._select_body(bid))
                head.addWidget(hl)
            v.addLayout(head)
            if b["role"] != "solid":
                v.addWidget(_muted("Air (the fluid region): no material to choose."))
            else:
                row = QHBoxLayout()
                row.addWidget(QLabel("Material"))
                cb = wid(QComboBox(), f"setup.material.{b['id']}")
                cb.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
                cb.setMinimumContentsLength(10)
                for k, m in materials.SOLIDS.items():
                    cb.addItem(m["label"], k)
                cb.setCurrentIndex(max(cb.findData(b.get("material")), 0))
                cb.setToolTip("The material of this part (its stiffness, density, conductivity ...)")
                cb.currentIndexChanged.connect(lambda _=0, c=cb, bid=b["id"]: self._set_material(bid, c.currentData()))
                row.addWidget(cb, 1)
                v.addLayout(row)
                if b.get("material_is_default"):
                    v.addWidget(_muted(f"Default material ({b.get('material_label')}): confirm it or choose another."))
                    ok = wid(QPushButton(f"✓ Use {b.get('material_label')}"), f"setup.body.{b['id']}.use_default")
                    ok.setToolTip("Keep this material (it was only a default)")
                    ok.clicked.connect(lambda _=False, bid=b["id"], c=cb: self._set_material(bid, c.currentData()))
                    v.addWidget(ok, 0, Qt.AlignLeft)
                params = spec.get("body_params") or []
                if params:
                    f = ParamForm(params, {"heat_W": b.get("heat_W", 0.0)}, f"setup.body.{b['id']}.param")
                    f.changed.connect(lambda k, val, bid=b["id"]: self._body_param(bid, k, val))
                    v.addWidget(f)
            self.holder.addWidget(card)

    def _rename(self, bid, text):
        text = text.strip()
        if not text:
            return
        try:
            self.engine.project().set_body(bid, name=text)
            self.engine.changed.emit()
        except Exception as e:  # noqa: BLE001
            self._fail(e)

    def _set_material(self, bid, key):
        try:
            self.engine.project().set_body(bid, material=key)
            self.engine.changed.emit()
        except Exception as e:  # noqa: BLE001
            self._fail(e)

    def _body_param(self, bid, key, val):
        try:
            self.engine.project().set_body(bid, **{key: val})
            self.engine.changed.emit()
        except Exception as e:  # noqa: BLE001
            self._fail(e)

    def _select_body(self, bid):
        ids = [f["id"] for f in self.engine.project().faces() if bid in (f.get("volumes") or []) and f.get("kind") not in ("farfield", "enclosure")]
        self.view.set_selection(ids)

    # ---- boundary conditions
    def _bcs(self, st, spec):
        self.holder.addWidget(_h("Boundary conditions"))
        types = spec["face_bc_types"]
        for i, b in enumerate(st.get("bcs") or []):
            bt = types.get(b["type"], {})
            card = _item()
            v = QVBoxLayout(card)
            v.setContentsMargins(10, 8, 10, 8)
            v.setSpacing(6)
            head = QHBoxLayout()
            sw = QLabel("")
            sw.setFixedSize(14, 14)
            sw.setStyleSheet(f"background: {bt.get('color', '#888')}; border: 1px solid {theme.tokens()['line_strong']}; border-radius: 3px;")
            sw.setToolTip(f"Colour of {bt.get('label', b['type'])} on the model")
            head.addWidget(sw)
            name = wid(QLineEdit(b["name"]), f"setup.bc.{i}.name")
            name.setToolTip("Rename this boundary condition")
            name.editingFinished.connect(lambda bid=b["id"], w=name: self._bc_update(bid, name=w.text().strip() or None))
            head.addWidget(name, 1)
            dele = wid(QPushButton("✕"), f"setup.bc.{i}.delete")
            dele.setObjectName("ghost")
            dele.setToolTip("Delete this boundary condition")
            dele.setAccessibleName(f"Delete {b['name']}")
            dele.clicked.connect(lambda _=False, bid=b["id"]: self.delete_bc(bid))
            head.addWidget(dele)
            v.addLayout(head)
            v.addWidget(_muted(bt.get("label", b["type"])))
            n = len(b.get("faces", []))
            if b.get("plane_id"):
                v.addWidget(_muted("A symmetry plane: its faces follow the plane."))
            else:
                fb = wid(QPushButton(("Done picking" if b["id"] == self.active else f"Faces ({n})")), f"setup.bc.{i}.faces")
                fb.setCheckable(True)
                fb.setChecked(b["id"] == self.active)
                fb.setObjectName("seg")
                fb.setToolTip("Press, then click faces in the 3D view to put them in this condition (Ctrl adds or removes a face)")
                fb.clicked.connect(lambda checked, bid=b["id"]: self.pick_faces(bid if checked else None))
                v.addWidget(fb)
                if b["id"] == self.active:
                    v.addWidget(_muted("Click faces in the 3D view. Click a face again with Ctrl to remove it."))
                elif n == 0:
                    v.addWidget(_muted("No faces yet: press Faces, then click faces in the 3D view."))
            if bt.get("params"):
                f = ParamForm(bt["params"], b.get("params") or {}, f"setup.bc.{i}.param")
                f.changed.connect(lambda k, val, bid=b["id"], cur=dict(b.get("params") or {}): self._bc_update(bid, params={**cur, k: val}))
                v.addWidget(f)
            if bt.get("help"):
                v.addWidget(_muted(bt["help"]))
            self.holder.addWidget(card)
        add = wid(QComboBox(), "setup.bc.add")
        add.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        add.setMinimumContentsLength(10)
        add.addItem(ADD_TEXT, None)
        for k, t in types.items():
            add.addItem(t["label"], k)
        add.setToolTip("Add a boundary condition: a support, a load, a heat source ... on faces you pick in the 3D view")
        add.currentIndexChanged.connect(lambda _=0, c=add: self._add_from_combo(c))
        self.holder.addWidget(add)
        if not (st.get("bcs") or []):
            self.holder.addWidget(_muted("Select faces in the 3D view first (click; Ctrl adds), then choose the type here: the condition takes the "
                                         "selected faces. Faces with no condition take the study's default."))

    def _add_from_combo(self, combo):
        key = combo.currentData()
        if key is None:
            return
        combo.blockSignals(True)
        combo.setCurrentIndex(0)
        combo.blockSignals(False)
        self.add_bc(key)

    def add_bc(self, bc_type: str, faces=None, name=None):
        """Create a condition of this type on the faces (default: the faces selected in the 3D view). With none selected it starts
        picking faces for the new condition."""
        try:
            faces = list(self.last_selection if faces is None else faces)
            bc = self.engine.project().add_bc(name, bc_type, faces, {})
            self.active = bc["id"] if bc.get("id") and not faces else None
            self.last_selection = []
            self.view.set_selection([])
            self.engine.changed.emit()
            return bc
        except Exception as e:  # noqa: BLE001
            self._fail(e)

    def delete_bc(self, bid):
        try:
            self.engine.project().delete_bc(bid)
            if self.active == bid:
                self.active = None
            self.engine.changed.emit()
        except Exception as e:  # noqa: BLE001
            self._fail(e)

    def pick_faces(self, bid):
        """Start (or stop) putting clicked faces into a condition."""
        self.active = bid
        self._sig = None
        if bid:
            st = self.engine.state()
            bc = next((b for b in st["bcs"] if b["id"] == bid), None)
            self.view.set_selection(bc.get("faces", []) if bc else [])
        else:
            self.view.set_selection([])
        self.engine.changed.emit()

    def set_bc_faces(self, bid, ids):
        self.engine.project().update_bc(bid, faces=list(ids))
        self.engine.changed.emit()

    def _bc_update(self, bid, **kw):
        try:
            self.engine.project().update_bc(bid, **kw)
            self.engine.changed.emit()
        except Exception as e:  # noqa: BLE001
            self._fail(e)

    # ---- settings
    def _settings(self, st, spec):
        params = spec.get("settings") or []
        if not params:
            return
        self.holder.addWidget(_h("Loads and settings"))
        main = [p for p in params if not p.get("adv")]
        adv = [p for p in params if p.get("adv")]
        vals = st.get("settings") or {}
        if main:
            f = ParamForm(main, vals, "setup.setting", simple=mode.simple())
            f.changed.connect(self._setting)
            self.holder.addWidget(f)
        if adv and not mode.simple():          # (Simple mode: the essentials only)
            s = Section("Advanced settings", "setup.advanced", expanded=False)
            f = ParamForm(adv, vals, "setup.setting")
            f.changed.connect(self._setting)
            s.body.addWidget(f)
            self.holder.addWidget(s)

    def _setting(self, key, val):
        try:
            self.engine.project().set_settings(**{key: val})
            self.engine.changed.emit()
        except Exception as e:  # noqa: BLE001
            self._fail(e)

    # ---- readiness
    def _checks(self, st):
        self.holder.addWidget(_h("Readiness"))
        checks = st.get("checks") or []
        t = theme.tokens()
        if not checks:
            ok = QLabel("✓ Ready to mesh: nothing is missing.")
            ok.setStyleSheet(f"color: {t['ok_text']}; font-weight: 600;")
            self.holder.addWidget(ok)
            return
        for i, c in enumerate(checks):
            lvl = c.get("level", "info")
            word, col = {"error": ("Missing", t["bad_text"]), "warning": ("Check", t["warn_text"])}.get(lvl, ("Note", t["muted"]))
            mark = "✖" if lvl == "error" else "⚠" if lvl == "warning" else "ℹ"
            b = wid(ClickLabel(f"{mark}  {word}: {c.get('message', '')}"), f"setup.check.{i}")
            b.setStyleSheet(f"color: {col}; padding: 4px 6px;")
            b.setToolTip(f"Go to the {c.get('step', 'setup')} step to fix this")
            b.clicked.connect(lambda _=False, s=c.get("step"): self.win.go(s) if s in ("geometry", "setup", "mesh", "solve", "results") else None)
            self.holder.addWidget(b)
