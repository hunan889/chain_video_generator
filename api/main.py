import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path
from api.config import API_HOST, API_PORT, VIDEOS_DIR, UPLOADS_DIR
from api.services.task_manager import TaskManager
from api.routes import generate, generate_i2v, tasks, loras, civitai, prompt, lora_recommend, extend, workflow

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

STATIC_DIR = Path(__file__).parent / "static"


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


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
