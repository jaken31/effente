"""The Machine page's 3D view as a widget: shows one GLB model, reports which point of it was clicked,
and draws a pin over each part. Qt Quick 3D inside a QQuickWidget (the one named exception to "every
chart is drawn with QPainter", docs/scan-and-log-plan.md section 7). Not a web view.

Kept thin on purpose. In offscreen mode, which the desktop tests use, the file loads but nothing is
drawn and no click hits, so everything that can be tested without a graphics card lives elsewhere.

All positions in and out are in the model's own coordinates, the GLB file's (plan 5.4).
"""

from __future__ import annotations

from pathlib import Path

import os
import sys

import PySide6

if sys.platform == "win32":
    # Windows, PySide6 6.12 from pip: Qt's 3D QML plugins (QtQuick3D, Helpers, AssetUtils and the GLB reader)
    # sit in sub-folders and need Qt6Quick3D*.dll from PySide6's own folder, which Windows does not search
    # for them: "Cannot load library ... The specified module could not be found". Loading those libraries
    # into this process first fixes it, because a library already loaded is found by name. Putting the
    # folder on the PATH did not help (seen on one Windows 11 laptop, 14 Nov).
    import ctypes

    _qt_dir = os.path.dirname(PySide6.__file__)
    for _name in ("Qt6Quick3DUtils", "Qt6Quick3DRuntimeRender", "Qt6Quick3D", "Qt6Quick3DAssetImport",
                  "Qt6Quick3DAssetUtils", "Qt6Quick3DHelpersImpl", "Qt6Quick3DHelpers"):
        try:
            ctypes.WinDLL(os.path.join(_qt_dir, _name + ".dll"))
        except OSError:
            pass  # a PySide6 without it: the view then reports its own problem()

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtWidgets import QVBoxLayout, QWidget

SCENE = Path(__file__).resolve().parent / "scene.qml"


def can_draw() -> bool:
    """False where a 3D view cannot draw at all: the offscreen and minimal platforms the tests use.
    True does not promise that this laptop's graphics work; `ModelView.problem()` says if they did not."""
    return QGuiApplication.platformName() not in ("offscreen", "minimal")


class ModelView(QWidget):
    """One model, turned by dragging and zoomed by scrolling."""

    picked = Signal(float, float, float)  # a click on the model: the point, in the model's coordinates
    missed = Signal()                     # a click beside the model
    pin_clicked = Signal(str)             # a click on a pin: its id
    status_changed = Signal(str)          # "empty", "loading", "ready" or "error"

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._quick = QQuickWidget(self)
        self._quick.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
        self._quick.setSource(QUrl.fromLocalFile(str(SCENE)))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._quick)
        self._root = self._quick.rootObject()
        if self._root is not None:
            self._root.picked.connect(self.picked)
            self._root.missed.connect(self.missed)
            self._root.pinClicked.connect(self.pin_clicked)
            self._root.statusChanged.connect(lambda: self.status_changed.emit(self.status()))
            window = self._quick.quickWindow()
            if window is not None:  # a pick needs one drawn frame after the meshes were marked
                window.afterRendering.connect(self._root.frameDrawn)

    # ---- what it shows

    def load(self, path: str | Path, box: dict | None = None) -> None:
        """Show this GLB file. `status()` moves to "ready" or "error". `box` is the scan's `model.bounds`,
        {"min": [x, y, z], "max": [x, y, z]}: the view uses it to scale the model to fit the window."""
        if self._root is not None:
            self._root.setProperty("box", dict(box) if box else None)
            self._root.setProperty("source", QUrl.fromLocalFile(str(Path(path).resolve())))

    def clear(self) -> None:
        if self._root is not None:
            self._root.setProperty("source", QUrl())

    def set_pins(self, pins: list[dict]) -> None:
        """Each pin: {"id", "x", "y", "z", "colour", "ring", "selected", "label"}, colours as "#rrggbb"."""
        if self._root is not None:
            self._root.setProperty("pins", [dict(p) for p in pins])

    def set_colours(self, background: str, label: str, label_background: str) -> None:
        if self._root is not None:
            self._root.setProperty("background", QColor(background))
            self._root.setProperty("labelColour", QColor(label))
            self._root.setProperty("labelBackground", QColor(label_background))

    def reset_camera(self) -> None:
        if self._root is not None:
            self._root.resetCamera()

    # ---- what it knows

    def status(self) -> str:
        return str(self._root.property("status")) if self._root is not None else "error"

    def problem(self) -> str:
        """Why nothing can be shown, as a plain sentence, or "" when all is well so far."""
        if self._root is None:
            errors = "; ".join(e.toString() for e in self._quick.errors())
            return f"the 3D view could not start on this laptop ({errors or 'no reason given'})"
        if self.status() == "error":
            return f"the model could not be shown: {self._root.property('error') or 'no reason given'}"
        return ""

    def frames_drawn(self) -> int:
        """Frames drawn since the model loaded. 0 means nothing has been drawn: a click cannot hit yet."""
        return int(self._root.property("frames")) if self._root is not None else 0

    def click_at(self, x: float, y: float) -> None:
        """As if the person clicked at this point of the view (for tests and the spike)."""
        if self._root is not None:
            self._root.click(float(x), float(y))

    def image(self) -> QImage:
        """What the view shows now."""
        return self._quick.grabFramebuffer()
