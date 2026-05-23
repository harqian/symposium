"""Phase 2 step 2: extract a structured personality JSON + blurb per cohort user.

Reads data/personal_sites/{username}.md, calls Claude Opus, writes
data/personality/{username}.json.

Resume-safe: skips users with an existing personality JSON.

Run:
    uv run python -m links_to_profile.personality
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from .lib.anthropic_client import complete_json

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
COHORT_PATH = PROJECT_ROOT / "data" / "cohort.jsonl"
SITES_DIR = PROJECT_ROOT / "data" / "personal_sites"
OUT_DIR = PROJECT_ROOT / "data" / "personality"

REQUIRED_KEYS = {
    "openness_to_strangers",
    "curiosity_breadth",
    "social_disposition",
    "key_interests",
    "communication_style",
    "blurb",
}

PROMPT_TEMPLATE = """\
You are profiling a person from their personal website. Read the markdown below
and produce JSON with this exact schema:

{{
  "openness_to_strangers": "low" | "medium" | "high" | null,
  "curiosity_breadth": "narrow" | "medium" | "wide" | null,
  "social_disposition": "short phrase, e.g. 'warm, curious, prefers depth'",
  "key_interests": ["..."],
  "communication_style": "short phrase",
  "blurb": "2-3 sentences describing how this person likely interacts with others"
}}

Focus on social disposition (openness to conversation, curiosity, warmth),
not voice or aesthetic. Be specific. Cite evidence implicitly through the
trait choices. If a field has too little signal, set it to null and continue.

Output JSON ONLY, no prose, no code fences.

Personal site markdown for {display_name} ({username}):
---
{markdown}
---
"""


def validate(record: dict) -> list[str]:
    """Return a list of validation errors, or [] if record is fine."""
    errors: list[str] = []
    missing = REQUIRED_KEYS - set(record)
    if missing:
        errors.append(f"missing keys: {sorted(missing)}")
    blurb = record.get("blurb")
    if not isinstance(blurb, str) or len(blurb) < 30:
        errors.append(f"blurb too short or not a string: {blurb!r}")
    return errors


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    config = yaml.safe_load(CONFIG_PATH.read_text())
    model = config["naming"]["model"]

    with COHORT_PATH.open() as f:
        cohort = [json.loads(line) for line in f if line.strip()]

    for user in cohort:
        username = user["username"]
        display_name = user.get("display_name") or username
        md_path = SITES_DIR / f"{username}.md"
        out_path = OUT_DIR / f"{username}.json"

        if out_path.exists() and out_path.stat().st_size > 0:
            print(f"  skip {username}: already extracted")
            continue
        if not md_path.exists():
            print(f"  skip {username}: no personal site markdown at {md_path}")
            continue

        markdown = md_path.read_text()
        print(f"  extract {username} from {md_path} ({len(markdown)} bytes)")

        prompt = PROMPT_TEMPLATE.format(
            display_name=display_name,
            username=username,
            markdown=markdown,
        )
        try:
            record = complete_json(model=model, prompt=prompt, max_tokens=2048)
        except Exception as e:
            print(f"    error: {type(e).__name__}: {e}")
            continue

        errors = validate(record)
        if errors:
            print(f"    validation errors: {errors}")
            print(f"    raw record: {record}")
            continue

        out_path.write_text(json.dumps(record, indent=2, ensure_ascii=False))
        print(f"    wrote {out_path}")


if __name__ == "__main__":
    main()
