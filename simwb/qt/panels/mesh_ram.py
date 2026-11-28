"""The memory card of the Mesh step: how many cells and how much memory the mesh will need (worked out from the size fields before meshing, simwb/meshplan.py), a
memory budget with its state, and a bar of the estimate against the budget and the free memory.

The estimate is computed on a thread of its own (it takes up to a few seconds for a fine mesh of a big domain) and shown when it is ready; a change of the settings
while it runs starts it again for the new settings. The card is kept apart from mesh.py and mesh_adv.py: the panel only builds it and calls `refresh(state)`."""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QDoubleValidator, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from ... import meshplan
from .. import theme
from ..ids import wid
from .common import _muted, _seg

FREE_BUCKET_MB = 128.0


class MemBar(QWidget):
    """The estimated memory as a bar (with its range as a lighter stretch) against a mark for the budget and the free memory as the length of the track."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(26)
        self.setMinimumWidth(60)
        self.d: dict | None = None
        theme.follow(self, lambda w, *_: w.update())

    def set_data(self, d: dict | None):
        self.d = d
        tip = ""
        if d:
            tip = (f"Estimated memory {meshplan.fmt_mb(d['mb'])} (range {meshplan.fmt_mb(d['low'])} to {meshplan.fmt_mb(d['high'])})"
                   + (f"; budget {meshplan.fmt_mb(d['budget'])}" if d.get("budget") else "")
                   + (f"; free now {meshplan.fmt_mb(d['free'])}" if d.get("free") else ""))
        self.setToolTip(tip)
        self.update()

    def paintEvent(self, _e):
        d = self.d
        if not d:
            return
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width() - 2, 12
        y = 4
        scale = max(d.get("free") or 0.0, d["high"], d.get("budget") or 0.0) * 1.04 or 1.0
        x = lambda mb: 1 + w * min(max(mb, 0.0) / scale, 1.0)  # noqa: E731
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(t["panel2"]))
        p.drawRoundedRect(QRectF(1, y, w, h), 3, 3)                         # the track: the free memory
        over_budget = bool(d.get("budget")) and d["mb"] > d["budget"]
        over_free = bool(d.get("free")) and d["mb"] > d["free"]
        col = QColor(t["bad"] if over_free else (t["warn"] if over_budget else t["accent"]))
        soft = QColor(col)
        soft.setAlpha(90)
        p.setBrush(soft)
        p.drawRoundedRect(QRectF(1, y, max(x(d["high"]) - 1, 2), h), 3, 3)  # the range
        p.setBrush(col)
        p.drawRoundedRect(QRectF(1, y, max(x(d["mb"]) - 1, 2), h), 3, 3)    # the estimate
        if d.get("budget"):
            p.setPen(QPen(QColor(t["ink"]), 2))
            bx = x(d["budget"])
            p.drawLine(int(bx), y - 3, int(bx), y + h + 3)                  # the budget
        p.end()


class _Result(QObject):
    done = Signal(object, object)          # (key, report or an error text)


class MemoryCard(QWidget):
    """The memory budget (off, MB, or percent of the free memory), the estimate of the mesh and what the budget did."""

    def __init__(self, panel, parent=None):
        super().__init__(parent)
        self.panel = panel
        self.engine = panel.engine
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        self.mode_row, self.mode_grp, self.mode_btns = _seg([("off", "Off"), ("mb", "MB"), ("percent", "% of free memory")], "off", prefix="mesh.budget")
        for k, b in self.mode_btns.items():
            b.clicked.connect(lambda _=False, k=k: self._mode(k))
        self.mode_btns["off"].setToolTip("No memory budget: the mesh is made as the sizes say")
        self.mode_btns["mb"].setToolTip("Keep the mesh within this many MB: the cells away from the body are made larger until it fits")
        self.mode_btns["percent"].setToolTip("Keep the mesh within this share of the memory that is free now")
        v.addWidget(self.mode_row)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self.value = wid(QLineEdit(), "mesh.budget.value")
        self.value.setValidator(QDoubleValidator(1.0, 4_000_000.0, 1, self.value))
        self.value.setFixedWidth(90)
        self.value.setToolTip("The most memory the mesh and the run may need")
        self.value.editingFinished.connect(self._value)
        self.unit = wid(QLabel("MB"), "mesh.budget.unit")
        row.addWidget(self.value)
        row.addWidget(self.unit)
        row.addStretch(1)
        self.value_row = QWidget()
        self.value_row.setLayout(row)
        v.addWidget(self.value_row)
        self.estimate = wid(QLabel(""), "mesh.estimate")
        self.estimate.setWordWrap(True)
        self.estimate.setTextFormat(Qt.RichText)
        v.addWidget(self.estimate)
        self.bar = wid(MemBar(), "mesh.estimate.bar")
        v.addWidget(self.bar)
        self.detail = wid(_muted(), "mesh.estimate.detail")
        v.addWidget(self.detail)
        self.state = wid(QLabel(""), "mesh.budget.state")
        self.state.setWordWrap(True)
        self.state.setTextFormat(Qt.RichText)
        v.addWidget(self.state)
        self.changes = wid(_muted(), "mesh.budget.changes")
        self.changes.setTextFormat(Qt.RichText)
        v.addWidget(self.changes)
        self.off_btn = wid(QPushButton("Switch the budget off"), "mesh.budget.switch_off")
        self.off_btn.setObjectName("ghostBorder")
        self.off_btn.setToolTip("Make the mesh as the sizes say, without the memory budget")
        self.off_btn.clicked.connect(lambda: self._mode("off"))
        v.addWidget(self.off_btn)
        self.notes = wid(_muted(), "mesh.estimate.notes")
        v.addWidget(self.notes)
        self._key = None
        self._report: dict | None = None
        self._running: threading.Thread | None = None
        self._pending = None
        self._res = _Result()
        self._res.done.connect(self._finished)
        self.last: dict | None = None
        theme.follow(self, lambda w, *_: w._show())

    # ------------------------------------------------------------------ the controls
    def _budget(self) -> dict | None:
        if not self.engine.pid:
            return None
        return self.engine.project().data["mesh"].get("budget")

    def _mode(self, k: str):
        try:
            pr = self.engine.project()
            old = pr.data["mesh"].get("budget") or {}
            if k == "off":
                pr.set_mesh(budget=False)
            else:
                val = old.get("value") if old.get("mode") == k else (4000.0 if k == "mb" else 50.0)
                if k == "mb" and old.get("mode") != "mb":
                    val = self._suggest_mb()
                pr.set_mesh(budget={"mode": k, "value": val, "on": True})
            self.engine.changed.emit()
        except Exception as e:  # noqa: BLE001
            self.panel._fail(e)
            self.engine.changed.emit()

    def _suggest_mb(self) -> float:
        """A first budget in MB: half of the free memory, rounded to 250 MB."""
        free = meshplan.free_ram_mb()
        return float(max(500, round((free or 8000.0) * 0.5 / 250.0) * 250))

    def _value(self):
        try:
            val = float(self.value.text().replace(",", "."))
        except ValueError:
            self.engine.changed.emit()
            return
        b = self._budget() or {}
        try:
            self.engine.project().set_mesh(budget={"mode": b.get("mode") or "mb", "value": val, "on": True})
            self.engine.changed.emit()
        except Exception as e:  # noqa: BLE001
            self.panel._fail(e)
            self.engine.changed.emit()

    # ------------------------------------------------------------------ the estimate (a thread)
    def _key_of(self, st) -> tuple:
        pr = self.engine.project()
        m = {k: v for k, v in pr.data["mesh"].items() if k not in ("stats", "status")}
        free = meshplan.free_ram_mb()
        return (pr.id, st["geometry"].get("updated"), st["geometry"].get("status"), repr(sorted(m.items(), key=lambda kv: kv[0])), repr(pr.data.get("settings")),
                repr(pr.data.get("moving_zones")), repr(pr.data.get("rotors")), round((free or 0) / FREE_BUCKET_MB) if (m.get("budget") or {}).get("mode") == "percent" else 0)

    def refresh(self, st):
        """Show the card for the state `st`; start the estimate when the settings changed since the last one."""
        if not st or not self.engine.pid:
            self.setVisible(False)
            return
        hex_ = (st["mesh"].get("method") == "hex" and st["study"] == "external_aero")
        ready = st["geometry"].get("status") == "ready"
        self.setVisible(True)
        b = st["mesh"].get("budget") or {}
        on = bool(b.get("on", True) and b.get("value"))
        k = b.get("mode") if on else "off"
        self.mode_btns[k if k in self.mode_btns else "off"].setChecked(True)
        self.mode_row.setEnabled(ready)
        self.value_row.setVisible(on and not hex_)
        if on:
            self.unit.setText("MB" if b.get("mode") == "mb" else "% of the free memory")
            if not self.value.hasFocus():
                self.value.setText(f"{b['value']:g}")
        if hex_ or not ready:
            self._show_text("The estimate is for the tetrahedral mesh." if hex_ else "Import the geometry to see how many cells and how much memory the mesh needs.",
                            muted=True)
            self.mode_row.setVisible(not hex_)
            if hex_:
                self.estimate.setText("")
                self.detail.setText("The hex-dominant mesh is not estimated: how many cells snappyHexMesh makes depends on how it cuts the surface, which is only known when it has done it.")
                self.detail.setVisible(True)
            self._clear_rest()
            return
        self.mode_row.setVisible(True)
        key = self._key_of(st)
        if key == self._key and self._report is not None:
            self._show()
            return
        self._key = key
        if self._running is not None and self._running.is_alive():
            self._pending = key                          # (the settings changed while it runs: again afterwards)
            return
        self._start(key)

    def _start(self, key):
        self._pending = None
        if self._report is None:
            self._show_text("Estimating the cells and the memory…", muted=True)
        pr = self.engine.project()

        def work():
            try:
                rep = meshplan.report(pr)
            except Exception as e:  # noqa: BLE001 - a help: the card says it could not
                rep = str(e)
            self._res.done.emit(key, rep)

        self._running = threading.Thread(target=work, daemon=True)
        self._running.start()

    def _finished(self, key, rep):
        if isinstance(rep, str):
            self._report = {"available": False, "reason": "The estimate could not be made: " + rep}
        elif key == self._key:
            self._report = rep
        if self._pending is not None:
            k = self._pending
            self._pending = None
            self._start(k)
        self._show()

    def wait(self, timeout: float = 120.0) -> None:
        """Wait for the estimate that is running (tests and the control API)."""
        from PySide6.QtCore import QCoreApplication
        import time
        t0 = time.time()
        while time.time() - t0 < timeout:
            QCoreApplication.processEvents()
            alive = self._running is not None and self._running.is_alive()
            if not alive and self._pending is None:
                QCoreApplication.processEvents()
                break
            time.sleep(0.02)

    # ------------------------------------------------------------------ what is shown
    def _clear_rest(self):
        for w in (self.bar, self.state, self.changes, self.off_btn, self.notes):
            w.setVisible(False)
        self.bar.set_data(None)

    def _show_text(self, text: str, muted: bool = False):
        t = theme.tokens()
        self.estimate.setText(f"<span style='color:{t['muted']}'>{text}</span>" if muted else text)
        self.estimate.setProperty("api_text", text)
        self.detail.setVisible(False)
        self._clear_rest()

    def _show(self):
        rep = self._report
        self.last = rep
        if not rep:
            return
        t = theme.tokens()
        if not rep.get("available"):
            self._show_text(rep.get("reason") or "No estimate.", muted=True)
            return
        e, bud = rep["estimate"], rep.get("budget") or {}
        free = rep.get("free_mb")
        shown = rep.get("applied") if (bud.get("status") in ("coarsened", "refused") and rep.get("applied")) else e
        cells = f"About <b>{meshplan.fmt_cells(shown['cells'])}</b> cells"
        rng = f" ({meshplan.fmt_cells(shown['cells_low'])} to {meshplan.fmt_cells(shown['cells_high'])})"
        mem = f", about <b>{meshplan.fmt_mb(shown['peak_mb'])}</b> of memory (range {meshplan.fmt_mb(shown['peak_low_mb'])} to {meshplan.fmt_mb(shown['peak_high_mb'])})"
        txt = cells + rng + mem + "."
        plain = (f"About {meshplan.fmt_cells(shown['cells'])} cells (range {meshplan.fmt_cells(shown['cells_low'])} to {meshplan.fmt_cells(shown['cells_high'])}), "
                 f"about {meshplan.fmt_mb(shown['peak_mb'])} of memory (range {meshplan.fmt_mb(shown['peak_low_mb'])} to {meshplan.fmt_mb(shown['peak_high_mb'])}).")
        if bud.get("status") in ("coarsened", "refused"):
            txt = ("With the budget: " if bud["status"] == "coarsened" else "Smallest the budget can make: ") + txt[0].lower() + txt[1:]
            plain = ("With the budget: " if bud["status"] == "coarsened" else "Smallest the budget can make: ") + plain[0].lower() + plain[1:]
        self.estimate.setText(txt)
        self.estimate.setProperty("api_text", plain)
        if shown.get("run_mb") is not None:
            self.detail.setText(f"Making the mesh needs about {meshplan.fmt_mb(shown['mesh_mb'])}; running the simulation on it about {meshplan.fmt_mb(shown['run_mb'])} "
                                "(from earlier runs). The estimate is for the tetrahedral mesh, before the wall layers.")
        else:
            self.detail.setText(f"Making the mesh needs about {meshplan.fmt_mb(shown['mesh_mb'])}. The estimate is for the tetrahedral mesh.")
        self.detail.setVisible(True)
        self.bar.setVisible(True)
        self.bar.set_data({"mb": shown["peak_mb"], "low": shown["peak_low_mb"], "high": shown["peak_high_mb"], "budget": bud.get("mb") if bud.get("on") else None,
                           "free": free})
        marks = [f"free now {meshplan.fmt_mb(free)}"] if free else []
        if bud.get("on") and bud.get("mb"):
            marks.insert(0, f"budget {meshplan.fmt_mb(bud['mb'])}")
        self.bar.setProperty("api_text", ", ".join(marks))
        # the budget: its state, in words, and what it changed
        st = bud.get("status")
        if bud.get("on") and st:
            col, sym, head = {"fits": (t["ok_text"], "✓", "Budget on: the mesh fits"), "coarsened": (t["ok_text"], "✓", "Budget on: the bulk is coarsened"),
                              "refused": (t["bad_text"], "✗", "Budget on: refused")}[st]
            self.state.setText(f"<span style='color:{col}'><b>{sym} {head}.</b></span> {bud['message']}")
            self.state.setProperty("api_text", f"{head}. {bud['message']}")
            self.state.setVisible(True)
            ch = bud.get("changes") or {}
            names = {"far_size": "Far field", "near_field": "Near field", "wake": "Wake", "body_size": "Cells away from the body", "growth": "Growth"}
            rows = []
            for k, (a, b2) in ch.items():
                if k not in names or (k == "growth" and b2 <= a * 1.0005):
                    continue
                rows.append(f"{names[k]}: {a:.2f} → {b2:.2f}" if k == "growth" else f"{names[k]}: {meshplan.fmt_len(a)} → {meshplan.fmt_len(b2)}")
            self.changes.setText("<br>".join(rows) if (rows and st != "fits") else "")
            self.changes.setVisible(bool(rows) and st != "fits")
            self.changes.setProperty("api_text", "; ".join(rows) if st != "fits" else "")
            self.off_btn.setVisible(True)
        else:
            self.state.setVisible(False)
            self.changes.setVisible(False)
            self.off_btn.setVisible(False)
        notes = [n for n in rep.get("notes") or [] if n]
        self.notes.setText(" ".join(notes))
        self.notes.setVisible(bool(notes))
