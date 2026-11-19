"""The parts of the desktop (Qt) application that need no screen: the engine facade (cases: new, open, recent, save as, export,
import) and the colour maps / legend ticks."""
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from simwb.qt import colormaps  # noqa: E402
from simwb.qt.legend import log_ticks  # noqa: E402


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMWB_HOME", str(tmp_path / "ws"))
    import importlib

    import simwb.project as P

    importlib.reload(P)
    from simwb.qt.engine import Engine

    QApplication.instance() or QApplication([])
    e = Engine(settings=QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat))
    e.P = P
    yield e
    P.JOBS.listeners.remove(e._on_event)


def test_new_study_opens_it_and_remembers_it(engine, tmp_path):
    seen = []
    engine.opened.connect(seen.append)
    pr = engine.new_study("Bracket", "static_structural", str(tmp_path / "mine"))
    assert engine.pid == pr.id and seen == [pr.id]
    assert engine.state()["name"] == "Bracket"
    assert [r["name"] for r in engine.recent()] == ["Bracket"]
    assert engine.recent()[0]["path"] == str(pr.dir)


def test_open_folder_and_recent_order(engine, tmp_path):
    a = engine.new_study("A", "static_structural", str(tmp_path / "x"))
    b = engine.new_study("B", "heat_conduction", str(tmp_path / "y"))
    engine.open_folder(str(a.dir))
    assert engine.pid == a.id
    assert [r["name"] for r in engine.recent()] == ["A", "B"]      # the last one opened first
    engine.close_study()
    assert engine.pid is None
    engine.clear_recent()
    assert engine.recent() == []


def test_recent_skips_folders_that_are_gone(engine, tmp_path):
    import shutil

    pr = engine.new_study("Gone", "static_structural", str(tmp_path / "z"))
    shutil.rmtree(pr.dir)
    assert engine.recent() == []


def test_save_as_makes_the_copy_the_open_study(engine, tmp_path):
    a = engine.new_study("Orig", "static_structural")
    (a.dir / "geometry").mkdir(exist_ok=True)
    (a.dir / "geometry" / "m.step").write_text("STEP")
    b = engine.save_as(str(tmp_path / "copies"), "Orig v2")
    assert engine.pid == b.id != a.id
    assert (b.dir / "geometry" / "m.step").read_text() == "STEP"
    assert a.data["name"] == "Orig"


def test_export_and_import_through_the_engine(engine, tmp_path):
    a = engine.new_study("Pack", "static_structural")
    (a.dir / "results").mkdir(exist_ok=True)
    (a.dir / "results" / "r.npz").write_text("R")
    z = engine.export_zip(str(tmp_path / "pack.simwb.zip"))
    b = engine.import_zip(str(z), str(tmp_path / "unpacked"))
    assert engine.pid == b.id != a.id
    assert (b.dir / "results" / "r.npz").read_text() == "R"
    with pytest.raises(ValueError):
        engine.import_zip(str(tmp_path / "missing.zip"))


def test_no_study_open_is_an_error_not_a_crash(engine):
    with pytest.raises(RuntimeError):
        engine.project()
    assert engine.state() is None


# ------------------------------------------------------------------------------------------------- colour maps
def test_every_colour_map_is_a_valid_table():
    for name in colormaps.NAMES:
        t = colormaps.table(name)
        assert t.shape == (256, 3) and t.min() >= 0 and t.max() <= 1
    with pytest.raises(ValueError, match="Unknown colour map"):
        colormaps.table("nope")


def test_reverse_and_bands():
    t, r = colormaps.table("viridis"), colormaps.table("viridis", reverse=True)
    assert np.allclose(t[::-1], r)
    b = colormaps.table("turbo", bands=5)
    assert len({tuple(np.round(c, 6)) for c in b}) == 5          # five flat colours


def test_nice_ticks_are_round_and_inside_the_range():
    t = colormaps.nice_ticks(0.00191, 41.23, 5)
    assert t == [10.0, 20.0, 30.0, 40.0]
    assert colormaps.nice_ticks(5, 5) == [5]
    assert all(0 <= v <= 1 for v in colormaps.nice_ticks(0, 1, 4))


def test_log_ticks():
    assert log_ticks(2.186, 178.6) == [5, 10, 20, 50, 100]
