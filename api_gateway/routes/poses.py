"""Pose endpoints — list, config, recommend, match, batch-config.

All data is read directly from MySQL (tudou_soga database).
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from api_gateway.config import GatewayConfig
from api_gateway.dependencies import get_config, get_mysql_connection

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["poses"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class PoseRecommendRequest(BaseModel):
    prompt: str
    top_k: int = 5
    # The admin "推荐测试" page sends these knobs; the underlying
    # PoseRecommender already supports use_llm, the others are accepted
    # for forward-compat / debug parity even if currently unused.
    selected_poses: Optional[list[str]] = None
    use_embedding: bool = True
    use_llm: bool = True
    min_score: Optional[float] = None


class BatchConfigRequest(BaseModel):
    pose_ids: list[int]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fetch_pose_config(config: GatewayConfig, pose_id: int) -> dict | None:
    """Fetch a full pose config: pose + reference_images + loras + prompt_templates."""
    try:
        with get_mysql_connection(config) as conn:
            with conn.cursor() as cur:
                # Pose
                cur.execute("SELECT * FROM poses WHERE id = %s", (pose_id,))
                pose = cur.fetchone()
                if not pose:
                    return None

                # Reference images
                cur.execute(
                    "SELECT * FROM pose_reference_images WHERE pose_id = %s ORDER BY is_default DESC, quality_score DESC",
                    (pose_id,),
                )
                reference_images = cur.fetchall()

                # Loras — join with lora_metadata for preview_url and civitai_id
                cur.execute(
                    """
                    SELECT pl.*, lm.preview_url, lm.civitai_id
                    FROM pose_loras pl
                    LEFT JOIN lora_metadata lm ON pl.lora_name = lm.name COLLATE utf8mb4_unicode_ci
                    WHERE pl.pose_id = %s
                    ORDER BY pl.sort_order ASC, pl.is_default DESC
                    """,
                    (pose_id,),
                )
                loras = cur.fetchall()

                # Prompt templates
                cur.execute(
                    "SELECT * FROM pose_prompt_templates WHERE pose_id = %s ORDER BY priority DESC",
                    (pose_id,),
                )
                prompt_templates = cur.fetchall()

        return {
            **_serialize_row(pose),
            "reference_images": [_serialize_row(r) for r in reference_images],
            "loras": [_serialize_row(l) for l in loras],
            "prompt_templates": [_serialize_row(t) for t in prompt_templates],
        }
    except Exception:
        logger.exception("Failed to fetch pose config for pose_id=%s", pose_id)
        raise


def _serialize_row(row: dict) -> dict:
    """Convert non-JSON-native types for serialization (datetime, Decimal, etc.)."""
    from datetime import date, datetime
    from decimal import Decimal

    result = {}
    for key, value in row.items():
        if isinstance(value, datetime):
            result[key] = value.isoformat()
        elif isinstance(value, date):
            result[key] = value.isoformat()
        elif isinstance(value, Decimal):
            result[key] = float(value)
        else:
            result[key] = value
    return result


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/poses")
def list_poses(
    include_disabled: bool = False,
    category: Optional[str] = None,
    config: GatewayConfig = Depends(get_config),
):
    """List all poses with reference_image_count and lora_count."""
    try:
        with get_mysql_connection(config) as conn:
            with conn.cursor() as cur:
                conditions = []
                params: list = []

                if not include_disabled:
                    conditions.append("p.enabled = 1")
                if category:
                    conditions.append("p.category = %s")
                    params.append(category)

                where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

                query = f"""
                    SELECT p.*,
                           COALESCE(ri.cnt, 0) AS reference_image_count,
                           COALESCE(pl.cnt, 0) AS lora_count
                    FROM poses p
                    LEFT JOIN (
                        SELECT pose_id, COUNT(*) AS cnt
                        FROM pose_reference_images
                        GROUP BY pose_id
                    ) ri ON p.id = ri.pose_id
                    LEFT JOIN (
                        SELECT pose_id, COUNT(*) AS cnt
                        FROM pose_loras
                        GROUP BY pose_id
                    ) pl ON p.id = pl.pose_id
                    {where_clause}
                    ORDER BY p.id ASC
                """
                cur.execute(query, params)
                poses = cur.fetchall()

        return {"poses": [_serialize_row(p) for p in poses]}
    except Exception:
        logger.exception("Failed to list poses")
        raise HTTPException(status_code=500, detail="Failed to list poses")


@router.get("/poses/{pose_id}/config")
def get_pose_config(
    pose_id: int,
    config: GatewayConfig = Depends(get_config),
):
    """Get full pose config: pose + reference_images + loras + prompt_templates."""
    try:
        result = _fetch_pose_config(config, pose_id)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch pose config")

    if result is None:
        raise HTTPException(status_code=404, detail=f"Pose {pose_id} not found")
    # Split loras into image_loras and video_loras
    all_loras = result.pop("loras", [])
    result["image_loras"] = [l for l in all_loras if l.get("lora_type") == "image"]
    result["video_loras"] = [l for l in all_loras if l.get("lora_type") != "image"]
    return result


@router.post("/poses/recommend")
async def recommend_poses(
    req: PoseRecommendRequest,
    request: Request,
    config: GatewayConfig = Depends(get_config),
):
    """Pose recommendation using the full 3-tier recommender.

    The pipeline is:
      1. Synonym expansion (rule-based, instant)
      2. BGE embedding cosine top-k via the inference worker (Tier 1)
      3. Optional LLM rerank for ambiguous results (Tier 1, when use_llm)
      4. Keyword fallback if embeddings unavailable (Tier 2)
      5. Legacy substring fallback as last resort (Tier 3)

    The admin "推荐测试" page reads ``data.recommendations`` so the
    response is shaped accordingly. Each item contains the fields the
    PoseRecommender's ``PoseRecommendation`` dataclass produces:
    ``pose_key``, ``name_cn``, ``name_en``, ``score`` (float 0-1),
    ``match_reason``, ``category``.
    """
    if not req.prompt or not req.prompt.strip():
        return {"recommendations": []}

    redis_conn = getattr(request.app.state, "redis", None)
    if redis_conn is None:
        logger.error("recommend_poses: Redis not available on app.state")
        raise HTTPException(status_code=503, detail="Recommender not initialised")

    try:
        from api_gateway.services.pose_recommender import get_pose_recommender
        recommender = get_pose_recommender(config, redis_conn)
        # Use a slightly looser threshold for the admin test page so
        # users can see fuzzy matches and tune from there. The
        # workflow_engine path uses 0.3-0.5 depending on mode.
        min_score = req.min_score if req.min_score is not None else 0.3
        results = await recommender.recommend(
            req.prompt,
            top_k=req.top_k,
            min_score=min_score,
            use_llm=req.use_llm,
        )
    except Exception:
        logger.exception("Pose recommender failed for prompt=%r", req.prompt[:120])
        raise HTTPException(status_code=500, detail="Pose recommender failed")

    return {"recommendations": [r.to_dict() for r in results]}


@router.post("/poses/match")
async def match_poses(
    req: PoseRecommendRequest,
    request: Request,
    config: GatewayConfig = Depends(get_config),
):
    """Alias for recommend — same logic."""
    return await recommend_poses(req, request, config)


@router.post("/poses/batch-config")
async def batch_config(
    request: Request,
    config: GatewayConfig = Depends(get_config),
):
    """Get configs for multiple poses.

    Accepts either ``{"pose_ids": [1,2,3]}`` or plain ``[1,2,3]``.
    """
    body = await request.json()
    if isinstance(body, list):
        pose_ids = body
    elif isinstance(body, dict):
        pose_ids = body.get("pose_ids", [])
    else:
        pose_ids = []
    if not pose_ids:
        return {}

    results = {}
    for pose_id in pose_ids:
        try:
            result = _fetch_pose_config(config, pose_id)
            if result is not None:
                # Split loras into image_loras and video_loras (frontend expects this)
                all_loras = result.pop("loras", [])
                result["image_loras"] = [l for l in all_loras if l.get("lora_type") == "image"]
                result["video_loras"] = [l for l in all_loras if l.get("lora_type") != "image"]
                results[str(pose_id)] = result
        except Exception:
            logger.warning("Failed to fetch config for pose_id=%s, skipping", pose_id)

    return results
