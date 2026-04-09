"""Migrate pose_reference_images from cvid/pose_refs/* to rgh/pose_refs/*.

Copies objects within the same COS bucket (no download needed),
then updates the DB URLs in-place.

Run on the gateway server:
  cd /usr/local/soft/chain_video_api
  python scripts/migrate_pose_images_to_rgh.py
"""

import os
import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Load gateway .env
from dotenv import load_dotenv
load_dotenv("api_gateway/.env")
load_dotenv(".env")

import pymysql
import pymysql.cursors

# COS config
SECRET_ID  = os.environ["COS_SECRET_ID"]
SECRET_KEY = os.environ["COS_SECRET_KEY"]
BUCKET     = os.environ["COS_BUCKET"]
REGION     = os.environ["COS_REGION"]
OLD_PREFIX = "cvid"
NEW_PREFIX = "rgh"

# DB config
MYSQL_HOST = os.getenv("MYSQL_HOST", "use-cdb-b9nvte6o.sql.tencentcdb.com")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "20603"))
MYSQL_USER = os.getenv("MYSQL_USER", "user_soga")
MYSQL_PASS = os.getenv("MYSQL_PASSWORD", "1IvO@*#68")
MYSQL_DB   = os.getenv("MYSQL_DB", "tudou_soga")

BASE_URL   = f"https://{BUCKET}.cos.{REGION}.myqcloud.com/"


def get_cos_client():
    from qcloud_cos import CosConfig, CosS3Client
    cfg = CosConfig(Region=REGION, SecretId=SECRET_ID, SecretKey=SECRET_KEY)
    return CosS3Client(cfg)


def get_db():
    return pymysql.connect(
        host=MYSQL_HOST, port=MYSQL_PORT,
        user=MYSQL_USER, password=MYSQL_PASS,
        database=MYSQL_DB, charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10,
    )


def migrate():
    cos = get_cos_client()
    db  = get_db()

    with db.cursor() as cur:
        cur.execute(
            "SELECT id, image_url FROM pose_reference_images "
            "WHERE image_url LIKE %s",
            (f"%/{OLD_PREFIX}/pose_refs/%",),
        )
        rows = cur.fetchall()

    logger.info("Found %d images to migrate", len(rows))

    ok = 0
    fail = 0

    for row in rows:
        img_id  = row["id"]
        old_url = row["image_url"]

        # old_url: https://<bucket>.cos.<region>.myqcloud.com/cvid/pose_refs/<pose_key>/<file>
        if not old_url.startswith(BASE_URL):
            logger.warning("id=%d unexpected URL, skipping: %s", img_id, old_url)
            fail += 1
            continue

        old_key = old_url[len(BASE_URL):]  # cvid/pose_refs/<pose_key>/<file>

        if not old_key.startswith(f"{OLD_PREFIX}/pose_refs/"):
            logger.warning("id=%d key doesn't match expected pattern: %s", img_id, old_key)
            fail += 1
            continue

        # Build new key: rgh/pose_refs/<pose_key>/<file>
        rest    = old_key[len(f"{OLD_PREFIX}/"):]   # pose_refs/<pose_key>/<file>
        new_key = f"{NEW_PREFIX}/{rest}"             # rgh/pose_refs/<pose_key>/<file>
        new_url = f"{BASE_URL}{new_key}"

        try:
            # COS intra-bucket copy (no data transfer out of bucket)
            cos.copy_object(
                Bucket=BUCKET,
                Key=new_key,
                CopySource={
                    "Bucket": BUCKET,
                    "Key":    old_key,
                    "Region": REGION,
                },
            )
        except Exception as exc:
            logger.error("id=%d COS copy failed: %s", img_id, exc)
            fail += 1
            continue

        # Update DB
        with db.cursor() as cur:
            cur.execute(
                "UPDATE pose_reference_images SET image_url = %s WHERE id = %s",
                (new_url, img_id),
            )
        db.commit()

        logger.info("id=%d migrated: %s → %s", img_id, old_key, new_key)
        ok += 1

    db.close()
    logger.info("Done. ok=%d  fail=%d", ok, fail)
    if fail:
        sys.exit(1)


if __name__ == "__main__":
    migrate()
