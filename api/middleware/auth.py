from pathlib import Path
import yaml
from fastapi import Request, HTTPException, Security
from fastapi.security import APIKeyHeader
from api.config import API_KEYS_PATH

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

_keys_cache: dict | None = None
_keys_mtime: float = 0


def _load_keys() -> dict[str, dict]:
    global _keys_cache, _keys_mtime
    mtime = API_KEYS_PATH.stat().st_mtime
    if _keys_cache is None or mtime != _keys_mtime:
        with open(API_KEYS_PATH) as f:
            data = yaml.safe_load(f)
        _keys_cache = {
            k["key"]: k for k in data.get("keys", []) if k.get("enabled", True)
        }
        _keys_mtime = mtime
    return _keys_cache


async def verify_api_key(api_key: str = Security(api_key_header)):
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing API key")
    keys = _load_keys()
    if api_key not in keys:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return keys[api_key]
