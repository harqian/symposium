"""Jina Reader client. Fetches any URL and returns extracted markdown.

API: https://r.jina.ai/{URL}
Free tier: ~100 RPM / 2 concurrent. Paid (Bearer token): ~500 RPM / 50 concurrent.

Secret cache convention (per repo AGENTS.md / feedback memory):
    op read "op://Personal/jina/api_key" > /tmp/.symposium_jina_key
"""

from __future__ import annotations

from pathlib import Path

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

JINA_BASE = "https://r.jina.ai"
KEY_PATH = Path("/tmp/.symposium_jina_key")


def load_api_key() -> str | None:
    """Read cached Jina key. None means anonymous (slower rate limits)."""
    if KEY_PATH.exists():
        key = KEY_PATH.read_text().strip()
        return key or None
    return None


class JinaReader:
    """Sync client for one-shot fetches. Use JinaAsyncReader (Phase 3) for bulk."""

    def __init__(self, api_key: str | None = None, timeout: float = 60.0):
        headers = {"Accept": "text/markdown"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self.client = httpx.Client(headers=headers, timeout=timeout)

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "JinaReader":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=2, max=30),
        retry=retry_if_exception_type(
            (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)
        ),
        reraise=True,
    )
    def fetch(self, url: str) -> tuple[int, str]:
        """Return (status_code, markdown_or_error_body)."""
        r = self.client.get(f"{JINA_BASE}/{url}")
        return r.status_code, r.text
