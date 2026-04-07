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

    # WorkflowBuilder paths (shared/workflow_builder.py)
    workflows_dir: str  # path to workflows/ directory containing JSON templates

    # LLM
    llm_api_key: str
    llm_base_url: str
    llm_model: str

    # Vision
    vision_api_key: str
    vision_base_url: str
    vision_model: str

    # LoRA catalog (optional — used by prompt/optimize and loras/recommend)
    loras_yaml_path: str

    # Third-party APIs
    wan26_api_key: str
    wan26_api_url: str
    byteplus_api_key: str
    byteplus_api_url: str
    civitai_api_token: str

    # MySQL
    mysql_host: str
    mysql_port: int
    mysql_user: str
    mysql_password: str
    mysql_db: str

    # Forge (Reactor face swap, Stable Diffusion WebUI)
    forge_url: str

    # BytePlus SeeDream
    byteplus_endpoint: str
    byteplus_seedream_model: str

    # Seedance 2.0 (OpenGW)
    seedance2_api_key: str
    seedance2_api_url: str

    # Reverse proxy to old monolith
    monolith_url: str


def load_config(env_file: str = ".env") -> GatewayConfig:
    """Load configuration from environment variables (with .env fallback).

    Returns an immutable GatewayConfig instance.
    """
    # Load service-local .env first, then the supplied env_file (project root .env)
    service_dir = os.path.dirname(os.path.abspath(__file__))
    load_dotenv(os.path.join(service_dir, ".env"))  # api_gateway/.env
    load_dotenv(env_file)                            # project root .env (shared fallback)

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
        # WorkflowBuilder
        workflows_dir=os.getenv("WORKFLOWS_DIR", ""),
        # LLM
        llm_api_key=os.getenv("LLM_API_KEY", ""),
        llm_base_url=os.getenv("LLM_BASE_URL", ""),
        llm_model=os.getenv("LLM_MODEL", ""),
        # Vision
        vision_api_key=os.getenv("VISION_API_KEY", ""),
        vision_base_url=os.getenv("VISION_BASE_URL", ""),
        vision_model=os.getenv("VISION_MODEL", ""),
        # LoRA catalog
        loras_yaml_path=os.getenv("LORAS_YAML_PATH", ""),
        # Third-party APIs
        wan26_api_key=os.getenv("WAN26_API_KEY", ""),
        wan26_api_url=os.getenv("WAN26_API_URL", ""),
        byteplus_api_key=os.getenv("BYTEPLUS_API_KEY", ""),
        byteplus_api_url=os.getenv("BYTEPLUS_API_URL", ""),
        civitai_api_token=os.getenv("CIVITAI_API_TOKEN", ""),
        # MySQL
        mysql_host=os.getenv("MYSQL_HOST", "use-cdb-b9nvte6o.sql.tencentcdb.com"),
        mysql_port=int(os.getenv("MYSQL_PORT", "20603")),
        mysql_user=os.getenv("MYSQL_USER", "user_soga"),
        mysql_password=os.getenv("MYSQL_PASSWORD", "1IvO@*#68"),
        mysql_db=os.getenv("MYSQL_DB", "tudou_soga"),
        # Forge
        forge_url=os.getenv("FORGE_URL", "http://127.0.0.1:7860"),
        # BytePlus SeeDream
        byteplus_endpoint=os.getenv(
            "BYTEPLUS_ENDPOINT",
            "https://ark.ap-southeast.bytepluses.com/api/v3/images/generations",
        ),
        byteplus_seedream_model=os.getenv(
            "BYTEPLUS_SEEDREAM_MODEL", "ep-20260302170919-cggr8"
        ),
        # Seedance 2.0 (OpenGW)
        seedance2_api_key=os.getenv(
            "SEEDANCE2_API_KEY",
            "sk-pXqPcMUyJJ0cThnANLBmQ4h5Ucv3fmpp7db8gaN9sMsElBVP",
        ),
        seedance2_api_url=os.getenv(
            "SEEDANCE2_API_URL", "https://opengw.com"
        ),
        # Reverse proxy
        monolith_url=os.getenv("MONOLITH_URL", "http://148.153.121.44:8000"),
    )
