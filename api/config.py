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
COMFYUI_A14B_URL = os.getenv("COMFYUI_A14B_URL", "http://127.0.0.1:8188")
COMFYUI_5B_URL = os.getenv("COMFYUI_5B_URL", "http://127.0.0.1:8189")
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
WORKFLOWS_DIR = PROJECT_ROOT / "workflows"

_ALL_COMFYUI_URLS = {
    "a14b": COMFYUI_A14B_URL,
    "5b": COMFYUI_5B_URL,
}
_enabled = os.getenv("ENABLED_WORKERS", "a14b,5b").split(",")
COMFYUI_URLS = {k: v for k, v in _ALL_COMFYUI_URLS.items() if k.strip() in [w.strip() for w in _enabled]}

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
