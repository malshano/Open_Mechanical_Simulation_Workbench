"""The application shell of the Qt window: toasts, the status bar's job part with Cancel, Probe and the section bar in the 3D view's bar, the
face tooltip, the hint bar, keyboard shortcuts, and the assistant's Stop / Claude Code / slash commands / kept history.
Every test drives the real window (SIMWB_NO_GL) through the control API, as a person's click would."""
import threading
import time
from pathlib import Path

import pytest
from qt_support import ctl, from_thread  # noqa: F401

from simwb import agent
from simwb import project as P


def pump(c, until=None, seconds=3.0):
    """Let the window's events run (toasts fade, signals from threads arrive) until `until()` is true or the time is up."""
    t0 = time.time()
    while time.time() - t0 < seconds:
        c.app.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.01)
    return until is None or bool(until())


def wait_jobs(c):
    assert from_thread(c.app, lambda: c.wait_idle(300), 320)["idle"]
    c._settle()


# ------------------------------------------------------------------------------------------------------------ toasts
def test_a_toast_appears_stacks_closes_by_its_x_and_is_logged(ctl):
    r = ctl.do("ui.toast", {"text": "Saved at 12:30", "kind": "ok"})["result"]
    assert r["toasts"] == [{"kind": "ok", "text": "Saved at 12:30"}]
    ctl.do("ui.toast", {"text": "Mesh failed: boom", "kind": "error"})
    assert [t["kind"] for t in ctl.do("ui.toasts")["result"]["showing"]] == ["ok", "error"]          # they stack
    assert ctl.win.toasts.box.isVisible()
    assert ctl.log.since(contains="Mesh failed: boom")["entries"]                                       # and are written to the log
    ctl.widget_do("toast.close", "click")                                                              # the x of a toast
    assert len(ctl.do("ui.toasts")["result"]["showing"]) == 1
    ctl.do("ui.toast_close", {"all": True})
    assert ctl.do("ui.toasts")["result"]["showing"] == [] and not ctl.win.toasts.box.isVisible()
    assert len(ctl.do("ui.toasts")["result"]["history"]) >= 2                                          # (kept for the API)


def test_a_toast_fades_by_itself_and_the_same_notice_is_not_doubled(ctl):
    t = ctl.win.toasts.show("short lived", "info", seconds=0.05)
    ctl.win.toasts.show("short lived", "info", seconds=0.05)
    assert len(ctl.win.toasts.items) == 1
    assert pump(ctl, lambda: not ctl.win.toasts.items, 4.0), "the toast did not fade away"
    assert t.gone


def test_a_toast_stays_while_the_pointer_is_on_it(ctl):
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QEnterEvent
    t = ctl.win.toasts.show("being read", "info", seconds=0.05)
    ctl.app.sendEvent(t, QEnterEvent(t.rect().center(), t.rect().center(), t.rect().center()))
    pump(ctl, None, 0.5)
    assert not t.gone                                                                                   # (the timer stopped)
    ctl.app.sendEvent(t, QEvent(QEvent.Leave))
    ctl.do("ui.toast_close", {"all": True})


def test_at_most_five_toasts_and_escape_closes_the_newest(ctl):
    for i in range(7):
        ctl.win.toasts.show(f"notice {i}", "info")
    assert len(ctl.win.toasts.items) == 5 and ctl.win.toasts.items[-1].text == "notice 6"
    ctl.do("ui.escape")
    assert [t.text for t in ctl.win.toasts.items][-1] == "notice 5"
    ctl.do("ui.toast_close", {"all": True})


def test_an_error_is_a_toast_not_a_box_that_waits(ctl):
    """(The fixture of qt_support fails a test that shows a modal warning box.)"""
    ctl.win._try(lambda: (_ for _ in ()).throw(ValueError("no such thing")))
    ctl.engine.new_study("Plate", "static_structural")
    ctl._settle()
    ctl.win.panels["setup"]._fail(ValueError("the panel's trouble"))
    texts = [t["text"] for t in ctl.do("ui.toasts")["result"]["showing"]]
    assert any("no such thing" in x for x in texts) and any("the panel's trouble" in x for x in texts)
    assert all(t["kind"] == "error" for t in ctl.do("ui.toasts")["result"]["showing"])
    ctl.do("ui.toast_close", {"all": True})


def test_saving_says_so_with_a_toast(ctl):
    ctl.do("study.new", {"name": "Plate", "study": "static_structural"})
    ctl.win.save()
    assert any(t["text"].startswith("Saved at") for t in ctl.do("ui.toasts")["result"]["showing"])


def test_the_about_entry_is_a_toast(ctl):
    ctl.widget_do("help.about", "click")
    assert any("GPLv3" in t["text"] for t in ctl.do("ui.toasts")["result"]["showing"])


# ------------------------------------------------------------------------------------------------------------ status bar
def _slow_job(pid, steps=200):
    def fn(job):
        for i in range(steps):
            job.check_cancel()
            P.JOBS.update(job, progress=i / steps, message=f"step {i}")
            time.sleep(0.03)
    return P.JOBS.start("mesh", pid, fn)


def test_the_status_bar_shows_the_job_with_progress_and_cancel_stops_it(ctl):
    ctl.do("study.new", {"name": "Plate", "study": "static_structural"})
    idle = ctl.do("status.job")["result"]
    assert idle["running"] is False and not ctl.widget_do("status.cancel")["visible"]
    job = _slow_job(ctl.engine.pid)
    assert pump(ctl, lambda: ctl.do("status.job")["result"]["running"])
    s = ctl.do("status.job")["result"]
    assert s["message"].startswith("Mesh:") and s["cancel_enabled"] and ctl.widget_do("status.cancel")["visible"]
    assert ctl.widget_do("status.progress")["visible"]
    assert ctl.state()["shell"]["status_job"].startswith("Mesh:")
    ctl.do("job.cancel")                                                                                 # the Cancel button
    assert job.wait(10) and job.status == "cancelled"
    assert pump(ctl, lambda: not ctl.do("status.job")["result"]["running"])
    assert not ctl.widget_do("status.cancel")["visible"]
    assert any("stopped" in t["text"] for t in ctl.do("ui.toasts")["result"]["showing"])
    ctl.do("ui.toast_close", {"all": True})


def test_a_finished_job_is_a_toast_and_a_failed_one_an_error_toast(ctl):
    ctl.do("study.new", {"name": "Plate", "study": "static_structural"})

    def bad(job):
        raise RuntimeError("boom")
    P.JOBS.start("solve", ctl.engine.pid, bad)
    assert pump(ctl, lambda: any(t["kind"] == "error" for t in ctl.do("ui.toasts")["result"]["showing"]))
    assert any("Solution failed: boom" in t["text"] for t in ctl.do("ui.toasts")["result"]["showing"])
    ctl.do("ui.toast_close", {"all": True})
    P.JOBS.start("mesh", ctl.engine.pid, lambda job: None)
    assert pump(ctl, lambda: any(t["text"] == "Mesh ready" for t in ctl.do("ui.toasts")["result"]["showing"]))
    ctl.do("ui.toast_close", {"all": True})


def test_cancel_without_a_job_says_why(ctl):
    ctl.do("study.new", {"name": "Plate", "study": "static_structural"})
    with pytest.raises(ValueError, match="not visible|disabled"):
        ctl.do("job.cancel")


def test_a_message_that_stays_a_while_is_also_a_toast(ctl):
    ctl.do("study.new", {"name": "Plate", "study": "static_structural"})
    ctl.win.statusBar().showMessage("Video saved: 40 pictures", 9000)
    ctl.win.statusBar().showMessage("Video: picture 3 of 40", 4000)                                     # (a running note: no toast)
    texts = [t["text"] for t in ctl.do("ui.toasts")["result"]["showing"]]
    assert "Video saved: 40 pictures" in texts and "Video: picture 3 of 40" not in texts
    ctl.do("ui.toast_close", {"all": True})


# ------------------------------------------------------------------------------------------------------------ Probe and section
def _as_results(c):
    """The Results step with the shell told that a solution is there (the section bar and Probe link to the panel's controls; no solve needed)."""
    c.do("study.new", {"name": "Plate", "study": "static_structural"})
    c._settle()
    c.do("step.go", {"step": "results"})
    c.win.shell.facts.update({"solve": "done", "study": "static_structural"})
    c.win.shell.sync()


def test_probe_is_in_the_view_bar_and_says_why_when_it_cannot_be_used(ctl):
    ctl.do("study.new", {"name": "Plate", "study": "static_structural"})
    ctl._settle()
    b = ctl._index()["viewbar.probe"]
    assert b.isVisible() and not b.isEnabled() and "Results" in b.toolTip()
    with pytest.raises(ValueError, match="disabled"):
        ctl.do("view.probe")
    assert ctl.coverage()["complete"]


def test_probe_in_the_bar_drives_the_results_probe(ctl):
    _as_results(ctl)
    b = ctl._index()["viewbar.probe"]
    assert b.isEnabled() and "Ctrl+Shift+P" in b.toolTip()
    r = ctl.do("view.probe", {"on": True})["result"]
    assert r == {"probe": True, "pick_mode": "point"}
    assert ctl.win.panels["results"].probe_btn.isChecked()                                              # (the panel's own button follows)
    assert "Probe is on" in ctl.do("view.hint")["result"]["text"]                                       # the hint says what to click
    assert ctl.do("view.hint")["result"]["button"] == "Stop probing"
    ctl.do("view.hint_button")                                                                          # ... and its button stops it
    assert not ctl._index()["viewbar.probe"].isChecked() and ctl.win.view.pick_mode is None
    ctl.widget_do("results.probe", "click")                                                             # the other way round: the panel's button
    ctl.win.shell.sync()
    assert ctl._index()["viewbar.probe"].isChecked()
    ctl.do("view.probe", {"on": False})
    assert not ctl.win.panels["results"].probe_btn.isChecked()


def test_probe_goes_off_and_disabled_when_the_step_is_left(ctl):
    _as_results(ctl)
    ctl.do("view.probe", {"on": True})
    ctl.do("step.go", {"step": "setup"})
    ctl.win.shell.sync()
    assert not ctl._index()["viewbar.probe"].isChecked() and not ctl._index()["viewbar.probe"].isEnabled()


def test_the_section_bar_is_hidden_until_there_is_something_to_cut_then_drives_the_panel(ctl):
    ctl.do("study.new", {"name": "Plate", "study": "static_structural"})
    ctl._settle()
    assert not ctl._index()["viewbar.section.x"].isVisible()
    with pytest.raises(ValueError, match="section bar"):
        ctl.do("view.section", {"axis": "x"})
    _as_results(ctl)
    assert ctl._index()["viewbar.section.x"].isVisible()
    assert not ctl._index()["viewbar.section.pos"].isEnabled() and "Choose X" in ctl._index()["viewbar.section.pos"].toolTip()   # (why)
    r = ctl.do("view.section", {"axis": "x", "position": 0.25})["result"]
    assert r["axis"] == "x" and r["position"] == pytest.approx(0.25) and r["visible"] and r["kind"] == "results"
    p = ctl.win.panels["results"]
    assert p.section_btns["x"].isChecked() and p.section_pos.value() == 250                              # the panel's controls moved
    assert ctl._index()["viewbar.section.pos"].isEnabled() and ctl._index()["viewbar.section.value"].text().startswith("x")
    ctl.widget_do("viewbar.section.y", "click")                                                         # a click on the bar's own button
    assert p.section_btns["y"].isChecked() and ctl.do("view.section")["result"]["axis"] == "y"
    ctl.widget_do("viewbar.section.pos", "set", 900)
    assert p.section_pos.value() == 900
    p.section_btns["z"].click()                                                                          # the panel changes: the bar follows
    ctl.win.shell.sync()
    assert ctl.do("view.section")["result"]["axis"] == "z"
    ctl.do("view.section", {"axis": "off"})
    assert not ctl._index()["viewbar.section.pos"].isEnabled() and ctl.do("view.section")["result"]["axis"] == ""


def test_play_sweeps_the_plane_from_one_end_to_the_other_and_stops(ctl):
    _as_results(ctl)
    with pytest.raises(ValueError, match="Choose X"):
        ctl.do("view.section_play")                                                                      # (no axis: it says why)
    ctl.do("view.section", {"axis": "x", "position": 0.0})
    r = ctl.do("view.section_play", {"on": True, "steps": 3})["result"]
    assert r["playing"] and r["position"] == pytest.approx(0.036)
    r = ctl.do("view.section_play", {"on": True, "steps": 200})["result"]
    assert r["position"] == 1.0 and not r["playing"]                                                    # it stops at the far end
    ctl.do("view.section", {"position": 0.5})
    ctl.do("view.section_play", {"on": True})
    assert ctl.do("view.section")["result"]["playing"] and ctl._index()["viewbar.section.play"].isChecked()
    ctl.do("view.section_play", {"on": False})
    assert not ctl.do("view.section")["result"]["playing"]


def test_hide_air_shows_for_cooling_results_only(ctl):
    _as_results(ctl)
    assert not ctl._index()["viewbar.section.hide_air"].isVisible()
    with pytest.raises(ValueError, match="cooling"):
        ctl.do("view.section", {"hide_air": True})
    ctl.win.shell.facts["study"] = "cht_enclosure"
    ctl.win.shell.sync()
    assert ctl._index()["viewbar.section.hide_air"].isVisible()
    ctl.do("view.section", {"hide_air": False})
    assert not ctl.win.panels["results"].hide_air.isChecked()
    ctl.do("view.section", {"hide_air": True})
    assert ctl.win.panels["results"].hide_air.isChecked()


def test_the_mesh_step_has_the_section_bar_too(ctl):
    ctl.do("study.new", {"name": "Plate", "study": "static_structural"})
    ctl._settle()
    ctl.do("step.go", {"step": "mesh"})
    ctl.win.shell.facts.update({"mesh": "ready"})
    ctl.win.shell.sync()
    r = ctl.do("view.section", {"axis": "y", "position": 0.75})["result"]
    assert r["kind"] == "mesh" and ctl.win.panels["mesh"].cut_btns["y"].isChecked() and ctl.win.panels["mesh"].slider.value() == 750


# ------------------------------------------------------------------------------------------------------------ hover tip and hint bar
def test_hovering_a_face_gives_its_name_kind_part_area_and_condition(ctl):
    ctl.do("study.example", {"study": "heat_conduction"})
    wait_jobs(ctl)
    faces = ctl.engine.project().faces()
    f = faces[0]
    ctl.do("step.go", {"step": "setup"})
    txt = ctl.do("view.hover", {"face": f["id"]})["result"]["text"]
    assert f"Face {f['id']}" in txt and "mm" in txt and f["surface_type"] in txt
    assert ctl.state()["shell"]["hover"] == txt
    ctl.do("setup.bc_add", {"type": "fixed_temperature", "faces": [f["id"]], "name": "Cold"})
    ctl._settle()
    assert "Cold" in ctl.do("view.hover", {"face": f["id"]})["result"]["text"]                                # (the condition the face carries)
    assert ctl.do("view.hover", {"face": -1})["result"]["text"] == ""                                   # leaving the faces: no tip


def test_the_hint_bar_says_what_to_click_next_and_has_a_way_out(ctl):
    ctl.do("study.example", {"study": "heat_conduction"})
    wait_jobs(ctl)
    ctl.do("step.go", {"step": "geometry"})
    assert "Click a face" not in ctl.do("view.hint")["result"]["text"]                                 # (nothing to pick in the geometry step)
    ctl.do("step.go", {"step": "setup"})
    h = ctl.do("view.hint")["result"]
    assert h["text"].startswith("Click a face to select it") and "Ctrl" in h["text"] and h["button"] == ""
    ctl.do("setup.bc_add", {"type": "fixed_temperature", "name": "Cold"})                               # no faces: it starts picking
    h = ctl.do("view.hint")["result"]
    assert "Cold" in h["text"] and "Click faces" in h["text"] and h["button"] == "Done"
    assert ctl._index()["view.banner.button"].toolTip()                                                 # the button says what it does
    ctl.do("view.hint_button")                                                                          # Done
    assert ctl.win.panels["setup"].active is None
    assert ctl.do("view.hint")["result"]["button"] == ""
    ctl.do("step.go", {"step": "mesh"})
    assert "Click" not in ctl.do("view.hint")["result"]["text"]                                        # (the hint goes with the picking)


def test_a_symmetry_plane_pick_has_its_own_hint_and_escape_answers_it(ctl):
    ctl.do("study.example", {"study": "heat_conduction"})
    wait_jobs(ctl)
    ctl.do("setup.bc_add", {"type": "symmetry"})
    assert "symmetry plane" in ctl.do("view.hint")["result"]["text"]
    ctl.do("ui.escape")                                                                                 # Esc = the bar's Cancel
    assert not ctl.win.panels["setup"].sym.picking


# ------------------------------------------------------------------------------------------------------------ keys and signifiers
def test_the_view_buttons_name_their_keys_and_the_keys_work(ctl):
    ctl.do("study.new", {"name": "Plate", "study": "static_structural"})
    ctl._settle()
    for key, text in (("iso", "Ctrl+1"), ("x", "Ctrl+2"), ("y", "Ctrl+3"), ("z", "Ctrl+4"), ("fit", "(F)"), ("edges", "Ctrl+E"), ("picture", "Ctrl+P")):
        assert text in ctl._index()[f"viewbar.{key}"].toolTip(), key
    keys = ctl.do("ui.shortcuts")["result"]["shortcuts"]
    assert keys["Ctrl+2"] == "view.+x" and keys["F"] == "view.fit" and keys["Ctrl+E"] == "viewbar.edges" and keys["Alt+3"] == "step.mesh"
    assert keys["Ctrl+Shift+P"] == "viewbar.probe" and keys["Ctrl+P"] == "viewbar.picture" and keys["Ctrl+Shift+Space"] == "viewbar.section.play"
    ctl._index()["view.+x"].trigger()                                                                     # (the menu entry the key is on)
    p = ctl.state()["view"]["camera"]["position"]
    assert p[0] > 0 and abs(p[1]) < 1e-6
    ctl._index()["view.+x"].trigger()
    assert ctl.state()["view"]["camera"]["position"][0] < 0                                              # (again: the other side, as the button)
    assert "Alt+5" in ctl._index()["step.results"].toolTip()


def test_every_shell_control_has_an_id_a_tooltip_and_a_pointing_hand(ctl):
    ctl.do("study.new", {"name": "Plate", "study": "static_structural"})
    ctl._settle()
    cov = ctl.coverage()
    assert cov["complete"], cov
    for i in ("viewbar.probe", "viewbar.section.x", "viewbar.section.pos", "viewbar.section.play", "viewbar.section.hide_air", "status.cancel",
              "assistant.stop", "assistant.claude_code", "assistant.send", "view.banner.button"):
        o = ctl._index()[i]
        assert o.toolTip(), i
    for k in ("viewbar.section.off", "viewbar.section.x", "viewbar.probe", "status.cancel", "assistant.stop"):
        assert ctl._index()[k].cursor().shape().name == "PointingHandCursor", k


def test_ctrl_k_takes_the_cursor_to_the_message_box(ctl):
    ctl.do("study.new", {"name": "Plate", "study": "static_structural"})
    ctl._settle()
    ctl.win.dock_chat.hide()
    ctl.do("assistant.focus")
    assert ctl.win.dock_chat.isVisible()


# ------------------------------------------------------------------------------------------------------------ the assistant
class FakeTerminal:
    """Stands for the claude command line: records the arguments, prints two lines and exits (from a thread, as the real one does)."""

    def __init__(self, monkeypatch, code=0, auth=False):
        self.calls, self.code, self.auth = [], code, auth
        monkeypatch.setattr(agent.TERMINAL, "run", self.run)

    def run(self, pid, args, emit):
        self.calls.append(list(args))

        def go():
            emit({"type": "term_out", "project": pid, "text": "Logged in as you@example.com"})
            emit({"type": "term_done", "project": pid, "code": self.code, "auth": self.auth})
        threading.Thread(target=go, daemon=True).start()
        return "cwd"


def _log(c):
    return c.win.assistant.log.get(c.engine.pid, [])


def test_stop_is_always_there_and_enabled_only_while_something_runs(ctl, monkeypatch):
    ctl.do("study.new", {"name": "Plate", "study": "static_structural"})
    ctl._settle()
    b = ctl._index()["assistant.stop"]
    assert b.isVisible() and not b.isEnabled() and "Nothing to stop" in b.toolTip()
    stopped = []
    monkeypatch.setattr(ctl.win.assistant.bridge, "stop", lambda pid: stopped.append(pid))
    monkeypatch.setattr(ctl.win.assistant.bridge, "ask", lambda *a, **k: None)
    ctl.do("assistant.ask", {"text": "hello"})
    assert b.isEnabled() and "Stop Claude" in b.toolTip() and not ctl._index()["assistant.send"].isEnabled()
    ctl.do("assistant.stop")
    assert stopped == [ctl.engine.pid]
    ctl.win.assistant._on_event({"type": "chat_done", "project": ctl.engine.pid})
    assert not b.isEnabled() and ctl._index()["assistant.send"].isEnabled()


def test_the_claude_code_button_opens_claude_code_in_the_study_folder(ctl, monkeypatch):
    assert ctl.win.assistant.pid is None
    ctl.do("study.new", {"name": "Plate", "study": "static_structural"})
    ctl._settle()
    opened = []
    monkeypatch.setattr(agent, "open_in_claude_code", lambda folder: opened.append(Path(folder)) or f"Claude Code opened in {folder}")
    assert ctl._index()["assistant.claude_code"].isEnabled()
    ctl.do("assistant.claude_code")
    folder = Path(ctl.state()["study"]["path"])
    assert opened == [folder] and (folder / "CLAUDE.md").exists()                                        # (the folder knows the study)
    assert "Claude Code opened" in ctl.win.assistant.status.text()
    assert any("Claude Code opened" in t["text"] for t in ctl.do("ui.toasts")["result"]["showing"])
    ctl.do("ui.toast_close", {"all": True})


def test_slash_commands_run_the_claude_command_line_and_show_its_output(ctl, monkeypatch):
    ctl.do("study.new", {"name": "Plate", "study": "static_structural"})
    ctl._settle()
    fake = FakeTerminal(monkeypatch)
    ctl.do("assistant.command", {"text": "/status"})
    assert pump(ctl, lambda: any("[exit 0]" in t for r, t in _log(ctl) if r == "term"))
    assert fake.calls == [["auth", "status"]]
    term = [t for r, t in _log(ctl) if r == "term"][-1]
    assert "/status" in term and "Logged in as you@example.com" in term

    ctl.do("assistant.command", {"text": "/version"})
    assert pump(ctl, lambda: len(fake.calls) == 2) and fake.calls[1] == ["--version"]
    ctl.do("assistant.command", {"text": "/ask what is Q?"})
    assert pump(ctl, lambda: len(fake.calls) == 3)
    assert fake.calls[2][:2] == ["-p", "what is Q?"] and "Bash" in fake.calls[2][-1]                      # (no shell, no file tools)


def test_help_and_unknown_commands_answer_in_the_chat(ctl):
    ctl.do("study.new", {"name": "Plate", "study": "static_structural"})
    ctl._settle()
    ctl.do("assistant.command", {"text": "/help"})
    ctl.do("assistant.command", {"text": "nonsense"})
    terms = [t for r, t in _log(ctl) if r == "term"]
    assert "/login" in terms[0] and "/ask" in terms[0] and "Unknown command" in terms[1]
    assert not ctl.win.assistant.busy                                                                    # (nothing went to Claude)


def test_a_slash_line_in_the_box_is_a_command_and_anything_else_goes_to_claude(ctl, monkeypatch):
    ctl.do("study.new", {"name": "Plate", "study": "static_structural"})
    ctl._settle()
    asked = []
    monkeypatch.setattr(ctl.win.assistant.bridge, "ask", lambda pid, text, history=None: asked.append(text))
    a = ctl.win.assistant
    a.input.setPlainText("/help")
    ctl.widget_do("assistant.send", "click")
    assert asked == [] and any("Unknown" not in t and "/login" in t for r, t in _log(ctl) if r == "term")
    a.input.setPlainText("Is the mesh fine enough?")
    ctl.widget_do("assistant.send", "click")
    assert len(asked) == 1 and asked[0].endswith("Is the mesh fine enough?")
    assert "GUI context" in asked[0]                                                                     # Claude is told which step the user is on


def test_log_in_and_log_out_run_the_commands_in_the_chat(ctl, monkeypatch):
    ctl.do("study.new", {"name": "Plate", "study": "static_structural"})
    ctl._settle()
    fake = FakeTerminal(monkeypatch, auth=True)
    ctl.win.assistant._on_auth({"loggedIn": False})
    assert ctl._index()["assistant.login"].isVisibleTo(ctl.win.assistant)
    ctl.widget_do("assistant.login", "click")
    assert pump(ctl, lambda: fake.calls == [["auth", "login"]] and any("[exit 0]" in t for r, t in _log(ctl)))
    ctl.win.assistant._on_auth({"loggedIn": True, "email": "a@b.c"})
    ctl.widget_do("assistant.logout", "click")
    assert pump(ctl, lambda: len(fake.calls) == 2) and fake.calls[1] == ["auth", "logout"]


def test_the_conversation_is_kept_per_study_and_comes_back_after_a_restart(ctl, monkeypatch):
    from simwb.qt.assistant import AssistantPanel
    ctl.do("study.new", {"name": "Plate", "study": "static_structural"})
    pid_a = ctl.engine.pid
    ctl._settle()
    a = ctl.win.assistant
    monkeypatch.setattr(a.bridge, "ask", lambda *x, **k: None)
    ctl.do("assistant.ask", {"text": "Which material?"})
    a._on_event({"type": "chat_text", "project": pid_a, "text": "Aluminium."})
    a._on_event({"type": "chat_done", "project": pid_a})
    ctl.do("assistant.command", {"text": "/help"})
    ctl.do("study.new", {"name": "Other", "study": "heat_conduction"})                                    # another study: its own, empty chat
    ctl._settle()
    assert a.log.get(ctl.engine.pid, []) == []
    ctl.engine.open_id(pid_a)
    ctl._settle()
    assert [r for r, _ in a.log[pid_a]][:2] == ["user", "assistant"]
    # a restarted window: a new panel on the same settings file reads the kept conversation
    fresh = AssistantPanel(a.bridge, settings=ctl.engine.settings)
    fresh.set_project(pid_a)
    assert [(r, t) for r, t in fresh.log[pid_a] if r in ("user", "assistant")] == [("user", "Which material?"), ("assistant", "Aluminium.")]
    assert any(r == "term" and "/login" in t for r, t in fresh.log[pid_a])
    assert "Aluminium." in fresh.view.toPlainText()                                                     # and it is on the screen
    r = ctl.do("assistant.reload")["result"]
    assert r["messages"] >= 3
    ctl.do("assistant.new_chat")                                                                         # New chat forgets it for good
    other = AssistantPanel(a.bridge, settings=ctl.engine.settings)
    other.set_project(pid_a)
    assert other.log[pid_a] == []
    fresh.deleteLater()
    other.deleteLater()


def test_busy_belongs_to_the_study_not_the_window(ctl, monkeypatch):
    ctl.do("study.new", {"name": "A", "study": "static_structural"})
    pid_a = ctl.engine.pid
    ctl._settle()
    monkeypatch.setattr(ctl.win.assistant.bridge, "ask", lambda *x, **k: None)
    ctl.do("assistant.ask", {"text": "hello"})
    assert ctl.win.assistant.busy
    ctl.do("study.new", {"name": "B", "study": "static_structural"})
    ctl._settle()
    assert not ctl.win.assistant.busy                                                                     # B is free ...
    ctl.win.assistant._on_event({"type": "chat_text", "project": pid_a, "text": "late answer"})           # ... A's answer lands in A's chat
    ctl.win.assistant._on_event({"type": "chat_done", "project": pid_a})
    assert ("assistant", "late answer") in ctl.win.assistant.log[pid_a] and ctl.win.assistant.log.get(ctl.engine.pid, []) == []
    assert not ctl.win.assistant._busy[pid_a]


def test_a_tool_line_gets_its_result_mark(ctl, monkeypatch):
    ctl.do("study.new", {"name": "A", "study": "static_structural"})
    pid = ctl.engine.pid
    ctl._settle()
    a = ctl.win.assistant
    a._on_event({"type": "chat_tool", "project": pid, "name": "generate_mesh"})
    a._on_event({"type": "chat_tool_result", "project": pid, "name": "generate_mesh", "ok": True, "preview": "ok"})
    a._on_event({"type": "chat_tool", "project": pid, "name": "run_simulation"})
    a._on_event({"type": "chat_tool_result", "project": pid, "name": "run_simulation", "ok": False, "preview": "ERROR: no mesh"})
    lines = [t for r, t in a.log[pid] if r == "tool"]
    assert lines[0] == "Generating mesh ✓" and lines[1].startswith("Running simulation") and lines[1].endswith("!") and "no mesh" in lines[1]


def test_the_terminal_rules_live_in_the_engine_and_refuse_dangerous_flags():
    assert agent.terminal_line_args("status") == ["auth", "status"]
    assert agent.slash_command("/ask hi")[0] == "run" and agent.slash_command("/rm -rf")[0] == "unknown"
    for bad in ("--dangerously-skip-permissions", "mcp add x", "-p --allowedTools Bash"):
        with pytest.raises(ValueError):
            agent.terminal_line_args(bad)


def test_the_assistant_cannot_sign_in_or_open_an_unrestricted_terminal():
    """Claude drives the window through the ui tool; the claude command line and Claude Code (no tool limits) are for the person."""
    from simwb import tools
    for action, args in (("assistant.command", {"text": "/login"}), ("assistant.claude_code", {})):
        with pytest.raises(ValueError, match="not operated through the assistant"):
            tools.ui_do(None, {"action": action, "args": args})
    with pytest.raises(ValueError, match="not operated through the assistant"):
        tools.ui_widget(None, {"id": "assistant.claude_code", "op": "click"})
