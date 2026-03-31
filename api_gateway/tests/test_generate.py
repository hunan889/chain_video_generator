"""Tests for the POST /api/v1/generate endpoint.

Written FIRST (TDD red phase). Uses FakeRedis to mock Redis
and a mock COSClient so no real uploads occur.
"""

import json
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from shared.cos.client import COSClient
from shared.cos.config import COSConfig
from shared.task_gateway import TaskGateway


# ============================================================
# Fake Redis (same pattern as test_gateway_app.py)
# ============================================================


class FakeRedis:
    """Minimal async Redis fake for hash/list/scan ops."""

    def __init__(self):
        self._data: dict[str, dict | list | set] = {}
        self._expiry: dict[str, int] = {}

    async def ping(self) -> bool:
        return True

    async def hset(self, key: str, mapping: dict = None, **kwargs):
        if key not in self._data or not isinstance(self._data[key], dict):
            self._data[key] = {}
        if mapping:
            self._data[key].update(mapping)
        self._data[key].update(kwargs)

    async def hgetall(self, key: str) -> dict:
        val = self._data.get(key, {})
        return val if isinstance(val, dict) else {}

    async def hget(self, key: str, field: str) -> str | None:
        val = self._data.get(key, {})
        if isinstance(val, dict):
            return val.get(field)
        return None

    async def expire(self, key: str, seconds: int):
        self._expiry[key] = seconds

    async def rpush(self, key: str, *values):
        if key not in self._data or not isinstance(self._data[key], list):
            self._data[key] = []
        self._data[key].extend(values)

    async def lrem(self, key: str, count: int, value: str):
        if key in self._data and isinstance(self._data[key], list):
            self._data[key] = [v for v in self._data[key] if v != value]

    async def llen(self, key: str) -> int:
        val = self._data.get(key, [])
        return len(val) if isinstance(val, list) else 0

    async def scan(self, cursor: int, match: str = "*", count: int = 100):
        pattern = match.replace("*", "")
        keys = [k for k in self._data if k.startswith(pattern)]
        return 0, keys

    async def keys(self, pattern: str = "*") -> list[str]:
        prefix = pattern.replace("*", "")
        return [k for k in self._data if k.startswith(prefix)]

    async def aclose(self):
        pass


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture()
def fake_redis():
    return FakeRedis()


@pytest.fixture()
def gateway(fake_redis):
    return TaskGateway(redis=fake_redis, task_expiry=86400)


@pytest.fixture()
def cos_client():
    """COSClient with a mocked upload_file method."""
    config = COSConfig.disabled()
    client = COSClient(config)
    client.upload_file = MagicMock(return_value="https://cdn.example.com/wan22/inputs/test.png")
    return client


@pytest.fixture()
def app(fake_redis, gateway, cos_client):
    """Create a FastAPI app with injected test dependencies."""
    from api_gateway.dependencies import get_cos_client, get_gateway
    from api_gateway.main import create_app

    application = create_app()

    application.dependency_overrides[get_gateway] = lambda: gateway
    application.dependency_overrides[get_cos_client] = lambda: cos_client

    application.state.redis = fake_redis
    application.state.gateway = gateway
    application.state.cos_client = cos_client

    return application


@pytest_asyncio.fixture()
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ============================================================
# Tests
# ============================================================


class TestGenerateT2V:
    """Test basic T2V (text-to-video) generation requests."""

    @pytest.mark.asyncio
    async def test_generate_t2v_returns_task_id(self, client: AsyncClient):
        """POST /api/v1/generate with prompt returns task_id."""
        response = await client.post(
            "/api/v1/generate",
            data={
                "prompt": "a cat dancing",
                "model": "a14b",
                "mode": "t2v",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "task_id" in data
        assert data["status"] == "queued"

    @pytest.mark.asyncio
    async def test_generate_t2v_defaults(self, client: AsyncClient):
        """T2V with only prompt uses sensible defaults."""
        response = await client.post(
            "/api/v1/generate",
            data={"prompt": "sunset over ocean"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "task_id" in data
        assert data["status"] == "queued"


class TestGenerateTaskInQueue:
    """Verify that created tasks appear in the task list."""

    @pytest.mark.asyncio
    async def test_generate_task_in_queue(self, client: AsyncClient):
        """Created task appears in task list."""
        response = await client.post(
            "/api/v1/generate",
            data={"prompt": "test video"},
        )
        task_id = response.json()["task_id"]

        # Verify task exists via GET
        response2 = await client.get(f"/api/v1/tasks/{task_id}")
        assert response2.status_code == 200
        assert response2.json()["status"] == "queued"


class TestGenerateI2V:
    """Test I2V (image-to-video) mode validation."""

    @pytest.mark.asyncio
    async def test_generate_i2v_without_image_fails(self, client: AsyncClient):
        """I2V mode without image or workflow_json returns 400."""
        response = await client.post(
            "/api/v1/generate",
            data={
                "prompt": "animate this",
                "mode": "i2v",
            },
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_generate_i2v_with_workflow_json_ok(self, client: AsyncClient):
        """I2V mode with pre-built workflow_json does not require image."""
        workflow = {"nodes": {"1": {"class_type": "KSampler"}}}
        response = await client.post(
            "/api/v1/generate",
            data={
                "prompt": "animate this",
                "mode": "i2v",
                "workflow_json": json.dumps(workflow),
            },
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_generate_i2v_with_image(self, client: AsyncClient, cos_client):
        """I2V mode with image file uploads to COS and succeeds."""
        fake_image = BytesIO(b"fake png data")
        response = await client.post(
            "/api/v1/generate",
            data={
                "prompt": "animate this photo",
                "mode": "i2v",
            },
            files={"image": ("test.png", fake_image, "image/png")},
        )
        assert response.status_code == 200
        data = response.json()
        assert "task_id" in data
        assert data["status"] == "queued"
        # COS upload should have been called
        cos_client.upload_file.assert_called_once()


class TestGenerateWithWorkflowJson:
    """Test pre-built workflow JSON handling."""

    @pytest.mark.asyncio
    async def test_generate_with_workflow_json(self, client: AsyncClient):
        """Pre-built workflow JSON is accepted and used directly."""
        workflow = {"nodes": {"1": {"class_type": "KSampler"}}}
        response = await client.post(
            "/api/v1/generate",
            data={
                "prompt": "test",
                "workflow_json": json.dumps(workflow),
            },
        )
        assert response.status_code == 200
        assert "task_id" in response.json()

    @pytest.mark.asyncio
    async def test_generate_invalid_workflow_json(self, client: AsyncClient):
        """Invalid JSON string in workflow_json returns 400."""
        response = await client.post(
            "/api/v1/generate",
            data={
                "prompt": "test",
                "workflow_json": "not valid json {{{",
            },
        )
        assert response.status_code == 400


class TestGenerateCustomParams:
    """Test that custom parameters are stored correctly."""

    @pytest.mark.asyncio
    async def test_generate_custom_params(self, client: AsyncClient):
        """Custom width/height/steps are stored in params."""
        response = await client.post(
            "/api/v1/generate",
            data={
                "prompt": "test",
                "width": "1024",
                "height": "576",
                "steps": "30",
                "cfg": "7.5",
                "seed": "42",
            },
        )
        assert response.status_code == 200
        task_id = response.json()["task_id"]

        task = await client.get(f"/api/v1/tasks/{task_id}")
        assert task.status_code == 200
        params = task.json()["params"]
        assert params["width"] == 1024
        assert params["height"] == 576
        assert params["steps"] == 30
        assert params["cfg"] == 7.5
        assert params["seed"] == 42

    @pytest.mark.asyncio
    async def test_generate_default_params(self, client: AsyncClient):
        """Default parameters are used when not specified."""
        response = await client.post(
            "/api/v1/generate",
            data={"prompt": "test defaults"},
        )
        task_id = response.json()["task_id"]

        task = await client.get(f"/api/v1/tasks/{task_id}")
        params = task.json()["params"]
        assert params["width"] == 832
        assert params["height"] == 480
        assert params["num_frames"] == 81
        assert params["fps"] == 16
        assert params["steps"] == 20


class TestGenerateInputFiles:
    """Test that input file metadata is stored on the task."""

    @pytest.mark.asyncio
    async def test_input_files_stored_on_task(
        self, client: AsyncClient, fake_redis, cos_client,
    ):
        """When an image is uploaded, input_files metadata is stored in Redis."""
        fake_image = BytesIO(b"fake image bytes")
        response = await client.post(
            "/api/v1/generate",
            data={
                "prompt": "animate",
                "mode": "i2v",
            },
            files={"image": ("photo.png", fake_image, "image/png")},
        )
        assert response.status_code == 200
        task_id = response.json()["task_id"]

        # Check that input_files was stored in Redis
        raw = await fake_redis.hget(f"task:{task_id}", "input_files")
        assert raw is not None
        input_files = json.loads(raw)
        assert len(input_files) == 1
        assert "cos_url" in input_files[0]
        assert input_files[0]["original_filename"] == "photo.png"
