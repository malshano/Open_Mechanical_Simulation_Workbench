"""The setup step panel, in the order of the web version's step: about the study (and its solver), parts and materials, boundary conditions
(picked on the model; auto-detect inlet & outlet of a cooling study), symmetry planes, rotors (drone), moving zones (flow studies), the
study's settings under the heading the web version gives them (Flow condition, Loads ...) with the suggested end time of a sliding-mesh run
and the weight / thrust of a drone, readiness.

Everything here is built from the engine's own definitions of the study (simwb.studies: boundary-condition types with their colours,
help and parameters; settings; what the study needs), so every study type gets a complete panel."""
from __future__ import annotations

import json

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from ... import materials
from .. import bcs as bcmarks
from .. import materialsui, mode, theme
from .. import rotors as rotmod
from .. import symmetry as symmod
from ..forms import ParamForm, fmt_num
from ..ids import wid
from .common import ClickLabel, Section, _h, _muted, _Panel, clear_layout

ADD_TEXT = "＋ Add boundary condition…"
SETTINGS_HEADING = {"external_aero": "Flow condition", "vibration_modal": "Modes and vibration source", "aeroelastic_flutter": "Air flow and modes",
                    "static_structural": "Loads", "heat_conduction": "Solver settings"}


def fmt_time(t: float) -> str:
    return f"{t / 3600:.3g} h" if t >= 3600 else f"{t / 60:.3g} min" if t >= 120 else f"{t:.3g} s"


bc_summary = lambda btype, params: bcmarks.bc_value({"params": params}, btype)  # noqa: E731  (the value a condition carries)


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
        self.axis_pick: tuple | None = None     # ('rotor' | 'zone', index) while a centre of rotation is picked on the model
        self.sym = symmod.Symmetry(self)
        self.rot = rotmod.Rotors(self)
        self.status = wid(_muted(""), "setup.status")         # what the last action did (or why it could not), until the next one
        self.status.setVisible(False)
        self.v.addWidget(self.status)
        self.holder = QVBoxLayout()
        self.holder.setSpacing(10)
        self.v.addLayout(self.holder)
        self.v.addStretch(1)

    def say(self, text: str):
        """A line at the top of the panel (and in the status bar) saying what happened."""
        self.status.setText(text)
        self.status.setVisible(bool(text))
        if text and self.win is not None:
            self.win.statusBar().showMessage(text, 8000)

    def redo(self):
        """Build the panel again now (a choice that only the panel holds: a draft plane, a pick mode) and redraw the marks."""
        self._sig = None
        if self.engine.pid:
            self.refresh(self.engine.state())

    # ------------------------------------------------------------------ the 3D view
    def bind(self, win):
        super().bind(win)
        win.view.selectionChanged.connect(self._selection_changed)
        win.view.facePicked.connect(self._face_picked)

    def shown(self):
        self.view.set_pick_mode("face")
        self.draw_marks()

    def left(self):
        self.rot.stop_preview()
        self.view.set_pick_mode(None)
        self.view.set_selection([])
        self.view.set_face_tints({})
        self.view.set_labels([])
        self.view.set_arrows([])
        self.active = None
        self.axis_pick = None
        self.sym.picking = False
        self.view.plane_handle.clear()                              # (the draft plane itself is kept: it is shown again when the step is)

    def _picking_other(self) -> bool:
        return bool(self.axis_pick or self.sym.picking)

    def _selection_changed(self, ids):
        if self._picking_other() or getattr(self, "_swallow", False):   # (the click placed a plane or a centre of rotation: _face_picked)
            self._swallow = False
            self.view.set_selection([])
            return
        self.last_selection = list(ids)
        if self.win is not None and self.win.step == "setup" and self.active:
            try:
                self.engine.project().update_bc(self.active, faces=list(ids))
                self.engine.changed.emit()
            except Exception as e:  # noqa: BLE001
                self._fail(e)

    def _face_picked(self, fid: int, add: bool):
        if self.win is None or self.win.step != "setup" or not self.engine.pid:
            return
        if self.sym.picking:
            self._swallow = True
            self.sym.from_face(fid)
        elif self.axis_pick:
            self._swallow = True
            # (the point clicked, for the circular edge nearest to it, is known once the view has finished the click)
            QTimer.singleShot(0, lambda fid=fid: self._axis_face(fid))

    def _axis_face(self, fid: int):
        if not self.axis_pick:
            return
        h = self.view.last_hit or {}
        point = h.get("point") if h.get("face") == fid else None
        self.view.set_selection([])
        self.rot.picked(fid, list(point) if point is not None else None)

    def draw_marks(self):
        """Everything the setup hangs on the model (simwb.qt.bcs): each condition's faces tinted in its colour with its name and value,
        arrows for loads and flows, the free stream, rotors and zones, heat sources, symmetry planes and gravity."""
        if self.win is None or self.win.step != "setup" or not self.engine.pid:
            return
        self.sym.show_preview()
        st = self.engine.state()
        info = (st["geometry"].get("info") or {})
        bb = info.get("parts_bbox") or info.get("bbox")
        size = max((bb[k + 3] - bb[k]) for k in range(3)) if bb else 0.1
        t = theme.tokens()
        tints, labels, arrows = bcmarks.marks(st, self.engine.project().faces(), self.view.face_center, size, self.active, self.sym.draft)
        for a in arrows:
            a["color"] = a.get("color") or t["ink"]
        for la in labels:
            la["color"] = la.get("color") or t["muted"]
        self.view.set_face_tints(tints)
        self.view.set_labels(labels)
        self.view.set_arrows(arrows)
        self.rot.draw_zones()
        if self.active:
            bc = next((b for b in st.get("bcs") or [] if b["id"] == self.active), None)
            self.view.set_selection(bc.get("faces", []) if bc else [])

    # ------------------------------------------------------------------ building the panel
    def refresh(self, st):
        if not st:
            return
        hidden = sorted(self.win.hidden_bodies) if self.win is not None else []
        busy = self.engine.project().busy()
        sig = json.dumps([st["study"], st.get("bcs"), [(b["id"], b["name"], b.get("material"), b.get("material_is_default"), b.get("heat_W"))
                          for b in st["body_list"]], st.get("settings"), st.get("checks"), self.active, theme.effective(), hidden,
                          st.get("symmetry"), st.get("rotors"), st.get("moving_zones"), (st.get("domain") or {}).get("enclosure_mode"),
                          st["geometry"].get("updated"), busy, self.sym.draft, self.sym.picking, self.axis_pick, self.rot.oblique, materials.VERSION],
                         default=str, sort_keys=True) + str(mode.simple())
        if sig == self._sig:
            self.draw_marks()
            return
        self._sig = sig
        clear_layout(self.holder)
        spec = st["study_spec"]
        self._about(st, spec)
        if busy:
            self.holder.addWidget(wid(_muted("⏳ A job is running for this study: changes apply to the next run; the geometry tools wait for it."),
                                      "setup.busy"))
        self._parts(st, spec)
        self._bcs(st, spec)
        for w in self.sym.build(st, busy):
            self.holder.addWidget(w)
        if st["study"] == "external_aero":
            for w in self.rot.build_rotors(st, busy):
                self.holder.addWidget(w)
        if st["study"] in ("external_aero", "cht_enclosure"):
            for w in self.rot.build_zones(st, busy):
                self.holder.addWidget(w)
        self._settings(st, spec)
        self._checks(st)
        nxt = wid(QPushButton("Next: mesh →"), "setup.next")
        nxt.setObjectName("primary")
        nxt.setToolTip("Go to the Mesh step")
        nxt.clicked.connect(lambda: self.win.go("mesh"))
        self.holder.addWidget(nxt)
        self.draw_marks()

    def _about(self, st, spec):
        s = Section("About this simulation", "setup.about", expanded=True)
        s.body.addWidget(_muted(spec.get("explain", "") or spec.get("description", "")))
        needs = spec.get("needs") or []
        if needs:
            s.body.addWidget(_muted("What it needs: " + "; ".join(needs) + "."))
        sv = (st.get("settings") or {}).get("structural_solver")
        label = "CalculiX (the separate open-source program)" if str(sv or "").startswith("calculix") else spec.get("solver_label", "")
        sol = wid(_muted(f"Solver: {label}"), "setup.about.solver")
        sol.setToolTip("The program that solves this study" + (" (change it under Advanced settings, in Expert mode)" if sv else ""))
        s.body.addWidget(sol)
        self.holder.addWidget(s)

    # ---- parts & materials
    def _parts(self, st, spec):
        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(_h("Parts & materials"), 1)
        lib = wid(QPushButton("Materials…"), "setup.materials.open")
        lib.setObjectName("ghostBorder")
        lib.setToolTip("The materials library: every solid and fluid you can choose, and the ones you added")
        lib.clicked.connect(self.open_library)
        hl.addWidget(lib)
        self.holder.addWidget(head)
        fluid_param = next((p for p in spec.get("settings") or [] if p["key"] == "fluid" and p.get("options")), None)
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
            if b["role"] != "solid" and fluid_param is None:
                v.addWidget(_muted("The fluid region: this study has no fluid to choose."))
            elif b["role"] != "solid":
                row = QHBoxLayout()
                row.addWidget(QLabel("Fluid"))
                cb = wid(QComboBox(), f"setup.material.{b['id']}")
                cb.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
                cb.setMinimumContentsLength(10)
                names = fluid_param.get("option_labels") or {}
                for k in fluid_param["options"]:
                    cb.addItem(names.get(k, k), k)
                    f = materials.FLUIDS.get(k) or {}
                    cb.setItemData(cb.count() - 1, f"{f.get('kind', 'gas').capitalize()}: density {fmt_num(f.get('rho', 0))} kg/m³, viscosity "
                                   f"{fmt_num(f.get('mu', 0) * 1e3)} mPa·s, conductivity {fmt_num(f.get('k', 0))} W/(m·K)", Qt.ToolTipRole)
                cb.addItem("＋ Add a fluid…", "__add__")
                cur = (st.get("settings") or {}).get("fluid", fluid_param.get("default"))
                cb.setCurrentIndex(max(cb.findData(cur), 0))
                cb.setToolTip("The fluid the study is run in: a gas or a liquid, from the library (Materials…) or one you add here")
                cb.currentIndexChanged.connect(lambda _=0, c=cb, prev=cur: self._fluid_chosen(c, prev))
                row.addWidget(cb, 1)
                v.addLayout(row)
                f = materials.FLUIDS.get(cur) or {}
                if f:
                    v.addWidget(_muted(f"{f.get('kind', 'gas').capitalize()}: density {fmt_num(f['rho'])} kg/m³, viscosity {fmt_num(f['mu'] * 1e3)} mPa·s."
                                       + (" Constant density." if f.get("kind") == "liquid" else "")))
            else:
                row = QHBoxLayout()
                row.addWidget(QLabel("Material"))
                cb = wid(QComboBox(), f"setup.material.{b['id']}")
                cb.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
                cb.setMinimumContentsLength(10)
                for k, m in materials.SOLIDS.items():
                    cb.addItem(m["label"], k)
                    cb.setItemData(cb.count() - 1, f"Density {fmt_num(m['rho'])} kg/m³, conductivity {fmt_num(m['k'])} W/(m·K), Young's modulus "
                                   f"{fmt_num(m['E'] / 1e9)} GPa" + (f", yield {fmt_num(m['Sy'] / 1e6)} MPa" if m.get("Sy") else "")
                                   + (" (yours)" if m.get("custom") else ""), Qt.ToolTipRole)
                cb.addItem("＋ Add a material…", "__add__")
                cb.setCurrentIndex(max(cb.findData(b.get("material")), 0))
                cb.setToolTip("The material of this part (its stiffness, density, conductivity ...); the last entry adds one of your own")
                cb.currentIndexChanged.connect(lambda _=0, c=cb, bid=b["id"], prev=b.get("material"): self._material_chosen(bid, c, prev))
                row.addWidget(cb, 1)
                v.addLayout(row)
                if b.get("material_is_default"):
                    v.addWidget(_muted(f"Default material ({b.get('material_label')}): confirm it or choose another."))
                    ok = wid(QPushButton(f"✓ Use {b.get('material_label')}"), f"setup.body.{b['id']}.use_default")
                    ok.setToolTip("Keep this material (it was only a default)")
                    ok.clicked.connect(lambda _=False, bid=b["id"], c=cb: self._set_material(bid, c.currentData()) if c.currentData() != "__add__" else None)
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

    def open_library(self):
        """The materials library dialog (not blocking: the window stays usable)."""
        d = getattr(self, "_library", None)
        if d is None or not d.isVisible():
            self._library = d = materialsui.LibraryDialog(self.win, on_change=self.engine.changed.emit)
        d.refresh()
        d.show()
        d.raise_()
        return d

    def _new_material(self, kind: str, on_done):
        """The add dialog; on_done(key) runs when the material was saved."""
        def saved(key):
            self.engine.changed.emit()
            on_done(key)
        return materialsui.open_material_dialog(kind, self.win, on_done=saved)

    def _material_chosen(self, bid, cb, prev):
        key = cb.currentData()
        if key != "__add__":
            self._set_material(bid, key)
            return
        cb.blockSignals(True)                                  # (the entry only opens the dialog: the choice goes back until a material is saved)
        cb.setCurrentIndex(max(cb.findData(prev), 0))
        cb.blockSignals(False)
        self._new_material("solid", lambda k: self._set_material(bid, k))

    def _fluid_chosen(self, cb, prev):
        key = cb.currentData()
        if key != "__add__":
            self._setting("fluid", key)
            return
        cb.blockSignals(True)
        cb.setCurrentIndex(max(cb.findData(prev), 0))
        cb.blockSignals(False)
        self._new_material("fluid", lambda k: self._setting("fluid", k))

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
        nat = st["study"] == "cht_enclosure" and (st.get("settings") or {}).get("air_flow") == "natural"
        auto_box = (st.get("domain") or {}).get("enclosure_mode") == "auto_box"
        other = (("Adiabatic wall (automatic air box sides: open to the room)" if auto_box else "Adiabatic wall") if nat
                 else (spec.get("default_face_bc") or {}).get("label", ""))
        if other:
            self.holder.addWidget(wid(_muted(f"Other faces: {other}"), "setup.bc.other"))
        if st["study"] == "cht_enclosure" and not nat:
            io = wid(QPushButton("✨ Auto-detect inlet & outlet"), "setup.bc.auto_io")
            io.setObjectName("ghostBorder")
            io.setToolTip("Put the inlet and the outlet on the two end faces of the air volume (along its longest side, or the flow axis of "
                          "an automatic air box); an inlet or outlet set before is replaced")
            io.clicked.connect(self.auto_inlet_outlet)
            self.holder.addWidget(io, 0, Qt.AlignLeft)
        for i, b in enumerate(st.get("bcs") or []):
            bt = types.get(b["type"], {})
            if b.get("plane_id"):                    # the condition of a symmetry plane: set with the plane (below), shown greyed and linked
                self.holder.addWidget(self._linked_card(i, b, bt, st))
                continue
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
            kind = _muted(bt.get("label", b["type"]) + " ⓘ")
            kind.setToolTip(bt.get("help", ""))
            v.addWidget(kind)
            n = len(b.get("faces", []))
            fb = wid(QPushButton(("Done picking" if b["id"] == self.active else f"Faces ({n})")), f"setup.bc.{i}.faces")
            fb.setCheckable(True)
            fb.setChecked(b["id"] == self.active)
            fb.setObjectName("toggle")
            fb.setToolTip("Press, then click faces in the 3D view to put them in this condition (Ctrl adds or removes a face)")
            fb.clicked.connect(lambda checked, bid=b["id"]: self.pick_faces(bid if checked else None))
            v.addWidget(fb, 0, Qt.AlignLeft)
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
            if bc_type == "symmetry" and not faces:        # (a mirror is a plane through a flat face: the next click places it)
                self.sym.toggle_pick(True)
                return {"id": None, "name": "Symmetry plane", "type": "symmetry", "faces": [], "note": "Click a flat face in the 3D view"}
            bc = self.engine.project().add_bc(name, bc_type, faces, {})
            if bc_type == "symmetry":
                self.say("Rebuilding the geometry with the plane…")
            self.active = bc["id"] if bc.get("id") and not faces else None
            self.last_selection = []
            self.view.set_selection([])
            self.engine.changed.emit()
            return bc
        except Exception as e:  # noqa: BLE001
            self._fail(e)

    def _linked_card(self, i, b, bt, st):
        """The condition of a symmetry plane: shown, greyed, with no controls of its own (move, flip or remove the plane instead)."""
        pl = next((q for q in st.get("symmetry") or [] if q["id"] == b["plane_id"]), None)
        card = _item()
        wid(card, f"setup.bc.{i}.linked")
        card.setToolTip("Set by the symmetry plane: move, flip or remove the plane under Symmetry planes")
        v = QVBoxLayout(card)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(4)
        head = QHBoxLayout()
        sw = QLabel("")
        sw.setFixedSize(14, 14)
        sw.setStyleSheet(f"background: {bt.get('color', '#888')}; border: 1px solid {theme.tokens()['line_strong']}; border-radius: 3px;")
        head.addWidget(sw)
        nm = QLabel(b["name"])
        nm.setStyleSheet(f"font-weight: 600; color: {theme.tokens()['muted']};")
        head.addWidget(nm, 1)
        n = len(b.get("faces") or [])
        head.addWidget(_muted(f"🔗 plane · {n} face{'' if n == 1 else 's'}"))
        v.addLayout(head)
        v.addWidget(_muted(f"{bt.get('label', 'Symmetry')} · connected to {pl['name'] if pl else 'its plane'} in Symmetry planes below "
                           "(change it there)"))
        return card

    def auto_inlet_outlet(self):
        """Cooling studies: the inlet and the outlet on the two end faces of the air volume."""
        try:
            res = self.engine.project().suggest_inlet_outlet(apply=True)
        except Exception as e:  # noqa: BLE001
            self._fail(e)
            return None
        self.say(f"Inlet ({len(res['inlet'])} face{'s' if len(res['inlet']) != 1 else ''}) and outlet ({len(res['outlet'])}) assigned "
                 f"along {res['axis']}: set the inlet's speed or flow rate.")
        self.engine.changed.emit()
        return res

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
        self.holder.addWidget(_h(SETTINGS_HEADING.get(st["study"], "Flow & solver settings")))
        main = [p for p in params if not p.get("adv")]
        adv = [p for p in params if p.get("adv")]
        vals = st.get("settings") or {}
        if main:
            f = ParamForm(main, vals, "setup.setting", simple=mode.simple())
            f.changed.connect(self._setting)
            self.holder.addWidget(f)
        if adv and not mode.simple():          # (Simple mode: the essentials only; Expert: shown open, as the web version lists them)
            s = Section("Advanced settings", "setup.advanced", expanded=True)
            s.body.addWidget(_muted("Expert settings: the defaults suit a first run" +
                                    (" (the solver program, the flow model, numerics)." if st["study"] != "static_structural" else " (the solver program).")))
            f = ParamForm(adv, vals, "setup.setting")
            f.changed.connect(self._setting)
            s.body.addWidget(f)
            self.holder.addWidget(s)
        if st["study"] == "external_aero":
            self._end_time(st)
            w = self.rot.weight_note(st)
            if w is not None:
                self.holder.addWidget(w)

    def _end_time(self, st):
        """A transient run with sliding-mesh zones: the end time the engine suggests, with Use it."""
        try:
            a = self.engine.project().end_time_advice()
        except Exception:  # noqa: BLE001
            a = None
        if not a:
            return
        card = _item()
        v = QVBoxLayout(card)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(4)
        row = QHBoxLayout()
        head = wid(QLabel(f"Suggested end time: {fmt_time(a['t'])}"), "setup.endtime.text")
        head.setStyleSheet("font-weight: 600;")
        head.setToolTip(f"{a['revs']:.3g} revolutions of {a['fast']}, about {round(a['steps']):,} time steps")
        row.addWidget(head, 1)
        if a["same"]:
            row.addWidget(_muted("this is your end time"))
        else:
            use = wid(QPushButton("Use it"), "setup.endtime.use")
            use.setObjectName("ghostBorder")
            use.setToolTip(f"Set the end time to {fmt_time(a['t'])}")
            use.clicked.connect(lambda _=False, t=a["t"]: self._setting("end_time", t))
            row.addWidget(use)
        v.addLayout(row)
        v.addWidget(_muted(f"({a['revs']:.3g} revolutions of {a['fast']}, about {round(a['steps']):,} time steps.) Set by {a['why']}: the longest of 5 "
                           "revolutions of the fastest zone, 2 of the slowest and 3 flow-through times of the model. Average the forces over the "
                           "last revolution only."))
        if a["long"]:
            w = QLabel(f"⚠ Your end time of {fmt_time(a['cur'])} is {a['cur_revs']:.3g} revolutions of {a['fast']}: about "
                       f"{round(a['cur'] / a['t'] * a['steps']):,} time steps.")
            w.setWordWrap(True)
            w.setStyleSheet(f"color: {theme.tokens()['warn_text']};")
            v.addWidget(w)
        self.holder.addWidget(card)

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
