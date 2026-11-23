"""Scrolling only scrolls (issue #6): a drop-down never takes the wheel; a slider or number field only after it was clicked."""
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


def test_a_slider_or_number_field_takes_the_wheel_after_it_was_clicked(panel):
    sc, cb, sp, sl, other = panel
    sl.setFocus()
    wheel(sl, 120)
    assert sl.value() != 50
    sp.setFocus()
    v = sp.value()
    wheel(sp, 120)
    assert sp.value() != v


def test_a_drop_down_never_takes_the_wheel_not_even_when_it_was_clicked(panel):
    sc, cb, sp, sl, other = panel
    cb.setFocus()
    before, bar = cb.currentIndex(), sc.verticalScrollBar().value()
    for _ in range(3):
        wheel(cb, -120)
    assert cb.currentIndex() == before                                         # (it changes only when an entry is chosen from its list)
    assert sc.verticalScrollBar().value() > bar                                # (the panel scrolled instead)


def test_the_wheel_no_longer_gives_a_control_the_keyboard_focus(panel):
    sc, cb, sp, sl, other = panel
    other.setFocus()
    wheel(cb, -120)
    assert cb.focusPolicy() == Qt.StrongFocus and not cb.hasFocus()
