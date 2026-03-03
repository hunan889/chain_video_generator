"""Tencent Cloud COS client wrapper."""
import logging
from pathlib import Path
from qcloud_cos import CosConfig, CosS3Client
from api.config import COS_SECRET_ID, COS_SECRET_KEY, COS_BUCKET, COS_REGION, COS_PREFIX

logger = logging.getLogger(__name__)

_config = CosConfig(
    Region=COS_REGION,
    SecretId=COS_SECRET_ID,
    SecretKey=COS_SECRET_KEY,
)
_client = CosS3Client(_config)


def _make_key(subdir: str, filename: str) -> str:
    return f"{COS_PREFIX}/{subdir}/{filename}" if COS_PREFIX else f"{subdir}/{filename}"


def _make_url(key: str) -> str:
    return f"https://{COS_BUCKET}.cos.{COS_REGION}.myqcloud.com/{key}"


def upload_file(local_path: str | Path, subdir: str, filename: str) -> str:
    """Upload a local file to COS. Returns the public URL."""
    key = _make_key(subdir, filename)
    _client.upload_file(
        Bucket=COS_BUCKET,
        Key=key,
        LocalFilePath=str(local_path),
    )
    url = _make_url(key)
    logger.info("Uploaded to COS: %s", url)
    return url


def download_file(subdir: str, filename: str, local_path: str | Path):
    """Download a COS object to a local path."""
    key = _make_key(subdir, filename)
    _client.download_file(
        Bucket=COS_BUCKET,
        Key=key,
        DestFilePath=str(local_path),
    )
    logger.info("Downloaded from COS: %s -> %s", key, local_path)


def delete_file(subdir: str, filename: str):
    """Delete a COS object."""
    key = _make_key(subdir, filename)
    _client.delete_object(Bucket=COS_BUCKET, Key=key)
    logger.info("Deleted from COS: %s", key)


def parse_cos_url(url: str) -> tuple[str, str] | None:
    """Parse a COS URL into (subdir, filename). Returns None if not a COS URL."""
    prefix = f"https://{COS_BUCKET}.cos.{COS_REGION}.myqcloud.com/"
    if not url.startswith(prefix):
        return None
    key = url[len(prefix):]
    # key format: {COS_PREFIX}/{subdir}/{filename} or {subdir}/{filename}
    parts = key.rsplit("/", 1)
    if len(parts) != 2:
        return None
    path_part, filename = parts
    # Strip COS_PREFIX if present
    if COS_PREFIX and path_part.startswith(COS_PREFIX + "/"):
        subdir = path_part[len(COS_PREFIX) + 1:]
    else:
        subdir = path_part
    return subdir, filename
