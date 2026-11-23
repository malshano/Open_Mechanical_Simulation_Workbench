"""Dark and light themes: readable, and every control both says what it is (signifier) and looks/acts usable (affordance).

* the colour tokens meet WCAG contrast ratios in both themes (all text, disabled text included, 4.5:1; edges and the focus ring 3:1);
* the stylesheet really renders them: pixels of real widgets (buttons, check boxes, combo boxes, labels) have enough contrast, in every
  step, and disabled / focused / checked controls look different from the normal one;
* switching the theme at run time works (menu, control API, remembered)."""
import numpy as np
import pytest
from qt_support import ctl, from_thread  # noqa: F401

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QAbstractButton, QCheckBox, QComboBox, QLabel, QPushButton, QWidget  # noqa: E402
from simwb.qt import contrast, theme  # noqa: E402

THEMES = ("dark", "light")

# (foreground token, background token, minimum ratio)
PAIRS = [("ink", "panel", 7), ("ink", "bg", 7), ("ink", "panel2", 7), ("ink", "raised", 7), ("ink", "accent_soft", 7),
         ("muted", "panel", 6), ("muted", "bg", 6), ("muted", "panel2", 6), ("muted", "raised", 4.5), ("muted", "accent_soft", 4.5),
         ("on_accent", "accent", 4.5), ("on_accent", "accent_hi", 4.5), ("accent_text", "panel", 7), ("accent_text", "panel2", 7), ("accent_text", "bg", 7),
         ("ok_text", "panel", 7), ("warn_text", "panel", 7), ("bad_text", "panel", 7), ("ok_text", "bg", 7), ("warn_text", "bg", 7), ("bad_text", "bg", 7),
         ("ok_text", "panel2", 7), ("warn_text", "panel2", 7), ("bad_text", "panel2", 7),
         ("ok_text", "raised", 6), ("warn_text", "raised", 6), ("bad_text", "raised", 6), ("ok_text", "accent_soft", 5.5), ("warn_text", "accent_soft", 5.5),
         ("bad_text", "accent_soft", 5.5),
         ("line_strong", "panel", 3), ("line_strong", "bg", 3), ("line_strong", "panel2", 3), ("focus", "panel", 3), ("focus", "bg", 3),
         ("disabled", "panel", 4.5), ("disabled", "panel2", 4.5), ("disabled", "raised", 4.5), ("disabled", "bg", 4.5), ("disabled", "accent_soft", 4.5),
         ("ok", "panel", 3), ("warn", "panel", 3), ("bad", "panel", 3),
         ("vp_text", "vp_top", 3), ("vp_text", "vp_bottom", 3), ("air_edge", "vp_bottom", 3), ("air_edge", "vp_top", 1.5),
         ("edge", "vp_bottom", 3), ("edge", "vp_top", 1.5)]


@pytest.mark.parametrize("name", THEMES)
def test_tokens_meet_the_contrast_ratios(name):
    t = theme.THEMES[name]
    bad = [(a, b, round(contrast.ratio(t[a], t[b]), 2), m) for a, b, m in PAIRS if contrast.ratio(t[a], t[b]) < m]
    assert not bad, f"{name}: {bad}"


def test_the_two_themes_really_differ():
    assert contrast.luminance(contrast.hex_rgb(theme.DARK["bg"])) < 0.05 < 0.6 < contrast.luminance(contrast.hex_rgb(theme.LIGHT["bg"]))
    assert set(theme.DARK) == set(theme.LIGHT)               # (a token in one and not the other would be a hole)


# ------------------------------------------------------------------------------------------------------------ pixels
def pixels(widget):
    img = widget.grab().toImage().convertToFormat(QImage.Format_RGB32)
    a = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine() // 4, 4)[:, : img.width()]
    return a[..., [2, 1, 0]].reshape(-1, 3)


def contrast_on_screen(win, widget, arr=None, dpr=None):
    """(background, strongest colour, ratio) of a widget as it looks IN its window. Grabbing a transparent label on its own paints it on black, which
    is not what anyone sees; the window's picture, cropped to the widget, is."""
    from PySide6.QtCore import QPoint
    if arr is None:
        img = win.grab().toImage().convertToFormat(QImage.Format_RGB32)
        arr = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine() // 4, 4)[:, : img.width()][..., [2, 1, 0]]
        dpr = img.devicePixelRatio()
    vr = widget.visibleRegion().boundingRect()                   # (what of the widget is really shown: not scrolled away, not under another)
    if vr.width() < 0.9 * widget.width() or vr.height() < 0.9 * widget.height():
        return None, "off screen", 99.0
    p = widget.mapTo(win, QPoint(0, 0))
    x0, y0 = int(p.x() * dpr), int(p.y() * dpr)
    crop = arr[max(y0, 0): y0 + int(widget.height() * dpr), max(x0, 0): x0 + int(widget.width() * dpr)]
    if crop.size == 0:                                           # (scrolled out of sight: nothing is seen there)
        return None, "off screen", 99.0
    packed = (crop[..., 0].astype(np.int64) << 16 | crop[..., 1].astype(np.int64) << 8 | crop[..., 2].astype(np.int64)).ravel()
    cols, counts = np.unique(packed, return_counts=True)
    bg = cols[np.argmax(counts)]
    rgb = lambda v: ((v >> 16) & 255, (v >> 8) & 255, v & 255)  # noqa: E731
    best = (None, 1.0)
    for c, n in zip(cols, counts):
        if n >= 3 and c != bg:
            r = contrast.ratio(rgb(int(c)), rgb(int(bg)))
            if r > best[1]:
                best = (rgb(int(c)), r)
    return rgb(int(bg)), best[0], best[1]


def best_contrast(widget):
    """(background colour, the colour that contrasts most with it, ratio) of the widget as it looks in its window (what a user sees); an
    off-screen widget gives (None, "off screen", 99.0). Used by the legibility tests of every step."""
    return contrast_on_screen(widget.window(), widget)


def visible_controls(ctl):
    idx = ctl._index()
    for i, o in sorted(idx.items()):
        if isinstance(o, (QAbstractButton, QComboBox, QLabel)) and o.isVisible() and o.width() > 8 and o.height() > 8:
            if isinstance(o, QLabel) and not o.text().strip():
                continue
            yield i, o


@pytest.mark.parametrize("name", THEMES)
def test_every_visible_control_is_legible_in_every_step(ctl, name):
    ctl.do("theme.set", {"mode": name})
    assert ctl.state()["window"]["theme"] == {"mode": name, "effective": name}
    weak = []
    seen = 0
    for screen in ("home", "study"):
        if screen == "study":
            ctl.do("study.new", {"name": "Plate", "study": "static_structural"})
            steps = ("geometry", "setup", "mesh", "solve", "results")
        else:
            steps = ("home",)
        for step in steps:
            if step != "home":
                ctl.do("step.go", {"step": step})
            ctl._settle()
            for i, o in visible_controls(ctl):
                seen += 1
                bg, fg, r = contrast_on_screen(ctl.win, o)
                if fg == "off screen":
                    seen -= 1
                    continue
                if fg is None or r < 3.0:                 # (text and control edges must stand out of their fill)
                    weak.append((step, i, bg, fg, round(r, 2)))
    assert seen > 40
    assert not weak, f"{name}: {weak[:8]}"


@pytest.mark.parametrize("name", THEMES)
def test_states_are_visible_not_just_defined(ctl, name):
    """Disabled, focused and checked controls look different from the normal one, and disabled text is still readable."""
    ctl.do("theme.set", {"mode": name})
    ctl.do("study.new", {"name": "Plate", "study": "static_structural"})
    ctl.do("step.go", {"step": "mesh"})
    ctl._settle()
    t = theme.THEMES[name]
    b = ctl._index()["mesh.generate"]
    normal = pixels(b).copy()
    b.setEnabled(False)
    disabled = pixels(b).copy()
    _, fg, r = best_contrast(b)
    b.setEnabled(True)
    assert not np.array_equal(normal, disabled)
    assert r >= 3.0, f"disabled text {r:.2f}"
    assert contrast.ratio(t["disabled"], t["panel2"]) >= 3.0
    b.setFocus(Qt.TabFocusReason)
    focused = pixels(b).copy()
    assert not np.array_equal(normal, focused), "keyboard focus is not visible"
    # a checkable control: checked differs from unchecked and shows the accent colour
    c = ctl._index()["results.reverse"]
    ctl.do("step.go", {"step": "results"})
    ctl._settle()
    c.setChecked(False)
    off = pixels(c).copy()
    c.setChecked(True)
    on = pixels(c).copy()
    assert not np.array_equal(off, on)
    ac = contrast.hex_rgb(t["accent"])
    assert any(tuple(p) == ac for p in on[:: max(1, len(on) // 4000)].tolist()) or (on == np.array(ac)).all(axis=1).any()
    # a combo box shows its arrow (the drop-down is a visible control, not a bare box)
    cb = ctl._index()["results.colormap"]
    left, right = pixels(cb), None
    img = cb.grab().toImage().convertToFormat(QImage.Format_RGB32)
    arrow_zone = [img.pixelColor(x, y).name() for x in range(img.width() - 24, img.width() - 4) for y in range(2, img.height() - 2)]
    assert len(set(arrow_zone)) >= 3, "the combo box has no arrow"


# ------------------------------------------------------------------------------------------------------------ signifiers, affordances
@pytest.mark.parametrize("name", THEMES)
def test_every_control_has_a_signifier_and_an_affordance(ctl, name):
    ctl.do("theme.set", {"mode": name})
    ctl.do("study.new", {"name": "Plate", "study": "static_structural"})
    for step in ("geometry", "setup", "mesh", "solve", "results"):
        ctl.do("step.go", {"step": step})
        cov = ctl.coverage()
        assert cov["unlabelled"] == [] and cov["no_pointer_cursor"] == [], (step, cov["unlabelled"], cov["no_pointer_cursor"])
    # what is pressable shows the pointing hand; a text box does not
    idx = ctl._index()
    assert idx["mesh.generate"].cursor().shape() == Qt.PointingHandCursor
    assert idx["results.colormap"].cursor().shape() == Qt.PointingHandCursor
    assert idx["assistant.input"].cursor().shape() != Qt.PointingHandCursor
    # a control that has no visible text still says what it is
    assert idx["results.bands"].toolTip() and idx["results.colormap"].accessibleName()


def test_a_disabled_control_says_why(ctl):
    """The solve button is off until the mesh exists, and its tooltip tells the user what to do."""
    ctl.do("study.new", {"name": "Plate", "study": "static_structural"})
    ctl.do("step.go", {"step": "solve"})
    b = ctl._index()["solve.run"]
    assert not b.isEnabled() and "mesh" in b.toolTip().lower()


# ------------------------------------------------------------------------------------------------------------ switching
def test_the_theme_can_be_switched_and_is_remembered(ctl):
    assert ctl.state()["window"]["theme"]["mode"] == "dark"
    ctl.widget_do("theme.light", "click")
    s = ctl.state()["window"]["theme"]
    assert s == {"mode": "light", "effective": "light"}
    assert contrast.hex_rgb(theme.tokens()["bg"]) == contrast.hex_rgb(theme.LIGHT["bg"])
    assert "#f1f3f8" in ctl.app.styleSheet().lower()
    assert ctl.engine.settings.value("theme", type=str) == "light"
    assert ctl.widget_do("theme.light")["value"] == {"checked": True, "enabled": True}
    ctl.do("theme.set", {"mode": "system"})
    assert ctl.state()["window"]["theme"]["mode"] == "system" and ctl.state()["window"]["theme"]["effective"] in THEMES
    ctl.do("theme.set", {"mode": "dark"})
    with pytest.raises(ValueError, match="theme must be"):
        theme.apply(ctl.app, "sepia")


def test_the_3d_view_and_legend_follow_the_theme(ctl):
    v = ctl.win.view
    ctl.do("theme.set", {"mode": "dark"})
    dark_bg = v.ren.GetBackground()
    ctl.do("theme.set", {"mode": "light"})
    light_bg = v.ren.GetBackground()
    assert sum(light_bg) > sum(dark_bg) + 0.5                      # a light backdrop in light mode
    t = theme.LIGHT
    assert contrast.ratio(t["vp_text"], t["vp_bottom"]) >= 3       # the axis labels stay readable on it
    ctl.do("theme.set", {"mode": "dark"})


def test_dialogs_keep_their_background_the_widget_rule_must_not_win():
    """Qt style sheets: of two rules with the same weight the later one wins, and every dialog is a widget. With 'QWidget { background:
    transparent }' after the dialog rule, message boxes had no background at all (dark text on black in the light theme)."""
    import inspect
    from simwb.qt import theme
    src = inspect.getsource(theme.stylesheet)
    assert src.index("QWidget {{ background: transparent; }}") < src.index("QMainWindow, QDialog, QMessageBox")


# ------------------------------------------------------------------------------------------------------------ messages, windows and the theme switch
def picture(top):
    """What the user sees of a window: its pixels as an (h, w, 3) array and the device pixel ratio."""
    img = top.grab().toImage().convertToFormat(QImage.Format_RGB32)
    a = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine() // 4, 4)[:, : img.width()]
    return a[..., [2, 1, 0]], img.devicePixelRatio()


def best_in(arr):
    """(background, the colour that contrasts most with it, ratio) of a crop of a picture."""
    px = arr.reshape(-1, 3)
    packed = (px[:, 0].astype(np.int64) << 16) | (px[:, 1].astype(np.int64) << 8) | px[:, 2]
    cols, counts = np.unique(packed, return_counts=True)
    bg = cols[np.argmax(counts)]
    rgb = lambda v: ((v >> 16) & 255, (v >> 8) & 255, v & 255)  # noqa: E731
    best = (None, 1.0)
    for c, n in zip(cols, counts):
        if n >= 3 and c != bg:
            r = contrast.ratio(rgb(int(c)), rgb(int(bg)))
            if r > best[1]:
                best = (rgb(int(c)), r)
    return rgb(int(bg)), best[0], best[1]


def weak_texts(top, children_of=None, body_min=7.0, text_min=4.5):
    """Every piece of text in `top` (labels, buttons, fields, drop-downs, tables, tabs) whose rendered contrast is below the minimum:
    [(what, text, ratio)]. Body text (labels) must reach `body_min`, the rest `text_min`."""
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QAbstractItemView, QLineEdit, QTabBar
    from PySide6.QtWidgets import QMenu
    arr, dpr = picture(top)
    out = []
    n = 0
    if isinstance(top, QMenu):                                   # (a menu draws its own entries: measure each one where it is)
        for a in top.actions():
            if a.isSeparator() or not a.text().strip():
                continue
            g = top.actionGeometry(a)
            crop = arr[int(g.y() * dpr): int((g.y() + g.height()) * dpr), int(g.x() * dpr): int((g.x() + g.width()) * dpr)]
            if crop.size == 0:
                continue
            n += 1
            _bg, fg, r = best_in(crop)
            if fg is None or r < (text_min if not a.isEnabled() else body_min):
                out.append(("menu entry", a.text()[:40], round(r, 2), body_min))
        assert n > 0, "no text was found to measure"
        return out
    root = children_of or top
    for w in ([root] if root is not top else []) + root.findChildren(QWidget):
        if not w.isVisible() or w.width() < 6 or w.height() < 6:
            continue
        text = ""
        if isinstance(w, (QLabel, QAbstractButton, QLineEdit)):
            text = (w.text() or "").strip()
        elif isinstance(w, QComboBox):
            text = w.currentText().strip()
        elif isinstance(w, (QAbstractItemView, QTabBar)):
            text = "(items)"
        if not text:
            continue
        p = w.mapTo(top, QPoint(0, 0))
        x0, y0 = int(p.x() * dpr), int(p.y() * dpr)
        crop = arr[max(y0, 0): y0 + int(w.height() * dpr), max(x0, 0): x0 + int(w.width() * dpr)]
        if crop.size == 0:
            continue
        n += 1
        _bg, fg, r = best_in(crop)
        need = body_min if isinstance(w, QLabel) and w.objectName() != "muted" else text_min
        if fg is None or r < need:
            out.append((type(w).__name__, text[:40], round(r, 2), need))
    assert n > 0, "no text was found to measure"
    return out


def open_window(win, kind: str):
    """One of the messages and windows of the program, open and shown (not blocking). Returns (top level widget, what to measure inside it)."""
    from PySide6.QtWidgets import QInputDialog, QMenu, QMessageBox
    from simwb.qt import materialsui
    if kind == "warning":
        w = QMessageBox(QMessageBox.Warning, "Cannot do that", "The forward axis (+x) and the up axis (+x) are along the same line.",
                        QMessageBox.Ok | QMessageBox.Cancel, win)
        w.setInformativeText("Set them to two different axes in the Setup step.")
        w.setDetailedText("More detail that a person can open: which setting, which step.")
    elif kind == "question":
        w = QMessageBox(QMessageBox.Question, "Close", "Stop the running solver and close?", QMessageBox.Yes | QMessageBox.No, win)
    elif kind == "information":
        w = QMessageBox(QMessageBox.Information, "About", "<b>Mechanical Simulation Workbench</b><br>Version 0.4<br><a href='https://example.org'>a link</a>",
                        QMessageBox.Ok, win)
    elif kind == "input":
        w = QInputDialog(win)
        w.setLabelText("Name of the study")
        w.setTextValue("Wing test")
        w.setWindowTitle("New study")
    elif kind == "material-with-error":
        w = materialsui.MaterialDialog("fluid", win)
        w.name.setText("Brine")
        w.edits["rho"][0].setText("-5")                        # (wrong on purpose: the dialog says what is wrong, in its own colour)
        w.ok.click()
    elif kind == "material-solid":
        w = materialsui.MaterialDialog("solid", win)
    elif kind == "library-solids":
        w = materialsui.LibraryDialog(win)
    elif kind == "library-fluids":
        w = materialsui.LibraryDialog(win)
        w.tabs.setCurrentIndex(1)
        w.tables["fluid"].selectRow(0)
    elif kind == "menu":
        w = win.findChild(QMenu, "menu.theme")
        w.popup(win.mapToGlobal(win.rect().center()))
        return w
    else:
        raise ValueError(kind)
    w.show()
    return w


MESSAGES = ("warning", "question", "information", "input", "material-with-error", "material-solid", "library-solids", "library-fluids", "menu")


@pytest.mark.parametrize("name", THEMES)
@pytest.mark.parametrize("kind", MESSAGES)
def test_every_message_and_window_is_clearly_legible(ctl, name, kind):
    ctl.do("theme.set", {"mode": name})
    ctl._settle()
    w = open_window(ctl.win, kind)
    ctl.app.processEvents()
    try:
        weak = weak_texts(w)
        assert not weak, f"{name} / {kind}: {weak}"
    finally:
        w.close()


@pytest.mark.parametrize("name", THEMES)
def test_toasts_are_legible(ctl, name):
    ctl.do("theme.set", {"mode": name})
    ctl.win.toasts.show("The model was saved.", "info", 60)
    ctl.win.toasts.error("Cannot do that: the forward axis and the up axis are along the same line.", 60)
    ctl.app.processEvents()
    weak = weak_texts(ctl.win, children_of=ctl.win.toasts.box, body_min=4.5)
    assert not weak, f"{name}: {weak}"


@pytest.mark.parametrize("name", THEMES)
def test_an_open_drop_down_list_is_legible(ctl, name):
    ctl.do("theme.set", {"mode": name})
    ctl.do("study.new", {"name": "Plate", "study": "static_structural"})
    ctl.do("step.go", {"step": "setup"})
    ctl._settle()
    cb = next(c for c in ctl.win.panels["setup"].findChildren(QComboBox) if c.isVisible() and c.count() > 3)
    cb.showPopup()
    ctl.app.processEvents()
    try:
        view = cb.view()
        weak = weak_texts(view.window(), children_of=view, text_min=4.5)
        assert not weak, f"{name}: {weak}"
    finally:
        cb.hidePopup()


def test_the_theme_has_a_visible_switch_that_says_what_a_click_does(ctl):
    b = ctl.win.theme_btn
    assert b.isVisible() and b.property("wid") == "chrome.theme" and b.width() > 60
    ctl.do("theme.set", {"mode": "dark"})
    assert "Light" in b.text() and "light" in b.toolTip().lower()           # (it is dark now: the button offers the light theme)
    b.click()
    assert theme.effective() == "light" and ctl.engine.settings.value("theme") == "light"
    assert "Dark" in b.text() and "dark" in b.toolTip().lower()
    b.click()
    assert theme.effective() == "dark" and ctl.engine.settings.value("theme") == "dark"


def test_the_switch_is_itself_legible_in_both_themes(ctl):
    for name in THEMES:
        ctl.do("theme.set", {"mode": name})
        ctl._settle()
        bg, fg, r = best_contrast(ctl.win.theme_btn)
        assert fg is not None and r >= 4.5, f"{name}: {r}"


def test_the_screen_around_the_switch_is_legible_in_both_themes(ctl):
    """The whole top bar and the status bar, in both themes."""
    for name in THEMES:
        ctl.do("theme.set", {"mode": name})
        ctl._settle()
        weak = weak_texts(ctl.win, children_of=ctl.win.menuBar(), body_min=4.5)
        assert not weak, f"{name}: {weak}"
