"""Tests for post-processing endpoints."""

import json
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from api_gateway.main import create_app
from api_gateway.config import GatewayConfig
from shared.enums import GenerateMode, TaskStatus


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
def client(fake_redis, monkeypatch):
    # Mock COS upload so tests don't need real credentials
    monkeypatch.setattr(
        "shared.cos.client.COSClient.upload_file",
        lambda self, local_path, subdir, filename: f"https://cdn.example.com/{subdir}/{filename}",
    )
    app = create_app()
    app.state.config = _make_config()
    from shared.task_gateway import TaskGateway
    from shared.cos.config import COSConfig
    from shared.cos.client import COSClient
    from api_gateway.services.chain_orchestrator import ChainOrchestrator
    from api_gateway.routes.postprocess import router as pp_router
    app.state.gateway = TaskGateway(redis=fake_redis)
    cos_cfg = COSConfig(secret_id="", secret_key="", bucket="test-bucket", region="ap-guangzhou")
    app.state.cos_client = COSClient(cos_cfg)
    app.state.chain_orchestrator = ChainOrchestrator(
        redis=fake_redis,
        gateway=app.state.gateway,
    )
    app.include_router(pp_router)
    return TestClient(app, raise_server_exceptions=True)


def _seed_completed_task(fake_redis, task_id: str, video_url: str = "https://cdn.example.com/videos/task.mp4"):
    import asyncio
    from shared.redis_keys import task_key
    async def _do():
        await fake_redis.hset(task_key(task_id), mapping={
            "status": TaskStatus.COMPLETED.value,
            "mode": "t2v",
            "model": "a14b",
            "workflow": "{}",
            "progress": "1.0",
            "video_url": video_url,
            "error": "",
            "created_at": "1700000000",
            "params": json.dumps({"width": 832, "height": 480}),
        })
    asyncio.get_event_loop().run_until_complete(_do())


class TestInterpolate:
    def test_task_id_source_creates_task(self, client, fake_redis):
        _seed_completed_task(fake_redis, "parent123")
        resp = client.post("/api/v1/postprocess/interpolate",
                           data={"task_id": "parent123", "multiplier": "2", "fps": "16.0"})
        assert resp.status_code == 200
        data = resp.json()
        assert "task_id" in data
        assert data["status"] == "queued"

    def test_nonexistent_task_returns_404(self, client):
        resp = client.post("/api/v1/postprocess/interpolate",
                           data={"task_id": "nonexistent"})
        assert resp.status_code == 404

    def test_incomplete_task_returns_400(self, client, fake_redis):
        import asyncio
        from shared.redis_keys import task_key
        async def _seed():
            await fake_redis.hset(task_key("running123"), mapping={
                "status": "running", "mode": "t2v", "model": "a14b",
                "workflow": "{}", "progress": "0.5", "video_url": "",
                "error": "", "created_at": "1700000000",
            })
        asyncio.get_event_loop().run_until_complete(_seed())
        resp = client.post("/api/v1/postprocess/interpolate",
                           data={"task_id": "running123"})
        assert resp.status_code == 400


class TestUpscale:
    def test_creates_task_with_upscale_mode(self, client, fake_redis):
        _seed_completed_task(fake_redis, "src_task")
        resp = client.post("/api/v1/postprocess/upscale",
                           data={"task_id": "src_task", "resize_to": "2x"})
        assert resp.status_code == 200
        assert "task_id" in resp.json()


class TestAudio:
    def test_creates_task_with_audio_mode(self, client, fake_redis):
        _seed_completed_task(fake_redis, "src_audio")
        resp = client.post("/api/v1/postprocess/audio",
                           data={"task_id": "src_audio", "prompt": "rain sounds"})
        assert resp.status_code == 200
        assert "task_id" in resp.json()


class TestFaceswap:
    def test_faceswap_with_face_upload(self, client, fake_redis):
        _seed_completed_task(fake_redis, "src_face")
        face_bytes = b"fake_face_image_data"
        resp = client.post(
            "/api/v1/postprocess/faceswap",
            data={"task_id": "src_face", "strength": "0.9"},
            files={"face_image": ("face.png", BytesIO(face_bytes), "image/png")},
        )
        assert resp.status_code == 200
        assert "task_id" in resp.json()

    def test_faceswap_without_face_returns_422(self, client, fake_redis):
        _seed_completed_task(fake_redis, "src_face2")
        resp = client.post("/api/v1/postprocess/faceswap",
                           data={"task_id": "src_face2"})
        assert resp.status_code == 422
