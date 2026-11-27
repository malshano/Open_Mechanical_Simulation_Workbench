"""The Solve step says what changed (or that an older study cannot tell) when an old run is locked, offers the ways out, and
offers 'Load results from case' only when it can work."""
import json
import os
import sys
from pathlib import Path

from qt_support import ctl, from_thread  # noqa: F401


def _study(ctl, status="failed", snapshot=True, pieces=0, aside=0):
    from simwb import project as P

    ctl.do("study.new", {"name": "Wing", "study": "external_aero"})
    pr = ctl.engine.project()
    pr.set_settings(analysis="transient", end_time=0.2, speed=30)
    pr.case_dir.mkdir(parents=True)
    (pr.case_dir / "simwb_case.json").write_text(json.dumps({"app": "pimpleFoam", "regions": [], "steps": [], "study": "external_aero",
                                                             "transient": {"end_time": 0.2, "write_interval": 0.01}}))
    pr.data["solve"] = {"status": status, "case_dir": str(pr.case_dir)}
    if snapshot:
        pr.data["case_setup"] = P.setup_snapshot(pr.data)
    else:
        pr.data["solve"]["setup_changed"] = True
    for i in range(pieces):
        (pr.case_dir / "processor0" / f"0.0{i + 1}5").mkdir(parents=True)  # saved times that only exist as pieces
    for i in range(aside):
        (pr.case_dir / "unjoined" / f"0.0{i + 7}" / "processor0").mkdir(parents=True)
    pr.save()
    return pr


def _show(ctl, foam=True):
    ctl.win.panels["solve"].foam = {"available": bool(foam)}   # (the machine may have no OpenFOAM: the panel is told what it should assume)
    ctl.do("step.go", {"step": "solve"})
    ctl.engine.changed.emit()
    ctl._settle()
    return str(ctl.widget_do("solve.note")["value"])


def test_the_solve_step_names_what_changed_and_the_way_back(ctl):
    pr = _study(ctl, pieces=1)
    pr.set_settings(speed=45)
    note = _show(ctl)
    assert "The setup changed since the last run" in note and "Flow speed: 30.0 to 45.0 m/s" in note and "Change it back" in note
    assert "1 saved time(s) that are not loaded yet" in note and "Load results from case" in note
    assert ctl.widget_do("solve.resume")["visible"] is False and ctl.widget_do("solve.adopt_setup")["visible"] is False
    assert ctl.widget_do("solve.load_case")["visible"] and ctl.widget_do("solve.load_case")["enabled"]
    pr.set_settings(speed=30)  # changed back: the old run can be resumed again
    note = _show(ctl)
    assert ctl.widget_do("solve.resume")["visible"] is True and "The setup changed" not in note


def test_an_old_study_gets_a_note_and_a_way_out(ctl):
    pr = _study(ctl, snapshot=False)
    note = _show(ctl)
    assert "cannot tell what" in note and "older version" in note and "run the simulation again" in note
    assert ctl.widget_do("solve.resume")["visible"] is False
    assert ctl.widget_do("solve.adopt_setup")["visible"] is True
    assert "cannot check" in ctl._index()["solve.adopt_setup"].toolTip()          # the risk is said where the button is
    ctl.do("solve.adopt_setup")                                                      # the control command clicks it
    ctl._settle()
    assert pr.data["case_setup"] and not pr.data["solve"].get("setup_changed")
    _show(ctl)
    assert ctl.widget_do("solve.resume")["visible"] is True and ctl.widget_do("solve.adopt_setup")["visible"] is False


def test_a_wrong_confirmation_in_the_window_says_why_and_changes_nothing(ctl):
    pr = _study(ctl, snapshot=False)
    meta = json.loads((pr.case_dir / "simwb_case.json").read_text())
    meta.pop("transient")
    (pr.case_dir / "simwb_case.json").write_text(json.dumps(meta))
    _show(ctl)
    ctl.widget_do("solve.adopt_setup", "click")
    ctl._settle()
    assert "case_setup" not in pr.data and pr.data["solve"].get("setup_changed")
    assert any("do not match" in h["text"] for h in ctl.win.toasts.history)


def test_load_results_needs_openfoam_only_when_times_are_still_split(ctl):
    _study(ctl, pieces=2)
    note = _show(ctl, foam=False)
    assert ctl.widget_do("solve.load_case")["enabled"] is False
    assert "needs OpenFOAM" in ctl._index()["solve.load_case"].toolTip()
    assert "2 saved time(s) that are not loaded yet" in note
    _show(ctl, foam=True)
    assert ctl.widget_do("solve.load_case")["enabled"] is True


def test_load_results_without_openfoam_says_the_real_reason(ctl, monkeypatch):
    from simwb.solvers.openfoam import runner

    pr = _study(ctl, pieces=1)
    monkeypatch.setattr(runner, "detect", lambda refresh=False: {"available": False, "backend": None})
    job = pr.load_case_results()
    assert job.wait(30) and job.status == "failed"
    assert "needs OpenFOAM" in job.error and "not installed" in job.error and "untouched" in job.error


def test_times_set_aside_after_a_failed_join_are_counted_and_named(ctl):
    from simwb.solvers.openfoam import runner

    pr = _study(ctl, aside=2)
    assert runner.unjoined_times(pr.case_dir) == 2 and runner.unjoined_times(pr.case_dir, aside=False) == 0
    assert runner.set_aside_count(pr.case_dir) == 2
    note = _show(ctl)
    assert "2 saved time(s) could not be joined and were set aside in case/unjoined/" in note and "deletes them" in note


def test_the_load_control_command_starts_the_load(ctl, monkeypatch):
    """solve.load_case clicks the button; the job joins the pieces (fake reconstruct) and loads the newest time."""
    from simwb.solvers.openfoam import results_loader, runner

    pr = _study(ctl, pieces=1)
    fake = pr.dir / "fake_reconstruct.py"
    fake.write_text("import sys, pathlib\na = sys.argv\nt, c = a[a.index('-time') + 1], a[a.index('-case') + 1]\n"
                    "(pathlib.Path(c) / t).mkdir()\n(pathlib.Path(c) / t / 'U').write_text('x')\n")
    real = runner.run_split_times
    monkeypatch.setattr(runner, "detect", lambda refresh=False: {"available": True, "backend": "windows",
                                                                 "project_dir": str(pr.dir), "bin_dir": str(pr.dir)})
    monkeypatch.setattr(runner, "windows_env", lambda *a: dict(os.environ))
    monkeypatch.setattr(runner, "run_split_times", lambda case, st, exe, env, *a, **k: real(
        case, dict(st, args=[str(fake)]), Path(sys.executable), env, *a, **k))
    seen = {}

    class Rs:
        def save(self, path):
            Path(path).write_bytes(b"x")
    monkeypatch.setattr(results_loader, "load", lambda case, data: (seen.update(t=results_loader.latest_time(case)) or Rs(), {"time": 1}))
    _show(ctl)
    assert ctl.do("solve.load_case")["result"]["started"]
    from simwb import project as P
    job = P.JOBS.latest(pr.id, "solve")
    assert job.wait(60) and job.status == "done", (job.error, job.log[-5:])
    assert seen["t"] == "0.015" and (pr.case_dir / "0.015" / "U").exists()
