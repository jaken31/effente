"""Ask a model server on this laptop for the review. Nothing leaves the site.

The transport is the report agent's `LocalCall` (device/agent/harness.py),
unchanged: finding the model, the request, the fall back to plain JSON when
the server cannot force a schema, and the separate reasons for no server, no
answer in time and an answer that is not JSON. Only the prompt and what the
answer is checked against differ, and `LocalCall.__call__` has both built in,
so this class asks through its `_ask` and repeats its one retry here.

UNVERIFIED: only run against the fake model server in the tests, never a real model.
"""

from __future__ import annotations

import json
from typing import Callable

from device.agent.harness import LocalCall

SYSTEM = """You review one part of a machine for a maintenance planner, from that part's \
maintenance log.

get_part_history gives you a fact sheet: the part, its state as the platform worked it \
out, the entries people wrote about it, and the alarms linked to it. Use nothing else.

Write what: what the log says is wrong with this part, or that nothing points at it. \
Write why: the facts behind your advice. Set needs_repair to yes, no or not_sure. It is \
advice for a person, who decides. List in cited_alarms and cited_entries the ids from \
the fact sheet you relied on.

An alarm points at a part only because its fault type is linked to that part. Until a \
person's finding or inspection confirms it, call it possible. Answer yes only when an \
entry says the part needs repair and no repair is newer. Answer not_sure when nobody \
has looked. Do not say how the fault came about: that is filled in separately, from \
the fault type's own text.

The review is checked in code, and one that breaks a rule is thrown away. Write a \
number only if it is on the fact sheet, copied exactly: do not round it. Name a fault \
type only if the fact sheet names it, and name no other part of the machine. Cite only \
ids that are on the fact sheet."""


class LocalReviewCall(LocalCall):
    """`LocalCall` for a part review: (fact sheet, schema, check) -> (draft, usage)."""

    def __call__(self, sheet: dict, schema: dict, check: Callable[[dict], list[str]]) -> tuple[dict, dict]:
        part = sheet["part"]
        messages = [
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": f"Review part {part['part_id']} ({part['name']}) of machine {sheet['machine_id']}.\n\n"
                f"get_part_history returned:\n{json.dumps(sheet)}\n\n"
                f"Answer with one JSON object that matches this schema:\n{json.dumps(schema)}",
            },
        ]
        body = {
            "model": self._find_model(),
            "messages": messages,
            "stream": False,
            "temperature": 0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "part_review", "strict": True, "schema": schema},
            },
        }
        usage = {"input_tokens": 0, "output_tokens": 0}
        draft, text = self._ask(body, schema, usage)
        problems = check(draft)
        if problems:
            # One more try, told what was wrong. run_review checks whatever comes back.
            messages.append({"role": "assistant", "content": text})
            messages.append(
                {
                    "role": "user",
                    "content": "That review failed these checks:\n"
                    + "\n".join(f"- {problem}" for problem in problems)
                    + "\nWrite it again with them fixed. Answer with the JSON object only.",
                }
            )
            draft, _ = self._ask(body, schema, usage)
        return draft, usage
