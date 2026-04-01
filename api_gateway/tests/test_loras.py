"""Tests for GET /api/v1/loras and POST /api/v1/loras/download."""

import json

import pytest
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
    import fakeredis.aioredis
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def client(fake_redis):
    app = create_app()
    app.state.config = _make_config()
    from shared.task_gateway import TaskGateway
    from shared.cos.config import COSConfig
    from shared.cos.client import COSClient
    from api_gateway.services.chain_orchestrator import ChainOrchestrator
    from api_gateway.routes.loras import router as loras_router
    app.state.gateway = TaskGateway(redis=fake_redis)
    cos_cfg = COSConfig(secret_id="", secret_key="", bucket="", region="ap-guangzhou")
    app.state.cos_client = COSClient(cos_cfg)
    app.state.chain_orchestrator = ChainOrchestrator(
        redis=fake_redis,
        gateway=app.state.gateway,
    )
    app.include_router(loras_router)
    return TestClient(app, raise_server_exceptions=True)


class TestLoraList:
    def test_no_workers_returns_empty(self, client):
        resp = client.get("/api/v1/loras")
        assert resp.status_code == 200
        assert resp.json()["loras"] == []

    def test_single_worker_loras_returned(self, client, fake_redis):
        import asyncio
        loras = [
            {"name": "my_lora", "filename": "my_lora.safetensors", "size_mb": 120.5},
            {"name": "another_lora", "filename": "another_lora.safetensors", "size_mb": 85.0},
        ]
        async def _seed():
            from shared.redis_keys import worker_loras_key
            await fake_redis.set(worker_loras_key("gpu-worker-1"), json.dumps(loras))
        asyncio.get_event_loop().run_until_complete(_seed())

        resp = client.get("/api/v1/loras")
        assert resp.status_code == 200
        result = resp.json()["loras"]
        assert len(result) == 2
        names = {l["name"] for l in result}
        assert names == {"my_lora", "another_lora"}

    def test_two_workers_deduplicated_by_name(self, client, fake_redis):
        import asyncio
        loras_w1 = [{"name": "shared_lora", "filename": "shared_lora.safetensors", "size_mb": 100.0},
                    {"name": "only_w1", "filename": "only_w1.safetensors", "size_mb": 50.0}]
        loras_w2 = [{"name": "shared_lora", "filename": "shared_lora.safetensors", "size_mb": 100.0},
                    {"name": "only_w2", "filename": "only_w2.safetensors", "size_mb": 60.0}]
        async def _seed():
            from shared.redis_keys import worker_loras_key
            await fake_redis.set(worker_loras_key("worker-1"), json.dumps(loras_w1))
            await fake_redis.set(worker_loras_key("worker-2"), json.dumps(loras_w2))
        asyncio.get_event_loop().run_until_complete(_seed())

        resp = client.get("/api/v1/loras")
        assert resp.status_code == 200
        result = resp.json()["loras"]
        names = [l["name"] for l in result]
        assert len(names) == len(set(names)), "Duplicate LoRA names found"
        assert "shared_lora" in names
        assert "only_w1" in names
        assert "only_w2" in names
        assert len(names) == 3


class TestLoraDownload:
    def test_download_creates_task(self, client):
        resp = client.post("/api/v1/loras/download", json={
            "civitai_version_id": 123456,
            "filename": "my_new_lora.safetensors",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "task_id" in data
        assert data["status"] == "queued"

    def test_download_missing_fields_returns_422(self, client):
        resp = client.post("/api/v1/loras/download", json={"civitai_version_id": 123})
        assert resp.status_code == 422
