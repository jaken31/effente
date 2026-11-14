"""Start the whole laptop platform with one command: receiver, then the simulated device.

    python3 platform/run.py            # receiver + simulated device, keys 1-6 switch case
    python3 platform/run.py --no-sim   # receiver only, for a real device
    python3 platform/run.py --device http://10.110.213.214:8765
                                       # a real device, read from its own port (platform/link)

The simulated device takes over this terminal for its keys. The receiver's output
goes to a log file in the data folder. Quitting the simulator (q) stops both.
"""

from __future__ import annotations

import argparse
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "server" / "data"


def wait_until_up(url: str, server: subprocess.Popen, seconds: float = 10.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if server.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(f"{url}/api/state", timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-sim", action="store_true", help="receiver only, for a real device")
    parser.add_argument("--device", metavar="URL",
                        help="read a real device from its own port, for example http://10.110.213.214:8765")
    parser.add_argument("--fresh", action="store_true", help="delete stored history first")
    parser.add_argument("--case", default="normal", help="case the simulator starts in (see platform/sim/README.md)")
    args = parser.parse_args()

    # Without this, a kill or a closed terminal would skip the cleanup below
    # and leave the receiver running on the port.
    for name in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), lambda *_: sys.exit(0))

    if args.fresh and DATA.exists():
        shutil.rmtree(DATA)
    DATA.mkdir(parents=True, exist_ok=True)
    url = f"http://localhost:{args.port}"
    server_log = DATA / "receiver.log"

    with open(server_log, "w", encoding="utf-8") as log:
        server = subprocess.Popen(
            [sys.executable, str(HERE / "server" / "main.py"), "--port", str(args.port),
             "--data-dir", str(DATA)],
            stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            if not wait_until_up(url, server):
                print(f"The receiver did not start. See {server_log}", file=sys.stderr)
                print(server_log.read_text(encoding="utf-8")[-2000:], file=sys.stderr)
                return 1
            print(f"Dashboard: {url}")
            print("Report:    python3 platform/report.py   (in a second terminal)")
            if args.device:
                sys.path.insert(0, str(HERE / "link"))
                import device_link

                device_link.DeviceLink(args.device, url).start()
                print(f"Reading the device at {args.device}. Ctrl-C to stop.")
                server.wait()
                return 0
            if args.no_sim:
                # The receiver's first line gives the address a device should use.
                print(server_log.read_text(encoding="utf-8").splitlines()[0])
                print("Receiver only. Waiting for a real device. Ctrl-C to stop.")
                server.wait()
                return 0
            print("Simulated device: keys are listed below; q quits both.\n")
            return subprocess.call(
                [sys.executable, str(HERE / "sim" / "main.py"), "--url", url,
                 "--case", args.case,
                 "--log", str(DATA / "device.jsonl"),
                 "--model-file", str(DATA / "model.json")],
            )
        except KeyboardInterrupt:
            return 0
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()


if __name__ == "__main__":
    sys.exit(main())
