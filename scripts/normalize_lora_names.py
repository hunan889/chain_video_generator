#!/usr/bin/env python3
"""
统一LORA命名规范
"""
import sqlite3
import pymysql
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "data" / "wan22.db"

MYSQL_CONFIG = {
    'host': 'use-cdb-b9nvte6o.sql.tencentcdb.com',
    'port': 20603,
    'user': 'user_soga',
    'password': '1IvO@*#68',
    'database': 'tudou_soga',
    'charset': 'utf8mb4'
}


def normalize_lora_name(name):
    """标准化LORA名称，去除HIGH/LOW后缀"""
    if not name:
        return None

    # 移除常见的noise标识
    patterns = [
        r'[_-]?(high|low)[_-]?noise',
        r'[_-]?(HIGH|LOW)[_-]?NOISE',
        r'[_-]?(highnoise|lownoise)',
        r'[_-]?(HIGHNOISE|LOWNOISE)',
        r'_high$',
        r'_low$',
        r'_HIGH$',
        r'_LOW$',
    ]

    normalized = name
    for pattern in patterns:
        normalized = re.sub(pattern, '', normalized, flags=re.IGNORECASE)

    # 清理多余的下划线
    normalized = re.sub(r'_+', '_', normalized)
    normalized = normalized.strip('_')

    return normalized


def main():
    print("开始统一LORA命名...")

    # 连接数据库
    sqlite_conn = sqlite3.connect(str(DB_PATH))
    sqlite_conn.row_factory = sqlite3.Row
    sqlite_cursor = sqlite_conn.cursor()

    mysql_conn = pymysql.connect(**MYSQL_CONFIG)
    mysql_cursor = mysql_conn.cursor(pymysql.cursors.DictCursor)

    # 获取所有LORA
    sqlite_cursor.execute("SELECT * FROM pose_loras WHERE lora_type = 'video'")
    loras = sqlite_cursor.fetchall()

    updated_count = 0

    for lora in loras:
        lora_id = lora['lora_id']
        current_name = lora['lora_name']

        # 如果有lora_id，从MySQL获取名称
        if lora_id:
            mysql_cursor.execute(
                "SELECT name, file FROM lora_metadata WHERE id = %s",
                (lora_id,)
            )
            mysql_row = mysql_cursor.fetchone()

            if mysql_row:
                # 优先使用name，如果为空则使用file
                source_name = mysql_row['name'] or mysql_row['file']

                if source_name:
                    # 标准化名称
                    normalized_name = normalize_lora_name(source_name)

                    if normalized_name != current_name:
                        sqlite_cursor.execute(
                            "UPDATE pose_loras SET lora_name = ? WHERE id = ?",
                            (normalized_name, lora['id'])
                        )
                        print(f"✓ ID {lora['id']}: {current_name} -> {normalized_name}")
                        updated_count += 1

        # 如果没有lora_id但有lora_name，也标准化
        elif current_name:
            normalized_name = normalize_lora_name(current_name)

            if normalized_name != current_name:
                sqlite_cursor.execute(
                    "UPDATE pose_loras SET lora_name = ? WHERE id = ?",
                    (normalized_name, lora['id'])
                )
                print(f"✓ ID {lora['id']}: {current_name} -> {normalized_name}")
                updated_count += 1

    sqlite_conn.commit()
    sqlite_cursor.close()
    sqlite_conn.close()
    mysql_cursor.close()
    mysql_conn.close()

    print(f"\n完成！共更新 {updated_count} 条记录")


if __name__ == "__main__":
    main()
