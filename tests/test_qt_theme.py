"""Dark and light themes: readable, and every control both says what it is (signifier) and looks/acts usable (affordance).

* the colour tokens meet WCAG contrast ratios in both themes (text 4.5:1, edges, focus ring and disabled text 3:1);
* the stylesheet really renders them: pixels of real widgets (buttons, check boxes, combo boxes, labels) have enough contrast, in every
  step, and disabled / focused / checked controls look different from the normal one;
* switching the theme at run time works (menu, control API, remembered)."""
import numpy as np
import pytest
from qt_support import ctl, from_thread  # noqa: F401

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QAbstractButton, QCheckBox, QComboBox, QLabel, QPushButton  # noqa: E402
from simwb.qt import contrast, theme  # noqa: E402

THEMES = ("dark", "light")

# (foreground token, background token, minimum ratio)
PAIRS = [("ink", "panel", 7), ("ink", "bg", 7), ("ink", "panel2", 7), ("ink", "raised", 7), ("ink", "accent_soft", 7),
         ("muted", "panel", 4.5), ("muted", "bg", 4.5), ("muted", "panel2", 4.5), ("muted", "raised", 4.5),
         ("on_accent", "accent", 4.5), ("on_accent", "accent_hi", 4.5), ("accent_text", "panel", 4.5), ("accent_text", "panel2", 4.5),
         ("ok_text", "panel", 4.5), ("warn_text", "panel", 4.5), ("bad_text", "panel", 4.5),
         ("ok_text", "panel2", 4.5), ("warn_text", "panel2", 4.5), ("bad_text", "panel2", 4.5),
         ("line_strong", "panel", 3), ("line_strong", "bg", 3), ("line_strong", "panel2", 3), ("focus", "panel", 3), ("focus", "bg", 3),
         ("disabled", "panel", 3), ("disabled", "panel2", 3), ("disabled", "raised", 3),
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


def best_contrast(widget):
    """(background colour, the colour that contrasts most with it among those that cover enough pixels, ratio): what a user sees."""
    px = pixels(widget)
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
                bg, fg, r = best_contrast(o)
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
