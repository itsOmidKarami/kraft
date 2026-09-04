"""Local embedding for vector search (04 §7, Effort 4C).

The only module that knows about `fastembed`. It is an optional extra: without
it FTS keeps working, hybrid search degrades to text, and an explicit
`mode=vector` is refused with the reason. Nobody who wants text search is made
to download a model.

Model choice is 04 §10's first placeholder, settled here as
`BAAI/bge-small-en-v1.5` — small, ONNX, no torch. Embeddings live in their own
table keyed to chunks, so swapping it costs an index rebuild and nothing else.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

MODEL_NAME = "BAAI/bge-small-en-v1.5"
DIMENSIONS = 384


def cache_dir() -> Path:
    """Where model weights live.

    fastembed defaults to $TMPDIR/fastembed_cache, which macOS is free to sweep
    and which does not survive a reboot — that turns a one-off 130MB download
    into a recurring one. Pin a stable path instead, overridable for tests and
    for anyone who wants the weights elsewhere.
    """
    override = os.environ.get("KRAFT_EMBED_CACHE")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "kraft" / "fastembed"


_MISSING = (
    "the 'vector' extra is not installed — `uv sync --extra vector` to enable semantic search"
)


class Embedder:
    """Lazily loads the model on first use; caches it thereafter."""

    def __init__(self, model_name: str = MODEL_NAME) -> None:
        self.model_name = model_name
        self.reason: str | None = None
        self._model = None

    def available(self) -> bool:
        """Whether the library is importable. Says nothing about whether the
        model weights are downloaded — that happens on first encode."""
        try:
            import fastembed  # noqa: F401
        except ImportError:
            self.reason = _MISSING
            return False
        return True

    def _load(self):
        if self._model is not None:
            return self._model
        from fastembed import TextEmbedding

        target = cache_dir()
        target.mkdir(parents=True, exist_ok=True)
        self._model = TextEmbedding(model_name=self.model_name, cache_dir=str(target))
        self.reason = None
        return self._model

    def encode(self, texts: list[str]) -> list[list[float]]:
        """Embed texts. Returns [] for no input without touching the model."""
        if not texts:
            return []
        if not self.available():
            raise RuntimeError(self.reason or _MISSING)
        return [list(map(float, v)) for v in self._load().embed(texts)]

    async def encode_async(self, texts: list[str]) -> list[list[float]]:
        """Model load and inference are CPU-bound; keep them off the loop."""
        if not texts:
            return []
        return await asyncio.to_thread(self.encode, texts)

    def try_encode(self, texts: list[str]) -> list[list[float]] | None:
        """Embed, or None on any failure. Ingestion uses this: losing text
        search because a model would not load is never the right trade."""
        try:
            return self.encode(texts)
        except Exception as exc:  # noqa: BLE001 - reported, never raised onward
            self.reason = str(exc)
            logger.warning("embedding unavailable: %s", exc)
            return None
