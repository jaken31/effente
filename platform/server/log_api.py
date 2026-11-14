"""The part and log routes (platform/CONTRACT.md sections 8.5 and 8.6).

receiver_server.py calls each function as `fn(request, query, *ids)`: see
scan_api.py for what `request` offers. The log store is `request.server.store.partlog`.
The answer that names a part (section 8.6) needs no route here: it comes in on
the alarm answer route and reaches the log store through its `alarm_answered` hook.

The three routes of section 9 (part readings) are at the end.
"""

from log_store import DEFAULT_LOG_LIMIT, ENTRY_KINDS
import normal_spectrum  # noqa: E402  platform/partlog, put on the path by log_store
from receiver_store import MAX_HISTORY_LIMIT, BadRequest, parse_time

DEFAULT_READINGS_LIMIT = 300


def _log(request):
    return request.server.store.partlog


def _filters(query):
    """The filters of the two log reads: part, kind, since and limit, each optional."""
    kind = query.get("kind", [None])[-1]
    if kind is not None and kind not in ENTRY_KINDS:
        raise BadRequest(f"kind must be one of {', '.join(ENTRY_KINDS)}")
    since = None
    if "since" in query:
        since = parse_time(query["since"][-1])
        if since is None:
            raise BadRequest("since must be an ISO 8601 time")
    try:
        limit = int(query.get("limit", [str(DEFAULT_LOG_LIMIT)])[-1])
    except ValueError:
        raise BadRequest("limit must be a whole number")
    if limit < 0:
        raise BadRequest("limit must be 0 or more")
    return {"part": query.get("part", [None])[-1], "kind": kind, "since": since, "limit": min(limit, MAX_HISTORY_LIMIT)}


def post_part(request, query, machine_id):
    """POST /api/machines/<machine_id>/parts"""
    request.send_json(200, {"ok": True, "part": _log(request).create_part(machine_id, request.read_json())})


def get_machine_parts(request, query, machine_id):
    """GET /api/machines/<machine_id>/parts"""
    removed = query.get("removed", ["0"])[-1].lower() in ("1", "true", "yes")
    request.send_json(200, {"ok": True, "parts": _log(request).machine_part_list(machine_id, removed)})


def put_part(request, query, part_id):
    """PUT /api/parts/<part_id>"""
    request.send_json(200, {"ok": True, "part": _log(request).update_part(part_id, request.read_json())})


def post_entry(request, query, machine_id):
    """POST /api/machines/<machine_id>/log"""
    entry, part = _log(request).add_entry(machine_id, request.read_json())
    request.send_json(200, {"ok": True, "entry": entry, "part": part})


def get_machine_log(request, query, machine_id):
    """GET /api/machines/<machine_id>/log"""
    request.send_json(200, {"ok": True, "entries": _log(request).machine_entries_list(machine_id, **_filters(query))})


def get_machine_log_csv(request, query, machine_id):
    """GET /api/machines/<machine_id>/log.csv"""
    request.send_bytes(200, _log(request).machine_csv(machine_id, **_filters(query)), "text/csv; charset=utf-8")


# ---- section 9: part readings ---------------------------------------------


def put_probe(request, query, machine_id):
    """PUT /api/machines/<machine_id>/probe (section 9.1)"""
    request.send_json(200, {"ok": True, "probe": _log(request).set_probe(machine_id, request.read_json())})


def get_part_readings(request, query, part_id):
    """GET /api/parts/<part_id>/readings (section 9.3). `projection` is null until row P1 builds it."""
    since = None
    if "since" in query:
        since = parse_time(query["since"][-1])
        if since is None:
            raise BadRequest("since must be an ISO 8601 time")
    try:
        limit = int(query.get("limit", [str(DEFAULT_READINGS_LIMIT)])[-1])
    except ValueError:
        raise BadRequest("limit must be a whole number")
    if limit < 0:
        raise BadRequest("limit must be 0 or more")
    reply = _log(request).part_readings(part_id, since, min(limit, MAX_HISTORY_LIMIT))
    request.send_json(200, dict({"ok": True}, **reply, projection=None))


def get_normal_spectrum(request, query, machine_id):
    """GET /api/machines/<machine_id>/normal-spectrum (section 9.4). With `part`, only its records."""
    part_id = query.get("part", [None])[-1] or None
    store = request.server.store
    with store.lock:
        part = _log(request).parts.get(part_id) if part_id is not None else None
        if part_id is not None and (part is None or part["machine_id"] != machine_id):
            raise BadRequest(f"no such part on {machine_id}: {part_id}")
        records = list(store.by_machine.get(machine_id, ()))
        models = dict(store.models)
    reply = normal_spectrum.normal_spectrum(machine_id, records, part_id, lambda version: (models.get(version) or {}).get("spectrum_edges_hz"))
    request.send_json(200, dict({"ok": True}, **reply))
