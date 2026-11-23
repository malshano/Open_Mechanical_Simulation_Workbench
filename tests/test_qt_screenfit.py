"""The window stays on the screen it is on (issue #5): a fixed size left the bottom and right edge, and the controls there, off a laptop screen."""
from qt_support import ctl  # noqa: F401


def test_a_window_bigger_than_the_screen_is_made_to_fit_it(ctl):
    win = ctl.win
    win.fit_to_screen(9000, 9000)
    ctl.app.processEvents()
    av = win.screen().availableGeometry()
    fr = win.frameGeometry()
    assert fr.width() <= av.width() and fr.height() <= av.height()
    assert av.contains(fr)


def test_a_window_moved_partly_off_the_screen_is_brought_back(ctl):
    win = ctl.win
    av = win.screen().availableGeometry()
    win.move(av.right() - 100, av.bottom() - 100)
    win.fit_to_screen()
    ctl.app.processEvents()
    assert av.contains(win.frameGeometry())


def test_the_minimum_size_gives_way_on_a_small_screen(ctl):
    win = ctl.win
    win.fit_to_screen(300, 200)
    assert win.minimumSize().width() <= 300 and win.minimumSize().height() <= 200
