"""Jina Reader extractor — kept as the fallback when generic Trafilatura whiffs.

Use sparingly: anonymous Jina is the slow path. Routing should send only the
genuinely JS-rendered tail here.
"""

from __future__ import annotations

import httpx

from ..jina import JINA_BASE, load_api_key
from ..router import Route
from . import ExtractResult


async def fetch(route: Route, client: httpx.AsyncClient) -> ExtractResult:
    api_key = load_api_key()
    headers = {"Accept": "text/markdown", "User-Agent": "SymposiumBot/0.1"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        r = await client.get(f"{JINA_BASE}/{route.url}", headers=headers)
    except httpx.TimeoutException:
        return ExtractResult(None, "timeout", {"source": "jina"})
    except Exception as e:
        return ExtractResult(None, "other",
                             {"source": "jina", "error": f"{type(e).__name__}: {e}"})

    if r.status_code == 429:
        return ExtractResult(None, "rate_limited", {"source": "jina"})
    if r.status_code >= 500:
        return ExtractResult(None, "http_5xx",
                             {"source": "jina", "http_status": r.status_code})
    if r.status_code >= 400:
        return ExtractResult(None, "http_4xx",
                             {"source": "jina", "http_status": r.status_code})

    body = r.text
    if not body or len(body) < 100:
        return ExtractResult(None, "empty", {"source": "jina"})
    return ExtractResult(body, "ok",
                         {"source": "jina", "bytes": len(body.encode("utf-8"))})
