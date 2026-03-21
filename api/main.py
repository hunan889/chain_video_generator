import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from pathlib import Path
from urllib.parse import urlparse
from api.config import API_HOST, API_PORT, VIDEOS_DIR, UPLOADS_DIR
from api.services.task_manager import TaskManager
from api.routes import generate, generate_i2v, tasks, loras, civitai, prompt, lora_recommend, extend, workflow, tts, postprocess, image, chat, resources, lora_admin, search, recommend, embeddings, resource_admin, pose_images, poses, pose_admin, pose_synonyms_admin

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

task_manager = TaskManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    await task_manager.start()
    logger.info("Wan2.2 Video Service started")
    yield
    await task_manager.stop()
    logger.info("Wan2.2 Video Service stopped")


app = FastAPI(title="Wan2.2 Video Generation API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(generate.router, prefix="/api/v1", tags=["generate"])
app.include_router(generate_i2v.router, prefix="/api/v1", tags=["generate"])
app.include_router(tasks.router, prefix="/api/v1", tags=["tasks"])
app.include_router(loras.router, prefix="/api/v1", tags=["loras"])
app.include_router(civitai.router, prefix="/api/v1", tags=["civitai"])
app.include_router(prompt.router, prefix="/api/v1", tags=["prompt"])
app.include_router(lora_recommend.router, prefix="/api/v1", tags=["loras"])
app.include_router(extend.router, prefix="/api/v1", tags=["generate"])
app.include_router(workflow.router, prefix="/api/v1", tags=["workflow"])
app.include_router(tts.router, prefix="/api/v1", tags=["tts"])
app.include_router(postprocess.router, prefix="/api/v1", tags=["postprocess"])
app.include_router(image.router, prefix="/api/v1", tags=["image"])
app.include_router(chat.router, prefix="/api/v1", tags=["chat"])
app.include_router(lora_admin.router, prefix="/api/v1", tags=["lora_admin"])
app.include_router(resource_admin.router, prefix="/api/v1", tags=["resource_admin"])
app.include_router(search.router, prefix="/api/v1", tags=["search"])
app.include_router(recommend.router, prefix="/api/v1", tags=["recommend"])
app.include_router(embeddings.router, prefix="/api/v1", tags=["embeddings"])
app.include_router(resources.router)
app.include_router(pose_images.router)
app.include_router(poses.router, prefix="/api/v1", tags=["poses"])
app.include_router(pose_admin.router, prefix="/api/v1", tags=["pose_admin"])
app.include_router(pose_synonyms_admin.router)

STATIC_DIR = Path(__file__).parent / "static"


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/annotate.html")
async def annotate():
    response = FileResponse(STATIC_DIR / "annotate.html")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.get("/lora_manager.html")
async def lora_manager():
    return FileResponse(STATIC_DIR / "lora_manager.html")


@app.get("/advanced_workflow_v2.html")
async def advanced_workflow_v2():
    return FileResponse(STATIC_DIR / "advanced_workflow_v2.html")


@app.get("/workflow_test.html")
async def workflow_test():
    return FileResponse(STATIC_DIR / "workflow_test.html")


@app.get("/pose_preview.html")
async def pose_preview():
    return FileResponse(STATIC_DIR / "pose_preview.html")


@app.get("/pose_manager.html")
async def pose_manager():
    return FileResponse(STATIC_DIR / "pose_manager.html")


@app.get("/pose_recommend_test.html")
async def pose_recommend_test():
    return FileResponse(STATIC_DIR / "pose_recommend_test.html")


@app.get("/pose_synonyms_admin.html")
async def pose_synonyms_admin():
    return FileResponse(STATIC_DIR / "pose_synonyms_admin.html")


@app.get("/keywords_editor.html")
async def keywords_editor():
    return FileResponse(STATIC_DIR / "keywords_editor.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")
app.mount("/api/v1/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="api_uploads")
app.mount("/api/v1/results", StaticFiles(directory=str(VIDEOS_DIR)), name="api_results")


ALLOWED_PROXY_HOSTS = {
    "image.civitai.com",
    "cdn.imagime.co",
    "imagime.co",
    "civitai.com",
    "image.civitai.com",
}


@app.get("/api/v1/proxy-media")
async def proxy_media(url: str = Query(...)):
    """Proxy external media (images/videos) to avoid browser PNA CORS issues."""
    import aiohttp
    parsed = urlparse(url)
    if parsed.hostname not in ALLOWED_PROXY_HOSTS:
        raise HTTPException(status_code=403, detail="Host not allowed")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                content = await resp.read()
                content_type = resp.headers.get("Content-Type", "application/octet-stream")
                return Response(content=content, media_type=content_type)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/health")
async def health():
    from api.models.schemas import HealthResponse
    a14b_ok = await task_manager.clients.get("a14b", _Stub()).is_alive()
    five_b_ok = await task_manager.clients.get("5b", _Stub()).is_alive()
    redis_ok = await task_manager.redis_alive()
    return HealthResponse(status="ok", comfyui_a14b=a14b_ok, comfyui_5b=five_b_ok, redis=redis_ok)


@app.get("/api/v1/model-presets")
async def list_model_presets():
    from api.services.workflow_builder import get_model_presets
    return get_model_presets()


@app.get("/api/v1/t5-presets")
async def list_t5_presets():
    from api.services.workflow_builder import get_t5_presets
    return get_t5_presets()


class _Stub:
    async def is_alive(self):
        return False


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host=API_HOST, port=API_PORT, reload=False)
