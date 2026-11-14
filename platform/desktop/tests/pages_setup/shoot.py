"""Render the Setup page inside the shell on FakeState, once for each state it has.

    QT_QPA_PLATFORM=offscreen python3 platform/desktop/tests/pages_setup/shoot.py [out_dir]

The real MainWindow 1600 wide, with the sample of tests/fake_setup.py, written by
hand. The page is taller than 1000, so the window grows until every step shows.
Offscreen, as the other pages' pictures: a tiling window manager would resize a real window.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = Path(__file__).resolve().parent
for p in (HERE.parents[1], HERE.parent):
    sys.path.insert(0, str(p))

from app.shell import MainWindow
from app.theme import apply_theme
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

W, H = 1600, 1000


def _first_time(state, page):
    """A machine with no profile and nobody named yet: what opens by itself."""
    state.setup.clear()


def _answered(state, page):
    state.setup.clear()
    state.setup.set_local_profile("Sam", "technician")
    typed = {"machine_type": "a belt conveyor", "mounting": "bolted to the floor"}
    for a in page.questions.items:
        if a.q.id in typed:
            a.text.setText(typed[a.q.id])
        elif a.q.id in ("drive",):
            a.text.setText("through a belt")
        else:
            a.dont_know.setChecked(True)


def _running(state, page):
    _answered(state, page)
    page.questions.fill_button.click()


def _standin_result(state, page):
    """No model server: the stand-in filled three fields, labelled. Not confirmed yet."""
    _running(state, page)
    state.setup.finish_run(ok=True)


def _agent_failed(state, page):
    _running(state, page)
    state.setup.finish_run(ok=False, reason="the agent wrote a speed that is not in your answers, twice")


def _all_empty(state, page):
    from fake_setup import sample_choice

    state.setup.clear()
    state.setup.set_local_profile("Sam", "technician")
    state.setup.set_choice(dict(sample_choice(), library_entry="generic_rotating", status="unverified",
                                reason="machine type not given, so the general one"))


def _no_device(state, page):
    _standin_result(state, page)
    page.use()
    state.setup.set_entries("conveyor", None)
    state.device_id = lambda: None


def _not_answered(state, page):
    _standin_result(state, page)
    page.use()
    state.setup.set_entries("conveyor", None)


def _differs(state, page):
    """The sample: confirmed, and the device still reports its own entry."""


def _taken(state, page):
    state.setup.set_entries("conveyor", "conveyor")


def _lookup_failed(state, page):
    state.setup.set_lookup_problem("The starting model could not be looked up: no library entry could be read")


def _local_model(state, page):
    _answered(state, page)
    state.set_model_status("ready", "Running with small.gguf.", True, "small.gguf")


# file name -> what to do to a fresh FakeState and its Setup page before the picture
SHOTS = {
    "setup-first-time": _first_time,
    "setup-answered": _answered,
    "setup-local-model": _local_model,
    "setup-running": _running,
    "setup-standin-result": _standin_result,
    "setup-agent-failed": _agent_failed,
    "setup-all-empty": _all_empty,
    "setup-no-device": _no_device,
    "setup-device-not-answered": _not_answered,
    "setup-device-differs": _differs,
    "setup-device-taken": _taken,
    "setup-lookup-failed": _lookup_failed,
}


def main(out_dir: str | None = None, only: str | None = None) -> None:
    from fakes import FakeState

    app = QApplication.instance() or QApplication(sys.argv[:1])
    apply_theme(app)
    out = Path(out_dir or HERE.parents[1] / "screenshots" / "setup")
    out.mkdir(parents=True, exist_ok=True)
    for name, prepare in SHOTS.items():
        if only and only not in name:
            continue
        state = FakeState()
        w = MainWindow(state, receiver_address="0.0.0.0:8000")
        w.resize(W, H)
        w.show()
        w.navigate("setup")
        prepare(state, w.pages["setup"])
        w.pages["setup"].refresh()
        for _ in range(5):
            app.processEvents()
        # Grow the window until the page fits, so the picture shows every step.
        extra = w.stack.currentWidget().verticalScrollBar().maximum()
        if extra:
            w.resize(W, H + extra)
            for _ in range(5):
                app.processEvents()
        path = out / f"{name}.png"
        # At one image pixel to one window pixel, as the other pages' pictures, on any screen.
        shot = w.grab().scaled(w.size(), Qt.AspectRatioMode.IgnoreAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
        shot.save(str(path))
        print(path)
        w.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None, sys.argv[2] if len(sys.argv) > 2 else None)
