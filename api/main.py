import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path
from api.config import API_HOST, API_PORT, VIDEOS_DIR, UPLOADS_DIR
from api.services.task_manager import TaskManager
from api.routes import generate, generate_i2v, tasks, loras, civitai, prompt, lora_recommend, extend, workflow, tts, postprocess, image, chat, resources, lora_admin, search, recommend, embeddings, resource_admin

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

STATIC_DIR = Path(__file__).parent / "static"


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/annotate.html")
async def annotate():
    return FileResponse(STATIC_DIR / "annotate.html")


@app.get("/search.html")
async def search():
    return FileResponse(STATIC_DIR / "search.html")


@app.get("/favorites.html")
async def favorites():
    return FileResponse(STATIC_DIR / "favorites.html")


@app.get("/lora_manager.html")
async def lora_manager():
    return FileResponse(STATIC_DIR / "lora_manager.html")


@app.get("/search_debug.html")
async def search_debug():
    return FileResponse(STATIC_DIR / "search_debug.html")


@app.get("/advanced_workflow.html")
async def advanced_workflow():
    return FileResponse(STATIC_DIR / "advanced_workflow.html")


@app.get("/advanced_workflow_v2.html")
async def advanced_workflow_v2():
    return FileResponse(STATIC_DIR / "advanced_workflow_v2.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


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
