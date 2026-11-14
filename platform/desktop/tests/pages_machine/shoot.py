"""Render the Machine page inside the shell on FakeState, once for each state it has.

    python3 platform/desktop/tests/pages_machine/shoot.py [out_dir]

Run it on a real screen, not with QT_QPA_PLATFORM=offscreen: offscreen the 3D view draws nothing, so
the pictures that show the model are left out there and the script says so. On a real screen a
window opens for each picture and closes again.

The real MainWindow at 1600x1000, with the sample data of tests/fake_scans.py and
tests/fake_partlog.py. That sample is written by hand and is all simulated, and the model is a plain
box written by code, not a scan of anything. `convert_again` is added to the fake here: it is not in
`state.scans` yet.
"""

from __future__ import annotations

import sys
import time
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
for p in (HERE.parents[1], HERE.parent):
    sys.path.insert(0, str(p))

from app.shell import MainWindow
from app.theme import apply_theme
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

W, H = 1600, 1000
NOW = datetime(2026, 11, 14, 16, 45, tzinfo=UTC)  # the fake's clock is the day it runs: pin it
POINT = (0.05624, 0.11301, 0.09)  # a point on the box's front face, as a click there returned it


def _ready(state, page):
    from fake_partlog import DRIVE_ROLLER

    state.log.select_part(DRIVE_ROLLER)


def _possible(state, page):
    from fake_partlog import MOTOR_MOUNT

    state.log.select_part(MOTOR_MOUNT)


def _nothing_found(state, page):
    from fake_partlog import BELT

    state.log.select_part(BELT)


def _adding(state, page):
    page.add_part()


def _new_part(state, page):
    page.add_part()
    page.view.picked.emit(*POINT)
    page.stage.form.name.setText("Guard plate")
    page.stage.form.kind.setCurrentIndex(page.stage.form.kind.findData("frame"))


def _placing(state, page):
    from fake_partlog import IDLE_ROLLER

    page.place(IDLE_ROLLER)


def _new_scan(state, page):
    page.toggle_scan_panel()
    page.scan_panel.name.setText("Demo fan 1")
    page.scan_panel.changed.emit()


def _no_scan(state, page):
    state.scans.clear()


def _waiting(state, page):
    state.scans.set_status("waiting")


def _converting(state, page):
    state.scans.set_status("converting")


def _failed(state, page):
    state.scans.set_status("failed", "Blender is not installed on this laptop. Install it, then convert again.")


def _cannot_draw(state, page):
    from fake_partlog import DRIVE_ROLLER

    page._can_draw = lambda: False
    state.log.select_part(DRIVE_ROLLER)


def _no_parts(state, page):
    state.log.clear()


def _no_pin(state, page):
    state.scans.set_status("converting")
    page.add_part()
    page.stage.form.name.setText("Gearbox")
    page.stage.form.kind.setCurrentIndex(page.stage.form.kind.findData("gearbox"))


# file name -> (what to do to a fresh FakeState and its Machine page before the picture, shows the model)
SHOTS = {
    "machine": (_ready, True),
    "machine-possible": (_possible, True),
    "machine-nothing-found": (_nothing_found, True),
    "machine-adding": (_adding, True),
    "machine-new-part": (_new_part, True),
    "machine-placing": (_placing, True),
    "machine-new-scan": (_new_scan, True),
    "machine-no-parts": (_no_parts, True),
    "machine-no-scan": (_no_scan, False),
    "machine-waiting": (_waiting, False),
    "machine-converting": (_converting, False),
    "machine-failed": (_failed, False),
    "machine-cannot-draw": (_cannot_draw, False),
    "machine-part-without-pin": (_no_pin, False),
}


def _settle(app, page, model: bool) -> None:
    """Let the window lay out, and the 3D view load the model and draw a few frames."""
    until = time.monotonic() + (6.0 if model else 0.4)
    while time.monotonic() < until:
        app.processEvents()
        if model and page.view is not None and page.view.frames_drawn() >= 5:
            model = False
            until = time.monotonic() + 0.4
        time.sleep(0.01)


def main(out_dir: str | None = None) -> None:
    from app.pages.machine.view3d import can_draw
    from fakes import FakeState

    app = QApplication.instance() or QApplication(sys.argv[:1])
    apply_theme(app)
    out = Path(out_dir or HERE.parents[1] / "screenshots" / "machine")
    out.mkdir(parents=True, exist_ok=True)
    for name, (prepare, model) in SHOTS.items():
        if model and not can_draw():
            print(f"left out, needs a real screen: {name}")
            continue
        state = FakeState()
        state._now = NOW.timestamp() * 1000.0
        state.scans.convert_again = lambda scan_id, s=state: s.scans.set_status("converting")
        w = MainWindow(state, receiver_address="0.0.0.0:8000")
        w.setFixedSize(W, H)  # a fixed size also keeps a tiling window manager from resizing it
        w.show()
        w.navigate("machine")
        page = w.pages["machine"]
        _settle(app, page, model)
        prepare(state, page)
        _settle(app, page, False)
        path = out / f"{name}.png"
        # one picture pixel for each window pixel, whatever the screen's scale
        w.grab().scaled(W, H, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation).save(
            str(path))
        print(path)
        w.close()
        w.deleteLater()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
