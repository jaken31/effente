"""A CSV that cannot be written says so in a notice instead of raising inside the click."""

from app.pages.history import HistoryPage
from app.pages.live import LivePage
from fakes import FakeState
from PySide6.QtWidgets import QFileDialog


def test_export_to_a_folder_that_is_gone_is_reported(qtbot, tmp_path, monkeypatch):
    path = str(tmp_path / "gone" / "out.csv")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (path, ""))
    for cls in (LivePage, HistoryPage):
        state = FakeState()
        said: list[str] = []
        state.action_failed.connect(said.append)
        page = cls()
        qtbot.addWidget(page)
        page.bind(state)
        page.export_dialog()
        assert len(said) == 1 and said[0].startswith("Export not saved: ")
