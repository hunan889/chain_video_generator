"""
将 data/pose_references/ 下的图片导入到 wan22.db 的 pose_reference_images 表
- 自动为不存在的 pose_key 创建新的 pose 记录
- 读取 _metadata.json 获取 prompt 信息
- image_url 格式: /pose-files/{pose_key}/{filename}
"""
import sqlite3
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_ROOT, "data", "wan22.db")
POSE_DIR = os.path.join(PROJECT_ROOT, "data", "pose_references")

# 新姿势的中文名和分类
NEW_POSES = {
    "against_wall": {"name_cn": "壁式", "name_en": "Against Wall", "category": "position"},
    "bukkake": {"name_cn": "颜射群交", "name_en": "Bukkake", "category": "other"},
    "creampie": {"name_cn": "内射", "name_en": "Creampie", "category": "other"},
    "deepthroat": {"name_cn": "深喉", "name_en": "Deepthroat", "category": "oral"},
    "facial": {"name_cn": "颜射", "name_en": "Facial", "category": "other"},
    "lap_dance": {"name_cn": "大腿舞", "name_en": "Lap Dance", "category": "other"},
    "mating_press": {"name_cn": "压腿式", "name_en": "Mating Press", "category": "position"},
    "pile_driver": {"name_cn": "打桩式", "name_en": "Pile Driver", "category": "position"},
    "shower_sex": {"name_cn": "淋浴式", "name_en": "Shower Sex", "category": "position"},
    "strap_on": {"name_cn": "穿戴式", "name_en": "Strap On", "category": "other"},
}

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def main():
    if not os.path.exists(DB_PATH):
        print(f"ERROR: Database not found: {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # 1. 获取现有 pose_key -> id 映射
    cur.execute("SELECT id, pose_key FROM poses")
    pose_map = {row["pose_key"]: row["id"] for row in cur.fetchall()}
    print(f"Existing poses in DB: {len(pose_map)}")

    # 2. 扫描本地文件夹
    if not os.path.isdir(POSE_DIR):
        print(f"ERROR: Pose directory not found: {POSE_DIR}")
        sys.exit(1)

    folders = sorted([
        d for d in os.listdir(POSE_DIR)
        if os.path.isdir(os.path.join(POSE_DIR, d)) and not d.startswith(".")
    ])
    print(f"Local pose folders: {len(folders)}")

    # 3. 创建缺失的 pose 记录
    created = 0
    for folder in folders:
        if folder not in pose_map:
            info = NEW_POSES.get(folder, {})
            name_cn = info.get("name_cn", folder)
            name_en = info.get("name_en", folder.replace("_", " ").title())
            category = info.get("category", "other")
            cur.execute(
                "INSERT INTO poses (pose_key, name_en, name_cn, category, enabled) VALUES (?, ?, ?, ?, 1)",
                (folder, name_en, name_cn, category),
            )
            pose_map[folder] = cur.lastrowid
            print(f"  Created pose: {folder} (id={pose_map[folder]}, {name_cn})")
            created += 1

    if created:
        conn.commit()
        print(f"Created {created} new poses")

    # 4. 获取已有的 image_url 集合（避免重复插入）
    cur.execute("SELECT image_url FROM pose_reference_images")
    existing_urls = {row["image_url"] for row in cur.fetchall()}
    print(f"Existing reference images in DB: {len(existing_urls)}")

    # 5. 导入图片
    total_inserted = 0
    total_skipped = 0

    for folder in folders:
        pose_key = folder
        pose_id = pose_map.get(pose_key)
        if not pose_id:
            print(f"  SKIP {folder}: no pose_id found")
            continue

        folder_path = os.path.join(POSE_DIR, folder)

        # 读取 _metadata.json
        meta_path = os.path.join(folder_path, "_metadata.json")
        metadata = {}
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r") as f:
                    metadata = json.load(f)
            except Exception:
                pass

        # 扫描图片文件
        images = sorted([
            f for f in os.listdir(folder_path)
            if os.path.splitext(f)[1].lower() in IMAGE_EXTS
        ])

        inserted = 0
        skipped = 0
        for img_file in images:
            image_url = f"/pose-files/{pose_key}/{img_file}"
            if image_url in existing_urls:
                skipped += 1
                continue

            # 从 metadata 中提取 prompt（key 是 post_id）
            post_id = img_file.rsplit("_", 1)[-1].rsplit(".", 1)[0] if "_" in img_file else ""
            meta = metadata.get(post_id, {})
            prompt = meta.get("prompt", "")

            cur.execute(
                """INSERT INTO pose_reference_images
                   (pose_id, image_url, style, prompt, is_default)
                   VALUES (?, ?, 'realistic', ?, 0)""",
                (pose_id, image_url, prompt),
            )
            existing_urls.add(image_url)
            inserted += 1

        total_inserted += inserted
        total_skipped += skipped
        status = f"inserted={inserted}"
        if skipped:
            status += f", skipped={skipped}"
        print(f"  {pose_key}: {len(images)} files, {status}")

    conn.commit()
    conn.close()

    print(f"\nDone! Inserted {total_inserted} new images, skipped {total_skipped} duplicates.")


if __name__ == "__main__":
    main()
