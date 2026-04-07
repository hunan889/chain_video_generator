"""Environment-driven configuration for the inference worker."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dotenv is optional in tests
    def load_dotenv(*_args, **_kwargs):
        return None


@dataclass(frozen=True)
class InferenceWorkerConfig:
    """All knobs for one inference_worker process.

    Loaded from environment variables (or .env files in dev). Defaults match
    the production layout described in CLAUDE.md / the GPU plan."""

    worker_id: str
    redis_url: str

    # Embedding model
    embedding_model: str          # e.g. "BAAI/bge-large-zh-v1.5"
    embedding_device: str         # "cuda", "cuda:7", "cpu"
    embedding_batch_size: int

    # vLLM endpoints (loopback on 148)
    llm_base_url: str             # e.g. "http://127.0.0.1:20001/v1"
    llm_model: str                # e.g. "Qwen3-14B-v2-Abliterated"
    vlm_base_url: str             # e.g. "http://127.0.0.1:20010/v1"
    vlm_model: str                # e.g. "Qwen2.5-VL-7B-Instruct-Unredacted-MAX"

    # Worker behaviour
    queue_blpop_timeout: int      # seconds for BLPOP
    task_expiry: int              # how long to keep task hashes
    heartbeat_interval: int


def load_config() -> InferenceWorkerConfig:
    """Load env vars; service-local .env overrides project root .env."""
    service_dir = Path(__file__).resolve().parent
    project_root = service_dir.parent.parent  # gpu/inference_worker -> repo root
    load_dotenv(service_dir / ".env")
    load_dotenv(project_root / ".env")

    return InferenceWorkerConfig(
        worker_id=os.getenv("INFERENCE_WORKER_ID", "inference-worker-1"),
        redis_url=os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
        embedding_model=os.getenv("EMBEDDING_MODEL", "BAAI/bge-large-zh-v1.5"),
        embedding_device=os.getenv("EMBEDDING_DEVICE", "cuda"),
        embedding_batch_size=int(os.getenv("EMBEDDING_BATCH_SIZE", "32")),
        llm_base_url=os.getenv("LLM_BASE_URL", "http://127.0.0.1:20001/v1").rstrip("/"),
        llm_model=os.getenv("LLM_MODEL", "Qwen3-14B-v2-Abliterated"),
        vlm_base_url=os.getenv("VLM_BASE_URL", "http://127.0.0.1:20010/v1").rstrip("/"),
        vlm_model=os.getenv("VLM_MODEL", "Qwen2.5-VL-7B-Instruct-Unredacted-MAX"),
        queue_blpop_timeout=int(os.getenv("INFERENCE_BLPOP_TIMEOUT", "1")),
        task_expiry=int(os.getenv("INFERENCE_TASK_EXPIRY", "300")),
        heartbeat_interval=int(os.getenv("HEARTBEAT_INTERVAL", "30")),
    )
