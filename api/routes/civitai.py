import re
import json
import asyncio
import logging
import yaml
from fastapi import APIRouter, Depends, HTTPException
from api.models.schemas import (
    CivitAISearchResponse, CivitAIModelResult, CivitAIDownloadRequest,
)
from api.services import civitai_client
from api.config import LORAS_PATH, COMFYUI_PATH, CIVITAI_API_TOKEN
from api.middleware.auth import verify_api_key
from api.utils.lora_naming import normalize_lora_name

DB_CONFIG = {
    'host': 'use-cdb-b9nvte6o.sql.tencentcdb.com',
    'port': 20603,
    'user': 'user_soga',
    'password': '1IvO@*#68',
    'database': 'tudou_soga',
    'charset': 'utf8mb4',
}


def _sync_entry_to_db(entry: dict):
    """Sync a loras.yaml entry into MySQL lora_metadata (best-effort)."""
    try:
        import pymysql
        file_lower = entry.get('file', '').lower()
        if 'high' in file_lower:
            noise_stage = 'high'
        elif 'low' in file_lower:
            noise_stage = 'low'
        else:
            noise_stage = 'single'
        combined = (entry.get('name', '') + ' ' + entry.get('file', '')).lower()
        if 'i2v' in combined and 't2v' not in combined:
            mode = 'I2V'
        elif 't2v' in combined and 'i2v' not in combined:
            mode = 'T2V'
        else:
            mode = 'both'
        conn = pymysql.connect(**DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO lora_metadata
                (name, file, description, tags, trigger_words, preview_url, civitai_id, enabled, mode, noise_stage)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 1, %s, %s)
            ON DUPLICATE KEY UPDATE
                name=VALUES(name), preview_url=VALUES(preview_url),
                trigger_words=VALUES(trigger_words), mode=VALUES(mode)
        """, (
            entry.get('name', ''),
            entry.get('file', ''),
            (entry.get('description', '') or '')[:2000],
            json.dumps(entry.get('tags', []) or []),
            json.dumps(entry.get('trigger_words', []) or []),
            entry.get('preview_url', ''),
            entry.get('civitai_id'),
            mode,
            noise_stage,
        ))
        conn.commit()
        cursor.close()
        conn.close()
        logger.info('Synced LoRA to DB: %s', entry.get('name'))
    except Exception as e:
        logger.warning('Failed to sync LoRA to DB: %s', e)

logger = logging.getLogger(__name__)
router = APIRouter()
LORAS_DIR = COMFYUI_PATH / "models" / "loras"

_download_tasks: dict[str, dict] = {}


@router.get("/civitai/search", response_model=CivitAISearchResponse)
async def search_civitai(
    query: str = "wan 2.1", limit: int = 100, cursor: str = "",
    nsfw: bool = True, sort: str = "Most Downloaded", base_model: str = "",
    _=Depends(verify_api_key),
):
    try:
        raw = await civitai_client.search_loras(query, limit, cursor, nsfw, sort, base_model)
    except Exception as e:
        raise HTTPException(502, f"CivitAI API error: {e}")
    items = [civitai_client.extract_model_result(m) for m in raw.get("items", [])]
    metadata = raw.get("metadata", {})
    return CivitAISearchResponse(
        items=items,
        next_cursor=metadata.get("nextCursor", ""),
    )


@router.get("/civitai/models/{model_id}", response_model=CivitAIModelResult)
async def get_civitai_model(model_id: int, _=Depends(verify_api_key)):
    try:
        raw = await civitai_client.get_model(model_id)
    except Exception as e:
        raise HTTPException(502, f"CivitAI API error: {e}")
    return civitai_client.extract_model_result(raw)


def _sanitize_name(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9\s]", "", name).strip().lower()
    return re.sub(r"\s+", "_", s)


def _register_lora(model_data: dict, version_data: dict, filename: str):
    """Register downloaded LoRA into loras.yaml with CivitAI metadata."""
    try:
        with open(LORAS_PATH) as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        data = {}
    loras = data.get("loras", [])

    version_id = version_data.get("id")
    # Strip HIGH/LOW variant suffixes to get base name for loras.yaml
    file_base = filename.replace(".safetensors", "")
    file_base = normalize_lora_name(file_base)

    # Dedup by civitai_version_id or file base name — update existing if found
    for existing in loras:
        if existing.get("civitai_version_id") == version_id or existing.get("file") == file_base:
            # Update file to base name in case it was stored with variant suffix
            existing["file"] = file_base
            with open(LORAS_PATH, "w") as f:
                yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
            return existing

    trained_words = version_data.get("trainedWords", []) or []
    model_tags = model_data.get("tags", []) or []
    preview_url = None
    images = version_data.get("images", [])
    if images:
        preview_url = images[0].get("url")

    # Extract unique example prompts from CivitAI image metadata
    example_prompts = []
    seen = set()
    for img in images[:10]:
        meta = img.get("meta") or {}
        p = (meta.get("prompt") or "").strip()
        if p and p not in seen:
            seen.add(p)
            example_prompts.append(p)
        if len(example_prompts) >= 3:
            break

    entry = {
        "name": _sanitize_name(model_data.get("name", file_base)),
        "file": file_base,
        "description": civitai_client._strip_html(model_data.get("description", ""), 200),
        "default_strength": 0.8,
        "trigger_words": trained_words,
        "example_prompts": example_prompts,
        "tags": model_tags,
        "civitai_id": model_data.get("id"),
        "civitai_version_id": version_id,
        "preview_url": preview_url,
    }
    loras.append(entry)
    data["loras"] = loras
    with open(LORAS_PATH, "w") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
    _sync_entry_to_db(entry)
    return entry


@router.post("/civitai/download")
async def download_from_civitai(req: CivitAIDownloadRequest, _=Depends(verify_api_key)):
    import uuid

    try:
        model_data = await civitai_client.get_model(req.model_id)
    except Exception as e:
        raise HTTPException(502, f"CivitAI API error: {e}")

    # Resolve version data
    if req.version_id:
        try:
            version_data = await civitai_client.get_version(req.version_id)
        except Exception as e:
            raise HTTPException(502, f"CivitAI API error: {e}")
    else:
        # Try to find version from download_url
        version_data = {}
        if req.download_url:
            for mv in model_data.get("modelVersions", []):
                for fi in mv.get("files", []):
                    if fi.get("downloadUrl") == req.download_url:
                        version_data = mv
                        break
                if version_data:
                    break
        if not version_data:
            raise HTTPException(400, "version_id or valid download_url required")

    # Collect all safetensors files to download
    files_to_dl = []
    for fi in version_data.get("files", []):
        fname = fi.get("name", "")
        dl_url = fi.get("downloadUrl", "")
        if fname.endswith(".safetensors") and dl_url:
            files_to_dl.append({"filename": fname, "url": dl_url})

    # If single download_url provided and no version files matched, use it directly
    if not files_to_dl and req.download_url:
        filename = req.filename.strip()
        if not filename:
            fname_part = req.download_url.split("/")[-1].split("?")[0]
            filename = fname_part if fname_part and "." in fname_part else f"civitai_{req.model_id}.safetensors"
        if not filename.endswith(".safetensors"):
            filename += ".safetensors"
        files_to_dl = [{"filename": filename, "url": req.download_url}]

    if not files_to_dl:
        raise HTTPException(400, "No downloadable safetensors files found")

    dl_id = uuid.uuid4().hex[:8]
    filenames = [f["filename"] for f in files_to_dl]
    _download_tasks[dl_id] = {"status": "downloading", "filenames": filenames, "error": "", "completed": []}

    async def _do_download():
        try:
            for fi in files_to_dl:
                dest = str(LORAS_DIR / fi["filename"])
                # CivitAI requires token as query param for downloads
                url = fi["url"]
                if CIVITAI_API_TOKEN:
                    sep = "&" if "?" in url else "?"
                    url = f"{url}{sep}token={CIVITAI_API_TOKEN}"
                cmd = ["curl", "-sL", "-o", dest, url]
                proc = await asyncio.create_subprocess_exec(*cmd)
                await proc.wait()
                if proc.returncode == 0:
                    import os
                    size = os.path.getsize(dest)
                    if size > 1_000_000:
                        _download_tasks[dl_id]["completed"].append(fi["filename"])
                    else:
                        _download_tasks[dl_id]["status"] = "failed"
                        _download_tasks[dl_id]["error"] = f"{fi['filename']}: too small ({size} bytes), auth may have failed"
                        return
                else:
                    _download_tasks[dl_id]["status"] = "failed"
                    _download_tasks[dl_id]["error"] = f"{fi['filename']}: curl exit code {proc.returncode}"
                    return
            # All files downloaded — register once with base name
            entry = _register_lora(model_data, version_data, files_to_dl[0]["filename"])
            _download_tasks[dl_id]["status"] = "completed"
            _download_tasks[dl_id]["lora_entry"] = entry
        except Exception as e:
            _download_tasks[dl_id]["status"] = "failed"
            _download_tasks[dl_id]["error"] = str(e)

    asyncio.create_task(_do_download())
    return {"download_id": dl_id, "status": "downloading", "filenames": filenames}


@router.get("/civitai/download/{dl_id}")
async def get_civitai_download_status(dl_id: str, _=Depends(verify_api_key)):
    if dl_id not in _download_tasks:
        raise HTTPException(404, "Download not found")
    return _download_tasks[dl_id]


@router.post("/civitai/sync-examples")
async def sync_lora_examples(_=Depends(verify_api_key)):
    """Fetch example prompts from CivitAI for all LoRAs that have civitai_version_id but no example_prompts."""
    try:
        with open(LORAS_PATH) as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        raise HTTPException(404, "loras.yaml not found")
    loras = data.get("loras", [])
    updated = []
    for entry in loras:
        vid = entry.get("civitai_version_id")
        if not vid or entry.get("example_prompts"):
            continue
        try:
            version_data = await civitai_client.get_version(vid)
            images = version_data.get("images", [])
            prompts = []
            seen = set()
            for img in images[:10]:
                meta = img.get("meta") or {}
                p = (meta.get("prompt") or "").strip()
                if p and p not in seen:
                    seen.add(p)
                    prompts.append(p)
                if len(prompts) >= 3:
                    break
            if prompts:
                entry["example_prompts"] = prompts
                updated.append(entry["name"])
        except Exception as e:
            logger.warning("Failed to fetch examples for %s: %s", entry.get("name"), e)
    if updated:
        with open(LORAS_PATH, "w") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
    return {"updated": updated, "count": len(updated)}
