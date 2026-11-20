"""Coupled-run controller (flutter_case.SpeedRun, flutter_run.Controller) without OpenFOAM: a stand-in fluid thread follows
the real lock protocol (forces.out, remove the lock, wait for the lock, read positions.in) and writes time directories."""
import json
import math
import re
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from simwb.solvers.openfoam import flutter_case as FCASE
from simwb.solvers.openfoam import flutter_cfd as FC
from simwb.solvers.openfoam import flutter_run as FR
from simwb.solvers.openfoam import lumped as L

DT = 2.5e-4
F1, F2 = 40.0, 90.0


# ------------------------------------------------------------------------------------------------ the structure and fluid
def _model():
    xs, ys = np.meshgrid(np.linspace(0, 0.2, 5), np.linspace(0, 1.0, 21))
    X = np.column_stack([xs.ravel(), ys.ravel(), np.zeros(xs.size)])
    heave = np.zeros_like(X)
    heave[:, 2] = X[:, 1] ** 2
    pitch = np.zeros_like(X)
    pitch[:, 2] = -(X[:, 0] - 0.08) * X[:, 1]
    return FC.build_node_model(X, np.arange(len(X)), np.array([heave, pitch]), [2 * math.pi * F1, 2 * math.pi * F2],
                               [1.5, 0.3], [1, 0, 0], [0, 1, 0], n_nodes=5, zeta=0.005)


class Aero:
    """Quasi-steady linear aerodynamics Q = -Ka q - Ca q' (coupling the two modes) with known complex eigenvalues."""

    def __init__(self, md):
        K = md.mg * md.omega ** 2
        self.Ka = np.array([[0.04 * K[0], 0.03 * K[0]], [-0.05 * K[1], 0.06 * K[1]]])
        self.Ca = np.array([[2 * 0.02 * md.mg[0] * md.omega[0], 0.0], [0.01 * md.mg[1] * md.omega[1], 2 * 0.012 * md.mg[1] * md.omega[1]]])
        M, Kk, C = np.diag(md.mg), np.diag(K) + self.Ka, np.diag(2 * md.zeta * md.mg * md.omega) + self.Ca
        A = np.block([[np.zeros((2, 2)), np.eye(2)], [-np.linalg.solve(M, Kk), -np.linalg.solve(M, C)]])
        ev = np.linalg.eigvals(A)
        ev = ev[ev.imag > 0]
        self.poles = sorted((e.imag / 2 / math.pi, -e.real / abs(e)) for e in ev)  # (f_Hz, zeta)
        self.Tm = md.T.reshape(md.m, -1)
        self.pinv = np.linalg.pinv(self.Tm)
        self.A = np.concatenate([md.T.reshape(md.m, -1), md.W.reshape(md.m, -1)], axis=1).T
        self.md = md

    def q_from(self, pos, ang):
        rot = np.array([[a[1] * math.cos(a[0]), a[1] * math.sin(a[0]), a[0] + a[2]] for a in ang])
        b = np.concatenate([(pos - self.md.x0).reshape(-1), rot.reshape(-1)])
        return np.linalg.lstsq(self.A, b, rcond=None)[0]


def _parse_positions(text):
    head, _, tail = text.partition("angles")
    pts = np.array(re.findall(L._VEC, head.partition("points")[2]), float).reshape(-1, 3)
    return pts, np.array(re.findall(L._VEC, tail), float).reshape(-1, 3)


class StubFluid(threading.Thread):
    """OpenFOAM's master side: per step it writes forces.out, removes the lock and waits for the structure's answer;
    it writes time directories every `write_every` of simulated time (with its own memory in stub.json, as the real
    flow field is in the time directory). The forces follow the wall motion it was given (Aero) plus a steady lift."""

    def __init__(self, case, aero, t_start, t_end, write_every, restart=None, hang_after=None, n_procs=2, steady=3.0):
        super().__init__(daemon=True)
        self.case, self.aero, self.t0, self.t_end, self.write_every = Path(case), aero, t_start, t_end, write_every
        self.restart, self.hang_after, self.n_procs, self.steady = restart, hang_after, n_procs, steady
        self.stop = threading.Event()
        self.exchanges = 0
        self.rc = None

    def _wait(self, cond):
        while not cond():
            if self.stop.is_set():
                return False
            time.sleep(0.0004)
        return True

    def run(self):
        comms = self.case / "comms"
        comms.mkdir(parents=True, exist_ok=True)
        lock = comms / L.LOCK
        if not self._wait(lock.exists):  # the structure's first answer (the start-up handshake)
            self.rc = -9
            return
        qa_prev = qa_prev2 = None
        if self.restart:
            d = json.loads((self.case / "processor0" / self.restart / "stub.json").read_text())
            qa_prev, qa_prev2 = np.array(d["qa_prev"]), np.array(d["qa_prev2"])
        md, ae = self.aero.md, self.aero
        next_write = self.t0 + self.write_every
        steady = np.zeros((md.n, 3))
        steady[:, 2] = self.steady
        k = 0
        while True:
            k += 1
            t = self.t0 + k * DT
            if t > self.t_end + 1e-9:
                break
            pos, ang = _parse_positions((comms / "positions.in").read_text())
            qa = ae.q_from(pos, ang)  # the wall state held during this step
            if qa_prev is None:
                qa_prev = qa
            d1 = (qa - qa_prev) / DT
            qd = d1 if qa_prev2 is None else 1.5 * d1 - 0.5 * (qa_prev - qa_prev2) / DT
            qm = qa + qd * DT / 2
            Qd = -ae.Ka @ qm - ae.Ca @ qd
            F = (ae.pinv @ Qd).reshape(-1, 3) + steady
            rows = lambda a: "".join("(%.12g %.12g %.12g)\n" % tuple(r) for r in a)  # noqa: E731
            (comms / "forces.out").write_text(
                f"time {t:.10g};\nprevTime {t - DT:.10g};\n\npoints\n{md.n}\n(\n{rows(md.x0)});\n\n"
                f"forces\n{md.n}\n(\n{rows(F)});\n\nmoments\n{md.n}\n(\n{rows(np.zeros_like(F))});\n")
            self.exchanges += 1
            if self.hang_after is not None and self.exchanges > self.hang_after:
                self.stop.wait()  # the exchange hangs: the lock is never removed, nothing more is written
                self.rc = -9
                return
            lock.unlink()
            if not self._wait(lock.exists):
                self.rc = -9
                return
            qa_prev2, qa_prev = qa_prev, qa
            if t >= next_write - 1e-9:
                next_write += self.write_every
                name = f"{t:.6g}"
                for p in range(self.n_procs):
                    (self.case / f"processor{p}" / name).mkdir(parents=True, exist_ok=True)
                (self.case / "processor0" / name / "stub.json").write_text(
                    json.dumps({"qa_prev": qa_prev.tolist(), "qa_prev2": qa_prev2.tolist()}))
        self.rc = 0


class FakeProc:
    def __init__(self, stub):
        self.stub = stub
        self.pid = -1

    def poll(self):
        return None if self.stub.is_alive() else (self.stub.rc if self.stub.rc is not None else 0)

    @property
    def returncode(self):
        return self.poll()


class FakeRunner:
    """Stands in for flutter_case.FoamRunner."""

    def __init__(self, case, aero, t_settle, write_every, hang_first_after=None, hangs=1):
        self.case, self.aero, self.t_settle, self.write_every = Path(case), aero, t_settle, write_every
        self.hang_first_after, self.hangs = hang_first_after, hangs
        self.starts = []   # names OpenFOAM was (re)started from
        self.stubs = []
        self.phase_a_calls = 0

    def run_phase_a(self, alive=lambda: True):
        self.phase_a_calls += 1
        name = f"{self.t_settle:.6g}"
        for p in range(2):
            (self.case / f"processor{p}" / name).mkdir(parents=True, exist_ok=True)
        return {"t": float(name), "name": name}

    def start_phase_b(self, name, t_end):
        self.starts.append(name)
        hang = self.hang_first_after if len(self.starts) <= self.hangs else None
        stub = StubFluid(self.case, self.aero, float(name), t_end, self.write_every,
                         restart=name if (self.case / "processor0" / name / "stub.json").exists() else None, hang_after=hang)
        self.stubs.append(stub)
        stub.start()
        return FakeProc(stub)

    def stop(self, proc):
        proc.stub.stop.set()
        proc.stub.join(5)

    def log_tail(self, n=800):
        return "stub log"


SETTINGS = dict(t_settle=0.05, t_ref=0.001, t_run=0.18, checkpoint_every_s=0.01, keep_checkpoints=2, poll=0.0004,
                perturbation=[0.2, 0.1], stall_timeout_s=2.0, stall_factor=20, startup_timeout_s=5.0, max_retries=2, n_procs=2)


def _speed(tmp_path, name="a", settings=None, **runner_kw):
    md = _model()
    aero = Aero(md)
    case, rdir = tmp_path / name / "case", tmp_path / name / "run"
    case.mkdir(parents=True)
    runner = FakeRunner(case, aero, 0.05, 0.01, **runner_kw)
    sr = FCASE.SpeedRun(case, md, {**SETTINGS, **(settings or {})}, None, rdir, runner=runner)
    return md, aero, sr, runner, rdir, case


def _check_poles(aero, res, tol_f=0.01):
    for f, z in aero.poles:
        near = [p for p in res["poles"] if abs(p["f_Hz"] - f) < tol_f * f]
        assert near, (f, res["poles"])
        p = near[0]
        sig = p.get("zeta_sigma") or 0.0
        assert abs(p["zeta"] - z) <= max(0.003, 2 * sig), (f, z, p)


# ------------------------------------------------------------------------------------------------ tests
@pytest.fixture(scope="module")
def reference(tmp_path_factory):
    """One complete uninterrupted run, shared by the tests that compare against it."""
    out = _speed(tmp_path_factory.mktemp("ref"))
    assert out[2].run_all() == "done"
    return out


def test_full_speed_run_recovers_the_known_eigenvalues(reference):
    md, aero, sr, runner, rdir, case = reference
    assert runner.phase_a_calls == 1 and runner.starts == ["0.05"] and sr.retries == 0
    t, q = sr.series()
    assert len(t) > 600 and abs(q).max() > 1e-4
    # the steady lift is the reference, not a motion
    assert abs(q.mean(0)).max() < 0.5 * abs(q).max()
    res = FR.default_analyse(t, q, md.omega / (2 * math.pi))  # (the estimator of flutter_damping when it exists)
    _check_poles(aero, res)
    # checkpoints were taken and old time directories pruned (the base and the last two checkpoints remain)
    ck = json.loads((rdir / "checkpoint.json").read_text())
    names = [n for n, _ in FCASE.written_times(case)]
    assert ck["state"]["released"] and "0.05" in names and len(names) <= 4 and ck["name"] in names
    assert sr.progress["checkpoint_t"] == float(ck["name"])
    assert json.loads((rdir / "speed.json").read_text())["state"] == "done"


def test_a_stalled_exchange_is_killed_and_the_run_resumes_from_the_checkpoint(tmp_path, reference):
    ref_rdir = reference[4]
    # the fluid stops answering after 350 exchanges (0.1375 s into the run) of its first life
    md, aero, sr, runner, rdir, case = _speed(tmp_path, "hang", hang_first_after=350, settings={"stall_timeout_s": 0.8})
    t0 = time.time()
    assert sr.run_all() == "done"
    assert time.time() - t0 < 60
    assert sr.retries == 1 and len(runner.starts) == 2 and runner.starts[0] == "0.05" and runner.starts[1] != "0.05"
    assert runner.stubs[0].rc == -9  # the first one was killed
    restart_t = float(runner.starts[1])
    assert 0.09 < restart_t < 0.15
    # the history is continuous (every exchange once, in order) and equals the uninterrupted run
    rows = FCASE.read_history_rows(rdir / "history.jsonl")
    ref_rows = FCASE.read_history_rows(ref_rdir / "history.jsonl")
    ts = np.array([r[0] for r in rows])
    assert np.allclose(np.diff(ts), DT, atol=1e-9) and len(rows) == len(ref_rows)
    for a, b in zip(rows, ref_rows):
        assert a[0] == pytest.approx(b[0], abs=1e-9) and a[3] == b[3]
        assert np.allclose(a[1], b[1], atol=1e-9, rtol=0) and np.allclose(a[2], b[2], atol=1e-9, rtol=0)
    t, q = sr.series()
    _check_poles(aero, FR.default_analyse(t, q, md.omega / (2 * math.pi)))


def test_after_too_many_stalls_the_speed_is_stalled_and_the_data_is_kept(tmp_path):
    md, aero, sr, runner, rdir, case = _speed(tmp_path, hang_first_after=150, hangs=99, settings={"max_retries": 1, "stall_timeout_s": 0.6})
    assert sr.run_all() == "stalled"
    assert sr.retries == 2 and len(runner.starts) == 2  # the first run and one resume
    assert (rdir / "checkpoint.json").exists() and len(FCASE.read_history_rows(rdir / "history.jsonl")) > 100
    assert json.loads((rdir / "speed.json").read_text())["state"] == "stalled"


def test_a_solver_that_exits_early_is_retried_then_failed(tmp_path):
    md, aero, sr, runner, rdir, case = _speed(tmp_path, settings={"max_retries": 1, "t_run": 0.18})
    orig = runner.start_phase_b

    def dying(name, t_end):  # the solver stops at 0.07 s although the end time is later
        return orig(name, min(t_end, 0.07))
    runner.start_phase_b = dying
    assert sr.run_all() == "failed" and "stub log" in sr.message


def test_the_time_limit_stops_cleanly_and_marks_the_run_partial(tmp_path):
    md, aero, sr, runner, rdir, case = _speed(tmp_path, settings={"speed_time_limit_s": 0.8})
    assert sr.run_all() == "partial"
    assert "time limit" in sr.message and len(sr.series()[0]) > 50
    assert runner.stubs[0].rc == -9 and not runner.stubs[0].is_alive()  # the fluid was stopped
    md, aero, sr2, runner2, rdir2, case2 = _speed(tmp_path, "dl", settings={"deadline_ts": time.time() + 0.8})
    assert sr2.run_all() == "partial" and "total" in sr2.message


def test_cancel_stops_the_run_and_a_new_run_resumes_it(tmp_path):
    md, aero, sr, runner, rdir, case = _speed(tmp_path, settings={"t_run": 0.1})
    assert sr.phase_a()
    th = threading.Thread(target=sr.phase_b)
    th.start()
    while sr.progress["exchanges"] < 120 and th.is_alive():
        time.sleep(0.01)
    sr.cancel()
    th.join(15)
    assert not th.is_alive() and sr.state == "cancelled" and not runner.stubs[0].is_alive()
    n_before = len(FCASE.read_history_rows(rdir / "history.jsonl"))
    assert n_before >= 120 and (rdir / "checkpoint.json").exists()
    # a new SpeedRun on the same folder (a restarted controller) goes on from the checkpoint and finishes
    sr2 = FCASE.SpeedRun(case, md, {**SETTINGS, "t_run": 0.1}, None, rdir, runner=runner)
    assert sr2.phase_a() and runner.phase_a_calls == 1  # phase A is not done again
    assert sr2.resume() == "done"
    ts = np.array([r[0] for r in FCASE.read_history_rows(rdir / "history.jsonl")])
    assert np.allclose(np.diff(ts), DT, atol=1e-9) and ts[-1] > 0.15


def test_prescribed_motion_run_through_the_speed_run(tmp_path):
    md, aero, sr, runner, rdir, case = _speed(tmp_path, settings={"prescribed": {"mode": 0, "amp": 1e-3, "f_hz": 25.0},
                                                                  "t_run": 0.08})
    assert sr.run_all() == "done"
    rows = [r for r in FCASE.read_history_rows(rdir / "history.jsonl") if r[3]]
    q0 = np.array([r[1][0] for r in rows])
    assert abs(q0).max() == pytest.approx(1e-3, rel=0.02) and abs(np.array([r[1][1] for r in rows])).max() == 0.0


# ------------------------------------------------------------------------------------------------ case preparation
def _fake_foam_case(path):
    for d in ("system", "0", "constant"):
        (path / d).mkdir(parents=True, exist_ok=True)
    (path / "0" / "pointDisplacement").write_text(
        "boundaryField\n{\n    \".*\"\n    {\n        type            fixedValue;\n        value           uniform (0 0 0);\n    }\n"
        "    wing\n    {\n        type            oscillatingDisplacement;\n        amplitude       (0 0 0);\n"
        "        omega           62.8;\n        value           uniform (0 0 0);\n    }\n}\n")
    (path / "system" / "controlDict").write_text(
        "application     pimpleFoam;\nstartFrom       latestTime;\nstartTime       0;\nendTime         0.3;\ndeltaT          1e-05;\n"
        "writeControl    runTime;\nwriteInterval   0.1;\nadjustTimeStep  no;\nmaxCo           1;\nmaxDeltaT       0.1;\n")
    (path / "system" / "fvSolution").write_text("PIMPLE\n{\n    nOuterCorrectors 1;\n}\n")
    (path / "system" / "decomposeParDict").write_text("numberOfSubdomains 1;\nmethod scotch;\n")
    (path / "constant" / "dynamicMeshDict").write_text(
        "updateControl    timeStep;\nupdateInterval   1;\ndynamicFvMesh   dynamicMotionSolverFvMesh;\n")
    return path


def test_prepare_coupled_case_edits_the_case_and_keeps_both_mesh_dicts(tmp_path):
    case = _fake_foam_case(tmp_path)
    md = _model()
    info = FCASE.prepare_coupled_case(case, md, {"n_procs": 6, "max_co": 6, "max_delta_t": 5e-4, "n_outer": 2, "t_settle": 0.05,
                                                  "t_ref": 0.004, "t_run": 0.3, "checkpoint_every_s": 0.01}, det=None)
    assert info["n_procs"] == 6 and info["t_release"] == pytest.approx(0.054) and info["t_end"] == pytest.approx(0.354)
    cd = (case / "system" / "controlDict").read_text()
    assert re.search(r"^maxCo\s+6;", cd, re.M) and re.search(r"^maxDeltaT\s+0.0005;", cd, re.M)
    assert re.search(r"^writeControl\s+adjustableRunTime;", cd, re.M) and re.search(r"^adjustTimeStep\s+yes;", cd, re.M)
    assert re.search(r"^endTime\s+0.05;", cd, re.M) and "lumpedPointMotion" in cd
    assert "nOuterCorrectors 2;" in (case / "system" / "fvSolution").read_text()
    dp = (case / "system" / "decomposeParDict").read_text()
    assert "numberOfSubdomains 6;" in dp and "method scotch;" in dp
    c = case / "constant"
    assert "staticFvMesh" in (c / "dynamicMeshDict").read_text()  # the active one is the uncoupled (phase A) version
    assert "staticFvMesh" in (c / "dynamicMeshDict.static").read_text()
    coupled = (c / "dynamicMeshDict.coupled").read_text()
    assert "dynamicMotionSolverFvMesh" in coupled and "updateControl    timeStep;" in coupled
    assert "lumpedPointDisplacement" in (case / "0" / "pointDisplacement").read_text()
    FCASE.use_dynamic_mesh(case, "coupled")
    assert (c / "dynamicMeshDict").read_text() == coupled
    # again: nothing is duplicated, the mesh dicts and controls are as after the first time
    FCASE.prepare_coupled_case(case, md, {"n_procs": 6, "t_settle": 0.05}, det=None)
    assert (c / "dynamicMeshDict.coupled").read_text() == coupled and "staticFvMesh" in (c / "dynamicMeshDict").read_text()
    assert (case / "system" / "controlDict").read_text().count("lumpedPointMotion") == 1
    FCASE.set_control(case, startFrom="startTime", endTime=0.123456789, writeInterval=0.01, newEntry="yes")
    cd = (case / "system" / "controlDict").read_text()
    assert re.search(r"^startFrom\s+startTime;", cd, re.M) and re.search(r"^endTime\s+0.123456789;", cd, re.M)
    assert re.search(r"^newEntry\s+yes;", cd, re.M) and cd.count("endTime") == 1


def test_time_directories_are_listed_pruned_and_phase_a_finished(tmp_path):
    for p in range(3):
        for n in ("0", "0.05", "0.06", "0.07", "1e-05"):
            (tmp_path / f"processor{p}" / n).mkdir(parents=True)
        (tmp_path / f"processor{p}" / "constant").mkdir()
        (tmp_path / f"processor{p}" / "0" / "pointDisplacement").write_text("x")
    assert FCASE.written_times(tmp_path) == [("1e-05", 1e-05), ("0.05", 0.05), ("0.06", 0.06), ("0.07", 0.07)]
    assert FCASE.time_dir_everywhere(tmp_path, "0.06") and not FCASE.time_dir_everywhere(tmp_path, "0.08")
    FCASE.remove_times(tmp_path, after=0.06)  # the half written later times go
    assert [n for n, _ in FCASE.written_times(tmp_path)] == ["1e-05", "0.05", "0.06"]
    FCASE.remove_times(tmp_path, keep={"0.05"}, before=0.06)
    assert [n for n, _ in FCASE.written_times(tmp_path)] == ["0.05", "0.06"]
    assert (tmp_path / "processor1" / "0").is_dir()  # time 0 is never touched
    res = FCASE.finish_phase_a(tmp_path)
    assert res == {"t": 0.06, "name": "0.06"}
    assert all((tmp_path / f"processor{p}" / "0.06" / "pointDisplacement").exists() for p in range(3))


def test_history_log_truncates_atomically_and_ignores_a_torn_last_line(tmp_path):
    h = FCASE.HistoryLog(tmp_path / "h.jsonl", flush_every=2)
    for k in range(5):
        h.append((k * 0.1, np.array([k, 2.0 * k]), np.array([0.5, 0.25]), k >= 2))
    h.close()
    with open(tmp_path / "h.jsonl", "a") as f:
        f.write('{"t": 0.5, "q": [1')  # (a kill in the middle of a write)
    rows = FCASE.read_history_rows(tmp_path / "h.jsonl")
    assert len(rows) == 5 and rows[3][3] is True and rows[1][3] is False
    kept = h.truncate(3)
    assert len(kept) == 3 and len(FCASE.read_history_rows(tmp_path / "h.jsonl")) == 3
    t, q = FCASE.read_series(tmp_path / "h.jsonl")
    assert len(t) == 1 and q.shape == (1, 2)


# ------------------------------------------------------------------------------------------------ the controller
def _manifest(tmp_path, speeds=(10.0, 20.0), t_run=0.06, **settings):
    md = _model()
    rdir = tmp_path / "ctl"
    FR.RunDir.create(rdir, md, speeds, {**SETTINGS, "t_run": t_run, "prepare": False, "slot_root": str(tmp_path / "slots"),
                                        **settings}, settings_hash="abc")
    for e in FR.RunDir(rdir).read_manifest()["speeds"]:
        (rdir / e["dir"] / "case").mkdir(parents=True)
    return md, rdir


def _controller(rdir, md, **kw):
    aero = Aero(md)
    made = []

    def factory(case, settings, det):
        r = FakeRunner(case, aero, 0.05, 0.01, **kw)
        r.settings = settings
        made.append(r)
        return r

    def analyse(t, q, hints):  # (the estimator itself is tested with the full run)
        return {"poles": [{"f_Hz": h, "zeta": 0.02, "zeta_sigma": 0.001, "spread": 0.1, "n_periods": 6.0, "ok": True,
                           "reasons": [], "hint_Hz": h} for h in hints], "critical": None, "method": "stub"}
    return FR.Controller(rdir, analyse_fn=analyse, runner_factory=factory, model=md, status_every=0.1), made, aero


def test_controller_runs_the_speeds_one_after_the_other_and_writes_status_and_results(tmp_path):
    md, rdir = _manifest(tmp_path)
    ctl, made, aero = _controller(rdir, md)
    seen = []
    done = threading.Event()

    def watch():
        while not done.is_set():
            st = FR.attach(rdir)
            if st["state"] != "none":
                seen.append(st)
            time.sleep(0.05)
    w = threading.Thread(target=watch)
    w.start()
    try:
        assert ctl.run() == 0
    finally:
        done.set()
        w.join()
    m = FR.RunDir(rdir).read_manifest()
    assert [s["state"] for s in m["speeds"]] == ["analysed", "analysed"] and m["wall_s"] > 0
    for s in m["speeds"]:
        r = s["result"]
        assert r["ok"] and not r["partial"] and r["n_exchanges"] > 150 and r["method"] == "stub"
        assert {"f_Hz", "zeta", "zeta_sigma", "spread", "n_periods", "ok", "reasons", "hint_Hz"} <= set(r["poles"][0])
        assert (rdir / r["history"]).exists()
    st = FR.attach(rdir)
    assert st["state"] == "done" and not st["alive"] and [s["state"] for s in st["speeds"]] == ["analysed"] * 2
    assert not (rdir / "pid").exists()
    # while it ran: heartbeat, simulated time, exchanges, speed index and an eta were reported
    running = [s for s in seen if s["state"] == "running" and s.get("t_sim")]
    assert running and any(s["alive"] for s in running)
    assert any(s["speed_index"] == 1 for s in running) and any(s["speed_index"] == 0 for s in running)
    assert any(s.get("exchanges") and s.get("eta_s") is not None for s in running)
    assert max(s["t_sim"] for s in running) > 0.08 and all(abs(time.time() - s["heartbeat"]) < 600 for s in running)


def test_controller_honours_the_cancel_flag_and_a_restart_resumes(tmp_path):
    md, rdir = _manifest(tmp_path, speeds=(10.0,), t_run=0.12)
    ctl, made, aero = _controller(rdir, md)
    th = threading.Thread(target=ctl.run)
    th.start()
    t0 = time.time()
    while time.time() - t0 < 30:
        st = FR.attach(rdir)
        if (st.get("exchanges") or 0) > 100:
            break
        time.sleep(0.02)
    FR.request_cancel(rdir)  # the server's call
    th.join(20)
    assert not th.is_alive()
    st = FR.attach(rdir)
    assert st["state"] == "cancelled" and not st["alive"]
    assert FR.RunDir(rdir).read_manifest()["speeds"][0]["state"] == "cancelled"
    assert not made[0].stubs[0].is_alive() and not (rdir / "pid").exists()
    # start again (the server removes the flag): the run is resumed from its checkpoint and finishes
    (rdir / "cancel.flag").unlink()
    ctl2, made2, _ = _controller(rdir, md)
    assert ctl2.run() == 0
    assert made2[0].phase_a_calls == 0 and made2[0].starts and made2[0].starts[0] != "0.05"  # (resumed, not restarted)
    assert FR.RunDir(rdir).read_manifest()["speeds"][0]["state"] == "analysed"
    assert FR.attach(rdir)["state"] == "done"


def test_controller_reports_a_stalled_speed_and_runs_speeds_in_parallel(tmp_path):
    md, rdir = _manifest(tmp_path, speeds=(10.0,), max_retries=0, stall_timeout_s=0.6)
    ctl, made, aero = _controller(rdir, md, hang_first_after=100)
    assert ctl.run() == 0
    m = FR.RunDir(rdir).read_manifest()
    assert m["speeds"][0]["state"] == "stalled" and not m["speeds"][0]["result"]["ok"] and m["settings_hash"] == "abc"
    assert FR.attach(rdir)["state"] == "stalled"
    # parallel_runs = 2: both speeds go at the same time, and the three-slot capacity holds them
    md, rdir2 = _manifest(tmp_path / "p", speeds=(10.0, 20.0), parallel_runs=2, cores=8)
    ctl2, made2, _ = _controller(rdir2, md)
    seen = set()
    th = threading.Thread(target=ctl2.run)
    th.start()
    while th.is_alive():
        seen.add(len(ctl2.active))
        time.sleep(0.01)
    assert 2 in seen
    assert [s["state"] for s in FR.RunDir(rdir2).read_manifest()["speeds"]] == ["analysed", "analysed"]
    assert all(r.settings["n_procs"] == 4 for r in made2)  # 8 cores over two runs


def test_a_second_controller_refuses_a_folder_that_is_running(tmp_path):
    md, rdir = _manifest(tmp_path, speeds=())
    rd = FR.RunDir(rdir)
    other = {"state": "running", "pid": 1, "heartbeat": time.time()}
    FCASE.write_json_atomic(rd.status_path, other)
    import os
    rd.pid_path.write_text(str(os.getppid()))  # a live process that is not us
    ctl, _, _ = _controller(rdir, md)
    assert ctl.run() == 3
    assert rd.attach()["alive"]
    rd.pid_path.write_text("999999999")
    assert not rd.attach()["alive"]
    FCASE.write_json_atomic(rd.status_path, {"state": "running", "pid": os.getpid(), "heartbeat": time.time() - 100})
    rd.pid_path.write_text(str(os.getpid()))
    assert not rd.attach()["alive"]  # (a heartbeat older than 30 s: the controller is stuck or gone)


def test_the_detached_entry_point_runs_without_the_server(tmp_path):
    """The real process: `python -m simwb.solvers.openfoam.flutter_run <run_dir>` with an empty manifest finishes at once."""
    md, rdir = _manifest(tmp_path, speeds=())
    rd = FR.RunDir(rdir)
    proc = rd.start_detached()
    t0 = time.time()
    while time.time() - t0 < 60 and (rd.attach()["state"] != "done" or proc.poll() is None):
        time.sleep(0.2)
    assert rd.attach()["state"] == "done" and proc.poll() == 0
    # a manifest that is missing: the controller reports it instead of dying silently
    (rdir / "manifest.json").unlink()
    rd.start_detached().wait(60)
    st = rd.attach()
    assert st["state"] == "failed" and "manifest" in st["message"]
