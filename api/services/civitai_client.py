import re
import logging
import aiohttp
from typing import Optional
from api.config import CIVITAI_API_TOKEN
from api.models.schemas import CivitAIModelResult, CivitAIModelVersion, CivitAIFile

logger = logging.getLogger(__name__)

BASE_URL = "https://civitai.com/api/v1"
TIMEOUT = aiohttp.ClientTimeout(total=15)


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


async def search_loras(query: str = "wan 2.1", limit: int = 20, cursor: str = "",
                       nsfw: bool = True, sort: str = "Most Downloaded",
                       base_model: str = "") -> dict:
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
    url = f"{BASE_URL}/model-versions/{version_id}/download"
    if CIVITAI_API_TOKEN:
        url += f"?token={CIVITAI_API_TOKEN}"
    return url


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
