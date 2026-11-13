"""Render the shell with placeholder pages at 1600x1000 into screenshots/shell/.

    QT_QPA_PLATFORM=offscreen python3 platform/desktop/tests/shell/make_screenshots.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = Path(__file__).resolve().parent
DESKTOP = HERE.parents[1]
sys.path[:0] = [str(DESKTOP), str(HERE)]

from app.shell import MainWindow
from app.theme import apply_theme
from PySide6.QtWidgets import QApplication
from shell_stub_state import StubState

OUT = DESKTOP / "screenshots" / "shell"


def main() -> None:
    app = QApplication([])
    print("fonts:", apply_theme(app))
    state = StubState()
    state.unanswered = [{"alarm_id": "a1", "answer": None}, {"alarm_id": "a2", "answer": None}]
    w = MainWindow(state, placeholders=True, receiver_address="0.0.0.0:8000")
    w.resize(1600, 1000)
    w.show()
    OUT.mkdir(parents=True, exist_ok=True)
    shots = [("live", None), ("spectrum", None), ("history", None)]
    for key, _ in shots:
        w.navigate(key)
        app.processEvents()
        w.grab().save(str(OUT / f"{key}.png"))
    # Stale freshness and a running agent.
    state.fresh = {"status": "no_readings", "age_s": 12, "platform_up": True}
    state.running = True
    w.navigate("live")
    w.refresh()
    app.processEvents()
    w.grab().save(str(OUT / "live-stale.png"))
    print("saved to", OUT)


if __name__ == "__main__":
    main()
