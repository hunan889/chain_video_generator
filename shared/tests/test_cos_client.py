"""Tests for shared.cos — write FIRST, implement after."""

import dataclasses
from unittest.mock import MagicMock, patch

import pytest

from shared.cos.config import COSConfig


# ============================================================
# COSConfig tests
# ============================================================


class TestCOSConfig:
    @pytest.fixture()
    def config(self) -> COSConfig:
        return COSConfig(
            secret_id="sid",
            secret_key="skey",
            bucket="my-bucket-123",
            region="ap-guangzhou",
            prefix="wan22",
            cdn_domain="cdn.example.com",
        )

    def test_creation(self, config: COSConfig):
        assert config.secret_id == "sid"
        assert config.secret_key == "skey"
        assert config.bucket == "my-bucket-123"
        assert config.region == "ap-guangzhou"
        assert config.prefix == "wan22"
        assert config.cdn_domain == "cdn.example.com"

    def test_frozen(self, config: COSConfig):
        with pytest.raises(dataclasses.FrozenInstanceError):
            config.bucket = "other"  # type: ignore[misc]

    def test_enabled_true(self, config: COSConfig):
        assert config.enabled is True

    def test_enabled_false_empty_bucket(self):
        c = COSConfig(secret_id="", secret_key="", bucket="")
        assert c.enabled is False

    def test_disabled_factory(self):
        c = COSConfig.disabled()
        assert c.enabled is False
        assert c.bucket == ""

    def test_effective_cdn_domain_explicit(self, config: COSConfig):
        assert config.effective_cdn_domain == "cdn.example.com"

    def test_effective_cdn_domain_derived(self):
        c = COSConfig(secret_id="s", secret_key="k", bucket="bkt", region="ap-shanghai")
        assert c.effective_cdn_domain == "bkt.cos.ap-shanghai.myqcloud.com"

    def test_effective_cdn_domain_no_bucket(self):
        c = COSConfig.disabled()
        assert c.effective_cdn_domain == ""

    def test_defaults(self):
        c = COSConfig(secret_id="s", secret_key="k", bucket="b")
        assert c.region == "ap-guangzhou"
        assert c.prefix == "wan22"
        assert c.cdn_domain == ""


# ============================================================
# COSClient tests
# ============================================================


from shared.cos.client import COSClient


class TestCOSClientMakeKey:
    def test_with_prefix(self):
        c = COSClient(COSConfig(secret_id="s", secret_key="k", bucket="b", prefix="wan22"))
        assert c.make_key("videos", "a.mp4") == "wan22/videos/a.mp4"

    def test_without_prefix(self):
        c = COSClient(COSConfig(secret_id="s", secret_key="k", bucket="b", prefix=""))
        assert c.make_key("videos", "a.mp4") == "videos/a.mp4"


class TestCOSClientMakeUrl:
    def test_make_url(self):
        c = COSClient(COSConfig(
            secret_id="s", secret_key="k", bucket="b",
            cdn_domain="cdn.example.com",
        ))
        assert c.make_url("wan22/videos/a.mp4") == "https://cdn.example.com/wan22/videos/a.mp4"


class TestCOSClientThumbnail:
    @pytest.fixture()
    def client(self) -> COSClient:
        return COSClient(COSConfig(
            secret_id="s", secret_key="k", bucket="b",
            cdn_domain="cdn.example.com",
        ))

    def test_image_url(self, client: COSClient):
        url = "https://cdn.example.com/wan22/img/photo.jpg"
        result = client.make_thumbnail_url(url)
        assert "imageMogr2/thumbnail/400x400/format/webp" in result

    def test_custom_dimensions(self, client: COSClient):
        url = "https://cdn.example.com/wan22/img/photo.png"
        result = client.make_thumbnail_url(url, width=200, height=300)
        assert "thumbnail/200x300" in result

    def test_non_image_returns_original(self, client: COSClient):
        url = "https://cdn.example.com/wan22/vid/clip.mp4"
        assert client.make_thumbnail_url(url) == url

    def test_non_cos_url_returns_original(self, client: COSClient):
        url = "https://other-cdn.com/photo.jpg"
        assert client.make_thumbnail_url(url) == url

    def test_empty_returns_original(self, client: COSClient):
        assert client.make_thumbnail_url("") == ""

    def test_none_returns_original(self, client: COSClient):
        assert client.make_thumbnail_url(None) is None  # type: ignore[arg-type]


class TestCOSClientParseUrl:
    @pytest.fixture()
    def client(self) -> COSClient:
        return COSClient(COSConfig(
            secret_id="s", secret_key="k",
            bucket="my-bkt-123",
            region="ap-guangzhou",
            prefix="wan22",
            cdn_domain="cdn.example.com",
        ))

    def test_cdn_url(self, client: COSClient):
        url = "https://cdn.example.com/wan22/videos/result.mp4"
        assert client.parse_cos_url(url) == ("videos", "result.mp4")

    def test_cos_direct_url(self, client: COSClient):
        url = "https://my-bkt-123.cos.ap-guangzhou.myqcloud.com/wan22/uploads/img.png"
        assert client.parse_cos_url(url) == ("uploads", "img.png")

    def test_non_cos_url_returns_none(self, client: COSClient):
        assert client.parse_cos_url("https://other.com/file.txt") is None

    def test_malformed_no_slash_returns_none(self, client: COSClient):
        url = "https://cdn.example.com/singlepart"
        assert client.parse_cos_url(url) is None


class TestCOSClientSDKCalls:
    @pytest.fixture()
    def client_and_mock(self):
        config = COSConfig(
            secret_id="sid", secret_key="skey",
            bucket="test-bucket",
            region="ap-guangzhou",
            prefix="wan22",
            cdn_domain="cdn.test.com",
        )
        client = COSClient(config)
        mock_sdk = MagicMock()
        client._sdk_client = mock_sdk
        return client, mock_sdk

    def test_upload_file(self, client_and_mock):
        client, mock_sdk = client_and_mock
        result = client.upload_file("/tmp/video.mp4", "videos", "out.mp4")
        mock_sdk.upload_file.assert_called_once_with(
            Bucket="test-bucket",
            Key="wan22/videos/out.mp4",
            LocalFilePath="/tmp/video.mp4",
        )
        assert result == "https://cdn.test.com/wan22/videos/out.mp4"

    def test_download_file(self, client_and_mock):
        client, mock_sdk = client_and_mock
        client.download_file("videos", "out.mp4", "/tmp/out.mp4")
        mock_sdk.download_file.assert_called_once_with(
            Bucket="test-bucket",
            Key="wan22/videos/out.mp4",
            DestFilePath="/tmp/out.mp4",
        )

    def test_delete_file(self, client_and_mock):
        client, mock_sdk = client_and_mock
        client.delete_file("videos", "out.mp4")
        mock_sdk.delete_object.assert_called_once_with(
            Bucket="test-bucket",
            Key="wan22/videos/out.mp4",
        )


class TestCOSClientLazyInit:
    def test_constructor_does_not_create_sdk_client(self):
        config = COSConfig(secret_id="s", secret_key="k", bucket="b")
        client = COSClient(config)
        assert client._sdk_client is None

    def test_sdk_client_created_on_first_use(self):
        config = COSConfig(secret_id="s", secret_key="k", bucket="b", region="ap-gz")
        client = COSClient(config)
        with patch("qcloud_cos.CosConfig") as mock_cos_config, \
             patch("qcloud_cos.CosS3Client") as mock_cos_s3:
            mock_cos_s3.return_value = MagicMock()
            sdk = client._get_sdk_client()
            mock_cos_config.assert_called_once_with(
                Region="ap-gz", SecretId="s", SecretKey="k",
            )
            mock_cos_s3.assert_called_once()
            assert sdk is not None

    def test_sdk_client_reused(self):
        config = COSConfig(secret_id="s", secret_key="k", bucket="b", region="ap-gz")
        client = COSClient(config)
        mock_sdk = MagicMock()
        client._sdk_client = mock_sdk
        sdk1 = client._get_sdk_client()
        sdk2 = client._get_sdk_client()
        assert sdk1 is sdk2
        assert sdk1 is mock_sdk
