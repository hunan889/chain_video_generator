import logging
from fastapi import APIRouter, Depends
from api.middleware.auth import verify_api_key

logger = logging.getLogger(__name__)
router = APIRouter()

# Default values for system settings
DEFAULTS = {
    "prompt_optimize_min_chars": 20,
    "prompt_optimize_non_turbo": True,
    "inject_trigger_prompt": True,
    "inject_trigger_words": True,
}

REDIS_KEY = "system:settings"


def _get_task_manager():
    from api.main import task_manager
    return task_manager


@router.get("/admin/settings", dependencies=[Depends(verify_api_key)])
async def get_settings():
    """Return all system settings (with defaults for unset keys)."""
    tm = _get_task_manager()
    stored = await tm.redis.hgetall(REDIS_KEY)
    result = {}
    for key, default in DEFAULTS.items():
        raw = stored.get(key)
        if raw is None:
            result[key] = default
        elif isinstance(default, bool):
            result[key] = raw.lower() in ("true", "1")
        elif isinstance(default, int):
            result[key] = int(raw)
        else:
            result[key] = raw
    return result


@router.put("/admin/settings", dependencies=[Depends(verify_api_key)])
async def update_settings(data: dict):
    """Bulk-update system settings (only known keys are accepted)."""
    tm = _get_task_manager()
    mapping = {}
    for k, v in data.items():
        if k not in DEFAULTS:
            continue
        if isinstance(v, bool):
            mapping[k] = str(v).lower()
        else:
            mapping[k] = str(v)
    if mapping:
        await tm.redis.hset(REDIS_KEY, mapping=mapping)
        logger.info(f"[SETTINGS] Updated: {mapping}")
    return await get_settings()


async def get_prompt_optimize_settings() -> dict:
    """Read prompt optimization settings from Redis (async).

    Returns dict with 'min_chars' (int) and 'non_turbo' (bool).
    """
    tm = _get_task_manager()
    stored = await tm.redis.hgetall(REDIS_KEY)
    min_chars_raw = stored.get("prompt_optimize_min_chars")
    non_turbo_raw = stored.get("prompt_optimize_non_turbo")
    inject_prompt_raw = stored.get("inject_trigger_prompt")
    inject_words_raw = stored.get("inject_trigger_words")
    return {
        "min_chars": int(min_chars_raw) if min_chars_raw is not None else DEFAULTS["prompt_optimize_min_chars"],
        "non_turbo": non_turbo_raw.lower() in ("true", "1") if non_turbo_raw is not None else DEFAULTS["prompt_optimize_non_turbo"],
        "inject_trigger_prompt": inject_prompt_raw.lower() in ("true", "1") if inject_prompt_raw is not None else DEFAULTS["inject_trigger_prompt"],
        "inject_trigger_words": inject_words_raw.lower() in ("true", "1") if inject_words_raw is not None else DEFAULTS["inject_trigger_words"],
    }
