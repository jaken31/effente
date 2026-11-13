"""Font loading: SF Pro, falling back to Inter, then to the system font.

Call load_fonts() once after the QApplication exists. It fills tokens.Fonts with the
families actually picked and logs them. Then build fonts with font().
"""

from __future__ import annotations

import logging

from PySide6.QtGui import QFont, QFontDatabase

from . import tokens
from .tokens import Fonts

log = logging.getLogger("effente.desktop.fonts")

_WANTED = {
    "display": ("SF Pro Display", "SF Pro"),
    "text": ("SF Pro Text", "SF Pro"),
    "mono": ("SF Mono",),
}
_loaded = False


def _pick(candidates: tuple[str, ...], families: set[str], system: str) -> str:
    for name in (*candidates, Fonts.FALLBACK):
        if name in families:
            return name
    return system


def load_fonts() -> dict[str, str]:
    """Pick and record the families. Returns {"display": ..., "text": ..., "mono": ...}."""
    global _loaded
    families = set(QFontDatabase.families())
    system = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()
    system_mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()
    picked = {
        "display": _pick(_WANTED["display"], families, system),
        "text": _pick(_WANTED["text"], families, system),
        "mono": _pick(_WANTED["mono"], families, system_mono),
    }
    Fonts.display, Fonts.text, Fonts.mono = picked["display"], picked["text"], picked["mono"]
    _loaded = True
    log.info("fonts: display=%s, text=%s, mono=%s", *picked.values())
    return picked


def font(
    px: float = tokens.FONT_BASE,
    weight: int = 400,
    *,
    display: bool | None = None,
    mono: bool = False,
    tracking: float = 0.0,
    caps: bool = False,
) -> QFont:
    """A font at a pixel size. `display` defaults to True from 20px up. `tracking` is
    letter spacing in em, as in the mockup's CSS."""
    if not _loaded:
        load_fonts()
    if mono:
        family = Fonts.mono
    elif display if display is not None else px >= tokens.DISPLAY_FROM:
        family = Fonts.display
    else:
        family = Fonts.text
    f = QFont(family)
    f.setPixelSize(max(1, round(px)))
    f.setWeight(QFont.Weight(weight))
    if tracking:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, tracking * px)
    if caps:
        f.setCapitalization(QFont.Capitalization.AllUppercase)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return f


# Named roles used by the widgets and the shell.
def body() -> QFont:
    return font(tokens.FONT_BASE, tracking=tokens.TRACK_BASE)


def small() -> QFont:
    return font(tokens.FONT_SMALL)


def label_caps() -> QFont:
    """Sidebar section labels: small uppercase with letter spacing."""
    return font(tokens.FONT_LABEL, 600, tracking=tokens.TRACK_LABEL, caps=True)


def table_header() -> QFont:
    return font(tokens.FONT_LABEL, 600, tracking=tokens.TRACK_TH, caps=True)


def panel_title() -> QFont:
    return font(tokens.FONT_PANEL_TITLE, 600, tracking=tokens.TRACK_HEADING)


def page_title() -> QFont:
    return font(tokens.FONT_PAGE_TITLE, 700, tracking=tokens.TRACK_TITLE)


def kpi_value() -> QFont:
    return font(tokens.FONT_KPI, 400, display=True, tracking=tokens.TRACK_HEADING)


def big_value(px: float = 21) -> QFont:
    """Values like the signals table's "Now" column and the status meta."""
    return font(px, 600, display=True, tracking=tokens.TRACK_HEADING)


def button() -> QFont:
    return font(tokens.FONT_BASE, 500, tracking=tokens.TRACK_BUTTON)


def chip() -> QFont:
    return font(tokens.FONT_SMALL, 500, tracking=tokens.TRACK_BUTTON)


def mono() -> QFont:
    return font(tokens.FONT_MONO, mono=True)
