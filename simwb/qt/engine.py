"""What the Qt window talks to: the workbench engine (simwb.project) used in this process - no web server in between.
Long jobs (CAD import, meshing, solving) already run on their own threads in the engine; their progress comes back here as
Qt signals. Cases (a study = a folder with a project.json) are created, opened, saved, copied, exported and imported here."""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Signal

from .. import project as P

MAX_RECENT = 10


class Engine(QObject):
    opened = Signal(str)            # a study was opened (its id), or "" when closed
    changed = Signal()              # the study's data changed (a job finished, the assistant used a tool)
    job = Signal(dict)              # progress of a job of the open study
    message = Signal(str, bool)     # text for the status bar, is-an-error
    saved = Signal(str)             # time the study was last written

    def __init__(self, parent=None, settings: QSettings | None = None):
        super().__init__(parent)
        self.pid: str | None = None
        self.settings = settings or QSettings("SimWorkbench", "Workbench")   # (tests pass a file of their own)
        P.JOBS.listeners.append(self._on_event)   # (called from job threads: Qt queues the signals to the window's thread)

    # ------------------------------------------------------------------ events from job threads
    def _on_event(self, ev: dict):
        t = ev.get("type")
        if t == "job":
            j = ev.get("job") or {}
            if j.get("project") != self.pid:
                return
            self.job.emit(ev)
            if ev.get("final") or j.get("status") in ("done", "failed", "cancelled"):
                self.changed.emit()
        elif t == "project_changed" and ev.get("project") == self.pid:
            self.changed.emit()

    # ------------------------------------------------------------------ the open study
    def project(self) -> P.Project:
        if self.pid is None:
            raise RuntimeError("No study is open")
        return P.open_project(self.pid)

    def state(self) -> dict | None:
        return self.project().state() if self.pid else None

    def _open(self, pr: P.Project):
        self.pid = pr.id
        self._remember(pr.dir)
        self.opened.emit(pr.id)
        self.changed.emit()
        self.saved.emit(time.strftime("%H:%M:%S"))
        return pr

    def close_study(self):
        self.pid = None
        self.opened.emit("")

    # ------------------------------------------------------------------ cases: new, open, recent
    def new_study(self, name: str, study: str, location: str | None = None) -> P.Project:
        return self._open(P.create_project(name.strip() or "Study", study, location or None))

    def open_folder(self, path: str) -> P.Project:
        """Open a study folder from anywhere on the disk (one with a project.json)."""
        return self._open(P.open_project_folder(path))

    def open_id(self, pid: str) -> P.Project:
        return self._open(P.open_project(pid))

    def workspace_list(self) -> list:
        return P.list_projects()

    def recent(self) -> list:
        """[{name, path}] of the studies opened lately that are still on the disk, newest first."""
        out = []
        for p in self.settings.value("recent", [], type=list) or []:
            pj = Path(p) / "project.json"
            if pj.exists():
                try:
                    import json
                    out.append({"name": json.loads(pj.read_text()).get("name", Path(p).name), "path": str(p)})
                except (OSError, ValueError):
                    pass
        return out

    def _remember(self, folder: Path):
        cur = [str(folder)] + [p for p in (self.settings.value("recent", [], type=list) or []) if p != str(folder)]
        self.settings.setValue("recent", cur[:MAX_RECENT])

    def clear_recent(self):
        self.settings.setValue("recent", [])

    # ------------------------------------------------------------------ cases: save, save as, export, import
    def save(self) -> str:
        """Write the study to its folder now (it is also written after every change; this makes it explicit and says when)."""
        self.project().save(quiet=True)
        t = time.strftime("%H:%M:%S")
        self.saved.emit(t)
        return t

    def save_as(self, location: str, name: str | None = None, keep_results: bool = True) -> P.Project:
        """A full copy in another folder, which becomes the open study."""
        return self._open(P.save_project_as(self.pid, location, name, keep_results))

    def export_zip(self, path: str, keep_results: bool = True) -> Path:
        return P.export_project(self.pid, path, keep_results)

    def import_zip(self, path: str, location: str | None = None) -> P.Project:
        return self._open(P.import_project(path, location or None))

    def delete_open(self):
        pid = self.pid
        self.close_study()
        P.delete_project(pid)

    # ------------------------------------------------------------------ the steps (each returns the engine's job)
    def import_step(self, path: str):
        return self.project().import_step(path)

    def generate_mesh(self):
        return self.project().generate_mesh()

    def solve(self):
        return self.project().solve()

    def set_mesh_preset(self, preset: str):
        self.project().set_mesh(preset=preset)
        self.changed.emit()

    # ------------------------------------------------------------------ what the views draw
    def mesh_payload(self, axis: str | None, frac: float, color: str):
        pr = self.project()
        if axis:
            m = pr.load_mesh()
            ax = "xyz".index(axis)
            lo, hi = float(m["nodes"][:, ax].min()), float(m["nodes"][:, ax].max())
            return pr.mesh_view(axis, lo + (hi - lo) * frac, False, None, color)
        return pr.mesh_view(None, None, False, None, color)

    def result_fields(self) -> list:
        return self.project().results().field_info()

    def result_surface(self, field: str, time_index: int = -1):
        return self.project().results().at(time_index).surface(field)

    def summary_text(self) -> str:
        return self.project().summary_text()
