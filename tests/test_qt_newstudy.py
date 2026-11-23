"""A new study first asks where to save it; the 3D model is imported inside the study afterwards."""
import os

import pytest
from qt_support import ctl, from_thread  # noqa: F401

from simwb.qt import newstudy


def wait(c, t=300):
    assert from_thread(c.app, lambda: c.wait_idle(t), t + 20)["idle"]
    c._settle()


@pytest.fixture
def home(ctl):
    ctl.win.new_study()                                   # (the start screen: no study open)
    ctl.app.processEvents()
    assert not ctl.engine.pid
    return ctl


def open_dialog(c, key="static_structural"):
    d = c.win.new_from_card(key)
    c.app.processEvents()
    assert isinstance(d, newstudy.NewStudyDialog) and d.isVisible()
    return d


def test_a_new_study_asks_where_to_save_before_anything_is_made(home, tmp_path):
    d = open_dialog(home)
    chosen = tmp_path / "chosen"
    assert d.name.text() and d.folder.text()                                       # (a suggested name and the folder last used)
    d.folder.setText(str(chosen))
    assert not home.engine.pid and not chosen.exists()                               # (nothing exists yet)
    d.reject()
    assert not home.engine.pid and not chosen.exists()                               # (cancel: nothing was made)


def test_create_makes_the_study_in_the_chosen_folder_and_opens_the_geometry_step(home, tmp_path):
    d = open_dialog(home)
    folder = tmp_path / "my studies"                                                 # (does not exist yet: made)
    d.name.setText("Bracket test")
    d.folder.setText(str(folder))
    assert d.path.text().endswith(str(folder / "Bracket-test"))                      # (it shows the folder it will be)
    d.ok.click()
    assert (folder / "Bracket-test" / "project.json").exists()
    st = home.engine.state()
    assert st["name"] == "Bracket test" and st["geometry"]["status"] == "none" and home.win.step == "geometry"
    assert home.engine.settings.value("saveDir") == str(folder)                      # (the next study starts there)
    home._settle()
    assert home.widget_do("geometry.import")["visible"] is True                      # (the model is imported inside the study)


def test_the_dialog_says_what_is_wrong_and_stays_open(home, tmp_path):
    d = open_dialog(home)
    d.name.setText("   ")
    d.ok.click()
    assert d.isVisible() and d.error.isVisible() and "name" in d.error.text().lower()
    d.name.setText("Plate")
    blocker = tmp_path / "a file"
    blocker.write_text("x")
    d.folder.setText(str(blocker / "inside"))                                        # (a folder cannot be made inside a file)
    d.ok.click()
    assert d.isVisible() and d.error.isVisible() and not home.engine.pid
    d.reject()


def test_a_name_that_exists_gets_a_number_not_an_overwrite(home, tmp_path):
    for _ in range(2):
        d = open_dialog(home)
        d.name.setText("Plate")
        d.folder.setText(str(tmp_path))
        d.ok.click()
        home.win.new_study()
    assert (tmp_path / "Plate").is_dir() and (tmp_path / "Plate-2").is_dir()


def test_the_model_is_imported_inside_the_new_study(home, tmp_path, box_step):
    d = open_dialog(home)
    chosen = tmp_path / "chosen"
    d.name.setText("Imported")
    d.folder.setText(str(chosen))
    d.ok.click()
    home.do("geometry.import", {"path": str(box_step)})
    wait(home)
    assert home.engine.state()["geometry"]["status"] == "ready"
    assert any(p.name.startswith("source") for p in (chosen / "Imported" / "geometry").iterdir())      # (the model is inside the study's folder)


def test_the_scripted_example_still_makes_its_study_at_once(home):
    home.do("study.example", {"study": "static_structural"})                        # (no dialog to answer for scripts and Claude)
    wait(home)
    assert home.engine.pid and home.engine.state()["geometry"]["status"] == "ready"


def test_file_import_model_needs_a_study(home):
    assert not home.win.act_importmodel.isEnabled()
    home.do("study.new", {"name": "Plate", "study": "static_structural"})
    assert home.win.act_importmodel.isEnabled() and home.win.act_importmodel.shortcut().toString() == "Ctrl+I"
