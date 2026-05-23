"""OpenRouter embedding client. OpenAI-compatible /v1/embeddings.

Secret cache:
    op read "op://Personal/openrouter/api_key" > /tmp/.symposium_openrouter_key
"""

from __future__ import annotations

from pathlib import Path

from openai import OpenAI

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
KEY_PATH = Path("/tmp/.symposium_openrouter_key")

# Qwen3-Embedding-8B supports an optional instruction prefix that steers the
# embedding toward the retrieval task. Per spec the format is:
#     Instruct: <task description>\nQuery: <text>
DEFAULT_INSTRUCTION = (
    "Given a web article, retrieve articles with similar intellectual "
    "character and topic"
)


def load_api_key() -> str:
    if not KEY_PATH.exists():
        raise FileNotFoundError(
            f"missing {KEY_PATH}. cache the key first:\n"
            f'  op read "op://Personal/openrouter/api_key" > {KEY_PATH}'
        )
    key = KEY_PATH.read_text().strip()
    if not key:
        raise ValueError(f"{KEY_PATH} is empty")
    return key


def make_client() -> OpenAI:
    return OpenAI(base_url=OPENROUTER_BASE, api_key=load_api_key())


def format_input(text: str, instruction: str = DEFAULT_INSTRUCTION) -> str:
    return f"Instruct: {instruction}\nQuery: {text}"


def embed_batch(
    client: OpenAI,
    texts: list[str],
    model: str = "qwen/qwen3-embedding-8b",
    instruction: str = DEFAULT_INSTRUCTION,
) -> list[list[float]]:
    """One API call. Returns embeddings in input order."""
    prefixed = [format_input(t, instruction) for t in texts]
    resp = client.embeddings.create(
        model=model,
        input=prefixed,
        encoding_format="float",
    )
    return [d.embedding for d in resp.data]
