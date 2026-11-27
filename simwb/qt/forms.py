"""Forms made from the engine's own parameter definitions (simwb.studies): a list of {key, label, unit, default, options, help, min, max,
show_if, adv; and for forms made in the Qt app: option_labels {value: words}, placeholder (text of an empty field), blank (an emptied field
means None, 'automatic')}. The same code builds the form of a boundary condition, of a body, and of a study's settings, for every study type.

Each control has an API id ("<prefix>.<key>"), a visible label with its unit, and a tooltip with the engine's help text; parameters whose
`show_if` is not met are hidden, and `adv` ones are hidden in Simple mode."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import QComboBox, QFormLayout, QLabel, QLineEdit, QWidget

from .ids import wid


def fmt_num(v) -> str:
    """A number as short text (no trailing zeros): 20, 0.5, 1e-06."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{f:.6g}"


def _met(show_if: dict | None, values: dict) -> bool:
    if not show_if:
        return True
    for k, want in show_if.items():
        have = values.get(k)
        ok = (str(have) in [str(x) for x in want]) if isinstance(want, (list, tuple)) else str(have) == str(want)
        if not ok:
            return False
    return True


class ParamForm(QWidget):
    changed = Signal(str, object)      # (key, new value) after the user finished editing one

    def __init__(self, params: list, values: dict, prefix: str, simple: bool = False, parent=None):
        super().__init__(parent)
        self.params = {p["key"]: p for p in params}
        self.prefix = prefix
        self.simple = simple
        self.vals = dict(values)
        self.rows: dict = {}          # key -> (label, control, explain)
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(6)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.form = form
        for p in params:
            k = p["key"]
            cur = self.vals.get(k, p.get("default"))
            unit = f" [{p['unit']}]" if p.get("unit") else ""
            lab = QLabel(f"{p['label']}{unit}")
            lab.setWordWrap(True)
            if p.get("help"):
                lab.setToolTip(p["help"])
            if p.get("options"):
                c = wid(QComboBox(), f"{prefix}.{k}")
                c.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
                c.setMinimumContentsLength(8)
                names = p.get("option_labels") or {}         # (a word for each value: 'MRF (steady)' for 'mrf')
                for o in p["options"]:
                    c.addItem(str(names.get(o, o)), o)
                i = c.findData(cur)
                c.setCurrentIndex(max(i if i >= 0 else c.findText(str(cur)), 0))
                c.currentIndexChanged.connect(lambda i, k=k, c=c: self._edited(k, c.itemData(i)))
            else:
                c = wid(QLineEdit(fmt_num(cur) if cur not in (None, "") else ""), f"{prefix}.{k}")
                if p.get("placeholder"):                     # (empty means automatic: the field says so)
                    c.setPlaceholderText(str(p["placeholder"]))
                v = QDoubleValidator(c)
                v.setNotation(QDoubleValidator.ScientificNotation)
                if "min" in p:
                    v.setBottom(float(p["min"]))
                if "max" in p:
                    v.setTop(float(p["max"]))
                c.setValidator(v)
                c.editingFinished.connect(lambda k=k, c=c: self._edited(k, c.text()))
            oh = p.get("option_help") or {}
            c.setToolTip(p.get("help") or ("  ".join(f"{o}: {t}" for o, t in oh.items()) if oh else f"{p['label']}{unit}"))
            c.setAccessibleName(f"{p['label']}{unit}")
            expl = QLabel("")
            expl.setObjectName("muted")
            expl.setWordWrap(True)
            expl.setVisible(False)
            col = QWidget()
            from PySide6.QtWidgets import QVBoxLayout
            cl = QVBoxLayout(col)
            cl.setContentsMargins(0, 0, 0, 0)
            cl.setSpacing(2)
            cl.addWidget(c)
            cl.addWidget(expl)
            form.addRow(lab, col)
            self.rows[k] = (lab, c, expl, col)
        self._show_help()
        self._visibility()

    def _edited(self, key: str, text):
        p = self.params[key]
        if p.get("options"):
            val = text
        elif p.get("blank") and str(text).strip() == "":     # (emptied: automatic)
            val = None
        else:
            try:
                val = float(text)
            except ValueError:
                return                       # (an empty or half-typed number: keep the last good value)
            if val == self.vals.get(key):
                return
        if val == self.vals.get(key):
            return
        self.vals[key] = val
        self._show_help()
        self._visibility()
        self.changed.emit(key, val)

    def show_value(self, key: str, value, force: bool = False):
        """Show a value that did not come from typing (a handle in the 3D view is being dragged): nothing is emitted, the typed number and the handle agree."""
        row = self.rows.get(key)
        if row is None:
            return
        c = row[1]
        self.vals[key] = value
        try:
            if hasattr(c, "setText"):
                if force or not c.hasFocus():
                    c.setText(fmt_num(value) if value not in (None, "") else "")
            else:
                i = c.findData(value)
                if i >= 0 and i != c.currentIndex():
                    c.blockSignals(True)
                    c.setCurrentIndex(i)
                    c.blockSignals(False)
        except RuntimeError:
            self.rows.pop(key, None)

    def _show_help(self):
        for k, (lab, c, expl, col) in self.rows.items():
            p = self.params[k]
            why = (p.get("option_help") or {}).get(str(self.vals.get(k)))
            expl.setText(why or "")
            expl.setVisible(bool(why))

    def _visibility(self):
        for k, (lab, c, expl, col) in self.rows.items():
            p = self.params[k]
            on = _met(p.get("show_if"), self.vals) and not (self.simple and p.get("adv"))
            lab.setVisible(on)
            col.setVisible(on)

    def values(self) -> dict:
        return dict(self.vals)
