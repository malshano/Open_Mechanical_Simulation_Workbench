"""Scrolling only scrolls: a drop-down, slider or number field takes the wheel only after it was clicked (issue #6)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QComboBox, QDoubleSpinBox, QLineEdit, QScrollArea, QSlider, QVBoxLayout, QWidget

from simwb.qt import wheelguard


def wheel(w, dy):
    pos = QPointF(w.width() / 2, w.height() / 2)
    QApplication.sendEvent(w, QWheelEvent(pos, w.mapToGlobal(pos), QPoint(0, 0), QPoint(0, dy), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False))
    QApplication.processEvents()


@pytest.fixture
def panel():
    app = QApplication.instance() or QApplication([])
    wheelguard.install(app)
    inner = QWidget()
    v = QVBoxLayout(inner)
    cb = QComboBox()
    cb.addItems([f"item {i}" for i in range(6)])
    cb.setCurrentIndex(3)
    sp = QDoubleSpinBox()
    sl = QSlider(Qt.Horizontal)
    sl.setValue(50)
    other = QLineEdit()
    for w in (other, cb, sp, sl):
        v.addWidget(w)
    v.addSpacing(900)                                  # (taller than the scroll area: there is something to scroll)
    sc = QScrollArea()
    sc.setWidgetResizable(True)
    sc.setWidget(inner)
    sc.resize(300, 200)
    sc.show()
    QApplication.processEvents()
    yield sc, cb, sp, sl, other
    sc.close()


def test_the_wheel_over_a_control_that_was_not_clicked_scrolls_the_panel_and_changes_nothing(panel):
    sc, cb, sp, sl, other = panel
    other.setFocus()
    for w, get in ((cb, cb.currentIndex), (sp, sp.value), (sl, sl.value)):
        before, bar = get(), sc.verticalScrollBar().value()
        wheel(w, -120)
        assert get() == before
    assert sc.verticalScrollBar().value() > 0                                  # (the panel did scroll)


def test_a_control_takes_the_wheel_after_it_was_clicked(panel):
    sc, cb, sp, sl, other = panel
    cb.setFocus()
    before = cb.currentIndex()
    wheel(cb, 120)
    assert cb.currentIndex() == before - 1
    sl.setFocus()
    wheel(sl, 120)
    assert sl.value() != 50


def test_the_wheel_no_longer_gives_a_control_the_keyboard_focus(panel):
    sc, cb, sp, sl, other = panel
    other.setFocus()
    wheel(cb, -120)
    assert cb.focusPolicy() == Qt.StrongFocus and not cb.hasFocus()
