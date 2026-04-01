"""Tests for third-party API proxy endpoints (Wan26 + Seedance)."""

from unittest.mock import AsyncMock, MagicMock, patch

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
        wan26_api_key="test_wan26_key", wan26_api_url="https://wan26.example.com/api",
        byteplus_api_key="test_byteplus_key", byteplus_api_url="https://byteplus.example.com/api",
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
    from api_gateway.routes.thirdparty import router as tp_router
    app.state.gateway = TaskGateway(redis=fake_redis)
    cos_cfg = COSConfig(secret_id="", secret_key="", bucket="", region="ap-guangzhou")
    app.state.cos_client = COSClient(cos_cfg)
    app.state.chain_orchestrator = ChainOrchestrator(
        redis=fake_redis,
        gateway=app.state.gateway,
    )
    # TaskStore mock — submit endpoints record tasks via task_store.create()
    mock_task_store = AsyncMock()
    mock_task_store.create = AsyncMock()
    app.state.task_store = mock_task_store
    app.include_router(tp_router)
    return TestClient(app, raise_server_exceptions=True)


def _mock_aiohttp_post(status: int, body: dict):
    """Return a context-manager mock for aiohttp.ClientSession.post."""
    resp = AsyncMock()
    resp.status = status
    resp.json = AsyncMock(return_value=body)
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)

    session = MagicMock()
    session.post = MagicMock(return_value=resp)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


def _mock_aiohttp_get(status: int, body: dict):
    resp = AsyncMock()
    resp.status = status
    resp.json = AsyncMock(return_value=body)
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)

    session = MagicMock()
    session.get = MagicMock(return_value=resp)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


class TestWan26:
    def test_t2v_submit_success(self, client):
        session = _mock_aiohttp_post(200, {
            "output": {"task_id": "wan_task_123", "task_status": "PENDING"},
        })
        with patch("aiohttp.ClientSession", return_value=session):
            resp = client.post("/api/v1/thirdparty/wan26/text-to-video", json={
                "prompt": "a cat walking in the park",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["task_id"] == "wan_task_123"
        assert data["provider"] == "wan26"

    def test_t2v_submit_api_error_returns_error_response(self, client):
        session = _mock_aiohttp_post(500, {"message": "internal error"})
        with patch("aiohttp.ClientSession", return_value=session):
            resp = client.post("/api/v1/thirdparty/wan26/text-to-video", json={
                "prompt": "a dog",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False

    def test_query_task_succeeded(self, client):
        session = _mock_aiohttp_get(200, {
            "output": {
                "task_id": "wan_task_123",
                "task_status": "SUCCEEDED",
                "video_url": "https://example.com/video.mp4",
            }
        })
        with patch("aiohttp.ClientSession", return_value=session):
            resp = client.get("/api/v1/thirdparty/wan26/tasks/wan_task_123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["task_status"] == "SUCCEEDED"
        assert data["video_url"] == "https://example.com/video.mp4"


class TestSeedance:
    def test_t2v_submit_success(self, client):
        session = _mock_aiohttp_post(200, {
            "id": "seed_task_456",
            "status": "pending",
        })
        with patch("aiohttp.ClientSession", return_value=session):
            resp = client.post("/api/v1/thirdparty/seedance/text-to-video", json={
                "prompt": "a beautiful sunset",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["task_id"] == "seed_task_456"
        assert data["provider"] == "seedance"

    def test_aiohttp_exception_returns_error(self, client):
        import aiohttp as _aiohttp
        session = MagicMock()
        session.__aenter__ = AsyncMock(side_effect=_aiohttp.ClientError("connection refused"))
        session.__aexit__ = AsyncMock(return_value=False)
        with patch("aiohttp.ClientSession", return_value=session):
            resp = client.post("/api/v1/thirdparty/seedance/text-to-video", json={
                "prompt": "test",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error"] is not None
