"""
Output watermark rendering — WAN backend side.

This is the counterpart to the H5 backend's `server/watermark.py`. H5
computes the per-user watermark config and forwards it in two surfaces:

1. JSON body — `body["watermark"] = { ... }` for endpoints like
   /api/v1/workflow/generate-advanced
2. HTTP headers — `X-Watermark-*` for multipart endpoints like
   /api/v1/image/transform and /api/v1/video/transform

This module handles both surfaces with a unified `parse()` entry point
and exposes async `apply_to_image_path()` and `apply_to_video_path()`
helpers that mutate files in place.

Contract reference: docs/launch/WATERMARK_CONTRACT.md in the H5 repo.

Fail-open policy: every apply_* function catches its own exceptions and
returns silently on failure. A failed watermark must NEVER block an
otherwise-successful generation — the user still gets their output.
"""
import asyncio
import logging
import shutil
from pathlib import Path
from typing import Any, Optional

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)


# Where we look for a TrueType font. DejaVu ships with nearly every Linux
# distro and macOS dev environment. PIL's default bitmap font is ugly and
# doesn't scale, so we fall back to that only as a last resort.
_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]


def _load_font(size: int) -> ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    logger.warning("watermark: no TrueType font found, using default bitmap font")
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Parsing — JSON body OR HTTP headers
# ---------------------------------------------------------------------------


def parse_from_body(body: Any) -> Optional[dict]:
    """Extract a watermark config dict from a JSON request body.

    Returns None when the body contains no watermark field (fail-open).
    Returns a normalized dict when present.
    """
    if not isinstance(body, dict):
        return None
    wm = body.get("watermark")
    if not isinstance(wm, dict):
        return None
    return _normalize(wm)


def parse_from_headers(headers: Any) -> Optional[dict]:
    """Extract a watermark config dict from an HTTP headers mapping.

    `headers` should be a case-insensitive mapping (FastAPI Request.headers
    works directly). Returns None when the X-Watermark-Enabled header is
    absent (fail-open).
    """
    if headers is None:
        return None
    enabled = headers.get("x-watermark-enabled")
    if enabled is None:
        return None
    if enabled == "0":
        return {"enabled": False}
    if enabled != "1":
        return None
    try:
        opacity = int(headers.get("x-watermark-opacity", "60"))
    except ValueError:
        opacity = 60
    try:
        font_size = int(headers.get("x-watermark-font-size", "24"))
    except ValueError:
        font_size = 24
    return _normalize({
        "enabled": True,
        "text": headers.get("x-watermark-text", ""),
        "position": headers.get("x-watermark-position", "bottom_right"),
        "opacity": opacity,
        "font_size": font_size,
        "apply_to_image": headers.get("x-watermark-apply-image", "1") == "1",
        "apply_to_video": headers.get("x-watermark-apply-video", "1") == "1",
    })


def _normalize(cfg: dict) -> dict:
    """Coerce a raw watermark dict into the canonical internal shape.

    Defensive: missing keys get sensible defaults so downstream code
    can assume every key is present.
    """
    return {
        "enabled": bool(cfg.get("enabled", False)),
        "text": str(cfg.get("text", "")),
        "position": str(cfg.get("position", "bottom_right")),
        "opacity": max(0, min(100, int(cfg.get("opacity", 60) or 0))),
        "font_size": max(8, min(200, int(cfg.get("font_size", 24) or 24))),
        "apply_to_image": bool(cfg.get("apply_to_image", True)),
        "apply_to_video": bool(cfg.get("apply_to_video", True)),
    }


# ---------------------------------------------------------------------------
# Image watermarking — PIL
# ---------------------------------------------------------------------------


def _position_xy(position: str, img_size: tuple[int, int], text_size: tuple[int, int], margin: int) -> tuple[int, int]:
    iw, ih = img_size
    tw, th = text_size
    positions = {
        "top_left":     (margin, margin),
        "top_right":    (iw - tw - margin, margin),
        "bottom_left":  (margin, ih - th - margin),
        "bottom_right": (iw - tw - margin, ih - th - margin),
    }
    return positions.get(position, positions["bottom_right"])


def apply_to_image_path(path: Path | str, cfg: Optional[dict]) -> None:
    """Apply watermark to an image file in place. No-op on failure.

    `path` must be a local file path. The function opens the image,
    overlays the watermark text, and writes back to the same path
    (preserving format from the file extension).
    """
    if cfg is None or not cfg.get("enabled") or not cfg.get("apply_to_image", True):
        return
    if not cfg.get("text"):
        return
    try:
        path = Path(path)
        if not path.exists():
            logger.warning("watermark: image path missing: %s", path)
            return
        img = Image.open(path).convert("RGBA")
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        font = _load_font(int(cfg["font_size"]))
        text = cfg["text"]
        # textbbox returns (x0, y0, x1, y1) — take width/height from it.
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        margin = max(12, int(cfg["font_size"]) // 2)
        x, y = _position_xy(cfg["position"], img.size, (text_w, text_h), margin)
        alpha = int(255 * (int(cfg["opacity"]) / 100.0))
        # Drop-shadow for readability on light/busy backgrounds.
        draw.text((x + 1, y + 1), text, font=font, fill=(0, 0, 0, alpha))
        draw.text((x, y), text, font=font, fill=(255, 255, 255, alpha))
        out = Image.alpha_composite(img, overlay)
        # Preserve original format — most of our outputs are PNG via
        # _save_result_image, but some engines drop JPEGs.
        ext = path.suffix.lower().lstrip(".")
        if ext in ("jpg", "jpeg"):
            out.convert("RGB").save(path, "JPEG", quality=92)
        else:
            out.save(path, "PNG")
        logger.info("watermark: applied to image %s position=%s", path.name, cfg["position"])
    except Exception as e:
        # Fail-open: log and move on.
        logger.warning("watermark: image apply failed: %s", e)


# ---------------------------------------------------------------------------
# Video watermarking — ffmpeg drawtext filter
# ---------------------------------------------------------------------------


def _ffmpeg_drawtext_expr(cfg: dict) -> str:
    """Build an ffmpeg drawtext filter expression from the config."""
    # Escape single quotes and backslashes in the text so the filter
    # doesn't break on unusual usernames. ffmpeg's drawtext filter uses
    # single-quoted strings with backslash-escaped special chars.
    text = str(cfg.get("text", ""))
    text = text.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")
    font_size = int(cfg.get("font_size", 24))
    opacity = int(cfg.get("opacity", 60)) / 100.0
    position = cfg.get("position", "bottom_right")
    # Offset from the frame edge (in pixels).
    margin = 20
    xy = {
        "top_left":     (f"{margin}", f"{margin}"),
        "top_right":    (f"w-tw-{margin}", f"{margin}"),
        "bottom_left":  (f"{margin}", f"h-th-{margin}"),
        "bottom_right": (f"w-tw-{margin}", f"h-th-{margin}"),
    }
    x, y = xy.get(position, xy["bottom_right"])
    # Include a semi-transparent box behind the text for legibility on
    # busy video frames. boxborderw gives a small padding.
    return (
        f"drawtext=text='{text}'"
        f":fontsize={font_size}"
        f":fontcolor=white@{opacity:.2f}"
        f":box=1:boxcolor=black@0.35:boxborderw=6"
        f":x={x}:y={y}"
    )


async def apply_to_video_path(path: Path | str, cfg: Optional[dict]) -> None:
    """Apply watermark to a video file in place via ffmpeg drawtext.

    Writes to a temp sibling file, then atomically renames onto `path`
    so a failed run can never corrupt the original. No-op on failure.
    """
    if cfg is None or not cfg.get("enabled") or not cfg.get("apply_to_video", True):
        return
    if not cfg.get("text"):
        return
    try:
        path = Path(path)
        if not path.exists():
            logger.warning("watermark: video path missing: %s", path)
            return
        tmp = path.with_name(f"{path.stem}.wm{path.suffix}")
        filter_expr = _ffmpeg_drawtext_expr(cfg)
        cmd = [
            "ffmpeg", "-y",
            "-i", str(path),
            "-vf", filter_expr,
            "-c:a", "copy",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "23",
            str(tmp),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0 or not tmp.exists():
            logger.warning(
                "watermark: ffmpeg drawtext failed rc=%s tail=%s",
                proc.returncode,
                stderr.decode(errors="replace")[-400:],
            )
            if tmp.exists():
                tmp.unlink()
            return
        shutil.move(str(tmp), str(path))
        logger.info("watermark: applied to video %s position=%s", path.name, cfg["position"])
    except Exception as e:
        logger.warning("watermark: video apply failed: %s", e)


# ---------------------------------------------------------------------------
# Path resolution helper
# ---------------------------------------------------------------------------


def resolve_result_url_to_local_path(url: str) -> Optional[Path]:
    """Turn a `/api/v1/results/<filename>` URL into a local file Path.

    Returns None when the URL is not a local result URL (e.g. remote
    CDN links) — the caller should skip watermarking in that case.
    """
    if not url:
        return None
    marker = "/api/v1/results/"
    idx = url.find(marker)
    if idx < 0:
        return None
    filename = url[idx + len(marker):]
    # Strip any query string.
    q = filename.find("?")
    if q >= 0:
        filename = filename[:q]
    if "/" in filename or ".." in filename:
        # Defensive — should never happen but blocks path traversal if it did.
        return None
    try:
        from api.config import UPLOADS_DIR
        return UPLOADS_DIR / filename
    except Exception:
        return None
