"""Aggressive markdown trim before embedding.

Jina Reader gives us reasonably clean markdown but it's still bloated with link
URLs, image embeds, footer chrome (related-posts, subscribe, comments). For
topical PCA we only need the title + lead + key argument; everything else costs
tokens without changing the embedding much.

Goal: bring median per-page tokens from ~4-5k down to ~1-2k.
"""

from __future__ import annotations

import re

LINK_RE = re.compile(r"\[([^\]]+)\]\((?:https?://|/|#|mailto:)[^)]+\)")
IMG_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
URL_RE = re.compile(r"https?://\S+")

# tail sections — once we see one of these as a header, everything after is chrome.
TAIL_PHRASES = (
    r"related (posts|articles|reading|content)|"
    r"see also|further reading|read (next|more)|"
    r"comments?(\s*\(\d+\))?|reader comments|discussion|"
    r"tags?|categor(y|ies)|filed under|topics?|"
    r"subscribe|sign (in|up)|newsletter|email list|join (my|our|the) "
    r"(list|newsletter|mailing list)|get (more|new) (posts?|articles?|"
    r"essays?) (in|to) your inbox|"
    r"share (this|on)|follow (me|us)|"
    r"about (the )?author|author bio|written by|"
    r"footnotes?|references?|citations?|bibliography|"
    r"latest (from|posts?)|popular (posts?|now)|trending|"
    r"more from (this )?author|more (essays|posts|articles)|"
    r"previous (post|article)|next (post|article)|"
    r"like this( post)?|enjoyed this|if you liked|"
    r"support (my|our) work|buy me a coffee|"
    r"copyright|all rights reserved|©|"
    r"posted (in|by|on)|published (in|by|on)"
)
TAIL_RE = re.compile(
    rf"\n\s*(#+\s+|\*\*|__|<h[1-6][^>]*>)\s*(?:{TAIL_PHRASES})\b.*",
    re.IGNORECASE | re.DOTALL,
)

INLINE_CTA_RE = re.compile(
    r"^.{0,200}(subscribe to|sign up (for|to)|join (the |my )?newsletter|"
    r"share this on|follow me on|click here to|read the full|"
    r"thanks for reading|leave a comment).{0,200}$",
    re.IGNORECASE | re.MULTILINE,
)

HEAD_NAV_RE = re.compile(
    r"^(skip to (main )?content|menu|search|toggle .+|breadcrumb).*$",
    re.IGNORECASE | re.MULTILINE,
)

# strip Jina Reader's metadata header block (URL Source/Title/Published Time/Markdown Content)
JINA_HEADER_RE = re.compile(
    r"\A(?:Title:.*\n|URL Source:.*\n|Published Time:.*\n|Number of Pages:.*\n"
    r"|Warning:.*\n|Markdown Content:\s*\n|\s*\n)+",
    re.IGNORECASE,
)

WS_RE = re.compile(r"[ \t]+")
MULTI_NL_RE = re.compile(r"\n{3,}")


def trim(md: str, max_chars: int = 16000) -> str:
    """Aggressively trim markdown for embedding. Returns trimmed text."""
    if not md:
        return ""
    # 0. strip Jina Reader's metadata header — we re-prepend our own title in embed.py
    md = JINA_HEADER_RE.sub("", md, count=1)
    # 1. drop image markdown entirely
    md = IMG_RE.sub("", md)
    # 2. replace markdown links with anchor text only
    md = LINK_RE.sub(r"\1", md)
    # 3. drop bare URLs
    md = URL_RE.sub("", md)
    # 4. strip leading nav residue
    md = HEAD_NAV_RE.sub("", md)
    # 5. truncate at the first recognizable tail section
    md = TAIL_RE.sub("", md)
    # 6. strip inline CTA lines
    md = INLINE_CTA_RE.sub("", md)
    # 7. collapse whitespace
    md = WS_RE.sub(" ", md)
    md = MULTI_NL_RE.sub("\n\n", md).strip()
    # 8. cap by characters (~4 chars/token → ~4k tokens at 16k chars)
    if len(md) > max_chars:
        md = md[:max_chars]
    return md
