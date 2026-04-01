"""Import pose reference images from 148's local files to COS + MySQL.

Run on 148 server:
  cd /home/gime/soft/wan22-service
  python scripts/import_pose_data.py
"""
import json
import os
import sys
import pymysql

# COS config
COS_SECRET_ID = os.getenv("COS_SECRET_ID", "IKIDXw72g14nLZUXGgmY0A0XkcDYbMpzqPkl")
COS_SECRET_KEY = os.getenv("COS_SECRET_KEY", "mUsxi9G8fslJWPxB6k7A9q5SVFgnc8JA")
COS_BUCKET = os.getenv("COS_BUCKET", "overseas-gime-1370751292")
COS_REGION = os.getenv("COS_REGION", "na-ashburn")
COS_PREFIX = os.getenv("COS_PREFIX", "cvid")

# MySQL config (external address for 148)
MYSQL_CONFIG = {
    'host': os.getenv('MYSQL_HOST', 'use-cdb-b9nvte6o.sql.tencentcdb.com'),
    'port': int(os.getenv('MYSQL_PORT', '20603')),
    'user': 'user_soga',
    'password': '1IvO@*#68',
    'database': 'tudou_soga',
    'charset': 'utf8mb4',
}

POSE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "pose_references")
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def get_cos_client():
    from qcloud_cos import CosConfig, CosS3Client
    config = CosConfig(
        Region=COS_REGION,
        SecretId=COS_SECRET_ID,
        SecretKey=COS_SECRET_KEY,
    )
    return CosS3Client(config)


def upload_to_cos(cos_client, local_path, subdir, filename):
    key = f"{COS_PREFIX}/{subdir}/{filename}"
    cos_client.upload_file(Bucket=COS_BUCKET, Key=key, LocalFilePath=local_path)
    url = f"https://{COS_BUCKET}.cos.{COS_REGION}.myqcloud.com/{key}"
    return url


def main():
    conn = pymysql.connect(**MYSQL_CONFIG, cursorclass=pymysql.cursors.DictCursor)
    cursor = conn.cursor()
    cos = get_cos_client()

    # Get pose_key -> id mapping
    cursor.execute("SELECT id, pose_key FROM poses")
    pose_map = {r["pose_key"]: r["id"] for r in cursor.fetchall()}
    print(f"Poses in MySQL: {len(pose_map)}")

    # Get existing image URLs to avoid duplicates
    cursor.execute("SELECT image_url FROM pose_reference_images")
    existing_urls = {r["image_url"] for r in cursor.fetchall()}
    print(f"Existing reference images: {len(existing_urls)}")

    # Get lora_metadata for auto-association
    cursor.execute("SELECT id, name, file, mode, noise_stage FROM lora_metadata WHERE enabled = 1")
    all_loras = cursor.fetchall()
    print(f"Available loras: {len(all_loras)}")

    if not os.path.isdir(POSE_DIR):
        print(f"ERROR: Pose directory not found: {POSE_DIR}")
        sys.exit(1)

    total_uploaded = 0
    total_skipped = 0
    total_loras = 0

    for pose_key in sorted(os.listdir(POSE_DIR)):
        pose_path = os.path.join(POSE_DIR, pose_key)
        if not os.path.isdir(pose_path):
            continue

        pose_id = pose_map.get(pose_key)
        if not pose_id:
            print(f"  SKIP {pose_key}: not in MySQL poses table")
            continue

        # Read metadata if exists
        meta_path = os.path.join(pose_path, "_metadata.json")
        metadata = {}
        if os.path.exists(meta_path):
            try:
                with open(meta_path) as f:
                    metadata = json.load(f)
            except Exception:
                pass

        # Upload images
        images = sorted([
            f for f in os.listdir(pose_path)
            if os.path.splitext(f)[1].lower() in IMAGE_EXTS
        ])

        uploaded = 0
        for i, img_file in enumerate(images):
            cos_subdir = f"pose_refs/{pose_key}"
            cos_url_check = f"https://{COS_BUCKET}.cos.{COS_REGION}.myqcloud.com/{COS_PREFIX}/{cos_subdir}/{img_file}"
            if cos_url_check in existing_urls:
                total_skipped += 1
                continue

            local_path = os.path.join(pose_path, img_file)
            try:
                cos_url = upload_to_cos(cos, local_path, cos_subdir, img_file)

                # Get prompt from metadata
                prompt = ""
                img_meta = metadata.get(img_file, {})
                if isinstance(img_meta, dict):
                    prompt = img_meta.get("prompt", "")

                cursor.execute(
                    "INSERT INTO pose_reference_images (pose_id, image_url, style, prompt, is_default) "
                    "VALUES (%s, %s, 'realistic', %s, %s)",
                    (pose_id, cos_url, prompt, 1 if i == 0 else 0),
                )
                uploaded += 1
            except Exception as e:
                print(f"  ERROR uploading {img_file}: {e}")

        if uploaded:
            conn.commit()
            total_uploaded += uploaded
            print(f"  {pose_key}: {uploaded}/{len(images)} images uploaded")

        # Auto-associate loras by name matching
        cursor.execute("SELECT COUNT(*) as cnt FROM pose_loras WHERE pose_id = %s", (pose_id,))
        existing_lora_count = cursor.fetchone()["cnt"]
        if existing_lora_count == 0:
            matched = 0
            for lora in all_loras:
                lora_name = (lora.get("name") or lora.get("file") or "").lower()
                if pose_key.replace("_", "") in lora_name.replace("_", "").replace("-", ""):
                    # All lora_metadata entries are video loras (T2V/I2V/both)
                    # Image loras come from image_lora_metadata table (separate)
                    lora_type = "video"
                    try:
                        cursor.execute(
                            "INSERT INTO pose_loras (pose_id, lora_id, lora_name, lora_type, noise_stage, recommended_weight) "
                            "VALUES (%s, %s, %s, %s, %s, 0.8)",
                            (pose_id, lora["id"], lora.get("name"), lora_type, lora.get("noise_stage", "high")),
                        )
                        matched += 1
                    except pymysql.err.IntegrityError:
                        pass
            if matched:
                conn.commit()
                total_loras += matched
                print(f"  {pose_key}: {matched} loras auto-associated")

    print(f"\nDone: {total_uploaded} images uploaded, {total_skipped} skipped, {total_loras} loras associated")
    conn.close()


if __name__ == "__main__":
    main()
