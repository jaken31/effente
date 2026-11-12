"""The agent's tools as plain functions, each call recorded on the run as it happens.

The live Claude path and the stand-in both call these, so the platform sees
the same tool calls either way.
"""

from __future__ import annotations

import json
from typing import Callable

from .data import summarize_readings
from .parts import search_parts as tavily_search_parts
from .run import Run


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
        self.search = search or tavily_search_parts

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
        return self._record("search_parts", {"query": query}, output)
