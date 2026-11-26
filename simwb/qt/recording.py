"""What the window says while it is being recorded (the MP4 button of the Results step draws the 3D view of THIS window picture by picture).

A minimised window draws nothing the recording could use, so the user is told, in three places that stay out of the way of the work:
a toast when it starts (the current window is recorded: keep it open, do not minimise it), a bar under the 3D view for as long as it runs
("● Recording ..." with a Stop button; Esc does the same) and "● Recording" in the window title, which stays visible in the taskbar.
If the window is minimised anyway, the recording PAUSES (the pictures are made one at a time, so nothing is lost) with a warning, and goes on
by itself when the window is restored. Stop keeps the pictures made so far."""
from __future__ import annotations

import time

from PySide6.QtWidgets import QApplication

KEY = "recording"
PRIORITY = 100               # (above the hints of picking and gizmos: while it records, this is what the bar says)


class RecordingStopped(Exception):
    """Stopped before there were two pictures: no video."""


class Recording:
    def __init__(self, win, what: str = "the 3D view of this window"):
        self.win, self.what = win, what
        self.stop_requested = False
        self.paused = False
        self.pauses = 0                  # (how many times the window was minimised: for the control API and tests)
        self.i, self.n = 0, 0
        self.active = False
        self._title = ""

    # ------------------------------------------------------------------ the bar, title, toasts
    def _bar(self):
        bar = self.win.view.banner
        if self.paused:
            text = f"⏸ Recording paused: the window is minimised. Restore it to carry on (picture {self.i + 1} of {self.n})."
        else:
            text = (f"● Recording {self.what} (picture {self.i + 1} of {self.n}). Keep this window open and do not minimise it.")
        bar.set(KEY, text, PRIORITY, "Stop recording", self.request_stop,
                "Stop here and keep the pictures made so far as the video (Esc does the same)")

    def start(self, n: int):
        self.n, self.i, self.active = n, 0, True
        self._title = self.win.windowTitle()
        self.win.setWindowTitle("● Recording - " + self._title)
        self._bar()
        self.win.toasts.warn(f"Recording {self.what}. Please keep this window open and do not minimise it: a minimised window records nothing. "
                             "Stop recording (or Esc) ends it early.", 9.0)

    def encoding(self):
        bar = self.win.view.banner
        bar.set(KEY, "Encoding the video: the recording is done, you may use the window again.", PRIORITY, None, None)

    def finish(self):
        if not self.active:
            return
        self.active = False
        self.win.view.banner.clear(KEY)
        self.win.setWindowTitle(self._title)

    def request_stop(self):
        self.stop_requested = True

    # ------------------------------------------------------------------ the gate in front of every picture
    def hidden(self) -> bool:
        """Can the window be drawn right now? A minimised (or hidden) window cannot."""
        w = self.win
        return w.isMinimized() or not w.isVisible()

    def gate(self, i: int) -> bool:
        """Called before picture i. False: stop now. While the window is minimised it waits (the events keep running), warning once."""
        self.i = i
        if self.hidden() and not self.stop_requested:
            self.paused = True
            self.pauses += 1
            self._bar()
            self.win.toasts.warn("The window is minimised, so the recording is paused. Restore the window to carry on, or press Stop recording "
                                 "in it to end the video here.", 9.0)
            self.win.statusBar().showMessage("Recording paused: the window is minimised", 0)
            while self.hidden() and not self.stop_requested:
                QApplication.processEvents()
                time.sleep(0.05)
            self.paused = False
            if not self.stop_requested:
                self.win.toasts.show("Recording goes on.", "ok", 3.0)
            self.win.statusBar().clearMessage()
        if not self.stop_requested:
            self._bar()
        QApplication.processEvents()
        return not self.stop_requested

    def state(self) -> dict:
        return {"active": self.active, "paused": self.paused, "picture": self.i, "of": self.n, "pauses": self.pauses,
                "stop_requested": self.stop_requested}
