"""Design tokens, copied from the mockup's :root (docs/design/desktop-dashboard-mockup-red-apple.html).

Values are the ones in force after the mockup's last style layer ("Apple type" and
"No red text"), since those override the earlier ones.

The RED rule: RED is for shapes only (dots, bars, rings, chart marks, selected
outlines). Never paint text in RED or in any colour of its family; an abnormal
value gets a red dot beside it instead (see widgets.FlagValue).
"""

from __future__ import annotations

# Surfaces
BG = "#0a0a0a"
SIDE = "rgba(8, 8, 8, .55)"  # sidebar, over the page glow
PANEL = "#141414"
RAISED = "#1a1a1a"
HOVER = "rgba(255, 255, 255, .05)"
NAV_ON = "rgba(255, 255, 255, .06)"  # selected nav item
ASK_BG = "rgba(255, 255, 255, .03)"  # the alarm question strip

# Hairlines
BORDER = "rgba(255, 255, 255, .1)"
BORDER_STRONG = "rgba(255, 255, 255, .18)"
BORDER_PANEL = "rgba(255, 255, 255, .07)"  # panels, KPI strips, status card
BORDER_TOPBAR = "rgba(255, 255, 255, .06)"
GRID = "rgba(255, 255, 255, .07)"  # table row lines, chart grid

# Text
TEXT = "#ededed"
SOFT = "#a1a1a1"
MUTED = "#878787"
ON_PRIMARY = "#120400"  # text on a primary (red) button
WHITE = "#ffffff"

# Marks
DOT = "#e9e9e9"  # normal chart marks and the live dot
DIM = "#2a2a2a"
NORMAL = "#bdbdbd"
SCROLL_THUMB = "#262626"

# The red family. RED is the accent, primary, warn and imbalance colour; ALERT is
# the unknown colour. Shapes only.
RED = "#ff4d2e"
ACCENT = RED
PRIMARY = RED
WARN = RED
IMBALANCE = RED
PRIMARY_HOVER = "#ff6a4f"
ALERT = "#ff9a80"
UNKNOWN = ALERT
ACCENT_BG = "rgba(255, 77, 46, .12)"  # selected table row
LINK_HOVER_BORDER = "rgba(255, 77, 46, .5)"  # clickable panel hover

# Colours that must never be used for text (RED rule).
RED_FAMILY = (RED, PRIMARY_HOVER, ALERT)

# Radii (px)
R = 16  # panels, KPI strips
R_SM = 6  # select
R_NAV = 10  # nav items
R_PILL = 999  # buttons, chips, pills, segmented, badges

# Spacing (px)
SIDEBAR_W = 232
SIDEBAR_PAD_V = 16
SIDEBAR_PAD_H = 12
SIDEBAR_GAP = 16
TOPBAR_H = 56
TOPBAR_PAD_H = 24
TOPBAR_GAP = 12
CONTENT_PAD = 24
PAGE_GAP = 16
PANEL_HEAD_PAD = (10, 16)  # vertical, horizontal
PANEL_HEAD_MIN_H = 44
PANEL_HEAD_GAP = 10
PANEL_BODY_PAD = (14, 16)
KPI_PAD = (12, 16)
CELL_PAD_H = 12  # table cells
CELL_PAD_V = 8
ROW_H = 36
BUTTON_H = 28
BUTTON_SMALL_H = 26
BUTTON_PAD_H = 12
BUTTON_SMALL_PAD_H = 10
SCROLLBAR = 10

# Font sizes (px) and weights
FONT_BASE = 13
FONT_SMALL = 12
FONT_LABEL = 11  # uppercase labels: table headers, sidebar section labels
FONT_TABLE = 13.5
FONT_TITLE = 15  # top bar line
FONT_PAGE_TITLE = 20  # top bar page name, brand
FONT_PANEL_TITLE = 14
FONT_KPI = 26
FONT_STATUS = 32
FONT_MONO = 11
DISPLAY_FROM = 20  # display face for 20px and up

# Letter spacing, in em (multiply by the pixel size)
TRACK_BASE = -0.003
TRACK_TITLE = -0.01
TRACK_HEADING = -0.015
TRACK_LABEL = 0.06  # sidebar section labels
TRACK_TH = 0.05  # table headers
TRACK_BUTTON = -0.005


def qcolor(value: str):
    """A QColor from a token, including the rgba(r, g, b, a) form with a 0-1 alpha."""
    from PySide6.QtGui import QColor

    v = value.strip()
    if v.startswith("rgba("):
        r, g, b, a = (p.strip() for p in v[5:-1].split(","))
        c = QColor(int(r), int(g), int(b))
        c.setAlphaF(float(a))
        return c
    return QColor(v)


def is_red(color) -> bool:
    """True if a colour (token string or QColor) is in the red family, ignoring alpha."""
    from PySide6.QtGui import QColor

    c = qcolor(color) if isinstance(color, str) else QColor(color)
    rgb = (c.red(), c.green(), c.blue())
    return any(rgb == (r.red(), r.green(), r.blue()) for r in map(qcolor, RED_FAMILY))


def text_color(color):
    """A colour safe for text: anything in the red family becomes TEXT."""
    from PySide6.QtGui import QColor

    c = qcolor(color) if isinstance(color, str) else QColor(color)
    return qcolor(TEXT) if is_red(c) else c


class Fonts:
    """Font families. Holds the wanted faces until fonts.load_fonts() runs; after it, each
    attribute is the family actually picked (SF Pro, else Inter, else the system font).
    Use the display face for 20px and up."""

    display = "SF Pro Display"
    text = "SF Pro Text"
    mono = "SF Mono"
    FALLBACK = "Inter"
