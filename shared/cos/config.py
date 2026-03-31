"""COS configuration container -- injectable, no env dependency."""

from dataclasses import dataclass


@dataclass(frozen=True)
class COSConfig:
    """Tencent Cloud COS configuration.

    All fields are required for a functional COS client.
    Use COSConfig.disabled() for a no-op placeholder.
    """

    secret_id: str
    secret_key: str
    bucket: str
    region: str = "ap-guangzhou"
    prefix: str = "wan22"
    cdn_domain: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.bucket)

    @property
    def effective_cdn_domain(self) -> str:
        if self.cdn_domain:
            return self.cdn_domain
        if self.bucket:
            return f"{self.bucket}.cos.{self.region}.myqcloud.com"
        return ""

    @classmethod
    def disabled(cls) -> "COSConfig":
        """Return a config that signals COS is disabled."""
        return cls(secret_id="", secret_key="", bucket="", region="", prefix="", cdn_domain="")
