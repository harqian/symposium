"""YouTube extractor: pull video transcript via youtube-transcript-api.

This avoids Jina's youtube extraction (which returns the page chrome — useless
for topical embedding). When no transcript is available, return empty so the
caller can decide to skip or fall back.
"""

from __future__ import annotations

import asyncio

import httpx
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
)

from ..router import Route
from . import ExtractResult


def _get_transcript(video_id: str) -> str | None:
    """Try English first, fall back to any language. Returns plain text or None."""
    api = YouTubeTranscriptApi()
    try:
        transcript_list = api.list(video_id)
    except (TranscriptsDisabled, VideoUnavailable, NoTranscriptFound):
        return None
    except Exception:
        return None

    candidate = None
    for t in transcript_list:
        if t.language_code.startswith("en"):
            candidate = t
            break
    if candidate is None:
        for t in transcript_list:
            candidate = t
            break
    if candidate is None:
        return None
    try:
        segments = candidate.fetch()
    except Exception:
        return None
    text = " ".join(seg.text for seg in segments if seg.text)
    return text or None


async def fetch(route: Route, client: httpx.AsyncClient) -> ExtractResult:
    vid = route.youtube_video_id
    if not vid:
        return ExtractResult(None, "not_supported", {"reason": "no_video_id"})

    try:
        text = await asyncio.to_thread(_get_transcript, vid)
    except Exception as e:
        return ExtractResult(None, "other", {"error": f"{type(e).__name__}: {e}"})

    if not text or len(text) < 200:
        return ExtractResult(None, "empty",
                             {"reason": "no_transcript", "video_id": vid})
    return ExtractResult(
        f"YouTube video {vid} transcript:\n\n{text}",
        "ok",
        {"video_id": vid, "bytes": len(text.encode("utf-8"))},
    )
