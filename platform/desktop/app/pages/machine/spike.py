"""Plan item C4, the first hour: does the 3D view work inside a widget window on this laptop?

    python platform/desktop/app/pages/machine/spike.py                 # a window: drag, scroll, click
    python platform/desktop/app/pages/machine/spike.py model.glb       # your own GLB file
    python platform/desktop/app/pages/machine/spike.py --check out.png # no hands: load, draw, click the
                                                                       # middle, save a picture, exit

Shows a sample box (written by code, no file committed) inside a normal widget window with the app's
theme, beside ordinary widgets. A click on the model prints the point in the model's own coordinates
and drops a pin there. `--check` exits 0 only if the model drew and the click hit it.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DESKTOP = HERE.parents[2]
PLATFORM = DESKTOP.parent
for path in (str(DESKTOP), str(PLATFORM / "scan")):
    if path not in sys.path:
        sys.path.insert(0, path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="3D view spike")
    parser.add_argument("model", nargs="?", help="a GLB file (default: a sample box)")
    parser.add_argument("--check", metavar="PNG", help="load, draw, click the middle, save a picture, exit")
    args = parser.parse_args(argv)

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QMainWindow, QVBoxLayout, QWidget

    from app.theme import apply_theme, schemes

    schemes.apply(schemes.saved())
    app = QApplication(sys.argv[:1])
    apply_theme(app)

    from scan_glb import bounds, box_glb

    from app.pages.machine.view3d import ModelView, can_draw

    if args.model:
        model = Path(args.model)
    else:
        model = Path(tempfile.mkdtemp(prefix="effente-spike-")) / "box.glb"
        model.write_bytes(box_glb())
    file_box = bounds(str(model))

    window = QMainWindow()
    window.setWindowTitle("effente: 3D view spike")
    centre = QWidget()
    row = QHBoxLayout(centre)
    view = ModelView()
    side = QWidget()
    side.setFixedWidth(280)
    col = QVBoxLayout(side)
    title = QLabel("Parts")
    said = QLabel("Click the model.")
    said.setWordWrap(True)
    col.addWidget(title)
    col.addWidget(said)
    col.addStretch(1)
    row.addWidget(view, 1)
    row.addWidget(side)
    window.setCentralWidget(centre)
    window.resize(1000, 640)

    pins: list[dict] = []
    hits: list[tuple[float, float, float]] = []

    def on_picked(x, y, z):
        hits.append((x, y, z))
        text = f"picked {x:.4f}, {y:.4f}, {z:.4f} (model coordinates)"
        print(text, flush=True)
        said.setText(text)
        for p in pins:
            p["selected"] = False
        pins.append({"id": f"pin-{len(pins) + 1}", "x": x, "y": y, "z": z, "colour": "#d0d0d0", "ring": "#101214",
                     "selected": True, "label": f"pin {len(pins) + 1}"})
        view.set_pins(pins)

    def on_missed():
        print("missed the model", flush=True)
        said.setText("That click missed the model.")

    def on_pin(pin_id):
        print(f"pin clicked: {pin_id}", flush=True)
        said.setText(f"Clicked {pin_id}.")

    view.picked.connect(on_picked)
    view.missed.connect(on_missed)
    view.pin_clicked.connect(on_pin)
    view.status_changed.connect(lambda s: print(f"status: {s} {view.problem()}".rstrip(), flush=True))
    print(f"platform: {app.platformName()}, can draw: {can_draw()}", flush=True)
    print(f"file's box: {file_box}", flush=True)
    view.load(model, file_box)
    window.show()

    if not args.check:
        return app.exec()

    result = {"code": 1}

    def check():
        print(f"status: {view.status()} {view.problem()}".rstrip(), flush=True)
        print(f"frames drawn: {view.frames_drawn()}", flush=True)
        view.click_at(view.width() / 2, view.height() / 2)
        app.processEvents()
        image = view.image()
        colours = {image.pixel(x, y) for x in range(0, image.width(), 16) for y in range(0, image.height(), 16)}
        saved = image.save(args.check)
        print(f"picture: {image.width()} x {image.height()}, {len(colours)} colours sampled, saved: {saved}", flush=True)
        drew = view.status() == "ready" and view.frames_drawn() > 0 and len(colours) > 2
        result["code"] = 0 if drew and hits else 1
        print("CHECK PASSED" if result["code"] == 0 else "CHECK FAILED", flush=True)
        app.quit()

    QTimer.singleShot(2500, check)
    app.exec()
    return result["code"]


if __name__ == "__main__":
    sys.exit(main())
