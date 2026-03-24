import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from api.middleware.auth import verify_api_key

logger = logging.getLogger(__name__)
router = APIRouter()


class AddWorkerRequest(BaseModel):
    model_key: str
    url: str


def _get_task_manager():
    from api.main import task_manager
    return task_manager


@router.get("/admin/workers", dependencies=[Depends(verify_api_key)])
async def list_workers():
    """List all active ComfyUI workers with status."""
    tm = _get_task_manager()
    workers = await tm.list_workers()
    return {"workers": workers}


@router.post("/admin/workers", dependencies=[Depends(verify_api_key)])
async def add_worker(req: AddWorkerRequest):
    """Add a new ComfyUI worker at runtime."""
    tm = _get_task_manager()
    try:
        worker_id = await tm.add_worker(req.model_key, req.url)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"worker_id": worker_id, "status": "added"}


@router.delete("/admin/workers/{worker_id}", dependencies=[Depends(verify_api_key)])
async def remove_worker(worker_id: str):
    """Remove a ComfyUI worker."""
    tm = _get_task_manager()
    removed = await tm.remove_worker(worker_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Worker {worker_id} not found")
    return {"worker_id": worker_id, "status": "removed"}
