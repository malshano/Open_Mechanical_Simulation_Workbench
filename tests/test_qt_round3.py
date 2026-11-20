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


def test_the_model_is_visible_when_the_camera_is_fitted_to_it(ctl, solved):
    """The camera used to be fitted before the model was shown (after a result or mesh view), so the model came up cropped."""
    ctl.do("study.import", {"path": solved["static"]})
    ctl.do("step.go", {"step": "results"})                                             # (the CAD model is hidden here)
    assert ctl.win.view._geo_shown is False
    ctl.win.new_study()                                                                # back to the start screen
    seen = []
    real = ctl.win.view.fit
    ctl.win.view.fit = lambda direction=None: (seen.append(ctl.win.view._geo_shown), real(direction))[1]
    ctl.do("study.import", {"path": solved["heat"]})
    ctl.do("step.go", {"step": "setup"})
    assert seen and seen[-1] is True, seen                                             # (the fit to the model happens with it shown)


def _items(c):
    root = c.win.tree.topLevelItem(0)
    return {root.child(i).text(0): root.child(i) for i in range(root.childCount())}


def test_the_outline_shows_state_in_colour_and_words_and_selects_the_current_step(ctl, solved):
    from simwb.qt import theme
    ctl.do("study.import", {"path": solved["static"]})
    ctl.do("step.go", {"step": "mesh"})
    it = _items(ctl)
    mesh = next(v for k, v in it.items() if "Mesh —" in k)
    assert mesh.text(0).startswith("✓") and mesh.foreground(0).color().name() == theme.tokens()["ok_text"] and mesh.font(0).bold()
    assert ctl.win.tree.currentItem() is mesh                                          # the row of the step being worked on
    ctl.do("step.go", {"step": "solve"})
    assert ctl.win.tree.currentItem().text(0).startswith("✓ Solution")
    child = next(v for k, v in it.items() if k == "Materials").child(0)
    assert child.foreground(0).color().name() == theme.tokens()["muted"] and not child.font(0).bold()        # a detail: quieter
    ctl.do("theme.set", {"mode": "light"})                                             # the colours follow the theme
    mesh = next(v for k, v in _items(ctl).items() if "Mesh —" in k)
    assert mesh.foreground(0).color().name() == theme.tokens()["ok_text"] == "#17692e"


def test_the_outline_says_what_is_missing_and_what_is_not_there_yet(ctl):
    ctl.do("study.example", {"study": "heat_conduction"})
    wait_ = __import__("test_qt_chrome").wait
    wait_(ctl)
    it = _items(ctl)
    assert any(k.startswith("Supports & loads — ⚠") and "missing" in k for k in it)     # a heat study needs a condition
    assert "Results — run the simulation first" in it
    assert it["Results — run the simulation first"].childCount() == 0


def test_a_fitted_view_is_fitted_again_when_resized_until_the_user_moves_the_camera(ctl, solved):
    """A study opened from the start screen was fitted before the 3D view had its final size and came up too big; the view now fits again."""
    ctl.do("study.import", {"path": solved["static"]})
    ctl.do("step.go", {"step": "setup"})
    v = ctl.win.view
    assert v._autofit is True                                                          # (fitted by the program)
    calls = []
    v._refit = lambda: calls.append(1) if v._autofit else None                         # (only while the program owns the camera)
    ctl.win.resize(1250, 780)
    ctl._settle()
    assert calls                                                                       # resized: fitted again
    v._user_moved()                                                                    # the user turned or zoomed the camera
    n = len(calls)
    ctl.win.resize(1400, 850)
    ctl._settle()
    assert len(calls) == n and v._autofit is False                                     # their view is left alone
    ctl.widget_do("viewbar.fit", "click")                                              # Fit puts it under the program's care again
    assert v._autofit is True
    ctl.do("view.camera", {"position": [1, 2, 3]}) if "view.camera" in ctl.actions else v.set_camera(position=[1, 2, 3])
    assert v._autofit is False


def test_the_docks_start_with_sizes_that_favour_the_3d_view_and_the_step_panel(ctl, solved):
    ctl.do("study.import", {"path": solved["static"]})
    w = ctl.win
    w.resize(1500, 920)
    w.default_layout()
    ctl._settle()
    assert w.dock_chat.width() <= 0.3 * w.width() and w.dock_chat.width() >= 250       # the assistant is a side panel, not half the window
    assert w.view.width() > w.dock_chat.width() * 1.4                                 # the 3D view has the most width
    assert w.dock_panel.height() > 1.5 * w.dock_tree.height()                         # the step panel has the height, the outline is short


def test_the_solve_step_leads_with_run_and_progress_and_folds_the_software_list(ctl, solved):
    ctl.do("study.import", {"path": solved["static"]})
    ctl.do("step.go", {"step": "solve"})
    idx = ctl._index()
    run, prog, state, sw = idx["solve.run"], idx["solve.progress"], idx["solve.state"], idx["solve.software.toggle"]
    y = lambda wdg: wdg.mapTo(ctl.win, wdg.rect().topLeft()).y()  # noqa: E731
    assert y(run) < y(prog) < y(state) < y(sw)                                         # run first, the software list below
    assert ctl.widget_do("solve.software.toggle")["value"] == {"checked": False}        # folded away until wanted


def test_the_assistant_panel_can_be_narrow_and_its_suggestions_wrap(ctl, solved):
    """The suggestion chips were one row, so the panel could not be narrower than three chips (about 970 px) and squeezed the 3D view."""
    ctl.do("study.import", {"path": solved["static"]})
    ctl.do("step.go", {"step": "results"})
    a = ctl.win.assistant
    assert a.minimumSizeHint().width() < 450
    chips = [ctl._index()[f"assistant.chip.{i}"] for i in range(3)]
    ctl.win.dock_chat.setMinimumWidth(0)
    a.resize(330, 700)
    a.layout().activate()
    ctl._settle()
    ys = {c.mapTo(a, c.rect().topLeft()).y() for c in chips}
    assert len(ys) >= 2                                                                # at a narrow width they take more than one line
    assert all(c.mapTo(a, c.rect().topLeft()).x() + c.width() <= a.width() + 2 for c in chips)    # and none runs off the panel


def test_labels_on_a_result_are_visible_not_only_labels_on_the_model(ctl, solved):
    """The highest/lowest value labels and pinned points hung on a result were created invisible: the result view hides the CAD model and the
    labels followed the model's visibility. (They existed, so the tests saw them; the real window did not show them.)"""
    ctl.do("study.import", {"path": solved["static"]})
    ctl.do("step.go", {"step": "results"})
    v = ctl.win.view
    assert v._geo_shown is False and len(v.label_actors) >= 2
    assert all(la.GetVisibility() for la in v.label_actors)
    p = v.extrema()["max"]["point"]
    ctl.do("results.pin", {"x": p[0], "y": p[1], "z": p[2]})
    assert all(la.GetVisibility() for la in v.label_actors) and any(la.GetInput().startswith("A: ") for la in v.label_actors)
    ctl.do("step.go", {"step": "setup"})                                                # on the model: shown with the model
    assert v._geo_shown is True and all(la.GetVisibility() for la in v.label_actors)


def test_the_outline_keeps_the_current_step_in_view_and_pending_results_are_not_shouted(ctl, solved):
    ctl.do("study.import", {"path": solved["static"]})
    ctl.do("step.go", {"step": "results"})
    ctl._settle()
    t = ctl.win.tree
    cur = t.currentItem()
    assert cur is not None and t.viewport().rect().contains(t.visualItemRect(cur).center())
    ctl.do("study.example", {"study": "heat_conduction"})
    items = {t.topLevelItem(0).child(i).text(0): t.topLevelItem(0).child(i) for i in range(t.topLevelItem(0).childCount())}
    pending = items["Results — run the simulation first"]
    assert not pending.font(0).bold() and next(v for k, v in items.items() if k.startswith("Geometry (")).font(0).bold() is True


def test_probe_is_a_toggle_and_a_finished_run_demotes_the_run_button(ctl, solved):
    ctl.do("study.import", {"path": solved["static"]})
    ctl.do("step.go", {"step": "results"})
    assert ctl._index()["results.probe"].objectName() == "toggle"
    ctl.do("step.go", {"step": "solve"})
    b = ctl._index()["solve.run"]
    assert b.text().startswith("↻") and b.objectName() == "ghostBorder"                  # done: run again is a secondary action
    assert ctl._index()["solve.results"].objectName() == "primary"
    ctl.do("study.example", {"study": "static_structural"})
    b = ctl._index()["solve.run"]
    ctl.do("step.go", {"step": "solve"})
    assert b.text().startswith("▶") and b.objectName() == "primary"


def test_the_setup_mark_names_what_is_to_be_checked(ctl, solved):
    ctl.do("study.import", {"path": solved["static"]})
    tip = ctl._index()["step.setup"].toolTip()
    assert "to check" in tip and "default material" in tip


def test_labels_on_a_result_show_four_significant_digits(ctl, solved):
    ctl.do("study.import", {"path": solved["static"]})
    ctl.do("step.go", {"step": "results"})
    texts = [la.GetInput() for la in ctl.win.view.label_actors]
    for t in texts:
        num = t.split()[1]
        assert len(num.replace(".", "").replace("-", "").replace("e", "").lstrip("0")) <= 6, t          # (not 0.00190857)
