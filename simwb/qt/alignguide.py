"""The guided popup of "Align a face to a plane...": a card laid over the top of the 3D view with the three steps of the job - 1 click the
surface to align, 2 click the side, plane or face to align it with (any face of the model, or a wall of the air volume = the control
volume), 3 check the preview and Apply. The current step is big, finished steps carry a tick and what was chosen, and Back / Cancel / Apply
are in the card, so the person never has to look elsewhere to know what to do next.

It is a native child window of the 3D view (a plain child widget would be painted UNDER the OpenGL surface), raised above it."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QVBoxLayout

from .. import orient as engine_orient
from . import theme
from .ids import wid

STEP_TEXT = {
    1: "Click the surface you want to align",
    2: "Click the side, plane or face to align it with: any face of the model, or a wall of the air volume (control volume)",
    3: "Check the preview, then Apply",
}


def _minus(t: str) -> str:
    return t.replace("-", "−")


class AlignGuide(QFrame):
    def __init__(self, ctrl, view):
        super().__init__(view)
        self.c, self.view = ctrl, view
        self.setAttribute(Qt.WA_NativeWindow, True)               # (a native window: it is stacked above the OpenGL surface, not under it)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setObjectName("alignGuide")
        wid(self, "align.guide")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setSpacing(6)
        self.title = QLabel("⬓  Align a face to a plane")
        self.title.setObjectName("alignGuideTitle")
        lay.addWidget(self.title)
        self.rows = []
        for n in (1, 2, 3):
            lab = wid(QLabel(""), f"align.guide.step{n}")
            lab.setWordWrap(True)
            lab.setTextFormat(Qt.RichText)
            self.rows.append(lab)
            lay.addWidget(lab)
        self.extra = QFrame()                                      # the ways to choose the plane (step 2)
        ex = QHBoxLayout(self.extra)
        ex.setContentsMargins(24, 0, 0, 0)
        ex.setSpacing(6)
        self.b_plane = wid(QPushButton("Coordinate plane…"), "align.guide.plane")
        self.b_plane.setToolTip("Lay the surface on a coordinate plane (XY, YZ or XZ), facing either way")
        self.b_wall = wid(QPushButton("Air volume wall…"), "align.guide.wall")
        self.b_wall.setToolTip("Lay the surface against a wall of the air volume (the control volume), chosen by its direction")
        self.b_face = wid(QPushButton("Another face - just click it"), "align.guide.face")
        self.b_face.setToolTip("Click any face of the model in the 3D view: the surface is laid against it")
        for b in (self.b_plane, self.b_wall, self.b_face):
            b.setObjectName("ghostBorder")
            ex.addWidget(b)
        self.b_face.clicked.connect(lambda: self._try(self.c.start_ref_pick))
        lay.addWidget(self.extra)
        row = QHBoxLayout()
        self.b_back = wid(QPushButton("◀ Back"), "align.guide.back")
        self.b_back.setObjectName("ghostBorder")
        self.b_back.clicked.connect(lambda: self._try(self.c.back))
        self.b_cancel = wid(QPushButton("Cancel"), "align.guide.cancel")
        self.b_cancel.setObjectName("ghostBorder")
        self.b_cancel.setToolTip("Stop aligning: the model stays as it was")
        self.b_cancel.clicked.connect(lambda: self._try(self.c.p.orient.cancel))
        self.b_apply = wid(QPushButton("Apply"), "align.guide.apply")
        self.b_apply.setObjectName("primary")
        self.b_apply.setToolTip("Rebuild the geometry turned like the preview (takes a moment)")
        self.b_apply.clicked.connect(lambda: self._try(self.c.p.orient.apply))
        row.addWidget(self.b_back)
        row.addStretch(1)
        row.addWidget(self.b_cancel)
        row.addWidget(self.b_apply)
        lay.addLayout(row)
        self.hide()
        theme.bus.changed.connect(lambda _: self.refresh())

    def _try(self, fn):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            self.c.p.fail(e)

    # ------------------------------------------------------------------ what it says
    def texts(self) -> list:
        """The three step lines as plain text (for the control API and the tests)."""
        return [r.property("plain") or "" for r in self.rows]

    def _chosen(self, n: int) -> str:
        c = self.c
        if n == 1 and c.face is not None:
            f = c.face
            kind = ("curved, average direction" if c.mode == "average" else "curved, normal at the click") if f.get("curved") else "flat"
            return f"Surface: face {f['face']} ({kind})"
        if n == 2:
            if c.ref is not None:
                lab = c.ref["label"]
                how = ("facing away" if c.ref["away"] else "facing the wall") if c.ref["info"]["kind"] == "wall" else ("face to face" if c.ref["away"] else "same direction")
                return f"Plane: {lab[:1].upper() + lab[1:]}, {how}"
            if c.target:
                return f"Plane: coordinate plane facing {_minus(c.target)}"
        return ""

    def refresh(self):
        c = self.c
        step = c.step()
        t = theme.tokens()
        host_ok = self.view.isVisible() or True
        if step == 0 or not host_ok:
            self.hide()
            return
        self.setStyleSheet(
            f"QFrame#alignGuide {{ background: {t['panel']}; border: 2px solid {t['accent']}; border-radius: 12px; }}"
            f"QLabel#alignGuideTitle {{ background: {t['panel']}; color: {t['accent_text']}; font-weight: 700; font-size: 13px; }}")
        for n, lab in enumerate(self.rows, 1):
            if n < step:
                done = self._chosen(n)
                html = f"<span style='color:{t['ok_text']}'><b>✓</b></span> Step {n} of 3 - {done}"
                plain = f"Step {n} of 3 - done. {done}"
                css = f"background: {t['panel']}; color: {t['ink']};"
            elif n == step:
                html = f"<span style='font-size:15px'><b>▶ Step {n} of 3 - {STEP_TEXT[n]}</b></span>"
                plain = f"Step {n} of 3 - {STEP_TEXT[n]}"
                css = f"background: {t['accent_soft']}; color: {t['ink']}; padding: 6px; border-radius: 6px;"
            else:
                html = f"Step {n} of 3 - {STEP_TEXT[n].split(':')[0] if n == 2 else STEP_TEXT[n]}"
                plain = f"Step {n} of 3 - {STEP_TEXT[n]}"
                css = f"background: {t['panel']}; color: {t['muted']};"
            lab.setText(html)
            lab.setProperty("plain", plain)
            lab.setProperty("api_text", plain)
            lab.setStyleSheet(css)
        self.extra.setVisible(step == 2)
        if step == 2:
            self._menus()
            self.b_face.setCheckable(True)
            self.b_face.setChecked(self.c.ref_pick)
        self.b_back.setVisible(step >= 2)
        self.b_back.setToolTip("Back to step 1: choose another surface" if step == 2 else "Back to step 2: choose another plane")
        self.b_apply.setVisible(step == 3)
        self.b_apply.setEnabled(self.c.p.orient.pending)
        self.show()
        self.place()

    def _menus(self):
        c = self.c
        m = QMenu(self.b_plane)
        for plane, targets in (("XY plane", ("+z", "-z")), ("YZ plane", ("+x", "-x")), ("XZ plane", ("+y", "-y"))):
            for tg in targets:
                a = m.addAction(f"{plane}, facing {_minus(tg)}")
                a.setObjectName(f"align.guide.plane.{tg}")
                a.triggered.connect(lambda _=False, tg=tg: self._try(lambda: c.choose_plane(tg)))
        self.b_plane.setMenu(m)
        walls = c.p.engine.project().air_walls()
        self.b_wall.setEnabled(bool(walls))
        self.b_wall.setToolTip("Lay the surface against a wall of the air volume (the control volume), chosen by its direction" if walls else
                               "This study has no air volume: click a face of the model, or use a coordinate plane")
        if walls:
            w = QMenu(self.b_wall)
            for wl in walls:
                a = w.addAction(f"Air volume: {wl['name']}")
                a.setObjectName(f"align.guide.wall.{wl['face']}")
                a.triggered.connect(lambda _=False, f=wl["face"], pt=wl.get("point"): self._try(lambda: c.choose_ref_face(f, pt, "wall")))
            self.b_wall.setMenu(w)
        else:
            self.b_wall.setMenu(None)

    # ------------------------------------------------------------------ where it is
    def place(self):
        """Top centre of the 3D picture, as wide as there is room for (up to 580 px)."""
        iren = self.view.iren
        w = max(260, min(580, iren.width() - 24))
        self.setFixedWidth(w)
        self.setFixedHeight(max(self.layout().totalHeightForWidth(w), self.layout().minimumSize().height()))
        x = iren.x() + (iren.width() - w) // 2
        self.move(max(0, x), iren.y() + 10)
        self.raise_()

    def describe(self) -> dict:
        return {"visible": self.isVisible(), "step": self.c.step(), "texts": self.texts(),
                "geometry": [self.x(), self.y(), self.width(), self.height()],
                "buttons": {k: b.isVisible() and b.isEnabled() for k, b in (("back", self.b_back), ("cancel", self.b_cancel), ("apply", self.b_apply),
                                                                           ("plane", self.b_plane), ("wall", self.b_wall), ("face", self.b_face))}}
