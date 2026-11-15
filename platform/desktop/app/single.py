"""One effente window for each receiver port.

A second start on the same port used to end with "the receiver did not start".
Now it asks the window already open to come forward, and ends. The two find each
other through a local socket named after the port (QLocalServer), so nothing
here uses the network.

Not seen on a screen: that the window comes to the front on macOS and Linux. The
exchange itself is run in tests/shell/test_shell_single.py.
"""

from __future__ import annotations

from PySide6.QtCore import QObject
from PySide6.QtNetwork import QLocalServer, QLocalSocket

ASK_MS = 500


def name(port: int) -> str:
    return f"effente-desktop-{port}"


def running(port: int, timeout_ms: int = ASK_MS) -> str | None:
    """Ask a window already open on `port` to come forward.
    What it says it is showing ("" when it said nothing), or None when there is no window."""
    sock = QLocalSocket()
    sock.connectToServer(name(port))
    if not sock.waitForConnected(timeout_ms):
        return None
    showing = ""
    if sock.waitForReadyRead(timeout_ms):
        showing = bytes(sock.readAll().data()).decode("utf-8", errors="replace")
    sock.disconnectFromServer()
    return showing


class Listener(QObject):
    """The open window's side: answers `running()` with `showing` and calls `come_forward`."""

    def __init__(self, port: int, showing: str, come_forward, parent=None):
        super().__init__(parent)
        self.showing = showing
        self.come_forward = come_forward
        QLocalServer.removeServer(name(port))  # a socket file left behind by a run that was killed
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._asked)
        self.listening = self.server.listen(name(port))

    def _asked(self) -> None:
        while (asker := self.server.nextPendingConnection()) is not None:
            asker.write(self.showing.encode("utf-8"))
            asker.flush()
            asker.disconnectFromServer()
            asker.disconnected.connect(asker.deleteLater)
            self.come_forward()

    def close(self) -> None:
        self.server.close()


def bring_forward(window) -> None:
    """Un-minimise the window and put it in front."""
    from PySide6.QtCore import Qt

    window.setWindowState(window.windowState() & ~Qt.WindowState.WindowMinimized)
    window.show()
    window.raise_()
    window.activateWindow()
