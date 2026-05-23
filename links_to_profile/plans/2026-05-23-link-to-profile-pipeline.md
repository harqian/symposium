# Link → Profile Pipeline Implementation Plan

## Overview

Build a pipeline that ingests the Curius bookmark export and produces JSON profiles for a cohort of users that LLM agents (the "Symposium") can use as identity/context. For each user in the cohort: an identity block, a vector of LLM-named PCA-axis coordinates derived from their bookmark embeddings, a personality blurb generated from their personal website, top-bookmarked pages, friendship-graph neighbors, and a self-declared topic histogram.

MVP cohort: 3 users — Harrison Qian (id 5790, `moonflowers.xyz`, 203 bookmarks), Eva L (id 4501, `evabrl.me`, 126 bookmarks), Danoli2 (id 1383, `vernatus.com`, 758 bookmarks).

PCA corpus: full 181,326 pages from the Curius export, fetched and embedded once, with PCA fit on a configurable subset (default: pages bookmarked by ≥3 users, ~10,051 pages) so axes are interpretable rather than dominated by the single-bookmark long tail.

## Current State Analysis

- Repo is greenfield: only `.git/` exists at `/Users/hq/github_projects/symposium/`.
- The Curius export is on disk at `/Users/hq/github_projects/symposium/curius_export_20260523_184955.json` (108 MB, 3.6M lines, top keys `exported_at`, `data`).
- `data` contains four tables: `users` (6,156), `pages` (181,326), `bookmarks` (244,544), `friendships` (17,399).
- All 3 cohort users exist in the export; their bookmark counts are confirmed at 203 / 126 / 758.
- All 3 personal sites return HTTP 200; `moonflowers.xyz` and `vernatus.com` have very small initial HTML payloads (likely JS-rendered) — Jina Reader will handle the render.
- Bookmark popularity is long-tailed: 86% of pages have a single bookmark; 10,051 pages have ≥3 bookmarks (the default PCA-fit corpus); only 3,795 have ≥5.
- Title-language scan over the ≥3-bookmark corpus: 99.2% English, 0.8% non-English (mostly Chinese). No language preprocessing needed.

## Desired End State

After running the full pipeline against the export, the following exist under `links_to_profile/`:

```
links_to_profile/
├── README.md                        # how to run, extend, scale
├── pyproject.toml, uv.lock          # uv-managed venv
├── config.yaml                      # cohort, corpus-filter, model IDs
├── src/links_to_profile/             # pipeline code
│   ├── __init__.py
│   ├── filter_export.py             # Phase 1
│   ├── fetch_personal_sites.py      # Phase 2
│   ├── personality.py               # Phase 2
│   ├── fetch_pages.py               # Phase 3
│   ├── embed.py                     # Phase 4
│   ├── pca.py                       # Phase 5
│   ├── name_axes.py                 # Phase 6
│   ├── build_profiles.py            # Phase 7
│   └── lib/
│       ├── jina.py                  # Jina Reader client w/ rate limit + retry
│       ├── openrouter.py            # OpenRouter embedding client
│       └── anthropic_client.py      # Claude Opus client
├── data/
│   ├── cohort.jsonl                 # 3 users + their bookmarks
│   ├── corpus_pages.jsonl           # all 181k pages w/ bookmark counts
│   ├── personal_sites/{username}.md
│   ├── personality/{username}.json
│   ├── pages/{page_id}.md           # fetched markdown
│   └── pages/index.jsonl            # fetch status per page_id
├── embeddings/{page_id}.npy         # 4096-dim float32, Qwen3-Embedding-8B
├── axes/
│   ├── pca_model.pkl
│   ├── page_coords.parquet          # page_id × pc1..pc10
│   ├── scree.png
│   └── named_axes.json              # LLM-named axes w/ pole descriptions
└── profiles/
    ├── harrison-qian2.json
    ├── eva-l5.json
    ├── danoli2.json
    └── {username}.md                # human-readable view
```

Each `profiles/{username}.json` validates against this schema:

```json
{
  "schema_version": 1,
  "identity": {
    "curius_id": 5790,
    "username": "harrison-qian2",
    "display_name": "Harrison Qian",
    "website": "moonflowers.xyz",
    "twitter": null,
    "self_declared_topics": ["..."]
  },
  "axes": [
    {"name": "startup-ness", "positive_pole": "...", "negative_pole": "...",
     "user_mean": 0.62, "user_stddev": 0.31, "explained_variance": 0.087}
    // ... K=10 axes
  ],
  "personality": {
    "structured": {"openness": "high", "social_disposition": "warm, curious", ...},
    "blurb": "2-3 sentence prose summary"
  },
  "top_pages": [
    {"page_id": 12345, "url": "...", "title": "...",
     "highlight_count": 4, "is_favorite": true, "bookmarked_at": "..."}
  ],
  "friendship_neighbors": [{"curius_id": 1383, "display_name": "Danoli2"}],
  "topic_histogram": {"hci": 23, "art": 17, "ML": 12, ...},
  "generated_at": "2026-05-23T...",
  "pipeline_version": "0.1.0"
}
```

### Key Discoveries
- Curius export pages have only `url`, `title`, `domain`, `created_at` — **no cached content**. We must fetch all page content ourselves.
- The `domain` field is empty for ~99.7% of pages (180,795 / 181,326). Domain extraction from URL is a needed preprocessing step (cheap).
- `friendships` is a directed graph (`user_id`, `friend_id`) — use bidirectional union for "neighbors."
- The `bookmarks.topics` field is a JSON-encoded string like `'["hci", "tech"]'`, not a real array. Parse-on-load.
- Some bookmarked URLs are arxiv PDFs, YouTube videos, GitHub repos, raw images. Jina Reader handles HTML + PDFs; videos and image-only URLs will return near-empty content (filter these by markdown length < 100 chars).

## What We're NOT Doing

- Auth-walled scraping (Twitter content past Jina's reach, LinkedIn, Substack paywalls). We accept failure on these.
- Webhook / incremental updates. This is a batch pipeline.
- Multi-modal embedding (images from `imgur`, video transcripts from `youtube`).
- Highlight/note text extraction. The Curius export records `highlight_count` but not the highlight contents.
- Friendship-graph community detection / clustering. We list direct neighbors only.
- A web UI. Output is JSON + markdown; the Symposium consumes those directly.
- Browser-based JS rendering beyond what Jina does. If Jina can't extract content, we skip.

## Implementation Approach

**Sequential phases with checkpointed intermediates.** Each phase reads from previous-phase artifacts on disk and writes its own, so re-running a single phase doesn't require re-running upstream. Page fetch (Phase 3) is by far the longest step and is fully resume-safe via `data/pages/index.jsonl`.

**Configuration in `config.yaml`** so the cohort and corpus filter are knobs, not code:
```yaml
cohort:
  curius_user_ids: [5790, 4501, 1383]
  personal_sites:
    5790: "https://moonflowers.xyz"
    4501: "https://evabrl.me"
    1383: "https://www.vernatus.com"
corpus:
  min_bookmark_count: 3      # PCA-fit filter; 1 = all pages
embedding:
  model: "qwen/qwen3-embedding-8b"
  provider: "openrouter"
  dim: 4096
  instruction: "Given a web article, retrieve articles with similar intellectual character and topic"
  max_tokens: 32000
pca:
  n_components: 10
naming:
  model: "anthropic/claude-opus-4-7"
  exemplars_per_pole: 20
```

**Secrets via 1Password CLI** (cached once per session per feedback memory):
```bash
op read "op://Personal/openrouter/api_key" > /tmp/.symposium_openrouter_key
op read "op://Personal/jina/api_key" > /tmp/.symposium_jina_key   # optional, raises rate limits
op read "op://Personal/anthropic/api_key" > /tmp/.symposium_anthropic_key
```

**Python env:** `uv venv` + `uv pip install`. Deps: `requests`, `httpx`, `pandas`, `pyarrow`, `numpy`, `scikit-learn`, `tqdm`, `pyyaml`, `anthropic`, `openai` (OpenRouter is OpenAI-API-compatible), `python-dotenv`, `tenacity`. No heavy ML deps; embedding/inference is all remote.

---

## Phase 1: Repo scaffold + filter datasets

### Overview
Create the project layout, set up the uv venv, and produce two filtered JSONL artifacts from the Curius export: the cohort and the corpus-to-fetch.

### Changes Required

#### 1. Project scaffold
**Files**: `pyproject.toml`, `config.yaml`, `src/links_to_profile/__init__.py`, `.gitignore`

`pyproject.toml`:
```toml
[project]
name = "link-to-profile"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "requests>=2.32", "httpx>=0.27", "pandas>=2.2", "pyarrow>=16",
  "numpy>=1.26", "scikit-learn>=1.5", "tqdm>=4.66", "pyyaml>=6.0",
  "anthropic>=0.40", "openai>=1.50", "tenacity>=9.0",
]

[tool.uv]
package = false
```

`.gitignore`:
```
.venv/
__pycache__/
data/pages/
embeddings/
*.npy
/tmp/.symposium_*
```
Markdown caches and embedding binaries stay out of git (will be ~12 GB once Phase 3-4 finish).

#### 2. Filter the export
**File**: `src/links_to_profile/filter_export.py`

Streams the JSON once (file is 108 MB, fits in RAM but stream-parse with `ijson` if memory pressure becomes an issue), then writes:

- `data/cohort.jsonl` — one record per cohort user: `{user_id, username, display_name, website, twitter, self_declared_topics, bookmarks: [{page_id, url, title, topics, highlight_count, is_favorite, is_to_read, bookmarked_at}], friendships: [user_id, ...]}`. ~3 lines.
- `data/corpus_pages.jsonl` — all 181,326 pages with bookmark popularity: `{page_id, url, title, domain, n_bookmarks}`. Domain extracted via `urllib.parse.urlparse` since the export's `domain` field is empty 99.7% of the time.

### Success Criteria

#### Automated Verification:
- [x] `uv sync` succeeds
- [x] `uv run python -m links_to_profile.filter_export` produces `data/cohort.jsonl` and `data/corpus_pages.jsonl`
- [x] `wc -l data/cohort.jsonl` == 3
- [x] `wc -l data/corpus_pages.jsonl` == 181326
- [x] Cohort bookmark counts match: 203 / 126 / 758 (check with `jq '.bookmarks | length' data/cohort.jsonl`)

#### Manual Verification:
- [ ] Spot-check Harrison's record in `data/cohort.jsonl` — `username == "harrison-qian2"`, `website == "moonflowers.xyz"`, friendship list non-empty.
  - NOTE during impl: Harrison has 0 friendship edges in the export (verified directly). Eva has 6, Danoli2 has 57 (bidirectional union; reciprocal edges deduped). The "friendship list non-empty" check holds for Eva and Danoli2, not Harrison.

---

## Phase 2: Personal-site fetch + personality blurb

### Overview
Fetch the 3 personal sites through Jina Reader, then run Claude Opus on each markdown to produce both a structured personality JSON and a 2-3 sentence prose blurb. Focus is on social-disposition traits (openness to conversation, curiosity, warmth), not voice/aesthetic.

### Changes Required

#### 1. Jina Reader client
**File**: `src/links_to_profile/lib/jina.py`

```python
import httpx, time
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

JINA_BASE = "https://r.jina.ai"

class JinaReader:
    def __init__(self, api_key: str | None = None, timeout: float = 60.0):
        headers = {"Accept": "text/markdown"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self.client = httpx.Client(headers=headers, timeout=timeout)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30),
           retry=retry_if_exception_type((httpx.HTTPError,)))
    def fetch(self, url: str) -> tuple[int, str]:
        r = self.client.get(f"{JINA_BASE}/{url}")
        return r.status_code, r.text
```

#### 2. Personal-site fetcher
**File**: `src/links_to_profile/fetch_personal_sites.py`

Iterates cohort, fetches each `website` via Jina, writes `data/personal_sites/{username}.md`. Skips if file already exists (resume-safe).

#### 3. Personality extraction
**File**: `src/links_to_profile/personality.py`

Sends each markdown to Claude Opus with the prompt:

```
You are profiling a person from their personal website. Read the markdown below
and produce JSON with this schema:

{
  "openness_to_strangers": "low"|"medium"|"high",
  "curiosity_breadth": "narrow"|"medium"|"wide",
  "social_disposition": "short phrase",
  "key_interests": ["..."],
  "communication_style": "short phrase",
  "blurb": "2-3 sentences describing how this person likely interacts with others"
}

Personal site markdown:
---
{markdown}
---

Be specific. Cite the evidence implicitly through the trait choices.
If the site has too little signal for a field, set it to null and continue.
```

Writes `data/personality/{username}.json`.

### Success Criteria

#### Automated Verification:
- [ ] `data/personal_sites/{harrison-qian2,eva-l5,danoli2}.md` all exist and are >500 bytes each
- [ ] `data/personality/{harrison-qian2,eva-l5,danoli2}.json` validate against schema (every required key present)
- [ ] All 3 `blurb` fields are 2-3 sentence non-empty strings

#### Manual Verification:
- [ ] Harrison's personality JSON reads accurate when checked against `moonflowers.xyz`
- [ ] Blurb tone matches the social-disposition focus, not voice/aesthetic

---

## Phase 3: Bulk page fetch via Jina Reader

### Overview
Fetch all 181,326 pages from the Curius export through Jina Reader. Resume-safe: re-running picks up from where it left off via `data/pages/index.jsonl`. This is the slowest phase (hours, possibly overnight).

### Changes Required

#### 1. Concurrent fetcher
**File**: `src/links_to_profile/fetch_pages.py`

Design:
- Read `data/corpus_pages.jsonl` for the URL list.
- Read `data/pages/index.jsonl` (if exists) for already-completed page_ids; skip those.
- Concurrent fetches using `httpx.AsyncClient` with a bounded semaphore.
- Rate-limit aware: respect Jina's free-key budget (100 RPM, 2 concurrent) or paid-key (500 RPM, 50 concurrent) based on which key is configured.
- On 429: exponential backoff + jitter, then retry.
- On 4xx (not 429) or 5xx after retries: record as failure in `index.jsonl`, continue.
- Write markdown to `data/pages/{page_id}.md` only on success and only if markdown length > 100 chars (below that → record `status: "empty"` and skip).
- Append result to `data/pages/index.jsonl` per page: `{page_id, url, status, bytes, fetched_at, error?}` where `status ∈ {ok, empty, http_4xx, http_5xx, timeout, other}`.

Pseudocode skeleton:
```python
async def fetch_one(page, sem, client, results_file):
    async with sem:
        try:
            r = await client.get(f"{JINA_BASE}/{page['url']}", timeout=60)
            if r.status_code == 429:
                # back off + retry up to N times handled by tenacity
                ...
            elif r.status_code >= 400:
                record(page, status=f"http_{r.status_code}")
                return
            md = r.text
            if len(md) < 100:
                record(page, status="empty")
                return
            (PAGES_DIR / f"{page['page_id']}.md").write_text(md)
            record(page, status="ok", bytes=len(md))
        except (httpx.TimeoutException, ...) as e:
            record(page, status="timeout", error=str(e))
```

#### 2. Progress + monitoring
- `tqdm` progress bar with ETA, success rate, current RPM.
- Every 1000 pages: log success rate to stderr and snapshot disk usage.
- Run in tmux per project conventions: `tmux new-window -t symposium -n fetch`.

### Success Criteria

#### Automated Verification:
- [ ] `wc -l data/pages/index.jsonl` == 181326 (one record per input page, success or fail)
- [ ] Success rate: `jq -s 'map(select(.status=="ok")) | length' data/pages/index.jsonl` ≥ 80% of total
- [ ] Random sample of 10 page files exists and is well-formed markdown (no raw HTML, no obvious extraction failure)
- [ ] Total markdown disk usage between 5-15 GB

#### Manual Verification:
- [ ] Pick 5 page_ids and read the markdown — confirm Jina extracted the article body, not the chrome
- [ ] Check failure breakdown: most failures should be `http_4xx` (dead links) or `empty` (videos/images), not `timeout` (which would indicate fetcher misconfiguration)

**Implementation Note**: Pause after this phase for manual confirmation. Phase 4 commits the embedding budget; verify Phase 3 looks good first.

---

## Phase 4: Embeddings via Qwen3-Embedding-8B (OpenRouter)

### Overview
Embed every successfully-fetched page using Qwen3-Embedding-8B via OpenRouter. Each embedding is 4096-dim float32, cached per page_id. Expected total: ~145k embeddings (80% of 181k), ~$3-9 at $0.01/M tokens.

### Changes Required

#### 1. OpenRouter embedding client
**File**: `src/links_to_profile/lib/openrouter.py`

OpenRouter exposes an OpenAI-compatible `/v1/embeddings` endpoint. Qwen3-Embedding-8B supports an optional instruction prefix to steer the embedding toward topical retrieval — we use it.

```python
from openai import OpenAI

client = OpenAI(base_url="https://openrouter.ai/api/v1",
                api_key=open("/tmp/.symposium_openrouter_key").read().strip())

INSTRUCTION = ("Given a web article, retrieve articles with similar "
               "intellectual character and topic")

def embed_batch(texts: list[str]) -> list[list[float]]:
    # Prepend Qwen3 instruction prefix per the Qwen3-Embedding spec
    prefixed = [f"Instruct: {INSTRUCTION}\nQuery: {t}" for t in texts]
    resp = client.embeddings.create(
        model="qwen/qwen3-embedding-8b",
        input=prefixed,
        encoding_format="float",
    )
    return [d.embedding for d in resp.data]
```

#### 2. Markdown trimmer (pre-embed)
**File**: `src/links_to_profile/lib/trim.py`

Jina Reader returns markdown with main content extracted, but the output still includes a lot of low-signal noise for embedding purposes. We strip aggressively before embedding so token cost stays in the ~1-2K/page range rather than 3-5K. This roughly halves embedding spend with minimal quality loss for topical PCA.

```python
import re

LINK_RE = re.compile(r"\[([^\]]+)\]\((?:https?://|/|#|mailto:)[^)]+\)")
IMG_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
URL_RE = re.compile(r"https?://\S+")

# Tail sections — match both header (#+) and bold (**) styles
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

# Inline CTA patterns — strip lines (not whole tail) that match
INLINE_CTA_RE = re.compile(
    r"^.{0,200}(subscribe to|sign up (for|to)|join (the |my )?newsletter|"
    r"share this on|follow me on|click here to|read the full|"
    r"thanks for reading|leave a comment).{0,200}$",
    re.IGNORECASE | re.MULTILINE,
)

# "Skip to" nav-residue at the very top
HEAD_NAV_RE = re.compile(
    r"^(skip to (main )?content|menu|search|toggle .+|breadcrumb).*$",
    re.IGNORECASE | re.MULTILINE,
)

WS_RE = re.compile(r"[ \t]+")
MULTI_NL_RE = re.compile(r"\n{3,}")

def trim(md: str, max_chars: int = 16000) -> str:
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
    # 6. strip inline CTA lines (newsletter, share, etc.)
    md = INLINE_CTA_RE.sub("", md)
    # 7. collapse whitespace
    md = WS_RE.sub(" ", md)
    md = MULTI_NL_RE.sub("\n\n", md).strip()
    # 8. cap by characters (~4 chars/token ⇒ ~4k tokens at 16k chars)
    if len(md) > max_chars:
        md = md[:max_chars]
    return md
```

Cap at 16k chars (~4k tokens). For PCA-on-topical-axes, the title + lead + key argument carries ~95% of the signal; capping beyond this is diminishing returns.

#### 2a. Trim verification (runs once before bulk embed)
**File**: `src/links_to_profile/embed.py` — `--sample N` mode

Before committing the embedding budget on 145k pages, run the trim against a random sample of 20 fetched pages and print before/after diffs. Eyeball confirms the regex isn't:

- Dropping legitimate article content (false positive on a tail-section regex)
- Leaving in obvious chrome (false negative — a CTA pattern not matched)
- Over-truncating short articles that are entirely content

Iterate trim regex against real Jina output, not imagined patterns. This 2-minute step prevents 145k embeddings of subtly-noisy text.

#### 3. Embedder
**File**: `src/links_to_profile/embed.py`

For each page in `data/pages/index.jsonl` with `status == "ok"`:
- If `embeddings/{page_id}.npy` exists → skip (resume-safe).
- Read `data/pages/{page_id}.md`, run through `trim(md)`.
- After trim, if length < 200 chars → skip (insufficient signal, log as `status: "trimmed_empty"`).
- Prepend the page title (from `data/corpus_pages.jsonl`) so it always lands in the embedding even after truncation: `f"{title}\n\n{trimmed}"`.
- Embed once (trim cap guarantees we're under Qwen3's 32k limit).
- Batch up to 64 docs per API call where possible (OpenRouter batch limit).
- Save `embeddings/{page_id}.npy` (float32, shape (4096,)).

Record per-page token counts to `data/pages/trim_stats.jsonl` (`{page_id, raw_chars, trimmed_chars, tokens_est}`) for cost auditing.

### Success Criteria

#### Automated Verification:
- [ ] Count of `.npy` files in `embeddings/` ≥ 0.95 × count of `ok` pages in index
- [ ] All saved arrays have shape `(4096,)` and dtype `float32`
- [ ] All saved arrays are approximately unit-norm (L2 norm in [0.95, 1.05])
- [ ] Total OpenRouter spend within $5 (sanity ceiling — actual expected $2-3 after trim)
- [ ] `data/pages/trim_stats.jsonl` shows median `tokens_est` < 2000 (sanity check that trim is doing real work)
- [ ] `--sample 20` mode ran and produced before/after diffs that were manually inspected

#### Manual Verification:
- [ ] Pick 5 known-topic pages (e.g. an arxiv paper, a startup essay, an art-blog post), check that pairwise cosine similarity between topically-related ones is > between unrelated ones

---

## Phase 5: PCA on the embedding corpus

### Overview
Fit PCA on the embeddings of the configurable subset (default: pages with `n_bookmarks ≥ 3`, ~10k pages). The fitted axes are then used to project every cohort user's bookmarks into PC space in Phase 7.

### Changes Required

#### 1. PCA fitter
**File**: `src/links_to_profile/pca.py`

```python
from sklearn.decomposition import PCA
import numpy as np, pandas as pd

def load_subset(min_bookmarks: int) -> tuple[np.ndarray, list[int]]:
    corpus = pd.read_json("data/corpus_pages.jsonl", lines=True)
    subset = corpus[corpus.n_bookmarks >= min_bookmarks]
    vecs, ids = [], []
    for pid in subset.page_id:
        p = Path(f"embeddings/{pid}.npy")
        if p.exists():
            vecs.append(np.load(p))
            ids.append(int(pid))
    return np.vstack(vecs), ids

X, ids = load_subset(min_bookmarks=3)
pca = PCA(n_components=10, random_state=0).fit(X)
coords = pca.transform(X)  # (N, 10)

# save
joblib.dump(pca, "axes/pca_model.pkl")
pd.DataFrame({"page_id": ids,
              **{f"pc{i+1}": coords[:, i] for i in range(10)}}
            ).to_parquet("axes/page_coords.parquet")
# scree plot
plt.plot(np.arange(1, 11), pca.explained_variance_ratio_.cumsum())
plt.savefig("axes/scree.png")
```

Use sklearn `PCA` directly (the subset fits in RAM at ~10k × 4096 = 156 MB float32). If we later scale corpus_filter=1 (all 181k), switch to `IncrementalPCA` with batches of 10k.

### Success Criteria

#### Automated Verification:
- [ ] `axes/pca_model.pkl`, `axes/page_coords.parquet`, `axes/scree.png` all exist
- [ ] `page_coords.parquet` has N rows = subset size, 11 columns (page_id + pc1..pc10)
- [ ] Cumulative explained variance for top 10 ≥ 15% (loose sanity threshold; embedding clouds at this dim never explain a huge fraction in top 10)

#### Manual Verification:
- [ ] Scree plot eyeball — should show a smooth decay; no anomalies like 90% in PC1 (would suggest data corruption)

---

## Phase 6: LLM-name the axes

### Overview
For each of the top 10 PCs: gather the 20 pages with the highest PC-k coordinate and the 20 lowest, feed (title + domain + ~300-char markdown excerpt) for those 40 pages to Claude Opus, ask it to name the axis and describe both poles.

### Changes Required

#### 1. Axis-namer
**File**: `src/links_to_profile/name_axes.py`

```python
import json, pandas as pd
from anthropic import Anthropic

client = Anthropic(api_key=open("/tmp/.symposium_anthropic_key").read().strip())

PROMPT = """\
Below are 20 web articles that score HIGH on a latent dimension, and 20 that
score LOW on the same dimension. Identify the dimension.

Output JSON ONLY:
{
  "name": "short snake_case name, e.g. startup_ness",
  "positive_pole": "what makes a page score HIGH (one sentence)",
  "negative_pole": "what makes a page score LOW (one sentence)",
  "confidence": "low" | "medium" | "high",
  "notes": "optional caveats"
}

HIGH:
{high_block}

LOW:
{low_block}
"""

def name_one_axis(pc_idx: int, page_coords, pages_meta) -> dict:
    sorted_ = page_coords.sort_values(f"pc{pc_idx+1}")
    low = sorted_.head(20)
    high = sorted_.tail(20)
    high_block = format_pages(high, pages_meta)
    low_block = format_pages(low, pages_meta)
    msg = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=1024,
        messages=[{"role": "user",
                   "content": PROMPT.format(high_block=high_block, low_block=low_block)}])
    return json.loads(msg.content[0].text)
```

Where `format_pages` joins title + domain + ~300-char excerpt of `data/pages/{page_id}.md`.

Output: `axes/named_axes.json` — list of 10 named axes with their pole exemplars (page_ids) attached for downstream introspection.

### Success Criteria

#### Automated Verification:
- [ ] `axes/named_axes.json` exists with 10 entries
- [ ] Every entry has `name`, `positive_pole`, `negative_pole`, `confidence`
- [ ] All `name` values are unique and snake_case
- [ ] No entry has `confidence == "low"` AND vacuous names like "axis_unclear"

#### Manual Verification:
- [ ] At least 6 of the 10 names pass a "could-this-describe-anything?" smell test — they should name a real intellectual dimension (e.g. `startup_vs_humanities`, `technical_depth`, `personal_essay_vs_news`)
- [ ] Pole descriptions for at least the top 5 PCs are concrete, not generic ("articles about X" rather than "more interesting articles")

---

## Phase 7: Per-user profile assembly

### Overview
For each of the 3 cohort users: load all their bookmark embeddings, project onto the PCA axes, compute mean + stddev per axis, assemble the final JSON profile + a markdown view.

### Changes Required

#### 1. Profile assembler
**File**: `src/links_to_profile/build_profiles.py`

```python
def build_profile(user_record, pca, named_axes, friendships, pages_meta):
    # 1. project user's bookmarks onto PCA axes
    bookmark_vecs = []
    for bm in user_record["bookmarks"]:
        p = Path(f"embeddings/{bm['page_id']}.npy")
        if p.exists():
            bookmark_vecs.append(np.load(p))
    if not bookmark_vecs:
        raise ValueError(f"User {user_record['username']} has no embedded bookmarks")
    user_X = np.vstack(bookmark_vecs)
    coords = pca.transform(user_X)  # (n_bookmarks, 10)

    # 2. per-axis mean + stddev
    axis_block = []
    for i, axis in enumerate(named_axes):
        axis_block.append({
            "name": axis["name"],
            "positive_pole": axis["positive_pole"],
            "negative_pole": axis["negative_pole"],
            "user_mean": float(coords[:, i].mean()),
            "user_stddev": float(coords[:, i].std()),
            "explained_variance": float(pca.explained_variance_ratio_[i]),
        })

    # 3. top pages (by highlight_count desc, then is_favorite, then recency)
    top = sorted(user_record["bookmarks"],
                 key=lambda b: (b["highlight_count"], int(b["is_favorite"]),
                                b["bookmarked_at"]),
                 reverse=True)[:20]

    # 4. topic histogram from per-bookmark topics field
    topic_hist = Counter()
    for bm in user_record["bookmarks"]:
        for t in json.loads(bm.get("topics") or "[]"):
            topic_hist[t] += 1

    # 5. friendship neighbors (bidirectional union)
    neighbors = friendships_for(user_record["user_id"], friendships)

    return {
        "schema_version": 1,
        "identity": {...},
        "axes": axis_block,
        "personality": load_personality(user_record["username"]),
        "top_pages": top,
        "friendship_neighbors": neighbors,
        "topic_histogram": dict(topic_hist),
        "generated_at": utcnow_iso(),
        "pipeline_version": "0.1.0",
    }
```

#### 2. Markdown view
For each profile JSON, render a human-readable `profiles/{username}.md` for direct prompt-injection use by the Symposium agents. Template:

```markdown
# {display_name} (@{username})

{website} · {twitter_handle_if_any}

## Personality
{blurb}

- Openness to strangers: {openness}
- Curiosity: {curiosity_breadth}
- Social disposition: {social_disposition}

## Where they sit on the Curius axes
- **{axis_name}** ({positive_pole} ↔ {negative_pole}): {user_mean:+.2f} ± {user_stddev:.2f}
...

## What they read about (top 20)
- [{title}]({url})
...

## Self-declared topics
{topic_histogram top 10}

## Connected to
- @{neighbor_username} ({display_name})
...
```

### Success Criteria

#### Automated Verification:
- [ ] `profiles/{harrison-qian2,eva-l5,danoli2}.json` all exist and validate against the schema
- [ ] Each JSON has exactly 10 axes, all axes have a `name` matching `axes/named_axes.json`
- [ ] Each JSON has ≥1 entry in `top_pages`, ≥1 entry in `topic_histogram`
- [ ] Each `.md` view exists and renders top axes + personality + top pages

#### Manual Verification:
- [ ] Harrison's own profile passes a personal smell test — the axes that score high match his actual interests, personality blurb reads accurate, top pages are recognizable
- [ ] Eva's and Danoli2's profiles read coherent (no axis with `user_mean` of NaN, no empty blocks)
- [ ] Symposium prompt-injection trial: paste `harrison-qian2.md` into a Claude system prompt and ask it to "introduce yourself in 3 sentences" — output should sound plausibly like Harrison

---

## Phase 8: README + scale-out documentation

### Overview
Document the pipeline end-to-end and the path to adding more users / different corpus filters.

### Changes Required

#### 1. `README.md`

Sections:
- **Quickstart**: 1Password commands to cache secrets, `uv sync`, then ordered `uv run python -m links_to_profile.{filter_export,fetch_personal_sites,personality,fetch_pages,embed,pca,name_axes,build_profiles}` invocations.
- **Configuration**: explains `config.yaml` knobs (cohort, corpus filter, models).
- **Adding a user**: append to `config.yaml`'s `cohort.curius_user_ids` + `personal_sites`, re-run from Phase 1 (filter), then 2, then 4 (only the new user's bookmarks need fresh embeds — Phase 3 cache hits), then 7. Phases 5 + 6 only need re-running if the corpus filter changes.
- **Changing the PCA corpus**: edit `corpus.min_bookmark_count`, re-run Phases 5-7.
- **Cost expectations**: Phase 3 fetch ~$10-50, Phase 4 embed ~$2-3, Phase 6 + 7 Claude ~$2-5, total ~$15-55 for the full corpus run.

### Success Criteria

#### Automated Verification:
- [ ] `README.md` exists
- [ ] Every command in the README is executable as-written (no placeholder `<...>` left)

#### Manual Verification:
- [ ] A fresh checkout + the README produces equivalent `profiles/*.json` outputs

---

## Testing Strategy

### Unit Tests (light, where it matters)
- `lib/jina.py`: mock httpx, verify retry logic on 429
- `lib/openrouter.py`: mock client, verify instruction prefix is applied
- `filter_export.py`: tiny fixture export, verify counts and dedup
- `pca.py`: synthetic data, verify projection roundtrip

### Integration Tests
- End-to-end on a tiny config (`cohort: [5790]`, `corpus.min_bookmark_count: 10`) to keep cost trivial. Should produce a valid `profiles/harrison-qian2.json`.

### Manual Testing Steps
1. Phase 1: inspect `data/cohort.jsonl` for the 3 users.
2. Phase 2: read each `data/personality/*.json` and ask "would I recognize this person from this?"
3. Phase 3: sample 10 markdown files at random, verify they're clean article extractions.
4. Phase 4: pick 3 known-topic page pairs, manually verify cosine similarity reflects topical similarity.
5. Phase 5: eyeball `axes/scree.png`.
6. Phase 6: read `axes/named_axes.json`, sanity-check that pole descriptions are concrete.
7. Phase 7: paste each `profiles/{user}.md` into a Claude prompt as system context, ask it to introduce the person — outputs should feel like the actual people.

## Performance Considerations

| Phase | Time (realistic) | Cost | Disk delta |
|-------|------------------|------|------------|
| 1 Filter | ~30s | $0 | ~30 MB |
| 2 Personal sites + personality | ~1 min | <$0.50 | <1 MB |
| 3 Page fetch (181k via Jina) | 6h paid / 30h free | $10-50 paid / $0 free | ~9 GB |
| 4 Embed (~145k, trimmed) | 1-2h | ~$2-3 | ~2.4 GB |
| 5 PCA fit | <1 min | $0 | <50 MB |
| 6 Name axes | ~2 min | ~$1 | <1 MB |
| 7 Build profiles | <1 min | $0 | <1 MB |
| **Total** | **~10h realistic** | **~$15-55** | **~12 GB** |

Disk: Harrison's mac had ~58 GiB free as of 2026-05-12 (project AGENTS.md); 12 GB fits comfortably but worth re-checking before Phase 3.

## Migration Notes

N/A — greenfield. The pipeline is designed for re-running individual phases when the cohort or corpus filter changes. No schema migration needed within v0.1.

## References

- Curius export: `/Users/hq/github_projects/symposium/curius_export_20260523_184955.json`
- Qwen3-Embedding-8B: https://huggingface.co/Qwen/Qwen3-Embedding-8B (4096-dim, MTEB multilingual #1, supports instruction prefix)
- Qwen3-Embedding via OpenRouter: https://openrouter.ai/qwen/qwen3-embedding-8b ($0.01/M)
- Jina Reader: https://jina.ai/reader/ (free for basic usage, paid for higher RPM)
- AGENTS.md preferences: terseness, `gog` not Gmail MCP, `op://` secret caching to `/tmp`, no em dashes
- Feedback memory cohort: confirmed Curius export schema (`users`, `pages`, `bookmarks`, `friendships`) and cohort presence in this session
