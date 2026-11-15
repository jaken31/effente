"""The agent's tools as plain functions, each call recorded on the run as it happens.

The local model path and the stand-in both call these, so the platform sees
the same tool calls either way.
"""

from __future__ import annotations

import json
from typing import Callable

from .data import summarize_readings
from .run import Run

SEARCH_DISABLED = "unavailable: disabled at this site"


def search_disabled(query: str) -> str:
    """The parts lookup: there is none. Nothing may leave the site, so no outside search exists in
    this code. A test may pass its own `search` to Tools."""
    return SEARCH_DISABLED


class Tools:
    def __init__(
        self,
        records: list[dict],
        features: dict[str, str],
        run: Run | None = None,
        search: Callable[[str], str] | None = None,
    ):
        self.records = records
        self.features = features
        self.run = run
        self.search = search or search_disabled
        # What parts lookups found in this run: check_report lets part_reference hold only text from here.
        self.parts_found: list[str] = []

    def _record(self, name: str, tool_input: dict, output: str) -> str:
        if self.run is not None:
            self.run.record_tool_call(name, tool_input, output)
        return output

    def get_readings(self, since: str = "") -> str:
        chosen = [r for r in self.records if not since or r.get("at", "") >= since]
        return self._record("get_readings", {"since": since}, json.dumps(summarize_readings(chosen)))

    def get_feature_table(self) -> str:
        return self._record("get_feature_table", {}, json.dumps(self.features))

    def search_parts(self, query: str) -> str:
        try:
            output = self.search(query)
        except Exception as error:  # an injected search must not break the loop either
            output = f"Parts lookup is unavailable ({type(error).__name__}). Leave part_reference empty."
        try:
            if isinstance(json.loads(output), dict):  # results; anything else is a sentence saying there are none
                self.parts_found.append(output)
        except ValueError:
            pass
        return self._record("search_parts", {"query": query}, output)
