"""Generic httpx + Trafilatura extractor for arbitrary HTML pages."""

from __future__ import annotations

import httpx
import trafilatura

from ..router import Route
from . import ExtractResult

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0 Safari/537.36 SymposiumBot/0.1"
)

MIN_OK_CHARS = 200


def _extract(html: str, url: str) -> str | None:
    """Trafilatura → markdown text. Returns None if extraction returns empty."""
    text = trafilatura.extract(
        html,
        output_format="markdown",
        url=url,
        favor_recall=False,
        include_comments=False,
        include_tables=True,
        deduplicate=True,
    )
    return text or None


async def fetch(route: Route, client: httpx.AsyncClient) -> ExtractResult:
    try:
        r = await client.get(route.url, headers={"User-Agent": USER_AGENT},
                             follow_redirects=True)
    except httpx.TimeoutException:
        return ExtractResult(None, "timeout")
    except Exception as e:
        return ExtractResult(None, "other", {"error": f"{type(e).__name__}: {e}"})

    if r.status_code >= 500:
        return ExtractResult(None, "http_5xx", {"http_status": r.status_code})
    if r.status_code >= 400:
        return ExtractResult(None, "http_4xx", {"http_status": r.status_code})

    content_type = r.headers.get("content-type", "").lower()
    if "application/pdf" in content_type:
        # generic doesn't handle PDFs — caller should fall back
        return ExtractResult(None, "not_supported", {"reason": "pdf"})

    body = r.text
    if not body:
        return ExtractResult(None, "empty")

    text = _extract(body, route.url)
    if not text or len(text) < MIN_OK_CHARS:
        return ExtractResult(None, "empty", {"raw_html_bytes": len(body)})
    return ExtractResult(text, "ok", {"bytes": len(text.encode("utf-8"))})
