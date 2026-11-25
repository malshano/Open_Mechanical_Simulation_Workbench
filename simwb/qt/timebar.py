"""The time bar of the results step (the web version's, under the field choice): for a transient result the saved time shown, a slider over the
saved times, play / pause, and the video (frames per second, a camera orbit, ● MP4); for a steady result a turntable video. The panel listens
to timeChanged and recordRequested; everything has an id (results.time.*) so the control API reaches it."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget

from .exports import fmt_time
from .ids import wid
from .panels.common import FlowLayout, _muted


class TimeBar(QWidget):
    timeChanged = Signal(int)
    recordRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.times: list = []
        self.index = -1
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        row = QHBoxLayout()
        self.label = wid(QLabel(""), "results.time.label")
        self.label.setToolTip("The saved time shown on the model")
        row.addWidget(self.label)
        row.addStretch(1)
        v.addLayout(row)
        row = QHBoxLayout()
        self.play = wid(QPushButton("▶  Play"), "results.time.play")
        self.play.setCheckable(True)
        self.play.setObjectName("toggle")
        self.play.setToolTip("Play the saved times one after the other (press again to pause)")
        self.play.toggled.connect(self._play_toggled)
        row.addWidget(self.play)
        self.slider = wid(QSlider(Qt.Horizontal), "results.time.slider")
        self.slider.setToolTip("Move through the saved times of the run")
        self.slider.valueChanged.connect(self._slid)
        row.addWidget(self.slider, 1)
        v.addLayout(row)
        row = QHBoxLayout()
        self.fps = wid(QComboBox(), "results.time.fps")
        self.fps.setToolTip("Frames per second of the video")
        self.orbit = wid(QComboBox(), "results.time.orbit")
        self.orbit.setToolTip("The camera turns this many degrees around the model while the video runs (0 = a fixed view)")
        for d in (0, 30, 60, 90, 180, 360):
            self.orbit.addItem(f"orbit {d}°" if d else "no orbit", d)
        self.rec = wid(QPushButton("●  MP4"), "results.time.record")
        self.rec.setObjectName("ghostBorder")
        self.rec.clicked.connect(lambda _=False: self.recordRequested.emit())
        self.kind = QLabel("")
        holder = QWidget()                              # (wraps: a row that cannot shrink made the whole Results panel wider than its dock, and its right edge was cut off)
        flow = FlowLayout(holder, 6, 4)
        flow.setContentsMargins(0, 0, 0, 0)
        for w in (self.kind, QLabel("Video"), self.fps, self.orbit, self.rec):
            flow.addWidget(w)
        v.addWidget(holder)
        self.note = wid(_muted(""), "results.time.note")
        v.addWidget(self.note)
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._step)
        self.set_times([])

    # ------------------------------------------------------------------ what the result has
    @property
    def transient(self) -> bool:
        return len(self.times) >= 2

    def set_times(self, times: list, note: str = ""):
        """The saved times of the result (fewer than two: a steady result, the turntable video only)."""
        same = list(times or []) == self.times
        self.times = list(times or [])
        tr = self.transient
        for w in (self.label, self.play, self.slider):
            w.setVisible(tr)
        if not same:
            self.stop()
            fps = (4, 8, 12, 24, 30) if tr else (12, 24, 30)
            self.fps.blockSignals(True)
            self.fps.clear()
            for f in fps:
                self.fps.addItem(f"{f} fps", f)
            self.fps.setCurrentIndex(fps.index(12 if tr else 24))
            self.fps.blockSignals(False)
            self.orbit.setCurrentIndex(0 if tr else 5)
            self.slider.blockSignals(True)
            self.slider.setRange(0, max(len(self.times) - 1, 0))
            self.slider.setValue(max(len(self.times) - 1, 0))
            self.slider.blockSignals(False)
            self.index = len(self.times) - 1 if tr else -1
        self.kind.setText("" if tr else "Showcase video")
        self.rec.setText("●  MP4" if tr else "●  Turntable MP4")
        self.rec.setToolTip("Save the animation as an MP4 video: every saved time drawn as it is shown now, with the colour scale and the time"
                            if tr else "Save a turntable MP4: the camera turns around the model (the orbit), the result drawn as shown now, with the colour scale")
        self.note.setText(note)
        self.note.setVisible(bool(note))
        self._label()

    def _label(self):
        if self.transient and 0 <= self.index < len(self.times):
            self.label.setText(f"Time <b>{fmt_time(self.times[self.index])}</b>  ({self.index + 1} of {len(self.times)})")

    def time_index(self) -> int:
        """The saved time shown (-1: the last one, or a steady result)."""
        return self.index if self.transient else -1

    def set_index(self, i: int):
        if not self.transient:
            raise ValueError("This result has no saved times (it is steady)")
        i = int(i) % len(self.times) if i < 0 else int(i)
        if not 0 <= i < len(self.times):
            raise ValueError(f"time index must be 0 to {len(self.times) - 1}")
        self.slider.setValue(i)
        if self.index != i:
            self._slid(i)

    def _slid(self, i: int):
        if i != self.index:
            self.index = int(i)
            self._label()
            self.timeChanged.emit(self.index)

    # ------------------------------------------------------------------ play
    def _play_toggled(self, on: bool):
        self.play.setText("❚❚  Pause" if on else "▶  Play")
        if on:
            if self.index >= len(self.times) - 1:
                self.set_index(0)
            self._timer.start()
        else:
            self._timer.stop()

    def _step(self):
        if self.index >= len(self.times) - 1:
            self.play.setChecked(False)
            return
        self.set_index(self.index + 1)

    def stop(self):
        self._timer.stop()
        if self.play.isChecked():
            self.play.setChecked(False)

    def video_settings(self) -> dict:
        return {"fps": float(self.fps.currentData() or 12), "orbit": float(self.orbit.currentData() or 0)}
