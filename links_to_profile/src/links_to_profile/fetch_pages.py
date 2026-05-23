"""Phase 3: bulk-fetch all corpus pages via Jina Reader.

Reads:  data/corpus_pages.jsonl
Writes:
  data/pages/{page_id}.md          (on success only, > 100 chars)
  data/pages/index.jsonl           (append-only, one line per attempt)

Resume-safe: any page_id already in index.jsonl is skipped.
Crash-safe: markdown writes are atomic (tmp + rename); index entries are appended
            only after the file is written.

Run:
    uv run python -m links_to_profile.fetch_pages
    uv run python -m links_to_profile.fetch_pages --max 100      # smoke
    uv run python -m links_to_profile.fetch_pages --concurrency 8 --target-rpm 200

Anonymous Jina is rate-limited (~20 RPM, ~2 concurrent). With a paid key, raise
--concurrency and --target-rpm.
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
from typing import Iterable

import httpx
from tqdm import tqdm

from .lib.jina import JINA_BASE, load_api_key

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CORPUS_PATH = PROJECT_ROOT / "data" / "corpus_pages.jsonl"
PAGES_DIR = PROJECT_ROOT / "data" / "pages"
INDEX_PATH = PAGES_DIR / "index.jsonl"

MIN_OK_BYTES = 100
WRITE_LOCK = asyncio.Lock()
INDEX_LOCK = asyncio.Lock()


def _iter_jsonl(path: Path):
    """Iterate parsed JSON records from a jsonl file. Uses file iteration (not
    str.splitlines) because splitlines() splits on Unicode U+2028/U+2029 which
    are valid inside JSON strings — Curius page titles contain them."""
    with path.open() as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            yield json.loads(line)


def load_corpus() -> list[dict]:
    return list(_iter_jsonl(CORPUS_PATH))


def load_done_ids() -> set[int]:
    """page_ids already recorded in index.jsonl (any status counts as done)."""
    if not INDEX_PATH.exists():
        return set()
    done: set[int] = set()
    with INDEX_PATH.open() as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            try:
                r = json.loads(line)
                done.add(int(r["page_id"]))
            except (json.JSONDecodeError, KeyError, ValueError):
                continue
    return done


async def write_index_record(record: dict) -> None:
    line = json.dumps(record, ensure_ascii=False) + "\n"
    async with INDEX_LOCK:
        # synchronous append within the lock — fast, atomic at the OS layer for small lines
        with INDEX_PATH.open("a") as f:
            f.write(line)


def atomic_write_md(page_id: int, body: str) -> int:
    """Write data/pages/{page_id}.md atomically. Returns bytes written."""
    out = PAGES_DIR / f"{page_id}.md"
    tmp = PAGES_DIR / f".{page_id}.md.tmp"
    data = body.encode("utf-8")
    tmp.write_bytes(data)
    os.replace(tmp, out)
    return len(data)


class RateLimiter:
    """Simple sliding-window limiter. Allows up to `rpm` requests per 60s window."""

    def __init__(self, rpm: int):
        self.rpm = max(1, rpm)
        self.timestamps: list[float] = []
        self.lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self.lock:
                now = time.monotonic()
                self.timestamps = [t for t in self.timestamps if now - t < 60.0]
                if len(self.timestamps) < self.rpm:
                    self.timestamps.append(now)
                    return
                sleep_for = 60.0 - (now - self.timestamps[0]) + random.uniform(0.01, 0.2)
            await asyncio.sleep(max(0.1, sleep_for))


async def fetch_one(
    page: dict,
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    limiter: RateLimiter,
    pbar: tqdm,
    counts: Counter,
) -> None:
    pid = int(page["page_id"])
    url = page.get("url")
    if not url:
        await write_index_record({"page_id": pid, "url": None, "status": "no_url"})
        counts["no_url"] += 1
        pbar.update(1)
        return

    async with sem:
        for attempt in range(4):
            await limiter.acquire()
            try:
                r = await client.get(f"{JINA_BASE}/{url}")
            except httpx.TimeoutException:
                if attempt == 3:
                    await write_index_record(
                        {"page_id": pid, "url": url, "status": "timeout"}
                    )
                    counts["timeout"] += 1
                    pbar.update(1)
                    return
                await asyncio.sleep(2 ** attempt + random.uniform(0, 1))
                continue
            except Exception as e:
                await write_index_record(
                    {
                        "page_id": pid,
                        "url": url,
                        "status": "other",
                        "error": f"{type(e).__name__}: {e}",
                    }
                )
                counts["other"] += 1
                pbar.update(1)
                return

            if r.status_code == 429:
                # respect server backoff
                wait = 5 * (2 ** attempt) + random.uniform(0, 2)
                await asyncio.sleep(wait)
                continue
            if 500 <= r.status_code < 600 and attempt < 3:
                await asyncio.sleep(2 ** attempt + random.uniform(0, 1))
                continue
            if r.status_code >= 400:
                bucket = "http_4xx" if r.status_code < 500 else "http_5xx"
                await write_index_record(
                    {
                        "page_id": pid,
                        "url": url,
                        "status": bucket,
                        "http_status": r.status_code,
                    }
                )
                counts[bucket] += 1
                pbar.update(1)
                return

            body = r.text
            if len(body) < MIN_OK_BYTES:
                await write_index_record(
                    {
                        "page_id": pid,
                        "url": url,
                        "status": "empty",
                        "bytes": len(body),
                    }
                )
                counts["empty"] += 1
                pbar.update(1)
                return

            bytes_written = atomic_write_md(pid, body)
            await write_index_record(
                {
                    "page_id": pid,
                    "url": url,
                    "status": "ok",
                    "bytes": bytes_written,
                    "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
            )
            counts["ok"] += 1
            pbar.update(1)
            return

        # exhausted retries on 429
        await write_index_record({"page_id": pid, "url": url, "status": "rate_limited"})
        counts["rate_limited"] += 1
        pbar.update(1)


async def run_fetch(
    pages: Iterable[dict],
    concurrency: int,
    target_rpm: int,
    request_timeout: float,
) -> Counter:
    api_key = load_api_key()
    headers = {"Accept": "text/markdown"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    pages = list(pages)
    sem = asyncio.Semaphore(concurrency)
    limiter = RateLimiter(target_rpm)
    counts: Counter = Counter()

    timeout = httpx.Timeout(request_timeout, connect=10.0)
    limits = httpx.Limits(max_connections=concurrency * 2, max_keepalive_connections=concurrency)

    async with httpx.AsyncClient(headers=headers, timeout=timeout, limits=limits) as client:
        with tqdm(total=len(pages), unit="page", smoothing=0.05) as pbar:
            tasks = [
                asyncio.create_task(fetch_one(p, client, sem, limiter, pbar, counts))
                for p in pages
            ]
            # progress log every 1000 completions
            log_every = 1000
            last_logged = 0
            while pbar.n < len(pages):
                await asyncio.sleep(15)
                if pbar.n - last_logged >= log_every:
                    print(
                        f"\n[progress] done={pbar.n}/{len(pages)} "
                        f"buckets={dict(counts)} rate~={pbar.format_dict.get('rate')}",
                        flush=True,
                    )
                    last_logged = pbar.n
            for t in tasks:
                await t
    return counts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=None, help="cap total pages this run (smoke test)")
    ap.add_argument("--concurrency", type=int, default=2, help="bounded by Jina free tier")
    ap.add_argument("--target-rpm", type=int, default=20, help="requests per minute ceiling")
    ap.add_argument("--timeout", type=float, default=90.0)
    ap.add_argument(
        "--shuffle",
        action="store_true",
        help="randomize fetch order so a partial run produces a diverse sample",
    )
    args = ap.parse_args()

    PAGES_DIR.mkdir(parents=True, exist_ok=True)
    corpus = load_corpus()
    done = load_done_ids()
    pending = [p for p in corpus if int(p["page_id"]) not in done]
    print(
        f"corpus={len(corpus)}  already_indexed={len(done)}  pending={len(pending)}",
        flush=True,
    )

    if args.shuffle:
        random.shuffle(pending)
    if args.max is not None:
        pending = pending[: args.max]
        print(f"--max={args.max}  fetching {len(pending)} this run")

    api_key_state = "configured" if load_api_key() else "anonymous"
    print(
        f"jina={api_key_state}  concurrency={args.concurrency}  "
        f"target_rpm={args.target_rpm}",
        flush=True,
    )

    if not pending:
        print("nothing to fetch — index already covers the full corpus")
        return

    counts = asyncio.run(
        run_fetch(
            pending,
            concurrency=args.concurrency,
            target_rpm=args.target_rpm,
            request_timeout=args.timeout,
        )
    )
    print(f"\ndone. buckets={dict(counts)}")


if __name__ == "__main__":
    main()
