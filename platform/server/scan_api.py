"""The scan routes (platform/CONTRACT.md sections 8.1, 8.3 and 8.4). Stubs:
every route answers 400 "not built yet".

receiver_server.py calls each function as `fn(request, query, *ids)`:

- `request` is the receiver's Handler. Read with `request.read_json()` or
  `request.read_body_to_file(path, max_bytes)`. Answer with
  `request.send_json(status, obj)`, `request.send_bytes(status, body, content_type)`
  or `request.send_file(path, content_type)`. Raise BadRequest for a 400.
- `query` is the parsed query string: name -> list of values.
- `ids` are the path's variable parts, in order.
- The scan store is `request.server.store.scans`.
"""

from receiver_store import BadRequest

NOT_BUILT = "not built yet"


def _not_built(request):
    request.discard_body()  # unread bytes would be taken as the next request
    raise BadRequest(NOT_BUILT)


def get_hello(request, query):
    """GET /api/hello"""
    _not_built(request)


def post_scan(request, query, machine_id):
    """POST /api/machines/<machine_id>/scans, the body is the file's raw bytes"""
    _not_built(request)


def get_machine_scans(request, query, machine_id):
    """GET /api/machines/<machine_id>/scans"""
    _not_built(request)


def get_scan(request, query, scan_id):
    """GET /api/scans/<scan_id>"""
    _not_built(request)


def get_scan_model(request, query, scan_id):
    """GET /api/scans/<scan_id>/model.glb"""
    _not_built(request)


def get_scan_file(request, query, scan_id):
    """GET /api/scans/<scan_id>/file"""
    _not_built(request)
