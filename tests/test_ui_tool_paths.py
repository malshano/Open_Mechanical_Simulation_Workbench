"""The tools that drive the desktop window name files only inside the workspace or the temp folder, and only the kinds the workbench
makes (a model may have been steered by text in a study); a person or the REST window control is not limited."""
import tempfile
from pathlib import Path

import pytest

from simwb import project as P, tools, ui_bridge


@pytest.fixture
def window(monkeypatch):
    calls = []
    monkeypatch.setattr(ui_bridge, "HANDLER", lambda op, args: calls.append((op, args)) or {"ok": True})
    return calls


def test_writes_outside_the_workspace_are_refused(window):
    outside = Path.home() / "Startup" / "x.png"
    for action, args in [("view.picture", {"path": str(outside)}), ("study.export", {"path": str(outside.with_suffix('.zip'))}),
                         ("results.export", {"format": "csv", "path": str(outside.with_suffix('.csv'))}),
                         ("study.save_as", {"location": str(outside.parent)}),
                         ("study.new", {"name": "x", "study": "static_structural", "location": str(outside.parent)}),
                         ("study.open_folder", {"path": str(outside.parent)}),
                         ("study.import", {"path": str(Path(P.WORKSPACE) / "a.zip"), "location": str(outside.parent)})]:
        with pytest.raises(ValueError, match="only inside the workspace"):
            tools.ui_do(None, {"action": action, "args": args})
    assert window == []                                           # (nothing reached the window)


def test_a_path_that_climbs_out_of_the_workspace_is_refused(window):
    sneaky = Path(P.WORKSPACE) / ".." / ".." / "evil.png"
    with pytest.raises(ValueError, match="only inside the workspace"):
        tools.ui_do(None, {"action": "view.picture", "args": {"path": str(sneaky)}})
    assert window == []


def test_inside_the_workspace_or_temp_it_goes_through(window):
    ok = [("view.picture", {"path": str(Path(P.WORKSPACE) / "a.png")}),
          ("study.export", {"path": str(Path(tempfile.gettempdir()) / "a.zip")})]
    for action, args in ok:
        tools.ui_do(None, {"action": action, "args": args})
    assert [c[1]["action"] for c in window] == ["view.picture", "study.export"]


def test_only_the_kinds_of_file_the_workbench_makes(window):
    with pytest.raises(ValueError, match="files only"):
        tools.ui_do(None, {"action": "view.picture", "args": {"path": str(Path(P.WORKSPACE) / "run.bat")}})
    with pytest.raises(ValueError, match="files only"):
        tools.ui_do(None, {"action": "study.export", "args": {"path": str(Path(tempfile.gettempdir()) / "x.exe")}})


def test_other_actions_are_untouched(window):
    tools.ui_do(None, {"action": "step.go", "args": {"step": "results"}})
    assert window and window[0][1]["action"] == "step.go"


def test_reading_a_file_is_limited_to_the_kind_it_expects(window):
    with pytest.raises(ValueError, match="files only"):
        tools.ui_do(None, {"action": "geometry.import", "args": {"path": str(Path.home() / ".ssh" / "id_rsa")}})
    with pytest.raises(ValueError, match="files only"):
        tools.ui_do(None, {"action": "study.import", "args": {"path": str(Path(P.WORKSPACE) / "notes.txt")}})


def test_an_action_not_listed_that_takes_a_path_is_refused(window):
    with pytest.raises(ValueError, match="not allowed through the assistant"):
        tools.ui_do(None, {"action": "some.future_action", "args": {"path": str(Path(P.WORKSPACE) / "a.txt")}})
    with pytest.raises(ValueError, match="not allowed through the assistant"):
        tools.ui_do(None, {"action": "view.picture", "args": {"path": str(Path(P.WORKSPACE) / "a.png"), "dest": "C:/x"}})


def test_network_and_device_paths_are_refused(window):
    bs = chr(92)
    unc = bs * 2 + "attacker" + bs + "share" + bs + "wing.step"
    dev = bs * 2 + "?" + bs + "C:" + bs + "x" + bs + "wing.step"
    dot = bs * 2 + "." + bs + "pipe" + bs + "x.step"
    for raw in [unc, "//attacker/share/wing.step", dev, dot]:
        with pytest.raises(ValueError, match="network|device"):
            tools.ui_do(None, {"action": "geometry.import", "args": {"path": raw}})
    with pytest.raises(ValueError, match="network|device"):
        tools.ui_do(None, {"action": "view.picture", "args": {"path": unc.replace("wing.step", "a.png")}})
    with pytest.raises(ValueError, match="alternate data stream"):
        tools.ui_do(None, {"action": "view.picture", "args": {"path": str(Path(P.WORKSPACE) / "a.png") + ":evil"}})
    assert window == []


def test_a_read_needs_an_existing_ordinary_file(window, tmp_path):
    with pytest.raises(ValueError, match="not an existing file"):
        tools.ui_do(None, {"action": "geometry.import", "args": {"path": str(tmp_path / "missing.step")}})
    f = tmp_path / "wing.step"
    f.write_text("ISO-10303-21;")
    tools.ui_do(None, {"action": "geometry.import", "args": {"path": str(f)}})
    assert window[-1][1]["action"] == "geometry.import"


def test_only_full_drive_paths_get_through(window):
    bs = chr(92)
    for raw in [bs + "??" + bs + "UNC" + bs + "attacker" + bs + "share" + bs + "w.step", bs + "attacker" + bs + "w.step", "w.step",
                "~/w.step", ".." + bs + "w.step", "/attacker/share/w.step", "C:w.step"]:
        with pytest.raises(ValueError, match="full local path"):
            tools.ui_do(None, {"action": "geometry.import", "args": {"path": raw}})
    assert window == []


def test_device_names_are_refused(window):
    for name in ["CON.png", "nul.png", "COM1.png", "aux.png"]:
        with pytest.raises(ValueError, match="device name"):
            tools.ui_do(None, {"action": "view.picture", "args": {"path": str(Path(P.WORKSPACE) / name)}})
    assert window == []


def test_the_window_gets_the_checked_path_not_the_text_given(window, tmp_path):
    f = tmp_path / "sub" / ".." / "wing.step"
    (tmp_path / "sub").mkdir()
    (tmp_path / "wing.step").write_text("ISO-10303-21;")
    tools.ui_do(None, {"action": "geometry.import", "args": {"path": str(f)}})
    assert window[-1][1]["args"]["path"] == str((tmp_path / "wing.step").resolve())


def test_links_in_a_path_are_not_followed(window, tmp_path):
    import os
    import subprocess
    import sys

    real = tmp_path / "real"
    real.mkdir()
    (real / "wing.step").write_text("ISO-10303-21;")
    link = tmp_path / "link"
    try:
        if sys.platform == "win32":                               # a junction needs no privilege
            subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(real)], check=True, capture_output=True)
        else:
            os.symlink(real, link, target_is_directory=True)
    except Exception:
        pytest.skip("cannot make a link here")
    with pytest.raises(ValueError, match="is a link"):
        tools.ui_do(None, {"action": "geometry.import", "args": {"path": str(link / "wing.step")}})
    assert window == []
    tools.ui_do(None, {"action": "geometry.import", "args": {"path": str(real / "wing.step")}})   # (the real folder is fine)
    assert window[-1][1]["action"] == "geometry.import"
