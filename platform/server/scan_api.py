"""The scan routes (platform/CONTRACT.md sections 8.1, 8.3 and 8.4).

receiver_server.py calls each function as `fn(request, query, *ids)`:

- `request` is the receiver's Handler. Read with `request.read_json()` or
  `request.read_body_to_file(path, max_bytes)`. Answer with
  `request.send_json(status, obj)`, `request.send_bytes(status, body, content_type)`
  or `request.send_file(path, content_type)`. Raise BadRequest for a 400.
- `query` is the parsed query string: name -> list of values.
- `ids` are the path's variable parts, in order.
- The scan store is `request.server.store.scans`.
"""

import functools

from receiver_store import BadRequest
from scan_store import ScanError

DROP_CHUNK_BYTES = 64 * 1024
CONTENT_TYPES = {"usdz": "model/vnd.usdz+zip", "glb": "model/gltf-binary"}


def _scans(request):
    return request.server.store.scans


def _route(function):
    """The scan store says what was wrong with a ScanError: that sentence is the 400's error."""

    @functools.wraps(function)
    def route(request, query, *ids):
        try:
            return function(request, query, *ids)
        except ScanError as e:
            raise BadRequest(str(e)) from None

    return route


class _Body:
    """The upload's body, read a piece at a time, and a count of what is still to come."""

    def __init__(self, request):
        self.rfile = request.rfile
        self.length = None  # what the scan store checks: None when there is no usable Content-Length
        if "chunked" not in request.headers.get("Transfer-Encoding", "").lower():
            self.length = request.headers.get("Content-Length")
        try:
            self.left = max(int(self.length), 0)
        except (TypeError, ValueError):
            self.left = None  # unknown: it can be neither read nor dropped

    def read(self, n):
        chunk = self.rfile.read(n)
        self.left -= len(chunk)
        return chunk

    def drop(self, max_bytes):
        """Read and throw away what was not read, a piece at a time. True if the
        connection is now clean: nothing of this body is left on it."""
        if self.left is None or self.left > max_bytes:
            return False
        while self.left:
            if not self.read(min(self.left, DROP_CHUNK_BYTES)):
                return False  # the phone went away
        return True


@_route
def get_hello(request, query):
    """GET /api/hello"""
    request.send_json(200, _scans(request).hello())


@_route
def post_scan(request, query, machine_id):
    """POST /api/machines/<machine_id>/scans, the body is the file's raw bytes.

    The scan store writes the body to disk as it arrives. When it refuses the
    upload, or the client_id is a repeat, it has not read the body (or not all
    of it). A phone that is still sending would then lose the reply, and the
    unread bytes would be taken as the next request. So what is left is read
    and dropped, and the connection is closed when that cannot be done: a body
    over the limit, no Content-Length, or an upload that was cut off."""
    scans = _scans(request)
    body = _Body(request)
    request.close_connection = True  # until nothing of the body is left on the connection
    try:
        scan, duplicate = scans.receive(machine_id, {name: values[-1] for name, values in query.items()},
                                        body.length, body.read)
    except ScanError:
        request.close_connection = not body.drop(scans.max_bytes)
        raise
    request.close_connection = not body.drop(scans.max_bytes)
    request.send_json(200, {"ok": True, "duplicate": duplicate, "scan": scan})


@_route
def get_machine_scans(request, query, machine_id):
    """GET /api/machines/<machine_id>/scans"""
    limit = query.get("limit", [None])[-1]
    request.send_json(200, {"ok": True, "scans": _scans(request).scans(machine_id, limit)})


@_route
def get_scan(request, query, scan_id):
    """GET /api/scans/<scan_id>"""
    request.send_json(200, {"ok": True, "scan": _scans(request).scan(scan_id)})


@_route
def get_scan_model(request, query, scan_id):
    """GET /api/scans/<scan_id>/model.glb"""
    request.send_file(_scans(request).model_path(scan_id), CONTENT_TYPES["glb"])


@_route
def get_scan_file(request, query, scan_id):
    """GET /api/scans/<scan_id>/file"""
    scans = _scans(request)
    request.send_file(scans.file_path(scan_id), CONTENT_TYPES[scans.scan(scan_id)["format"]])
