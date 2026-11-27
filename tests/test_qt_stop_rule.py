"""Issue 14: 'Stop when forces settle' is on the Solve step next to Max iterations, kept in step with the setting."""
from qt_support import ctl, from_thread  # noqa: F401


def _show(ctl):
    ctl.win.panels["solve"].foam = {"available": True}
    ctl.do("step.go", {"step": "solve"})
    ctl.engine.changed.emit()
    ctl._settle()


def test_field_on_the_solve_step_is_in_sync_with_the_setting(ctl):
    ctl.do("study.new", {"name": "Plate", "study": "external_aero"})
    pr = ctl.engine.project()
    _show(ctl)
    e = ctl.widget_do("solve.auto_stop_tol")
    assert e["visible"] and e["value"] == "0.2"                           # the default, as in Setup
    assert ctl.widget_do("solve.auto_stop_tol.hint")["visible"]           # the meaning of 0 is visible, not only a tooltip
    ctl.widget_do("solve.auto_stop_tol", "set", "0")
    ctl._settle()
    assert float(pr.data["settings"]["auto_stop_tol"]) == 0.0
    pr.set_settings(auto_stop_tol=0.02)                                    # changed in Setup: the Solve field follows
    ctl.engine.changed.emit()
    ctl._settle()
    assert ctl.widget_do("solve.auto_stop_tol")["value"] == "0.02"
    pr.set_settings(analysis="transient", end_time=0.1)                    # a transient run has no stop rule
    ctl.engine.changed.emit()
    ctl._settle()
    assert not ctl.widget_do("solve.auto_stop_tol")["visible"]


def test_value_above_the_cap_is_refused(ctl):
    ctl.do("study.new", {"name": "Plate", "study": "external_aero"})
    pr = ctl.engine.project()
    import pytest
    with pytest.raises(ValueError):
        pr.set_settings(auto_stop_tol=100)


def test_a_bad_typed_value_is_replaced_by_the_last_valid_one(ctl):
    ctl.do("study.new", {"name": "Plate", "study": "external_aero"})
    _show(ctl)
    panel = ctl.win.panels["solve"]
    errors = []
    panel._fail = errors.append                                   # (the toast would only be shown)
    ctl.widget_do("solve.auto_stop_tol", "set", "0.05")
    panel.stoptol.setText("50")                                   # (setText skips the validator)
    panel._stoptol()
    assert errors and panel.stoptol.text() == "0.05"
    assert float(ctl.engine.project().data["settings"]["auto_stop_tol"]) == 0.05
