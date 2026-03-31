"""
Migrate local pose reference images to Tencent COS CDN.

Reads all pose_reference_images with /pose-files/ URLs,
uploads the local file to COS, and updates the DB URL.

Usage:
    python scripts/migrate_poses_to_cos.py [--dry-run]
"""
import sqlite3
import sys
import os
import logging
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# Setup path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from api.services.cos_client import upload_file, _make_url, _make_key

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = PROJECT_ROOT / "data" / "wan22.db"
POSE_REF_DIR = PROJECT_ROOT / "data" / "pose_references"
COS_SUBDIR = "pose_references"

DRY_RUN = "--dry-run" in sys.argv


def upload_single(row_id: int, image_url: str) -> tuple[int, str | None, str | None]:
    """Upload one file. Returns (row_id, new_url, error)."""
    rel_path = image_url[len("/pose-files/"):]
    local_path = POSE_REF_DIR / rel_path

    if not local_path.exists():
        return row_id, None, f"File not found: {local_path}"

    # Use pose_key/filename as COS key
    cos_filename = rel_path  # e.g. "threesome/threesome_abc.jpg"
    cos_subdir = COS_SUBDIR

    try:
        if DRY_RUN:
            key = _make_key(f"{cos_subdir}/{os.path.dirname(cos_filename)}", os.path.basename(cos_filename))
            new_url = _make_url(key)
            return row_id, new_url, None

        new_url = upload_file(
            str(local_path),
            f"{cos_subdir}/{os.path.dirname(cos_filename)}",
            os.path.basename(cos_filename),
        )
        return row_id, new_url, None
    except Exception as e:
        return row_id, None, str(e)


def main():
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    cur.execute(
        "SELECT id, image_url FROM pose_reference_images WHERE image_url LIKE '/pose-files/%'"
    )
    rows = cur.fetchall()
    total = len(rows)
    logger.info(f"Found {total} local pose images to migrate. dry_run={DRY_RUN}")

    if total == 0:
        logger.info("Nothing to do.")
        return

    success = 0
    failed = 0
    not_found = 0

    # Upload in parallel (8 threads)
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(upload_single, row_id, url): (row_id, url)
            for row_id, url in rows
        }

        for i, future in enumerate(as_completed(futures), 1):
            row_id, new_url, error = future.result()

            if error:
                if "File not found" in error:
                    not_found += 1
                else:
                    failed += 1
                logger.warning(f"[{i}/{total}] ID={row_id} FAILED: {error}")
                continue

            # Update DB
            if not DRY_RUN:
                cur.execute(
                    "UPDATE pose_reference_images SET image_url = ? WHERE id = ?",
                    (new_url, row_id),
                )
                if i % 100 == 0:
                    conn.commit()

            success += 1
            if i % 50 == 0 or i == total:
                logger.info(f"[{i}/{total}] Uploaded: {success}, Failed: {failed}, Not found: {not_found}")

    conn.commit()
    conn.close()

    logger.info(f"Migration complete: {success} uploaded, {failed} failed, {not_found} not found")


if __name__ == "__main__":
    main()
