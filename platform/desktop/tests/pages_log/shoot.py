"""Render the Log page inside the shell on FakeState, once for each state it has.

    QT_QPA_PLATFORM=offscreen python3 platform/desktop/tests/pages_log/shoot.py [out_dir]

The real MainWindow at 1600x1000, with the sample data of tests/fake_partlog.py.
That sample is written by hand and is all simulated.
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
from PySide6.QtWidgets import QApplication

W, H = 1600, 1000


def _whole_machine(state, page):
    state.log.select_part(None)


def _part(state, page):
    from fake_partlog import DRIVE_ROLLER

    state.log.select_part(DRIVE_ROLLER)


def _nothing_found(state, page):
    from fake_partlog import BELT

    state.log.select_part(BELT)


def _no_entries(state, page):
    from fake_partlog import IDLE_ROLLER

    state.log.select_part(IDLE_ROLLER)


def _answering(state, page):
    from fake_partlog import OPEN_ALARM

    state.log.select_part(None)
    page.alarms_panel.open_form(OPEN_ALARM)


def _review_running(state, page):
    from fake_partlog import DRIVE_ROLLER

    state.log.select_part(DRIVE_ROLLER)
    state.log.start_review(DRIVE_ROLLER)


def _review_failed(state, page):
    from fake_partlog import DRIVE_ROLLER

    state.log.select_part(DRIVE_ROLLER)
    state.log._reviews.append(dict(state.log._reviews[-1], ok=False, review=None,
                                   reason="the review named a part that is not in this part's history"))
    state.log.reviews_changed.emit()


def _agree(state, page):
    from fake_partlog import DRIVE_ROLLER

    state.log.select_part(DRIVE_ROLLER)
    page.agree()


def _no_parts(state, page):
    state.log.clear()


# file name -> what to do to a fresh FakeState and its Log page before the picture
SHOTS = {
    "log": _whole_machine,
    "log-part": _part,
    "log-nothing-found": _nothing_found,
    "log-no-entries": _no_entries,
    "log-answering": _answering,
    "log-review-running": _review_running,
    "log-review-failed": _review_failed,
    "log-agree": _agree,
    "log-no-parts": _no_parts,
}


def main(out_dir: str | None = None) -> None:
    from fakes import FakeState

    app = QApplication.instance() or QApplication(sys.argv[:1])
    apply_theme(app)
    out = Path(out_dir or HERE.parents[1] / "screenshots" / "log")
    out.mkdir(parents=True, exist_ok=True)
    for name, prepare in SHOTS.items():
        state = FakeState()
        w = MainWindow(state, receiver_address="0.0.0.0:8000")
        w.resize(W, H)
        w.show()
        w.navigate("log")
        prepare(state, w.pages["log"])
        for _ in range(5):
            app.processEvents()
        path = out / f"{name}.png"
        w.grab().save(str(path))
        print(path)
        w.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
