"""Migrate existing Redis task/workflow data to MySQL generation_tasks table."""
import json
import os
import sys
import pymysql
import redis

# Redis config
REDIS_URL = os.getenv("REDIS_URL", "redis://:Docare123456@10.200.0.11:6379/8")

# MySQL config
MYSQL_CONFIG = {
    'host': os.getenv('MYSQL_HOST', '10.200.0.21'),
    'port': int(os.getenv('MYSQL_PORT', '3306')),
    'user': os.getenv('MYSQL_USER', 'user_soga'),
    'password': os.getenv('MYSQL_PASSWORD', '1IvO@*#68'),
    'database': os.getenv('MYSQL_DB', 'tudou_soga'),
    'charset': 'utf8mb4',
}

WORKFLOW_MODES = {"t2v", "i2v", "extend", "chain", "first_frame",
                  "full_body_reference", "face_reference",
                  "vace_ref2v", "vace_v2v", "vace_inpainting", "vace_flf2v"}

CATEGORY_MAP = {
    "t2v": "local", "i2v": "local", "extend": "local", "chain": "local",
    "first_frame": "local", "full_body_reference": "local", "face_reference": "local",
    "vace_ref2v": "local", "vace_v2v": "local", "vace_inpainting": "local", "vace_flf2v": "local",
    "concat": "local",
    "wan26_t2v": "thirdparty", "wan26_i2v": "thirdparty",
    "seedance_t2v": "thirdparty", "seedance_i2v": "thirdparty",
    "clothoff": "thirdparty",
    "interpolate": "postprocess", "upscale": "postprocess",
    "audio": "postprocess", "faceswap": "postprocess",
    "lora_download": "utility",
}

INSERT_SQL = """
INSERT IGNORE INTO generation_tasks
(task_id, task_type, category, provider, status, progress, error_message,
 prompt, model, result_url, thumbnail_url, extra_urls,
 parent_task_id, chain_id, external_task_id, created_at, completed_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
        FROM_UNIXTIME(%s), FROM_UNIXTIME(NULLIF(%s, 0)))
"""


def migrate():
    r = redis.from_url(REDIS_URL, decode_responses=True)
    conn = pymysql.connect(**MYSQL_CONFIG)
    cursor = conn.cursor()

    migrated = 0
    skipped = 0
    errors = 0

    # --- Source 1: workflow:wf_* keys (old monolith advanced workflows) ---
    print("Scanning workflow:wf_* keys...")
    wf_cursor = 0
    while True:
        wf_cursor, keys = r.scan(wf_cursor, match="workflow:wf_*", count=200)
        for key in keys:
            if key.count(":") > 1:
                continue
            try:
                raw = r.hgetall(key)
                if not raw:
                    continue
                wf_id = key.split(":", 1)[1]
                mode = raw.get("mode", "unknown")
                category = CATEGORY_MAP.get(mode, "local")
                status = raw.get("status", "unknown")
                prompt = raw.get("user_prompt", "")
                created_at = float(raw.get("created_at", 0))
                completed_at = float(raw.get("completed_at", 0)) if raw.get("completed_at") else 0

                extra = {}
                if raw.get("first_frame_url"):
                    extra["first_frame_url"] = raw["first_frame_url"]
                if raw.get("edited_frame_url"):
                    extra["edited_frame_url"] = raw["edited_frame_url"]

                cursor.execute(INSERT_SQL, (
                    wf_id, mode, category, "comfyui", status, 1.0 if status == "completed" else 0.0,
                    raw.get("error"), prompt, raw.get("model"),
                    raw.get("final_video_url"),
                    raw.get("edited_frame_url") or raw.get("first_frame_url"),
                    json.dumps(extra) if extra else None,
                    None, raw.get("chain_id"), None,
                    created_at, completed_at,
                ))
                migrated += 1
            except pymysql.err.IntegrityError:
                skipped += 1
            except Exception as e:
                errors += 1
                print(f"  Error on {key}: {e}")
        if wf_cursor == 0:
            break

    conn.commit()
    print(f"  Workflows: migrated={migrated}, skipped={skipped}, errors={errors}")

    # --- Source 2: task:* keys (gateway generate tasks) ---
    print("Scanning task:* keys...")
    wf2_migrated = 0
    t_cursor = 0
    while True:
        t_cursor, keys = r.scan(t_cursor, match="task:*", count=200)
        for key in keys:
            try:
                raw = r.hgetall(key)
                if not raw:
                    continue
                mode = raw.get("mode", "")
                if not mode:
                    continue
                task_id = key.split(":", 1)[1] if ":" in key else key
                category = CATEGORY_MAP.get(mode, "local")
                status = raw.get("status", "unknown")

                params_str = raw.get("params", "{}")
                try:
                    params = json.loads(params_str)
                except (json.JSONDecodeError, TypeError):
                    params = {}

                prompt = params.get("prompt", "")
                created_at = float(raw.get("created_at", 0))
                completed_at = float(raw.get("completed_at", 0)) if raw.get("completed_at") else 0

                extra = {}
                if raw.get("last_frame_url"):
                    extra["last_frame_url"] = raw["last_frame_url"]

                cursor.execute(INSERT_SQL, (
                    task_id, mode, category, "comfyui", status,
                    float(raw.get("progress", 0)),
                    raw.get("error"), prompt, raw.get("model"),
                    raw.get("video_url"),
                    raw.get("last_frame_url"),
                    json.dumps(extra) if extra else None,
                    params.get("source_task_id") or params.get("parent_task_id"),
                    raw.get("chain_id"), None,
                    created_at, completed_at,
                ))
                wf2_migrated += 1
            except pymysql.err.IntegrityError:
                skipped += 1
            except Exception as e:
                errors += 1
                print(f"  Error on {key}: {e}")
        if t_cursor == 0:
            break

    conn.commit()
    print(f"  Tasks: migrated={wf2_migrated}, skipped={skipped}, errors={errors}")

    # Verify
    cursor.execute("SELECT COUNT(*) as cnt FROM generation_tasks")
    total = cursor.fetchone()[0]
    cursor.execute("SELECT category, COUNT(*) as cnt FROM generation_tasks GROUP BY category")
    cats = {row[0]: row[1] for row in cursor.fetchall()}
    print(f"\nTotal in MySQL: {total}")
    print(f"By category: {cats}")

    conn.close()
    r.close()


if __name__ == "__main__":
    migrate()
