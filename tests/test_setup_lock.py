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
    pr.data["case_run"] = {k: pr.data["settings"].get(k) for k in ("n_frames", "frame_deg")}
    pr.save()
    return pr


def test_changing_a_setting_and_back_unlocks_the_run(P, tmp_path):
    pr = _study(P, tmp_path)
    pr.set_settings(speed=45)
    assert pr.data["solve"].get("setup_changed") is True
    assert pr.setup_changes() == ["Flow speed: 30.0 to 45.0 m/s"]
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
    with pytest.raises(ValueError, match="cannot tell what"):
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


def test_the_list_of_changes_has_plain_names_without_double_brackets(P, tmp_path):
    pr = _study(P, tmp_path)
    labels = pr._setup_labels()
    assert all("(" not in name for name, _ in labels.values())            # "Time step (0 = auto)" -> "Time step"
    assert labels["time_step"][0] == "Time step" and labels["time_step"][1] == "s"
    pr.set_settings(time_step=0.0001)
    ch = pr.setup_changes()
    assert ch == ["Time step: 0.0 to 0.0001 s"], ch
    with pytest.raises(ValueError) as e:
        pr.solve(continue_iterations=1)
    assert "(Time step: 0.0 to 0.0001 s)" in str(e.value) and "((" not in str(e.value) and "))" not in str(e.value)


# ------------------------------------------------------------------------------ a study made by the older version
def _old_study(P, tmp_path, status="failed"):
    pr = _study(P, tmp_path, status)
    pr.data.pop("case_setup")
    pr.data.pop("case_run")
    pr.data["solve"]["setup_changed"] = True
    pr.save()
    return pr


def test_an_old_study_shows_why_it_is_locked_and_what_can_be_done(P, tmp_path):
    pr = _old_study(P, tmp_path)
    lock = pr.state()["solve_lock"]
    assert lock["can_adopt"] and not lock["known"]
    m = lock["message"]
    assert "older version" in m and "The setup matches the case folder: use it" in m and "Load results from case" in m
    assert "run the simulation again" in m
    pr.set_settings(speed=45)                                  # (nothing to compare with: still locked)
    assert pr.state()["solve_lock"] is not None


def test_confirming_that_the_setup_matches_unlocks_the_old_study(P, tmp_path, monkeypatch):
    pr = _old_study(P, tmp_path)
    with pytest.raises(ValueError, match="older version"):
        pr.solve(continue_iterations=1)
    pr.adopt_current_setup()
    assert not pr.data["solve"].get("setup_changed") and pr.state()["solve_lock"] is None
    assert pr.setup_changes() == []
    from simwb.solvers.openfoam import runner
    monkeypatch.setattr(runner, "saved_transient_time", lambda case, prune=False: 0.05)
    monkeypatch.setattr(P.JOBS, "start", lambda *a, **k: "job")
    assert pr.solve(continue_iterations=1) == "job"            # the real resume checks the case on disk as it always did
    # from now on a real change locks it again, with the names
    pr.set_settings(speed=45)
    with pytest.raises(ValueError, match="Flow speed"):
        pr.solve(continue_iterations=1)


def test_a_wrong_confirmation_is_refused_when_the_case_folder_can_tell(P, tmp_path):
    pr = _old_study(P, tmp_path)
    meta = json.loads((pr.case_dir / "simwb_case.json").read_text())
    meta.pop("transient")                                      # the case folder holds a steady run, the setup says transient
    (pr.case_dir / "simwb_case.json").write_text(json.dumps(meta))
    with pytest.raises(ValueError, match="do not match"):
        pr.adopt_current_setup()
    assert pr.data["solve"].get("setup_changed") and "case_setup" not in pr.data     # nothing changed
    meta["study"] = "cht_enclosure"
    (pr.case_dir / "simwb_case.json").write_text(json.dumps(meta))
    with pytest.raises(ValueError, match="another kind of study"):
        pr.adopt_current_setup()


def test_adopting_is_only_for_studies_without_a_record(P, tmp_path):
    pr = _study(P, tmp_path)
    with pytest.raises(ValueError, match="already knows"):
        pr.adopt_current_setup()


def test_a_done_old_study_comes_back_as_done(P, tmp_path):
    pr = _old_study(P, tmp_path, "stale")
    pr.data["solve"]["summary"] = {"time": 0.1}
    pr.adopt_current_setup()
    assert pr.data["solve"]["status"] == "done"


# ------------------------------------------------------------------------------ the save interval on resume
def test_a_changed_save_interval_does_not_lock_and_is_applied_on_resume(P, tmp_path, monkeypatch):
    from simwb.solvers.openfoam import runner

    pr = _study(P, tmp_path)
    monkeypatch.setattr(runner, "saved_transient_time", lambda case, prune=False: 0.15)
    seen = []
    monkeypatch.setattr(pr, "_extend_transient", lambda end, wi=None: seen.append((end, wi)) or "job")
    monkeypatch.setattr(P.JOBS, "start", lambda *a, **k: seen.append("plain") or "job")
    pr.set_settings(n_frames=11)                                # 21 points -> 11 points over 0.2 s: every 0.02 s
    assert pr.setup_changes() == [] and not pr.data["solve"].get("setup_changed")
    assert pr.solve(continue_iterations=1) == "job"
    assert seen[0][0] == 0.2 and abs(seen[0][1] - 0.02) < 1e-12 and pr.data["case_run"]["n_frames"] == 11
    seen.clear()
    pr.solve(continue_iterations=1)                              # nothing changed since: a plain resume
    assert seen == ["plain"]


def test_a_changed_frame_angle_changes_the_interval_of_a_sliding_run(P, tmp_path, monkeypatch):
    from simwb.solvers.openfoam import runner

    pr = _study(P, tmp_path)
    meta = json.loads((pr.case_dir / "simwb_case.json").read_text())
    meta["transient"]["frame_deg"] = 8.0                         # (the case saves every 8 degrees: 0.01 s)
    (pr.case_dir / "simwb_case.json").write_text(json.dumps(meta))
    monkeypatch.setattr(runner, "saved_transient_time", lambda case, prune=False: 0.15)
    seen = []
    monkeypatch.setattr(pr, "_extend_transient", lambda end, wi=None: seen.append((end, wi)) or "job")
    pr.data["settings"]["frame_deg"] = 8.0
    pr.data["case_run"]["frame_deg"] = 8.0
    pr.set_settings(frame_deg=16)
    pr.solve(continue_iterations=1)
    assert abs(seen[0][1] - 0.02) < 1e-12                        # twice the angle, twice the interval
