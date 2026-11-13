"""Named colour schemes: sets of colour tokens that replace the defaults in tokens.py.

A scheme is applied once, before the window is built (see main.py). Widgets read
tokens when they are made or painted, so a new choice shows the next time the app
starts. The choice is kept with QSettings.

The token RED keeps its name in every scheme: it is the accent and warning colour,
whatever its hue, and the RED rule (shapes only, never text) follows it.
"""

from __future__ import annotations

from .. import settings
from . import tokens

DEFAULT = "red"
KEY = "color_scheme"


def _accent(rgb: tuple[int, int, int], hover: str, alert: str, on_primary: str) -> dict:
    """The accent colour and everything derived from it."""
    r, g, b = rgb
    dark = tokens.qcolor(on_primary)
    return {
        "RED": f"#{r:02x}{g:02x}{b:02x}",
        "PRIMARY_HOVER": hover,
        "ALERT": alert,
        "ON_PRIMARY": on_primary,
        "ACCENT_BG": f"rgba({r}, {g}, {b}, .12)",
        "LINK_HOVER_BORDER": f"rgba({r}, {g}, {b}, .5)",
        "PRIMARY_DISABLED": f"rgba({r}, {g}, {b}, .4)",
        "ON_PRIMARY_DISABLED": f"rgba({dark.red()}, {dark.green()}, {dark.blue()}, .6)",
    }


# key -> (name shown in the picker, tokens that differ from tokens.py)
SCHEMES: dict[str, tuple[str, dict]] = {
    "red": ("Red apple", {}),
    "amber": ("Amber", {
        **_accent((255, 176, 32), "#ffc24d", "#ffdc94", "#1a1000"),
        "GLOW_TOP": (255, 170, 40, 0.13),
        "GLOW_BOTTOM": (255, 150, 30, 0.05),
    }),
    "blue": ("Blue", {
        **_accent((77, 141, 255), "#6ea3ff", "#a9c7ff", "#020a1a"),
        "GLOW_TOP": (60, 120, 255, 0.15),
        "GLOW_BOTTOM": (40, 90, 255, 0.06),
    }),
    "light": ("Light", {
        "BG": "#f4f4f3",
        "SIDE": "rgba(255, 255, 255, .55)",
        "PANEL": "#ffffff",
        "RAISED": "#ececeb",
        "HOVER": "rgba(0, 0, 0, .05)",
        "NAV_ON": "rgba(0, 0, 0, .06)",
        "ASK_BG": "rgba(0, 0, 0, .03)",
        "BORDER": "rgba(0, 0, 0, .12)",
        "BORDER_STRONG": "rgba(0, 0, 0, .22)",
        "BORDER_PANEL": "rgba(0, 0, 0, .09)",
        "BORDER_TOPBAR": "rgba(0, 0, 0, .08)",
        "GRID": "rgba(0, 0, 0, .08)",
        "TEXT": "#171717",
        "SOFT": "#525252",
        "MUTED": "#6f6f6f",
        "TEXT_DISABLED": "rgba(23, 23, 23, .4)",
        "BORDER_DISABLED": "rgba(0, 0, 0, .05)",
        "TINT": (0, 0, 0),
        "CHEVRON": "chevron-dark.svg",
        "DOT": "#262626",
        "DIM": "#e0e0e0",
        "NORMAL": "#5c5c5c",
        "SCROLL_THUMB": "#c9c9c9",
        "GLOW_TOP": (255, 120, 60, 0.12),
        "GLOW_BOTTOM": (255, 90, 40, 0.06),
    }),
}

_DEFAULTS = {name: getattr(tokens, name) for _, changed in SCHEMES.values() for name in changed}
_current = DEFAULT


def names() -> list[tuple[str, str]]:
    """(key, name shown) for every scheme, in picker order."""
    return [(key, label) for key, (label, _) in SCHEMES.items()]


def current() -> str:
    """The scheme the tokens hold now."""
    return _current


def apply(key: str) -> str:
    """Put a scheme's colours into tokens; an unknown key gives the default. Returns the key used."""
    global _current
    if key not in SCHEMES:
        key = DEFAULT
    for name, value in {**_DEFAULTS, **SCHEMES[key][1]}.items():
        setattr(tokens, name, value)
    # The names that follow RED and ALERT.
    tokens.ACCENT = tokens.PRIMARY = tokens.WARN = tokens.IMBALANCE = tokens.RED
    tokens.UNKNOWN = tokens.ALERT
    tokens.RED_FAMILY = (tokens.RED, tokens.PRIMARY_HOVER, tokens.ALERT)
    _current = key
    return key


def saved() -> str:
    """The scheme chosen last time, else the default."""
    key = settings.store().value(KEY, DEFAULT)
    return key if key in SCHEMES else DEFAULT


def save(key: str) -> None:
    if key in SCHEMES:
        settings.store().setValue(KEY, key)
