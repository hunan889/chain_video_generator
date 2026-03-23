#!/usr/bin/env python3
"""
Scrape pose reference images from createporn.com using Playwright.

Uses network interception to capture API responses, with DOM scraping fallback.
Downloads images via httpx from CDN.
Filters: portrait-only (height > width) and realistic-only (no anime).

Usage:
    python scripts/scrape_createporn_poses.py --poses "cowgirl,missionary,doggy style:doggy_style"
    python scripts/scrape_createporn_poses.py --poses "cowgirl" --max-images 30
    python scripts/scrape_createporn_poses.py --poses "cowgirl" --no-headless
    python scripts/scrape_createporn_poses.py --poses "cowgirl" --dry-run
    python scripts/scrape_createporn_poses.py --poses "cowgirl" --allow-anime --allow-landscape
"""

import argparse
import asyncio
import io
import json
import logging
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote

import httpx
import numpy as np
from PIL import Image
from playwright.async_api import async_playwright

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_BASE = PROJECT_ROOT / "data" / "pose_references"

SEARCH_URL_TEMPLATE = "https://www.createporn.com/zh/cn/post/search?search={keyword}"
CDN_BASE = "https://cdn2.createporn.com"
SITE_BASE = "https://www.createporn.com"

# Anime detection thresholds (tuned from real samples)
# Primary: gradient magnitude — anime has sharp cel-shading edges (>= 5.0)
#          while photorealistic images have smooth gradients (< 5.0)
# Secondary: sharp edge percentage — anime >= 0.07, realistic < 0.06
ANIME_GRADIENT_THRESHOLD = 4.7
ANIME_EDGE_THRESHOLD = 0.060

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Image filters
# ---------------------------------------------------------------------------

def is_portrait(img: Image.Image) -> bool:
    """Check if image is portrait orientation (height > width)."""
    w, h = img.size
    return h > w


def is_anime(img: Image.Image) -> bool:
    """Detect anime/cartoon images via gradient analysis.

    Anime/cel-shaded images have sharp color transitions (high gradient),
    while photorealistic images have smooth gradients with camera noise.
    This is the most reliable single feature for separating the two.
    """
    gray = np.array(img.convert("L"), dtype=float)

    # Compute gradient magnitude (average of horizontal + vertical)
    grad_h = np.abs(np.diff(gray, axis=1))
    grad_v = np.abs(np.diff(gray, axis=0))
    mean_grad = (np.mean(grad_h) + np.mean(grad_v)) / 2

    if mean_grad >= ANIME_GRADIENT_THRESHOLD:
        return True

    # Secondary check: percentage of sharp edge pixels
    sharp_edges = (np.mean(grad_h > 30) + np.mean(grad_v > 30)) / 2
    if sharp_edges >= ANIME_EDGE_THRESHOLD:
        return True

    return False


def check_image(
    image_data: bytes,
    require_portrait: bool = True,
    reject_anime: bool = True,
) -> tuple[bool, str]:
    """Validate downloaded image against filters.

    Returns (passed, reason) tuple.
    """
    try:
        img = Image.open(io.BytesIO(image_data))
    except Exception:
        return False, "invalid_image"

    w, h = img.size

    if require_portrait and not is_portrait(img):
        return False, f"landscape_{w}x{h}"

    if reject_anime and is_anime(img):
        return False, "anime"

    return True, "ok"


# ---------------------------------------------------------------------------
# Network interception
# ---------------------------------------------------------------------------

async def scrape_search_results(page, keyword: str, max_results: int = 30) -> list[dict]:
    """Search createporn.com for a keyword and extract post data.

    Strategy A: Intercept XHR/fetch responses containing post arrays.
    Strategy B: Fall back to DOM scraping if no API response captured.
    """
    captured_posts: list[dict] = []
    api_captured = asyncio.Event()

    async def on_response(response):
        """Capture JSON responses that look like search result APIs."""
        nonlocal captured_posts
        url = response.url
        content_type = response.headers.get("content-type", "")

        if "json" not in content_type:
            return
        if any(skip in url for skip in ["/analytics", "/config", "/user", "/auth"]):
            return

        try:
            body = await response.json()
        except Exception:
            return

        posts = _extract_posts_from_json(body)
        if posts:
            log.info(f"  API intercepted: {len(posts)} posts from {url}")
            captured_posts.extend(posts)
            api_captured.set()

    page.on("response", on_response)

    search_url = SEARCH_URL_TEMPLATE.format(keyword=quote(keyword))
    log.info(f"  Navigating to: {search_url}")
    await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)

    try:
        await asyncio.wait_for(api_captured.wait(), timeout=10)
    except asyncio.TimeoutError:
        log.info("  No API response captured within 10s, waiting for page load...")
        await page.wait_for_load_state("networkidle", timeout=15000)

    if captured_posts:
        log.info(f"  Strategy A (network interception): got {len(captured_posts)} posts")
        if len(captured_posts) < max_results:
            captured_posts = await _scroll_for_more(
                page, captured_posts, max_results, api_captured
            )
        page.remove_listener("response", on_response)
        return captured_posts[:max_results]

    log.info("  Strategy B (DOM scraping fallback)")
    page.remove_listener("response", on_response)
    return await _dom_scrape(page, max_results)


def _extract_posts_from_json(data) -> list[dict]:
    """Recursively search JSON data for arrays of post objects."""
    posts = []

    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and _looks_like_post(item):
                posts.append(_normalize_post(item))
            elif isinstance(item, (dict, list)):
                posts.extend(_extract_posts_from_json(item))
    elif isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict) and _looks_like_post(item):
                        posts.append(_normalize_post(item))
            elif isinstance(value, (dict, list)):
                posts.extend(_extract_posts_from_json(value))

    return posts


def _looks_like_post(obj: dict) -> bool:
    """Heuristic: does this dict look like a post/image object?"""
    has_id = any(k in obj for k in ("_id", "id", "postId", "post_id"))
    has_content = any(k in obj for k in ("prompt", "tags", "image", "imageUrl", "url", "thumbnail", "views"))
    return has_id and has_content


def _normalize_post(obj: dict) -> dict:
    """Extract standard fields from a raw post object."""
    post_id = obj.get("_id") or obj.get("id") or obj.get("postId") or obj.get("post_id", "")
    post_id = str(post_id)

    prompt = obj.get("prompt") or obj.get("description") or obj.get("title") or ""

    raw_tags = obj.get("tags") or obj.get("categories") or []
    if raw_tags and isinstance(raw_tags[0], dict):
        tags = [t.get("name", "") for t in raw_tags if isinstance(t, dict)]
    else:
        tags = [str(t) for t in raw_tags]

    image_url = (
        obj.get("imageUrl")
        or obj.get("image_url")
        or obj.get("image")
        or obj.get("thumbnail")
        or ""
    )
    if not image_url and post_id:
        image_url = f"{CDN_BASE}/{post_id}.jpg"

    return {
        "post_id": post_id,
        "prompt": prompt,
        "tags": tags,
        "image_url": image_url,
        "raw": obj,
    }


async def _scroll_for_more(
    page, posts: list[dict], max_results: int, api_event: asyncio.Event
) -> list[dict]:
    """Scroll page to trigger loading more results."""
    scroll_attempts = 0
    max_scrolls = 20  # increased for filtering overhead

    while len(posts) < max_results and scroll_attempts < max_scrolls:
        scroll_attempts += 1
        prev_count = len(posts)
        api_event.clear()

        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        try:
            await asyncio.wait_for(api_event.wait(), timeout=5)
        except asyncio.TimeoutError:
            pass
        await asyncio.sleep(1)

        if len(posts) == prev_count:
            log.info(f"  No new posts after scroll {scroll_attempts}, stopping")
            break
        log.info(f"  Scroll {scroll_attempts}: {len(posts)} posts total")

    return posts


async def _dom_scrape(page, max_results: int) -> list[dict]:
    """Fall back to extracting post IDs from the DOM."""
    posts = []

    await page.wait_for_timeout(3000)

    selectors = [
        'a[href*="/post/"]',
        'a[href*="/p/"]',
        '[data-post-id]',
        '.post-card a',
        '.grid a',
    ]

    links = []
    for selector in selectors:
        try:
            elements = await page.query_selector_all(selector)
            if elements:
                log.info(f"  Found {len(elements)} elements with selector: {selector}")
                links = elements
                break
        except Exception:
            continue

    if not links:
        all_links = await page.query_selector_all("a[href]")
        log.info(f"  Scanning {len(all_links)} links for post patterns...")
        for link in all_links:
            href = await link.get_attribute("href") or ""
            if re.search(r"/post/[a-f0-9]{10,}", href):
                links.append(link)

    seen_ids = set()
    for link in links:
        if len(posts) >= max_results:
            break

        href = await link.get_attribute("href") if hasattr(link, "get_attribute") else ""
        if not href:
            continue

        match = re.search(r"/post/([a-f0-9]{10,})", href or "")
        if not match:
            post_id = await link.get_attribute("data-post-id") if hasattr(link, "get_attribute") else None
            if not post_id:
                continue
        else:
            post_id = match.group(1)

        if post_id in seen_ids:
            continue
        seen_ids.add(post_id)

        image_url = ""
        try:
            img = await link.query_selector("img")
            if img:
                image_url = await img.get_attribute("src") or await img.get_attribute("data-src") or ""
        except Exception:
            pass

        if not image_url:
            image_url = f"{CDN_BASE}/{post_id}.jpg"

        posts.append({
            "post_id": post_id,
            "prompt": "",
            "tags": [],
            "image_url": image_url,
        })

    log.info(f"  DOM scraping found {len(posts)} posts")

    scroll_attempts = 0
    while len(posts) < max_results and scroll_attempts < 10:
        scroll_attempts += 1
        prev_count = len(posts)

        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(2000)

        new_links = await page.query_selector_all('a[href*="/post/"]')
        for link in new_links:
            if len(posts) >= max_results:
                break
            href = await link.get_attribute("href") or ""
            match = re.search(r"/post/([a-f0-9]{10,})", href)
            if match:
                post_id = match.group(1)
                if post_id not in seen_ids:
                    seen_ids.add(post_id)
                    posts.append({
                        "post_id": post_id,
                        "prompt": "",
                        "tags": [],
                        "image_url": f"{CDN_BASE}/{post_id}.jpg",
                    })

        if len(posts) == prev_count:
            break
        log.info(f"  Scroll {scroll_attempts}: {len(posts)} posts total")

    return posts[:max_results]


# ---------------------------------------------------------------------------
# Post detail scraping (optional enrichment)
# ---------------------------------------------------------------------------

async def enrich_post_details(page, post: dict) -> dict:
    """Visit individual post page to get prompt, tags, and style.

    Page text layout (between markers):
      Style: <style_name>
      Tags\n<tag1>\n<tag2>\n...\nCustom Prompt\n<prompt text>\nAI Porn Disclaimer
    """
    if post.get("prompt") and post.get("tags") and post.get("style"):
        return post

    post_url = f"{SITE_BASE}/post/{post['post_id']}"
    try:
        await page.goto(post_url, wait_until="networkidle", timeout=20000)
        await page.wait_for_timeout(2000)

        body_text = await page.inner_text("body")

        # Extract style
        if not post.get("style"):
            style_match = re.search(r"Style:\s*(.+?)(?:\n|View)", body_text)
            if style_match:
                post["style"] = style_match.group(1).strip()

        # Extract aspect ratio
        if not post.get("aspect_ratio"):
            ratio_match = re.search(r"Aspect Ratio:\s*(\d+:\d+)", body_text)
            if ratio_match:
                post["aspect_ratio"] = ratio_match.group(1)

        # Extract tags
        if not post.get("tags"):
            tags_match = re.search(
                r"Tags\n(.*?)(?:Custom Prompt|AI Porn Disclaimer)", body_text, re.DOTALL
            )
            if tags_match:
                raw = tags_match.group(1).strip()
                tags = [t.strip() for t in raw.split("\n") if t.strip()]
                if tags:
                    post["tags"] = tags

        # Extract prompt
        if not post.get("prompt"):
            prompt_match = re.search(
                r"Custom Prompt\n(.*?)AI Porn Disclaimer", body_text, re.DOTALL
            )
            if prompt_match:
                post["prompt"] = prompt_match.group(1).strip()

    except Exception as e:
        log.warning(f"  Failed to enrich post {post['post_id']}: {e}")

    return post


# ---------------------------------------------------------------------------
# Image downloading with filtering
# ---------------------------------------------------------------------------

async def download_images(
    posts: list[dict],
    pose_name: str,
    output_dir: Path,
    max_concurrent: int = 5,
    dry_run: bool = False,
    require_portrait: bool = True,
    reject_anime: bool = True,
) -> dict:
    """Download images from CDN using httpx, with post-download filtering."""
    pose_dir = output_dir / pose_name
    pose_dir.mkdir(parents=True, exist_ok=True)

    stats = {
        "downloaded": 0,
        "skipped": 0,
        "failed": 0,
        "filtered_landscape": 0,
        "filtered_anime": 0,
        "filtered_invalid": 0,
        "total": len(posts),
    }
    semaphore = asyncio.Semaphore(max_concurrent)

    async with httpx.AsyncClient(
        timeout=30.0,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
    ) as client:
        tasks = [
            _download_one(
                client, post, pose_name, pose_dir, semaphore,
                dry_run, stats, require_portrait, reject_anime,
            )
            for post in posts
        ]
        await asyncio.gather(*tasks)

    return stats


async def _download_one(
    client: httpx.AsyncClient,
    post: dict,
    pose_name: str,
    pose_dir: Path,
    semaphore: asyncio.Semaphore,
    dry_run: bool,
    stats: dict,
    require_portrait: bool,
    reject_anime: bool,
):
    """Download a single image with filtering."""
    post_id = post["post_id"]
    image_url = post.get("image_url") or f"{CDN_BASE}/{post_id}.jpg"

    ext = ".jpg"
    url_path = image_url.split("?")[0]
    if url_path.endswith(".png"):
        ext = ".png"
    elif url_path.endswith(".webp"):
        ext = ".webp"

    filename = f"{pose_name}_{post_id}{ext}"
    filepath = pose_dir / filename

    if filepath.exists():
        stats["skipped"] += 1
        return

    if dry_run:
        log.info(f"  [DRY RUN] Would download: {image_url} -> {filename}")
        stats["skipped"] += 1
        return

    async with semaphore:
        try:
            resp = await client.get(image_url)
            if resp.status_code != 200:
                log.warning(f"  Failed {post_id}: HTTP {resp.status_code}")
                stats["failed"] += 1
                return

            content_type = resp.headers.get("content-type", "")
            if "image" not in content_type and len(resp.content) < 1000:
                log.warning(f"  Skipping {post_id}: not an image")
                stats["filtered_invalid"] += 1
                return

            # Apply filters
            passed, reason = check_image(
                resp.content,
                require_portrait=require_portrait,
                reject_anime=reject_anime,
            )

            if not passed:
                if "landscape" in reason:
                    stats["filtered_landscape"] += 1
                elif reason == "anime":
                    stats["filtered_anime"] += 1
                else:
                    stats["filtered_invalid"] += 1
                return

            filepath.write_bytes(resp.content)
            size_kb = len(resp.content) / 1024
            log.info(f"  Downloaded: {filename} ({size_kb:.1f} KB)")
            stats["downloaded"] += 1

        except Exception as e:
            log.warning(f"  Failed {post_id}: {e}")
            stats["failed"] += 1


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def update_metadata(pose_dir: Path, posts: list[dict], search_keyword: str):
    """Update _metadata.json — only for posts whose files exist on disk."""
    meta_path = pose_dir / "_metadata.json"

    metadata = {}
    if meta_path.exists():
        try:
            metadata = json.loads(meta_path.read_text())
        except Exception:
            pass

    # Only write metadata for images that actually exist on disk
    existing_files = {f.stem for f in pose_dir.iterdir() if f.suffix in ('.jpg', '.jpeg', '.png', '.webp')}

    for post in posts:
        post_id = post["post_id"]
        # Check if any file for this post_id exists
        if not any(post_id in fname for fname in existing_files):
            # Image was filtered out, remove from metadata if present
            metadata.pop(post_id, None)
            continue

        existing = metadata.get(post_id, {})
        entry = {
            "prompt": post.get("prompt") or existing.get("prompt", ""),
            "model": "CreatePorn",
            "search_tag": search_keyword,
            "civitai_id": None,
            "tags": post.get("tags") or existing.get("tags", []),
            "source_url": f"{SITE_BASE}/post/{post_id}",
        }
        metadata[post_id] = entry

    meta_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False))
    log.info(f"  Metadata updated: {len(metadata)} entries in {meta_path.name}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_poses(poses_str: str) -> dict[str, str]:
    """Parse pose spec string into {keyword: pose_name} dict.

    Format: "cowgirl,missionary,doggy style:doggy_style"
    """
    result = {}
    for item in poses_str.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" in item:
            keyword, pose_name = item.rsplit(":", 1)
            result[keyword.strip()] = pose_name.strip()
        else:
            result[item] = item.lower().replace(" ", "_")
    return result


async def main():
    parser = argparse.ArgumentParser(
        description="Scrape pose reference images from createporn.com"
    )
    parser.add_argument(
        "--poses",
        required=True,
        help='Comma-separated poses. Format: "cowgirl,doggy style:doggy_style"',
    )
    parser.add_argument(
        "--max-images",
        type=int,
        default=30,
        help="Max images per pose (default: 30)",
    )
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Show browser window (debug mode)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't download images, just show what would be scraped",
    )
    parser.add_argument(
        "--enrich",
        action="store_true",
        help="Visit each post page to get prompt/tags (slower)",
    )
    parser.add_argument(
        "--allow-anime",
        action="store_true",
        help="Don't filter out anime/cartoon images",
    )
    parser.add_argument(
        "--allow-landscape",
        action="store_true",
        help="Don't filter out landscape images",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=OUTPUT_BASE,
        help=f"Output directory (default: {OUTPUT_BASE})",
    )
    args = parser.parse_args()

    poses = parse_poses(args.poses)
    if not poses:
        log.error("No poses specified")
        sys.exit(1)

    require_portrait = not args.allow_landscape
    reject_anime = not args.allow_anime

    log.info(f"Poses to scrape: {poses}")
    log.info(f"Max images per pose: {args.max_images}")
    log.info(f"Filters: portrait_only={require_portrait}, reject_anime={reject_anime}")
    log.info(f"Output directory: {args.output_dir}")
    if args.dry_run:
        log.info("DRY RUN mode - no images will be downloaded")

    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Fetch more candidates than needed to compensate for filtering
    fetch_multiplier = 3 if (require_portrait or reject_anime) else 1

    summary = {}
    start_time = time.time()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=not args.no_headless)
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        for keyword, pose_name in poses.items():
            log.info(f"\n{'='*60}")
            log.info(f"Scraping: '{keyword}' -> {pose_name}/")
            log.info(f"{'='*60}")

            # Fetch extra candidates to account for filtering
            fetch_count = args.max_images * fetch_multiplier

            # Step 1: Search and extract post data
            posts = await scrape_search_results(page, keyword, fetch_count)
            log.info(f"  Found {len(posts)} candidates (need {args.max_images} after filtering)")

            if not posts:
                summary[pose_name] = {
                    "found": 0, "downloaded": 0, "skipped": 0, "failed": 0,
                    "filtered_landscape": 0, "filtered_anime": 0,
                }
                continue

            # Step 2: Optionally enrich with per-post details
            if args.enrich:
                log.info("  Enriching post details...")
                for i, post in enumerate(posts):
                    posts[i] = await enrich_post_details(page, post)
                    if (i + 1) % 5 == 0:
                        log.info(f"  Enriched {i+1}/{len(posts)} posts")

            # Step 3: Download images (with filtering)
            pose_dir = args.output_dir / pose_name
            stats = await download_images(
                posts, pose_name, args.output_dir,
                dry_run=args.dry_run,
                require_portrait=require_portrait,
                reject_anime=reject_anime,
            )

            # Step 4: Update metadata
            if not args.dry_run:
                update_metadata(pose_dir, posts, keyword)

            summary[pose_name] = {
                "found": len(posts),
                **stats,
            }

        await browser.close()

    # Print summary
    elapsed = time.time() - start_time
    log.info(f"\n{'='*60}")
    log.info("SUMMARY")
    log.info(f"{'='*60}")
    for pose_name, stats in summary.items():
        parts = [
            f"found={stats['found']}",
            f"downloaded={stats.get('downloaded', 0)}",
        ]
        fl = stats.get("filtered_landscape", 0)
        fa = stats.get("filtered_anime", 0)
        if fl:
            parts.append(f"filtered_landscape={fl}")
        if fa:
            parts.append(f"filtered_anime={fa}")
        sk = stats.get("skipped", 0)
        if sk:
            parts.append(f"skipped={sk}")
        fail = stats.get("failed", 0)
        if fail:
            parts.append(f"failed={fail}")
        log.info(f"  {pose_name}: {', '.join(parts)}")
    log.info(f"  Total time: {elapsed:.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
