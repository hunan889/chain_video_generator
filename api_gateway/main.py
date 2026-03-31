"""API Gateway — minimal FastAPI application.

Serves as the public entry point for the video generation service.
Has NO dependency on ComfyUI or GPU resources.
"""

import logging
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import Depends, FastAPI, HTTPException

from api_gateway.config import GatewayConfig, load_config
from api_gateway.dependencies import get_cos_client, get_gateway
from api_gateway.services.chain_orchestrator import ChainOrchestrator
from shared.cos.client import COSClient
from shared.cos.config import COSConfig
from shared.redis_keys import WORKER_HEARTBEAT_PREFIX
from shared.task_gateway import TaskGateway

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create Redis connection, TaskGateway, COSClient.

    Shutdown: close Redis connection.
    """
    config: GatewayConfig = app.state.config

    redis_conn = aioredis.from_url(
        config.redis_url,
        decode_responses=True,
    )
    gateway = TaskGateway(redis=redis_conn, task_expiry=config.task_expiry)

    cos_config = COSConfig(
        secret_id=config.cos_secret_id,
        secret_key=config.cos_secret_key,
        bucket=config.cos_bucket,
        region=config.cos_region,
        prefix=config.cos_prefix,
        cdn_domain=config.cos_cdn_domain,
    )
    cos_client = COSClient(cos_config)

    chain_orchestrator = ChainOrchestrator(gateway=gateway, redis=redis_conn)

    app.state.redis = redis_conn
    app.state.gateway = gateway
    app.state.cos_client = cos_client
    app.state.chain_orchestrator = chain_orchestrator

    logger.info("API Gateway started (redis=%s)", config.redis_url)
    yield

    await redis_conn.aclose()
    logger.info("API Gateway shut down")


def create_app(config: GatewayConfig | None = None) -> FastAPI:
    """Factory function to create the FastAPI application."""
    app = FastAPI(
        title="Chain Video Generator — API Gateway",
        version="0.1.0",
        lifespan=lifespan,
    )

    if config is None:
        config = load_config()
    app.state.config = config

    # ------------------------------------------------------------------
    # Routers
    # ------------------------------------------------------------------
    from api_gateway.routes.generate import router as generate_router
    from api_gateway.routes.chain import router as chain_router

    app.include_router(generate_router)
    app.include_router(chain_router)

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    @app.get("/health")
    async def health(gw: TaskGateway = Depends(get_gateway)):
        """Health check: Redis connectivity and worker count."""
        redis_ok = await gw.redis_alive()
        worker_count = 0
        if redis_ok:
            try:
                keys = await gw.redis.keys(f"{WORKER_HEARTBEAT_PREFIX}:*")
                worker_count = len(keys)
            except Exception:
                pass
        return {
            "status": "ok" if redis_ok else "degraded",
            "redis": redis_ok,
            "workers": worker_count,
        }

    @app.get("/api/v1/tasks")
    async def list_tasks(gw: TaskGateway = Depends(get_gateway)):
        """List all tasks (excludes chain segment tasks)."""
        tasks = await gw.list_tasks()
        return {"tasks": tasks}

    @app.get("/api/v1/tasks/{task_id}")
    async def get_task(task_id: str, gw: TaskGateway = Depends(get_gateway)):
        """Get a single task by ID."""
        task = await gw.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
        return task

    @app.post("/api/v1/tasks/{task_id}/cancel")
    async def cancel_task(task_id: str, gw: TaskGateway = Depends(get_gateway)):
        """Cancel a queued task."""
        success = await gw.cancel_queued_task(task_id)
        if not success:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot cancel task {task_id} (not found or not queued)",
            )
        return {"cancelled": True, "task_id": task_id}

    return app


# Default app instance for `uvicorn api_gateway.main:app`
app = create_app()
