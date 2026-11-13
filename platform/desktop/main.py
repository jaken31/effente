"""effente desktop console: the receiver, the simulated device and the dashboard in one window.

    python3 platform/desktop/main.py            # receiver + simulated device
    python3 platform/desktop/main.py --no-sim   # receiver only, for a real device
    python3 platform/desktop/main.py --fake     # no receiver: fixed sample data (FakeState)

Arguments match platform/run.py. Closing the window stops everything.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import signal
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLATFORM = HERE.parent
DATA = PLATFORM / "server" / "data"
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

log = logging.getLogger("effente.desktop")


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-sim", action="store_true", help="receiver only, for a real device")
    parser.add_argument("--fresh", action="store_true", help="delete stored history first")
    parser.add_argument("--case", default="normal", help="case the simulator starts in (see platform/sim/README.md)")
    parser.add_argument("--fake", action="store_true", help="no receiver: show fixed sample data (tests/fakes.py)")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

    from app.shell import MainWindow
    from app.theme import apply_theme
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv[:1])
    app.setApplicationName("effente")
    apply_theme(app)

    host = state = None
    if args.fake:
        from tests.fakes import FakeState

        state = FakeState(with_sim=not args.no_sim)
        address = None
    else:
        if args.fresh and DATA.exists():
            shutil.rmtree(DATA)
        DATA.mkdir(parents=True, exist_ok=True)
        from app.data.host import ReceiverHost
        from app.data.state import DashboardState

        host = ReceiverHost(str(DATA), args.port, not args.no_sim, args.case)
        try:
            host.start()
        except OSError as e:
            print(f"The receiver did not start on port {args.port}: {e}", file=sys.stderr)
            return 1
        state = DashboardState(host.url, host=host)
        state.start()
        address = f"{host.host}:{host.port}"

    window = MainWindow(state, receiver_address=address)
    window.show()

    # Ctrl-C, a kill or a closed terminal quit through Qt, so the cleanup below runs.
    for name in ("SIGINT", "SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), lambda *_: app.quit())
    wake = QTimer()
    wake.timeout.connect(lambda: None)  # lets Python run signal handlers
    wake.start(250)

    try:
        return app.exec()
    finally:
        stop = getattr(state, "stop", None)
        if callable(stop):
            stop()
        if host is not None:
            host.stop()


if __name__ == "__main__":
    sys.exit(main())
