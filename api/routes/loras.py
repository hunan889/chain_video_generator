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


async def _add_civitai_id_to_file(file_path: str, civitai_id: int, civitai_version_id: int):
    """Add CivitAI IDs to a safetensors file's metadata."""
    try:
        import safetensors.torch
        import torch
        from pathlib import Path
        import shutil

        file_path_obj = Path(file_path)

        # Load existing tensors and metadata
        tensors = {}
        metadata = {}

        with safetensors.torch.safe_open(file_path_obj, framework="pt") as f:
            # Copy existing metadata
            if f.metadata():
                metadata = dict(f.metadata())

            # Load all tensors
            for key in f.keys():
                tensors[key] = f.get_tensor(key)

        # Add CivitAI IDs to metadata
        metadata["civitai_model_id"] = str(civitai_id)
        metadata["civitai_version_id"] = str(civitai_version_id)

        # Create backup
        backup_path = file_path_obj.with_suffix('.safetensors.bak')
        shutil.copy2(file_path_obj, backup_path)

        # Save with updated metadata
        safetensors.torch.save_file(tensors, file_path_obj, metadata=metadata)

        # Remove backup if successful
        backup_path.unlink()

        logger.info(f"Added CivitAI ID {civitai_id}/{civitai_version_id} to {file_path_obj.name}")
    except Exception as e:
        logger.warning(f"Failed to add CivitAI ID to file: {e}")
        # Restore from backup if it exists
        backup_path = Path(file_path).with_suffix('.safetensors.bak')
        if backup_path.exists():
            shutil.copy2(backup_path, file_path)
            backup_path.unlink()



@router.get("/loras", response_model=list[LoraInfo])
async def list_loras(_=Depends(verify_api_key)):
    try:
        with open(LORAS_PATH) as f:
            data = yaml.safe_load(f)
        loras = data.get("loras") or []
        # Deduplicate by name: merge HIGH/LOW variants into one entry
        seen: dict[str, dict] = {}
        for l in loras:
            name = l.get("name", "")
            if name in seen:
                # Merge: keep the entry with more trigger_words or preview_url
                existing = seen[name]
                if not existing.get("preview_url") and l.get("preview_url"):
                    existing["preview_url"] = l["preview_url"]
                for tw in l.get("trigger_words", []):
                    if tw not in existing.get("trigger_words", []):
                        existing.setdefault("trigger_words", []).append(tw)
            else:
                seen[name] = dict(l)
        return [LoraInfo(**v) for v in seen.values()]
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

                    # Add CivitAI ID to file metadata if version_id provided
                    if req.civitai_version_id:
                        try:
                            version_data = await civitai_client.get_version(req.civitai_version_id)
                            model_id = version_data.get("modelId")

                            # Add ID to file metadata
                            await _add_civitai_id_to_file(dest, model_id, req.civitai_version_id)

                            # Register with loras.yaml
                            model_data = await civitai_client.get_model(model_id) if model_id else {}
                            from api.routes.civitai import _register_lora
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
