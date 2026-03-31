"""Backward-compatible COS client -- delegates to shared.cos.COSClient.

All new code should use shared.cos.COSClient directly with injected config.
"""

import logging
from pathlib import Path

from api.config import (
    COS_BUCKET,
    COS_CDN_DOMAIN,
    COS_PREFIX,
    COS_REGION,
    COS_SECRET_ID,
    COS_SECRET_KEY,
)
from shared.cos import COSClient, COSConfig

logger = logging.getLogger(__name__)

_cos_config = COSConfig(
    secret_id=COS_SECRET_ID,
    secret_key=COS_SECRET_KEY,
    bucket=COS_BUCKET,
    region=COS_REGION,
    prefix=COS_PREFIX,
    cdn_domain=COS_CDN_DOMAIN,
)
_client = COSClient(_cos_config)


def make_thumbnail_url(url: str, width: int = 400, height: int = 400) -> str:
    """Generate a thumbnail URL for COS images using Tencent Cloud CI."""
    return _client.make_thumbnail_url(url, width, height)


def upload_file(local_path: str | Path, subdir: str, filename: str) -> str:
    """Upload a local file to COS. Returns the public URL."""
    return _client.upload_file(local_path, subdir, filename)


def download_file(subdir: str, filename: str, local_path: str | Path) -> None:
    """Download a COS object to a local path."""
    return _client.download_file(subdir, filename, local_path)


def delete_file(subdir: str, filename: str) -> None:
    """Delete a COS object."""
    return _client.delete_file(subdir, filename)


def parse_cos_url(url: str) -> tuple[str, str] | None:
    """Parse a COS/CDN URL into (subdir, filename). Returns None if not a COS/CDN URL."""
    return _client.parse_cos_url(url)


# Private functions used by scripts/migrate_poses_to_cos.py
def _make_key(subdir: str, filename: str) -> str:
    return _client.make_key(subdir, filename)


def _make_url(key: str) -> str:
    return _client.make_url(key)
