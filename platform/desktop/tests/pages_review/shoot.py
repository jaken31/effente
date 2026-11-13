"""Render the Evidence, Agents and Device & model pages inside the shell on FakeState.

    QT_QPA_PLATFORM=offscreen python3 platform/desktop/tests/pages_review/shoot.py [out_dir]

The real MainWindow at 1600x1000, with the sample data of tests/fakes.py.
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


def main(out_dir: str | None = None) -> None:
    from fakes import FakeState

    app = QApplication.instance() or QApplication(sys.argv[:1])
    apply_theme(app)
    out = Path(out_dir or HERE.parents[1] / "screenshots" / "review")
    out.mkdir(parents=True, exist_ok=True)
    w = MainWindow(FakeState(), receiver_address="0.0.0.0:8000")
    w.resize(W, H)
    w.show()
    for key in ("evidence", "agent", "device"):
        w.navigate(key)
        for _ in range(5):
            app.processEvents()
        path = out / f"{key}.png"
        w.grab().save(str(path))
        print(path)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
