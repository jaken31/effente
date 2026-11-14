"""Ask a model server on this laptop to fill the profile. Nothing leaves the site.

The transport is the report agent's `LocalCall` (device/agent/harness.py),
unchanged: finding the model, the request, the fall back to plain JSON when the
server cannot force a schema, and the reasons for no server, no answer in time
and an answer that is not JSON. Its `__call__` is tied to the report, so this
class asks through its `_ask` and repeats its one retry here, as the part
review does (platform/partlog/review/local.py).

The model is given the answers, the five fields with their value lists, and
the rules. Not the library, the lookup table or any manifest.
"""

from __future__ import annotations

import json
from typing import Callable

from device.agent.harness import LocalCall

from setup import lookup

# `_ask` checks only that the answer is a JSON object against this. Everything
# else is check_draft's, so a wrong shape is a failed check and gets the retry.
ANY_OBJECT = {"properties": {}}

SYSTEM = f"""You fill in a machine profile from a person's answers to a few questions \
about one machine they want to watch for faults.

The profile has five fields. Set each to one of its values, or to null:
- machine_type: {", ".join(lookup.VALUES["machine_type"])}
- drive: {", ".join(lookup.VALUES["drive"])}
- speed_rpm: a number, or [low, high] for a range, in revolutions per minute
- mounting: {", ".join(lookup.VALUES["mounting"])}
- sensor_location: {", ".join(lookup.VALUES["sensor_location"])}

Rules:
- Use only what the person said. An answer of null means they said "I don't know".
- When the person did not say, or said they do not know, or you are unsure, set null. \
An empty field is fine. A wrong one is not. An answer that fits two values is unsure: null.
- Never guess a number and never work one out. Set speed_rpm only to a number the \
person wrote in rpm, copied exactly. A speed in Hz stays null.
- Write explanation for the person, in plain words: what you took from their answers, \
and which fields you left empty and why. Write a number only if the person wrote it. \
Do not repeat the lists of allowed values.
- Say nothing about which model, library entry or starting point to use. That is \
picked afterwards, in code, from the profile.

Your answer is checked in code, and one that breaks a rule is thrown away."""


class LocalSetupCall(LocalCall):
    """`LocalCall` for a setup run: (answers, schema, check) -> (draft, usage)."""

    def __call__(self, answers: list[dict], schema: dict, check: Callable[[dict], list[str]]) -> tuple[dict, dict]:
        shown = [{"question": a["question"], "answer": a["answer"]} for a in answers]
        messages = [
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": f"get_answers returned:\n{json.dumps(shown)}\n\n"
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
                "json_schema": {"name": "machine_profile", "strict": True, "schema": schema},
            },
        }
        usage = {"input_tokens": 0, "output_tokens": 0}
        draft, text = self._ask(body, ANY_OBJECT, usage)
        problems = check(draft)
        if problems:
            # One more try, told what was wrong. run_setup checks whatever comes back.
            messages.append({"role": "assistant", "content": text})
            messages.append(
                {
                    "role": "user",
                    "content": "That answer failed these checks:\n"
                    + "\n".join(f"- {problem}" for problem in problems)
                    + "\nWrite it again with them fixed. Answer with the JSON object only.",
                }
            )
            draft, _ = self._ask(body, ANY_OBJECT, usage)
        return draft, usage
