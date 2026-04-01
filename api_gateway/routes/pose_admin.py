"""Pose admin endpoints — CRUD for poses, reference images, loras, auto-associate.

All data operations go directly to MySQL (tudou_soga database).
"""

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from api_gateway.config import GatewayConfig
from api_gateway.dependencies import get_config, get_mysql_connection
from api_gateway.routes.poses import _fetch_pose_config, _serialize_row

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["pose-admin"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class CreatePoseRequest(BaseModel):
    pose_key: str
    name_en: str
    name_cn: str = ""
    description: str = ""
    difficulty: str = ""
    category: str = ""
    enabled: bool = True


class UpdatePoseRequest(BaseModel):
    pose_key: Optional[str] = None
    name_en: Optional[str] = None
    name_cn: Optional[str] = None
    description: Optional[str] = None
    difficulty: Optional[str] = None
    category: Optional[str] = None
    enabled: Optional[bool] = None


class AddReferenceImageRequest(BaseModel):
    pose_id: int
    image_url: str
    angle: str = ""
    style: str = ""
    prompt: str = ""
    model: str = ""
    is_default: bool = False
    quality_score: float = 0.0


class AddLoraRequest(BaseModel):
    pose_id: int
    lora_id: Optional[int] = None
    lora_name: str = ""
    lora_type: str = ""
    noise_stage: str = ""
    trigger_words: str = ""
    trigger_prompt: str = ""
    recommended_weight: float = 1.0
    is_default: bool = False
    sort_order: int = 0


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/admin/poses")
def admin_list_poses(
    category: Optional[str] = None,
    config: GatewayConfig = Depends(get_config),
):
    """List all poses (always includes disabled) for admin use."""
    try:
        with get_mysql_connection(config) as conn:
            with conn.cursor() as cur:
                conditions: list[str] = []
                params: list[Any] = []

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
        logger.exception("Failed to list poses (admin)")
        raise HTTPException(status_code=500, detail="Failed to list poses")


@router.post("/admin/poses")
def create_pose(
    req: CreatePoseRequest,
    config: GatewayConfig = Depends(get_config),
):
    """Create a new pose."""
    try:
        with get_mysql_connection(config) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO poses (pose_key, name_en, name_cn, description, difficulty, category, enabled)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        req.pose_key,
                        req.name_en,
                        req.name_cn,
                        req.description,
                        req.difficulty,
                        req.category,
                        1 if req.enabled else 0,
                    ),
                )
                conn.commit()
                pose_id = cur.lastrowid

                cur.execute("SELECT * FROM poses WHERE id = %s", (pose_id,))
                pose = cur.fetchone()

        return {"pose": _serialize_row(pose)}
    except Exception:
        logger.exception("Failed to create pose")
        raise HTTPException(status_code=500, detail="Failed to create pose")


@router.put("/admin/poses/{pose_id}")
def update_pose(
    pose_id: int,
    req: UpdatePoseRequest,
    config: GatewayConfig = Depends(get_config),
):
    """Update an existing pose."""
    updates: list[str] = []
    params: list[Any] = []

    if req.pose_key is not None:
        updates.append("pose_key = %s")
        params.append(req.pose_key)
    if req.name_en is not None:
        updates.append("name_en = %s")
        params.append(req.name_en)
    if req.name_cn is not None:
        updates.append("name_cn = %s")
        params.append(req.name_cn)
    if req.description is not None:
        updates.append("description = %s")
        params.append(req.description)
    if req.difficulty is not None:
        updates.append("difficulty = %s")
        params.append(req.difficulty)
    if req.category is not None:
        updates.append("category = %s")
        params.append(req.category)
    if req.enabled is not None:
        updates.append("enabled = %s")
        params.append(1 if req.enabled else 0)

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    params.append(pose_id)

    try:
        with get_mysql_connection(config) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE poses SET {', '.join(updates)} WHERE id = %s",
                    params,
                )
                if cur.rowcount == 0:
                    raise HTTPException(status_code=404, detail=f"Pose {pose_id} not found")
                conn.commit()

                cur.execute("SELECT * FROM poses WHERE id = %s", (pose_id,))
                pose = cur.fetchone()

        return {"pose": _serialize_row(pose)}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to update pose %s", pose_id)
        raise HTTPException(status_code=500, detail="Failed to update pose")


@router.delete("/admin/poses/{pose_id}")
def delete_pose(
    pose_id: int,
    config: GatewayConfig = Depends(get_config),
):
    """Delete a pose and cascade (reference_images, loras, prompt_templates)."""
    try:
        with get_mysql_connection(config) as conn:
            with conn.cursor() as cur:
                # Check existence
                cur.execute("SELECT id FROM poses WHERE id = %s", (pose_id,))
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail=f"Pose {pose_id} not found")

                # Cascade delete related records
                cur.execute("DELETE FROM pose_reference_images WHERE pose_id = %s", (pose_id,))
                cur.execute("DELETE FROM pose_loras WHERE pose_id = %s", (pose_id,))
                cur.execute("DELETE FROM pose_prompt_templates WHERE pose_id = %s", (pose_id,))
                cur.execute("DELETE FROM poses WHERE id = %s", (pose_id,))
                conn.commit()

        return {"deleted": True, "pose_id": pose_id}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to delete pose %s", pose_id)
        raise HTTPException(status_code=500, detail="Failed to delete pose")


@router.post("/admin/poses/reference-images")
def add_reference_image(
    req: AddReferenceImageRequest,
    config: GatewayConfig = Depends(get_config),
):
    """Add a reference image to a pose."""
    try:
        with get_mysql_connection(config) as conn:
            with conn.cursor() as cur:
                # Verify pose exists
                cur.execute("SELECT id FROM poses WHERE id = %s", (req.pose_id,))
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail=f"Pose {req.pose_id} not found")

                cur.execute(
                    """
                    INSERT INTO pose_reference_images
                        (pose_id, image_url, angle, style, prompt, model, is_default, quality_score)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        req.pose_id,
                        req.image_url,
                        req.angle,
                        req.style,
                        req.prompt,
                        req.model,
                        1 if req.is_default else 0,
                        req.quality_score,
                    ),
                )
                conn.commit()
                image_id = cur.lastrowid

                cur.execute("SELECT * FROM pose_reference_images WHERE id = %s", (image_id,))
                image = cur.fetchone()

        return {"reference_image": _serialize_row(image)}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to add reference image")
        raise HTTPException(status_code=500, detail="Failed to add reference image")


@router.delete("/admin/poses/reference-images/{image_id}")
def delete_reference_image(
    image_id: int,
    config: GatewayConfig = Depends(get_config),
):
    """Remove a reference image."""
    try:
        with get_mysql_connection(config) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM pose_reference_images WHERE id = %s", (image_id,))
                if cur.rowcount == 0:
                    raise HTTPException(status_code=404, detail=f"Reference image {image_id} not found")
                conn.commit()

        return {"deleted": True, "image_id": image_id}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to delete reference image %s", image_id)
        raise HTTPException(status_code=500, detail="Failed to delete reference image")


@router.post("/admin/poses/loras")
def add_lora(
    req: AddLoraRequest,
    config: GatewayConfig = Depends(get_config),
):
    """Add a lora association to a pose."""
    try:
        with get_mysql_connection(config) as conn:
            with conn.cursor() as cur:
                # Verify pose exists
                cur.execute("SELECT id FROM poses WHERE id = %s", (req.pose_id,))
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail=f"Pose {req.pose_id} not found")

                cur.execute(
                    """
                    INSERT INTO pose_loras
                        (pose_id, lora_id, lora_name, lora_type, noise_stage,
                         trigger_words, trigger_prompt, recommended_weight, is_default, sort_order)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        req.pose_id,
                        req.lora_id,
                        req.lora_name,
                        req.lora_type,
                        req.noise_stage,
                        req.trigger_words,
                        req.trigger_prompt,
                        req.recommended_weight,
                        1 if req.is_default else 0,
                        req.sort_order,
                    ),
                )
                conn.commit()
                lora_row_id = cur.lastrowid

                cur.execute("SELECT * FROM pose_loras WHERE id = %s", (lora_row_id,))
                lora = cur.fetchone()

        return {"lora": _serialize_row(lora)}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to add lora to pose")
        raise HTTPException(status_code=500, detail="Failed to add lora")


@router.delete("/admin/poses/loras/{lora_id}")
def delete_lora(
    lora_id: int,
    config: GatewayConfig = Depends(get_config),
):
    """Remove a lora association from a pose."""
    try:
        with get_mysql_connection(config) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM pose_loras WHERE id = %s", (lora_id,))
                if cur.rowcount == 0:
                    raise HTTPException(status_code=404, detail=f"Pose lora {lora_id} not found")
                conn.commit()

        return {"deleted": True, "lora_id": lora_id}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to delete lora %s", lora_id)
        raise HTTPException(status_code=500, detail="Failed to delete lora")


@router.patch("/admin/poses/loras/{lora_id}")
async def update_lora(
    lora_id: int,
    request: Request,
    config: GatewayConfig = Depends(get_config),
):
    """Update a pose lora association (weight, noise_stage, etc.)."""
    body = await request.json()

    try:
        updates = []
        params = []
        for field in ("recommended_weight", "noise_stage", "lora_type", "is_default", "sort_order"):
            if field in body:
                updates.append(f"{field} = %s")
                params.append(body[field])

        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        params.append(lora_id)
        with get_mysql_connection(config) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE pose_loras SET {', '.join(updates)} WHERE id = %s",
                    params,
                )
                if cur.rowcount == 0:
                    raise HTTPException(status_code=404, detail=f"Pose lora {lora_id} not found")
                conn.commit()

        return {"updated": True, "lora_id": lora_id}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to update lora %s", lora_id)
        raise HTTPException(status_code=500, detail="Failed to update lora")


@router.post("/admin/poses/{pose_id}/auto-associate")
def auto_associate_loras(
    pose_id: int,
    config: GatewayConfig = Depends(get_config),
):
    """Auto-associate loras: find loras in lora_metadata whose name contains pose_key."""
    try:
        with get_mysql_connection(config) as conn:
            with conn.cursor() as cur:
                # Get the pose
                cur.execute("SELECT id, pose_key FROM poses WHERE id = %s", (pose_id,))
                pose = cur.fetchone()
                if not pose:
                    raise HTTPException(status_code=404, detail=f"Pose {pose_id} not found")

                pose_key = pose["pose_key"]

                # Find matching loras in lora_metadata
                cur.execute(
                    "SELECT * FROM lora_metadata WHERE name LIKE %s",
                    (f"%{pose_key}%",),
                )
                matching_loras = cur.fetchall()

                associated_count = 0
                for lm in matching_loras:
                    # Check if already associated
                    cur.execute(
                        "SELECT id FROM pose_loras WHERE pose_id = %s AND lora_name = %s",
                        (pose_id, lm["name"]),
                    )
                    if cur.fetchone():
                        continue

                    cur.execute(
                        """
                        INSERT INTO pose_loras
                            (pose_id, lora_id, lora_name, lora_type, noise_stage,
                             trigger_words, trigger_prompt, recommended_weight, is_default, sort_order)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            pose_id,
                            lm.get("id"),
                            lm.get("name", ""),
                            "",
                            "",
                            lm.get("trigger_words", "") or "",
                            lm.get("trigger_prompt", "") or "",
                            1.0,
                            0,
                            0,
                        ),
                    )
                    associated_count += 1

                conn.commit()

        return {
            "pose_id": pose_id,
            "pose_key": pose_key,
            "matched_loras": len(matching_loras),
            "newly_associated": associated_count,
        }
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to auto-associate loras for pose %s", pose_id)
        raise HTTPException(status_code=500, detail="Failed to auto-associate loras")
