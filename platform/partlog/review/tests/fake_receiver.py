"""A tiny standard library HTTP server that stands in for the receiver's part and log routes.

It serves one machine's data in the shapes of platform/CONTRACT.md sections
5, 6, 8.5 and 8.6, keeps every run PUT to it and every entry POSTed to it.
"""

from __future__ import annotations

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeReceiver:
    def __init__(self, data: dict, log_built: bool = True):
        self.data = data
        self.log_built = log_built  # False: the parts and log routes answer 400 "not built yet"
        self.runs: list[dict] = []  # every PUT body, in order
        self.posted: list[dict] = []  # every entry POSTed
        self.gets: list[str] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def reply(self, body, status=200):
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def body(self):
                return json.loads(self.rfile.read(int(self.headers["Content-Length"])))

            def do_GET(self):
                fake.gets.append(self.path)
                url = urllib.parse.urlsplit(self.path)
                query = urllib.parse.parse_qs(url.query)
                machine = f"/api/machines/{fake.data['machine_id']}"
                if url.path in (f"{machine}/parts", f"{machine}/log") and not fake.log_built:
                    self.reply({"ok": False, "error": "not built yet"}, 400)
                elif url.path == f"{machine}/parts":
                    self.reply({"ok": True, "parts": fake.data["parts"]})
                elif url.path == f"{machine}/log":
                    part = query.get("part", [None])[0]
                    entries = [e for e in fake.data["entries"] + fake.posted if part in (None, e["part_id"])]
                    self.reply({"ok": True, "entries": sorted(entries, key=lambda e: e["at"], reverse=True)})
                elif url.path == f"{machine}/alarms":
                    self.reply({"ok": True, "alarms": fake.data["alarms"]})
                elif url.path == f"{machine}/agent-runs":
                    latest = {run["run_id"]: run for run in fake.runs}
                    self.reply({"ok": True, "runs": sorted(latest.values(), key=lambda r: r["at"], reverse=True)})
                elif url.path == "/api/state":
                    self.reply({"ok": True, "models": {"v1": {
                        "machine_id": fake.data["machine_id"], "model_version": "v1",
                        "fault_types": fake.data["fault_types"]}}})
                else:
                    self.reply({"ok": False, "error": "no such route"}, 400)

            def do_PUT(self):
                fake.runs.append(self.body())
                self.reply({"ok": True})

            def do_POST(self):
                body = self.body()
                if not fake.log_built:
                    self.reply({"ok": False, "error": "not built yet"}, 400)
                    return
                entry = {
                    "entry_id": f"entry-9{len(fake.posted):05d}", "machine_id": fake.data["machine_id"],
                    "received_at": body.get("at"), "needs_repair": None, "alarm_id": None, "diagnosis": None,
                    "source": "agent", **body,
                }
                fake.posted.append(entry)
                self.reply({"ok": True, "entry": entry, "part": None})

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)

    def __enter__(self) -> "FakeReceiver":
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
