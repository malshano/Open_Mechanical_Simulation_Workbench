"""The main window: menus (cases: new, open, recent, save, save as, export, import), the five steps, the outline, the 3D view and
the Claude assistant dock."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QKeySequence, QPainter, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDockWidget, QFileDialog, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar, QPushButton, QScrollArea,
                               QStackedWidget, QStatusBar, QToolBar, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import project as P
from .. import studies
from .assistant import AssistantPanel, ChatBridge
from .engine import Engine
from .home import HomePage
from .ids import wid
from .panels import GeometryPanel, MeshPanel, ResultsPanel, SetupPanel, SolvePanel
from .viewport import Viewport

# the faces that make up the air box of a study (drawn translucent, with a toggle), and whether it is shown at first (the far field of
# a flow study is big: off; the enclosure of a cooling study is part of the model: on)
AIR_KINDS = {"cht_enclosure": ("enclosure",), "external_aero": ("farfield",)}
STEPS = [("geometry", "Geometry"), ("setup", "Setup"), ("mesh", "Mesh"), ("solve", "Solve"), ("results", "Results")]


class MainWindow(QMainWindow):
    def __init__(self, engine: Engine, bridge: ChatBridge):
        super().__init__()
        self.engine, self.bridge = engine, bridge
        self.step = "geometry"
        self.setMinimumSize(1100, 700)
        self.view = wid(Viewport(), "view.canvas")
        wid(self.view.legend, "view.legend")
        self.view.faceHovered.connect(self._hover_tip)
        self.home = wid(HomePage(), "home.page")
        self.center = QStackedWidget()
        self.center.addWidget(self.home)
        self.center.addWidget(self.view)
        self.setCentralWidget(self.center)
        self._dock_pref: dict = {}
        self._at_home = True
        self._geo_key = None
        self._fit_key = None

        # ---- the panels of the steps
        self.panels = {"geometry": GeometryPanel(engine), "setup": SetupPanel(engine), "mesh": MeshPanel(engine),
                       "solve": SolvePanel(engine), "results": ResultsPanel(engine)}
        self.stack = QStackedWidget()
        for k, _ in STEPS:
            sc = QScrollArea()
            sc.setWidgetResizable(True)
            sc.setFrameShape(QScrollArea.NoFrame)
            sc.setWidget(self.panels[k])
            self.stack.addWidget(sc)
            self.panels[k].changed.connect(self.update_view)

        # ---- docks: outline + step panel on the left, assistant on the right, messages below
        self.tree = wid(QTreeWidget(), "outline.tree")
        self.tree.setHeaderHidden(True)
        self.tree.itemClicked.connect(self._tree_clicked)
        self.dock_tree = self._dock("Outline", self.tree, Qt.LeftDockWidgetArea, "outline")
        self.dock_panel = self._dock("Step", self.stack, Qt.LeftDockWidgetArea, "step")
        self.splitDockWidget(self.dock_tree, self.dock_panel, Qt.Vertical)
        self.assistant = AssistantPanel(bridge)
        self.assistant.toolDone.connect(lambda *_: self.engine.changed.emit())
        self.dock_chat = self._dock("Claude assistant", self.assistant, Qt.RightDockWidgetArea, "assistant")
        self.log = QLabel()   # (messages go to the status bar; kept short)
        self.resizeDocks([self.dock_tree, self.dock_panel], [220, 480], Qt.Vertical)
        self.resizeDocks([self.dock_tree, self.dock_chat], [340, 400], Qt.Horizontal)

        # ---- step bar, status bar
        tb = QToolBar("Steps")
        tb.setObjectName("toolbar.steps")
        tb.toggleViewAction().setObjectName("toolbar.steps.toggle")
        tb.setMovable(False)
        tb.setIconSize(QSize(16, 16))
        self.addToolBar(Qt.TopToolBarArea, tb)
        grp = QActionGroup(self)
        self.step_actions = {}
        for i, (k, label) in enumerate(STEPS, 1):
            a = QAction(f"{i}  {label}", self)
            a.setObjectName(f"step.{k}")
            a.setCheckable(True)
            a.setChecked(k == self.step)
            a.triggered.connect(lambda _=False, k=k: self.go(k))
            grp.addAction(a)
            tb.addAction(a)
            self.step_actions[k] = a
        tb.addSeparator()
        self.chk_air = wid(QCheckBox("Air box"), "view.air_box")
        self.chk_air.setToolTip("Show the box of air round the part (translucent)")
        self.chk_air.toggled.connect(self._air_toggled)
        self.act_air = tb.addWidget(self.chk_air)
        self.act_air.setObjectName("view.air_box.item")
        self.act_air.setVisible(False)
        self._air_pref: dict = {}
        sb = QStatusBar()
        self.setStatusBar(sb)
        self.lbl_saved = wid(QLabel(""), "status.saved")
        self.lbl_path = wid(QLabel(""), "status.path")
        self.progress = wid(QProgressBar(), "status.progress")
        self.progress.setFixedWidth(200)
        self.progress.setRange(0, 1000)
        self.progress.hide()
        sb.addWidget(self.lbl_path, 1)
        sb.addPermanentWidget(self.progress)
        sb.addPermanentWidget(self.lbl_saved)

        self._menus()
        self.home.newStudy.connect(lambda k: self._try(lambda: self.new_from_card(k)))
        self.home.exampleStudy.connect(lambda k, e: self._try(lambda: self.new_from_card(k, e)))
        self.home.openFolder.connect(lambda: self._try(self.open_folder))
        self.home.importFile.connect(lambda: self._try(self.import_zip))
        self.home.openRecent.connect(lambda p: self._try(lambda: self.engine.open_folder(p)))
        for d in (self.dock_tree, self.dock_panel, self.dock_chat):   # (what the user chose to show while working on a study)
            self._dock_pref[d] = True
            d.visibilityChanged.connect(lambda vis, d=d: None if self._at_home else self._dock_pref.__setitem__(d, bool(vis)))
        self.show_home(True)
        engine.opened.connect(self._on_opened)
        engine.changed.connect(self._on_changed)
        engine.job.connect(self._on_job)
        engine.message.connect(lambda t, bad: self.statusBar().showMessage(t, 8000))
        engine.saved.connect(lambda t: self.lbl_saved.setText(f"Saved {t}"))
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self._refresh_all)
        self._title()

    # ------------------------------------------------------------------ building
    def _dock(self, title, widget, area, name=""):
        d = QDockWidget(title, self)
        d.setObjectName(f"dock.{name}" if name else title)
        d.toggleViewAction().setObjectName(f"dock.{name}.toggle" if name else f"{title}.toggle")
        d.setWidget(widget)
        d.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.addDockWidget(area, d)
        return d

    def _menus(self):
        mb = self.menuBar()
        f = mb.addMenu("&File")

        def act(menu, text, fn, shortcut=None, name=""):
            a = QAction(text, self)
            a.setObjectName(name)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            a.triggered.connect(lambda _=False: self._try(fn))
            menu.addAction(a)
            return a
        self.act_new = act(f, "&New study (start screen)", self.new_study, QKeySequence.New, "file.new")
        self.act_open = act(f, "&Open study folder…", self.open_folder, QKeySequence.Open, "file.open_folder")
        self.menu_workspace = f.addMenu("Open from the &workspace")
        self.menu_workspace.setObjectName("menu.workspace")
        self.menu_recent = f.addMenu("Open &recent")
        self.menu_recent.setObjectName("menu.recent")
        self.menu_workspace.aboutToShow.connect(self._fill_workspace)
        self.menu_recent.aboutToShow.connect(self._fill_recent)
        f.addSeparator()
        self.act_save = act(f, "&Save", self.save, QKeySequence.Save, "file.save")
        self.act_saveas = act(f, "Save &as…", self.save_as, "Ctrl+Shift+S", "file.save_as")
        f.addSeparator()
        self.act_export = act(f, "&Export study to one file…", self.export_zip, None, "file.export")
        self.act_import = act(f, "&Import study from a file…", self.import_zip, None, "file.import")
        f.addSeparator()
        self.act_close = act(f, "&Close study", self.engine.close_study, None, "file.close")
        act(f, "E&xit", self.close, "Ctrl+Q", "file.exit")
        v = mb.addMenu("&View")
        for name, key in (("Isometric", "iso"), ("Front (+Y)", "+y"), ("Side (+X)", "+x"), ("Top (+Z)", "+z")):
            a = QAction(name, self)
            a.setObjectName(f"view.{key}")
            a.triggered.connect(lambda _=False, k=key: self.view.set_view(k))
            v.addAction(a)
        a = QAction("Fit to window", self)
        a.setObjectName("view.fit")
        a.setShortcut("F")
        a.triggered.connect(lambda _=False: self.view.fit())
        v.addAction(a)
        v.addSeparator()
        for d in (self.dock_tree, self.dock_panel, self.dock_chat):
            v.addAction(d.toggleViewAction())
        h = mb.addMenu("&Help")
        act(h, "&About", lambda: QMessageBox.about(self, "About", "Mechanical Simulation Workbench (Qt)\nOpen source, GPLv3."), None, "help.about")
        self._gate()

    def _gate(self):
        on = bool(self.engine.pid)
        for a in (self.act_save, self.act_saveas, self.act_export, self.act_close):
            a.setEnabled(on)

    def _try(self, fn):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Cannot do that", str(e))

    # ------------------------------------------------------------------ cases
    def _save_dir(self) -> str:
        return self.engine.settings.value("saveDir", "", type=str) or str(P.WORKSPACE)

    def new_study(self):
        """File > New study: back to the start screen, where the type of study is chosen (the open study is saved first)."""
        if self.engine.pid:
            self.engine.save()
        self.engine.close_study()

    def new_from_card(self, key: str, example: str = ""):
        """A card of the start screen. A bundled example is imported at once. Your own CAD file is asked for first: the study is made
        from it and named after it (cancel the chooser and nothing is created). Studies go in the folder last used for Save as (the
        workspace at first); File > Save as moves one."""
        spec = studies.STUDIES[key]
        if example:
            name = f"{spec['label']} - {example.replace('.step', '').replace('_', ' ')}"
            path = str(P.EXAMPLES / example)
        else:
            path, _ = QFileDialog.getOpenFileName(self, f"CAD file for the {spec['label']} study", "",
                                                  "CAD files (*.step *.stp *.stl *.obj);;All files (*)")
            if not path:
                return
            name = Path(path).stem.replace("_", " ").strip() or spec["label"]
        self.engine.new_study(name, key, self._save_dir())
        self.engine.import_step(path)

    def show_home(self, on: bool):
        """The start screen while no study is open (the working docks and the step bar are put away), the study's views otherwise."""
        self._at_home = on
        docks = (self.dock_tree, self.dock_panel, self.dock_chat)
        bar = self.findChild(QToolBar, "toolbar.steps")
        if on:
            for d in docks:
                d.hide()
            bar.hide()
            self.center.setCurrentWidget(self.home)
            self.home.set_recent(self.engine.recent())
        else:
            self.center.setCurrentWidget(self.view)
            bar.show()
            for d in docks:
                d.setVisible(self._dock_pref.get(d, True))

    def open_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Open a study folder (the one with project.json)", str(P.WORKSPACE))
        if d:
            self.engine.open_folder(d)

    def _fill_workspace(self):
        self.menu_workspace.clear()
        items = self.engine.workspace_list()
        for p in items[:30]:
            a = QAction(f"{p['name']}   ({p.get('study') or ''})", self)
            a.setObjectName(f"workspace.{p['id']}")
            a.triggered.connect(lambda _=False, pid=p["id"]: self._try(lambda: self.engine.open_id(pid)))
            self.menu_workspace.addAction(a)
        if not items:
            self.menu_workspace.addAction("(no studies yet)").setEnabled(False)

    def _fill_recent(self):
        self.menu_recent.clear()
        rec = self.engine.recent()
        for n, r in enumerate(rec):
            a = QAction(f"{r['name']}   —   {r['path']}", self)
            a.setObjectName(f"recent.{n}")
            a.triggered.connect(lambda _=False, p=r["path"]: self._try(lambda: self.engine.open_folder(p)))
            self.menu_recent.addAction(a)
        if rec:
            self.menu_recent.addSeparator()
            c = QAction("Clear the list", self)
            c.setObjectName("recent.clear")
            c.triggered.connect(self.engine.clear_recent)
            self.menu_recent.addAction(c)
        else:
            self.menu_recent.addAction("(nothing opened yet)").setEnabled(False)

    def save(self):
        t = self.engine.save()
        self.statusBar().showMessage(f"Saved at {t} in {self.engine.project().dir}", 6000)

    def _ask_results(self) -> bool | None:
        b = QMessageBox(self)
        b.setWindowTitle("Include the results?")
        b.setText("Include the solver case, results and videos?\nWithout them the file is much smaller; the mesh is kept and you can run again.")
        yes = b.addButton("Include them", QMessageBox.AcceptRole)
        no = b.addButton("Leave them out", QMessageBox.DestructiveRole)
        b.addButton(QMessageBox.Cancel)
        b.exec()
        if b.clickedButton() is yes:
            return True
        if b.clickedButton() is no:
            return False
        return None

    def save_as(self):
        keep = self._ask_results()
        if keep is None:
            return
        d = QFileDialog.getExistingDirectory(self, "Folder to save a copy in", str(P.WORKSPACE))
        if not d:
            return
        cur = self.engine.state()["name"]
        name, ok = QInputDialog.getText(self, "Save as", "Name of the new study:", text=f"{cur} copy")
        if ok and name.strip():
            self.engine.settings.setValue("saveDir", d)       # (new studies go where the last copy went)
            self.engine.save_as(d, name.strip(), keep)

    def export_zip(self):
        keep = self._ask_results()
        if keep is None:
            return
        cur = self.engine.state()["name"]
        path, _ = QFileDialog.getSaveFileName(self, "Export the study to one file", str(Path.home() / f"{cur}.simwb.zip"), "Study file (*.zip)")
        if path:
            out = self.engine.export_zip(path, keep)
            self.statusBar().showMessage(f"Exported to {out}", 8000)

    def import_zip(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import a study file", "", "Study file (*.zip)")
        if path:
            self.engine.import_zip(path)

    # ------------------------------------------------------------------ events from the engine
    def _on_opened(self, pid: str):
        self.show_home(not pid)
        self._geo_key = None
        self.assistant.set_project(pid)
        self._gate()
        self._title()
        self.view.clear_overlay()
        if pid:
            st = self.engine.state()
            self.go("results" if st["solve"].get("status") == "done" else "mesh" if st["mesh"].get("status") == "ready"
                    else "geometry" if st["geometry"].get("status") != "ready" else "setup")
        else:
            self.view.set_geometry([])

    def _on_changed(self):
        self._refresh_timer.start(80)     # (bursts of changes make one redraw)

    def _on_job(self, ev: dict):
        j = ev.get("job") or {}
        self.panels["solve"].on_job(ev)
        running = j.get("status") == "running"
        self.progress.setVisible(running)
        if running:
            self.progress.setValue(int(float(j.get("progress", 0)) * 1000))
            self.statusBar().showMessage(f"{j.get('kind', '')}: {j.get('message', '')}")
        elif j.get("status") in ("failed",):
            QMessageBox.warning(self, "The job failed", str(j.get("error") or j.get("message")))
        elif j.get("status") == "done":
            self.statusBar().showMessage(f"{j.get('kind', '')} done", 6000)

    def _hover_tip(self, fid: int):
        """The face under the pointer, in the status bar (the web version showed it as a tooltip)."""
        if fid < 0 or not self.engine.pid:
            self.statusBar().clearMessage()
            return
        f = self.engine.project().face_map().get(fid)
        if f:
            self.statusBar().showMessage(f"Face {fid}: {f.get('surface_type', '')}, {f.get('area', 0) * 1e6:.1f} mm\u00b2")

    def _air_toggled(self, on: bool):
        if self.engine.pid:
            self._air_pref[self.engine.pid] = bool(on)
        self.view.set_air_visible(on)
        self.view.fit()          # (the box is far bigger than the part: fit the view to what is shown now)

    def _refresh_all(self):
        st = self.engine.state() if self.engine.pid else None
        self.act_air.setVisible(bool(st) and st["study"] in AIR_KINDS)
        self._title(st)
        self._fill_tree(st)
        for p in self.panels.values():
            p.refresh(st)
        self.update_view()

    # ------------------------------------------------------------------ steps
    def go(self, step: str):
        self.step = step
        self.step_actions[step].setChecked(True)
        self.stack.setCurrentIndex([k for k, _ in STEPS].index(step))
        self.assistant.set_context(step)
        self.update_view()

    def _tree_clicked(self, item, _col=0):
        s = item.data(0, Qt.UserRole)
        if s:
            self.go(s)

    def _title(self, st=None):
        if self.engine.pid:
            st = st or self.engine.state()
            self.setWindowTitle(f"{st['name']} — Simulation Workbench")
            self.lbl_path.setText(str(st.get("path", "")))
        else:
            self.setWindowTitle("Simulation Workbench")
            self.lbl_path.setText("No study open: File > New study, or Open")

    def _fill_tree(self, st):
        self.tree.clear()
        if not st:
            return

        def node(parent, text, step=None):
            it = QTreeWidgetItem(parent, [text])
            if step:
                it.setData(0, Qt.UserRole, step)
            return it
        root = node(self.tree, f"{st['name']}")
        root.setExpanded(True)
        g = node(root, f"Geometry ({len(st['body_list'])} bod{'y' if len(st['body_list']) == 1 else 'ies'})", "geometry")
        for b in st["body_list"]:
            node(g, b["name"], "geometry")
        m = node(root, "Materials", "setup")
        for b in st["body_list"]:
            node(m, f"{b['name']} — {b.get('material_label') or b.get('material') or 'default'}", "setup")
        s = node(root, "Supports & loads", "setup")
        for b in st.get("bcs") or []:
            node(s, f"{b['name']} — {b['type'].replace('_', ' ')}", "setup")
        ms = st["mesh"]
        node(root, f"Mesh — {ms['stats']['cells']:,} cells" if ms.get("stats") and ms.get("status") in ("ready", "stale") else "Mesh — none", "mesh")
        node(root, f"Solution — {st['solve'].get('status', 'none')}", "solve")
        r = node(root, "Results", "results")
        if st["solve"].get("status") == "done":
            try:
                for f in self.engine.result_fields():
                    node(r, f"{f['name']} ({f['unit']})", "results")
            except Exception:  # noqa: BLE001
                pass
        self.tree.expandAll()

    # ------------------------------------------------------------------ the 3D view
    def _fit_once(self, key, look=None):
        """Fit the camera to what is drawn the first time a view (study, step) is shown, not on every change. look: a view
        direction name (+x, +y, +z) to turn to, for a cut that should be seen straight on."""
        if key != self._fit_key:
            self._fit_key = key
            self.view.set_view(look) if look else self.view.fit()

    def update_view(self):
        if not self.engine.pid:
            return
        st = self.engine.state()
        try:
            if self.step == "mesh" and st["mesh"].get("status") in ("ready", "stale"):
                axis, frac, color = self.panels["mesh"].request()
                p = self.engine.mesh_payload(axis, frac, color)
                self.view.show_geometry(False)
                self.view.show_surface(p, name={"quality": "Cell quality", "size": "Cell size", "region": "Region"}[color],
                                       unit="mm" if color == "size" else "-", edges=True, fixed_quality=color == "quality",
                                       vrange=(0, 1) if color == "quality" else None, deform=0)
                self._fit_once((self.engine.pid, "mesh", axis), f"+{axis}" if axis else None)
                return
            if self.step == "results" and st["solve"].get("status") in ("done", "stale"):
                r = self.panels["results"].request()
                if r["field"]:
                    p = self.engine.result_surface(r["field"])
                    info = next((f for f in self.panels["results"].fields if f["name"] == r["field"]), {})
                    self.view.show_geometry(False)
                    self.view.show_surface(p, name=r["field"], unit=info.get("unit", ""), cmap=r["cmap"], reverse=r["reverse"],
                                           bands=r["bands"], edges=r["edges"], deform=r["deform"])
                    self._fit_once((self.engine.pid, "results"))
                    return
            self.view.clear_overlay()
            key = (self.engine.pid, st["geometry"].get("status"), st["geometry"].get("updated"))
            if key != self._geo_key:
                self._geo_key = key
                kinds = AIR_KINDS.get(st["study"], ())
                on = self._air_pref.get(self.engine.pid, st["study"] == "cht_enclosure")
                self.view.set_geometry(self.engine.project().faces() if st["geometry"].get("status") == "ready" else [], kinds, on)
                self.chk_air.blockSignals(True)
                self.chk_air.setChecked(on)
                self.chk_air.blockSignals(False)
                self.view.fit()
            self.view.show_geometry(True)
        except Exception as e:  # noqa: BLE001
            self.statusBar().showMessage(f"Cannot draw: {e}", 10000)

    # ------------------------------------------------------------------ pictures (for checking the look)
    def screenshot(self, path: str):
        """The whole window, with the 3D view put in (a widget grab leaves the GL surface out)."""
        img = self.grab().toImage()
        if self.view.isVisible():
            vp = self.view.image()
            p = QPainter(img)      # (a painter on a high-DPI image works in logical coordinates)
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            p.drawImage(QRect(self.view.iren.mapTo(self, QPoint(0, 0)), self.view.iren.size()), vp)
            p.end()
        img.save(path, "PNG")

    def closeEvent(self, e):  # noqa: N802
        try:
            if self.engine.pid:
                self.engine.save()
        finally:
            if getattr(self, "control", None) is not None:
                self.control.close()
            self.bridge.shutdown()
            self.view.close()
        super().closeEvent(e)
