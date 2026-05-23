"""Phase 1: slice the Curius export into cohort.jsonl and corpus_pages.jsonl.

Run from the project root:
    uv run python -m links_to_profile.filter_export

Outputs:
    data/cohort.jsonl        — one record per cohort user (3 lines)
    data/corpus_pages.jsonl  — one record per page (181,326 lines)
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
DATA_DIR = PROJECT_ROOT / "data"


def extract_domain(url: str) -> str:
    """urlparse-based domain extraction. handles missing scheme."""
    if not url:
        return ""
    parsed = urlparse(url if "://" in url else f"http://{url}")
    return parsed.netloc.lower().removeprefix("www.")


def load_export(path: Path) -> dict:
    with path.open() as f:
        return json.load(f)


def build_cohort_records(
    export: dict,
    cohort_ids: list[int],
    personal_sites: dict[int, str],
) -> list[dict]:
    """One record per cohort user, with their bookmarks + outgoing/incoming friend ids."""
    users_by_id = {u["id"]: u for u in export["data"]["users"]}

    bookmarks_by_user: dict[int, list[dict]] = defaultdict(list)
    for bm in export["data"]["bookmarks"]:
        if bm["user_id"] in cohort_ids:
            bookmarks_by_user[bm["user_id"]].append(bm)

    # bidirectional union of friendship edges per user
    friends_by_user: dict[int, set[int]] = defaultdict(set)
    for f in export["data"]["friendships"]:
        if f["user_id"] in cohort_ids:
            friends_by_user[f["user_id"]].add(f["friend_id"])
        if f["friend_id"] in cohort_ids:
            friends_by_user[f["friend_id"]].add(f["user_id"])

    pages_by_id = {p["id"]: p for p in export["data"]["pages"]}

    records = []
    for uid in cohort_ids:
        if uid not in users_by_id:
            raise ValueError(f"cohort id {uid} not found in users table")
        u = users_by_id[uid]
        # personal_sites config overrides users.website (most cohort users have null)
        site = personal_sites.get(uid) or u.get("website")

        bookmarks_out = []
        for bm in bookmarks_by_user.get(uid, []):
            page = pages_by_id.get(bm["page_id"])
            bookmarks_out.append({
                "page_id": bm["page_id"],
                "url": page["url"] if page else None,
                "title": page["title"] if page else None,
                "topics": bm.get("topics"),  # JSON-encoded string per export; parse later
                "highlight_count": bm.get("highlight_count", 0),
                "is_favorite": bool(bm.get("is_favorite", 0)),
                "is_to_read": bool(bm.get("is_to_read", 0)),
                "bookmarked_at": bm.get("bookmarked_at"),
            })

        records.append({
            "user_id": uid,
            "curius_id": u.get("curius_id"),
            "username": u.get("username"),
            "display_name": u.get("display_name"),
            "website": site,
            "twitter": u.get("twitter"),
            "self_declared_topics": u.get("topics"),  # JSON-encoded string; parse later
            "bookmarks": bookmarks_out,
            "friendships": sorted(friends_by_user.get(uid, set())),
        })
    return records


def build_corpus_records(export: dict) -> list[dict]:
    """All pages with bookmark popularity. ~181k records."""
    bookmark_counts: dict[int, int] = defaultdict(int)
    for bm in export["data"]["bookmarks"]:
        bookmark_counts[bm["page_id"]] += 1

    out = []
    for p in export["data"]["pages"]:
        domain = p.get("domain") or extract_domain(p.get("url", ""))
        out.append({
            "page_id": p["id"],
            "url": p.get("url"),
            "title": p.get("title"),
            "domain": domain,
            "n_bookmarks": bookmark_counts.get(p["id"], 0),
        })
    return out


def write_jsonl(records: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def main() -> None:
    config = yaml.safe_load(CONFIG_PATH.read_text())
    cohort_ids = list(config["cohort"]["curius_user_ids"])
    personal_sites = {int(k): v for k, v in config["cohort"]["personal_sites"].items()}
    export_path = PROJECT_ROOT / config["paths"]["curius_export"]

    print(f"loading export from {export_path}")
    export = load_export(export_path)
    counts = {k: len(v) for k, v in export["data"].items()}
    print(f"  loaded: {counts}")

    print(f"building cohort records for ids={cohort_ids}")
    cohort = build_cohort_records(export, cohort_ids, personal_sites)
    cohort_path = DATA_DIR / "cohort.jsonl"
    write_jsonl(cohort, cohort_path)
    for r in cohort:
        print(
            f"  id={r['user_id']} username={r['username']!r} "
            f"bookmarks={len(r['bookmarks'])} friends={len(r['friendships'])}"
        )
    print(f"  wrote {cohort_path}")

    print("building corpus page records")
    corpus = build_corpus_records(export)
    corpus_path = DATA_DIR / "corpus_pages.jsonl"
    write_jsonl(corpus, corpus_path)
    print(f"  wrote {corpus_path} ({len(corpus)} records)")


if __name__ == "__main__":
    main()
