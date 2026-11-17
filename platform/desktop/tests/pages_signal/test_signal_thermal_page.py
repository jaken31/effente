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
        assert p.hover_line.text() == f"Pixel {col}, {row} · {v:.1f} °C · {stats.one_decimal(v - bg, sign=True)} above room"
    p.image.leaveEvent(None)
    assert p.hover_line.text() == "Hover the image to read a pixel."


def test_cells_grid_is_the_records_cells(page):
    p, st = page
    h = st.latest()["heat"]
    for i, j in ((0, 7), (47, 40)):  # shown mirrored: the cell on screen is the record's cell in the same row, other side
        assert [float(x) for x in p.cells.cell_texts(i)] == pytest.approx([h["cells"][j], h["spots"][j]], abs=0.05)


def test_one_decimal_has_no_negative_zero():
    assert stats.one_decimal(-0.04) == "0.0"
    assert stats.one_decimal(-0.04, sign=True) == "+0.0"
    assert stats.one_decimal(-0.06) == "-0.1"
    assert stats.one_decimal(2.25, sign=True) == "+2.2"


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
    assert p.is_empty()
    assert words in p.empty_label.text()
    # And back when heat returns.
    st.push("record", _with_heat(st))
    assert not p.is_empty()


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
        p.image.grab()  # forces the paint, scaling included
        times.append((time.perf_counter() - t0) * 1000)
    times.sort()
    median, worst = times[len(times) // 2], times[-1]
    print(f"\nthermal frame: median {median:.2f} ms, worst {worst:.2f} ms at {p.image.width()}x{p.image.height()}")
    assert median < 16


def test_colours_are_the_mockup_ramp():
    table = heat_image.lut()
    assert len(table) == 256
    assert table[0] == (0x0B, 0x0B, 0x0B)
    assert table[255] == (0xFF, 0x4D, 0x2E)
    # Pure grays up to white at 7/9 of the way, then red for the hottest.
    assert all(r == g == b for r, g, b in table[: 255 * 7 // 9])


def image(at_ms, level, **pixels):
    """A decoded frame at `level` degC everywhere, except the pixels given as p<index>=degC."""
    px = [level] * (stats.W * stats.H)
    for name, c in pixels.items():
        px[int(name[1:])] = c
    return {"at_ms": at_ms, "pixels": px}


def test_fastest_change_is_against_the_image_a_second_before():
    """The camera gives about 2 images a second: the change is taken over one second, not against the
    image just before (a short gap blows the camera's noise up)."""
    earlier = [image(0, 22.0), image(300, 22.0), image(1000, 22.0, p5=22.4)]
    newest = image(1300, 22.0, p5=24.0)
    assert stats.change_over_second(earlier, newest) == pytest.approx(2.0)  # against the one at 300: 2.0 degC in 1.0 s
    assert stats.fastest_change(earlier[-1], newest) == pytest.approx(1.6 / 0.3)  # what the image before would say
    late = image(1700, 22.0, p5=24.1)  # nearest to a second before is the one at 1000 (0.7 s): scaled to a second
    assert stats.change_over_second(earlier, late) == pytest.approx(1.7 / 0.7)


def test_fastest_change_needs_an_image_about_a_second_before():
    newest = image(5000, 22.0, p5=30.0)
    assert stats.change_over_second([], newest) is None
    assert stats.change_over_second([image(4700, 22.0)], newest) is None  # 0.3 s before: too close
    assert stats.change_over_second([image(1000, 22.0)], newest) is None  # 4 s before: too old
    assert stats.change_over_second([image(None, 22.0)], newest) is None
    assert stats.change_over_second([image(4000, 22.0)], image(None, 22.0)) is None
    assert stats.change_over_second([image(4000, 22.0)], None) is None


def test_the_page_keeps_the_frames_shown_in_the_last_second_and_a_half(page):
    p, _ = page
    p._recent = []
    for at in (0, 300, 1000, 1300, 2000, 2300):
        p._shown(image(at, 22.0))
    assert [f["at_ms"] for f in p._recent] == [1000, 1300, 2000, 2300]
    again = p._recent[-1]
    p._shown(again)  # the same frame, shown again
    assert len(p._recent) == 4
    p._view, p._index = dict(p._view, frames=[image(3000, 22.0, p9=23.5)]), 0  # the record on screen, one new frame
    p._redraw()
    assert p._rate == pytest.approx(1.5)  # against the frame at 2000
    p._show_empty(None)
    assert p._recent == []


def test_the_boards_own_image_is_shown_at_once_and_a_record_does_not_go_back(page):
    """state.heat_frames: an image read straight from the board is on screen as it comes; the record
    that brings the same image up to a second later changes the summary only."""
    p, st = page
    rec = next_record(st)
    st.push("record", rec)
    assert p._index == 0 and p._live is None
    live = dict(rec["heat"]["frames"][-1], at="2026-11-15T00:03:03.120Z")
    st.heat_frames.emit("another-machine", [live])
    assert p._live is None
    st.heat_frames.emit(st.current_machine(), [live])
    assert p._frame() is p._live and p._live["pixels"] == logic.decode_frame(live)["pixels"]
    assert not p._timer.isActive()  # the record's second image is not played over it
    st.push("record", next_record(st))
    assert p._frame() is p._live
    p._live_at -= 10  # the board's images stopped: the record's own play again
    st.push("record", next_record(st))
    assert p._live is None and p._index == 0 and p._frame() is p._view["frames"][0]


def test_the_boards_images_show_when_its_records_carry_no_camera_summary(page):
    """Seen on the real board on 16 Nov 2026: GET /heat gave an image every half second while every
    feature row lacked its th_* values, so the records' heat was the IMU's alone. The page shows the
    board's own image, with that image's own numbers, and says where they come from."""
    p, st = page
    summary = st.latest()["heat"]
    camera = summary["frames"][-1]
    st.push("record", next_record(st, heat={"source": "imu", "imu_temp_c": 28.3}))
    assert p.is_empty()
    live = dict(camera, at="2026-11-15T00:03:03.120Z")
    st.heat_frames.emit(st.current_machine(), [live])
    assert not p.is_empty() and p._frame() is p._live and p._view["from_image"]
    px = list(logic.decode_frame(live)["pixels"])
    assert p._view["max_c"] == max(px) and p.kpis.cell(0).value() == f"{max(px):.1f} °C"
    assert p.kpis.cell(5).value() == "–"  # the scene match is the device's to give
    st.push("record", next_record(st, heat={"source": "imu", "imu_temp_c": 28.3}))
    assert not p.is_empty() and p._frame() is p._live  # such a record does not take the image off
    st.push("record", next_record(st, heat=summary))  # a record with the camera's summary: its own numbers again
    assert not p._view.get("from_image")
    st.push("record", next_record(st, heat={"source": "imu", "imu_temp_c": 28.3}))
    p._live_at -= 60  # the board's images stopped
    st.push("record", next_record(st, heat={"source": "imu", "imu_temp_c": 28.3}))
    assert p.is_empty()


def test_a_view_from_one_image_follows_the_contracts_definitions():
    flat = [20.0] * (logic.HEAT_W * logic.HEAT_H)
    flat[5 * logic.HEAT_W + 9] = 36.0  # row 5, column 9: block row 1, block column 2
    view = logic.heat_view_from_image({"at_ms": 0, "pixels": flat})
    assert view["kind"] == "camera" and view["from_image"] and view["scene_match"] is None and not view["frozen"]
    assert (view["max_c"], view["background_c"], view["hot_row"], view["hot_col"]) == (36.0, 20.0, 5, 9)
    block = 1 * logic.HEAT_COLS + 2
    assert view["spots"][block] == 16.0 and view["cells"][block] == pytest.approx(1.0)
    assert sum(1 for c in view["cells"] if c) == 1 and logic.heat_range(view) == {"lo": 20, "hi": 36}
