"""Shared bits for the signal page tests."""

from __future__ import annotations

import copy

from app.theme import apply_theme, load_fonts
from fakes import FakeState, sample_records

_themed = False


def themed(qapp) -> None:
    """Fonts and stylesheet as the app sets them, once per process."""
    global _themed
    if not _themed:
        load_fonts()
        apply_theme(qapp)
        _themed = True


def state() -> FakeState:
    return FakeState(with_sim=True)


def next_record(st: FakeState, **changes) -> dict:
    """A copy of the newest record, one second and one seq later, with changes."""
    rec = copy.deepcopy(st.latest())
    rec["seq"] = rec.get("seq", 0) + 1
    rec.update(changes)
    return rec


def heat_records():
    return sample_records(heat=True)
