"""Nav icons: the mockup's 24x24 stroke paths, rendered at 16px in any colour."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF
from PySide6.QtGui import QColor, QPainter
from PySide6.QtSvg import QSvgRenderer

PATHS = {
    "live": '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
    "spectrum": '<path d="M4 20V14M8 20V6M12 20v-9M16 20v-4M20 20V9"/>',
    "thermal": '<path d="M14 14.8V5a2 2 0 0 0-4 0v9.8a4 4 0 1 0 4 0z"/><path d="M12 11v6"/>',
    "history": '<path d="M3 3v18h18"/><path d="M7 15l4-4 3 3 5-6"/>',
    "evidence": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4M8 11h6M11 8v6"/>',
    "agent": '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M8 13h8M8 17h5"/>',
    "device": '<rect x="6" y="2" width="12" height="20" rx="2"/><path d="M11 18h2"/>',
    "machine": '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M4 7.5l8 4.5 8-4.5M12 12v9"/>',
    "log": '<path d="M9 6h11M9 12h11M9 18h11"/><path d="M4 6h1M4 12h1M4 18h1"/>',
}

_cache: dict[tuple[str, str], QSvgRenderer] = {}


def renderer(key: str, color: QColor) -> QSvgRenderer:
    name = color.name(QColor.NameFormat.HexArgb)
    r = _cache.get((key, name))
    if r is None:
        rgb, alpha = color.name(), color.alphaF()
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{rgb}" '
            f'stroke-opacity="{alpha:.3f}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
            f"{PATHS[key]}</svg>"
        )
        r = QSvgRenderer(QByteArray(svg.encode()))
        _cache[(key, name)] = r
    return r


def paint_icon(p: QPainter, key: str, rect: QRectF, color: QColor) -> None:
    renderer(key, color).render(p, rect)
