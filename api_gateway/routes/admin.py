"""Admin endpoints: worker status, GPU status, system settings."""

import json
import time
from typing import Any

from fastapi import APIRouter, Request

from shared.redis_keys import SYSTEM_SETTINGS_KEY, WORKER_HEARTBEAT_PREFIX, queue_key

router = APIRouter(prefix="/api/v1", tags=["admin"])

WORKER_ALIVE_THRESHOLD = 120  # seconds — workers heartbeat every 10-15s; allow slack for multiple workers

DEFAULT_SETTINGS: dict[str, Any] = {
    "prompt_optimize_min_chars": 20,
    "prompt_optimize_non_turbo": True,
    "inject_trigger_prompt": True,
    "inject_trigger_words": True,
}


async def _scan_workers(redis, now: int) -> list[dict]:
    """Scan Redis for worker heartbeat keys and return worker info dicts."""
    workers: list[dict] = []
    cursor = 0
    while True:
        cursor, keys = await redis.scan(
            cursor, match=f"{WORKER_HEARTBEAT_PREFIX}:*", count=100
        )
        for key in keys:
            raw = await redis.hgetall(key)
            if not raw:
                continue
            worker_id = key[len(WORKER_HEARTBEAT_PREFIX) + 1 :]
            last_seen = int(raw.get("last_seen", 0))
            model_keys = json.loads(raw.get("model_keys", "[]"))
            status = raw.get("status", "unknown")
            alive = (now - last_seen) < WORKER_ALIVE_THRESHOLD
            workers.append(
                {
                    "worker_id": worker_id,
                    "last_seen": last_seen,
                    "model_keys": model_keys,
                    "status": status,
                    "alive": alive,
                    "raw": raw,
                }
            )
        if cursor == 0:
            break
    return workers


async def _queue_lengths(redis) -> dict[str, int]:
    """Return queue lengths for known model keys."""
    lengths: dict[str, int] = {}
    for mk in ("a14b", "5b"):
        try:
            lengths[mk] = await redis.llen(queue_key(mk))
        except Exception:
            lengths[mk] = 0
    return lengths


@router.get("/admin/workers")
async def get_workers(request: Request):
    """List all GPU workers and their status from Redis heartbeat keys."""
    redis = request.app.state.gateway.redis
    now = int(time.time())

    workers_raw = await _scan_workers(redis, now)
    # Strip internal 'raw' field for the public response
    workers = [{k: v for k, v in w.items() if k != "raw"} for w in workers_raw]

    return {"workers": workers, "queue_lengths": await _queue_lengths(redis)}


@router.get("/admin/gpu-status")
async def get_gpu_status(request: Request):
    """Build GPU status in the format expected by the frontend renderGpuCards().

    Each worker heartbeat is expanded into one entry per model_key so the
    frontend can render a card per GPU-model combination.
    """
    redis = request.app.state.gateway.redis
    now = int(time.time())

    workers_raw = await _scan_workers(redis, now)

    gpus: list[dict] = []
    for w in workers_raw:
        raw = w["raw"]
        model_keys = w["model_keys"]
        # If worker has no model_keys, emit a single entry
        entries = model_keys if model_keys else [None]
        for mk in entries:
            gpus.append(
                {
                    "worker_id": w["worker_id"],
                    "model_key": mk,
                    "url": raw.get("comfyui_url", raw.get("url", "")),
                    "alive": w["alive"],
                    "status": w["status"],
                    "device_name": raw.get("device_name", "GPU"),
                    "vram_total_mb": int(raw.get("vram_total_mb", 0)),
                    "vram_used_mb": int(raw.get("vram_used_mb", 0)),
                    "vram_free_mb": int(raw.get("vram_free_mb", 0)),
                    "torch_vram_total_mb": int(raw.get("torch_vram_total_mb", 0)),
                    "torch_vram_used_mb": int(raw.get("torch_vram_used_mb", 0)),
                    "task": raw.get("current_task", None),
                }
            )

    return {"gpus": gpus, "queue_lengths": await _queue_lengths(redis)}


@router.get("/admin/settings")
async def get_settings(request: Request):
    """Return system settings from Redis, merged with defaults."""
    redis = request.app.state.gateway.redis
    raw = await redis.get(SYSTEM_SETTINGS_KEY)
    stored = json.loads(raw) if raw else {}
    # Defaults merged with stored values (stored takes precedence)
    merged = {**DEFAULT_SETTINGS, **stored}
    return merged


@router.put("/admin/settings")
async def put_settings(request: Request):
    """Save system settings to Redis."""
    redis = request.app.state.gateway.redis
    body = await request.json()
    # Merge with defaults so we always persist a complete settings object
    current_raw = await redis.get(SYSTEM_SETTINGS_KEY)
    current = json.loads(current_raw) if current_raw else {}
    updated = {**DEFAULT_SETTINGS, **current, **body}
    await redis.set(SYSTEM_SETTINGS_KEY, json.dumps(updated))
    return updated
