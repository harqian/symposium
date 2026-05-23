"""Per-domain extractors. Each module exposes:

    async def fetch(route: Route, client: httpx.AsyncClient) -> ExtractResult

where ExtractResult holds (text: str | None, status: str, extra: dict).
status is one of: ok, empty, http_4xx, http_5xx, timeout, not_supported, other.
"""

from dataclasses import dataclass, field


@dataclass
class ExtractResult:
    text: str | None
    status: str
    extra: dict = field(default_factory=dict)
