import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")


def _resolve_path(env_var: str, default: str) -> Path:
    """Resolve a path from env. Relative paths are resolved against PROJECT_ROOT."""
    raw = os.getenv(env_var, default)
    p = Path(raw)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p.resolve()


COMFYUI_PATH = _resolve_path("COMFYUI_PATH", "./ComfyUI")
FORGE_URL = os.getenv("FORGE_URL", "http://127.0.0.1:7860")
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "8000"))
STORAGE_PATH = _resolve_path("STORAGE_PATH", "./storage")
API_KEYS_PATH = _resolve_path("API_KEYS_PATH", "./config/api_keys.yaml")
LORAS_PATH = _resolve_path("LORAS_PATH", "./config/loras.yaml")
VIDEO_BASE_URL = os.getenv("VIDEO_BASE_URL", "/api/v1/results")

VIDEOS_DIR = STORAGE_PATH / "videos"
UPLOADS_DIR = STORAGE_PATH / "uploads"
RESULTS_DIR = UPLOADS_DIR  # Alias: /api/v1/results/ serves files from UPLOADS_DIR for images
WORKFLOWS_DIR = PROJECT_ROOT / "workflows"


def _parse_urls(env_var: str, default: str) -> list[str]:
    """Parse comma-separated URLs from env var."""
    raw = os.getenv(env_var, default)
    return [u.strip() for u in raw.split(",") if u.strip()]


def _expand_comfyui_urls() -> dict[str, str]:
    """Expand model URL arrays into worker keys: a14b#0, a14b#1, 5b#0, etc."""
    model_defaults = {
        "a14b": "http://127.0.0.1:8188",
        "5b": "http://127.0.0.1:8189",
    }
    # Support both old single-URL and new multi-URL env vars
    # New: COMFYUI_A14B_URLS=url1,url2  Old: COMFYUI_A14B_URL=url1
    env_vars = {
        "a14b": ("COMFYUI_A14B_URLS", os.getenv("COMFYUI_A14B_URL", model_defaults["a14b"])),
        "5b": ("COMFYUI_5B_URLS", os.getenv("COMFYUI_5B_URL", model_defaults["5b"])),
    }
    enabled = [w.strip() for w in os.getenv("ENABLED_WORKERS", "a14b,5b").split(",")]
    result = {}
    for model, (env_key, fallback) in env_vars.items():
        if model not in enabled:
            continue
        urls = _parse_urls(env_key, fallback)
        for i, url in enumerate(urls):
            result[f"{model}#{i}"] = url
    return result


# COMFYUI_URLS maps worker_key -> url, e.g. {"a14b#0": "...", "a14b#1": "...", "5b#0": "..."}
COMFYUI_URLS = _expand_comfyui_urls()

# Task expiry in seconds (24 hours)
TASK_EXPIRY = 86400

# External API keys
CIVITAI_API_TOKEN = os.getenv("CIVITAI_API_TOKEN", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_BASE_URL = os.getenv("ANTHROPIC_BASE_URL", "")

# LLM for prompt optimization (OpenAI-compatible API)
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
LLM_MODEL = os.getenv("LLM_MODEL", "")

# Vision model for image description (Gemini API)
VISION_API_KEY = os.getenv("VISION_API_KEY", "")
VISION_BASE_URL = os.getenv("VISION_BASE_URL", "")
VISION_MODEL = os.getenv("VISION_MODEL", "gemini-2.5-flash")

# Tencent COS
COS_SECRET_ID = os.getenv("COS_SECRET_ID", "")
COS_SECRET_KEY = os.getenv("COS_SECRET_KEY", "")
COS_BUCKET = os.getenv("COS_BUCKET", "")
COS_REGION = os.getenv("COS_REGION", "ap-guangzhou")
COS_PREFIX = os.getenv("COS_PREFIX", "wan22")
COS_ENABLED = bool(COS_BUCKET)
