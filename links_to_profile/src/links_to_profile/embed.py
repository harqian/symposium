"""Phase 4: embed every successfully-fetched page via Qwen3-Embedding-8B.

Reads:
    data/pages/index.jsonl    — status==ok rows
    data/pages/{page_id}.md   — raw markdown
    data/corpus_pages.jsonl   — page titles (for the embed-with-title prefix)

Writes:
    embeddings/{page_id}.npy           — float32, shape (4096,)
    data/pages/trim_stats.jsonl        — per-page raw/trimmed chars + token estimate

Modes:
    uv run python -m links_to_profile.embed                 # full embed
    uv run python -m links_to_profile.embed --sample 20     # trim diff only, no API
    uv run python -m links_to_profile.embed --max 100       # embed cap (smoke)
    uv run python -m links_to_profile.embed --dry-run       # trim + count, no API

Resume-safe: skips page_ids whose .npy file already exists.
"""

from __future__ import annotations

import argparse
import json
import random
import time
from collections import Counter
from pathlib import Path

import numpy as np
import yaml
from tqdm import tqdm

from .lib.openrouter import (
    DEFAULT_INSTRUCTION,
    embed_batch,
    make_client,
)
from .lib.trim import trim

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
INDEX_PATH = PROJECT_ROOT / "data" / "pages" / "index.jsonl"
PAGES_DIR = PROJECT_ROOT / "data" / "pages"
CORPUS_PATH = PROJECT_ROOT / "data" / "corpus_pages.jsonl"
EMB_DIR = PROJECT_ROOT / "embeddings"
TRIM_STATS_PATH = PAGES_DIR / "trim_stats.jsonl"

MIN_TRIMMED_CHARS = 200


def iter_jsonl(path: Path):
    with path.open() as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            yield json.loads(line)


def load_titles() -> dict[int, str]:
    return {int(r["page_id"]): (r.get("title") or "") for r in iter_jsonl(CORPUS_PATH)}


def load_ok_page_ids() -> list[int]:
    return [int(r["page_id"]) for r in iter_jsonl(INDEX_PATH) if r.get("status") == "ok"]


def estimate_tokens(text: str) -> int:
    """Crude ~4 chars/token estimate."""
    return max(1, len(text) // 4)


def build_input(title: str, trimmed: str) -> str:
    """Prepend the page title so it lands in the embedding even if we truncate."""
    if title:
        return f"{title}\n\n{trimmed}"
    return trimmed


def run_sample_mode(n: int) -> None:
    """Show before/after diffs for n random fetched pages. No API calls."""
    ok_ids = load_ok_page_ids()
    if len(ok_ids) < n:
        print(f"only {len(ok_ids)} ok pages, sampling all")
        sample = ok_ids
    else:
        sample = random.sample(ok_ids, n)
    print(f"--sample {n}: trim diff on {len(sample)} pages\n")
    for pid in sample:
        md_path = PAGES_DIR / f"{pid}.md"
        if not md_path.exists():
            continue
        raw = md_path.read_text()
        trimmed = trim(raw)
        print(f"=== page_id={pid}  raw={len(raw)}  trimmed={len(trimmed)} "
              f"(ratio={len(trimmed)/max(1,len(raw)):.2f})")
        print(f"--- TRIMMED HEAD ---")
        print(trimmed[:600])
        print(f"--- TRIMMED TAIL ---")
        print(trimmed[-400:] if len(trimmed) > 400 else "")
        print()


def append_trim_stat(stat: dict) -> None:
    with TRIM_STATS_PATH.open("a") as f:
        f.write(json.dumps(stat) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=None,
                    help="show trim diffs on N random pages, no API calls")
    ap.add_argument("--dry-run", action="store_true",
                    help="trim everything and log stats but skip API calls")
    ap.add_argument("--max", type=int, default=None,
                    help="cap embeddings this run (smoke test)")
    ap.add_argument("--batch-size", type=int, default=16,
                    help="docs per OpenRouter embeddings call (max 64)")
    args = ap.parse_args()

    config = yaml.safe_load(CONFIG_PATH.read_text())
    model = config["embedding"]["model"]
    instruction = config["embedding"].get("instruction") or DEFAULT_INSTRUCTION
    expected_dim = int(config["embedding"]["dim"])

    PAGES_DIR.mkdir(parents=True, exist_ok=True)
    EMB_DIR.mkdir(parents=True, exist_ok=True)

    if args.sample is not None:
        run_sample_mode(args.sample)
        return

    titles = load_titles()
    ok_ids = load_ok_page_ids()
    pending = [pid for pid in ok_ids if not (EMB_DIR / f"{pid}.npy").exists()]
    print(f"ok pages={len(ok_ids)}  already embedded={len(ok_ids) - len(pending)}  "
          f"pending={len(pending)}")
    if args.max is not None:
        pending = pending[: args.max]
        print(f"--max={args.max}  embedding {len(pending)} this run")
    if not pending:
        print("nothing to do")
        return

    client = None if args.dry_run else make_client()

    counts: Counter = Counter()
    pbar = tqdm(total=len(pending), unit="page")

    batch_ids: list[int] = []
    batch_inputs: list[str] = []
    batch_stats: list[dict] = []

    def flush_batch() -> None:
        nonlocal batch_ids, batch_inputs, batch_stats
        if not batch_inputs:
            return
        if args.dry_run:
            # synthesize zeros; only count tokens
            for stat in batch_stats:
                append_trim_stat(stat)
            counts["dry_ok"] += len(batch_ids)
            pbar.update(len(batch_ids))
            batch_ids, batch_inputs, batch_stats = [], [], []
            return

        try:
            vecs = embed_batch(client, batch_inputs, model=model, instruction=instruction)
        except Exception as e:
            print(f"\n  batch error ({len(batch_inputs)} items): {type(e).__name__}: {e}")
            counts["api_error"] += len(batch_ids)
            pbar.update(len(batch_ids))
            batch_ids, batch_inputs, batch_stats = [], [], []
            time.sleep(5)
            return

        for pid, vec, stat in zip(batch_ids, vecs, batch_stats):
            arr = np.asarray(vec, dtype=np.float32)
            if arr.shape != (expected_dim,):
                print(f"\n  page {pid}: unexpected embedding shape {arr.shape}, skipping")
                counts["bad_shape"] += 1
                continue
            np.save(EMB_DIR / f"{pid}.npy", arr)
            stat["embedded"] = True
            append_trim_stat(stat)
            counts["ok"] += 1
        pbar.update(len(batch_ids))
        batch_ids, batch_inputs, batch_stats = [], [], []

    for pid in pending:
        md_path = PAGES_DIR / f"{pid}.md"
        if not md_path.exists():
            counts["missing_md"] += 1
            pbar.update(1)
            continue
        raw = md_path.read_text()
        trimmed = trim(raw)
        if len(trimmed) < MIN_TRIMMED_CHARS:
            append_trim_stat({
                "page_id": pid, "raw_chars": len(raw), "trimmed_chars": len(trimmed),
                "tokens_est": estimate_tokens(trimmed), "embedded": False,
                "reason": "trimmed_empty",
            })
            counts["trimmed_empty"] += 1
            pbar.update(1)
            continue

        text = build_input(titles.get(pid, ""), trimmed)
        batch_ids.append(pid)
        batch_inputs.append(text)
        batch_stats.append({
            "page_id": pid, "raw_chars": len(raw), "trimmed_chars": len(trimmed),
            "tokens_est": estimate_tokens(text),
        })
        if len(batch_inputs) >= args.batch_size:
            flush_batch()

    flush_batch()
    pbar.close()
    print(f"\ndone. buckets={dict(counts)}")


if __name__ == "__main__":
    main()
