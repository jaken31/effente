"""One window for each port (app/single.py): a second start finds the first and asks it forward."""

import socket

from app import single


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_no_window_open_answers_none(qapp):
    assert single.running(free_port(), timeout_ms=200) is None


def test_second_start_is_told_what_the_first_shows_and_brings_it_forward(qtbot, qapp):
    port = free_port()
    came = []
    listener = single.Listener(port, "the simulated device", lambda: came.append(1))
    assert listener.listening
    try:
        # The asker waits inside running(), so the listener's side runs from a timer in between.
        from PySide6.QtCore import QTimer

        answers = []
        sock_wait = QTimer()
        sock_wait.setSingleShot(True)
        sock_wait.timeout.connect(lambda: answers.append(single.running(port, timeout_ms=2000)))
        sock_wait.start(0)
        qtbot.waitUntil(lambda: bool(came), timeout=5000)
        qtbot.waitUntil(lambda: bool(answers), timeout=5000)
        assert came == [1]
        assert answers[0] in ("the simulated device", "")  # "" when the answer came after the wait
    finally:
        listener.close()
    assert single.running(port, timeout_ms=200) is None


def test_a_socket_left_by_a_killed_run_does_not_block_the_next(qapp):
    port = free_port()
    first = single.Listener(port, "x", lambda: None)
    assert first.listening
    second = single.Listener(port, "y", lambda: None)  # as after a kill: the name is still taken
    assert second.listening
    first.close()
    second.close()


def test_bring_forward_shows_a_minimised_window(qtbot):
    from PySide6.QtWidgets import QWidget

    w = QWidget()
    qtbot.addWidget(w)
    w.showMinimized()
    single.bring_forward(w)
    assert w.isVisible() and not w.isMinimized()
