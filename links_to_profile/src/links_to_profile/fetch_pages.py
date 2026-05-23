"""Phase 3: hybrid bulk-fetch all corpus pages.

Routes each URL through the per-domain dispatcher:
  - arxiv → HTML mirror (Trafilatura) → fall back to PDF via PyMuPDF
  - youtube → youtube-transcript-api
  - github → REST /readme or raw.githubusercontent.com
  - JS-heavy hosts (twitter/x/linkedin/etc.) → Jina Reader
  - everything else → httpx + Trafilatura → Jina fallback on empty

Reads:  data/corpus_pages.jsonl
Writes:
  data/pages/{page_id}.md          (on success only, > 100 chars)
  data/pages/index.jsonl           (append-only, one line per attempt)

Resume-safe: any page_id already in index.jsonl is skipped.

Run:
    uv run python -m links_to_profile.fetch_pages
    uv run python -m links_to_profile.fetch_pages --max 100      # smoke
    uv run python -m links_to_profile.fetch_pages --no-jina-fallback   # disable Jina entirely
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import time
from collections import Counter
from pathlib import Path

import httpx
from tqdm import tqdm

from .lib.extractors import ExtractResult
from .lib.extractors import arxiv as arxiv_x
from .lib.extractors import generic as generic_x
from .lib.extractors import github as github_x
from .lib.extractors import jina as jina_x
from .lib.extractors import youtube as youtube_x
from .lib.router import Route, classify

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CORPUS_PATH = PROJECT_ROOT / "data" / "corpus_pages.jsonl"
PAGES_DIR = PROJECT_ROOT / "data" / "pages"
INDEX_PATH = PAGES_DIR / "index.jsonl"

MIN_OK_BYTES = 100
INDEX_LOCK = asyncio.Lock()

EXTRACTORS = {
    "generic": generic_x.fetch,
    "arxiv": arxiv_x.fetch,
    "youtube": youtube_x.fetch,
    "github": github_x.fetch,
    "jina": jina_x.fetch,
}

# per-extractor concurrency caps. Generic httpx + Trafilatura is fast and not
# rate-limited; arxiv PDF is gated by ToU; jina anonymous is the slowest.
DEFAULT_CONCURRENCY = {
    "generic": 50,
    "arxiv": 6,
    "youtube": 8,
    "github": 6,
    "jina": 2,
}

# fall back to jina when an extractor returns one of these statuses
JINA_FALLBACK_STATUSES = {"empty", "timeout", "http_5xx", "other", "not_supported"}


def iter_jsonl(path: Path):
    with path.open() as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            yield json.loads(line)


def load_corpus() -> list[dict]:
    return list(iter_jsonl(CORPUS_PATH))


def load_done_ids() -> set[int]:
    if not INDEX_PATH.exists():
        return set()
    done: set[int] = set()
    for r in iter_jsonl(INDEX_PATH):
        try:
            done.add(int(r["page_id"]))
        except (KeyError, ValueError, TypeError):
            continue
    return done


async def write_index_record(record: dict) -> None:
    line = json.dumps(record, ensure_ascii=False) + "\n"
    async with INDEX_LOCK:
        with INDEX_PATH.open("a") as f:
            f.write(line)


def atomic_write_md(page_id: int, body: str) -> int:
    out = PAGES_DIR / f"{page_id}.md"
    tmp = PAGES_DIR / f".{page_id}.md.tmp"
    data = body.encode("utf-8")
    tmp.write_bytes(data)
    os.replace(tmp, out)
    return len(data)


async def run_one_extractor(
    extractor_name: str,
    route: Route,
    clients: dict[str, httpx.AsyncClient],
    semaphores: dict[str, asyncio.Semaphore],
) -> ExtractResult:
    sem = semaphores[extractor_name]
    client = clients[extractor_name]
    fn = EXTRACTORS[extractor_name]
    async with sem:
        return await fn(route, client)


async def fetch_one(
    page: dict,
    clients: dict[str, httpx.AsyncClient],
    semaphores: dict[str, asyncio.Semaphore],
    pbar: tqdm,
    counts: Counter,
    enable_jina_fallback: bool,
) -> None:
    pid = int(page["page_id"])
    url = page.get("url")
    if not url:
        await write_index_record({"page_id": pid, "url": None, "status": "no_url"})
        counts["no_url"] += 1
        pbar.update(1)
        return

    route = classify(url)
    primary = route.extractor

    try:
        result = await run_one_extractor(primary, route, clients, semaphores)
    except Exception as e:
        result = ExtractResult(None, "other",
                               {"error": f"{type(e).__name__}: {e}"})

    used = primary
    fell_back = False
    if (result.status in JINA_FALLBACK_STATUSES
        and primary != "jina"
        and enable_jina_fallback):
        fell_back = True
        jina_route = Route(extractor="jina", url=url)
        try:
            result = await run_one_extractor("jina", jina_route, clients, semaphores)
        except Exception as e:
            result = ExtractResult(None, "other",
                                   {"error": f"jina_fallback: {type(e).__name__}: {e}"})
        used = "jina"

    record = {
        "page_id": pid,
        "url": url,
        "status": result.status,
        "extractor": used,
        "primary_extractor": primary,
        "jina_fallback": fell_back,
        **(result.extra or {}),
    }
    if result.status == "ok" and result.text and len(result.text) >= MIN_OK_BYTES:
        bytes_written = atomic_write_md(pid, result.text)
        record["bytes"] = bytes_written
        record["fetched_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        counts[f"ok:{used}"] += 1
    else:
        # downgrade ok-but-too-short to empty
        if result.status == "ok":
            record["status"] = "empty"
        counts[f"{record['status']}:{used}"] += 1

    await write_index_record(record)
    pbar.update(1)


def build_clients(timeout_s: float) -> dict[str, httpx.AsyncClient]:
    timeout = httpx.Timeout(timeout_s, connect=10.0)
    # generous limits — concurrency is bounded by the semaphores instead
    limits = httpx.Limits(max_connections=128, max_keepalive_connections=64)
    return {name: httpx.AsyncClient(timeout=timeout, limits=limits)
            for name in EXTRACTORS}


async def run_fetch(
    pages: list[dict],
    concurrency: dict[str, int],
    request_timeout: float,
    enable_jina_fallback: bool,
) -> Counter:
    clients = build_clients(request_timeout)
    semaphores = {name: asyncio.Semaphore(n) for name, n in concurrency.items()}
    counts: Counter = Counter()
    try:
        with tqdm(total=len(pages), unit="page", smoothing=0.05,
                  mininterval=2.0) as pbar:
            tasks = [
                asyncio.create_task(
                    fetch_one(p, clients, semaphores, pbar, counts,
                              enable_jina_fallback)
                )
                for p in pages
            ]
            done_threshold = 0
            log_every = 1000
            while pbar.n < len(pages):
                await asyncio.sleep(15)
                if pbar.n - done_threshold >= log_every:
                    print(f"\n[progress] done={pbar.n}/{len(pages)} "
                          f"buckets={dict(counts.most_common(8))}", flush=True)
                    done_threshold = pbar.n
            for t in tasks:
                await t
    finally:
        for c in clients.values():
            await c.aclose()
    return counts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=None, help="cap total pages this run")
    ap.add_argument("--sort", choices=("popularity_desc", "shuffle", "sequential"),
                    default="popularity_desc")
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--no-jina-fallback", action="store_true",
                    help="disable Jina fallback (keep budget entirely local + free)")
    ap.add_argument("--concurrency", type=str, default="",
                    help="override per-extractor concurrency, "
                         "e.g. 'generic=80,arxiv=4,jina=1'")
    args = ap.parse_args()

    PAGES_DIR.mkdir(parents=True, exist_ok=True)
    corpus = load_corpus()
    done = load_done_ids()
    pending = [p for p in corpus if int(p["page_id"]) not in done]
    print(f"corpus={len(corpus)}  already_indexed={len(done)}  pending={len(pending)}",
          flush=True)

    if args.sort == "shuffle":
        random.shuffle(pending)
    elif args.sort == "popularity_desc":
        pending.sort(key=lambda p: int(p.get("n_bookmarks", 0)), reverse=True)
    print(f"sort={args.sort}")
    if args.max is not None:
        pending = pending[: args.max]
        print(f"--max={args.max}  fetching {len(pending)} this run")

    concurrency = dict(DEFAULT_CONCURRENCY)
    if args.concurrency:
        for kv in args.concurrency.split(","):
            k, v = kv.split("=")
            concurrency[k.strip()] = int(v)
    print(f"concurrency={concurrency}  jina_fallback={not args.no_jina_fallback}")

    if not pending:
        print("nothing to fetch")
        return

    counts = asyncio.run(
        run_fetch(
            pending,
            concurrency=concurrency,
            request_timeout=args.timeout,
            enable_jina_fallback=not args.no_jina_fallback,
        )
    )
    print(f"\ndone. buckets={dict(counts)}")


if __name__ == "__main__":
    main()
