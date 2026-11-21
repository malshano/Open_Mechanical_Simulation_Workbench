"""The tools that drive the desktop window write files only inside the workspace or the temp folder (a model may have been steered
by text in a study); a person or the REST window control is not limited."""
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
                         ("study.save_as", {"location": str(outside.parent)})]:
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
