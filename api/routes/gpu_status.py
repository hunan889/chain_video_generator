import asyncio
import logging
from fastapi import APIRouter, Depends
from api.middleware.auth import verify_api_key

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_task_manager():
    from api.main import task_manager
    return task_manager


async def _fetch_worker_gpu(worker_info: dict) -> dict | None:
    """Fetch GPU info from a single ComfyUI worker via /system_stats."""
    client = worker_info["client"]
    stats = await client.get_system_stats()
    if not stats:
        return None
    return stats


@router.get("/admin/gpu-status", dependencies=[Depends(verify_api_key)])
async def get_gpu_status():
    """Get GPU status from all registered ComfyUI workers."""
    tm = _get_task_manager()

    # Get running tasks concurrently with worker stats
    running_tasks_future = tm.get_running_tasks_by_worker()

    # Collect all workers and fetch their system_stats in parallel
    workers_raw = tm._workers  # {worker_id: {model_key, url, client, ...}}
    fetch_tasks = {}
    for wid, info in workers_raw.items():
        fetch_tasks[wid] = _fetch_worker_gpu(info)

    # Run all fetches + running tasks concurrently
    results = await asyncio.gather(
        running_tasks_future,
        *fetch_tasks.values(),
        return_exceptions=True,
    )

    running_tasks = results[0] if not isinstance(results[0], Exception) else {}
    stats_results = dict(zip(fetch_tasks.keys(), results[1:]))

    # Build GPU list from worker stats
    gpus = []
    for wid, info in workers_raw.items():
        stats = stats_results.get(wid)
        if isinstance(stats, Exception):
            stats = None

        url = info["url"]
        model_key = info["model_key"]
        alive = stats is not None

        # Parse devices from system_stats
        devices = stats.get("devices", []) if stats else []
        system = stats.get("system", {}) if stats else {}

        if devices:
            for dev in devices:
                vram_total = dev.get("vram_total", 0)
                vram_free = dev.get("vram_free", 0)
                vram_used = vram_total - vram_free
                torch_total = dev.get("torch_vram_total", 0)
                torch_free = dev.get("torch_vram_free", 0)
                torch_used = torch_total - torch_free

                gpu_entry = {
                    "worker_id": wid,
                    "model_key": model_key,
                    "url": url,
                    "alive": alive,
                    "device_name": dev.get("name", "Unknown"),
                    "device_type": dev.get("type", "unknown"),
                    "device_index": dev.get("index", 0),
                    "vram_total_mb": round(vram_total / 1024 / 1024),
                    "vram_used_mb": round(vram_used / 1024 / 1024),
                    "vram_free_mb": round(vram_free / 1024 / 1024),
                    "torch_vram_total_mb": round(torch_total / 1024 / 1024),
                    "torch_vram_used_mb": round(torch_used / 1024 / 1024),
                    "comfyui_version": system.get("comfyui_version", ""),
                    "pytorch_version": system.get("pytorch_version", ""),
                    "task": running_tasks.get(url),
                }
                gpus.append(gpu_entry)
        else:
            # Worker unreachable — still show it with offline status
            gpus.append({
                "worker_id": wid,
                "model_key": model_key,
                "url": url,
                "alive": False,
                "device_name": "Unknown (offline)",
                "device_type": "unknown",
                "device_index": 0,
                "vram_total_mb": 0,
                "vram_used_mb": 0,
                "vram_free_mb": 0,
                "torch_vram_total_mb": 0,
                "torch_vram_used_mb": 0,
                "comfyui_version": "",
                "pytorch_version": "",
                "task": running_tasks.get(url),
            })

    # Queue lengths
    queue_lengths = {}
    try:
        for mk in ("a14b", "5b"):
            qlen = await tm.redis.llen(f"queue:{mk}")
            queue_lengths[mk] = qlen
    except Exception:
        pass

    return {
        "gpus": gpus,
        "queue_lengths": queue_lengths,
    }
