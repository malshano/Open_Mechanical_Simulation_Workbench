"""MOOSE engine registry: where it is offered, how it is found, that the installer refuses where MOOSE has no build,
and the subprocess runner (with a tiny fake MOOSE application; the real one is never needed)."""
import os
import stat
import sys

import pytest

from simwb.solvers import moose
from simwb.solvers.moose import api, installer

FAKE_PY = r'''
import sys, pathlib
args = sys.argv[1:]
if args == ["--version"]:
    print("MOOSE Version: 9.9.9 (abc123)")
    sys.exit(0)
inp = pathlib.Path(args[args.index("-i") + 1])
text = inp.read_text()
print("Framework Information:")
print("Running", inp.name)
if "fail" in text:
    print("*** ERROR ***", file=sys.stderr)
    sys.exit(3)
stem = inp.stem
pathlib.Path(stem + "_out.e").write_bytes(b"EXODUS")
pathlib.Path(stem + "_out.csv").write_text("time,T\n0,300\n1,310\n")
print("Finished Executing")
'''


def make_fake(tmp_path, name="fake-opt"):
    """An executable that runs FAKE_PY with the current Python (a .bat wrapper on Windows, a script elsewhere)."""
    py = tmp_path / "fake_moose.py"
    py.write_text(FAKE_PY)
    if sys.platform == "win32":
        exe = tmp_path / (name + ".bat")
        exe.write_text(f'@"{sys.executable}" "{py}" %*\r\n')
    else:
        exe = tmp_path / name
        exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{py}" "$@"\n')
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return exe


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    installer._cache.clear()
    monkeypatch.delenv("SIMWB_MOOSE", raising=False)
    yield
    installer._cache.clear()


def test_supported_per_platform():
    for p in ("linux", "linux2", "darwin"):
        assert installer.supported(p) == (True, "")
    ok, reason = installer.supported("win32")
    assert not ok
    assert reason == ("MOOSE has no native Windows build; its official Windows route is WSL, which the workbench "
                      "does not use.")
    assert not installer.supported("cygwin")[0]
    assert not installer.supported("freebsd13")[0]
    assert moose.supported is installer.supported


def test_conda_subdir_only_where_inl_has_a_package():
    assert installer.conda_subdir("Linux", "x86_64") == "linux-64"
    assert installer.conda_subdir("Darwin", "arm64") == "osx-arm64"
    assert installer.conda_subdir("Darwin", "x86_64") is None
    assert installer.conda_subdir("Linux", "aarch64") is None
    assert installer.conda_subdir("Windows", "AMD64") is None
    assert set(installer.MICROMAMBA) == {"linux-64", "osx-arm64"}
    assert all(len(sha) == 64 for _, sha in installer.MICROMAMBA.values())


def test_detect_via_env_var(tmp_path, monkeypatch):
    exe = make_fake(tmp_path)
    monkeypatch.setenv("SIMWB_MOOSE", str(exe))
    info = installer.detect(refresh=True)
    assert info["installed"] is True and info["path"] == str(exe)
    assert info["version"] == "9.9.9"
    assert info["supported"] is True and info["help"] == ""


def test_detect_nothing_found(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMWB_MOOSE", str(tmp_path / "missing-opt"))
    monkeypatch.setattr(installer, "BUNDLED_DIR", tmp_path / "none")
    info = installer.detect(refresh=True)
    assert info["installed"] is False and info["path"] is None and info["version"] is None


def test_detect_on_path_and_in_workbench_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "supported", lambda platform=None: (True, ""))
    monkeypatch.delenv("SIMWB_MOOSE_DIR", raising=False)
    # in the workbench folder
    wb = tmp_path / "wb" / "moose"
    wb.mkdir(parents=True)
    exe = wb / "app-opt"
    exe.write_text("x")
    exe.chmod(0o755)
    monkeypatch.setattr(installer, "BUNDLED_DIR", wb)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert installer.find_moose() == exe
    assert installer.detect(refresh=True)["source"] == "workbench copy"
    # on the PATH
    monkeypatch.setattr(installer, "BUNDLED_DIR", tmp_path / "none")
    pdir = tmp_path / "bin"
    pdir.mkdir()
    p = pdir / "moose-opt"
    p.write_text("x")
    p.chmod(0o755)
    (pdir / "moose").write_text("x")  # a bare `moose` on the PATH is not taken
    (pdir / "notamoose").write_text("x")
    monkeypatch.setenv("PATH", str(pdir))
    assert installer.find_moose() == p
    assert installer.detect(refresh=True)["source"] == "found on this computer"


def test_detect_unsupported_system_reports_reason(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(installer, "BUNDLED_DIR", installer.BUNDLED_DIR.parent / "no-such-moose-folder")
    info = installer.detect(refresh=True)
    assert info["installed"] is False and info["supported"] is False and info["installable"] is False
    assert "WSL" in info["reason"] and info["help"] == info["reason"]


def test_installer_refuses_on_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")

    class Job:
        proc = None

        def check_cancel(self):
            raise AssertionError("must not start")

    class JM:
        def update(self, *a, **k):
            raise AssertionError("must not start")
    with pytest.raises(RuntimeError, match="no native Windows build.*WSL"):
        installer.install(Job(), JM())
    with pytest.raises(RuntimeError, match="no native Windows build"):
        installer.start(JM())


def test_installer_refuses_uncovered_machine(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(installer, "conda_subdir", lambda *a: None)
    with pytest.raises(RuntimeError, match="Linux x86-64 and macOS Apple silicon"):
        installer.install(None, None)


def test_import_is_light():
    import subprocess
    root = str(__import__("pathlib").Path(__file__).resolve().parents[1])
    code = "import sys, simwb.solvers.moose as m; print(m.supported('win32')[0], 'subprocess' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=root)
    assert out.returncode == 0, out.stderr
    assert out.stdout.split()[0] == "False"


def test_run_moose_captures_log_and_result_files(tmp_path):
    exe = make_fake(tmp_path)
    work = tmp_path / "case"
    work.mkdir()
    (work / "heat.i").write_text("[Mesh]\n[]\n")
    (work / "old.e").write_bytes(b"old")  # existing but untouched: not reported
    lines = []
    procs = []
    res = api.run_moose(work, "heat.i", exe=exe, on_proc=procs.append, log=lines.append, timeout=60)
    assert res["returncode"] == 0
    assert "Finished Executing" in res["log"] and lines == res["log"]
    assert res["exodus"] == ["heat_out.e"] and res["csv"] == ["heat_out.csv"]
    assert (work / "heat_out.csv").read_text().startswith("time,T")
    assert len(procs) == 1 and procs[0].returncode == 0
    assert res["command"][1:3] == ["-i", "heat.i"]


def test_run_moose_failure_and_missing_input(tmp_path, monkeypatch):
    exe = make_fake(tmp_path)
    work = tmp_path / "case"
    work.mkdir()
    (work / "bad.i").write_text("fail")
    with pytest.raises(api.MooseError, match="exit code 3") as ei:
        api.run_moose(work, "bad.i", exe=exe, log=None)
    assert ei.value.result["returncode"] == 3 and ei.value.result["exodus"] == []
    with pytest.raises(FileNotFoundError):
        api.run_moose(work, "nothere.i", exe=exe)
    (work / "ok.i").write_text("x")
    monkeypatch.setattr(installer, "find_moose", lambda: None)
    with pytest.raises(api.MooseError, match="No MOOSE application"):
        api.run_moose(work, "ok.i", log=None)


def test_run_moose_timeout(tmp_path):
    slow = tmp_path / "slow.py"
    slow.write_text("import time\ntime.sleep(5)\n")
    if sys.platform == "win32":
        exe = tmp_path / "slow-opt.bat"
        exe.write_text(f'@"{sys.executable}" "{slow}" %*\r\n')
    else:
        exe = tmp_path / "slow-opt"
        exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{slow}" "$@"\n')
        os.chmod(exe, 0o755)
    (tmp_path / "a.i").write_text("x")
    with pytest.raises(TimeoutError):
        api.run_moose(tmp_path, "a.i", exe=exe, log=None, timeout=1)
