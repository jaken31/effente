"""The setup routes (platform/CONTRACT.md sections 10.1 and 10.2). Stubs: every
route answers 400 "not built yet".

receiver_server.py calls each function as `fn(request, query, *ids)`: see
scan_api.py for what `request` offers. The setup store is `request.server.store.setup`.
"""

from receiver_store import BadRequest

NOT_BUILT = "not built yet"


def _not_built(request):
    request.discard_body()  # unread bytes would be taken as the next request
    raise BadRequest(NOT_BUILT)


def put_profile(request, query, machine_id):
    """PUT /api/machines/<machine_id>/profile"""
    _not_built(request)


def get_profile(request, query, machine_id):
    """GET /api/machines/<machine_id>/profile"""
    _not_built(request)


def post_lookup(request, query):
    """POST /api/library/lookup"""
    _not_built(request)
