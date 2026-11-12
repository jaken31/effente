"""effente receiver: accepts device records, serves the dashboard.

Run from anywhere:  python3 platform/server/main.py
"""

import argparse
import os
import signal
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from receiver_server import Receiver, lan_address  # noqa: E402


def parse_args(argv):
    parser = argparse.ArgumentParser(description="effente receiver: log store, live stream and dashboard host.")
    parser.add_argument("--port", type=int, default=8000, help="port to listen on (default 8000)")
    parser.add_argument("--host", default="0.0.0.0", help="address to listen on (default 0.0.0.0, all interfaces)")
    parser.add_argument(
        "--data-dir", default=os.path.join(HERE, "data"), help="where records, models and agent runs are kept"
    )
    parser.add_argument(
        "--web-dir", default=os.path.join(os.path.dirname(HERE), "web"), help="dashboard files to serve at /"
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        receiver = Receiver(args.host, args.port, os.path.abspath(args.data_dir), os.path.abspath(args.web_dir))
    except OSError as e:
        print(f"error: cannot listen on {args.host}:{args.port}: {e.strerror or e}", file=sys.stderr)
        return 1

    # Ctrl-C and SIGTERM both stop the server cleanly.
    def on_sigterm(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, on_sigterm)

    shown = "localhost" if args.host in ("0.0.0.0", "", "::") else args.host
    line = f"effente receiver: open http://{shown}:{receiver.port}/"
    if args.host in ("0.0.0.0", "", "::"):
        lan = lan_address()
        if lan:
            line += f"  (devices use http://{lan}:{receiver.port})"
    print(line, flush=True)
    print(f"data in {os.path.abspath(args.data_dir)}. Ctrl-C to stop.", flush=True)
    try:
        receiver.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        receiver.stop()
    print("receiver stopped", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
