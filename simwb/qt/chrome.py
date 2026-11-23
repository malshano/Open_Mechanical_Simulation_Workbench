"""The application bar: the study switcher and New on the left, Simple/Expert, Jobs, the assistant and Close on the right of the step bar.

Same things as the web version's top bar. Every control has an id (chrome.*) and a label."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QResizeEvent
from PySide6.QtWidgets import (QApplication, QComboBox, QHBoxLayout, QMenu, QMessageBox, QPushButton, QSizePolicy, QToolButton, QWidget)

from .. import project as P
from . import mode, theme
from .ids import wid


def install(win, bar):
    """Add the bar's controls to the step toolbar `bar` of `win`."""
    first = bar.actions()[0]
    # --- left: which study, and a new one
    box = QWidget()
    win._chrome_studies = box
    row = QHBoxLayout(box)
    row.setContentsMargins(0, 0, 8, 0)
    row.setSpacing(6)
    win.project_box = wid(QComboBox(), "chrome.project")
    win.project_box.setMinimumContentsLength(14)
    win.project_box.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    win.project_box.setToolTip("Switch to another study")
    win.project_box.activated.connect(lambda i: _switch(win, i))
    new = wid(QPushButton("＋ New"), "chrome.new")
    new.setObjectName("ghostBorder")
    new.setToolTip("Start another study (back to the start screen)")
    new.clicked.connect(win.new_study)
    row.addWidget(win.project_box)
    row.addWidget(new)
    a1 = bar.insertWidget(first, box)
    a1.setObjectName("chrome.studies.item")
    a2 = bar.insertSeparator(first)
    a2.setObjectName("chrome.sep")
    # --- right, in the menu bar's free corner (there is room for it at any window width): mode, jobs, assistant, close
    corner = QWidget()
    cr = QHBoxLayout(corner)
    cr.setContentsMargins(0, 0, 8, 0)
    cr.setSpacing(8)
    seg = QWidget()
    sr = QHBoxLayout(seg)
    sr.setContentsMargins(0, 0, 0, 0)
    sr.setSpacing(0)
    win.mode_btns = {}
    for key, label, tip in (("simple", "Simple", "Only the essential settings, and a hint for the next step"),
                            ("expert", "Expert", "Every setting and tool (advanced meshing, solver numerics, case files, extra checks)")):
        b = wid(QPushButton(label), f"chrome.mode.{key}")
        b.setObjectName("seg")
        b.setCheckable(True)
        b.setChecked((key == "simple") == mode.simple())
        b.setToolTip(tip)
        b.clicked.connect(lambda _=False, k=key: mode.set_simple(k == "simple"))
        sr.addWidget(b)
        win.mode_btns[key] = b
    tb = wid(QPushButton(), "chrome.theme")                     # (the dark / light switch: it says what a click does)
    tb.setObjectName("ghostBorder")

    def theme_label(*_):
        dark = theme.effective() == "dark"
        tb.setText("☀ Light mode" if dark else "☾ Dark mode")
        tb.setToolTip(("Switch to the light theme" if dark else "Switch to the dark theme") + " (View > Theme also has 'Follow the system')")
        tb.setAccessibleName("Switch to the light theme" if dark else "Switch to the dark theme")
    tb.clicked.connect(lambda: win.set_theme("light" if theme.effective() == "dark" else "dark"))
    theme.bus.changed.connect(theme_label)
    theme_label()
    win.theme_btn = tb
    cr.addWidget(tb)
    cr.addWidget(seg)
    mode.bus.changed.connect(lambda s: (win.mode_btns["simple"].setChecked(s), win.mode_btns["expert"].setChecked(not s),
                                        win.engine.settings.setValue("mode", "simple" if s else "expert"), _on_mode(win)))
    win.jobs_btn = wid(QToolButton(), "chrome.jobs")
    win.jobs_btn.setPopupMode(QToolButton.InstantPopup)
    win.jobs_btn.setToolTip("Jobs running in all studies: stop them here (the status bar also has Cancel for the study you are in)")
    win.jobs_menu = QMenu(win.jobs_btn)
    win.jobs_btn.setMenu(win.jobs_menu)
    win.jobs_menu.aboutToShow.connect(lambda: _fill_jobs(win))
    cr.addWidget(win.jobs_btn)
    chat = wid(QPushButton("✦ Claude"), "chrome.claude")
    chat.setCheckable(True)
    chat.setChecked(True)
    chat.setObjectName("ghostBorder")
    chat.setToolTip("Show or hide the Claude assistant (Ctrl+K: write to Claude)")
    chat.toggled.connect(lambda on: win.dock_chat.setVisible(on) if not win._at_home else None)
    win.dock_chat.visibilityChanged.connect(lambda v: chat.setChecked(v) if not win._at_home else None)
    cr.addWidget(chat)
    close = wid(QPushButton("⏻ Close"), "chrome.close")
    close.setObjectName("ghostBorder")
    close.setToolTip("Close the workbench (running jobs are stopped)")
    close.clicked.connect(lambda: _close(win))
    cr.addWidget(close)
    corner.setParent(win.menuBar())
    win.menuBar().setCornerWidget(corner, Qt.TopRightCorner)
    win._chrome_corner = corner              # (kept alive: PySide would delete an unreferenced widget when this function returns)
    corner.show()
    # the jobs count follows the jobs
    t = QTimer(win)
    t.timeout.connect(lambda: _count(win))
    t.start(1000)
    win.engine.opened.connect(lambda _pid: _studies(win))
    win.engine.changed.connect(lambda: _studies(win))
    _studies(win)
    _count(win)


def _on_mode(win):
    for p in win.panels.values():
        p._sig = None
    win._refresh_all()


def _studies(win):
    """The switcher lists the studies of the workspace; the open one is chosen."""
    box = win.project_box
    box.blockSignals(True)
    box.clear()
    box.addItem("(start screen)", None)
    cur = 0
    for p in win.engine.workspace_list():
        box.addItem(p["name"], p["id"])
        if p["id"] == win.engine.pid:
            cur = box.count() - 1
    box.setCurrentIndex(cur)
    box.blockSignals(False)


def _switch(win, i):
    pid = win.project_box.itemData(i)
    try:
        if pid is None:
            win.new_study()
        elif pid != win.engine.pid:
            win.engine.save() if win.engine.pid else None
            win.engine.open_id(pid)
    except Exception as e:  # noqa: BLE001
        win.toasts.error(f"Cannot open it: {e}")


def running():
    return [j for j in list(P.JOBS.jobs.values()) if j.status == "running"]


def _count(win):
    n = len(running())
    text = f"⚙ Jobs ({n})" if n else "⚙ Jobs"
    if win.jobs_btn.text() != text:
        win.jobs_btn.setText(text)
        corner = getattr(win, "_chrome_corner", None)
        if corner is not None:                              # (the corner is as wide as its parts now: Simple / Expert are not squeezed)
            corner.updateGeometry()
            QApplication.sendEvent(win.menuBar(), QResizeEvent(win.menuBar().size(), win.menuBar().size()))   # (the bar places its corner again)


def _fill_jobs(win):
    m = win.jobs_menu
    m.clear()
    js = running()
    if not js:
        m.addAction("No job is running").setEnabled(False)
        return
    for j in js:
        a = QAction(f"{j.project}: {j.kind} {int(j.progress * 100)} %  —  Stop", win)
        a.setObjectName(f"jobs.stop.{j.id}")
        a.triggered.connect(lambda _=False, jid=j.id: P.JOBS.cancel(jid))
        m.addAction(a)


def _close(win):
    n = len(running())
    if n:
        r = QMessageBox.question(win, "Close the workbench", f"{n} job(s) are still running and will be stopped. Close anyway?")
        if r != QMessageBox.Yes:
            return
        for j in running():
            P.JOBS.cancel(j.id)
    win.close()
