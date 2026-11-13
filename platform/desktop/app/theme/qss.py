"""The app's one stylesheet, built from tokens. Apply with apply_theme(app).

Selectors use dynamic properties so pages never style their own widgets:
  QLabel[role="soft" | "muted" | "hint" | "strong"]
  QPushButton[kind="primary" | "secondary" | "ghost"][small="true"]
  QFrame[cls="panel" | "notice" | "kpis" | "kpi"]
Fonts are not set here: a stylesheet font replaces a widget's QFont and would drop
letter spacing, which stylesheets cannot express. Widgets set fonts from theme.fonts.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from . import tokens as t
from .fonts import body, load_fonts
from .tokens import Fonts

HERE = Path(__file__).resolve().parent


def css(token: str) -> str:
    """A token as a colour Qt stylesheets read reliably (alpha as 0-255)."""
    c = t.qcolor(token)
    if c.alpha() == 255:
        return c.name()
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {c.alpha()})"


def stylesheet() -> str:
    chevron = (HERE / "chevron.svg").as_posix()
    return f"""
* {{
    color: {t.TEXT};
    selection-background-color: {css(t.ACCENT_BG)};
    selection-color: {t.TEXT};
    outline: 0;
}}
QMainWindow, QDialog {{ background: {t.BG}; }}
QToolTip {{
    background: {t.RAISED}; color: {t.TEXT}; border: 1px solid {css(t.BORDER)};
    border-radius: {t.R_SM}px; padding: 4px 8px; font-size: {t.FONT_SMALL}px;
}}

QLabel {{ background: transparent; }}
QLabel[role="strong"] {{ color: {t.TEXT}; }}
QLabel[role="soft"] {{ color: {t.SOFT}; }}
QLabel[role="muted"] {{ color: {t.MUTED}; }}
QLabel[role="hint"] {{ color: {t.MUTED}; }}
QLabel[role="small"] {{ color: {t.MUTED}; }}
QLabel[role="code"] {{ color: {t.MUTED}; }}
QLabel[role="empty"] {{ color: {t.MUTED}; padding: 18px 16px; }}

QFrame[cls="panel"] {{
    background: {t.PANEL}; border: 1px solid {css(t.BORDER_PANEL)}; border-radius: {t.R}px;
}}
QFrame[cls="notice"] {{
    background: {t.RAISED}; border: 1px solid {css(t.BORDER_STRONG)}; border-radius: {t.R}px;
}}
QFrame[cls="panel"][link="true"]:hover {{ border-color: {css(t.LINK_HOVER_BORDER)}; }}
QFrame[cls="kpis"] {{
    background: {t.PANEL}; border: 1px solid {css(t.BORDER_PANEL)}; border-radius: {t.R}px;
}}
QFrame[cls="kpi"] {{ background: transparent; border: 0; border-left: 1px solid {css(t.BORDER)}; }}
QFrame[cls="kpi"][first="true"] {{ border-left: 0; }}
QWidget[cls="panel-body"], QWidget[cls="panel-head"] {{ background: transparent; }}

QPushButton {{
    background: {t.BG}; color: {t.TEXT}; border: 1px solid {css(t.BORDER)};
    border-radius: {t.BUTTON_H // 2}px; padding: 0 {t.BUTTON_PAD_H}px;
    min-height: {t.BUTTON_H - 2}px; max-height: {t.BUTTON_H - 2}px;
}}
QPushButton:hover {{ background: {t.RAISED}; border-color: {css(t.BORDER_STRONG)}; }}
QPushButton[kind="primary"] {{ background: {t.PRIMARY}; border-color: {t.PRIMARY}; color: {t.ON_PRIMARY}; }}
QPushButton[kind="primary"]:hover {{ background: {t.PRIMARY_HOVER}; border-color: {t.PRIMARY_HOVER}; }}
QPushButton[kind="ghost"] {{ background: transparent; border-color: transparent; color: {t.SOFT}; }}
QPushButton[kind="ghost"]:hover {{ background: {css(t.HOVER)}; color: {t.TEXT}; }}
QPushButton[small="true"] {{
    border-radius: {t.BUTTON_SMALL_H // 2}px; padding: 0 {t.BUTTON_SMALL_PAD_H}px;
    min-height: {t.BUTTON_SMALL_H - 2}px; max-height: {t.BUTTON_SMALL_H - 2}px;
}}
QPushButton:disabled {{ color: {css('rgba(237, 237, 237, .4)')}; border-color: {css('rgba(255, 255, 255, .04)')}; }}
QPushButton[kind="primary"]:disabled {{
    background: {css('rgba(255, 77, 46, .4)')}; border-color: transparent; color: {css('rgba(18, 4, 0, .6)')};
}}

QComboBox {{
    background: {t.BG}; color: {t.TEXT}; border: 1px solid {css(t.BORDER)};
    border-radius: {t.R_SM}px; padding: 5px 8px 5px 12px; min-height: 20px;
}}
QComboBox:hover {{ border-color: {css(t.BORDER_STRONG)}; }}
QComboBox::drop-down {{ border: 0; width: 24px; subcontrol-origin: padding; subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: url("{chevron}"); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{
    background: {t.RAISED}; color: {t.TEXT}; border: 1px solid {css(t.BORDER)};
    selection-background-color: {css(t.HOVER)}; padding: 4px; outline: 0;
}}

QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: 0; }}
QAbstractScrollArea {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: {t.SCROLLBAR}px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: {t.SCROLLBAR}px; margin: 0; }}
QScrollBar::handle {{ background: {t.SCROLL_THUMB}; border-radius: 3px; margin: 2px; }}
QScrollBar::handle:vertical {{ min-height: 24px; }}
QScrollBar::handle:horizontal {{ min-width: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: 0; background: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QAbstractScrollArea::corner {{ background: transparent; }}

QTableView {{
    background: transparent; border: 0; gridline-color: transparent;
    color: {t.SOFT};
}}
QHeaderView {{ background: {t.PANEL}; border: 0; }}
QHeaderView::section:vertical {{ padding: 0; border: 0; }}
QHeaderView::section:horizontal {{
    background: {t.PANEL}; color: {t.MUTED}; border: 0; border-bottom: 1px solid {css(t.BORDER)};
    padding: {t.CELL_PAD_V}px {t.CELL_PAD_H}px;
}}
QTableCornerButton::section {{ background: {t.PANEL}; border: 0; }}

QPlainTextEdit[role="io"] {{
    background: {t.BG}; color: {t.SOFT}; border: 0; border-top: 1px solid {css(t.BORDER)};
    font-family: "{Fonts.mono}"; font-size: 12.5px; padding: 12px 16px;
}}
QLineEdit {{
    background: {t.BG}; color: {t.TEXT}; border: 1px solid {css(t.BORDER)};
    border-radius: {t.R_SM}px; padding: 5px 8px;
}}
QLineEdit:focus {{ border-color: {css(t.BORDER_STRONG)}; }}
"""


def palette() -> QPalette:
    p = QPalette()
    roles = {
        QPalette.ColorRole.Window: t.BG,
        QPalette.ColorRole.Base: t.BG,
        QPalette.ColorRole.AlternateBase: t.PANEL,
        QPalette.ColorRole.Button: t.BG,
        QPalette.ColorRole.WindowText: t.TEXT,
        QPalette.ColorRole.Text: t.TEXT,
        QPalette.ColorRole.ButtonText: t.TEXT,
        QPalette.ColorRole.BrightText: t.WHITE,
        QPalette.ColorRole.PlaceholderText: t.MUTED,
        QPalette.ColorRole.ToolTipBase: t.RAISED,
        QPalette.ColorRole.ToolTipText: t.TEXT,
        QPalette.ColorRole.Highlight: t.ACCENT_BG,
        QPalette.ColorRole.HighlightedText: t.TEXT,
        QPalette.ColorRole.Link: t.TEXT,
        QPalette.ColorRole.LinkVisited: t.TEXT,
        QPalette.ColorRole.Mid: t.BORDER,
    }
    for role, value in roles.items():
        p.setColor(role, t.qcolor(value))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor(t.MUTED))
    return p


def apply_theme(app: QApplication) -> dict[str, str]:
    """Load fonts, then set the app font, palette and stylesheet. Returns the picked fonts."""
    picked = load_fonts()
    app.setStyle("Fusion")
    app.setFont(body())
    app.setPalette(palette())
    app.setStyleSheet(stylesheet())
    return picked
