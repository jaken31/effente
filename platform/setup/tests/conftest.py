"""Put platform/ (for `setup`), platform/server/ (for a real receiver) and the repo root
(for `device.agent`) on sys.path.

platform/ is not a package: it would shadow the stdlib `platform`.
"""

import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent
PLATFORM = TESTS.parents[1]
for path in (PLATFORM.parent, PLATFORM, PLATFORM / "server", TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import pytest  # noqa: E402


@pytest.fixture
def receiver(tmp_path):
    """A real receiver on a free port, with its own data dir. Yields its URL."""
    from receiver_server import Receiver

    running = Receiver("127.0.0.1", 0, str(tmp_path / "data"), str(tmp_path / "web"), log=lambda m: None)
    running.start()
    yield f"http://127.0.0.1:{running.port}"
    running.stop()


@pytest.fixture(autouse=True)
def no_env(monkeypatch):
    monkeypatch.delenv("EFFENTE_LLM_URL", raising=False)
