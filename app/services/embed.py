"""BGE-small-zh local embedding via sentence-transformers (CPU).

Loaded once at process start (singleton) to avoid 5-10s warm-up per call.
Embedding is used in two places (沉没成本复用, Q2 decision):
- P3 ingest-time dedup: ANN query against news_event.embedding
- P5 RAG retrieval: query vector -> ANN Top-K events
"""
from __future__ import annotations

import asyncio
import os
from functools import lru_cache

# Use hf-mirror (China-friendly) by default; allow override via env.
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
# Disable HF telemetry during dev
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

from sentence_transformers import SentenceTransformer

from app.core.config import settings

EMBED_DIM = 512  # bge-small-zh output


@lru_cache(maxsize=1)
def _get_model() -> SentenceTransformer:
    """Lazy-load the model. First call downloads ~100MB weights to HF cache
    and warms up (~3-5s on CPU). Subsequent calls in same process reuse."""
    name = settings.embedding_model
    print(f"[embedding] loading {name} (CPU)...")
    m = SentenceTransformer(name, device="cpu")
    print(f"[embedding] ready, dim={m.get_sentence_embedding_dimension()}")
    return m


def embed_sync(texts: list[str]) -> list[list[float]]:
    """Sync embedding. Empty strings -> zero vectors (avoids error)."""
    if not texts:
        return []
    model = _get_model()
    cleaned = [t if t and t.strip() else "（空内容）" for t in texts]
    vecs = model.encode(cleaned, normalize_embeddings=True, show_progress_bar=False)
    return [v.tolist() for v in vecs]


async def embed_async(texts: list[str]) -> list[list[float]]:
    """Async wrapper - sentence-transformers is sync, run in worker thread."""
    return await asyncio.to_thread(embed_sync, texts)


async def embed_one(text: str) -> list[float]:
    """Embed a single text. Convenience for query encoding (P5 RAG)."""
    if not text or not text.strip():
        return [0.0] * EMBED_DIM
    vecs = await embed_async([text])
    return vecs[0]