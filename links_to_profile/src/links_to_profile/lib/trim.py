"""Light markdown trim before embedding.

When pages come from Trafilatura/PyMuPDF (most of the hybrid extractor path),
they're already main-content. The previous aggressive tail-section/CTA killer
was over-fitted to Jina's chrome-heavy output and was nuking ~12% of pages.

Strategy now: minimal cleanup that's universally safe.
  - strip Jina Reader's metadata header (Title/URL Source/Published Time/...)
    when present so the title isn't double-prepended in embed.py
  - drop image markdown (no value for textual embedding)
  - collapse whitespace
  - cap at 16k chars (~4k tokens)
"""

from __future__ import annotations

import re

IMG_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")

JINA_HEADER_RE = re.compile(
    r"\A(?:Title:.*\n|URL Source:.*\n|Published Time:.*\n|Number of Pages:.*\n"
    r"|Warning:.*\n|Markdown Content:\s*\n|\s*\n)+",
    re.IGNORECASE,
)

WS_RE = re.compile(r"[ \t]+")
MULTI_NL_RE = re.compile(r"\n{3,}")


def trim(md: str, max_chars: int = 16000) -> str:
    if not md:
        return ""
    md = JINA_HEADER_RE.sub("", md, count=1)
    md = IMG_RE.sub("", md)
    md = WS_RE.sub(" ", md)
    md = MULTI_NL_RE.sub("\n\n", md).strip()
    if len(md) > max_chars:
        md = md[:max_chars]
    return md
