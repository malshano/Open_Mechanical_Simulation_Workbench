"""Issue 12: one damaged saved time must not stop the join of the others, and a failure is named for what it is.

Everything here is synthetic: fake solver logs, a fake processor folder tree and a stand-in for reconstructPar."""
import json
import os
import sys
from pathlib import Path

import pytest

from simwb.solvers.openfoam import runner

BANNER = ("Build  : v2312\nExec   : pimpleFoam -parallel\n"
          "sigFpe : Enabling floating point exception trapping (FOAM_SIGFPE).\n"
          "trapFpe: Floating point exception trapping enabled (FOAM_SIGFPE).\n")
IO_ERROR = ("--> FOAM FATAL IO ERROR: \nBad token - could not get scalar value\n\n"
            "file: 0.015/meshPhi at line 2068449.\n\n    From function Foam::operator>>(Foam::Istream&, Foam::scalar&)\n")


def test_the_banner_line_is_not_a_divergence(tmp_path):
    """Every run prints that floating point exceptions are trapped: that says nothing about the run."""
    clean = BANNER + "Time = 0.15\nEnd\n\nFinalising parallel run\n"
    assert not runner.real_fpe(clean)
    assert runner.failure_hint(tmp_path, clean) == ""
    assert runner.failure_hint(tmp_path, BANNER, "reconstructPar") == ""


def test_a_real_floating_point_exception_is_still_a_divergence(tmp_path):
    crash = BANNER + "#0  Foam::error::printStack(Foam::Ostream&)\n#1  Foam::sigFpe::sigHandler(int)\n"
    assert runner.real_fpe(crash) and "diverged" in runner.failure_hint(tmp_path, crash)
    assert runner.real_fpe("Floating point exception (core dumped)")
    assert "diverged" in runner.failure_hint(tmp_path, "sigFpe : Floating point exception")
    # ... but not when the program that failed was the join, not the solver
    assert "diverged" not in runner.failure_hint(tmp_path, crash, "reconstructPar")


def test_an_unreadable_file_is_reported_as_what_it_is(tmp_path):
    log = BANNER + "joined 4 of 31\n" + IO_ERROR
    assert runner.io_error(log) == {"why": "Bad token - could not get scalar value", "file": "0.015/meshPhi", "line": 2068449}
    hint = runner.failure_hint(tmp_path, log, "reconstructPar")
    assert "0.015/meshPhi" in hint and "2068449" in hint and "diverged" not in hint and "damaged" in hint
    assert "diverged" not in runner.failure_hint(tmp_path, log)  # (the banner above it does not turn it into one)


# --------------------------------------------------------------------------------------------- joining the saved times
FAKE = """import sys, pathlib
a = sys.argv
t, c = a[a.index('-time') + 1], a[a.index('-case') + 1]
bad = {b for b in %r}
print('Create time', t)
if t in bad:
    print('--> FOAM FATAL IO ERROR: ')
    print('Bad token - could not get scalar value')
    print()
    print('file: %%s/meshPhi at line 2068449.' %% t)
    (pathlib.Path(c) / t).mkdir(exist_ok=True)   # (a half-written join)
    (pathlib.Path(c) / t / 'U').write_text('partial')
    sys.exit(1)
(pathlib.Path(c) / t).mkdir()
print('End')
"""


def _tree(tmp_path, times, nproc=3):
    for i in range(nproc):
        for t in times + ["0"]:
            (tmp_path / f"processor{i}" / t).mkdir(parents=True)
            (tmp_path / f"processor{i}" / t / "U").write_text(f"{t}")


def _join(tmp_path, bad, times, monkeypatch, **kw):
    fake = tmp_path / "fake_reconstruct.py"
    fake.write_text(FAKE % (list(bad),))
    monkeypatch.setattr(runner, "JOIN_RAMP_S", 0.0)
    from simwb import memlimit
    monkeypatch.setattr(memlimit, "memory", lambda: (32 * 2 ** 30, 30 * 2 ** 30))
    rep, lines = {}, []
    rc = runner.run_split_times(tmp_path, {"split_times": 1, "log": "log.reconstructPar", "args": [str(fake)]},
                                Path(sys.executable), dict(os.environ), on_line=lines.append, report=rep, **kw)
    return rc, rep, lines


def test_one_damaged_time_does_not_stop_the_others(tmp_path, monkeypatch):
    times = ["0.001", "0.005", "0.010", "0.015", "0.020", "0.025"]
    _tree(tmp_path, times)
    rc, rep, lines = _join(tmp_path, ["0.015"], times, monkeypatch)
    assert rc != 0  # (the worst exit code is still reported)
    assert sorted(rep["joined"]) == sorted(t for t in times if t != "0.015")
    assert [s["time"] for s in rep["skipped"]] == ["0.015"]
    assert "meshPhi" in rep["skipped"][0]["reason"] and "2068449" in rep["skipped"][0]["reason"]
    for t in times:
        assert (tmp_path / t).is_dir() == (t != "0.015")
    # nothing is deleted: the damaged time's pieces are put aside, the good times' pieces were removed after joining
    for i in range(3):
        assert (tmp_path / "unjoined" / "0.015" / f"processor{i}" / "U").read_text() == "0.015"
        assert not any((tmp_path / f"processor{i}" / t).exists() for t in times)
        assert (tmp_path / f"processor{i}" / "0").is_dir()
    assert any("0.015" in ln and "skipped" in ln for ln in lines)


def test_the_newest_time_is_joined_first(tmp_path, monkeypatch):
    times = ["0.001", "0.002", "0.003", "0.004"]
    _tree(tmp_path, times)
    rc, rep, _ = _join(tmp_path, [], times, monkeypatch)
    assert rc == 0 and rep["joined"][0] == "0.004" and sorted(rep["joined"]) == times


def test_when_the_latest_time_is_the_damaged_one_the_rest_is_still_joined(tmp_path, monkeypatch):
    times = ["0.001", "0.002", "0.003"]
    _tree(tmp_path, times)
    rc, rep, _ = _join(tmp_path, ["0.003"], times, monkeypatch)
    assert sorted(rep["joined"]) == ["0.001", "0.002"] and (tmp_path / "unjoined" / "0.003").is_dir()
    assert not (tmp_path / "0.003").exists()  # the half-written join is gone


def test_when_nothing_can_be_joined_the_pieces_stay_where_they_are(tmp_path, monkeypatch):
    times = ["0.001", "0.002", "0.003", "0.004", "0.005"]
    _tree(tmp_path, times)
    rc, rep, _ = _join(tmp_path, times, times, monkeypatch)
    assert rc != 0 and rep["joined"] == [] and len(rep["skipped"]) == 3  # stops trying after three failures
    assert all((tmp_path / "processor0" / t).is_dir() for t in times) and not (tmp_path / "unjoined").exists()


def test_loading_leaves_the_pieces_when_asked(tmp_path, monkeypatch):
    times = ["0.001", "0.002", "0.003"]
    _tree(tmp_path, times)
    rc, rep, _ = _join(tmp_path, ["0.002"], times, monkeypatch, keep_pieces=True)
    assert all((tmp_path / "processor1" / t).is_dir() for t in times) and not (tmp_path / "unjoined").exists()
    assert (tmp_path / "0.001").is_dir() and (tmp_path / "0.003").is_dir() and not (tmp_path / "0.002").exists()


# ---------------------------------------------------------------------------------------- the whole run of steps
def _steps(**over):
    return [dict({"app": "reconstructPar", "args": [], "log": "log.reconstructPar", "split_times": 2, "main": True}, **over),
            {"action": "rm_processors"}]


@pytest.fixture
def run(tmp_path, monkeypatch):
    real = runner.run_split_times
    fake = tmp_path / "fake_reconstruct.py"

    def go(bad, times):
        _tree(tmp_path, times)
        fake.write_text(FAKE % (list(bad),))
        monkeypatch.setattr(runner, "JOIN_RAMP_S", 0.0)
        monkeypatch.setattr(runner, "run_split_times", lambda case, st, exe, env, *a, **k: real(
            case, dict(st, args=[str(fake)]), Path(sys.executable), env, *a, **k))
        monkeypatch.setattr(runner, "windows_env", lambda *a: dict(os.environ))
        from simwb import memlimit
        monkeypatch.setattr(memlimit, "memory", lambda: (32 * 2 ** 30, 30 * 2 ** 30))
        lines = []
        ok = runner.run_steps_windows(tmp_path, _steps(), {"project_dir": str(tmp_path), "bin_dir": str(tmp_path)},
                                      on_line=lines.append)
        return ok, lines
    return go


def test_a_run_with_one_damaged_time_finishes_and_names_it(tmp_path, run):
    times = ["0.001", "0.002", "0.003", "0.004"]
    ok, lines = run(["0.002"], times)
    assert ok and (tmp_path / "case.done").exists() and not (tmp_path / "case.failed").exists()
    d = json.loads((tmp_path / runner.JOIN_SKIPPED).read_text())
    assert [s["time"] for s in d["skipped"]] == ["0.002"] and d["joined"] == 3
    assert any(ln.startswith("WARNING: time 0.002") for ln in lines)
    assert not list(tmp_path.glob("processor*"))  # (the usual clean-up happened; the damaged pieces are in unjoined/)
    assert (tmp_path / "unjoined" / "0.002" / "processor0" / "U").exists()


def test_a_failed_join_names_the_failed_step(tmp_path, run):
    times = ["0.001", "0.002", "0.003", "0.004"]
    ok, _ = run(times, times)
    assert not ok
    note = json.loads((tmp_path / "case.failed").read_text())
    assert note["step"] == "reconstructPar" and note["log"] == "log.reconstructPar"
    assert (tmp_path / "processor0" / "0.004").is_dir()  # nothing was deleted
    assert "diverged" not in runner.failure_hint(tmp_path, (tmp_path / "log.reconstructPar").read_text(), note["step"])


# ------------------------------------------------------------------------------------ "Load results from case"
class _Rs:
    def save(self, path):
        Path(path).write_bytes(b"x")


@pytest.fixture
def loader(tmp_path, monkeypatch):
    import importlib

    monkeypatch.setenv("SIMWB_HOME", str(tmp_path / "ws"))
    import simwb.project as P

    importlib.reload(P)
    from simwb.solvers.openfoam import results_loader

    pr = P.create_project("Wing", "external_aero", str(tmp_path / "studies"))
    pr.set_settings(analysis="transient", end_time=0.2)
    pr.case_dir.mkdir(parents=True)
    (pr.case_dir / "simwb_case.json").write_text(json.dumps({
        "app": "pimpleFoam", "regions": [], "steps": [], "study": "external_aero",
        "transient": {"end_time": 0.2, "write_interval": 0.01, "n_frames": 21, "dt": 1e-4, "fixed_dt": True}}))
    (pr.case_dir / "log.pimpleFoam").write_text(BANNER + "Time = 0.15\nEnd\n\nFinalising parallel run\n")
    pr.data["solve"] = {"status": "failed", "case_dir": str(pr.case_dir), "setup_changed": True}
    pr.save()
    real = runner.run_split_times
    fake = tmp_path / "fake_reconstruct.py"
    monkeypatch.setattr(runner, "detect", lambda refresh=False: {"available": True, "backend": "windows",
                                                                 "project_dir": str(tmp_path), "bin_dir": str(tmp_path)})
    monkeypatch.setattr(runner, "windows_env", lambda *a: dict(os.environ))
    monkeypatch.setattr(runner, "JOIN_RAMP_S", 0.0)
    monkeypatch.setattr(runner, "run_split_times", lambda case, st, exe, env, *a, **k: real(
        case, dict(st, args=[str(fake)]), Path(sys.executable), env, *a, **k))
    from simwb import memlimit
    monkeypatch.setattr(memlimit, "memory", lambda: (32 * 2 ** 30, 30 * 2 ** 30))
    monkeypatch.setattr(results_loader, "load", lambda case, data: (_Rs(), {"time": 0.15}))

    def go(bad, times):
        _tree(pr.case_dir, times)
        fake.write_text(FAKE % (list(bad),))
        return pr.load_case_results()
    return pr, go


def test_load_results_from_case_skips_the_damaged_time_and_keeps_the_pieces(loader):
    pr, go = loader
    times = ["0.01", "0.015", "0.02", "0.03"]
    job = go(["0.015"], times)
    assert job.wait(60) and job.status == "done", (job.error, job.log[-10:])
    sv = pr.data["solve"]
    assert sv["status"] in ("done", "stale") and sv["summary"]["loaded_from_case"]
    assert any("0.015" in w and "could not be joined" in w for w in sv["summary"]["warnings"])
    for t in times:
        assert (pr.case_dir / t).is_dir() == (t != "0.015")
    # loading is not destructive: the run can still be resumed from the pieces
    assert all((pr.case_dir / "processor1" / t).is_dir() for t in times)
    assert (pr.results_dir / "result.npz").exists()
    # a second load joins nothing twice and still works
    job = pr.load_case_results()
    assert job.wait(60) and job.status == "done", (job.error, job.log[-10:])


def test_load_results_from_case_says_so_when_nothing_can_be_read(loader):
    pr, go = loader
    times = ["0.01", "0.02", "0.03", "0.04"]
    job = go(times, times)
    assert job.wait(60) and job.status == "failed"
    assert "could be read" in job.error and "nothing was lost" in job.error
    assert all((pr.case_dir / "processor0" / t).is_dir() for t in times)


def test_a_failed_join_is_not_reported_as_a_divergence_by_the_run(tmp_path):
    """The job error of a run whose join failed names the join and the file, whatever the solver log says."""
    import importlib

    os.environ["SIMWB_HOME"] = str(tmp_path / "ws")
    import simwb.project as P

    importlib.reload(P)
    pr = P.create_project("Wing", "external_aero", str(tmp_path / "studies"))
    pr.case_dir.mkdir(parents=True)
    (pr.case_dir / "log.pimpleFoam").write_text(BANNER + "Time = 0.15\nEnd\n\nFinalising parallel run\n")
    (pr.case_dir / "log.reconstructPar").write_text(BANNER + "joined 4 of 31\n" + IO_ERROR)
    (pr.case_dir / "case.failed").write_text(json.dumps({"step": "reconstructPar", "log": "log.reconstructPar"}))
    step, tail = pr._failed_step()
    assert step["step"] == "reconstructPar" and "meshPhi" in tail
    assert "diverged" not in runner.failure_hint(pr.case_dir, tail, step["step"])
    # an old-style empty marker still works (the newest log is read)
    (pr.case_dir / "case.failed").write_text("")
    assert pr._failed_step()[0] == {}


# ---------------------------------------------- the load must never hand back an older time without saying so
def _fake(pr, bad):
    (pr.dir.parent.parent / "fake_reconstruct.py").write_text(FAKE % (list(bad),))


def _joined(case, t):
    (case / t).mkdir(parents=True, exist_ok=True)
    (case / t / "U").write_text(t)


def _load(pr, monkeypatch):
    """Run the load; returns (job, the case folder's latest time as results_loader.load saw it)."""
    from simwb.solvers.openfoam import results_loader

    seen = {}

    def load(case, data):
        seen["latest"] = results_loader.latest_time(case)
        return _Rs(), {"time": float(seen["latest"])}
    monkeypatch.setattr(results_loader, "load", load)
    job = pr.load_case_results()
    assert job.wait(60), "timed out"
    return job, seen


def test_one_newer_unjoined_time_is_joined_not_skipped(loader, monkeypatch):
    """processor*/{0, 0.1, 0.15} with case/0.1 joined: the old code joined nothing and silently loaded 0.1."""
    pr, go = loader
    _tree(pr.case_dir, ["0.1", "0.15"])
    _joined(pr.case_dir, "0.1")
    _fake(pr, [])
    job, seen = _load(pr, monkeypatch)
    assert job.status == "done", (job.error, job.log[-5:])
    assert seen["latest"] == "0.15" and (pr.case_dir / "0.15").is_dir()
    assert not any("could not be loaded" in w for w in pr.data["solve"]["summary"].get("warnings", []))
    assert (pr.case_dir / "processor0" / "0.15").is_dir()  # the pieces stay


def test_a_single_unjoined_time_with_nothing_joined_is_joined(loader, monkeypatch):
    pr, go = loader
    _tree(pr.case_dir, ["0.15"])
    _fake(pr, [])
    job, seen = _load(pr, monkeypatch)
    assert job.status == "done", (job.error, job.log[-5:])
    assert seen["latest"] == "0.15"


def test_when_the_newest_time_cannot_be_joined_the_older_result_is_flagged(loader, monkeypatch):
    pr, go = loader
    _tree(pr.case_dir, ["0.1", "0.15"])
    _joined(pr.case_dir, "0.1")
    _fake(pr, ["0.15"])
    job, seen = _load(pr, monkeypatch)
    assert job.status == "done", (job.error, job.log[-5:])
    assert seen["latest"] == "0.1"
    ws = pr.data["solve"]["summary"]["warnings"]
    assert any("newest saved time" in w and "0.15" in w and "0.1 s" in w for w in ws), ws
    assert any("could not be joined" in w for w in ws)
    assert pr.data["solve"]["summary"]["loaded_from_case"]


def test_a_half_written_join_does_not_count_as_joined(tmp_path):
    _tree(tmp_path, ["0.1"])
    (tmp_path / "0.1").mkdir()
    assert not runner.time_is_joined(tmp_path, "0.1")        # (empty folder: a join that was cut off)
    (tmp_path / "0.1" / "U").write_text("x")
    assert runner.time_is_joined(tmp_path, "0.1")
    assert runner.unjoined_times(tmp_path) == 0 and runner.newest_piece_time(tmp_path) == 0.1


def test_the_load_route_starts_the_job(loader, monkeypatch):
    import importlib

    from fastapi.testclient import TestClient

    pr, go = loader
    import simwb.server as S

    importlib.reload(S)
    _tree(pr.case_dir, ["0.1"])
    _fake(pr, [])
    from simwb.solvers.openfoam import results_loader
    monkeypatch.setattr(results_loader, "load", lambda case, data: (_Rs(), {"time": 0.1}))
    c = TestClient(S.app, base_url="http://127.0.0.1:8765")
    r = c.post(f"/api/projects/{pr.id}/results/load_case", headers={"X-SimWB": "1"})
    assert r.status_code == 200, r.text
    import simwb.project as P
    job = P.JOBS.latest(pr.id, "solve")
    assert job.wait(60) and job.status == "done", (job.error, job.log[-5:])
    assert (pr.case_dir / "0.1").is_dir()
    # a study without a case is refused with a plain message
    other = P.create_project("Other", "external_aero", str(pr.dir.parent))
    r = c.post(f"/api/projects/{other.id}/results/load_case", headers={"X-SimWB": "1"})
    assert r.status_code >= 400 and "no openfoam case" in r.text.lower()
