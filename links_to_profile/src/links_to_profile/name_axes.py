"""Phase 6: name each PCA axis using Claude Opus.

For each principal component k:
  1. Pull the K exemplars with the highest coord_k and the K lowest
  2. Pass title + domain + ~300-char excerpt for both poles to Claude Opus
  3. Ask it to name the axis and describe both poles

Writes axes/named_axes.json — list of K named axes with exemplar page_ids.

Run:
    uv run python -m links_to_profile.name_axes
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import yaml

from .lib.anthropic_client import complete_json

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
CORPUS_PATH = PROJECT_ROOT / "data" / "corpus_pages.jsonl"
PAGES_DIR = PROJECT_ROOT / "data" / "pages"
AXES_DIR = PROJECT_ROOT / "axes"
COORDS_PATH = AXES_DIR / "page_coords.parquet"
OUT_PATH = AXES_DIR / "named_axes.json"

EXCERPT_CHARS = 300

PROMPT = """\
Below are {k} web articles that score HIGH on a latent dimension, and {k} that
score LOW on the same dimension. Identify the dimension.

Output JSON ONLY, with this exact schema:

{{
  "name": "short snake_case name, e.g. startup_ness",
  "positive_pole": "what makes a page score HIGH (one specific sentence)",
  "negative_pole": "what makes a page score LOW (one specific sentence)",
  "confidence": "low" | "medium" | "high",
  "notes": "optional caveats"
}}

Be concrete. "Articles about more interesting things" is not a useful pole;
"long-form personal essays on craft" is.

HIGH:
{high_block}

LOW:
{low_block}
"""


def iter_jsonl(path: Path):
    with path.open() as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            yield json.loads(line)


def load_pages_meta() -> dict[int, dict]:
    return {int(r["page_id"]): r for r in iter_jsonl(CORPUS_PATH)}


def excerpt(page_id: int) -> str:
    p = PAGES_DIR / f"{page_id}.md"
    if not p.exists():
        return ""
    text = p.read_text()
    # skip Jina's metadata header
    idx = text.find("Markdown Content:")
    if idx != -1:
        text = text[idx + len("Markdown Content:") :].lstrip()
    text = " ".join(text.split())  # collapse whitespace
    return text[:EXCERPT_CHARS]


def format_pages(coord_df: pd.DataFrame, meta: dict[int, dict]) -> str:
    lines = []
    for _, row in coord_df.iterrows():
        pid = int(row["page_id"])
        m = meta.get(pid, {})
        title = m.get("title") or "(no title)"
        domain = m.get("domain") or ""
        ex = excerpt(pid)
        lines.append(f"- [{domain}] {title}\n  {ex}")
    return "\n".join(lines)


def name_one_axis(
    pc_idx: int,
    coord_df: pd.DataFrame,
    meta: dict[int, dict],
    k: int,
    model: str,
) -> dict:
    col = f"pc{pc_idx + 1}"
    sorted_ = coord_df.sort_values(col)
    low = sorted_.head(k)
    high = sorted_.tail(k)

    prompt = PROMPT.format(
        k=k,
        high_block=format_pages(high, meta),
        low_block=format_pages(low, meta),
    )
    record = complete_json(model=model, prompt=prompt, max_tokens=1024)
    record["pc_index"] = pc_idx + 1
    record["high_exemplar_page_ids"] = [int(x) for x in high["page_id"].tolist()]
    record["low_exemplar_page_ids"] = [int(x) for x in low["page_id"].tolist()]
    return record


def main() -> None:
    config = yaml.safe_load(CONFIG_PATH.read_text())
    model = config["naming"]["model"]
    k = int(config["naming"]["exemplars_per_pole"])
    n_components = int(config["pca"]["n_components"])

    coord_df = pd.read_parquet(COORDS_PATH)
    meta = load_pages_meta()
    print(f"naming {n_components} axes  k={k}  model={model}")

    axes_out: list[dict] = []
    for i in range(n_components):
        print(f"  pc{i + 1} ...", flush=True)
        try:
            rec = name_one_axis(i, coord_df, meta, k, model)
        except Exception as e:
            print(f"    error: {type(e).__name__}: {e}")
            rec = {
                "pc_index": i + 1,
                "name": f"pc_{i + 1}_unnamed",
                "positive_pole": None,
                "negative_pole": None,
                "confidence": "low",
                "notes": f"naming failed: {type(e).__name__}: {e}",
            }
        axes_out.append(rec)
        print(f"    name={rec.get('name')!r} confidence={rec.get('confidence')!r}")

    OUT_PATH.write_text(json.dumps(axes_out, indent=2, ensure_ascii=False))
    print(f"  wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
