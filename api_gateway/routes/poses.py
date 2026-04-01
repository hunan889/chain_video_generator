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
    return result


@router.post("/poses/recommend")
def recommend_poses(
    req: PoseRecommendRequest,
    config: GatewayConfig = Depends(get_config),
):
    """Pose recommendation via simple keyword matching against pose_key/name_en/name_cn."""
    prompt_lower = req.prompt.lower()
    try:
        with get_mysql_connection(config) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM poses WHERE enabled = 1")
                all_poses = cur.fetchall()
    except Exception:
        logger.exception("Failed to query poses for recommendation")
        raise HTTPException(status_code=500, detail="Failed to query poses")

    scored: list[tuple[int, dict]] = []
    for pose in all_poses:
        score = 0
        pose_key = (pose.get("pose_key") or "").lower()
        name_en = (pose.get("name_en") or "").lower()
        name_cn = (pose.get("name_cn") or "").lower()

        # Exact match on pose_key
        if pose_key and pose_key in prompt_lower:
            score += 10
        # Partial match on name_en words
        if name_en:
            for word in name_en.split():
                if word in prompt_lower:
                    score += 3
        # Match on name_cn
        if name_cn and name_cn in prompt_lower:
            score += 5
        # Match pose_key tokens (e.g. "reverse_cowgirl" -> "reverse", "cowgirl")
        if pose_key:
            for token in pose_key.split("_"):
                if token in prompt_lower:
                    score += 2

        if score > 0:
            scored.append((score, pose))

    # Sort by score descending, take top_k
    scored.sort(key=lambda x: x[0], reverse=True)
    top_poses = [_serialize_row(p) for _, p in scored[: req.top_k]]

    return {"poses": top_poses}


@router.post("/poses/match")
def match_poses(
    req: PoseRecommendRequest,
    config: GatewayConfig = Depends(get_config),
):
    """Alias for recommend — same logic."""
    return recommend_poses(req, config)


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
        return {"configs": []}

    configs: list[dict] = []
    for pose_id in pose_ids:
        try:
            result = _fetch_pose_config(config, pose_id)
            if result is not None:
                configs.append(result)
        except Exception:
            logger.warning("Failed to fetch config for pose_id=%s, skipping", pose_id)

    return {"configs": configs}
