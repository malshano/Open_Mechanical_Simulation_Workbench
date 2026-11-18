"""Built-in OpenFOAM installer: logic that runs without network access or a real installation."""
import io
import urllib.error

import pytest

from simwb.jobs import Job, JobManager
from simwb.solvers.openfoam import installer, runner


def test_installer_is_pinned_with_checksum(monkeypatch):
    monkeypatch.delenv("SIMWB_OPENFOAM_URL", raising=False)
    url, sha = installer.installer_source()
    assert url.startswith("https://") and installer.OPENFOAM_VERSION in url and len(sha) == 64


def test_custom_installer_url_needs_checksum(monkeypatch):
    monkeypatch.setenv("SIMWB_OPENFOAM_URL", "https://mirror.invalid/of.exe")
    monkeypatch.delenv("SIMWB_OPENFOAM_SHA256", raising=False)
    with pytest.raises(RuntimeError, match="SIMWB_OPENFOAM_SHA256"):
        installer.installer_source()
    monkeypatch.setenv("SIMWB_OPENFOAM_SHA256", "AB" * 32)
    assert installer.installer_source() == ("https://mirror.invalid/of.exe", "ab" * 32)


def test_tampered_installer_is_refused(tmp_path):
    import hashlib
    f = tmp_path / "of.exe"
    f.write_bytes(b"MZ official")
    installer.verify_sha256(f, hashlib.sha256(b"MZ official").hexdigest())
    f.write_bytes(b"MZ changed")
    with pytest.raises(RuntimeError, match="does not match its checksum"):
        installer.verify_sha256(f, hashlib.sha256(b"MZ official").hexdigest())


def test_install_refuses_non_empty_target(monkeypatch, tmp_path):
    target = tmp_path / "OpenFOAM"
    target.mkdir()
    (target / "leftover.txt").write_text("x")
    monkeypatch.setattr(installer.sys, "platform", "win32")
    monkeypatch.setenv("SIMWB_OPENFOAM_DIR", str(target))
    monkeypatch.setattr(runner, "detect", lambda refresh=False: {"available": False, "backend": None})
    with pytest.raises(RuntimeError, match="exists but holds no working OpenFOAM"):
        installer.install(Job("install", installer.JOB_PROJECT), JobManager())
    assert (target / "leftover.txt").exists()  # never deletes a folder it did not create


def test_download_rejects_non_installer(monkeypatch, tmp_path):
    class Resp(io.BytesIO):
        headers = {"Content-Length": "13"}
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
    monkeypatch.setattr(installer.urllib.request, "urlopen", lambda *a, **k: Resp(b"<html>404</html>"[:13]))
    with pytest.raises(RuntimeError, match="not a Windows installer"):
        installer._download("https://example.invalid/x.exe", tmp_path / "x.exe",
                            Job("install", installer.JOB_PROJECT), JobManager())


def test_detect_reports_installable_on_windows(monkeypatch):
    monkeypatch.delenv("SIMWB_OPENFOAM_CMD", raising=False)
    monkeypatch.setattr(runner.sys, "platform", "win32")
    monkeypatch.setattr(runner.shutil, "which", lambda name: None)
    monkeypatch.setattr(runner, "find_windows_install", lambda: None)
    info = runner.detect(refresh=True)
    runner._CACHE.clear()
    assert info["available"] is False and info["installable"] is True
    assert "Install OpenFOAM" in info["help"]


def test_detection_skips_tree_being_installed(tmp_path):
    b = tmp_path / "OpenFOAM-v2606" / "platforms" / "win64MingwDPInt32Opt" / "bin"
    b.mkdir(parents=True)
    (b / "simpleFoam.exe").write_bytes(b"MZ")
    assert runner.find_windows_install(roots=[tmp_path])[1] == b
    (tmp_path / runner.INSTALLING_MARKER).write_text("installing")
    assert runner.find_windows_install(roots=[tmp_path]) is None
    assert runner.find_windows_install(roots=[tmp_path], include_installing=True)[1] == b


def test_download_error_is_actionable(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise installer.urllib.error.URLError("no route to host")
    monkeypatch.setattr(installer.urllib.request, "urlopen", boom)
    with pytest.raises(RuntimeError, match="Could not download .*no route to host.*install manually"):
        installer._download("https://example.invalid/x.exe", tmp_path / "x.exe",
                            Job("install", installer.JOB_PROJECT), JobManager())


def test_job_residual_history_is_thinned():
    from simwb import project as P
    from simwb.server import job as job_route
    j = Job("solve", "thin-test")
    j.extra["residuals"] = {"iters": list(range(1, 2001)), "residuals": {"air/Ux": [1.0 / i for i in range(1, 2001)]}}
    P.JOBS.jobs[j.id] = j
    try:
        d = job_route(j.id, log_tail=0, max_points=400)
    finally:
        del P.JOBS.jobs[j.id]
    r = d["extra"]["residuals"]
    assert len(r["iters"]) <= 401 and r["iters"][-1] == 2000 and len(r["residuals"]["air/Ux"]) == len(r["iters"])
    assert len(j.extra["residuals"]["iters"]) == 2000  # the job itself keeps the full history
