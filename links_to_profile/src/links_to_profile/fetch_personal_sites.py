"""Phase 2 step 1: fetch each cohort user's personal site via Jina Reader.

Resume-safe: skips users whose markdown file already exists.

Run:
    uv run python -m links_to_profile.fetch_personal_sites
"""

from __future__ import annotations

import json
from pathlib import Path

from .lib.jina import JinaReader, load_api_key

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COHORT_PATH = PROJECT_ROOT / "data" / "cohort.jsonl"
OUT_DIR = PROJECT_ROOT / "data" / "personal_sites"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    api_key = load_api_key()
    print(f"jina api key: {'configured' if api_key else 'anonymous (slower)'}")

    with COHORT_PATH.open() as f:
        cohort = [json.loads(line) for line in f if line.strip()]

    with JinaReader(api_key=api_key) as reader:
        for user in cohort:
            username = user["username"]
            url = user.get("website")
            out_path = OUT_DIR / f"{username}.md"

            if not url:
                print(f"  skip {username}: no website")
                continue
            if out_path.exists() and out_path.stat().st_size > 0:
                print(f"  skip {username}: already fetched ({out_path.stat().st_size} bytes)")
                continue

            print(f"  fetch {username} <- {url}")
            try:
                status, body = reader.fetch(url)
            except Exception as e:
                print(f"    error: {type(e).__name__}: {e}")
                continue

            if status >= 400:
                print(f"    http {status} (body bytes={len(body)}) — not writing")
                continue

            out_path.write_text(body)
            print(f"    wrote {out_path} ({len(body)} bytes)")


if __name__ == "__main__":
    main()
