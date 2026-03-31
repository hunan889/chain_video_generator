"""Worker-specific configuration.

Loaded from environment variables / .env file.
"""

import json
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class WorkerConfig:
    """Immutable configuration for a GPU worker process."""

    # Worker identity
    worker_id: str

    # Redis
    redis_url: str

    # COS
    cos_secret_id: str
    cos_secret_key: str
    cos_bucket: str
    cos_region: str
    cos_prefix: str
    cos_cdn_domain: str

    # ComfyUI (local instances, model_key -> URL)
    comfyui_urls: dict[str, str] = field(default_factory=dict)

    # Task
    task_expiry: int = 86400

    # Heartbeat
    heartbeat_interval: float = 10  # seconds

    @property
    def model_keys(self) -> list[str]:
        """Return the list of model keys this worker handles."""
        return list(self.comfyui_urls.keys())


def load_config() -> WorkerConfig:
    """Load WorkerConfig from environment variables.

    Environment variables:
        WORKER_ID           -- unique worker identifier (auto-generated if missing)
        REDIS_URL           -- Redis connection URL
        COS_SECRET_ID       -- Tencent COS credentials
        COS_SECRET_KEY
        COS_BUCKET
        COS_REGION
        COS_PREFIX
        COS_CDN_DOMAIN
        COMFYUI_URLS        -- JSON dict: {"a14b": "http://...", "5b": "http://..."}
        TASK_EXPIRY          -- task TTL in seconds (default 86400)
        HEARTBEAT_INTERVAL   -- heartbeat period in seconds (default 10)
    """
    # Load .env from project root (two levels up from this file)
    project_root = Path(__file__).resolve().parent.parent
    load_dotenv(project_root / ".env")

    # Parse COMFYUI_URLS as JSON dict
    comfyui_urls_raw = os.getenv("COMFYUI_URLS", "{}")
    try:
        comfyui_urls = json.loads(comfyui_urls_raw)
    except (json.JSONDecodeError, TypeError):
        comfyui_urls = {}

    return WorkerConfig(
        worker_id=os.getenv("WORKER_ID", uuid.uuid4().hex[:12]),
        redis_url=os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
        cos_secret_id=os.getenv("COS_SECRET_ID", ""),
        cos_secret_key=os.getenv("COS_SECRET_KEY", ""),
        cos_bucket=os.getenv("COS_BUCKET", ""),
        cos_region=os.getenv("COS_REGION", "ap-guangzhou"),
        cos_prefix=os.getenv("COS_PREFIX", "wan22"),
        cos_cdn_domain=os.getenv("COS_CDN_DOMAIN", ""),
        comfyui_urls=comfyui_urls,
        task_expiry=int(os.getenv("TASK_EXPIRY", "86400")),
        heartbeat_interval=int(os.getenv("HEARTBEAT_INTERVAL", "10")),
    )
