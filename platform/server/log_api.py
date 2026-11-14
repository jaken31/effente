"""The part and log routes (platform/CONTRACT.md sections 8.5 and 8.6). Stubs:
every route answers 400 "not built yet".

receiver_server.py calls each function as `fn(request, query, *ids)`: see
scan_api.py for what `request` offers. The log store is `request.server.store.partlog`.
"""

from receiver_store import BadRequest

NOT_BUILT = "not built yet"


def _not_built(request):
    request.discard_body()  # unread bytes would be taken as the next request
    raise BadRequest(NOT_BUILT)


def post_part(request, query, machine_id):
    """POST /api/machines/<machine_id>/parts"""
    _not_built(request)


def get_machine_parts(request, query, machine_id):
    """GET /api/machines/<machine_id>/parts"""
    _not_built(request)


def put_part(request, query, part_id):
    """PUT /api/parts/<part_id>"""
    _not_built(request)


def post_entry(request, query, machine_id):
    """POST /api/machines/<machine_id>/log"""
    _not_built(request)


def get_machine_log(request, query, machine_id):
    """GET /api/machines/<machine_id>/log"""
    _not_built(request)


def get_machine_log_csv(request, query, machine_id):
    """GET /api/machines/<machine_id>/log.csv"""
    _not_built(request)
