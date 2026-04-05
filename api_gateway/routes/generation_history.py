"""Unified generation history endpoint — MySQL-backed with Redis merge.

Replaces the old workflow_history.py which scanned Redis keys.
Uses TaskStore for MySQL persistence and merges real-time progress
from Redis for running/queued tasks.
Also provides regenerate and status detail endpoints.
"""

import json
import logging
import math
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from api_gateway.dependencies import get_cos_client, get_gateway
from shared.cos.client import COSClient
from shared.enums import GenerateMode, ModelType, category_for_mode
from shared.task_gateway import TaskGateway

logger = logging.getLogger(__name__)

router = APIRouter(tags=["generation-history"])


def _get_task_store(request: Request):
    """Return the TaskStore singleton from app state."""
    return request.app.state.task_store


async def _list_history(
    request: Request,
    page: int = 1,
    page_size: int = 24,
    status: Optional[str] = None,
    category: Optional[str] = None,
    q: Optional[str] = None,
    gw: TaskGateway = Depends(get_gateway),
):
    """Shared handler for all three route aliases.

    1. Query MySQL via TaskStore.list_history for paginated results.
    2. For running/queued tasks, merge real-time progress from Redis.
    3. Lazy sync: if Redis shows terminal status but MySQL doesn't, update MySQL.
    """
    page_size = min(page_size, 100)
    page = max(page, 1)

    task_store = _get_task_store(request)

    # Step 1: Query MySQL
    result = await task_store.list_history(
        page=page,
        page_size=page_size,
        status=status,
        category=category,
        q=q,
    )

    workflows = result.get("tasks", [])
    total = result.get("total", 0)
    category_counts = result.get("category_counts", {})

    # Step 2: For running/queued tasks, merge real-time progress from Redis
    redis = gw.redis
    lazy_sync_updates: list[dict] = []

    for item in workflows:
        task_id = item.get("workflow_id", "")
        mysql_status = item.get("status", "")

        if mysql_status in ("running", "queued"):
            # Fetch real-time data from Redis
            try:
                redis_data = await gw.get_task(task_id)

                # Workflow tasks (wf_*) are stored under workflow:{id}, not task:{id}
                if not redis_data and task_id.startswith("wf_"):
                    wf_raw = await redis.hgetall(f"workflow:{task_id}")
                    if wf_raw:
                        redis_data = {
                            "status": wf_raw.get("status", ""),
                            "video_url": wf_raw.get("final_video_url") or wf_raw.get("video_url"),
                            "error": wf_raw.get("error"),
                            "first_frame_url": wf_raw.get("first_frame_url"),
                            "edited_frame_url": wf_raw.get("edited_frame_url"),
                        }

                if redis_data:
                    redis_status = redis_data.get("status", "")
                    # Merge real-time fields
                    if redis_status:
                        item["status"] = redis_status
                    progress = redis_data.get("progress")
                    if progress is not None:
                        item["progress"] = progress

                    # Update video URL if completed in Redis
                    video_url = redis_data.get("video_url")
                    if video_url:
                        item["final_video_url"] = video_url

                    # Merge frame URLs from workflow Redis data
                    if redis_data.get("first_frame_url"):
                        item["first_frame_url"] = redis_data["first_frame_url"]
                    if redis_data.get("edited_frame_url"):
                        item["edited_frame_url"] = redis_data["edited_frame_url"]

                    error = redis_data.get("error")
                    if error:
                        item["error"] = error

                    # Lazy sync: if Redis has terminal status but MySQL doesn't
                    if redis_status in ("completed", "failed") and mysql_status not in ("completed", "failed"):
                        lazy_sync_updates.append({
                            "task_id": task_id,
                            "status": redis_status,
                            "video_url": video_url,
                            "error": error,
                        })
            except Exception:
                logger.debug("Failed to fetch Redis data for task %s", task_id, exc_info=True)

    # Step 3: Lazy sync — fire-and-forget updates to MySQL
    for update in lazy_sync_updates:
        try:
            await task_store.update_status(
                update["task_id"],
                update["status"],
                error=update.get("error"),
            )
            if update.get("video_url"):
                await task_store.set_result(
                    update["task_id"],
                    result_url=update["video_url"],
                )
            logger.info(
                "Lazy-synced task %s to %s in MySQL",
                update["task_id"],
                update["status"],
            )
        except Exception:
            logger.warning(
                "Failed to lazy-sync task %s to MySQL",
                update["task_id"],
                exc_info=True,
            )

    total_pages = max(1, math.ceil(total / page_size))

    return {
        "workflows": workflows,
        "total": total,
        "total_pages": total_pages,
        "page": page,
        "page_size": page_size,
        "category_counts": category_counts,
    }


# --- Route 1: Canonical path ---
@router.get("/api/v1/tasks")
async def list_tasks_history(
    request: Request,
    page: int = 1,
    page_size: int = 24,
    status: Optional[str] = None,
    category: Optional[str] = None,
    q: Optional[str] = None,
    gw: TaskGateway = Depends(get_gateway),
):
    """List generation history (canonical endpoint)."""
    return await _list_history(
        request=request,
        page=page,
        page_size=page_size,
        status=status,
        category=category,
        q=q,
        gw=gw,
    )


# --- Route 2: Alias for frontend ---
@router.get("/api/v1/generation/history")
async def generation_history_alias(
    request: Request,
    page: int = 1,
    page_size: int = 24,
    status: Optional[str] = None,
    category: Optional[str] = None,
    q: Optional[str] = None,
    gw: TaskGateway = Depends(get_gateway),
):
    """List generation history (alias)."""
    return await _list_history(
        request=request,
        page=page,
        page_size=page_size,
        status=status,
        category=category,
        q=q,
        gw=gw,
    )


# --- Route 3: Backward compat alias ---
@router.get("/api/v1/workflow/history")
async def workflow_history_compat(
    request: Request,
    page: int = 1,
    page_size: int = 24,
    status: Optional[str] = None,
    category: Optional[str] = None,
    q: Optional[str] = None,
    gw: TaskGateway = Depends(get_gateway),
):
    """List generation history (backward compat alias)."""
    return await _list_history(
        request=request,
        page=page,
        page_size=page_size,
        status=status,
        category=category,
        q=q,
        gw=gw,
    )


# --- Regenerate endpoint ---
@router.post("/api/v1/workflow/{workflow_id}/regenerate")
async def regenerate_task(
    workflow_id: str,
    request: Request,
    gw: TaskGateway = Depends(get_gateway),
):
    """Re-submit a failed/completed task with the same parameters."""
    task_store = _get_task_store(request)

    # 1. Get original task from MySQL
    original = await task_store.get(workflow_id)
    if not original:
        # Try Redis (for wf_ tasks)
        redis_data = await gw.redis.hgetall(f"workflow:{workflow_id}")
        if not redis_data:
            raise HTTPException(404, f"Task {workflow_id} not found")
        original = {
            "task_type": redis_data.get("mode", "t2v"),
            "prompt": redis_data.get("user_prompt", ""),
            "model": redis_data.get("model"),
            "params": None,
        }

    # 2. Determine mode and model
    task_type = original.get("task_type", original.get("mode", "t2v"))
    prompt = original.get("prompt") or original.get("user_prompt", "")

    try:
        mode = GenerateMode(task_type)
    except ValueError:
        mode = GenerateMode.T2V

    model_str = original.get("model", "a14b")
    try:
        model = ModelType(model_str) if model_str else ModelType.A14B
    except ValueError:
        model = ModelType.A14B

    # 3. Build params
    params = original.get("params") or {}
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except (json.JSONDecodeError, TypeError):
            params = {}
    params.setdefault("prompt", prompt)
    params.setdefault("width", 832)
    params.setdefault("height", 480)
    params.setdefault("num_frames", 81)
    params.setdefault("fps", 16)
    params.setdefault("steps", 20)
    params.setdefault("cfg", 6.0)
    params.setdefault("shift", 8.0)
    params.setdefault("seed", -1)

    # 4. Build workflow
    try:
        from shared.workflow_builder import build_workflow
        workflow = build_workflow(
            mode=mode, model=model,
            prompt=params["prompt"],
            negative_prompt=params.get("negative_prompt", ""),
            width=params["width"], height=params["height"],
            num_frames=params["num_frames"], fps=params["fps"],
            steps=params["steps"], cfg=params["cfg"],
            shift=params["shift"],
            seed=params["seed"] if params["seed"] != -1 else None,
        )
    except Exception:
        workflow = params

    # 5. Create new task
    task_id = await gw.create_task(mode=mode, model=model, workflow=workflow, params=params)
    return {"workflow_id": task_id, "status": "queued", "regenerated_from": workflow_id}


# --- Status detail endpoint ---

# Stage names used in the old monolith workflow system
_STAGE_NAMES = ["prompt_analysis", "first_frame_acquisition", "seedream_edit", "video_generation"]


def _build_stages_from_redis(raw: dict) -> tuple[list, dict]:
    """Extract stage progress and stage_details from Redis workflow hash."""
    stages = []
    stage_details = {}

    for name in _STAGE_NAMES:
        stage_status = raw.get(f"stage_{name}", "")
        if not stage_status:
            continue
        stage_entry = {"name": name, "status": stage_status}
        # Check for stage-level error
        details_raw = raw.get(f"stage_{name}_details", "")
        if details_raw:
            try:
                details = json.loads(details_raw)
                stage_details[name] = details
                if details.get("error"):
                    stage_entry["error"] = details["error"]
            except (json.JSONDecodeError, TypeError):
                pass
        stages.append(stage_entry)

    return stages, stage_details


@router.get("/api/v1/workflow/status/{workflow_id}")
async def task_status_detail(
    workflow_id: str,
    request: Request,
    detail: bool = False,
    gw: TaskGateway = Depends(get_gateway),
):
    """Get detailed status for a task.

    For wf_ workflow tasks, includes stage progress and stage_details
    expected by the frontend detail modal.
    """
    task_store = _get_task_store(request)
    result = await task_store.get(workflow_id)

    # Always read Redis for real-time data + stage details
    redis_raw = await gw.redis.hgetall(f"workflow:{workflow_id}")
    if not redis_raw:
        # Try task:* key for non-workflow tasks
        redis_task = await gw.get_task(workflow_id)
        if redis_task:
            redis_raw = redis_task

    if not result and not redis_raw:
        raise HTTPException(404, f"Task {workflow_id} not found")

    # Start from MySQL data or build from Redis
    if result:
        out = dict(result)
    else:
        out = {
            "workflow_id": workflow_id,
            "status": redis_raw.get("status", "unknown"),
            "mode": redis_raw.get("mode", ""),
            "user_prompt": redis_raw.get("user_prompt", redis_raw.get("prompt", "")),
            "final_video_url": redis_raw.get("final_video_url") or redis_raw.get("video_url"),
            "first_frame_url": redis_raw.get("first_frame_url"),
            "edited_frame_url": redis_raw.get("edited_frame_url"),
            "error": redis_raw.get("error"),
            "created_at": redis_raw.get("created_at"),
            "completed_at": redis_raw.get("completed_at"),
        }

    # Merge real-time fields from Redis
    if redis_raw:
        redis_status = redis_raw.get("status", "")
        if redis_status:
            out["status"] = redis_status
        if redis_raw.get("final_video_url"):
            out["final_video_url"] = redis_raw["final_video_url"]
        elif redis_raw.get("video_url"):
            out["final_video_url"] = redis_raw["video_url"]
        if redis_raw.get("first_frame_url"):
            out["first_frame_url"] = redis_raw["first_frame_url"]
        if redis_raw.get("edited_frame_url"):
            out["edited_frame_url"] = redis_raw["edited_frame_url"]
        if redis_raw.get("reference_image"):
            out["reference_image"] = redis_raw["reference_image"]
        if redis_raw.get("error"):
            out["error"] = redis_raw["error"]
        if redis_raw.get("user_prompt"):
            out["user_prompt"] = redis_raw["user_prompt"]

        # Elapsed time
        try:
            c = float(redis_raw.get("created_at", 0))
            e = float(redis_raw.get("completed_at", 0))
            if c and e:
                out["elapsed_time"] = e - c
        except (ValueError, TypeError):
            pass

        # Stage details (for wf_ workflow tasks)
        stages, stage_details = _build_stages_from_redis(redis_raw)
        if stages:
            out["stages"] = stages
            out["stage_details"] = stage_details

        # Analysis result
        ar_raw = redis_raw.get("analysis_result", "")
        if ar_raw:
            try:
                out["analysis_result"] = json.loads(ar_raw)
            except (json.JSONDecodeError, TypeError):
                pass

    # Ensure created_at is a number for frontend
    if out.get("created_at"):
        try:
            out["created_at"] = float(out["created_at"])
        except (ValueError, TypeError):
            pass
    if out.get("completed_at"):
        try:
            out["completed_at"] = float(out["completed_at"])
        except (ValueError, TypeError):
            pass

    return out
