import os
import sys

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SERVER_DIR = os.path.dirname(TESTS_DIR)
for path in (SERVER_DIR, TESTS_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

from receiver_server import Receiver  # noqa: E402
from receiver_testkit import Client, Stream  # noqa: E402


@pytest.fixture
def make_receiver(tmp_path):
    started = []

    def make(data_dir=None, web_dir=None, **kw):
        data_dir = str(data_dir or tmp_path / "data")
        web_dir = str(web_dir or tmp_path / "web")
        receiver = Receiver("127.0.0.1", 0, data_dir, web_dir, log=lambda m: None, **kw)
        receiver.start()
        started.append(receiver)
        return receiver, Client(receiver.port)

    yield make
    for receiver in started:
        receiver.stop()


@pytest.fixture
def client(make_receiver):
    return make_receiver()[1]


@pytest.fixture
def open_stream():
    streams = []

    def opener(port):
        s = Stream(port)
        streams.append(s)
        return s

    yield opener
    for s in streams:
        s.close()
