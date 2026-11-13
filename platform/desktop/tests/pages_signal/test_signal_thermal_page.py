"""Thermal page on FakeState: decoding through logic.py, hover, empty state, timing."""

import time

import pytest
from app.data import logic
from app.pages.thermal import ThermalPage, heat_image, stats
from app.pages.thermal.page import empty_message
from PySide6.QtCore import Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication
from signal_helpers import next_record, state, themed


def hover(widget, pos) -> None:
    ev = QMouseEvent(QMouseEvent.Type.MouseMove, pos, widget.mapToGlobal(pos), Qt.MouseButton.NoButton,
                     Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, ev)


@pytest.fixture
def page(qtbot, qapp):
    themed(qapp)
    st = state()
    p = ThermalPage()
    qtbot.addWidget(p)
    p.resize(1320, 896)
    p.bind(st)
    p.show()
    qtbot.waitExposed(p)
    return p, st


def test_page_interface(page):
    p, _ = page
    assert p.key == "thermal" and p.title == "Thermal"


def test_frames_are_decoded_by_logic(page, monkeypatch):
    p, st = page
    calls = []
    real = logic.decode_frame

    def spy(frame):
        calls.append(frame)
        return real(frame)

    monkeypatch.setattr(logic, "decode_frame", spy)
    rec = next_record(st)
    st.push("record", rec)
    assert len(calls) == len(rec["heat"]["frames"])
    # The image shows logic's pixels in logic's colours.
    px = real(rec["heat"]["frames"][p._index])["pixels"]
    lo, hi = p.current_range()
    p.view_seg.set_current("pixels")
    p.image.set_smooth(False)
    img = p.image.grab().toImage()
    for col, row in ((0, 0), (20, 12), (31, 23)):
        c = p.image.pixel_center(col, row)
        got = img.pixelColor(int(c.x()) + 2, int(c.y()) + 2)  # off the grid lines and ring centre
        want = heat_image.color_for(px[row * 32 + col], lo, hi)
        assert (got.red(), got.green(), got.blue()) == want


def test_no_second_decoder_in_these_pages():
    import pathlib

    root = pathlib.Path(heat_image.__file__).resolve().parents[1]
    for path in [*root.joinpath("thermal").glob("*.py"), *root.joinpath("spectrum").glob("*.py")]:
        text = path.read_text()
        assert "b64decode" not in text and "import base64" not in text, path


def test_frames_play_at_their_spacing(page, qtbot):
    p, st = page
    st.push("record", next_record(st))
    assert p._index == 0
    qtbot.waitUntil(lambda: p._index == 1, timeout=1500)


def test_kpis_from_the_record(page):
    p, st = page
    h = st.latest()["heat"]
    k = p.kpis
    assert k.cell(0).value() == f"{h['max_c']:.1f} °C"
    assert k.cell(1).value() == f"{h['max_c'] - h['background_c']:+.1f} °C"
    assert k.cell(5).value() == f"{round(h['scene_match'] * 100)}%"


def test_hover_reads_the_pixel(page):
    p, st = page
    view = logic.heat_view(st.latest())
    px = view["frames"][p._index]["pixels"]
    bg = view["background_c"]
    for col, row in ((3, 4), (20, 12)):
        hover(p.image, p.image.pixel_center(col, row))
        v = px[row * 32 + col]
        assert p.hover_line.text() == f"Pixel {col}, {row} · {v:.1f} °C · {v - bg:+.1f} above room"
    p.image.leaveEvent(None)
    assert p.hover_line.text() == "Hover the image to read a pixel."


def test_cells_grid_is_the_records_cells(page):
    p, st = page
    h = st.latest()["heat"]
    assert p.cells.cell_texts(0) == (f"{h['cells'][0]:.1f}", f"{h['spots'][0]:.1f}")
    assert p.cells.cell_texts(47) == (f"{h['cells'][47]:.1f}", f"{h['spots'][47]:.1f}")


@pytest.mark.parametrize(
    "heat, words",
    [
        (None, "sends no thermal data"),
        ({"source": "imu", "imu_temp_c": 42.6}, "IMU temperature 42.6 °C"),
        ({"source": "camera"}, "could not be read"),
    ],
)
def test_empty_state_without_heat(page, heat, words):
    p, st = page
    rec = next_record(st)
    if heat is None:
        rec.pop("heat")
    else:
        rec["heat"] = heat
    st.push("record", rec)
    assert p.stack.currentIndex() == 0
    assert words in p.empty_label.text()
    # And back when heat returns.
    st.push("record", _with_heat(st))
    assert p.stack.currentIndex() == 1


def _with_heat(st):
    from signal_helpers import heat_records

    rec = next_record(st)
    rec["heat"] = heat_records()[-1]["heat"]
    return rec


def test_empty_message_with_no_record():
    assert empty_message(None) == "No readings yet."


def test_fixed_range_and_grid_toggle(page):
    p, _ = page
    p.scale_seg.set_current("fixed")
    p.scale_seg.changed.emit("fixed")
    assert p.current_range() == stats.FIXED_RANGE
    assert p.bar.label_texts() == ("60 °C", "40", "20 °C")
    p.grid_toggle.click()
    assert p.image.grid() is False


def test_sim_button_sends_t(page):
    p, st = page
    p.sim_button.click()
    assert st.sim().keys == ["t"]


def test_frame_time(page):
    """Each frame is built and painted well inside its 500 ms slot (2 a second)."""
    p, st = page
    view = logic.heat_view(st.latest())
    times = []
    for k in range(60):
        p._index = k % len(view["frames"])
        t0 = time.perf_counter()
        p._redraw()
        p.image.repaint()
        times.append((time.perf_counter() - t0) * 1000)
    times.sort()
    median, worst = times[len(times) // 2], times[-1]
    print(f"\nthermal frame: median {median:.2f} ms, worst {worst:.2f} ms at {p.image.width()}x{p.image.height()}")
    assert median < 16
