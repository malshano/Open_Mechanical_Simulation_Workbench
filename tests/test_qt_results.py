"""The Results step of the Qt window: Summary / Display / Probe / Checks, the parts bars, cautions, export, deformation, labels."""
import importlib
from pathlib import Path

import numpy as np
import pytest
from qt_support import EXAMPLES, ctl, from_thread  # noqa: F401
from test_qt_theme import best_contrast, visible_controls  # noqa: F401


def _wait(job, timeout=900):
    assert job.wait(timeout) and job.status == "done", (job.error, job.log[-5:])
    return job.result


@pytest.fixture(scope="session")
def solved(tmp_path_factory):
    """Three solved studies (static bracket, heated plate, bracket modes) built once and saved as single files."""
    home = tmp_path_factory.mktemp("results_ws")
    mp = pytest.MonkeyPatch()
    mp.setenv("SIMWB_HOME", str(home / "ws"))
    import simwb.project as P

    importlib.reload(P)
    out = {}
    # heat
    pr = P.create_project("Plate heat", "heat_conduction")
    _wait(pr.import_step(EXAMPLES / "heated_plate.step"))
    plate, block = sorted(pr.bodies(), key=lambda b: -b["volume"])
    pr.set_body(plate["id"], material="aluminium_6061", name="Plate")
    pr.set_body(block["id"], material="ceramic_alumina", heat_W=10, name="Resistor")
    pr.add_bc("Cold plate", "fixed_temperature", [pr.find_faces(normal="-z", at="min_z")[0]["id"]], {"T": 30})
    pr.set_mesh(preset="coarse")
    _wait(pr.generate_mesh())
    _wait(pr.solve())
    out["heat"] = str(P.export_project(pr.id, str(home / "heat.zip")))
    # static
    pr = P.create_project("Bracket static", "static_structural")
    _wait(pr.import_step(EXAMPLES / "camera_mount.step"))
    holes = [f["id"] for f in pr.faces() if f["surface_type"] == "Cylinder"]
    front = [f["id"] for f in pr.faces() if abs(f["normal"][0]) > 0.9 and abs(f["centroid"][0] - 3e-3) < 1e-4]
    pr.add_bc("Bolts", "fixed_support", holes)
    pr.add_bc("Camera", "force", front, {"fx_N": -20.0, "fy_N": 0, "fz_N": 0})
    pr.set_settings(gravity="-z")
    pr.set_mesh(preset="coarse")
    _wait(pr.generate_mesh())
    _wait(pr.solve())
    out["static"] = str(P.export_project(pr.id, str(home / "static.zip")))
    # modal
    pr = P.create_project("Bracket modal", "vibration_modal")
    _wait(pr.import_step(EXAMPLES / "camera_mount.step"))
    pr.add_bc("Bolts", "fixed_support", holes)
    pr.add_bc("Camera", "added_mass", front, {"mass_kg": 0.06})
    pr.set_settings(excitation_min_hz=100, excitation_max_hz=400, n_modes=6)
    pr.set_mesh(preset="coarse")
    _wait(pr.generate_mesh())
    _wait(pr.solve())
    out["modal"] = str(P.export_project(pr.id, str(home / "modal.zip")))
    mp.undo()
    return out


def open_results(ctl, solved, kind):
    ctl.do("study.import", {"path": solved[kind]})
    ctl.do("step.go", {"step": "results"})
    return ctl


# ------------------------------------------------------------------------------------------------------------ Summary
def test_the_static_summary_has_the_numbers_the_bars_and_the_cautions(ctl, solved):
    c = open_results(ctl, solved, "static")
    kp = {w["id"]: w["text"] for w in c.widgets("results.kpi.")["widgets"]}
    sm = c.engine.state()["solve"]["summary"]
    assert f"{sm['max_von_mises_MPa']:.1f} MPa" in kp["results.kpi.max_von_mises_stress"]
    assert f"{sm['max_displacement_mm']:.4f}" in kp["results.kpi.max_deformation"]
    assert f"{sm['min_safety_factor']:.2f}" in kp["results.kpi.safety_factor__yield"] and "margin" in kp["results.kpi.safety_factor__yield"]   # a word, not only a colour
    assert f"{sm['mass_kg'] * 1000:.1f} g" in kp["results.kpi.mass"]
    bars = c.win.panels["results"].findChildren(__import__("simwb.qt.charts", fromlist=["HBar"]).HBar)
    assert len(bars) == 1 and "Camera bracket" in bars[0].label and "SF " in bars[0].value_text
    txt = " ".join(w.text() for w in c.win.panels["results"].pages["summary"].parentWidget().findChildren(__import__("PySide6.QtWidgets", fromlist=["QLabel"]).QLabel))
    assert "Read before you rely" in txt and "MPa" in txt                              # the accuracy caution is shown
    assert "unknowns" in str(c.widget_do("results.details")["value"])
    assert {"results.export.report", "results.export.csv", "results.export.vtk"} <= {w["id"] for w in c.widgets("results.export")["widgets"]}


def test_show_on_model_switches_the_field(ctl, solved):
    c = open_results(ctl, solved, "static")
    links = [w for w in c.widgets("results.show.")["widgets"]]
    assert len(links) >= 3
    c.widget_do("results.show.1", "click")                                             # "Max deformation: show on model"
    assert c.widget_do("results.field")["value"]["text"] == "Total deformation"
    assert c.state()["view"]["overlay"]["name"] == "Total deformation"


def test_the_thermal_summary(ctl, solved):
    c = open_results(ctl, solved, "heat")
    kp = {w["id"]: w["text"] for w in c.widgets("results.kpi.")["widgets"]}
    sm = c.engine.state()["solve"]["summary"]
    assert "Max temperature" in kp["results.kpi.max_temperature"] and f"{sm['T_max']:.1f}" in kp["results.kpi.max_temperature"]
    assert any(("heat balance closes" in t or "% off" in t) for t in kp.values())   # the verdict in words
    bars = c.win.panels["results"].findChildren(__import__("simwb.qt.charts", fromlist=["HBar"]).HBar)
    assert sorted(b.label for b in bars) == ["Plate", "Resistor"]


def test_the_modal_summary_lists_the_modes(ctl, solved):
    c = open_results(ctl, solved, "modal")
    kp = {w["id"]: w["text"] for w in c.widgets("results.kpi.")["widgets"]}
    assert "First natural frequency" in kp["results.kpi.first_natural_frequency"] and "Hz" in kp["results.kpi.first_natural_frequency"]
    modes = c.widgets("results.mode.")["widgets"]
    assert len(modes) >= 4 and modes[0]["text"].startswith("Mode 1")
    c.widget_do(modes[1]["id"], "click")
    assert c.widget_do("results.field")["value"]["text"] == "Mode 2"


# ------------------------------------------------------------------------------------------------------------ the tabs and Display
def test_the_tabs_show_their_pages(ctl, solved):
    c = open_results(ctl, solved, "static")
    stack = c.win.panels["results"].stack
    assert stack.currentIndex() == 0 and c.widget_do("results.tab.summary")["value"] == {"checked": True}
    for i, name in enumerate(("summary", "display", "probe", "checks")):
        c.do("results.tab", {"name": name})
        assert stack.currentIndex() == i
    assert c.widget_do("results.field")["visible"] is False                           # (on the Display tab, not shown now)
    c.do("results.tab", {"name": "display"})
    assert c.widget_do("results.field")["visible"] is True


def test_deformation_modes_range_and_labels(ctl, solved):
    c = open_results(ctl, solved, "static")
    ov = lambda: c.state()["view"]["overlay"]  # noqa: E731
    assert ov()["deform"] and ov()["deform"]["scale"] > 10 and ov()["animated"] is False        # auto: exaggerated so it shows
    c.do("results.show", {"scale": 1})
    assert ov()["deform"]["scale"] == 1.0                                                          # true size
    c.do("results.show", {"scale": 0, "animate": True})
    assert ov()["animated"] is True and c.win.view._anim["kind"] == "static"
    c.do("results.show", {"animate": False, "deform": False})
    assert ov()["deform"] is None and ov()["animated"] is False
    c.do("results.show", {"deform": True})
    # a fixed colour range, then automatic again
    c.do("results.show", {"range": [0, 100]})
    assert ov()["range"] == [0.0, 100.0]
    c.do("results.show", {"range": []})                                                            # [] = automatic again
    vmax = c.engine.state()["solve"]["summary"]["max_von_mises_MPa"]
    top = c.win.view.extrema()["max"]["value"]
    assert ov()["range"][1] == pytest.approx(top, rel=1e-6)                                        # the legend ends where the drawn surface does
    assert 0.7 * vmax <= top <= vmax * (1 + 1e-6)                                                  # (the solver's maximum is over the whole volume)
    # the highest and lowest values are labelled on the model
    labels = [la.GetInput() for la in c.win.view.label_actors]
    assert any(t.startswith("Max ") for t in labels) and any(t.startswith("Min") for t in labels)
    c.do("results.show", {"minmax": False})
    assert not c.win.view.label_actors


# ------------------------------------------------------------------------------------------------------------ Probe
def test_pinned_values_and_the_line_plot(ctl, solved):
    c = open_results(ctl, solved, "static")
    ex = c.win.view.extrema()
    vmax = c.engine.state()["solve"]["summary"]["max_von_mises_MPa"]
    assert 0.7 * vmax <= ex["max"]["value"] <= vmax * (1 + 1e-6) and ex["min"]["value"] < 0.1      # (surface maximum <= the volume's)
    p = ex["max"]["point"]
    pin = c.do("results.pin", {"x": p[0], "y": p[1], "z": p[2]})["result"]["pin"]
    assert pin["value"] > 0.7 * ex["max"]["value"] and pin["unit"] == "MPa"
    assert any(la.GetInput().startswith("A: ") for la in c.win.view.label_actors)
    c.do("results.tab", {"name": "probe"})
    assert c.widget_do("results.pin.0.delete")["visible"] is True
    d = c.do("results.line_minmax")["result"]["line"]
    assert d["max"] > d["min"] and d["n_inside"] > 10 and d["length"] > 0.01
    chart = c.win.panels["results"].chart
    assert len(chart.x) == 160 and np.isfinite(chart.y).sum() > 10
    assert chart.value_at(float(chart.x[40])) is not None
    assert "min" in c.widget_do("results.line.info")["value"]
    text = c.win.panels["results"].copy_line()
    assert text.startswith("distance_m\tvalue") and len(text.splitlines()) == 161
    assert [la.GetInput() for la in c.win.view.label_actors if la.GetInput() in ("A", "B")] == ["A", "B"]
    c.widget_do("results.pin.0.delete", "click")
    assert c.win.panels["results"].pins == []
    c.widget_do("results.line.clear", "click")
    assert c.win.panels["results"].line_data is None and len(chart.x) == 0


def test_clicking_the_model_with_probe_on_pins_a_value(ctl, solved):
    c = open_results(ctl, solved, "static")
    c.do("results.tab", {"name": "probe"})
    c.widget_do("results.probe", "click")
    assert c.state()["view"]["pick_mode"] == "point"
    ex = c.win.view.extrema()["max"]["point"]
    h = c.do("view.pick_ray", {"p0": [ex[0], ex[1], ex[2] + 1], "p1": [ex[0], ex[1], ex[2] - 1]})["result"]["hit"]
    assert h["kind"] == "surface"
    c.win.view.last_hit = h
    c.win.view.pointPicked.emit(*h["point"])                                            # as a click on the model would
    assert len(c.win.panels["results"].pins) == 1


# ------------------------------------------------------------------------------------------------------------ Checks and export
def test_checks_list_warnings_and_assumptions(ctl, solved):
    c = open_results(ctl, solved, "static")
    c.do("results.tab", {"name": "checks"})
    p = c.win.panels["results"]
    from PySide6.QtWidgets import QLabel
    txt = " ".join(w.text() for w in p.pages["checks"].parentWidget().findChildren(QLabel))
    assert "Unaveraged peak" in txt and "Linear elastic" in txt and "Mesh sensitivity" in txt.upper().title() or "MESH SENSITIVITY" in txt.upper()
    assert c.widget_do("results.meshcheck.run")["enabled"] is True


def test_export_writes_the_three_files(ctl, solved, tmp_path):
    c = open_results(ctl, solved, "static")
    for key, name in (("csv", "r.csv"), ("vtk", "r.vtu"), ("report", "r.html")):
        out = tmp_path / name
        c.do("results.export", {"format": key, "path": str(out)})
        assert out.exists() and out.stat().st_size > 200, key
    assert "<html" in (tmp_path / "r.html").read_text(encoding="utf-8").lower()
    assert "<VTKFile" in (tmp_path / "r.vtu").read_text(errors="ignore")[:400]
    with pytest.raises(ValueError, match="results.export"):
        c.widget_do("results.export.csv", "click")                                      # a file dialog: the action takes the path
    with pytest.raises(ValueError, match="format must be"):
        c.do("results.export", {"format": "pdf", "path": str(tmp_path / "x.pdf")})


# ------------------------------------------------------------------------------------------------------------ both themes
@pytest.mark.parametrize("name", ("dark", "light"))
def test_every_tab_is_legible_with_real_results(ctl, solved, name):
    c = open_results(ctl, solved, "static")
    c.do("theme.set", {"mode": name})
    weak, seen = [], 0
    for tab in ("summary", "display", "probe", "checks"):
        c.do("results.tab", {"name": tab})
        c._settle()
        for i, o in visible_controls(c):
            seen += 1
            bg, fg, r = best_contrast(o)
            if fg is None or r < 3.0:
                weak.append((tab, i, round(r, 2)))
    assert seen > 40 and not weak, weak[:6]
    cov = c.coverage()
    assert cov["complete"], (cov["unnamed_widgets"][:3], cov["unlabelled"][:3], cov["no_pointer_cursor"][:3])
