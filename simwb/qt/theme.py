"""The look of the Qt window: two sets of design tokens (dark and light), one stylesheet made from them, and a live switch.

Every colour pair that carries text or marks an edge is checked against WCAG contrast ratios by tests/test_qt_theme.py (text 4.5:1 and
more, control edges, focus ring and disabled text 3:1), in BOTH themes. Controls show their states: hover, pressed, keyboard focus (a
2 px ring), checked and disabled each look different, so that what can be used, and what is being used, is visible without guessing."""
from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

COMMON = {"radius": "7px", "font": "Segoe UI", "on_accent": "#ffffff"}

DARK = {
    **COMMON,
    "bg": "#16181d", "panel": "#1d2026", "panel2": "#242830", "raised": "#2b303a",
    "line": "#323844", "line_strong": "#6b7587",
    "ink": "#e6e9ef", "muted": "#9aa3b2", "disabled": "#7a8394",
    "accent": "#2f6fdb", "accent_hi": "#2459bc", "accent_soft": "#1f3a66", "accent_text": "#7db0ff", "focus": "#7db0ff",
    "ok": "#3fb950", "warn": "#d29922", "bad": "#f85149",                 # (marks and stripes)
    "ok_text": "#4cc263", "warn_text": "#e0ac3a", "bad_text": "#ff7b72",  # (as text)
    "vp_top": "#3b4760", "vp_bottom": "#667791", "vp_text": "#ffffff", "air_edge": "#cfe6ff", "edge": "#0d1017",
}

LIGHT = {
    **COMMON,
    "bg": "#f1f3f8", "panel": "#ffffff", "panel2": "#eceff5", "raised": "#e2e7ef",
    "line": "#c9d0dc", "line_strong": "#7a8598",
    "ink": "#1b2230", "muted": "#566175", "disabled": "#767f91",
    "accent": "#1f5fcf", "accent_hi": "#174cb0", "accent_soft": "#dbe7fb", "accent_text": "#1a56bd", "focus": "#1a56bd",
    "ok": "#1a7f37", "warn": "#9a6700", "bad": "#c62828",
    "ok_text": "#17692e", "warn_text": "#855a00", "bad_text": "#b3261e",
    "vp_top": "#c3cfe2", "vp_bottom": "#f4f7fb", "vp_text": "#1b2230", "air_edge": "#2f5d9e", "edge": "#2a3140",
}

THEMES = {"dark": DARK, "light": LIGHT}
TOKENS = DARK        # (kept for older imports)

_state = {"mode": "dark", "effective": "dark"}


class _Bus(QObject):
    changed = Signal(str)      # the effective theme ("dark" / "light") after a switch: custom-drawn widgets repaint


bus = _Bus()


def tokens() -> dict:
    """The colours of the theme in use now."""
    return THEMES[_state["effective"]]


def mode() -> str:
    """What the user chose: 'system', 'dark' or 'light'."""
    return _state["mode"]


def effective() -> str:
    """The theme actually shown: 'dark' or 'light'."""
    return _state["effective"]


def _icons(t: dict, name: str) -> dict:
    """The small pictures the stylesheet needs (a check mark, arrows), drawn in this theme's colours into a cache folder."""
    d = Path(tempfile.gettempdir()) / "simwb_qt_icons" / name
    d.mkdir(parents=True, exist_ok=True)

    def svg(file, path, colour, size=12, fill="none", width=2):
        (d / file).write_text(
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {size} {size}">'
            f'<path d="{path}" fill="{fill}" stroke="{colour}" stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round"/></svg>')
        return (d / file).as_posix()
    return {
        "check": svg("check.svg", "M2.5 6.5l2.5 2.5L9.5 3.5", t["on_accent"], 12, width=2.2),
        "down": svg("down.svg", "M2.5 4.5l3.5 3.5 3.5-3.5", t["ink"], 12, width=1.8),
        "down_dis": svg("down_dis.svg", "M2.5 4.5l3.5 3.5 3.5-3.5", t["disabled"], 12, width=1.8),
        "up": svg("up.svg", "M2.5 7.5l3.5-3.5 3.5 3.5", t["ink"], 12, width=1.8),
    }


def stylesheet(t: dict, ic: dict) -> str:
    return f"""
* {{ font-family: "{t['font']}", "Inter", sans-serif; font-size: 13px; color: {t['ink']}; }}
QMainWindow, QDialog {{ background: {t['bg']}; }}
QWidget {{ background: transparent; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: {t['panel']}; }}
QDockWidget {{ titlebar-close-icon: none; font-weight: 600; }}
QDockWidget::title {{ background: {t['panel2']}; padding: 8px 12px; border-bottom: 1px solid {t['line']}; }}
QDockWidget > QWidget {{ background: {t['panel']}; }}
QMainWindow::separator {{ background: {t['line']}; width: 1px; height: 1px; }}
QMenuBar {{ background: {t['panel2']}; padding: 2px 6px; }}
QMenuBar::item {{ padding: 5px 10px; border-radius: 5px; background: transparent; }}
QMenuBar::item:selected {{ background: {t['raised']}; }}
QMenu {{ background: {t['panel2']}; border: 1px solid {t['line_strong']}; border-radius: {t['radius']}; padding: 5px; }}
QMenu::item {{ padding: 6px 26px 6px 14px; border-radius: 5px; }}
QMenu::item:selected {{ background: {t['accent_soft']}; }}
QMenu::item:disabled {{ color: {t['disabled']}; }}
QMenu::separator {{ height: 1px; background: {t['line']}; margin: 5px 8px; }}
QToolBar {{ background: {t['panel2']}; border: 0; border-bottom: 1px solid {t['line']}; padding: 3px 8px; spacing: 4px; }}
QToolButton {{ background: transparent; border: 1px solid {t['line']}; border-radius: {t['radius']}; padding: 4px 14px; }}
QToolButton:hover {{ background: {t['raised']}; border-color: {t['line_strong']}; }}
QToolButton:pressed {{ background: {t['accent_soft']}; }}
QToolButton:focus {{ border: 2px solid {t['focus']}; padding: 3px 13px; }}
QToolButton:checked {{ background: {t['accent']}; color: {t['on_accent']}; font-weight: 600; border-color: {t['accent']}; }}
QToolButton:checked:hover {{ background: {t['accent_hi']}; }}

QPushButton {{ background: {t['raised']}; border: 1px solid {t['line_strong']}; border-radius: {t['radius']}; padding: 6px 14px; }}
QPushButton:hover {{ border-color: {t['accent']}; background: {t['panel2']}; }}
QPushButton:pressed {{ background: {t['accent_soft']}; border-color: {t['accent']}; }}
QPushButton:focus {{ border: 2px solid {t['focus']}; padding: 5px 13px; }}
QPushButton:disabled {{ color: {t['disabled']}; background: {t['panel2']}; border: 1px solid {t['line']}; }}
QPushButton#primary {{ background: {t['accent']}; border-color: {t['accent']}; color: {t['on_accent']}; font-weight: 600; }}
QPushButton#primary:hover, QPushButton#primary:pressed {{ background: {t['accent_hi']}; border-color: {t['accent_hi']}; }}
QPushButton#primary:focus {{ border: 2px solid {t['focus']}; }}
QPushButton#primary:disabled {{ background: {t['panel2']}; color: {t['disabled']}; border-color: {t['line']}; }}
QPushButton#ghost {{ background: transparent; border-color: transparent; color: {t['muted']}; padding: 4px 8px; }}
QPushButton#ghost:hover {{ color: {t['ink']}; background: {t['raised']}; border-color: {t['line_strong']}; }}
QPushButton#ghost:disabled {{ color: {t['disabled']}; background: transparent; border-color: transparent; }}
QPushButton#chip {{ background: {t['panel2']}; border: 1px solid {t['line_strong']}; border-radius: 12px; padding: 3px 10px; font-size: 12px; }}
QPushButton#chip:hover {{ border-color: {t['accent']}; background: {t['accent_soft']}; }}
QPushButton#seg {{ border-radius: 0; margin: 0; padding: 5px 10px; }}
QPushButton#seg:checked {{ background: {t['accent']}; border-color: {t['accent']}; color: {t['on_accent']}; font-weight: 600; }}
QPushButton#seg:checked:hover {{ background: {t['accent_hi']}; }}
QPushButton#ghostBorder {{ background: transparent; border: 1px solid {t['line_strong']}; }}
QPushButton#ghostBorder:hover {{ border-color: {t['accent']}; background: {t['accent_soft']}; }}
QPushButton#viewbtn {{ padding: 3px 10px; font-size: 12px; }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1.5px solid {t['line_strong']}; border-radius: 4px; background: {t['panel']}; }}
QCheckBox::indicator:hover {{ border-color: {t['accent']}; }}
QCheckBox::indicator:focus {{ border: 2px solid {t['focus']}; }}
QCheckBox::indicator:checked {{ background: {t['accent']}; border-color: {t['accent']}; image: url({ic['check']}); }}
QCheckBox::indicator:checked:hover {{ background: {t['accent_hi']}; }}
QCheckBox::indicator:disabled {{ border-color: {t['line']}; background: {t['panel2']}; }}
QCheckBox:disabled {{ color: {t['disabled']}; }}

QLabel#muted {{ color: {t['muted']}; }}
QLabel#h {{ font-weight: 600; font-size: 12px; color: {t['muted']}; }}
QLabel#kpiv {{ font-size: 22px; font-weight: 600; }}
QLabel#kpil {{ color: {t['muted']}; font-size: 11px; }}
QFrame#kpi {{ background: {t['panel2']}; border: 1px solid {t['line']}; border-radius: {t['radius']}; }}
QFrame#kpi[tone="ok"] {{ border-left: 4px solid {t['ok']}; }}
QFrame#kpi[tone="warn"] {{ border-left: 4px solid {t['warn']}; }}
QFrame#kpi[tone="bad"] {{ border-left: 4px solid {t['bad']}; }}

QLineEdit, QPlainTextEdit, QTextBrowser, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
  background: {t['panel']}; border: 1px solid {t['line_strong']}; border-radius: {t['radius']}; padding: 5px 8px; selection-background-color: {t['accent']}; selection-color: {t['on_accent']}; }}
QLineEdit:hover, QPlainTextEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {{ border-color: {t['accent']}; }}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border: 2px solid {t['focus']}; padding: 4px 7px; }}
QLineEdit:disabled, QPlainTextEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{ color: {t['disabled']}; background: {t['panel2']}; border-color: {t['line']}; }}
QComboBox {{ padding-right: 26px; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 24px; border-left: 1px solid {t['line']}; }}
QComboBox::down-arrow {{ image: url({ic['down']}); width: 12px; height: 12px; }}
QComboBox::down-arrow:disabled {{ image: url({ic['down_dis']}); }}
QComboBox QAbstractItemView {{ background: {t['panel2']}; border: 1px solid {t['line_strong']}; selection-background-color: {t['accent_soft']}; selection-color: {t['ink']}; outline: 0; }}
QAbstractSpinBox {{ padding-right: 22px; padding-left: 10px; }}
QAbstractSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right; width: 20px; border-left: 1px solid {t['line']}; }}
QAbstractSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right; width: 20px; border-left: 1px solid {t['line']}; }}
QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {{ background: {t['raised']}; }}
QAbstractSpinBox::up-arrow {{ image: url({ic['up']}); width: 10px; height: 10px; }}
QAbstractSpinBox::down-arrow {{ image: url({ic['down']}); width: 10px; height: 10px; }}
QTextBrowser {{ background: {t['panel']}; border: 0; }}

QTreeWidget, QListWidget, QTableWidget {{ background: {t['panel']}; border: 0; outline: 0; }}
QTreeWidget::item, QListWidget::item {{ padding: 3px 2px; }}
QTreeWidget::item:hover, QListWidget::item:hover {{ background: {t['raised']}; border-radius: 4px; }}
QTreeWidget::item:selected, QListWidget::item:selected {{ background: {t['accent_soft']}; color: {t['ink']}; border-radius: 4px; }}
QTreeWidget:focus {{ border: 2px solid {t['focus']}; }}
QHeaderView::section {{ background: {t['panel2']}; border: 0; padding: 5px; }}
QStatusBar {{ background: {t['panel2']}; border-top: 1px solid {t['line']}; }}
QStatusBar QLabel {{ color: {t['muted']}; }}
QProgressBar {{ background: {t['panel']}; border: 1px solid {t['line_strong']}; border-radius: 5px; text-align: center; height: 14px; }}
QProgressBar::chunk {{ background: {t['accent']}; border-radius: 4px; }}
QScrollBar:vertical {{ background: transparent; width: 9px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {t['line_strong']}; border-radius: 4px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {t['muted']}; }}
QScrollBar:horizontal {{ background: transparent; height: 9px; }}
QScrollBar::handle:horizontal {{ background: {t['line_strong']}; border-radius: 4px; min-width: 28px; }}
QScrollBar::handle:horizontal:hover {{ background: {t['muted']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QSlider::groove:horizontal {{ height: 4px; background: {t['line_strong']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {t['accent']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {t['accent']}; width: 14px; margin: -6px 0; border-radius: 7px; border: 2px solid {t['panel']}; }}
QSlider::handle:horizontal:hover {{ background: {t['accent_hi']}; }}
QSlider:focus {{ border: 1px solid {t['focus']}; }}
QSlider::handle:horizontal:disabled {{ background: {t['disabled']}; }}
QToolTip {{ background: {t['panel2']}; color: {t['ink']}; border: 1px solid {t['line_strong']}; padding: 5px; }}

QWidget#tabhead {{ background: {t['panel2']}; border-bottom: 1px solid {t['line']}; }}
QPushButton#dtab {{ background: transparent; border: 0; border-bottom: 2px solid transparent; border-radius: 0; padding: 6px 16px; color: {t['muted']}; }}
QPushButton#dtab:hover {{ color: {t['ink']}; background: {t['raised']}; }}
QPushButton#dtab:checked {{ color: {t['ink']}; font-weight: 600; border-bottom: 2px solid {t['accent']}; background: {t['panel']}; }}
QPushButton#dtab:focus {{ border-bottom: 2px solid {t['focus']}; padding: 6px 16px; }}
QTabWidget::pane {{ border: 0; border-top: 1px solid {t['line']}; background: {t['panel']}; }}
QTabBar {{ background: {t['panel2']}; qproperty-drawBase: 0; }}
QTabWidget {{ background: {t['panel2']}; }}
QTabBar::tab {{ background: {t['panel2']}; color: {t['muted']}; padding: 6px 16px; border: 0; border-bottom: 2px solid transparent; }}
QTabBar::tab:hover {{ color: {t['ink']}; background: {t['raised']}; }}
QTabBar::tab:selected {{ color: {t['ink']}; font-weight: 600; border-bottom: 2px solid {t['accent']}; background: {t['panel']}; }}
QTabBar::tab:focus {{ border-bottom: 2px solid {t['focus']}; }}
QFrame#item {{ background: {t['panel2']}; border: 1px solid {t['line']}; border-radius: {t['radius']}; }}
QWidget#home, QWidget#homeBody {{ background: {t['bg']}; }}
QLabel#homeTitle {{ font-size: 30px; font-weight: 700; }}
QFrame#card {{ background: {t['panel']}; border: 1px solid {t['line']}; border-radius: 12px; }}
QLabel#cardTitle {{ font-size: 16px; font-weight: 600; }}
QLabel#badge {{ background: {t['panel2']}; border: 1px solid {t['line']}; border-radius: 9px; padding: 2px 9px; color: {t['muted']}; font-size: 11px; }}
QLabel#chipLabel {{ background: {t['accent_soft']}; border-radius: 9px; padding: 2px 9px; font-size: 11px; }}
QWidget#viewbar {{ background: {t['panel2']}; border-bottom: 1px solid {t['line']}; }}
QGroupBox {{ border: 1px solid {t['line']}; border-radius: {t['radius']}; margin-top: 14px; padding: 12px 8px 8px 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 5px; color: {t['muted']}; }}
"""


def _palette(t: dict) -> QPalette:
    pal = QPalette()
    for role, key in ((QPalette.Window, "bg"), (QPalette.Base, "panel"), (QPalette.AlternateBase, "panel2"),
                      (QPalette.Button, "raised"), (QPalette.Text, "ink"), (QPalette.WindowText, "ink"),
                      (QPalette.ButtonText, "ink"), (QPalette.Highlight, "accent"), (QPalette.HighlightedText, "on_accent"),
                      (QPalette.ToolTipBase, "panel2"), (QPalette.ToolTipText, "ink"), (QPalette.Link, "accent_text"),
                      (QPalette.PlaceholderText, "muted")):
        pal.setColor(role, QColor(t[key]))
    for role, key in ((QPalette.Shadow, "line_strong"), (QPalette.Dark, "line_strong"), (QPalette.Mid, "line"), (QPalette.Light, "panel2")):
        pal.setColor(role, QColor(t[key]))                    # (Fusion draws frames and bases with these: never black)
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor(t["disabled"]))
    return pal


def _system_scheme(app) -> str:
    try:
        return "light" if app.styleHints().colorScheme() == Qt.ColorScheme.Light else "dark"
    except Exception:  # noqa: BLE001
        return "dark"


def apply(app: QApplication, choice: str = "dark"):
    """Show the window in 'dark', 'light' or the 'system' theme (and keep following the system one when asked to)."""
    if choice not in ("system", "dark", "light"):
        raise ValueError("theme must be system, dark or light")
    _state["mode"] = choice
    eff = _system_scheme(app) if choice == "system" else choice
    _state["effective"] = eff
    t = THEMES[eff]
    app.setStyle("Fusion")
    app.setPalette(_palette(t))
    app.setFont(QFont(t["font"], 10))
    app.setStyleSheet(stylesheet(t, _icons(t, eff)))
    if not getattr(app, "_simwb_theme_hooked", False):
        app._simwb_theme_hooked = True
        try:
            app.styleHints().colorSchemeChanged.connect(lambda *_: apply(app, "system") if _state["mode"] == "system" else None)
        except Exception:  # noqa: BLE001
            pass
    bus.changed.emit(eff)
