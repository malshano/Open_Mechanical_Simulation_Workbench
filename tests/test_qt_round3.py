"""Fixes from the third review: the start screen opens at its top and fits the window, a done step with something to check shows the warning
mark, the Probe tab is a real toggle whose buttons wait for data, sections look like headings, the step panel starts at its top."""
import pytest
from PySide6.QtCore import Qt
from qt_support import ctl, from_thread  # noqa: F401
from test_qt_results import solved  # noqa: F401


def test_the_start_screen_opens_at_the_top_and_never_scrolls_sideways(ctl, tmp_path):
    h = ctl.win.home
    ctl.win.resize(900, 500)
    ctl.win.show_home(True)
    ctl.win.show()
    ctl._settle()
    sb = h.scroll.verticalScrollBar()
    assert sb.maximum() > 0                                                           # (the page is longer than the window)
    sb.setValue(sb.maximum())
    ctl.win.show_home(True)                                                           # coming back to it
    assert sb.value() == 0
    assert h.scroll.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff


def test_recent_studies_have_short_names_in_rows_of_three(ctl):
    from simwb.qt.home import elide_middle
    long = "External aerodynamics - quadcopter  (External-aerodynamics---quadcopter-7)"
    s = elide_middle(long, 34)
    assert len(s) == 34 and s.startswith("External aerod") and s.endswith("quadcopter-7)") is False or s.endswith("copter-7)")
    assert "…" in s and elide_middle("Bracket static", 34) == "Bracket static"
    recent = [{"name": "External aerodynamics - quadcopter", "path": f"/x/External-aerodynamics---quadcopter-{i}"} for i in range(6)]
    ctl.win.home.set_recent(recent)
    chips = [ctl.widget_do(f"home.recent.{i}") for i in range(6)]
    assert all(len(c["text"]) <= 34 for c in chips)
    assert len({c["text"] for c in chips}) == 6                                       # (the end of the name still tells them apart)
    rows = {ctl.win.home.findChild(type(ctl.win.home), "x")} if False else None
    tops = {ctl._index()[f"home.recent.{i}"].parentWidget() for i in range(6)}
    assert len(tops) == 2                                                             # two rows of three


def test_a_done_step_with_something_to_check_shows_the_warning_mark(ctl, solved):
    ctl.do("study.import", {"path": solved["static"]})
    t = {k: ctl._index()[f"step.{k}"] for k in ("geometry", "setup", "mesh", "solve", "results")}
    n = sum(1 for c in ctl.engine.state()["checks"] if c.get("level") == "warning" and c.get("step") == "setup")
    assert n >= 1                                                                     # (the default material is to be confirmed)
    assert "⚠" in t["setup"].text() and "to check" in t["setup"].toolTip() and "ready" in t["setup"].toolTip()
    assert "✓" in t["geometry"].text() and "✓" in t["mesh"].text() and "✓" in t["solve"].text()


def test_the_probe_tab_is_a_toggle_and_its_buttons_wait_for_data(ctl, solved):
    ctl.do("study.import", {"path": solved["static"]})
    ctl.do("step.go", {"step": "results"})
    ctl.do("results.tab", {"name": "probe"})
    assert "off" in ctl.widget_do("results.probe")["text"].lower()
    assert ctl.widget_do("results.line.copy")["enabled"] is False and ctl.widget_do("results.line.clear")["enabled"] is False
    ctl.widget_do("results.probe", "click")
    assert "ON" in ctl.widget_do("results.probe")["text"] and ctl.widget_do("results.probe")["value"] == {"checked": True}
    ctl.do("results.line_minmax")
    assert ctl.widget_do("results.line.copy")["enabled"] is True and ctl.widget_do("results.line.clear")["enabled"] is True
    ctl.widget_do("results.line.clear", "click")
    assert ctl.widget_do("results.line.copy")["enabled"] is False and ctl.widget_do("results.line.clear")["enabled"] is False


def test_the_step_panel_starts_at_its_top_whenever_a_step_is_shown(ctl, solved):
    ctl.do("study.import", {"path": solved["static"]})
    ctl.win.resize(1200, 600)
    ctl.do("step.go", {"step": "results"})
    ctl._settle()
    sc = ctl.win.stack.currentWidget().verticalScrollBar()
    assert sc.maximum() > 0
    sc.setValue(sc.maximum())
    ctl.do("step.go", {"step": "setup"})
    ctl.do("step.go", {"step": "results"})
    assert ctl.win.stack.currentWidget().verticalScrollBar().value() == 0


def test_collapsible_sections_are_styled_as_headings(ctl, solved):
    ctl.do("study.import", {"path": solved["static"]})
    ctl.do("step.go", {"step": "mesh"})
    b = ctl._index()["mesh.parts.toggle"]
    assert b.objectName() == "secHead" and b.text().startswith(("▶", "▼"))
    ctl.widget_do("mesh.parts.toggle", "click")
    assert b.text().startswith("▼")
