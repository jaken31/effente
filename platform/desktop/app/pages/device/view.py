"""What the Device & model page shows, worked out from the state.

No Qt here, so it can be tested without a display. Everything shown comes from
the devices, the model announcement (CONTRACT.md section 1), alarms and runs.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.data import logic
from app.pages.agents.view import clock, parse_time, run_status

NOT_REPORTED = "not reported"


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else ""


@dataclass(frozen=True)
class Level:
    text: str
    on: bool
    planned: bool


def protection(mode: str | None, model: dict | None) -> tuple[str, list[Level]]:
    """The headline and the protection level steps."""
    source = (model or {}).get("source")
    entry = logic.plain_name((model or {}).get("library_entry"))
    learning = mode == "calibrating" or not model
    if mode == "calibrating":
        head = "Watching for serious problems. Learning this machine."
    elif not model:
        head = "Watching for serious problems. No model announced yet."
    elif source == "trained":
        head = "Trained on this machine. Watching for changes from its normal."
    else:
        head = "Calibrated. Watching for changes from this machine's normal."
    calibrated = not learning and source != "trained"
    trained = not learning and source == "trained"
    levels = [
        Level("Day-0 rules only", learning, False),
        Level("Calibrated · library entry " + entry if entry else "Calibrated", calibrated, False),
        Level("Trained on this machine (optional long stay)", trained, not trained),
        Level("Model change waiting for approval (later)", False, True),
    ]
    return head, levels


def model_fields(model: dict | None, latest: dict | None) -> list[tuple[str, str]]:
    """(label, value) rows for the model panel. Fields the contract does not carry say "not reported"."""
    m = model or {}
    if not m:
        return [("Model", "no model announced yet")]
    types = [t for t in m.get("fault_types") or [] if isinstance(t, dict)]
    validated = sum(1 for t in types if t.get("validated") is True)
    feats = [f for f in m.get("features") or [] if isinstance(f, dict)]
    fv = m.get("features_version")
    return [
        ("Fit check", _text(m.get("fit_check"))),
        ("Calibrated", clock(m["calibrated_at"]) if parse_time(m.get("calibrated_at")) else NOT_REPORTED),
        ("Library entry", logic.plain_name(m.get("library_entry")) or NOT_REPORTED),
        ("Source", m.get("source") or NOT_REPORTED),
        ("Model version", m.get("model_version") or NOT_REPORTED),
        ("Features", f"{len(feats)} readings" + (f" · {fv}" if fv else "")),
        ("Alarm line", alarm_line_text(latest, m)),
        ("Fault types", f"{len(types)} · {validated} validated"),
        ("Calibration", _text(m.get("calibration"))),
    ]


def alarm_line_text(latest: dict | None, model: dict | None) -> str:
    """The line the newest record was judged at, else the model's sensitivity."""
    s = (latest or {}).get("sensitivity")
    line = (latest or {}).get("alarm_line")
    if not _num(s):
        s = (model or {}).get("sensitivity")
        line = None
    if not _num(s):
        return "sensitivity not reported"
    if not _num(line):
        line = logic.alarm_line(s)
    return f"score {line:.2f} · sensitivity {s:.2f} ({logic.sensitivity_name(s)})"


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _text(v) -> str:
    return str(v) if isinstance(v, str) and v else NOT_REPORTED


@dataclass(frozen=True)
class Event:
    at: str  # ISO time, for sorting
    kind: str
    detail: str

    @property
    def time(self) -> str:
        return clock(self.at)


def events(alarms: list[dict], runs: list[dict], extra: list[Event] = ()) -> list[Event]:
    """Alarms, answers and agent runs from the record, plus events the page saw live, newest first."""
    out = list(extra)
    for a in alarms or []:
        if not isinstance(a, dict) or not a.get("start"):
            continue
        name = logic.display_name(a.get("diagnosis"))
        out.append(Event(a["start"], "alarm", name))
        if a.get("open") is False and a.get("end"):
            periods = a.get("periods")
            out.append(Event(a["end"], "back to normal", f"after {periods} period{'' if periods == 1 else 's'}" if _num(periods) else name))
        for ans in a.get("answers") or ([a["answer"]] if a.get("answer") else []):
            if isinstance(ans, dict) and ans.get("at"):
                out.append(Event(ans["at"], "answer", f"{logic.verdict_text(ans.get('verdict'))} · {name} at {clock(a['start'])}"))
    for r in runs or []:
        if not isinstance(r, dict) or not r.get("at"):
            continue
        st = run_status(r)
        if st == "report ready":
            pri = (r.get("report") or {}).get("priority")
            detail = f"report ready · priority {pri}" if pri else st
        elif st == "no report":
            detail = f"no report · {r['reason']}" if r.get("reason") else st
        else:
            detail = st
        out.append(Event(r["at"], "agent", detail))
    out.sort(key=lambda e: parse_time(e.at).timestamp() if parse_time(e.at) else 0, reverse=True)
    return out


def mode_event(at: str, before: str | None, after: str | None) -> Event:
    return Event(at, "mode", f"{logic.mode_name(before) or '–'} → {logic.mode_name(after) or '–'}")


def model_event(at: str, model: dict) -> Event:
    return Event(at, "model", f"{model.get('model_version', '')} announced · {model.get('source') or 'source not reported'}")


@dataclass(frozen=True)
class DeviceRow:
    device_id: str
    status: str
    connected: bool
    mode: str
    last_seen: str
    machine: str
    model_version: str


def device_rows(devices: dict | list | None) -> list[DeviceRow]:
    items = devices.items() if isinstance(devices, dict) else ((d.get("device_id"), d) for d in devices or [])
    rows = []
    for did, d in items:
        if not did or not isinstance(d, dict):
            continue
        seen = parse_time(d.get("last_seen"))
        connected = d.get("connected") is True
        status = "connected" if connected else ("quiet" if seen else "never seen")
        rows.append(DeviceRow(
            device_id=str(did),
            status=status,
            connected=connected,
            mode=(d.get("mode") or "–") if seen or connected else "–",
            last_seen=clock(d.get("last_seen")) if seen else "–",
            machine=d.get("machine_id") or "–",
            model_version=d.get("model_version") or "–",
        ))
    return sorted(rows, key=lambda r: r.device_id)


# ---- a board picked by address ------------------------------------------------

CONNECT, DISCONNECT = "Connect", "Disconnect"
ADDRESS_HINT = "the board's address on the network, for example 192.168.43.20"
# What device/app/README.md and platform/README.md say about reading the board's own port.
LINK_WHY = ("The board and this laptop must be on the same network, and it must let them reach each other: "
            "a phone hotspot does, university WiFi usually does not. Read this way, the board sends no "
            "fault type sentences and no model file.")
TWICE = " A board is also read over USB. If it is the same board, every message arrives twice: disconnect here."


def link_status(link: dict | None) -> str:
    """One sentence on the board read by address. Says only what the link reported."""
    link = link or {}
    url, usb = link.get("url") or "", link.get("usb") or []
    if not url:
        boards = f"{len(usb)} board{'' if len(usb) == 1 else 's'}"
        return f"No board connected by address. Reading {boards} over USB." if usb else "No board connected by address."
    reachable = link.get("reachable")
    if reachable is True:
        text = f"Reading the board at {url}."
    elif reachable is False:
        text = f"No answer from {url}. Still trying: check the board is on and on this network."
    else:
        text = f"Trying {url}."
    return text + (TWICE if usb else "")


# ---- boards found without typing an address -------------------------------------

FIND = "Find boards"
FIND_TITLE = "Boards this laptop can reach"
SEARCH_AGAIN = "Search again"
SEARCHING = "Searching: a board on USB, then the addresses near this laptop."
FIND_WHY = ("Tried: the WiFi address of a board plugged in by USB, and the addresses near this laptop's own and "
            "near the one connected to last. On a large network (a university's) a board elsewhere is not found "
            "this way: plug it in by USB, or type its address.")
NOT_FROM_HERE = "does not answer from this laptop's network"
NO_ANSWER = "no answer"  # in the table, where there is little room


def board_rows(found: dict | None) -> list[list[str]]:
    """Device, machine, mode, address, found by: one row a board, boards that answer first.
    The first three are the board's own words, a dash when it did not answer."""
    rows = []
    for b in sorted((found or {}).get("boards") or [], key=lambda b: (not b.get("answers"), b.get("address") or "")):
        how = "USB" if b.get("how") == "usb" else "network search"
        if not b.get("answers"):
            how += " · " + NO_ANSWER
        rows.append([b.get("device_id") or "–", b.get("machine_id") or "–", logic.mode_name(b.get("mode")) or "–",
                     b.get("address") or "", how])
    return rows


def board_addresses(found: dict | None) -> list[tuple[str, bool]]:
    """(address, answers) for each row of board_rows, in its order."""
    boards = sorted((found or {}).get("boards") or [], key=lambda b: (not b.get("answers"), b.get("address") or ""))
    return [(b.get("address") or "", bool(b.get("answers"))) for b in boards]


def find_status(found: dict | None) -> str:
    """One sentence on the search. Counts only what it tried and what answered."""
    found = found or {}
    boards = found.get("boards") or []
    n = sum(1 for b in boards if b.get("answers"))
    count = f"{n} board{'' if n == 1 else 's'}"
    if found.get("searching"):
        return SEARCHING + (f" {count} so far." if n else "")
    tried = found.get("tried")
    if tried is None:
        return "Not searched yet." if not boards else f"Search stopped. {count} found."
    if tried == 0:
        near = "This laptop has no ordinary local address on its network, so there were no nearby addresses to try."
    else:
        near = f"Tried {tried} nearby address{'' if tried == 1 else 'es'}."
    silent = len(boards) - n
    usb = (f" {silent} board{'' if silent == 1 else 's'} on USB {NOT_FROM_HERE}: "
           "it is on another network than this laptop.") if silent else ""
    return (f"Found {count}. " if n else "No board answered. ") + near + usb
