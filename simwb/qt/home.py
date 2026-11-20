"""The start screen: what do you want to simulate? One card per study type (what it does, how it is solved, your own CAD file or a
bundled example), the studies opened lately, and Open / Import. Shown while no study is open."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from .. import studies
from .ids import wid

ICONS = {"thermo": "🌡️", "fan": "🌀", "drone": "🚁", "wave": "📳", "beam": "🏗️", "flutter": "✈️"}
ACCENT = {"thermo": "#e4572e", "wave": "#8b5cf6", "flutter": "#3b82f6", "beam": "#10b981", "fan": "#06b6d4", "drone": "#f59e0b"}


def elide_middle(text: str, n: int) -> str:
    """Long names keep their start and their end (the end is what tells two studies apart)."""
    if len(text) <= n:
        return text
    keep = n - 1
    return text[:keep - keep // 3] + "…" + text[-(keep // 3):]


class Card(QFrame):
    """One study type."""

    def __init__(self, key: str, spec: dict, on_new, on_example):
        super().__init__()
        self.key = key
        self.setObjectName("card")
        accent = ACCENT.get(spec.get("icon"), "#3b82f6")
        self.setStyleSheet(f"QFrame#card {{ border-top: 3px solid {accent}; }} QFrame#card:hover {{ border-color: {accent}; }}")
        self.setAttribute(Qt.WA_StyledBackground, True)
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(8)
        top = QHBoxLayout()
        ico = QLabel(ICONS.get(spec.get("icon"), "◆"))
        ico.setObjectName("cardIcon")
        ico.setFixedSize(46, 46)
        ico.setAlignment(Qt.AlignCenter)
        ico.setStyleSheet(f"background: {accent}33; border-radius: 12px; font-size: 24px;")
        title = QLabel(spec["label"])
        title.setObjectName("cardTitle")
        title.setWordWrap(True)
        top.addWidget(ico)
        top.addSpacing(8)
        top.addWidget(title, 1)
        v.addLayout(top)
        if spec.get("physics"):      # a coupled study: the physics it joins
            chips = QHBoxLayout()
            chips.setSpacing(6)
            for i, p in enumerate(spec["physics"]):
                c = QLabel(p.split(":")[0])
                c.setObjectName("chipLabel")
                chips.addWidget(c)
                if i < len(spec["physics"]) - 1:
                    chips.addWidget(QLabel("⇄"))
            chips.addStretch(1)
            v.addLayout(chips)
        d = QLabel(spec.get("description", ""))
        d.setObjectName("muted")
        d.setWordWrap(True)
        v.addWidget(d)
        builtin = spec.get("solver") == "builtin_fem"
        badge = QLabel("Built-in solver · seconds to minutes" if builtin else "OpenFOAM flow simulation · minutes to hours")
        badge.setObjectName("badge")
        badge.setToolTip("Solved by the workbench itself: nothing else to install" if builtin
                         else "Flow simulation with OpenFOAM, set up by the workbench on the first run")
        v.addWidget(badge, 0, Qt.AlignLeft)
        v.addStretch(1)
        row = QHBoxLayout()
        row.setSpacing(8)
        b = wid(QPushButton("Use my CAD file"), f"home.new.{key}")
        b.setObjectName("primary")
        b.clicked.connect(lambda _=False: on_new(key))
        row.addWidget(b)
        exs = spec.get("examples") or []
        for i, e in enumerate(exs):
            eb = wid(QPushButton(("Example: " + e.replace(".step", "").replace("_", " ")) if len(exs) > 1 else "Try example"),
                     f"home.example.{key}.{i}")
            eb.setObjectName("ghostBorder")
            eb.clicked.connect(lambda _=False, e=e: on_example(key, e))
            row.addWidget(eb)
        row.addStretch(1)
        v.addLayout(row)


class HomePage(QWidget):
    newStudy = Signal(str)              # a study type: ask for a name and folder, then the CAD file
    exampleStudy = Signal(str, str)     # a study type and a bundled example
    openFolder = Signal()
    importFile = Signal()
    openRecent = Signal(str)            # a study folder

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setObjectName("home")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        sc = QScrollArea()
        self.scroll = sc
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.NoFrame)
        outer.addWidget(sc)
        body = QWidget()
        body.setObjectName("homeBody")
        sc.setWidget(body)
        self.v = QVBoxLayout(body)
        self.v.setContentsMargins(48, 36, 48, 36)
        self.v.setSpacing(14)
        h = QLabel("What do you want to simulate?")
        h.setObjectName("homeTitle")
        s = QLabel("Choose a study type, then pick your CAD file or try a bundled example.")
        s.setObjectName("muted")
        self.v.addWidget(h)
        self.v.addWidget(s)

        bar = QHBoxLayout()
        self.btn_open = wid(QPushButton("Open a saved study…"), "home.open_folder")
        self.btn_import = wid(QPushButton("Import a study file…"), "home.import")
        self.btn_open.clicked.connect(self.openFolder)
        self.btn_import.clicked.connect(self.importFile)
        bar.addWidget(self.btn_open)
        bar.addWidget(self.btn_import)
        bar.addStretch(1)
        self.v.addLayout(bar)

        self.recent_box = QWidget()
        self.recent_lay = QVBoxLayout(self.recent_box)
        self.recent_lay.setContentsMargins(0, 6, 0, 0)
        self.recent_lay.setSpacing(6)
        self.v.addWidget(self.recent_box)

        self.cards: list = []
        spec = studies.STUDIES
        single = [(k, v) for k, v in spec.items() if v.get("category") != "multiphysics"]
        multi = [(k, v) for k, v in spec.items() if v.get("category") == "multiphysics"]
        self.grids = []
        for title, items, note in (("Studies", single, ""), ("Multiphysics", multi,
                                   "Several kinds of physics solved together: the result of one changes the other.")):
            if not items:
                continue
            t = QLabel(title.upper())
            t.setObjectName("h")
            self.v.addSpacing(8)
            self.v.addWidget(t)
            if note:
                n = QLabel(note)
                n.setObjectName("muted")
                self.v.addWidget(n)
            grid_host = QWidget()
            g = QGridLayout(grid_host)
            g.setContentsMargins(0, 0, 0, 0)
            g.setSpacing(16)
            cards = [Card(k, v, self.newStudy.emit, self.exampleStudy.emit) for k, v in items]
            self.cards += cards
            self.grids.append((g, cards))
            self.v.addWidget(grid_host)
        self.v.addStretch(1)
        self._cols = 0
        self.set_recent([])

    def to_top(self):
        self.scroll.verticalScrollBar().setValue(0)

    def set_recent(self, recent: list):
        while self.recent_lay.count():
            w = self.recent_lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.recent_box.setVisible(bool(recent))
        if not recent:
            return
        t = QLabel("RECENT STUDIES")
        t.setObjectName("h")
        self.recent_lay.addWidget(t)
        from pathlib import Path
        shown = recent[:6]
        names = [r["name"] for r in shown]
        r = None
        for i, rec in enumerate(shown):
            if i % 3 == 0:                                   # (three to a row: the row never runs off the window)
                row = QWidget()
                r = QHBoxLayout(row)
                r.setContentsMargins(0, 0, 0, 0)
                r.setSpacing(8)
                self.recent_lay.addWidget(row)
            label = rec["name"] if names.count(rec["name"]) == 1 else f"{rec['name']}  ({Path(rec['path']).name})"
            b = wid(QPushButton(elide_middle(label, 34)), f"home.recent.{i}")
            b.setObjectName("chip")
            b.setToolTip(f"{label}\n{rec['path']}")
            b.clicked.connect(lambda _=False, p=rec["path"]: self.openRecent.emit(p))
            r.addWidget(b)
            if i % 3 == 2 or i == len(shown) - 1:
                r.addStretch(1)

    def resizeEvent(self, e):  # noqa: N802
        """As many columns of cards as fit: the cards re-flow when the window is resized."""
        super().resizeEvent(e)
        cols = max(1, min(3, (self.width() - 96) // 340))
        if cols == self._cols:
            return
        self._cols = cols
        for g, cards in self.grids:
            for c in cards:
                g.removeWidget(c)
            for i, c in enumerate(cards):
                g.addWidget(c, i // cols, i % cols)
            for k in range(cols):
                g.setColumnStretch(k, 1)
