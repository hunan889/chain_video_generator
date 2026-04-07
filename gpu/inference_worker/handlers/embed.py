"""BGE embedding handler — sentence-transformers in-process.

Loads the model once on first use, then reuses it for all subsequent embed
requests. The wrapper class is intentionally tiny so we can swap models or
backends later (e.g. vLLM embedding endpoint) without touching the worker
loop.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class EmbeddingHandler:
    """Wraps a SentenceTransformer model for batch text embedding.

    The model is loaded lazily on first call so importing this module is
    cheap (sentence-transformers + torch can take ~3 s to import). Once
    loaded, ``embed_texts`` is run inside a thread to avoid blocking the
    asyncio event loop.
    """

    def __init__(
        self,
        model_name: str,
        device: str = "cuda",
        batch_size: int = 32,
    ) -> None:
        self._model_name = model_name
        self._device = device
        self._batch_size = batch_size
        self._model = None  # lazy
        self._lock = asyncio.Lock()

    @property
    def model_name(self) -> str:
        return self._model_name

    async def ensure_loaded(self) -> None:
        """Load the model if it hasn't been loaded yet."""
        if self._model is not None:
            return
        async with self._lock:
            if self._model is not None:
                return
            logger.info(
                "Loading embedding model %s on %s ...",
                self._model_name, self._device,
            )
            self._model = await asyncio.to_thread(self._load_model_sync)
            logger.info("Embedding model %s loaded", self._model_name)

    def _load_model_sync(self):
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer(self._model_name, device=self._device)

    async def embed_texts(
        self,
        texts: list[str],
        *,
        normalize: bool = True,
    ) -> list[list[float]]:
        """Embed a batch of texts. Returns plain Python lists of floats."""
        await self.ensure_loaded()
        if not texts:
            return []
        return await asyncio.to_thread(
            self._embed_sync, list(texts), normalize,
        )

    def _embed_sync(self, texts: list[str], normalize: bool) -> list[list[float]]:
        vectors = self._model.encode(
            texts,
            batch_size=self._batch_size,
            normalize_embeddings=normalize,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return vectors.tolist()


# Module-level singleton (one model per worker process)
_handler: Optional[EmbeddingHandler] = None


def get_handler(model_name: str, device: str, batch_size: int) -> EmbeddingHandler:
    """Return the process-wide EmbeddingHandler, creating it if needed."""
    global _handler
    if _handler is None:
        _handler = EmbeddingHandler(model_name, device, batch_size)
    return _handler


async def handle(payload: dict, *, model_name: str, device: str, batch_size: int) -> dict:
    """Worker entry point for ``inference_embed`` tasks.

    Payload shape::
        {
            "texts": ["text1", "text2", ...],
            "model": "bge-large-zh-v1.5",   # optional, must match worker default
            "normalize": true                # optional
        }
    """
    texts = payload.get("texts") or []
    if not isinstance(texts, list):
        raise ValueError("payload.texts must be a list")

    requested_model = payload.get("model")
    if requested_model and requested_model not in (model_name, _short_name(model_name)):
        raise ValueError(
            f"requested model {requested_model!r} does not match worker model "
            f"{model_name!r}; restart worker with EMBEDDING_MODEL={requested_model}"
        )

    normalize = bool(payload.get("normalize", True))
    handler = get_handler(model_name, device, batch_size)
    vectors = await handler.embed_texts(texts, normalize=normalize)

    return {
        "vectors": vectors,
        "model": _short_name(model_name),
        "dim": len(vectors[0]) if vectors else 0,
    }


def _short_name(full: str) -> str:
    """Return the trailing component of a HF-style model name (BAAI/bge → bge)."""
    return full.split("/")[-1]
