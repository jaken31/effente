"""The setup routes (platform/CONTRACT.md sections 10.1 and 10.2).

When no library entry can be picked (not even the general one's manifest can be
read), a profile is not stored and the lookup answers HTTP 500 with the reason.

receiver_server.py calls each function as `fn(request, query, *ids)`: see
scan_api.py for what `request` offers. The setup store is `request.server.store.setup`.
"""

from setup_store import lookup


def _setup(request):
    return request.server.store.setup


def _unavailable(request, error):
    request.send_json(500, {"ok": False, "error": str(error)})


def put_profile(request, query, machine_id):
    """PUT /api/machines/<machine_id>/profile"""
    try:
        profile = _setup(request).put_profile(machine_id, request.read_json())
    except lookup.LookupUnavailable as error:
        return _unavailable(request, error)
    request.send_json(200, {"ok": True, "profile": profile})


def get_profile(request, query, machine_id):
    """GET /api/machines/<machine_id>/profile"""
    request.send_json(200, {"ok": True, "profile": _setup(request).get_profile(machine_id)})


def post_lookup(request, query):
    """POST /api/library/lookup"""
    try:
        choice = _setup(request).lookup(request.read_json())
    except lookup.LookupUnavailable as error:
        return _unavailable(request, error)
    request.send_json(200, {"ok": True, "choice": choice})
