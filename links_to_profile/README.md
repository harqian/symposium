# links_to_profile

Pipeline that turns the Curius bookmark export into per-user JSON profiles
the Symposium agents can use as identity/context. Each profile has:
identity, a vector of LLM-named PCA-axis coordinates over the user's bookmark
embeddings, a personality blurb from their personal website, top-bookmarked
pages, friendship neighbors, and a self-declared topic histogram.

See [`plans/2026-05-23-link-to-profile-pipeline.md`](plans/2026-05-23-link-to-profile-pipeline.md)
for the full design. This README is the operator's guide.

## What's already in the repo (no pipeline run needed)

If you just want to *look at* the outputs, everything's checked in:

| What | Path | Format |
|---|---|---|
| **Profile per cohort user** (Harrison, Eva, Danoli2) | [`profiles/harrison-qian2.md`](profiles/harrison-qian2.md) · [`profiles/eva-l5.md`](profiles/eva-l5.md) · [`profiles/danoli2.md`](profiles/danoli2.md) | human-readable markdown |
| Same profiles as JSON (for code) | `profiles/{username}.json` | one JSON object per file |
| **PCA axis names + pole descriptions** | [`axes/named_axes.json`](axes/named_axes.json) | 10 axes, each with name + positive_pole + negative_pole + confidence |
| Fitted PCA model | `axes/pca_model.npz` + `axes/pca_meta.json` | numpy archive of mean+components+variance; load via `pca.load_pca()` |
| Per-page PCA projections | `axes/page_coords.parquet` | `page_id × pc1..pc10` |
| Scree plot | `axes/scree.png` | cumulative explained variance |
| Personality JSONs (structured + blurb) | `data/personality/{username}.json` | one file per cohort user |
| Personal-site markdown | `data/personal_sites/{username}.md` | fetched via Jina Reader |
| Cohort bookmarks + friendships | `data/cohort.jsonl` | one JSON line per user |
| All Curius users (id, curius_id, names, websites) | `data/users.jsonl` | 6,156 lines |
| Page → bookmarker user_ids | `data/page_bookmarkers.jsonl` | 181,326 lines |
| Frozen prior runs | [`snapshots/`](snapshots/) | dated subdirs, each with full INTERPRETATION.md |

Quick read for a teammate: start at [`profiles/harrison-qian2.md`](profiles/harrison-qian2.md), then [`axes/named_axes.json`](axes/named_axes.json), then [`snapshots/2026-05-23-1349/INTERPRETATION.md`](snapshots/2026-05-23-1349/INTERPRETATION.md) for context on what the axes mean.

## Quickstart

```bash
cd links_to_profile

# 1. Place the Curius export at the path in config.yaml (default:
#    ./curius_export_20260523_184955.json)

# 2. Cache secrets once per session. Re-run any time the cached key
#    expires; the modules just re-read the file.
op read "op://Private/OpenRouter/credential" > /tmp/.symposium_openrouter_key
# Anthropic key:
grep '^ANTHROPIC_API_KEY=' ~/.env_llm | sed -E 's/^[^=]+=//; s/[[:space:]]*#.*$//; s/[[:space:]]+$//' > /tmp/.symposium_anthropic_key
# GitHub token (optional; lifts rate limit 60→5000/h for repo readmes):
gh auth token > /tmp/.symposium_github_token
chmod 600 /tmp/.symposium_*_key /tmp/.symposium_*_token

# 3. Install deps
uv sync

# 4. Run the pipeline. Each phase reads upstream artifacts on disk, so you
#    can re-run a single phase without touching the others.
uv run python -m links_to_profile.filter_export
uv run python -m links_to_profile.fetch_personal_sites
uv run python -m links_to_profile.personality
uv run python -m links_to_profile.fetch_pages          # long: 1-3h
uv run python -m links_to_profile.embed                # long: ~6h
uv run python -m links_to_profile.pca
uv run python -m links_to_profile.name_axes
uv run python -m links_to_profile.build_profiles
```

The long-running phases (`fetch_pages`, `embed`) live happily in tmux:

```bash
tmux new-window -t symposium -n fetch -c $PWD
tmux send-keys -t symposium:fetch "uv run python -u -m links_to_profile.fetch_pages 2>&1 | tee -a logs/fetch_pages.log" Enter

tmux new-window -t symposium -n embed -c $PWD
tmux send-keys -t symposium:embed \
  "while true; do uv run python -u -m links_to_profile.embed --batch-size 64 2>&1 | tee -a logs/embed.log; sleep 60; done" Enter
```

Both phases are resume-safe (skip page_ids already in `data/pages/index.jsonl`
and embeddings whose `.npy` already exists), and they coexist — fetcher
appends to the index while the embedder loops over what's available so far.

## Configuration (`config.yaml`)

```yaml
cohort:
  curius_user_ids: [5790, 4501, 1383]   # NB: users.id, not users.curius_id
  personal_sites:
    5790: "https://moonflowers.xyz"
    4501: "https://evabrl.me"
    1383: "https://www.vernatus.com"

corpus:
  min_bookmark_count: 3    # PCA is fit on pages bookmarked by ≥ this many users

embedding:
  model: "qwen/qwen3-embedding-8b"
  provider: "openrouter"
  dim: 4096

pca:
  n_components: 10

naming:
  model: "anthropic/claude-opus-4-7"
  exemplars_per_pole: 20
```

`cohort.curius_user_ids` is named misleadingly — those numbers are the
**export primary key** (`users.id`), not `users.curius_id`. The plan author
named the field that way and we preserved it; the comment in `config.yaml`
documents the difference.

## Adding a user

1. Append the user's `users.id` to `cohort.curius_user_ids` and their personal
   site URL to `cohort.personal_sites` in `config.yaml`.
2. Re-run, in order:
   - `filter_export` — produces a new `data/cohort.jsonl` line.
   - `fetch_personal_sites` + `personality` — fetches the new site only.
   - `fetch_pages` and `embed` — the existing caches hit, so only the new
     user's not-yet-fetched bookmarks are processed.
   - `build_profiles` — emits the new profile alongside the others.
3. PCA + `name_axes` only need re-running if you also changed
   `corpus.min_bookmark_count`.

## Changing the PCA corpus

Set `corpus.min_bookmark_count` (e.g. 1 = full 181k corpus; 5 = only widely-
shared pages, ~3,800 pages). Then:

```bash
uv run python -m links_to_profile.pca           # refit axes
uv run python -m links_to_profile.name_axes     # rename them (Opus call)
uv run python -m links_to_profile.build_profiles
```

`pca.py` auto-switches to `IncrementalPCA` above 50k pages so the full corpus
fits in RAM.

## Phase-by-phase notes

- **Phase 1 — `filter_export`** (~30 s). Slices the 108 MB Curius dump into
  `data/cohort.jsonl` (3 cohort users) and `data/corpus_pages.jsonl` (181k
  pages with bookmark popularity).
- **Phase 2 — `fetch_personal_sites` + `personality`** (~1 min, <$0.50). Jina
  fetches the cohort's personal sites; Claude Opus emits a structured
  personality JSON per site.
- **Phase 3 — `fetch_pages`** (~1-3h, $0). **Hybrid extractor**, not the
  Jina-only path the original plan called for. Each URL is routed by domain:
  arxiv → HTML mirror (Trafilatura) → PDF (PyMuPDF) fallback; YouTube →
  transcript API; GitHub → REST `/readme` or `raw.githubusercontent.com`;
  generic → httpx + Trafilatura; JS-heavy hosts (twitter/x/linkedin/facebook)
  → Jina; everything else falls back to Jina on `empty`/`timeout`/`5xx`.
  Resume-safe via `data/pages/index.jsonl`.
- **Phase 4 — `embed`** (~6 h, ~$2-3). Qwen3-Embedding-8B via OpenRouter
  with the Qwen3 `Instruct: ...\nQuery: ...` prefix. `--sample N` previews
  trim diffs without spending budget. `--dry-run` runs the whole pipeline
  except the API call. Resume-safe via `embeddings/{page_id}.npy`.
- **Phase 5 — `pca`** (<1 min). Fits PCA on embeddings of the
  `n_bookmarks >= min_bookmark_count` subset. Serializes the model as
  `axes/pca_model.npz` plus `axes/pca_meta.json`; `load_pca()` reconstructs
  the sklearn PCA from those arrays for downstream `.transform()`.
- **Phase 6 — `name_axes`** (~2 min, ~$1). Claude Opus names each PC from
  20 high + 20 low exemplar pages (title + domain + 300-char excerpt).
- **Phase 7 — `build_profiles`** (<1 min). Projects each cohort user's
  bookmark embeddings onto PCA, computes per-axis mean+stddev, top pages,
  topic histogram, friendship neighbors. Emits `profiles/{username}.json`
  + a human-readable `.md` view for prompt-injection.
- **Phase 8** — this file.

## Cost expectations (full 181k-page run)

| Phase | Time | Cost |
|---|---|---|
| 1 Filter | ~30 s | $0 |
| 2 Personal sites + personality | ~1 min | <$0.50 |
| 3 Page fetch (hybrid) | 1-3 h | ~$0 (Jina anonymous on JS-tail only) |
| 4 Embed via OpenRouter | ~6 h | ~$2-3 |
| 5 PCA fit | <1 min | $0 |
| 6 Name axes | ~2 min | ~$1 |
| 7 Build profiles | <1 min | $0 |
| **Total** | **~10 h** | **~$4-5** |

## Disk

- `data/pages/` and `embeddings/` together top out around 12 GB for the full
  corpus. The gitignore excludes both, plus PCA artifacts, plus the raw export.
- Re-check `df -h ~` before kicking off Phase 3 if you're tight; both phases
  fail safely (Phase 3's writes are atomic via `tmp+rename`).

## What we're not doing

- Auth-walled scraping (paid Twitter, LinkedIn, paywalled Substack).
- Webhook / incremental updates — batch pipeline only.
- Multi-modal embedding (images, video — YouTube uses transcript text only).
- Highlight/note text extraction (the export records `highlight_count` but
  not the content).
- Friendship-graph community detection (we list direct neighbors only).
- A web UI — outputs are JSON + markdown for direct prompt-injection.
