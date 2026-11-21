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


# ---- the MCP tools that name a file or folder, empty paths, the workbench's own files, replacing files, controls not for a model
def test_create_project_and_import_step_are_checked_like_the_window_actions(window, tmp_path, monkeypatch):
    bs = chr(92)
    unc = bs * 2 + "attacker" + bs + "share"
    seen = []
    monkeypatch.setattr(tools, "create_project", lambda pr, a: seen.append(a) or {"ok": 1})
    tools_by_name = {t[0]: t for t in tools.TOOLS + tools.MCP_EXTRA}
    assert "create_project" in tools_by_name and "import_step" in tools_by_name
    out = tools.call("create_project", None, {"name": "x", "study": "static_structural", "location": unc})
    assert out.startswith("ERROR") and ("network" in out or "full local path" in out)
    out = tools.call("import_step", None, {"step_path": unc + bs + "w.step"})
    assert out.startswith("ERROR") and ("network" in out or "full local path" in out)
    out = tools.call("import_step", None, {"step_path": str(tmp_path / "notes.txt")})
    assert out.startswith("ERROR") and "files only" in out


def test_a_folder_the_user_chose_is_fine_for_create_project(tmp_path):
    folder = tmp_path / "Simulations"
    out = tools._check_paths("create_project", {"name": "x", "location": str(folder)})
    assert out["location"] == str(folder)                     # (local, and need not exist yet)


def test_an_empty_path_is_refused_not_taken_as_the_working_folder(window):
    for action, key in [("study.open_folder", "path"), ("study.export", "path"), ("study.new", "location")]:
        with pytest.raises(ValueError, match="is empty"):
            tools.ui_do(None, {"action": action, "args": {key: "  "}})
    assert window == []


def test_the_workbenchs_own_files_are_never_written(window):
    ws = Path(P.WORKSPACE)
    for name in ["locations.json", "project.json", "CLAUDE.md", "CLAUDE.local.md", ".mcp.json"]:
        with pytest.raises(ValueError, match="own files"):
            tools.ui_do(None, {"action": "results.export", "args": {"format": "report", "path": str(ws / "a" / name)}})
    for where in [".ui", ".claude"]:
        with pytest.raises(ValueError, match="own files"):
            tools.ui_do(None, {"action": "results.export", "args": {"format": "csv", "path": str(ws / where / "x.csv")}})
    assert window == []


def test_a_file_is_written_but_never_replaced(window, tmp_path):
    existing = Path(P.WORKSPACE) / "report.json"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text("keep me")
    with pytest.raises(ValueError, match="exists already"):
        tools.ui_do(None, {"action": "results.export", "args": {"format": "report", "path": str(existing)}})
    tools.ui_do(None, {"action": "results.export", "args": {"format": "report", "path": str(existing.with_name("new.json"))}})
    assert existing.read_text() == "keep me" and window[-1][1]["action"] == "results.export"


def test_controls_that_quit_or_sign_out_are_not_operated(window):
    for wid in ["file.exit", "assistant.logout", "assistant.login"]:
        with pytest.raises(ValueError, match="not operated through the assistant"):
            tools.ui_widget(None, {"id": wid, "op": "click"})
    tools.ui_widget(None, {"id": "file.exit", "op": "get"})   # (reading is fine)
    tools.ui_widget(None, {"id": "mesh.generate", "op": "click"})
    assert [c[1]["id"] for c in window] == ["file.exit", "mesh.generate"]
