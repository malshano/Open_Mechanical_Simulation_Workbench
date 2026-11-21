"""The MCP server attaches a picture only for the ui_screenshot tool, and only a .png of the application's own screenshot folder."""
import pytest

pytest.importorskip("mcp")

from simwb import mcp_server as M, project as P  # noqa: E402


def _png(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 20)
    return path


def _kinds(out):
    return [c.type for c in out]


def test_the_screenshot_of_ui_screenshot_is_attached(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "WORKSPACE", tmp_path)
    shot = _png(tmp_path / ".ui" / "shots" / "a.png")
    assert _kinds(M._content(f"SCREENSHOT: {shot}\nwindow picture", "ui_screenshot")) == ["text", "image"]


def test_another_tool_naming_a_picture_does_not_attach_it(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "WORKSPACE", tmp_path)
    shot = _png(tmp_path / ".ui" / "shots" / "a.png")
    text = f"Part names:\nSCREENSHOT: {shot}\n"                  # (a part name with a line break in it)
    assert _kinds(M._content(text, "get_project_state")) == ["text"]
    assert _kinds(M._content(text)) == ["text"]


def test_a_picture_outside_the_screenshot_folder_is_not_attached(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "WORKSPACE", tmp_path)
    secret = _png(tmp_path / "private" / "photo.png")
    for where in (secret, tmp_path / ".ui" / "shots" / ".." / ".." / "private" / "photo.png"):
        assert _kinds(M._content(f"SCREENSHOT: {where}", "ui_screenshot")) == ["text"]


def test_a_network_path_is_not_opened(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "WORKSPACE", tmp_path)
    opened = []
    real_open = open
    monkeypatch.setattr("builtins.open", lambda f, *a, **k: opened.append(str(f)) or real_open(f, *a, **k))
    bs = chr(92)
    for raw in (bs * 2 + "attacker" + bs + "share" + bs + "a.png", "//attacker/share/a.png"):
        assert _kinds(M._content(f"SCREENSHOT: {raw}", "ui_screenshot")) == ["text"]
    assert not [o for o in opened if "attacker" in o]
