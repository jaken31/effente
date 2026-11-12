"""Parts and manual lookup through Tavily's search API.

UNVERIFIED: the request and response shapes below are written from what we
know of Tavily's public HTTP API (POST https://api.tavily.com/search, key as a
Bearer token, JSON body with `query` and `max_results`, reply with a `results`
list of `title`, `url`, `content`). This code has never been run against the
live service. If the live call fails, the agent still works: it is told the
lookup is unavailable and leaves part_reference empty.
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Callable

TAVILY_URL = "https://api.tavily.com/search"
TIMEOUT_SECONDS = 5.0
MAX_RESULTS = 3
SNIPPET_CHARS = 300

# (url, body bytes, headers, timeout) -> response body bytes. Raises on any failure.
Fetch = Callable[[str, bytes, dict, float], bytes]


def _urllib_fetch(url: str, body: bytes, headers: dict, timeout: float) -> bytes:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def unavailable(why: str) -> str:
    return f"Parts lookup is unavailable ({why}). Leave part_reference empty."


def search_parts(query: str, fetch: Fetch | None = None) -> str:
    """Return a few short search results as JSON text, or a sentence saying why not.

    Never raises: any failure becomes the "unavailable" sentence.
    """
    key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not key:
        return unavailable("no TAVILY_API_KEY set")
    body = json.dumps(
        {"query": query, "max_results": MAX_RESULTS, "search_depth": "basic"}
    ).encode("utf-8")
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
    try:
        raw = (fetch or _urllib_fetch)(TAVILY_URL, body, headers, TIMEOUT_SECONDS)
        results = json.loads(raw).get("results", [])
        trimmed = [
            {
                "title": str(item.get("title", ""))[:200],
                "url": str(item.get("url", "")),
                "snippet": str(item.get("content", ""))[:SNIPPET_CHARS],
            }
            for item in results[:MAX_RESULTS]
            if isinstance(item, dict)
        ]
    except Exception as error:  # network, timeout, HTTP error, bad JSON, odd shape
        return unavailable(f"lookup failed: {type(error).__name__}")
    if not trimmed:
        return "Parts lookup found nothing for that query. Leave part_reference empty."
    return json.dumps(trimmed)
