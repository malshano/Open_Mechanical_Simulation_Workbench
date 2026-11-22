"""Toasts: small notices over the corner of the window that do not wait for a click ("Saved", "Mesh ready", "Cannot do that: ...").

They replace the message boxes that only inform: a box in the way of the work, to be closed before the next click, for a thing that needs no
answer. A toast appears at the top right, under the bars, stays a few seconds (an error longer; resting the pointer on it keeps it), fades away and can be
closed with its x at once. They stack (at most five), and every one is also written to the log, so nothing is lost
when it fades. Real questions (close while a job runs, include the results?) stay dialogs."""
from __future__ import annotations

import itertools
import logging
import time

from PySide6.QtCore import QEvent, QObject, QPoint, QPropertyAnimation, Qt, QTimer
from PySide6.QtWidgets import (QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget)

from . import theme
from .ids import wid

log = logging.getLogger("simwb.qt")
KINDS = ("info", "ok", "warn", "error")
WIDTH = 380
MAX_SHOWN = 5
SECONDS = {"info": 4.5, "ok": 4.5, "warn": 7.0, "error": 9.0}
_ids = itertools.count(1)


class Toast(QFrame):
    def __init__(self, host: "ToastHost", text: str, kind: str, seconds: float):
        super().__init__(host.box)
        self.host, self.kind, self.text, self.n = host, kind, text, next(_ids)
        self.setObjectName("toast")
        self.setProperty("kind", kind)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedWidth(WIDTH)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 8, 6, 8)
        row.setSpacing(8)
        self.label = QLabel(text)
        self.label.setObjectName("toastText")
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.label.setMinimumWidth(0)
        self.close_btn = wid(QPushButton("✕"), "toast.close")           # (one id for the close button of whichever toast is newest)
        self.close_btn.setObjectName("ghost")
        self.close_btn.setFixedSize(26, 26)
        self.close_btn.setToolTip("Close this notice")
        self.close_btn.setAccessibleName("Close this notice")
        self.close_btn.clicked.connect(self.dismiss)
        row.addWidget(self.label, 1)
        row.addWidget(self.close_btn, 0, Qt.AlignTop)
        self.setAccessibleName(f"{kind}: {text}")
        self.label.setFixedWidth(WIDTH - 12 - 6 - 8 - 26 - 4)
        self.setFixedHeight(max(40, self.label.heightForWidth(self.label.width()) + 16))     # (a wrapped label: the height is told, not guessed)
        self.fx = QGraphicsOpacityEffect(self)
        self.fx.setOpacity(1.0)
        self.setGraphicsEffect(self.fx)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.fade)
        self.seconds = seconds
        self.anim = None
        self.gone = False
        self.timer.start(int(seconds * 1000))

    def enterEvent(self, e):  # noqa: N802
        self.timer.stop()                      # (the pointer is on it: it is being read)
        if self.anim is not None:
            self.anim.stop()
            self.fx.setOpacity(1.0)
            self.anim = None
        super().enterEvent(e)

    def leaveEvent(self, e):  # noqa: N802
        if not self.gone:
            self.timer.start(1800)
        super().leaveEvent(e)

    def fade(self):
        if self.gone:
            return
        self.anim = QPropertyAnimation(self.fx, b"opacity", self)
        self.anim.setDuration(450)
        self.anim.setStartValue(1.0)
        self.anim.setEndValue(0.0)
        self.anim.finished.connect(self.dismiss)
        self.anim.start()

    def dismiss(self):
        if self.gone:
            return
        self.gone = True
        self.timer.stop()
        self.host._removed(self)


class ToastHost(QObject):
    """Owns the stack of toasts of one window. `show` is the one call: toasts.show("Saved", "ok")."""

    def __init__(self, win: QWidget):
        super().__init__(win)
        self.win = win
        self.box = QWidget(win)
        self.box.setObjectName("toastBox")
        self.box.setAttribute(Qt.WA_TranslucentBackground, True)
        lay = QVBoxLayout(self.box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.setAlignment(Qt.AlignBottom)
        self.lay = lay
        self.items: list[Toast] = []
        self.history: list[dict] = []          # every toast ever shown (kind, text, time): the log's short form, for the control API
        self.box.hide()
        win.installEventFilter(self)
        theme.bus.changed.connect(lambda _m: self._style())
        self._style()

    # ------------------------------------------------------------------ showing
    def show(self, text: str, kind: str = "info", seconds: float | None = None) -> Toast:
        """A notice. kind: info | ok | warn | error. It is written to the log as well."""
        kind = kind if kind in KINDS else "info"
        text = str(text).strip() or "(no message)"
        {"error": log.warning, "warn": log.warning}.get(kind, log.info)("%s%s", "" if kind in ("info", "ok") else f"{kind}: ", text)
        for old in [t for t in self.items if t.text == text and t.kind == kind]:       # (the same notice again: one, renewed)
            old.dismiss()
        t = Toast(self, text, kind, seconds or SECONDS[kind])
        self.items.append(t)
        self.history.append({"kind": kind, "text": text, "time": time.strftime("%H:%M:%S")})
        del self.history[:-200]
        self.lay.addWidget(t)
        while len(self.items) > MAX_SHOWN:
            self.items[0].dismiss()
        self.box.show()
        self._place()
        self.box.raise_()
        return t

    def ok(self, text: str, seconds: float | None = None) -> Toast:
        return self.show(text, "ok", seconds)

    def warn(self, text: str, seconds: float | None = None) -> Toast:
        return self.show(text, "warn", seconds)

    def error(self, text: str, seconds: float | None = None) -> Toast:
        return self.show(text, "error", seconds)

    def close_all(self):
        for t in list(self.items):
            t.dismiss()

    def close_one(self, index: int = -1):
        if self.items:
            self.items[index].dismiss()

    def listing(self) -> list[dict]:
        return [{"kind": t.kind, "text": t.text} for t in self.items]

    # ------------------------------------------------------------------ placing
    def _removed(self, t: Toast):
        if t in self.items:
            self.items.remove(t)
        self.lay.removeWidget(t)
        t.hide()
        t.deleteLater()
        if not self.items:
            self.box.hide()
        else:
            self._place()

    def _place(self):
        w = self.win
        h = sum(t.height() for t in self.items) + self.lay.spacing() * max(0, len(self.items) - 1)
        self.box.setFixedSize(WIDTH, max(h, 1))
        cw = w.centralWidget() if callable(getattr(w, "centralWidget", None)) else None
        top = (cw.mapTo(w, QPoint(0, 0)).y() if cw is not None else 0) + 10         # (top right, under the bars: the message box and Send stay free)
        self.box.setGeometry(w.width() - WIDTH - 16, top, WIDTH, h)

    def eventFilter(self, obj, ev):  # noqa: N802
        if obj is self.win and ev.type() in (QEvent.Resize, QEvent.Show) and self.items:
            self._place()
        return False

    def _style(self):
        t = theme.tokens()
        self.box.setStyleSheet(
            f"QFrame#toast {{ background: {t['panel2']}; border: 1px solid {t['line_strong']}; border-left: 5px solid {t['accent']}; border-radius: 8px; }}"
            f"QFrame#toast[kind=\"ok\"] {{ border-left: 5px solid {t['ok']}; }}"
            f"QFrame#toast[kind=\"warn\"] {{ border-left: 5px solid {t['warn']}; }}"
            f"QFrame#toast[kind=\"error\"] {{ border-left: 5px solid {t['bad']}; }}"
            f"QLabel#toastText {{ background: transparent; color: {t['ink']}; }}")
