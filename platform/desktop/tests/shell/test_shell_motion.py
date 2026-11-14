"""Motion: off under test by default, and with it on everything still ends where it should."""

import pytest
from app.shell.notices import Notices
from app.widgets import Segmented, StatusDot, motion
from PySide6.QtCore import QRectF
from PySide6.QtWidgets import QWidget


@pytest.fixture
def moving():
    motion.set_enabled(True)
    yield
    motion.set_enabled(None)


def shown_widget(qtbot, w):
    qtbot.addWidget(w)
    w.show()
    qtbot.waitExposed(w)
    return w


def test_motion_is_off_on_the_offscreen_platform(qtbot):
    assert not motion.enabled()
    w = shown_widget(qtbot, QWidget())
    glide = motion.Glide(w)
    glide.to(1.0)
    assert glide.at == 1.0 and not glide.moving()


def test_reduce_motion_turns_it_off(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "cocoa")
    assert motion.enabled()
    monkeypatch.setenv("EFFENTE_REDUCE_MOTION", "1")
    assert not motion.enabled()


def test_glide_eases_to_its_target(qtbot, moving):
    w = shown_widget(qtbot, QWidget())
    seen = []
    glide = motion.Glide(w, 60, on_change=lambda: seen.append(glide.at))
    glide.to(1.0)
    assert glide.moving() and glide.at < 1.0
    qtbot.waitUntil(lambda: not glide.moving(), timeout=2000)
    assert glide.at == 1.0
    assert any(0.0 < v < 1.0 for v in seen)  # it passed through the middle


def test_glide_jumps_on_a_hidden_widget(qtbot, moving):
    w = QWidget()
    qtbot.addWidget(w)
    glide = motion.Glide(w)
    glide.to(1.0)
    assert glide.at == 1.0 and not glide.moving()


def test_segmented_ring_slides_to_the_chosen_option(qtbot, moving):
    s = shown_widget(qtbot, Segmented(["Low", "Balanced", "High"], current="Low"))
    low, high = QRectF(s.button("Low").geometry()), QRectF(s.button("High").geometry())
    assert s._ring() == low
    s.button("High").click()
    assert s.current() == "High"
    assert s._ring() == low  # it sets off from where it was
    qtbot.waitUntil(lambda: not s._slide.moving(), timeout=2000)
    assert s._ring() == high


def test_segmented_ring_is_on_the_chosen_option_with_motion_off(qtbot):
    s = shown_widget(qtbot, Segmented(["Low", "Balanced", "High"], current="Low"))
    s.button("High").click()
    assert s._ring() == QRectF(s.button("High").geometry())
    s.set_current(None)
    assert s._ring() is None


def test_notice_fades_in_and_out(qtbot, moving):
    host = shown_widget(qtbot, QWidget())
    host.resize(800, 600)
    stack = Notices(host)
    card = stack.show_failure("Answer not saved: receiver did not answer")
    assert card.graphicsEffect() is not None
    qtbot.waitUntil(lambda: card.graphicsEffect() is None, timeout=2000)  # solid again
    card.closed.emit()
    assert stack.notices() == []  # no longer counted, though still fading
    assert card.parent() is stack
    assert stack.isVisible()
    qtbot.waitUntil(lambda: not stack.isVisible(), timeout=2000)  # gone once the fade ends


def test_status_dot_beats_and_comes_back(qtbot, moving):
    dot = shown_widget(qtbot, StatusDot())
    dot.pulse()
    assert dot._beat.at < 1.0
    qtbot.waitUntil(lambda: not dot._beat.moving(), timeout=2000)
    assert dot._beat.at == 1.0
