"""Saving and opening cases: save as (a full copy), export to one file, import from one file."""
import json
import zipfile

import pytest


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMWB_HOME", str(tmp_path / "ws"))
    import importlib

    import simwb.project as P

    importlib.reload(P)
    yield P


def _study(P):
    """A study with files of every kind a finished one has."""
    pr = P.create_project("Bracket", "static_structural")
    for rel, content in (("geometry/model.step", "STEP"), ("mesh/mesh.npz", "MESH"), ("case/log.solver", "LOG"),
                         ("results/result.npz", "RESULT"), ("videos/a.mp4", "VIDEO"), ("scratch.tmp", "T")):
        f = pr.dir / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
    return pr


def test_save_as_copies_everything_and_is_a_study_of_its_own(workspace, tmp_path):
    P = workspace
    pr = _study(P)
    cp = P.save_project_as(pr.id, str(tmp_path / "elsewhere"), name="Bracket v2")
    assert cp.id != pr.id and cp.dir.parent == (tmp_path / "elsewhere").resolve()
    assert cp.data["name"] == "Bracket v2"
    for rel in ("geometry/model.step", "mesh/mesh.npz", "case/log.solver", "results/result.npz", "videos/a.mp4"):
        assert (cp.dir / rel).read_text() == (pr.dir / rel).read_text()
    assert not (cp.dir / "scratch.tmp").exists()
    assert pr.data["name"] == "Bracket"                       # the original is untouched
    assert cp.id in {p["id"] for p in P.list_projects()}      # and the copy is in the list
    assert P.open_project(cp.id).dir == cp.dir


def test_save_as_without_results_resets_the_solution(workspace, tmp_path):
    P = workspace
    pr = _study(P)
    pr.data["solve"] = {"status": "done"}
    pr.save(quiet=True)
    cp = P.save_project_as(pr.id, str(tmp_path / "lean"), keep_results=False)
    assert cp.data["solve"] == {"status": "none"}
    assert (cp.dir / "mesh/mesh.npz").exists() and not (cp.dir / "results").exists() and not (cp.dir / "case").exists()


def test_export_then_import_round_trip(workspace, tmp_path):
    P = workspace
    pr = _study(P)
    z = P.export_project(pr.id, str(tmp_path / "bracket.simwb.zip"))
    with zipfile.ZipFile(z) as f:
        names = set(f.namelist())
    assert "project.json" in names and "results/result.npz" in names
    assert "scratch.tmp" not in names and "CLAUDE.md" not in names and ".mcp.json" not in names   # machine-specific files stay out
    back = P.import_project(str(z), location=str(tmp_path / "other"))
    assert back.id != pr.id and back.data["name"] == "Bracket"
    assert (back.dir / "results/result.npz").read_text() == "RESULT"
    assert back.dir.parent == (tmp_path / "other").resolve()
    again = P.import_project(str(z))                                # into the workspace: a third, separate study
    assert again.id not in (pr.id, back.id)
    lean = P.export_project(pr.id, str(tmp_path / "lean.zip"), keep_results=False)
    with zipfile.ZipFile(lean) as f:
        assert not any(n.startswith(("results/", "case/", "videos/")) for n in f.namelist())


def test_import_refuses_what_is_not_a_study(workspace, tmp_path):
    P = workspace
    not_zip = tmp_path / "x.zip"
    not_zip.write_text("hello")
    with pytest.raises(ValueError, match="not a zip"):
        P.import_project(str(not_zip))
    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w") as f:
        f.writestr("readme.txt", "no project here")
    with pytest.raises(ValueError, match="no project.json"):
        P.import_project(str(empty))
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as f:
        f.writestr("project.json", json.dumps({"name": "x", "study": "not_a_study"}))
    with pytest.raises(ValueError, match="unreadable"):
        P.import_project(str(bad))


def test_import_refuses_entries_outside_the_study_folder(workspace, tmp_path):
    P = workspace
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as f:
        f.writestr("project.json", json.dumps({"name": "evil", "study": "static_structural"}))
        f.writestr("../../escaped.txt", "gotcha")
    before = {p.name for p in P.WORKSPACE.iterdir()} if P.WORKSPACE.exists() else set()
    with pytest.raises(ValueError, match="outside the study folder"):
        P.import_project(str(evil))
    assert not (tmp_path / "escaped.txt").exists() and not (P.WORKSPACE.parent / "escaped.txt").exists()
    assert {p.name for p in P.WORKSPACE.iterdir()} == before      # nothing half-unpacked is left behind
