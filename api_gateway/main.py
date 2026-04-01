"""API Gateway — minimal FastAPI application.

Serves as the public entry point for the video generation service.
Has NO dependency on ComfyUI or GPU resources.
"""

import logging
import os
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from api_gateway.config import GatewayConfig, load_config
from api_gateway.dependencies import get_cos_client, get_gateway
from api_gateway.services.chain_orchestrator import ChainOrchestrator
from api_gateway.services.task_store import TaskStore
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

    # Configure WorkflowBuilder paths before any route handler runs
    if config.workflows_dir:
        from shared.workflow_builder import configure as _wb_configure
        _wb_configure(workflows_dir=config.workflows_dir)
        logger.info("WorkflowBuilder configured with workflows_dir=%s", config.workflows_dir)
    else:
        logger.warning(
            "WORKFLOWS_DIR not set -- WorkflowBuilder will use default paths. "
            "Set WORKFLOWS_DIR env var to point to the workflows/ directory."
        )

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

    chain_orchestrator = ChainOrchestrator(
        gateway=gateway,
        redis=redis_conn,
        vision_api_key=config.vision_api_key,
        vision_base_url=config.vision_base_url,
        vision_model=config.vision_model,
        llm_api_key=config.llm_api_key,
        llm_base_url=config.llm_base_url,
        llm_model=config.llm_model,
    )

    # TaskStore — MySQL-backed persistent task storage (best-effort)
    task_store = TaskStore(config)
    gateway.task_store = task_store  # hook for auto-write on create_task

    # Workflow Engine — advanced 4-stage pipeline
    from api_gateway.services.workflow_engine import WorkflowEngine
    workflow_engine = WorkflowEngine(
        gateway=gateway, redis=redis_conn, config=config,
        cos_client=cos_client, task_store=task_store,
    )

    app.state.redis = redis_conn
    app.state.gateway = gateway
    app.state.cos_client = cos_client
    app.state.chain_orchestrator = chain_orchestrator
    app.state.task_store = task_store
    app.state.workflow_engine = workflow_engine

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
    from api_gateway.routes.admin import router as admin_router
    from api_gateway.routes.loras import router as loras_router
    from api_gateway.routes.postprocess import router as postprocess_router
    from api_gateway.routes.extend import router as extend_router
    from api_gateway.routes.thirdparty import router as thirdparty_router
    from api_gateway.routes.prompt import router as prompt_router
    from api_gateway.routes.civitai import router as civitai_router
    from api_gateway.routes.workflow_advanced import router as workflow_advanced_router
    from api_gateway.routes.presets import router as presets_router
    from api_gateway.routes.poses import router as poses_router
    from api_gateway.routes.pose_admin import router as pose_admin_router
    from api_gateway.routes.pose_synonyms import router as pose_synonyms_router
    from api_gateway.routes.generation_history import router as generation_history_router
    from api_gateway.routes.proxy import router as proxy_router

    app.include_router(generate_router)
    app.include_router(chain_router)
    app.include_router(admin_router)
    app.include_router(loras_router)
    app.include_router(postprocess_router)
    app.include_router(extend_router)
    app.include_router(thirdparty_router)
    app.include_router(prompt_router)
    app.include_router(civitai_router)
    app.include_router(workflow_advanced_router)
    app.include_router(presets_router)
    app.include_router(poses_router)
    app.include_router(pose_admin_router)
    app.include_router(pose_synonyms_router)
    app.include_router(generation_history_router)

    # ------------------------------------------------------------------
    # Inline routes as a router (must be registered BEFORE proxy catch-all)
    # ------------------------------------------------------------------
    from fastapi import APIRouter as _AR
    _core = _AR(tags=["core"])

    @_core.get("/api/v1/tasks/{task_id}")
    async def get_task(task_id: str, gw: TaskGateway = Depends(get_gateway)):
        """Get a single task by ID."""
        task = await gw.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
        return task

    @_core.post("/api/v1/tasks/{task_id}/cancel")
    async def cancel_task(task_id: str, gw: TaskGateway = Depends(get_gateway)):
        """Cancel a queued task."""
        success = await gw.cancel_queued_task(task_id)
        if not success:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot cancel task {task_id} (not found or not queued)",
            )
        return {"cancelled": True, "task_id": task_id}

    app.include_router(_core)
    # proxy_router MUST be last — it has a catch-all /api/v1/{path:path} route
    app.include_router(proxy_router)

    # ------------------------------------------------------------------
    # Static files (UI)
    # ------------------------------------------------------------------
    _static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "api", "static")
    if os.path.isdir(_static_dir):
        app.mount("/static", StaticFiles(directory=_static_dir), name="static")
        logger.info("Serving static files from %s", _static_dir)

    # ------------------------------------------------------------------
    # Root-level HTML fallback (iframe pages reference /*.html without /static/)
    # ------------------------------------------------------------------
    if os.path.isdir(_static_dir):
        from fastapi.responses import FileResponse

        @app.get("/{filename:path}.html", include_in_schema=False)
        async def root_html_fallback(filename: str):
            """Serve HTML files at root level for iframe compatibility."""
            filepath = os.path.join(_static_dir, f"{filename}.html")
            if os.path.isfile(filepath):
                return FileResponse(filepath)
            raise HTTPException(status_code=404, detail="Not found")

    # ------------------------------------------------------------------
    # Non-API routes (after proxy, no /api/v1/ prefix conflict)
    # ------------------------------------------------------------------

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse(url="/static/index.html")

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

    return app


# Default app instance for `uvicorn api_gateway.main:app`
app = create_app()
