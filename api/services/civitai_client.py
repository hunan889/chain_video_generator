import re
import asyncio
import logging
import aiohttp
from typing import Optional
from api.config import CIVITAI_API_TOKEN
from api.models.schemas import CivitAIModelResult, CivitAIModelVersion, CivitAIFile

logger = logging.getLogger(__name__)

BASE_URL = "https://civitai.com/api/v1"
MEILI_URL = "https://search.civitai.com"
MEILI_KEY = "8c46eb2508e21db1e9828a97968d91ab1ca1caa5f70a00e88a2ba1e286603b61"
TIMEOUT = aiohttp.ClientTimeout(total=15)
MEILI_TIMEOUT = aiohttp.ClientTimeout(total=10)


def _headers() -> dict:
    h = {"Content-Type": "application/json", "Accept-Encoding": "gzip, deflate"}
    if CIVITAI_API_TOKEN:
        h["Authorization"] = f"Bearer {CIVITAI_API_TOKEN}"
    return h


def _strip_html(text: str, max_len: int = 500) -> str:
    if not text:
        return ""
    clean = re.sub(r"<[^>]+>", "", text)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean[:max_len] if len(clean) > max_len else clean


async def search_loras(query: str = "wan 2.1", limit: int = 100, cursor: str = "",
                       nsfw: bool = True, sort: str = "Most Downloaded",
                       base_model: str = "") -> dict:
    """Search CivitAI using Meilisearch (same engine as website) with v1 API fallback."""
    try:
        return await _search_meili(query, limit, cursor, nsfw, sort, base_model)
    except Exception as e:
        logger.warning("Meilisearch failed, falling back to v1 API: %s", e)
        return await _search_v1(query, limit, cursor, nsfw, sort, base_model)


async def _search_meili(query: str, limit: int, cursor: str,
                        nsfw: bool, sort: str, base_model: str) -> dict:
    """Search via CivitAI's Meilisearch endpoint (full-text, same as website)."""
    offset = int(cursor) if cursor and cursor.isdigit() else 0
    filters = ["type = LORA"]
    if not nsfw:
        filters.append("nsfwLevel = 1")
    if base_model:
        filters.append(f"version.baseModel = '{base_model}'")
    else:
        # Default: only Wan-family base models
        wan_models = [
            "Wan Video 2.2 I2V-A14B", "Wan Video 2.2 T2V-A14B",
            "Wan Video 2.2 14B", "Wan Video 2.1 14B",
            "Wan Video 14B i2v 480p", "Wan Video 1.3B t2v",
        ]
        wan_filter = " OR ".join(f"version.baseModel = '{m}'" for m in wan_models)
        filters.append(wan_filter)

    # Map sort options to Meilisearch sort
    sort_map = {
        "Most Downloaded": ["metrics.downloadCount:desc"],
        "Highest Rated": ["metrics.thumbsUpCount:desc"],
        "Newest": ["createdAt:desc"],
    }
    meili_sort = sort_map.get(sort, ["metrics.downloadCount:desc"])

    body = {
        "q": query,
        "limit": limit,
        "offset": offset,
        "filter": filters,
        "sort": meili_sort,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {MEILI_KEY}",
        "Accept-Encoding": "gzip, deflate",
    }
    async with aiohttp.ClientSession(timeout=MEILI_TIMEOUT) as session:
        async with session.post(f"{MEILI_URL}/indexes/models_v9/search",
                                json=body, headers=headers) as resp:
            if resp.status != 200:
                err_text = await resp.text()
                raise RuntimeError(f"Meilisearch {resp.status}: {err_text[:300]}")
            data = await resp.json()

    hits = data.get("hits", [])
    total = data.get("estimatedTotalHits", 0)
    next_cursor = str(offset + limit) if offset + limit < total else ""

    # Convert Meilisearch hits to v1-compatible format for extract_model_result
    IMAGE_CDN = "https://image.civitai.com/xG1nkqKTMzGDvpLrqFT7WA"
    items = []
    for hit in hits:
        ver = hit.get("version", {})
        # Build preview URL from first image
        preview_images = []
        for img in hit.get("images", []):
            url_id = img.get("url", "")
            img_type = img.get("type", "image")
            img_name = img.get("name", "")
            if url_id:
                # Build full URL: CDN/{uuid}/original=true/{name}
                ext = img_name if img_name else ("video.mp4" if img_type == "video" else "image.jpeg")
                full_url = f"{IMAGE_CDN}/{url_id}/original=true/{ext}"
                preview_images.append({"url": full_url})
            break  # Only need first image for preview

        mv = {
            "id": ver.get("id", 0),
            "name": ver.get("name", ""),
            "baseModel": ver.get("baseModel", ""),
            "trainedWords": ver.get("trainedWords") or hit.get("triggerWords") or [],
            "files": [],
            "images": preview_images,
        }

        # Map metrics to stats format
        metrics = hit.get("metrics", {})
        stats = {
            "downloadCount": metrics.get("downloadCount", 0),
            "thumbsUpCount": metrics.get("thumbsUpCount", 0),
            "rating": metrics.get("rating", 0),
        }

        item = {
            "id": hit.get("id"),
            "name": hit.get("name", ""),
            "description": "",
            "tags": [t["name"] if isinstance(t, dict) else t for t in (hit.get("tags") or [])],
            "stats": stats,
            "modelVersions": [mv],
        }
        items.append(item)

    return {"items": items, "metadata": {"nextCursor": next_cursor}}


async def _search_v1(query: str, limit: int, cursor: str,
                     nsfw: bool, sort: str, base_model: str) -> dict:
    """Fallback: search via CivitAI v1 REST API."""
    params = {
        "types": "LORA",
        "query": query,
        "sort": sort,
        "limit": limit,
        "nsfw": str(nsfw).lower(),
    }
    if cursor:
        params["cursor"] = cursor
    if base_model:
        params["baseModels"] = base_model
    else:
        params["baseModels"] = "Wan Video 2.2 I2V-A14B,Wan Video 2.2 T2V-A14B,Wan Video 2.2 14B,Wan Video 2.1 14B,Wan Video 1.3B t2v"
    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        async with session.get(f"{BASE_URL}/models", params=params, headers=_headers()) as resp:
            resp.raise_for_status()
            return await resp.json()


async def get_model(model_id: int) -> dict:
    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        async with session.get(f"{BASE_URL}/models/{model_id}", headers=_headers()) as resp:
            resp.raise_for_status()
            return await resp.json()


async def get_version(version_id: int) -> dict:
    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        async with session.get(f"{BASE_URL}/model-versions/{version_id}", headers=_headers()) as resp:
            resp.raise_for_status()
            return await resp.json()


def download_url(version_id: int) -> str:
    return f"{BASE_URL}/model-versions/{version_id}/download"


def extract_model_result(raw: dict) -> CivitAIModelResult:
    versions = []
    for mv in raw.get("modelVersions", []):
        trained_words = mv.get("trainedWords", []) or []
        dl_url = ""
        file_size_mb = 0
        file_list = []
        files = mv.get("files", [])
        for fi in files:
            fname = fi.get("name", "")
            size_kb = fi.get("sizeKB", 0)
            sz_mb = round(size_kb / 1024, 1) if size_kb else 0
            f_dl = fi.get("downloadUrl", "")
            file_list.append(CivitAIFile(name=fname, size_mb=sz_mb, download_url=f_dl))
        if files:
            dl_url = files[0].get("downloadUrl", "")
            file_size_mb = file_list[0].size_mb if file_list else 0
        versions.append(CivitAIModelVersion(
            id=mv["id"],
            name=mv.get("name", ""),
            trained_words=trained_words,
            download_url=dl_url,
            base_model=mv.get("baseModel", ""),
            file_size_mb=file_size_mb,
            files=file_list,
        ))

    preview_url = None
    all_versions = raw.get("modelVersions", [])
    if all_versions:
        images = all_versions[0].get("images", [])
        if images:
            preview_url = images[0].get("url")

    stats = raw.get("stats", {})

    return CivitAIModelResult(
        id=raw["id"],
        name=raw.get("name", ""),
        description=_strip_html(raw.get("description", "")),
        tags=raw.get("tags", []) or [],
        preview_url=preview_url,
        versions=versions,
        stats=stats,
    )
