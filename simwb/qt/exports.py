"""Videos of the results (the web version's MP4 export): every saved time of a transient result drawn as it is shown now, or a turntable of a
steady one (the camera turns once around the model), each picture with the colour legend and the time written on it, then encoded to an
H.264 MP4 by the engine (simwb.export.encode_mp4)."""
from __future__ import annotations

import math
import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication


def fmt_time(t: float) -> str:
    """A time as the web version writes it: seconds, minutes past 2 min, hours past 1 h."""
    return f"{t / 3600:.3g} h" if t >= 3600 else f"{t / 60:.3g} min" if t >= 120 else f"{t:.3g} s"


def frame(view, lines=()) -> QImage:
    """The 3D view as a picture with its colour legend below it and lines of text (the time, the study) at the bottom right."""
    img = view.image()
    leg = view.legend.grab().toImage() if view.legend.isVisible() and view.legend.height() > 0 and view.legend.width() > 0 else None
    if leg is not None and not leg.isNull():
        leg = leg.scaledToWidth(img.width(), Qt.SmoothTransformation)
        out = QImage(img.width(), img.height() + leg.height(), QImage.Format_RGB888)
        p = QPainter(out)
        p.drawImage(0, 0, img)
        p.drawImage(0, img.height(), leg)
        p.end()
        img = out
    lines = [s for s in lines if s]
    if lines:
        img = img.convertToFormat(QImage.Format_RGB888)
        p = QPainter(img)
        p.setRenderHint(QPainter.TextAntialiasing)
        f = QFont()
        f.setPixelSize(max(14, img.height() // 40))
        p.setFont(f)
        fm = p.fontMetrics()
        y = img.height() - (view.legend.height() if leg is not None else 0) - 10
        for s in reversed(lines):
            x = img.width() - fm.horizontalAdvance(s) - 12
            p.setPen(QColor(0, 0, 0, 160))
            p.drawText(QPointF(x + 1, y + 1), s)
            p.setPen(QColor(255, 255, 255))
            p.drawText(QPointF(x, y), s)
            y -= fm.height() + 2
        p.end()
    return img


def record(view, out: str, fps: float, n_frames: int, go, orbit_deg: float = 0.0, text=lambda i: (), progress=None) -> dict:
    """Draw n_frames pictures and encode them to the MP4 `out`. go(i) puts picture i in the view (a saved time; nothing for a turntable);
    the camera turns orbit_deg over the whole video (360: once around). text(i): the lines written on picture i. progress(i, n): feedback.
    The camera is put back as it was afterwards."""
    from ..export import encode_mp4

    if n_frames < 2:
        raise ValueError("A video needs at least two pictures")
    cam = view.ren.GetActiveCamera()
    saved = (cam.GetPosition(), cam.GetFocalPoint(), cam.GetViewUp())
    tmp = Path(tempfile.mkdtemp(prefix="simwb_video_"))
    try:
        for i in range(n_frames):
            go(i)
            if orbit_deg:
                cam.SetPosition(*saved[0])
                cam.SetFocalPoint(*saved[1])
                cam.SetViewUp(*saved[2])
                cam.Azimuth(orbit_deg * i / n_frames)
                view.ren.ResetCameraClippingRange()
            view.render()
            QApplication.processEvents()
            frame(view, text(i)).save(str(tmp / f"f{i:05d}.jpg"), "JPG", 92)
            if progress:
                progress(i + 1, n_frames)
        if progress:
            progress(n_frames, n_frames, "encoding")
        path = encode_mp4(tmp, fps, Path(out))
    finally:
        cam.SetPosition(*saved[0])
        cam.SetFocalPoint(*saved[1])
        cam.SetViewUp(*saved[2])
        view.render()
        shutil.rmtree(tmp, ignore_errors=True)
    return {"file": str(path), "frames": n_frames, "fps": fps, "seconds": round(n_frames / fps, 2), "orbit_deg": orbit_deg,
            "bytes": path.stat().st_size}


def turntable_frames(fps: float, seconds: float = 4.0) -> int:
    return max(2, int(math.ceil(seconds * fps)))
