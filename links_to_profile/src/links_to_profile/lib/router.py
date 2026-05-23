"""URL → extractor router.

Each URL is classified into one of: arxiv, youtube, github, jina, generic.
The router also rewrites URLs into the form the extractor expects (e.g.
arxiv /pdf/2401.12345 → /html/2401.12345 to avoid the PDF rate limit).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse


@dataclass
class Route:
    extractor: str            # 'arxiv', 'youtube', 'github', 'jina', 'generic'
    url: str                  # the URL the extractor should hit (possibly rewritten)
    arxiv_id: str | None = None
    youtube_video_id: str | None = None
    github_repo: tuple[str, str] | None = None       # (owner, repo)
    github_blob: tuple[str, str, str, str] | None = None  # (owner, repo, ref, path)


_ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})(?:v\d+)?", re.IGNORECASE)
_YT_HOST_RE = re.compile(r"(?:^|\.)(?:youtube\.com|youtu\.be|youtube-nocookie\.com)$",
                          re.IGNORECASE)


def _strip_www(host: str) -> str:
    return host.lower().removeprefix("www.")


def _extract_arxiv_id(url: str) -> str | None:
    """Pull the arxiv id out of /abs/X, /pdf/X, /html/X (with optional .pdf)."""
    parsed = urlparse(url)
    path = parsed.path
    # strip trailing .pdf
    path = re.sub(r"\.pdf$", "", path, flags=re.IGNORECASE)
    parts = [p for p in path.split("/") if p]
    if not parts:
        return None
    # arxiv URLs look like /abs/<id>, /pdf/<id>, /html/<id>, sometimes /abs/<id>v1
    last = parts[-1]
    m = _ARXIV_ID_RE.match(last)
    if m:
        return m.group(1)
    # legacy IDs like /abs/cs.CL/0301001 — fall back to "last 2 joined"
    if len(parts) >= 2 and re.match(r"[a-z\-]+(\.[A-Z]{2})?", parts[-2], re.IGNORECASE):
        return f"{parts[-2]}/{last}"
    return None


def _extract_youtube_id(url: str) -> str | None:
    parsed = urlparse(url)
    host = _strip_www(parsed.netloc)
    if host == "youtu.be":
        vid = parsed.path.lstrip("/").split("/")[0]
        return vid or None
    # parse v= from query
    from urllib.parse import parse_qs
    qs = parse_qs(parsed.query)
    if "v" in qs and qs["v"]:
        return qs["v"][0]
    # /shorts/<id> or /embed/<id>
    m = re.match(r"^/(?:shorts|embed|v|live)/([^/]+)", parsed.path)
    if m:
        return m.group(1)
    return None


def _extract_github(url: str):
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        return None, None
    owner, repo = parts[0], parts[1]
    repo = re.sub(r"\.git$", "", repo)
    if len(parts) >= 5 and parts[2] in ("blob", "tree", "raw"):
        ref = parts[3]
        path = "/".join(parts[4:])
        return (owner, repo), (owner, repo, ref, path)
    return (owner, repo), None


def classify(url: str) -> Route:
    """Decide which extractor handles a URL. Falls back to 'generic'."""
    if not url:
        return Route(extractor="generic", url=url)
    parsed = urlparse(url)
    host = _strip_www(parsed.netloc)

    if host.endswith("arxiv.org"):
        aid = _extract_arxiv_id(url)
        if aid:
            # rewrite to the HTML mirror; extractor will fall back to PDF if 404
            rewritten = urlunparse(parsed._replace(
                path=f"/html/{aid}", query="", fragment=""))
            return Route(extractor="arxiv", url=rewritten, arxiv_id=aid)

    if _YT_HOST_RE.search(host):
        vid = _extract_youtube_id(url)
        if vid:
            return Route(extractor="youtube", url=url, youtube_video_id=vid)

    if host in ("github.com", "gist.github.com"):
        if host == "github.com":
            repo, blob = _extract_github(url)
            if repo is not None:
                return Route(
                    extractor="github",
                    url=url,
                    github_repo=repo,
                    github_blob=blob,
                )
        # gist falls through to generic — its content is JS-rendered, jina handles it

    # known-JS sites where generic Trafilatura is doomed: route straight to jina
    JS_HEAVY = {
        "twitter.com", "x.com", "linkedin.com", "instagram.com",
        "facebook.com", "tiktok.com", "reddit.com",
    }
    if host in JS_HEAVY or any(host.endswith("." + h) for h in JS_HEAVY):
        return Route(extractor="jina", url=url)

    return Route(extractor="generic", url=url)
