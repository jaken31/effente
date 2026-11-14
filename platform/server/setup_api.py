"""The setup routes (platform/CONTRACT.md sections 10.1 and 10.2). The lookup route
is a stub: it answers 400 "not built yet".

receiver_server.py calls each function as `fn(request, query, *ids)`: see
scan_api.py for what `request` offers. The setup store is `request.server.store.setup`.
"""

from receiver_store import BadRequest

NOT_BUILT = "not built yet"


def _not_built(request):
    request.discard_body()  # unread bytes would be taken as the next request
    raise BadRequest(NOT_BUILT)


def _setup(request):
    return request.server.store.setup


def put_profile(request, query, machine_id):
    """PUT /api/machines/<machine_id>/profile"""
    request.send_json(200, {"ok": True, "profile": _setup(request).put_profile(machine_id, request.read_json())})


def get_profile(request, query, machine_id):
    """GET /api/machines/<machine_id>/profile"""
    request.send_json(200, {"ok": True, "profile": _setup(request).get_profile(machine_id)})


def post_lookup(request, query):
    """POST /api/library/lookup"""
    _not_built(request)
