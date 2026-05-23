"""Thin Anthropic client wrapper. Shared by personality.py and name_axes.py.

Secret cache (per repo AGENTS.md):
    op read "op://Personal/anthropic/api_key" > /tmp/.symposium_anthropic_key
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from anthropic import Anthropic

KEY_PATH = Path("/tmp/.symposium_anthropic_key")

# accept either bare or provider-prefixed model ids from config
_PROVIDER_PREFIX_RE = re.compile(r"^[a-zA-Z0-9_-]+/")


def load_api_key() -> str:
    if not KEY_PATH.exists():
        raise FileNotFoundError(
            f"missing {KEY_PATH}. cache the key first:\n"
            f'  op read "op://Personal/anthropic/api_key" > {KEY_PATH}'
        )
    key = KEY_PATH.read_text().strip()
    if not key:
        raise ValueError(f"{KEY_PATH} is empty")
    return key


def normalize_model_id(model: str) -> str:
    """Strip provider prefix (e.g. 'anthropic/claude-opus-4-7' -> 'claude-opus-4-7')."""
    return _PROVIDER_PREFIX_RE.sub("", model)


def get_client() -> Anthropic:
    return Anthropic(api_key=load_api_key())


_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def extract_json(text: str) -> dict:
    """Pull JSON out of the model's response. Tolerates code fences and prose wrapping."""
    text = text.strip()
    text = _JSON_FENCE_RE.sub("", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # fallback: find first balanced {...} block
    start = text.find("{")
    if start == -1:
        raise ValueError(f"no JSON object found in model response: {text[:300]!r}")
    depth = 0
    for i, ch in enumerate(text[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError(f"unbalanced JSON in model response: {text[:300]!r}")


def complete_json(
    *,
    model: str,
    prompt: str,
    max_tokens: int = 2048,
    system: str | None = None,
) -> dict:
    """Single user-turn call that expects a JSON object back. Returns parsed dict."""
    client = get_client()
    kwargs = {
        "model": normalize_model_id(model),
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system
    msg = client.messages.create(**kwargs)
    text_blocks = [b.text for b in msg.content if getattr(b, "type", None) == "text"]
    return extract_json("\n".join(text_blocks))
