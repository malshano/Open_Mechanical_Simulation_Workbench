"""Issue 12, part 3: a study is locked against carrying on an old OpenFOAM run only while its setup really differs from
what the case on disk was made from. Changing a setting and changing it back leaves the run resumable; a real change says
what changed and what to do."""
import importlib
import json

import pytest


@pytest.fixture
def P(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMWB_HOME", str(tmp_path / "ws"))
    import simwb.project as P

    importlib.reload(P)
    return P


def _study(P, tmp_path, status="failed"):
    """A transient external-aero study with a written case and a run in the given state (no solver involved)."""
    pr = P.create_project("Wing", "external_aero", str(tmp_path / "studies"))
    pr.set_settings(analysis="transient", end_time=0.2, speed=30)
    pr.case_dir.mkdir(parents=True)
    (pr.case_dir / "simwb_case.json").write_text(json.dumps({
        "app": "pimpleFoam", "regions": [], "steps": [],
        "transient": {"end_time": 0.2, "write_interval": 0.01, "n_frames": 21, "dt": 1e-4, "fixed_dt": True}}))
    pr.data["solve"] = {"status": status, "case_dir": str(pr.case_dir)}
    pr.data["case_setup"] = P.setup_snapshot(pr.data)  # (what _solve_openfoam_inner stores when it writes the case)
    pr.save()
    return pr


def test_changing_a_setting_and_back_unlocks_the_run(P, tmp_path):
    pr = _study(P, tmp_path)
    pr.set_settings(speed=45)
    assert pr.data["solve"].get("setup_changed") is True
    assert len(pr.setup_changes()) == 1 and pr.setup_changes()[0].startswith("Flow speed")
    pr.set_settings(speed=30)
    assert not pr.data["solve"].get("setup_changed") and pr.setup_changes() == []
    # it survives closing and reopening the study
    pr2 = P.Project(pr.dir)
    assert not pr2.data["solve"].get("setup_changed") and pr2.setup_changes() == []


def test_a_finished_run_is_stale_while_changed_and_valid_again_when_changed_back(P, tmp_path):
    pr = _study(P, tmp_path, "done")
    pr.set_settings(pitch_deg=3)
    assert pr.data["solve"]["status"] == "stale"
    pr.set_settings(pitch_deg=pr.data["case_setup"]["settings"]["pitch_deg"])
    assert pr.data["solve"]["status"] == "done" and "stale_from" not in pr.data["solve"]


def test_a_real_change_says_what_changed_and_what_to_do(P, tmp_path):
    pr = _study(P, tmp_path)
    pr.set_settings(speed=45)
    pr.set_foam_advanced(convection="first order")
    with pytest.raises(ValueError) as e:
        pr.solve(continue_iterations=10)
    msg = str(e.value)
    assert "Flow speed" in msg and "Advanced OpenFOAM settings" in msg
    assert "Change it back" in msg and "run the simulation again" in msg
    pr.set_settings(speed=30)
    assert pr.setup_changes() == ["Advanced OpenFOAM settings"]  # one change left: still named, still locked
    assert pr.data["solve"].get("setup_changed")
    pr.set_foam_advanced(convection="default")
    assert pr.setup_changes() == [] and not pr.data["solve"].get("setup_changed")


def test_run_controls_do_not_lock_the_old_run(P, tmp_path):
    """The end time (applied on resume), the iterations, the cores and the time limit are read again at every start."""
    pr = _study(P, tmp_path)
    pr.set_settings(end_time=0.155, iterations=500, n_procs=4, max_hours=2)
    assert pr.setup_changes() == [] and not pr.data["solve"].get("setup_changed")


def test_resuming_with_a_changed_end_time_carries_on_to_it(P, tmp_path, monkeypatch):
    from simwb.solvers.openfoam import runner

    pr = _study(P, tmp_path)
    monkeypatch.setattr(runner, "saved_transient_time", lambda case, prune=False: 0.15)
    seen = {}
    monkeypatch.setattr(pr, "_extend_transient", lambda end, wi=None: seen.update(end=end, wi=wi) or "job")
    monkeypatch.setattr(P.JOBS, "start", lambda *a, **k: seen.update(resumed=True) or "job")
    pr.set_settings(end_time=0.155)
    assert pr.solve(continue_iterations=1) == "job"
    assert seen["end"] == 0.155 and 0 < seen["wi"] <= 0.005 + 1e-12 and "resumed" not in seen  # (clamped to what is left)
    seen.clear()
    pr.set_settings(end_time=0.2)  # back to the case's own end time: a plain resume
    pr.solve(continue_iterations=1)
    assert seen == {"resumed": True}


def test_a_study_run_by_an_older_version_keeps_the_old_rule_with_a_clear_message(P, tmp_path):
    pr = _study(P, tmp_path)
    pr.data.pop("case_setup")
    pr.set_settings(speed=45)
    assert pr.data["solve"].get("setup_changed") and pr.setup_changes() is None
    pr.set_settings(speed=30)
    assert pr.data["solve"].get("setup_changed")  # nothing to compare with: cannot know it is the same
    with pytest.raises(ValueError, match="cannot say what"):
        pr.solve(continue_iterations=1)


def test_a_copy_without_results_does_not_carry_the_case_snapshot(P, tmp_path):
    pr = _study(P, tmp_path)
    cp = P.duplicate_project(pr.id)
    assert "case_setup" not in cp.data


def test_a_change_made_while_running_and_undone_does_not_make_the_result_stale(P, tmp_path, monkeypatch):
    pr = _study(P, tmp_path, "running")
    monkeypatch.setattr(pr, "busy", lambda: True)
    pr.set_settings(speed=45)
    assert pr.data.get("edited_during_run")
    pr.set_settings(speed=30)
    assert not pr.data.get("edited_during_run")
