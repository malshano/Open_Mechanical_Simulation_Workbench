"""Saving a study must survive something else having project.json open for a moment (on Windows the final rename then fails with
'[WinError 5] Access is denied': the window reading the study, a virus scanner, the search indexer). A solver run used to fail on it."""
import importlib
import json
import sys
import threading
import time

import pytest


@pytest.fixture
def P(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMWB_HOME", str(tmp_path / "ws"))
    import simwb.project as P

    importlib.reload(P)
    return P


@pytest.fixture
def study(P, tmp_path):
    return P.create_project("Plate", "static_structural", str(tmp_path / "studies"))


def _tmp_files(study):
    return sorted(p.name for p in study.dir.glob("*.tmp"))


def test_a_save_is_tried_again_when_the_file_is_busy(P, study, monkeypatch):
    real, calls = P.os.replace, []

    def busy(src, dst):
        calls.append(1)
        if len(calls) <= 3:
            raise PermissionError(13, "Access is denied", str(dst))
        return real(src, dst)
    monkeypatch.setattr(P.os, "replace", busy)
    monkeypatch.setattr(P.time, "sleep", lambda s: None)
    study.data["name"] = "Changed"
    study.save(quiet=True)
    assert len(calls) == 4
    assert json.loads((study.dir / "project.json").read_text())["name"] == "Changed" and _tmp_files(study) == []


def test_a_save_that_cannot_be_done_says_so_and_leaves_no_temp_file(P, study, monkeypatch):
    def never(src, dst):
        raise PermissionError(13, "Access is denied", str(dst))
    monkeypatch.setattr(P.os, "replace", never)
    monkeypatch.setattr(P.time, "sleep", lambda s: None)
    before = (study.dir / "project.json").read_text()
    study.data["name"] = "Lost"
    with pytest.raises(PermissionError):
        study.save(quiet=True)
    assert (study.dir / "project.json").read_text() == before and _tmp_files(study) == []      # (the old file is intact)


@pytest.mark.skipif(sys.platform != "win32", reason="the sharing rules of Windows")
def test_a_save_waits_for_a_reader_that_holds_the_file_open(P, study):
    f = open(study.dir / "project.json", "rb")            # (Python's open() does not share the right to delete: the rename is refused while this is open)
    threading.Timer(0.4, f.close).start()
    study.data["name"] = "Saved while read"
    t0 = time.time()
    study.save(quiet=True)
    assert time.time() - t0 >= 0.3                          # (it really had to wait)
    assert json.loads((study.dir / "project.json").read_text())["name"] == "Saved while read" and _tmp_files(study) == []


def test_two_writers_never_share_a_temp_file(P, study):
    errors = []

    def work(tag):
        try:
            for i in range(25):
                study.data["name"] = f"{tag}{i}"
                study.save(quiet=True)
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))
    ts = [threading.Thread(target=work, args=(t,)) for t in "AB"]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors and _tmp_files(study) == []
    assert json.loads((study.dir / "project.json").read_text())["name"][0] in "AB"
