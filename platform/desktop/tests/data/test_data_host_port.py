"""The receiver's port: a program already answering on 127.0.0.1 counts as the port being taken."""

import errno
import socket

import pytest

pytest.importorskip("PySide6")

from app.data.host import ReceiverHost, answers_here


def test_port_held_on_this_laptops_own_address_is_refused(tmp_path):
    with socket.socket() as other:
        other.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        other.bind(("127.0.0.1", 0))
        other.listen()
        port = other.getsockname()[1]
        assert answers_here(port)
        host = ReceiverHost(str(tmp_path), port)
        with pytest.raises(OSError) as e:
            host.start()
        assert e.value.errno == errno.EADDRINUSE
        assert host.receiver is None
    assert not answers_here(port)
