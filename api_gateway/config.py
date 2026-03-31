"""Gateway-specific configuration loaded from environment variables."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class GatewayConfig:
    """Immutable configuration for the API Gateway service."""

    # Redis
    redis_url: str

    # COS (Tencent Cloud Object Storage)
    cos_secret_id: str
    cos_secret_key: str
    cos_bucket: str
    cos_region: str
    cos_prefix: str
    cos_cdn_domain: str

    # API Server
    api_host: str
    api_port: int

    # Task
    task_expiry: int

    # LLM
    llm_api_key: str
    llm_base_url: str
    llm_model: str

    # Vision
    vision_api_key: str
    vision_base_url: str
    vision_model: str


def load_config(env_file: str = ".env") -> GatewayConfig:
    """Load configuration from environment variables (with .env fallback).

    Returns an immutable GatewayConfig instance.
    """
    load_dotenv(env_file)

    return GatewayConfig(
        # Redis
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        # COS
        cos_secret_id=os.getenv("COS_SECRET_ID", ""),
        cos_secret_key=os.getenv("COS_SECRET_KEY", ""),
        cos_bucket=os.getenv("COS_BUCKET", ""),
        cos_region=os.getenv("COS_REGION", "ap-guangzhou"),
        cos_prefix=os.getenv("COS_PREFIX", "wan22"),
        cos_cdn_domain=os.getenv("COS_CDN_DOMAIN", ""),
        # API Server
        api_host=os.getenv("API_HOST", "0.0.0.0"),
        api_port=int(os.getenv("API_PORT", "8000")),
        # Task
        task_expiry=int(os.getenv("TASK_EXPIRY", "86400")),
        # LLM
        llm_api_key=os.getenv("LLM_API_KEY", ""),
        llm_base_url=os.getenv("LLM_BASE_URL", ""),
        llm_model=os.getenv("LLM_MODEL", ""),
        # Vision
        vision_api_key=os.getenv("VISION_API_KEY", ""),
        vision_base_url=os.getenv("VISION_BASE_URL", ""),
        vision_model=os.getenv("VISION_MODEL", ""),
    )
