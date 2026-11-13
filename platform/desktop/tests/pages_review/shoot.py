"""Render the Evidence, Agents and Device & model pages on FakeState to PNGs.

    QT_QPA_PLATFORM=offscreen python3 platform/desktop/tests/pages_review/shoot.py [out_dir]

Each page is placed where the shell puts it in a 1600x1000 window (after the
232px sidebar, the 56px top bar and 24px of padding) on the page background.
The sidebar and top bar are left empty: this renders the page alone.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = Path(__file__).resolve().parent
for p in (HERE.parents[1], HERE.parent):
    sys.path.insert(0, str(p))

from app.theme import apply_theme
from app.theme import tokens as t
from PySide6.QtWidgets import QApplication, QWidget

W, H = 1600, 1000
PAGE_X = t.SIDEBAR_W + t.CONTENT_PAD
PAGE_Y = t.TOPBAR_H + t.CONTENT_PAD


def render(page_cls, state, out: Path, prepare=None) -> Path:
    frame = QWidget()
    frame.setObjectName("frame")
    frame.setStyleSheet(f"#frame {{ background: {t.BG}; }}")
    frame.resize(W, H)
    page = page_cls(frame)
    page.setGeometry(PAGE_X, PAGE_Y, W - PAGE_X - t.CONTENT_PAD, H - PAGE_Y - t.CONTENT_PAD)
    page.bind(state)
    if prepare:
        prepare(page)
    frame.show()
    for _ in range(5):
        QApplication.processEvents()
    frame.grab().save(str(out))
    frame.close()
    return out


def main(out_dir: str | None = None) -> None:
    from app.pages.agents import AgentsPage
    from app.pages.device import DevicePage
    from app.pages.evidence import EvidencePage
    from fakes import FakeState

    app = QApplication.instance() or QApplication(sys.argv[:1])
    apply_theme(app)
    out = Path(out_dir or HERE.parents[1] / "screenshots" / "review")
    out.mkdir(parents=True, exist_ok=True)
    state = FakeState()
    for name, cls in (("evidence", EvidencePage), ("agent", AgentsPage), ("device", DevicePage)):
        print(render(cls, state, out / f"{name}.png"))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
