"""Sidecar to filter_export.py: emit page→bookmarkers attribution.

filter_export.py only keeps the cohort users' bookmarks; this module emits the
full cross-user attribution so any downstream code (or sub-agent) can ask
"which Curius users bookmarked this page" without re-parsing the 108 MB export.

Writes:
  data/users.jsonl            — one line per user, all 6,156 users
  data/page_bookmarkers.jsonl — one line per page that has ≥1 bookmark:
                                {page_id, user_ids: [int, ...]}

Join shape:
  page_id in page_bookmarkers → user_ids → look up in users.jsonl (by .id)
  → get curius_id, username, display_name, website, twitter

Run:
    uv run python -m links_to_profile.attribute_pages
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
DATA_DIR = PROJECT_ROOT / "data"


def main() -> None:
    config = yaml.safe_load(CONFIG_PATH.read_text())
    export_path = PROJECT_ROOT / config["paths"]["curius_export"]

    print(f"loading export from {export_path}")
    with export_path.open() as f:
        export = json.load(f)

    users = export["data"]["users"]
    bookmarks = export["data"]["bookmarks"]
    print(f"  users={len(users)}  bookmarks={len(bookmarks)}")

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # 1. users.jsonl — full table
    users_path = DATA_DIR / "users.jsonl"
    with users_path.open("w") as f:
        for u in users:
            row = {
                "user_id": u["id"],
                "curius_id": u.get("curius_id"),
                "username": u.get("username"),
                "display_name": u.get("display_name"),
                "website": u.get("website"),
                "twitter": u.get("twitter"),
                "self_declared_topics": u.get("topics"),
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"  wrote {users_path} ({len(users)} users)")

    # 2. page_bookmarkers.jsonl — inverted index from page → user_ids
    bookmarkers: dict[int, list[int]] = defaultdict(list)
    for bm in bookmarks:
        bookmarkers[bm["page_id"]].append(bm["user_id"])

    pages_path = DATA_DIR / "page_bookmarkers.jsonl"
    with pages_path.open("w") as f:
        for pid in sorted(bookmarkers):
            f.write(json.dumps({
                "page_id": pid,
                "user_ids": sorted(bookmarkers[pid]),
            }) + "\n")
    print(f"  wrote {pages_path} ({len(bookmarkers)} pages with ≥1 bookmark)")

    # sanity: pages with ≥3 bookmarks (the PCA-fit subset)
    big = sum(1 for v in bookmarkers.values() if len(v) >= 3)
    print(f"  of which {big} have ≥3 bookmarkers (PCA-fit subset)")


if __name__ == "__main__":
    main()
