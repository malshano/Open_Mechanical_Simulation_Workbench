"""The look of the Qt window: one set of design tokens (colours, radius, spacing, type) and one stylesheet made from them.
Change a token here and the whole application follows."""
from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

TOKENS = {
    "bg": "#16181d", "panel": "#1d2026", "panel2": "#242830", "raised": "#2b303a", "line": "#323844",
    "ink": "#e6e9ef", "muted": "#8b93a1", "accent": "#3b82f6", "accent_hi": "#5a9bff", "accent_soft": "#1f3a66",
    "ok": "#3fb950", "warn": "#d29922", "bad": "#f85149", "radius": "7px", "font": "Segoe UI",
}


def stylesheet(t: dict = TOKENS) -> str:
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
QMenu {{ background: {t['panel2']}; border: 1px solid {t['line']}; border-radius: {t['radius']}; padding: 5px; }}
QMenu::item {{ padding: 6px 26px 6px 14px; border-radius: 5px; }}
QMenu::item:selected {{ background: {t['accent_soft']}; }}
QMenu::separator {{ height: 1px; background: {t['line']}; margin: 5px 8px; }}
QToolBar {{ background: {t['panel2']}; border: 0; border-bottom: 1px solid {t['line']}; padding: 5px 8px; spacing: 4px; }}
QToolButton {{ background: transparent; border: 0; border-radius: {t['radius']}; padding: 6px 14px; }}
QToolButton:hover {{ background: {t['raised']}; }}
QToolButton:checked {{ background: {t['accent']}; color: white; font-weight: 600; }}
QPushButton {{ background: {t['raised']}; border: 1px solid {t['line']}; border-radius: {t['radius']}; padding: 6px 14px; }}
QPushButton:hover {{ border-color: {t['accent']}; }}
QPushButton:disabled {{ color: {t['muted']}; }}
QPushButton#primary {{ background: {t['accent']}; border-color: {t['accent']}; color: white; font-weight: 600; }}
QPushButton#primary:hover {{ background: {t['accent_hi']}; }}
QPushButton#ghost {{ background: transparent; border-color: transparent; color: {t['muted']}; padding: 4px 8px; }}
QPushButton#ghost:hover {{ color: {t['ink']}; background: {t['raised']}; }}
QPushButton#chip {{ background: {t['panel2']}; border: 1px solid {t['line']}; border-radius: 12px; padding: 3px 10px; font-size: 12px; }}
QPushButton#seg {{ border-radius: 0; margin: 0; padding: 5px 10px; }}
QPushButton#seg:checked {{ background: {t['accent']}; border-color: {t['accent']}; color: white; }}
QLabel#muted {{ color: {t['muted']}; }}
QLabel#h {{ font-weight: 600; font-size: 12px; color: {t['muted']}; text-transform: uppercase; letter-spacing: 1px; }}
QLabel#kpiv {{ font-size: 22px; font-weight: 600; }}
QLabel#kpil {{ color: {t['muted']}; font-size: 11px; }}
QFrame#kpi {{ background: {t['panel2']}; border: 1px solid {t['line']}; border-radius: {t['radius']}; }}
QFrame#kpi[tone="ok"] {{ border-left: 4px solid {t['ok']}; }}
QFrame#kpi[tone="warn"] {{ border-left: 4px solid {t['warn']}; }}
QFrame#kpi[tone="bad"] {{ border-left: 4px solid {t['bad']}; }}
QLineEdit, QPlainTextEdit, QTextBrowser, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
  background: {t['bg']}; border: 1px solid {t['line']}; border-radius: {t['radius']}; padding: 5px 8px; selection-background-color: {t['accent']}; }}
QComboBox::drop-down {{ border: 0; width: 22px; }}
QComboBox QAbstractItemView {{ background: {t['panel2']}; border: 1px solid {t['line']}; selection-background-color: {t['accent_soft']}; }}
QTextBrowser {{ background: {t['panel']}; border: 0; }}
QTreeWidget, QListWidget, QTableWidget {{ background: {t['panel']}; border: 0; outline: 0; }}
QTreeWidget::item, QListWidget::item {{ padding: 3px 2px; }}
QTreeWidget::item:selected, QListWidget::item:selected {{ background: {t['accent_soft']}; border-radius: 4px; }}
QHeaderView::section {{ background: {t['panel2']}; border: 0; padding: 5px; }}
QStatusBar {{ background: {t['panel2']}; border-top: 1px solid {t['line']}; color: {t['muted']}; }}
QStatusBar QLabel {{ color: {t['muted']}; }}
QProgressBar {{ background: {t['bg']}; border: 1px solid {t['line']}; border-radius: 5px; text-align: center; height: 14px; }}
QProgressBar::chunk {{ background: {t['accent']}; border-radius: 4px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {t['line']}; border-radius: 5px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {t['muted']}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; }}
QScrollBar::handle:horizontal {{ background: {t['line']}; border-radius: 5px; min-width: 28px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QSlider::groove:horizontal {{ height: 4px; background: {t['line']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {t['accent']}; width: 14px; margin: -6px 0; border-radius: 7px; }}
QToolTip {{ background: {t['panel2']}; color: {t['ink']}; border: 1px solid {t['line']}; padding: 5px; }}
QWidget#home, QWidget#homeBody {{ background: {t['bg']}; }}
QLabel#homeTitle {{ font-size: 30px; font-weight: 700; }}
QFrame#card {{ background: {t['panel']}; border: 1px solid {t['line']}; border-radius: 12px; }}
QLabel#cardTitle {{ font-size: 16px; font-weight: 600; }}
QLabel#badge {{ background: {t['panel2']}; border: 1px solid {t['line']}; border-radius: 9px; padding: 2px 9px; color: {t['muted']}; font-size: 11px; }}
QLabel#chipLabel {{ background: {t['accent_soft']}; border-radius: 9px; padding: 2px 9px; font-size: 11px; }}
QPushButton#ghostBorder {{ background: transparent; border: 1px solid {t['line']}; }}
QPushButton#ghostBorder:hover {{ border-color: {t['accent']}; }}
QWidget#viewbar {{ background: {t['panel2']}; border-bottom: 1px solid {t['line']}; }}
QPushButton#viewbtn {{ padding: 3px 10px; font-size: 12px; }}
QGroupBox {{ border: 1px solid {t['line']}; border-radius: {t['radius']}; margin-top: 14px; padding: 12px 8px 8px 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 5px; color: {t['muted']}; }}
"""


def apply(app: QApplication, t: dict = TOKENS):
    app.setStyle("Fusion")
    pal = QPalette()
    for role, key in ((QPalette.Window, "bg"), (QPalette.Base, "panel"), (QPalette.AlternateBase, "panel2"),
                      (QPalette.Button, "raised"), (QPalette.Text, "ink"), (QPalette.WindowText, "ink"),
                      (QPalette.ButtonText, "ink"), (QPalette.Highlight, "accent"), (QPalette.ToolTipBase, "panel2"),
                      (QPalette.ToolTipText, "ink")):
        pal.setColor(role, QColor(t[key]))
    pal.setColor(QPalette.HighlightedText, QColor("white"))
    app.setPalette(pal)
    app.setFont(QFont(t["font"], 10))
    app.setStyleSheet(stylesheet(t))
