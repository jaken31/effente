"""Motion: off under test by default, and with it on everything still ends where it should."""

import pytest
from app.shell.notices import Notices
from app.widgets import Button, Segmented, StatusDot, motion
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QEnterEvent
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget


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


# ---- set_shown: a card that comes and goes ----------------------------------------


def host_with_card(qtbot):
    host = QWidget()
    card = QWidget(host)
    card.setGeometry(10, 10, 80, 40)
    host.resize(120, 80)
    shown_widget(qtbot, host)
    return host, card


def still(w) -> bool:
    return w.graphicsEffect() is None


def test_set_shown_fades_out_before_it_hides(qtbot, moving):
    _host, card = host_with_card(qtbot)
    motion.set_shown(card, False)
    assert card.isVisible() and not still(card)  # still there, on its way out
    qtbot.waitUntil(card.isHidden, timeout=2000)
    assert still(card)


def test_set_shown_settles_in_and_ends_plain(qtbot, moving):
    _host, card = host_with_card(qtbot)
    card.hide()
    motion.set_shown(card, True)
    assert card.isVisible() and isinstance(card.graphicsEffect(), motion.Lift)
    assert card.graphicsEffect().opacity == 0.0  # clear at first: it never flashes in whole
    qtbot.waitUntil(lambda: still(card), timeout=2000)
    assert card.isVisible()


def test_set_shown_asked_twice_moves_once(qtbot, moving):
    _host, card = host_with_card(qtbot)
    card.hide()
    motion.set_shown(card, True)
    first = card.graphicsEffect()
    motion.set_shown(card, True)  # a page that redraws every second asks again and again
    assert card.graphicsEffect() is first
    qtbot.waitUntil(lambda: still(card), timeout=2000)
    motion.set_shown(card, True)
    assert still(card)


def test_set_shown_turned_back_halfway_ends_shown(qtbot, moving):
    _host, card = host_with_card(qtbot)
    motion.set_shown(card, False)
    qtbot.wait(40)
    motion.set_shown(card, True)
    qtbot.waitUntil(lambda: still(card), timeout=2000)
    qtbot.wait(motion.EXIT + 60)  # the fade that was under way must not hide it after all
    assert card.isVisible()


def test_set_shown_is_plain_set_visible_with_motion_off(qtbot):
    _host, card = host_with_card(qtbot)
    motion.set_shown(card, False)
    assert card.isHidden() and still(card)
    motion.set_shown(card, True)
    assert card.isVisible() and still(card)


def test_set_shown_follows_a_plain_hide(qtbot, moving):
    _host, card = host_with_card(qtbot)
    motion.set_shown(card, False)
    qtbot.waitUntil(card.isHidden, timeout=2000)
    motion.set_shown(card, True)
    qtbot.waitUntil(lambda: still(card), timeout=2000)
    assert card.isVisible()


# ---- Button: eased hover, a dip while held ------------------------------------------


def point_at(w, on: bool) -> None:
    w.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, on)
    c = QPointF(w.rect().center())
    QApplication.sendEvent(w, QEnterEvent(c, c, c) if on else QEvent(QEvent.Type.Leave))


def button_in_host(qtbot, **kw):
    host = QWidget()
    lay = QVBoxLayout(host)
    b = Button("Add entry", **kw)
    lay.addWidget(b)
    shown_widget(qtbot, host)
    b.host = host  # held here: nothing else keeps the host alive
    return b


def test_button_hover_eases_in_and_out(qtbot, moving):
    b = button_in_host(qtbot)
    point_at(b, True)
    assert b._hover.moving() and b._hover.at < 1.0
    assert not b.grab().isNull()  # paints part way
    qtbot.waitUntil(lambda: b._hover.at == 1.0, timeout=2000)
    point_at(b, False)
    qtbot.waitUntil(lambda: b._hover.at == 0.0, timeout=2000)


def test_button_dips_while_held_and_comes_back(qtbot, moving):
    b = button_in_host(qtbot)
    clicks = []
    b.clicked.connect(lambda: clicks.append(1))
    qtbot.mousePress(b, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: b._down.at == 1.0, timeout=2000)
    qtbot.mouseRelease(b, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: b._down.at == 0.0, timeout=2000)
    assert clicks == [1]


def test_button_at_rest_paints_what_the_stylesheet_says(qtbot):
    """The button draws itself; at rest that must be the stylesheet's look, pixel for pixel."""
    from PySide6.QtWidgets import QPushButton

    for kind in ("primary", "secondary", "ghost"):
        for small in (False, True):
            host = QWidget()
            lay = QVBoxLayout(host)
            ours = Button("Add entry", kind, small=small)
            plain = QPushButton("Add entry")
            plain.setProperty("kind", kind)
            plain.setProperty("small", "true" if small else "false")
            plain.setFont(ours.font())
            lay.addWidget(ours)
            lay.addWidget(plain)
            shown_widget(qtbot, host)
            assert ours.size() == plain.size()
            assert ours.grab().toImage() == plain.grab().toImage(), (kind, small)


def test_a_disabled_button_does_not_light_up(qtbot, moving):
    b = button_in_host(qtbot)
    b.setEnabled(False)
    point_at(b, True)
    assert b._hover.at == 0.0 and not b._hover.moving()
