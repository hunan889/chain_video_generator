from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from api.models.schemas import TaskResponse
from api.middleware.auth import verify_api_key
from api.services import storage

router = APIRouter()


@router.get("/tasks", response_model=list[TaskResponse])
async def list_tasks(_=Depends(verify_api_key)):
    from api.main import task_manager
    return await task_manager.list_tasks()


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str, _=Depends(verify_api_key)):
    from api.main import task_manager

    task = await task_manager.get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return TaskResponse(**task)


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, _=Depends(verify_api_key)):
    from api.main import task_manager

    ok = await task_manager.cancel_task(task_id)
    if not ok:
        raise HTTPException(400, "Task cannot be cancelled")
    return {"status": "cancelled", "task_id": task_id}


@router.get("/results/{filename}")
async def get_result(filename: str):
    path = storage.get_video_path(filename)
    if not path:
        raise HTTPException(404, "File not found")
    return FileResponse(path, media_type="video/mp4", filename=filename)
