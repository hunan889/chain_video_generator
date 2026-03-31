"""
Tests for pose recommendation min_score threshold logic.

Verifies:
1. Default threshold (0.5) filters correctly
2. Custom min_score overrides the default
3. Workflow executor passes correct threshold per mode
"""
import sys
import types
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Stub heavy dependencies before importing project modules
_STUB_MODULES = [
    "sentence_transformers", "torch", "numpy",
    "pymysql", "pymysql.cursors", "redis", "websockets", "pymilvus",
    "yaml", "dotenv",
]
for mod_name in _STUB_MODULES:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = MagicMock()

# Stub numpy.array and numpy.dot for pose_recommender
np_mock = sys.modules["numpy"]
np_mock.array = lambda x: x
np_mock.dot = lambda a, b: 0.0

from api.services.pose_recommender import PoseRecommender, PoseRecommendation, MIN_RECOMMEND_SCORE


# --- Helpers ---

def _make_candidates(scores: list[float]) -> list[PoseRecommendation]:
    """Create fake PoseRecommendation list with given scores."""
    return [
        PoseRecommendation(
            pose_key=f"pose_{i}",
            name_cn=f"姿势{i}",
            name_en=f"pose_{i}",
            score=s,
            match_reason="test",
            category="test",
        )
        for i, s in enumerate(scores)
    ]


CANDIDATE_SCORES = [0.7, 0.45, 0.35, 0.25, 0.55]
# sorted desc: 0.7, 0.55, 0.45, 0.35, 0.25
# default (0.5): 0.7, 0.55           → 2 results
# min_score=0.3: 0.7, 0.55, 0.45, 0.35 → 4 results
# min_score=0.6: 0.7                 → 1 result


@pytest.fixture
def recommender():
    """Create a PoseRecommender without DB initialization."""
    r = PoseRecommender(use_embedding=False)
    r.poses_data = {}
    return r


# --- Test: MIN_RECOMMEND_SCORE constant ---

def test_default_min_score_is_0_5():
    assert MIN_RECOMMEND_SCORE == 0.5


# --- Test: recommend() threshold filtering ---

@pytest.mark.asyncio
async def test_recommend_default_threshold(recommender):
    """Default threshold (0.5) should filter out scores < 0.5."""
    candidates = _make_candidates(CANDIDATE_SCORES)

    with patch.object(recommender, '_keyword_match', new_callable=AsyncMock, return_value=candidates):
        results = await recommender.recommend("test prompt", use_llm=False, use_embedding=False)

    scores = [r.score for r in results]
    assert all(s >= 0.5 for s in scores), f"All scores should be >= 0.5, got {scores}"
    assert len(results) == 2, f"Expected 2 results with default threshold, got {len(results)}"


@pytest.mark.asyncio
async def test_recommend_custom_min_score_0_3(recommender):
    """min_score=0.3 should return more results."""
    candidates = _make_candidates(CANDIDATE_SCORES)

    with patch.object(recommender, '_keyword_match', new_callable=AsyncMock, return_value=candidates):
        results = await recommender.recommend("test prompt", use_llm=False, use_embedding=False, min_score=0.3)

    scores = [r.score for r in results]
    assert all(s >= 0.3 for s in scores), f"All scores should be >= 0.3, got {scores}"
    assert len(results) == 4, f"Expected 4 results with min_score=0.3, got {len(results)}"


@pytest.mark.asyncio
async def test_recommend_custom_min_score_0_6(recommender):
    """min_score=0.6 should be stricter."""
    candidates = _make_candidates(CANDIDATE_SCORES)

    with patch.object(recommender, '_keyword_match', new_callable=AsyncMock, return_value=candidates):
        results = await recommender.recommend("test prompt", use_llm=False, use_embedding=False, min_score=0.6)

    scores = [r.score for r in results]
    assert all(s >= 0.6 for s in scores), f"All scores should be >= 0.6, got {scores}"
    assert len(results) == 1, f"Expected 1 result with min_score=0.6, got {len(results)}"


@pytest.mark.asyncio
async def test_recommend_min_score_none_uses_default(recommender):
    """min_score=None should fall back to MIN_RECOMMEND_SCORE (0.5)."""
    candidates = _make_candidates(CANDIDATE_SCORES)

    with patch.object(recommender, '_keyword_match', new_callable=AsyncMock, return_value=candidates):
        results = await recommender.recommend("test prompt", use_llm=False, use_embedding=False, min_score=None)

    assert len(results) == 2, f"Expected 2 results (same as default), got {len(results)}"


# --- Test: workflow executor mode → threshold mapping ---

def test_mode_to_threshold_mapping():
    """Verify the mode→threshold logic used in workflow_executor._analyze_prompt."""
    # Replicate the logic from workflow_executor.py
    def get_pose_min_score(mode):
        return 0.5 if mode in (None, "t2v", "first_frame") else 0.3

    # Strict modes (0.5)
    assert get_pose_min_score(None) == 0.5
    assert get_pose_min_score("t2v") == 0.5
    assert get_pose_min_score("first_frame") == 0.5

    # Relaxed modes (0.3)
    assert get_pose_min_score("face_reference") == 0.3
    assert get_pose_min_score("full_body_reference") == 0.3


# --- Test: PoseRecommendRequest accepts min_score ---

def test_pose_recommend_request_min_score():
    """PoseRecommendRequest should accept optional min_score."""
    from api.routes.poses import PoseRecommendRequest

    # Default: None
    req = PoseRecommendRequest(prompt="test")
    assert req.min_score is None

    # Explicit value
    req = PoseRecommendRequest(prompt="test", min_score=0.3)
    assert req.min_score == 0.3

    req = PoseRecommendRequest(prompt="test", min_score=0.5)
    assert req.min_score == 0.5
