"""The application shell of the Qt window: what surrounds the steps and the 3D view and is the same in every step.

* the status bar's job part: the running job's message, progress bar and a Cancel button
* toasts for what finished or failed (simwb/qt/toasts.py), instead of boxes that wait for a click
* the 3D view's Probe button and section bar, linked to the panel of the step (the panel's own controls stay the one source of truth)
* the face under the pointer as a tooltip, and the selection hint in the bar under the 3D view (what to click next)
* keyboard shortcuts

`install(win)` is the one call the main window makes. `register(control)` adds the control-API actions of all of this (docs/PARALLEL_WORK.md).
The panels are only read and driven through the widgets a person would use."""
from __future__ import annotations

from PySide6.QtCore import QObject, QPoint, Qt, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QLabel, QPushButton, QStatusBar, QToolTip

from .. import project as P
from .. import studies
from .ids import wid
from .toasts import ToastHost
from .viewtools import KEYS

JOB_LABELS = {"prepare": "Geometry", "mesh": "Mesh", "solve": "Solution", "check": "Check", "install": "Install", "video": "Video"}
DONE_TEXT = {"prepare": "Geometry ready", "mesh": "Mesh ready", "solve": "Solution finished", "check": "Check finished"}
KIND_LABEL = {"body": "body surface", "farfield": "far-field", "enclosure": "air boundary", "solid_outer": "part outer face",
              "interface": "contact between bodies", "boundary": "outer face"}
STEP = 12                      # the section plane moves by this much (of 1000) every tick of the play sweep
TICK_MS = 150


class ElideLabel(QLabel):
    """A label that is cut with ... when the status bar is narrow (the whole text stays in the tooltip)."""

    def __init__(self, *a):
        super().__init__(*a)
        self.full = ""
        self.setMinimumWidth(60)

    def setFull(self, text: str):
        self.full = text
        self._fit()
        self.setToolTip(text)

    def _fit(self):
        w = max(self.width(), 60)
        super().setText(self.fontMetrics().elidedText(self.full, Qt.ElideRight, w))

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._fit()


class ToastStatusBar(QStatusBar):
    """The status bar: a message that stays a while (an outcome: "Video saved", "Exported to ...") is also shown as a toast, so it is seen
    even when the eye is on the 3D view; the short running notes ("Video: picture 3 of 40") are not."""

    def __init__(self, win):
        super().__init__(win)
        self.win = win

    def showMessage(self, text: str, timeout: int = 0):  # noqa: N802
        super().showMessage(text, timeout)
        toasts = getattr(self.win, "toasts", None)
        if toasts is not None and text and timeout >= 8000:
            toasts.show(text, "warn" if text.startswith(("Cannot", "Could not")) else "info")


class Shell(QObject):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.toasts: ToastHost = win.toasts
        self.jobs: dict = {}                  # id -> job dict, the running jobs of the open study
        self.current: dict | None = None      # the job the status bar shows
        self._told: set = set()               # (job id, status) already announced
        self.facts: dict = {}                 # what the step panels last said (study kind, solve and mesh status, faces of the conditions)
        self._hint_cb: dict = {}
        self.hover_text = ""
        self._build_status()
        self._link_probe()
        self._link_section()
        win.view.faceHovered.connect(self.hover)
        win.assistant.notice.connect(self.toasts.show)
        t = QTimer(self)
        t.timeout.connect(self.sync)
        t.start(250)
        # keys: Ctrl+K takes the cursor to the Claude message box; Esc answers the hint bar (Done / Cancel), else closes the newest toast
        QShortcut(QKeySequence("Ctrl+K"), win, activated=self.focus_assistant)
        QShortcut(QKeySequence("Esc"), win, activated=self.escape)

    def focus_assistant(self):
        w = self.win
        if w._at_home:
            return
        w.dock_chat.show()
        w.assistant.input.setFocus()

    def escape(self):
        """Esc: finish what the hint bar asks about (Done / Cancel), or else close the newest toast."""
        b = self.win.view.banner
        if b.isVisible() and b.button.isVisible() and b.button.isEnabled():
            b.button.click()
        elif self.toasts.items:
            self.toasts.close_one(-1)

    # ------------------------------------------------------------------ the status bar: job message, progress, Cancel
    def _build_status(self):
        w = self.win
        sb = w.statusBar()
        self.job_msg = wid(ElideLabel(""), "status.job")
        self.job_msg.setMaximumWidth(440)
        self.job_msg.setToolTip("What the running job (meshing, solving ...) is doing")
        self.cancel = wid(QPushButton("Cancel"), "status.cancel")
        self.cancel.setObjectName("viewbtn")
        self.cancel.setToolTip("Stop the running job (a stopped run keeps no results)")      # (the running job's own name replaces this while it runs)
        self.cancel.clicked.connect(self.cancel_job)
        self.job_msg.setMinimumWidth(240)
        for x in (w.progress, w.lbl_saved):               # (the right end, in this order: what the job says, its bar, Cancel, when it was saved)
            sb.removeWidget(x)
        for x in (self.job_msg, w.progress, self.cancel, w.lbl_saved):
            sb.addPermanentWidget(x)
            x.show() if x is w.lbl_saved else None
        self.job_msg.hide()
        self.cancel.hide()

    def sync_jobs(self):
        """A study was opened: show the jobs that are already running in it (the window may have been opened on a working study)."""
        self.jobs = {j.id: j.to_dict(log_tail=0) for j in P.JOBS.jobs.values() if j.status == "running" and j.project == self.win.engine.pid}
        self._show_job()

    def job_event(self, ev: dict):
        j = ev.get("job") or {}
        jid, st = j.get("id"), j.get("status")
        if st == "running":
            if (self.jobs.get(jid) or {}).get("cancel_requested"):
                j["cancel_requested"] = True
            self.jobs[jid] = j
        else:
            self.jobs.pop(jid, None)
        self._show_job()
        if st in ("done", "failed", "cancelled") and (jid, st) not in self._told:
            self._told.add((jid, st))
            kind = j.get("kind", "")
            label = JOB_LABELS.get(kind, str(kind).capitalize())
            if st == "failed":
                self.toasts.error(f"{label} failed: {j.get('error') or j.get('message')}")
            elif st == "cancelled":
                self.toasts.show(f"{label} stopped")
            elif kind == "check":
                self.toasts.ok(f"{str((j.get('extra') or {}).get('check_kind') or 'check').capitalize()} finished")
            else:
                self.toasts.ok(DONE_TEXT.get(kind, f"{label} done"))

    def _show_job(self):
        w = self.win
        self.current = list(self.jobs.values())[-1] if self.jobs else None
        j = self.current
        running = j is not None
        w.progress.setVisible(running)
        self.cancel.setVisible(running)
        self.job_msg.setVisible(running)
        if not running:
            return
        label = JOB_LABELS.get(j.get("kind", ""), str(j.get("kind", "")).capitalize())
        p = float(j.get("progress") or 0.0)
        if p > 0:
            w.progress.setRange(0, 1000)
            w.progress.setValue(int(p * 1000))
        else:
            w.progress.setRange(0, 0)                   # (no figure yet: the bar moves instead)
        w.progress.setToolTip(f"{label}: {int(p * 100)} %")
        self.job_msg.setFull(f"{label}: {j.get('message') or 'working'}")
        self.cancel.setEnabled(not j.get("cancel_requested"))
        self.cancel.setText("Cancel" if not j.get("cancel_requested") else "Stopping…")
        self.cancel.setToolTip(f"Stop the {label.lower()} job (a stopped run keeps no results)" if not j.get("cancel_requested")
                               else "The job is being stopped")

    def cancel_job(self):
        """The Cancel button of the status bar: stop the job it shows."""
        j = self.current
        if not j:
            raise ValueError("No job is running in this study")
        P.JOBS.cancel(j["id"])
        j["cancel_requested"] = True
        self._show_job()
        self.toasts.show(f"Stopping the {JOB_LABELS.get(j.get('kind', ''), j.get('kind', 'job')).lower()} job…")

    # ------------------------------------------------------------------ facts: what the panels last drew from
    def sync(self, st: dict | None = None, force: bool = False):
        """Called after the window redraws (with the study's state) and by a timer: bring the view's own controls in line with the panels."""
        w = self.win
        if st is not None or force:
            if st is None:
                self.facts = {}
            else:
                bcs = {}
                for b in st.get("bcs") or []:
                    for f in b.get("faces") or []:
                        bcs[int(f)] = b.get("name", "")
                self.facts = {"study": st["study"], "solve": st["solve"].get("status"), "mesh": st["mesh"].get("status"), "bcs": bcs,
                              "bodies": {b["id"]: b["name"] for b in st.get("body_list") or []},
                              "stamp": (st["solve"].get("finished"), (st["mesh"].get("stats") or {}).get("cells"), st["geometry"].get("updated")),
                              "bbox": (st["geometry"].get("info") or {}).get("bbox")}
        if not w.engine.pid or w.center.currentWidget() is w.home:
            return
        self._sync_probe()
        self._sync_section()
        self._sync_hint()

    def results_ready(self) -> bool:
        return self.win.step == "results" and self.facts.get("solve") in ("done", "stale")

    # ------------------------------------------------------------------ Probe in the view's bar
    def _link_probe(self):
        bar = self.win.view.viewbar
        rp = self.win.panels["results"]
        bar.probe.toggled.connect(self._probe_clicked)
        rp.probe_btn.toggled.connect(lambda on: bar.probe.setChecked(bool(on)) if bar.probe.isChecked() != bool(on) else None)

    def _probe_clicked(self, on: bool):
        rp = self.win.panels["results"]
        if rp.probe_btn.isChecked() != bool(on):
            rp.probe_btn.setChecked(bool(on))

    def _sync_probe(self):
        bar = self.win.view.viewbar
        ok = self.results_ready()
        if bar.probe.isEnabled() != ok:
            why = ("Probe reads the value of a result at a point: run the simulation first, then open the Results step" if self.win.step == "results"
                   or self.facts.get("solve") not in ("done", "stale") else
                   "Probe reads the value of a result at a point: open the Results step")
            bar.set_probe_usable(ok, why)
        if not ok and bar.probe.isChecked():
            bar.probe.setChecked(False)
        elif ok and bar.probe.isChecked() != self.win.panels["results"].probe_btn.isChecked():
            bar.probe.blockSignals(True)
            bar.probe.setChecked(self.win.panels["results"].probe_btn.isChecked())
            bar.probe.blockSignals(False)

    # ------------------------------------------------------------------ Section in the view's bar
    def _link_section(self):
        s = self.win.view.secbar
        self._ext: dict = {}
        self._play = QTimer(self)
        self._play.timeout.connect(self._tick)
        s.axisChosen.connect(self._axis_chosen)
        s.positionChosen.connect(self._pos_chosen)
        s.moved.connect(lambda v: self._value_label(v))
        s.playToggled.connect(self._play_toggled)
        s.hideAirToggled.connect(self._hide_air_toggled)

    def _target(self) -> dict | None:
        """The panel controls the section bar works: the Results step (the section) or the Mesh step (the cut)."""
        w, f = self.win, self.facts
        if w.step == "results" and f.get("solve") in ("done", "stale"):
            p = w.panels["results"]
            return {"kind": "results", "btns": p.section_btns, "slider": p.section_pos,
                    "air": p.hide_air if f.get("study") == "cht_enclosure" else None}
        if w.step == "mesh" and f.get("mesh") in ("ready", "stale"):
            p = w.panels["mesh"]
            return {"kind": "mesh", "btns": p.cut_btns, "slider": p.slider, "air": None}
        return None

    def section_state(self) -> dict:
        t = self._target()
        s = self.win.view.secbar
        if t is None:
            return {"visible": False}
        axis = next((k for k, b in t["btns"].items() if k and b.isChecked()), "")
        return {"visible": True, "kind": t["kind"], "axis": axis, "position": t["slider"].value() / 1000, "value": s.value.text(),
                "playing": self._play.isActive(), "hide_air": None if t["air"] is None else t["air"].isChecked()}

    def _sync_section(self):
        s = self.win.view.secbar
        t = self._target()
        if t is None:
            if s.isVisibleTo(self.win.view):
                self._stop_play()
                s.setVisible(False)
            return
        if not s.isVisibleTo(self.win.view):
            s.setVisible(True)
        axis = next((k for k, b in t["btns"].items() if k and b.isChecked()), "")
        if next((k for k, b in s.btns.items() if b.isChecked()), "") != axis:
            s.set_axis(axis)
        elif s.slider.isEnabled() != bool(axis):
            s.set_axis(axis)
        if not s.slider.isSliderDown() and s.slider.value() != t["slider"].value():
            s.set_pos(t["slider"].value())
        if not axis and self._play.isActive():
            self._stop_play()
        s.hide_air.setVisible(t["air"] is not None)
        if t["air"] is not None and s.hide_air.isChecked() != t["air"].isChecked():
            s.hide_air.blockSignals(True)
            s.hide_air.setChecked(t["air"].isChecked())
            s.hide_air.blockSignals(False)
        if axis:
            self._value_label(t["slider"].value())

    def _extent(self, kind: str, axis: str):
        """(low, high) of the model along an axis in metres, for the value of the plane; None when it cannot be told."""
        key = (self.win.engine.pid, kind, axis, self.facts.get("stamp"))
        if key in self._ext:
            return self._ext[key]
        ext = None
        try:
            i = "xyz".index(axis)
            pr = self.win.engine.project()
            if kind == "results":
                rs = pr.results()
                box = self.win.engine._flow_box(rs) if self.facts.get("study") in ("external_aero", "cht_enclosure") else None
                ext = (float(box[i]), float(box[3 + i])) if box else (float(rs.nodes[:, i].min()), float(rs.nodes[:, i].max()))
            else:
                nodes = pr.load_mesh()["nodes"]
                ext = (float(nodes[:, i].min()), float(nodes[:, i].max()))
        except Exception:  # noqa: BLE001
            ext = None
        if len(self._ext) > 40:
            self._ext.clear()
        self._ext[key] = ext
        return ext

    def _value_label(self, v: int):
        s, t = self.win.view.secbar, self._target()
        if t is None:
            return
        axis = next((k for k, b in t["btns"].items() if k and b.isChecked()), "")
        if not axis:
            s.value.setText("")
            return
        e = self._extent(t["kind"], axis)
        s.value.setText(f"{axis} = {(e[0] + (e[1] - e[0]) * v / 1000) * 1000:.1f} mm" if e else f"{axis}: {v / 10:.0f} %")

    def _axis_chosen(self, axis: str):
        t = self._target()
        if t is None:
            return
        t["btns"][axis].click()                         # (the panel's own button: its handler redraws)
        if axis and t["kind"] == "results" and self.facts.get("study") not in ("external_aero", "cht_enclosure"):
            self.win.view.set_view(f"+{axis}")          # (as the web version: look straight at the cut)
        self._sync_section()

    def _pos_chosen(self, v: int):
        t = self._target()
        if t is not None and t["slider"].value() != v:
            t["slider"].setValue(v)
        self._value_label(v)

    def _hide_air_toggled(self, on: bool):
        t = self._target()
        if t is not None and t["air"] is not None and t["air"].isChecked() != bool(on):
            t["air"].setChecked(bool(on))

    def _play_toggled(self, on: bool):
        s = self.win.view.secbar
        t = self._target()
        if on and (t is None or not any(k and b.isChecked() for k, b in t["btns"].items())):
            s.set_playing(False)
            return
        s.set_playing(on)
        if on:
            if t["slider"].value() >= 1000 - STEP:
                t["slider"].setValue(0)
            self._play.start(TICK_MS)
        else:
            self._play.stop()

    def _stop_play(self):
        self._play.stop()
        self.win.view.secbar.set_playing(False)

    def _tick(self):
        """One step of the sweep: the plane moves on; at the far end it stops."""
        t = self._target()
        if t is None or not any(k and b.isChecked() for k, b in t["btns"].items()):
            self._stop_play()
            return
        v = min(1000, t["slider"].value() + STEP)
        t["slider"].setValue(v)
        self.win.view.secbar.set_pos(v)
        self._value_label(v)
        if v >= 1000:
            self._stop_play()

    # ------------------------------------------------------------------ the face under the pointer
    def hover(self, fid: int):
        """The face under the pointer as a tooltip: its number, kind, part, area, surface type and the condition it carries."""
        w = self.win
        self.hover_text = ""
        if fid < 0 or not w.engine.pid:
            QToolTip.hideText()
            return
        f = w.engine.project().face_map().get(fid)
        if not f:
            return
        bodies = self.facts.get("bodies") or {}
        owners = f.get("part") or " / ".join(bodies[v] for v in f.get("volumes") or [] if v in bodies)
        bc = (self.facts.get("bcs") or {}).get(int(fid))
        default = (studies.STUDIES.get(self.facts.get("study") or "", {}).get("default_face_bc") or {}).get("label", "")
        lines = [f"Face {fid} · {KIND_LABEL.get(f.get('kind'), f.get('kind', ''))}"]
        if owners:
            lines.append(owners)
        lines.append(f"{f.get('area', 0) * 1e6:.1f} mm² · {f.get('surface_type', '')}")
        if w.step == "setup" or bc:
            lines.append(f"→ {bc}" if bc else default)
        self.hover_text = "\n".join(x for x in lines if x)
        html = "<b>" + lines[0] + "</b>" + "".join("<br>" + x for x in lines[1:] if x)
        if w.view.isVisible():
            QToolTip.showText(QCursor.pos() + QPoint(14, 14), html, w.view.iren)

    # ------------------------------------------------------------------ the selection hint: what to click next
    def hint(self) -> tuple | None:
        """(key, text, priority, button, callback, tip) for the bar under the 3D view, or None."""
        w = self.win
        v = w.view
        if w.step == "setup":
            sp = w.panels["setup"]
            if sp.axis_pick:
                return ("pick", "Click a circular edge or a cylindrical face: the centre of rotation is put there", 0, "Cancel",
                        lambda: (setattr(sp, "axis_pick", None), sp.redo()), "Stop choosing the centre of rotation")
            if sp.sym.picking:
                return ("pick", "Click a flat face: the symmetry plane goes through it", 0, "Cancel",
                        lambda: (setattr(sp.sym, "picking", False), sp.redo()), "Stop choosing the face for the symmetry plane")
            if sp.sym.draft:
                return ("pick", "Drag the amber arrow to move the symmetry plane (hold Shift for round steps), or type its position; then press Apply", 0,
                        "Cancel", lambda: sp.sym.cancel(), "Discard the new symmetry plane")
            if sp.active:
                bc = next((b for b in (w.engine.state().get("bcs") or []) if b["id"] == sp.active), None)
                if bc:
                    n = len(bc.get("faces") or [])
                    return ("pick", f"Click faces to add or remove them from {bc['name']} ({n} selected). Ctrl-click adds more faces", 0, "Done",
                            lambda: (setattr(sp, "active", None), sp.redo()), "Finish picking faces for this condition")
        if w.step == "mesh" and w.panels["mesh"].adv.xform.active:
            xf = w.panels["mesh"].adv.xform
            return ("pick", "Drag an arrow to move the region, a ring to turn it, a square-ended arrow to resize it (Shift: round steps), or type the numbers; then press Apply", 0,
                    "Cancel", lambda: xf.cancel(), "Leave the region as it is")
        if w.step == "mesh" and getattr(w.panels["mesh"], "picking_part", False):
            mp = w.panels["mesh"]
            return ("pick", "Click a part in the 3D view: all its faces get a cell size (change it in Part sizing afterwards)", 0, "Cancel",
                    lambda: mp.set_picking_part(False), "Stop picking a part")
        if w.step == "results" and v.pick_mode == "point":
            rp = w.panels["results"]
            if rp.line_pick:
                n = len(rp.line_ends)
                return ("pick", "Click the first point of the line on the coloured model" if n == 0 else "Click the second point of the line", 0, "Cancel",
                        lambda: (setattr(rp, "line_pick", False), setattr(rp, "line_ends", []),
                                 v.set_pick_mode("point" if rp.probe_on else None)), "Stop picking the line (the pinned values stay)")
            if rp.probe_on:
                return ("pick", "Probe is on: click the coloured result to read its value there", 0, "Stop probing",
                        lambda: rp.probe_btn.setChecked(False), "Turn the probe off (the pinned values stay)")
        if v.pick_mode == "face":
            return ("pick", "Click a face to select it; Ctrl-click adds more faces" + (". To put faces on a condition, press its Faces button" if w.step == "setup" else ""),
                    -1, None, None, None)
        return None

    def _sync_hint(self):
        bar = self.win.view.banner
        h = self.hint()
        if h is None:
            bar.clear("select")
            return
        key, text, prio, button, cb, tip = h
        bar.set("select", text, prio, button, cb, tip) if not button else bar.set("select", text, prio, button, self._cached_cb(button, text, cb), tip)

    def _cached_cb(self, button, text, cb):
        k = (button, text)
        self._hint_cb = {k: self._hint_cb.get(k) or cb}       # (the same callable while the hint is unchanged: the bar is not redrawn each tick)
        return self._hint_cb[k]


def install(win) -> Shell:
    """Called by the main window once its widgets exist."""
    win.toasts = ToastHost(win)
    win.shell = Shell(win)
    return win.shell


# ---------------------------------------------------------------------------------------------------------------- the control API
def register(ctl):
    """The actions of the shell: toasts, the status bar's Cancel, Probe, the section bar, hover, the selection hint, the assistant's commands."""
    R, win, S, N, B = ctl._reg, ctl.win, "string", "number", "boolean"

    def sh() -> Shell:
        return win.shell

    # ---- toasts
    R("ui.toast", "Show a toast (a small notice over the corner of the window that fades by itself; also written to the log).",
      {"text": (S, "message", True), "kind": (S, "info|ok|warn|error (default info)", False)},
      lambda text, kind="info": (win.toasts.show(text, kind), {"toasts": win.toasts.listing()})[1])
    R("ui.toasts", "The toasts on the screen now, and the last ones shown (history).", {"history": (N, "how many of the last shown (default 10)", False)},
      lambda history=10: {"showing": win.toasts.listing(), "history": win.toasts.history[-int(history):]})
    R("ui.toast_close", "Close a toast with its x: the newest (default), or all.", {"all": (B, "close every toast", False)},
      lambda all=False: (win.toasts.close_all() if all else win.toasts.close_one(-1), {"showing": win.toasts.listing()})[1])
    # ---- status bar
    R("job.cancel", "Press Cancel in the status bar: stop the job it shows.", {}, lambda: (ctl._click("status.cancel"), {"cancelled": True})[1])
    R("status.job", "What the status bar says about the running job.", {},
      lambda: {"running": sh().current is not None, "message": sh().job_msg.full if sh().current else "",
               "progress": win.progress.value() / 1000 if sh().current else None, "cancel_enabled": sh().cancel.isEnabled() and sh().current is not None})
    # ---- the view's bar
    def probe(on=True):
        ctl._click("viewbar.probe") if win.view.viewbar.probe.isChecked() != bool(on) else None
        return {"probe": win.view.viewbar.probe.isChecked(), "pick_mode": win.view.pick_mode}
    R("view.probe", "Press Probe in the 3D view's bar (Results step): a click on the result then pins its value.", {"on": (B, "default true", False)}, probe)

    def section(axis=None, position=None, hide_air=None):
        sh().sync()
        if not win.view.secbar.isVisibleTo(win.view):
            raise ValueError("The section bar is on the Mesh step (once the mesh is made) and the Results step (once the simulation has run)")
        s = win.view.secbar
        if axis is not None:
            key = "" if str(axis).lower() in ("", "off", "none") else str(axis).lower()
            if key not in s.btns:
                raise ValueError("axis must be x, y, z or off")
            s.btns[key].click()
        if position is not None:
            if not s.slider.isEnabled():
                raise ValueError("Choose an axis (x, y or z) first")
            s.slider.setValue(int(round(min(max(float(position), 0.0), 1.0) * 1000)))
        if hide_air is not None:
            if not s.hide_air.isVisibleTo(s):
                raise ValueError("Hide air belongs to cooling studies' results")
            s.hide_air.setChecked(bool(hide_air))
        sh().sync()
        return sh().section_state()
    R("view.section", "The section bar of the 3D view (Mesh and Results steps): choose the axis, move the plane (0..1), hide the air (cooling studies).",
      {"axis": (S, "x|y|z|off", False), "position": (N, "0..1 across the model", False), "hide_air": (B, "cooling results: hide the air", False)}, section)

    def section_play(on=True, steps=None):
        sh().sync()
        s = win.view.secbar
        if not s.play.isEnabled():
            raise ValueError(s.play.toolTip())
        if bool(on) != s.play.isChecked():
            s.play.click()
        if on and steps:
            for _ in range(int(steps)):
                if not sh()._play.isActive():
                    break
                sh()._tick()
        elif not on:
            sh()._stop_play()
        return sh().section_state()
    R("view.section_play", "Play (or pause) the section sweep: the plane runs from one end of the model to the other. steps: do that many ticks now "
                           "(a script need not wait for the timer).", {"on": (B, "default true", False), "steps": (N, "ticks to run at once", False)}, section_play)

    def hover(face):
        win.view._set_hover(int(face)) if int(face) != win.view.hover_id else sh().hover(int(face))
        return {"face": int(face), "text": sh().hover_text}
    R("view.hover", "Move the pointer over a face: the tooltip text the window shows for it (number, kind, part, area, the condition it carries).",
      {"face": (N, "face id, -1 for none", True)}, hover)
    R("view.hint", "The hint bar under the 3D view: what to click next.", {},
      lambda: (sh().sync(), {"text": win.view.banner.text(), "button": win.view.banner.button.text() if win.view.banner.button.isVisibleTo(win.view.banner) else ""})[1])
    R("view.hint_button", "Press the button of the hint bar (Done / Cancel / Stop probing).", {}, lambda: (ctl._click("view.banner.button"), {"ok": True})[1])
    # ---- the assistant
    def command(text):
        if not win.assistant.pid:
            raise ValueError("No study is open")
        win.assistant.command(text if text.startswith("/") else "/" + text)
        return {"messages": win.assistant.log.get(win.assistant.pid, [])[-3:], "running": win.assistant.term_busy()}
    R("assistant.command", "Type a slash command into the chat box: /login /logout /status /ask question /version /help (runs the claude command line in the study folder).",
      {"text": (S, "the command, with or without the slash", True)}, command)
    R("assistant.stop", "Press Stop in the assistant: stops Claude's answer (or the running command).", {},
      lambda: (ctl._click("assistant.stop"), {"stopped": True})[1])
    R("assistant.claude_code", "Press Claude Code in the assistant: Claude Code in a terminal window, in the study's folder.", {},
      lambda: (ctl._click("assistant.claude_code"), {"status": win.assistant.status.text()})[1])

    def reload():
        a = win.assistant
        if not a.pid:
            raise ValueError("No study is open")
        a.log.pop(a.pid, None)
        a.set_project(a.pid)                          # (what a restarted window does: the kept conversation is read again)
        return {"messages": len(a.log.get(a.pid, []))}
    R("assistant.reload", "Read the kept conversation of the open study again, as a restarted window does.", {}, reload)
    R("assistant.focus", "Ctrl+K: put the cursor in the Claude message box (shows the assistant if it is hidden).", {},
      lambda: (sh().focus_assistant(), {"focus": win.assistant.input.hasFocus()})[1])
    R("ui.escape", "Press Esc: finish what the hint bar asks about, or close the newest toast.", {}, lambda: (sh().escape(), {"toasts": win.toasts.listing()})[1])
    R("ui.shortcuts", "The keyboard shortcuts of the shell and what they do.", {}, lambda: {"shortcuts": shortcut_table(win)})


def shortcut_table(win) -> dict:
    """{shortcut: what it does}: the actions with a key (menus, steps) and the buttons that carry one."""
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QAbstractButton
    out = {}
    for a in win.findChildren(QAction):
        if not a.shortcut().isEmpty() and a.objectName():
            out[a.shortcut().toString()] = a.objectName()
    for b in win.findChildren(QAbstractButton):
        if not b.shortcut().isEmpty() and b.property("wid"):
            out[b.shortcut().toString()] = b.property("wid")
    return out


__all__ = ["install", "register", "Shell", "KEYS"]
