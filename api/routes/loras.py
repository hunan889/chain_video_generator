import yaml
import asyncio
import subprocess
import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from api.models.schemas import LoraInfo
from api.config import LORAS_PATH, COMFYUI_PATH, CIVITAI_API_TOKEN
from api.middleware.auth import verify_api_key
from api.services import civitai_client

logger = logging.getLogger(__name__)
router = APIRouter()
LORAS_DIR = COMFYUI_PATH / "models" / "loras"


@router.get("/loras", response_model=list[LoraInfo])
async def list_loras(_=Depends(verify_api_key)):
    try:
        with open(LORAS_PATH) as f:
            data = yaml.safe_load(f)
        loras = data.get("loras") or []
        return [LoraInfo(**l) for l in loras]
    except Exception:
        return []


class LoraDownloadRequest(BaseModel):
    url: str
    filename: str = ""
    token: str = ""
    civitai_version_id: int | None = None


_download_tasks: dict[str, dict] = {}


async def _resolve_filename(url: str, token: str) -> str:
    """Get filename from Content-Disposition header via HEAD/GET redirect."""
    import aiohttp
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                cd = resp.headers.get("Content-Disposition", "")
                if "filename=" in cd:
                    # Parse filename from: attachment; filename="xxx.safetensors"
                    for part in cd.split(";"):
                        part = part.strip()
                        if part.startswith("filename="):
                            name = part.split("=", 1)[1].strip().strip('"')
                            if name:
                                return name
                # Fallback: use last part of final URL
                path = str(resp.url).split("?")[0].rstrip("/")
                name = path.split("/")[-1]
                if name and "." in name:
                    return name
    except Exception as e:
        logger.warning("Failed to resolve filename: %s", e)
    return ""


@router.post("/loras/download")
async def download_lora(req: LoraDownloadRequest, _=Depends(verify_api_key)):
    import uuid
    dl_id = uuid.uuid4().hex[:8]

    # Resolve filename if not provided
    filename = req.filename.strip()
    if not filename:
        token = req.token or CIVITAI_API_TOKEN or ""
        filename = await _resolve_filename(req.url, token)
    if not filename:
        raise HTTPException(400, "无法自动获取文件名，请手动输入")
    if not filename.endswith(".safetensors"):
        filename += ".safetensors"

    _download_tasks[dl_id] = {"status": "downloading", "filename": filename, "error": ""}

    async def _do_download():
        try:
            dest = str(LORAS_DIR / filename)
            token = req.token or CIVITAI_API_TOKEN or ""
            url = req.url
            cmd = ["curl", "-sL", "-o", dest]
            if token:
                cmd += ["-H", f"Authorization: Bearer {token}"]
            cmd.append(url)
            proc = await asyncio.create_subprocess_exec(*cmd)
            await proc.wait()
            if proc.returncode == 0:
                import os
                size = os.path.getsize(dest)
                if size > 1000000:  # > 1MB
                    _download_tasks[dl_id]["status"] = "completed"
                    # Register with CivitAI metadata if version_id provided
                    if req.civitai_version_id:
                        try:
                            from api.routes.civitai import _register_lora
                            version_data = await civitai_client.get_version(req.civitai_version_id)
                            model_id = version_data.get("modelId")
                            model_data = await civitai_client.get_model(model_id) if model_id else {}
                            entry = _register_lora(model_data, version_data, filename)
                            _download_tasks[dl_id]["lora_entry"] = entry
                        except Exception as e:
                            logger.warning("Failed to register LoRA metadata: %s", e)
                else:
                    _download_tasks[dl_id]["status"] = "failed"
                    _download_tasks[dl_id]["error"] = f"文件太小 ({size} bytes)，可能下载失败"
            else:
                _download_tasks[dl_id]["status"] = "failed"
                _download_tasks[dl_id]["error"] = f"curl exit code {proc.returncode}"
        except Exception as e:
            _download_tasks[dl_id]["status"] = "failed"
            _download_tasks[dl_id]["error"] = str(e)

    asyncio.create_task(_do_download())
    return {"download_id": dl_id, "status": "downloading", "filename": filename}


@router.get("/loras/download/{dl_id}")
async def get_download_status(dl_id: str, _=Depends(verify_api_key)):
    if dl_id not in _download_tasks:
        raise HTTPException(404, "Download not found")
    return _download_tasks[dl_id]


@router.get("/loras/files")
async def list_lora_files(_=Depends(verify_api_key)):
    """List actual LoRA files on disk."""
    files = []
    for f in sorted(LORAS_DIR.glob("*.safetensors")):
        size_mb = f.stat().st_size / (1024 * 1024)
        files.append({"name": f.name, "size_mb": round(size_mb, 1)})
    return files
