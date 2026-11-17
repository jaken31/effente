"""effente desktop console: the receiver, the simulated device and the dashboard in one window.

    python3 platform/desktop/main.py            # receiver + simulated device, and any board on USB
    python3 platform/desktop/main.py --no-sim   # receiver only, for a real device
    python3 platform/desktop/main.py --device http://10.110.213.214:8765
                                                # a real device, read from its own port
    python3 platform/desktop/main.py --fake     # no receiver: fixed sample data (FakeState)
    python3 platform/desktop/main.py --no-picker   # do not ask which device at start

At start a window lists the devices found on the laptop's network, with "No device"
(app/shell/device_picker.py). It is not shown with --device, --fake or --no-picker.

Arguments match platform/run.py. Closing the window stops everything, the model
server included if the app started it (Agents page).
`python3 platform/desktop/install_launcher.py` adds it to the app launcher (Linux, macOS).
"""

from __future__ import annotations

import argparse
import errno
import logging
import os
import shutil
import signal
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLATFORM = HERE.parent
REPO = PLATFORM.parent
DATA = PLATFORM / "server" / "data"


def set_aside(data: Path) -> Path:
    """Move the stored history out of the way and give its new place. Nothing is deleted: --fresh
    used to remove the folder, which also held a real device's records, scans and alarm answers."""
    kept = data.parent / "kept"
    kept.mkdir(exist_ok=True)
    place = Path(tempfile.mkdtemp(prefix=time.strftime("%Y%m%d-%H%M%S-"), dir=kept)) / "data"
    shutil.move(str(data), str(place))
    return place
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

log = logging.getLogger("effente.desktop")


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-sim", action="store_true", help="receiver only, for a real device")
    parser.add_argument("--device", metavar="URL",
                        help="read a real device from its own port, for example http://10.110.213.214:8765 "
                             "(platform/link); no simulated device")
    parser.add_argument("--no-usb", action="store_true",
                        help="do not look for a board plugged into this laptop (platform/link/usb_link.py)")
    parser.add_argument("--no-picker", action="store_true",
                        help="do not show the pairing window at start (as with --device or --fake)")
    parser.add_argument("--fresh", action="store_true", help="start with no stored history; the old history is moved to platform/server/kept/, not deleted")
    parser.add_argument("--case", default="normal", help="case the simulator starts in (see platform/sim/README.md)")
    parser.add_argument("--fake", action="store_true", help="no receiver: show fixed sample data (tests/fakes.py)")
    parser.add_argument("--no-motion", action="store_true", help="no animations (as EFFENTE_REDUCE_MOTION=1)")
    parser.add_argument("--scheme", help="colour scheme for this run: red, amber, blue or light (default: the saved one)")
    return parser.parse_args(argv)


def showing(args) -> str:
    """What this start would show, as the words another start is told (app/single.py)."""
    if args.device:
        return f"the device at {args.device}"
    return "a real device, with no simulated one" if args.no_sim else "the simulated device"


def have_pyside(argv) -> bool:
    """Whether this Python has PySide6. When it does not and the repo has a .venv, the app
    starts again with that one, so `python3 platform/desktop/main.py` works whichever
    python3 comes first on PATH. Otherwise says what to install."""
    try:
        import PySide6  # noqa: F401
        return True
    except ImportError:
        pass
    venv = REPO / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if venv.is_file() and not os.environ.get("EFFENTE_VENV_TRIED"):
        os.environ["EFFENTE_VENV_TRIED"] = "1"  # so a .venv without PySide6 does not loop
        sys.stdout.flush()
        os.execv(str(venv), [str(venv), str(HERE / "main.py"), *argv])
    print(f"The desktop app needs PySide6, and this Python ({sys.executable}) does not have it.\n"
          f"Install it:  {sys.executable} -m pip install -r platform/desktop/requirements.txt\n"
          f"A virtual environment at {REPO / '.venv'} is found by itself.", file=sys.stderr)
    return False


def restart(argv) -> None:
    """Start the app again with the same arguments: how a new size shows (app/zoom.py).
    Called after the receiver has stopped, so the new start finds the port free.
    Does not return."""
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(sys.executable, [sys.executable, str(HERE / "main.py"), *argv])


def main(argv=None) -> int:
    args = parse_args(argv)
    argv = sys.argv[1:] if argv is None else list(argv)
    if not have_pyside(argv):
        return 1
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    if args.no_motion:
        os.environ["EFFENTE_REDUCE_MOTION"] = "1"

    from app import settings, zoom
    from app.theme import apply_theme, schemes

    # Before app.shell is imported: some widgets read colour tokens at import.
    schemes.apply(args.scheme or schemes.saved())
    zoom.apply(zoom.saved())  # before the QApplication: Qt reads its scale factor once

    from app.shell import MainWindow
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication(sys.argv[:1])
    app.setApplicationName("effente")
    app.setDesktopFileName("effente")  # the launcher entry's name: see install_launcher.py
    app.setWindowIcon(QIcon(str(HERE / "app" / "theme" / "effente.svg")))
    apply_theme(app)

    listener, shown = None, []  # shown: the main window, once there is one
    # One window for each port: a second start brings the first forward and ends.
    if not args.fake and args.port:
        from app import single

        already = single.running(args.port)
        if already is not None:
            if already and already != showing(args):
                QMessageBox.information(
                    None, "effente is already open",
                    f"effente is already open on port {args.port}, showing {already}.\n\n"
                    f"Close it first to show {showing(args)}.")
            return 0
        # Listening from here, before the receiver and the window are up, so two starts
        # at nearly the same moment still find each other.
        listener = single.Listener(args.port, showing(args),
                                   lambda: shown and single.bring_forward(shown[0]), parent=app)

    if not (args.fake or args.device or args.no_picker):
        from app.data import device_address, devices
        from app.shell.device_picker import DevicePicker
        from PySide6.QtWidgets import QDialog

        picker = DevicePicker(with_sim=not args.no_sim,
                              last=str(settings.store().value(device_address.ADDRESS_KEY, "") or ""))
        if picker.exec() != QDialog.DialogCode.Accepted:
            return 0  # closed without choosing: the app does not start
        args.device = picker.device_url()  # None for "No device"
        picked = devices.host_of(args.device)
        if picked and picked != devices.THIS_LAPTOP:  # found again next time, as one connected on the Device page
            settings.store().setValue(device_address.ADDRESS_KEY, picked)

    host = state = None
    if args.fake:
        from tests.fakes import FakeState

        state = FakeState(with_sim=not args.no_sim)
        address = None
    else:
        if args.fresh and DATA.exists():
            log.info("stored history moved to %s", set_aside(DATA))
        DATA.mkdir(parents=True, exist_ok=True)
        from app.data.host import ReceiverHost
        from app.data.state import DashboardState

        with_sim = not args.no_sim and not args.device
        host = ReceiverHost(str(DATA), args.port, with_sim, args.case, device_url=args.device,
                            log=log.info if args.device else None,
                            usb=not args.no_usb and not args.device, usb_log=log.info)
        try:
            host.start()
        except OSError as e:
            text = f"The receiver did not start on port {args.port}: {e.strerror or e}"
            print(text, file=sys.stderr)
            # Started from the launcher there is no terminal to read that in.
            busy = e.errno == errno.EADDRINUSE
            QMessageBox.critical(None, "effente did not start",
                                 text + ("\n\nAnother program has the port: platform/run.py in a terminal, "
                                         "or something else. Close it, or start with --port." if busy else ""))
            return 1
        state = DashboardState(host.url, host=host, settings=settings.store())
        state.start()
        address = f"{host.host}:{host.port}"

    window = MainWindow(state, receiver_address=address, settings=settings.store())
    window.show()
    shown.append(window)

    # Ctrl-C, a kill or a closed terminal quit through Qt, so the cleanup below runs.
    for name in ("SIGINT", "SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), lambda *_: app.quit())
    wake = QTimer()
    wake.timeout.connect(lambda: None)  # lets Python run signal handlers
    wake.start(250)

    try:
        code = app.exec()
    finally:
        if listener is not None:
            listener.close()
        stop = getattr(state, "stop", None)
        if callable(stop):
            stop()
        if host is not None:
            host.stop()
    if window.restart_wanted:
        settings.store().sync()  # the size, and the window's place and page, before this process is replaced
        restart([a for a in argv if a != "--fresh"])  # --fresh already set the history aside once
    return code


if __name__ == "__main__":
    sys.exit(main())
