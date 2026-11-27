"""Issue 14: the steady-run stop rule ('Stop when forces settle') is a setting, 0 turns it off, and a tight value
keeps a run going that the default would have stopped."""
import importlib
import json
import math

import pytest

from simwb.solvers.openfoam.runner import monitor_outputs


def _log(force_of_it, n=1500):
    """A steady aero log: 'Time = i' and the 'Sum of forces' block of every iteration."""
    return "".join(f"Time = {i}\n\nforces forces write:\n    Sum of forces\n        Total    : ({force_of_it(i):.8f} 0 0)\n"
                   f"        Pressure : (0 0 0)\n        Viscous  : (0 0 0)\n" for i in range(1, n + 1))


META = {"study": "external_aero", "rotors": [], "ref_length": 1.0}


def _creep(i):                       # a force still creeping towards 1.0 with a time constant of 70 iterations
    return 1.0 + 0.5 * math.exp(-i / 70)


def test_default_rule_stops_a_creeping_force_tight_rule_does_not():
    txt = _log(_creep, 600)
    assert monitor_outputs(txt, META)["stable"] is True                              # 0.2 % of the load: stable
    assert monitor_outputs(txt, dict(META, auto_stop_tol_pct=0.2))["stable"] is True
    assert monitor_outputs(txt, dict(META, auto_stop_tol_pct=0.02))["stable"] is False


def test_zero_never_stops_early():
    mon = monitor_outputs(_log(lambda i: 1.0, 800), dict(META, auto_stop_tol_pct=0))
    assert mon["stable"] is False and mon["iteration"] == 800
    assert monitor_outputs(_log(lambda i: 1.0, 800), META)["stable"] is True        # the same flat log, default rule


def test_reason_names_the_tolerance_used():
    mon = monitor_outputs(_log(lambda i: 1.0, 800), dict(META, auto_stop_tol_pct=0.05))
    assert mon["stable"] and "0.05 % of the total load" in mon["reason"] and "friction force" in mon["reason"]


@pytest.fixture
def P(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMWB_HOME", str(tmp_path / "ws"))
    import simwb.project as P

    importlib.reload(P)
    return P


def test_setting_reaches_the_monitor_and_is_a_run_control(P, tmp_path):
    from simwb import studies

    names = [p["key"] if isinstance(p, dict) else p.key for p in studies.STUDIES["external_aero"]["settings"]]
    assert "auto_stop_tol" in names
    pr = P.create_project("Plate", "external_aero", str(tmp_path / "studies"))
    assert pr._stop_rule({})["auto_stop_tol_pct"] == 0.2                  # the default is unchanged
    pr.set_settings(auto_stop_tol=0)
    assert pr._stop_rule({"x": 1}) == {"x": 1, "auto_stop_tol_pct": 0.0}
    assert "auto_stop_tol" in P.RUN_CONTROLS                               # changing it does not lock an old run
    pr.case_dir.mkdir(parents=True)
    (pr.case_dir / "simwb_case.json").write_text(json.dumps({"app": "simpleFoam", "regions": [], "steps": []}))
    pr.data["solve"] = {"status": "done", "case_dir": str(pr.case_dir)}
    pr.data["case_setup"] = P.setup_snapshot(pr.data)
    pr.set_settings(auto_stop_tol=0.02)
    assert pr.setup_changes() == []


def test_note_of_a_run_with_the_rule_off_does_not_say_still_changing(P, tmp_path):
    log = tmp_path / "log.simpleFoam"
    log.write_text(_log(lambda i: 1.0, 600))
    off = P._convergence(log, 600, dict(META, auto_stop_tol_pct=0))
    assert off["converged"] is False and "does not stop early" in off["note"] and "still changing" not in off["note"]
    assert off["monitor"]["auto_stop_tol_pct"] == 0                       # (the Results step words its note from this)
    on = P._convergence(log, 600, dict(META, auto_stop_tol_pct=0.2))
    assert on["converged"] is True and "0.2 % of the total load" in on["note"] and "friction force" in on["note"]


def _log_split(total_of_it, visc_of_it, n=1500):
    return "".join(f"Time = {i}\n\nforces forces write:\n    Sum of forces\n        Total    : ({total_of_it(i):.8f} 0 0)\n"
                   f"        Pressure : ({total_of_it(i) - visc_of_it(i):.8f} 0 0)\n        Viscous  : ({visc_of_it(i):.8f} 0 0)\n"
                   for i in range(1, n + 1))


def test_a_drifting_friction_force_holds_the_run_back_even_when_the_total_is_steady():
    """Total steady, friction 1 % of it and decaying with a 600-iteration time constant: it still changes 1.4 % between the last
    two windows. The rule used to stop at 0.2 % and at 0.02 % (only 0 prevented it)."""
    visc = lambda i: 0.01 * (1.0 + 0.5 * math.exp(-i / 600))        # noqa: E731
    txt = _log_split(lambda i: 1.0 + 0.0 * i + (visc(i) - 0.01), visc, 600)   # total = pressure 0.99 + friction
    for tol in (0.2, 0.02):
        mon = monitor_outputs(txt, dict(META, auto_stop_tol_pct=tol))
        assert mon["stable"] is False and mon["dV_window_N"] > mon["tolerance_viscous_N"]
    settled = _log_split(lambda i: 1.0, lambda i: 0.01, 600)
    assert monitor_outputs(settled, META)["stable"] is True               # a steady friction force does not hold it back


def _first_stop(txt, n=1500, every=10):
    """The first iteration (from 500) at which the monitor says stable, scanning the log as the live monitor does; None = never."""
    for it in range(500, n + 1, every):
        part = txt if it >= n else txt[:txt.index(f"Time = {it + 1}\n")]
        if monitor_outputs(part, META)["stable"]:
            return it
    return None


@pytest.mark.parametrize("share,noise", [(0.08, 0.10), (0.8, 0.8)])
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_friction_noise_around_a_steady_mean_does_not_hold_the_run_back(share, noise, seed):
    """Per-iteration noise (10 % of an 8 % friction share, or 80 % of an 80 % share) averages out over 100 iterations: the run stops."""
    import numpy as np

    eps = np.random.default_rng(seed).normal(0, noise, 1600)
    txt = _log_split(lambda i: 1.0, lambda i: share * (1 + eps[i]), 1500)
    it = _first_stop(txt)
    assert it is not None and 500 <= it <= 700


def test_a_slow_oscillation_of_the_friction_force_holds_the_run_to_max_iterations():
    """The hold-back risk, as it is: friction swinging by 30 % with a period of 150 iterations (shedding-like) never settles, so the
    run goes to Max iterations although the total force is steady. A small swing (5 %) does not hold it."""
    swing = lambda amp: _log_split(lambda i: 1.0, lambda i: 0.08 * (1 + amp * math.sin(2 * math.pi * i / 150)), 1500)   # noqa: E731
    assert _first_stop(swing(0.30)) is None
    assert _first_stop(swing(0.05)) is not None


def test_missing_viscous_lines_are_said_not_silently_skipped():
    txt = "".join(f"Time = {i}{chr(10)}{chr(10)}forces forces write:{chr(10)}    Sum of forces{chr(10)}        Total    : (1.0 0 0){chr(10)}"
                  for i in range(1, 801))                                   # a log without Pressure / Viscous lines
    mon = monitor_outputs(txt, META)
    assert mon["stable"] and mon["viscous_check"].startswith("skipped") and "friction force was not checked" in mon["reason"]
