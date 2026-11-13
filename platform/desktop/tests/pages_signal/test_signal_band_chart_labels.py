"""The band chart's two top notes never overlap, at the Live preview width and narrower."""

import pytest
from app.pages.spectrum.band_chart import FILTER_NOTE, BandChart
from app.shell.window import MainWindow
from signal_helpers import state, themed


def _apart(labels) -> None:
    if "speed" in labels and "filter" in labels:
        assert not labels["speed"][0].intersects(labels["filter"][0])
        assert labels["speed"][0].right() + 16 <= labels["filter"][0].left() + 0.01


def test_live_preview_width(qtbot, qapp):
    themed(qapp)
    w = MainWindow(state())
    qtbot.addWidget(w)
    w.resize(1600, 1000)
    w.show()
    w.navigate("live")
    qtbot.waitExposed(w)
    chart = w.pages["live"].spectrum
    assert isinstance(chart, BandChart) and chart.width() > 0
    labels = chart.top_labels()
    assert labels["filter"][1] == FILTER_NOTE  # the filter note stays whole
    _apart(labels)


@pytest.mark.parametrize("width", range(300, 1401, 50))
def test_every_width(qtbot, qapp, width):
    themed(qapp)
    st = state()
    chart = BandChart()
    qtbot.addWidget(chart)
    chart.set_record(st.latest(), st.model())
    chart.resize(width, 260)
    labels = chart.top_labels()
    assert "filter" in labels
    _apart(labels)
    if "speed" in labels:
        assert labels["speed"][0].left() >= 44  # inside the plot, right of the level labels
    chart.grab()


def test_no_speed_note_with_a_running_speed(qtbot):
    st = state()
    chart = BandChart()
    qtbot.addWidget(chart)
    chart.set_record(st.latest(), st.model())
    chart.set_speed_hz(24.6)
    chart.resize(800, 260)
    assert "speed" not in chart.top_labels()
