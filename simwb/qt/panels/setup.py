"""The setup step panel."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)
from ... import materials
from ..ids import wid
from .common import _h, _muted, _Panel


# ---------------------------------------------------------------------------------------------------------- setup
class SetupPanel(_Panel):
    def __init__(self, engine, parent=None):
        super().__init__(engine, parent)
        self.v.addWidget(_h("Materials"))
        self.mats = QVBoxLayout()
        self.v.addLayout(self.mats)
        self.v.addWidget(_h("Supports, loads and sources"))
        self.bcs = wid(_muted(), "setup.bcs")
        self.v.addWidget(self.bcs)
        note = _muted("Editing supports and loads here is being ported. Until then, describe them to the Claude assistant "
                      "(\"fix the two bolt holes, 20 N on the front face\") and it sets them up.")
        self.v.addWidget(note)
        self.v.addStretch(1)
        self._key = None

    def refresh(self, st):
        if not st:
            return
        key = tuple((b["id"], b.get("material")) for b in st["body_list"])
        if key != self._key:
            self._key = key
            while self.mats.count():
                w = self.mats.takeAt(0).widget()
                if w:
                    w.deleteLater()
            for b in st["body_list"]:
                if b["role"] == "fluid":
                    self.mats.addWidget(QLabel(f"{b['name']} — air"))
                    continue
                row = QWidget()
                r = QHBoxLayout(row)
                r.setContentsMargins(0, 0, 0, 0)
                r.addWidget(QLabel(b["name"]), 1)
                cb = wid(QComboBox(), f"setup.material.{b['id']}")
                for k, m in materials.SOLIDS.items():
                    cb.addItem(m["label"], k)
                i = cb.findData(b.get("material"))
                cb.setCurrentIndex(max(i, 0))
                cb.currentIndexChanged.connect(lambda _=0, c=cb, bid=b["id"]: self._set_material(bid, c.currentData()))
                r.addWidget(cb)
                self.mats.addWidget(row)
        bcs = st.get("bcs") or []
        self.bcs.setText("<br>".join(f"<b>{b['name']}</b> — {b['type'].replace('_', ' ')}, {len(b.get('faces', []))} face(s)" for b in bcs)
                         or "None yet.")

    def _set_material(self, body_id: int, key: str):
        try:
            self.engine.project().set_body(body_id, material=key)
            self.engine.changed.emit()
        except Exception as e:  # noqa: BLE001
            self._fail(e)
