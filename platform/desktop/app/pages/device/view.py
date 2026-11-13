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
    entry = (model or {}).get("library_entry") or ""
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
        ("Library entry", m.get("library_entry") or NOT_REPORTED),
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
