"""arxiv extractor: try HTML mirror first (no rate limit), fall back to PDF via PyMuPDF."""

from __future__ import annotations

import asyncio
import io

import httpx
import pymupdf
import trafilatura

from ..router import Route
from . import ExtractResult
from .generic import USER_AGENT

# arxiv ToU: 1 req per 3 sec on the /pdf endpoint. shared lock per process.
_PDF_RATE_LOCK = asyncio.Lock()
_PDF_LAST_FETCH = 0.0
_PDF_MIN_INTERVAL = 3.0  # seconds


async def _pdf_rate_gate() -> None:
    global _PDF_LAST_FETCH
    async with _PDF_RATE_LOCK:
        loop = asyncio.get_event_loop()
        now = loop.time()
        wait = _PDF_MIN_INTERVAL - (now - _PDF_LAST_FETCH)
        if wait > 0:
            await asyncio.sleep(wait)
        _PDF_LAST_FETCH = loop.time()


def _pdf_to_text(blob: bytes) -> str:
    """Extract plain text from a PDF blob via PyMuPDF."""
    doc = pymupdf.open(stream=io.BytesIO(blob), filetype="pdf")
    try:
        parts = [page.get_text("text") for page in doc]
    finally:
        doc.close()
    return "\n\n".join(p.strip() for p in parts if p.strip())


async def fetch(route: Route, client: httpx.AsyncClient) -> ExtractResult:
    headers = {"User-Agent": USER_AGENT}
    aid = route.arxiv_id

    # 1) try the HTML mirror (no rate limit per arxiv ToU)
    try:
        r = await client.get(route.url, headers=headers, follow_redirects=True)
    except httpx.TimeoutException:
        r = None
    except Exception as e:
        return ExtractResult(None, "other", {"error": f"{type(e).__name__}: {e}"})

    if r is not None and r.status_code == 200 and r.text:
        text = trafilatura.extract(
            r.text, output_format="markdown", url=route.url,
            include_tables=True, deduplicate=True,
        )
        if text and len(text) >= 500:
            return ExtractResult(
                text, "ok",
                {"source": "arxiv_html", "bytes": len(text.encode("utf-8"))},
            )

    # 2) fall back to PDF (rate-limited)
    if not aid:
        return ExtractResult(None, "empty", {"reason": "no_arxiv_id"})
    pdf_url = f"https://arxiv.org/pdf/{aid}"
    await _pdf_rate_gate()
    try:
        r = await client.get(pdf_url, headers=headers, follow_redirects=True)
    except httpx.TimeoutException:
        return ExtractResult(None, "timeout", {"source": "arxiv_pdf"})
    except Exception as e:
        return ExtractResult(None, "other",
                             {"source": "arxiv_pdf", "error": f"{type(e).__name__}: {e}"})

    if r.status_code != 200 or not r.content:
        bucket = "http_5xx" if r.status_code >= 500 else "http_4xx"
        return ExtractResult(None, bucket,
                             {"source": "arxiv_pdf", "http_status": r.status_code})
    try:
        text = await asyncio.to_thread(_pdf_to_text, r.content)
    except Exception as e:
        return ExtractResult(None, "other",
                             {"source": "arxiv_pdf",
                              "error": f"pdf_parse: {type(e).__name__}: {e}"})
    if not text or len(text) < 200:
        return ExtractResult(None, "empty", {"source": "arxiv_pdf"})
    return ExtractResult(
        text, "ok",
        {"source": "arxiv_pdf", "bytes": len(text.encode("utf-8"))},
    )
