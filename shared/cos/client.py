"""Tencent Cloud COS client -- dependency-injected, no global state."""

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from shared.cos.config import COSConfig

if TYPE_CHECKING:
    from qcloud_cos import CosS3Client

logger = logging.getLogger(__name__)


class COSClient:
    """COS operations backed by a given COSConfig.

    The underlying qcloud_cos SDK client is created lazily on first use.
    """

    def __init__(self, config: COSConfig) -> None:
        self._config = config
        self._sdk_client: Optional["CosS3Client"] = None

    @property
    def config(self) -> COSConfig:
        return self._config

    def _get_sdk_client(self) -> "CosS3Client":
        """Lazy-init the qcloud_cos SDK client."""
        if self._sdk_client is None:
            from qcloud_cos import CosConfig, CosS3Client

            sdk_config = CosConfig(
                Region=self._config.region,
                SecretId=self._config.secret_id,
                SecretKey=self._config.secret_key,
            )
            self._sdk_client = CosS3Client(sdk_config)
        return self._sdk_client

    def make_key(self, subdir: str, filename: str) -> str:
        """Build a COS object key from subdir and filename."""
        prefix = self._config.prefix
        return f"{prefix}/{subdir}/{filename}" if prefix else f"{subdir}/{filename}"

    def make_url(self, key: str) -> str:
        """Build a public URL from a COS object key."""
        return f"https://{self._config.effective_cdn_domain}/{key}"

    def make_thumbnail_url(self, url: str, width: int = 400, height: int = 400) -> str:
        """Generate a thumbnail URL for COS images using Tencent Cloud CI."""
        if not url or not isinstance(url, str):
            return url

        cdn = self._config.effective_cdn_domain
        if cdn not in url and ".cos." not in url and ".myqcloud.com" not in url:
            return url

        image_exts = (".jpg", ".jpeg", ".png", ".webp", ".gif")
        if not any(url.lower().endswith(ext) for ext in image_exts):
            return url

        return f"{url}?imageMogr2/thumbnail/{width}x{height}/format/webp"

    def upload_file(self, local_path: str | Path, subdir: str, filename: str) -> str:
        """Upload a local file to COS. Returns the public URL."""
        key = self.make_key(subdir, filename)
        self._get_sdk_client().upload_file(
            Bucket=self._config.bucket,
            Key=key,
            LocalFilePath=str(local_path),
        )
        url = self.make_url(key)
        logger.info("Uploaded to COS: %s", url)
        return url

    def download_file(self, subdir: str, filename: str, local_path: str | Path) -> None:
        """Download a COS object to a local path."""
        key = self.make_key(subdir, filename)
        self._get_sdk_client().download_file(
            Bucket=self._config.bucket,
            Key=key,
            DestFilePath=str(local_path),
        )
        logger.info("Downloaded from COS: %s -> %s", key, local_path)

    def delete_file(self, subdir: str, filename: str) -> None:
        """Delete a COS object."""
        key = self.make_key(subdir, filename)
        self._get_sdk_client().delete_object(Bucket=self._config.bucket, Key=key)
        logger.info("Deleted from COS: %s", key)

    def parse_cos_url(self, url: str) -> tuple[str, str] | None:
        """Parse a COS/CDN URL into (subdir, filename). Returns None if not a COS URL."""
        cos_prefix = f"https://{self._config.bucket}.cos.{self._config.region}.myqcloud.com/"
        cdn_prefix = f"https://{self._config.effective_cdn_domain}/"

        if url.startswith(cdn_prefix):
            key = url[len(cdn_prefix):]
        elif url.startswith(cos_prefix):
            key = url[len(cos_prefix):]
        else:
            return None

        parts = key.rsplit("/", 1)
        if len(parts) != 2:
            return None

        path_part, filename = parts
        prefix = self._config.prefix
        if prefix and path_part.startswith(prefix + "/"):
            subdir = path_part[len(prefix) + 1 :]
        else:
            subdir = path_part
        return subdir, filename
