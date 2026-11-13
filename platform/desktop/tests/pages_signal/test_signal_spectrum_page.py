"""Spectrum page and the shared band chart, on FakeState."""

import pytest
from app.pages.spectrum import BandChart, SpectrumPage, maths
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication
from signal_helpers import next_record, state, themed


def hover(widget, pos: QPointF) -> None:
    ev = QMouseEvent(QMouseEvent.Type.MouseMove, pos, widget.mapToGlobal(pos), Qt.MouseButton.NoButton,
                     Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, ev)


@pytest.fixture
def page(qtbot, qapp):
    themed(qapp)
    st = state()
    p = SpectrumPage()
    qtbot.addWidget(p)
    p.resize(1320, 896)
    p.bind(st)
    p.show()
    qtbot.waitExposed(p)
    return p, st


def test_page_interface(page):
    p, _ = page
    assert p.key == "spectrum" and p.title == "Spectrum" and p.subtitle == "what the sensor hears"


def test_chart_shows_newest_record_with_model_edges(page):
    p, st = page
    assert p.chart.spectrum() == pytest.approx(st.latest()["spectrum"])
    assert p.chart.edges() == st.model()["spectrum_edges_hz"]


def test_hover_readout_matches_the_band(page):
    p, st = page
    rec = st.latest()
    base, _n = maths.baseline_from(st.records(limit=300, spectrum=True))
    for i in (0, 6, 19):
        hover(p.chart, p.chart.band_center(i))
        edges = st.model()["spectrum_edges_hz"]
        d = rec["spectrum"][i] - base[i]
        want = (f"Band {i + 1} · {edges[i]:.1f} to {edges[i + 1]:.1f} Hz · level {rec['spectrum'][i]:.2f}"
                f" · baseline {base[i]:.2f} · {10 ** d:.1f}× baseline")
        assert p.hover_text() == want
    p.chart.leaveEvent(None)
    assert p.hover_text() == "Hover a band to see its range and level."


def test_table_sorted_by_change_and_use_current_as_baseline(page):
    p, st = page
    diffs = [float(p.model.item(r, 4).text()) for r in range(p.model.rowCount())]
    assert diffs == sorted(diffs, reverse=True)
    assert p.model.rowCount() == maths.TABLE_ROWS
    p.base_button.click()
    assert p.chart.baseline() == pytest.approx(st.latest()["spectrum"])
    assert all(p.model.item(r, 4).text() == "+0.0" for r in range(p.model.rowCount()))
    assert "baseline set at" in p.table_panel.hint()


def test_new_record_redraws(page):
    p, st = page
    rec = next_record(st, spectrum=[-3.0] * 20)
    st.push("record", rec)
    assert p.chart.spectrum() == pytest.approx([-3.0] * 20)


def test_band_chart_alone_for_the_live_page(qtbot):
    st = state()
    chart = BandChart(hover=False)
    qtbot.addWidget(chart)
    chart.bind(st)
    assert chart.spectrum() == pytest.approx(st.latest()["spectrum"])
    assert chart.baseline() is not None
    chart.set_record(None, None)
    chart.resize(600, 300)
    chart.grab()  # paints the "no spectrum" state without error


def test_band_edges_clear_for_a_machine_without_a_model(qtbot):
    st = state()
    chart = BandChart(hover=False)
    qtbot.addWidget(chart)
    chart.set_record(st.latest(), st.model())
    assert chart.edges() is not None
    chart.set_record(st.latest(), None)  # e.g. a historical machine with no device assigned now
    assert chart.edges() is None  # no Hz labels left over from the previous machine's model
