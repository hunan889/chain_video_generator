"""Reverse proxy to the old monolith for routes the gateway doesn't natively handle."""

import logging

import aiohttp
from fastapi import APIRouter, Request
from fastapi.responses import Response

logger = logging.getLogger(__name__)

router = APIRouter(tags=["proxy"])

PROXY_PREFIXES = [
    "admin/loras",
    "admin/embeddings",
    "pose-images",
    "upload",
    "results",
    "proxy-media",
]


def _is_proxied_path(path: str) -> bool:
    """Return True if *path* should be forwarded to the monolith."""
    for prefix in PROXY_PREFIXES:
        if path == prefix or path.startswith(f"{prefix}/"):
            return True
    return False


@router.api_route(
    "/api/v1/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    include_in_schema=False,
)
async def proxy(request: Request, path: str) -> Response:
    """Forward matching requests to the old monolith; return 404 for unknown routes."""
    if not _is_proxied_path(path):
        return Response(
            content='{"detail":"Not found"}',
            status_code=404,
            media_type="application/json",
        )

    monolith_url: str = request.app.state.config.monolith_url
    target_url = f"{monolith_url}/api/v1/{path}"

    # Preserve query string
    if request.url.query:
        target_url = f"{target_url}?{request.url.query}"

    # Forward headers, dropping Host (aiohttp sets it from the target URL)
    forwarded_headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() != "host"
    }

    body = await request.body()

    try:
        async with aiohttp.ClientSession() as session:
            async with session.request(
                method=request.method,
                url=target_url,
                headers=forwarded_headers,
                data=body if body else None,
            ) as resp:
                response_body = await resp.read()
                content_type = resp.headers.get("content-type", "application/octet-stream")
                return Response(
                    content=response_body,
                    status_code=resp.status,
                    media_type=content_type,
                )
    except (aiohttp.ClientError, OSError) as exc:
        logger.error("Proxy error forwarding %s %s: %s", request.method, target_url, exc)
        return Response(
            content='{"detail":"Bad gateway — upstream unreachable"}',
            status_code=502,
            media_type="application/json",
        )
