"""Tests for POST /api/v1/generate/extend."""

import json

import pytest
from fastapi.testclient import TestClient

from api_gateway.main import create_app
from api_gateway.config import GatewayConfig
from shared.enums import TaskStatus


def _make_config(**overrides) -> GatewayConfig:
    base = dict(
        redis_url="redis://localhost",
        cos_secret_id="", cos_secret_key="", cos_bucket="test-bucket",
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
    from api_gateway.routes.extend import router as extend_router
    app.state.gateway = TaskGateway(redis=fake_redis)
    cos_cfg = COSConfig(secret_id="", secret_key="", bucket="test-bucket", region="ap-guangzhou")
    app.state.cos_client = COSClient(cos_cfg)
    app.state.chain_orchestrator = ChainOrchestrator(
        redis=fake_redis,
        gateway=app.state.gateway,
    )
    app.include_router(extend_router)
    return TestClient(app, raise_server_exceptions=True)


def _seed_parent_task(
    fake_redis,
    task_id: str,
    video_url: str = "https://cdn.example.com/videos/parent.mp4",
    last_frame_url: str = "https://cdn.example.com/frames/last.png",
    status: str = TaskStatus.COMPLETED.value,
):
    import asyncio
    from shared.redis_keys import task_key

    async def _do():
        mapping = {
            "status": status,
            "mode": "t2v",
            "model": "a14b",
            "workflow": "{}",
            "progress": "1.0",
            "video_url": video_url,
            "error": "",
            "created_at": "1700000000",
            "params": json.dumps({"width": 832, "height": 480, "fps": 16, "num_frames": 81}),
        }
        if last_frame_url:
            mapping["last_frame_url"] = last_frame_url
        await fake_redis.hset(task_key(task_id), mapping=mapping)

    asyncio.get_event_loop().run_until_complete(_do())


class TestExtend:
    def test_happy_path_creates_i2v_task(self, client, fake_redis):
        _seed_parent_task(fake_redis, "parent_ok")
        resp = client.post("/api/v1/generate/extend", json={
            "parent_task_id": "parent_ok",
            "prompt": "continue the scene",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "task_id" in data
        assert data["status"] == "queued"

    def test_parent_not_found_returns_404(self, client):
        resp = client.post("/api/v1/generate/extend", json={
            "parent_task_id": "nonexistent",
            "prompt": "extend",
        })
        assert resp.status_code == 404

    def test_parent_not_completed_returns_400(self, client, fake_redis):
        _seed_parent_task(fake_redis, "running_task", status="running")
        resp = client.post("/api/v1/generate/extend", json={
            "parent_task_id": "running_task",
            "prompt": "extend",
        })
        assert resp.status_code == 400

    def test_parent_no_last_frame_returns_400(self, client, fake_redis):
        _seed_parent_task(fake_redis, "no_frame_task", last_frame_url="")
        resp = client.post("/api/v1/generate/extend", json={
            "parent_task_id": "no_frame_task",
            "prompt": "extend",
        })
        assert resp.status_code == 400
