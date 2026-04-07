"""Three-stage pose recommender (synonym expansion + embedding + LLM rerank).

Lives in the gateway because it's pure business logic — it talks to GPU
services only via the Redis-backed :class:`InferenceClient`. Designed with
a 4-tier fallback chain so it never blocks workflow execution:

    Tier 0:  synonym expand + BGE embedding match + LLM rerank   (full)
    Tier 1:  synonym expand + BGE embedding match                 (LLM dead)
    Tier 2:  synonym expand + keyword scoring                     (BGE dead)
    Tier 3:  legacy substring matching against MySQL poses table  (zero deps)

Replaces the broken naive matcher in
``api_gateway/services/stages/prompt_analysis.py:_recommend_poses_sync``.
"""

from api_gateway.services.pose_recommender.recommender import (
    PoseRecommendation,
    PoseRecommender,
    get_pose_recommender,
)

__all__ = [
    "PoseRecommendation",
    "PoseRecommender",
    "get_pose_recommender",
]
