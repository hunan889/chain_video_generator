"""Tests for GET /api/v1/admin/workers."""

import json
import time

import pytest
import pytest_asyncio
import fakeredis.aioredis
from fastapi.testclient import TestClient

from api_gateway.main import create_app
from api_gateway.config import GatewayConfig


def _make_config(**overrides) -> GatewayConfig:
    base = dict(
        redis_url="redis://localhost",
        cos_secret_id="", cos_secret_key="", cos_bucket="",
        cos_region="ap-guangzhou", cos_prefix="test", cos_cdn_domain="",
        api_host="0.0.0.0", api_port=8000, task_expiry=86400,
        workflows_dir="",
        llm_api_key="", llm_base_url="", llm_model="",
        vision_api_key="", vision_base_url="", vision_model="",
        wan26_api_key="", wan26_api_url="",
        byteplus_api_key="", byteplus_api_url="",
        civitai_api_token="",
        loras_yaml_path="",
        mysql_host="localhost", mysql_port=3306,
        mysql_user="test", mysql_password="test", mysql_db="test",
        forge_url="http://localhost:7860",
        byteplus_endpoint="https://example.com/api",
        byteplus_seedream_model="test-model",
        monolith_url="http://localhost:8000",
    )
    base.update(overrides)
    return GatewayConfig(**base)


@pytest.fixture
def fake_redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def client(fake_redis):
    app = create_app()
    app.state.config = _make_config()
    from shared.task_gateway import TaskGateway
    from shared.cos.config import COSConfig
    from shared.cos.client import COSClient
    from api_gateway.services.chain_orchestrator import ChainOrchestrator
    app.state.gateway = TaskGateway(redis=fake_redis)
    cos_cfg = COSConfig(secret_id="", secret_key="", bucket="", region="ap-guangzhou")
    app.state.cos_client = COSClient(cos_cfg)
    app.state.chain_orchestrator = ChainOrchestrator(
        redis=fake_redis,
        gateway=app.state.gateway,
    )
    from api_gateway.routes.admin import router as admin_router
    app.include_router(admin_router)
    return TestClient(app, raise_server_exceptions=True)


class TestAdminWorkers:
    def test_empty_redis_returns_empty_list(self, client):
        resp = client.get("/api/v1/admin/workers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["workers"] == []
        assert "queue_lengths" in data

    def test_fresh_worker_is_alive(self, client, fake_redis):
        import asyncio
        async def _seed():
            from shared.redis_keys import worker_heartbeat_key
            await fake_redis.hset(
                worker_heartbeat_key("gpu-worker-1"),
                mapping={
                    "last_seen": str(int(time.time())),
                    "model_keys": json.dumps(["a14b"]),
                    "status": "idle",
                },
            )
        asyncio.get_event_loop().run_until_complete(_seed())

        resp = client.get("/api/v1/admin/workers")
        assert resp.status_code == 200
        workers = resp.json()["workers"]
        assert len(workers) == 1
        w = workers[0]
        assert w["worker_id"] == "gpu-worker-1"
        assert w["alive"] is True
        assert w["status"] == "idle"
        assert w["model_keys"] == ["a14b"]

    def test_stale_worker_is_not_alive(self, client, fake_redis):
        import asyncio
        async def _seed():
            from shared.redis_keys import worker_heartbeat_key
            await fake_redis.hset(
                worker_heartbeat_key("old-worker"),
                mapping={
                    "last_seen": str(int(time.time()) - 120),  # 2 minutes ago
                    "model_keys": json.dumps(["5b"]),
                    "status": "idle",
                },
            )
        asyncio.get_event_loop().run_until_complete(_seed())

        resp = client.get("/api/v1/admin/workers")
        assert resp.status_code == 200
        workers = resp.json()["workers"]
        assert len(workers) == 1
        assert workers[0]["alive"] is False

    def test_queue_lengths_returned(self, client, fake_redis):
        import asyncio
        async def _seed():
            await fake_redis.rpush("queue:a14b", "task1", "task2")
            await fake_redis.rpush("queue:5b", "task3")
        asyncio.get_event_loop().run_until_complete(_seed())

        resp = client.get("/api/v1/admin/workers")
        assert resp.status_code == 200
        ql = resp.json()["queue_lengths"]
        assert ql["a14b"] == 2
        assert ql["5b"] == 1
