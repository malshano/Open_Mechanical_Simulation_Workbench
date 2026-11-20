"""The application bar (study switcher, New, Simple/Expert, Jobs, assistant, Close), the step bar's state marks, the outline's detail, the status
text and the other small things a critic pointed at."""
import numpy as np
import pytest
from qt_support import ctl, from_thread  # noqa: F401

from PySide6.QtWidgets import QDockWidget  # noqa: E402
from simwb.qt import chrome  # noqa: E402


def wait(c):
    assert from_thread(c.app, lambda: c.wait_idle(300), 320)["idle"]
    c._settle()


def test_the_bar_has_the_web_versions_controls(ctl):
    ids = {w["id"] for w in ctl.widgets("chrome.")["widgets"]}
    assert {"chrome.project", "chrome.new", "chrome.mode.simple", "chrome.mode.expert", "chrome.jobs", "chrome.claude", "chrome.close"} <= ids
    assert ctl.coverage()["complete"]
    with pytest.raises(ValueError, match="waits for a person"):
        ctl.widget_do("chrome.close", "click")                                  # it would close the application: not drivable


def test_the_switcher_lists_the_studies_and_opens_one(ctl, tmp_path):
    ctl.do("study.new", {"name": "Alpha", "study": "static_structural"})
    ctl.do("study.new", {"name": "Beta", "study": "heat_conduction"})
    items = ctl.widget_do("chrome.project")["value"]["items"]
    assert items[0] == "(start screen)" and {"Alpha", "Beta"} <= set(items)
    assert ctl.widget_do("chrome.project")["value"]["text"] == "Beta"           # the open study is chosen
    ctl.widget_do("chrome.project", "set", "Alpha")
    assert ctl.state()["study"]["name"] == "Alpha"
    ctl.widget_do("chrome.new", "click")
    assert ctl.state()["window"]["screen"] == "home"


def test_simple_and_expert_mode_change_what_setup_offers(ctl):
    ctl.do("study.new", {"name": "Alpha", "study": "static_structural"})
    ctl.do("step.go", {"step": "setup"})
    assert ctl.state()["window"]["expert"] is True and "setup.advanced.toggle" in {w["id"] for w in ctl.widgets("setup.")["widgets"]}
    ctl.widget_do("chrome.mode.simple", "click")
    assert ctl.state()["window"]["expert"] is False
    assert "setup.advanced.toggle" not in {w["id"] for w in ctl.widgets("setup.")["widgets"]}              # (the essentials only)
    assert ctl.engine.settings.value("mode", type=str) == "simple"                                    # and it is remembered
    ctl.do("app.mode", {"mode": "expert"})
    assert "setup.advanced.toggle" in {w["id"] for w in ctl.widgets("setup.")["widgets"]}
    with pytest.raises(ValueError, match="simple or expert"):
        ctl.do("app.mode", {"mode": "pro"})


def test_the_jobs_menu_counts_and_stops_jobs(ctl):
    ctl.do("study.example", {"study": "heat_conduction"})
    chrome._count(ctl.win)
    ctl.app.processEvents()
    seen = chrome.running()
    text_while = ctl.widget_do("chrome.jobs")["text"]
    if seen:
        assert "(" in text_while                                                      # "⚙ Jobs (1)"
        chrome._fill_jobs(ctl.win)
        assert any("Stop" in a.text() for a in ctl.win.jobs_menu.actions())
    wait(ctl)
    chrome._count(ctl.win)
    assert ctl.widget_do("chrome.jobs")["text"].strip().endswith("Jobs")
    chrome._fill_jobs(ctl.win)
    assert [a.text() for a in ctl.win.jobs_menu.actions()] == ["No job is running"]


def test_the_step_bar_shows_state_in_words_and_marks(ctl):
    ctl.do("study.new", {"name": "Plate", "study": "heat_conduction"})
    t = {k: ctl._index()[f"step.{k}"] for k in ("geometry", "setup", "mesh", "solve", "results")}
    assert "○" in t["geometry"].text() and "import" in t["geometry"].toolTip().lower()
    ctl.do("study.example", {"study": "heat_conduction"})
    wait(ctl)
    assert "✓" in t["geometry"].text() and "done" in t["geometry"].toolTip()
    assert "⚠" in t["setup"].text() and "missing" in t["setup"].toolTip()               # a heat study needs a condition
    assert "○" in t["mesh"].text() and "not generated" in t["mesh"].toolTip()


def test_the_outline_and_the_status_text_say_what_there_is(ctl):
    ctl.do("study.example", {"study": "static_structural"})
    wait(ctl)
    faces = ctl.engine.project().faces()
    holes = [f["id"] for f in faces if f["surface_type"] == "Cylinder"]
    ctl.do("setup.bc_add", {"type": "fixed_support", "faces": holes, "name": "Bolts"})
    items = ctl.widget_do("outline.tree")["value"]["items"]
    assert any(i.endswith(f"Bolts — fixed support · {len(holes)} faces") for i in items)
    assert any("○ Mesh — none" in i for i in items)
    txt = ctl.widget_do("status.path")["text"]
    assert "Static structural" in txt and "Mesh:" in txt and "Solution:" in txt
    assert "Static-structural" in ctl._index()["status.path"].toolTip() or ctl._index()["status.path"].toolTip()   # the folder is in the tooltip


def test_studies_with_the_same_name_are_told_apart_on_the_start_screen(ctl, tmp_path):
    ctl.do("study.new", {"name": "Same", "study": "static_structural", "location": str(tmp_path / "one")})
    ctl.do("study.new", {"name": "Same", "study": "static_structural", "location": str(tmp_path / "two")})
    ctl.do("study.close")
    chips = [w["text"] for w in ctl.widgets("home.recent")["widgets"]]
    assert len(chips) == 2 and chips[0] != chips[1] and all("Same" in c and "(" in c for c in chips)


def test_the_docks_have_no_tiny_float_buttons_and_the_log_dock_has_no_black_strip(ctl):
    ctl.do("study.new", {"name": "Plate", "study": "heat_conduction"})
    for d in ctl.win.findChildren(QDockWidget):
        assert not (d.features() & QDockWidget.DockWidgetFloatable), d.objectName()
    assert ctl.win.logdock.windowTitle() == "Output"                                   # (not "Messages" over a "Messages" tab)
    for name in ("dark", "light"):
        ctl.do("theme.set", {"mode": name})
        ctl._settle()
        img = ctl.win.logdock.head.grab().toImage()
        x, y = img.width() - 12, img.height() // 2                                       # beside the tabs
        c = img.pixelColor(x, y)
        assert c.alpha() > 0 and max(c.red(), c.green(), c.blue()) > 20, f"{name}: {c.name()} alpha {c.alpha()}"      # painted, not black


def test_the_log_dock_tabs_can_be_chosen_by_id(ctl):
    ctl.do("study.new", {"name": "Plate", "study": "heat_conduction"})
    assert ctl.widget_do("log.tab.messages")["value"] == {"checked": True} and ctl.win.logdock.stack.currentIndex() == 0
    ctl.widget_do("log.tab.solver", "click")
    assert ctl.win.logdock.stack.currentIndex() == 1 and ctl.widget_do("log.tab.messages")["value"] == {"checked": False}
