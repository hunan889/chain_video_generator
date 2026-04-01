"""E2E test fixtures — spins up API Gateway in-process with fakeredis.

No real Redis, no real ComfyUI, no port conflicts with the existing service.
Uses httpx.AsyncClient + ASGITransport so tests exercise the full ASGI stack.

Note: httpx.ASGITransport does NOT send ASGI lifespan events, so app.state
is populated directly before yielding the client.
"""

from contextlib import asynccontextmanager

import fakeredis.aioredis as fake_aioredis
import httpx
import pytest_asyncio
from fastapi import Depends, FastAPI, HTTPException

from api_gateway.config import GatewayConfig
from api_gateway.dependencies import get_gateway
from api_gateway.services.chain_orchestrator import ChainOrchestrator
from shared.cos.client import COSClient
from shared.cos.config import COSConfig
from shared.redis_keys import WORKER_HEARTBEAT_PREFIX
from shared.task_gateway import TaskGateway


_BASE_CONFIG = GatewayConfig(
    redis_url="redis://localhost:6379/15",  # not used — we inject fakeredis directly
    api_host="127.0.0.1",
    api_port=9001,  # distinct from existing service port 8000
    task_expiry=300,
    cos_secret_id="",
    cos_secret_key="",
    cos_bucket="",
    cos_region="ap-guangzhou",
    cos_prefix="test",
    cos_cdn_domain="",
    workflows_dir="",
    vision_api_key="",
    vision_base_url="",
    vision_model="",
    llm_api_key="",
    llm_base_url="",
    llm_model="",
    loras_yaml_path="",
    wan26_api_key="",
    wan26_api_url="",
    byteplus_api_key="",
    byteplus_api_url="",
    civitai_api_token="",
    monolith_url="http://localhost:8000",
)


def _build_test_app(fake_redis) -> FastAPI:
    """Build a FastAPI app without lifespan, state pre-populated with fakeredis."""
    gateway = TaskGateway(redis=fake_redis, task_expiry=300)
    cos_client = COSClient(COSConfig.disabled())
    chain_orchestrator = ChainOrchestrator(gateway=gateway, redis=fake_redis)

    # No lifespan — httpx.ASGITransport doesn't send lifespan ASGI events anyway.
    # We populate app.state directly after construction.
    app = FastAPI(
        title="Chain Video Generator — API Gateway (test)",
        version="0.1.0",
    )

    # Populate state immediately — available for every request
    app.state.config = _BASE_CONFIG
    app.state.redis = fake_redis
    app.state.gateway = gateway
    app.state.cos_client = cos_client
    app.state.chain_orchestrator = chain_orchestrator

    # Include the same routers as the real app
    from api_gateway.routes.generate import router as generate_router
    from api_gateway.routes.chain import router as chain_router

    app.include_router(generate_router)
    app.include_router(chain_router)

    # Health + task routes (mirrors api_gateway/main.py)
    @app.get("/health")
    async def health(gw: TaskGateway = Depends(get_gateway)):
        redis_ok = await gw.redis_alive()
        worker_count = 0
        if redis_ok:
            try:
                keys = await gw.redis.keys(f"{WORKER_HEARTBEAT_PREFIX}:*")
                worker_count = len(keys)
            except Exception:
                pass
        return {"status": "ok" if redis_ok else "degraded", "redis": redis_ok, "workers": worker_count}

    @app.get("/api/v1/tasks")
    async def list_tasks(gw: TaskGateway = Depends(get_gateway)):
        tasks = await gw.list_tasks()
        return {"tasks": tasks}

    @app.get("/api/v1/tasks/{task_id}")
    async def get_task(task_id: str, gw: TaskGateway = Depends(get_gateway)):
        task = await gw.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
        return task

    @app.post("/api/v1/tasks/{task_id}/cancel")
    async def cancel_task(task_id: str, gw: TaskGateway = Depends(get_gateway)):
        success = await gw.cancel_queued_task(task_id)
        if not success:
            raise HTTPException(status_code=409, detail=f"Cannot cancel task {task_id}")
        return {"cancelled": True, "task_id": task_id}

    return app


@pytest_asyncio.fixture
async def client():
    """httpx.AsyncClient wired to the test API Gateway with fakeredis."""
    fake_redis = fake_aioredis.FakeRedis(decode_responses=True)
    app = _build_test_app(fake_redis)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as c:
        yield c
    await fake_redis.aclose()
