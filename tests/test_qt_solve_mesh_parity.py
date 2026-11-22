"""The Mesh and Solve steps of the Qt window against the web version: every control of those steps has a button and does what the web
version's does (the request the panel builds and the engine call it makes). No real OpenFOAM run: flow studies are set up by hand and the
engine's solve / install calls are recorded."""
import threading
import zipfile

import pytest
from qt_support import ctl, from_thread  # noqa: F401
from test_qt_results import solved  # noqa: F401
from test_qt_theme import best_contrast, visible_controls  # noqa: F401

from simwb.qt import jobs as J
from simwb.qt import meshtools as MT

FOAM = {"available": True, "backend": "windows", "parallel": True, "cores": 4, "detail": "test build"}


def ids(c, prefix):
    return {w["id"]: w for w in c.widgets(prefix)["widgets"]}


# ------------------------------------------------------------------------------------------------------------ Mesh
def open_static(c, solved):
    c.do("study.import", {"path": solved["static"]})
    c.do("step.go", {"step": "mesh"})
    return c


def test_statistics_table_verdict_notes_and_cell_budget(ctl, solved):
    c = open_static(ctl, solved)
    table = c.widget_do("mesh.stats.table")["text"]
    assert "Cells:" in table and "Min / mean quality" in table and "Poor cells (<0.1)" in table and "Time:" in table
    s = c.engine.state()["mesh"]["stats"]
    assert f"{s['cells']:,}" in c.widget_do("mesh.stat.cells")["value"]
    notes = c.widget_do("mesh.verdict.notes")["value"]
    assert "Thinnest solid feature" in notes                                             # the web version's verdict, in words
    budget = c.widget_do("mesh.budget")["text"]
    assert " mm: " in budget and "% cells" in budget                                     # where the cells are spent
    assert c.widget_do("mesh.next")["enabled"] is True
    c.widget_do("mesh.next", "click")
    assert c.state()["window"]["step"] == "solve"


@pytest.mark.parametrize("metric,label", [("aspect", "Aspect ratio"), ("growth", "Size jump to neighbours"), ("volume", "Cell volume")])
def test_colour_the_mesh_by_the_new_metrics(ctl, solved, metric, label):
    c = open_static(ctl, solved)
    r = c.do("mesh.view", {"color": metric})
    assert r["overlay"]["name"] == label and r["new_log"] == []
    assert c.widget_do(f"mesh.color.{metric}")["value"] == {"checked": True}
    assert c.widget_do("mesh.color.quality")["value"] == {"checked": False}             # (one group across both rows)


def test_a_hex_mesh_offers_only_quality_and_region():
    hexs = {"study": "external_aero", "settings": {}, "geometry": {}, "mesh": {"layers": {"n": 3}, "stats": {
        "method": "hex", "cells": 1000, "nodes": 900, "quality": {"min": 0.4, "mean": 0.8, "n_poor": 0},
        "foam_quality": {"non_orthogonality_max": 50, "skewness_max": 1.2}, "cell_types": {"hexahedra": 800, "prisms": 100, "polyhedra": 100},
        "hex": {"surface_cell_mm": 2, "base_mm": 16, "levels": 3, "aspect_ratio_max": 7.5, "layers_extruded_percent": 92, "problems": [], "warnings": []},
        "seconds": 12}}}
    rows = dict(MT.stats_rows(hexs))
    assert rows["Hexahedra"] == "80 %" and rows["Max skewness"] == "1.20" and rows["Wall faces with layers"] == "92.0 %"
    tone, warn, info = MT.mesh_notes(hexs)
    assert tone == "ok" and not warn and any("92.0 % of the wall faces" in x for x in info)


def test_region_fit_and_cell_estimate(ctl, solved):
    c = open_static(ctl, solved)
    c.do("mesh.region_add", {"kind": "box"})
    assert "cells in this box" in c.widget_do("mesh.region.0.cells")["value"]
    body = c.engine.state()["body_list"][0]
    r = c.do("mesh.region_fit", {"index": 0, "part": body["name"]})["result"]
    bb = body["bbox"]
    assert all(r["min"][k] < bb[k] and r["max"][k] > bb[k + 3] for k in range(3))   # round the part, clear of it


def test_wake_and_tip_vortex_regions_follow_the_rotating_zones(ctl):
    c = ctl
    c.do("study.new", {"name": "Prop", "study": "external_aero"})
    c.do("step.go", {"step": "mesh"})
    if not c.widget_do("mesh.regions.toggle")["value"]["checked"]:
        c.widget_do("mesh.regions.toggle", "click")
    w = c.widget_do("mesh.region.add_wake")
    assert w["enabled"] is False and "rotating" in c._index()["mesh.region.add_wake"].toolTip()   # why not, and what to do
    pr = c.engine.project()
    pr.data["moving_zones"] = [{"name": "prop", "center": [0, 0, 0], "axis": "+x", "radius": 0.1, "length": 0.02, "rpm": 3000, "method": "mrf"}]
    pr.save()
    c.engine.changed.emit()
    c._settle()
    c.do("mesh.region_add", {"kind": "wake"})
    c.do("mesh.region_add", {"kind": "tip_rings"})
    c.do("mesh.region_add", {"kind": "wake"})                                           # (again: replaces, does not pile up)
    regs = {r["name"]: r for r in c.engine.state()["mesh"]["regions"]}
    assert set(regs) == {"prop wake", "prop tip vortices"}
    fd = c.engine.state()["flow_dir"]
    wake = regs["prop wake"]
    assert wake["shape"] == "cylinder" and wake["end"] == pytest.approx([fd[k] * 0.4 for k in range(3)])
    ring = regs["prop tip vortices"]
    assert ring["inner_radius"] == pytest.approx(0.065) and ring["size"] == pytest.approx(0.002)


def test_mesh_sensitivity_and_three_level_check(ctl, solved, monkeypatch):
    import simwb.project as P
    c = open_static(ctl, solved)
    calls = []
    monkeypatch.setattr(P.Project, "mesh_check", lambda self, levels=1: calls.append(levels))
    assert c.widget_do("mesh.check.run")["enabled"] is True
    shown = ids(c, "mesh.check")
    assert c.engine.state()["mesh"]["preset"] in ("quick", "coarse", "medium") and shown["mesh.check.three"]["visible"]
    c.do("mesh.check", {"levels": 1})
    c.do("mesh.check", {"levels": 2})
    assert calls == [1, 2]


def test_mesh_check_table_reads_like_the_web_version():
    mc = {"rows": [{"name": "Max stress", "coarse": 10.0, "fine": 10.5, "fine2": 10.6, "unit": "MPa", "change_percent": 5.0,
                    "gci_percent": 1.25, "order": 1.8}]}
    (name, text), = MT.check_rows(mc)
    assert name == "Max stress" and "10 → 10.5 MPa (5 %)" in text and "GCI 1.2 %, order 1.8" in text and "→ 10.6" in text


# ------------------------------------------------------------------------------------------------------------ Solve
@pytest.fixture
def flow(ctl, monkeypatch):
    """An external-flow study with a (pretend) mesh, the setup complete, OpenFOAM found; the engine's solve calls are recorded."""
    import simwb.project as P
    calls = []
    monkeypatch.setattr(J, "foam_status", lambda refresh=False: dict(FOAM))
    monkeypatch.setattr(P.Project, "check", lambda self: [])
    monkeypatch.setattr(P.Project, "solve", lambda self, **kw: calls.append(kw))
    ctl.do("study.new", {"name": "Wing", "study": "external_aero"})
    ctl.calls = calls

    def setup(solve=None, settings=None):
        pr = ctl.engine.project()
        pr.data["mesh"].update(status="ready", preset="coarse", stats={
            "cells": 200000, "nodes": 40000, "quality": {"min": 0.3, "mean": 0.8, "n_poor": 0, "histogram": [0] * 20},
            "sizes": {"near_wall": 0.001, "body": 0.01}, "seconds": 5})
        pr.data["settings"].update(settings or {})
        pr.data["solve"].update(solve or {})
        pr.save()
        ctl.engine.changed.emit()
        ctl.do("step.go", {"step": "solve"})
        ctl._settle()
        return pr
    ctl.setup = setup
    return ctl


def test_cores_iterations_run_time_and_start_from(flow):
    c = flow
    c.engine.new_study("Earlier wing", "external_aero")                                  # (another study of the same kind to start from)
    other = c.engine.pid
    c.do("study.new", {"name": "Wing 2", "study": "external_aero"})
    c.setup()
    items = c.widget_do("solve.cores")["value"]["items"]
    assert items[0] == "Auto (4)" and items[1:] == ["1", "2", "3", "4"]                 # 200,000 cells / 20,000 per core, at most 4 cores
    r = c.do("solve.options", {"cores": 2, "iterations": 800, "init_from": other})["result"]
    assert r["cores"] == 2 and r["iterations"] == 800 and r["init_from"] == other
    assert "Expected run time" in c.widget_do("solve.eta")["value"]
    c.widget_do("solve.run", "click")
    assert c.calls[-1] == {"init_from": other}                                           # the run starts from the other study's solution


def test_no_openfoam_offers_install_recheck_and_writes_the_case(flow, monkeypatch):
    c = flow
    seen, installs = [], []
    monkeypatch.setattr(J, "foam_status", lambda refresh=False: (seen.append(refresh), {"available": False, "installable": True,
                                                                                        "detail": "", "help": "Other ways: WSL"})[1])
    monkeypatch.setattr(J, "install", lambda name: installs.append(name))
    c.win.panels["solve"].foam = None
    c.setup()
    assert "not found" in c.widget_do("solve.foam.status")["value"]
    assert c.widget_do("solve.install.openfoam")["visible"] and c.widget_do("solve.recheck")["visible"]
    assert c.widget_do("solve.run")["text"].startswith("Write OpenFOAM case")
    c.do("software.install", {"recheck": True})
    assert True in seen
    c.do("software.install", {"name": "openfoam"})
    assert installs == ["openfoam"]
    c.widget_do("solve.run", "click")
    assert c.calls[-1] == {"export_only": True}


def test_continue_extend_and_resume(flow, tmp_path):
    c = flow
    pr = c.setup(solve={"status": "done", "case_dir": "case"}, settings={"analysis": "steady"})
    assert c.widget_do("solve.continue")["visible"]
    c.do("solve.continue", {"iterations": 500})
    assert c.calls[-1] == {"continue_iterations": 500}
    c.setup(solve={"status": "done", "case_dir": "case"}, settings={"analysis": "transient", "end_time": 0.2, "n_frames": 21})
    assert c.widget_do("solve.extend")["visible"] and not c.widget_do("solve.continue")["visible"]
    assert float(c.widget_do("solve.extend.end")["value"]) == pytest.approx(0.4)        # (no rotating zone: twice the end time)
    c.do("solve.continue", {"end_time": 0.5, "write_interval": 0.05})
    assert c.calls[-1] == {"end_time": 0.5, "write_interval": 0.05}
    c.setup(solve={"status": "interrupted", "case_dir": "case"}, settings={"analysis": "transient"})
    assert c.widget_do("solve.resume")["visible"] and "stopped" in c.widget_do("solve.note")["value"]
    c.do("solve.continue", {})
    assert c.calls[-1] == {"continue_iterations": 1}
    assert pr.id == c.engine.pid


def test_stop_and_cancel_while_running(flow):
    import simwb.project as P
    c = flow
    c.setup()
    gate = threading.Event()

    def work(job):
        while not gate.is_set():
            if job.cancel_requested:
                from simwb.jobs import Cancelled
                raise Cancelled()
            gate.wait(0.05)
    job = P.JOBS.start("solve", c.engine.pid, work)
    c.engine.changed.emit()
    c._settle()
    assert c.widget_do("solve.stop")["visible"] and c.widget_do("solve.cancel")["visible"]
    assert c.widget_do("solve.run")["enabled"] is False
    c.do("solve.cancel")
    assert job.cancel_requested
    gate.set()
    job._done.wait(5)


def test_advanced_numerics_reset_and_case_files(flow, monkeypatch):
    import simwb.project as P
    c = flow
    c.setup()

    def preview(self):
        f = self.preview_dir / "system" / "fvSchemes"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("ddtSchemes { default steadyState; }\n")
        return self.case_files()
    monkeypatch.setattr(P.Project, "foam_preview", preview)
    r = c.do("solve.advanced", {"values": {"convection": "first order", "relax_p": "0.2"}})["result"]
    assert r["foam_advanced"] == {"convection": "first order", "relax_p": 0.2}
    assert "2 settings changed" in c._index()["solve.advanced.toggle"].text()
    assert c.do("solve.advanced", {"reset": True})["result"]["foam_advanced"] == {}
    assert c.do("solve.case_files")["result"]["files"] == ["system/fvSchemes"]
    assert c.widget_do("solve.casefile.system_fvSchemes")["visible"]
    d = c.do("solve.case_file", {"dictionary": "system/fvSchemes", "text": "ddtSchemes { default Euler; }\n"})["result"]
    assert d["overridden"] and "Euler" in d["text"]
    assert c.widget_do("solve.casefile.revert")["enabled"] is True
    d = c.do("solve.case_file", {"dictionary": "system/fvSchemes", "revert": True})["result"]
    assert not d["overridden"] and "steadyState" in d["text"]


def test_save_the_case_as_a_zip(flow, tmp_path):
    c = flow
    pr = c.setup(solve={"status": "done", "case_dir": "case"})
    (pr.case_dir / "system").mkdir(parents=True, exist_ok=True)
    (pr.case_dir / "system" / "controlDict").write_text("x")
    c.engine.changed.emit()
    c._settle()
    assert c.widget_do("solve.case_zip")["enabled"] is True
    out = tmp_path / "case.zip"
    c.do("solve.case_zip", {"path": str(out)})
    assert "system/controlDict" in zipfile.ZipFile(out).namelist()


@pytest.mark.parametrize("name", ("dark", "light"))
def test_the_flow_solve_step_is_legible(flow, name):
    c = flow
    c.do("theme.set", {"mode": name})
    c.setup(solve={"status": "done", "case_dir": "case"})
    if not c.widget_do("solve.advanced.toggle")["value"]["checked"]:
        c.widget_do("solve.advanced.toggle", "click")
    c._settle()
    weak, seen = [], 0
    for i, o in visible_controls(c):
        seen += 1
        bg, fg, r = best_contrast(o)
        if fg is None or r < 3.0:
            weak.append((i, round(r, 2)))
    assert seen > 15 and not weak, weak[:6]
    cov = c.coverage()
    assert cov["complete"], (cov["unnamed_widgets"][:3], cov["unlabelled"][:3], cov["no_pointer_cursor"][:3])
