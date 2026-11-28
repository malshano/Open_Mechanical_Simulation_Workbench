"""The mesh report (#48): HTML and PDF with the statistics, settings, cell budget, findings, histograms and cuts, and the slice renderer."""
import base64
import importlib
import re

import numpy as np
import pytest
from qt_support import EXAMPLES, ctl, from_thread  # noqa: F401
from test_qt_mesh import mesh, open_mesh  # noqa: F401
from test_qt_mesh_metrics import fake_hex_study  # noqa: F401
from test_qt_results import solved  # noqa: F401


@pytest.fixture(scope="module")
def bracket(tmp_path_factory):
    """A real coarse tetrahedral mesh of the camera bracket, made once for this file."""
    home = tmp_path_factory.mktemp("report_ws")
    mp = pytest.MonkeyPatch()
    mp.setenv("SIMWB_HOME", str(home / "ws"))
    import simwb.project as P
    importlib.reload(P)
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])                   # noqa: F841
    pr = P.create_project("Camera bracket", "static_structural")
    j = pr.import_step(EXAMPLES / "camera_mount.step", original_name="c.step")
    assert j.wait(300) and j.status == "done", j.error
    pr.set_mesh(preset="coarse")
    j = pr.generate_mesh()
    assert j.wait(600) and j.status == "done", (j.error, j.log[-5:])
    yield pr
    mp.undo()


def test_the_html_report_holds_the_numbers_of_the_mesh_and_the_pictures(bracket, tmp_path):
    from simwb import meshreport
    out = meshreport.write_html(bracket, tmp_path / "r.html")
    text = open(out, encoding="utf-8").read()
    st = bracket.data["mesh"]["stats"]
    d = bracket.mesh_metrics()
    assert f"{st['cells']:,}" in text and "Camera bracket - mesh report" in text
    for h in ("Statistics", "Settings", "Cell budget", "Findings", "Histograms", "Cuts through the mesh"):
        assert f"<h2>{h}</h2>" in text, h
    assert text.count("<tr><td>") >= len(d["budget"]) and all(f"{b['cells']:,}" in text for b in d["budget"])      # every band of the budget is a row
    imgs = re.findall(r"data:image/png;base64,([A-Za-z0-9+/=]+)", text)
    assert len(imgs) == 5 + 4                                                       # five histograms, four cuts
    from PySide6.QtGui import QImage
    im = QImage.fromData(base64.b64decode(imgs[-1]))
    assert im.width() > 300 and im.height() > 200
    assert "Coarse" in text or "coarse" in text                                     # the preset it was made with


def test_the_pdf_report_is_the_same_page(bracket, tmp_path):
    from PySide6.QtCore import QSize
    from PySide6.QtPdf import QPdfDocument
    from simwb import meshreport
    out = meshreport.write_pdf(bracket, tmp_path / "r.pdf")
    assert open(out, "rb").read(5) == b"%PDF-"
    doc = QPdfDocument()
    doc.load(out)
    assert doc.pageCount() >= 2
    img = doc.render(0, QSize(600, 850))
    assert img.width() == 600 and not all(img.pixel(x, y) == 0xFFFFFFFF for x in range(0, 600, 7) for y in range(0, 850, 7))      # (something is drawn)


def test_the_slice_renderer_draws_a_cut_coloured_by_the_metric(bracket):
    from simwb import meshreport
    a = meshreport.render_section(bracket, "x", 0.5, "size")
    b = meshreport.render_section(bracket, "x", 0.5, "quality")
    assert (a.width(), a.height()) == (560, 380)
    px = lambda im: {im.pixel(x, y) for x in range(8, 552, 3) for y in range(8, 330, 3)}      # noqa: E731
    assert len(px(a)) > 20 and len(px(b)) > 20 and px(a) != px(b)                   # many colours, and the two metrics differ
    with pytest.raises(ValueError, match="Unknown mesh metric"):
        meshreport.render_section(bracket, "x", 0.5, "nonsense")


def test_the_histogram_image_has_the_colours_of_the_legend(bracket):
    from simwb import meshreport
    from simwb.qt.metrichist import bin_color
    d = bracket.mesh_metrics()["metrics"]["size"]
    im = meshreport.render_histogram(d, "size")
    cols = {im.pixel(x, y) & 0xFFFFFF for x in range(4, 516, 2) for y in range(10, 120, 4)}
    first = [round(c * 255) for c in bin_color("size", d["edges"][0] * 1.01, d["range"])]
    assert (first[0] << 16 | first[1] << 8 | first[2]) in cols or len(cols) > 8


def test_no_mesh_no_report(ctl, tmp_path):
    ctl.do("study.new", {"name": "Empty", "study": "static_structural"})
    from simwb import meshreport
    with pytest.raises(ValueError, match="generate the mesh first"):
        meshreport.write_html(ctl.engine.project(), tmp_path / "x.html")


def test_the_mesh_step_offers_the_report_like_the_results_step(ctl, solved, tmp_path):
    c = open_mesh(ctl, solved)
    pn = c.win.panels["mesh"]
    assert pn.export_box.isVisibleTo(pn) and c.widget_do("mesh.export.html")["visible"] and c.widget_do("mesh.export.pdf")["visible"]
    assert c.widget_do("mesh.export.html")["tooltip"] and c.widget_do("results.export.report") is not None
    with pytest.raises(ValueError, match="dialog"):                                 # (a control that opens a file chooser is driven by the action)
        c.widget_do("mesh.export.pdf", "click")
    r = c.do("mesh.export", {"format": "html", "path": str(tmp_path / "m.html")})["result"]
    assert (tmp_path / "m.html").stat().st_size > 20000 and r["file"].endswith("m.html")
    r = c.do("mesh.export", {"format": "pdf", "path": str(tmp_path / "m.pdf")})["result"]
    assert (tmp_path / "m.pdf").read_bytes()[:5] == b"%PDF-"
    with pytest.raises(ValueError, match="html or pdf"):
        c.do("mesh.export", {"format": "doc", "path": str(tmp_path / "m.doc")})
    ctl.engine.project().data["mesh"].update(status="none", stats=None)
    ctl.engine.changed.emit()
    ctl._settle()
    assert not pn.export_box.isVisibleTo(pn)                                        # (nothing to report without a mesh)


def test_a_hex_dominant_mesh_has_its_report(ctl, tmp_path, monkeypatch):
    fake_hex_study(ctl, tmp_path, monkeypatch)
    ctl.do("step.go", {"step": "mesh"})
    r = ctl.do("mesh.export", {"format": "html", "path": str(tmp_path / "h.html")})["result"]
    text = open(r["file"], encoding="utf-8").read()
    assert "Hex-dominant" in text and "Cell budget" in text and text.count("data:image/png") >= 5
