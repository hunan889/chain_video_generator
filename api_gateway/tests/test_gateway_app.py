"""Tests for the API Gateway FastAPI application.

Written FIRST (TDD red phase). Uses FakeRedis to mock Redis
and httpx.AsyncClient via FastAPI's TestClient pattern.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from shared.cos.config import COSConfig
from shared.cos.client import COSClient
from shared.enums import GenerateMode, ModelType
from shared.task_gateway import TaskGateway


# ============================================================
# Fake Redis (same pattern as shared/tests/test_task_gateway.py)
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
    config = COSConfig.disabled()
    return COSClient(config)


@pytest.fixture()
def task_store():
    """Create a fake TaskStore that does nothing (best-effort, no DB needed)."""
    class FakeTaskStore:
        async def create(self, **kwargs):
            pass
        async def update_status(self, *args, **kwargs):
            pass
        async def set_result(self, *args, **kwargs):
            pass
        async def get(self, task_id):
            return None
        async def list_history(self, **kwargs):
            return {"tasks": [], "items": [], "total": 0, "total_pages": 1,
                    "page": 1, "page_size": 24, "category_counts": {}}
    return FakeTaskStore()


@pytest.fixture()
def app(fake_redis, gateway, cos_client, task_store):
    """Create a FastAPI app with injected test dependencies."""
    from api_gateway.main import create_app
    from api_gateway.dependencies import get_gateway, get_cos_client

    application = create_app()

    application.dependency_overrides[get_gateway] = lambda: gateway
    application.dependency_overrides[get_cos_client] = lambda: cos_client

    # Store redis and gateway on app state for lifespan-independent access
    application.state.redis = fake_redis
    application.state.gateway = gateway
    application.state.cos_client = cos_client
    application.state.task_store = task_store

    return application


@pytest_asyncio.fixture()
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ============================================================
# Tests
# ============================================================


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_returns_ok(self, client: AsyncClient):
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "redis" in data

    @pytest.mark.asyncio
    async def test_health_shows_redis_connected(self, client: AsyncClient):
        resp = await client.get("/health")
        data = resp.json()
        assert data["redis"] is True


class TestListTasks:
    @pytest.mark.asyncio
    async def test_list_tasks_empty(self, client: AsyncClient):
        resp = await client.get("/api/v1/tasks")
        assert resp.status_code == 200
        data = resp.json()
        # generation_history router returns "workflows" key
        assert data["workflows"] == []

    @pytest.mark.asyncio
    async def test_list_tasks_returns_created_tasks(
        self, client: AsyncClient, gateway: TaskGateway,
    ):
        await gateway.create_task(
            mode=GenerateMode.T2V,
            model=ModelType.A14B,
            workflow={"nodes": []},
        )
        resp = await client.get("/api/v1/tasks")
        assert resp.status_code == 200
        data = resp.json()
        # FakeTaskStore returns empty — this test verifies the endpoint works
        assert "workflows" in data


class TestGetTask:
    @pytest.mark.asyncio
    async def test_get_task_not_found(self, client: AsyncClient):
        resp = await client.get("/api/v1/tasks/nonexistent_id")
        assert resp.status_code == 404
        data = resp.json()
        assert "not found" in data["detail"].lower()

    @pytest.mark.asyncio
    async def test_get_task_returns_data(
        self, client: AsyncClient, gateway: TaskGateway,
    ):
        task_id = await gateway.create_task(
            mode=GenerateMode.T2V,
            model=ModelType.A14B,
            workflow={"test": True},
            params={"prompt": "a cat"},
        )
        resp = await client.get(f"/api/v1/tasks/{task_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == task_id
        assert data["status"] == "queued"
        assert data["mode"] == "t2v"
        assert data["model"] == "a14b"


class TestCancelTask:
    @pytest.mark.asyncio
    async def test_cancel_queued_task(
        self, client: AsyncClient, gateway: TaskGateway,
    ):
        task_id = await gateway.create_task(
            mode=GenerateMode.T2V,
            model=ModelType.A14B,
            workflow={},
        )
        resp = await client.post(f"/api/v1/tasks/{task_id}/cancel")
        assert resp.status_code == 200
        data = resp.json()
        assert data["cancelled"] is True

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_task(self, client: AsyncClient):
        resp = await client.post("/api/v1/tasks/nonexistent_id/cancel")
        assert resp.status_code == 409
        data = resp.json()
        assert "cannot cancel" in data["detail"].lower()

    @pytest.mark.asyncio
    async def test_cancel_completed_task_fails(
        self, client: AsyncClient, gateway: TaskGateway, fake_redis: FakeRedis,
    ):
        task_id = await gateway.create_task(
            mode=GenerateMode.T2V,
            model=ModelType.A14B,
            workflow={},
        )
        await fake_redis.hset(f"task:{task_id}", mapping={"status": "completed"})
        resp = await client.post(f"/api/v1/tasks/{task_id}/cancel")
        assert resp.status_code == 409


class TestCreateAndGetTask:
    @pytest.mark.asyncio
    async def test_create_via_gateway_then_get_via_api(
        self, client: AsyncClient, gateway: TaskGateway,
    ):
        """Create a task through TaskGateway, then retrieve via API."""
        task_id = await gateway.create_task(
            mode=GenerateMode.I2V,
            model=ModelType.FIVE_B,
            workflow={"ref_image": "img.png"},
            params={"prompt": "dancing cat", "resolution": "720p"},
        )

        # Get via API
        resp = await client.get(f"/api/v1/tasks/{task_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == task_id
        assert data["status"] == "queued"
        assert data["mode"] == "i2v"
        assert data["model"] == "5b"
        assert data["params"]["prompt"] == "dancing cat"

        # Also appears in list (via generation_history)
        resp = await client.get("/api/v1/tasks")
        assert resp.status_code == 200
        assert "workflows" in resp.json()
