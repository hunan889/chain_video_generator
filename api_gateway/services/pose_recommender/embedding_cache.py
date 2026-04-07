"""Pose embedding cache backed by Redis.

Computes one embedding per pose at startup (via the inference_worker), then
stores the full vector dictionary in Redis under a content-hashed key. As
long as the pose set + synonyms haven't changed, gateway restarts reuse the
cached vectors and avoid the BGE round-trip.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass

from shared.redis_keys import pose_embeddings_current_key, pose_embeddings_key

from api_gateway.services.gpu_clients.inference import InferenceClient, InferenceError
from api_gateway.services.pose_recommender.mysql_loader import PoseMeta

logger = logging.getLogger(__name__)


@dataclass
class PoseEmbeddingSet:
    """Loaded set of pose embeddings ready for cosine similarity."""

    model_name: str
    dim: int
    pose_keys: list[str]                  # parallel to vectors
    vectors: list[list[float]]            # already L2-normalized
    poses_by_key: dict[str, PoseMeta]


def _content_hash(model_name: str, poses: list[PoseMeta]) -> str:
    """Stable hash over the model + poses search texts.

    Whenever a pose is added/edited or the synonyms dict changes, the hash
    changes and the cache is invalidated.
    """
    hasher = hashlib.sha256()
    hasher.update(model_name.encode("utf-8"))
    for pose in sorted(poses, key=lambda p: p.pose_key):
        hasher.update(b"\x00")
        hasher.update(pose.pose_key.encode("utf-8"))
        hasher.update(b"\x01")
        hasher.update(pose.search_text.encode("utf-8"))
    return hasher.hexdigest()[:16]


async def load_or_build_embeddings(
    *,
    redis,
    inference: InferenceClient,
    poses: list[PoseMeta],
    model_name: str = "bge-large-zh-v1.5",
) -> PoseEmbeddingSet:
    """Return embeddings for ``poses``, using Redis cache when fresh.

    Falls through to a fresh embed call against the inference worker on
    cache miss or hash mismatch.
    """
    poses_by_key = {p.pose_key: p for p in poses}
    chash = _content_hash(model_name, poses)
    cache_key = pose_embeddings_key(model_name, chash)

    # 1) Try to load from Redis
    cached_raw = await redis.get(cache_key)
    if cached_raw:
        try:
            cached = json.loads(cached_raw)
            pose_keys = cached["pose_keys"]
            vectors = cached["vectors"]
            dim = int(cached.get("dim") or (len(vectors[0]) if vectors else 0))
            if pose_keys and vectors and len(pose_keys) == len(vectors):
                logger.info(
                    "Loaded %d pose embeddings from Redis cache (hash=%s)",
                    len(pose_keys), chash,
                )
                return PoseEmbeddingSet(
                    model_name=model_name,
                    dim=dim,
                    pose_keys=pose_keys,
                    vectors=vectors,
                    poses_by_key=poses_by_key,
                )
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning("Cached pose embeddings invalid (%s); rebuilding", exc)

    # 2) Compute fresh embeddings via the inference worker
    pose_keys = [p.pose_key for p in poses]
    texts = [p.search_text for p in poses]
    logger.info(
        "Computing fresh pose embeddings: %d poses, model=%s",
        len(texts), model_name,
    )
    try:
        vectors = await inference.embed(texts, model=model_name, normalize=True)
    except InferenceError as exc:
        logger.error("Failed to compute pose embeddings: %s", exc)
        raise

    if not vectors or len(vectors) != len(pose_keys):
        raise InferenceError(
            f"embed returned {len(vectors)} vectors for {len(pose_keys)} poses"
        )
    dim = len(vectors[0])

    # 3) Persist to Redis (no TTL — invalidation is by hash change)
    payload = json.dumps({
        "model": model_name,
        "dim": dim,
        "pose_keys": pose_keys,
        "vectors": vectors,
    })
    await redis.set(cache_key, payload)
    await redis.set(pose_embeddings_current_key(model_name), chash)
    logger.info(
        "Stored %d pose embeddings in Redis cache (hash=%s, dim=%d)",
        len(pose_keys), chash, dim,
    )

    return PoseEmbeddingSet(
        model_name=model_name,
        dim=dim,
        pose_keys=pose_keys,
        vectors=vectors,
        poses_by_key=poses_by_key,
    )


def cosine_top_k(
    query_vec: list[float],
    embedding_set: PoseEmbeddingSet,
    *,
    top_k: int = 10,
) -> list[tuple[str, float]]:
    """Return ``[(pose_key, cosine_similarity), ...]`` sorted desc.

    Both inputs are assumed L2-normalized so cosine reduces to a dot product.
    Implemented in plain Python to avoid pulling numpy into the gateway.
    """
    if not query_vec:
        return []
    scored: list[tuple[str, float]] = []
    for pose_key, pose_vec in zip(embedding_set.pose_keys, embedding_set.vectors):
        score = 0.0
        for a, b in zip(query_vec, pose_vec):
            score += a * b
        scored.append((pose_key, score))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]
