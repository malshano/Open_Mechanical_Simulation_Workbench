"""The materials library in the window: a dialog to add (or change) a material, and the list of all of them.

Solids and fluids are the engine's own (simwb.materials): the built-in ones cannot be changed, the ones you add can be edited, copied and
removed. The dialogs open without blocking the window (QDialog.open), so the control API and Claude can work with them as a person does.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
                               QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from .. import materials
from . import theme
from .forms import fmt_num
from .ids import wid

# (key, label, unit shown, scale to SI, optional, only for)
SOLID_FIELDS = [("rho", "Density", "kg/m³", 1.0, False, None), ("cp", "Specific heat", "J/(kg·K)", 1.0, False, None),
                ("k", "Thermal conductivity", "W/(m·K)", 1.0, False, None), ("E", "Young's modulus", "GPa", 1e9, False, None),
                ("nu", "Poisson's ratio", "-", 1.0, False, None), ("Sy", "Yield strength (optional)", "MPa", 1e6, True, None)]
FLUID_FIELDS = [("rho", "Density", "kg/m³", 1.0, False, None), ("mu", "Viscosity", "mPa·s", 1e-3, False, None),
                ("cp", "Specific heat", "J/(kg·K)", 1.0, False, None), ("k", "Thermal conductivity", "W/(m·K)", 1.0, False, None),
                ("Pr", "Prandtl number (empty: worked out)", "-", 1.0, True, None),
                ("beta", "Thermal expansion (empty: typical)", "1/K", 1.0, True, None), ("M", "Molar mass", "kg/kmol", 1.0, False, "gas")]
HELP = {"rho": "Mass per volume. Water is 998 kg/m³, steel 7850.", "mu": "Dynamic viscosity in millipascal-seconds (the same number as centipoise): "
        "water 1.0, air 0.018, engine oil 800.", "cp": "Heat needed to warm 1 kg by 1 K.", "k": "How well it conducts heat.",
        "E": "Stiffness: steel is 200 GPa, aluminium 69, plastics 2-4.", "nu": "How much it narrows when stretched, usually 0.25 to 0.4.",
        "Sy": "The stress where it starts to deform for good (leave empty for a brittle material).",
        "Pr": "Viscosity x specific heat / conductivity. Leave it empty and it is worked out.",
        "beta": "How much it expands per kelvin (used for natural convection). Empty: typical for a gas / liquid.",
        "M": "Gas only: the mass of a kilomole (air 28.96)."}


def _validator(parent) -> QDoubleValidator:
    v = QDoubleValidator(parent)
    v.setNotation(QDoubleValidator.ScientificNotation)
    return v


class MaterialDialog(QDialog):
    """Add a material (kind "solid" or "fluid"), or change one of yours (edit_key). The values are checked by the engine; what is wrong is
    said in the dialog and it stays open."""

    def __init__(self, kind: str, parent=None, start: str | None = None, edit_key: str | None = None):
        super().__init__(parent)
        self.kind = kind
        self.edit_key = edit_key
        self.key: str | None = None
        solid = kind == "solid"
        self.table = materials.SOLIDS if solid else materials.FLUIDS
        self.fields = SOLID_FIELDS if solid else FLUID_FIELDS
        noun = "material" if solid else "fluid"
        self.setObjectName("materials.dialog")
        self.setWindowTitle(f"Change {noun}" if edit_key else f"Add a {noun}")
        self.setModal(True)
        self.setMinimumWidth(460)
        v = QVBoxLayout(self)
        v.setSpacing(10)
        intro = QLabel("The values you enter are used as they are, at the temperature you work at. They are saved with your materials and with "
                       "every study that uses them." if not edit_key else
                       "Studies that use this material need to be solved again after a change.")
        intro.setObjectName("muted")
        intro.setWordWrap(True)
        v.addWidget(intro)
        form = QFormLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(7)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        v.addLayout(form)
        self.start = None
        if not edit_key:                                       # (copy the numbers of one that is like it)
            self.start = wid(QComboBox(), "materials.dialog.start")
            self.start.addItem("— enter the values —", None)
            for k, m in self.table.items():
                self.start.addItem(m["label"], k)
            self.start.setToolTip(f"Fill the fields with the values of a {noun} that is like the one you add, then change what differs")
            self.start.currentIndexChanged.connect(self._copy_values)
            form.addRow(QLabel("Start from"), self.start)
        self.name = wid(QLineEdit(), "materials.dialog.name")
        self.name.setPlaceholderText("for example: Aluminium 7075" if solid else "for example: Cooling brine")
        self.name.setMaxLength(60)
        form.addRow(QLabel("Name"), self.name)
        self.phase = None
        if not solid:
            self.phase = wid(QComboBox(), "materials.dialog.phase")
            self.phase.addItem("Liquid (constant density)", "liquid")
            self.phase.addItem("Gas (perfect gas)", "gas")
            self.phase.setToolTip("A liquid keeps its density; a gas follows pressure and temperature (the perfect-gas law)")
            self.phase.currentIndexChanged.connect(self._phase_changed)
            form.addRow(QLabel("Type"), self.phase)
        self.edits: dict = {}
        self.rows: dict = {}
        for key, label, unit, scale, optional, only in self.fields:
            e = wid(QLineEdit(), f"materials.dialog.{key}")
            e.setValidator(_validator(e))
            e.setToolTip(HELP.get(key, label))
            e.setAccessibleName(f"{label} [{unit}]")
            if optional:
                e.setPlaceholderText("optional")
            lab = QLabel(f"{label} [{unit}]")
            lab.setWordWrap(True)
            lab.setToolTip(HELP.get(key, label))
            form.addRow(lab, e)
            self.edits[key] = (e, scale)
            self.rows[key] = (lab, e, only)
        self.error = wid(QLabel(""), "materials.dialog.error")
        self.error.setWordWrap(True)
        self.error.setVisible(False)
        v.addWidget(self.error)
        row = QHBoxLayout()
        row.addStretch(1)
        self.cancel = wid(QPushButton("Cancel"), "materials.dialog.cancel")
        self.cancel.setToolTip("Close without adding anything")
        self.cancel.clicked.connect(self.reject)
        self.ok = wid(QPushButton("Save changes" if edit_key else f"Add the {noun}"), "materials.dialog.ok")
        self.ok.setObjectName("primary")
        self.ok.setDefault(True)
        self.ok.setToolTip("Check the values and put it in the library")
        self.ok.clicked.connect(self._save)
        row.addWidget(self.cancel)
        row.addWidget(self.ok)
        v.addLayout(row)
        src = edit_key or start
        if src and src in self.table:
            self._fill(self.table[src], keep_name=False if edit_key else True)
            if edit_key:
                self.name.setText(self.table[edit_key]["label"])
        elif self.phase is not None:
            self.phase.setCurrentIndex(0)
        self._phase_changed()
        self.name.setFocus()

    # ---- values
    def _fill(self, m: dict, keep_name: bool = True):
        if self.phase is not None:
            self.phase.setCurrentIndex(max(self.phase.findData(m.get("kind", "liquid")), 0))
        for key, (e, scale) in self.edits.items():
            val = None if key == "Pr" else m.get(key)           # (the Prandtl number follows from the others: copying it would keep a stale one)
            e.setText("" if val in (None, "") else fmt_num(float(val) / scale))
        if not keep_name:
            self.name.setText(m["label"])
        self._phase_changed()

    def _copy_values(self):
        k = self.start.currentData()
        if k:
            self._fill(self.table[k])
            self.name.setFocus()

    def _phase_changed(self):
        gas = self.phase is not None and self.phase.currentData() == "gas"
        for key, (lab, e, only) in self.rows.items():
            show = only is None or (only == "gas" and gas)
            lab.setVisible(show)
            e.setVisible(show)

    def values(self) -> dict:
        out = {"label": self.name.text()}
        if self.phase is not None:
            out["kind"] = self.phase.currentData()
        for key, (e, scale) in self.edits.items():
            if not e.isVisible() and key == "M":
                continue
            t = e.text().strip()
            if not t:
                out[key] = None
                continue
            try:
                out[key] = float(t.replace(",", ".")) * scale
            except ValueError:
                raise ValueError(f"'{t}' is not a number ({dict((k, l) for k, l, *_ in self.fields)[key]})") from None
        return out

    def _show_error(self, text: str):
        self.error.setText(f"<span style='color:{theme.tokens()['bad_text']}'><b>✖ {text}</b></span>")
        self.error.setVisible(True)

    def _save(self):
        try:
            f = self.values()
            if self.edit_key:
                self.key = materials.update_custom(self.kind, self.edit_key, **f)
            elif self.kind == "solid":
                self.key = materials.add_solid(**f)
            else:
                self.key = materials.add_fluid(**f)
        except ValueError as e:
            self._show_error(str(e))
            return
        self.accept()


def open_material_dialog(kind: str, parent, on_done=None, start: str | None = None, edit_key: str | None = None) -> MaterialDialog:
    """Show the add / change dialog without blocking; on_done(key) is called with the new key when it was saved (nothing when cancelled)."""
    d = MaterialDialog(kind, parent, start=start, edit_key=edit_key)
    d.setAttribute(Qt.WA_DeleteOnClose, False)
    if on_done is not None:
        d.accepted.connect(lambda: on_done(d.key))
    d.open()
    return d


def _fmt(v) -> str:
    return "-" if v in (None, "") else fmt_num(v)


class LibraryDialog(QDialog):
    """All the materials: the built-in ones (read only) and yours (change, copy, remove)."""

    def __init__(self, parent=None, on_change=None):
        super().__init__(parent)
        self.setObjectName("materials.library")
        self.setWindowTitle("Materials library")
        self.setMinimumSize(680, 460)
        self._on_change = on_change
        self._open: list = []
        v = QVBoxLayout(self)
        v.setSpacing(8)
        note = QLabel("The materials you can give a part or fill the flow with. The built-in ones cannot be changed; add your own, or copy one "
                      "as a starting point.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        v.addWidget(note)
        self.tabs = wid(QTabWidget(), "materials.library.tabs")
        self.tabs.tabBar().setUsesScrollButtons(False)         # (two tabs: they always fit; the arrows only clipped the first one)
        self.tabs.tabBar().setExpanding(False)
        self.tables: dict = {}
        for kind, title in (("solid", "Solids (parts)"), ("fluid", "Fluids (liquids and gases)")):
            t = QTableWidget(0, 6)
            t.setObjectName(f"materials.library.{kind}")
            t.setSelectionBehavior(QAbstractItemView.SelectRows)
            t.setSelectionMode(QAbstractItemView.SingleSelection)
            t.setEditTriggers(QAbstractItemView.NoEditTriggers)
            t.verticalHeader().setVisible(False)
            t.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
            for c in range(1, 6):
                t.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeToContents)
            t.itemSelectionChanged.connect(self._buttons)
            t.itemDoubleClicked.connect(lambda *_: self._edit())
            self.tables[kind] = t
            self.tabs.addTab(t, title)
        self.tabs.currentChanged.connect(lambda *_: self._buttons())
        v.addWidget(self.tabs, 1)
        self.msg = wid(QLabel(""), "materials.library.message")
        self.msg.setObjectName("muted")
        self.msg.setWordWrap(True)
        v.addWidget(self.msg)
        row = QHBoxLayout()
        self.b_add = wid(QPushButton("＋ Add…"), "materials.library.add")
        self.b_add.setToolTip("Add a material of your own")
        self.b_copy = wid(QPushButton("Copy as new…"), "materials.library.copy")
        self.b_copy.setToolTip("Add a new material that starts with the values of the selected one")
        self.b_edit = wid(QPushButton("Change…"), "materials.library.edit")
        self.b_rm = wid(QPushButton("Remove"), "materials.library.remove")
        self.b_close = wid(QPushButton("Close"), "materials.library.close")
        self.b_close.setObjectName("primary")
        for b in (self.b_add, self.b_copy, self.b_edit, self.b_rm):
            b.setObjectName("ghostBorder")
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(self.b_close)
        v.addLayout(row)
        self.b_add.clicked.connect(self._add)
        self.b_copy.clicked.connect(self._copy)
        self.b_edit.clicked.connect(self._edit)
        self.b_rm.clicked.connect(self._remove)
        self.b_close.clicked.connect(self.close)
        self.refresh()

    # ---- contents
    @property
    def kind(self) -> str:
        return "solid" if self.tabs.currentIndex() == 0 else "fluid"

    def selected(self):
        t = self.tables[self.kind]
        r = t.currentRow()
        it = t.item(r, 0) if r >= 0 else None
        return it.data(Qt.UserRole) if it is not None else None

    def select(self, kind: str, key: str):
        self.tabs.setCurrentIndex(0 if kind == "solid" else 1)
        t = self.tables[kind]
        for r in range(t.rowCount()):
            if t.item(r, 0).data(Qt.UserRole) == key:
                t.selectRow(r)
                return

    def refresh(self, keep=None):
        heads = {"solid": ["Name", "Density kg/m³", "Conductivity W/(m·K)", "Young's modulus GPa", "Yield MPa", "Source"],
                 "fluid": ["Name", "Type", "Density kg/m³", "Viscosity mPa·s", "Conductivity W/(m·K)", "Source"]}
        for kind, t in self.tables.items():
            tab = materials.SOLIDS if kind == "solid" else materials.FLUIDS
            t.setHorizontalHeaderLabels(heads[kind])
            t.setRowCount(len(tab))
            for r, (key, m) in enumerate(tab.items()):
                mine = materials.is_custom(kind, key)
                if kind == "solid":
                    cells = [m["label"], _fmt(m["rho"]), _fmt(m["k"]), _fmt(m["E"] / 1e9), _fmt(m["Sy"] / 1e6 if m.get("Sy") else None),
                             "yours" if mine else "built-in"]
                else:
                    cells = [m["label"], m.get("kind", "gas"), _fmt(m["rho"]), _fmt(m["mu"] * 1e3), _fmt(m["k"]), "yours" if mine else "built-in"]
                for c, text in enumerate(cells):
                    it = QTableWidgetItem(text)
                    it.setData(Qt.UserRole, key)
                    if c:
                        it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter if c in (1, 2, 3, 4) and not (kind == "fluid" and c == 1) else Qt.AlignLeft | Qt.AlignVCenter)
                    t.setItem(r, c, it)
        if keep:
            self.select(*keep)
        self._buttons()

    def _buttons(self):
        key = self.selected()
        mine = key is not None and materials.is_custom(self.kind, key)
        self.b_copy.setEnabled(key is not None)
        self.b_edit.setEnabled(mine)
        self.b_rm.setEnabled(mine)
        self.b_edit.setToolTip("Change the values of the selected material" if mine else
                               "Built-in materials cannot be changed: use Copy as new… and change the copy" if key else "Select one of your materials first")
        self.b_rm.setToolTip("Remove the selected material from the library (studies that use it keep their own copy)" if mine else
                             "Only the materials you added can be removed" if key else "Select one of your materials first")
        self.b_copy.setToolTip("Add a new material that starts with the values of the selected one" if key else "Select a material to copy")

    # ---- actions
    def _done(self, kind, key):
        self.msg.setText(f"Saved: {(materials.SOLIDS if kind == 'solid' else materials.FLUIDS)[key]['label']}.")
        self.refresh(keep=(kind, key))
        if self._on_change:
            self._on_change()

    def _add(self):
        k = self.kind
        self._open.append(open_material_dialog(k, self, on_done=lambda key, k=k: self._done(k, key)))

    def _copy(self):
        k, src = self.kind, self.selected()
        if src:
            self._open.append(open_material_dialog(k, self, on_done=lambda key, k=k: self._done(k, key), start=src))

    def _edit(self):
        k, src = self.kind, self.selected()
        if src and materials.is_custom(k, src):
            self._open.append(open_material_dialog(k, self, on_done=lambda key, k=k: self._done(k, key), edit_key=src))

    def _remove(self):
        k, key = self.kind, self.selected()
        if not key or not materials.is_custom(k, key):
            return
        label = (materials.SOLIDS if k == "solid" else materials.FLUIDS)[key]["label"]
        try:
            materials.remove_custom(k, key)
        except ValueError as e:
            self.msg.setText(str(e))
            return
        self.msg.setText(f"Removed {label}. Studies that use it keep their own copy.")
        self.refresh()
        if self._on_change:
            self._on_change()
