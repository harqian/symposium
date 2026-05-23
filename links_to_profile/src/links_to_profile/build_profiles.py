"""Phase 7: per-cohort-user profile assembly.

For each user in data/cohort.jsonl:
  1. Load all bookmark embeddings that exist on disk
  2. Project them onto the PCA axes
  3. Compute per-axis user_mean + user_stddev
  4. Pick top pages (highlight_count desc, then is_favorite, then recency)
  5. Build topic histogram from per-bookmark topics field
  6. Pull friendship neighbors with names
  7. Render profiles/{username}.json + profiles/{username}.md

Run:
    uv run python -m links_to_profile.build_profiles
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .pca import load_pca

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
COHORT_PATH = PROJECT_ROOT / "data" / "cohort.jsonl"
EMB_DIR = PROJECT_ROOT / "embeddings"
PERS_DIR = PROJECT_ROOT / "data" / "personality"
AXES_DIR = PROJECT_ROOT / "axes"
PCA_PATH = AXES_DIR / "pca_model.npz"
NAMED_PATH = AXES_DIR / "named_axes.json"
OUT_DIR = PROJECT_ROOT / "profiles"

PIPELINE_VERSION = "0.1.0"


def iter_jsonl(path: Path):
    with path.open() as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            yield json.loads(line)


def load_friendship_lookup(cohort: list[dict]) -> dict[int, list[dict]]:
    """For every cohort user, return friend records {curius_id, username, display_name}.
    Falls back to {user_id, username: None} when the friend isn't in cohort.jsonl."""
    # cohort.jsonl only has the 3 cohort users, but friendships reference any user
    # by users.id. We need a users-table lookup. The export is the canonical source,
    # but we don't want to reload 108 MB here — fall back to "user_id only" for now.
    by_id: dict[int, dict] = {u["user_id"]: u for u in cohort}
    out: dict[int, list[dict]] = {}
    for user in cohort:
        friends = []
        for fid in user.get("friendships", []):
            if fid in by_id:
                friends.append({
                    "user_id": fid,
                    "username": by_id[fid].get("username"),
                    "display_name": by_id[fid].get("display_name"),
                })
            else:
                friends.append({"user_id": fid, "username": None, "display_name": None})
        out[user["user_id"]] = friends
    return out


def load_personality(username: str) -> dict | None:
    p = PERS_DIR / f"{username}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text())


def parse_self_topics(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        topics = json.loads(raw)
        return [t for t in topics if isinstance(t, str)]
    except (json.JSONDecodeError, TypeError):
        return []


def project_user(user: dict, pca) -> tuple[np.ndarray | None, int, int]:
    """Returns (coords array (n_bookmarks, k), n_with_embedding, n_total).
    coords is None when nothing was embeddable."""
    vecs = []
    n_total = len(user["bookmarks"])
    for bm in user["bookmarks"]:
        p = EMB_DIR / f"{bm['page_id']}.npy"
        if p.exists():
            vecs.append(np.load(p))
    if not vecs:
        return None, 0, n_total
    X = np.vstack(vecs)
    coords = pca.transform(X)
    return coords, len(vecs), n_total


def build_axes_block(coords: np.ndarray | None, named_axes: list[dict]) -> list[dict]:
    out = []
    for i, axis in enumerate(named_axes):
        block = {
            "pc_index": axis.get("pc_index", i + 1),
            "name": axis.get("name"),
            "positive_pole": axis.get("positive_pole"),
            "negative_pole": axis.get("negative_pole"),
            "confidence": axis.get("confidence"),
            "user_mean": float(coords[:, i].mean()) if coords is not None else None,
            "user_stddev": float(coords[:, i].std()) if coords is not None else None,
        }
        out.append(block)
    return out


def top_pages(user: dict, k: int = 20) -> list[dict]:
    ranked = sorted(
        user["bookmarks"],
        key=lambda b: (
            b.get("highlight_count", 0),
            int(bool(b.get("is_favorite"))),
            b.get("bookmarked_at") or "",
        ),
        reverse=True,
    )
    return [
        {
            "page_id": b["page_id"],
            "url": b.get("url"),
            "title": b.get("title"),
            "highlight_count": b.get("highlight_count", 0),
            "is_favorite": bool(b.get("is_favorite")),
            "bookmarked_at": b.get("bookmarked_at"),
        }
        for b in ranked[:k]
    ]


def topic_histogram(user: dict) -> dict[str, int]:
    counter: Counter = Counter()
    for bm in user["bookmarks"]:
        topics = bm.get("topics")
        if not topics:
            continue
        try:
            for t in json.loads(topics):
                if isinstance(t, str):
                    counter[t] += 1
        except (json.JSONDecodeError, TypeError):
            continue
    return dict(counter.most_common())


def render_markdown(profile: dict) -> str:
    ident = profile["identity"]
    lines = [f"# {ident['display_name']} (@{ident['username']})", ""]
    extras = []
    if ident.get("website"):
        extras.append(ident["website"])
    if ident.get("twitter"):
        extras.append(f"@{ident['twitter']} (twitter)")
    if extras:
        lines += [" · ".join(extras), ""]

    pers = profile.get("personality") or {}
    structured = pers.get("structured") or {}
    if pers.get("blurb"):
        lines += ["## Personality", "", pers["blurb"], ""]
        bullets = []
        for k in ("openness_to_strangers", "curiosity_breadth",
                  "social_disposition", "communication_style"):
            v = structured.get(k)
            if v:
                bullets.append(f"- {k.replace('_', ' ')}: {v}")
        if bullets:
            lines += bullets + [""]

    lines += ["## Where they sit on the Curius axes", ""]
    for axis in profile.get("axes", []):
        if axis.get("user_mean") is None:
            continue
        name = axis.get("name") or f"pc{axis['pc_index']}"
        pos = axis.get("positive_pole") or ""
        neg = axis.get("negative_pole") or ""
        lines.append(
            f"- **{name}** ({pos} ↔ {neg}): "
            f"{axis['user_mean']:+.2f} ± {axis['user_stddev']:.2f}"
        )
    lines.append("")

    lines += ["## What they read about (top 20)", ""]
    for p in profile.get("top_pages", []):
        title = p.get("title") or "(no title)"
        url = p.get("url") or ""
        lines.append(f"- [{title}]({url})")
    lines.append("")

    self_topics = ident.get("self_declared_topics") or []
    if self_topics:
        lines += ["## Self-declared topics", "", ", ".join(self_topics), ""]

    hist = profile.get("topic_histogram") or {}
    top_hist = list(hist.items())[:10]
    if top_hist:
        lines += ["## Bookmark-tagged topics (top 10)", ""]
        for t, n in top_hist:
            lines.append(f"- {t} ({n})")
        lines.append("")

    neighbors = profile.get("friendship_neighbors") or []
    if neighbors:
        lines += ["## Connected to", ""]
        for n in neighbors:
            handle = f"@{n['username']}" if n.get("username") else f"user_id={n['user_id']}"
            name = n.get("display_name") or ""
            lines.append(f"- {handle} {('(' + name + ')') if name else ''}".rstrip())
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    config = yaml.safe_load(CONFIG_PATH.read_text())
    n_components = int(config["pca"]["n_components"])

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cohort = list(iter_jsonl(COHORT_PATH))
    named_axes = json.loads(NAMED_PATH.read_text())
    if len(named_axes) != n_components:
        print(f"warning: named_axes has {len(named_axes)} entries, "
              f"config requested {n_components}")

    pca = load_pca(PCA_PATH)
    friendship_lookup = load_friendship_lookup(cohort)

    for user in cohort:
        username = user["username"]
        coords, n_with_emb, n_total = project_user(user, pca)
        print(f"  {username}: {n_with_emb}/{n_total} bookmarks have embeddings")

        # rewrite self_declared_topics from JSON-string to list[str] in the profile
        pers = load_personality(username)
        structured = None
        blurb = None
        if pers is not None:
            blurb = pers.get("blurb")
            structured = {k: v for k, v in pers.items() if k != "blurb"}

        profile = {
            "schema_version": 1,
            "identity": {
                "user_id": user["user_id"],
                "curius_id": user.get("curius_id"),
                "username": username,
                "display_name": user.get("display_name"),
                "website": user.get("website"),
                "twitter": user.get("twitter"),
                "self_declared_topics": parse_self_topics(user.get("self_declared_topics")),
            },
            "axes": build_axes_block(coords, named_axes),
            "personality": {
                "structured": structured,
                "blurb": blurb,
            },
            "top_pages": top_pages(user),
            "friendship_neighbors": friendship_lookup.get(user["user_id"], []),
            "topic_histogram": topic_histogram(user),
            "embedding_coverage": {
                "bookmarks_with_embedding": n_with_emb,
                "bookmarks_total": n_total,
            },
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "pipeline_version": PIPELINE_VERSION,
        }

        json_path = OUT_DIR / f"{username}.json"
        md_path = OUT_DIR / f"{username}.md"
        json_path.write_text(json.dumps(profile, indent=2, ensure_ascii=False))
        md_path.write_text(render_markdown(profile))
        print(f"    wrote {json_path}, {md_path}")


if __name__ == "__main__":
    main()
