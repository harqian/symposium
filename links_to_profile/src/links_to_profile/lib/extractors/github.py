"""GitHub extractor: fetch a repo's README or a specific blob via raw.githubusercontent.com."""

from __future__ import annotations

import os
from pathlib import Path

import httpx

from ..router import Route
from . import ExtractResult

# optional GitHub token — bumps unauthenticated 60/h to 5000/h
_GH_TOKEN_PATHS = [
    Path("/tmp/.symposium_github_token"),
]


def _load_token() -> str | None:
    for p in _GH_TOKEN_PATHS:
        if p.exists():
            t = p.read_text().strip()
            if t:
                return t
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or None


def _headers() -> dict[str, str]:
    h = {"User-Agent": "SymposiumBot/0.1", "Accept": "application/vnd.github+json"}
    tok = _load_token()
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


async def fetch(route: Route, client: httpx.AsyncClient) -> ExtractResult:
    if route.github_blob:
        owner, repo, ref, path = route.github_blob
        raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"
        try:
            r = await client.get(raw_url, headers={"User-Agent": "SymposiumBot/0.1"},
                                 follow_redirects=True)
        except httpx.TimeoutException:
            return ExtractResult(None, "timeout")
        if r.status_code >= 400:
            bucket = "http_4xx" if r.status_code < 500 else "http_5xx"
            return ExtractResult(None, bucket, {"http_status": r.status_code})
        text = r.text
        if not text or len(text) < 100:
            return ExtractResult(None, "empty")
        return ExtractResult(text, "ok",
                             {"source": "github_raw", "bytes": len(text.encode("utf-8"))})

    if route.github_repo:
        owner, repo = route.github_repo
        api_url = f"https://api.github.com/repos/{owner}/{repo}/readme"
        try:
            r = await client.get(api_url, headers=_headers(), follow_redirects=True)
        except httpx.TimeoutException:
            return ExtractResult(None, "timeout")
        if r.status_code >= 400:
            bucket = "http_4xx" if r.status_code < 500 else "http_5xx"
            return ExtractResult(None, bucket, {"http_status": r.status_code})
        try:
            data = r.json()
        except Exception:
            return ExtractResult(None, "other", {"error": "json_parse"})
        download_url = data.get("download_url")
        if not download_url:
            return ExtractResult(None, "empty", {"reason": "no_readme"})
        try:
            r2 = await client.get(download_url, headers={"User-Agent": "SymposiumBot/0.1"},
                                  follow_redirects=True)
        except httpx.TimeoutException:
            return ExtractResult(None, "timeout")
        if r2.status_code >= 400:
            return ExtractResult(None, "http_4xx", {"http_status": r2.status_code})
        text = r2.text
        if not text or len(text) < 100:
            return ExtractResult(None, "empty")
        return ExtractResult(
            text, "ok",
            {"source": "github_readme", "repo": f"{owner}/{repo}",
             "bytes": len(text.encode("utf-8"))},
        )

    return ExtractResult(None, "not_supported", {"reason": "no_repo_or_blob"})
