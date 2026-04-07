"""Three-stage pose recommender with 4-tier fallback.

Tier 0: synonym expansion + BGE embedding match + LLM rerank   (full)
Tier 1: synonym expansion + BGE embedding match                 (LLM dead)
Tier 2: synonym expansion + keyword scoring                     (BGE dead)
Tier 3: legacy substring matching                               (zero deps)

The recommender is constructed once per gateway process via
:func:`get_pose_recommender`. It lazily loads pose metadata + embeddings
on first use; subsequent calls reuse the cached state.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional

from api_gateway.config import GatewayConfig
from api_gateway.services.gpu_clients.inference import (
    InferenceClient,
    InferenceError,
    InferenceTimeout,
)
from api_gateway.services.pose_recommender.embedding_cache import (
    PoseEmbeddingSet,
    cosine_top_k,
    load_or_build_embeddings,
)
from api_gateway.services.pose_recommender.mysql_loader import (
    PoseMeta,
    load_enabled_poses,
)
from api_gateway.services.pose_recommender.synonyms import (
    POSE_SYNONYMS,
    expand_query,
    find_matching_pose_keys,
    get_synonyms,
)

logger = logging.getLogger(__name__)


# Score thresholds (cosine sims, 0..1)
DEFAULT_MIN_SCORE = 0.5
LLM_TRUST_THRESHOLD = 0.85   # if top embedding score >= this, skip LLM rerank
EMBEDDING_TOP_K = 10


@dataclass
class PoseRecommendation:
    """Single recommendation result."""

    pose_key: str
    name_cn: str
    name_en: str
    score: float
    match_reason: str
    category: str

    def to_dict(self) -> dict:
        return {
            "pose_key": self.pose_key,
            "name_cn": self.name_cn,
            "name_en": self.name_en,
            "score": float(self.score),
            "match_reason": self.match_reason,
            "category": self.category,
        }


class PoseRecommender:
    """Three-stage recommender with fallback chain.

    Lazily initialised: the first call to :meth:`recommend` will load poses
    from MySQL and either fetch cached embeddings from Redis or compute
    fresh ones via the inference worker.
    """

    def __init__(
        self,
        config: GatewayConfig,
        redis,
        inference: Optional[InferenceClient] = None,
        embedding_model: str = "bge-large-zh-v1.5",
    ) -> None:
        self._config = config
        self._redis = redis
        self._inference = inference or InferenceClient(redis)
        self._embedding_model = embedding_model
        self._poses: Optional[list[PoseMeta]] = None
        self._poses_by_key: dict[str, PoseMeta] = {}
        self._embedding_set: Optional[PoseEmbeddingSet] = None
        self._init_lock = asyncio.Lock()
        self._init_failed_at: float = 0.0  # for backoff

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------
    async def initialize(self) -> None:
        """Load poses + embeddings. Idempotent.

        Embedding load may fail (worker dead) — that's OK, the recommender
        will gracefully fall back to Tier 2 (keyword scoring).
        """
        async with self._init_lock:
            if self._poses is None:
                self._poses = await load_enabled_poses(self._config)
                self._poses_by_key = {p.pose_key: p for p in self._poses}

            if self._embedding_set is None:
                try:
                    self._embedding_set = await load_or_build_embeddings(
                        redis=self._redis,
                        inference=self._inference,
                        poses=self._poses,
                        model_name=self._embedding_model,
                    )
                except (InferenceError, InferenceTimeout) as exc:
                    logger.warning(
                        "Pose embedding initialisation failed (%s); "
                        "recommender will run in Tier 2 keyword mode",
                        exc,
                    )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def recommend(
        self,
        prompt: str,
        *,
        top_k: int = 5,
        min_score: float = DEFAULT_MIN_SCORE,
        use_llm: bool = True,
    ) -> list[PoseRecommendation]:
        """Return up to ``top_k`` poses, descending by score.

        Empty list = no pose was confident enough.
        """
        if not prompt or not prompt.strip():
            return []
        await self.initialize()

        # Tier 0/1: full pipeline (synonym + embedding +/- LLM)
        if self._embedding_set is not None:
            try:
                return await self._embedding_pipeline(
                    prompt, top_k=top_k, min_score=min_score, use_llm=use_llm,
                )
            except (InferenceError, InferenceTimeout) as exc:
                logger.warning("Embedding pipeline failed: %s; falling back", exc)

        # Tier 2: synonym + keyword scoring (no GPU calls)
        try:
            return self._keyword_pipeline(prompt, top_k=top_k, min_score=min_score)
        except Exception as exc:
            logger.exception("Keyword pipeline failed: %s; falling back to legacy", exc)

        # Tier 3: legacy substring matching
        return self._legacy_substring(prompt, top_k=top_k, min_score=min_score)

    # ------------------------------------------------------------------
    # Tier 0/1: Embedding pipeline
    # ------------------------------------------------------------------
    async def _embedding_pipeline(
        self,
        prompt: str,
        *,
        top_k: int,
        min_score: float,
        use_llm: bool,
    ) -> list[PoseRecommendation]:
        assert self._embedding_set is not None  # for type checker

        # Stage 1: synonym expansion + direct synonym matches as a strong prior
        synonym_matches = find_matching_pose_keys(prompt)
        expanded = expand_query(prompt)

        # Stage 2: embedding match against expanded query
        query_vecs = await self._inference.embed([expanded], model=self._embedding_model)
        if not query_vecs:
            raise InferenceError("embed returned empty result")
        query_vec = query_vecs[0]

        embedding_scores = dict(cosine_top_k(
            query_vec, self._embedding_set, top_k=EMBEDDING_TOP_K,
        ))

        # Combine: a direct synonym match dominates by adding +0.5 to embedding
        # score, scaled by phrase length (multi-word synonyms outrank single words).
        combined: dict[str, float] = dict(embedding_scores)
        for pose_key, word_count in synonym_matches:
            boost = min(0.4 + 0.05 * word_count, 0.55)
            combined[pose_key] = combined.get(pose_key, 0.0) + boost

        # Build candidate recommendations
        candidates: list[PoseRecommendation] = []
        for pose_key, score in sorted(combined.items(), key=lambda x: x[1], reverse=True):
            pose = self._poses_by_key.get(pose_key)
            if pose is None:
                continue
            candidates.append(PoseRecommendation(
                pose_key=pose_key,
                name_cn=pose.name_cn,
                name_en=pose.name_en,
                score=float(score),
                match_reason=self._reason_for(pose, score),
                category=pose.category or "uncategorized",
            ))
            if len(candidates) >= EMBEDDING_TOP_K:
                break

        if not candidates:
            return []

        # Stage 3: LLM rerank if top score isn't already confident
        top_score = candidates[0].score
        if use_llm and len(candidates) > 1 and top_score < LLM_TRUST_THRESHOLD:
            try:
                candidates = await self._llm_rerank(prompt, candidates)
            except (InferenceError, InferenceTimeout) as exc:
                logger.warning("LLM rerank skipped: %s", exc)

        # Filter by min_score and clip to top_k
        filtered = [c for c in candidates if c.score >= min_score]
        return filtered[:top_k]

    async def _llm_rerank(
        self,
        prompt: str,
        candidates: list[PoseRecommendation],
    ) -> list[PoseRecommendation]:
        """Ask the LLM to re-order candidates by relevance to the query.

        Returns the reranked candidates with score recomputed from the new
        position (highest = original highest, descending).
        """
        if len(candidates) <= 1:
            return candidates

        sorted_candidates = sorted(candidates, key=lambda c: c.score, reverse=True)
        lines = []
        for i, c in enumerate(sorted_candidates):
            pose = self._poses_by_key.get(c.pose_key)
            desc = pose.description if pose and pose.description else c.name_en
            lines.append(
                f"{i + 1}. {c.name_en} ({c.name_cn}) [score:{c.score:.2f}] - {desc}"
            )
        candidate_block = "\n".join(lines)

        system_prompt = (
            "/no_think You are a sex position classifier. Your ONLY job is to "
            "rank candidate positions by relevance to the user query. "
            "Output ONLY a JSON array of integers like [1,2,3]. No explanation."
        )
        user_prompt = (
            f'Query: "{prompt}"\n\n'
            f"Candidates (sorted by current score, higher = better pre-match):\n"
            f"{candidate_block}\n\n"
            "Rules:\n"
            "- Focus on the PRIMARY sexual act in the query.\n"
            "- Candidate with score >= 0.8 is almost certainly correct, keep it at rank 1.\n"
            "- Re-rank ALL candidates by how well they match the query.\n"
            "- Output ONLY a JSON array of integers representing your ranked order. "
            "Do NOT output [1,2,3] by default — actually reason about the best match."
        )

        text = await self._inference.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=100,
            temperature=0.1,
        )

        match = re.search(r"\[[\d,\s]+\]", text)
        if not match:
            logger.warning("LLM rerank: no JSON array in response: %r", text[:200])
            return candidates

        try:
            ranking = json.loads(match.group())
        except json.JSONDecodeError:
            return candidates

        reranked: list[PoseRecommendation] = []
        seen: set[str] = set()
        for idx in ranking:
            if not isinstance(idx, int) or idx < 1 or idx > len(sorted_candidates):
                continue
            cand = sorted_candidates[idx - 1]
            if cand.pose_key in seen:
                continue
            seen.add(cand.pose_key)
            reranked.append(cand)

        # Ensure we don't drop any candidates the LLM forgot
        for cand in sorted_candidates:
            if cand.pose_key not in seen:
                reranked.append(cand)

        # Reassign scores so the new top dominates filtering
        n = max(len(reranked), 1)
        for i, cand in enumerate(reranked):
            new_score = max(0.0, 1.0 - (i * (1.0 / n) * 0.5))
            cand.score = max(cand.score * 0.5, new_score)
        return reranked

    # ------------------------------------------------------------------
    # Tier 2: Synonym + keyword scoring
    # ------------------------------------------------------------------
    def _keyword_pipeline(
        self,
        prompt: str,
        *,
        top_k: int,
        min_score: float,
    ) -> list[PoseRecommendation]:
        """Pure-Python scoring. Used when embedding worker is unreachable."""
        original = prompt.lower().replace("-", " ")
        synonym_matches = dict(find_matching_pose_keys(prompt))

        scored: list[PoseRecommendation] = []
        if self._poses is None:
            return []
        for pose in self._poses:
            phrase_score = 0.0
            if pose.name_en and pose.name_en.lower() in original:
                phrase_score = 1.0
            elif pose.pose_key.replace("_", " ") in original:
                phrase_score = 0.9
            elif pose.pose_key in synonym_matches:
                wc = synonym_matches[pose.pose_key]
                phrase_score = min(0.85 + wc * 0.03, 1.0)

            # Fuzzy fallback (cheap)
            fuzzy = SequenceMatcher(None, original, pose.search_text.lower()).ratio()
            score = max(phrase_score, fuzzy * 0.5)

            if score >= min_score:
                scored.append(PoseRecommendation(
                    pose_key=pose.pose_key,
                    name_cn=pose.name_cn,
                    name_en=pose.name_en,
                    score=float(score),
                    match_reason=self._reason_for(pose, score),
                    category=pose.category or "uncategorized",
                ))

        scored.sort(key=lambda c: c.score, reverse=True)
        return scored[:top_k]

    # ------------------------------------------------------------------
    # Tier 3: Legacy substring fallback
    # ------------------------------------------------------------------
    def _legacy_substring(
        self,
        prompt: str,
        *,
        top_k: int,
        min_score: float,
    ) -> list[PoseRecommendation]:
        """Last-resort: substring match against pose_key/name_en/name_cn only.

        Mirrors the behaviour of the broken
        ``prompt_analysis.py:_recommend_poses_sync`` so callers always get
        *something* even if MySQL is fine but everything else is on fire.
        """
        if not self._poses:
            return []
        prompt_lower = prompt.lower()
        scored: list[tuple[float, PoseMeta]] = []
        for pose in self._poses:
            score = 0.0
            if pose.pose_key and pose.pose_key in prompt_lower:
                score += 10
            if pose.name_en:
                for word in pose.name_en.lower().split():
                    if word and word in prompt_lower:
                        score += 3
            if pose.name_cn and pose.name_cn in prompt_lower:
                score += 5
            if pose.pose_key:
                for token in pose.pose_key.split("_"):
                    if token and token in prompt_lower:
                        score += 2
            if score > 0:
                scored.append((score, pose))

        scored.sort(key=lambda x: x[0], reverse=True)
        # Convert raw score to a 0..1-ish value so callers can apply min_score
        if not scored:
            return []
        max_score = scored[0][0] or 1.0
        out: list[PoseRecommendation] = []
        for raw_score, pose in scored[:top_k]:
            normalized = raw_score / max_score
            if normalized < min_score:
                continue
            out.append(PoseRecommendation(
                pose_key=pose.pose_key,
                name_cn=pose.name_cn,
                name_en=pose.name_en,
                score=float(normalized),
                match_reason=self._reason_for(pose, normalized),
                category=pose.category or "uncategorized",
            ))
        return out

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _reason_for(pose: PoseMeta, score: float) -> str:
        if score >= 0.7:
            return f"strong match: {pose.name_cn or pose.name_en}"
        if score >= 0.4:
            return f"match: {pose.name_cn or pose.name_en}"
        return f"weak match: {pose.name_cn or pose.name_en}"


# ---------------------------------------------------------------------------
# Process-wide singleton
# ---------------------------------------------------------------------------

_recommender: Optional[PoseRecommender] = None


def get_pose_recommender(
    config: GatewayConfig,
    redis,
    *,
    inference: Optional[InferenceClient] = None,
    embedding_model: str = "bge-large-zh-v1.5",
) -> PoseRecommender:
    """Return the process-wide PoseRecommender, creating it if needed."""
    global _recommender
    if _recommender is None:
        _recommender = PoseRecommender(
            config=config,
            redis=redis,
            inference=inference,
            embedding_model=embedding_model,
        )
    return _recommender


def reset_pose_recommender() -> None:
    """Drop the singleton (used by tests)."""
    global _recommender
    _recommender = None
