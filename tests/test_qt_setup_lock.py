"""The Solve step says what changed when an old run is locked, and offers 'Load results from case'."""
import json

from qt_support import ctl, from_thread  # noqa: F401


def test_the_solve_step_names_what_changed_and_the_way_back(ctl):
    from simwb import project as P

    ctl.do("study.new", {"name": "Wing", "study": "external_aero"})
    pr = ctl.engine.project()
    pr.set_settings(analysis="transient", end_time=0.2, speed=30)
    pr.case_dir.mkdir(parents=True)
    (pr.case_dir / "simwb_case.json").write_text(json.dumps({"app": "pimpleFoam", "regions": [], "steps": [],
                                                             "transient": {"end_time": 0.2, "write_interval": 0.01}}))
    pr.data["solve"] = {"status": "failed", "case_dir": str(pr.case_dir)}
    pr.data["case_setup"] = P.setup_snapshot(pr.data)
    (pr.case_dir / "processor0" / "0.05").mkdir(parents=True)  # a saved time that only exists as pieces
    pr.set_settings(speed=45)
    ctl.do("step.go", {"step": "solve"})
    ctl.engine.changed.emit()
    ctl._settle()
    note = ctl.widget_do("solve.note")["value"] if "solve.note" in ctl._index() else str(ctl._index())
    assert "Changed since the last run" in str(note) and "Flow speed" in str(note) and "Change it back" in str(note)
    assert "1 saved time(s) that are not loaded yet" in str(note) and "Load results from case" in str(note)
    assert ctl.widget_do("solve.resume")["visible"] is False
    assert ctl.widget_do("solve.load_case")["visible"] and ctl.widget_do("solve.load_case")["enabled"]
    ctl.win.panels["solve"].foam = {"available": True}  # (the machine may have no OpenFOAM: the buttons are the same)
    pr.set_settings(speed=30)  # changed back: the old run can be resumed again
    ctl.engine.changed.emit()
    ctl._settle()
    assert ctl.widget_do("solve.resume")["visible"] is True
    assert "Changed since the last run" not in str(ctl.widget_do("solve.note")["value"])
