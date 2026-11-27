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
    assert mon["stable"] and "< 0.05 % of the total load" in mon["reason"]


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
    assert on["converged"] is True and "0.2 % of the total load" in on["note"]


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
